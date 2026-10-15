"""Fail-closed Phase F recovery and performance rehearsal entrypoint.

Every command is a preview unless ``--execute`` is present. Database actions
also require an exact database name, a matching database comment attestation,
and an exact per-task confirmation. Credentials are accepted only through
named environment variables and are never included in reports or argv.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypeVar
from urllib.parse import parse_qs, unquote, urlsplit

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_ROOT = ROOT / "docs" / "phase-f" / "evidence"
ALEMBIC_CONFIG = ROOT / "backend" / "alembic.ini"
CASE_BENCHMARK = ROOT / "scripts" / "benchmark_cases.py"
KNOWLEDGE_REBUILD = ROOT / "scripts" / "rebuild_knowledge_projection.py"
LIGHTRAG_FAILOVER = ROOT / "scripts" / "rehearse_lightrag_failover.py"
PHASE_F_IMPORT_ROOT = ROOT / "scripts" / "phase-f"
COMPOSE_FILE = ROOT / "deploy" / "compose.dev.yml"
COMPOSE_POSTGRES_SERVICE = "postgres"

HEAD_REVISION = "20260902_0030"
ROLLBACK_REVISION = "20260901_0029"
KNOWLEDGE_FACTORY = "phase_f_knowledge_factory:build_consumer"
KNOWLEDGE_CONFIRMATION = "REBUILD-KNOWLEDGE-PROJECTION"
LIGHTRAG_CONFIRMATION = "RUN TC LIGHTRAG FAILOVER REHEARSAL"
TARGET_CLASSES = ("disposable", "staging-copy")
PRODUCTION_TOKENS = frozenset({"prod", "production", "live", "primary", "main"})
SAFE_ENV_PATTERN = re.compile(r"^TC_PHASE_F_[A-Z0-9_]+$")
HEX_DIGEST_PATTERN = re.compile(r"^[a-f0-9]{64}$")
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
CATALOG_FINGERPRINT_VERSION = "postgresql-public-catalog-v3"
CATALOG_CATEGORIES = frozenset(
    {
        "collations",
        "columns",
        "constraints",
        "extensions",
        "indexes",
        "inheritance",
        "partitions",
        "policies",
        "relations",
        "routines",
        "rules",
        "sequences",
        "statistics",
        "triggers",
        "types",
        "views",
    }
)
SQL_STRING_CAST_PATTERN = re.compile(
    r"\('((?:''|[^'])*)'::character varying\)::text|"
    r"'((?:''|[^'])*)'::(?:character varying|text)"
)
TEXT_ARRAY_CAST_PATTERN = re.compile(
    r"\(\((ARRAY\[[^\[\]]*\])\)::text\[\]\)"
)
T = TypeVar("T")


class SafetyError(RuntimeError):
    """The requested action is ambiguous or outside the rehearsal boundary."""


def _canonical_catalog_scalar(value: object) -> object:
    if not isinstance(value, str):
        return value

    def uncast_literal(match: re.Match[str]) -> str:
        literal = match.group(1) if match.group(1) is not None else match.group(2)
        return f"'{literal}'"

    normalized = SQL_STRING_CAST_PATTERN.sub(uncast_literal, value)
    return TEXT_ARRAY_CAST_PATTERN.sub(r"(\1)", normalized)


@dataclass(frozen=True, slots=True)
class Target:
    name: str
    target_class: str

    def __post_init__(self) -> None:
        if self.target_class not in TARGET_CLASSES:
            raise SafetyError("target class must be disposable or staging-copy")
        expected_suffix = self.target_class
        pattern = rf"^tc-phase-f-(?:[a-z0-9]+-)+{re.escape(expected_suffix)}$"
        if not re.fullmatch(pattern, self.name):
            raise SafetyError(
                f"target name must be explicit: tc-phase-f-<purpose>-{expected_suffix}"
            )
        _reject_production_like(self.name, "target name")

    @property
    def database_attestation(self) -> str:
        return f"tc-phase-f-rehearsal:{self.target_class}:{self.name}"


@dataclass(frozen=True, slots=True)
class DatabaseTarget:
    expected_name: str
    url_env: str
    url: str
    libpq_url: str
    connection_environment: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class PostgreSQLClient:
    mode: Literal["host", "compose-exec"]
    executables: tuple[tuple[str, str], ...]

    def executable(self, tool: str) -> str:
        paths = dict(self.executables)
        try:
            return paths[tool]
        except KeyError as error:
            raise SafetyError(f"PostgreSQL client does not provide {tool}") from error


@dataclass(frozen=True, slots=True)
class FileIdentity:
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    mode: int
    link_count: int

    @classmethod
    def from_stat(cls, metadata: os.stat_result) -> FileIdentity:
        return cls(
            device=metadata.st_dev,
            inode=metadata.st_ino,
            size=metadata.st_size,
            mtime_ns=metadata.st_mtime_ns,
            ctime_ns=metadata.st_ctime_ns,
            mode=metadata.st_mode,
            link_count=metadata.st_nlink,
        )

    def evidence(self) -> dict[str, object]:
        return {
            "device": self.device,
            "inode": self.inode,
            "size": self.size,
            "mtimeNs": self.mtime_ns,
            "ctimeNs": self.ctime_ns,
            "modeOctal": f"{stat.S_IMODE(self.mode):04o}",
            "linkCount": self.link_count,
        }


class PinnedReadFile:
    """Consume one private file through one O_NOFOLLOW descriptor."""

    def __init__(
        self,
        path: Path,
        *,
        label: str,
        maximum_size: int | None = None,
        require_nonempty: bool = False,
    ) -> None:
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise SafetyError(f"{label} requires O_NOFOLLOW support")
        self.path = path
        self.label = label
        self._stages: list[str] = []
        try:
            self.descriptor = os.open(path, os.O_RDONLY | nofollow)
        except OSError as error:
            raise SafetyError(
                f"{label} could not be opened as a non-symlink file"
            ) from error
        try:
            self.identity = FileIdentity.from_stat(os.fstat(self.descriptor))
            self._require_safe_identity(self.identity)
            if maximum_size is not None and self.identity.size > maximum_size:
                raise SafetyError(f"{label} exceeds the maximum accepted size")
            if require_nonempty and self.identity.size <= 0:
                raise SafetyError(f"{label} must not be empty")
            self.assert_unchanged("initial open")
        except BaseException:
            os.close(self.descriptor)
            raise

    def _require_safe_identity(self, identity: FileIdentity) -> None:
        if not stat.S_ISREG(identity.mode):
            raise SafetyError(f"{self.label} must be a regular non-symlink file")
        if stat.S_IMODE(identity.mode) & ~0o600:
            raise SafetyError(f"{self.label} permissions must be mode 0600 or stricter")
        if identity.link_count != 1:
            raise SafetyError(f"{self.label} must have exactly one hard link")

    def assert_unchanged(self, stage: str) -> None:
        try:
            descriptor_identity = FileIdentity.from_stat(os.fstat(self.descriptor))
            path_identity = FileIdentity.from_stat(os.lstat(self.path))
        except (FileNotFoundError, OSError) as error:
            raise SafetyError(f"{self.label} changed during {stage}") from error
        self._require_safe_identity(descriptor_identity)
        self._require_safe_identity(path_identity)
        if descriptor_identity != self.identity or path_identity != self.identity:
            raise SafetyError(f"{self.label} changed during {stage}")

    def run_stage(self, stage: str, operation: Callable[[], T]) -> T:
        self.assert_unchanged(f"before {stage}")
        try:
            result = operation()
        except BaseException as error:
            try:
                self.assert_unchanged(stage)
            except SafetyError as mutation:
                raise mutation from error
            raise
        self.assert_unchanged(stage)
        self._stages.append(stage)
        return result

    def read_bytes(self) -> bytes:
        os.lseek(self.descriptor, 0, os.SEEK_SET)
        content = bytearray()
        while chunk := os.read(self.descriptor, 1024 * 1024):
            content.extend(chunk)
        if len(content) != self.identity.size:
            raise SafetyError(f"{self.label} size changed while reading")
        return bytes(content)

    def safety_evidence(self) -> dict[str, object]:
        return {
            "singleOpenNoFollowFileDescriptor": True,
            "pathnameReopened": False,
            "regularFile": True,
            "permissionsAtMost0600": True,
            "singleLink": True,
            "identity": self.identity.evidence(),
            "verifiedBeforeAndAfterStages": list(self._stages),
        }

    def __enter__(self) -> PinnedReadFile:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> Literal[False]:
        del exception_type, traceback
        try:
            if not isinstance(exception, SafetyError):
                self.assert_unchanged("final close")
        finally:
            os.close(self.descriptor)
        return False


def _tokens(value: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", value.casefold()) if token}


def _reject_production_like(value: str, label: str) -> None:
    found = sorted(_tokens(value) & PRODUCTION_TOKENS)
    if found:
        raise SafetyError(
            f"{label} contains production-like token(s): {', '.join(found)}"
        )


def _required_environment(name: str) -> str:
    if not SAFE_ENV_PATTERN.fullmatch(name):
        raise SafetyError("environment variable names must start with TC_PHASE_F_")
    value = os.getenv(name)
    if not value:
        raise SafetyError(f"required environment variable is not set: {name}")
    return value


def _validate_database_name(name: str, target: Target) -> None:
    suffix = target.target_class.replace("-", "_")
    if not re.fullmatch(rf"tc_phase_f_[a-z0-9]+(?:_[a-z0-9]+)*_{suffix}", name):
        raise SafetyError(
            f"database name must be explicit: tc_phase_f_<purpose>_{suffix}"
        )
    if not (_tokens(name) & {"test", "rehearsal", "staging", "stage"}):
        raise SafetyError(
            "database name must include test, rehearsal, staging, or stage"
        )
    _reject_production_like(name, "database name")


def _database_target(args: argparse.Namespace, target: Target) -> DatabaseTarget:
    expected_name = str(args.database_name)
    _validate_database_declaration(args, target)
    url_env = str(args.database_url_env)
    raw_url = _required_environment(url_env)
    parsed = urlsplit(raw_url)
    if parsed.scheme not in {"postgresql", "postgresql+psycopg"}:
        raise SafetyError("database URL must use postgresql or postgresql+psycopg")
    if not parsed.hostname or parsed.fragment:
        raise SafetyError("database URL must name a host")
    database_name = unquote(parsed.path.removeprefix("/"))
    if database_name != expected_name:
        raise SafetyError(
            f"database URL resolves to {database_name!r}, not declared {expected_name!r}"
        )
    _reject_production_like(parsed.hostname, "database host")
    if target.target_class == "disposable" and parsed.hostname not in LOOPBACK_HOSTS:
        raise SafetyError("disposable database targets must use a loopback host")
    if (
        target.target_class == "staging-copy"
        and parsed.hostname
        not in LOOPBACK_HOSTS
        and not (_tokens(parsed.hostname) & {"stage", "staging", "stg"})
    ):
        raise SafetyError(
            "remote staging-copy database hosts must contain stage/staging/stg"
        )
    libpq_url = raw_url.replace("postgresql+psycopg://", "postgresql://", 1)
    query = parse_qs(parsed.query, keep_blank_values=True)
    unsupported = sorted(set(query) - {"sslmode"})
    if unsupported:
        raise SafetyError(
            "database URL query may contain only sslmode; unsupported: "
            + ", ".join(unsupported)
        )
    connection_environment = {
        "PGDATABASE": expected_name,
        "PGHOST": parsed.hostname,
        "PGPORT": str(parsed.port or 5432),
        "PGCONNECT_TIMEOUT": "10",
    }
    if parsed.username:
        connection_environment["PGUSER"] = unquote(parsed.username)
    if parsed.password:
        connection_environment["PGPASSWORD"] = unquote(parsed.password)
    if query.get("sslmode"):
        connection_environment["PGSSLMODE"] = query["sslmode"][0]
    if (
        target.target_class == "staging-copy"
        and parsed.hostname
        not in LOOPBACK_HOSTS
        and query.get("sslmode") != ["verify-full"]
    ):
        raise SafetyError(
            "remote staging-copy database URLs require sslmode=verify-full"
        )
    return DatabaseTarget(
        expected_name,
        url_env,
        raw_url,
        libpq_url,
        tuple(sorted(connection_environment.items())),
    )


def _validate_database_declaration(args: argparse.Namespace, target: Target) -> None:
    _validate_database_name(str(args.database_name), target)
    if not SAFE_ENV_PATTERN.fullmatch(str(args.database_url_env)):
        raise SafetyError("environment variable names must start with TC_PHASE_F_")


def _require_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise SafetyError(f"required executable is not installed: {name}")
    return path


def _resolve_postgresql_client(
    database: DatabaseTarget | None = None,
) -> PostgreSQLClient:
    tools = ("psql", "pg_dump", "pg_restore")
    host_paths = tuple((tool, shutil.which(tool) or "") for tool in tools)
    if all(path for _, path in host_paths):
        return PostgreSQLClient("host", host_paths)
    if database is None or urlsplit(database.url).hostname not in LOOPBACK_HOSTS:
        missing = ", ".join(tool for tool, path in host_paths if not path)
        raise SafetyError(
            "PostgreSQL host clients are required for remote database targets; "
            f"Compose fallback is loopback-only; missing: {missing}"
        )
    docker = shutil.which("docker")
    if docker is None or not COMPOSE_FILE.is_file():
        missing = ", ".join(tool for tool, path in host_paths if not path)
        raise SafetyError(
            "PostgreSQL host clients are unavailable and the local Compose fallback "
            f"cannot be used; missing: {missing}"
        )
    status = _command(
        [
            docker,
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "ps",
            "--status",
            "running",
            "--services",
            COMPOSE_POSTGRES_SERVICE,
        ],
        env=_clean_process_environment(),
        capture=True,
    )
    if status.stdout.split() != [COMPOSE_POSTGRES_SERVICE]:
        raise SafetyError(
            "local Compose PostgreSQL service must already be running for the "
            "client fallback"
        )
    return PostgreSQLClient(
        "compose-exec",
        (("docker", docker),),
    )


def _postgresql_client_evidence(client: PostgreSQLClient) -> dict[str, object]:
    return {
        "mode": client.mode,
        "composeService": (
            COMPOSE_POSTGRES_SERVICE if client.mode == "compose-exec" else None
        ),
        "credentialsInArgv": False,
    }


def _postgresql_command(
    client: PostgreSQLClient,
    tool: str,
    database: DatabaseTarget | None,
    *arguments: str,
) -> tuple[list[str], dict[str, str]]:
    environment = (
        _database_environment(database)
        if database is not None
        else _clean_process_environment()
    )
    if client.mode == "host":
        return [client.executable(tool), *arguments], environment
    if database is not None:
        # The Compose client runs inside the already-running PostgreSQL service.
        # Override only routing metadata; values, including PGPASSWORD, are
        # inherited by name and never rendered in argv.
        environment["PGHOST"] = "127.0.0.1"
        environment["PGPORT"] = "5432"
    exported = [
        name
        for name in (
            "PGDATABASE",
            "PGHOST",
            "PGPORT",
            "PGCONNECT_TIMEOUT",
            "PGUSER",
            "PGPASSWORD",
            "PGSSLMODE",
        )
        if name in environment
    ]
    command = [
        client.executable("docker"),
        "compose",
        "-f",
        str(COMPOSE_FILE),
        "exec",
        "-T",
    ]
    for name in exported:
        command.extend(["--env", name])
    command.extend([COMPOSE_POSTGRES_SERVICE, tool, *arguments])
    return command, environment


def _command(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=ROOT,
        env=dict(env) if env is not None else None,
        check=True,
        capture_output=capture,
        text=True,
    )


def _command_to_new_file(
    argv: Sequence[str],
    path: Path,
    *,
    env: Mapping[str, str],
) -> None:
    descriptor: int | None = None
    identity: tuple[int, int, int] | None = None
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        metadata = os.fstat(descriptor)
        identity = (metadata.st_dev, metadata.st_ino, metadata.st_size)
        with os.fdopen(descriptor, "wb") as output:
            descriptor = None
            subprocess.run(
                list(argv),
                cwd=ROOT,
                env=dict(env),
                check=True,
                stdout=output,
                stderr=subprocess.PIPE,
            )
        if not _same_file_identity(path, identity):
            raise SafetyError("exclusive dump path changed inode while being written")
        if stat.S_IMODE(os.lstat(path).st_mode) != 0o600:
            raise SafetyError("exclusive dump was not created with mode 0600")
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        if identity is not None and _same_file_identity(path, identity):
            path.unlink()
        raise


def _command_from_pinned_file(
    argv: Sequence[str],
    source: PinnedReadFile,
    *,
    env: Mapping[str, str],
    capture: bool,
) -> subprocess.CompletedProcess[bytes]:
    os.lseek(source.descriptor, 0, os.SEEK_SET)
    return subprocess.run(
        list(argv),
        cwd=ROOT,
        env=dict(env),
        check=True,
        stdin=source.descriptor,
        capture_output=capture,
    )


def _database_environment(database: DatabaseTarget) -> dict[str, str]:
    environment = _clean_process_environment()
    environment.update(database.connection_environment)
    return environment


def _clean_process_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PG") and not key.startswith("TC_PHASE_F_")
    }


def _psql(
    database: DatabaseTarget,
    sql_text: str,
    *,
    client: PostgreSQLClient | None = None,
) -> list[str]:
    active_client = client or _resolve_postgresql_client(database)
    command, environment = _postgresql_command(
        active_client,
        "psql",
        database,
        "-X",
        "--no-psqlrc",
        "--tuples-only",
        "--no-align",
        "--field-separator",
        "\t",
        "--set",
        "ON_ERROR_STOP=1",
        "--command",
        sql_text,
    )
    result = _command(
        command,
        env=environment,
        capture=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def _database_preflight(
    database: DatabaseTarget,
    target: Target,
    *,
    require_empty: bool = False,
    expected_revision: str | None = None,
    client: PostgreSQLClient | None = None,
) -> dict[str, object]:
    facts = _psql(
        database,
        "SELECT current_database(), current_setting('server_version_num'), "
        "COALESCE(shobj_description(oid, 'pg_database'), '') "
        "FROM pg_database WHERE datname = current_database();",
        client=client,
    )
    if len(facts) != 1:
        raise SafetyError(
            "database identity preflight returned an unexpected row count"
        )
    fields = facts[0].split("\t")
    if len(fields) != 3:
        raise SafetyError("database identity preflight returned an unexpected shape")
    actual_name, version_text, attestation = fields
    if actual_name != database.expected_name:
        raise SafetyError("connected database does not match the declared database")
    if not version_text.isdigit() or int(version_text) < 160_000:
        raise SafetyError("Phase F rehearsal requires PostgreSQL 16 or newer")
    if attestation != target.database_attestation:
        raise SafetyError(
            "database comment attestation mismatch; expected exactly "
            f"{target.database_attestation!r}"
        )
    table_rows = _psql(
        database,
        "SELECT count(*) FROM pg_tables WHERE schemaname = 'public';",
        client=client,
    )
    if len(table_rows) != 1 or not table_rows[0].isdigit():
        raise SafetyError("could not determine public table count")
    table_count = int(table_rows[0])
    if require_empty and table_count != 0:
        raise SafetyError("destination must be a freshly created empty database")
    revision = _current_revision(database, client=client)
    if expected_revision is not None and revision != expected_revision:
        raise SafetyError(
            f"database revision is {revision or 'absent'}, expected {expected_revision}"
        )
    return {
        "databaseName": actual_name,
        "serverVersionNum": int(version_text),
        "attested": True,
        "databaseAttestation": attestation,
        "publicTableCount": table_count,
        "alembicRevision": revision,
    }


def _current_revision(
    database: DatabaseTarget, *, client: PostgreSQLClient | None = None
) -> str | None:
    relation_rows = _psql(
        database,
        "SELECT to_regclass('public.alembic_version') IS NOT NULL;",
        client=client,
    )
    if relation_rows == ["f"]:
        return None
    if relation_rows != ["t"]:
        raise SafetyError("could not determine whether Alembic revision exists")
    rows = _psql(
        database,
        "SELECT COALESCE((SELECT version_num FROM alembic_version LIMIT 1), '<absent>');",
        client=client,
    )
    if len(rows) != 1:
        raise SafetyError("could not determine Alembic revision")
    return None if rows[0] == "<absent>" else rows[0]


def _target_from_args(args: argparse.Namespace) -> Target:
    return Target(name=str(args.target_name), target_class=str(args.target_class))


def _confirmation(task: str, action: str, target: Target) -> str:
    return f"RUN {task} {action} ON {target.name}"


def _require_confirmation(args: argparse.Namespace, required: str) -> None:
    if str(args.confirm or "") != required:
        raise SafetyError(f"--execute requires --confirm {required!r}")


def _base_plan(
    *,
    task: str,
    action: str,
    target: Target,
    mutates: Sequence[str],
    reads: Sequence[str],
    steps: Sequence[str],
) -> dict[str, object]:
    return {
        "ok": True,
        "mode": "preview",
        "executes": False,
        "task": task,
        "action": action,
        "target": {"name": target.name, "class": target.target_class},
        "confirmationRequired": _confirmation(task, action, target),
        "mutates": list(mutates),
        "reads": list(reads),
        "steps": list(steps),
        "safety": {
            "rejectsProductionLikeNames": True,
            "databaseCommentAttestationRequired": any(
                "PostgreSQL" in item for item in (*reads, *mutates)
            ),
            "credentialsPrinted": False,
        },
    }


def _safe_evidence_output(path_value: str | None) -> Path | None:
    if not path_value:
        return None
    path = _resolve_non_symlink_path(path_value)
    try:
        path.relative_to(EVIDENCE_ROOT)
    except ValueError as error:
        raise SafetyError(f"evidence output must be below {EVIDENCE_ROOT}") from error
    if path.suffix != ".json":
        raise SafetyError("evidence output must use a .json suffix")
    if path.exists():
        raise SafetyError("refusing to overwrite an existing evidence report")
    if not path.parent.is_dir():
        raise SafetyError("evidence output parent directory must already exist")
    return path


def _safe_synthetic_output(path_value: str | None) -> Path | None:
    return _safe_evidence_output(path_value)


def _write_json_evidence(path: Path | None, payload: Mapping[str, object]) -> None:
    if path is None:
        return
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _write_bytes_to_new_file(path, encoded)


def _pinned_file_sha256(source: PinnedReadFile) -> str:
    digest = hashlib.sha256()
    os.lseek(source.descriptor, 0, os.SEEK_SET)
    bytes_read = 0
    while chunk := os.read(source.descriptor, 1024 * 1024):
        bytes_read += len(chunk)
        digest.update(chunk)
    if bytes_read != source.identity.size:
        raise SafetyError(f"{source.label} size changed while hashing")
    return digest.hexdigest()


def _safe_dump_path(path_value: str, *, must_exist: bool) -> Path:
    unexpanded = Path(path_value).expanduser()
    if not unexpanded.is_absolute():
        raise SafetyError("dump file path must be absolute")
    path = _resolve_non_symlink_path(path_value)
    if path == ROOT or ROOT in path.parents:
        raise SafetyError("database dumps must stay outside the repository")
    if path.suffix not in {".dump", ".backup"}:
        raise SafetyError("custom PostgreSQL dump must use .dump or .backup")
    if must_exist:
        if not path.is_file():
            raise SafetyError(
                "restore dump file does not exist or is not a regular file"
            )
    else:
        if path.exists():
            raise SafetyError("refusing to overwrite an existing database dump")
        if not path.parent.is_dir():
            raise SafetyError("dump parent directory must already exist")
    return path


def _resolve_non_symlink_path(path_value: str | Path) -> Path:
    """Reject symlinks before canonicalization so ``resolve`` cannot hide them."""

    candidate = Path(path_value).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    absolute = Path(os.path.abspath(candidate))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise SafetyError("path and every existing parent must not be a symlink")
    return absolute.resolve(strict=False)


def _regular_file_identity(path: Path) -> tuple[int, int, int]:
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode):
        raise SafetyError("path must identify a regular non-symlink file")
    return metadata.st_dev, metadata.st_ino, metadata.st_size


def _same_file_identity(path: Path, identity: tuple[int, int, int]) -> bool:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False
    return (
        stat.S_ISREG(metadata.st_mode)
        and (metadata.st_dev, metadata.st_ino) == identity[:2]
    )


def _write_bytes_to_new_file(path: Path, content: bytes) -> None:
    descriptor: int | None = None
    identity: tuple[int, int, int] | None = None
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        metadata = os.fstat(descriptor)
        identity = (metadata.st_dev, metadata.st_ino, metadata.st_size)
        with os.fdopen(descriptor, "wb") as output:
            descriptor = None
            output.write(content)
        if not _same_file_identity(path, identity):
            raise SafetyError("exclusive output path changed inode while being written")
        if stat.S_IMODE(os.lstat(path).st_mode) != 0o600:
            raise SafetyError("exclusive output was not created with mode 0600")
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        if identity is not None and _same_file_identity(path, identity):
            path.unlink()
        raise


def _run_benchmark(args: argparse.Namespace, target: Target) -> dict[str, object]:
    if target.target_class != "disposable":
        raise SafetyError(
            "the destructive 100k fixture benchmark requires a disposable target"
        )
    _validate_database_declaration(args, target)
    if args.warmups < 0 or args.runs < 1 or args.target_ms <= 0:
        raise SafetyError(
            "benchmark warmups/runs/target must be non-negative and positive"
        )
    output = _safe_synthetic_output(args.output)
    plan = _base_plan(
        task="PF-SF1-T06",
        action="BENCHMARK-CASES",
        target=target,
        mutates=["declared empty disposable PostgreSQL database"],
        reads=[],
        steps=[
            "verify exact URL/database identity and database comment attestation",
            "refuse any non-empty destination",
            "migrate and load exactly 100000 synthetic cases",
            "run warm-cache single-process API/query scenarios",
        ],
    )
    plan["limitations"] = [
        "This is not Slack or generation concurrency evidence.",
        "This is not cold-cache, network, p99, or production capacity evidence.",
    ]
    if not args.execute:
        return plan
    _require_confirmation(args, str(plan["confirmationRequired"]))
    database = _database_target(args, target)
    preflight = _database_preflight(database, target, require_empty=True)
    command = [
        sys.executable,
        str(CASE_BENCHMARK),
        "--test-cases",
        "100000",
        "--warmups",
        str(args.warmups),
        "--runs",
        str(args.runs),
        "--target-ms",
        str(args.target_ms),
    ]
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        command.extend(["--output", str(output)])
    result = _command(
        command,
        env={**_clean_process_environment(), "TC_TEST_DATABASE_URL": database.url},
        capture=True,
    )
    benchmark = json.loads(result.stdout)
    return {
        **plan,
        "mode": "executed",
        "executes": True,
        "preflight": preflight,
        "benchmark": benchmark,
        "output": str(output.relative_to(ROOT)) if output else None,
    }


RECOVERY_SEED_SQL = """
SET CONSTRAINTS ALL DEFERRED;
UPDATE workspace_settings SET
    team_name = 'Phase F Recovery Fixture', case_prefix = 'TC', issue_prefix = 'ISSUE',
    default_environment = 'staging', generation_quiet_minutes = 10, version = 1,
    created_at = '2026-09-04T00:00:00Z', updated_at = '2026-09-04T00:00:00Z'
WHERE id = '00000000-0000-7000-8000-000000000100';
UPDATE workspace_key_counters SET
    next_value = CASE entity_type WHEN 'TEST_CASE' THEN 4 ELSE 2 END,
    updated_at = '2026-09-04T00:00:00Z'
WHERE workspace_id = '00000000-0000-7000-8000-000000000100';
UPDATE folders SET
    created_at = '2026-09-04T00:00:00Z', updated_at = '2026-09-04T00:00:00Z'
WHERE id = '00000000-0000-7000-8000-000000000101';
INSERT INTO folders
    (id, workspace_id, parent_id, name, normalized_name, sort_order, version,
     system_kind, created_at, updated_at)
VALUES
    ('00000000-0000-7000-8000-000000000110',
     '00000000-0000-7000-8000-000000000100', NULL, 'Recovery checkout',
     'recovery checkout', 0, 1, NULL,
     '2026-09-04T00:00:01Z', '2026-09-04T00:00:01Z');
INSERT INTO work_items
    (id, workspace_id, category_id, work_item_key, summary, description,
     owner_label, labels, version, created_at, updated_at)
VALUES
    ('00000000-0000-7000-8000-000000000201',
     '00000000-0000-7000-8000-000000000100',
     '00000000-0000-7000-8000-000000000110', 'RECOVERY-001',
     'Checkout recovery ledger', 'Deterministic non-sensitive recovery fixture',
     'fixture-owner-a', '["checkout", "recovery"]', 1,
     '2026-09-04T00:00:02Z', '2026-09-04T00:00:02Z'),
    ('00000000-0000-7000-8000-000000000202',
     '00000000-0000-7000-8000-000000000100',
     '00000000-0000-7000-8000-000000000110', 'RECOVERY-002',
     'Account recovery ledger', 'Second deterministic work item',
     'fixture-owner-b', '["account", "recovery"]', 1,
     '2026-09-04T00:00:03Z', '2026-09-04T00:00:03Z');
INSERT INTO tags (id, workspace_id, name, normalized_name, created_at)
VALUES
    ('00000000-0000-7000-8000-000000000301',
     '00000000-0000-7000-8000-000000000100', 'recovery', 'recovery',
     '2026-09-04T00:00:04Z'),
    ('00000000-0000-7000-8000-000000000302',
     '00000000-0000-7000-8000-000000000100', 'critical-path', 'critical-path',
     '2026-09-04T00:00:04Z');
INSERT INTO test_cases
    (id, workspace_id, work_item_id, category_id, case_key, current_version_id,
     status, sort_order, version, created_at, updated_at)
VALUES
    ('00000000-0000-7000-8000-000000000401',
     '00000000-0000-7000-8000-000000000100',
     '00000000-0000-7000-8000-000000000201',
     '00000000-0000-7000-8000-000000000110', 'TC-000001',
     '00000000-0000-7000-8000-000000000502', 'ACTIVE', 0, 2,
     '2026-09-04T00:00:05Z', '2026-09-04T00:00:08Z'),
    ('00000000-0000-7000-8000-000000000402',
     '00000000-0000-7000-8000-000000000100',
     '00000000-0000-7000-8000-000000000201',
     '00000000-0000-7000-8000-000000000110', 'TC-000002',
     '00000000-0000-7000-8000-000000000503', 'ACTIVE', 1, 1,
     '2026-09-04T00:00:06Z', '2026-09-04T00:00:06Z'),
    ('00000000-0000-7000-8000-000000000403',
     '00000000-0000-7000-8000-000000000100',
     '00000000-0000-7000-8000-000000000202',
     '00000000-0000-7000-8000-000000000110', 'TC-000003',
     '00000000-0000-7000-8000-000000000504', 'DEPRECATED', 0, 1,
     '2026-09-04T00:00:07Z', '2026-09-04T00:00:07Z');
INSERT INTO test_case_versions
    (id, test_case_id, version_number, status, summary, precondition,
     expected_result_mode, expected_result, priority, assignee_label, source_type,
     source_metadata, change_reason, created_at)
VALUES
    ('00000000-0000-7000-8000-000000000501',
     '00000000-0000-7000-8000-000000000401', 1, 'ACTIVE',
     'Checkout accepts a valid synthetic order', 'A synthetic cart exists',
     'PER_STEP', NULL, 'HIGH', 'fixture-owner-a', 'MANUAL',
     '{"fixture":"pf-sf1-t07","revision":1}', 'Initial fixture version',
     '2026-09-04T00:00:05Z'),
    ('00000000-0000-7000-8000-000000000502',
     '00000000-0000-7000-8000-000000000401', 2, 'ACTIVE',
     'Checkout records a valid synthetic order', 'A synthetic cart exists',
     'PER_STEP', NULL, 'CRITICAL', 'fixture-owner-a', 'MANUAL',
     '{"fixture":"pf-sf1-t07","revision":2}', 'Clarify ledger result',
     '2026-09-04T00:00:08Z'),
    ('00000000-0000-7000-8000-000000000503',
     '00000000-0000-7000-8000-000000000402', 1, 'ACTIVE',
     'Checkout rejects an expired synthetic token', 'An expired fixture token exists',
     'CASE', 'The request is rejected without creating an order', 'HIGH',
     'fixture-owner-b', 'API', '{"fixture":"pf-sf1-t07","revision":1}',
     'Initial fixture version', '2026-09-04T00:00:06Z'),
    ('00000000-0000-7000-8000-000000000504',
     '00000000-0000-7000-8000-000000000403', 1, 'DEPRECATED',
     'Legacy account recovery path', 'A synthetic legacy account exists',
     'PER_STEP', NULL, 'LOW', NULL, 'FILE_IMPORT',
     '{"fixture":"pf-sf1-t07","revision":1}', 'Deprecated fixture coverage',
     '2026-09-04T00:00:07Z');
INSERT INTO test_steps
    (id, test_case_version_id, sequence, precondition, action, expected_result)
VALUES
    ('00000000-0000-7000-8000-000000000601',
     '00000000-0000-7000-8000-000000000501', 1, NULL,
     'Submit the synthetic order', 'The order is accepted'),
    ('00000000-0000-7000-8000-000000000602',
     '00000000-0000-7000-8000-000000000502', 1, NULL,
     'Submit the synthetic order', 'The ledger records one order'),
    ('00000000-0000-7000-8000-000000000603',
     '00000000-0000-7000-8000-000000000502', 2, NULL,
     'Read the synthetic order', 'The recorded values match'),
    ('00000000-0000-7000-8000-000000000604',
     '00000000-0000-7000-8000-000000000504', 1, NULL,
     'Open the legacy recovery page', 'A deprecation notice is shown'),
    ('00000000-0000-7000-8000-000000000605',
     '00000000-0000-7000-8000-000000000503', 1, NULL,
     'Submit the expired synthetic token', NULL);
INSERT INTO test_case_tags (test_case_id, tag_id)
VALUES
    ('00000000-0000-7000-8000-000000000401',
     '00000000-0000-7000-8000-000000000301'),
    ('00000000-0000-7000-8000-000000000401',
     '00000000-0000-7000-8000-000000000302'),
    ('00000000-0000-7000-8000-000000000402',
     '00000000-0000-7000-8000-000000000301');
INSERT INTO test_case_version_tags (test_case_version_id, tag_id)
VALUES
    ('00000000-0000-7000-8000-000000000501',
     '00000000-0000-7000-8000-000000000301'),
    ('00000000-0000-7000-8000-000000000501',
     '00000000-0000-7000-8000-000000000302'),
    ('00000000-0000-7000-8000-000000000502',
     '00000000-0000-7000-8000-000000000301'),
    ('00000000-0000-7000-8000-000000000502',
     '00000000-0000-7000-8000-000000000302'),
    ('00000000-0000-7000-8000-000000000503',
     '00000000-0000-7000-8000-000000000301');
INSERT INTO test_cycles
    (id, workspace_id, name, description, starts_at, status, scope_type,
     scope_value, scope_values, resource_version, run_number, created_at, updated_at)
VALUES
    ('00000000-0000-7000-8000-000000000801',
     '00000000-0000-7000-8000-000000000100', 'Recovery rehearsal cycle',
     'Deterministic planned cycle', '2026-09-05T00:00:00Z', 'PLANNED',
     'WORK_ITEM', 'RECOVERY-001', '["RECOVERY-001"]', 1, 1,
     '2026-09-04T00:00:09Z', '2026-09-04T00:00:09Z');
INSERT INTO cycle_items
    (id, cycle_id, test_case_id, test_case_version_id, work_item_id,
     assignee_label, created_at, updated_at)
VALUES
    ('00000000-0000-7000-8000-000000000811',
     '00000000-0000-7000-8000-000000000801',
     '00000000-0000-7000-8000-000000000401',
     '00000000-0000-7000-8000-000000000502',
     '00000000-0000-7000-8000-000000000201', 'fixture-owner-a',
     '2026-09-04T00:00:09Z', '2026-09-04T00:00:09Z');
INSERT INTO issues
    (id, workspace_id, issue_key, summary, precondition, expected_result,
     actual_result, source_note, status, severity, assignee_label,
     resource_version, created_at, updated_at)
VALUES
    ('00000000-0000-7000-8000-000000000901',
     '00000000-0000-7000-8000-000000000100', 'ISSUE-000001',
     'Synthetic recovery observation', 'The deterministic fixture is loaded',
     'The ledger remains readable', 'The local rehearsal captured this observation',
     'Synthetic only', 'OPEN', 'MEDIUM', 'fixture-owner-b', 1,
     '2026-09-04T00:00:10Z', '2026-09-04T00:00:10Z');
INSERT INTO issue_steps (id, issue_id, sequence, action, expected_result)
VALUES
    ('00000000-0000-7000-8000-000000000911',
     '00000000-0000-7000-8000-000000000901', 1,
     'Read restored synthetic case TC-000001', 'The case is returned');
INSERT INTO issue_links
    (id, issue_id, test_case_id, test_case_version_id, cycle_item_id, created_at)
VALUES
    ('00000000-0000-7000-8000-000000000921',
     '00000000-0000-7000-8000-000000000901',
     '00000000-0000-7000-8000-000000000401',
     '00000000-0000-7000-8000-000000000502',
     '00000000-0000-7000-8000-000000000811', '2026-09-04T00:00:10Z');
INSERT INTO comments
    (id, workspace_id, owner_type, owner_id, body, author_label, created_at, updated_at)
VALUES
    ('00000000-0000-7000-8000-000000000931',
     '00000000-0000-7000-8000-000000000100', 'ISSUE',
     '00000000-0000-7000-8000-000000000901',
     'Deterministic synthetic recovery comment', 'fixture-owner-a',
     '2026-09-04T00:00:11Z', '2026-09-04T00:00:11Z');
INSERT INTO audit_logs
    (id, workspace_id, entity_type, entity_id, action, before, after,
     source_type, actor_label, reason, trace_id, idempotency_key, occurred_at)
VALUES
    ('00000000-0000-7000-8000-000000000701',
     '00000000-0000-7000-8000-000000000100', 'TEST_CASE',
     '00000000-0000-7000-8000-000000000401', 'CREATE', NULL,
     '{"caseKey":"TC-000001","fixture":"pf-sf1-t07"}', 'SYSTEM',
     'phase-f-fixture', 'Deterministic recovery rehearsal',
     'trace_pf_sf1_t07_fixture_1', 'pf-sf1-t07-fixture-case-1',
     '2026-09-04T00:00:12Z'),
    ('00000000-0000-7000-8000-000000000702',
     '00000000-0000-7000-8000-000000000100', 'ISSUE',
     '00000000-0000-7000-8000-000000000901', 'CREATE', NULL,
     '{"issueKey":"ISSUE-000001","fixture":"pf-sf1-t07"}', 'SYSTEM',
     'phase-f-fixture', 'Deterministic recovery rehearsal',
     'trace_pf_sf1_t07_fixture_2', 'pf-sf1-t07-fixture-issue-1',
     '2026-09-04T00:00:13Z');
"""


def _recovery_seed_counts(connection: psycopg.Connection[Any]) -> dict[str, int]:
    tables = (
        "workspace_settings",
        "folders",
        "work_items",
        "test_cases",
        "test_case_versions",
        "test_steps",
        "test_cycles",
        "cycle_items",
        "issues",
        "audit_logs",
    )
    counts: dict[str, int] = {}
    for table in tables:
        row = connection.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
        ).fetchone()
        if row is None:
            raise SafetyError(f"could not count recovery fixture table {table}")
        counts[table] = int(row[0])
    return counts


def _database_rows_fingerprint(
    connection: psycopg.Connection[Any],
) -> dict[str, object]:
    table_rows = connection.execute(
        "SELECT c.relname FROM pg_class AS c "
        "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') "
        "ORDER BY c.relname"
    ).fetchall()
    table_evidence: list[dict[str, object]] = []
    combined = hashlib.sha256()
    total_rows = 0
    for (table_name_value,) in table_rows:
        table_name = str(table_name_value)
        digest = hashlib.sha256()
        count = 0
        cursor = connection.execute(
            sql.SQL(
                "SELECT to_jsonb(row_value)::text FROM {} AS row_value "
                "ORDER BY to_jsonb(row_value)::text"
            ).format(sql.Identifier(table_name))
        )
        for (serialized,) in cursor:
            encoded = (str(serialized) + "\n").encode("utf-8")
            digest.update(encoded)
            count += 1
        table_digest = digest.hexdigest()
        combined.update(
            f"{table_name}\t{count}\t{table_digest}\n".encode("utf-8")
        )
        table_evidence.append(
            {"table": table_name, "rowCount": count, "sha256": table_digest}
        )
        total_rows += count
    return {
        "tableCount": len(table_evidence),
        "totalRows": total_rows,
        "dataSha256": combined.hexdigest(),
        "tables": table_evidence,
    }


def _explain_output_expression(plan_value: object) -> str:
    if isinstance(plan_value, dict):
        output = plan_value.get("Output")
        if isinstance(output, list) and len(output) == 1 and isinstance(output[0], str):
            return output[0]
        for child in plan_value.values():
            try:
                return _explain_output_expression(child)
            except SafetyError:
                continue
    elif isinstance(plan_value, list):
        for child in plan_value:
            try:
                return _explain_output_expression(child)
            except SafetyError:
                continue
    raise SafetyError("could not canonicalize a catalog expression")


def _canonicalize_relation_check_constraints(
    connection: psycopg.Connection[Any], rows: list[tuple[object, ...]]
) -> list[tuple[object, ...]]:
    checks = connection.execute(
        "SELECT c.relname, x.conname, pg_get_expr(x.conbin,x.conrelid,false) "
        "FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND x.contype='c' ORDER BY 1,2"
    ).fetchall()
    canonical: dict[tuple[str, str], str] = {}
    for relation_value, constraint_value, expression_value in checks:
        relation = str(relation_value)
        expression = str(expression_value)
        statement = sql.SQL(
            "EXPLAIN (VERBOSE, FORMAT JSON, COSTS FALSE) "
            "SELECT ({}) AS probe FROM {}.{} LIMIT 0"
        ).format(
            sql.SQL(expression),
            sql.Identifier("public"),
            sql.Identifier(relation),
        )
        result = connection.execute(statement).fetchone()
        if result is None:
            raise SafetyError("could not canonicalize a public CHECK constraint")
        canonical[(relation, str(constraint_value))] = (
            "CHECK (" + _explain_output_expression(result[0]) + ")"
        )
    normalized: list[tuple[object, ...]] = []
    for row in rows:
        relation = str(row[0]).removeprefix("public.")
        definition = canonical.get((relation, str(row[1])))
        if definition is None:
            normalized.append(row)
        else:
            replacement = list(row)
            replacement[3] = definition
            normalized.append(tuple(replacement))
    return normalized


def _database_schema_fingerprint(
    connection: psycopg.Connection[Any],
) -> dict[str, object]:
    # PostgreSQL's deparsers are the stable semantic boundary: they avoid
    # volatile OIDs and dump-session boilerplate while retaining the complete
    # definition of constraints, expression/partial indexes, routines, views,
    # triggers, policies, and partition bounds. Never reduce expressions to
    # booleans such as "is present"; doing so made the original wave-6 verdict
    # falsely green for meaningfully different catalogs.
    queries: dict[str, str] = {
        "relations": (
            "SELECT c.relname, c.relkind, c.relpersistence, c.relispartition, "
            "c.relrowsecurity, c.relforcerowsecurity, c.relreplident, "
            "COALESCE(am.amname,''), COALESCE(ts.spcname,''), "
            "COALESCE(c.reloptions::text,''), "
            "COALESCE(obj_description(c.oid,'pg_class'),'') "
            "FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace "
            "LEFT JOIN pg_am AS am ON am.oid=c.relam "
            "LEFT JOIN pg_tablespace AS ts ON ts.oid=c.reltablespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','f') "
            "ORDER BY 1,2,3"
        ),
        "columns": (
            "SELECT c.relname, a.attname, a.attnum, format_type(a.atttypid,a.atttypmod), "
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
            "AND a.attnum>0 AND NOT a.attisdropped ORDER BY 1,3"
        ),
        "constraints": (
            "SELECT CASE WHEN x.conrelid<>0 THEN quote_ident(rn.nspname)||'.'||"
            "quote_ident(c.relname) ELSE quote_ident(tn.nspname)||'.'||"
            "quote_ident(t.typname) END, x.conname, x.contype, "
            "pg_get_constraintdef(x.oid,false), x.condeferrable, x.condeferred, "
            "x.convalidated, x.connoinherit, x.conislocal, x.coninhcount, "
            "COALESCE(quote_ident(fn.nspname)||'.'||quote_ident(f.relname),'') "
            "FROM pg_constraint AS x "
            "LEFT JOIN pg_class AS c ON c.oid=x.conrelid "
            "LEFT JOIN pg_namespace AS rn ON rn.oid=c.relnamespace "
            "LEFT JOIN pg_type AS t ON t.oid=x.contypid "
            "LEFT JOIN pg_namespace AS tn ON tn.oid=t.typnamespace "
            "LEFT JOIN pg_class AS f ON f.oid=x.confrelid "
            "LEFT JOIN pg_namespace AS fn ON fn.oid=f.relnamespace "
            "WHERE rn.nspname='public' OR tn.nspname='public' ORDER BY 1,2"
        ),
        "indexes": (
            "SELECT t.relname, i.relname, x.indisunique, x.indisprimary, "
            "x.indisexclusion, x.indisvalid, x.indisready, x.indislive, "
            "x.indisclustered, x.indisreplident, x.indnullsnotdistinct, "
            "pg_get_indexdef(i.oid,0,false), "
            "COALESCE(pg_get_expr(x.indexprs,x.indrelid,false),''), "
            "COALESCE(pg_get_expr(x.indpred,x.indrelid,false),''), "
            "am.amname, COALESCE(ts.spcname,''), COALESCE(i.reloptions::text,'') "
            "FROM pg_index AS x "
            "JOIN pg_class AS t ON t.oid=x.indrelid "
            "JOIN pg_class AS i ON i.oid=x.indexrelid "
            "JOIN pg_namespace AS n ON n.oid=t.relnamespace "
            "JOIN pg_am AS am ON am.oid=i.relam "
            "LEFT JOIN pg_tablespace AS ts ON ts.oid=i.reltablespace "
            "WHERE n.nspname='public' ORDER BY 1,2"
        ),
        "triggers": (
            "SELECT c.relname, t.tgname, t.tgenabled, "
            "pg_get_triggerdef(t.oid,false), t.tgdeferrable, t.tginitdeferred, "
            "COALESCE(t.tgoldtable,''), COALESCE(t.tgnewtable,'') "
            "FROM pg_trigger AS t "
            "JOIN pg_class AS c ON c.oid=t.tgrelid "
            "JOIN pg_namespace AS n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY 1,2"
        ),
        "policies": (
            "SELECT c.relname, p.polname, p.polcmd, p.polpermissive, "
            "ARRAY(SELECT r.rolname FROM unnest(p.polroles) role_oid "
            "JOIN pg_roles r ON r.oid=role_oid ORDER BY r.rolname)::text, "
            "COALESCE(pg_get_expr(p.polqual,p.polrelid,false),''), "
            "COALESCE(pg_get_expr(p.polwithcheck,p.polrelid,false),'') "
            "FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' ORDER BY 1,2"
        ),
        "routines": (
            "SELECT p.proname, p.prokind, pg_get_function_identity_arguments(p.oid), "
            "CASE WHEN p.prokind='a' THEN '' ELSE pg_get_functiondef(p.oid) END, "
            "p.proconfig::text, p.proparallel, p.procost, p.prorows, "
            "p.proleakproof, p.proisstrict, "
            "COALESCE(a.aggkind::text,''), COALESCE(a.aggnumdirectargs,0), "
            "COALESCE(a.aggtransfn::regprocedure::text,''), "
            "COALESCE(a.aggfinalfn::regprocedure::text,''), "
            "COALESCE(a.aggcombinefn::regprocedure::text,''), "
            "COALESCE(a.aggserialfn::regprocedure::text,''), "
            "COALESCE(a.aggdeserialfn::regprocedure::text,''), "
            "COALESCE(a.aggmtransfn::regprocedure::text,''), "
            "COALESCE(a.aggminvtransfn::regprocedure::text,''), "
            "COALESCE(a.aggmfinalfn::regprocedure::text,''), "
            "COALESCE(format_type(a.aggtranstype,NULL),''), "
            "COALESCE(format_type(a.aggmtranstype,NULL),''), "
            "COALESCE(a.agginitval,''), COALESCE(a.aggminitval,'') "
            "FROM pg_proc AS p JOIN pg_namespace AS n ON n.oid=p.pronamespace "
            "LEFT JOIN pg_aggregate a ON a.aggfnoid=p.oid "
            "WHERE n.nspname='public' ORDER BY 1,2,3"
        ),
        "views": (
            "SELECT c.relname, c.relkind, pg_get_viewdef(c.oid,false), "
            "c.relispopulated, COALESCE(c.reloptions::text,'') FROM pg_class AS c "
            "JOIN pg_namespace AS n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relkind IN ('v','m') ORDER BY 1,2"
        ),
        "types": (
            "SELECT t.typname, t.typtype, t.typcategory, t.typispreferred, "
            "t.typdelim, t.typnotnull, format_type(t.typbasetype,t.typtypmod), "
            "COALESCE(pg_get_expr(t.typdefaultbin,0,false),t.typdefault,''), "
            "CASE WHEN t.typcollation=0 THEN '' ELSE quote_ident(cn.nspname)||'.'||"
            "quote_ident(co.collname) END, "
            "COALESCE((SELECT string_agg(e.enumlabel, E'\\n' ORDER BY e.enumsortorder) "
            "FROM pg_enum e WHERE e.enumtypid=t.oid),''), "
            "COALESCE((SELECT string_agg(a.attname||':'||format_type(a.atttypid,a.atttypmod), "
            "E'\\n' ORDER BY a.attnum) FROM pg_attribute a WHERE a.attrelid=t.typrelid "
            "AND a.attnum>0 AND NOT a.attisdropped),''), "
            "COALESCE(format_type(r.rngsubtype,NULL),''), "
            "COALESCE(opn.nspname||'.'||opc.opcname,''), "
            "COALESCE(obj_description(t.oid,'pg_type'),'') "
            "FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace "
            "LEFT JOIN pg_class tc ON tc.oid=t.typrelid "
            "LEFT JOIN pg_range r ON r.rngtypid=t.oid "
            "LEFT JOIN pg_opclass opc ON opc.oid=r.rngsubopc "
            "LEFT JOIN pg_namespace opn ON opn.oid=opc.opcnamespace "
            "LEFT JOIN pg_collation co ON co.oid=t.typcollation "
            "LEFT JOIN pg_namespace cn ON cn.oid=co.collnamespace "
            "WHERE n.nspname='public' AND t.typisdefined "
            "AND t.typname !~ '^_' AND (t.typrelid=0 OR tc.relkind='c') "
            "ORDER BY 1"
        ),
        "partitions": (
            "SELECT c.relname, c.relkind, COALESCE(pg_get_partkeydef(c.oid),''), "
            "COALESCE(pg_get_expr(c.relpartbound,c.oid,false),''), "
            "COALESCE(pn.nspname||'.'||p.relname,''), "
            "COALESCE(pt.partstrat::text,''), COALESCE(pt.partnatts,0) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "LEFT JOIN pg_inherits inh ON inh.inhrelid=c.oid "
            "LEFT JOIN pg_class p ON p.oid=inh.inhparent "
            "LEFT JOIN pg_namespace pn ON pn.oid=p.relnamespace "
            "LEFT JOIN pg_partitioned_table pt ON pt.partrelid=c.oid "
            "WHERE n.nspname='public' AND (c.relkind='p' OR c.relispartition) "
            "ORDER BY 1,5"
        ),
        "inheritance": (
            "SELECT c.relname, pn.nspname||'.'||p.relname, i.inhseqno, "
            "i.inhdetachpending FROM pg_inherits i "
            "JOIN pg_class c ON c.oid=i.inhrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "JOIN pg_class p ON p.oid=i.inhparent "
            "JOIN pg_namespace pn ON pn.oid=p.relnamespace "
            "WHERE n.nspname='public' ORDER BY 1,3"
        ),
        "collations": (
            "SELECT c.collname, c.collprovider, c.collisdeterministic, c.collencoding, "
            "c.collcollate, c.collctype, COALESCE(c.colliculocale,''), "
            "COALESCE(c.collicurules,''), COALESCE(c.collversion,'') "
            "FROM pg_collation c JOIN pg_namespace n ON n.oid=c.collnamespace "
            "WHERE n.nspname='public' ORDER BY 1"
        ),
        "rules": (
            "SELECT c.relname, r.rulename, r.ev_enabled, pg_get_ruledef(r.oid,false) "
            "FROM pg_rewrite r JOIN pg_class c ON c.oid=r.ev_class "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND r.rulename<>'_RETURN' ORDER BY 1,2"
        ),
        "statistics": (
            "SELECT s.stxname, pg_get_statisticsobjdef(s.oid), "
            "COALESCE(s.stxstattarget,-1), s.stxkind::text "
            "FROM pg_statistic_ext s JOIN pg_namespace n ON n.oid=s.stxnamespace "
            "WHERE n.nspname='public' ORDER BY 1"
        ),
        "extensions": (
            "SELECT e.extname, e.extversion, n.nspname, e.extrelocatable, "
            "COALESCE(e.extcondition::text,'') FROM pg_extension AS e "
            "JOIN pg_namespace AS n ON n.oid=e.extnamespace "
            "WHERE e.extname <> 'plpgsql' ORDER BY 1"
        ),
    }
    categories: dict[str, list[tuple[object, ...]]] = {
        category: connection.execute(query).fetchall()
        for category, query in queries.items()
    }
    categories["constraints"] = _canonicalize_relation_check_constraints(
        connection, categories["constraints"]
    )
    sequence_rows = connection.execute(
        "SELECT c.relname, format_type(s.seqtypid,NULL), s.seqstart, "
        "s.seqincrement, s.seqmax, s.seqmin, s.seqcache, s.seqcycle, "
        "COALESCE(dn.nspname||'.'||dc.relname||'.'||a.attname,'') "
        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_sequence s ON s.seqrelid=c.oid "
        "LEFT JOIN pg_depend d ON d.classid='pg_class'::regclass "
        "AND d.objid=c.oid AND d.deptype IN ('a','i') "
        "LEFT JOIN pg_class dc ON dc.oid=d.refobjid "
        "LEFT JOIN pg_namespace dn ON dn.oid=dc.relnamespace "
        "LEFT JOIN pg_attribute a ON a.attrelid=d.refobjid AND a.attnum=d.refobjsubid "
        "WHERE n.nspname='public' ORDER BY c.relname"
    ).fetchall()
    sequences: list[tuple[object, ...]] = []
    for sequence_row in sequence_rows:
        sequence_name = str(sequence_row[0])
        state = connection.execute(
            sql.SQL("SELECT last_value, is_called FROM {}.{}").format(
                sql.Identifier("public"), sql.Identifier(sequence_name)
            )
        ).fetchone()
        if state is None:
            raise SafetyError("could not read complete public sequence state")
        sequences.append((*sequence_row, *state))
    categories["sequences"] = sequences

    digest = hashlib.sha256(CATALOG_FINGERPRINT_VERSION.encode("utf-8"))
    counts: dict[str, int] = {}
    category_digests: dict[str, str] = {}
    for category in sorted(categories):
        rows = categories[category]
        counts[category] = len(rows)
        digest.update(f"[{category}]\n".encode("utf-8"))
        category_digest = hashlib.sha256()
        for row in rows:
            encoded = (
                json.dumps(
                    [_canonical_catalog_scalar(value) for value in row],
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
                + "\n"
            ).encode("utf-8")
            category_digest.update(encoded)
            digest.update(encoded)
        category_digests[category] = category_digest.hexdigest()
    return {
        "catalogFingerprintVersion": CATALOG_FINGERPRINT_VERSION,
        "schemaSha256": digest.hexdigest(),
        "schemaObjectCounts": counts,
        "schemaCategorySha256": category_digests,
    }


def _snapshot_metadata(
    connection: psycopg.Connection[Any],
) -> dict[str, str]:
    row = connection.execute(
        "SELECT pg_export_snapshot(), "
        "to_char(clock_timestamp() AT TIME ZONE 'UTC', "
        "'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"'), "
        "pg_current_wal_lsn()::text, txid_current_snapshot()::text"
    ).fetchone()
    if row is None:
        raise SafetyError("could not export an exact PostgreSQL recovery snapshot")
    return {
        "snapshotId": str(row[0]),
        "capturedAtUtc": str(row[1]),
        "walLsn": str(row[2]),
        "transactionSnapshot": str(row[3]),
    }


def _begin_read_only_snapshot(database: DatabaseTarget) -> psycopg.Connection[Any]:
    connection: psycopg.Connection[Any] = psycopg.connect(database.libpq_url)
    connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
    return connection


def _run_postgres_seed(args: argparse.Namespace, target: Target) -> dict[str, object]:
    if target.target_class != "disposable":
        raise SafetyError("the synthetic recovery fixture requires a disposable target")
    _validate_database_declaration(args, target)
    output = _safe_evidence_output(getattr(args, "output", None))
    plan = _base_plan(
        task="PF-SF1-T07",
        action="POSTGRES-SEED",
        target=target,
        mutates=["declared empty attested disposable PostgreSQL source"],
        reads=[],
        steps=[
            "verify exact database identity, comment attestation, and zero public tables",
            f"migrate the source to Alembic head {HEAD_REVISION}",
            "insert fixed-ID, fixed-time, non-sensitive representative ledger rows",
            "record exact table counts and SHA-256 fingerprints",
        ],
    )
    plan["evidenceFile"] = str(output.relative_to(ROOT)) if output else None
    if not args.execute:
        return plan
    _require_confirmation(args, str(plan["confirmationRequired"]))
    database = _database_target(args, target)
    client = _resolve_postgresql_client(database)
    preflight = _database_preflight(
        database, target, require_empty=True, client=client
    )
    started = time.perf_counter()
    _alembic(database, "upgrade", HEAD_REVISION)
    post_migration = _database_preflight(
        database, target, expected_revision=HEAD_REVISION, client=client
    )
    with psycopg.connect(database.libpq_url) as connection:
        empty_counts = _recovery_seed_counts(connection)
        expected_baseline = {
            "workspace_settings": 1,
            "folders": 1,
            "work_items": 0,
            "test_cases": 0,
            "test_case_versions": 0,
            "test_steps": 0,
            "test_cycles": 0,
            "cycle_items": 0,
            "issues": 0,
            "audit_logs": 0,
        }
        if empty_counts != expected_baseline:
            raise SafetyError("recovery source ledger tables must be empty before seeding")
        connection.execute(RECOVERY_SEED_SQL)
        seeded_counts = _recovery_seed_counts(connection)
    fingerprint = _capture_database_fingerprint(database, client=client)
    result: dict[str, object] = {
        **plan,
        "mode": "executed",
        "executes": True,
        "preflight": preflight,
        "postMigration": post_migration,
        "client": _postgresql_client_evidence(client),
        "fixture": {
            "deterministic": True,
            "sensitiveData": False,
            "counts": seeded_counts,
        },
        "fingerprint": fingerprint,
        "elapsedSeconds": round(time.perf_counter() - started, 6),
    }
    _write_json_evidence(output, result)
    return result


def _plain_dump(
    database: DatabaseTarget,
    *,
    schema_only: bool,
    client: PostgreSQLClient | None = None,
    snapshot_id: str | None = None,
) -> str:
    active_client = client or _resolve_postgresql_client(database)
    option = "--schema-only" if schema_only else "--data-only"
    arguments = [
        "--format=plain",
        option,
        "--no-owner",
        "--no-privileges",
    ]
    if snapshot_id is not None:
        arguments.append(f"--snapshot={snapshot_id}")
    command, environment = _postgresql_command(
        active_client, "pg_dump", database, *arguments
    )
    result = _command(command, env=environment, capture=True)
    return result.stdout


def _capture_database_fingerprint(
    database: DatabaseTarget,
    *,
    client: PostgreSQLClient,
) -> dict[str, object]:
    connection = _begin_read_only_snapshot(database)
    try:
        rows = _database_rows_fingerprint(connection)
        schema = _database_schema_fingerprint(connection)
        return {**schema, **rows}
    finally:
        connection.rollback()
        connection.close()


def _archive_listing(
    dump_source: PinnedReadFile,
    *,
    client: PostgreSQLClient,
    dump_sha256: str,
) -> dict[str, object]:
    command, environment = _postgresql_command(
        client, "pg_restore", None, "--list"
    )
    def inventory() -> dict[str, object]:
        result = _command_from_pinned_file(
            command, dump_source, env=environment, capture=True
        )
        listing = result.stdout.decode("utf-8")
        entries = [
            line for line in listing.splitlines() if line and not line.startswith(";")
        ]
        if not entries:
            raise SafetyError("pg_restore --list returned an empty archive inventory")
        return {
            "validated": True,
            "entryCount": len(entries),
            "listingSha256": hashlib.sha256(listing.encode("utf-8")).hexdigest(),
            "dumpBytes": dump_source.identity.size,
            "dumpSha256": dump_sha256,
        }

    return dump_source.run_stage("pg_restore archive inventory", inventory)


def _recovery_point_age_seconds(captured_at: str) -> float:
    captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    return round(max(0.0, (datetime.now(UTC) - captured).total_seconds()), 6)


def _run_postgres_backup(args: argparse.Namespace, target: Target) -> dict[str, object]:
    _validate_database_declaration(args, target)
    dump_file = _safe_dump_path(args.dump_file, must_exist=False)
    output = _safe_evidence_output(getattr(args, "output", None))
    plan = _base_plan(
        task="PF-SF1-T07",
        action="POSTGRES-BACKUP",
        target=target,
        mutates=["new custom-format dump outside the repository"],
        reads=["declared attested PostgreSQL source"],
        steps=[
            "verify database identity and comment attestation",
            f"require source Alembic revision {HEAD_REVISION}",
            "export one read-only repeatable-read snapshot with timestamp and WAL LSN",
            "run pg_dump --format=custom against that exact snapshot without owner or ACL",
            "verify archive readability with pg_restore --list",
            "report timing, schema/data/row digests, bytes, and SHA-256 without credentials",
        ],
    )
    plan["evidenceFile"] = str(output.relative_to(ROOT)) if output else None
    plan["rpoBoundary"] = (
        "measured recovery-point timestamp and age only; no production RPO promise"
    )
    if not args.execute:
        return plan
    _require_confirmation(args, str(plan["confirmationRequired"]))
    database = _database_target(args, target)
    client = _resolve_postgresql_client(database)
    preflight = _database_preflight(
        database,
        target,
        expected_revision=HEAD_REVISION,
        client=client,
    )
    backup_started_at = datetime.now(UTC)
    backup_started = time.perf_counter()
    connection = _begin_read_only_snapshot(database)
    try:
        recovery_point = _snapshot_metadata(connection)
        source_rows = _database_rows_fingerprint(connection)
        source_schema = _database_schema_fingerprint(connection)
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
    with PinnedReadFile(
        dump_file,
        label="database dump",
        require_nonempty=True,
    ) as dump_source:
        checksum = dump_source.run_stage(
            "SHA-256", lambda: _pinned_file_sha256(dump_source)
        )
        archive = _archive_listing(
            dump_source,
            client=client,
            dump_sha256=checksum,
        )
        dump_bytes = dump_source.identity.size
        dump_file_safety = dump_source.safety_evidence()
    backup_finished_at = datetime.now(UTC)
    if archive.get("dumpSha256") != checksum or archive.get("dumpBytes") != dump_bytes:
        raise SafetyError("custom archive changed while its final inventory was validated")
    result: dict[str, object] = {
        **plan,
        "mode": "executed",
        "executes": True,
        "preflight": preflight,
        "client": _postgresql_client_evidence(client),
        "dumpFile": str(dump_file),
        "dumpBytes": dump_bytes,
        "dumpSha256": checksum,
        "dumpFileSafety": dump_file_safety,
        "archive": archive,
        "sourceFingerprint": {
            **source_schema,
            **source_rows,
        },
        "recoveryPoint": {
            **recovery_point,
            "measuredAgeAtBackupCompletionSeconds": _recovery_point_age_seconds(
                recovery_point["capturedAtUtc"]
            ),
            "productionRpoPromise": "NOT_CLAIMED",
        },
        "timing": {
            "startedAtUtc": backup_started_at.isoformat(),
            "finishedAtUtc": backup_finished_at.isoformat(),
            "backupSeconds": round(time.perf_counter() - backup_started, 6),
        },
    }
    _write_json_evidence(output, result)
    return result


def _require_exact_keys(
    value: object, expected: frozenset[str], label: str
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise SafetyError(f"backup evidence {label} has an invalid schema")
    return value


def _require_string_list(value: object, label: str) -> None:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise SafetyError(f"backup evidence {label} must be a non-empty string list")


def _require_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or HEX_DIGEST_PATTERN.fullmatch(value) is None:
        raise SafetyError(f"backup evidence {label} is not a lowercase SHA-256")
    return value


def _require_nonnegative_number(value: object, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise SafetyError(f"backup evidence {label} must be non-negative")


def _require_utc_timestamp(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise SafetyError(f"backup evidence {label} must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise SafetyError(f"backup evidence {label} must be a UTC timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise SafetyError(f"backup evidence {label} must be a UTC timestamp")
    return value


def _validate_source_fingerprint(value: object) -> dict[str, Any]:
    fingerprint = _require_exact_keys(
        value,
        frozenset(
            {
                "catalogFingerprintVersion",
                "schemaSha256",
                "schemaObjectCounts",
                "schemaCategorySha256",
                "tableCount",
                "totalRows",
                "dataSha256",
                "tables",
            }
        ),
        "sourceFingerprint",
    )
    if fingerprint["catalogFingerprintVersion"] != CATALOG_FINGERPRINT_VERSION:
        raise SafetyError("backup evidence uses an unsupported catalog fingerprint")
    _require_digest(fingerprint["schemaSha256"], "source schema digest")
    _require_digest(fingerprint["dataSha256"], "source data digest")
    counts = _require_exact_keys(
        fingerprint["schemaObjectCounts"], CATALOG_CATEGORIES, "catalog counts"
    )
    category_digests = _require_exact_keys(
        fingerprint["schemaCategorySha256"],
        CATALOG_CATEGORIES,
        "catalog category digests",
    )
    if not all(type(count) is int and count >= 0 for count in counts.values()):
        raise SafetyError("backup evidence catalog counts are invalid")
    for category, digest in category_digests.items():
        _require_digest(digest, f"{category} catalog digest")
    if type(fingerprint["tableCount"]) is not int or fingerprint["tableCount"] < 0:
        raise SafetyError("backup evidence table count is invalid")
    if type(fingerprint["totalRows"]) is not int or fingerprint["totalRows"] < 0:
        raise SafetyError("backup evidence total row count is invalid")
    tables = fingerprint["tables"]
    if not isinstance(tables, list) or len(tables) != fingerprint["tableCount"]:
        raise SafetyError("backup evidence per-table fingerprint count is invalid")
    names: list[str] = []
    row_sum = 0
    for table_value in tables:
        table = _require_exact_keys(
            table_value,
            frozenset({"table", "rowCount", "sha256"}),
            "per-table fingerprint",
        )
        if not isinstance(table["table"], str) or not table["table"]:
            raise SafetyError("backup evidence table name is invalid")
        if type(table["rowCount"]) is not int or table["rowCount"] < 0:
            raise SafetyError("backup evidence table row count is invalid")
        _require_digest(table["sha256"], "table digest")
        names.append(table["table"])
        row_sum += table["rowCount"]
    if names != sorted(set(names)) or row_sum != fingerprint["totalRows"]:
        raise SafetyError("backup evidence per-table fingerprints are inconsistent")
    return fingerprint


def _validate_file_safety(
    value: object,
    *,
    label: str,
    expected_size: int,
    expected_stages: list[str],
) -> dict[str, Any]:
    safety = _require_exact_keys(
        value,
        frozenset(
            {
                "singleOpenNoFollowFileDescriptor",
                "pathnameReopened",
                "regularFile",
                "permissionsAtMost0600",
                "singleLink",
                "identity",
                "verifiedBeforeAndAfterStages",
            }
        ),
        label,
    )
    if safety != {
        **safety,
        "singleOpenNoFollowFileDescriptor": True,
        "pathnameReopened": False,
        "regularFile": True,
        "permissionsAtMost0600": True,
        "singleLink": True,
    }:
        raise SafetyError(f"backup evidence {label} assertions are invalid")
    if safety["verifiedBeforeAndAfterStages"] != expected_stages:
        raise SafetyError(f"backup evidence {label} stages are invalid")
    identity = _require_exact_keys(
        safety["identity"],
        frozenset(
            {
                "device",
                "inode",
                "size",
                "mtimeNs",
                "ctimeNs",
                "modeOctal",
                "linkCount",
            }
        ),
        f"{label} identity",
    )
    for key in ("device", "inode", "size", "mtimeNs", "ctimeNs"):
        if type(identity[key]) is not int or identity[key] < 0:
            raise SafetyError(f"backup evidence {label} identity is invalid")
    if (
        identity["size"] != expected_size
        or identity["linkCount"] != 1
        or not isinstance(identity["modeOctal"], str)
        or re.fullmatch(r"0[0246]00", identity["modeOctal"]) is None
    ):
        raise SafetyError(f"backup evidence {label} identity is invalid")
    return safety


def _validate_backup_evidence_payload(
    payload_value: object, evidence_path: Path
) -> dict[str, Any]:
    payload = _require_exact_keys(
        payload_value,
        frozenset(
            {
                "ok",
                "mode",
                "executes",
                "task",
                "action",
                "target",
                "confirmationRequired",
                "mutates",
                "reads",
                "steps",
                "safety",
                "evidenceFile",
                "rpoBoundary",
                "preflight",
                "client",
                "dumpFile",
                "dumpBytes",
                "dumpSha256",
                "dumpFileSafety",
                "archive",
                "sourceFingerprint",
                "recoveryPoint",
                "timing",
            }
        ),
        "document",
    )
    if (
        payload["ok"] is not True
        or payload["mode"] != "executed"
        or payload["executes"] is not True
        or payload["task"] != "PF-SF1-T07"
        or payload["action"] != "POSTGRES-BACKUP"
    ):
        raise SafetyError("backup evidence is not an executed PF-SF1-T07 backup")
    for key in ("mutates", "reads", "steps"):
        _require_string_list(payload[key], key)
    target_value = _require_exact_keys(
        payload["target"], frozenset({"name", "class"}), "target"
    )
    if not all(isinstance(target_value[key], str) for key in target_value):
        raise SafetyError("backup evidence target is invalid")
    source_target = Target(target_value["name"], target_value["class"])
    if payload["confirmationRequired"] != _confirmation(
        "PF-SF1-T07", "POSTGRES-BACKUP", source_target
    ):
        raise SafetyError("backup evidence confirmation is not target-bound")
    try:
        expected_evidence_file = str(evidence_path.relative_to(ROOT))
    except ValueError as error:
        raise SafetyError("backup evidence is outside the repository") from error
    if payload["evidenceFile"] != expected_evidence_file:
        raise SafetyError("backup evidence path is not self-bound")
    if not isinstance(payload["rpoBoundary"], str) or not payload["rpoBoundary"]:
        raise SafetyError("backup evidence RPO boundary is invalid")
    safety = _require_exact_keys(
        payload["safety"],
        frozenset(
            {
                "rejectsProductionLikeNames",
                "databaseCommentAttestationRequired",
                "credentialsPrinted",
            }
        ),
        "safety",
    )
    if safety != {
        "rejectsProductionLikeNames": True,
        "databaseCommentAttestationRequired": True,
        "credentialsPrinted": False,
    }:
        raise SafetyError("backup evidence safety assertions are invalid")
    preflight = _require_exact_keys(
        payload["preflight"],
        frozenset(
            {
                "databaseName",
                "serverVersionNum",
                "attested",
                "databaseAttestation",
                "publicTableCount",
                "alembicRevision",
            }
        ),
        "preflight",
    )
    if not isinstance(preflight["databaseName"], str):
        raise SafetyError("backup evidence source database identity is invalid")
    _validate_database_name(preflight["databaseName"], source_target)
    if (
        type(preflight["serverVersionNum"]) is not int
        or preflight["serverVersionNum"] < 160_000
        or preflight["attested"] is not True
        or preflight["databaseAttestation"] != source_target.database_attestation
        or type(preflight["publicTableCount"]) is not int
        or preflight["publicTableCount"] < 0
        or preflight["alembicRevision"] != HEAD_REVISION
    ):
        raise SafetyError("backup evidence source preflight is invalid")
    client = _require_exact_keys(
        payload["client"],
        frozenset({"mode", "composeService", "credentialsInArgv"}),
        "client",
    )
    if client["mode"] not in {"host", "compose-exec"} or client[
        "credentialsInArgv"
    ] is not False:
        raise SafetyError("backup evidence client boundary is invalid")
    expected_service = (
        COMPOSE_POSTGRES_SERVICE if client["mode"] == "compose-exec" else None
    )
    if client["composeService"] != expected_service:
        raise SafetyError("backup evidence client service is invalid")
    dump_file = payload["dumpFile"]
    if (
        not isinstance(dump_file, str)
        or not Path(dump_file).is_absolute()
        or Path(dump_file).suffix not in {".dump", ".backup"}
    ):
        raise SafetyError("backup evidence dump path is invalid")
    if type(payload["dumpBytes"]) is not int or payload["dumpBytes"] <= 0:
        raise SafetyError("backup evidence dump size is invalid")
    dump_sha = _require_digest(payload["dumpSha256"], "dump digest")
    _validate_file_safety(
        payload["dumpFileSafety"],
        label="dump file safety",
        expected_size=payload["dumpBytes"],
        expected_stages=["SHA-256", "pg_restore archive inventory"],
    )
    archive = _require_exact_keys(
        payload["archive"],
        frozenset(
            {"validated", "entryCount", "listingSha256", "dumpBytes", "dumpSha256"}
        ),
        "archive",
    )
    if (
        archive["validated"] is not True
        or type(archive["entryCount"]) is not int
        or archive["entryCount"] <= 0
        or archive["dumpBytes"] != payload["dumpBytes"]
        or archive["dumpSha256"] != dump_sha
    ):
        raise SafetyError("backup evidence archive inventory is inconsistent")
    _require_digest(archive["listingSha256"], "archive listing digest")
    fingerprint = _validate_source_fingerprint(payload["sourceFingerprint"])
    if fingerprint["tableCount"] != preflight["publicTableCount"]:
        raise SafetyError("backup evidence source table counts are inconsistent")
    recovery = _require_exact_keys(
        payload["recoveryPoint"],
        frozenset(
            {
                "snapshotId",
                "capturedAtUtc",
                "walLsn",
                "transactionSnapshot",
                "measuredAgeAtBackupCompletionSeconds",
                "productionRpoPromise",
            }
        ),
        "recoveryPoint",
    )
    if not all(
        isinstance(recovery[key], str) and recovery[key]
        for key in ("snapshotId", "walLsn", "transactionSnapshot")
    ):
        raise SafetyError("backup evidence recovery identity is invalid")
    _require_utc_timestamp(recovery["capturedAtUtc"], "recovery timestamp")
    _require_nonnegative_number(
        recovery["measuredAgeAtBackupCompletionSeconds"], "recovery age"
    )
    if recovery["productionRpoPromise"] != "NOT_CLAIMED":
        raise SafetyError("backup evidence makes an unsupported RPO claim")
    timing = _require_exact_keys(
        payload["timing"],
        frozenset({"startedAtUtc", "finishedAtUtc", "backupSeconds"}),
        "timing",
    )
    _require_utc_timestamp(timing["startedAtUtc"], "backup start")
    _require_utc_timestamp(timing["finishedAtUtc"], "backup finish")
    _require_nonnegative_number(timing["backupSeconds"], "backup duration")
    return payload


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SafetyError("backup evidence contains a duplicate JSON key")
        result[key] = value
    return result


def _load_backup_evidence(
    path_value: str,
) -> tuple[Path, dict[str, Any], dict[str, object]]:
    path = _resolve_non_symlink_path(path_value)
    try:
        path.relative_to(EVIDENCE_ROOT.resolve())
    except ValueError as error:
        raise SafetyError("backup evidence must be below the Phase F evidence root") from error
    if path.suffix != ".json":
        raise SafetyError("backup evidence must be a regular non-symlink JSON file")
    with PinnedReadFile(
        path,
        label="backup evidence",
        maximum_size=2 * 1024 * 1024,
        require_nonempty=True,
    ) as evidence_source:
        try:
            payload_value = evidence_source.run_stage(
                "JSON read/parse",
                lambda: json.load(
                    io.StringIO(evidence_source.read_bytes().decode("utf-8")),
                    object_pairs_hook=_reject_duplicate_json_keys,
                ),
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SafetyError("backup evidence is not valid UTF-8 JSON") from error
        payload = _validate_backup_evidence_payload(payload_value, path)
        file_safety = evidence_source.safety_evidence()
    return path, payload, file_safety


def _application_restore_smoke(database: DatabaseTarget) -> dict[str, object]:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text

    from tc_api.main import create_app  # type: ignore[import-untyped]

    engine = create_engine(
        database.url,
        pool_pre_ping=True,
        connect_args={"options": "-c default_transaction_read_only=on"},
    )
    requests = (
        ("health", "/api/health"),
        ("ready", "/api/ready"),
        ("workspace", "/api/workspace/settings"),
        ("workItems", "/api/work-items"),
        ("testCases", "/api/test-cases?includeDeleted=true&limit=50"),
    )
    results: dict[str, dict[str, object]] = {}
    try:
        with engine.connect() as connection:
            default_read_only = connection.execute(
                text("SHOW default_transaction_read_only")
            ).scalar_one()
            transaction_read_only = connection.execute(
                text("SHOW transaction_read_only")
            ).scalar_one()
        if default_read_only != "on" or transaction_read_only != "on":
            raise SafetyError("restored application connections are not read-only")
        with TestClient(
            create_app(validate_database=False, database_engine=engine)
        ) as client:
            for label, path in requests:
                response = client.get(path)
                if response.status_code != 200:
                    raise SafetyError(
                        f"restored application smoke failed for {label}: "
                        f"HTTP {response.status_code}"
                    )
                payload = response.json()
                encoded = json.dumps(
                    payload, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
                evidence: dict[str, object] = {
                    "httpStatus": response.status_code,
                    "responseSha256": hashlib.sha256(encoded).hexdigest(),
                }
                if isinstance(payload, dict) and isinstance(payload.get("total"), int):
                    evidence["total"] = payload["total"]
                if label == "ready" and isinstance(payload, dict):
                    evidence["status"] = payload.get("status")
                results[label] = evidence
    finally:
        engine.dispose()
    if results["ready"].get("status") != "ready":
        raise SafetyError("restored API readiness smoke did not report ready")
    if results["workItems"].get("total") != 2:
        raise SafetyError("restored work-item smoke did not find the deterministic fixture")
    if results["testCases"].get("total") != 3:
        raise SafetyError("restored test-case smoke did not find the deterministic fixture")
    return {
        "inProcess": True,
        "readOnly": True,
        "transactionDefaultReadOnly": True,
        "requests": results,
    }


def _compare_database_fingerprints(
    observed: Mapping[str, object], expected: Mapping[str, object]
) -> dict[str, bool]:
    return {
        "catalogEqual": observed.get("schemaSha256")
        == expected.get("schemaSha256"),
        "catalogVersionEqual": observed.get("catalogFingerprintVersion")
        == expected.get("catalogFingerprintVersion"),
        "catalogCategoryDigestsEqual": observed.get("schemaCategorySha256")
        == expected.get("schemaCategorySha256"),
        "catalogObjectCountsEqual": observed.get("schemaObjectCounts")
        == expected.get("schemaObjectCounts"),
        "dataEqual": observed.get("dataSha256") == expected.get("dataSha256"),
        "tableCountEqual": observed.get("tableCount")
        == expected.get("tableCount"),
        "totalRowsEqual": observed.get("totalRows")
        == expected.get("totalRows"),
        "perTableEqual": observed.get("tables") == expected.get("tables"),
    }


def _run_postgres_restore(
    args: argparse.Namespace, target: Target
) -> dict[str, object]:
    _validate_database_declaration(args, target)
    dump_file = _safe_dump_path(args.dump_file, must_exist=True)
    output = _safe_evidence_output(getattr(args, "output", None))
    (
        backup_evidence_path,
        backup_evidence,
        backup_evidence_file_safety,
    ) = _load_backup_evidence(
        str(args.backup_evidence)
    )
    plan = _base_plan(
        task="PF-SF1-T07",
        action="POSTGRES-RESTORE",
        target=target,
        mutates=["declared empty attested PostgreSQL restore destination"],
        reads=["existing custom-format dump outside the repository"],
        steps=[
            "validate the matched backup evidence, dump SHA-256, and archive inventory",
            "verify exact database identity, comment attestation, and zero public tables",
            "restore without --clean, owner, or ACL",
            f"require restored Alembic revision {HEAD_REVISION}",
            "compare stable catalog structure plus per-table counts and SHA-256 data digests",
            "run read-only in-process health/readiness/workspace/work-item/test-case API smoke",
            "report restore command timing, measured RTO, and recovery-point age",
        ],
    )
    plan["evidenceFile"] = str(output.relative_to(ROOT)) if output else None
    plan["backupEvidenceFile"] = str(backup_evidence_path.relative_to(ROOT))
    plan["rpoBoundary"] = (
        "measured recovery-point timestamp and age only; no production RPO promise"
    )
    if not args.execute:
        return plan
    _require_confirmation(args, str(plan["confirmationRequired"]))
    expected_dump_sha = str(backup_evidence["dumpSha256"])
    evidence_dump_file = _resolve_non_symlink_path(str(backup_evidence["dumpFile"]))
    if evidence_dump_file != dump_file:
        raise SafetyError("restore dump path does not match backup evidence")
    source_preflight = backup_evidence["preflight"]
    source_fingerprint = backup_evidence["sourceFingerprint"]
    recovery_point = backup_evidence["recoveryPoint"]
    if source_preflight["databaseName"] == args.database_name:
        raise SafetyError("restore destination must be distinct from the backup source")
    if backup_evidence["target"] == plan["target"]:
        raise SafetyError("restore target must be distinct from the backup source")
    database = _database_target(args, target)
    client = _resolve_postgresql_client(database)
    with PinnedReadFile(
        dump_file,
        label="database dump",
        require_nonempty=True,
    ) as dump_source:
        if (
            dump_source.identity.evidence()
            != backup_evidence["dumpFileSafety"]["identity"]
        ):
            raise SafetyError("restore dump identity does not match backup evidence")
        actual_dump_sha = dump_source.run_stage(
            "SHA-256", lambda: _pinned_file_sha256(dump_source)
        )
        if actual_dump_sha != expected_dump_sha:
            raise SafetyError("restore dump SHA-256 does not match backup evidence")
        archive = _archive_listing(
            dump_source,
            client=client,
            dump_sha256=actual_dump_sha,
        )
        if archive != backup_evidence["archive"]:
            raise SafetyError("restore archive inventory does not match backup evidence")
        dump_bytes = dump_source.identity.size
        if dump_bytes != backup_evidence["dumpBytes"]:
            raise SafetyError("restore dump size does not match backup evidence")
        preflight = _database_preflight(
            database, target, require_empty=True, client=client
        )
        restore_started_at = datetime.now(UTC)
        rto_started = time.perf_counter()
        restore_started = time.perf_counter()
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
        dump_source.run_stage(
            "pg_restore actual restore",
            lambda: _command_from_pinned_file(
                command,
                dump_source,
                env=environment,
                capture=False,
            ),
        )
        restore_seconds = time.perf_counter() - restore_started
        dump_file_safety = dump_source.safety_evidence()
    postflight = _database_preflight(
        database, target, expected_revision=HEAD_REVISION, client=client
    )
    restored_fingerprint = _capture_database_fingerprint(database, client=client)
    comparison = _compare_database_fingerprints(
        restored_fingerprint, source_fingerprint
    )
    if not all(comparison.values()):
        raise SafetyError("restored catalog or row/data fingerprints differ from source")
    application_smoke = _application_restore_smoke(database)
    after_smoke_fingerprint = _capture_database_fingerprint(database, client=client)
    post_smoke_comparison = _compare_database_fingerprints(
        after_smoke_fingerprint, restored_fingerprint
    )
    if not all(post_smoke_comparison.values()):
        raise SafetyError("read-only application smoke mutated catalog or table data")
    restore_finished_at = datetime.now(UTC)
    result: dict[str, object] = {
        **plan,
        "mode": "executed",
        "executes": True,
        "preflight": preflight,
        "postflight": postflight,
        "client": _postgresql_client_evidence(client),
        "source": {
            "target": backup_evidence.get("target"),
            "preflight": source_preflight,
            "fingerprint": source_fingerprint,
        },
        "backupEvidenceFileSafety": backup_evidence_file_safety,
        "dumpFileSafety": dump_file_safety,
        "archive": archive,
        "dumpBytes": dump_bytes,
        "dumpSha256": actual_dump_sha,
        "restoredFingerprint": restored_fingerprint,
        "comparison": comparison,
        "applicationSmoke": application_smoke,
        "postSmokeFingerprint": after_smoke_fingerprint,
        "postSmokeComparison": post_smoke_comparison,
        "recoveryPoint": {
            **recovery_point,
            "measuredAgeAtRestoredSmokeSeconds": _recovery_point_age_seconds(
                str(recovery_point["capturedAtUtc"])
            ),
            "productionRpoPromise": "NOT_CLAIMED",
        },
        "timing": {
            "startedAtUtc": restore_started_at.isoformat(),
            "finishedAtUtc": restore_finished_at.isoformat(),
            "restoreCommandSeconds": round(restore_seconds, 6),
            "rtoMeasuredThroughReadOnlyApplicationSmokeSeconds": round(
                time.perf_counter() - rto_started, 6
            ),
            "productionRtoPromise": "NOT_CLAIMED",
        },
    }
    _write_json_evidence(output, result)
    return result


def _validate_api_base_url(value: str, target: Target) -> str:
    parsed = urlsplit(value.rstrip("/"))
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise SafetyError("API base URL must be a credential-free origin")
    _reject_production_like(parsed.hostname, "API host")
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if target.target_class == "disposable":
        if not loopback or parsed.scheme != "http":
            raise SafetyError("disposable API targets must use an http loopback origin")
    elif parsed.scheme != "https" or (
        not loopback and not (_tokens(parsed.hostname) & {"stage", "staging", "stg"})
    ):
        raise SafetyError("staging-copy API targets require an HTTPS staging host")
    if parsed.path not in {"", "/"}:
        raise SafetyError("API base URL must not contain a path")
    return value.rstrip("/")


def _validate_bucket(value: str, target: Target) -> str:
    suffix = target.target_class
    if not re.fullmatch(rf"tc-phase-f-(?:[a-z0-9]+-)+{re.escape(suffix)}", value):
        raise SafetyError(
            "object-storage bucket name must be an explicit tc-phase-f target bucket"
        )
    _reject_production_like(value, "object-storage bucket")
    return value


def _http_json(
    method: str,
    url: str,
    *,
    payload: Mapping[str, object] | None,
    token: str | None,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    body = None
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with _open_without_redirect(request) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        raise SafetyError(f"API request failed with HTTP {error.code}") from error
    except urllib.error.URLError as error:
        raise SafetyError("API request failed before receiving a response") from error
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise SafetyError("API response must be a JSON object")
    return value


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep bearer tokens and signed URLs on their explicitly declared origin."""

    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: object,
        code: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> None:
        del request, file_pointer, code, message, headers, new_url
        return None


def _open_without_redirect(
    request: urllib.request.Request,
) -> Any:
    return urllib.request.build_opener(_NoRedirectHandler()).open(request, timeout=30)


def _api_token(args: argparse.Namespace) -> str | None:
    name = args.api_token_env
    if name is None:
        return None
    return _required_environment(str(name))


def _run_object_storage_verify(
    args: argparse.Namespace,
    target: Target,
    *,
    forced_provider: Literal["R2", "MINIO"] | None = None,
) -> dict[str, object]:
    api = _validate_api_base_url(args.api_base_url, target)
    bucket = _validate_bucket(args.bucket, target)
    provider = forced_provider or str(args.provider)
    if provider not in {"R2", "MINIO"}:
        raise SafetyError("object-storage provider must be exactly R2 or MINIO")
    if provider == "MINIO" and target.target_class != "disposable":
        raise SafetyError("MINIO verification is restricted to disposable loopback targets")
    if args.api_token_env is not None and not SAFE_ENV_PATTERN.fullmatch(
        args.api_token_env
    ):
        raise SafetyError("API token environment variable must start with TC_PHASE_F_")
    if not 1 <= args.max_bytes <= 50 * 1024 * 1024:
        raise SafetyError(
            "object-storage restore sample max bytes must be between 1 and 50 MiB"
        )
    action = "R2-VERIFY" if forced_provider == "R2" else "OBJECT-STORAGE-VERIFY"
    plan = _base_plan(
        task="PF-SF1-T08",
        action=action,
        target=target,
        mutates=[],
        reads=[
            "declared API database attachment metadata",
            f"declared private {provider} bucket objects",
        ],
        steps=[
            f"bind API status to exact provider {provider} and the declared bucket",
            "paginate metadata verification with dryRun=true only",
            "download a deterministic bounded sample and verify full-byte SHA-256",
            "fail on empty samples, mismatches, missing objects, or retryable failures",
        ],
    )
    plan["expectedBucket"] = bucket
    if not args.execute:
        return plan
    _require_confirmation(args, str(plan["confirmationRequired"]))
    token = _api_token(args)
    status = _http_json(
        "GET",
        f"{api}/api/integrations/object-storage/status",
        payload=None,
        token=token,
    )
    expected_status = {
        "status": "READY",
        "provider": provider,
        "bucket": bucket,
        "privacy": "PRIVATE",
        "scanPolicy": "FAIL_CLOSED_CLAMAV",
    }
    for field, expected in expected_status.items():
        if status.get(field) != expected:
            raise SafetyError(
                f"object-storage status {field} does not match the declared target"
            )
    totals = {"scanned": 0, "consistent": 0, "mismatch": 0, "failed": 0, "pages": 0}
    cursor: str | None = None
    seen_cursors: set[str] = set()
    while True:
        request_payload: dict[str, object] = {"dryRun": True, "limit": 1000}
        if cursor is not None:
            request_payload["cursor"] = cursor
        page = _http_json(
            "POST",
            f"{api}/api/integrations/object-storage/verify-metadata",
            payload=request_payload,
            token=token,
        )
        if page.get("dryRun") is not True:
            raise SafetyError(
                "metadata verification response did not attest dryRun=true"
            )
        totals["pages"] += 1
        totals["scanned"] += int(page.get("scannedCount", -1))
        totals["consistent"] += int(page.get("consistentCount", -1))
        totals["mismatch"] += int(page.get("mismatchCount", -1))
        totals["failed"] += int(page.get("failedCount", -1))
        next_cursor = page.get("nextCursor")
        if next_cursor is None:
            break
        if (
            not isinstance(next_cursor, str)
            or not next_cursor
            or next_cursor in seen_cursors
        ):
            raise SafetyError("metadata verification returned an invalid cursor")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    if totals["scanned"] < 1:
        raise SafetyError(
            f"{provider} verification requires at least one active attachment"
        )
    if (
        totals["consistent"] != totals["scanned"]
        or totals["mismatch"]
        or totals["failed"]
    ):
        raise SafetyError(
            f"{provider} metadata verification found a mismatch or retryable failure"
        )
    restore = _http_json(
        "POST",
        f"{api}/api/integrations/object-storage/restore-sample",
        payload={"sampleSize": args.sample_size, "maxBytesPerObject": args.max_bytes},
        token=token,
    )
    sampled = int(restore.get("sampledCount", -1))
    verified = int(restore.get("verifiedCount", -1))
    failed = int(restore.get("failedCount", -1))
    if sampled < 1 or failed != 0 or verified != sampled:
        raise SafetyError(
            f"{provider} full-byte restore sample did not verify every sampled object"
        )
    return {
        **plan,
        "mode": "executed",
        "executes": True,
        "provider": provider,
        "status": {key: status.get(key) for key in expected_status},
        "metadata": totals,
        "restoreSample": {"sampled": sampled, "verified": verified, "failed": failed},
    }


def _run_r2_verify(args: argparse.Namespace, target: Target) -> dict[str, object]:
    """Retain the strict real-R2 command and confirmation contract."""

    return _run_object_storage_verify(args, target, forced_provider="R2")


def _run_provider_verify(
    args: argparse.Namespace, target: Target
) -> dict[str, object]:
    return _run_object_storage_verify(args, target)


def _knowledge_environment(database: DatabaseTarget) -> dict[str, str]:
    existing_pythonpath = os.getenv("PYTHONPATH")
    paths = [str(PHASE_F_IMPORT_ROOT)]
    if existing_pythonpath:
        paths.append(existing_pythonpath)
    return {
        **_clean_process_environment(),
        "TC_DATABASE_URL": database.url,
        "PYTHONPATH": os.pathsep.join(paths),
    }


def _knowledge_result_summary(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise SafetyError("knowledge rebuild returned an unexpected JSON value")
    common = {
        "mode": payload.get("mode"),
        "writeScope": payload.get("writeScope"),
        "preservedLedgers": payload.get("preservedLedgers"),
        "eventCount": payload.get("eventCount"),
        "eventOrderDigest": payload.get("eventOrderDigest"),
        "sourceLedgerDigest": payload.get("sourceLedgerDigest"),
        "sensitiveProjectionOmitted": True,
    }
    if payload.get("mode") == "dry-run":
        current = payload.get("currentProjection")
        if not isinstance(current, dict):
            raise SafetyError("knowledge preview is missing currentProjection")
        common["eventStatusCounts"] = payload.get("eventStatusCounts")
        common["currentProjection"] = {
            "schemaVersion": current.get("schemaVersion"),
            "digest": current.get("digest"),
            "counts": current.get("counts"),
        }
        common["auxiliaryIndexIncluded"] = payload.get("auxiliaryIndexIncluded")
        common["transaction"] = payload.get("transaction")
        return common
    if payload.get("mode") == "apply":
        after = payload.get("afterProjection")
        if not isinstance(after, dict):
            raise SafetyError("knowledge apply result is missing afterProjection")
        common["beforeDigest"] = payload.get("beforeDigest")
        common["afterProjection"] = {
            "schemaVersion": after.get("schemaVersion"),
            "digest": after.get("digest"),
            "counts": after.get("counts"),
        }
        return common
    raise SafetyError("knowledge rebuild returned an unexpected mode")


def _validate_knowledge_runtime(target: Target) -> dict[str, str]:
    if os.getenv("TC_AUTOMATIC_KNOWLEDGE_PROJECTION_ENABLED", "").casefold() != "true":
        raise SafetyError(
            "knowledge apply requires TC_AUTOMATIC_KNOWLEDGE_PROJECTION_ENABLED=true"
        )
    ollama_url = os.getenv("TC_OLLAMA_BASE_URL")
    if not ollama_url:
        raise SafetyError("knowledge apply requires an explicit TC_OLLAMA_BASE_URL")
    parsed = urlsplit(ollama_url.rstrip("/"))
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise SafetyError("Ollama URL must be a credential-free HTTP(S) origin")
    _reject_production_like(parsed.hostname, "Ollama host")
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if target.target_class == "disposable" and not loopback:
        raise SafetyError("disposable knowledge rebuild requires loopback Ollama")
    if (
        target.target_class == "staging-copy"
        and not loopback
        and not (_tokens(parsed.hostname) & {"stage", "staging", "stg"})
    ):
        raise SafetyError("remote staging-copy Ollama host must identify staging")
    models = {
        "generationModel": os.getenv("TC_GENERATION_MODEL"),
        "embeddingModel": os.getenv("TC_EMBEDDING_MODEL"),
    }
    if models != {"generationModel": "qwen3:8b", "embeddingModel": "bge-m3"}:
        raise SafetyError("knowledge apply requires qwen3:8b and bge-m3 model pins")
    return {
        "ollamaOrigin": ollama_url.rstrip("/"),
        "generationModel": "qwen3:8b",
        "embeddingModel": "bge-m3",
    }


def _run_knowledge_preview(
    args: argparse.Namespace, target: Target
) -> dict[str, object]:
    _validate_database_declaration(args, target)
    plan = _base_plan(
        task="PF-SF1-T09",
        action="KNOWLEDGE-PREVIEW",
        target=target,
        mutates=[],
        reads=["attested PostgreSQL source ledgers and derived knowledge projection"],
        steps=[
            f"require database revision {HEAD_REVISION}",
            "compute event-order, source-ledger, and current-projection digests",
            "emit a fresh apply plan without acquiring a write lock",
        ],
    )
    if not args.execute:
        return plan
    _require_confirmation(args, str(plan["confirmationRequired"]))
    database = _database_target(args, target)
    preflight = _database_preflight(database, target, expected_revision=HEAD_REVISION)
    result = _command(
        [sys.executable, str(KNOWLEDGE_REBUILD)],
        env=_knowledge_environment(database),
        capture=True,
    )
    return {
        **plan,
        "mode": "executed-read-only",
        "executes": True,
        "preflight": preflight,
        "rebuildPlan": _knowledge_result_summary(json.loads(result.stdout)),
    }


def _run_knowledge_apply(args: argparse.Namespace, target: Target) -> dict[str, object]:
    _validate_database_declaration(args, target)
    for label, value in (
        ("event-order digest", args.event_order_digest),
        ("source-ledger digest", args.source_ledger_digest),
    ):
        if not HEX_DIGEST_PATTERN.fullmatch(str(value)):
            raise SafetyError(f"{label} must be a lowercase 64-character SHA-256")
    plan = _base_plan(
        task="PF-SF1-T09",
        action="KNOWLEDGE-APPLY",
        target=target,
        mutates=["six derived PostgreSQL knowledge projection tables"],
        reads=[
            "authoritative source ledgers",
            "configured Ollama extraction and embedding",
        ],
        steps=[
            "require an attested head-revision database",
            "require fresh event-order and source-ledger digests from preview",
            "use the same opt-in worker consumer composition as incremental processing",
            "atomically clear/replay only derived projection tables under advisory lock",
            "fail if authoritative ledgers change",
        ],
    )
    if not args.execute:
        return plan
    _require_confirmation(args, str(plan["confirmationRequired"]))
    runtime = _validate_knowledge_runtime(target)
    database = _database_target(args, target)
    preflight = _database_preflight(database, target, expected_revision=HEAD_REVISION)
    result = _command(
        [
            sys.executable,
            str(KNOWLEDGE_REBUILD),
            "--apply",
            "--confirm",
            KNOWLEDGE_CONFIRMATION,
            "--expected-event-order-digest",
            str(args.event_order_digest),
            "--expected-source-ledger-digest",
            str(args.source_ledger_digest),
            "--consumer-factory",
            KNOWLEDGE_FACTORY,
        ],
        env=_knowledge_environment(database),
        capture=True,
    )
    return {
        **plan,
        "mode": "executed",
        "executes": True,
        "preflight": preflight,
        "runtime": runtime,
        "rebuild": _knowledge_result_summary(json.loads(result.stdout)),
    }


def _run_lightrag_fallback(
    args: argparse.Namespace, target: Target
) -> dict[str, object]:
    if target.target_class != "disposable":
        raise SafetyError(
            "LightRAG process failover rehearsal requires a disposable target"
        )
    plan = _base_plan(
        task="PF-SF1-T09",
        action="LIGHTRAG-FALLBACK",
        target=target,
        mutates=["fixed labeled tc-lightrag-failover-rehearsal Docker resources"],
        reads=[],
        steps=[
            "delegate to the existing fixed-name collision-checked rehearsal",
            "create isolated PostgreSQL authoritative and AGE+pgvector auxiliary containers",
            "kill/restart only the labeled auxiliary container",
            "verify deterministic fallback/recovery and guarded cleanup",
        ],
    )
    if not args.execute:
        return plan
    _require_confirmation(args, str(plan["confirmationRequired"]))
    result = _command(
        [
            sys.executable,
            str(LIGHTRAG_FAILOVER),
            "--run",
            "--confirm",
            LIGHTRAG_CONFIRMATION,
        ],
        capture=True,
    )
    return {
        **plan,
        "mode": "executed",
        "executes": True,
        "fallback": json.loads(result.stdout),
    }


def _normalized_dump_digest(raw: str) -> str:
    lines = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        if stripped.startswith(("\\restrict ", "\\unrestrict ")):
            continue
        lines.append(line.rstrip())
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _alembic(database: DatabaseTarget, *arguments: str) -> None:
    _command(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_CONFIG), *arguments],
        env={**_clean_process_environment(), "TC_DATABASE_URL": database.url},
    )


def _run_migration_roundtrip(
    args: argparse.Namespace, target: Target
) -> dict[str, object]:
    _validate_database_declaration(args, target)
    plan = _base_plan(
        task="PF-SF1-T10",
        action="MIGRATION-ROUNDTRIP",
        target=target,
        mutates=["declared attested disposable/staging-copy PostgreSQL database"],
        reads=["complete schema and data dumps for before/after digests"],
        steps=[
            f"require current revision {HEAD_REVISION}",
            "capture normalized schema/data digests",
            f"downgrade exactly one revision to {ROLLBACK_REVISION}",
            f"roll forward to {HEAD_REVISION}",
            "require exact normalized schema/data digest equality",
        ],
    )
    if not args.execute:
        return plan
    _require_confirmation(args, str(plan["confirmationRequired"]))
    database = _database_target(args, target)
    preflight = _database_preflight(database, target, expected_revision=HEAD_REVISION)
    before_schema = _normalized_dump_digest(_plain_dump(database, schema_only=True))
    before_data = _normalized_dump_digest(_plain_dump(database, schema_only=False))
    _alembic(database, "downgrade", ROLLBACK_REVISION)
    if _current_revision(database) != ROLLBACK_REVISION:
        raise SafetyError(
            "migration downgrade did not reach the declared rollback revision"
        )
    _alembic(database, "upgrade", HEAD_REVISION)
    postflight = _database_preflight(database, target, expected_revision=HEAD_REVISION)
    after_schema = _normalized_dump_digest(_plain_dump(database, schema_only=True))
    after_data = _normalized_dump_digest(_plain_dump(database, schema_only=False))
    if before_schema != after_schema or before_data != after_data:
        raise SafetyError("migration round-trip changed normalized schema or data")
    return {
        **plan,
        "mode": "executed",
        "executes": True,
        "preflight": preflight,
        "postflight": postflight,
        "rollbackRevision": ROLLBACK_REVISION,
        "rollForwardRevision": HEAD_REVISION,
        "schemaDigest": after_schema,
        "dataDigest": after_data,
        "exactRoundTrip": True,
    }


def _origin(value: str, *, staging_required: bool) -> str:
    parsed = urlsplit(value)
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise SafetyError("Web origin must be a credential-free origin without a path")
    if parsed.scheme != "https":
        raise SafetyError("deployed Web origin must use HTTPS")
    _reject_production_like(parsed.hostname, "Web origin host")
    if staging_required and not (
        _tokens(parsed.hostname) & {"stage", "staging", "stg"}
    ):
        raise SafetyError("staging Web origin host must contain stage/staging/stg")
    return value.rstrip("/")


def _cors_rule(policy: object) -> Mapping[str, object]:
    if isinstance(policy, dict) and isinstance(policy.get("rules"), list):
        rules = policy["rules"]
        if len(rules) != 1 or not isinstance(rules[0], dict):
            raise SafetyError("Cloudflare CORS policy must contain exactly one rule")
        rule = rules[0]
        allowed = rule.get("allowed")
        if not isinstance(allowed, dict):
            raise SafetyError("Cloudflare CORS rule is missing allowed")
        return {
            "origins": allowed.get("origins"),
            "methods": allowed.get("methods"),
            "headers": allowed.get("headers"),
            "exposeHeaders": rule.get("exposeHeaders"),
            "maxAgeSeconds": rule.get("maxAgeSeconds"),
        }
    if isinstance(policy, list) and len(policy) == 1 and isinstance(policy[0], dict):
        rule = policy[0]
        return {
            "origins": rule.get("AllowedOrigins"),
            "methods": rule.get("AllowedMethods"),
            "headers": rule.get("AllowedHeaders"),
            "exposeHeaders": rule.get("ExposeHeaders"),
            "maxAgeSeconds": rule.get("MaxAgeSeconds"),
        }
    raise SafetyError("CORS policy must contain exactly one Cloudflare or S3 rule")


def _string_set(value: object, label: str, *, upper: bool = False) -> set[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SafetyError(f"CORS {label} must be a string array")
    return {item.upper() if upper else item.casefold() for item in value}


def _run_cors_policy(args: argparse.Namespace, target: Target) -> dict[str, object]:
    if target.target_class != "staging-copy":
        raise SafetyError(
            "deployed HTTPS CORS preparation requires a staging-copy target"
        )
    origin = _origin(args.web_origin, staging_required=True)
    policy_path = Path(args.policy_file).expanduser().resolve()
    if not policy_path.is_file():
        raise SafetyError("CORS policy file does not exist")
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    rule = _cors_rule(policy)
    origins = _string_set(rule["origins"], "origins")
    methods = _string_set(rule["methods"], "methods", upper=True)
    headers = _string_set(rule["headers"], "headers")
    exposed = _string_set(rule["exposeHeaders"], "expose headers")
    if origins != {origin.casefold()}:
        raise SafetyError("CORS policy must allow only the exact declared HTTPS origin")
    if methods != {"PUT", "GET", "HEAD"}:
        raise SafetyError("CORS methods must be exactly PUT, GET, and HEAD")
    if headers != {"content-type", "x-amz-meta-sha256"}:
        raise SafetyError(
            "CORS request headers must be exactly the approved signed headers"
        )
    if exposed != {"etag", "content-length", "content-type", "x-amz-meta-sha256"}:
        raise SafetyError("CORS exposed headers do not match the approved policy")
    if rule["maxAgeSeconds"] != 300:
        raise SafetyError("CORS MaxAgeSeconds must be exactly 300")
    return {
        "ok": True,
        "mode": "validated-local-policy",
        "task": "PF-SF1-T15",
        "action": "CORS-POLICY",
        "target": {"name": target.name, "class": target.target_class},
        "origin": origin,
        "policyFile": str(policy_path),
        "mutates": [],
        "physicalBrowserEvidence": "NOT_RUN",
    }


def _run_cors_preflight(args: argparse.Namespace, target: Target) -> dict[str, object]:
    if target.target_class != "staging-copy":
        raise SafetyError(
            "deployed HTTPS CORS preflight requires a staging-copy target"
        )
    origin = _origin(args.web_origin, staging_required=True)
    if not SAFE_ENV_PATTERN.fullmatch(args.presigned_url_env):
        raise SafetyError(
            "presigned URL environment variable must start with TC_PHASE_F_"
        )
    plan = _base_plan(
        task="PF-SF1-T15",
        action="CORS-PREFLIGHT",
        target=target,
        mutates=[],
        reads=["presigned R2 URL from a secret environment variable"],
        steps=[
            "send credential-free OPTIONS requests for PUT, GET, and HEAD",
            "require exact allow-origin, methods, and signed request headers",
            "never print or persist the presigned URL",
        ],
    )
    plan["origin"] = origin
    plan["physicalBrowserEvidence"] = "NOT_RUN"
    if not args.execute:
        return plan
    _require_confirmation(args, str(plan["confirmationRequired"]))
    presigned_url = _required_environment(args.presigned_url_env)
    parsed = urlsplit(presigned_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".r2.cloudflarestorage.com")
        or parsed.username
        or parsed.password
        or not parsed.query
    ):
        raise SafetyError(
            "presigned R2 URL must be credential-free HTTPS authority plus query"
        )
    outcomes: dict[str, dict[str, object]] = {}
    for method in ("PUT", "GET", "HEAD"):
        request_headers = {
            "Origin": origin,
            "Access-Control-Request-Method": method,
        }
        if method == "PUT":
            request_headers["Access-Control-Request-Headers"] = (
                "content-type,x-amz-meta-sha256"
            )
        request = urllib.request.Request(
            presigned_url,
            headers=request_headers,
            method="OPTIONS",
        )
        try:
            with _open_without_redirect(request) as response:
                headers = response.headers
                status = response.status
        except urllib.error.HTTPError as error:
            raise SafetyError(
                f"R2 {method} preflight failed with HTTP {error.code}"
            ) from error
        except urllib.error.URLError as error:
            raise SafetyError(
                f"R2 {method} preflight did not receive a response"
            ) from error
        allow_origin = headers.get("Access-Control-Allow-Origin")
        allow_methods = {
            item.strip().upper()
            for item in (headers.get("Access-Control-Allow-Methods") or "").split(",")
            if item.strip()
        }
        allow_headers = {
            item.strip().casefold()
            for item in (headers.get("Access-Control-Allow-Headers") or "").split(",")
            if item.strip()
        }
        if allow_origin != origin or method not in allow_methods:
            raise SafetyError(
                f"R2 {method} preflight did not allow the exact origin/method"
            )
        if method == "PUT" and not {
            "content-type",
            "x-amz-meta-sha256",
        }.issubset(allow_headers):
            raise SafetyError(
                "R2 PUT preflight did not allow both signed request headers"
            )
        outcomes[method] = {"httpStatus": status, "allowed": True}
    return {
        **plan,
        "mode": "executed-read-only",
        "executes": True,
        "preflights": outcomes,
        "physicalBrowserEvidence": "NOT_RUN",
    }


def _add_target(parser: argparse.ArgumentParser, *, database: bool = False) -> None:
    parser.add_argument("--target-name", required=True)
    parser.add_argument("--target-class", required=True, choices=TARGET_CLASSES)
    if database:
        parser.add_argument("--database-url-env", required=True)
        parser.add_argument("--database-name", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preview or execute guarded Phase F recovery/performance rehearsals."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    benchmark = commands.add_parser("benchmark-cases")
    _add_target(benchmark, database=True)
    benchmark.add_argument("--warmups", type=int, default=3)
    benchmark.add_argument("--runs", type=int, default=30)
    benchmark.add_argument("--target-ms", type=float, default=1000.0)
    benchmark.add_argument("--output")

    seed = commands.add_parser("postgres-seed")
    _add_target(seed, database=True)
    seed.add_argument("--output", required=True)

    backup = commands.add_parser("postgres-backup")
    _add_target(backup, database=True)
    backup.add_argument("--dump-file", required=True)
    backup.add_argument("--output", required=True)

    restore = commands.add_parser("postgres-restore")
    _add_target(restore, database=True)
    restore.add_argument("--dump-file", required=True)
    restore.add_argument("--backup-evidence", required=True)
    restore.add_argument("--output", required=True)

    r2 = commands.add_parser("r2-verify")
    _add_target(r2)
    r2.add_argument("--api-base-url", required=True)
    r2.add_argument("--bucket", required=True)
    r2.add_argument("--api-token-env")
    r2.add_argument("--sample-size", type=int, default=100, choices=range(1, 101))
    r2.add_argument("--max-bytes", type=int, default=50 * 1024 * 1024)

    object_storage = commands.add_parser("object-storage-verify")
    _add_target(object_storage)
    object_storage.add_argument("--provider", required=True, choices=("R2", "MINIO"))
    object_storage.add_argument("--api-base-url", required=True)
    object_storage.add_argument("--bucket", required=True)
    object_storage.add_argument("--api-token-env")
    object_storage.add_argument(
        "--sample-size", type=int, default=100, choices=range(1, 101)
    )
    object_storage.add_argument(
        "--max-bytes", type=int, default=50 * 1024 * 1024
    )

    preview = commands.add_parser("knowledge-preview")
    _add_target(preview, database=True)

    apply = commands.add_parser("knowledge-apply")
    _add_target(apply, database=True)
    apply.add_argument("--event-order-digest", required=True)
    apply.add_argument("--source-ledger-digest", required=True)

    fallback = commands.add_parser("lightrag-fallback")
    _add_target(fallback)

    migration = commands.add_parser("migration-roundtrip")
    _add_target(migration, database=True)

    cors_policy = commands.add_parser("cors-policy")
    cors_policy.add_argument("--target-name", required=True)
    cors_policy.add_argument("--target-class", required=True, choices=TARGET_CLASSES)
    cors_policy.add_argument("--web-origin", required=True)
    cors_policy.add_argument("--policy-file", required=True)

    cors_preflight = commands.add_parser("cors-preflight")
    _add_target(cors_preflight)
    cors_preflight.add_argument("--web-origin", required=True)
    cors_preflight.add_argument("--presigned-url-env", required=True)
    return parser


def _dispatch(args: argparse.Namespace, target: Target) -> dict[str, object]:
    handlers = {
        "benchmark-cases": _run_benchmark,
        "postgres-seed": _run_postgres_seed,
        "postgres-backup": _run_postgres_backup,
        "postgres-restore": _run_postgres_restore,
        "r2-verify": _run_r2_verify,
        "object-storage-verify": _run_provider_verify,
        "knowledge-preview": _run_knowledge_preview,
        "knowledge-apply": _run_knowledge_apply,
        "lightrag-fallback": _run_lightrag_fallback,
        "migration-roundtrip": _run_migration_roundtrip,
        "cors-policy": _run_cors_policy,
        "cors-preflight": _run_cors_preflight,
    }
    return handlers[str(args.command)](args, target)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        result = _dispatch(args, _target_from_args(args))
    except (
        SafetyError,
        json.JSONDecodeError,
        psycopg.Error,
        subprocess.CalledProcessError,
    ) as error:
        if isinstance(error, subprocess.CalledProcessError):
            detail = f"subprocess exited with status {error.returncode}"
        elif isinstance(error, psycopg.Error):
            detail = "PostgreSQL operation failed"
        elif isinstance(error, json.JSONDecodeError):
            detail = "received invalid JSON"
        else:
            detail = str(error)
        print(
            json.dumps({"ok": False, "error": detail}, sort_keys=True), file=sys.stderr
        )
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
