from __future__ import annotations

import json
import subprocess

import pytest

from scripts import rehearse_lightrag_failover as rehearsal


def test_preview_is_non_mutating_and_uses_exact_scoped_resources(capsys: pytest.CaptureFixture[str]) -> None:
    assert rehearsal.main([]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["mode"] == "preview"
    assert payload["mutatesDocker"] is False
    assert payload["project"] == "tc-lightrag-failover-rehearsal"
    assert payload["resources"] == {
        "containers": [
            "tc-lightrag-failover-authoritative-postgres",
            "tc-lightrag-failover-aux-postgres",
        ],
        "volumes": [
            "tc-lightrag-failover-authoritative-data",
            "tc-lightrag-failover-aux-data",
        ],
        "network": "tc-lightrag-failover-network",
    }
    assert "test" in payload["databases"]["authoritative"]["name"]
    assert "test" in payload["databases"]["lightragAuxiliary"]["name"]


def test_mutating_actions_require_the_exact_confirmation() -> None:
    with pytest.raises(SystemExit) as missing:
        rehearsal.main(["--run"])
    assert missing.value.code == 2

    with pytest.raises(SystemExit) as wrong:
        rehearsal.main(["--cleanup", "--confirm", "yes"])
    assert wrong.value.code == 2


def test_plan_rejects_shared_or_invalid_ports() -> None:
    with pytest.raises(ValueError, match="must differ"):
        rehearsal.RehearsalPlan(authoritative_port=55438, auxiliary_port=55438)
    with pytest.raises(ValueError, match="between 1 and 65535"):
        rehearsal.RehearsalPlan(authoritative_port=0)


def test_cleanup_refuses_an_exact_name_without_both_ownership_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unowned = rehearsal.DockerResource(
        kind="container",
        name=rehearsal.AUXILIARY_CONTAINER,
        project_label="someone-else",
        rehearsal_label=None,
    )

    def inspect(kind: str, name: str) -> rehearsal.DockerResource | None:
        if kind == "container" and name == rehearsal.AUXILIARY_CONTAINER:
            return unowned
        return None

    monkeypatch.setattr(rehearsal, "_inspect_resource", inspect)
    with pytest.raises(rehearsal.RehearsalError, match="refusing to touch"):
        rehearsal._cleanup(rehearsal.RehearsalPlan(), {})


def test_collision_check_never_reuses_a_previous_rehearsal_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owned = rehearsal.DockerResource(
        kind="volume",
        name=rehearsal.AUXILIARY_VOLUME,
        project_label=rehearsal.PROJECT,
        rehearsal_label=rehearsal.PROJECT,
    )
    monkeypatch.setattr(
        rehearsal,
        "_inspect_resource",
        lambda kind, name: owned if name == rehearsal.AUXILIARY_VOLUME else None,
    )

    with pytest.raises(rehearsal.RehearsalError, match="already exist"):
        rehearsal._assert_no_resource_collisions()


def test_successful_cleanup_uses_only_the_fixed_compose_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"exists": True}
    calls: list[tuple[str, ...]] = []

    def inspect(kind: str, name: str) -> rehearsal.DockerResource | None:
        if not state["exists"] or name != rehearsal.AUXILIARY_CONTAINER:
            return None
        return rehearsal.DockerResource(
            kind=kind,
            name=name,
            project_label=rehearsal.PROJECT,
            rehearsal_label=rehearsal.PROJECT,
        )

    def command(
        args: tuple[str, ...],
        *,
        env: dict[str, str] | None = None,
        capture: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        del env, capture, check
        calls.append(tuple(args))
        state["exists"] = False
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(rehearsal, "_inspect_resource", inspect)
    monkeypatch.setattr(rehearsal, "_command", command)

    assert rehearsal._cleanup(rehearsal.RehearsalPlan(), {}) is True
    assert calls == [
        (
            "docker",
            "compose",
            "--project-name",
            rehearsal.PROJECT,
            "--file",
            str(rehearsal.COMPOSE_FILE),
            "down",
            "--volumes",
            "--timeout",
            "5",
        )
    ]


def test_pytest_environment_keeps_databases_test_only_and_separate() -> None:
    plan = rehearsal.RehearsalPlan(authoritative_port=60001, auxiliary_port=60002)
    compose_environment = rehearsal._compose_environment(
        plan,
        authoritative_password="authoritative password",
        auxiliary_password="auxiliary password",
    )

    environment = rehearsal._pytest_environment(plan, compose_environment)

    assert "tc_lightrag_authoritative_test" in environment["TC_TEST_DATABASE_URL"]
    assert "authoritative%20password" in environment["TC_TEST_DATABASE_URL"]
    assert environment["POSTGRES_DATABASE"] == "tc_lightrag_auxiliary_test"
    assert environment["POSTGRES_PORT"] == "60002"
    assert environment["TC_LIGHTRAG_FAILOVER_LIVE"] == "1"
    assert environment["POSTGRES_CONNECTION_RETRIES"] == "3"
