from __future__ import annotations

import importlib.util
import json
import sys
from argparse import Namespace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[1] / "phase-f" / "load_profile.py"
COMPOSE = Path(__file__).resolve().parents[2] / "deploy" / "compose.dev.yml"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("phase_f_load_profile", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


load_profile = _load()


def _args(**overrides: object) -> Namespace:
    values: dict[str, object] = {
        "target_name": "tc-phase-f-load-disposable",
        "target_class": "disposable",
        "database_url_env": "TC_PHASE_F_LOAD_DATABASE_URL",
        "database_name": "tc_phase_f_load_test_disposable",
        "concurrency": 8,
        "duration_seconds": 30.0,
        "queue_sample_interval": 0.5,
        "output": Path("docs/phase-f/evidence/pf-sf1-t06-test.json"),
        "execute": False,
        "confirm": None,
    }
    return Namespace(**(values | overrides))


def _operation_summary(
    *,
    count: int = 1,
    errors: int = 0,
    p95_ms: float = 1.0,
    throughput: float = 20.0,
) -> dict[str, Any]:
    if count == 0:
        return {
            "count": 0,
            "successes": 0,
            "errors": 0,
            "p50Ms": 0.0,
            "p95Ms": 0.0,
            "p99Ms": 0.0,
            "errorRate": 0.0,
            "throughputPerSecond": 0.0,
        }
    successes = count - errors
    return {
        "count": count,
        "successes": successes,
        "errors": errors,
        "p50Ms": min(1.0, p95_ms),
        "p95Ms": p95_ms,
        "p99Ms": p95_ms,
        "errorRate": round(errors / count, 6),
        "throughputPerSecond": throughput,
    }


def _phase_metrics(
    surface_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    counts = {surface: 1 for surface in load_profile.REQUIRED_SURFACES}
    counts.update(surface_counts or {})
    surfaces = {
        surface: _operation_summary(count=counts[surface])
        for surface in load_profile.REQUIRED_SURFACES
    }
    total = sum(counts.values())
    return {
        "overall": _operation_summary(count=total),
        "surfaces": surfaces,
    }


def _lag_metrics(
    *,
    samples: int = 1,
    p95_ms: float = 1.0,
    p99_ms: float | None = None,
) -> dict[str, Any]:
    resolved_p99 = p95_ms if p99_ms is None else p99_ms
    if samples == 0:
        p95_ms = 0.0
        resolved_p99 = 0.0
    return {
        "samples": samples,
        "p50Ms": min(1.0, p95_ms),
        "p95Ms": p95_ms,
        "p99Ms": resolved_p99,
        "maxMs": resolved_p99,
    }


@pytest.mark.parametrize(
    ("target_name", "target_class"),
    [
        ("tc-phase-f-load-staging-copy", "staging-copy"),
        ("tc-phase-f-prod-disposable", "disposable"),
        ("load-disposable", "disposable"),
    ],
)
def test_rejects_non_disposable_production_like_and_ambiguous_targets(
    target_name: str, target_class: str
) -> None:
    with pytest.raises(load_profile.SafetyError):
        load_profile._target(
            _args(target_name=target_name, target_class=target_class)
        )


def test_rejects_remote_database_even_when_name_looks_disposable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _args()
    target = load_profile._target(args)
    monkeypatch.setenv(
        "TC_PHASE_F_LOAD_DATABASE_URL",
        "postgresql://stage.example.test/tc_phase_f_load_test_disposable",
    )

    with pytest.raises(load_profile.rehearsal.SafetyError, match="loopback"):
        load_profile.rehearsal._database_target(args, target)


@pytest.mark.parametrize("flag_value", ["true", "1", "unexpected"])
def test_enabled_provider_flag_stops_before_database_preflight(
    monkeypatch: pytest.MonkeyPatch,
    flag_value: str,
) -> None:
    args = _args(
        execute=True,
        confirm="RUN PF-SF1-T06 LOAD-PROFILE ON tc-phase-f-load-disposable",
    )
    target = load_profile._target(args)
    monkeypatch.setenv("TC_AUTOMATIC_GENERATION_ENABLED", flag_value)
    monkeypatch.setattr(
        load_profile.rehearsal,
        "_database_target",
        lambda *unused: pytest.fail("provider guard allowed database resolution"),
    )

    with pytest.raises(load_profile.SafetyError, match="must be disabled"):
        load_profile._execute(args, target)


def test_preview_executes_zero_work_reads_no_database_url_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("TC_PHASE_F_LOAD_DATABASE_URL", raising=False)
    monkeypatch.setenv("TC_AUTOMATIC_GENERATION_ENABLED", "true")
    monkeypatch.setattr(
        load_profile,
        "_execute",
        lambda *unused: pytest.fail("preview executed workload"),
    )

    result = load_profile.main(
        [
            "--target-name",
            "tc-phase-f-load-disposable",
            "--database-url-env",
            "TC_PHASE_F_LOAD_DATABASE_URL",
            "--database-name",
            "tc_phase_f_load_test_disposable",
            "--output",
            "docs/phase-f/evidence/pf-sf1-t06-zero-work.json",
        ]
    )

    assert result == 0
    evidence = json.loads(capsys.readouterr().out)
    assert evidence["mode"] == "preview"
    assert evidence["workUnitsExecuted"] == 0
    assert evidence["loadGate"] == "NOT_RUN"
    assert not (load_profile.ROOT / evidence["output"]).exists()


def test_threshold_failure_is_fail_closed_and_sorted() -> None:
    metrics = _phase_metrics()
    metrics["surfaces"]["api"] = _operation_summary(
        errors=1,
        p95_ms=1_000.001,
        throughput=0.0,
    )
    metrics["overall"] = _operation_summary(count=4, errors=1, throughput=2.0)

    gate = load_profile.evaluate_gate(
        warm_metrics=metrics,
        combined_metrics=metrics,
        queue_metrics=_lag_metrics(p95_ms=300_000.001),
        lease_metrics=_lag_metrics(p95_ms=1.0, p99_ms=60_000.001),
        fixture_cases=99_999,
        provider_outbound_calls=1,
    )

    assert gate["passed"] is False
    assert gate["failures"] == sorted(gate["failures"])
    assert {
        "API_P95_EXCEEDED",
        "ERROR_RATE_NONZERO",
        "FIXTURE_CASE_COUNT_MISMATCH",
        "LEASE_LAG_P99_EXCEEDED",
        "PROVIDER_OUTBOUND_CALLS_DETECTED",
        "QUEUE_LAG_P95_EXCEEDED",
        "THROUGHPUT_BELOW_FLOOR",
    } <= set(gate["failures"])


def test_gate_passes_only_with_consistent_nonzero_phase_and_lag_samples() -> None:
    metrics = _phase_metrics()

    gate = load_profile.evaluate_gate(
        warm_metrics=metrics,
        combined_metrics=metrics,
        queue_metrics=_lag_metrics(),
        lease_metrics=_lag_metrics(),
        fixture_cases=100_000,
        provider_outbound_calls=0,
    )

    assert gate["passed"] is True
    assert gate["failures"] == []


def test_zero_warm_generation_samples_cannot_pass_via_zero_p95() -> None:
    warm = _phase_metrics({"generationScheduling": 0})

    gate = load_profile.evaluate_gate(
        warm_metrics=warm,
        combined_metrics=_phase_metrics(),
        queue_metrics=_lag_metrics(),
        lease_metrics=_lag_metrics(),
        fixture_cases=100_000,
        provider_outbound_calls=0,
    )

    assert gate["passed"] is False
    assert gate["checks"]["GENERATION_SCHEDULE_P95_EXCEEDED"] is False
    assert gate["checks"]["SURFACE_MISSING"] is False
    assert "GENERATION_SCHEDULE_P95_EXCEEDED" in gate["failures"]
    assert "SURFACE_MISSING" in gate["failures"]


@pytest.mark.parametrize(
    ("phase", "surface", "mode"),
    [
        ("warm", "api", "missing"),
        ("warm", "slackIngress", "zero"),
        ("combined", "workerPoll", "missing"),
        ("combined", "generationScheduling", "zero"),
    ],
)
def test_gate_fails_closed_for_missing_or_zero_required_surface(
    phase: str,
    surface: str,
    mode: str,
) -> None:
    warm = _phase_metrics()
    combined = _phase_metrics()
    selected = warm if phase == "warm" else combined
    if mode == "missing":
        selected["surfaces"].pop(surface)
    else:
        selected["surfaces"][surface] = _operation_summary(count=0)

    gate = load_profile.evaluate_gate(
        warm_metrics=warm,
        combined_metrics=combined,
        queue_metrics=_lag_metrics(),
        lease_metrics=_lag_metrics(),
        fixture_cases=100_000,
        provider_outbound_calls=0,
    )

    assert gate["passed"] is False
    assert gate["checks"]["SURFACE_MISSING"] is False
    assert "SURFACE_MISSING" in gate["failures"]


@pytest.mark.parametrize(
    "case",
    ["overall", "surface", "combined_smaller_than_warm"],
)
def test_gate_fails_closed_for_inconsistent_operation_totals(case: str) -> None:
    warm = _phase_metrics()
    combined = _phase_metrics()
    if case == "overall":
        warm["overall"]["count"] += 1
        warm["overall"]["successes"] += 1
    elif case == "surface":
        combined["surfaces"]["api"]["successes"] = 0
    else:
        warm = _phase_metrics({"api": 2})

    gate = load_profile.evaluate_gate(
        warm_metrics=warm,
        combined_metrics=combined,
        queue_metrics=_lag_metrics(),
        lease_metrics=_lag_metrics(),
        fixture_cases=100_000,
        provider_outbound_calls=0,
    )

    assert gate["passed"] is False
    assert gate["checks"]["SURFACE_TOTAL_MISMATCH"] is False
    assert "SURFACE_TOTAL_MISMATCH" in gate["failures"]


@pytest.mark.parametrize(
    ("queue_metrics", "lease_metrics"),
    [
        ({}, _lag_metrics()),
        (_lag_metrics(samples=0), _lag_metrics()),
        (_lag_metrics(), {}),
        (_lag_metrics(), _lag_metrics(samples=0)),
    ],
)
def test_gate_fails_closed_when_queue_or_lease_observations_are_absent(
    queue_metrics: dict[str, Any],
    lease_metrics: dict[str, Any],
) -> None:
    metrics = _phase_metrics()

    gate = load_profile.evaluate_gate(
        warm_metrics=metrics,
        combined_metrics=metrics,
        queue_metrics=queue_metrics,
        lease_metrics=lease_metrics,
        fixture_cases=100_000,
        provider_outbound_calls=0,
    )

    assert gate["passed"] is False
    assert gate["checks"]["LAG_SAMPLE_MISSING"] is False
    assert "LAG_SAMPLE_MISSING" in gate["failures"]


def test_gate_fails_closed_when_queue_and_lease_sample_totals_disagree() -> None:
    metrics = _phase_metrics()

    gate = load_profile.evaluate_gate(
        warm_metrics=metrics,
        combined_metrics=metrics,
        queue_metrics=_lag_metrics(samples=1),
        lease_metrics=_lag_metrics(samples=2),
        fixture_cases=100_000,
        provider_outbound_calls=0,
    )

    assert gate["passed"] is False
    assert gate["checks"]["LAG_SAMPLE_COUNT_MISMATCH"] is False
    assert "LAG_SAMPLE_COUNT_MISMATCH" in gate["failures"]


def test_percentiles_error_rate_and_throughput_are_deterministic() -> None:
    samples = [
        load_profile.OperationSample("api", latency, success)
        for latency, success in [
            (5.0, True),
            (1.0, True),
            (4.0, False),
            (2.0, True),
            (3.0, True),
        ]
    ]

    first = load_profile.metric_summary(samples, elapsed_seconds=2.0)
    second = load_profile.metric_summary(tuple(reversed(samples)), elapsed_seconds=2.0)

    assert first == second
    assert first == {
        "count": 5,
        "successes": 4,
        "errors": 1,
        "p50Ms": 3.0,
        "p95Ms": 5.0,
        "p99Ms": 5.0,
        "errorRate": 0.2,
        "throughputPerSecond": 2.0,
    }


def test_profile_is_fixed_at_one_hundred_thousand_cases() -> None:
    assert load_profile.FIXED_CASE_COUNT == 100_000
    assert load_profile.MAX_PROFILE_WORK_UNITS == 100_000
    assert load_profile._fixture_args().test_cases == 100_000
    option_strings = {
        option
        for action in load_profile._parser()._actions
        for option in action.option_strings
    }
    assert "--test-cases" not in option_strings


def test_database_pool_has_worker_and_sampler_headroom() -> None:
    assert load_profile._engine_pool_options(16) == {
        "pool_pre_ping": True,
        "pool_size": 18,
        "max_overflow": 0,
    }
    assert load_profile._engine_pool_options(64)["pool_size"] == 66


def test_dev_postgres_shared_memory_supports_concurrent_profile() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))

    assert compose["services"]["postgres"]["shm_size"] == "256mb"
