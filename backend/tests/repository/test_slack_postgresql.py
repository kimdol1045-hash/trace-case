from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from tc_backend.db.errors import SlackChannelDeniedError, SlackPrivateChannelDeniedError
from tc_backend.db.models import (
    AuditLog,
    BackgroundJob,
    OutboxEvent,
    SlackEvent,
    SlackMessage,
    SlackMessageRevision,
    SlackParseResult,
    SlackThread,
    SlackThreadSyncStatus,
    SlackWorkItem,
)
from tc_backend.db.slack import SlackEventInput, SlackMessageSnapshot, SlackRepository

pytestmark = pytest.mark.postgres

SLACK_WORKSPACE = "T_SPRINT5"
CHANNEL = "C_ALLOWLISTED"


def _event(
    event_id: str,
    event_type: str,
    event_ts: str,
    message_ts: str,
    body: str | None,
    *,
    thread_ts: str | None = None,
    revision_ts: str | None = None,
    channel_id: str = CHANNEL,
    private: bool = False,
) -> SlackEventInput:
    return SlackEventInput(
        event_id=event_id,
        slack_workspace_id=SLACK_WORKSPACE,
        channel_id=channel_id,
        event_type=event_type,
        event_ts=event_ts,
        message_ts=message_ts,
        thread_ts=thread_ts,
        revision_ts=revision_ts,
        body=body,
        author_id="U_TEST",
        permalink=f"https://example.slack.com/archives/{channel_id}/p{message_ts.replace('.', '')}",
        channel_is_private=private,
        raw_payload={"event_id": event_id, "type": event_type},
    )


def _allow(sessions: sessionmaker[Session]) -> None:
    with sessions.begin() as session:
        SlackRepository(session).put_channel(
            slack_workspace_id=SLACK_WORKSPACE,
            channel_id=CHANNEL,
            channel_name="qa-work-items",
        )


def test_slack_migration_installs_fk_unique_check_and_source_guards(
    migrated_engine: Engine,
) -> None:
    with migrated_engine.connect() as connection:
        constraints = set(
            connection.scalars(
                text(
                    "SELECT conname FROM pg_constraint WHERE conrelid IN ("
                    "'slack_channel_allowlist'::regclass, 'slack_events'::regclass, "
                    "'slack_work_items'::regclass, 'slack_threads'::regclass, "
                    "'slack_messages'::regclass, 'slack_message_revisions'::regclass, "
                    "'slack_parse_results'::regclass)"
                )
            )
        )
        triggers = set(
            connection.scalars(
                text(
                    "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgrelid IN ("
                    "'slack_events'::regclass, 'slack_work_items'::regclass, "
                    "'slack_threads'::regclass, 'slack_messages'::regclass, "
                    "'slack_message_revisions'::regclass, 'slack_parse_results'::regclass)"
                )
            )
        )

    assert {
        "fk_slack_events_allowlisted_channel",
        "uq_slack_events_workspace_event_id",
        "uq_slack_events_workspace_channel_event_ts",
        "uq_slack_work_items_workspace_channel_message",
        "uq_slack_messages_thread_message",
        "uq_slack_message_revisions_message_revision",
        "ck_slack_channel_allowlist_private_explicit",
        "ck_slack_message_revisions_tombstone_shape",
    }.issubset(constraints)
    assert {
        "trg_slack_events_guard",
        "trg_slack_work_items_guard",
        "trg_slack_threads_guard",
        "trg_slack_messages_shape",
        "trg_slack_message_revisions_append_only",
        "trg_slack_parse_results_append_only",
    }.issubset(triggers)


def test_allowlist_denies_unknown_and_private_by_default(migrated_engine: Engine) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    with sessions.begin() as session:
        SlackRepository(session).put_channel(
            slack_workspace_id=SLACK_WORKSPACE,
            channel_id="C_PRIVATE",
            is_private=True,
        )

    with pytest.raises(SlackPrivateChannelDeniedError):
        with sessions.begin() as session:
            SlackRepository(session).ingest_event(
                _event(
                    "Ev-private-default-deny",
                    "ROOT",
                    "1724200000.000001",
                    "1724200000.000000",
                    "private denied unless explicit",
                    channel_id="C_PRIVATE",
                    private=True,
                )
            )

    with pytest.raises(SlackChannelDeniedError):
        with sessions.begin() as session:
            SlackRepository(session).ingest_event(
                _event(
                    "Ev-denied",
                    "ROOT",
                    "1724200000.000001",
                    "1724200000.000000",
                    "denied",
                    channel_id="C_UNKNOWN",
                )
            )

    with sessions.begin() as session:
        SlackRepository(session).put_channel(
            slack_workspace_id=SLACK_WORKSPACE,
            channel_id="C_PRIVATE",
            is_private=True,
            include_private=True,
        )
        accepted = SlackRepository(session).ingest_event(
            _event(
                "Ev-private",
                "ROOT",
                "1724200001.000001",
                "1724200001.000000",
                "explicitly allowed private source",
                channel_id="C_PRIVATE",
                private=True,
            )
        )
        assert not accepted.duplicate


def test_same_event_three_times_has_one_atomic_ledger_change(migrated_engine: Engine) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    _allow(sessions)
    root = _event(
        "Ev-root",
        "ROOT",
        "1724200100.000100",
        "1724200100.000000",
        "password reset work item",
    )
    outcomes = []
    for _ in range(3):
        with sessions.begin() as session:
            outcomes.append(SlackRepository(session).ingest_event(root))
    assert [result.duplicate for result in outcomes] == [False, True, True]
    assert len({result.work_item_id for result in outcomes}) == 1
    assert len({result.revision_id for result in outcomes}) == 1
    assert len({result.quiet_job_id for result in outcomes}) == 1

    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(SlackEvent)) == 1
        assert session.scalar(select(func.count()).select_from(SlackWorkItem)) == 1
        assert session.scalar(select(func.count()).select_from(SlackThread)) == 1
        assert session.scalar(select(func.count()).select_from(SlackMessage)) == 1
        assert session.scalar(select(func.count()).select_from(SlackMessageRevision)) == 1
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 1
        assert session.scalar(select(func.count()).select_from(BackgroundJob)) == 1
        assert session.scalar(
            select(func.count()).select_from(AuditLog).where(
                AuditLog.entity_type == "SLACK_WORK_ITEM"
            )
        ) == 1
        job = session.scalar(select(BackgroundJob))
        assert job is not None
        assert job.job_type == "SLACK_QUIET_WINDOW"
        assert job.payload["sourceVersion"] == 1


def test_parse_snapshot_and_quiet_window_update_and_cancel(migrated_engine: Engine) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    _allow(sessions)
    with sessions.begin() as session:
        ingested = SlackRepository(session).ingest_event(
            _event(
                "Ev-parse-root",
                "ROOT",
                "1724200150.000100",
                "1724200150.000000",
                "parse this source",
            )
        )
        assert ingested.work_item_id is not None
        assert ingested.quiet_job_id is not None
        work_item_id = ingested.work_item_id
        quiet_job_id = ingested.quiet_job_id

    with sessions.begin() as session:
        repository = SlackRepository(session)
        stored = repository.store_parse_result(
            work_item_id=work_item_id,
            source_version=1,
            parser_version="deterministic-v1",
            status="NEEDS_REVIEW",
            result={"developmentItem": "parse this source"},
            missing_fields=("requestedMenu",),
            confidence="0.7500",
        )
        assert stored.source_version == 1

    with sessions() as session:
        work_item = session.get(SlackWorkItem, work_item_id)
        job = session.get(BackgroundJob, quiet_job_id)
        assert work_item is not None and job is not None
        assert work_item.parse_status == "NEEDS_REVIEW"
        assert work_item.quiet_due_at is not None
        assert work_item.last_activity_at is not None
        assert work_item.quiet_due_at - work_item.last_activity_at == timedelta(minutes=10)
        assert job.run_after == work_item.quiet_due_at
        parse_result_id = session.scalar(select(SlackParseResult.id))
        assert parse_result_id is not None

    with pytest.raises(DBAPIError, match="append-only"):
        with sessions.begin() as session:
            session.execute(
                update(SlackParseResult)
                .where(SlackParseResult.id == parse_result_id)
                .values(result={"tampered": True})
            )

    with sessions.begin() as session:
        repository = SlackRepository(session)
        assert repository.cancel_quiet_window(work_item_id=work_item_id)
        assert not repository.quiet_job_is_current(
            work_item_id=work_item_id,
            job_id=quiet_job_id,
            source_version=1,
        )

    with sessions() as session:
        work_item = session.get(SlackWorkItem, work_item_id)
        job = session.get(BackgroundJob, quiet_job_id)
        assert work_item is not None and work_item.quiet_job_id is None
        assert job is not None and job.status == "SUCCEEDED"


def test_reply_first_and_reversed_edit_delete_preserve_ordered_source(
    migrated_engine: Engine,
) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    _allow(sessions)
    inputs = (
        _event(
            "Ev-reply-first",
            "REPLY",
            "1724200202.000100",
            "1724200202.000000",
            "original reply",
            thread_ts="1724200200.000000",
        ),
        _event(
            "Ev-delete-arrives-first",
            "DELETE",
            "1724200207.000000",
            "1724200202.000000",
            None,
            thread_ts="1724200200.000000",
            revision_ts="1724200206.000000",
        ),
        _event(
            "Ev-edit-arrives-late",
            "EDIT",
            "1724200205.000000",
            "1724200202.000000",
            "edited reply",
            thread_ts="1724200200.000000",
            revision_ts="1724200204.000000",
        ),
        _event(
            "Ev-root-late",
            "ROOT",
            "1724200208.000000",
            "1724200200.000000",
            "root source",
        ),
    )
    for item in inputs:
        with sessions.begin() as session:
            SlackRepository(session).ingest_event(item)

    with sessions() as session:
        work_item = session.scalar(select(SlackWorkItem))
        assert work_item is not None
        assert work_item.root_seen
        assert work_item.source_version == 4
        assert work_item.last_activity_ts == "1724200206.000000"
        messages = list(session.scalars(select(SlackMessage).order_by(SlackMessage.message_ts_key)))
        assert [message.is_root for message in messages] == [True, False]
        reply_history = list(
            session.scalars(
                select(SlackMessageRevision)
                .where(SlackMessageRevision.message_id == messages[1].id)
                .order_by(SlackMessageRevision.revision)
            )
        )
        assert [revision.body for revision in reply_history] == [
            "original reply",
            "edited reply",
            None,
        ]
        assert [revision.is_deleted for revision in reply_history] == [False, False, True]
        assert session.scalar(select(func.count()).select_from(BackgroundJob)) == 1
        job = session.scalar(select(BackgroundJob))
        assert job is not None and job.payload["sourceVersion"] == 4
        revision_id = reply_history[0].id

    with pytest.raises(DBAPIError, match="append-only"):
        with sessions.begin() as session:
            session.execute(
                update(SlackMessageRevision)
                .where(SlackMessageRevision.id == revision_id)
                .values(body="tampered")
            )


def test_thread_sync_fills_missing_reply_and_tracks_watermark_without_duplicates(
    migrated_engine: Engine,
) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    _allow(sessions)
    with sessions.begin() as session:
        result = SlackRepository(session).ingest_event(
            _event(
                "Ev-sync-root",
                "ROOT",
                "1724200300.000100",
                "1724200300.000000",
                "root",
            )
        )
        assert result.thread_id is not None
        thread_id = result.thread_id

    missing = SlackMessageSnapshot(
        message_ts="1724200301.000000",
        revision_ts="1724200301.000000",
        body="reply missed by Events API",
        author_id="U_SYNC",
        raw_payload={"source": "conversations.replies"},
    )
    with sessions.begin() as session:
        repository = SlackRepository(session)
        repository.begin_thread_sync(thread_id=thread_id)
        first = repository.merge_thread_page(
            thread_id=thread_id,
            messages=(missing,),
            watermark_ts="1724200301.000000",
        )
        assert len(first.inserted_revision_ids) == 1
        assert first.status is SlackThreadSyncStatus.SYNCED

    with sessions.begin() as session:
        repository = SlackRepository(session)
        repository.begin_thread_sync(thread_id=thread_id)
        duplicate = repository.merge_thread_page(
            thread_id=thread_id,
            messages=(missing,),
            watermark_ts="1724200301.000000",
        )
        assert duplicate.inserted_revision_ids == ()

    with sessions() as session:
        thread = session.get(SlackThread, thread_id)
        assert thread is not None
        assert thread.sync_watermark_ts == "1724200301.000000"
        assert thread.last_synced_at is not None
        assert thread.sync_attempts == 0
        assert session.scalar(select(func.count()).select_from(SlackMessageRevision)) == 2
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 2
        assert session.scalar(select(func.count()).select_from(BackgroundJob)) == 1

    with sessions.begin() as session:
        first_jobs = SlackRepository(session).enqueue_recent_thread_syncs(
            recent_since=datetime(2020, 1, 1, tzinfo=UTC),
            synced_before=datetime.now(UTC) + timedelta(minutes=1),
        )
    with sessions.begin() as session:
        repeated_jobs = SlackRepository(session).enqueue_recent_thread_syncs(
            recent_since=datetime(2020, 1, 1, tzinfo=UTC),
            synced_before=datetime.now(UTC) + timedelta(minutes=1),
        )
    assert first_jobs == repeated_jobs
    assert len(first_jobs) == 1
    with sessions() as session:
        assert session.scalar(
            select(func.count()).select_from(BackgroundJob).where(
                BackgroundJob.job_type == "SLACK_THREAD_SYNC"
            )
        ) == 1


def test_thread_sync_rechecks_revoked_private_channel_access_before_network(
    migrated_engine: Engine,
) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    _allow(sessions)
    with sessions.begin() as session:
        result = SlackRepository(session).ingest_event(
            _event(
                "Ev-sync-private-recheck",
                "ROOT",
                "1724200350.000100",
                "1724200350.000000",
                "root",
            )
        )
        assert result.thread_id is not None
        thread_id = result.thread_id
        SlackRepository(session).put_channel(
            slack_workspace_id=SLACK_WORKSPACE,
            channel_id=CHANNEL,
            is_private=True,
            include_private=False,
        )

    with pytest.raises(SlackPrivateChannelDeniedError, match="sync is disabled"):
        with sessions.begin() as session:
            SlackRepository(session).begin_thread_sync(thread_id=thread_id)


def test_rate_limit_retry_reaches_visible_dead_state(migrated_engine: Engine) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    _allow(sessions)
    with sessions.begin() as session:
        result = SlackRepository(session).ingest_event(
            _event(
                "Ev-rate-root",
                "ROOT",
                "1724200400.000100",
                "1724200400.000000",
                "root",
            )
        )
        assert result.thread_id is not None
        thread = session.get(SlackThread, result.thread_id)
        assert thread is not None
        thread.max_sync_attempts = 2
        thread_id = thread.id

    with sessions.begin() as session:
        repository = SlackRepository(session)
        repository.begin_thread_sync(thread_id=thread_id)
        retry = repository.record_thread_sync_failure(
            thread_id=thread_id,
            error="Slack rate_limited",
            retry_after=timedelta(seconds=30),
        )
        assert retry.status is SlackThreadSyncStatus.RETRY
        assert retry.retry_at is not None
        assert retry.job_id is not None

    with sessions.begin() as session:
        repository = SlackRepository(session)
        repository.begin_thread_sync(thread_id=thread_id)
        dead = repository.record_thread_sync_failure(
            thread_id=thread_id,
            error="Slack rate_limited again",
            retry_after=timedelta(seconds=30),
        )
        assert dead.status is SlackThreadSyncStatus.DEAD
        assert dead.retry_at is None
        assert dead.job_id is None

    with sessions() as session:
        thread = session.get(SlackThread, thread_id)
        assert thread is not None
        assert thread.sync_status == "DEAD"
        assert thread.sync_attempts == 2
        assert thread.last_error == "Slack rate_limited again"


def test_ingest_rollback_removes_event_revision_audit_outbox_and_job(
    migrated_engine: Engine,
) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    _allow(sessions)
    event = _event(
        "Ev-rollback",
        "ROOT",
        "1724200500.000100",
        "1724200500.000000",
        "roll me back",
    )
    with pytest.raises(RuntimeError, match="inject rollback"):
        with sessions.begin() as session:
            SlackRepository(session).ingest_event(event)
            raise RuntimeError("inject rollback")

    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(SlackEvent)) == 0
        assert session.scalar(select(func.count()).select_from(SlackWorkItem)) == 0
        assert session.scalar(select(func.count()).select_from(SlackMessageRevision)) == 0
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0
        assert session.scalar(select(func.count()).select_from(BackgroundJob)) == 0
        assert session.scalar(
            select(func.count()).select_from(AuditLog).where(
                AuditLog.entity_type == "SLACK_WORK_ITEM"
            )
        ) == 0


def test_concurrent_first_replies_share_one_work_item_and_quiet_job(
    migrated_engine: Engine,
) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    _allow(sessions)
    barrier = Barrier(2)

    def ingest(index: int) -> uuid.UUID | None:
        with sessions.begin() as session:
            barrier.wait()
            result = SlackRepository(session).ingest_event(
                _event(
                    f"Ev-concurrent-{index}",
                    "REPLY",
                    f"172420060{index}.000100",
                    f"172420060{index}.000000",
                    f"reply {index}",
                    thread_ts="1724200600.000000",
                )
            )
            return result.work_item_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        work_item_ids = list(pool.map(ingest, (1, 2)))
    assert work_item_ids[0] == work_item_ids[1]

    with sessions() as session:
        work_item = session.scalar(select(SlackWorkItem))
        assert work_item is not None
        assert work_item.source_version == 2
        assert not work_item.root_seen
        assert session.scalar(select(func.count()).select_from(SlackThread)) == 1
        assert session.scalar(select(func.count()).select_from(SlackMessage)) == 2
        assert session.scalar(select(func.count()).select_from(SlackMessageRevision)) == 2
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 2
        assert session.scalar(select(func.count()).select_from(BackgroundJob)) == 1
