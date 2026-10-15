"""Deterministic Slack Web API and Socket Mode fakes for fault/reorder tests."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

from tc_backend.infrastructure.slack.errors import (
    SlackAmbiguousWriteError,
    SlackTransportError,
)
from tc_backend.infrastructure.slack.models import (
    SlackMessageOperation,
    SlackMessageSnapshot,
    SlackPostMessageResult,
    SlackReplyReconciliation,
)


class DeterministicFakeSlackTransport:
    """Mutable provider timeline with explicit missing/duplicate/order/fault controls."""

    def __init__(self) -> None:
        self._messages: dict[tuple[str, str], list[SlackMessageSnapshot]] = defaultdict(list)
        self._hidden_once: set[tuple[str, str, str]] = set()
        self._duplicate_once: set[tuple[str, str, str]] = set()
        self._reverse_once: set[tuple[str, str]] = set()
        self._failures: list[SlackTransportError] = []
        self._ack_loss_count = 0
        self._next_message_number = 1
        self.calls: list[tuple[str, str, str | None, str | None]] = []
        self.socket_mode_url = "wss://wss-primary.slack.com/link/?ticket=fake"
        self.socket_open_calls = 0

    def seed_thread(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        messages: Iterable[SlackMessageSnapshot],
    ) -> None:
        self._messages[(channel_id, thread_ts)].extend(messages)

    def add_message(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        message_ts: str,
        text: str,
        user_id: str = "U_FAKE",
    ) -> SlackMessageSnapshot:
        message = SlackMessageSnapshot(
            channel_id=channel_id,
            message_ts=message_ts,
            thread_ts=thread_ts,
            revision_ts=message_ts,
            operation=SlackMessageOperation.CREATE,
            text=text,
            user_id=user_id,
            raw_payload={"fake": True},
        )
        self._messages[(channel_id, thread_ts)].append(message)
        return message

    def edit_message(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        message_ts: str,
        revision_ts: str,
        text: str,
        user_id: str = "U_FAKE",
    ) -> SlackMessageSnapshot:
        revision = SlackMessageSnapshot(
            channel_id=channel_id,
            message_ts=message_ts,
            thread_ts=thread_ts,
            revision_ts=revision_ts,
            operation=SlackMessageOperation.EDIT,
            text=text,
            user_id=user_id,
            raw_payload={"fake": True, "edited": True},
        )
        self._messages[(channel_id, thread_ts)].append(revision)
        return revision

    def delete_message(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        message_ts: str,
        revision_ts: str,
        user_id: str = "U_FAKE",
    ) -> SlackMessageSnapshot:
        revision = SlackMessageSnapshot(
            channel_id=channel_id,
            message_ts=message_ts,
            thread_ts=thread_ts,
            revision_ts=revision_ts,
            operation=SlackMessageOperation.DELETE,
            text=None,
            user_id=user_id,
            raw_payload={"fake": True, "deleted": True},
        )
        self._messages[(channel_id, thread_ts)].append(revision)
        return revision

    def hide_message_once(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        message_ts: str,
    ) -> None:
        self._hidden_once.add((channel_id, thread_ts, message_ts))

    def duplicate_message_once(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        message_ts: str,
    ) -> None:
        self._duplicate_once.add((channel_id, thread_ts, message_ts))

    def reverse_order_once(self, *, channel_id: str, thread_ts: str) -> None:
        self._reverse_once.add((channel_id, thread_ts))

    def queue_failure(self, error: SlackTransportError) -> None:
        self._failures.append(error)

    def lose_ack_after_accept_once(self) -> None:
        """Accept the next post, then simulate response loss before local receipt."""

        self._ack_loss_count += 1

    def get_permalink(self, *, channel_id: str, message_ts: str) -> str:
        self.calls.append(("chat.getPermalink", channel_id, message_ts, None))
        self._raise_failure()
        return (
            f"https://fake.slack.invalid/archives/{channel_id}/"
            f"p{message_ts.replace('.', '')}"
        )

    def conversations_history(
        self,
        *,
        channel_id: str,
        oldest: str | None = None,
        latest: str | None = None,
        inclusive: bool = True,
        page_size: int = 200,
    ) -> tuple[SlackMessageSnapshot, ...]:
        del page_size
        self.calls.append(("conversations.history", channel_id, oldest, latest))
        self._raise_failure()
        roots = [
            message
            for (candidate_channel, _), messages in self._messages.items()
            if candidate_channel == channel_id
            for message in messages
            if message.message_ts == message.thread_ts
        ]
        return tuple(
            message
            for message in roots
            if _inside_range(
                message.message_ts,
                oldest=oldest,
                latest=latest,
                inclusive=inclusive,
            )
        )

    def conversations_replies(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        oldest: str | None = None,
        latest: str | None = None,
        inclusive: bool = True,
        page_size: int = 200,
    ) -> tuple[SlackMessageSnapshot, ...]:
        del page_size
        self.calls.append(("conversations.replies", channel_id, oldest, latest))
        self._raise_failure()
        key = (channel_id, thread_ts)
        hidden = {
            candidate_message_ts
            for candidate_channel, candidate_thread, candidate_message_ts in self._hidden_once
            if (candidate_channel, candidate_thread) == key
        }
        messages = [
            message
            for message in self._messages[key]
            if message.message_ts not in hidden
            and _inside_range(
                message.message_ts,
                oldest=oldest,
                latest=latest,
                inclusive=inclusive,
            )
        ]
        self._hidden_once = {
            hidden_key for hidden_key in self._hidden_once if hidden_key[:2] != key
        }
        duplicate_ts = {
            candidate_message_ts
            for candidate_channel, candidate_thread, candidate_message_ts in self._duplicate_once
            if (candidate_channel, candidate_thread) == key
        }
        messages.extend(
            message for message in tuple(messages) if message.message_ts in duplicate_ts
        )
        self._duplicate_once = {
            duplicate_key
            for duplicate_key in self._duplicate_once
            if duplicate_key[:2] != key
        }
        if key in self._reverse_once:
            messages.reverse()
            self._reverse_once.remove(key)
        return tuple(messages)

    def post_thread_reply(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        text: str,
        client_message_id: str,
        metadata: Mapping[str, Any],
    ) -> SlackPostMessageResult:
        self.calls.append(
            ("chat.postMessage", channel_id, thread_ts, client_message_id)
        )
        self._raise_failure()
        message_ts = f"1900000000.{self._next_message_number:06d}"
        self._next_message_number += 1
        message = SlackMessageSnapshot(
            channel_id=channel_id,
            message_ts=message_ts,
            thread_ts=thread_ts,
            revision_ts=message_ts,
            operation=SlackMessageOperation.CREATE,
            text=text,
            user_id="B_TC_NOTIFICATION",
            raw_payload={
                "type": "message",
                "channel": channel_id,
                "ts": message_ts,
                "thread_ts": thread_ts,
                "text": text,
                "client_msg_id": client_message_id,
                "metadata": dict(metadata),
                "bot_id": "B_TC_NOTIFICATION",
                "app_id": "A_TC_NOTIFICATION",
            },
            client_message_id=client_message_id,
            metadata=dict(metadata),
            bot_id="B_TC_NOTIFICATION",
            app_id="A_TC_NOTIFICATION",
        )
        self._messages[(channel_id, thread_ts)].append(message)
        if self._ack_loss_count:
            self._ack_loss_count -= 1
            raise SlackAmbiguousWriteError(
                "deterministic fake accepted the post and lost its acknowledgement",
                provider_code="response_lost",
            )
        return SlackPostMessageResult(channel_id=channel_id, message_ts=message_ts)

    def find_thread_reply_by_notification(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        notification_id: str,
        client_message_id: str,
        expected_text: str,
    ) -> SlackReplyReconciliation:
        self.calls.append(
            ("find.notification", channel_id, thread_ts, client_message_id)
        )
        observed = self.conversations_replies(
            channel_id=channel_id,
            thread_ts=thread_ts,
        )
        matches: list[SlackPostMessageResult] = []
        for message in observed:
            metadata = message.metadata
            if metadata is None:
                continue
            payload = metadata.get("event_payload")
            if not isinstance(payload, Mapping):
                continue
            if (
                metadata.get("event_type")
                != "tc_generation_terminal_notification_v1"
                or payload.get("notification_id") != notification_id
                or message.client_message_id != client_message_id
                or message.channel_id != channel_id
                or message.text != expected_text
                or message.thread_ts != thread_ts
                or message.message_ts == thread_ts
                or (message.bot_id is None and message.app_id is None)
            ):
                continue
            matches.append(
                SlackPostMessageResult(
                    channel_id=message.channel_id,
                    message_ts=message.message_ts,
                )
            )
        return SlackReplyReconciliation(
            matches=tuple(sorted(matches, key=lambda match: match.message_ts))
        )

    def open_socket_mode_url(self, *, app_token: str) -> str:
        if not app_token:
            raise ValueError("Slack app token must not be empty")
        self.socket_open_calls += 1
        self._raise_failure()
        return self.socket_mode_url

    def _raise_failure(self) -> None:
        if self._failures:
            raise self._failures.pop(0)


class DeterministicFakeSocketConnection:
    def __init__(self, incoming: Iterable[str | bytes]) -> None:
        self._incoming = list(incoming)
        self.sent: list[str] = []

    async def recv(self) -> str | bytes:
        if not self._incoming:
            raise RuntimeError("fake Socket Mode connection has no pending envelope")
        return self._incoming.pop(0)

    async def send(self, message: str) -> None:
        self.sent.append(message)


def _inside_range(
    value: str,
    *,
    oldest: str | None,
    latest: str | None,
    inclusive: bool,
) -> bool:
    timestamp = Decimal(value)
    if oldest is not None:
        boundary = Decimal(oldest)
        if timestamp < boundary or (timestamp == boundary and not inclusive):
            return False
    if latest is not None:
        boundary = Decimal(latest)
        if timestamp > boundary or (timestamp == boundary and not inclusive):
            return False
    return True
