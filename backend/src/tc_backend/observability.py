"""Dependency-free, bounded-cardinality Prometheus metric primitives.

The registry accepts only metric families and label values declared below.  This
is intentionally stricter than a general Prometheus client: callers cannot turn
request paths, identifiers, provider URLs, object keys, or exception text into
labels by accident.
"""

from __future__ import annotations

import math
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

MetricKind = Literal["counter", "gauge", "histogram"]


@dataclass(frozen=True, slots=True)
class LabelSpec:
    name: str
    values: frozenset[str]


@dataclass(frozen=True, slots=True)
class MetricSpec:
    name: str
    kind: MetricKind
    help: str
    labels: tuple[LabelSpec, ...] = ()
    buckets: tuple[float, ...] = ()


@dataclass(slots=True)
class _Histogram:
    bucket_counts: list[int]
    count: int = 0
    total: float = 0.0


def _label(name: str, *values: str) -> LabelSpec:
    return LabelSpec(name=name, values=frozenset(values))


_COMPONENTS = ("api", "generation", "knowledge", "object_storage", "slack", "system")
_JOB_COMPONENTS = ("generation", "knowledge", "slack", "worker")
_ERROR_CODES = (
    "NONE",
    "OTHER",
    "GENERATION_AUTH_REJECTED",
    "GENERATION_AUTH_REQUIRED",
    "GENERATION_CONFIG_INVALID",
    "GENERATION_EXECUTION_OUTCOME_UNKNOWN",
    "GENERATION_INVALID_CONTENT_TYPE",
    "GENERATION_INVALID_RESPONSE",
    "GENERATION_MALFORMED_JSON",
    "GENERATION_PROVIDER_ERROR",
    "GENERATION_PROVIDER_REJECTED",
    "GENERATION_RATE_LIMITED",
    "GENERATION_RESPONSE_TOO_LARGE",
    "GENERATION_SOURCE_STALE",
    "GENERATION_SOURCE_UNAVAILABLE",
    "GENERATION_TIMEOUT",
    "GENERATION_TRANSPORT_UNAVAILABLE",
    "GENERATION_UNSUPPORTED_SCHEMA_VERSION",
    "GENERATION_WORKER_DELIVERY_EXHAUSTED",
    "GENERATION_WORK_ITEM_DELETED",
)


METRIC_SPECS = (
    MetricSpec("tc_api_up", "gauge", "Whether this API process has completed startup."),
    MetricSpec("tc_api_ready", "gauge", "Whether the API can serve database-backed traffic."),
    MetricSpec("tc_api_degraded", "gauge", "Whether an enabled optional dependency is degraded."),
    MetricSpec("tc_database_up", "gauge", "Whether the database observation query succeeded."),
    MetricSpec(
        "tc_dependency_ready",
        "gauge",
        "Whether a required dependency is ready; disabled optional dependencies report one.",
        (_label("dependency", "database", "generation", "knowledge", "lightrag", "object_storage", "slack"),),
    ),
    MetricSpec(
        "tc_api_http_requests_total",
        "counter",
        "API HTTP requests grouped only by stable component, method, and status class.",
        (
            _label("component", *_COMPONENTS),
            _label("method", "DELETE", "GET", "HEAD", "OPTIONS", "OTHER", "PATCH", "POST", "PUT"),
            _label("status_class", "1xx", "2xx", "3xx", "4xx", "5xx"),
        ),
    ),
    MetricSpec(
        "tc_api_http_request_duration_seconds",
        "histogram",
        "API HTTP request duration grouped only by stable component and method.",
        (
            _label("component", *_COMPONENTS),
            _label("method", "DELETE", "GET", "HEAD", "OPTIONS", "OTHER", "PATCH", "POST", "PUT"),
        ),
        (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0),
    ),
    MetricSpec(
        "tc_background_jobs",
        "gauge",
        "Current background jobs by bounded component and state.",
        (
            _label("component", *_JOB_COMPONENTS),
            _label("status", "QUEUED", "RUNNING", "SUCCEEDED", "RETRY", "DEAD"),
        ),
    ),
    MetricSpec(
        "tc_background_job_oldest_ready_age_seconds",
        "gauge",
        "Age of the oldest runnable background job by bounded component.",
        (_label("component", *_JOB_COMPONENTS),),
    ),
    MetricSpec(
        "tc_outbox_events",
        "gauge",
        "Current transactional outbox events by state.",
        (_label("status", "READY", "PROCESSING", "PUBLISHED", "FAILED", "DEAD"),),
    ),
    MetricSpec(
        "tc_outbox_oldest_ready_age_seconds",
        "gauge",
        "Age of the oldest READY or FAILED outbox event available for processing.",
    ),
    MetricSpec(
        "tc_slack_events",
        "gauge",
        "Current Slack ingress ledger events by state.",
        (_label("status", "RECEIVED", "PROCESSED", "IGNORED", "FAILED"),),
    ),
    MetricSpec(
        "tc_generation_jobs",
        "gauge",
        "Current generation jobs by lifecycle state.",
        (_label("status", "QUEUED", "RUNNING", "SUCCEEDED", "FAILED"),),
    ),
    MetricSpec(
        "tc_generation_execution_schedules",
        "gauge",
        "Current automatic generation execution schedules by state.",
        (_label("state", "NOT_BEFORE", "READY", "LEASED", "PROVIDER_OUTCOME_UNKNOWN", "TERMINAL"),),
    ),
    MetricSpec(
        "generation_execution_claim_total",
        "counter",
        "Durable generation execution lease claims.",
        (_label("outcome", "LEASED"),),
    ),
    MetricSpec(
        "generation_execution_schedule_lag_seconds",
        "gauge",
        "Maximum age past not-before for a READY generation execution schedule.",
    ),
    MetricSpec(
        "generation_execution_outcome_unknown",
        "gauge",
        "Current schedules requiring review because provider outcome is unknown.",
    ),
    MetricSpec(
        "generation_execution_outcome_unknown_total",
        "counter",
        "Durable terminal receipts for unknown generation provider outcomes.",
    ),
    MetricSpec(
        "generation_execution_lease_reclaim_total",
        "counter",
        "Durable additional generation leases beyond the first schedule claim.",
        (_label("phase", "EXECUTION"),),
    ),
    MetricSpec(
        "generation_provider_attempt_total",
        "counter",
        "Durable provider call observations by safe outcome and safe error code.",
        (
            _label("outcome", "AMBIGUOUS", "NON_RETRYABLE_FAILURE", "RETRYABLE_FAILURE", "SUCCEEDED"),
            _label("error_code", *_ERROR_CODES),
        ),
    ),
    MetricSpec(
        "generation_retry_exhaustion_total",
        "counter",
        "Durable generation retry exhaustion receipts by safe code and finality.",
        (
            _label("error_code", *_ERROR_CODES),
            _label("finality", "TERMINAL_EXHAUSTED", "TERMINAL_NON_RETRYABLE", "TERMINAL_REVIEW_REQUIRED"),
        ),
    ),
    MetricSpec(
        "generation_job_retry_total",
        "counter",
        "Durable generation retry jobs by bounded origin and safe error code.",
        (
            _label("origin", "AUTOMATIC", "OPERATOR"),
            _label("error_code", *_ERROR_CODES),
        ),
    ),
    MetricSpec(
        "generation_operator_retry_total",
        "counter",
        "Durably accepted generation operator retries.",
        (_label("outcome", "ACCEPTED"),),
    ),
    MetricSpec(
        "generation_failure_queue_actionable",
        "gauge",
        "Current durable automatic-generation failures requiring operator action.",
    ),
    MetricSpec(
        "tc_generation_materialization_schedules",
        "gauge",
        "Current generation materialization schedules by bounded mode and state.",
        (
            _label("mode", "AUTOMATIC_APPLY", "AUTOMATIC_SUGGESTIONS"),
            _label(
                "state",
                "COMMITTED",
                "FAILED",
                "LEASED",
                "OTHER",
                "QUEUED",
                "REVIEW_REQUIRED",
                "SKIPPED_SOURCE_UNAVAILABLE",
                "SKIPPED_STALE_SOURCE",
            ),
        ),
    ),
    MetricSpec(
        "tc_generation_materialization_oldest_nonterminal_age_seconds",
        "gauge",
        "Age of the oldest non-terminal generation materialization schedule.",
    ),
    MetricSpec(
        "generation_materialization_total",
        "counter",
        "Durable terminal generation materializations by mode and outcome.",
        (
            _label("mode", "AUTOMATIC_APPLY", "AUTOMATIC_SUGGESTIONS"),
            _label("outcome", "COMMITTED", "FAILED", "REVIEW_REQUIRED", "SKIPPED"),
        ),
    ),
    MetricSpec(
        "generation_terminal_notification_capture_total",
        "counter",
        "Durable generation terminal notification intents by bounded disposition.",
        (_label("outcome", "POST", "SUPPRESSED"),),
    ),
    MetricSpec(
        "generation_terminal_notification_capture_gap",
        "gauge",
        "Terminal generation failure receipts without a notification intent.",
    ),
    MetricSpec(
        "tc_knowledge_projection_events_total",
        "counter",
        "Durable knowledge projection checkpoints.",
    ),
    MetricSpec(
        "tc_knowledge_degraded_responses_total",
        "counter",
        "Knowledge responses served through a typed degraded path.",
        (_label("reason", "LIGHTRAG_UNAVAILABLE", "OTHER", "VECTOR_UNAVAILABLE"),),
    ),
    MetricSpec("tc_object_storage_ready", "gauge", "Whether storage and malware scanning are configured."),
    MetricSpec("tc_object_storage_bytes", "gauge", "Active R2 attachment bytes from database metadata."),
    MetricSpec(
        "tc_object_storage_requests_total",
        "counter",
        "Process-local object storage request observations by provider billing class.",
        (_label("class", "A", "B"),),
    ),
    MetricSpec(
        "tc_object_storage_budget_ratio",
        "gauge",
        "Process-local configured object storage budget ratio.",
        (_label("resource", "class_a_requests", "class_b_requests", "storage_bytes"),),
    ),
    MetricSpec("tc_worker_up", "gauge", "Whether this worker process is running."),
    MetricSpec("tc_worker_last_poll_timestamp_seconds", "gauge", "Unix time of the latest completed queue poll."),
    MetricSpec("tc_worker_last_success_timestamp_seconds", "gauge", "Unix time of the latest successful job."),
    MetricSpec(
        "tc_worker_poll_total",
        "counter",
        "Worker queue polls by bounded outcome.",
        (_label("outcome", "claimed", "error", "idle"),),
    ),
    MetricSpec(
        "tc_worker_jobs_total",
        "counter",
        "Worker job attempts by bounded component and transition outcome.",
        (
            _label("component", *_JOB_COMPONENTS),
            _label("outcome", "dead", "failure", "lease_lost", "retry", "success"),
        ),
    ),
    MetricSpec("tc_worker_heartbeat_failures_total", "counter", "Background-job lease heartbeat failures."),
)

_SPECS = {spec.name: spec for spec in METRIC_SPECS}


class MetricsRegistry:
    """Thread-safe registry with a closed metric and label vocabulary."""

    def __init__(self) -> None:
        self._values: dict[tuple[str, tuple[str, ...]], float] = {}
        self._histograms: dict[tuple[str, tuple[str, ...]], _Histogram] = {}
        self._lock = threading.Lock()

    def inc(self, name: str, amount: float = 1.0, **labels: str) -> None:
        spec, key = self._key(name, labels, expected="counter")
        del spec
        if not math.isfinite(amount) or amount < 0:
            raise ValueError("counter increments must be finite and non-negative")
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def set_counter(self, name: str, value: float, **labels: str) -> None:
        """Set a counter reconstructed from an append-only durable ledger."""

        _spec, key = self._key(name, labels, expected="counter")
        self._set_value(key, value, non_negative=True)

    def set_gauge(self, name: str, value: float, **labels: str) -> None:
        _spec, key = self._key(name, labels, expected="gauge")
        self._set_value(key, value, non_negative=False)

    def observe(self, name: str, value: float, **labels: str) -> None:
        spec, key = self._key(name, labels, expected="histogram")
        if not math.isfinite(value) or value < 0:
            raise ValueError("histogram observations must be finite and non-negative")
        with self._lock:
            histogram = self._histograms.setdefault(
                key, _Histogram(bucket_counts=[0] * len(spec.buckets))
            )
            histogram.count += 1
            histogram.total += value
            for index, boundary in enumerate(spec.buckets):
                if value <= boundary:
                    histogram.bucket_counts[index] += 1

    def render(self) -> str:
        with self._lock:
            values = dict(self._values)
            histograms = {
                key: _Histogram(list(value.bucket_counts), value.count, value.total)
                for key, value in self._histograms.items()
            }
        lines: list[str] = []
        for spec in METRIC_SPECS:
            samples = sorted(
                (key, value) for key, value in values.items() if key[0] == spec.name
            )
            histogram_samples = sorted(
                (key, value) for key, value in histograms.items() if key[0] == spec.name
            )
            if not samples and not histogram_samples:
                continue
            lines.extend((f"# HELP {spec.name} {spec.help}", f"# TYPE {spec.name} {spec.kind}"))
            for (_name, label_values), value in samples:
                lines.append(f"{spec.name}{_format_labels(spec, label_values)} {_number(value)}")
            for (_name, label_values), histogram in histogram_samples:
                base = dict(zip((label.name for label in spec.labels), label_values, strict=True))
                for boundary, count in zip(spec.buckets, histogram.bucket_counts, strict=True):
                    lines.append(
                        f"{spec.name}_bucket{_format_label_mapping(base | {'le': _number(boundary)})} {count}"
                    )
                lines.append(
                    f"{spec.name}_bucket{_format_label_mapping(base | {'le': '+Inf'})} {histogram.count}"
                )
                labels_text = _format_label_mapping(base)
                lines.append(f"{spec.name}_sum{labels_text} {_number(histogram.total)}")
                lines.append(f"{spec.name}_count{labels_text} {histogram.count}")
        return "\n".join(lines) + ("\n" if lines else "")

    def _key(
        self, name: str, labels: dict[str, str], *, expected: MetricKind
    ) -> tuple[MetricSpec, tuple[str, tuple[str, ...]]]:
        try:
            spec = _SPECS[name]
        except KeyError as error:
            raise ValueError(f"metric is not registered: {name}") from error
        if spec.kind != expected:
            raise ValueError(f"metric {name} is {spec.kind}, not {expected}")
        expected_names = tuple(label.name for label in spec.labels)
        if set(labels) != set(expected_names):
            raise ValueError(f"metric {name} requires labels {expected_names}")
        values: list[str] = []
        for label in spec.labels:
            value = str(labels[label.name])
            if value not in label.values:
                if "OTHER" in label.values:
                    value = "OTHER"
                else:
                    raise ValueError(f"label value is not allowed for {name}.{label.name}")
            values.append(value)
        return spec, (name, tuple(values))

    def _set_value(
        self, key: tuple[str, tuple[str, ...]], value: float, *, non_negative: bool
    ) -> None:
        if not math.isfinite(value) or (non_negative and value < 0):
            raise ValueError("metric values must be finite")
        with self._lock:
            self._values[key] = value


def render_registries(*registries: MetricsRegistry) -> str:
    """Merge exposition from registries without opening their internal state."""

    sections = [registry.render().rstrip() for registry in registries]
    return "\n".join(section for section in sections if section) + "\n"


class PrometheusTextfileExporter:
    """Atomically publish one worker registry for node-exporter textfile collection."""

    def __init__(self, path: Path) -> None:
        if path.suffix != ".prom":
            raise ValueError("worker metrics path must end in .prom")
        self.path = path

    def write(self, registry: MetricsRegistry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(registry.render())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise


@dataclass(frozen=True, slots=True)
class AlertThresholds:
    api_error_ratio: float = 0.05
    api_p95_seconds: float = 2.0
    worker_poll_age_seconds: float = 120.0
    outbox_lag_seconds: float = 300.0
    storage_budget_ratio: float = 0.85
    generation_schedule_lag_seconds: float = 300.0
    generation_materialization_age_seconds: float = 600.0


@dataclass(frozen=True, slots=True)
class OperationalMeasurements:
    database_up: float = 1.0
    api_error_ratio: float = 0.0
    api_p95_seconds: float = 0.0
    worker_poll_age_seconds: float = 0.0
    outbox_lag_seconds: float = 0.0
    storage_budget_ratio: float = 0.0
    generation_schedule_lag_seconds: float = 0.0
    generation_materialization_age_seconds: float = 0.0
    generation_outcome_unknown: float = 0.0
    generation_notification_gap: float = 0.0


DEFAULT_ALERT_THRESHOLDS = AlertThresholds()


def breached_thresholds(
    measurements: OperationalMeasurements,
    thresholds: AlertThresholds = DEFAULT_ALERT_THRESHOLDS,
) -> tuple[str, ...]:
    """Pure boundary evaluator mirrored by deploy/observability alert rules."""

    breached: list[str] = []
    if measurements.database_up < 1:
        breached.append("TCPostgreSQLUnavailable")
    if measurements.api_error_ratio > thresholds.api_error_ratio:
        breached.append("TCApiHighErrorRatio")
    if measurements.api_p95_seconds > thresholds.api_p95_seconds:
        breached.append("TCApiHighLatency")
    if measurements.worker_poll_age_seconds > thresholds.worker_poll_age_seconds:
        breached.append("TCWorkerPollStale")
    if measurements.outbox_lag_seconds > thresholds.outbox_lag_seconds:
        breached.append("TCOutboxLagHigh")
    if measurements.storage_budget_ratio > thresholds.storage_budget_ratio:
        breached.append("TCObjectStorageBudgetHigh")
    if measurements.generation_schedule_lag_seconds > thresholds.generation_schedule_lag_seconds:
        breached.append("TCGenerationScheduleLagHigh")
    if (
        measurements.generation_materialization_age_seconds
        > thresholds.generation_materialization_age_seconds
    ):
        breached.append("TCGenerationMaterializationStuck")
    if measurements.generation_outcome_unknown > 0:
        breached.append("TCGenerationProviderOutcomeUnknown")
    if measurements.generation_notification_gap > 0:
        breached.append("TCGenerationTerminalNotificationGap")
    return tuple(breached)


def api_component(path: str) -> str:
    """Collapse request paths to a fixed, non-identifying component label."""

    if path in {"/api/health", "/api/metrics", "/api/ready"}:
        return "system"
    if path == "/integrations/slack/events" or path.startswith(
        ("/api/integrations/slack", "/api/slack")
    ):
        return "slack"
    if "attachment" in path or path.startswith("/api/integrations/object-storage"):
        return "object_storage"
    if path.startswith("/api/knowledge"):
        return "knowledge"
    if "generation" in path:
        return "generation"
    return "api"


def job_component(job_type: str) -> str:
    """Collapse job types to a fixed component label."""

    if "GENERATION" in job_type:
        return "generation"
    if job_type.startswith("SLACK_") or job_type in {"WORK_ITEM_PARSE"}:
        return "slack"
    if job_type.startswith("KNOWLEDGE_"):
        return "knowledge"
    return "worker"


def http_method(method: str) -> str:
    normalized = method.upper()
    return normalized if normalized in {"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"} else "OTHER"


def http_status_class(status_code: int) -> str:
    bounded = min(5, max(1, status_code // 100))
    return f"{bounded}xx"


def safe_error_code(value: str | None) -> str:
    return value if value in _ERROR_CODES else "NONE" if value is None else "OTHER"


def _format_labels(spec: MetricSpec, values: tuple[str, ...]) -> str:
    return _format_label_mapping(
        dict(zip((label.name for label in spec.labels), values, strict=True))
    )


def _format_label_mapping(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    rendered = ",".join(
        f'{name}="{_escape_label(value)}"' for name, value in sorted(labels.items())
    )
    return "{" + rendered + "}"


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _number(value: float) -> str:
    numeric = float(value)
    return str(int(numeric)) if numeric.is_integer() else format(numeric, ".15g")
