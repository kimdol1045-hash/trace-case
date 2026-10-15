from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from tc_backend.generation.slack_notifications import (
    GenerationSlackNotificationConflict,
    GenerationSlackNotificationService,
    render_applied_success,
    render_failure,
    render_suggestion_success,
)
from tc_backend.infrastructure.slack.models import (
    NormalizedSlackEvent,
    SlackMessageOperation,
    SlackMessageSnapshot,
    SlackNormalizedEventType,
)
from tc_backend.slack_pipeline import (
    is_generation_notification_message,
    ledger_event,
    ledger_snapshot,
)


def _snapshot(*, bot_id: str | None, text: str = "safe terminal fact") -> SlackMessageSnapshot:
    metadata = {
        "event_type": "tc_generation_terminal_notification_v1",
        "event_payload": {
            "notification_id": "generation_slack_notification_abc",
            "generation_job_id": "generation_job_abc",
            "outcome": "SUCCESS_APPLIED",
            "contract_version": "1.0.0",
        },
    }
    return SlackMessageSnapshot(
        channel_id="C123",
        message_ts="1700000001.000001",
        thread_ts="1700000000.000001",
        revision_ts="1700000001.000001",
        operation=SlackMessageOperation.CREATE,
        text=text,
        user_id="U_OR_BOT",
        raw_payload={"metadata": metadata, "bot_id": bot_id},
        metadata=metadata,
        bot_id=bot_id,
    )


def test_templates_freeze_truthful_counts_and_omit_sensitive_material() -> None:
    applied = render_applied_success(
        created_count=3,
        reused_count=2,
        parent_outcome="REUSED",
        ui_url="https://tc.example/generation/jobs/generation_job_abc",
    )
    suggested = render_suggestion_success(suggestion_count=4, ui_url=None)
    failed = render_failure(
        attempt_number=3,
        error_code="GENERATION_TIMEOUT",
        retryable=False,
        ui_url=None,
    )

    assert applied["createdCount"] == 3
    assert applied["reusedCount"] == 2
    assert applied["parentOutcome"] == "REUSED"
    assert "4" in suggested["messageText"]
    assert "GENERATION_TIMEOUT" in failed["messageText"]
    assert failed["errorCode"] == "GENERATION_TIMEOUT"
    assert failed["retryable"] is False
    serialized = repr((applied, suggested, failed)).casefold()
    for forbidden in ("prompt", "token", "permalink", "raw error", "provider result"):
        assert forbidden not in serialized


def test_failure_template_maps_unknown_errors_to_a_stable_safe_code() -> None:
    rendered = render_failure(
        attempt_number=1,
        error_code="secret-provider-message-with-user-data",
        retryable=False,
        ui_url=None,
    )

    assert rendered["errorCode"] == "GENERATION_PROVIDER_ERROR"
    assert "secret-provider-message-with-user-data" not in rendered["messageText"]


def test_self_reply_requires_both_safe_marker_and_provider_bot_identity() -> None:
    assert is_generation_notification_message(_snapshot(bot_id="B_TC"))
    assert not is_generation_notification_message(_snapshot(bot_id=None))

    user_lookalike = _snapshot(bot_id=None, text="copied bot reply")
    assert user_lookalike.metadata is not None
    assert user_lookalike.metadata["event_type"] == (
        "tc_generation_terminal_notification_v1"
    )


def test_self_reply_is_removed_before_events_ingress_and_thread_sync_ledgers() -> None:
    message = _snapshot(bot_id="B_TC")
    event = NormalizedSlackEvent(
        normalized_type=SlackNormalizedEventType.EVENT_CALLBACK,
        event_id="Ev-self-notification",
        workspace_id="T123",
        event_type="message",
        event_subtype="bot_message",
        event_time=1_700_000_001,
        message=message,
        challenge=None,
        raw_payload={"type": "event_callback", "event": message.raw_payload},
    )

    assert ledger_event(event) is None
    assert [
        ledger_snapshot(candidate)
        for candidate in (message,)
        if not is_generation_notification_message(candidate)
    ] == []


@pytest.mark.parametrize(
    "base_url",
    (
        "https://tc.example/app|spoof",
        "https://tc.example/app>spoof",
        "https://tc.example/app\\spoof",
        "https://tc.example/app spoof",
    ),
)
def test_direct_public_url_seam_rejects_slack_markup_injection(base_url: str) -> None:
    service = GenerationSlackNotificationService(
        Session(),
        public_web_base_url=base_url,
    )

    with pytest.raises(GenerationSlackNotificationConflict, match="trusted HTTPS"):
        service._ui_url(uuid.uuid4())
