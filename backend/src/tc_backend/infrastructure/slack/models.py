"""Provider-neutral Slack envelopes, messages, sync results, and parse output."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SlackIngressKind(StrEnum):
    HTTP = "http"
    SOCKET = "socket"


class SlackNormalizedEventType(StrEnum):
    EVENT_CALLBACK = "event_callback"
    URL_VERIFICATION = "url_verification"


class SlackMessageOperation(StrEnum):
    CREATE = "create"
    EDIT = "edit"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class SlackMessageSnapshot:
    """One observable provider revision of a Slack message."""

    channel_id: str
    message_ts: str
    thread_ts: str
    revision_ts: str
    operation: SlackMessageOperation
    text: str | None
    user_id: str | None
    raw_payload: dict[str, Any] = field(compare=False, repr=False)
    client_message_id: str | None = None
    metadata: Mapping[str, Any] | None = field(default=None, compare=False, repr=False)
    bot_id: str | None = None
    app_id: str | None = None

    @property
    def revision_key(self) -> str:
        """Stable provider-derived key suitable for repository idempotency."""

        return ":".join(
            (
                self.channel_id,
                self.message_ts,
                self.revision_ts,
                self.operation.value,
            )
        )

    @property
    def is_reply(self) -> bool:
        return self.thread_ts != self.message_ts


@dataclass(frozen=True, slots=True)
class SlackPostMessageResult:
    """Minimal provider acknowledgement retained outside raw Slack payloads."""

    channel_id: str
    message_ts: str


@dataclass(frozen=True, slots=True)
class SlackReplyReconciliation:
    """All exact notification correlations observed in the canonical thread."""

    matches: tuple[SlackPostMessageResult, ...]


@dataclass(frozen=True, slots=True)
class NormalizedSlackEvent:
    """Canonical event shared by HTTP Events and Socket Mode receivers."""

    normalized_type: SlackNormalizedEventType
    event_id: str
    workspace_id: str
    event_type: str
    event_subtype: str | None
    event_time: int | None
    message: SlackMessageSnapshot | None
    challenge: str | None
    raw_payload: dict[str, Any] = field(compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class SlackEnvelopeAcknowledgement:
    """One acknowledgement model rendered for either ingress transport."""

    envelope_id: str | None
    response_payload: dict[str, Any] | None = None

    def http_body(self) -> dict[str, Any] | None:
        return self.response_payload

    def socket_payload(self) -> dict[str, Any]:
        if self.envelope_id is None:
            raise ValueError("Socket Mode acknowledgement requires an envelope ID")
        payload: dict[str, Any] = {"envelope_id": self.envelope_id}
        if self.response_payload is not None:
            payload["payload"] = self.response_payload
        return payload


@dataclass(frozen=True, slots=True)
class ReceivedSlackEnvelope:
    ingress: SlackIngressKind
    event: NormalizedSlackEvent
    acknowledgement: SlackEnvelopeAcknowledgement


@dataclass(frozen=True, slots=True)
class SlackSyncResult:
    """Deterministically ordered, revision-deduplicated provider observations."""

    observed: tuple[SlackMessageSnapshot, ...]
    new_revisions: tuple[SlackMessageSnapshot, ...]
    duplicate_count: int
    high_watermark: str | None


SLACK_WORK_ITEM_FIELDS = (
    "developmentItem",
    "requestedMenu",
    "featureDescription",
    "requestDetails",
)


@dataclass(frozen=True, slots=True)
class SlackWorkItemParseResult:
    development_item: str | None
    requested_menu: str | None
    feature_description: str | None
    request_details: str | None
    missing_fields: tuple[str, ...]
    confidence: float
    source_message_revisions: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        """Return the contract's camel-case shape without inventing absent values."""

        result: dict[str, Any] = {
            "missingFields": list(self.missing_fields),
            "confidence": self.confidence,
            "sourceMessageRevisions": list(self.source_message_revisions),
        }
        optional_values = {
            "developmentItem": self.development_item,
            "requestedMenu": self.requested_menu,
            "featureDescription": self.feature_description,
            "requestDetails": self.request_details,
        }
        result.update({key: value for key, value in optional_values.items() if value is not None})
        return result


@dataclass(frozen=True, slots=True)
class SlackSourceMessage:
    message_revision_id: str
    body: str
    message_ts: str = ""
