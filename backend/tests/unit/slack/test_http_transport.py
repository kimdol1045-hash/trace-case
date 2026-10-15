from __future__ import annotations

import json
from collections.abc import Callable
from urllib.parse import parse_qs

import httpx
import pytest

from tc_backend.infrastructure.slack.errors import (
    SlackAmbiguousWriteError,
    SlackAuthError,
    SlackProviderError,
    SlackRateLimitError,
)
from tc_backend.infrastructure.slack.http_transport import SlackWebApiHttpTransport


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_permalink_lookup_uses_canonical_root_identity() -> None:
    forms: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat.getPermalink")
        forms.append(parse_qs(request.content.decode()))
        return httpx.Response(
            200,
            json={
                "ok": True,
                "permalink": "https://workspace.slack.com/archives/C123/p1700000000000001",
            },
        )

    transport = SlackWebApiHttpTransport(bot_token="xoxb-test", client=_client(handler))

    assert transport.get_permalink(
        channel_id="C123", message_ts="1700000000.000001"
    ) == "https://workspace.slack.com/archives/C123/p1700000000000001"
    assert forms == [
        {"channel": ["C123"], "message_ts": ["1700000000.000001"]}
    ]


def test_history_paginates_and_preserves_incremental_bounds() -> None:
    forms: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        forms.append(parse_qs(request.content.decode()))
        assert request.headers["Authorization"] == "Bearer xoxb-test"
        if len(forms) == 1:
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "messages": [{"type": "message", "ts": "20.000001", "text": "two"}],
                    "response_metadata": {"next_cursor": "cursor-2"},
                },
            )
        return httpx.Response(
            200,
            json={
                "ok": True,
                "messages": [{"type": "message", "ts": "10.000001", "text": "one"}],
                "response_metadata": {"next_cursor": ""},
            },
        )

    transport = SlackWebApiHttpTransport(
        bot_token="xoxb-test",
        client=_client(handler),
        max_provider_retries=0,
    )
    messages = transport.conversations_history(
        channel_id="C123",
        oldest="10.000000",
        latest="30.000000",
        page_size=100,
    )

    assert [message.message_ts for message in messages] == ["20.000001", "10.000001"]
    assert forms[0] == {
        "channel": ["C123"],
        "inclusive": ["true"],
        "latest": ["30.000000"],
        "limit": ["100"],
        "oldest": ["10.000000"],
    }
    assert forms[1]["cursor"] == ["cursor-2"]


def test_replies_paginates_with_thread_timestamp() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        assert form["channel"] == ["C123"]
        assert form["ts"] == ["10.000001"]
        return httpx.Response(
            200,
            json={
                "ok": True,
                "messages": [
                    {
                        "type": "message",
                        "ts": "11.000001",
                        "thread_ts": "10.000001",
                        "text": "reply",
                        "edited": {"ts": "12.000001"},
                    }
                ],
            },
        )

    transport = SlackWebApiHttpTransport(bot_token="xoxb-test", client=_client(handler))
    messages = transport.conversations_replies(channel_id="C123", thread_ts="10.000001")

    assert messages[0].thread_ts == "10.000001"
    assert messages[0].revision_ts == "12.000001"
    assert messages[0].operation.value == "edit"


def test_retry_after_is_honored_then_request_succeeds() -> None:
    sleeps: list[float] = []
    call_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={"ok": False})
        return httpx.Response(200, json={"ok": True, "messages": []})

    transport = SlackWebApiHttpTransport(
        bot_token="xoxb-test",
        client=_client(handler),
        sleep=sleeps.append,
        max_rate_limit_retries=1,
    )

    assert transport.conversations_history(channel_id="C123") == ()
    assert sleeps == [2.0]


def test_rate_auth_and_provider_failures_are_typed() -> None:
    rate_transport = SlackWebApiHttpTransport(
        bot_token="xoxb-test",
        client=_client(
            lambda _: httpx.Response(
                429,
                headers={"Retry-After": "3"},
                json={"ok": False},
            )
        ),
        max_rate_limit_retries=0,
    )
    with pytest.raises(SlackRateLimitError) as rate_error:
        rate_transport.conversations_history(channel_id="C123")
    assert rate_error.value.retry_after_seconds == 3

    auth_transport = SlackWebApiHttpTransport(
        bot_token="xoxb-test",
        client=_client(lambda _: httpx.Response(200, json={"ok": False, "error": "invalid_auth"})),
    )
    with pytest.raises(SlackAuthError) as auth_error:
        auth_transport.conversations_history(channel_id="C123")
    assert auth_error.value.provider_code == "invalid_auth"

    provider_transport = SlackWebApiHttpTransport(
        bot_token="xoxb-test",
        client=_client(lambda _: httpx.Response(503, json={"ok": False})),
        max_provider_retries=0,
    )
    with pytest.raises(SlackProviderError) as provider_error:
        provider_transport.conversations_history(channel_id="C123")
    assert provider_error.value.retryable is True


def test_post_thread_reply_is_one_json_write_without_hidden_retry() -> None:
    requests: list[httpx.Request] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503, json={"ok": False, "error": "internal_error"})

    transport = SlackWebApiHttpTransport(
        bot_token="xoxb-test",
        client=_client(handler),
        max_rate_limit_retries=9,
        max_provider_retries=9,
        sleep=sleeps.append,
    )

    with pytest.raises(SlackAmbiguousWriteError):
        transport.post_thread_reply(
            channel_id="C123",
            thread_ts="1700000000.000001",
            text="frozen terminal fact",
            client_message_id="11111111-1111-4111-8111-111111111111",
            metadata={"event_type": "tc_generation_terminal_notification_v1"},
        )

    assert len(requests) == 1
    assert requests[0].url.path.endswith("/chat.postMessage")
    assert requests[0].headers["content-type"].startswith("application/json")
    assert json.loads(requests[0].content) == {
        "channel": "C123",
        "thread_ts": "1700000000.000001",
        "text": "frozen terminal fact",
        "client_msg_id": "11111111-1111-4111-8111-111111111111",
        "reply_broadcast": False,
        "unfurl_links": False,
        "unfurl_media": False,
        "metadata": {"event_type": "tc_generation_terminal_notification_v1"},
    }
    assert sleeps == []


def test_post_thread_reply_returns_retry_after_without_sleeping_or_retrying() -> None:
    requests: list[httpx.Request] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            429,
            headers={"Retry-After": "7"},
            json={"ok": False, "error": "ratelimited"},
        )

    transport = SlackWebApiHttpTransport(
        bot_token="xoxb-test",
        client=_client(handler),
        max_rate_limit_retries=9,
        sleep=sleeps.append,
    )
    with pytest.raises(SlackRateLimitError) as error:
        transport.post_thread_reply(
            channel_id="C123",
            thread_ts="1700000000.000001",
            text="frozen terminal fact",
            client_message_id="11111111-1111-4111-8111-111111111111",
            metadata={"event_type": "tc_generation_terminal_notification_v1"},
        )

    assert error.value.retry_after_seconds == 7
    assert len(requests) == 1
    assert sleeps == []


def test_reconciliation_requires_exact_marker_client_parent_and_text() -> None:
    forms: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        forms.append(parse_qs(request.content.decode()))
        marker = {
            "event_type": "tc_generation_terminal_notification_v1",
            "event_payload": {"notification_id": "generation_slack_notification_abc"},
        }
        return httpx.Response(
            200,
            json={
                "ok": True,
                "messages": [
                    {
                        "type": "message",
                        "channel": "C123",
                        "ts": "1700000001.000001",
                        "thread_ts": "1700000000.000001",
                        "text": "frozen terminal fact",
                        "client_msg_id": "11111111-1111-4111-8111-111111111111",
                        "metadata": marker,
                        "bot_id": "B_TC_NOTIFICATION",
                    },
                    {
                        "type": "message",
                        "channel": "C123",
                        "ts": "1700000002.000001",
                        "thread_ts": "1700000000.000001",
                        "text": "modified text",
                        "client_msg_id": "11111111-1111-4111-8111-111111111111",
                        "metadata": marker,
                        "bot_id": "B_TC_NOTIFICATION",
                    },
                    {
                        "type": "message",
                        "channel": "C_WRONG",
                        "ts": "1700000003.000001",
                        "thread_ts": "1700000000.000001",
                        "text": "frozen terminal fact",
                        "client_msg_id": "11111111-1111-4111-8111-111111111111",
                        "metadata": marker,
                        "bot_id": "B_TC_NOTIFICATION",
                    },
                ],
            },
        )

    transport = SlackWebApiHttpTransport(bot_token="xoxb-test", client=_client(handler))
    result = transport.find_thread_reply_by_notification(
        channel_id="C123",
        thread_ts="1700000000.000001",
        notification_id="generation_slack_notification_abc",
        client_message_id="11111111-1111-4111-8111-111111111111",
        expected_text="frozen terminal fact",
    )

    assert [match.message_ts for match in result.matches] == ["1700000001.000001"]
    assert forms == [
        {
            "channel": ["C123"],
            "inclusive": ["true"],
            "include_all_metadata": ["true"],
            "limit": ["200"],
            "ts": ["1700000000.000001"],
        }
    ]
