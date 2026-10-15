"""Narrow Slack Web API contract used by ingestion and thread reconciliation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from tc_backend.infrastructure.slack.models import (
    SlackMessageSnapshot,
    SlackPostMessageResult,
    SlackReplyReconciliation,
)


class SlackWebApiTransport(Protocol):
    def get_permalink(self, *, channel_id: str, message_ts: str) -> str: ...

    def conversations_history(
        self,
        *,
        channel_id: str,
        oldest: str | None = None,
        latest: str | None = None,
        inclusive: bool = True,
        page_size: int = 200,
    ) -> tuple[SlackMessageSnapshot, ...]: ...

    def conversations_replies(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        oldest: str | None = None,
        latest: str | None = None,
        inclusive: bool = True,
        page_size: int = 200,
    ) -> tuple[SlackMessageSnapshot, ...]: ...

    def post_thread_reply(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        text: str,
        client_message_id: str,
        metadata: Mapping[str, Any],
    ) -> SlackPostMessageResult: ...

    def find_thread_reply_by_notification(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        notification_id: str,
        client_message_id: str,
        expected_text: str,
    ) -> SlackReplyReconciliation: ...

    def open_socket_mode_url(self, *, app_token: str) -> str: ...
