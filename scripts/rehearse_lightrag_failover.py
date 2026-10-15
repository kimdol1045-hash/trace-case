"""Safely rehearse severing LightRAG's disposable PostgreSQL/AGE process.

Preview is the default and does not mutate Docker. ``--run`` and ``--cleanup``
require an exact confirmation phrase and operate only on fixed, labeled
resources owned by this rehearsal project.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "deploy/knowledge/compose.failover.yml"
LIVE_TEST = ROOT / "backend/tests/postgres/test_lightrag_failover_live.py"

PROJECT = "tc-lightrag-failover-rehearsal"
CONFIRMATION = "RUN TC LIGHTRAG FAILOVER REHEARSAL"
AUTHORITATIVE_CONTAINER = "tc-lightrag-failover-authoritative-postgres"
AUXILIARY_CONTAINER = "tc-lightrag-failover-aux-postgres"
AUTHORITATIVE_VOLUME = "tc-lightrag-failover-authoritative-data"
AUXILIARY_VOLUME = "tc-lightrag-failover-aux-data"
NETWORK = "tc-lightrag-failover-network"
AUTHORITATIVE_DATABASE = "tc_lightrag_authoritative_test"
AUXILIARY_DATABASE = "tc_lightrag_auxiliary_test"
AUTHORITATIVE_USER = "tc_rehearsal"
AUXILIARY_USER = "tc_lightrag_rehearsal"
REHEARSAL_LABEL = "com.tc.rehearsal"


class RehearsalError(RuntimeError):
    """The rehearsal cannot proceed without violating its safety boundary."""


@dataclass(frozen=True, slots=True)
class RehearsalPlan:
    authoritative_port: int = 55438
    auxiliary_port: int = 55439

    def __post_init__(self) -> None:
        for label, port in (
            ("authoritative", self.authoritative_port),
            ("auxiliary", self.auxiliary_port),
        ):
            if isinstance(port, bool) or not 1 <= port <= 65_535:
                raise ValueError(f"{label} port must be between 1 and 65535")
        if self.authoritative_port == self.auxiliary_port:
            raise ValueError("authoritative and auxiliary ports must differ")
        _require_test_database(AUTHORITATIVE_DATABASE)
        _require_test_database(AUXILIARY_DATABASE)

    def preview(self) -> dict[str, object]:
        return {
            "mode": "preview",
            "mutatesDocker": False,
            "confirmationRequired": CONFIRMATION,
            "project": PROJECT,
            "composeFile": str(COMPOSE_FILE.relative_to(ROOT)),
            "resources": {
                "containers": [AUTHORITATIVE_CONTAINER, AUXILIARY_CONTAINER],
                "volumes": [AUTHORITATIVE_VOLUME, AUXILIARY_VOLUME],
                "network": NETWORK,
            },
            "databases": {
                "authoritative": {
                    "name": AUTHORITATIVE_DATABASE,
                    "port": self.authoritative_port,
                    "extensions": ["vector"],
                },
                "lightragAuxiliary": {
                    "name": AUXILIARY_DATABASE,
                    "port": self.auxiliary_port,
                    "extensions": ["age", "vector"],
                },
            },
            "sequence": [
                "reject colliding named resources and occupied loopback ports",
                "build and start the two disposable PostgreSQL containers",
                "run only backend/tests/postgres/test_lightrag_failover_live.py",
                "sever and restart only the labeled LightRAG auxiliary container",
                "verify the authoritative database stays online throughout",
                "remove only this compose project's containers, volumes, and network",
            ],
        }


@dataclass(frozen=True, slots=True)
class DockerResource:
    kind: str
    name: str
    project_label: str | None
    rehearsal_label: str | None


def _require_test_database(database: str) -> None:
    if "test" not in database.casefold():
        raise RehearsalError("rehearsal database names must contain 'test'")


def _command(
    args: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=ROOT,
        env=dict(env) if env is not None else None,
        capture_output=capture,
        text=True,
        check=check,
    )


def _compose_command(*args: str) -> tuple[str, ...]:
    return (
        "docker",
        "compose",
        "--project-name",
        PROJECT,
        "--file",
        str(COMPOSE_FILE),
        *args,
    )


def _compose_environment(
    plan: RehearsalPlan,
    *,
    authoritative_password: str,
    auxiliary_password: str,
) -> dict[str, str]:
    return {
        **os.environ,
        "TC_LIGHTRAG_REHEARSAL_AUTHORITATIVE_PASSWORD": authoritative_password,
        "TC_LIGHTRAG_REHEARSAL_AUXILIARY_PASSWORD": auxiliary_password,
        "TC_LIGHTRAG_REHEARSAL_AUTHORITATIVE_PORT": str(plan.authoritative_port),
        "TC_LIGHTRAG_REHEARSAL_AUXILIARY_PORT": str(plan.auxiliary_port),
    }


def _inspect_resource(kind: str, name: str) -> DockerResource | None:
    templates = {
        "container": (
            '{{ index .Config.Labels "com.docker.compose.project" }}|'
            '{{ index .Config.Labels "com.tc.rehearsal" }}'
        ),
        "volume": (
            '{{ index .Labels "com.docker.compose.project" }}|'
            '{{ index .Labels "com.tc.rehearsal" }}'
        ),
        "network": (
            '{{ index .Labels "com.docker.compose.project" }}|'
            '{{ index .Labels "com.tc.rehearsal" }}'
        ),
    }
    result = _command(
        ["docker", "inspect", "--type", kind, "--format", templates[kind], name],
        capture=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    project_label, separator, rehearsal_label = result.stdout.strip().partition("|")
    if not separator:
        raise RehearsalError(f"could not parse Docker labels for {kind} {name}")
    return DockerResource(
        kind=kind,
        name=name,
        project_label=project_label or None,
        rehearsal_label=rehearsal_label or None,
    )


def _resources() -> tuple[tuple[str, str], ...]:
    return (
        ("container", AUTHORITATIVE_CONTAINER),
        ("container", AUXILIARY_CONTAINER),
        ("volume", AUTHORITATIVE_VOLUME),
        ("volume", AUXILIARY_VOLUME),
        ("network", NETWORK),
    )


def _validate_owned(resource: DockerResource) -> None:
    if resource.project_label != PROJECT or resource.rehearsal_label != PROJECT:
        raise RehearsalError(
            f"refusing to touch {resource.kind} {resource.name}: expected labels "
            f"com.docker.compose.project={PROJECT} and {REHEARSAL_LABEL}={PROJECT}"
        )


def _assert_no_resource_collisions() -> None:
    existing = [resource for kind, name in _resources() if (resource := _inspect_resource(kind, name))]
    if existing:
        names = ", ".join(f"{item.kind}:{item.name}" for item in existing)
        raise RehearsalError(
            f"named rehearsal resources already exist ({names}); inspect them and run guarded "
            "--cleanup before starting a new rehearsal"
        )


def _assert_ports_available(plan: RehearsalPlan) -> None:
    for port in (plan.authoritative_port, plan.auxiliary_port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.25)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise RehearsalError(f"loopback port {port} is already occupied")


def _check_docker() -> None:
    _command(["docker", "version"], capture=True)
    _command(["docker", "compose", "version"], capture=True)


def _cleanup(plan: RehearsalPlan, environment: Mapping[str, str]) -> bool:
    existing = [resource for kind, name in _resources() if (resource := _inspect_resource(kind, name))]
    if not existing:
        return False
    for resource in existing:
        _validate_owned(resource)
    _command(
        _compose_command("down", "--volumes", "--timeout", "5"),
        env=environment,
    )
    remaining = [name for kind, name in _resources() if _inspect_resource(kind, name)]
    if remaining:
        raise RehearsalError("guarded cleanup left scoped resources: " + ", ".join(remaining))
    return True


def _pytest_environment(
    plan: RehearsalPlan,
    compose_environment: Mapping[str, str],
) -> dict[str, str]:
    authoritative_password = compose_environment[
        "TC_LIGHTRAG_REHEARSAL_AUTHORITATIVE_PASSWORD"
    ]
    auxiliary_password = compose_environment["TC_LIGHTRAG_REHEARSAL_AUXILIARY_PASSWORD"]
    authoritative_url = (
        "postgresql+psycopg://"
        f"{AUTHORITATIVE_USER}:{quote(authoritative_password, safe='')}"
        f"@127.0.0.1:{plan.authoritative_port}/{AUTHORITATIVE_DATABASE}"
    )
    return {
        **compose_environment,
        "TC_LIGHTRAG_FAILOVER_LIVE": "1",
        "TC_LIGHTRAG_FAILOVER_PROJECT": PROJECT,
        "TC_LIGHTRAG_FAILOVER_AUTHORITATIVE_CONTAINER": AUTHORITATIVE_CONTAINER,
        "TC_LIGHTRAG_FAILOVER_AUXILIARY_CONTAINER": AUXILIARY_CONTAINER,
        "TC_TEST_DATABASE_URL": authoritative_url,
        "POSTGRES_HOST": "127.0.0.1",
        "POSTGRES_PORT": str(plan.auxiliary_port),
        "POSTGRES_USER": AUXILIARY_USER,
        "POSTGRES_PASSWORD": auxiliary_password,
        "POSTGRES_DATABASE": AUXILIARY_DATABASE,
        "POSTGRES_MAX_CONNECTIONS": "8",
        "POSTGRES_WORKSPACE": "tc_lightrag_failover_test",
        "POSTGRES_SSL_MODE": "disable",
        # Bound the official asyncpg backend's own reconnect loop inside the
        # adapter deadline; the live test separately proves recovery.
        "POSTGRES_CONNECTION_RETRIES": "3",
        "POSTGRES_CONNECTION_RETRY_BACKOFF": "0.1",
        "POSTGRES_CONNECTION_RETRY_BACKOFF_MAX": "0.2",
        "POSTGRES_POOL_CLOSE_TIMEOUT": "0.5",
        "PYTHONUNBUFFERED": "1",
    }


def _run_rehearsal(plan: RehearsalPlan) -> dict[str, object]:
    _check_docker()
    _assert_no_resource_collisions()
    _assert_ports_available(plan)
    authoritative_password = secrets.token_urlsafe(24)
    auxiliary_password = secrets.token_urlsafe(24)
    environment = _compose_environment(
        plan,
        authoritative_password=authoritative_password,
        auxiliary_password=auxiliary_password,
    )
    started = False
    compose_attempted = False
    test_passed = False
    cleanup_performed = False
    try:
        compose_attempted = True
        _command(
            _compose_command("up", "--build", "--detach", "--wait", "--wait-timeout", "240"),
            env=environment,
        )
        started = True
        for kind, name in _resources():
            resource = _inspect_resource(kind, name)
            if resource is None:
                raise RehearsalError(f"expected scoped Docker {kind} was not created: {name}")
            _validate_owned(resource)
        result = _command(
            [sys.executable, "-m", "pytest", str(LIVE_TEST.relative_to(ROOT)), "-q", "-s"],
            env=_pytest_environment(plan, environment),
            check=False,
        )
        test_passed = result.returncode == 0
        if not test_passed:
            raise RehearsalError(f"live failover pytest exited with status {result.returncode}")
    except BaseException:
        if compose_attempted and not started:
            _command(
                _compose_command("logs", "--no-color", "--tail", "120"),
                env=environment,
                check=False,
            )
        raise
    finally:
        cleanup_performed = _cleanup(plan, environment)
    return {
        "mode": "run",
        "project": PROJECT,
        "composeStarted": started,
        "liveTestPassed": test_passed,
        "cleanupPerformed": cleanup_performed,
        "remainingScopedResources": [],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preview, run, or clean up the disposable LightRAG failover rehearsal."
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--run", action="store_true", help="execute the live Docker rehearsal")
    action.add_argument(
        "--cleanup",
        action="store_true",
        help="remove only labeled resources owned by this rehearsal project",
    )
    parser.add_argument("--confirm", help=f"mutation guard; must exactly equal: {CONFIRMATION}")
    parser.add_argument("--authoritative-port", type=int, default=55438)
    parser.add_argument("--auxiliary-port", type=int, default=55439)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    plan = RehearsalPlan(
        authoritative_port=args.authoritative_port,
        auxiliary_port=args.auxiliary_port,
    )
    mutating = args.run or args.cleanup
    if mutating and args.confirm != CONFIRMATION:
        parser.error(f"--run/--cleanup requires --confirm '{CONFIRMATION}'")
    if not mutating and args.confirm:
        parser.error("--confirm is valid only with --run or --cleanup")

    try:
        if not mutating:
            report = plan.preview()
        elif args.cleanup:
            _check_docker()
            environment = _compose_environment(
                plan,
                authoritative_password=secrets.token_urlsafe(24),
                auxiliary_password=secrets.token_urlsafe(24),
            )
            report = {
                "mode": "cleanup",
                "project": PROJECT,
                "cleanupPerformed": _cleanup(plan, environment),
            }
        else:
            report = _run_rehearsal(plan)
    except (RehearsalError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, **report}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
