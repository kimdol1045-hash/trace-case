"""Shared Slack normalization-to-ledger composition used by every ingress mode."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tc_backend.db.models import SlackEventType
from tc_backend.db.slack import SlackEventInput
from tc_backend.db.slack import SlackMessageSnapshot as LedgerSnapshot
from tc_backend.infrastructure.slack.models import (
    NormalizedSlackEvent,
    SlackMessageOperation,
    SlackMessageSnapshot,
)


def ledger_event(
    event: NormalizedSlackEvent, *, ingress_source: str = "HTTP_EVENTS"
) -> SlackEventInput | None:
    """Map a supported normalized message event to the repository boundary."""

    message = event.message
    if message is None:
        return None
    if is_generation_notification_message(message):
        return None
    event_type = {
        SlackMessageOperation.EDIT: SlackEventType.EDIT,
        SlackMessageOperation.DELETE: SlackEventType.DELETE,
    }.get(message.operation)
    if event_type is None:
        event_type = SlackEventType.REPLY if message.is_reply else SlackEventType.ROOT
    raw_event = event.raw_payload.get("event")
    raw_event_ts = raw_event.get("event_ts") if isinstance(raw_event, dict) else None
    event_ts = raw_event_ts if isinstance(raw_event_ts, str) else message.revision_ts
    channel_type = (
        raw_event.get("channel_type") if isinstance(raw_event, dict) else None
    )
    return SlackEventInput(
        event_id=event.event_id,
        slack_workspace_id=event.workspace_id,
        channel_id=message.channel_id,
        event_type=event_type,
        event_ts=event_ts,
        message_ts=message.message_ts,
        body=message.text,
        thread_ts=message.thread_ts if message.is_reply else None,
        revision_ts=message.revision_ts,
        author_id=message.user_id,
        channel_is_private=channel_type in {"group", "im", "mpim"},
        raw_payload={**event.raw_payload, "_tcIngressSource": ingress_source},
    )


def ledger_snapshot(
    message: SlackMessageSnapshot,
    *,
    root_permalink: str | None = None,
) -> LedgerSnapshot:
    """Map Web API observations into the repository's append-only sync input."""

    return LedgerSnapshot(
        message_ts=message.message_ts,
        body=message.text,
        revision_ts=message.revision_ts,
        author_id=message.user_id,
        permalink=(
            root_permalink if message.message_ts == message.thread_ts else None
        ),
        is_deleted=message.operation is SlackMessageOperation.DELETE,
        raw_payload=dict(message.raw_payload),
    )


def is_generation_notification_message(message: SlackMessageSnapshot) -> bool:
    """Recognize provider-attested app/bot replies carrying the T06 marker.

    Metadata alone is not trusted. Slack must also identify the message as bot/app
    authored, which prevents a user-authored lookalike from bypassing source capture.
    """

    if message.bot_id is None and message.app_id is None:
        return False
    metadata = message.metadata
    if not isinstance(metadata, Mapping):
        return False
    payload = metadata.get("event_payload")
    return (
        metadata.get("event_type") == "tc_generation_terminal_notification_v1"
        and isinstance(payload, Mapping)
        and isinstance(payload.get("notification_id"), str)
        and isinstance(payload.get("generation_job_id"), str)
        and payload.get("outcome")
        in {"SUCCESS_APPLIED", "SUCCESS_SUGGESTIONS", "FAILURE"}
        and payload.get("contract_version") == "1.0.0"
    )


def redact_slack_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove ingress-only credential-like keys before serializing an event."""

    return {
        key: redact_slack_payload(value) if isinstance(value, dict) else value
        for key, value in payload.items()
        if key.casefold() not in {"token", "authorization", "signing_secret"}
    }
