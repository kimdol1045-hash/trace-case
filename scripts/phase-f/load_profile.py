# mypy: disable-error-code=import-untyped
"""Preview-first concurrent PF-SF1-T06 load-profile harness.

The fixed 100,000-case fixture is destructive.  Execution is therefore limited
to an empty, attested, loopback PostgreSQL database and requires an exact
confirmation.  Preview is the default and performs no work, reads no database
URL, and creates no evidence file.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import math
import os
import resource
import sys
import threading
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PHASE_F_ROOT = Path(__file__).resolve().parent
EVIDENCE_ROOT = ROOT / "docs" / "phase-f" / "evidence"
for import_root in (
    ROOT / "scripts",
    ROOT / "backend" / "src",
    ROOT / "apps" / "api" / "src",
    ROOT / "apps" / "worker" / "src",
    PHASE_F_ROOT,
):
    sys.path.insert(0, str(import_root))

import rehearse as rehearsal  # type: ignore[import-not-found]  # noqa: E402

FIXED_CASE_COUNT = 100_000
MAX_PROFILE_WORK_UNITS = 100_000
DATABASE_POOL_HEADROOM = 2
PROFILE_ID = "PF-SF1-T06-CONCURRENT-100K-V1"
GENERATION_JOB_TYPE = "AUTOMATIC_GENERATION_EXECUTE"
SLACK_WORKSPACE_ID = "TLOAD0001"
SLACK_CHANNEL_ID = "CLOAD0001"
SYNTHETIC_SIGNING_SECRET = "phase-f-local-synthetic-signing-secret"
ENABLE_FLAGS = (
    "TC_AUTOMATIC_GENERATION_ENABLED",
    "TC_AUTOMATIC_KNOWLEDGE_PROJECTION_ENABLED",
    "TC_SLACK_GENERATION_NOTIFICATIONS_ENABLED",
    "TC_OBJECT_STORAGE_ENABLED",
)
FALSEY = frozenset({"0", "false", "no", "off", "disabled"})
SURFACE_CYCLE = (
    "api",
    "api",
    "api",
    "api",
    "slackIngress",
    "workerPoll",
    "slackIngress",
    "workerPoll",
    "generationScheduling",
    "workerPoll",
)
REQUIRED_SURFACES = tuple(sorted(set(SURFACE_CYCLE)))


class SafetyError(RuntimeError):
    """The requested execution does not meet the disposable-load boundary."""


@dataclass(frozen=True, slots=True)
class OperationSample:
    surface: str
    latency_ms: float
    success: bool
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class LagSample:
    queue_lag_ms: float
    lease_lag_ms: float
    ready_count: int
    running_count: int


@dataclass(frozen=True, slots=True)
class Threshold:
    code: str
    metric: str
    comparison: str
    value: float
    source: str


THRESHOLDS = (
    Threshold(
        "API_P95_EXCEEDED",
        "api.p95Ms",
        "<=",
        1_000.0,
        "기존 PF-SF1-T06 case benchmark p95 계약",
    ),
    Threshold(
        "SLACK_ACK_P95_EXCEEDED",
        "slackIngress.p95Ms",
        "<=",
        3_000.0,
        "Slack Events 3초 acknowledgement 계약",
    ),
    Threshold(
        "WORKER_POLL_P95_EXCEEDED",
        "workerPoll.p95Ms",
        "<=",
        2_000.0,
        "기본 worker poll interval 2초",
    ),
    Threshold(
        "GENERATION_SCHEDULE_P95_EXCEEDED",
        "generationScheduling.p95Ms",
        "<=",
        1_000.0,
        "로컬 durable scheduling harness 초기 상한",
    ),
    Threshold(
        "ERROR_RATE_NONZERO",
        "overall.errorRate",
        "<=",
        0.0,
        "synthetic disposable acceptance policy",
    ),
    Threshold(
        "THROUGHPUT_BELOW_FLOOR",
        "overall.throughputPerSecond",
        ">=",
        10.0,
        "로컬 harness 진행성 floor; production capacity SLO가 아님",
    ),
    Threshold(
        "QUEUE_LAG_P95_EXCEEDED",
        "queueLag.p95Ms",
        "<=",
        300_000.0,
        "generation/outbox lag alert 5분 경계",
    ),
    Threshold(
        "LEASE_LAG_P99_EXCEEDED",
        "leaseLag.p99Ms",
        "<=",
        60_000.0,
        "generation execution lease 60초 경계",
    ),
)


class NoOutboundGenerationStub:
    """Generation handler that deliberately has no transport/provider seam."""

    def __init__(self) -> None:
        self._handled = 0
        self._lock = threading.Lock()

    def __call__(
        self, payload: Mapping[str, Any], _claim: object | None = None
    ) -> None:
        if payload.get("profileId") != PROFILE_ID:
            raise ValueError("generation load payload is outside this profile")
        with self._lock:
            self._handled += 1

    @property
    def handled(self) -> int:
        with self._lock:
            return self._handled


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or run the fixed 100,000-case concurrent Phase F load profile. "
            "Execution accepts only an attested empty loopback disposable database."
        )
    )
    parser.add_argument("--target-name", required=True)
    parser.add_argument("--target-class", default="disposable")
    parser.add_argument("--database-url-env", required=True)
    parser.add_argument("--database-name", required=True)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--duration-seconds", type=float, default=60.0)
    parser.add_argument("--queue-sample-interval", type=float, default=0.5)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm")
    return parser


def _target(args: argparse.Namespace) -> rehearsal.Target:
    if args.target_class != "disposable":
        raise SafetyError("load execution requires target class exactly disposable")
    try:
        target = rehearsal.Target(str(args.target_name), str(args.target_class))
        rehearsal._validate_database_declaration(args, target)
    except rehearsal.SafetyError as error:
        raise SafetyError(str(error)) from error
    if not 1 <= args.concurrency <= 64:
        raise SafetyError("concurrency must be between 1 and 64")
    if not 1.0 <= args.duration_seconds <= 1_800.0:
        raise SafetyError("duration must be between 1 and 1800 seconds")
    if not 0.1 <= args.queue_sample_interval <= 5.0:
        raise SafetyError("queue sample interval must be between 0.1 and 5 seconds")
    _safe_output(args.output)
    return target


def _safe_output(path: Path) -> Path:
    resolved = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    try:
        resolved.relative_to(EVIDENCE_ROOT.resolve())
    except ValueError as error:
        raise SafetyError("output must be inside docs/phase-f/evidence") from error
    if resolved.suffix != ".json":
        raise SafetyError("output must be a .json evidence file")
    return resolved


def _write_evidence(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise SafetyError("evidence output already exists; refusing to overwrite") from error
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


def _confirmation(target: rehearsal.Target) -> str:
    return f"RUN PF-SF1-T06 LOAD-PROFILE ON {target.name}"


def _require_confirmation(args: argparse.Namespace, target: rehearsal.Target) -> None:
    expected = _confirmation(target)
    if args.confirm != expected:
        raise SafetyError(f"execution requires exact confirmation: {expected}")


def _provider_disable_guard(environment: Mapping[str, str]) -> None:
    enabled = sorted(
        name
        for name in ENABLE_FLAGS
        if environment.get(name, "").strip()
        and environment.get(name, "").strip().casefold() not in FALSEY
    )
    if enabled:
        raise SafetyError(
            "provider-capable features must be disabled before execution: "
            + ", ".join(enabled)
        )


def nearest_rank(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    if not 0 < quantile <= 1:
        raise ValueError("quantile must be greater than zero and at most one")
    ordered = sorted(float(value) for value in values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def metric_summary(samples: Sequence[OperationSample], elapsed_seconds: float) -> dict[str, Any]:
    if elapsed_seconds <= 0:
        raise ValueError("elapsed seconds must be positive")
    latencies = [sample.latency_ms for sample in samples]
    errors = sum(not sample.success for sample in samples)
    successes = len(samples) - errors
    return {
        "count": len(samples),
        "successes": successes,
        "errors": errors,
        "p50Ms": round(nearest_rank(latencies, 0.50), 3),
        "p95Ms": round(nearest_rank(latencies, 0.95), 3),
        "p99Ms": round(nearest_rank(latencies, 0.99), 3),
        "errorRate": round(errors / len(samples), 6) if samples else 0.0,
        "throughputPerSecond": round(successes / elapsed_seconds, 3),
    }


def lag_summary(samples: Sequence[LagSample], field: str) -> dict[str, Any]:
    values = [float(getattr(sample, field)) for sample in samples]
    return {
        "samples": len(values),
        "p50Ms": round(nearest_rank(values, 0.50), 3),
        "p95Ms": round(nearest_rank(values, 0.95), 3),
        "p99Ms": round(nearest_rank(values, 0.99), 3),
        "maxMs": round(max(values), 3) if values else 0.0,
    }


def summarize_operations(
    samples: Sequence[OperationSample], elapsed_seconds: float
) -> dict[str, Any]:
    grouped: dict[str, list[OperationSample]] = defaultdict(list)
    for sample in samples:
        grouped[sample.surface].append(sample)
    return {
        "overall": metric_summary(samples, elapsed_seconds),
        "surfaces": {
            surface: metric_summary(grouped.get(surface, ()), elapsed_seconds)
            for surface in REQUIRED_SURFACES
        },
        "errorCodes": dict(
            sorted(
                (
                    code,
                    sum(sample.error_code == code for sample in samples),
                )
                for code in {
                    sample.error_code for sample in samples if sample.error_code is not None
                }
            )
        ),
    }


def _nonnegative_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0.0 else None


def _nonnegative_int(value: Any) -> int | None:
    number = _nonnegative_float(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _operation_totals(summary: Any) -> tuple[int, int, int] | None:
    if not isinstance(summary, Mapping):
        return None
    count = _nonnegative_int(summary.get("count"))
    successes = _nonnegative_int(summary.get("successes"))
    errors = _nonnegative_int(summary.get("errors"))
    p50 = _nonnegative_float(summary.get("p50Ms"))
    p95 = _nonnegative_float(summary.get("p95Ms"))
    p99 = _nonnegative_float(summary.get("p99Ms"))
    error_rate = _nonnegative_float(summary.get("errorRate"))
    throughput = _nonnegative_float(summary.get("throughputPerSecond"))
    if (
        count is None
        or successes is None
        or errors is None
        or count != successes + errors
        or p50 is None
        or p95 is None
        or p99 is None
        or error_rate is None
        or error_rate > 1.0
        or throughput is None
    ):
        return None
    if not (p50 <= p95 <= p99):
        return None
    expected_error_rate = round(errors / count, 6) if count else 0.0
    if not math.isclose(error_rate, expected_error_rate, abs_tol=1e-9):
        return None
    return count, successes, errors


def _surface_summaries(metrics: Any) -> Mapping[str, Any] | None:
    if not isinstance(metrics, Mapping):
        return None
    surfaces = metrics.get("surfaces")
    if not isinstance(surfaces, Mapping):
        return None
    if set(surfaces) != set(REQUIRED_SURFACES):
        return None
    return surfaces


def _phase_has_required_samples(metrics: Any) -> bool:
    surfaces = _surface_summaries(metrics)
    if surfaces is None:
        return False
    for surface in REQUIRED_SURFACES:
        summary = surfaces[surface]
        if not isinstance(summary, Mapping):
            return False
        count = _nonnegative_int(summary.get("count"))
        if count is None or count <= 0:
            return False
    return True


def _phase_totals(metrics: Any) -> dict[str, tuple[int, int, int]] | None:
    if not isinstance(metrics, Mapping):
        return None
    surfaces = _surface_summaries(metrics)
    overall = _operation_totals(metrics.get("overall"))
    if surfaces is None or overall is None or not _phase_has_required_samples(metrics):
        return None
    surface_totals = {
        surface: _operation_totals(surfaces[surface])
        for surface in REQUIRED_SURFACES
    }
    if any(value is None for value in surface_totals.values()):
        return None
    totals = {
        surface: value
        for surface, value in surface_totals.items()
        if value is not None
    }
    aggregate = tuple(sum(value[index] for value in totals.values()) for index in range(3))
    if aggregate != overall:
        return None
    return totals | {"__overall__": overall}


def _combined_includes_warm(warm_metrics: Any, combined_metrics: Any) -> bool:
    warm_totals = _phase_totals(warm_metrics)
    combined_totals = _phase_totals(combined_metrics)
    if warm_totals is None or combined_totals is None:
        return False
    return all(
        combined_totals[surface][index] >= warm_totals[surface][index]
        for surface in (*REQUIRED_SURFACES, "__overall__")
        for index in range(3)
    )


def _surface_p95_within(metrics: Any, surface: str, limit: float) -> bool:
    surfaces = _surface_summaries(metrics)
    if surfaces is None:
        return False
    summary = surfaces.get(surface)
    totals = _operation_totals(summary)
    if totals is None or totals[0] <= 0 or not isinstance(summary, Mapping):
        return False
    p95 = _nonnegative_float(summary.get("p95Ms"))
    return p95 is not None and p95 <= limit


def _lag_observations(metrics: Any) -> int | None:
    if not isinstance(metrics, Mapping):
        return None
    samples = _nonnegative_int(metrics.get("samples"))
    p50 = _nonnegative_float(metrics.get("p50Ms"))
    p95 = _nonnegative_float(metrics.get("p95Ms"))
    p99 = _nonnegative_float(metrics.get("p99Ms"))
    maximum = _nonnegative_float(metrics.get("maxMs"))
    if (
        samples is None
        or samples <= 0
        or p50 is None
        or p95 is None
        or p99 is None
        or maximum is None
    ):
        return None
    if not (p50 <= p95 <= p99 <= maximum):
        return None
    return samples


def _lag_percentile_within(metrics: Any, field: str, limit: float) -> bool:
    if _lag_observations(metrics) is None or not isinstance(metrics, Mapping):
        return False
    value = _nonnegative_float(metrics.get(field))
    return value is not None and value <= limit


def evaluate_gate(
    *,
    warm_metrics: Mapping[str, Any],
    queue_metrics: Mapping[str, Any],
    lease_metrics: Mapping[str, Any],
    fixture_cases: int,
    provider_outbound_calls: int,
    combined_metrics: Mapping[str, Any] | None = None,
    lag_sample_errors: int = 0,
) -> dict[str, Any]:
    warm_overall = warm_metrics.get("overall")
    combined_overall = (
        combined_metrics.get("overall")
        if isinstance(combined_metrics, Mapping)
        else None
    )
    warm_throughput = (
        _nonnegative_float(warm_overall.get("throughputPerSecond"))
        if isinstance(warm_overall, Mapping)
        else None
    )
    combined_error_rate = (
        _nonnegative_float(combined_overall.get("errorRate"))
        if isinstance(combined_overall, Mapping)
        else None
    )
    queue_samples = _lag_observations(queue_metrics)
    lease_samples = _lag_observations(lease_metrics)
    failures: list[str] = []
    checks = {
        "API_P95_EXCEEDED": _surface_p95_within(warm_metrics, "api", 1_000.0),
        "SLACK_ACK_P95_EXCEEDED": _surface_p95_within(
            warm_metrics, "slackIngress", 3_000.0
        ),
        "WORKER_POLL_P95_EXCEEDED": _surface_p95_within(
            warm_metrics, "workerPoll", 2_000.0
        ),
        "GENERATION_SCHEDULE_P95_EXCEEDED": _surface_p95_within(
            warm_metrics, "generationScheduling", 1_000.0
        ),
        "ERROR_RATE_NONZERO": (
            combined_error_rate is not None and combined_error_rate == 0.0
        ),
        "THROUGHPUT_BELOW_FLOOR": (
            warm_throughput is not None and warm_throughput >= 10.0
        ),
        "QUEUE_LAG_P95_EXCEEDED": _lag_percentile_within(
            queue_metrics, "p95Ms", 300_000.0
        ),
        "LEASE_LAG_P99_EXCEEDED": _lag_percentile_within(
            lease_metrics, "p99Ms", 60_000.0
        ),
        "FIXTURE_CASE_COUNT_MISMATCH": fixture_cases == FIXED_CASE_COUNT,
        "PROVIDER_OUTBOUND_CALLS_DETECTED": provider_outbound_calls == 0,
        "LAG_SAMPLE_FAILED": lag_sample_errors == 0,
        "LAG_SAMPLE_MISSING": queue_samples is not None and lease_samples is not None,
        "LAG_SAMPLE_COUNT_MISMATCH": (
            queue_samples is not None
            and lease_samples is not None
            and queue_samples == lease_samples
        ),
        "SURFACE_MISSING": _phase_has_required_samples(warm_metrics)
        and _phase_has_required_samples(combined_metrics),
        "SURFACE_TOTAL_MISMATCH": _combined_includes_warm(
            warm_metrics, combined_metrics
        ),
    }
    failures.extend(code for code, passed in checks.items() if not passed)
    return {
        "passed": not failures,
        "failures": sorted(failures),
        "checks": {code: checks[code] for code in sorted(checks)},
    }


def _preview(args: argparse.Namespace, target: rehearsal.Target) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "task": "PF-SF1-T06",
        "profileId": PROFILE_ID,
        "mode": "preview",
        "executes": False,
        "workUnitsExecuted": 0,
        "loadGate": "NOT_RUN",
        "target": {
            "name": target.name,
            "class": target.target_class,
            "databaseName": args.database_name,
            "databaseUrlEnvironment": args.database_url_env,
            "requiredAttestation": target.database_attestation,
            "loopbackRequired": True,
            "emptyDatabaseRequired": True,
        },
        "profile": {
            "fixedSyntheticCaseCount": FIXED_CASE_COUNT,
            "maximumSyntheticWorkUnits": MAX_PROFILE_WORK_UNITS,
            "concurrency": args.concurrency,
            "durationSeconds": args.duration_seconds,
            "surfaces": sorted(set(SURFACE_CYCLE)),
            "providerMode": "NO_OUTBOUND_STUB",
        },
        "confirmation": _confirmation(target),
        "output": str(_safe_output(args.output).relative_to(ROOT)),
        "thresholds": [asdict(threshold) for threshold in THRESHOLDS],
        "limitations": _limitations(),
    }


def _limitations() -> list[str]:
    return [
        "firstTouch는 동일 process/database의 첫 구간이며 OS 또는 PostgreSQL cold cache가 아니다.",
        "ASGI in-process transport라 reverse proxy, TLS, WAN latency를 포함하지 않는다.",
        "generation은 durable queue scheduling/claim/lease만 측정하고 provider 실행은 stub이다.",
        "process max RSS/CPU와 DB 크기만 기록하며 DB/container/host 포화도는 측정하지 않는다.",
        "단일 실행은 production capacity 또는 장기 soak 안정성을 증명하지 않는다.",
    ]


def _fixture_args() -> argparse.Namespace:
    return argparse.Namespace(
        test_cases=FIXED_CASE_COUNT,
        categories=20,
        work_items=1_000,
        tags=20,
        deleted_every=20,
    )


def _database_size(engine: Any) -> int:
    from sqlalchemy import text

    with engine.connect() as connection:
        return int(
            connection.execute(text("SELECT pg_database_size(current_database())")).scalar_one()
        )


def _engine_pool_options(concurrency: int) -> dict[str, Any]:
    """Reserve one connection per worker plus sampler/lifespan headroom."""

    return {
        "pool_pre_ping": True,
        "pool_size": concurrency + DATABASE_POOL_HEADROOM,
        "max_overflow": 0,
    }


def _lag_sample(session_factory: Any) -> LagSample:
    from sqlalchemy import text

    statement = text(
        "SELECT "
        "COALESCE(EXTRACT(EPOCH FROM (now() - (SELECT min(run_after) "
        "FROM background_jobs WHERE status IN ('QUEUED', 'RETRY') "
        "AND run_after <= now()))), 0) * 1000, "
        "COALESCE(EXTRACT(EPOCH FROM (now() - (SELECT min(COALESCE(heartbeat_at, locked_at)) "
        "FROM background_jobs WHERE status = 'RUNNING'))), 0) * 1000, "
        "(SELECT count(*) FROM background_jobs WHERE status IN ('QUEUED', 'RETRY') "
        "AND run_after <= now()), "
        "(SELECT count(*) FROM background_jobs WHERE status = 'RUNNING')"
    )
    with session_factory() as session:
        row = session.execute(statement).one()
    return LagSample(
        queue_lag_ms=max(0.0, float(row[0])),
        lease_lag_ms=max(0.0, float(row[1])),
        ready_count=int(row[2]),
        running_count=int(row[3]),
    )


def _slack_payload(sequence: int) -> bytes:
    event_ts = f"1800000000.{sequence % 1_000_000:06d}"
    payload = {
        "token": "synthetic-not-a-credential",
        "team_id": SLACK_WORKSPACE_ID,
        "api_app_id": "ALOAD0001",
        "type": "event_callback",
        "event_id": f"EvLoad{sequence:016d}",
        "event_time": 1_800_000_000,
        "event": {
            "type": "message",
            "user": "ULOAD0001",
            "text": (
                f"개발건: 합성 부하 {sequence}\n"
                "요청 메뉴: 테스트 > 합성\n"
                "기능 설명: 비민감 결정론 데이터\n"
                "요청 내용: 로컬 부하 검증"
            ),
            "channel": SLACK_CHANNEL_ID,
            "channel_type": "channel",
            "ts": event_ts,
            "event_ts": event_ts,
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


async def _run_phase(
    *,
    label: str,
    duration_seconds: float,
    concurrency: int,
    client: Any,
    session_factory: Any,
    settings: Any,
    handlers: Mapping[str, Any],
    signing_secret: str,
    sequence_start: int,
    sample_interval: float,
    max_operations: int,
) -> tuple[list[OperationSample], list[LagSample], float, int, int]:
    from tc_backend.db.jobs import JobRepository  # type: ignore[import-untyped]
    from tc_backend.infrastructure.slack.signature import (  # type: ignore[import-untyped]
        sign_slack_request,
    )
    from tc_worker.main import Worker  # type: ignore[import-untyped]

    samples: list[OperationSample] = []
    lag_samples: list[LagSample] = []
    lag_sample_errors = 0
    deadline = time.perf_counter() + duration_seconds
    sequence = sequence_start
    operation_count = 0
    sequence_lock = asyncio.Lock()
    capped = asyncio.Event()

    async def next_sequence() -> int | None:
        nonlocal operation_count, sequence
        async with sequence_lock:
            if operation_count >= max_operations:
                capped.set()
                return None
            value = sequence
            sequence += 1
            operation_count += 1
            return value

    async def api_operation(_sequence: int) -> None:
        response = await client.get("/api/test-cases", params={"limit": 50})
        if response.status_code != 200:
            raise RuntimeError(f"API_STATUS_{response.status_code}")

    async def slack_operation(current: int) -> None:
        raw = _slack_payload(current)
        signed_at = int(time.time())
        response = await client.post(
            "/integrations/slack/events",
            content=raw,
            headers={
                "content-type": "application/json",
                "x-slack-request-timestamp": str(signed_at),
                "x-slack-signature": sign_slack_request(
                    signing_secret=signing_secret,
                    timestamp=signed_at,
                    raw_body=raw,
                ),
            },
        )
        if response.status_code != 200 or response.json().get("outcome") != "ACCEPTED":
            raise RuntimeError(f"SLACK_STATUS_{response.status_code}")

    def enqueue_generation(current: int) -> None:
        with session_factory() as session, session.begin():
            JobRepository(session).enqueue(
                job_type=GENERATION_JOB_TYPE,
                payload={"profileId": PROFILE_ID, "sequence": current},
                max_attempts=1,
                idempotency_key=f"{PROFILE_ID}:generation:{current}",
            )

    def poll_worker(current: int) -> None:
        Worker(
            session_factory=session_factory,
            settings=settings,
            worker_id=f"phase-f-load-{label}-{current}",
            handlers=handlers,
        ).run_once()

    async def worker_loop() -> None:
        while time.perf_counter() < deadline:
            current = await next_sequence()
            if current is None:
                return
            surface = SURFACE_CYCLE[current % len(SURFACE_CYCLE)]
            started = time.perf_counter()
            success = True
            error_code = None
            try:
                if surface == "api":
                    await api_operation(current)
                elif surface == "slackIngress":
                    await slack_operation(current)
                elif surface == "generationScheduling":
                    await asyncio.to_thread(enqueue_generation, current)
                else:
                    await asyncio.to_thread(poll_worker, current)
            except Exception as error:  # sanitized evidence: class/code only
                success = False
                error_code = (
                    str(error)
                    if isinstance(error, RuntimeError)
                    and str(error).startswith(("API_STATUS_", "SLACK_STATUS_"))
                    else type(error).__name__
                )
            samples.append(
                OperationSample(
                    surface,
                    (time.perf_counter() - started) * 1_000,
                    success,
                    error_code,
                )
            )

    async def sample_lag() -> None:
        nonlocal lag_sample_errors
        while time.perf_counter() < deadline and not capped.is_set():
            try:
                lag_samples.append(await asyncio.to_thread(_lag_sample, session_factory))
            except Exception:
                lag_sample_errors += 1
            await asyncio.sleep(sample_interval)

    started = time.perf_counter()
    async with asyncio.TaskGroup() as group:
        for _ in range(concurrency):
            group.create_task(worker_loop())
        group.create_task(sample_lag())
    elapsed = time.perf_counter() - started
    return samples, lag_samples, elapsed, sequence, lag_sample_errors


async def _mixed_load(
    *,
    engine: Any,
    database_url: str,
    duration_seconds: float,
    concurrency: int,
    sample_interval: float,
) -> dict[str, Any]:
    import httpx
    from sqlalchemy.orm import Session, sessionmaker

    from tc_api.main import create_app  # type: ignore[import-untyped]
    from tc_api.slack_runtime import SlackApiRuntime  # type: ignore[import-untyped]
    from tc_backend.config import Settings, SlackMode  # type: ignore[import-untyped]
    from tc_backend.db.slack import SlackRepository  # type: ignore[import-untyped]
    from tc_backend.infrastructure.slack.config import (  # type: ignore[import-untyped]
        SlackRuntimeState,
    )
    from tc_backend.infrastructure.slack.receivers import (  # type: ignore[import-untyped]
        SlackHttpEventsReceiver,
    )
    from tc_backend.infrastructure.slack.signature import (  # type: ignore[import-untyped]
        SlackRequestVerifier,
    )
    from tc_worker.slack_runtime import SlackWorkerRuntime  # type: ignore[import-untyped]

    sessions = sessionmaker(engine, expire_on_commit=False, class_=Session)
    with sessions() as session, session.begin():
        SlackRepository(session).put_channel(
            slack_workspace_id=SLACK_WORKSPACE_ID,
            channel_id=SLACK_CHANNEL_ID,
            channel_name="phase-f-synthetic-load",
        )
    slack_api = SlackApiRuntime(
        state=SlackRuntimeState.CONFIGURED,
        mode=SlackMode.HTTP,
        workspace_id=SLACK_WORKSPACE_ID,
        app_id="ALOAD0001",
        http_receiver=SlackHttpEventsReceiver(
            SlackRequestVerifier(SYNTHETIC_SIGNING_SECRET)
        ),
    )
    application = create_app(
        validate_database=False,
        database_engine=engine,
        slack_runtime=slack_api,
    )
    settings_values: dict[str, Any] = {
        "_env_file": None,
        "database_url": database_url,
        "encryption_master_key": "phase-f-load-synthetic-key-material-0001",
        "automatic_generation_enabled": False,
        "automatic_knowledge_projection_enabled": False,
        "slack_generation_notifications_enabled": False,
        "object_storage_enabled": False,
        "slack_mode": SlackMode.DISABLED,
        "job_retry_base_seconds": 0,
        "job_retry_max_seconds": 0,
        "job_stale_after_seconds": 60,
    }
    settings = Settings(**settings_values)
    generation_stub = NoOutboundGenerationStub()
    slack_worker = SlackWorkerRuntime(session_factory=sessions)
    handlers = {
        "SLACK_EVENT_PROCESS": slack_worker.process_event,
        GENERATION_JOB_TYPE: generation_stub,
    }
    first_touch_seconds = min(10.0, max(0.2, duration_seconds * 0.2))
    if first_touch_seconds >= duration_seconds:
        first_touch_seconds = duration_seconds * 0.25
    warm_seconds = duration_seconds - first_touch_seconds
    transport = httpx.ASGITransport(app=application)
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=transport, base_url="http://phase-f-load.local"
        ) as client:
            first, first_lag, first_elapsed, sequence, first_lag_errors = await _run_phase(
                label="first-touch",
                duration_seconds=first_touch_seconds,
                concurrency=concurrency,
                client=client,
                session_factory=sessions,
                settings=settings,
                handlers=handlers,
                signing_secret=SYNTHETIC_SIGNING_SECRET,
                sequence_start=0,
                sample_interval=sample_interval,
                max_operations=MAX_PROFILE_WORK_UNITS // 5,
            )
            warm, warm_lag, warm_elapsed, _, warm_lag_errors = await _run_phase(
                label="warm",
                duration_seconds=warm_seconds,
                concurrency=concurrency,
                client=client,
                session_factory=sessions,
                settings=settings,
                handlers=handlers,
                signing_secret=SYNTHETIC_SIGNING_SECRET,
                sequence_start=sequence,
                sample_interval=sample_interval,
                max_operations=MAX_PROFILE_WORK_UNITS - (MAX_PROFILE_WORK_UNITS // 5),
            )
    all_lag = first_lag + warm_lag
    return {
        "firstTouch": summarize_operations(first, first_elapsed),
        "warm": summarize_operations(warm, warm_elapsed),
        "combined": summarize_operations(first + warm, first_elapsed + warm_elapsed),
        "lagSamples": all_lag,
        "lagSampleErrorCount": first_lag_errors + warm_lag_errors,
        "elapsedSeconds": round(first_elapsed + warm_elapsed, 3),
        "workUnitsExecuted": len(first) + len(warm),
        "generationStubHandledCount": generation_stub.handled,
        "providerOutboundCallCount": 0,
    }


def _execute(args: argparse.Namespace, target: rehearsal.Target) -> tuple[dict[str, Any], bool]:
    _require_confirmation(args, target)
    _provider_disable_guard(os.environ)
    output = _safe_output(args.output)
    if output.exists():
        raise SafetyError("evidence output already exists; refusing to overwrite")
    try:
        database = rehearsal._database_target(args, target)
        preflight = rehearsal._database_preflight(
            database, target, require_empty=True
        )
    except rehearsal.SafetyError as error:
        raise SafetyError(str(error)) from error

    benchmark = importlib.import_module("benchmark_cases")
    from sqlalchemy import create_engine

    started_at = datetime.now(UTC)
    cpu_before = resource.getrusage(resource.RUSAGE_SELF)
    benchmark.migrate(database.url)
    engine = create_engine(database.url, **_engine_pool_options(args.concurrency))
    try:
        size_before_fixture = _database_size(engine)
        fixture = benchmark.load_fixture(engine, _fixture_args())
        size_after_fixture = _database_size(engine)
        load = asyncio.run(
            _mixed_load(
                engine=engine,
                database_url=database.url,
                duration_seconds=args.duration_seconds,
                concurrency=args.concurrency,
                sample_interval=args.queue_sample_interval,
            )
        )
        size_after_load = _database_size(engine)
    finally:
        engine.dispose()
    cpu_after = resource.getrusage(resource.RUSAGE_SELF)
    lag_samples = load.pop("lagSamples")
    queue_metrics = lag_summary(lag_samples, "queue_lag_ms")
    lease_metrics = lag_summary(lag_samples, "lease_lag_ms")
    gate = evaluate_gate(
        warm_metrics=load["warm"],
        queue_metrics=queue_metrics,
        lease_metrics=lease_metrics,
        fixture_cases=int(fixture["cases"]),
        provider_outbound_calls=int(load["providerOutboundCallCount"]),
        combined_metrics=load["combined"],
        lag_sample_errors=int(load["lagSampleErrorCount"]),
    )
    result = {
        "schemaVersion": 1,
        "task": "PF-SF1-T06",
        "profileId": PROFILE_ID,
        "mode": "execute",
        "loadGate": "PASS" if gate["passed"] else "FAIL",
        "startedAt": started_at.isoformat(),
        "finishedAt": datetime.now(UTC).isoformat(),
        "target": {
            "name": target.name,
            "class": target.target_class,
            "databaseName": database.expected_name,
            "databaseUrlEnvironment": database.url_env,
            "attested": True,
            "preflight": preflight,
        },
        "profile": {
            "fixedSyntheticCaseCount": FIXED_CASE_COUNT,
            "maximumSyntheticWorkUnits": MAX_PROFILE_WORK_UNITS,
            "concurrency": args.concurrency,
            "durationSeconds": args.duration_seconds,
            "providerMode": "NO_OUTBOUND_STUB",
        },
        "fixture": fixture,
        "measurements": {
            **load,
            "queueLag": queue_metrics,
            "leaseLag": lease_metrics,
        },
        "resources": {
            "processUserCpuSeconds": round(cpu_after.ru_utime - cpu_before.ru_utime, 3),
            "processSystemCpuSeconds": round(cpu_after.ru_stime - cpu_before.ru_stime, 3),
            "processMaxRssRaw": int(cpu_after.ru_maxrss),
            "processMaxRssUnit": "bytes" if sys.platform == "darwin" else "KiB",
            "databaseBytesBeforeFixture": size_before_fixture,
            "databaseBytesAfterFixture": size_after_fixture,
            "databaseBytesAfterLoad": size_after_load,
        },
        "thresholds": [asdict(threshold) for threshold in THRESHOLDS],
        "gate": gate,
        "limitations": _limitations(),
        "cleanup": {
            "automaticCleanup": False,
            "guidance": (
                "클라이언트와 worker가 멈춘 것을 확인하고 별도 관리자 연결에서 exact DB name과 "
                "database comment를 다시 확인한 뒤 evidence를 보관한다. 승인된 DBA 절차로 이 "
                "단일 disposable DB만 제거한다; 이 harness는 database cleanup 명령을 제공하지 않는다."
            ),
        },
    }
    _write_evidence(output, result)
    return result, bool(gate["passed"])


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        target = _target(args)
        if not args.execute:
            print(json.dumps(_preview(args, target), ensure_ascii=False, indent=2))
            return 0
        result, passed = _execute(args, target)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if passed else 1
    except SafetyError as error:
        print(f"safety error: {error}", file=sys.stderr)
        return 2
    except Exception as error:  # never expose URLs, credentials, or payloads
        print(f"execution error: {type(error).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
