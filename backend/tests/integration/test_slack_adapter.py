from __future__ import annotations

from tc_backend.infrastructure.slack.models import SlackMessageOperation
from tc_backend.infrastructure.slack.sync import SlackConversationSynchronizer
from tc_backend.infrastructure.slack.testing import DeterministicFakeSlackTransport


def test_recent_thread_resync_repairs_missing_duplicate_reversed_and_mutated_revisions() -> None:
    transport = DeterministicFakeSlackTransport()
    root = transport.add_message(
        channel_id="C123",
        thread_ts="100.000001",
        message_ts="100.000001",
        text="root",
    )
    reply = transport.add_message(
        channel_id="C123",
        thread_ts="100.000001",
        message_ts="110.000001",
        text="reply",
    )
    transport.hide_message_once(
        channel_id="C123",
        thread_ts="100.000001",
        message_ts=reply.message_ts,
    )
    synchronizer = SlackConversationSynchronizer(transport)

    first = synchronizer.sync_thread(channel_id="C123", thread_ts="100.000001")
    assert first.observed == (root,)

    transport.duplicate_message_once(
        channel_id="C123",
        thread_ts="100.000001",
        message_ts=reply.message_ts,
    )
    transport.reverse_order_once(channel_id="C123", thread_ts="100.000001")
    second = synchronizer.sync_thread(
        channel_id="C123",
        thread_ts="100.000001",
        known_revision_keys=frozenset(message.revision_key for message in first.observed),
    )
    assert [message.message_ts for message in second.observed] == ["100.000001", "110.000001"]
    assert second.new_revisions == (reply,)
    assert second.duplicate_count == 1

    edited = transport.edit_message(
        channel_id="C123",
        thread_ts="100.000001",
        message_ts="110.000001",
        revision_ts="120.000001",
        text="edited reply",
    )
    deleted = transport.delete_message(
        channel_id="C123",
        thread_ts="100.000001",
        message_ts="110.000001",
        revision_ts="130.000001",
    )
    third = synchronizer.sync_thread(
        channel_id="C123",
        thread_ts="100.000001",
        oldest="100.000001",
        latest="130.000001",
        known_revision_keys=frozenset(message.revision_key for message in second.observed),
    )

    assert third.new_revisions == (edited, deleted)
    assert [message.operation for message in third.observed[-2:]] == [
        SlackMessageOperation.EDIT,
        SlackMessageOperation.DELETE,
    ]
    assert transport.calls[-1] == (
        "conversations.replies",
        "C123",
        "100.000001",
        "130.000001",
    )
