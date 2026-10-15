"""Local disposable PostgreSQL + S3-compatible attachment recovery rehearsal.

This helper is intentionally narrower than the general Phase F harness.  It can
only address the loopback PostgreSQL, MinIO, and ClamAV services from
``deploy/compose.dev.yml`` and every mutation is gated by explicit wave-7
resource names plus a target-bound confirmation string.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import httpx
import psycopg
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import create_engine

from rehearse import (
    EVIDENCE_ROOT,
    HEAD_REVISION,
    RECOVERY_SEED_SQL,
    ROOT,
    DatabaseTarget,
    FileIdentity,
    PinnedReadFile,
    SafetyError,
    Target,
    _alembic,
    _archive_listing,
    _begin_read_only_snapshot,
    _command_from_pinned_file,
    _command_to_new_file,
    _database_rows_fingerprint,
    _database_schema_fingerprint,
    _pinned_file_sha256,
    _postgresql_command,
    _recovery_point_age_seconds,
    _reject_duplicate_json_keys,
    _required_environment,
    _resolve_non_symlink_path,
    _resolve_postgresql_client,
    _safe_dump_path,
    _safe_evidence_output,
    _snapshot_metadata,
    _write_json_evidence,
)
from tc_api.main import create_app  # type: ignore[import-untyped]
from tc_api.object_storage_runtime import ObjectStorageRuntime  # type: ignore[import-untyped]
from tc_backend.infrastructure.object_storage import (  # type: ignore[import-untyped]
    ClamAVInstreamScanner,
    ObjectNotFound,
    ObjectStorageError,
    S3CompatibleObjectStorage,
    verify_quarantined_object,
)

TASK = "PF-SF1-T08"
ACTION = "LOCAL-OBJECT-STORAGE-RECOVERY"
CLEANUP_ACTION = "LOCAL-OBJECT-STORAGE-CLEANUP"
MANIFEST_SCHEMA = "tc.phase-f.object-storage-recovery-manifest.v1"
EVIDENCE_SCHEMA = "tc.phase-f.object-storage-recovery-evidence.v1"
CLEANUP_SCHEMA = "tc.phase-f.object-storage-recovery-cleanup.v1"
CANONICAL_DIGEST_VERSION = "tc.canonical-json.sha256.v1"
BINDING_VERSION = "tc.phase-f.object-storage-recovery-binding.v1"
INVENTORY_VERSION = "tc.s3-compatible-inventory.v1"
ATTACHMENT_METADATA_VERSION = "tc.attachment-metadata.v1"
FIXTURE_VERSION = "pf-sf1-t08-wave7-fixture-v1"
LOCAL_PROVIDER = "MINIO"
DATABASE_PROVIDER_CONTRACT = "R2"
EXPECTED_OWNER_TYPES = ("EXECUTION_ATTEMPT", "ISSUE")
FIXTURE_MIME_TYPES = ("application/json", "image/png", "text/plain")
PAGE_LIMIT = 3
MAX_OBJECT_BYTES = 1024 * 1024
WAVE_TOKENS = frozenset({"pf", "sf1", "t08", "wave7"})
DATABASE_PATTERN = re.compile(
    r"^tc_phase_f_pf_sf1_t08_wave7_[a-z0-9]+_"
    r"(?P<role>source|recovery)_test_disposable$"
)
BUCKET_PATTERN = re.compile(
    r"^tc-phase-f-pf-sf1-t08-wave7-[a-z0-9]+-"
    r"(?P<role>source|recovery)-disposable$"
)
PREFIX_PATTERN = re.compile(r"^pf-sf1-t08-wave7/[a-z0-9]+/objects/$")
SAFE_ENV_PATTERN = re.compile(r"^TC_PHASE_F_[A-Z0-9_]+$")
HEX_DIGEST_PATTERN = re.compile(r"^[a-f0-9]{64}$")
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
WORKSPACE_ID = "00000000-0000-7000-8000-000000000100"
ISSUE_ID = "00000000-0000-7000-8000-000000000901"
ATTEMPT_ID = "00000000-0000-7000-8000-000000000821"
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
    "AScY42YAAAAASUVORK5CYII="
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _digest(value: object, *, version: str = CANONICAL_DIGEST_VERSION) -> str:
    framed = version.encode("utf-8") + b"\0" + _canonical_bytes(value)
    return hashlib.sha256(framed).hexdigest()


def _digest_record(value: object, *, version: str) -> dict[str, str]:
    return {"version": version, "sha256": _digest(value, version=version)}


def _contract_payload(payload: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key != "contractDigest"}


def _seal_contract(payload: Mapping[str, object]) -> dict[str, object]:
    result = dict(payload)
    result["contractDigest"] = _digest_record(
        _contract_payload(result), version=CANONICAL_DIGEST_VERSION
    )
    return result


def _verify_contract(payload: object, *, expected_schema: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise SafetyError("recovery contract must be a JSON object")
    if payload.get("schemaVersion") != expected_schema:
        raise SafetyError("recovery contract schema version is not supported")
    digest = payload.get("contractDigest")
    if not isinstance(digest, dict) or set(digest) != {"version", "sha256"}:
        raise SafetyError("recovery contract digest is malformed")
    if digest.get("version") != CANONICAL_DIGEST_VERSION:
        raise SafetyError("recovery contract digest version is not supported")
    expected = _digest(
        _contract_payload(payload), version=CANONICAL_DIGEST_VERSION
    )
    if digest.get("sha256") != expected:
        raise SafetyError("recovery contract digest mismatch")
    return payload


def _target(args: argparse.Namespace) -> Target:
    target = Target(str(args.target_name), str(args.target_class))
    if target.target_class != "disposable":
        raise SafetyError("local object recovery is disposable-only")
    tokens = {token for token in re.split(r"[^a-z0-9]+", target.name) if token}
    if not WAVE_TOKENS.issubset(tokens):
        raise SafetyError("target name must contain pf-sf1-t08-wave7")
    return target


def _confirmation(action: str, target: Target) -> str:
    return f"RUN {TASK} {action} ON {target.name}"


def _validate_database_name(name: str, *, role: str) -> str:
    match = DATABASE_PATTERN.fullmatch(name)
    if match is None or match.group("role") != role:
        raise SafetyError(
            f"{role} database must be a literal "
            "tc_phase_f_pf_sf1_t08_wave7_<run>_"
            f"{role}_test_disposable name"
        )
    return name


def _validate_bucket_name(name: str, *, role: str) -> str:
    match = BUCKET_PATTERN.fullmatch(name)
    if match is None or match.group("role") != role:
        raise SafetyError(
            f"{role} bucket must be a literal "
            "tc-phase-f-pf-sf1-t08-wave7-<run>-"
            f"{role}-disposable name"
        )
    return name


def _validate_prefix(value: str) -> str:
    if PREFIX_PATTERN.fullmatch(value) is None:
        raise SafetyError(
            "object prefix must be pf-sf1-t08-wave7/<run>/objects/"
        )
    return value


def _database_comment(recovery_point_id: str, role: str) -> str:
    return (
        f"tc-phase-f-object-storage-recovery:v1:{recovery_point_id}:{role}:"
        "test-disposable"
    )


def _validate_endpoint(value: str) -> str:
    parsed = urlsplit(value.rstrip("/"))
    if (
        parsed.scheme != "http"
        or parsed.hostname not in LOOPBACK_HOSTS
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise SafetyError("local MinIO endpoint must be a credential-free loopback HTTP origin")
    return value.rstrip("/")


def _validate_recovery_point(value: str) -> str:
    if re.fullmatch(r"pf-sf1-t08-wave7-[a-z0-9]+", value) is None:
        raise SafetyError("recovery point must be pf-sf1-t08-wave7-<run>")
    return value


def _validate_inputs(args: argparse.Namespace) -> dict[str, str]:
    source_database = _validate_database_name(
        str(args.source_database_name), role="source"
    )
    recovery_database = _validate_database_name(
        str(args.recovery_database_name), role="recovery"
    )
    source_bucket = _validate_bucket_name(str(args.source_bucket), role="source")
    recovery_bucket = _validate_bucket_name(
        str(args.recovery_bucket), role="recovery"
    )
    prefix = _validate_prefix(str(args.object_prefix))
    recovery_point = _validate_recovery_point(str(args.recovery_point))
    endpoint = _validate_endpoint(str(args.endpoint_url))
    if source_database == recovery_database or source_bucket == recovery_bucket:
        raise SafetyError("source and recovery destinations must be distinct")
    run_tokens = set(recovery_point.split("-"))
    for value in (
        source_database,
        recovery_database,
        source_bucket,
        recovery_bucket,
        prefix,
    ):
        if not any(token in value for token in run_tokens - WAVE_TOKENS):
            raise SafetyError("all disposable resources must share the recovery-point run id")
    for env_name in (
        args.admin_database_url_env,
        args.minio_access_key_env,
        args.minio_secret_key_env,
    ):
        if SAFE_ENV_PATTERN.fullmatch(str(env_name)) is None:
            raise SafetyError("credential environment names must start with TC_PHASE_F_")
    return {
        "sourceDatabase": source_database,
        "recoveryDatabase": recovery_database,
        "sourceBucket": source_bucket,
        "recoveryBucket": recovery_bucket,
        "objectPrefix": prefix,
        "recoveryPoint": recovery_point,
        "endpoint": endpoint,
    }


def _admin_database(args: argparse.Namespace) -> DatabaseTarget:
    env_name = str(args.admin_database_url_env)
    raw = _required_environment(env_name)
    parsed = urlsplit(raw)
    if (
        parsed.scheme not in {"postgresql", "postgresql+psycopg"}
        or parsed.hostname not in LOOPBACK_HOSTS
        or unquote(parsed.path.removeprefix("/")) != "tc"
        or parsed.fragment
        or parsed.query
    ):
        raise SafetyError(
            "admin database URL must address the loopback tc database without query or fragment"
        )
    libpq_url = raw.replace("postgresql+psycopg://", "postgresql://", 1)
    environment = {
        "PGDATABASE": "tc",
        "PGHOST": str(parsed.hostname),
        "PGPORT": str(parsed.port or 5432),
        "PGCONNECT_TIMEOUT": "10",
    }
    if parsed.username:
        environment["PGUSER"] = unquote(parsed.username)
    if parsed.password:
        environment["PGPASSWORD"] = unquote(parsed.password)
    return DatabaseTarget(
        "tc", env_name, raw, libpq_url, tuple(sorted(environment.items()))
    )


def _derived_database(admin: DatabaseTarget, name: str) -> DatabaseTarget:
    parsed = urlsplit(admin.url)
    raw = urlunsplit((parsed.scheme, parsed.netloc, f"/{name}", "", ""))
    libpq = raw.replace("postgresql+psycopg://", "postgresql://", 1)
    environment = dict(admin.connection_environment)
    environment["PGDATABASE"] = name
    return DatabaseTarget(
        name,
        admin.url_env,
        raw,
        libpq,
        tuple(sorted(environment.items())),
    )


def _storage_adapter(
    args: argparse.Namespace, *, endpoint: str, bucket: str
) -> S3CompatibleObjectStorage:
    return S3CompatibleObjectStorage(
        endpoint_url=endpoint,
        browser_endpoint_url=endpoint,
        bucket=bucket,
        region="us-east-1",
        access_key_id=_required_environment(str(args.minio_access_key_env)),
        secret_access_key=_required_environment(str(args.minio_secret_key_env)),
    )


def _database_inventory(connection: psycopg.Connection[Any]) -> dict[str, object]:
    names = [
        str(row[0])
        for row in connection.execute(
            "SELECT datname FROM pg_database ORDER BY datname"
        ).fetchall()
    ]
    return {
        "version": "postgresql-database-name-inventory.v1",
        "count": len(names),
        "sha256": _digest(names, version="postgresql-database-name-inventory.v1"),
        "tcPresent": "tc" in names,
    }


def _bucket_inventory(adapter: S3CompatibleObjectStorage) -> dict[str, object]:
    names = list(adapter.list_buckets())
    return {
        "version": "s3-compatible-bucket-name-inventory.v1",
        "count": len(names),
        "sha256": _digest(names, version="s3-compatible-bucket-name-inventory.v1"),
    }


def _prior_evidence_inventory(*, exclude: set[Path]) -> dict[str, object]:
    entries: list[dict[str, str]] = []
    excluded = {path.resolve() for path in exclude}
    for path in sorted(EVIDENCE_ROOT.glob("*.json")):
        if path.resolve() in excluded:
            continue
        with PinnedReadFile(
            path,
            label="prior Phase F JSON evidence",
            maximum_size=8 * 1024 * 1024,
            require_nonempty=True,
        ) as source:
            sha256 = source.run_stage(
                "SHA-256", lambda: _pinned_file_sha256(source)
            )
        entries.append(
            {"path": str(path.relative_to(ROOT)), "sha256": sha256}
        )
    return {
        "version": "phase-f-prior-json-evidence-inventory.v1",
        "count": len(entries),
        "entries": entries,
        "digest": _digest_record(
            entries, version="phase-f-prior-json-evidence-inventory.v1"
        ),
    }


def _tc_fingerprint(
    database: DatabaseTarget, *, client: Any
) -> dict[str, str]:
    from rehearse import _capture_database_fingerprint

    fingerprint = _capture_database_fingerprint(database, client=client)
    return _digest_record(fingerprint, version="postgresql-database-fingerprint.v1")


def _create_database(
    connection: psycopg.Connection[Any], *, name: str, comment: str
) -> None:
    existing = connection.execute(
        "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname=%s",
        (name,),
    ).fetchone()
    if existing is not None:
        raise SafetyError("refusing to reuse an existing disposable database")
    connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    connection.execute(
        sql.SQL("COMMENT ON DATABASE {} IS {}").format(
            sql.Identifier(name), sql.Literal(comment)
        )
    )


def _database_preflight(
    database: DatabaseTarget,
    *,
    expected_comment: str,
    require_empty: bool = False,
    expected_revision: str | None = None,
) -> dict[str, object]:
    with psycopg.connect(database.libpq_url) as connection:
        row = connection.execute(
            "SELECT current_database(), current_setting('server_version_num'), "
            "COALESCE(shobj_description(oid, 'pg_database'), '') "
            "FROM pg_database WHERE datname=current_database()"
        ).fetchone()
        if row is None or str(row[0]) != database.expected_name:
            raise SafetyError("disposable database identity preflight failed")
        if str(row[2]) != expected_comment:
            raise SafetyError("disposable database comment attestation mismatch")
        table_count_row = connection.execute(
            "SELECT count(*) FROM pg_tables WHERE schemaname='public'"
        ).fetchone()
        if table_count_row is None:
            raise SafetyError("could not count disposable database tables")
        table_count = int(table_count_row[0])
        if require_empty and table_count != 0:
            raise SafetyError("recovery database must start with zero public tables")
        revision_exists = connection.execute(
            "SELECT to_regclass('public.alembic_version') IS NOT NULL"
        ).fetchone()
        if revision_exists == (True,):
            revision_row = connection.execute(
                "SELECT version_num FROM alembic_version LIMIT 1"
            ).fetchone()
            revision = None if revision_row is None else revision_row[0]
        elif revision_exists == (False,):
            revision = None
        else:
            raise SafetyError("could not determine disposable database revision")
    if expected_revision is not None and revision != expected_revision:
        raise SafetyError("disposable database revision mismatch")
    return {
        "databaseName": database.expected_name,
        "serverVersionNum": int(row[1]),
        "databaseComment": expected_comment,
        "attested": True,
        "publicTableCount": table_count,
        "alembicRevision": revision,
    }


def _drop_database(
    connection: psycopg.Connection[Any], *, name: str, expected_comment: str
) -> None:
    row = connection.execute(
        "SELECT COALESCE(shobj_description(oid, 'pg_database'), '') "
        "FROM pg_database WHERE datname=%s",
        (name,),
    ).fetchone()
    if row is None or str(row[0]) != expected_comment:
        raise SafetyError("refusing to drop an absent or unattested database")
    connection.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname=%s AND pid<>pg_backend_pid()",
        (name,),
    )
    connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def _fixture_bytes(owner_type: str, mime_type: str, ordinal: int) -> bytes:
    label = f"{FIXTURE_VERSION}:{owner_type}:{mime_type}:{ordinal}"
    if mime_type == "application/json":
        return _canonical_bytes(
            {"fixture": FIXTURE_VERSION, "ownerType": owner_type, "ordinal": ordinal}
        )
    if mime_type == "image/png":
        return PNG_BYTES + b"\n" + label.encode("utf-8")
    return (label + "\n").encode("utf-8")


def _fixture(prefix: str) -> list[dict[str, object]]:
    owner_ids = {"ISSUE": ISSUE_ID, "EXECUTION_ATTEMPT": ATTEMPT_ID}
    extensions = {"application/json": "json", "image/png": "png", "text/plain": "txt"}
    objects: list[dict[str, object]] = []
    index = 0
    for owner_type in EXPECTED_OWNER_TYPES:
        for mime_type in FIXTURE_MIME_TYPES:
            for ordinal in (1, 2):
                index += 1
                content = _fixture_bytes(owner_type, mime_type, ordinal)
                token = owner_type.lower().replace("_", "-")
                mime_token = mime_type.replace("/", "-")
                key = (
                    f"{prefix}{token}/{mime_token}/{ordinal:02d}."
                    f"{extensions[mime_type]}"
                )
                objects.append(
                    {
                        "attachmentId": f"00000000-0000-7000-8100-{index:012d}",
                        "ownerType": owner_type,
                        "ownerId": owner_ids[owner_type],
                        "objectKey": key,
                        "fileName": f"wave7-{token}-{mime_token}-{ordinal:02d}.{extensions[mime_type]}",
                        "mimeType": mime_type,
                        "sizeBytes": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "content": content,
                        "createdAt": f"2026-09-04T00:01:{index:02d}Z",
                    }
                )
    keys = [str(item["objectKey"]) for item in objects]
    if len(keys) != len(set(keys)):
        raise SafetyError("deterministic fixture generated a duplicate object key")
    return objects


def _public_fixture(objects: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    return [
        {key: value for key, value in item.items() if key != "content"}
        for item in objects
    ]


def _put_object(
    adapter: S3CompatibleObjectStorage,
    *,
    key: str,
    content: bytes,
    mime_type: str,
    sha256: str,
) -> None:
    url, headers, _ = adapter.presign_put(
        key=key,
        content_type=mime_type,
        sha256=sha256,
        expires_seconds=60,
    )
    try:
        response = httpx.put(
            url,
            headers=headers,
            content=content,
            timeout=30,
            follow_redirects=False,
        )
    except httpx.HTTPError as error:
        raise SafetyError("local MinIO upload failed before a response") from error
    if response.is_redirect or not response.is_success:
        raise SafetyError(
            f"local MinIO upload failed with HTTP {response.status_code}"
        )


def _require_object_absent(adapter: S3CompatibleObjectStorage, key: str) -> None:
    try:
        adapter.head(key=key)
    except ObjectNotFound:
        return
    raise SafetyError("recovery copy refuses to overwrite an existing object")


def _anonymous_denial(endpoint: str, bucket: str, key: str) -> dict[str, object]:
    url = f"{endpoint}/{quote(bucket, safe='-_.~')}/{quote(key, safe='/-_.~')}"
    try:
        response = httpx.get(url, timeout=10, follow_redirects=False)
    except httpx.HTTPError as error:
        raise SafetyError("anonymous privacy probe failed before a response") from error
    if response.is_redirect or response.status_code not in {401, 403}:
        raise SafetyError("local recovery bucket did not deny anonymous object access")
    return {"denied": True, "httpStatus": response.status_code, "redirected": False}


def _object_inventory(
    adapter: S3CompatibleObjectStorage, *, page_limit: int = PAGE_LIMIT
) -> dict[str, object]:
    cursor: str | None = None
    seen_cursors: set[str] = set()
    listed: dict[str, int] = {}
    pages = 0
    while True:
        page = adapter.list_objects(prefix="", cursor=cursor, limit=page_limit)
        pages += 1
        if len(page.items) > page_limit:
            raise SafetyError("object-store pagination exceeded its requested limit")
        for item in page.items:
            if item.key in listed:
                raise SafetyError("object-store pagination returned a duplicate key")
            listed[item.key] = item.size_bytes
        next_cursor = page.next_cursor
        if next_cursor is None:
            break
        if not next_cursor or next_cursor in seen_cursors:
            raise SafetyError("object-store pagination returned a cursor cycle")
        if len(page.items) != page_limit:
            raise SafetyError("object-store pagination was incomplete before next cursor")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    entries: list[dict[str, object]] = []
    for key, listed_size in sorted(listed.items()):
        metadata = adapter.head(key=key)
        if metadata.size_bytes != listed_size:
            raise SafetyError("object-store LIST and HEAD sizes disagree")
        if metadata.sha256 is None or HEX_DIGEST_PATTERN.fullmatch(metadata.sha256) is None:
            raise SafetyError("object-store HEAD omitted canonical SHA-256 metadata")
        entries.append(
            {
                "objectKey": key,
                "sizeBytes": metadata.size_bytes,
                "mimeType": metadata.content_type,
                "sha256": metadata.sha256,
            }
        )
    return {
        "version": INVENTORY_VERSION,
        "count": len(entries),
        "pages": pages,
        "pageLimit": page_limit,
        "entries": entries,
        "digest": _digest_record(entries, version=INVENTORY_VERSION),
    }


def _assert_inventory_matches_fixture(
    inventory: Mapping[str, object], objects: Sequence[Mapping[str, object]]
) -> None:
    expected = [
        {
            "objectKey": item["objectKey"],
            "sizeBytes": item["sizeBytes"],
            "mimeType": item["mimeType"],
            "sha256": item["sha256"],
        }
        for item in objects
    ]
    expected.sort(key=lambda item: str(item["objectKey"]))
    if inventory.get("entries") != expected:
        raise SafetyError("object inventory differs from the deterministic fixture")


def _seed_source_database(
    database: DatabaseTarget, objects: Sequence[Mapping[str, object]]
) -> None:
    with psycopg.connect(database.libpq_url) as connection:
        connection.execute(RECOVERY_SEED_SQL)
        connection.execute(
            "UPDATE test_cycles SET status='IN_PROGRESS', resource_version=2, "
            "updated_at='2026-09-04T00:00:14Z' "
            "WHERE id='00000000-0000-7000-8000-000000000801'"
        )
        connection.execute(
            "INSERT INTO execution_attempts "
            "(id, cycle_item_id, platform, attempt_no, cycle_run_number, status, "
            "actual_result, note, executor_label, started_at, finished_at, created_at, updated_at) "
            "VALUES (%s, '00000000-0000-7000-8000-000000000811', 'WEB', 1, 1, "
            "'UNEXECUTED', NULL, "
            "'PF-SF1-T08 wave 7 fixture', 'fixture-owner-a', "
            "'2026-09-04T00:00:14Z', NULL, "
            "'2026-09-04T00:00:14Z', '2026-09-04T00:00:15Z')",
            (ATTEMPT_ID,),
        )
        connection.execute(
            "UPDATE execution_attempts SET status='PASS', "
            "actual_result='Deterministic recovery attempt passed', "
            "finished_at='2026-09-04T00:00:15Z', "
            "updated_at='2026-09-04T00:00:15Z' WHERE id=%s",
            (ATTEMPT_ID,),
        )
        for item in objects:
            connection.execute(
                "INSERT INTO attachments "
                "(id, workspace_id, owner_type, owner_id, drive_file_id, object_key, "
                "logical_path, file_name, checksum, mime_type, size_bytes, "
                "storage_provider, storage_status, deletion_status, reconcile_status, "
                "reconcile_detail, resource_version, deleted_at, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s, "
                "'R2', 'AVAILABLE', 'ACTIVE', 'CONSISTENT', NULL, 1, NULL, %s, %s)",
                (
                    item["attachmentId"],
                    WORKSPACE_ID,
                    item["ownerType"],
                    item["ownerId"],
                    item["objectKey"],
                    item["objectKey"],
                    item["fileName"],
                    item["sha256"],
                    item["mimeType"],
                    item["sizeBytes"],
                    item["createdAt"],
                    item["createdAt"],
                ),
            )


def _attachment_metadata(
    connection: psycopg.Connection[Any],
) -> dict[str, object]:
    rows = connection.execute(
        "SELECT id::text, owner_type, owner_id::text, object_key, file_name, "
        "mime_type, size_bytes, checksum, storage_provider, storage_status, "
        "deletion_status, reconcile_status, resource_version, "
        "to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') "
        "FROM attachments WHERE deleted_at IS NULL ORDER BY object_key, id"
    ).fetchall()
    records = [
        {
            "attachmentId": str(row[0]),
            "ownerType": str(row[1]),
            "ownerId": str(row[2]),
            "objectKey": str(row[3]),
            "fileName": str(row[4]),
            "mimeType": str(row[5]),
            "sizeBytes": int(row[6]),
            "sha256": str(row[7]),
            "storageProviderContract": str(row[8]),
            "storageStatus": str(row[9]),
            "deletionStatus": str(row[10]),
            "reconcileStatus": str(row[11]),
            "resourceVersion": int(row[12]),
            "createdAtUtc": str(row[13]),
        }
        for row in rows
    ]
    keys = [str(record["objectKey"]) for record in records]
    if len(keys) != len(set(keys)):
        raise SafetyError("attachment metadata contains duplicate object keys")
    return {
        "version": ATTACHMENT_METADATA_VERSION,
        "count": len(records),
        "records": records,
        "digest": _digest_record(records, version=ATTACHMENT_METADATA_VERSION),
    }


def _assert_metadata_matches_fixture(
    metadata: Mapping[str, object], objects: Sequence[Mapping[str, object]]
) -> None:
    records = metadata.get("records")
    if not isinstance(records, list) or len(records) != len(objects):
        raise SafetyError("attachment metadata population is incomplete")
    expected = {
        str(item["objectKey"]): item
        for item in objects
    }
    for record in records:
        if not isinstance(record, dict):
            raise SafetyError("attachment metadata record is invalid")
        item = expected.get(str(record.get("objectKey")))
        if item is None:
            raise SafetyError("attachment metadata contains an unexpected object key")
        for field in (
            "attachmentId",
            "ownerType",
            "ownerId",
            "fileName",
            "mimeType",
            "sizeBytes",
            "sha256",
        ):
            if record.get(field) != item.get(field):
                raise SafetyError(f"attachment metadata {field} differs from fixture")
        if (
            record.get("storageProviderContract") != DATABASE_PROVIDER_CONTRACT
            or record.get("storageStatus") != "AVAILABLE"
            or record.get("deletionStatus") != "ACTIVE"
            or record.get("reconcileStatus") != "CONSISTENT"
        ):
            raise SafetyError("attachment metadata policy state is not recoverable")


def _database_snapshot_and_dump(
    database: DatabaseTarget,
    *,
    dump_file: Path,
    client: Any,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    connection = _begin_read_only_snapshot(database)
    try:
        recovery_point = _snapshot_metadata(connection)
        metadata = _attachment_metadata(connection)
        fingerprint = {
            **_database_schema_fingerprint(connection),
            **_database_rows_fingerprint(connection),
        }
        command, environment = _postgresql_command(
            client,
            "pg_dump",
            database,
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            f"--snapshot={recovery_point['snapshotId']}",
        )
        _command_to_new_file(command, dump_file, env=environment)
    finally:
        connection.rollback()
        connection.close()
    return cast(dict[str, object], recovery_point), metadata, fingerprint


def _dump_contract(
    dump_file: Path, *, client: Any
) -> tuple[dict[str, object], dict[str, object]]:
    with PinnedReadFile(
        dump_file, label="wave-7 PostgreSQL dump", require_nonempty=True
    ) as source:
        sha256 = source.run_stage("SHA-256", lambda: _pinned_file_sha256(source))
        archive = _archive_listing(source, client=client, dump_sha256=sha256)
        contract = {
            "path": str(dump_file),
            "bytes": source.identity.size,
            "sha256": sha256,
            "fileIdentity": source.identity.evidence(),
            "archive": archive,
        }
        safety = source.safety_evidence()
    return contract, safety


def _restore_dump(
    database: DatabaseTarget,
    *,
    dump_file: Path,
    dump_contract: Mapping[str, object],
    client: Any,
) -> tuple[float, dict[str, object]]:
    with PinnedReadFile(
        dump_file, label="wave-7 PostgreSQL dump", require_nonempty=True
    ) as source:
        if source.identity.evidence() != dump_contract.get("fileIdentity"):
            raise SafetyError("dump identity no longer matches the manifest")
        actual_sha = source.run_stage("SHA-256", lambda: _pinned_file_sha256(source))
        if actual_sha != dump_contract.get("sha256"):
            raise SafetyError("dump SHA-256 no longer matches the manifest")
        archive = _archive_listing(source, client=client, dump_sha256=actual_sha)
        if archive != dump_contract.get("archive"):
            raise SafetyError("dump archive inventory no longer matches the manifest")
        command, environment = _postgresql_command(
            client,
            "pg_restore",
            database,
            "--exit-on-error",
            "--no-owner",
            "--no-privileges",
            "--dbname",
            database.expected_name,
        )
        started = time.perf_counter()
        source.run_stage(
            "pg_restore actual restore",
            lambda: _command_from_pinned_file(
                command, source, env=environment, capture=False
            ),
        )
        elapsed = time.perf_counter() - started
        safety = source.safety_evidence()
    return elapsed, safety


def _load_contract(
    path_value: str,
    *,
    expected_schema: str,
    label: str,
) -> tuple[Path, dict[str, Any], str, dict[str, object]]:
    path = _resolve_non_symlink_path(path_value)
    try:
        path.relative_to(EVIDENCE_ROOT.resolve())
    except ValueError as error:
        raise SafetyError(f"{label} must stay below the Phase F evidence root") from error
    if path.suffix != ".json":
        raise SafetyError(f"{label} must be a JSON file")
    with PinnedReadFile(
        path,
        label=label,
        maximum_size=8 * 1024 * 1024,
        require_nonempty=True,
    ) as source:
        raw = source.run_stage("SHA-256", source.read_bytes)
        file_sha = hashlib.sha256(raw).hexdigest()
        try:
            payload_value = source.run_stage(
                "JSON read/parse",
                lambda: json.load(
                    io.StringIO(raw.decode("utf-8")),
                    object_pairs_hook=_reject_duplicate_json_keys,
                ),
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SafetyError(f"{label} is not valid UTF-8 JSON") from error
        payload = _verify_contract(payload_value, expected_schema=expected_schema)
        safety = source.safety_evidence()
    return path, payload, file_sha, safety


def _safe_temp_directory(path_value: str, *, must_exist: bool) -> Path:
    path = _resolve_non_symlink_path(path_value)
    if not path.is_absolute() or path == ROOT or ROOT in path.parents:
        raise SafetyError("wave-7 temp directory must be absolute and outside the repository")
    if "pf-sf1-t08-wave7" not in path.name:
        raise SafetyError("wave-7 temp directory basename must contain pf-sf1-t08-wave7")
    if must_exist:
        metadata = os.lstat(path)
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise SafetyError("wave-7 temp directory must be a private mode-0700 directory")
    elif path.exists():
        raise SafetyError("refusing to reuse an existing wave-7 temp directory")
    elif not path.parent.is_dir():
        raise SafetyError("wave-7 temp directory parent must already exist")
    return path


def _directory_identity(path: Path) -> dict[str, int | str]:
    metadata = os.lstat(path)
    if not stat.S_ISDIR(metadata.st_mode):
        raise SafetyError("wave-7 temp path is no longer a directory")
    return {
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "modeOctal": f"{stat.S_IMODE(metadata.st_mode):04o}",
    }


def _base_plan(
    args: argparse.Namespace,
    target: Target,
    resources: Mapping[str, str],
    *,
    action: str,
) -> dict[str, object]:
    confirmation = _confirmation(action, target)
    mutates = (
        [
            f"create source database {resources['sourceDatabase']!r}",
            f"create recovery database {resources['recoveryDatabase']!r}",
            f"create source bucket {resources['sourceBucket']!r}",
            f"create recovery bucket {resources['recoveryBucket']!r}",
            f"write only under object prefix {resources['objectPrefix']!r}",
            f"create private temp directory {args.temp_directory!r}",
            f"create custom PostgreSQL dump {args.dump_file!r}",
            f"create immutable manifest {args.manifest_output!r}",
            f"create immutable recovery evidence {args.output!r}",
        ]
        if action == ACTION
        else []
    )
    return {
        "ok": True,
        "mode": "preview",
        "executes": False,
        "task": TASK,
        "action": action,
        "provider": LOCAL_PROVIDER,
        "target": {"name": target.name, "class": target.target_class},
        "confirmationRequired": confirmation,
        "resources": dict(resources),
        "mutates": mutates,
        "safety": {
            "loopbackOnly": True,
            "localComposeServicesOnly": ["postgres", "minio", "clamav"],
            "previewFirst": True,
            "nonOverwritingEvidence": True,
            "credentialsPrinted": False,
            "presignedUrlsRecorded": False,
            "productionOperations": "NOT_RUN",
        },
    }


def _copy_objects(
    source: S3CompatibleObjectStorage,
    recovery: S3CompatibleObjectStorage,
    objects: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    copied = 0
    started = time.perf_counter()
    for item in objects:
        key = str(item["objectKey"])
        _require_object_absent(recovery, key)
        content = b"".join(source.download(key=key))
        if len(content) != item["sizeBytes"]:
            raise SafetyError("source object size changed before recovery copy")
        if hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise SafetyError("source object bytes changed before recovery copy")
        _put_object(
            recovery,
            key=key,
            content=content,
            mime_type=str(item["mimeType"]),
            sha256=str(item["sha256"]),
        )
        copied += 1
    return {
        "copiedCount": copied,
        "overwriteAllowed": False,
        "localMeasuredSeconds": round(time.perf_counter() - started, 6),
    }


def _select_sample(
    objects: Sequence[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    selected: list[Mapping[str, object]] = []
    owner_coverage: set[str] = set()
    mime_coverage: set[str] = set()
    for item in sorted(objects, key=lambda value: str(value["objectKey"])):
        owner = str(item["ownerType"])
        mime = str(item["mimeType"])
        if owner not in owner_coverage or mime not in mime_coverage:
            selected.append(item)
            owner_coverage.add(owner)
            mime_coverage.add(mime)
    if owner_coverage != set(EXPECTED_OWNER_TYPES) or mime_coverage != set(
        FIXTURE_MIME_TYPES
    ):
        raise SafetyError("deterministic full-byte sample coverage is incomplete")
    if not selected:
        raise SafetyError("deterministic full-byte sample must not be empty")
    return selected


def _full_byte_sample(
    adapter: S3CompatibleObjectStorage,
    objects: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    selected = _select_sample(objects)
    items: list[dict[str, object]] = []
    for item in selected:
        key = str(item["objectKey"])
        content = b"".join(adapter.download(key=key))
        observed_sha = hashlib.sha256(content).hexdigest()
        if len(content) != item["sizeBytes"] or observed_sha != item["sha256"]:
            raise SafetyError("recovered full-byte sample did not match the manifest")
        items.append(
            {
                "objectKey": key,
                "ownerType": item["ownerType"],
                "mimeType": item["mimeType"],
                "sizeBytes": len(content),
                "sha256": observed_sha,
            }
        )
    return {
        "sampledCount": len(items),
        "ownerTypes": sorted({str(item["ownerType"]) for item in items}),
        "mimeTypes": sorted({str(item["mimeType"]) for item in items}),
        "items": items,
    }


def _api_validation(
    database: DatabaseTarget,
    *,
    adapter: S3CompatibleObjectStorage,
    scanner: ClamAVInstreamScanner,
    recovery_bucket: str,
    objects: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    runtime = ObjectStorageRuntime(
        adapter=adapter,
        scanner=scanner,
        signing_secret=b"pf-sf1-t08-wave7-local-signing",
        setup_error=None,
        provider=LOCAL_PROVIDER,
        bucket=recovery_bucket,
        allowed_mime_types=FIXTURE_MIME_TYPES,
        max_file_bytes=MAX_OBJECT_BYTES,
    )
    engine = create_engine(
        database.url,
        pool_pre_ping=True,
        connect_args={"options": "-c default_transaction_read_only=on"},
    )
    expected_by_key = {str(item["objectKey"]): item for item in objects}
    try:
        with TestClient(
            create_app(
                validate_database=False,
                database_engine=engine,
                object_storage_runtime=runtime,
            )
        ) as client:
            status_response = client.get("/api/integrations/object-storage/status")
            if status_response.status_code != 200:
                raise SafetyError("recovered API storage status request failed")
            status_payload = status_response.json()
            expected_status = {
                "status": "READY",
                "provider": LOCAL_PROVIDER,
                "bucket": recovery_bucket,
                "privacy": "PRIVATE",
                "scanPolicy": "FAIL_CLOSED_CLAMAV",
                "uploadTransport": "PRESIGNED_PUT",
                "downloadTransport": "BACKEND_PROXY",
                "legacyRowPolicy": "MIGRATION_REQUIRED",
                "legacyRowCount": 0,
            }
            for field, expected in expected_status.items():
                if status_payload.get(field) != expected:
                    raise SafetyError(f"recovered API status {field} mismatch")

            metadata_cursor: str | None = None
            seen_metadata_cursors: set[str] = set()
            metadata_keys: set[str] = set()
            metadata_totals = {
                "scanned": 0,
                "consistent": 0,
                "mismatch": 0,
                "failed": 0,
                "pages": 0,
            }
            while True:
                request: dict[str, object] = {"dryRun": True, "limit": PAGE_LIMIT}
                if metadata_cursor is not None:
                    request["cursor"] = metadata_cursor
                response = client.post(
                    "/api/integrations/object-storage/verify-metadata", json=request
                )
                if response.status_code != 200:
                    raise SafetyError("recovered API metadata verification failed")
                page = response.json()
                if page.get("dryRun") is not True:
                    raise SafetyError("recovered API metadata verification was not dry-run")
                items = page.get("items")
                scanned = page.get("scannedCount")
                if not isinstance(items, list) or scanned != len(items):
                    raise SafetyError("recovered API metadata page count is inconsistent")
                if len(items) > PAGE_LIMIT:
                    raise SafetyError("recovered API metadata page exceeded its limit")
                for item in items:
                    key = item.get("objectKey") if isinstance(item, dict) else None
                    if not isinstance(key, str) or key in metadata_keys:
                        raise SafetyError("recovered API metadata pagination duplicated a key")
                    if key not in expected_by_key or item.get("status") != "CONSISTENT":
                        raise SafetyError("recovered API metadata item mismatch")
                    metadata_keys.add(key)
                metadata_totals["pages"] += 1
                for target_key, response_key in (
                    ("scanned", "scannedCount"),
                    ("consistent", "consistentCount"),
                    ("mismatch", "mismatchCount"),
                    ("failed", "failedCount"),
                ):
                    value = page.get(response_key)
                    if type(value) is not int or value < 0:
                        raise SafetyError("recovered API metadata totals are invalid")
                    metadata_totals[target_key] += value
                next_cursor = page.get("nextCursor")
                if next_cursor is None:
                    break
                if (
                    not isinstance(next_cursor, str)
                    or not next_cursor
                    or next_cursor in seen_metadata_cursors
                    or len(items) != PAGE_LIMIT
                ):
                    raise SafetyError("recovered API metadata cursor is invalid")
                seen_metadata_cursors.add(next_cursor)
                metadata_cursor = next_cursor
            if (
                metadata_keys != set(expected_by_key)
                or metadata_totals["scanned"] != len(objects)
                or metadata_totals["consistent"] != len(objects)
                or metadata_totals["mismatch"] != 0
                or metadata_totals["failed"] != 0
                or metadata_totals["pages"] < 2
            ):
                raise SafetyError("recovered API metadata population or pagination is incomplete")

            attachment_cursor: str | None = None
            seen_attachment_cursors: set[str] = set()
            attachment_ids: set[str] = set()
            owner_types: set[str] = set()
            mime_types: set[str] = set()
            attachment_pages = 0
            while True:
                path = f"/api/attachments?limit={PAGE_LIMIT}"
                if attachment_cursor is not None:
                    path += "&cursor=" + quote(attachment_cursor, safe="")
                response = client.get(path)
                if response.status_code != 200:
                    raise SafetyError("recovered attachment API pagination failed")
                page = response.json()
                items = page.get("items")
                if not isinstance(items, list) or len(items) > PAGE_LIMIT:
                    raise SafetyError("recovered attachment API page is invalid")
                if page.get("total") != len(objects):
                    raise SafetyError("recovered attachment API total is incomplete")
                for item in items:
                    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                        raise SafetyError("recovered attachment API item is invalid")
                    if item["id"] in attachment_ids:
                        raise SafetyError("recovered attachment API duplicated an attachment")
                    if item.get("storageProvider") != DATABASE_PROVIDER_CONTRACT:
                        raise SafetyError("recovered attachment provider contract changed")
                    if item.get("scanStatus") != "CLEAN":
                        raise SafetyError("recovered attachment scan policy state changed")
                    owner = item.get("owner")
                    if not isinstance(owner, dict) or owner.get("type") not in EXPECTED_OWNER_TYPES:
                        raise SafetyError("recovered attachment owner is invalid")
                    attachment_ids.add(item["id"])
                    owner_types.add(str(owner["type"]))
                    mime_types.add(str(item.get("mimeType")))
                attachment_pages += 1
                next_cursor = page.get("nextCursor")
                if next_cursor is None:
                    break
                if (
                    not isinstance(next_cursor, str)
                    or not next_cursor
                    or next_cursor in seen_attachment_cursors
                    or len(items) != PAGE_LIMIT
                ):
                    raise SafetyError("recovered attachment API cursor is invalid")
                seen_attachment_cursors.add(next_cursor)
                attachment_cursor = next_cursor
            if (
                len(attachment_ids) != len(objects)
                or owner_types != set(EXPECTED_OWNER_TYPES)
                or mime_types != set(FIXTURE_MIME_TYPES)
                or attachment_pages < 2
            ):
                raise SafetyError("recovered attachment API coverage is incomplete")

            sample_response = client.post(
                "/api/integrations/object-storage/restore-sample",
                json={"sampleSize": len(objects), "maxBytesPerObject": MAX_OBJECT_BYTES},
            )
            if sample_response.status_code != 200:
                raise SafetyError("recovered API full-byte restore sample failed")
            sample = sample_response.json()
            sample_items = sample.get("items")
            if (
                not isinstance(sample_items, list)
                or sample.get("sampledCount") != len(objects)
                or sample.get("verifiedCount") != len(objects)
                or sample.get("failedCount") != 0
                or any(
                    not isinstance(item, dict) or item.get("status") != "VERIFIED"
                    for item in sample_items
                )
            ):
                raise SafetyError("recovered API full-byte sample was incomplete")
    finally:
        engine.dispose()
    return {
        "readOnlyDatabaseConnections": True,
        "status": expected_status,
        "metadataDryRun": metadata_totals,
        "attachmentPagination": {
            "count": len(attachment_ids),
            "pages": attachment_pages,
            "pageLimit": PAGE_LIMIT,
            "ownerTypes": sorted(owner_types),
            "mimeTypes": sorted(mime_types),
        },
        "restoreSample": {
            "sampled": len(objects),
            "verified": len(objects),
            "failed": 0,
        },
    }


def _manifest_resources(payload: Mapping[str, object]) -> dict[str, Any]:
    resources = payload.get("resources")
    if not isinstance(resources, dict):
        raise SafetyError("manifest resources are invalid")
    expected = {
        "sourceDatabase",
        "recoveryDatabase",
        "sourceBucket",
        "recoveryBucket",
        "objectPrefix",
        "dumpFile",
        "tempDirectory",
    }
    if set(resources) != expected:
        raise SafetyError("manifest resource set is invalid")
    return resources


def _run_recovery(args: argparse.Namespace) -> dict[str, object]:
    target = _target(args)
    resources = _validate_inputs(args)
    dump_file = _safe_dump_path(str(args.dump_file), must_exist=False)
    manifest_output = _safe_evidence_output(str(args.manifest_output))
    evidence_output = _safe_evidence_output(str(args.output))
    temp_directory = _safe_temp_directory(str(args.temp_directory), must_exist=False)
    if manifest_output is None or evidence_output is None:
        raise SafetyError("manifest and evidence outputs are required")
    if manifest_output == evidence_output:
        raise SafetyError("manifest and evidence outputs must be distinct")
    resources = {
        **resources,
        "dumpFile": str(dump_file),
        "tempDirectory": str(temp_directory),
    }
    plan = _base_plan(args, target, resources, action=ACTION)
    plan["steps"] = [
        "require source/recovery databases and buckets to be absent",
        "capture pre-existing database, tc database, and bucket preservation digests",
        "create and attest the source database/bucket, migrate, seed, HEAD, and ClamAV-scan fixtures",
        "capture one PostgreSQL snapshot/dump plus matched source object inventory",
        "write and pinned-read-verify an exclusive mode-0600 manifest",
        "create empty recovery database/bucket, restore without --clean, and copy without overwrite",
        "validate exact database/object digests plus read-only paginated API and full-byte samples",
        "write and pinned-read-verify exclusive mode-0600 recovery evidence",
        "retain exact resources until the separately previewed cleanup consumes durable evidence",
    ]
    plan["manifestFile"] = str(manifest_output.relative_to(ROOT))
    plan["evidenceFile"] = str(evidence_output.relative_to(ROOT))
    plan["limitations"] = [
        "Local timings are measurements only; no RTO or RPO objective is claimed.",
        "This is local MINIO evidence, not Cloudflare R2 evidence.",
        "Cloudflare R2 bucket backup/versioning, separate staging DB, operator handoff, deployment, and production objectives remain NOT_RUN.",
    ]
    if not args.execute:
        return plan
    if str(args.confirm or "") != str(plan["confirmationRequired"]):
        raise SafetyError(
            f"--execute requires --confirm {plan['confirmationRequired']!r}"
        )

    admin = _admin_database(args)
    source_database = _derived_database(admin, resources["sourceDatabase"])
    recovery_database = _derived_database(admin, resources["recoveryDatabase"])
    source_comment = _database_comment(resources["recoveryPoint"], "source")
    recovery_comment = _database_comment(resources["recoveryPoint"], "recovery")
    client = _resolve_postgresql_client(admin)
    source_adapter = _storage_adapter(
        args, endpoint=resources["endpoint"], bucket=resources["sourceBucket"]
    )
    recovery_adapter = _storage_adapter(
        args, endpoint=resources["endpoint"], bucket=resources["recoveryBucket"]
    )
    scanner = ClamAVInstreamScanner(
        host="127.0.0.1", port=int(args.clamav_port), timeout_seconds=30
    )
    objects = _fixture(resources["objectPrefix"])

    with psycopg.connect(admin.libpq_url, autocommit=True) as admin_connection:
        before_databases = _database_inventory(admin_connection)
        if not before_databases["tcPresent"]:
            raise SafetyError("local tc database must exist before the rehearsal")
        for name in (resources["sourceDatabase"], resources["recoveryDatabase"]):
            if admin_connection.execute(
                "SELECT 1 FROM pg_database WHERE datname=%s", (name,)
            ).fetchone() is not None:
                raise SafetyError("a declared disposable database already exists")
    before_tc_fingerprint = _tc_fingerprint(admin, client=client)
    before_buckets = _bucket_inventory(source_adapter)
    prior_evidence = _prior_evidence_inventory(
        exclude={manifest_output, evidence_output}
    )
    existing_buckets = set(source_adapter.list_buckets())
    if resources["sourceBucket"] in existing_buckets or resources["recoveryBucket"] in existing_buckets:
        raise SafetyError("a declared disposable bucket already exists")

    started_at = datetime.now(UTC)
    rehearsal_started = time.perf_counter()
    temp_directory.mkdir(mode=0o700)
    if stat.S_IMODE(os.lstat(temp_directory).st_mode) != 0o700:
        raise SafetyError("wave-7 temp directory was not created with mode 0700")
    temp_identity = _directory_identity(temp_directory)

    with psycopg.connect(admin.libpq_url, autocommit=True) as admin_connection:
        _create_database(
            admin_connection, name=resources["sourceDatabase"], comment=source_comment
        )
    source_adapter.create_bucket()
    if not source_adapter.bucket_exists():
        raise SafetyError("source bucket creation was not observable")
    if _object_inventory(source_adapter)["count"] != 0:
        raise SafetyError("source bucket must start empty")
    _database_preflight(
        source_database, expected_comment=source_comment, require_empty=True
    )
    _alembic(source_database, "upgrade", HEAD_REVISION)
    source_preflight = _database_preflight(
        source_database,
        expected_comment=source_comment,
        expected_revision=HEAD_REVISION,
    )

    scan_started = time.perf_counter()
    for item in objects:
        content = item["content"]
        if not isinstance(content, bytes):
            raise SafetyError("fixture content is not bytes")
        _put_object(
            source_adapter,
            key=str(item["objectKey"]),
            content=content,
            mime_type=str(item["mimeType"]),
            sha256=str(item["sha256"]),
        )
        expected_size = item["sizeBytes"]
        if type(expected_size) is not int:
            raise SafetyError("fixture size is not an integer")
        verified = verify_quarantined_object(
            source_adapter.download(key=str(item["objectKey"])),
            expected_mime_type=str(item["mimeType"]),
            expected_size_bytes=expected_size,
            expected_sha256=str(item["sha256"]),
            max_file_bytes=MAX_OBJECT_BYTES,
            scanner=scanner,
            temp_directory=temp_directory,
        )
        if verified.sha256 != item["sha256"]:
            raise SafetyError("source object scan verification returned a wrong digest")
    if any(temp_directory.iterdir()):
        raise SafetyError("ClamAV verification left a temp artifact behind")
    scan_seconds = time.perf_counter() - scan_started
    _seed_source_database(source_database, objects)

    recovery_point, source_metadata, source_fingerprint = _database_snapshot_and_dump(
        source_database, dump_file=dump_file, client=client
    )
    _assert_metadata_matches_fixture(source_metadata, objects)
    source_inventory = _object_inventory(source_adapter)
    _assert_inventory_matches_fixture(source_inventory, objects)
    source_privacy = _anonymous_denial(
        resources["endpoint"], resources["sourceBucket"], str(objects[0]["objectKey"])
    )
    dump_contract, dump_safety = _dump_contract(dump_file, client=client)
    manifest_payload = _seal_contract(
        {
            "schemaVersion": MANIFEST_SCHEMA,
            "task": TASK,
            "mode": "LOCAL_MINIO_DISPOSABLE",
            "provider": LOCAL_PROVIDER,
            "databaseProviderContract": DATABASE_PROVIDER_CONTRACT,
            "target": plan["target"],
            "manifestFile": str(manifest_output.relative_to(ROOT)),
            "recoveryPoint": {
                "id": resources["recoveryPoint"],
                **recovery_point,
                "productionRpoPromise": "NOT_CLAIMED",
            },
            "resources": {
                "sourceDatabase": {
                    "name": resources["sourceDatabase"], "comment": source_comment
                },
                "recoveryDatabase": {
                    "name": resources["recoveryDatabase"], "comment": recovery_comment
                },
                "sourceBucket": resources["sourceBucket"],
                "recoveryBucket": resources["recoveryBucket"],
                "objectPrefix": resources["objectPrefix"],
                "dumpFile": str(dump_file),
                "tempDirectory": {
                    "path": str(temp_directory), "identity": temp_identity
                },
            },
            "fixture": {
                "version": FIXTURE_VERSION,
                "deterministic": True,
                "sensitiveData": False,
                "ownerTypes": list(EXPECTED_OWNER_TYPES),
                "mimeTypes": list(FIXTURE_MIME_TYPES),
                "objectsPerOwnerMimePair": 2,
                "objectCount": len(objects),
                "objects": _public_fixture(objects),
            },
            "source": {
                "databasePreflight": source_preflight,
                "attachmentMetadata": source_metadata,
                "databaseFingerprint": source_fingerprint,
                "databaseFingerprintDigest": _digest_record(
                    source_fingerprint, version="postgresql-database-fingerprint.v1"
                ),
                "bucketInventory": source_inventory,
                "privacy": source_privacy,
            },
            "dump": {**dump_contract, "fileSafety": dump_safety},
            "preservation": {
                "beforeDatabaseInventory": before_databases,
                "beforeTcDatabaseFingerprint": before_tc_fingerprint,
                "beforeBucketInventory": before_buckets,
                "priorJsonEvidenceInventory": prior_evidence,
            },
            "limitations": plan["limitations"],
        }
    )
    _write_json_evidence(manifest_output, manifest_payload)
    (
        loaded_manifest_path,
        loaded_manifest,
        manifest_file_sha,
        manifest_file_safety,
    ) = _load_contract(
        str(manifest_output), expected_schema=MANIFEST_SCHEMA, label="wave-7 manifest"
    )
    if loaded_manifest != manifest_payload:
        raise SafetyError("written manifest did not round-trip exactly")

    with psycopg.connect(admin.libpq_url, autocommit=True) as admin_connection:
        _create_database(
            admin_connection,
            name=resources["recoveryDatabase"],
            comment=recovery_comment,
        )
    recovery_adapter.create_bucket()
    if not recovery_adapter.bucket_exists():
        raise SafetyError("recovery bucket creation was not observable")
    recovery_empty_database = _database_preflight(
        recovery_database, expected_comment=recovery_comment, require_empty=True
    )
    recovery_empty_bucket = _object_inventory(recovery_adapter)
    if recovery_empty_bucket["count"] != 0:
        raise SafetyError("recovery bucket must start empty")

    restore_seconds, restored_dump_safety = _restore_dump(
        recovery_database,
        dump_file=dump_file,
        dump_contract=dump_contract,
        client=client,
    )
    copy_result = _copy_objects(source_adapter, recovery_adapter, objects)
    recovery_preflight = _database_preflight(
        recovery_database,
        expected_comment=recovery_comment,
        expected_revision=HEAD_REVISION,
    )
    with psycopg.connect(recovery_database.libpq_url) as connection:
        connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        restored_metadata = _attachment_metadata(connection)
        restored_fingerprint = {
            **_database_schema_fingerprint(connection),
            **_database_rows_fingerprint(connection),
        }
        connection.rollback()
    _assert_metadata_matches_fixture(restored_metadata, objects)
    if restored_fingerprint != source_fingerprint:
        raise SafetyError("restored PostgreSQL fingerprint differs from source")
    recovered_inventory = _object_inventory(recovery_adapter)
    _assert_inventory_matches_fixture(recovered_inventory, objects)
    if recovered_inventory["digest"] != source_inventory["digest"]:
        raise SafetyError("recovered bucket inventory digest differs from source")
    recovery_privacy = _anonymous_denial(
        resources["endpoint"], resources["recoveryBucket"], str(objects[0]["objectKey"])
    )
    full_byte_sample = _full_byte_sample(recovery_adapter, objects)
    before_api_fingerprint = _digest_record(
        restored_fingerprint, version="postgresql-database-fingerprint.v1"
    )
    api = _api_validation(
        recovery_database,
        adapter=recovery_adapter,
        scanner=scanner,
        recovery_bucket=resources["recoveryBucket"],
        objects=objects,
    )
    from rehearse import _capture_database_fingerprint

    after_api_fingerprint_value = _capture_database_fingerprint(
        recovery_database, client=client
    )
    after_api_fingerprint = _digest_record(
        after_api_fingerprint_value, version="postgresql-database-fingerprint.v1"
    )
    if after_api_fingerprint != before_api_fingerprint:
        raise SafetyError("read-only recovery API validation mutated the database")
    if any(temp_directory.iterdir()):
        raise SafetyError("recovery validation left a temp artifact behind")

    source_metadata_digest = source_metadata["digest"]
    restored_metadata_digest = restored_metadata["digest"]
    source_inventory_digest = source_inventory["digest"]
    recovered_inventory_digest = recovered_inventory["digest"]
    bindings = {
        "version": BINDING_VERSION,
        "manifestFileSha256": manifest_file_sha,
        "manifestContractDigest": manifest_payload["contractDigest"],
        "sourceDatabaseMetadataDigest": source_metadata_digest,
        "sourceBucketInventoryDigest": source_inventory_digest,
        "restoredDatabaseMetadataDigest": restored_metadata_digest,
        "recoveredBucketInventoryDigest": recovered_inventory_digest,
        "databaseMetadataEqual": source_metadata_digest == restored_metadata_digest,
        "bucketInventoryEqual": source_inventory_digest == recovered_inventory_digest,
        "databaseFingerprintEqual": source_fingerprint == restored_fingerprint,
    }
    if not all(
        bindings[key]
        for key in (
            "databaseMetadataEqual",
            "bucketInventoryEqual",
            "databaseFingerprintEqual",
        )
    ):
        raise SafetyError("recovery binding comparison failed")
    finished_at = datetime.now(UTC)
    evidence_payload = _seal_contract(
        {
            "schemaVersion": EVIDENCE_SCHEMA,
            "task": TASK,
            "action": ACTION,
            "mode": "LOCAL_MINIO_DISPOSABLE_PASS",
            "provider": LOCAL_PROVIDER,
            "databaseProviderContract": DATABASE_PROVIDER_CONTRACT,
            "target": plan["target"],
            "confirmationRequired": plan["confirmationRequired"],
            "evidenceFile": str(evidence_output.relative_to(ROOT)),
            "manifest": {
                "path": str(loaded_manifest_path.relative_to(ROOT)),
                "fileSha256": manifest_file_sha,
                "contractDigest": manifest_payload["contractDigest"],
                "fileSafety": manifest_file_safety,
            },
            "resources": manifest_payload["resources"],
            "fixture": manifest_payload["fixture"],
            "source": manifest_payload["source"],
            "recovery": {
                "emptyDatabasePreflight": recovery_empty_database,
                "emptyBucketInventory": recovery_empty_bucket,
                "postflight": recovery_preflight,
                "attachmentMetadata": restored_metadata,
                "databaseFingerprint": restored_fingerprint,
                "databaseFingerprintDigest": after_api_fingerprint,
                "bucketInventory": recovered_inventory,
                "privacy": recovery_privacy,
                "copy": copy_result,
                "api": api,
                "fullByteSample": full_byte_sample,
                "readOnlyApiFingerprintPreserved": True,
            },
            "bindings": bindings,
            "dump": {
                **dump_contract,
                "sourceFileSafety": dump_safety,
                "restoredFileSafety": restored_dump_safety,
            },
            "preservation": manifest_payload["preservation"],
            "cleanup": {
                "required": True,
                "performed": False,
                "requiresDurableEvidenceVerification": True,
            },
            "timing": {
                "startedAtUtc": started_at.isoformat(),
                "finishedAtUtc": finished_at.isoformat(),
                "fixtureClamavScanLocalMeasuredSeconds": round(scan_seconds, 6),
                "postgresRestoreLocalMeasuredSeconds": round(restore_seconds, 6),
                "rehearsalLocalMeasuredSeconds": round(
                    time.perf_counter() - rehearsal_started, 6
                ),
                "productionRtoPromise": "NOT_CLAIMED",
                "productionRpoPromise": "NOT_CLAIMED",
                "recoveryPointAgeLocalMeasuredSeconds": _recovery_point_age_seconds(
                    str(recovery_point["capturedAtUtc"])
                ),
            },
            "safety": plan["safety"],
            "limitations": plan["limitations"],
        }
    )
    _write_json_evidence(evidence_output, evidence_payload)
    _, loaded_evidence, evidence_sha, evidence_file_safety = _load_contract(
        str(evidence_output),
        expected_schema=EVIDENCE_SCHEMA,
        label="wave-7 recovery evidence",
    )
    if loaded_evidence != evidence_payload:
        raise SafetyError("written recovery evidence did not round-trip exactly")
    return {
        **evidence_payload,
        "durableEvidenceVerification": {
            "verified": True,
            "fileSha256": evidence_sha,
            "fileSafety": evidence_file_safety,
        },
    }


def _require_digest_record(value: object, *, version: str, label: str) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "sha256"}
        or value.get("version") != version
        or not isinstance(value.get("sha256"), str)
        or HEX_DIGEST_PATTERN.fullmatch(str(value["sha256"])) is None
    ):
        raise SafetyError(f"{label} digest record is invalid")


def _validate_recovery_evidence(
    payload: Mapping[str, object], target: Target
) -> tuple[dict[str, Any], dict[str, Any]]:
    if (
        payload.get("task") != TASK
        or payload.get("action") != ACTION
        or payload.get("mode") != "LOCAL_MINIO_DISPOSABLE_PASS"
        or payload.get("provider") != LOCAL_PROVIDER
        or payload.get("databaseProviderContract") != DATABASE_PROVIDER_CONTRACT
        or payload.get("target")
        != {"name": target.name, "class": target.target_class}
    ):
        raise SafetyError("recovery evidence does not match the cleanup target")
    resources = _manifest_resources(payload)
    source_database = resources.get("sourceDatabase")
    recovery_database = resources.get("recoveryDatabase")
    if not isinstance(source_database, dict) or not isinstance(recovery_database, dict):
        raise SafetyError("recovery evidence database resources are invalid")
    _validate_database_name(str(source_database.get("name")), role="source")
    _validate_database_name(str(recovery_database.get("name")), role="recovery")
    _validate_bucket_name(str(resources.get("sourceBucket")), role="source")
    _validate_bucket_name(str(resources.get("recoveryBucket")), role="recovery")
    _validate_prefix(str(resources.get("objectPrefix")))
    fixture = payload.get("fixture")
    if not isinstance(fixture, dict):
        raise SafetyError("recovery evidence fixture is invalid")
    objects = fixture.get("objects")
    if (
        fixture.get("version") != FIXTURE_VERSION
        or fixture.get("deterministic") is not True
        or fixture.get("sensitiveData") is not False
        or fixture.get("ownerTypes") != list(EXPECTED_OWNER_TYPES)
        or fixture.get("mimeTypes") != list(FIXTURE_MIME_TYPES)
        or fixture.get("objectsPerOwnerMimePair") != 2
        or fixture.get("objectCount") != 12
        or not isinstance(objects, list)
        or len(objects) != 12
    ):
        raise SafetyError("recovery evidence fixture coverage is invalid")
    keys = [item.get("objectKey") for item in objects if isinstance(item, dict)]
    if len(keys) != 12 or len(set(keys)) != 12:
        raise SafetyError("recovery evidence fixture contains duplicate keys")
    bindings = payload.get("bindings")
    if not isinstance(bindings, dict) or bindings.get("version") != BINDING_VERSION:
        raise SafetyError("recovery evidence binding is invalid")
    for label in (
        "sourceDatabaseMetadataDigest",
        "sourceBucketInventoryDigest",
        "restoredDatabaseMetadataDigest",
        "recoveredBucketInventoryDigest",
    ):
        version = (
            ATTACHMENT_METADATA_VERSION if "Database" in label else INVENTORY_VERSION
        )
        _require_digest_record(bindings.get(label), version=version, label=label)
    if not all(
        bindings.get(key) is True
        for key in (
            "databaseMetadataEqual",
            "bucketInventoryEqual",
            "databaseFingerprintEqual",
        )
    ):
        raise SafetyError("recovery evidence does not attest exact binding equality")
    source = payload.get("source")
    recovery = payload.get("recovery")
    if not isinstance(source, dict) or not isinstance(recovery, dict):
        raise SafetyError("recovery evidence source/destination state is invalid")
    if source.get("attachmentMetadata", {}).get("digest") != bindings.get(
        "sourceDatabaseMetadataDigest"
    ):
        raise SafetyError("source metadata binding was tampered")
    if recovery.get("attachmentMetadata", {}).get("digest") != bindings.get(
        "restoredDatabaseMetadataDigest"
    ):
        raise SafetyError("restored metadata binding was tampered")
    if source.get("bucketInventory", {}).get("digest") != bindings.get(
        "sourceBucketInventoryDigest"
    ):
        raise SafetyError("source bucket binding was tampered")
    if recovery.get("bucketInventory", {}).get("digest") != bindings.get(
        "recoveredBucketInventoryDigest"
    ):
        raise SafetyError("recovered bucket binding was tampered")
    cleanup = payload.get("cleanup")
    if not isinstance(cleanup, dict) or cleanup.get("performed") is not False:
        raise SafetyError("recovery evidence cleanup state is invalid")
    return resources, fixture


def _delete_attested_bucket(
    adapter: S3CompatibleObjectStorage,
    *,
    expected_inventory: Mapping[str, object],
) -> int:
    observed = _object_inventory(adapter)
    if observed != expected_inventory:
        raise SafetyError("refusing cleanup because bucket inventory changed")
    entries = observed.get("entries")
    if not isinstance(entries, list):
        raise SafetyError("attested bucket inventory entries are invalid")
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("objectKey"), str):
            raise SafetyError("attested bucket cleanup key is invalid")
        adapter.delete(key=entry["objectKey"])
    empty = _object_inventory(adapter)
    if empty["count"] != 0:
        raise SafetyError("attested bucket did not become empty during cleanup")
    adapter.delete_bucket()
    if adapter.bucket_exists():
        raise SafetyError("attested bucket still exists after cleanup")
    return len(entries)


def _unlink_attested_dump(path: Path, contract: Mapping[str, object]) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise SafetyError("dump cleanup requires O_NOFOLLOW support")
    descriptor = os.open(path, os.O_RDONLY | nofollow)
    try:
        identity = FileIdentity.from_stat(os.fstat(descriptor))
        if (
            not stat.S_ISREG(identity.mode)
            or stat.S_IMODE(identity.mode) & ~0o600
            or identity.link_count != 1
            or identity.evidence() != contract.get("fileIdentity")
        ):
            raise SafetyError("refusing cleanup because dump identity changed")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        if digest.hexdigest() != contract.get("sha256"):
            raise SafetyError("refusing cleanup because dump SHA-256 changed")
        current = FileIdentity.from_stat(os.lstat(path))
        if current != identity:
            raise SafetyError("refusing cleanup because dump pathname changed")
        path.unlink()
        if path.exists():
            raise SafetyError("attested dump still exists after cleanup")
        return identity.size
    finally:
        os.close(descriptor)


def _remove_attested_temp_directory(
    value: Mapping[str, object], *, recovery_point: str
) -> None:
    path_value = value.get("path")
    identity = value.get("identity")
    if not isinstance(path_value, str) or not isinstance(identity, dict):
        raise SafetyError("temp directory cleanup contract is invalid")
    path = _safe_temp_directory(path_value, must_exist=True)
    if recovery_point not in path.name or _directory_identity(path) != identity:
        raise SafetyError("refusing cleanup because temp directory identity changed")
    if any(path.iterdir()):
        raise SafetyError("refusing cleanup because temp directory is not empty")
    path.rmdir()
    if path.exists():
        raise SafetyError("attested temp directory still exists after cleanup")


def _run_cleanup(args: argparse.Namespace) -> dict[str, object]:
    target = _target(args)
    evidence_path, evidence, evidence_sha, evidence_file_safety = _load_contract(
        str(args.recovery_evidence),
        expected_schema=EVIDENCE_SCHEMA,
        label="wave-7 recovery evidence",
    )
    resources, fixture = _validate_recovery_evidence(evidence, target)
    source_database = resources["sourceDatabase"]
    recovery_database = resources["recoveryDatabase"]
    if not isinstance(source_database, dict) or not isinstance(recovery_database, dict):
        raise SafetyError("recovery evidence database resources are invalid")
    plan_resources = {
        "sourceDatabase": str(source_database["name"]),
        "recoveryDatabase": str(recovery_database["name"]),
        "sourceBucket": str(resources["sourceBucket"]),
        "recoveryBucket": str(resources["recoveryBucket"]),
        "objectPrefix": str(resources["objectPrefix"]),
        "dumpFile": str(resources["dumpFile"]),
        "tempDirectory": str(resources["tempDirectory"]["path"]),
        "recoveryEvidence": str(evidence_path.relative_to(ROOT)),
    }
    output = _safe_evidence_output(str(args.output))
    if output is None:
        raise SafetyError("cleanup evidence output is required")
    plan = _base_plan(args, target, plan_resources, action=CLEANUP_ACTION)
    plan["mutates"] = [
        f"delete only manifest-attested objects then bucket {plan_resources['sourceBucket']!r}",
        f"delete only manifest-attested objects then bucket {plan_resources['recoveryBucket']!r}",
        f"drop only exact-comment database {plan_resources['sourceDatabase']!r}",
        f"drop only exact-comment database {plan_resources['recoveryDatabase']!r}",
        f"unlink only exact-identity dump {plan_resources['dumpFile']!r}",
        f"remove only exact-identity empty temp directory {plan_resources['tempDirectory']!r}",
        f"create immutable cleanup evidence {str(output)!r}",
    ]
    plan["steps"] = [
        "pinned-read recovery evidence and matched manifest before mutation",
        "require exact database comments and exact source/recovery bucket inventories",
        "delete only enumerated objects; require empty before exact bucket deletion",
        "drop only the two exact attested disposable databases",
        "remove only the exact pinned dump and empty attested temp directory",
        "prove pre-existing database/bucket inventories and tc database fingerprint are unchanged",
        "re-read prior contracts and write/verify exclusive mode-0600 cleanup evidence",
    ]
    plan["cleanupEvidenceFile"] = str(output.relative_to(ROOT))
    if not args.execute:
        return plan
    if str(args.confirm or "") != str(plan["confirmationRequired"]):
        raise SafetyError(
            f"--execute requires --confirm {plan['confirmationRequired']!r}"
        )

    manifest_value = evidence.get("manifest")
    if not isinstance(manifest_value, dict) or not isinstance(
        manifest_value.get("path"), str
    ):
        raise SafetyError("recovery evidence manifest binding is invalid")
    manifest_path, manifest, manifest_sha, manifest_file_safety = _load_contract(
        str(ROOT / manifest_value["path"]),
        expected_schema=MANIFEST_SCHEMA,
        label="wave-7 manifest",
    )
    if (
        manifest_sha != manifest_value.get("fileSha256")
        or manifest.get("contractDigest") != manifest_value.get("contractDigest")
        or manifest.get("resources") != resources
        or manifest.get("fixture") != fixture
    ):
        raise SafetyError("recovery evidence no longer matches the manifest")

    admin = _admin_database(args)
    client = _resolve_postgresql_client(admin)
    source_adapter = _storage_adapter(
        args,
        endpoint=_validate_endpoint(str(args.endpoint_url)),
        bucket=plan_resources["sourceBucket"],
    )
    recovery_adapter = _storage_adapter(
        args,
        endpoint=_validate_endpoint(str(args.endpoint_url)),
        bucket=plan_resources["recoveryBucket"],
    )
    source_state_value = evidence.get("source")
    recovery_state_value = evidence.get("recovery")
    dump_contract_value = evidence.get("dump")
    preservation_value = evidence.get("preservation")
    if not all(
        isinstance(value, dict)
        for value in (
            source_state_value,
            recovery_state_value,
            dump_contract_value,
            preservation_value,
        )
    ):
        raise SafetyError("recovery cleanup contract state is invalid")
    source_state = cast(dict[str, Any], source_state_value)
    recovery_state = cast(dict[str, Any], recovery_state_value)
    dump_contract = cast(dict[str, object], dump_contract_value)
    preservation = cast(dict[str, Any], preservation_value)
    source_database_target = _derived_database(admin, plan_resources["sourceDatabase"])
    recovery_database_target = _derived_database(
        admin, plan_resources["recoveryDatabase"]
    )
    _database_preflight(
        source_database_target,
        expected_comment=str(source_database["comment"]),
        expected_revision=HEAD_REVISION,
    )
    _database_preflight(
        recovery_database_target,
        expected_comment=str(recovery_database["comment"]),
        expected_revision=HEAD_REVISION,
    )
    source_expected_inventory = source_state.get("bucketInventory")
    recovery_expected_inventory = recovery_state.get("bucketInventory")
    if not isinstance(source_expected_inventory, dict) or not isinstance(
        recovery_expected_inventory, dict
    ):
        raise SafetyError("attested cleanup bucket inventories are invalid")
    if not source_adapter.bucket_exists() or not recovery_adapter.bucket_exists():
        raise SafetyError("an attested disposable cleanup bucket is absent")

    deleted_source_objects = _delete_attested_bucket(
        source_adapter, expected_inventory=source_expected_inventory
    )
    deleted_recovery_objects = _delete_attested_bucket(
        recovery_adapter, expected_inventory=recovery_expected_inventory
    )
    with psycopg.connect(admin.libpq_url, autocommit=True) as admin_connection:
        _drop_database(
            admin_connection,
            name=plan_resources["sourceDatabase"],
            expected_comment=str(source_database["comment"]),
        )
        _drop_database(
            admin_connection,
            name=plan_resources["recoveryDatabase"],
            expected_comment=str(recovery_database["comment"]),
        )
    dump_file = _safe_dump_path(plan_resources["dumpFile"], must_exist=True)
    removed_dump_bytes = _unlink_attested_dump(dump_file, dump_contract)
    recovery_point_value = manifest.get("recoveryPoint")
    if not isinstance(recovery_point_value, dict) or not isinstance(
        recovery_point_value.get("id"), str
    ):
        raise SafetyError("manifest recovery point is invalid")
    temp_value = resources.get("tempDirectory")
    if not isinstance(temp_value, dict):
        raise SafetyError("manifest temp directory is invalid")
    _remove_attested_temp_directory(
        temp_value, recovery_point=str(recovery_point_value["id"])
    )

    with psycopg.connect(admin.libpq_url, autocommit=True) as admin_connection:
        after_databases = _database_inventory(admin_connection)
        residual = [
            name
            for name in (
                plan_resources["sourceDatabase"],
                plan_resources["recoveryDatabase"],
            )
            if admin_connection.execute(
                "SELECT 1 FROM pg_database WHERE datname=%s", (name,)
            ).fetchone()
            is not None
        ]
    after_buckets = _bucket_inventory(source_adapter)
    after_tc_fingerprint = _tc_fingerprint(admin, client=client)
    after_prior_evidence = _prior_evidence_inventory(
        exclude={manifest_path, evidence_path, output}
    )
    if residual:
        raise SafetyError("disposable databases remain after cleanup")
    if source_adapter.bucket_exists() or recovery_adapter.bucket_exists():
        raise SafetyError("disposable buckets remain after cleanup")
    if dump_file.exists() or Path(plan_resources["tempDirectory"]).exists():
        raise SafetyError("disposable local file artifacts remain after cleanup")
    if (
        after_databases != preservation.get("beforeDatabaseInventory")
        or after_buckets != preservation.get("beforeBucketInventory")
        or after_tc_fingerprint != preservation.get("beforeTcDatabaseFingerprint")
        or after_prior_evidence != preservation.get("priorJsonEvidenceInventory")
    ):
        raise SafetyError("pre-existing PostgreSQL/MinIO state was not preserved")

    _, evidence_after, evidence_sha_after, _ = _load_contract(
        str(evidence_path),
        expected_schema=EVIDENCE_SCHEMA,
        label="wave-7 recovery evidence",
    )
    _, manifest_after, manifest_sha_after, _ = _load_contract(
        str(manifest_path), expected_schema=MANIFEST_SCHEMA, label="wave-7 manifest"
    )
    if (
        evidence_after != evidence
        or evidence_sha_after != evidence_sha
        or manifest_after != manifest
        or manifest_sha_after != manifest_sha
    ):
        raise SafetyError("prior durable wave-7 evidence changed during cleanup")

    cleanup_payload = _seal_contract(
        {
            "schemaVersion": CLEANUP_SCHEMA,
            "task": TASK,
            "action": CLEANUP_ACTION,
            "mode": "LOCAL_MINIO_DISPOSABLE_CLEANUP_PASS",
            "provider": LOCAL_PROVIDER,
            "target": plan["target"],
            "confirmationRequired": plan["confirmationRequired"],
            "cleanupEvidenceFile": str(output.relative_to(ROOT)),
            "consumedEvidence": {
                "path": str(evidence_path.relative_to(ROOT)),
                "fileSha256": evidence_sha,
                "fileSafety": evidence_file_safety,
                "contractDigest": evidence["contractDigest"],
            },
            "manifest": {
                "path": str(manifest_path.relative_to(ROOT)),
                "fileSha256": manifest_sha,
                "fileSafety": manifest_file_safety,
                "contractDigest": manifest["contractDigest"],
            },
            "removed": {
                "sourceObjects": deleted_source_objects,
                "recoveryObjects": deleted_recovery_objects,
                "sourceDatabase": True,
                "recoveryDatabase": True,
                "sourceBucket": True,
                "recoveryBucket": True,
                "dumpBytes": removed_dump_bytes,
                "tempDirectory": True,
            },
            "absence": {
                "disposableDatabases": True,
                "disposableBuckets": True,
                "dumpFile": True,
                "tempDirectory": True,
            },
            "preservation": {
                "databaseInventoryEqual": True,
                "bucketInventoryEqual": True,
                "tcDatabaseFingerprintEqual": True,
                "beforeDatabaseInventory": preservation["beforeDatabaseInventory"],
                "afterDatabaseInventory": after_databases,
                "beforeBucketInventory": preservation["beforeBucketInventory"],
                "afterBucketInventory": after_buckets,
                "beforeTcDatabaseFingerprint": preservation[
                    "beforeTcDatabaseFingerprint"
                ],
                "afterTcDatabaseFingerprint": after_tc_fingerprint,
                "priorJsonEvidenceInventory": after_prior_evidence,
            },
            "priorEvidenceHashesPreserved": True,
            "productionOperations": "NOT_RUN",
        }
    )
    _write_json_evidence(output, cleanup_payload)
    _, loaded_cleanup, cleanup_sha, cleanup_safety = _load_contract(
        str(output), expected_schema=CLEANUP_SCHEMA, label="wave-7 cleanup evidence"
    )
    if loaded_cleanup != cleanup_payload:
        raise SafetyError("written cleanup evidence did not round-trip exactly")
    return {
        **cleanup_payload,
        "durableEvidenceVerification": {
            "verified": True,
            "fileSha256": cleanup_sha,
            "fileSafety": cleanup_safety,
        },
    }


def _add_connection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--admin-database-url-env", required=True)
    parser.add_argument("--endpoint-url", required=True)
    parser.add_argument("--minio-access-key-env", required=True)
    parser.add_argument("--minio-secret-key-env", required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preview or execute the local disposable wave-7 object recovery drill."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    recover = commands.add_parser("recover")
    recover.add_argument("--target-name", required=True)
    recover.add_argument("--target-class", required=True, choices=("disposable",))
    recover.add_argument("--recovery-point", required=True)
    recover.add_argument("--source-database-name", required=True)
    recover.add_argument("--recovery-database-name", required=True)
    recover.add_argument("--source-bucket", required=True)
    recover.add_argument("--recovery-bucket", required=True)
    recover.add_argument("--object-prefix", required=True)
    recover.add_argument("--dump-file", required=True)
    recover.add_argument("--temp-directory", required=True)
    recover.add_argument("--manifest-output", required=True)
    recover.add_argument("--output", required=True)
    recover.add_argument("--clamav-port", type=int, default=53310)
    _add_connection_arguments(recover)
    recover.add_argument("--execute", action="store_true")
    recover.add_argument("--confirm")

    cleanup = commands.add_parser("cleanup")
    cleanup.add_argument("--target-name", required=True)
    cleanup.add_argument("--target-class", required=True, choices=("disposable",))
    cleanup.add_argument("--recovery-evidence", required=True)
    cleanup.add_argument("--output", required=True)
    _add_connection_arguments(cleanup)
    cleanup.add_argument("--execute", action="store_true")
    cleanup.add_argument("--confirm")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = _run_recovery(args) if args.command == "recover" else _run_cleanup(args)
    except (
        SafetyError,
        ObjectStorageError,
        psycopg.Error,
        subprocess.CalledProcessError,
        OSError,
    ) as error:
        detail = str(error) if isinstance(error, SafetyError) else error.__class__.__name__
        print(json.dumps({"ok": False, "error": detail}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
