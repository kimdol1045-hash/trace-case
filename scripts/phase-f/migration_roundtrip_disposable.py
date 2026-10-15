"""PF-SF1-T10 Wave 9 local-disposable migration round-trip rehearsal.

Preview is credential-free and non-mutating. Execute reads the current loopback
``tc`` database only through a repeatable-read, read-only exported snapshot and
``pg_dump``, restores that snapshot into one generated PostgreSQL 16 container,
downgrades exactly one dynamically resolved Alembic edge, and rolls forward to
the original head. Cleanup consumes sealed evidence and removes only the exact
evidence-bound Docker resources and private dump/temp paths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import socket
import stat
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote, urlsplit

import psycopg
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import create_engine, text

from rehearse import (
    EVIDENCE_ROOT,
    ROOT,
    DatabaseTarget,
    FileIdentity,
    PinnedReadFile,
    SafetyError,
    _command_from_pinned_file,
    _command_to_new_file,
    _database_rows_fingerprint,
    _database_schema_fingerprint,
    _pinned_file_sha256,
    _postgresql_command,
    _resolve_postgresql_client,
    _resolve_non_symlink_path,
    _safe_evidence_output,
    _snapshot_metadata,
    _write_json_evidence,
)
from tc_api.main import create_app
from tc_backend.config import Settings
from tc_backend.startup import validate_startup
from tc_worker.main import build_worker

TASK = "PF-SF1-T10"
WAVE = "wave9"
TOKEN = "pf-sf1-t10-wave9"
ACTION = "LOCAL-DISPOSABLE-MIGRATION-ROUNDTRIP"
CLEANUP_ACTION = "LOCAL-DISPOSABLE-CLEANUP"
SCHEMA = "tc.phase-f.migration-wave9-evidence.v1"
CLEANUP_SCHEMA = "tc.phase-f.migration-wave9-cleanup.v1"
CANONICAL_DIGEST_VERSION = "tc.canonical-json.sha256.v1"
PRIOR_EVIDENCE_VERSION = "tc.phase-f.prior-evidence-inventory.v1"
DOCKER_INVENTORY_VERSION = "tc.docker-local-inventory.v1"
DATABASE_INVENTORY_VERSION = "tc.postgresql-database-inventory.v1"
ROLE_FINGERPRINT_VERSION = "tc.postgresql-role-catalog.v1"
NORMALIZED_DATA_VERSION = "tc.postgresql-wave9-normalized-data.v1"
MIGRATION_GRAPH_VERSION = "tc.alembic-single-head-edge.v1"
SOURCE_IMAGE = (
    "pgvector/pgvector:0.8.1-pg16@sha256:"
    "33198da2828a14c30348d2ccb4750833d5ed9a44c88d840a0e523d7417120337"
)
COMPOSE_FILE = ROOT / "deploy" / "operations" / "compose.wave9.yml"
ALEMBIC_CONFIG = ROOT / "backend" / "alembic.ini"
MIGRATION_ROOT = ROOT / "backend" / "migrations" / "versions"
RUN_PATTERN = re.compile(r"^[a-z0-9]{8,16}$")
REVISION_PATTERN = re.compile(r"^[0-9]{8}_[0-9]{4}$")
HEX_DIGEST = re.compile(r"^[a-f0-9]{64}$")
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
PRODUCTION_TOKENS = frozenset({"prod", "production", "live", "primary", "main"})
HEAD_TABLES = frozenset(
    {
        "lightrag_generations",
        "lightrag_active_generation_pointer",
        "lightrag_generation_transition_events",
    }
)
DOWNGRADE_DATA_EXCLUSIONS = HEAD_TABLES | {"alembic_version"}
HEAD_FUNCTIONS = frozenset(
    {
        "tc_lightrag_reject_immutable_mutation",
        "tc_lightrag_guard_transition_event_insert",
        "tc_lightrag_guard_active_pointer",
    }
)
HEAD_TRIGGERS = frozenset(
    {
        "trg_lightrag_generations_immutable",
        "trg_lightrag_generation_transition_events_immutable",
        "trg_lightrag_generation_transition_events_insert_guard",
        "trg_lightrag_active_generation_pointer_guard",
    }
)


class VerificationError(RuntimeError):
    """A migration or preservation invariant did not hold."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest(value: object, *, version: str) -> str:
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
        raise SafetyError("wave-9 evidence schema mismatch")
    digest = payload.get("contractDigest")
    body = {key: value for key, value in payload.items() if key != "contractDigest"}
    if digest != _digest_record(body, version=CANONICAL_DIGEST_VERSION):
        raise SafetyError("wave-9 evidence canonical digest mismatch")
    return cast(dict[str, Any], payload)


def _tokens(value: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", value.casefold()) if token}


def _reject_production_like(value: str, label: str) -> None:
    found = sorted(_tokens(value) & PRODUCTION_TOKENS)
    if found:
        raise SafetyError(
            f"{label} contains production-like token(s): {', '.join(found)}"
        )


@dataclass(frozen=True, slots=True)
class MigrationEdge:
    head: str
    predecessor: str
    head_file: str
    head_file_sha256: str
    graph_digest: dict[str, str]

    def public_dict(self) -> dict[str, object]:
        return {
            "head": self.head,
            "immediatePredecessor": self.predecessor,
            "headFile": self.head_file,
            "headFileSha256": self.head_file_sha256,
            "graphDigest": self.graph_digest,
            "downgradeSteps": 1,
            "upgradeSteps": 1,
        }


def _resolve_migration_edge(
    script: ScriptDirectory | None = None,
) -> MigrationEdge:
    graph = script or ScriptDirectory.from_config(Config(str(ALEMBIC_CONFIG)))
    heads = tuple(graph.get_heads())
    if len(heads) != 1:
        raise SafetyError("migration graph must have exactly one head")
    head = heads[0]
    if REVISION_PATTERN.fullmatch(head) is None:
        raise SafetyError("migration head revision has an unexpected identifier")
    head_revision = graph.get_revision(head)
    if head_revision is None:
        raise SafetyError("migration head revision is unknown")
    predecessors = tuple(head_revision._normalized_down_revisions)
    if len(predecessors) != 1:
        raise SafetyError("migration head must have exactly one immediate predecessor")
    predecessor = predecessors[0]
    if REVISION_PATTERN.fullmatch(predecessor) is None:
        raise SafetyError("migration predecessor has an unexpected identifier")
    if graph.get_revision(predecessor) is None:
        raise SafetyError("migration predecessor revision is unknown")
    if not callable(getattr(head_revision.module, "upgrade", None)) or not callable(
        getattr(head_revision.module, "downgrade", None)
    ):
        raise SafetyError("migration head lacks a callable upgrade or downgrade")
    head_path = Path(str(head_revision.path))
    try:
        relative = head_path.resolve(strict=True).relative_to(ROOT)
    except (FileNotFoundError, ValueError) as error:
        raise SafetyError("migration head file is outside the repository") from error
    metadata = os.lstat(head_path)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise SafetyError("migration head must be a regular single-link file")
    revision_rows = sorted(
        (
            revision.revision,
            sorted(revision._normalized_down_revisions),
        )
        for revision in graph.walk_revisions(base="base", head="heads")
    )
    return MigrationEdge(
        head=head,
        predecessor=predecessor,
        head_file=str(relative),
        head_file_sha256=hashlib.sha256(head_path.read_bytes()).hexdigest(),
        graph_digest=_digest_record(revision_rows, version=MIGRATION_GRAPH_VERSION),
    )


@dataclass(frozen=True, slots=True)
class Resources:
    run_id: str
    port: int

    def __post_init__(self) -> None:
        if RUN_PATTERN.fullmatch(self.run_id) is None:
            raise SafetyError("run ID must contain 8-16 lowercase alphanumeric characters")
        if any(token in self.run_id for token in PRODUCTION_TOKENS):
            raise SafetyError("run ID contains a production-like token")
        if isinstance(self.port, bool) or not 1 <= self.port <= 65_535:
            raise SafetyError("wave-9 port must be a valid TCP port")
        for value in self.public_names():
            if TOKEN not in value.replace("_", "-"):
                raise SafetyError("every wave-9 resource must contain the exact task token")
            _reject_production_like(value, "wave-9 resource")

    @property
    def target_name(self) -> str:
        return f"tc-phase-f-{TOKEN}-{self.run_id}-disposable"

    @property
    def project(self) -> str:
        return f"tc-{TOKEN}-{self.run_id}"

    @property
    def container(self) -> str:
        return f"tc-{TOKEN}-{self.run_id}-postgres-disposable"

    @property
    def volume(self) -> str:
        return f"tc-{TOKEN}-{self.run_id}-data-disposable"

    @property
    def network(self) -> str:
        return f"tc-{TOKEN}-{self.run_id}-network-disposable"

    @property
    def database(self) -> str:
        return f"tc_phase_f_pf_sf1_t10_wave9_{self.run_id}_test_disposable"

    @property
    def database_user(self) -> str:
        return "tc_wave9_migration"

    @property
    def database_comment(self) -> str:
        return f"tc-phase-f-rehearsal:disposable:{self.target_name}"

    def public_names(self) -> tuple[str, ...]:
        return (
            self.target_name,
            self.project,
            self.container,
            self.volume,
            self.network,
            self.database,
        )

    def public_dict(self) -> dict[str, object]:
        return {
            "runId": self.run_id,
            "targetName": self.target_name,
            "targetClass": "disposable",
            "project": self.project,
            "container": self.container,
            "volume": self.volume,
            "network": self.network,
            "database": self.database,
            "databaseComment": self.database_comment,
            "port": {"host": "127.0.0.1", "generated": self.port},
            "postgresqlMajor": 16,
        }


def _confirmation(resources: Resources, *, cleanup: bool = False) -> str:
    action = CLEANUP_ACTION if cleanup else ACTION
    return f"RUN {TASK} {action} ON {resources.target_name}"


def _safe_process_environment() -> dict[str, str]:
    allowed = {
        "DOCKER_CONFIG",
        "DOCKER_CONTEXT",
        "DOCKER_HOST",
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "SYSTEMROOT",
        "TMPDIR",
        "WINDIR",
    }
    return {name: value for name, value in os.environ.items() if name in allowed}


def _command(
    argv: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        list(argv),
        cwd=ROOT,
        env=dict(environment) if environment is not None else _safe_process_environment(),
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
        raise SafetyError("pinned local PostgreSQL image identity is invalid")
    return value


def _compose_environment(
    resources: Resources, *, password: str, image_id: str
) -> dict[str, str]:
    return {
        **_safe_process_environment(),
        "TC_PHASE_F_T10_WAVE9_PROJECT": resources.project,
        "TC_PHASE_F_T10_WAVE9_CONTAINER": resources.container,
        "TC_PHASE_F_T10_WAVE9_VOLUME": resources.volume,
        "TC_PHASE_F_T10_WAVE9_NETWORK": resources.network,
        "TC_PHASE_F_T10_WAVE9_DATABASE": resources.database,
        "TC_PHASE_F_T10_WAVE9_USER": resources.database_user,
        "TC_PHASE_F_T10_WAVE9_PASSWORD": password,
        "TC_PHASE_F_T10_WAVE9_PORT": str(resources.port),
        "TC_PHASE_F_T10_WAVE9_IMAGE_ID": image_id,
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


def _resource_names(resources: Resources) -> tuple[tuple[str, str], ...]:
    return (
        ("container", resources.container),
        ("volume", resources.volume),
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
    if any(
        _resource_labels(kind, name) is not None
        for kind, name in _resource_names(resources)
    ):
        raise SafetyError("a generated wave-9 Docker resource already exists")


def _assert_owned_resources(resources: Resources) -> None:
    expected = (resources.project, TASK, WAVE)
    for kind, name in _resource_names(resources):
        if _resource_labels(kind, name) != expected:
            raise SafetyError("refusing a non-exact or unowned wave-9 Docker resource")


def _labeled_resource_inventory(resources: Resources) -> dict[str, list[str]]:
    label = f"com.tc.phase-f-rehearsal={resources.project}"
    commands = {
        "containers": [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label={label}",
            "--format",
            "{{.Names}}",
        ],
        "volumes": [
            "docker",
            "volume",
            "ls",
            "--filter",
            f"label={label}",
            "--format",
            "{{.Name}}",
        ],
        "networks": [
            "docker",
            "network",
            "ls",
            "--filter",
            f"label={label}",
            "--format",
            "{{.Name}}",
        ],
    }
    return {
        kind: sorted(
            line for line in _command(command, capture=True).stdout.decode().splitlines() if line
        )
        for kind, command in commands.items()
    }


def _assert_exact_project_inventory(resources: Resources) -> dict[str, list[str]]:
    expected = {
        "containers": [resources.container],
        "volumes": [resources.volume],
        "networks": [resources.network],
    }
    observed = _labeled_resource_inventory(resources)
    if observed != expected:
        raise SafetyError("wave-9 project contains missing or unexpected resources")
    return observed


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
    rows = {
        kind: sorted(
            line for line in _command(command, capture=True).stdout.decode().splitlines() if line
        )
        for kind, command in commands.items()
    }
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
            checksum = source.run_stage("hash", lambda: _pinned_file_sha256(source))
        entries.append({"path": str(path.relative_to(ROOT)), "sha256": checksum})
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
    if settings.environment != "local":
        raise SafetyError("current tc source must declare the local environment")
    raw = str(settings.database_url)
    parsed = urlsplit(raw)
    if (
        parsed.scheme not in {"postgresql", "postgresql+psycopg"}
        or parsed.hostname not in LOOPBACK
        or parsed.path.removeprefix("/") != "tc"
        or parsed.query
        or parsed.fragment
    ):
        raise SafetyError("current tc source must be the exact loopback tc database")
    _reject_production_like(str(parsed.hostname), "current tc host")
    environment = {
        "PGCONNECT_TIMEOUT": "10",
        "PGDATABASE": "tc",
        "PGHOST": str(parsed.hostname),
        "PGPORT": str(parsed.port or 5432),
    }
    if parsed.username:
        environment["PGUSER"] = parsed.username
    if parsed.password:
        environment["PGPASSWORD"] = parsed.password
    libpq = raw.replace("postgresql+psycopg://", "postgresql://", 1)
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


def _database_target(resources: Resources, *, password: str) -> DatabaseTarget:
    encoded = quote(password, safe="")
    raw = (
        f"postgresql+psycopg://{resources.database_user}:{encoded}"
        f"@127.0.0.1:{resources.port}/{resources.database}"
    )
    return DatabaseTarget(
        resources.database,
        "GENERATED_IN_MEMORY",
        raw,
        raw.replace("postgresql+psycopg://", "postgresql://", 1),
        (
            ("PGCONNECT_TIMEOUT", "10"),
            ("PGDATABASE", resources.database),
            ("PGHOST", "127.0.0.1"),
            ("PGPASSWORD", password),
            ("PGPORT", str(resources.port)),
            ("PGUSER", resources.database_user),
        ),
    )


def _assert_port_free(resources: Resources) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.25)
        if probe.connect_ex(("127.0.0.1", resources.port)) == 0:
            raise SafetyError("the generated loopback port is already occupied")


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
    raise SafetyError("wave-9 PostgreSQL container did not become healthy")


def _set_database_comment(database: DatabaseTarget, comment: str) -> None:
    with psycopg.connect(database.libpq_url, autocommit=True) as connection:
        connection.execute(
            sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                sql.Identifier(database.expected_name), sql.Literal(comment)
            )
        )


def _require_database_identity(
    *,
    actual_name: str,
    expected_name: str,
    server_version_num: int,
    actual_comment: str,
    expected_comment: str,
) -> None:
    if actual_name != expected_name:
        raise SafetyError("disposable database identity mismatch")
    if server_version_num < 160_000:
        raise SafetyError("wave-9 requires PostgreSQL 16 or newer")
    if actual_comment != expected_comment:
        raise SafetyError("disposable database comment attestation mismatch")


def _revision_rows(connection: psycopg.Connection[Any]) -> tuple[str, ...]:
    exists = connection.execute(
        "SELECT to_regclass('public.alembic_version') IS NOT NULL"
    ).fetchone()
    if exists is None or exists[0] is not True:
        raise SafetyError("Alembic revision table is absent")
    rows = tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT version_num FROM alembic_version ORDER BY version_num"
        ).fetchall()
    )
    return _validate_revision_values(rows)


def _validate_revision_values(rows: tuple[str, ...]) -> tuple[str, ...]:
    if len(rows) != 1:
        raise SafetyError("Alembic revision table must contain exactly one revision")
    if REVISION_PATTERN.fullmatch(rows[0]) is None:
        raise SafetyError("database contains an unknown revision identifier")
    return rows


def _require_expected_revision(actual: str, expected: str, *, stage: str) -> None:
    if actual != expected:
        raise VerificationError(f"{stage} reached an unexpected revision")


def _require_empty_target(*, table_count: int, extensions: tuple[str, ...]) -> None:
    if table_count != 0 or extensions:
        raise SafetyError("disposable restore target must be exactly empty")


def _attest_target(
    database: DatabaseTarget,
    resources: Resources,
    *,
    require_empty: bool,
    expected_revision: str | None = None,
) -> dict[str, object]:
    with psycopg.connect(database.libpq_url) as connection:
        connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        identity = connection.execute(
            "SELECT current_database(), current_setting('server_version_num'), "
            "COALESCE(shobj_description(oid, 'pg_database'), '') "
            "FROM pg_database WHERE datname=current_database()"
        ).fetchone()
        if identity is None:
            raise SafetyError("disposable database identity probe returned no row")
        _require_database_identity(
            actual_name=str(identity[0]),
            expected_name=resources.database,
            server_version_num=int(identity[1]),
            actual_comment=str(identity[2]),
            expected_comment=resources.database_comment,
        )
        table_row = connection.execute(
            "SELECT count(*) FROM pg_tables WHERE schemaname='public'"
        ).fetchone()
        if table_row is None:
            raise SafetyError("disposable public-table count is unavailable")
        table_count = int(table_row[0])
        extensions = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT extname FROM pg_extension WHERE extname <> 'plpgsql' ORDER BY extname"
            ).fetchall()
        )
        revision: str | None = None
        if table_count:
            revision = _revision_rows(connection)[0]
        connection.rollback()
    if require_empty:
        _require_empty_target(table_count=table_count, extensions=extensions)
    if expected_revision is not None and revision != expected_revision:
        raise SafetyError("disposable database revision differs from the expected revision")
    return {
        "databaseName": resources.database,
        "databaseCommentAttested": True,
        "serverVersionNum": int(identity[1]),
        "publicTableCount": table_count,
        "extensions": list(extensions),
        "emptyTargetRequired": require_empty,
        "alembicRevision": revision,
    }


def _role_fingerprint(connection: psycopg.Connection[Any]) -> dict[str, object]:
    roles = connection.execute(
        "SELECT rolname, rolsuper, rolinherit, rolcreaterole, rolcreatedb, "
        "rolcanlogin, rolreplication, rolconnlimit, rolbypassrls "
        "FROM pg_roles ORDER BY rolname"
    ).fetchall()
    memberships = connection.execute(
        "SELECT parent.rolname, member.rolname, membership.admin_option "
        "FROM pg_auth_members membership "
        "JOIN pg_roles parent ON parent.oid=membership.roleid "
        "JOIN pg_roles member ON member.oid=membership.member "
        "ORDER BY parent.rolname, member.rolname"
    ).fetchall()
    payload = {
        "roles": [list(row) for row in roles],
        "memberships": [list(row) for row in memberships],
    }
    return {
        "version": ROLE_FINGERPRINT_VERSION,
        "roleCount": len(roles),
        "membershipCount": len(memberships),
        "digest": _digest_record(payload, version=ROLE_FINGERPRINT_VERSION),
    }


def _capture_fingerprint(
    database: DatabaseTarget,
) -> dict[str, object]:
    with psycopg.connect(database.libpq_url) as connection:
        connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        schema = _database_schema_fingerprint(connection)
        rows = _database_rows_fingerprint(connection)
        roles = _role_fingerprint(connection)
        connection.rollback()
    return {**schema, **rows, "roleCatalog": roles}


def _logical_column_catalog(
    database: DatabaseTarget,
) -> tuple[tuple[object, ...], ...]:
    with psycopg.connect(database.libpq_url) as connection:
        connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        rows = connection.execute(
            "SELECT c.relname, a.attname, format_type(a.atttypid,a.atttypmod), "
            "a.attnotnull, a.attidentity, a.attgenerated, "
            "COALESCE(pg_get_expr(d.adbin,d.adrelid,false),''), "
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
        raise VerificationError("disposable restore data differs from current tc")
    current_categories = current_fingerprint.get("schemaCategorySha256")
    restored_categories = restored_fingerprint.get("schemaCategorySha256")
    current_counts = current_fingerprint.get("schemaObjectCounts")
    restored_counts = restored_fingerprint.get("schemaObjectCounts")
    if not isinstance(current_categories, dict) or not isinstance(
        restored_categories, dict
    ):
        raise VerificationError("restored schema fingerprint is malformed")
    unexpected = sorted(
        category
        for category in current_categories
        if category != "columns"
        and current_categories.get(category) != restored_categories.get(category)
    )
    if set(current_categories) != set(restored_categories) or current_counts != restored_counts:
        raise VerificationError("disposable restore schema semantics differ")
    current_columns = _logical_column_catalog(current)
    restored_columns = _logical_column_catalog(restored)
    if unexpected or current_columns != restored_columns:
        raise VerificationError("disposable restore logical catalog differs")
    raw_schema_equal = (
        current_fingerprint.get("schemaSha256")
        == restored_fingerprint.get("schemaSha256")
    )
    return {
        "logicalDataExact": True,
        "logicalSchemaExact": True,
        "logicalColumnCount": len(current_columns),
        "logicalColumnDigest": _digest_record(
            current_columns, version="tc.postgresql.logical-columns.v1"
        ),
        "rawPhysicalSchemaFingerprintEqual": raw_schema_equal,
        "rawPhysicalSchemaDifference": (
            "NONE"
            if raw_schema_equal
            else "DROPPED_COLUMN_ATTRIBUTE_SLOTS_COMPACTED_BY_PG_DUMP"
        ),
    }


def _normalized_data_fingerprint(
    connection: psycopg.Connection[Any],
    *,
    exclude: frozenset[str] = frozenset(),
) -> dict[str, object]:
    table_names = tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT c.relname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relkind IN ('r','p') "
            "ORDER BY c.relname"
        ).fetchall()
        if str(row[0]) not in exclude
    )
    tables: list[dict[str, object]] = []
    combined = hashlib.sha256(NORMALIZED_DATA_VERSION.encode())
    total_rows = 0
    for table_name in table_names:
        if table_name == "lightrag_active_generation_pointer":
            expression = (
                "CASE WHEN pointer_version=0 AND active_generation_id IS NULL "
                "THEN to_jsonb(row_value)-'updated_at'||"
                "jsonb_build_object('updated_at','<PRISTINE_SEED_TIMESTAMP>') "
                "ELSE to_jsonb(row_value) END"
            )
        else:
            expression = "to_jsonb(row_value)"
        rows = connection.execute(
            sql.SQL("SELECT ({})::text FROM {} AS row_value ORDER BY 1").format(
                sql.SQL(expression), sql.Identifier(table_name)
            )
        ).fetchall()
        digest = hashlib.sha256()
        for (serialized,) in rows:
            digest.update((str(serialized) + "\n").encode())
        entry = {
            "table": table_name,
            "rowCount": len(rows),
            "sha256": digest.hexdigest(),
        }
        combined.update(_canonical_bytes(entry) + b"\n")
        tables.append(entry)
        total_rows += len(rows)
    return {
        "version": NORMALIZED_DATA_VERSION,
        "tableCount": len(tables),
        "totalRows": total_rows,
        "dataSha256": combined.hexdigest(),
        "tables": tables,
        "normalization": {
            "pristineLightRagPointerUpdatedAt": "REPLACED_WITH_SENTINEL",
            "otherValues": "EXACT",
        },
    }


def _capture_normalized_data(
    database: DatabaseTarget, *, exclude: frozenset[str] = frozenset()
) -> dict[str, object]:
    with psycopg.connect(database.libpq_url) as connection:
        connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        result = _normalized_data_fingerprint(connection, exclude=exclude)
        connection.rollback()
    return result


def _different_raw_data_tables(
    before: Mapping[str, object], after: Mapping[str, object]
) -> list[str]:
    def entries(value: Mapping[str, object]) -> dict[str, tuple[object, object]]:
        rows = value.get("tables")
        if not isinstance(rows, list):
            raise VerificationError("raw data fingerprint table list is malformed")
        result: dict[str, tuple[object, object]] = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("table"), str):
                raise VerificationError("raw data fingerprint table entry is malformed")
            result[str(row["table"])] = (row.get("rowCount"), row.get("sha256"))
        return result

    left = entries(before)
    right = entries(after)
    return sorted(
        table
        for table in set(left) | set(right)
        if left.get(table) != right.get(table)
    )


def _require_final_equality(
    *,
    baseline: Mapping[str, object],
    final: Mapping[str, object],
    normalized_baseline: Mapping[str, object],
    normalized_final: Mapping[str, object],
) -> list[str]:
    if baseline.get("schemaSha256") != final.get("schemaSha256"):
        raise VerificationError("final normalized catalog differs from baseline")
    if baseline.get("schemaObjectCounts") != final.get("schemaObjectCounts"):
        raise VerificationError("final schema object counts differ from baseline")
    if baseline.get("schemaCategorySha256") != final.get("schemaCategorySha256"):
        raise VerificationError("final schema category digests differ from baseline")
    if baseline.get("roleCatalog") != final.get("roleCatalog"):
        raise VerificationError("final PostgreSQL role catalog differs from baseline")
    if normalized_baseline != normalized_final:
        raise VerificationError("final normalized data differs from baseline")
    raw_differences = _different_raw_data_tables(baseline, final)
    if not set(raw_differences).issubset({"lightrag_active_generation_pointer"}):
        raise VerificationError("final raw data drift exceeds the documented seed timestamp")
    return raw_differences


def _require_preservation(
    *,
    expected_source_digest: Mapping[str, object],
    actual_source_fingerprint: Mapping[str, object],
    expected_databases: Mapping[str, object],
    actual_databases: Mapping[str, object],
    expected_docker: Mapping[str, object] | None = None,
    actual_docker: Mapping[str, object] | None = None,
) -> None:
    if _digest_record(
        actual_source_fingerprint, version="tc.postgresql-fingerprint.v1"
    ) != expected_source_digest or expected_databases != actual_databases:
        raise SafetyError("pre-existing database state changed")
    if expected_docker is not None and expected_docker != actual_docker:
        raise SafetyError("pre-existing Docker state changed")


def _source_snapshot_and_dump(
    *,
    current: DatabaseTarget,
    current_client: Any,
    dump_path: Path,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    connection: psycopg.Connection[Any] = psycopg.connect(current.libpq_url)
    connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
    try:
        snapshot: dict[str, object] = {
            **_snapshot_metadata(connection),
            "alembicRevision": _revision_rows(connection)[0],
        }
        source_fingerprint: dict[str, object] = {
            **_database_schema_fingerprint(connection),
            **_database_rows_fingerprint(connection),
            "roleCatalog": _role_fingerprint(connection),
        }
        command, environment = _postgresql_command(
            current_client,
            "pg_dump",
            current,
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            f"--snapshot={snapshot['snapshotId']}",
        )
        pg_environment = {
            **_safe_process_environment(),
            **{name: value for name, value in environment.items() if name.startswith("PG")},
        }
        _command_to_new_file(command, dump_path, env=pg_environment)
    finally:
        connection.rollback()
        connection.close()
    with PinnedReadFile(
        dump_path,
        label="wave-9 source dump",
        maximum_size=512 * 1024 * 1024,
        require_nonempty=True,
    ) as source:
        checksum = source.run_stage("hash", lambda: _pinned_file_sha256(source))
        identity = source.identity.evidence()
    dump = {
        "sha256": checksum,
        "bytes": identity["size"],
        "fileIdentity": identity,
        "sourceAccess": "EXPORTED_REPEATABLE_READ_READ_ONLY_SNAPSHOT_AND_PG_DUMP",
    }
    return snapshot, source_fingerprint, dump


def _restore_dump(
    *, resources: Resources, dump_path: Path
) -> None:
    with PinnedReadFile(
        dump_path,
        label="wave-9 source dump",
        maximum_size=512 * 1024 * 1024,
        require_nonempty=True,
    ) as source:
        command = [
            "docker",
            "exec",
            "-i",
            resources.container,
            "pg_restore",
            "--exit-on-error",
            "--no-owner",
            "--no-privileges",
            "-U",
            resources.database_user,
            "-d",
            resources.database,
        ]
        source.run_stage(
            "restore",
            lambda: _command_from_pinned_file(
                command,
                source,
                env=_safe_process_environment(),
                capture=True,
            ),
        )


def _control_plane_safety(database: DatabaseTarget) -> dict[str, object]:
    with psycopg.connect(database.libpq_url) as connection:
        connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        generation_row = connection.execute(
            "SELECT count(*) FROM lightrag_generations"
        ).fetchone()
        transition_row = connection.execute(
            "SELECT count(*) FROM lightrag_generation_transition_events"
        ).fetchone()
        if generation_row is None or transition_row is None:
            raise SafetyError("head control-plane counts are unavailable")
        generation_count = int(generation_row[0])
        transition_count = int(transition_row[0])
        pointer = connection.execute(
            "SELECT count(*), count(*) FILTER (WHERE scope='GLOBAL' "
            "AND pointer_version=0 AND active_generation_id IS NULL) "
            "FROM lightrag_active_generation_pointer"
        ).fetchone()
        connection.rollback()
    if pointer is None:
        raise SafetyError("head control-plane pointer count is unavailable")
    _require_pristine_control_plane(
        generation_count=generation_count,
        transition_count=transition_count,
        pointer_count=int(pointer[0]),
        pristine_pointer_count=int(pointer[1]),
    )
    return {
        "generationRows": generation_count,
        "transitionRows": transition_count,
        "pointerRows": int(pointer[0]),
        "pristinePointerRows": int(pointer[1]),
        "safeDowngradeBoundary": True,
    }


def _require_pristine_control_plane(
    *,
    generation_count: int,
    transition_count: int,
    pointer_count: int,
    pristine_pointer_count: int,
) -> None:
    if (
        generation_count != 0
        or transition_count != 0
        or pointer_count != 1
        or pristine_pointer_count != 1
    ):
        raise SafetyError(
            "head downgrade is unsafe: LightRAG control-plane evidence is not pristine"
        )


def _alembic(database: DatabaseTarget, operation: str, revision: str) -> None:
    if operation not in {"downgrade", "upgrade"}:
        raise SafetyError("unexpected Alembic operation")
    if REVISION_PATTERN.fullmatch(revision) is None:
        raise SafetyError("unexpected Alembic target revision")
    environment = {
        **_safe_process_environment(),
        "TC_DATABASE_URL": database.url,
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(ALEMBIC_CONFIG),
            operation,
            revision,
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise VerificationError(f"Alembic {operation} failed")


def _downgrade_invariants(
    database: DatabaseTarget,
    *,
    edge: MigrationEdge,
    surviving_data_before: Mapping[str, object],
    roles_before: Mapping[str, object],
) -> dict[str, object]:
    with psycopg.connect(database.libpq_url) as connection:
        connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        revision = _revision_rows(connection)[0]
        table_presence: dict[str, bool] = {}
        for name in sorted(HEAD_TABLES):
            presence = connection.execute(
                "SELECT to_regclass(%s) IS NOT NULL", (f"public.{name}",)
            ).fetchone()
            if presence is None:
                raise VerificationError("head-table presence probe returned no row")
            table_presence[name] = bool(presence[0])
        functions = {
            str(row[0])
            for row in connection.execute(
                "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='public' AND p.proname=ANY(%s)",
                (sorted(HEAD_FUNCTIONS),),
            ).fetchall()
        }
        triggers = {
            str(row[0])
            for row in connection.execute(
                "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgname=ANY(%s)",
                (sorted(HEAD_TRIGGERS),),
            ).fetchall()
        }
        surviving_after = _normalized_data_fingerprint(
            connection, exclude=DOWNGRADE_DATA_EXCLUSIONS
        )
        roles_after = _role_fingerprint(connection)
        connection.rollback()
    _require_expected_revision(revision, edge.predecessor, stage="downgrade")
    if any(table_presence.values()) or functions or triggers:
        raise VerificationError("head-owned objects remain after downgrade")
    if surviving_after != surviving_data_before:
        raise VerificationError("downgrade changed data outside head-owned tables")
    if roles_after != roles_before:
        raise VerificationError("downgrade changed the PostgreSQL role catalog")
    return {
        "revision": revision,
        "headTablesAbsent": sorted(name for name, present in table_presence.items() if not present),
        "headFunctionsAbsent": sorted(HEAD_FUNCTIONS),
        "headTriggersAbsent": sorted(HEAD_TRIGGERS),
        "excludedRevisionBookkeepingTable": "alembic_version",
        "survivingDataExact": True,
        "roleCatalogExact": True,
    }


def _smoke(database: DatabaseTarget) -> dict[str, object]:
    settings = Settings.model_validate(
        {
            "environment": "local",
            "database_url": database.url,
            "encryption_master_key": "wave9-local-disposable-only-key-000000001",
            "slack_mode": "disabled",
            "object_storage_enabled": False,
            "automatic_knowledge_projection_enabled": False,
            "automatic_generation_enabled": False,
            "light_rag_enabled": False,
        }
    )
    capabilities = validate_startup(settings)
    engine = create_engine(database.url, pool_pre_ping=True)
    try:
        with TestClient(
            create_app(validate_database=False, database_engine=engine)
        ) as client:
            statuses = {
                "health": client.get("/api/health").status_code,
                "ready": client.get("/api/ready").status_code,
                "workspaceRead": client.get("/api/workspace/settings").status_code,
                "folderTreeRead": client.get("/api/folders/tree").status_code,
            }
        if set(statuses.values()) != {200}:
            raise VerificationError("final-head API read smoke failed")
        with engine.connect() as connection:
            transaction = connection.begin()
            changed = connection.execute(
                text(
                    "UPDATE workspace_settings SET updated_at=updated_at "
                    "WHERE singleton_key=1 RETURNING singleton_key"
                )
            ).fetchall()
            transaction.rollback()
        if len(changed) != 1:
            raise VerificationError("transactional write/rollback smoke failed")
        worker, worker_engine = build_worker(settings)
        try:
            worker_handlers = len(worker.handlers)
            worker.mark_stopped()
        finally:
            worker_engine.dispose()
    finally:
        engine.dispose()
    return {
        "databaseCapabilities": {
            "postgresql16OrNewer": capabilities.server_version_num >= 160_000,
            "pgvectorPresent": capabilities.vector_version is not None,
            "embeddingColumn": capabilities.embedding_column_type,
        },
        "apiStatusCodes": statuses,
        "transactionalWrite": {
            "rowsMatched": len(changed),
            "rolledBack": True,
        },
        "workerStartup": {
            "constructed": True,
            "handlerCount": worker_handlers,
            "externalIntegrationsEnabled": False,
            "jobsClaimed": 0,
        },
        "externalCalls": 0,
        "responseBodiesOmitted": True,
    }


def _target_from_args(args: argparse.Namespace) -> tuple[Resources, Path, Path]:
    resources = Resources(str(args.run_id), int(args.port))
    temp = Path(str(args.temp_directory)).resolve()
    dump = Path(str(args.dump_file)).resolve()
    if not temp.is_absolute() or temp.parent not in {Path("/tmp"), Path("/private/tmp")}:
        raise SafetyError("wave-9 temp directory must be a direct /tmp or /private/tmp child")
    if TOKEN not in temp.name or resources.run_id not in temp.name:
        raise SafetyError("wave-9 temp directory must bind the task token and run ID")
    if dump.parent != temp or dump.suffix != ".dump":
        raise SafetyError("wave-9 dump must be one .dump file inside the exact temp directory")
    if TOKEN not in dump.name or resources.run_id not in dump.name:
        raise SafetyError("wave-9 dump must bind the task token and run ID")
    return resources, temp, dump


def _plan(
    resources: Resources,
    *,
    edge: MigrationEdge,
    temp_directory: Path,
    dump_path: Path,
    output: Path,
) -> dict[str, object]:
    return {
        "ok": True,
        "schemaVersion": SCHEMA,
        "task": TASK,
        "mode": "preview",
        "executes": False,
        "subGate": "LOCAL_DISPOSABLE",
        "target": resources.public_dict(),
        "migration": edge.public_dict(),
        "confirmationRequired": _confirmation(resources),
        "evidenceFile": str(output.relative_to(ROOT)),
        "tempDirectory": str(temp_directory),
        "dumpFile": str(dump_path),
        "mutates": [
            resources.container,
            resources.volume,
            resources.network,
            resources.database,
            str(temp_directory),
            str(dump_path),
            str(output),
        ],
        "reads": [
            "real Alembic migration graph and head file",
            "current loopback tc through one exported repeatable-read read-only snapshot",
            "current loopback tc through pg_dump pinned to that snapshot",
            "pre-existing Docker/database inventory and prior Phase F evidence hashes",
        ],
        "steps": [
            "require one known head with one known immediate predecessor and safe downgrade callable",
            "require current tc at that exact head and a pristine head-owned control plane",
            "create one exact labeled loopback PostgreSQL 16 container/volume/network",
            "install exact database comment and attest a zero-table/zero-extension destination",
            "restore the pinned source dump without owner, privileges, clean, or create",
            "prove restored logical catalog/data equality and capture head baseline",
            f"downgrade exactly one revision to {edge.predecessor}",
            "prove revision and explicit head-object/surviving-data/role invariants",
            f"upgrade exactly one revision to {edge.head}",
            "prove normalized catalog/data/roles equal the baseline",
            "run no-external API read, transactional write rollback, and worker startup smoke",
            "prove smoke did not change the final fingerprint or current tc",
            "seal a new mode-0600 regular single-link evidence file without secrets/raw rows/URLs",
        ],
        "doesNotMutate": [
            "current tc database",
            "pre-existing Docker resources",
            "prior Phase F evidence",
            "staging, production, Cloudflare, Slack, providers, or deployments",
        ],
        "networkPolicy": "LOOPBACK_ONLY_NO_EXTERNAL_CALLS",
        "releaseGates": "UNCHANGED_NOT_RUN",
    }


def _secret_safe_payload(payload: Mapping[str, object], forbidden: Sequence[str]) -> None:
    encoded = _canonical_bytes(payload).decode()
    if any(value and value in encoded for value in forbidden):
        raise SafetyError("secret value reached wave-9 evidence")
    lowered = encoded.casefold()
    if "postgresql://" in lowered or "postgresql+psycopg://" in lowered:
        raise SafetyError("database URL reached wave-9 evidence")
    for key in (
        "password",
        "secret",
        "token",
        "authorization",
        "rawrow",
        "slacktext",
        "permalink",
        "attachmentcontent",
    ):
        if f'"{key}"' in lowered:
            raise SafetyError("forbidden sensitive field reached wave-9 evidence")


def _execute(args: argparse.Namespace) -> dict[str, object]:
    resources, temp_directory, dump_path = _target_from_args(args)
    edge = _resolve_migration_edge()
    output = _safe_evidence_output(str(args.output))
    if output is None:
        raise SafetyError("wave-9 rehearsal evidence output is required")
    plan = _plan(
        resources,
        edge=edge,
        temp_directory=temp_directory,
        dump_path=dump_path,
        output=output,
    )
    if not args.execute:
        return plan
    if args.confirm != plan["confirmationRequired"]:
        raise SafetyError("wave-9 execute confirmation differs from preview")
    if temp_directory.exists() or dump_path.exists():
        raise SafetyError("wave-9 temp or dump path already exists")
    _assert_no_collisions(resources)
    _assert_port_free(resources)

    current, current_client = _current_database()
    image_id = _image_id(SOURCE_IMAGE)
    before_docker = _docker_inventory()
    before_databases = _database_inventory(current)
    prior_evidence = _prior_evidence_inventory(exclude={output})
    started_at = datetime.now(UTC)
    started = time.perf_counter()
    password = secrets.token_urlsafe(32)
    database = _database_target(resources, password=password)
    compose_environment = _compose_environment(
        resources, password=password, image_id=image_id
    )
    temp_directory.mkdir(mode=0o700)
    if stat.S_IMODE(os.lstat(temp_directory).st_mode) != 0o700:
        raise SafetyError("wave-9 temp directory mode is not 0700")
    try:
        snapshot, source_fingerprint, dump = _source_snapshot_and_dump(
            current=current,
            current_client=current_client,
            dump_path=dump_path,
        )
        actual_source_revision = cast(str, snapshot["alembicRevision"])
        if actual_source_revision != edge.head:
            raise SafetyError("current tc is not at the dynamically resolved Alembic head")

        _command(
            _compose_command(
                resources, "up", "--detach", "--wait", "--wait-timeout", "180"
            ),
            environment=compose_environment,
        )
        _wait_healthy(resources.container)
        _assert_owned_resources(resources)
        project_inventory = _assert_exact_project_inventory(resources)
        _set_database_comment(database, resources.database_comment)
        empty_preflight = _attest_target(
            database, resources, require_empty=True
        )
        _restore_dump(resources=resources, dump_path=dump_path)
        restored_preflight = _attest_target(
            database,
            resources,
            require_empty=False,
            expected_revision=edge.head,
        )
        restored_fingerprint = _capture_fingerprint(database)
        restore_attestation = _logical_restore_attestation(
            current=current,
            restored=database,
            current_fingerprint=source_fingerprint,
            restored_fingerprint=restored_fingerprint,
        )
        safety_boundary = _control_plane_safety(database)
        baseline = _capture_fingerprint(database)
        normalized_baseline = _capture_normalized_data(database)
        surviving_baseline = _capture_normalized_data(
            database, exclude=DOWNGRADE_DATA_EXCLUSIONS
        )
        roles_before = cast(dict[str, object], baseline["roleCatalog"])

        _alembic(database, "downgrade", edge.predecessor)
        downgrade = _downgrade_invariants(
            database,
            edge=edge,
            surviving_data_before=surviving_baseline,
            roles_before=roles_before,
        )
        _alembic(database, "upgrade", edge.head)
        final_preflight = _attest_target(
            database,
            resources,
            require_empty=False,
            expected_revision=edge.head,
        )
        final = _capture_fingerprint(database)
        normalized_final = _capture_normalized_data(database)
        raw_differences = _require_final_equality(
            baseline=baseline,
            final=final,
            normalized_baseline=normalized_baseline,
            normalized_final=normalized_final,
        )

        before_smoke = _capture_fingerprint(database)
        before_smoke_normalized = _capture_normalized_data(database)
        smoke = _smoke(database)
        after_smoke = _capture_fingerprint(database)
        after_smoke_normalized = _capture_normalized_data(database)
        if before_smoke != after_smoke or before_smoke_normalized != after_smoke_normalized:
            raise VerificationError("final-head smoke mutated the disposable database")
        if final != after_smoke:
            raise VerificationError("post-smoke final fingerprint differs from roll-forward")

        after_tc = _capture_fingerprint(current)
        after_databases = _database_inventory(current)
        _require_preservation(
            expected_source_digest=_digest_record(
                source_fingerprint, version="tc.postgresql-fingerprint.v1"
            ),
            actual_source_fingerprint=after_tc,
            expected_databases=before_databases,
            actual_databases=after_databases,
        )
        if not _verify_prior_evidence(prior_evidence):
            raise SafetyError("prior Phase F evidence verification failed")
        payload = _seal(
            {
                "schemaVersion": SCHEMA,
                "task": TASK,
                "mode": "LOCAL_DISPOSABLE_PASS",
                "subGate": "LOCAL_DISPOSABLE",
                "releaseGate": "NOT_RUN",
                "target": resources.public_dict(),
                "migration": edge.public_dict(),
                "confirmationRequired": plan["confirmationRequired"],
                "evidenceFile": str(output.relative_to(ROOT)),
                "tempDirectory": str(temp_directory),
                "dumpFile": str(dump_path),
                "source": {
                    "database": "tc",
                    "environment": "local",
                    "alembicRevision": actual_source_revision,
                    "access": "EXPORTED_REPEATABLE_READ_READ_ONLY_SNAPSHOT_AND_PG_DUMP",
                    "snapshot": snapshot,
                    "fingerprintDigest": _digest_record(
                        source_fingerprint, version="tc.postgresql-fingerprint.v1"
                    ),
                    "beforeAfterFingerprintEqual": True,
                    "rawRowsOmitted": True,
                },
                "dump": dump,
                "preflight": {
                    "emptyTarget": empty_preflight,
                    "restoredHead": restored_preflight,
                    "restoreAttestation": restore_attestation,
                    "controlPlane": safety_boundary,
                    "projectInventory": project_inventory,
                },
                "roundTrip": {
                    "baseline": {
                        "catalogFingerprintVersion": baseline["catalogFingerprintVersion"],
                        "schemaSha256": baseline["schemaSha256"],
                        "schemaObjectCounts": baseline["schemaObjectCounts"],
                        "schemaCategorySha256": baseline["schemaCategorySha256"],
                        "normalizedData": normalized_baseline,
                        "roleCatalog": roles_before,
                    },
                    "downgrade": downgrade,
                    "finalPreflight": final_preflight,
                    "final": {
                        "catalogFingerprintVersion": final["catalogFingerprintVersion"],
                        "schemaSha256": final["schemaSha256"],
                        "schemaObjectCounts": final["schemaObjectCounts"],
                        "schemaCategorySha256": final["schemaCategorySha256"],
                        "normalizedData": normalized_final,
                        "roleCatalog": final["roleCatalog"],
                    },
                    "equality": {
                        "schemaExact": True,
                        "schemaCategoriesExact": True,
                        "schemaObjectCountsExact": True,
                        "normalizedDataExact": True,
                        "rolesExact": True,
                        "rawDataDifferenceTables": raw_differences,
                        "rawDataDifference": (
                            "NONE"
                            if not raw_differences
                            else "PRISTINE_POINTER_GENERATED_UPDATED_AT_ONLY"
                        ),
                        "physicalDriftExcluded": [
                            "volatile object identifiers",
                            "dropped-column physical attribute slots during source dump/restore",
                        ],
                    },
                    "steps": {
                        "downgrade": 1,
                        "upgrade": 1,
                        "broadDowngrade": False,
                    },
                },
                "smoke": smoke,
                "preservation": {
                    "beforeDockerInventory": before_docker,
                    "beforeDatabaseInventory": before_databases,
                    "afterDatabaseInventory": after_databases,
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
                    "staging migration rehearsal",
                    "live or production migration rehearsal",
                    "operator handoff",
                    "production RTO/RPO",
                    "PF-SF1-T10 task gate",
                    "Phase F release gate",
                ],
                "rawRowsOmitted": True,
                "credentialsOmitted": True,
                "databaseUrlsOmitted": True,
            }
        )
        _secret_safe_payload(payload, (password, current.url, database.url))
        _write_json_evidence(output, payload)
        with PinnedReadFile(
            output,
            label="wave-9 rehearsal evidence",
            maximum_size=8 * 1024 * 1024,
            require_nonempty=True,
        ) as evidence:
            evidence_sha = evidence.run_stage(
                "hash", lambda: _pinned_file_sha256(evidence)
            )
            safety = evidence.identity.evidence()
        return {
            **payload,
            "durableEvidenceVerification": {
                "fileSha256": evidence_sha,
                "fileSafety": safety,
                "verified": True,
            },
        }
    finally:
        password = ""


def _load_evidence(
    path_value: str,
) -> tuple[Path, dict[str, Any], str, dict[str, object]]:
    path = _resolve_non_symlink_path(path_value)
    try:
        path.relative_to(EVIDENCE_ROOT)
    except ValueError as error:
        raise SafetyError("wave-9 evidence path escaped the evidence root") from error
    with PinnedReadFile(
        path,
        label="wave-9 rehearsal evidence",
        maximum_size=8 * 1024 * 1024,
        require_nonempty=True,
    ) as source:
        raw = source.run_stage("read", source.read_bytes)
        checksum = source.run_stage("hash", lambda: _pinned_file_sha256(source))
        safety = source.identity.evidence()
    try:
        payload = json.loads(raw, object_pairs_hook=_reject_duplicate_json_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SafetyError("wave-9 evidence JSON is malformed") from error
    return path, _verify_seal(payload, schema=SCHEMA), checksum, safety


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SafetyError("wave-9 evidence contains a duplicate JSON key")
        result[key] = value
    return result


def _container_database_comment(resources: Resources) -> None:
    result = _command(
        [
            "docker",
            "exec",
            resources.container,
            "psql",
            "-U",
            resources.database_user,
            "-d",
            resources.database,
            "-Atqc",
            "SELECT COALESCE(shobj_description(oid, 'pg_database'), '') "
            "FROM pg_database WHERE datname=current_database()",
        ],
        capture=True,
    )
    if result.stdout.decode().strip() != resources.database_comment:
        raise SafetyError("cleanup database comment attestation mismatch")


def _cleanup(args: argparse.Namespace) -> dict[str, object]:
    evidence_path, evidence, evidence_sha, evidence_safety = _load_evidence(
        str(args.rehearsal_evidence)
    )
    target = evidence.get("target")
    if not isinstance(target, dict) or not isinstance(target.get("runId"), str):
        raise SafetyError("wave-9 evidence target is malformed")
    resources = Resources(str(target["runId"]), int(args.port))
    if target != resources.public_dict():
        raise SafetyError("cleanup target/resources/comment/port differ from evidence")
    output = _safe_evidence_output(str(args.output))
    if output is None:
        raise SafetyError("wave-9 cleanup evidence output is required")
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
            resources.container,
            resources.volume,
            resources.network,
            str(evidence.get("dumpFile")),
            str(evidence.get("tempDirectory")),
            str(output),
        ],
        "releaseGates": "UNCHANGED_NOT_RUN",
    }
    if not args.execute:
        return preview
    if args.confirm != confirmation:
        raise SafetyError("cleanup confirmation differs from preview")
    if evidence.get("mode") != "LOCAL_DISPOSABLE_PASS":
        raise SafetyError("cleanup requires successful wave-9 rehearsal evidence")
    _assert_owned_resources(resources)
    _assert_exact_project_inventory(resources)
    _container_database_comment(resources)
    preservation = evidence.get("preservation")
    dump_contract = evidence.get("dump")
    if not isinstance(preservation, dict) or not isinstance(dump_contract, dict):
        raise SafetyError("cleanup evidence preservation/dump binding is malformed")
    dump_path = _resolve_non_symlink_path(str(evidence.get("dumpFile", "")))
    temp_directory = _resolve_non_symlink_path(
        str(evidence.get("tempDirectory", ""))
    )
    if (
        dump_path.parent != temp_directory
        or TOKEN not in temp_directory.name
        or resources.run_id not in temp_directory.name
    ):
        raise SafetyError("cleanup file paths differ from the wave-9 namespace")
    with PinnedReadFile(
        dump_path,
        label="wave-9 source dump",
        maximum_size=512 * 1024 * 1024,
        require_nonempty=True,
    ) as source:
        actual_sha = source.run_stage("hash", lambda: _pinned_file_sha256(source))
        if (
            actual_sha != dump_contract.get("sha256")
            or source.identity.evidence() != dump_contract.get("fileIdentity")
        ):
            raise SafetyError("cleanup dump identity or digest differs from evidence")
        dump_identity = source.identity
    image_id = _image_id(SOURCE_IMAGE)
    cleanup_environment = _compose_environment(
        resources,
        password="cleanup-placeholder-not-retained",
        image_id=image_id,
    )
    _command(
        _compose_command(resources, "down", "--volumes", "--timeout", "5"),
        environment=cleanup_environment,
    )
    if any(
        _resource_labels(kind, name) is not None
        for kind, name in _resource_names(resources)
    ) or any(_labeled_resource_inventory(resources).values()):
        raise SafetyError("wave-9 Docker resource remained after cleanup")
    if FileIdentity.from_stat(os.lstat(dump_path)) != dump_identity:
        raise SafetyError("cleanup dump pathname changed after pinned verification")
    dump_path.unlink()
    if any(temp_directory.iterdir()):
        raise SafetyError("cleanup temp directory contains an unexpected artifact")
    temp_directory.rmdir()

    current, _current_client = _current_database()
    after_tc = _capture_fingerprint(current)
    after_databases = _database_inventory(current)
    after_docker = _docker_inventory()
    source_binding = evidence.get("source")
    if not isinstance(source_binding, dict):
        raise SafetyError("cleanup source preservation binding is malformed")
    expected_source_digest = source_binding.get("fingerprintDigest")
    expected_databases = preservation.get("beforeDatabaseInventory")
    expected_docker = preservation.get("beforeDockerInventory")
    if not all(
        isinstance(value, dict)
        for value in (expected_source_digest, expected_databases, expected_docker)
    ):
        raise SafetyError("cleanup preservation inventory is malformed")
    _require_preservation(
        expected_source_digest=cast(dict[str, object], expected_source_digest),
        actual_source_fingerprint=after_tc,
        expected_databases=cast(dict[str, object], expected_databases),
        actual_databases=after_databases,
        expected_docker=cast(dict[str, object], expected_docker),
        actual_docker=after_docker,
    )
    prior = preservation.get("priorEvidence")
    if not isinstance(prior, dict) or not _verify_prior_evidence(prior):
        raise SafetyError("prior Phase F evidence was not preserved")
    _, reread, reread_sha, _ = _load_evidence(str(evidence_path))
    if reread != evidence or reread_sha != evidence_sha:
        raise SafetyError("rehearsal evidence changed during cleanup")
    payload = _seal(
        {
            "schemaVersion": CLEANUP_SCHEMA,
            "task": TASK,
            "mode": "LOCAL_DISPOSABLE_CLEANUP_PASS",
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
                "containers": 1,
                "volumes": 1,
                "networks": 1,
                "containerOwnedDatabases": 1,
                "dump": True,
                "tempDirectory": True,
            },
            "absence": {
                "dockerResources": True,
                "disposableDatabase": True,
                "dump": True,
                "tempDirectory": True,
                "wave9ChildProcesses": True,
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
            "rawRowsOmitted": True,
            "credentialsOmitted": True,
            "databaseUrlsOmitted": True,
        }
    )
    _secret_safe_payload(payload, ())
    _write_json_evidence(output, payload)
    with PinnedReadFile(
        output,
        label="wave-9 cleanup evidence",
        maximum_size=8 * 1024 * 1024,
        require_nonempty=True,
    ) as cleanup:
        checksum = cleanup.run_stage("hash", lambda: _pinned_file_sha256(cleanup))
        safety = cleanup.identity.evidence()
    return {
        **payload,
        "durableEvidenceVerification": {
            "fileSha256": checksum,
            "fileSafety": safety,
            "verified": True,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preview/execute PF-SF1-T10 Wave 9 local disposable rehearsal."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    rehearse = commands.add_parser("rehearse")
    cleanup = commands.add_parser("cleanup")
    for command in (rehearse, cleanup):
        command.add_argument("--run-id", required=True)
        command.add_argument("--port", required=True, type=int)
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
        result = _execute(args) if args.command == "rehearse" else _cleanup(args)
    except (SafetyError, VerificationError) as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(error),
                    "rawRowsOmitted": True,
                    "credentialsOmitted": True,
                    "databaseUrlsOmitted": True,
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
                    "rawRowsOmitted": True,
                    "credentialsOmitted": True,
                    "databaseUrlsOmitted": True,
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
