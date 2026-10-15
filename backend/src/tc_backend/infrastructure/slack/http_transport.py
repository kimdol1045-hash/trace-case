"""Slack Web API transport with pagination and bounded retry behavior."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from tc_backend.infrastructure.slack.errors import (
    SlackAmbiguousWriteError,
    SlackAuthError,
    SlackPayloadError,
    SlackPermanentWriteError,
    SlackProviderError,
    SlackRateLimitError,
)
from tc_backend.infrastructure.slack.models import (
    SlackMessageSnapshot,
    SlackPostMessageResult,
    SlackReplyReconciliation,
)
from tc_backend.infrastructure.slack.normalization import parse_slack_message

SLACK_WEB_API_BASE_URL = "https://slack.com/api"
_AUTH_PROVIDER_CODES = frozenset(
    {
        "account_inactive",
        "invalid_auth",
        "missing_scope",
        "not_allowed_token_type",
        "not_authed",
        "token_expired",
        "token_revoked",
    }
)
_AMBIGUOUS_WRITE_CODES = frozenset(
    {
        "fatal_error",
        "internal_error",
        "request_timeout",
        "service_unavailable",
    }
)
_PERMANENT_WRITE_CODES = frozenset(
    {
        "channel_not_found",
        "ekm_access_denied",
        "is_archived",
        "message_not_found",
        "missing_scope",
        "no_permission",
        "not_allowed_token_type",
        "not_in_channel",
        "restricted_action",
        "restricted_action_read_only_channel",
        "thread_locked",
        "thread_not_found",
    }
)
_SLACK_TS = re.compile(r"^[0-9]{10,12}\.[0-9]{6}$")


class SlackWebApiHttpTransport:
    """Synchronous REST transport for the Slack calls needed by Sprint 5."""

    def __init__(
        self,
        *,
        bot_token: str,
        base_url: str = SLACK_WEB_API_BASE_URL,
        client: httpx.Client | None = None,
        max_rate_limit_retries: int = 2,
        max_provider_retries: int = 2,
        provider_retry_base_seconds: float = 0.25,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not bot_token:
            raise ValueError("Slack bot token must not be empty")
        if max_rate_limit_retries < 0 or max_provider_retries < 0:
            raise ValueError("Slack retry counts must not be negative")
        if provider_retry_base_seconds < 0:
            raise ValueError("Slack retry base must not be negative")
        self._bot_token = bot_token
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=30.0)
        self._owns_client = client is None
        self._max_rate_limit_retries = max_rate_limit_retries
        self._max_provider_retries = max_provider_retries
        self._provider_retry_base_seconds = provider_retry_base_seconds
        self._sleep = sleep

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def get_permalink(self, *, channel_id: str, message_ts: str) -> str:
        if not channel_id.strip() or not _SLACK_TS.fullmatch(message_ts):
            raise ValueError("Slack permalink lookup requires a canonical message identity")
        body = self._request(
            "chat.getPermalink",
            {"channel": channel_id, "message_ts": message_ts},
        )
        permalink = body.get("permalink")
        if (
            not isinstance(permalink, str)
            or not permalink.startswith("https://")
            or len(permalink) > 2_048
            or any(character.isspace() for character in permalink)
        ):
            raise SlackProviderError(
                "Slack chat.getPermalink returned an invalid permalink"
            )
        return permalink

    def conversations_history(
        self,
        *,
        channel_id: str,
        oldest: str | None = None,
        latest: str | None = None,
        inclusive: bool = True,
        page_size: int = 200,
    ) -> tuple[SlackMessageSnapshot, ...]:
        params = _conversation_range_params(
            channel_id=channel_id,
            oldest=oldest,
            latest=latest,
            inclusive=inclusive,
            page_size=page_size,
        )
        return self._paginated_messages(
            "conversations.history",
            params=params,
            fallback_channel_id=channel_id,
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
        if not thread_ts:
            raise ValueError("Slack thread timestamp must not be empty")
        params = _conversation_range_params(
            channel_id=channel_id,
            oldest=oldest,
            latest=latest,
            inclusive=inclusive,
            page_size=page_size,
        )
        params["ts"] = thread_ts
        params["include_all_metadata"] = "true"
        return self._paginated_messages(
            "conversations.replies",
            params=params,
            fallback_channel_id=channel_id,
        )

    def post_thread_reply(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        text: str,
        client_message_id: str,
        metadata: Mapping[str, Any],
    ) -> SlackPostMessageResult:
        """Perform one irreversible write attempt with no transport-level retry."""

        if not channel_id.strip() or not _SLACK_TS.fullmatch(thread_ts):
            raise ValueError("Slack write requires a canonical channel and root timestamp")
        if not text or len(text) > 4_000:
            raise ValueError("Slack write text must contain between 1 and 4000 characters")
        request_body: dict[str, Any] = {
            "channel": channel_id,
            "thread_ts": thread_ts,
            "text": text,
            "client_msg_id": client_message_id,
            "reply_broadcast": False,
            "unfurl_links": False,
            "unfurl_media": False,
            "metadata": dict(metadata),
        }
        try:
            response = self._client.post(
                f"{self._base_url}/chat.postMessage",
                json=request_body,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self._bot_token}",
                },
            )
        except httpx.TransportError as exc:
            raise SlackAmbiguousWriteError(
                "Slack chat.postMessage transport outcome is ambiguous"
            ) from exc

        retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
        if response.status_code == 429:
            raise SlackRateLimitError(
                "Slack chat.postMessage was rate limited",
                retry_after_seconds=retry_after,
            )
        if response.status_code in {401, 403}:
            raise SlackAuthError(
                "Slack chat.postMessage rejected authorization",
                status_code=response.status_code,
            )
        if response.status_code >= 500 or response.status_code in {408, 425}:
            raise SlackAmbiguousWriteError(
                "Slack chat.postMessage provider outcome is ambiguous",
                provider_code="http_5xx" if response.status_code >= 500 else "request_timeout",
                status_code=response.status_code,
            )
        if response.status_code != 200:
            raise SlackPermanentWriteError(
                "Slack chat.postMessage was definitively rejected",
                provider_code="http_rejected",
                status_code=response.status_code,
            )
        try:
            body = _json_object(response, method="chat.postMessage")
        except SlackProviderError as exc:
            raise SlackAmbiguousWriteError(
                "Slack chat.postMessage returned an invalid acknowledgement",
                provider_code="invalid_response",
                status_code=response.status_code,
            ) from exc
        if body.get("ok") is True:
            response_channel = body.get("channel")
            message_ts = body.get("ts")
            if response_channel != channel_id or not isinstance(
                message_ts, str
            ) or not _SLACK_TS.fullmatch(message_ts) or message_ts == thread_ts:
                raise SlackAmbiguousWriteError(
                    "Slack chat.postMessage acknowledgement identity is invalid",
                    provider_code="invalid_ack",
                    status_code=response.status_code,
                )
            return SlackPostMessageResult(
                channel_id=response_channel,
                message_ts=message_ts,
            )
        raw_code = body.get("error")
        provider_code = raw_code if isinstance(raw_code, str) else "unknown_error"
        if provider_code in _AUTH_PROVIDER_CODES:
            raise SlackAuthError(
                "Slack chat.postMessage rejected authorization",
                provider_code=provider_code,
                status_code=response.status_code,
            )
        if provider_code == "ratelimited":
            raise SlackRateLimitError(
                "Slack chat.postMessage was rate limited",
                retry_after_seconds=retry_after,
            )
        if provider_code in _AMBIGUOUS_WRITE_CODES:
            raise SlackAmbiguousWriteError(
                "Slack chat.postMessage provider outcome is ambiguous",
                provider_code=provider_code,
                status_code=response.status_code,
            )
        safe_code = (
            provider_code
            if provider_code in _PERMANENT_WRITE_CODES
            else "provider_rejected"
        )
        raise SlackPermanentWriteError(
            "Slack chat.postMessage was definitively rejected",
            provider_code=safe_code,
            status_code=response.status_code,
        )

    def find_thread_reply_by_notification(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        notification_id: str,
        client_message_id: str,
        expected_text: str,
    ) -> SlackReplyReconciliation:
        """Find only exact marker, client-ID, parent, and frozen-text matches."""

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
                or message.thread_ts != thread_ts
                or message.message_ts == thread_ts
                or message.text != expected_text
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
        response = self._request("apps.connections.open", {}, token=app_token)
        url = response.get("url")
        if not isinstance(url, str) or not url.startswith("wss://"):
            raise SlackProviderError("Slack apps.connections.open omitted a secure WebSocket URL")
        return url

    def _paginated_messages(
        self,
        method: str,
        *,
        params: dict[str, str | int],
        fallback_channel_id: str,
    ) -> tuple[SlackMessageSnapshot, ...]:
        messages: list[SlackMessageSnapshot] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            page_params = dict(params)
            if cursor is not None:
                page_params["cursor"] = cursor
            body = self._request(method, page_params)
            raw_messages = body.get("messages")
            if not isinstance(raw_messages, list):
                raise SlackProviderError(f"Slack {method} response omitted messages")
            for raw_message in raw_messages:
                if not isinstance(raw_message, dict):
                    raise SlackProviderError(f"Slack {method} returned invalid message metadata")
                try:
                    messages.append(
                        parse_slack_message(
                            raw_message,
                            fallback_channel_id=fallback_channel_id,
                        )
                    )
                except SlackPayloadError as exc:
                    raise SlackProviderError(
                        f"Slack {method} returned a malformed message"
                    ) from exc
            cursor = _next_cursor(body, method=method)
            if cursor is None:
                return tuple(messages)
            if cursor in seen_cursors:
                raise SlackProviderError(f"Slack {method} repeated a pagination cursor")
            seen_cursors.add(cursor)

    def _request(
        self,
        method: str,
        form: Mapping[str, str | int],
        *,
        token: str | None = None,
    ) -> dict[str, Any]:
        rate_attempt = 0
        provider_attempt = 0
        while True:
            try:
                response = self._client.post(
                    f"{self._base_url}/{method}",
                    data=form,
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {token or self._bot_token}",
                    },
                )
            except httpx.TransportError as exc:
                if provider_attempt < self._max_provider_retries:
                    self._sleep(self._provider_backoff(provider_attempt))
                    provider_attempt += 1
                    continue
                raise SlackProviderError(
                    f"Slack {method} transport failed",
                    retryable=True,
                ) from exc

            if response.status_code == 429:
                retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
                if rate_attempt < self._max_rate_limit_retries and retry_after is not None:
                    self._sleep(retry_after)
                    rate_attempt += 1
                    continue
                raise SlackRateLimitError(
                    f"Slack {method} rate limit retries exhausted",
                    retry_after_seconds=retry_after,
                )
            if response.status_code in {401, 403}:
                raise SlackAuthError(
                    f"Slack {method} rejected authorization",
                    status_code=response.status_code,
                )
            if response.status_code >= 500:
                if provider_attempt < self._max_provider_retries:
                    self._sleep(self._provider_backoff(provider_attempt))
                    provider_attempt += 1
                    continue
                raise SlackProviderError(
                    f"Slack {method} failed with HTTP {response.status_code}",
                    status_code=response.status_code,
                    retryable=True,
                )
            if response.status_code != 200:
                raise SlackProviderError(
                    f"Slack {method} failed with HTTP {response.status_code}",
                    status_code=response.status_code,
                )

            body = _json_object(response, method=method)
            if body.get("ok") is True:
                return body
            provider_code = body.get("error")
            safe_code = provider_code if isinstance(provider_code, str) else None
            if safe_code in _AUTH_PROVIDER_CODES:
                raise SlackAuthError(
                    f"Slack {method} rejected authorization: {safe_code}",
                    provider_code=safe_code,
                    status_code=response.status_code,
                )
            if safe_code == "ratelimited":
                retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
                if rate_attempt < self._max_rate_limit_retries and retry_after is not None:
                    self._sleep(retry_after)
                    rate_attempt += 1
                    continue
                raise SlackRateLimitError(
                    f"Slack {method} rate limit retries exhausted",
                    retry_after_seconds=retry_after,
                )
            raise SlackProviderError(
                f"Slack {method} provider error: {safe_code or 'unknown_error'}",
                provider_code=safe_code,
                status_code=response.status_code,
                retryable=safe_code in {"fatal_error", "internal_error", "service_unavailable"},
            )

    def _provider_backoff(self, attempt: int) -> float:
        return self._provider_retry_base_seconds * (2**attempt)


def _conversation_range_params(
    *,
    channel_id: str,
    oldest: str | None,
    latest: str | None,
    inclusive: bool,
    page_size: int,
) -> dict[str, str | int]:
    if not channel_id:
        raise ValueError("Slack channel ID must not be empty")
    if not 1 <= page_size <= 200:
        raise ValueError("Slack page size must be between 1 and 200")
    params: dict[str, str | int] = {
        "channel": channel_id,
        "inclusive": "true" if inclusive else "false",
        "limit": page_size,
    }
    if oldest is not None:
        params["oldest"] = oldest
    if latest is not None:
        params["latest"] = latest
    return params


def _next_cursor(body: dict[str, Any], *, method: str) -> str | None:
    metadata = body.get("response_metadata")
    if metadata is None:
        return None
    if not isinstance(metadata, dict):
        raise SlackProviderError(f"Slack {method} returned invalid response_metadata")
    cursor = metadata.get("next_cursor")
    if cursor in (None, ""):
        return None
    if not isinstance(cursor, str):
        raise SlackProviderError(f"Slack {method} returned an invalid pagination cursor")
    return cursor


def _retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    if seconds < 0:
        return None
    return seconds


def _json_object(response: httpx.Response, *, method: str) -> dict[str, Any]:
    try:
        value = response.json()
    except ValueError as exc:
        raise SlackProviderError(f"Slack {method} returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise SlackProviderError(f"Slack {method} response must be a JSON object")
    return value
