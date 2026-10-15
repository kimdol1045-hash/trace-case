"""Normalize Slack Events API payloads and message mutation shapes."""

from __future__ import annotations

from typing import Any

from tc_backend.infrastructure.slack.errors import SlackPayloadError
from tc_backend.infrastructure.slack.models import (
    NormalizedSlackEvent,
    SlackMessageOperation,
    SlackMessageSnapshot,
    SlackNormalizedEventType,
)


def normalize_events_api_payload(payload: dict[str, Any]) -> NormalizedSlackEvent:
    """Normalize the payload nested identically in HTTP and Socket Mode."""

    payload_type = _required_string(payload, "type", context="Slack envelope")
    if payload_type == SlackNormalizedEventType.URL_VERIFICATION:
        return NormalizedSlackEvent(
            normalized_type=SlackNormalizedEventType.URL_VERIFICATION,
            event_id=_optional_string(payload, "event_id") or "url_verification",
            workspace_id=_optional_string(payload, "team_id") or "",
            event_type=SlackNormalizedEventType.URL_VERIFICATION,
            event_subtype=None,
            event_time=_optional_integer(payload, "event_time"),
            message=None,
            challenge=_required_string(payload, "challenge", context="URL verification"),
            raw_payload=dict(payload),
        )
    if payload_type != SlackNormalizedEventType.EVENT_CALLBACK:
        raise SlackPayloadError(f"Unsupported Slack envelope type: {payload_type}")

    event_id = _required_string(payload, "event_id", context="event callback")
    workspace_id = _required_string(payload, "team_id", context="event callback")
    raw_event = payload.get("event")
    if not isinstance(raw_event, dict):
        raise SlackPayloadError("Slack event callback requires an object event")
    event: dict[str, Any] = raw_event
    event_type = _required_string(event, "type", context="Slack event")
    subtype = _optional_string(event, "subtype")
    message = (
        parse_slack_message(event, fallback_channel_id="")
        if event_type == "message"
        else None
    )
    return NormalizedSlackEvent(
        normalized_type=SlackNormalizedEventType.EVENT_CALLBACK,
        event_id=event_id,
        workspace_id=workspace_id,
        event_type=event_type,
        event_subtype=subtype,
        event_time=_optional_integer(payload, "event_time"),
        message=message,
        challenge=None,
        raw_payload=dict(payload),
    )


def parse_slack_message(
    payload: dict[str, Any],
    *,
    fallback_channel_id: str,
) -> SlackMessageSnapshot:
    """Normalize create, edit, and delete forms from Events or Web API calls."""

    subtype = _optional_string(payload, "subtype")
    channel_id = _optional_string(payload, "channel") or fallback_channel_id
    if not channel_id:
        raise SlackPayloadError("Slack message requires a channel ID")

    if subtype == "message_changed":
        nested = payload.get("message")
        if not isinstance(nested, dict):
            raise SlackPayloadError("Slack message_changed event requires message metadata")
        message: dict[str, Any] = nested
        message_ts = _required_string(message, "ts", context="changed message")
        revision_ts = _edited_timestamp(message) or _optional_string(payload, "event_ts")
        revision_ts = revision_ts or _required_string(payload, "ts", context="change event")
        thread_ts = _optional_string(message, "thread_ts") or message_ts
        return SlackMessageSnapshot(
            channel_id=channel_id,
            message_ts=message_ts,
            thread_ts=thread_ts,
            revision_ts=revision_ts,
            operation=SlackMessageOperation.EDIT,
            text=_optional_string(message, "text"),
            user_id=_optional_string(message, "user"),
            raw_payload=dict(payload),
            client_message_id=_optional_string(message, "client_msg_id"),
            metadata=_optional_mapping(message, "metadata"),
            bot_id=_optional_string(message, "bot_id"),
            app_id=_optional_string(message, "app_id"),
        )

    if subtype == "message_deleted":
        previous = payload.get("previous_message")
        previous_message = previous if isinstance(previous, dict) else {}
        deleted_message_ts = _optional_string(payload, "deleted_ts") or _optional_string(
            previous_message,
            "ts",
        )
        if deleted_message_ts is None:
            raise SlackPayloadError("Slack message_deleted event requires deleted_ts")
        revision_ts = _optional_string(payload, "event_ts") or _optional_string(payload, "ts")
        revision_ts = revision_ts or deleted_message_ts
        return SlackMessageSnapshot(
            channel_id=channel_id,
            message_ts=deleted_message_ts,
            thread_ts=_optional_string(previous_message, "thread_ts") or deleted_message_ts,
            revision_ts=revision_ts,
            operation=SlackMessageOperation.DELETE,
            text=None,
            user_id=_optional_string(previous_message, "user"),
            raw_payload=dict(payload),
            client_message_id=_optional_string(previous_message, "client_msg_id"),
            metadata=_optional_mapping(previous_message, "metadata"),
            bot_id=_optional_string(previous_message, "bot_id"),
            app_id=_optional_string(previous_message, "app_id"),
        )

    message_ts = _required_string(payload, "ts", context="Slack message")
    edited_timestamp = _edited_timestamp(payload)
    return SlackMessageSnapshot(
        channel_id=channel_id,
        message_ts=message_ts,
        thread_ts=_optional_string(payload, "thread_ts") or message_ts,
        revision_ts=edited_timestamp or message_ts,
        operation=(
            SlackMessageOperation.EDIT
            if edited_timestamp is not None
            else SlackMessageOperation.CREATE
        ),
        text=_optional_string(payload, "text"),
        user_id=_optional_string(payload, "user") or _optional_string(payload, "bot_id"),
        raw_payload=dict(payload),
        client_message_id=_optional_string(payload, "client_msg_id"),
        metadata=_optional_mapping(payload, "metadata"),
        bot_id=_optional_string(payload, "bot_id"),
        app_id=_optional_string(payload, "app_id"),
    )


def _edited_timestamp(payload: dict[str, Any]) -> str | None:
    edited = payload.get("edited")
    if edited is None:
        return None
    if not isinstance(edited, dict):
        raise SlackPayloadError("Slack edited metadata must be an object")
    return _required_string(edited, "ts", context="Slack edited metadata")


def _required_string(payload: dict[str, Any], key: str, *, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SlackPayloadError(f"{context} requires a non-empty {key}")
    return value


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise SlackPayloadError(f"Slack field {key} must be a string")
    return value or None


def _optional_integer(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise SlackPayloadError(f"Slack field {key} must be an integer")
    return value


def _optional_mapping(payload: dict[str, Any], key: str) -> dict[str, Any] | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise SlackPayloadError(f"Slack field {key} must be an object")
    return dict(value)
