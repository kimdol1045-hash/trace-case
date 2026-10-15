from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from tc_backend.config import Settings
from tc_backend.infrastructure.slack.http_transport import SlackWebApiHttpTransport
from tc_backend.infrastructure.slack.testing import DeterministicFakeSlackTransport
from tc_backend.infrastructure.slack.transport import SlackWebApiTransport
from tc_worker.slack_runtime import SlackWorkerRuntime


_ROOT = Path(__file__).resolve().parents[3]
_HANDLER = "SLACK_GENERATION_NOTIFICATION_DELIVERY"


def test_pe_se2_t06_notification_service_is_an_explicit_production_seam() -> None:
    """RED: T05 head has no trusted terminal capture/delivery service."""

    assert importlib.util.find_spec(
        "tc_backend.generation.slack_notifications"
    ) is not None, (
        "PE-SE2-T06 requires a transaction-scoped generation Slack notification "
        "service; notification truth cannot live only in the worker payload"
    )


def test_pe_se2_t06_slack_transport_has_no_hidden_retry_write_and_reconciliation() -> None:
    """RED: the Sprint 5 transport is ingestion/read-only."""

    required = {"post_thread_reply", "find_thread_reply_by_notification"}
    assert required <= set(dir(SlackWebApiTransport)), (
        "PE-SE2-T06 requires narrow post and exact reconciliation protocol methods"
    )
    assert required <= set(dir(SlackWebApiHttpTransport))
    assert required <= set(dir(DeterministicFakeSlackTransport)), (
        "the deterministic fake must exercise ack loss, ambiguity, reconciliation, "
        "duplicate correlation, rate limit, and permanent rejection without live Slack"
    )


def test_pe_se2_t06_worker_claims_delivery_only_with_injected_configured_transport() -> None:
    """RED: no T06 handler is registered at the current worker head."""

    unconfigured = SlackWorkerRuntime(session_factory=sessionmaker())
    configured = SlackWorkerRuntime.with_transport(
        session_factory=sessionmaker(),
        transport=DeterministicFakeSlackTransport(),
        token_workspace_id="T123",
    )
    unattested_team = SlackWorkerRuntime.with_transport(
        session_factory=sessionmaker(),
        transport=DeterministicFakeSlackTransport(),
    )

    assert _HANDLER not in unconfigured.handlers()
    assert _HANDLER not in unattested_team.handlers()
    assert _HANDLER in configured.handlers(), (
        "a transport-configured worker must expose the intent-ID-only delivery handler"
    )


def test_pe_se2_t06_public_web_link_setting_is_absent_until_validated() -> None:
    """RED: no trusted product-Web base URL exists for a frozen Slack link."""

    assert "public_web_base_url" in Settings.model_fields, (
        "PE-SE2-T06 requires an optional validated public_web_base_url distinct "
        "from the public Slack ingress URL; omission must remain a supported template"
    )


def test_pe_se2_t06_slack_manifest_is_still_ingestion_only() -> None:
    """RED: live write scope/metadata registration are intentionally not installed."""

    manifest = (_ROOT / "deploy/slack/app-manifest.yaml").read_text(encoding="utf-8")

    assert "- chat:write" in manifest, (
        "PE-SE2-T06 production requires bot chat:write; current manifest proves that "
        "this contract task made no live Slack write claim"
    )
    assert "tc_generation_terminal_notification_v1" in manifest, (
        "the custom safe metadata event must be registered before credentialed delivery"
    )
