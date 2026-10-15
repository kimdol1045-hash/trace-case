"""PF-SF1-T09 wave-8 local disposable full-reindex rehearsal.

Preview is the default.  Execute creates only generated, loopback-bound
``pf-sf1-t09-wave8`` Docker resources, restores a read-only copy of the local
``tc`` database, adds a deterministic non-sensitive corpus to that disposable
copy, and drives the production controller/resolver plus the pinned official
LightRAG PostgreSQL adapters with reviewed network-free callables.

Credentials and corpus text never enter JSON evidence or command output.
Cleanup consumes pinned evidence and removes only exactly labeled resources.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote, urlsplit

import psycopg
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session

from lightrag_full_reindex import (
    ActivationFailure,
    CorpusDocument,
    CorpusSnapshot,
    FullReindexWorkflow,
    IsolatedGenerationInspection,
    IsolatedGenerationStorageCapabilities,
    LedgerBackedGenerationController,
    LiveApplyUnsupported,
    PostgresSnapshotReader,
    ReindexPlan,
    SafetyError,
    TargetDeclaration,
    VerificationError,
    corpus_digest,
)
from rehearse import (
    EVIDENCE_ROOT,
    ROOT,
    DatabaseTarget,
    FileIdentity,
    PinnedReadFile,
    _capture_database_fingerprint,
    _command_from_pinned_file,
    _command_to_new_file,
    _pinned_file_sha256,
    _postgresql_command,
    _resolve_postgresql_client,
    _safe_evidence_output,
    _write_json_evidence,
)
from tc_backend.config import Settings
from tc_backend.db.models import (
    KnowledgeEdge,
    KnowledgeEmbedding,
    KnowledgeEvidence,
    KnowledgeNode,
    KnowledgeSourceRecord,
    OntologySchema,
)
from tc_backend.knowledge.embedding import EmbeddingBatch
from tc_backend.knowledge.lightrag import (
    LIGHTRAG_DISTRIBUTION,
    LIGHTRAG_PINNED_VERSION,
    OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID,
    LightRAGCallResult,
    LightRAGCallStatus,
    LightRAGIndexDocument,
    LightRAGSearchCandidate,
)
from tc_backend.knowledge.lightrag_generation_factory import (
    IsolatedLightRAGGenerationConfig,
    IsolatedLightRAGGenerationRuntimeFactory,
    LightRAGSubprocessPostgresCredentials,
    LightRAGSubprocessStorageConfig,
    SubprocessLightRAGIndex,
)
from tc_backend.knowledge.lightrag_local_rehearsal import (
    WAVE8_TOKEN,
    deterministic_embedding_vector,
)
from tc_backend.knowledge.lightrag_runtime import (
    LightRAGGenerationRuntime,
    LightRAGRuntimeResolver,
)
from tc_backend.repositories.lightrag_generation_repository import (
    ActiveGenerationBinding,
    DurableLightRAGGenerationControlPlane,
)
from tc_backend.services.knowledge_service import KnowledgeService, node_content

TASK = "PF-SF1-T09"
ACTION = "LOCAL-DISPOSABLE-NETWORK-FREE"
CLEANUP_ACTION = "LOCAL-DISPOSABLE-CLEANUP"
SCHEMA = "tc.phase-f.lightrag-wave8-evidence.v1"
CLEANUP_SCHEMA = "tc.phase-f.lightrag-wave8-cleanup.v1"
CANONICAL_DIGEST_VERSION = "tc.canonical-json.sha256.v1"
PRIOR_EVIDENCE_VERSION = "tc.phase-f.prior-evidence-inventory.v1"
DOCKER_INVENTORY_VERSION = "tc.docker-local-inventory.v1"
DATABASE_INVENTORY_VERSION = "tc.postgresql-database-inventory.v1"
SOURCE_IMAGE = (
    "pgvector/pgvector:0.8.1-pg16@sha256:"
    "33198da2828a14c30348d2ccb4750833d5ed9a44c88d840a0e523d7417120337"
)
AUXILIARY_IMAGE = "tc-lightrag-failover-postgres:pg16-age1.6.0-vector0.8.1"
COMPOSE_FILE = ROOT / "deploy" / "knowledge" / "compose.wave8.yml"
BOOTSTRAP = (
    "tc_backend.knowledge.lightrag_local_rehearsal:"
    "build_network_free_components"
)
RUN_PATTERN = re.compile(r"^[a-z0-9]{8,16}$")
WORKSPACE_PATTERN = re.compile(
    r"^pf-sf1-t09-wave8-[a-z0-9]{8,16}-(?:seed|[a-f0-9]{12})$"
)
HEX_DIGEST = re.compile(r"^[a-f0-9]{64}$")
GRAPH_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
FIXTURE_RECORDED_AT = datetime(2026, 9, 7, tzinfo=UTC)
FIXTURE_VERSION = "pf-sf1-t09-wave8-local-corpus-v1"
VECTOR_TABLE_SUFFIX = "pf_sf1_t09_wave8_network_free_1024_1024d"
VECTOR_CHUNKS_TABLE = f"lightrag_vdb_chunks_{VECTOR_TABLE_SUFFIX}"
VECTOR_ENTITY_TABLE = f"lightrag_vdb_entity_{VECTOR_TABLE_SUFFIX}"
VECTOR_RELATION_TABLE = f"lightrag_vdb_relation_{VECTOR_TABLE_SUFFIX}"
WORKSPACE_TABLES = (
    "lightrag_doc_full",
    "lightrag_doc_chunks",
    "lightrag_doc_status",
    VECTOR_CHUNKS_TABLE,
    VECTOR_ENTITY_TABLE,
    VECTOR_RELATION_TABLE,
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest(value: object, *, version: str = CANONICAL_DIGEST_VERSION) -> str:
    return hashlib.sha256(version.encode() + b"\0" + _canonical_bytes(value)).hexdigest()


def _digest_record(value: object, *, version: str) -> dict[str, str]:
    return {"version": version, "sha256": _digest(value, version=version)}


def _seal(payload: Mapping[str, object]) -> dict[str, object]:
    result = dict(payload)
    result["contractDigest"] = _digest_record(
        result, version=CANONICAL_DIGEST_VERSION
    )
    return result


def _verify_seal(payload: object, *, schema: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schemaVersion") != schema:
        raise SafetyError("wave-8 evidence schema mismatch")
    digest = payload.get("contractDigest")
    body = {key: value for key, value in payload.items() if key != "contractDigest"}
    if digest != _digest_record(body, version=CANONICAL_DIGEST_VERSION):
        raise SafetyError("wave-8 evidence digest mismatch")
    return cast(dict[str, Any], payload)


@dataclass(frozen=True, slots=True)
class Resources:
    run_id: str
    source_port: int
    auxiliary_port: int

    def __post_init__(self) -> None:
        if RUN_PATTERN.fullmatch(self.run_id) is None:
            raise SafetyError("run ID must contain 8-16 lowercase alphanumeric characters")
        for port in (self.source_port, self.auxiliary_port):
            if isinstance(port, bool) or not 1 <= port <= 65_535:
                raise SafetyError("wave-8 ports must be valid TCP ports")
        if self.source_port == self.auxiliary_port:
            raise SafetyError("source and auxiliary ports must be distinct")

    @property
    def target_name(self) -> str:
        return f"tc-phase-f-{WAVE8_TOKEN}-{self.run_id}-disposable"

    @property
    def project(self) -> str:
        return f"tc-{WAVE8_TOKEN}-{self.run_id}"

    @property
    def source_container(self) -> str:
        return f"tc-{WAVE8_TOKEN}-{self.run_id}-source-postgres-disposable"

    @property
    def auxiliary_container(self) -> str:
        return f"tc-{WAVE8_TOKEN}-{self.run_id}-aux-postgres-disposable"

    @property
    def source_volume(self) -> str:
        return f"tc-{WAVE8_TOKEN}-{self.run_id}-source-data-disposable"

    @property
    def auxiliary_volume(self) -> str:
        return f"tc-{WAVE8_TOKEN}-{self.run_id}-aux-data-disposable"

    @property
    def network(self) -> str:
        return f"tc-{WAVE8_TOKEN}-{self.run_id}-network-disposable"

    @property
    def source_database(self) -> str:
        return f"tc_phase_f_pf_sf1_t09_wave8_{self.run_id}_source_test_disposable"

    @property
    def auxiliary_database(self) -> str:
        return f"tc_phase_f_pf_sf1_t09_wave8_{self.run_id}_aux_test_disposable"

    @property
    def seed_workspace(self) -> str:
        return f"{WAVE8_TOKEN}-{self.run_id}-seed"

    @property
    def generation_prefix(self) -> str:
        return f"{WAVE8_TOKEN}-{self.run_id}"

    @property
    def source_user(self) -> str:
        return "tc_wave8_source"

    @property
    def auxiliary_user(self) -> str:
        return "tc_wave8_aux"

    def target(self, *, workspace: str | None = None) -> TargetDeclaration:
        return TargetDeclaration(
            name=self.target_name,
            target_class="disposable",
            authoritative_database_name=self.source_database,
            auxiliary_database_name=self.auxiliary_database,
            active_workspace=workspace or self.seed_workspace,
        )

    def public_dict(self) -> dict[str, object]:
        target = self.target()
        return {
            "runId": self.run_id,
            "target": target.public_dict(),
            "project": self.project,
            "containers": [self.source_container, self.auxiliary_container],
            "volumes": [self.source_volume, self.auxiliary_volume],
            "network": self.network,
            "sourceDatabaseComment": target.authoritative_comment,
            "auxiliaryDatabaseComment": target.auxiliary_comment,
            "seedWorkspace": self.seed_workspace,
            "ports": {
                "sourceLoopback": self.source_port,
                "auxiliaryLoopback": self.auxiliary_port,
            },
        }


def _confirmation(resources: Resources, *, cleanup: bool = False) -> str:
    action = CLEANUP_ACTION if cleanup else ACTION
    return f"RUN {TASK} {action} ON {resources.target_name}"


def _command(
    argv: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
    input_file: Any | None = None,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        list(argv),
        cwd=ROOT,
        env=dict(environment) if environment is not None else None,
        stdin=input_file,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=check,
    )


def _image_id(reference: str) -> str:
    result = _command(
        ["docker", "image", "inspect", reference, "--format", "{{.Id}}"],
        capture=True,
    )
    value = result.stdout.decode().strip()
    if re.fullmatch(r"sha256:[a-f0-9]{64}", value) is None:
        raise SafetyError("pinned local Docker image identity is invalid")
    return value


def _compose_environment(
    resources: Resources,
    *,
    source_password: str,
    auxiliary_password: str,
    source_image_id: str,
    auxiliary_image_id: str,
) -> dict[str, str]:
    return {
        **os.environ,
        "TC_PHASE_F_T09_WAVE8_PROJECT": resources.project,
        "TC_PHASE_F_T09_WAVE8_SOURCE_CONTAINER": resources.source_container,
        "TC_PHASE_F_T09_WAVE8_AUXILIARY_CONTAINER": resources.auxiliary_container,
        "TC_PHASE_F_T09_WAVE8_SOURCE_VOLUME": resources.source_volume,
        "TC_PHASE_F_T09_WAVE8_AUXILIARY_VOLUME": resources.auxiliary_volume,
        "TC_PHASE_F_T09_WAVE8_NETWORK": resources.network,
        "TC_PHASE_F_T09_WAVE8_SOURCE_DATABASE": resources.source_database,
        "TC_PHASE_F_T09_WAVE8_AUXILIARY_DATABASE": resources.auxiliary_database,
        "TC_PHASE_F_T09_WAVE8_SOURCE_USER": resources.source_user,
        "TC_PHASE_F_T09_WAVE8_AUXILIARY_USER": resources.auxiliary_user,
        "TC_PHASE_F_T09_WAVE8_SOURCE_PASSWORD": source_password,
        "TC_PHASE_F_T09_WAVE8_AUXILIARY_PASSWORD": auxiliary_password,
        "TC_PHASE_F_T09_WAVE8_SOURCE_PORT": str(resources.source_port),
        "TC_PHASE_F_T09_WAVE8_AUXILIARY_PORT": str(resources.auxiliary_port),
        "TC_PHASE_F_T09_WAVE8_SOURCE_IMAGE_ID": source_image_id,
        "TC_PHASE_F_T09_WAVE8_AUXILIARY_IMAGE_ID": auxiliary_image_id,
    }


def _compose_command(resources: Resources, *arguments: str) -> tuple[str, ...]:
    return (
        "docker",
        "compose",
        "--project-name",
        resources.project,
        "--file",
        str(COMPOSE_FILE),
        *arguments,
    )


def _docker_inventory() -> dict[str, object]:
    commands = {
        "containers": [
            "docker",
            "ps",
            "-a",
            "--no-trunc",
            "--format",
            "{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.State}}",
        ],
        "volumes": ["docker", "volume", "ls", "--format", "{{.Name}}"],
        "networks": ["docker", "network", "ls", "--format", "{{.ID}}\t{{.Name}}"],
        "images": [
            "docker",
            "image",
            "ls",
            "--no-trunc",
            "--format",
            "{{.ID}}\t{{.Repository}}:{{.Tag}}",
        ],
    }
    rows: dict[str, list[str]] = {}
    for kind, command in commands.items():
        result = _command(command, capture=True)
        rows[kind] = sorted(line for line in result.stdout.decode().splitlines() if line)
    return {
        "version": DOCKER_INVENTORY_VERSION,
        "counts": {key: len(value) for key, value in rows.items()},
        "digest": _digest_record(rows, version=DOCKER_INVENTORY_VERSION),
    }


def _prior_evidence_inventory(*, exclude: set[Path]) -> dict[str, object]:
    entries: list[dict[str, str]] = []
    excluded = {path.resolve() for path in exclude}
    for path in sorted(EVIDENCE_ROOT.glob("*.json")):
        if path.resolve() in excluded:
            continue
        with PinnedReadFile(
            path,
            label="prior Phase F evidence",
            maximum_size=8 * 1024 * 1024,
            require_nonempty=True,
        ) as source:
            sha256 = source.run_stage("hash", lambda: _pinned_file_sha256(source))
        entries.append({"path": str(path.relative_to(ROOT)), "sha256": sha256})
    return {
        "version": PRIOR_EVIDENCE_VERSION,
        "count": len(entries),
        "entries": entries,
        "digest": _digest_record(entries, version=PRIOR_EVIDENCE_VERSION),
    }


def _verify_prior_evidence(inventory: Mapping[str, object]) -> bool:
    entries = inventory.get("entries")
    if not isinstance(entries, list) or inventory.get("count") != len(entries):
        raise SafetyError("prior evidence inventory is malformed")
    observed: list[dict[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
            raise SafetyError("prior evidence inventory entry is malformed")
        path_value = entry["path"]
        expected = entry["sha256"]
        if not isinstance(path_value, str) or not isinstance(expected, str):
            raise SafetyError("prior evidence inventory entry is malformed")
        path = (ROOT / path_value).resolve()
        try:
            path.relative_to(EVIDENCE_ROOT)
        except ValueError as error:
            raise SafetyError("prior evidence path escaped the evidence root") from error
        with PinnedReadFile(
            path,
            label="prior Phase F evidence",
            maximum_size=8 * 1024 * 1024,
            require_nonempty=True,
        ) as source:
            actual = source.run_stage("rehash", lambda: _pinned_file_sha256(source))
        if actual != expected:
            raise SafetyError("prior Phase F evidence hash changed")
        observed.append({"path": path_value, "sha256": actual})
    if inventory.get("digest") != _digest_record(
        observed, version=PRIOR_EVIDENCE_VERSION
    ):
        raise SafetyError("prior evidence inventory digest changed")
    return True


def _current_database() -> tuple[DatabaseTarget, Any]:
    settings = Settings()  # type: ignore[call-arg]
    raw = str(settings.database_url)
    parsed = urlsplit(raw)
    if (
        parsed.scheme not in {"postgresql", "postgresql+psycopg"}
        or parsed.hostname not in LOOPBACK
        or parsed.path.removeprefix("/") != "tc"
        or parsed.query
        or parsed.fragment
    ):
        raise SafetyError("current tc database must be the exact loopback database")
    libpq = raw.replace("postgresql+psycopg://", "postgresql://", 1)
    environment = {
        "PGDATABASE": "tc",
        "PGHOST": str(parsed.hostname),
        "PGPORT": str(parsed.port or 5432),
        "PGCONNECT_TIMEOUT": "10",
    }
    if parsed.username:
        environment["PGUSER"] = parsed.username
    if parsed.password:
        environment["PGPASSWORD"] = parsed.password
    target = DatabaseTarget(
        "tc", "TC_DATABASE_URL", raw, libpq, tuple(sorted(environment.items()))
    )
    return target, _resolve_postgresql_client(target)


def _database_inventory(database: DatabaseTarget) -> dict[str, object]:
    with psycopg.connect(database.libpq_url) as connection:
        connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        names = [
            str(row[0])
            for row in connection.execute(
                "SELECT datname FROM pg_database ORDER BY datname"
            ).fetchall()
        ]
        connection.rollback()
    return {
        "version": DATABASE_INVENTORY_VERSION,
        "count": len(names),
        "digest": _digest_record(names, version=DATABASE_INVENTORY_VERSION),
        "tcPresent": "tc" in names,
    }


def _database_target(
    resources: Resources,
    *,
    source: bool,
    password: str,
) -> DatabaseTarget:
    user = resources.source_user if source else resources.auxiliary_user
    port = resources.source_port if source else resources.auxiliary_port
    name = resources.source_database if source else resources.auxiliary_database
    encoded = quote(password, safe="")
    raw = f"postgresql+psycopg://{user}:{encoded}@127.0.0.1:{port}/{name}"
    libpq = raw.replace("postgresql+psycopg://", "postgresql://", 1)
    return DatabaseTarget(
        name,
        "GENERATED_IN_MEMORY",
        raw,
        libpq,
        (
            ("PGCONNECT_TIMEOUT", "10"),
            ("PGDATABASE", name),
            ("PGHOST", "127.0.0.1"),
            ("PGPASSWORD", password),
            ("PGPORT", str(port)),
            ("PGUSER", user),
        ),
    )


def _assert_ports_free(resources: Resources) -> None:
    for port in (resources.source_port, resources.auxiliary_port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.25)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise SafetyError("a declared loopback port is already occupied")


def _wait_healthy(container: str, *, timeout_seconds: float = 90) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = _command(
            [
                "docker",
                "inspect",
                "--format",
                "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
                container,
            ],
            capture=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.decode().strip() == "healthy":
            return
        time.sleep(1)
    raise SafetyError("wave-8 PostgreSQL container did not become healthy")


def _resource_names(resources: Resources) -> tuple[tuple[str, str], ...]:
    return (
        ("container", resources.source_container),
        ("container", resources.auxiliary_container),
        ("volume", resources.source_volume),
        ("volume", resources.auxiliary_volume),
        ("network", resources.network),
    )


def _resource_labels(kind: str, name: str) -> tuple[str, str, str] | None:
    templates = {
        "container": (
            '{{ index .Config.Labels "com.tc.phase-f-rehearsal" }}|'
            '{{ index .Config.Labels "com.tc.phase-f-task" }}|'
            '{{ index .Config.Labels "com.tc.phase-f-wave" }}'
        ),
        "volume": (
            '{{ index .Labels "com.tc.phase-f-rehearsal" }}|'
            '{{ index .Labels "com.tc.phase-f-task" }}|'
            '{{ index .Labels "com.tc.phase-f-wave" }}'
        ),
        "network": (
            '{{ index .Labels "com.tc.phase-f-rehearsal" }}|'
            '{{ index .Labels "com.tc.phase-f-task" }}|'
            '{{ index .Labels "com.tc.phase-f-wave" }}'
        ),
    }
    result = _command(
        ["docker", "inspect", "--type", kind, "--format", templates[kind], name],
        capture=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    parts = result.stdout.decode().strip().split("|")
    if len(parts) != 3:
        raise SafetyError("Docker resource label attestation is malformed")
    return parts[0], parts[1], parts[2]


def _assert_no_collisions(resources: Resources) -> None:
    if any(_resource_labels(kind, name) is not None for kind, name in _resource_names(resources)):
        raise SafetyError("a generated wave-8 Docker resource already exists")


def _assert_owned_resources(resources: Resources) -> None:
    expected = (resources.project, TASK, "wave8")
    for kind, name in _resource_names(resources):
        if _resource_labels(kind, name) != expected:
            raise SafetyError("refusing a non-exact or unowned wave-8 Docker resource")


def _require_empty_target(*, public_tables: int, graph_count: int) -> None:
    if public_tables != 0 or graph_count != 0:
        raise SafetyError("auxiliary target must start with no public tables or AGE graphs")


def _attest_database(
    database: DatabaseTarget,
    *,
    expected_comment: str,
    extensions: set[str],
    require_empty: bool,
) -> dict[str, object]:
    with psycopg.connect(database.libpq_url) as connection:
        connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        row = connection.execute(
            "SELECT current_database(), current_setting('server_version_num'), "
            "COALESCE(shobj_description(oid, 'pg_database'), '') "
            "FROM pg_database WHERE datname=current_database()"
        ).fetchone()
        if row is None or row[0] != database.expected_name:
            raise SafetyError("database target identity mismatch")
        if int(row[1]) < 160_000 or row[2] != expected_comment:
            raise SafetyError("database version or comment attestation mismatch")
        observed_extensions = {
            str(value[0])
            for value in connection.execute(
                "SELECT extname FROM pg_extension WHERE extname = ANY(%s)",
                (sorted(extensions),),
            ).fetchall()
        }
        if observed_extensions != extensions:
            raise SafetyError("database extension attestation mismatch")
        public_table_row = connection.execute(
            "SELECT count(*) FROM pg_tables WHERE schemaname='public'"
        ).fetchone()
        if public_table_row is None:
            raise SafetyError("public table count is unavailable")
        public_tables = int(public_table_row[0])
        graph_count = 0
        if "age" in extensions:
            graph_count_row = connection.execute(
                "SELECT count(*) FROM ag_catalog.ag_graph"
            ).fetchone()
            if graph_count_row is None:
                raise SafetyError("AGE graph count is unavailable")
            graph_count = int(graph_count_row[0])
        connection.rollback()
    if require_empty:
        _require_empty_target(public_tables=public_tables, graph_count=graph_count)
    return {
        "databaseName": database.expected_name,
        "databaseCommentAttested": True,
        "serverVersionNum": int(row[1]),
        "extensions": sorted(observed_extensions),
        "publicTableCount": public_tables,
        "ageGraphCount": graph_count,
        "emptyTargetRequired": require_empty,
    }


def _set_database_comment_and_extensions(
    database: DatabaseTarget,
    *,
    comment: str,
    extensions: Sequence[str],
) -> None:
    with psycopg.connect(database.libpq_url, autocommit=True) as connection:
        connection.execute(
            sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                sql.Identifier(database.expected_name), sql.Literal(comment)
            )
        )
        for extension in extensions:
            if extension not in {"age", "vector"}:
                raise SafetyError("unexpected extension request")
            connection.execute(
                sql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(
                    sql.Identifier(extension)
                )
            )


def _logical_column_catalog(database: DatabaseTarget) -> tuple[tuple[object, ...], ...]:
    """Read column semantics without PostgreSQL's non-logical attnum gaps.

    A logical pg_dump/pg_restore intentionally compacts slots left by dropped
    columns.  Those physical slot numbers are not part of SQL-visible column
    semantics, so the wave-8 restore proof compares every remaining attribute
    while ordering by stable relation/column identity.
    """

    with psycopg.connect(database.libpq_url) as connection:
        connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        rows = connection.execute(
            "SELECT c.relname, a.attname, "
            "format_type(a.atttypid,a.atttypmod), a.attnotnull, a.attidentity, "
            "a.attgenerated, COALESCE(pg_get_expr(d.adbin,d.adrelid,false),''), "
            "CASE WHEN a.attcollation=0 THEN '' ELSE "
            "quote_ident(cn.nspname)||'.'||quote_ident(co.collname) END, "
            "a.attstorage, a.attcompression, COALESCE(a.attoptions::text,''), "
            "COALESCE(col_description(c.oid,a.attnum),'') "
            "FROM pg_attribute AS a JOIN pg_class AS c ON c.oid=a.attrelid "
            "JOIN pg_namespace AS n ON n.oid=c.relnamespace "
            "LEFT JOIN pg_attrdef AS d ON d.adrelid=a.attrelid AND d.adnum=a.attnum "
            "LEFT JOIN pg_collation AS co ON co.oid=a.attcollation "
            "LEFT JOIN pg_namespace AS cn ON cn.oid=co.collnamespace "
            "WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f') "
            "AND a.attnum>0 AND NOT a.attisdropped ORDER BY 1,2"
        ).fetchall()
        connection.rollback()
    return tuple(tuple(row) for row in rows)


def _logical_restore_attestation(
    *,
    current: DatabaseTarget,
    restored: DatabaseTarget,
    current_fingerprint: Mapping[str, object],
    restored_fingerprint: Mapping[str, object],
) -> dict[str, object]:
    data_keys = ("tableCount", "totalRows", "dataSha256", "tables")
    if any(
        current_fingerprint.get(key) != restored_fingerprint.get(key)
        for key in data_keys
    ):
        raise VerificationError("disposable source restore data differs from current tc")
    current_categories = current_fingerprint.get("schemaCategorySha256")
    restored_categories = restored_fingerprint.get("schemaCategorySha256")
    if not isinstance(current_categories, dict) or not isinstance(
        restored_categories, dict
    ):
        raise VerificationError("disposable source schema fingerprint is malformed")
    if set(current_categories) != set(restored_categories):
        raise VerificationError("disposable source schema categories differ")
    unexpected = sorted(
        category
        for category in current_categories
        if category != "columns"
        and current_categories[category] != restored_categories[category]
    )
    if unexpected or current_fingerprint.get(
        "schemaObjectCounts"
    ) != restored_fingerprint.get("schemaObjectCounts"):
        raise VerificationError("disposable source schema semantics differ")
    current_columns = _logical_column_catalog(current)
    restored_columns = _logical_column_catalog(restored)
    if current_columns != restored_columns:
        raise VerificationError("disposable source logical column catalog differs")
    physical_equal = current_fingerprint == restored_fingerprint
    physical_difference = (
        "NONE" if physical_equal else "DROPPED_COLUMN_ATTRIBUTE_SLOTS_COMPACTED_BY_PG_DUMP"
    )
    return {
        "logicalDataExact": True,
        "logicalSchemaExact": True,
        "logicalColumnCount": len(current_columns),
        "logicalColumnDigest": _digest_record(
            current_columns, version="tc.postgresql.logical-columns.v1"
        ),
        "rawPhysicalFingerprintEqual": physical_equal,
        "rawPhysicalDifference": physical_difference,
    }


def _restore_current_database(
    *,
    current: DatabaseTarget,
    current_client: Any,
    resources: Resources,
    dump_path: Path,
) -> dict[str, object]:
    command, environment = _postgresql_command(
        current_client,
        "pg_dump",
        current,
        "--format=custom",
        "--no-owner",
        "--no-privileges",
    )
    _command_to_new_file(command, dump_path, env=environment)
    with PinnedReadFile(
        dump_path,
        label="wave-8 source dump",
        maximum_size=512 * 1024 * 1024,
        require_nonempty=True,
    ) as source:
        sha256 = source.run_stage("hash", lambda: _pinned_file_sha256(source))
        restore = [
            "docker",
            "exec",
            "-i",
            resources.source_container,
            "pg_restore",
            "--exit-on-error",
            "--no-owner",
            "--no-privileges",
            "-U",
            resources.source_user,
            "-d",
            resources.source_database,
        ]
        _command_from_pinned_file(
            restore,
            source,
            env=os.environ,
            capture=True,
        )
        identity = source.identity.evidence()
    return {
        "sha256": sha256,
        "bytes": cast(int, identity["size"]),
        "fileIdentity": identity,
        "sourceAccess": "READ_ONLY_PG_DUMP",
        "restoreTarget": resources.source_database,
    }


def _fixture_ids(run_id: str) -> tuple[uuid.UUID, ...]:
    namespace = uuid.UUID("2b679a64-6cfd-4dd2-93ec-2db36557ac18")
    return tuple(
        uuid.uuid5(namespace, f"{FIXTURE_VERSION}:{run_id}:{label}")
        for label in ("node-a", "node-b", "edge", "source-a", "source-b", "source-edge")
    )


def _seed_disposable_corpus(engine: Engine, resources: Resources) -> dict[str, int]:
    node_a, node_b, edge_id, source_a, source_b, source_edge = _fixture_ids(
        resources.run_id
    )
    with Session(engine) as session, session.begin():
        if session.scalar(select(KnowledgeNode.id).limit(1)) is not None:
            raise SafetyError("restored source already has a corpus; fixture injection refused")
        schema = session.scalar(
            select(OntologySchema).where(OntologySchema.active.is_(True))
        )
        if schema is None:
            raise SafetyError("restored source has no active ontology schema")
        labels = (
            f"{WAVE8_TOKEN} alpha",
            f"{WAVE8_TOKEN} beta",
        )
        nodes = (
            KnowledgeNode(
                id=node_a,
                ontology_schema_id=schema.id,
                node_type="TEST_CASE",
                status="ACTIVE",
                label=labels[0],
                properties={"content": f"{labels[0]} deterministic verification source"},
                recorded_at=FIXTURE_RECORDED_AT,
                valid_from=FIXTURE_RECORDED_AT,
            ),
            KnowledgeNode(
                id=node_b,
                ontology_schema_id=schema.id,
                node_type="REQUIREMENT",
                status="ACTIVE",
                label=labels[1],
                properties={"content": f"{labels[1]} retains the prior generation"},
                recorded_at=FIXTURE_RECORDED_AT,
                valid_from=FIXTURE_RECORDED_AT,
            ),
        )
        session.add_all(nodes)
        # The relation validator is a database trigger rather than an ORM
        # relationship, so make both endpoint rows visible before its INSERT.
        session.flush()
        edge = KnowledgeEdge(
            id=edge_id,
            ontology_schema_id=schema.id,
            source_node_id=node_a,
            predicate="VERIFIES",
            target_node_id=node_b,
            status="ACTIVE",
            properties={"fixture": FIXTURE_VERSION},
            recorded_at=FIXTURE_RECORDED_AT,
            valid_from=FIXTURE_RECORDED_AT,
        )
        session.add(edge)
        session.flush()
        sources: list[KnowledgeSourceRecord] = []
        evidences: list[KnowledgeEvidence] = []
        for owner_id, source_id, owner_kind in (
            (node_a, source_a, "node"),
            (node_b, source_b, "node"),
            (edge_id, source_edge, "edge"),
        ):
            content_hash = hashlib.sha256(
                f"{FIXTURE_VERSION}:{owner_kind}:{owner_id}".encode()
            ).hexdigest()
            source = KnowledgeSourceRecord(
                id=source_id,
                source_kind="LEDGER_ENTITY",
                entity_type="WAVE8_FIXTURE",
                entity_id=owner_id,
                revision_key="1",
                content_hash=content_hash,
                permalink=None,
                recorded_at=FIXTURE_RECORDED_AT,
            )
            sources.append(source)
            evidences.append(
                KnowledgeEvidence(
                    id=uuid.uuid5(source_id, "evidence"),
                    ontology_schema_id=schema.id,
                    knowledge_node_id=owner_id if owner_kind == "node" else None,
                    knowledge_edge_id=owner_id if owner_kind == "edge" else None,
                    source_record_id=source_id,
                    source_type="MANUAL",
                    source_id=source_id,
                    source_revision="1",
                    permalink=None,
                    excerpt_hash=content_hash,
                    recorded_at=FIXTURE_RECORDED_AT,
                )
            )
        # These fixtures intentionally avoid production-domain relationships;
        # flush the explicit FK parent set before its evidence children.
        session.add_all(sources)
        session.flush()
        session.add_all(evidences)
        session.flush()
        for node in nodes:
            content = node_content(node)
            session.add(
                KnowledgeEmbedding(
                    id=uuid.uuid5(node.id, "embedding"),
                    owner_type="NODE",
                    owner_id=node.id,
                    model="bge-m3",
                    model_version="bge-m3",
                    content_hash=hashlib.sha256(content.encode()).hexdigest(),
                    embedding=list(deterministic_embedding_vector(content)),
                )
            )
    return {"nodes": 2, "edges": 1, "sources": 3, "evidence": 3, "embeddings": 2}


def _fetch_keyset_pages(
    fetch: Callable[[str | None, int], Sequence[Sequence[object]]],
    *,
    page_size: int,
) -> tuple[tuple[Sequence[object], ...], int]:
    if page_size < 1:
        raise SafetyError("page size must be positive")
    cursor: str | None = None
    seen: set[str] = set()
    rows: list[Sequence[object]] = []
    pages = 0
    while True:
        page = tuple(fetch(cursor, page_size))
        if len(page) > page_size:
            raise VerificationError("provider page exceeded its declared limit")
        if not page:
            break
        pages += 1
        page_ids = [str(row[0]) for row in page]
        if page_ids != sorted(page_ids) or len(page_ids) != len(set(page_ids)):
            raise VerificationError("provider page IDs are unordered or duplicated")
        if cursor is not None and page_ids[0] <= cursor:
            raise VerificationError("provider cursor did not advance")
        if any(row_id in seen for row_id in page_ids):
            raise VerificationError("provider pagination returned a duplicate identity")
        seen.update(page_ids)
        rows.extend(page)
        next_cursor = page_ids[-1]
        if next_cursor == cursor:
            raise VerificationError("provider cursor repeated")
        cursor = next_cursor
        if len(page) < page_size:
            break
    return tuple(rows), pages


def _workspace_graph_name(workspace: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_]", "_", workspace) + "_chunk_entity_relation"
    if GRAPH_IDENTIFIER.fullmatch(name) is None:
        raise SafetyError("derived AGE graph name is unsafe")
    return name


def _workspace_rows(
    connection: Any,
    *,
    table: str,
    columns: str,
    workspace: str,
    page_size: int = 1,
) -> tuple[tuple[Sequence[object], ...], int]:
    if table not in WORKSPACE_TABLES:
        raise SafetyError("unexpected LightRAG table")

    def fetch(cursor: str | None, limit: int) -> Sequence[Sequence[object]]:
        return connection.execute(
            text(
                f"SELECT id, {columns} FROM {table} "
                "WHERE workspace=:workspace AND "
                "(CAST(:cursor AS text) IS NULL OR id>CAST(:cursor AS text)) "
                "ORDER BY id LIMIT :limit"
            ),
            {"workspace": workspace, "cursor": cursor, "limit": limit},
        ).all()

    return _fetch_keyset_pages(fetch, page_size=page_size)


def _json_properties(value: object) -> dict[str, object]:
    try:
        decoded = json.loads(str(value))
    except json.JSONDecodeError as error:
        raise VerificationError("AGE graph property payload is malformed") from error
    if not isinstance(decoded, dict):
        raise VerificationError("AGE graph property payload is not an object")
    return cast(dict[str, object], decoded)


def _graph_entity_identity(properties: Mapping[str, object]) -> str:
    entity_id = properties.get("entity_id")
    if not isinstance(entity_id, str) or not entity_id:
        raise VerificationError("AGE graph node has no stable entity identity")
    return entity_id


def _semantic_graph_properties(properties: Mapping[str, object]) -> dict[str, object]:
    # LightRAG assigns this insertion-time metadata independently in every
    # immutable workspace. It is neither graph content nor topology.
    return {key: value for key, value in properties.items() if key != "created_at"}


def _semantic_graph_payload(
    graph_nodes: Sequence[Sequence[object]],
    graph_edges: Sequence[Sequence[object]],
) -> dict[str, object]:
    """Return graph semantics without workspace-local AGE storage identities."""
    internal_node_ids = [str(row[0]) for row in graph_nodes]
    if len(internal_node_ids) != len(set(internal_node_ids)):
        raise VerificationError("AGE graph contains a duplicate internal node identity")

    nodes: list[dict[str, object]] = []
    entity_ids: set[str] = set()
    for row in graph_nodes:
        properties = _json_properties(row[1])
        entity_id = _graph_entity_identity(properties)
        if entity_id in entity_ids:
            raise VerificationError("AGE graph contains a duplicate semantic node identity")
        entity_ids.add(entity_id)
        nodes.append(
            {
                "entityId": entity_id,
                "properties": _semantic_graph_properties(properties),
            }
        )

    internal_edge_ids = [str(row[0]) for row in graph_edges]
    if len(internal_edge_ids) != len(set(internal_edge_ids)):
        raise VerificationError("AGE graph contains a duplicate internal edge identity")

    edges: list[dict[str, object]] = []
    for row in graph_edges:
        source_id = _graph_entity_identity(_json_properties(row[1]))
        target_id = _graph_entity_identity(_json_properties(row[2]))
        if source_id not in entity_ids or target_id not in entity_ids:
            raise VerificationError("AGE graph edge references an unknown semantic node")
        endpoint_ids = sorted((source_id, target_id))
        edges.append(
            {
                "endpointEntityIds": endpoint_ids,
                "properties": _semantic_graph_properties(
                    _json_properties(row[3])
                ),
            }
        )

    return {
        "nodes": sorted(nodes, key=_canonical_bytes),
        "edges": sorted(edges, key=_canonical_bytes),
    }


def _semantic_graph_digest(
    graph_nodes: Sequence[Sequence[object]],
    graph_edges: Sequence[Sequence[object]],
) -> dict[str, str]:
    return _digest_record(
        _semantic_graph_payload(graph_nodes, graph_edges),
        version="tc.lightrag.semantic-age-graph.v2",
    )


def _assert_semantic_graph_digest_agreement(
    inspections: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    if set(inspections) != {"prior", "cutover", "failedActivation"}:
        raise VerificationError("semantic graph agreement requires all three generations")
    digests: dict[str, str] = {}
    version: str | None = None
    for generation, inspection in inspections.items():
        graph = inspection.get("graph")
        if not isinstance(graph, dict):
            raise VerificationError("generation inspection has no AGE graph evidence")
        digest = graph.get("digest")
        if (
            not isinstance(digest, dict)
            or digest.get("version") != "tc.lightrag.semantic-age-graph.v2"
            or not isinstance(digest.get("sha256"), str)
            or HEX_DIGEST.fullmatch(cast(str, digest["sha256"])) is None
        ):
            raise VerificationError("generation semantic graph digest is malformed")
        version = cast(str, digest["version"])
        digests[generation] = cast(str, digest["sha256"])
    unique_digests = set(digests.values())
    if len(unique_digests) != 1:
        raise VerificationError("semantic AGE graph differs across immutable generations")
    return {
        "generationCount": 3,
        "equal": True,
        "version": version,
        "sha256": next(iter(unique_digests)),
    }


def inspect_generation_workspace(
    engine: Engine,
    *,
    workspace: str,
    expected: CorpusSnapshot,
) -> dict[str, object]:
    if WORKSPACE_PATTERN.fullmatch(workspace) is None:
        raise SafetyError("workspace is not an exact wave-8 generation")
    graph = _workspace_graph_name(workspace)
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.exec_driver_sql(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY, DEFERRABLE"
            )
            full, full_pages = _workspace_rows(
                connection,
                table="lightrag_doc_full",
                columns="content",
                workspace=workspace,
            )
            statuses, status_pages = _workspace_rows(
                connection,
                table="lightrag_doc_status",
                columns="status, file_path",
                workspace=workspace,
            )
            chunks, chunk_pages = _workspace_rows(
                connection,
                table="lightrag_doc_chunks",
                columns="full_doc_id, file_path, content",
                workspace=workspace,
            )
            vectors, vector_pages = _workspace_rows(
                connection,
                table=VECTOR_CHUNKS_TABLE,
                columns=(
                    "full_doc_id, file_path, content, "
                    "vector_dims(content_vector), content_vector::text"
                ),
                workspace=workspace,
            )
            entities, entity_pages = _workspace_rows(
                connection,
                table=VECTOR_ENTITY_TABLE,
                columns=(
                    "entity_name, content, vector_dims(content_vector), "
                    "content_vector::text"
                ),
                workspace=workspace,
            )
            relations, relation_pages = _workspace_rows(
                connection,
                table=VECTOR_RELATION_TABLE,
                columns=(
                    "source_id, target_id, content, vector_dims(content_vector), "
                    "content_vector::text"
                ),
                workspace=workspace,
            )
            graph_exists = bool(
                connection.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM ag_catalog.ag_graph WHERE name=:name)"
                    ),
                    {"name": graph},
                ).scalar_one()
            )
            if not graph_exists:
                raise VerificationError("expected AGE generation graph is absent")
            graph_nodes = connection.execute(
                text(
                    f'SELECT id::text, properties::text FROM "{graph}"."base" '
                    "ORDER BY id"
                )
            ).all()
            graph_edges = connection.execute(
                text(
                    f'SELECT e.id::text, s.properties::text, t.properties::text, '
                    f'e.properties::text FROM "{graph}"."DIRECTED" AS e '
                    f'JOIN "{graph}"."base" AS s ON s.id::text=e.start_id::text '
                    f'JOIN "{graph}"."base" AS t ON t.id::text=e.end_id::text '
                    "ORDER BY e.id"
                )
            ).all()
            transaction.commit()
        except Exception:
            transaction.rollback()
            raise

    expected_by_id = {
        document.postgres_record_id: document for document in expected.documents
    }
    full_documents: list[CorpusDocument] = []
    for row in full:
        record_id, content = str(row[0]), row[1]
        expected_document = expected_by_id.get(record_id)
        if expected_document is None or not isinstance(content, str):
            raise VerificationError("LightRAG full-document identity is unexpected")
        full_documents.append(
            CorpusDocument(record_id, content, expected_document.source_uri)
        )
    actual_ids = tuple(document.postgres_record_id for document in full_documents)
    expected_ids = tuple(sorted(expected_by_id))
    status_ids = tuple(str(row[0]) for row in statuses)
    if (
        actual_ids != expected_ids
        or status_ids != expected_ids
        or any(str(row[1]) != "processed" for row in statuses)
        or any(str(row[2]) != str(row[0]) for row in statuses)
        or corpus_digest(full_documents) != expected.corpus_digest
    ):
        raise VerificationError("document/content/status identity or digest mismatch")
    chunk_ids = tuple(str(row[0]) for row in chunks)
    vector_ids = tuple(str(row[0]) for row in vectors)
    if chunk_ids != vector_ids or len(chunk_ids) < len(expected_ids):
        raise VerificationError("chunk and pgvector identities are incomplete")
    chunk_docs = [str(row[1]) for row in chunks]
    vector_docs = [str(row[1]) for row in vectors]
    if set(chunk_docs) != set(expected_ids) or chunk_docs != vector_docs:
        raise VerificationError("chunk document cardinality is incomplete")
    if any(str(row[2]) != str(row[1]) for row in chunks):
        raise VerificationError("chunk citation identity mismatch")
    if any(str(row[2]) != str(row[1]) for row in vectors):
        raise VerificationError("pgvector citation identity mismatch")
    if any(cast(int, row[4]) != 1024 for row in vectors):
        raise VerificationError("chunk vector dimension mismatch")
    if any(cast(int, row[3]) != 1024 for row in entities):
        raise VerificationError("entity vector dimension mismatch")
    if any(cast(int, row[4]) != 1024 for row in relations):
        raise VerificationError("relation vector dimension mismatch")
    if not entities or not relations or not graph_nodes or not graph_edges:
        raise VerificationError("entity/relation/vector/graph generation is empty")
    semantic_graph = _semantic_graph_payload(graph_nodes, graph_edges)
    graph_entity_names = sorted(
        cast(str, record["entityId"])
        for record in cast(list[dict[str, object]], semantic_graph["nodes"])
    )
    vector_entity_names = sorted(str(row[1]) for row in entities)
    if graph_entity_names != vector_entity_names:
        raise VerificationError("AGE and entity-vector identity sets differ")
    normalized_graph_pairs = sorted(
        tuple(cast(list[str], record["endpointEntityIds"]))
        for record in cast(list[dict[str, object]], semantic_graph["edges"])
    )
    normalized_vector_pairs = sorted(
        tuple(sorted((str(row[1]), str(row[2])))) for row in relations
    )
    if normalized_graph_pairs != normalized_vector_pairs:
        raise VerificationError("AGE and relation-vector identity sets differ")
    citation_payload = [(str(row[0]), str(row[1]), str(row[2])) for row in chunks]
    return {
        "workspace": workspace,
        "documentCount": len(full_documents),
        "processedCount": len(statuses),
        "failedCount": 0,
        "corpusDigest": expected.corpus_digest,
        "documentIdentitySetDigest": _digest_record(
            list(expected_ids), version="tc.lightrag.document-identities.v1"
        ),
        "contentDigest": _digest_record(
            [(str(row[0]), hashlib.sha256(str(row[1]).encode()).hexdigest()) for row in full],
            version="tc.lightrag.full-content.v1",
        ),
        "citationDigest": _digest_record(
            citation_payload, version="tc.lightrag.citations.v1"
        ),
        "citationCanonicalization": "UUID_BASENAME_OF_CANONICAL_POSTGRESQL_URI",
        "chunk": {
            "count": len(chunks),
            "pages": chunk_pages,
            "digest": _digest_record(
                [
                    (
                        str(row[0]),
                        str(row[1]),
                        hashlib.sha256(str(row[3]).encode()).hexdigest(),
                    )
                    for row in chunks
                ],
                version="tc.lightrag.chunks.v1",
            ),
        },
        "vectors": {
            "chunkCount": len(vectors),
            "entityCount": len(entities),
            "relationCount": len(relations),
            "dimension": 1024,
            "digest": _digest_record(
                {
                    "chunks": [(str(row[0]), str(row[5])) for row in vectors],
                    "entities": [(str(row[0]), str(row[4])) for row in entities],
                    "relations": [(str(row[0]), str(row[5])) for row in relations],
                },
                version="tc.lightrag.vectors.v1",
            ),
        },
        "graph": {
            "nameSha256": hashlib.sha256(graph.encode()).hexdigest(),
            "nodeCount": len(graph_nodes),
            "edgeCount": len(graph_edges),
            "identitySetsMatchVectors": True,
            "canonicalization": (
                "SEMANTIC_ENTITY_IDENTITIES_TOPOLOGY_AND_PROPERTIES;"
                "AGE_INTERNAL_NODE_AND_EDGE_IDS_AND_PROVIDER_CREATED_AT_OMITTED"
            ),
            "internalStorageIdsOmitted": True,
            "providerCreatedAtOmitted": True,
            "digest": _semantic_graph_digest(graph_nodes, graph_edges),
        },
        "pagination": {
            "pageSize": 1,
            "fullDocumentPages": full_pages,
            "statusPages": status_pages,
            "chunkPages": chunk_pages,
            "vectorChunkPages": vector_pages,
            "entityPages": entity_pages,
            "relationPages": relation_pages,
            "cursorMonotonic": True,
        },
        "rawCorpusOmitted": True,
    }


def _workspace_exists(engine: Engine, workspace: str) -> bool:
    graph = _workspace_graph_name(workspace)
    with engine.connect() as connection:
        for table in WORKSPACE_TABLES:
            present = connection.execute(
                text("SELECT to_regclass(:table) IS NOT NULL"), {"table": table}
            ).scalar_one()
            if present and connection.execute(
                text(f"SELECT EXISTS (SELECT 1 FROM {table} WHERE workspace=:workspace)"),
                {"workspace": workspace},
            ).scalar_one():
                return True
        return bool(
            connection.execute(
                text("SELECT EXISTS (SELECT 1 FROM ag_catalog.ag_graph WHERE name=:name)"),
                {"name": graph},
            ).scalar_one()
        )


class OfficialGenerationStorage:
    def __init__(
        self,
        *,
        plan: ReindexPlan,
        target: TargetDeclaration,
        engine: Engine,
        factory: IsolatedLightRAGGenerationRuntimeFactory,
    ) -> None:
        self.plan = plan
        self.target = target
        self.engine = engine
        self.factory = factory
        (self.attestation,) = factory.configuration_attestations()
        self.runtimes: dict[str, LightRAGGenerationRuntime] = {}
        self.inspections: dict[str, dict[str, object]] = {}
        self.pids: set[int] = set()

    def capabilities(self) -> IsolatedGenerationStorageCapabilities:
        return IsolatedGenerationStorageCapabilities(
            authority_id=self.attestation.authority_id,
            provider_id=self.attestation.provider_id,
            provider_contract_version=self.attestation.provider_contract_version,
            provider_distribution=self.attestation.provider_distribution,
            provider_version=self.attestation.provider_version,
            auxiliary_target_digest=self.target.auxiliary_target_digest,
            runtime_config_digest=self.attestation.runtime_config_digest,
            control_plane="embedded-postgresql",
            redirect_hops=0,
            fresh_generation_isolation=True,
        )

    def generation_exists(self, generation_id: str) -> bool:
        return _workspace_exists(self.engine, generation_id)

    def begin_generation(
        self,
        generation_id: str,
        *,
        workspace: str,
        manifest_digest: str,
    ) -> None:
        if (
            generation_id != self.plan.generation_id
            or workspace != generation_id
            or manifest_digest != self.plan.manifest_digest
            or WORKSPACE_PATTERN.fullmatch(workspace) is None
        ):
            raise VerificationError("official storage begin binding mismatch")
        if self.generation_exists(generation_id):
            raise SafetyError("official generation workspace is not empty")
        binding = ActiveGenerationBinding(
            generation_id=generation_id,
            pointer_version=1,
            pointer_updated_at=datetime.now(UTC),
            authority_id=self.attestation.authority_id,
            provider_id=self.attestation.provider_id,
            provider_contract_version=self.attestation.provider_contract_version,
            provider_distribution=self.attestation.provider_distribution,
            provider_version=self.attestation.provider_version,
            workspace=workspace,
            auxiliary_target_digest="sha256:" + self.target.auxiliary_target_digest,
            runtime_config_digest=self.attestation.runtime_config_digest,
            manifest_digest="sha256:" + self.plan.manifest_digest,
            corpus_digest="sha256:" + self.plan.snapshot.corpus_digest,
            source_ledger_digest="sha256:" + self.plan.snapshot.source_ledger_digest,
            projection_digest="sha256:" + self.plan.snapshot.projection_digest,
            pgvector_digest="sha256:" + self.plan.snapshot.pgvector_digest,
            document_count=self.plan.snapshot.document_count,
            registration_digest="sha256:" + hashlib.sha256(
                f"{generation_id}:{manifest_digest}".encode()
            ).hexdigest(),
        )
        self.runtimes[generation_id] = self.factory(binding)

    def ingest_batch(
        self, generation_id: str, documents: Sequence[CorpusDocument]
    ) -> None:
        runtime = self.runtimes[generation_id]
        for document in documents:
            result = asyncio.run(
                runtime.index.index_document(
                    LightRAGIndexDocument(
                        document.postgres_record_id,
                        document.content,
                        document.source_uri,
                    )
                )
            )
            if result.status is not LightRAGCallStatus.OK:
                raise VerificationError("official LightRAG indexing did not succeed")
            receipt = result.require_value()
            if receipt.postgres_record_id != document.postgres_record_id:
                raise VerificationError("official LightRAG receipt identity mismatch")
            if isinstance(runtime.index, SubprocessLightRAGIndex):
                pid = runtime.index.worker_pid
                if pid is not None:
                    self.pids.add(pid)

    def inspect_generation(self, generation_id: str) -> IsolatedGenerationInspection:
        inspection = inspect_generation_workspace(
            self.engine,
            workspace=generation_id,
            expected=self.plan.snapshot,
        )
        self.inspections[generation_id] = inspection
        identities = tuple(
            document.postgres_record_id for document in self.plan.snapshot.documents
        )
        return IsolatedGenerationInspection(
            generation_id=generation_id,
            workspace=generation_id,
            auxiliary_target_digest=self.target.auxiliary_target_digest,
            manifest_digest=self.plan.manifest_digest,
            document_count=cast(int, inspection["documentCount"]),
            processed_count=cast(int, inspection["processedCount"]),
            failed_count=cast(int, inspection["failedCount"]),
            corpus_digest=str(inspection["corpusDigest"]),
            processed_document_ids=identities,
        )

    def discard_generation(self, generation_id: str) -> None:
        runtime = self.runtimes.pop(generation_id, None)
        if runtime is not None:
            finalize = getattr(runtime.index, "finalize", None)
            if callable(finalize):
                asyncio.run(finalize())
        graph = _workspace_graph_name(generation_id)
        with self.engine.begin() as connection:
            for table in (
                "lightrag_llm_cache",
                "lightrag_full_relations",
                "lightrag_full_entities",
                "lightrag_doc_status",
                VECTOR_RELATION_TABLE,
                VECTOR_ENTITY_TABLE,
                VECTOR_CHUNKS_TABLE,
                "lightrag_doc_chunks",
                "lightrag_doc_full",
            ):
                if connection.execute(
                    text("SELECT to_regclass(:table) IS NOT NULL"), {"table": table}
                ).scalar_one():
                    connection.execute(
                        text(f"DELETE FROM {table} WHERE workspace=:workspace"),
                        {"workspace": generation_id},
                    )
            if connection.execute(
                text("SELECT EXISTS (SELECT 1 FROM ag_catalog.ag_graph WHERE name=:name)"),
                {"name": graph},
            ).scalar_one():
                connection.execute(
                    text("SELECT ag_catalog.drop_graph(:name, true)"), {"name": graph}
                )
        if _workspace_exists(self.engine, generation_id):
            raise SafetyError("discard left an official generation workspace")

    def finalize(self) -> dict[str, object]:
        for runtime in tuple(self.runtimes.values()):
            finalize = getattr(runtime.index, "finalize", None)
            if callable(finalize):
                asyncio.run(finalize())
        self.runtimes.clear()
        alive = [pid for pid in self.pids if _process_alive(pid)]
        if alive:
            raise SafetyError("official provider subprocess remained after finalize")
        return {"observedProcessCount": len(self.pids), "allProcessesAbsent": True}


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class _FailAfterActivationController(LedgerBackedGenerationController):
    def _after_activation(self) -> None:
        raise RuntimeError("injected post-CAS activation failure")


class _FailBeforeRollbackController(LedgerBackedGenerationController):
    def _before_rollback(self) -> None:
        raise RuntimeError("injected rollback failure")


class _DelayedIndex:
    def __init__(
        self,
        inner: Any,
        result_ready: threading.Event,
        release: threading.Event,
    ) -> None:
        self.inner = inner
        self.result_ready = result_ready
        self.release = release

    async def search(
        self, query: str, *, mode: str = "mix"
    ) -> LightRAGCallResult[LightRAGSearchCandidate]:
        result = await self.inner.search(query, mode=mode)
        self.result_ready.set()
        await asyncio.to_thread(self.release.wait, 30)
        return result

    async def index_document(self, document: LightRAGIndexDocument) -> Any:
        return await self.inner.index_document(document)

    async def finalize(self) -> Any:
        return await self.inner.finalize()


class _DelayedFactory:
    def __init__(
        self,
        inner: IsolatedLightRAGGenerationRuntimeFactory,
        result_ready: threading.Event,
        release: threading.Event,
    ) -> None:
        self.inner = inner
        self.result_ready = result_ready
        self.release = release

    def validate_binding(self, binding: ActiveGenerationBinding) -> None:
        self.inner.validate_binding(binding)

    def __call__(self, binding: ActiveGenerationBinding) -> LightRAGGenerationRuntime:
        runtime = self.inner(binding)
        return LightRAGGenerationRuntime(
            binding=binding,
            index=_DelayedIndex(runtime.index, self.result_ready, self.release),
        )


class _CapturingFactory:
    def __init__(self, inner: IsolatedLightRAGGenerationRuntimeFactory) -> None:
        self.inner = inner
        self.index: SubprocessLightRAGIndex | None = None

    def validate_binding(self, binding: ActiveGenerationBinding) -> None:
        self.inner.validate_binding(binding)

    def __call__(self, binding: ActiveGenerationBinding) -> LightRAGGenerationRuntime:
        runtime = self.inner(binding)
        if not isinstance(runtime.index, SubprocessLightRAGIndex):
            raise VerificationError("captured provider is not the production child index")
        self.index = runtime.index
        return runtime


class _EmbeddingProvider:
    async def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        normalized = tuple(text.strip() for text in texts)
        return EmbeddingBatch(
            model="bge-m3:latest",
            dimension=1024,
            vectors=tuple(deterministic_embedding_vector(text) for text in normalized),
            content_hashes=tuple(
                hashlib.sha256(text.encode()).hexdigest() for text in normalized
            ),
        )


def _query(resolver: LightRAGRuntimeResolver, query: str) -> LightRAGCallResult[Any]:
    return asyncio.run(resolver.search(query, mode="naive"))


def _resolver_smoke(
    control: DurableLightRAGGenerationControlPlane,
    factory: IsolatedLightRAGGenerationRuntimeFactory,
    expected_generation: str,
    expected_ids: set[str],
) -> tuple[dict[str, object], tuple[LightRAGRuntimeResolver, LightRAGRuntimeResolver]]:
    api = LightRAGRuntimeResolver(control, factory)
    worker = LightRAGRuntimeResolver(control, factory)
    results = {"api": _query(api, WAVE8_TOKEN), "worker": _query(worker, WAVE8_TOKEN)}
    public: dict[str, object] = {}
    for role, result in results.items():
        if result.status is not LightRAGCallStatus.OK:
            raise VerificationError("generation resolver query smoke failed")
        candidate = result.require_value()
        if not expected_ids.intersection(candidate.postgres_record_ids):
            raise VerificationError("generation query omitted canonical citation identity")
        public[role] = {
            "generationId": expected_generation,
            "status": result.status.value,
            "citationIdentityCount": len(candidate.postgres_record_ids),
            "citationIdentitySetDigest": _digest_record(
                sorted(candidate.postgres_record_ids),
                version="tc.lightrag.query-citations.v1",
            ),
            "contextSha256": hashlib.sha256(candidate.context.encode()).hexdigest(),
            "rawContextOmitted": True,
        }
    return public, (api, worker)


def _finalize_resolvers(resolvers: Sequence[LightRAGRuntimeResolver]) -> None:
    for resolver in resolvers:
        asyncio.run(resolver.finalize())


def _stale_result_check(
    *,
    control: DurableLightRAGGenerationControlPlane,
    factory: IsolatedLightRAGGenerationRuntimeFactory,
    activate_new: Callable[[], None],
) -> dict[str, object]:
    ready = threading.Event()
    release = threading.Event()
    resolver = LightRAGRuntimeResolver(control, _DelayedFactory(factory, ready, release))
    result: list[LightRAGCallResult[Any]] = []

    def run() -> None:
        result.append(_query(resolver, WAVE8_TOKEN))

    thread = threading.Thread(target=run, name="pf-sf1-t09-wave8-stale-result")
    thread.start()
    if not ready.wait(30):
        release.set()
        thread.join(timeout=5)
        raise VerificationError("stale-result query did not reach its injection gate")
    activate_new()
    release.set()
    thread.join(timeout=30)
    if thread.is_alive() or len(result) != 1:
        raise VerificationError("stale-result query did not terminate")
    asyncio.run(resolver.finalize())
    if result[0].status is not LightRAGCallStatus.UNAVAILABLE:
        raise VerificationError("stale provider result was exposed after pointer change")
    return {
        "injection": "POINTER_CHANGED_AFTER_PROVIDER_RESULT",
        "status": result[0].status.value,
        "staleContextExposed": False,
        "fallback": "typed_graph+pgvector",
    }


def _fallback_check(
    *,
    source_engine: Engine,
    resolver: LightRAGRuntimeResolver,
) -> dict[str, object]:
    with Session(source_engine) as session:
        result = asyncio.run(
            KnowledgeService(
                session,
                embedding_provider=_EmbeddingProvider(),
                light_rag=resolver,
            ).hybrid_search(
                query=WAVE8_TOKEN,
                statuses=("ACTIVE",),
                source_types=(),
                node_types=(),
                feature_id=None,
                limit=10,
                cursor=None,
            )
        )
    if (
        not result.degraded
        or "LIGHTRAG_UNAVAILABLE" not in result.degradation_reasons
        or not result.matches
        or any(
            not {"TYPED_GRAPH", "VECTOR"}.issubset(match.signals)
            for match in result.matches
        )
    ):
        raise VerificationError("typed graph plus pgvector fallback was incomplete")
    return {
        "degraded": True,
        "degradationReasons": list(result.degradation_reasons),
        "matchCount": len(result.matches),
        "typedGraphAndPgvector": True,
        "rawResultsOmitted": True,
    }


def _workspace_inventory(engine: Engine) -> dict[str, object]:
    with engine.connect() as connection:
        tables = WORKSPACE_TABLES
        table_rows: dict[str, list[tuple[str, int]]] = {}
        for table in tables:
            rows = connection.execute(
                text(
                    f"SELECT workspace, count(*) FROM {table} "
                    "GROUP BY workspace ORDER BY workspace"
                )
            ).all()
            table_rows[table] = [(str(row[0]), int(row[1])) for row in rows]
        graphs = [
            str(row[0])
            for row in connection.execute(
                text(
                    "SELECT name FROM ag_catalog.ag_graph "
                    "WHERE name LIKE 'pf_sf1_t09_wave8_%' ORDER BY name"
                )
            ).all()
        ]
    workspaces = sorted(
        {workspace for rows in table_rows.values() for workspace, _count in rows}
    )
    if any(WORKSPACE_PATTERN.fullmatch(workspace) is None for workspace in workspaces):
        raise SafetyError("auxiliary inventory includes an unexpected workspace")
    return {
        "workspaces": workspaces,
        "workspaceCount": len(workspaces),
        "graphCount": len(graphs),
        "digest": _digest_record(
            {"tables": table_rows, "graphs": graphs},
            version="tc.lightrag.workspace-inventory.v1",
        ),
    }


def _target_from_args(args: argparse.Namespace) -> tuple[Resources, Path, Path]:
    resources = Resources(str(args.run_id), int(args.source_port), int(args.auxiliary_port))
    temp_directory = Path(str(args.temp_directory)).resolve()
    dump_path = Path(str(args.dump_file)).resolve()
    if (
        temp_directory.parent not in {Path("/tmp"), Path("/private/tmp")}
        or WAVE8_TOKEN not in temp_directory.name
        or dump_path.parent != temp_directory
        or dump_path.suffix != ".dump"
    ):
        raise SafetyError("wave-8 temp directory/dump must be an exact /tmp-scoped path")
    return resources, temp_directory, dump_path


def _plan(
    resources: Resources,
    *,
    temp_directory: Path,
    dump_path: Path,
    output: Path,
) -> dict[str, object]:
    public_resources = resources.public_dict()
    containers = cast(list[str], public_resources["containers"])
    volumes = cast(list[str], public_resources["volumes"])
    return {
        "ok": True,
        "schemaVersion": SCHEMA,
        "task": TASK,
        "mode": "preview",
        "executes": False,
        "subGate": "LOCAL_DISPOSABLE_NETWORK_FREE",
        "target": public_resources,
        "composeFile": str(COMPOSE_FILE.relative_to(ROOT)),
        "tempDirectory": str(temp_directory),
        "dumpFile": str(dump_path),
        "evidenceFile": str(output.relative_to(ROOT)),
        "confirmationRequired": _confirmation(resources),
        "steps": [
            "reject collisions and occupied loopback ports",
            "fingerprint current tc and all prior Phase F JSON evidence",
            "create two exact disposable PostgreSQL 16 containers and volumes",
            "read-only dump tc and restore it into the disposable source",
            "prove restored copy equality before seeding a non-sensitive local corpus",
            "initialize/index official LightRAG PostgreSQL AGE+pgvector workspaces",
            "register immutable generations and exercise CAS cutover/rollback",
            "exercise separate API/worker resolvers, stale-result fencing, and fallback",
            "write one private single-link mode-0600 secret-free evidence file",
            "retain exact resources for separately previewed evidence-bound cleanup",
        ],
        "mutates": [
            *containers,
            *volumes,
            resources.network,
            resources.source_database,
            resources.auxiliary_database,
            str(temp_directory),
            str(dump_path),
            str(output),
        ],
        "doesNotMutate": [
            "current tc database",
            "pre-existing Docker resources",
            "prior Phase F evidence",
            "staging/live services",
        ],
        "networkPolicy": "LOOPBACK_ONLY_NO_MODEL_PROVIDER",
        "releaseGates": "UNCHANGED_NOT_RUN",
    }


def _secret_safe_payload(payload: Mapping[str, object], forbidden: Sequence[str]) -> None:
    encoded = _canonical_bytes(payload).decode()
    if any(value and value in encoded for value in forbidden):
        raise SafetyError("secret or corpus value reached wave-8 evidence")
    if "postgresql://" in encoded or "postgresql+psycopg://" in encoded:
        raise SafetyError("database URL reached wave-8 evidence")


def _execute(args: argparse.Namespace) -> dict[str, object]:
    resources, temp_directory, dump_path = _target_from_args(args)
    output = _safe_evidence_output(str(args.output))
    if output is None:
        raise SafetyError("wave-8 evidence output is required")
    plan = _plan(
        resources,
        temp_directory=temp_directory,
        dump_path=dump_path,
        output=output,
    )
    if not args.execute:
        return plan
    if args.confirm != plan["confirmationRequired"]:
        raise SafetyError("wave-8 execute confirmation differs from preview")
    if temp_directory.exists() or dump_path.exists():
        raise SafetyError("wave-8 temp or dump path already exists")
    _assert_no_collisions(resources)
    _assert_ports_free(resources)
    current, current_client = _current_database()
    source_image_id = _image_id(SOURCE_IMAGE)
    auxiliary_image_id = _image_id(AUXILIARY_IMAGE)
    before_docker = _docker_inventory()
    before_databases = _database_inventory(current)
    before_tc = _capture_database_fingerprint(current, client=current_client)
    prior_evidence = _prior_evidence_inventory(exclude={output})
    fingerprint_tables = cast(list[dict[str, object]], before_tc["tables"])
    current_corpus_count = next(
        (
            cast(int, entry["rowCount"])
            for entry in fingerprint_tables
            if entry["table"] == "knowledge_nodes"
        ),
        -1,
    )
    if current_corpus_count != 0:
        raise SafetyError(
            "wave-8 fixture route requires the audited current tc corpus to remain empty"
        )

    source_password = secrets.token_urlsafe(32)
    auxiliary_password = secrets.token_urlsafe(32)
    compose_environment = _compose_environment(
        resources,
        source_password=source_password,
        auxiliary_password=auxiliary_password,
        source_image_id=source_image_id,
        auxiliary_image_id=auxiliary_image_id,
    )
    source_database = _database_target(
        resources, source=True, password=source_password
    )
    auxiliary_database = _database_target(
        resources, source=False, password=auxiliary_password
    )
    source_engine: Engine | None = None
    auxiliary_engine: Engine | None = None
    storages: list[OfficialGenerationStorage] = []
    resolvers: list[LightRAGRuntimeResolver] = []
    started_at = datetime.now(UTC)
    started = time.perf_counter()
    temp_directory.mkdir(mode=0o700)
    if stat.S_IMODE(os.lstat(temp_directory).st_mode) != 0o700:
        raise SafetyError("wave-8 temp directory mode is not 0700")
    runtime_root = temp_directory / f"{WAVE8_TOKEN}-{resources.run_id}-runtimes"
    runtime_root.mkdir(mode=0o700)
    _command(
        _compose_command(resources, "up", "--detach", "--wait", "--wait-timeout", "180"),
        environment=compose_environment,
    )
    _assert_owned_resources(resources)
    try:
        dump = _restore_current_database(
            current=current,
            current_client=current_client,
            resources=resources,
            dump_path=dump_path,
        )
        _set_database_comment_and_extensions(
            source_database,
            comment=resources.target().authoritative_comment,
            extensions=("vector",),
        )
        _set_database_comment_and_extensions(
            auxiliary_database,
            comment=resources.target().auxiliary_comment,
            extensions=("vector", "age"),
        )
        source_engine = create_engine(source_database.url, pool_pre_ping=True)
        auxiliary_engine = create_engine(auxiliary_database.url, pool_pre_ping=True)
        active_source_engine = source_engine
        restored = _capture_database_fingerprint(source_database, client=current_client)
        restore_attestation = _logical_restore_attestation(
            current=current,
            restored=source_database,
            current_fingerprint=before_tc,
            restored_fingerprint=restored,
        )
        fixture_counts = _seed_disposable_corpus(source_engine, resources)
        target = resources.target()
        snapshot = PostgresSnapshotReader(source_engine, target=target).snapshot()
        source_before = PostgresSnapshotReader(source_engine, target=target).snapshot()
        if source_before != snapshot:
            raise VerificationError("pinned disposable source snapshot was not repeatable")
        auxiliary_preflight = _attest_database(
            auxiliary_database,
            expected_comment=target.auxiliary_comment,
            extensions={"age", "vector"},
            require_empty=True,
        )
        source_preflight = _attest_database(
            source_database,
            expected_comment=target.authoritative_comment,
            extensions={"vector"},
            require_empty=False,
        )
        config = IsolatedLightRAGGenerationConfig(
            enabled=True,
            authority_id=target.name,
            auxiliary_target_digest=target.auxiliary_target_digest,
            provider_id=OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID,
            postgres=LightRAGSubprocessPostgresCredentials(
                host="127.0.0.1",
                port=resources.auxiliary_port,
                user=resources.auxiliary_user,
                password=SecretStr(auxiliary_password),
                database=resources.auxiliary_database,
                max_connections=6,
                ssl_mode="disable",
            ),
            storage=LightRAGSubprocessStorageConfig(apache_age_available=True),
            runtime_bootstrap=BOOTSTRAP,
            deployment_environment="local",
            working_directory_root=runtime_root,
            startup_timeout_seconds=30,
            operation_timeout_seconds=30,
            shutdown_timeout_seconds=2,
        )
        factory = IsolatedLightRAGGenerationRuntimeFactory((config,))
        control = DurableLightRAGGenerationControlPlane(source_engine)

        prior_plan = ReindexPlan.create(
            target=target,
            snapshot=snapshot,
            generation_prefix=resources.generation_prefix,
        )
        prior_storage = OfficialGenerationStorage(
            plan=prior_plan,
            target=target,
            engine=auxiliary_engine,
            factory=factory,
        )
        storages.append(prior_storage)
        prior_controller = LedgerBackedGenerationController(
            plan=prior_plan,
            source=PostgresSnapshotReader(source_engine, target=target),
            storage=prior_storage,
            control_plane=control,
            actor_id=f"{WAVE8_TOKEN}-local-owner",
        )
        prior_report = FullReindexWorkflow(
            PostgresSnapshotReader(source_engine, target=target)
        ).apply(
            prior_plan,
            prior_controller,
            confirmation=prior_plan.confirmation_required,
            expected_manifest_digest=prior_plan.manifest_digest,
        )
        prior_binding = control.active_generation()
        if prior_binding.generation_id != prior_plan.generation_id:
            raise VerificationError("prior generation did not become active")

        next_target = resources.target(workspace=prior_plan.generation_id)
        next_snapshot = PostgresSnapshotReader(
            source_engine, target=next_target
        ).snapshot()
        next_plan = ReindexPlan.create(
            target=next_target,
            snapshot=next_snapshot,
            generation_prefix=resources.generation_prefix,
        )
        next_storage = OfficialGenerationStorage(
            plan=next_plan,
            target=next_target,
            engine=auxiliary_engine,
            factory=factory,
        )
        storages.append(next_storage)
        next_controller = LedgerBackedGenerationController(
            plan=next_plan,
            source=PostgresSnapshotReader(source_engine, target=next_target),
            storage=next_storage,
            control_plane=control,
            actor_id=f"{WAVE8_TOKEN}-local-owner",
        )
        next_report: dict[str, object] = {}

        def activate_next() -> None:
            nonlocal next_report
            next_report = FullReindexWorkflow(
                PostgresSnapshotReader(active_source_engine, target=next_target)
            ).apply(
                next_plan,
                next_controller,
                confirmation=next_plan.confirmation_required,
                expected_manifest_digest=next_plan.manifest_digest,
            )

        stale = _stale_result_check(
            control=control,
            factory=factory,
            activate_new=activate_next,
        )
        expected_ids = {
            document.postgres_record_id for document in snapshot.documents
        }
        smoke, active_resolvers = _resolver_smoke(
            control,
            factory,
            next_plan.generation_id,
            expected_ids,
        )
        resolvers.extend(active_resolvers)

        rollback_failure_controller = _FailBeforeRollbackController(
            plan=next_plan,
            source=PostgresSnapshotReader(source_engine, target=next_target),
            storage=next_storage,
            control_plane=control,
            actor_id=f"{WAVE8_TOKEN}-rollback-failure-owner",
        )
        try:
            rollback_failure_controller.rollback_generation(
                expected_current=next_plan.generation_id,
                restore_generation=prior_plan.generation_id,
            )
        except RuntimeError:
            rollback_failure = {
                "injected": True,
                "pointerUnchanged": (
                    control.active_generation().generation_id == next_plan.generation_id
                ),
            }
        else:
            raise VerificationError("injected rollback failure unexpectedly succeeded")
        next_controller.rollback_generation(
            expected_current=next_plan.generation_id,
            restore_generation=prior_plan.generation_id,
        )
        rolled_back = control.active_generation()
        if rolled_back.generation_id != prior_plan.generation_id:
            raise VerificationError("CAS rollback did not restore the prior generation")

        failed_target = resources.target(workspace=next_plan.generation_id)
        failed_snapshot = PostgresSnapshotReader(
            source_engine, target=failed_target
        ).snapshot()
        failed_plan = ReindexPlan.create(
            target=failed_target,
            snapshot=failed_snapshot,
            generation_prefix=resources.generation_prefix,
        )
        failed_storage = OfficialGenerationStorage(
            plan=failed_plan,
            target=failed_target,
            engine=auxiliary_engine,
            factory=factory,
        )
        storages.append(failed_storage)
        failed_controller = _FailAfterActivationController(
            plan=failed_plan,
            source=PostgresSnapshotReader(source_engine, target=failed_target),
            storage=failed_storage,
            control_plane=control,
            actor_id=f"{WAVE8_TOKEN}-failure-owner",
        )
        try:
            FullReindexWorkflow(
                PostgresSnapshotReader(source_engine, target=failed_target)
            ).apply(
                failed_plan,
                failed_controller,
                confirmation=failed_plan.confirmation_required,
                expected_manifest_digest=failed_plan.manifest_digest,
            )
        except ActivationFailure as error:
            activation_failure = {
                "injected": True,
                "rollbackAttempted": error.rollback_attempted,
                "rollbackSucceeded": error.rollback_succeeded,
            }
        else:
            raise VerificationError("injected activation failure unexpectedly succeeded")
        if control.active_generation().generation_id != prior_plan.generation_id:
            raise VerificationError("activation failure did not restore the current generation")
        retained = {
            plan.generation_id: _workspace_exists(auxiliary_engine, plan.generation_id)
            for plan in (prior_plan, next_plan, failed_plan)
        }
        if not all(retained.values()):
            raise VerificationError("prior/current/failure generation retention is incomplete")
        rollback_smoke, rollback_resolvers = _resolver_smoke(
            control,
            factory,
            prior_plan.generation_id,
            expected_ids,
        )
        resolvers.extend(rollback_resolvers)
        _finalize_resolvers(resolvers)
        resolvers.clear()

        capturing_factory = _CapturingFactory(factory)
        process_failure_resolver = LightRAGRuntimeResolver(control, capturing_factory)
        warm = _query(process_failure_resolver, WAVE8_TOKEN)
        if warm.status is not LightRAGCallStatus.OK:
            raise VerificationError("process failure resolver warmup failed")
        captured_index = capturing_factory.index
        if captured_index is None or captured_index.worker_pid is None:
            raise VerificationError("production child process was not captured")
        os.kill(captured_index.worker_pid, signal.SIGKILL)
        deadline = time.monotonic() + 10
        while captured_index.worker_running and time.monotonic() < deadline:
            time.sleep(0.01)
        if captured_index.worker_running:
            raise VerificationError("injected child process did not terminate")
        child_process_fallback = _fallback_check(
            source_engine=source_engine,
            resolver=process_failure_resolver,
        )
        asyncio.run(process_failure_resolver.finalize())

        provider_failure_resolver = LightRAGRuntimeResolver(control, factory)
        warm = _query(provider_failure_resolver, WAVE8_TOKEN)
        if warm.status is not LightRAGCallStatus.OK:
            raise VerificationError("provider failure resolver warmup failed")
        _command(["docker", "stop", "--time", "1", resources.auxiliary_container])
        provider_storage_fallback = _fallback_check(
            source_engine=source_engine,
            resolver=provider_failure_resolver,
        )
        asyncio.run(provider_failure_resolver.finalize())
        _command(["docker", "start", resources.auxiliary_container])
        _wait_healthy(resources.auxiliary_container)
        recovery_resolver = LightRAGRuntimeResolver(control, factory)
        recovered = _query(recovery_resolver, WAVE8_TOKEN)
        asyncio.run(recovery_resolver.finalize())
        if recovered.status is not LightRAGCallStatus.OK:
            raise VerificationError("official provider did not recover after process restart")

        counterfeit = replace(
            failed_storage.capabilities(), provider_id="counterfeit-provider"
        )
        try:
            failed_controller._storage = cast(Any, _CapabilityOnlyStorage(counterfeit))
            failed_controller.capabilities()
        except LiveApplyUnsupported:
            counterfeit_rejected = True
        else:
            raise VerificationError("counterfeit provider identity was accepted")

        workspace_inventory = _workspace_inventory(auxiliary_engine)
        expected_workspaces = sorted(
            (prior_plan.generation_id, next_plan.generation_id, failed_plan.generation_id)
        )
        if workspace_inventory["workspaces"] != expected_workspaces:
            raise VerificationError("retained workspace inventory is not exact")
        prior_inspection = prior_storage.inspections[prior_plan.generation_id]
        cutover_inspection = next_storage.inspections[next_plan.generation_id]
        failed_activation_inspection = failed_storage.inspections[
            failed_plan.generation_id
        ]
        semantic_graph_agreement = _assert_semantic_graph_digest_agreement(
            {
                "prior": prior_inspection,
                "cutover": cutover_inspection,
                "failedActivation": failed_activation_inspection,
            }
        )
        source_after_flow = PostgresSnapshotReader(
            source_engine, target=target
        ).snapshot()
        if source_after_flow != snapshot:
            raise VerificationError("reindex flow changed the disposable source corpus")
        process_cleanup = [storage.finalize() for storage in storages]
        storages.clear()
        if any(runtime_root.iterdir()):
            raise SafetyError("provider runtime root is not empty after finalize")
        source_engine.dispose()
        auxiliary_engine.dispose()
        source_engine = None
        auxiliary_engine = None
        after_tc = _capture_database_fingerprint(current, client=current_client)
        after_databases = _database_inventory(current)
        if after_tc != before_tc or after_databases != before_databases:
            raise SafetyError("current tc database changed during wave-8 execution")
        if not _verify_prior_evidence(prior_evidence):
            raise SafetyError("prior evidence verification failed")

        payload = _seal(
            {
                "schemaVersion": SCHEMA,
                "task": TASK,
                "mode": "LOCAL_DISPOSABLE_NETWORK_FREE_PASS",
                "subGate": "LOCAL_DISPOSABLE_NETWORK_FREE",
                "releaseGate": "NOT_RUN",
                "target": resources.public_dict(),
                "confirmationRequired": plan["confirmationRequired"],
                "evidenceFile": str(output.relative_to(ROOT)),
                "tempDirectory": str(temp_directory),
                "dumpFile": str(dump_path),
                "provider": {
                    "id": OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID,
                    "distribution": LIGHTRAG_DISTRIBUTION,
                    "version": LIGHTRAG_PINNED_VERSION,
                    "storage": [
                        "PGKVStorage",
                        "PGVectorStorage",
                        "PGDocStatusStorage",
                        "PGGraphStorage",
                    ],
                    "networkFreeBootstrap": BOOTSTRAP,
                    "runtimeConfigDigest": config.runtime_config_digest,
                },
                "source": {
                    "currentTcCorpusDocumentCount": current_corpus_count,
                    "currentTcAccess": "READ_ONLY",
                    "restoredCopyEqualBeforeFixture": True,
                    "restoreAttestation": restore_attestation,
                    "fixtureVersion": FIXTURE_VERSION,
                    "fixtureCounts": fixture_counts,
                    "preflight": source_preflight,
                    "snapshot": snapshot.public_dict(),
                    "beforeAfterSnapshotEqual": True,
                    "rawCorpusOmitted": True,
                },
                "auxiliary": {
                    "emptyPreflight": auxiliary_preflight,
                    "priorInspection": prior_inspection,
                    "cutoverInspection": cutover_inspection,
                    "failedActivationInspection": failed_activation_inspection,
                    "semanticGraphDigestAgreement": semantic_graph_agreement,
                    "retainedWorkspaces": retained,
                    "workspaceInventory": workspace_inventory,
                },
                "controlPlane": {
                    "prior": _public_apply(prior_report),
                    "cutover": _public_apply(next_report),
                    "staleResult": stale,
                    "activationFailure": activation_failure,
                    "rollbackFailure": rollback_failure,
                    "rollback": {
                        "restoredGenerationId": prior_plan.generation_id,
                        "pointerVersion": rolled_back.pointer_version,
                        "priorRetained": retained[prior_plan.generation_id],
                        "cutoverRetained": retained[next_plan.generation_id],
                    },
                    "counterfeitProviderRejected": counterfeit_rejected,
                },
                "runtime": {
                    "activeGenerationSmoke": smoke,
                    "rollbackGenerationSmoke": rollback_smoke,
                    "childProcessFailureFallback": child_process_fallback,
                    "providerStorageFailureFallback": provider_storage_fallback,
                    "failureInjections": [
                        "CHILD_PROCESS_SIGKILL",
                        "AUXILIARY_POSTGRESQL_STOP",
                    ],
                    "providerRestartRecovered": True,
                    "separateResolverInstances": ["api", "worker"],
                    "processCleanup": process_cleanup,
                    "runtimeDirectoriesEmpty": True,
                },
                "dump": dump,
                "preservation": {
                    "beforeDockerInventory": before_docker,
                    "beforeDatabaseInventory": before_databases,
                    "beforeTcFingerprintDigest": _digest_record(
                        before_tc, version="tc.postgresql-fingerprint.v1"
                    ),
                    "afterDatabaseInventory": after_databases,
                    "afterTcFingerprintDigest": _digest_record(
                        after_tc, version="tc.postgresql-fingerprint.v1"
                    ),
                    "currentTcFingerprintEqual": True,
                    "priorEvidence": prior_evidence,
                    "priorEvidenceHashesPreserved": True,
                },
                "cleanup": {
                    "required": True,
                    "performed": False,
                    "evidenceBound": True,
                },
                "timing": {
                    "startedAtUtc": started_at.isoformat(),
                    "finishedAtUtc": datetime.now(UTC).isoformat(),
                    "localMeasuredSeconds": round(time.perf_counter() - started, 6),
                    "productionRtoRpo": "NOT_RUN",
                },
                "notRun": [
                    "staging credentials and bootstrap",
                    "live model provider calls",
                    "staging apply",
                    "production-like multiprocess networking/load/alerts",
                    "operator handoff",
                    "production RTO/RPO",
                    "PF-SF1-T09 release gate",
                    "Phase F release gate",
                ],
                "rawCorpusOmitted": True,
                "credentialsOmitted": True,
            }
        )
        forbidden = [
            source_password,
            auxiliary_password,
            current.url,
            source_database.url,
            auxiliary_database.url,
            *(document.content for document in snapshot.documents),
            *(document.source_uri for document in snapshot.documents),
        ]
        _secret_safe_payload(payload, forbidden)
        _write_json_evidence(output, payload)
        with PinnedReadFile(
            output,
            label="wave-8 evidence",
            maximum_size=8 * 1024 * 1024,
            require_nonempty=True,
        ) as evidence:
            evidence_sha = evidence.run_stage(
                "hash", lambda: _pinned_file_sha256(evidence)
            )
            file_safety = evidence.identity.evidence()
        return {
            **payload,
            "durableEvidenceVerification": {
                "fileSha256": evidence_sha,
                "fileSafety": file_safety,
                "verified": True,
            },
        }
    finally:
        _finalize_resolvers(resolvers)
        for storage in storages:
            try:
                storage.finalize()
            except Exception:
                pass
        if source_engine is not None:
            source_engine.dispose()
        if auxiliary_engine is not None:
            auxiliary_engine.dispose()
        source_password = ""
        auxiliary_password = ""


@dataclass(slots=True)
class _CapabilityOnlyStorage:
    value: IsolatedGenerationStorageCapabilities

    def capabilities(self) -> IsolatedGenerationStorageCapabilities:
        return self.value


def _public_apply(report: Mapping[str, object]) -> dict[str, object]:
    allowed = (
        "executionStatus",
        "manifestDigest",
        "generationId",
        "previousGenerationId",
        "documentCount",
        "corpusDigest",
        "sourceLedgerDigest",
        "projectionDigest",
        "pgvectorDigest",
        "activation",
        "controlPlane",
    )
    return {key: report[key] for key in allowed if key in report}


def _load_evidence(path_value: str) -> tuple[Path, dict[str, Any], str, dict[str, object]]:
    path = Path(path_value).resolve()
    try:
        path.relative_to(EVIDENCE_ROOT)
    except ValueError as error:
        raise SafetyError("wave-8 evidence path escaped the evidence root") from error
    with PinnedReadFile(
        path,
        label="wave-8 rehearsal evidence",
        maximum_size=8 * 1024 * 1024,
        require_nonempty=True,
    ) as source:
        raw = source.run_stage("read", source.read_bytes)
        sha256 = source.run_stage("hash", lambda: _pinned_file_sha256(source))
        safety = source.identity.evidence()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise SafetyError("wave-8 evidence JSON is malformed") from error
    return path, _verify_seal(payload, schema=SCHEMA), sha256, safety


def _cleanup(args: argparse.Namespace) -> dict[str, object]:
    evidence_path, evidence, evidence_sha, evidence_safety = _load_evidence(
        str(args.rehearsal_evidence)
    )
    target = evidence.get("target")
    if not isinstance(target, dict) or not isinstance(target.get("runId"), str):
        raise SafetyError("wave-8 evidence target is malformed")
    resources = Resources(
        target["runId"], int(args.source_port), int(args.auxiliary_port)
    )
    if target != resources.public_dict():
        raise SafetyError("cleanup target/resources/comments/workspace differ from evidence")
    output = _safe_evidence_output(str(args.output))
    if output is None:
        raise SafetyError("cleanup evidence output is required")
    confirmation = _confirmation(resources, cleanup=True)
    preview = {
        "ok": True,
        "schemaVersion": CLEANUP_SCHEMA,
        "task": TASK,
        "mode": "preview",
        "executes": False,
        "target": resources.public_dict(),
        "rehearsalEvidence": str(evidence_path.relative_to(ROOT)),
        "cleanupEvidence": str(output.relative_to(ROOT)),
        "confirmationRequired": confirmation,
        "mutates": [
            "only exact evidence-bound wave-8 containers/volumes/network",
            "only the exact evidence-bound dump and empty temp directory",
            "one new private cleanup evidence file",
        ],
    }
    if not args.execute:
        return preview
    if args.confirm != confirmation:
        raise SafetyError("cleanup confirmation differs from preview")
    if evidence.get("mode") != "LOCAL_DISPOSABLE_NETWORK_FREE_PASS":
        raise SafetyError("cleanup requires successful wave-8 rehearsal evidence")
    _assert_owned_resources(resources)
    auxiliary = evidence.get("auxiliary")
    preservation = evidence.get("preservation")
    dump = evidence.get("dump")
    if not all(isinstance(value, dict) for value in (auxiliary, preservation, dump)):
        raise SafetyError("cleanup evidence bindings are malformed")
    expected_workspace_inventory = cast(dict[str, Any], auxiliary).get(
        "workspaceInventory"
    )
    if not isinstance(expected_workspace_inventory, dict):
        raise SafetyError("cleanup workspace inventory is malformed")
    observed_workspace_inventory = _container_workspace_inventory(resources)
    if observed_workspace_inventory != expected_workspace_inventory:
        raise SafetyError("cleanup workspace inventory differs from durable evidence")
    _container_database_comment(
        resources.source_container,
        resources.source_user,
        resources.source_database,
        resources.target().authoritative_comment,
    )
    _container_database_comment(
        resources.auxiliary_container,
        resources.auxiliary_user,
        resources.auxiliary_database,
        resources.target().auxiliary_comment,
    )
    source_image_id = _image_id(SOURCE_IMAGE)
    auxiliary_image_id = _image_id(AUXILIARY_IMAGE)
    cleanup_environment = _compose_environment(
        resources,
        source_password="cleanup-placeholder-not-a-retained-credential",
        auxiliary_password="cleanup-placeholder-not-a-retained-credential",
        source_image_id=source_image_id,
        auxiliary_image_id=auxiliary_image_id,
    )
    _command(
        _compose_command(resources, "down", "--volumes", "--timeout", "5"),
        environment=cleanup_environment,
    )
    if any(_resource_labels(kind, name) is not None for kind, name in _resource_names(resources)):
        raise SafetyError("wave-8 Docker resource remained after cleanup")

    dump_path = Path(str(evidence.get("dumpFile", ""))).resolve()
    temp_directory = Path(str(evidence.get("tempDirectory", ""))).resolve()
    if dump_path.parent != temp_directory or WAVE8_TOKEN not in temp_directory.name:
        raise SafetyError("cleanup file paths differ from the wave-8 namespace")
    dump_contract = cast(dict[str, Any], dump)
    with PinnedReadFile(
        dump_path,
        label="wave-8 source dump",
        maximum_size=512 * 1024 * 1024,
        require_nonempty=True,
    ) as source:
        actual_sha = source.run_stage("hash", lambda: _pinned_file_sha256(source))
        if (
            actual_sha != dump_contract.get("sha256")
            or source.identity.evidence() != dump_contract.get("fileIdentity")
        ):
            raise SafetyError("cleanup dump identity or digest differs from evidence")
        identity = source.identity
    current_identity = FileIdentity.from_stat(os.lstat(dump_path))
    if current_identity != identity:
        raise SafetyError("cleanup dump pathname changed after pinned verification")
    dump_path.unlink()
    runtime_root = temp_directory / f"{WAVE8_TOKEN}-{resources.run_id}-runtimes"
    if runtime_root.exists():
        if any(runtime_root.iterdir()):
            raise SafetyError("cleanup runtime root is not empty")
        runtime_root.rmdir()
    if any(temp_directory.iterdir()):
        raise SafetyError("cleanup temp directory contains an unattested artifact")
    temp_directory.rmdir()

    current, current_client = _current_database()
    after_tc = _capture_database_fingerprint(current, client=current_client)
    after_databases = _database_inventory(current)
    after_docker = _docker_inventory()
    preservation_map = cast(dict[str, Any], preservation)
    if (
        _digest_record(after_tc, version="tc.postgresql-fingerprint.v1")
        != preservation_map.get("beforeTcFingerprintDigest")
        or after_databases != preservation_map.get("beforeDatabaseInventory")
        or after_docker != preservation_map.get("beforeDockerInventory")
    ):
        raise SafetyError("pre-existing database or Docker state changed")
    prior = preservation_map.get("priorEvidence")
    if not isinstance(prior, dict) or not _verify_prior_evidence(prior):
        raise SafetyError("prior Phase F evidence was not preserved")
    _, reread, reread_sha, _ = _load_evidence(str(evidence_path))
    if reread != evidence or reread_sha != evidence_sha:
        raise SafetyError("rehearsal evidence changed during cleanup")
    payload = _seal(
        {
            "schemaVersion": CLEANUP_SCHEMA,
            "task": TASK,
            "mode": "LOCAL_DISPOSABLE_NETWORK_FREE_CLEANUP_PASS",
            "releaseGate": "NOT_RUN",
            "target": resources.public_dict(),
            "confirmationRequired": confirmation,
            "consumedEvidence": {
                "path": str(evidence_path.relative_to(ROOT)),
                "fileSha256": evidence_sha,
                "fileSafety": evidence_safety,
                "contractDigest": evidence["contractDigest"],
            },
            "removed": {
                "containers": 2,
                "volumes": 2,
                "network": 1,
                "databasesWithContainers": 2,
                "workspacesWithAuxiliaryDatabase": len(
                    expected_workspace_inventory["workspaces"]
                ),
                "providerProcesses": "ABSENT_BEFORE_CLEANUP",
                "dump": True,
                "tempDirectory": True,
            },
            "absence": {
                "dockerResources": True,
                "disposableDatabases": True,
                "workspaces": True,
                "providerProcesses": True,
                "dump": True,
                "tempDirectory": True,
            },
            "preservation": {
                "dockerInventoryEqual": True,
                "databaseInventoryEqual": True,
                "currentTcFingerprintEqual": True,
                "priorEvidenceHashesPreserved": True,
                "afterDockerInventory": after_docker,
                "afterDatabaseInventory": after_databases,
                "afterTcFingerprintDigest": _digest_record(
                    after_tc, version="tc.postgresql-fingerprint.v1"
                ),
                "priorEvidence": prior,
            },
            "notRun": evidence["notRun"],
            "rawCorpusOmitted": True,
            "credentialsOmitted": True,
        }
    )
    _secret_safe_payload(payload, ())
    _write_json_evidence(output, payload)
    with PinnedReadFile(
        output,
        label="wave-8 cleanup evidence",
        maximum_size=8 * 1024 * 1024,
        require_nonempty=True,
    ) as cleanup:
        sha256 = cleanup.run_stage("hash", lambda: _pinned_file_sha256(cleanup))
        safety = cleanup.identity.evidence()
    return {
        **payload,
        "durableEvidenceVerification": {
            "fileSha256": sha256,
            "fileSafety": safety,
            "verified": True,
        },
    }


def _container_database_comment(
    container: str,
    user: str,
    database: str,
    expected_comment: str,
) -> None:
    result = _command(
        [
            "docker",
            "exec",
            container,
            "psql",
            "-U",
            user,
            "-d",
            database,
            "-Atqc",
            "SELECT COALESCE(shobj_description(oid, 'pg_database'), '') "
            "FROM pg_database WHERE datname=current_database()",
        ],
        capture=True,
    )
    if result.stdout.decode().strip() != expected_comment:
        raise SafetyError("cleanup database comment attestation mismatch")


def _container_workspace_inventory(resources: Resources) -> dict[str, object]:
    query = " UNION ALL ".join(
        f"SELECT '{table}', workspace, count(*) FROM {table} GROUP BY workspace"
        for table in WORKSPACE_TABLES
    )
    query += (
        "; SELECT 'GRAPH', name, 1 FROM ag_catalog.ag_graph "
        "WHERE name LIKE 'pf_sf1_t09_wave8_%' ORDER BY 1,2"
    )
    result = _command(
        [
            "docker",
            "exec",
            resources.auxiliary_container,
            "psql",
            "-U",
            resources.auxiliary_user,
            "-d",
            resources.auxiliary_database,
            "-AtF",
            "\t",
            "-c",
            query,
        ],
        capture=True,
    )
    table_rows: dict[str, list[tuple[str, int]]] = {
        table: []
        for table in WORKSPACE_TABLES
    }
    graphs: list[str] = []
    for line in result.stdout.decode().splitlines():
        kind, workspace, count = line.split("\t")
        if kind == "GRAPH":
            graphs.append(workspace)
        else:
            table_rows[kind].append((workspace, int(count)))
    for values in table_rows.values():
        values.sort()
    graphs.sort()
    workspaces = sorted(
        {workspace for rows in table_rows.values() for workspace, _count in rows}
    )
    return {
        "workspaces": workspaces,
        "workspaceCount": len(workspaces),
        "graphCount": len(graphs),
        "digest": _digest_record(
            {"tables": table_rows, "graphs": graphs},
            version="tc.lightrag.workspace-inventory.v1",
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preview/execute PF-SF1-T09 wave-8 local disposable rehearsal."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    rehearse = commands.add_parser("rehearse")
    cleanup = commands.add_parser("cleanup")
    for command in (rehearse, cleanup):
        command.add_argument("--run-id", required=True)
        command.add_argument("--source-port", required=True, type=int)
        command.add_argument("--auxiliary-port", required=True, type=int)
        command.add_argument("--output", required=True)
        command.add_argument("--execute", action="store_true")
        command.add_argument("--confirm")
    rehearse.add_argument("--temp-directory", required=True)
    rehearse.add_argument("--dump-file", required=True)
    cleanup.add_argument("--rehearsal-evidence", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "rehearse":
            result = _execute(args)
        else:
            result = _cleanup(args)
    except (SafetyError, VerificationError, LiveApplyUnsupported) as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(error),
                    "rawCorpusOmitted": True,
                    "credentialsOmitted": True,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    except Exception as error:  # pragma: no cover - secret-safe process boundary
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": error.__class__.__name__,
                    "rawCorpusOmitted": True,
                    "credentialsOmitted": True,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
