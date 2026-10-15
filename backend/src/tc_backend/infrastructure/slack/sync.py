"""Provider-side history/thread reconciliation without repository coupling."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from tc_backend.infrastructure.slack.errors import SlackPayloadError
from tc_backend.infrastructure.slack.models import SlackMessageSnapshot, SlackSyncResult
from tc_backend.infrastructure.slack.transport import SlackWebApiTransport


class SlackConversationSynchronizer:
    """Fetch incremental ranges, canonicalize order, and deduplicate revisions."""

    def __init__(self, transport: SlackWebApiTransport) -> None:
        self._transport = transport

    def get_permalink(self, *, channel_id: str, message_ts: str) -> str:
        return self._transport.get_permalink(
            channel_id=channel_id,
            message_ts=message_ts,
        )

    def sync_history(
        self,
        *,
        channel_id: str,
        oldest: str | None = None,
        latest: str | None = None,
        known_revision_keys: frozenset[str] = frozenset(),
    ) -> SlackSyncResult:
        observed = self._transport.conversations_history(
            channel_id=channel_id,
            oldest=oldest,
            latest=latest,
            inclusive=True,
        )
        return reconcile_observations(observed, known_revision_keys=known_revision_keys)

    def sync_thread(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        oldest: str | None = None,
        latest: str | None = None,
        known_revision_keys: frozenset[str] = frozenset(),
    ) -> SlackSyncResult:
        observed = self._transport.conversations_replies(
            channel_id=channel_id,
            thread_ts=thread_ts,
            oldest=oldest,
            latest=latest,
            inclusive=True,
        )
        for message in observed:
            if message.thread_ts != thread_ts and message.message_ts != thread_ts:
                raise SlackPayloadError(
                    "Slack conversations.replies returned a message from another thread"
                )
        return reconcile_observations(observed, known_revision_keys=known_revision_keys)


def reconcile_observations(
    observed: tuple[SlackMessageSnapshot, ...],
    *,
    known_revision_keys: frozenset[str] = frozenset(),
) -> SlackSyncResult:
    """Deduplicate provider retries and repair provider order deterministically."""

    by_revision: dict[str, SlackMessageSnapshot] = {}
    duplicate_count = 0
    for message in observed:
        if message.revision_key in by_revision:
            duplicate_count += 1
            continue
        by_revision[message.revision_key] = message
    ordered = tuple(sorted(by_revision.values(), key=_message_sort_key))
    new_revisions = tuple(
        message for message in ordered if message.revision_key not in known_revision_keys
    )
    high_watermark = max((message.revision_ts for message in ordered), default=None, key=_ts)
    return SlackSyncResult(
        observed=ordered,
        new_revisions=new_revisions,
        duplicate_count=duplicate_count,
        high_watermark=high_watermark,
    )


def _message_sort_key(message: SlackMessageSnapshot) -> tuple[Decimal, Decimal, str]:
    return (_ts(message.message_ts), _ts(message.revision_ts), message.operation.value)


def _ts(value: str) -> Decimal:
    try:
        timestamp = Decimal(value)
    except InvalidOperation as exc:
        raise SlackPayloadError(f"Invalid Slack timestamp: {value}") from exc
    if not timestamp.is_finite() or timestamp < 0:
        raise SlackPayloadError(f"Invalid Slack timestamp: {value}")
    return timestamp
