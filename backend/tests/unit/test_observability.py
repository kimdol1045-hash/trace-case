from __future__ import annotations

from pathlib import Path

import pytest

from tc_backend.observability import (
    AlertThresholds,
    MetricsRegistry,
    OperationalMeasurements,
    PrometheusTextfileExporter,
    api_component,
    breached_thresholds,
    job_component,
)

ROOT = Path(__file__).resolve().parents[3]


def test_registry_exposes_only_registered_bounded_labels() -> None:
    metrics = MetricsRegistry()
    metrics.inc(
        "tc_api_http_requests_total",
        component="knowledge",
        method="POST",
        status_class="2xx",
    )
    metrics.observe(
        "tc_api_http_request_duration_seconds",
        0.25,
        component="knowledge",
        method="POST",
    )

    rendered = metrics.render()

    assert 'component="knowledge"' in rendered
    assert 'method="POST"' in rendered
    assert 'component="knowledge",le="0.25",method="POST"} 1' in rendered
    assert "_sum" in rendered
    with pytest.raises(ValueError, match="requires labels"):
        metrics.inc(
            "tc_api_http_requests_total",
            component="knowledge",
            method="POST",
            status_class="2xx",
            path="/api/knowledge/nodes/private-id",
        )
    with pytest.raises(ValueError, match="not allowed"):
        metrics.inc(
            "tc_api_http_requests_total",
            component="tenant-123",
            method="POST",
            status_class="2xx",
        )


def test_component_classification_never_uses_path_or_job_identifiers() -> None:
    assert api_component("/api/knowledge/nodes/knowledge_private-id") == "knowledge"
    assert api_component("/api/issues/issue_private-id/attachments") == "object_storage"
    assert api_component("/api/generation-jobs/generation_job_private-id") == "generation"
    assert job_component("SLACK_AUTOMATIC_GENERATION") == "generation"
    assert job_component("unrecognized-private-job-name") == "worker"


def test_textfile_export_is_atomic_and_contains_no_temporary_file(tmp_path: Path) -> None:
    target = tmp_path / "worker.prom"
    metrics = MetricsRegistry()
    metrics.set_gauge("tc_worker_up", 1)

    PrometheusTextfileExporter(target).write(metrics)

    assert target.read_text() == (
        "# HELP tc_worker_up Whether this worker process is running.\n"
        "# TYPE tc_worker_up gauge\n"
        "tc_worker_up 1\n"
    )
    assert list(tmp_path.iterdir()) == [target]


def test_alert_thresholds_are_strict_at_documented_boundaries() -> None:
    thresholds = AlertThresholds()
    at_boundary = OperationalMeasurements(
        api_error_ratio=thresholds.api_error_ratio,
        api_p95_seconds=thresholds.api_p95_seconds,
        worker_poll_age_seconds=thresholds.worker_poll_age_seconds,
        outbox_lag_seconds=thresholds.outbox_lag_seconds,
        storage_budget_ratio=thresholds.storage_budget_ratio,
        generation_schedule_lag_seconds=thresholds.generation_schedule_lag_seconds,
        generation_materialization_age_seconds=(
            thresholds.generation_materialization_age_seconds
        ),
    )
    assert breached_thresholds(at_boundary, thresholds) == ()

    above = OperationalMeasurements(
        database_up=0,
        api_error_ratio=thresholds.api_error_ratio + 0.001,
        api_p95_seconds=thresholds.api_p95_seconds + 0.001,
        worker_poll_age_seconds=thresholds.worker_poll_age_seconds + 0.001,
        outbox_lag_seconds=thresholds.outbox_lag_seconds + 0.001,
        storage_budget_ratio=thresholds.storage_budget_ratio + 0.001,
        generation_schedule_lag_seconds=(
            thresholds.generation_schedule_lag_seconds + 0.001
        ),
        generation_materialization_age_seconds=(
            thresholds.generation_materialization_age_seconds + 0.001
        ),
        generation_outcome_unknown=1,
        generation_notification_gap=1,
    )
    assert breached_thresholds(above, thresholds) == (
        "TCPostgreSQLUnavailable",
        "TCApiHighErrorRatio",
        "TCApiHighLatency",
        "TCWorkerPollStale",
        "TCOutboxLagHigh",
        "TCObjectStorageBudgetHigh",
        "TCGenerationScheduleLagHigh",
        "TCGenerationMaterializationStuck",
        "TCGenerationProviderOutcomeUnknown",
        "TCGenerationTerminalNotificationGap",
    )


def test_prometheus_rules_mirror_tested_thresholds_and_have_unique_names() -> None:
    rules = (ROOT / "deploy/observability/prometheus-rules.yml").read_text()
    alert_names = [
        line.split(":", 1)[1].strip()
        for line in rules.splitlines()
        if line.lstrip().startswith("- alert:")
    ]

    assert alert_names
    assert len(alert_names) == len(set(alert_names))
    for expression in (
        'absent(up{job="tc-api"})',
        'unless on(instance, job) tc_worker_up',
        ") > 0.05",
        ") > 2",
        ") > 120",
        "tc_outbox_oldest_ready_age_seconds > 300",
        "max(tc_object_storage_budget_ratio) > 0.85",
        "generation_execution_schedule_lag_seconds > 300",
        "tc_generation_materialization_oldest_nonterminal_age_seconds > 600",
        "generation_execution_outcome_unknown > 0",
        "generation_terminal_notification_capture_gap > 0",
    ):
        assert expression in rules
    assert "receiver:" not in rules
