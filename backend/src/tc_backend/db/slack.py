"""Transactional Slack source ledger and PostgreSQL scheduling boundary.

The transport layer is intentionally absent from this module.  HTTP Events API,
Socket Mode, and thread-sync adapters normalize Slack data into the immutable
inputs below; this repository owns allowlisting, idempotency, source ordering,
audit/outbox writes, and background-job scheduling in the caller's transaction.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from tc_backend.db.audit import AuditContext, append_audit
from tc_backend.db.constants import DEFAULT_WORKSPACE_ID
from tc_backend.db.errors import (
    ResourceNotFoundError,
    SlackChannelDeniedError,
    SlackPrivateChannelDeniedError,
    SlackThreadSyncError,
)
from tc_backend.db.jobs import JobRepository
from tc_backend.db.models import (
    AuditSourceType,
    BackgroundJob,
    JobStatus,
    OutboxEvent,
    OutboxStatus,
    SlackChannelAllowlist,
    SlackEvent,
    SlackEventStatus,
    SlackEventType,
    SlackMessage,
    SlackMessageRevision,
    SlackParseResult,
    SlackParseStatus,
    SlackThread,
    SlackThreadSyncStatus,
    SlackWorkItem,
    WorkspaceSettings,
)

_TS_PATTERN = re.compile(r"^(?P<seconds>[0-9]{10,12})(?:\.(?P<fraction>[0-9]{1,6}))?$")


@dataclass(frozen=True, slots=True)
class SlackEventInput:
    event_id: str
    slack_workspace_id: str
    channel_id: str
    event_type: SlackEventType | str
    event_ts: str
    message_ts: str
    body: str | None
    thread_ts: str | None = None
    revision_ts: str | None = None
    author_id: str | None = None
    permalink: str | None = None
    channel_is_private: bool = False
    raw_payload: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SlackMessageSnapshot:
    message_ts: str
    body: str | None
    revision_ts: str | None = None
    author_id: str | None = None
    permalink: str | None = None
    is_deleted: bool = False
    edited_at: datetime | None = None
    deleted_at: datetime | None = None
    raw_payload: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SlackIngestResult:
    event_id: uuid.UUID
    work_item_id: uuid.UUID | None
    thread_id: uuid.UUID | None
    message_id: uuid.UUID | None
    revision_id: uuid.UUID | None
    outbox_id: uuid.UUID | None
    quiet_job_id: uuid.UUID | None
    duplicate: bool


@dataclass(frozen=True, slots=True)
class SlackEventRegistration:
    event_id: uuid.UUID
    processing_job_id: uuid.UUID
    duplicate: bool
    duplicate_event_id: bool


@dataclass(frozen=True, slots=True)
class ThreadSyncResult:
    thread_id: uuid.UUID
    inserted_revision_ids: tuple[uuid.UUID, ...]
    watermark_ts: str | None
    next_cursor: str | None
    status: SlackThreadSyncStatus


@dataclass(frozen=True, slots=True)
class ThreadSyncFailurePlan:
    status: SlackThreadSyncStatus
    attempt: int
    max_attempts: int
    retry_at: datetime | None
    job_id: uuid.UUID | None


class SlackRepository:
    """Slack source operations scoped to one database transaction.

    The repository never commits.  Callers use ``with session.begin():`` so a
    delivery, revision, work-item projection, audit, outbox row, and quiet job
    either all commit or all roll back.
    """

    def __init__(self, session: Session, *, workspace_id: uuid.UUID = DEFAULT_WORKSPACE_ID) -> None:
        self.session = session
        self.workspace_id = workspace_id

    def put_channel(
        self,
        *,
        slack_workspace_id: str,
        channel_id: str,
        channel_name: str | None = None,
        is_private: bool = False,
        include_private: bool = False,
        enabled: bool = True,
    ) -> SlackChannelAllowlist:
        """Create or update an allowlist entry; private collection is opt-in."""

        slack_workspace_id = _nonblank(slack_workspace_id, "slack_workspace_id", 80)
        channel_id = _nonblank(channel_id, "channel_id", 80)
        channel_name = _optional_nonblank(channel_name, "channel_name", 200)
        entry_id = uuid.uuid4()
        statement = (
            insert(SlackChannelAllowlist)
            .values(
                id=entry_id,
                workspace_id=self.workspace_id,
                slack_workspace_id=slack_workspace_id,
                channel_id=channel_id,
                channel_name=channel_name,
                is_private=is_private,
                include_private=include_private,
                enabled=enabled,
            )
            .on_conflict_do_update(
                constraint="uq_slack_channel_allowlist_workspace_channel",
                set_={
                    "channel_name": channel_name,
                    "is_private": is_private,
                    "include_private": include_private,
                    "enabled": enabled,
                    "updated_at": func.now(),
                },
            )
            .returning(SlackChannelAllowlist.id)
        )
        resolved_id = self.session.execute(statement).scalar_one()
        return self.session.execute(
            select(SlackChannelAllowlist).where(SlackChannelAllowlist.id == resolved_id)
        ).scalar_one()

    upsert_channel = put_channel
    allow_channel = put_channel

    def set_channel_enabled(
        self,
        *,
        slack_workspace_id: str,
        channel_id: str,
        enabled: bool,
    ) -> SlackChannelAllowlist:
        entry = self._channel(
            slack_workspace_id=slack_workspace_id,
            channel_id=channel_id,
            for_update=True,
        )
        if entry is None:  # pragma: no cover - required=True raises first
            raise SlackChannelDeniedError("Slack channel is not allowlisted")
        entry.enabled = enabled
        entry.updated_at = datetime.now(UTC)
        self.session.flush()
        return entry

    def ingest_event(
        self,
        event: SlackEventInput,
        *,
        audit: AuditContext | None = None,
    ) -> SlackIngestResult:
        normalized = _normalize_event(event)
        channel = self._require_allowed_channel(
            slack_workspace_id=normalized.slack_workspace_id,
            channel_id=normalized.channel_id,
            event_says_private=normalized.channel_is_private,
        )
        if channel.is_private and not channel.include_private:
            raise SlackPrivateChannelDeniedError("private Slack channel collection is disabled")

        delivery_id = uuid.uuid4()
        inserted_id = self.session.execute(
            insert(SlackEvent)
            .values(
                id=delivery_id,
                workspace_id=self.workspace_id,
                slack_workspace_id=normalized.slack_workspace_id,
                channel_id=normalized.channel_id,
                event_id=normalized.event_id,
                event_type=SlackEventType(normalized.event_type).value,
                event_ts=normalized.event_ts,
                event_ts_key=_ts_key(normalized.event_ts),
                payload=dict(normalized.raw_payload or {}),
                status=SlackEventStatus.RECEIVED.value,
            )
            .on_conflict_do_nothing()
            .returning(SlackEvent.id)
        ).scalar_one_or_none()
        if inserted_id is None:
            existing = self.session.execute(
                select(SlackEvent).where(
                    SlackEvent.workspace_id == self.workspace_id,
                    or_(
                        SlackEvent.event_id == normalized.event_id,
                        and_(
                            SlackEvent.slack_workspace_id == normalized.slack_workspace_id,
                            SlackEvent.channel_id == normalized.channel_id,
                            SlackEvent.event_ts == normalized.event_ts,
                        ),
                    ),
                )
            ).scalar_one()
            return self._duplicate_result(existing=existing, event=normalized)

        delivery = self.session.execute(
            select(SlackEvent).where(SlackEvent.id == inserted_id)
        ).scalar_one()
        return self._process_delivery(delivery=delivery, normalized=normalized, audit=audit)

    def register_event(
        self,
        event: SlackEventInput,
        *,
        max_attempts: int = 5,
    ) -> SlackEventRegistration:
        """Persist an authenticated delivery and its processing job atomically."""

        normalized = _normalize_event(event)
        channel = self._require_allowed_channel(
            slack_workspace_id=normalized.slack_workspace_id,
            channel_id=normalized.channel_id,
            event_says_private=normalized.channel_is_private,
        )
        if channel.is_private and not channel.include_private:
            raise SlackPrivateChannelDeniedError("private Slack channel collection is disabled")
        candidate_id = uuid.uuid4()
        inserted_id = self.session.execute(
            insert(SlackEvent)
            .values(
                id=candidate_id,
                workspace_id=self.workspace_id,
                slack_workspace_id=normalized.slack_workspace_id,
                channel_id=normalized.channel_id,
                event_id=normalized.event_id,
                event_type=SlackEventType(normalized.event_type).value,
                event_ts=normalized.event_ts,
                event_ts_key=_ts_key(normalized.event_ts),
                payload=dict(normalized.raw_payload or {}),
                status=SlackEventStatus.RECEIVED.value,
            )
            .on_conflict_do_nothing()
            .returning(SlackEvent.id)
        ).scalar_one_or_none()
        duplicate = inserted_id is None
        if inserted_id is None:
            existing = self.session.execute(
                select(SlackEvent).where(
                    SlackEvent.workspace_id == self.workspace_id,
                    or_(
                        SlackEvent.event_id == normalized.event_id,
                        and_(
                            SlackEvent.slack_workspace_id == normalized.slack_workspace_id,
                            SlackEvent.channel_id == normalized.channel_id,
                            SlackEvent.event_ts == normalized.event_ts,
                        ),
                    ),
                )
            ).scalar_one()
            inserted_id = existing.id
            duplicate_event_id = existing.event_id == normalized.event_id
        else:
            duplicate_event_id = False
        job_id = JobRepository(self.session).enqueue(
            job_type="SLACK_EVENT_PROCESS",
            payload={"eventLogId": str(inserted_id)},
            max_attempts=max_attempts,
            idempotency_key=f"slack-event-process:{inserted_id}",
        )
        return SlackEventRegistration(
            event_id=inserted_id,
            processing_job_id=job_id,
            duplicate=duplicate,
            duplicate_event_id=duplicate_event_id,
        )

    def process_registered_event(
        self,
        event: SlackEventInput,
        *,
        audit: AuditContext | None = None,
    ) -> SlackIngestResult:
        """Project an already registered delivery into the append-only ledger."""

        normalized = _normalize_event(event)
        delivery = self.session.execute(
            select(SlackEvent)
            .where(
                SlackEvent.workspace_id == self.workspace_id,
                SlackEvent.event_id == normalized.event_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if delivery is None:
            raise ResourceNotFoundError(
                "SLACK_EVENT",
                uuid.uuid5(uuid.NAMESPACE_URL, normalized.event_id),
            )
        if delivery.status == SlackEventStatus.PROCESSED.value:
            return self._duplicate_result(existing=delivery, event=normalized)
        if (
            delivery.slack_workspace_id != normalized.slack_workspace_id
            or delivery.channel_id != normalized.channel_id
            or delivery.event_ts != normalized.event_ts
        ):
            raise ValueError("registered Slack event does not match the normalized payload")
        return self._process_delivery(delivery=delivery, normalized=normalized, audit=audit)

    def _process_delivery(
        self,
        *,
        delivery: SlackEvent,
        normalized: SlackEventInput,
        audit: AuditContext | None,
    ) -> SlackIngestResult:
        root_ts = normalized.thread_ts or normalized.message_ts
        self._lock_aggregate(
            slack_workspace_id=normalized.slack_workspace_id,
            channel_id=normalized.channel_id,
            root_ts=root_ts,
        )
        work_item = self._get_work_item_by_source(
            slack_workspace_id=normalized.slack_workspace_id,
            channel_id=normalized.channel_id,
            message_ts=root_ts,
            for_update=True,
        )
        is_new_work_item = work_item is None
        if work_item is None:
            work_item = SlackWorkItem(
                id=uuid.uuid4(),
                workspace_id=self.workspace_id,
                slack_workspace_id=normalized.slack_workspace_id,
                channel_id=normalized.channel_id,
                message_ts=root_ts,
                message_ts_key=_ts_key(root_ts),
                permalink=(
                    normalized.permalink
                    if normalized.message_ts == root_ts
                    else None
                ),
                root_seen=SlackEventType(normalized.event_type) is SlackEventType.ROOT,
                source_version=0,
                parse_status=SlackParseStatus.PENDING.value,
                created_event_id=delivery.id,
            )
            self.session.add(work_item)
            self.session.flush()
        before = None if is_new_work_item else _work_item_snapshot(work_item)
        thread = self._get_or_create_thread(work_item)
        message = self._get_or_create_message(
            thread=thread,
            delivery=delivery,
            message_ts=normalized.message_ts,
            author_id=normalized.author_id,
            permalink=normalized.permalink,
        )
        revision_ts = normalized.revision_ts or (
            normalized.event_ts
            if SlackEventType(normalized.event_type) in (SlackEventType.EDIT, SlackEventType.DELETE)
            else normalized.message_ts
        )
        revision_id = self._insert_revision(
            message=message,
            source_event_id=delivery.id,
            source_kind="EVENT",
            revision_ts=revision_ts,
            body=normalized.body,
            is_deleted=SlackEventType(normalized.event_type) is SlackEventType.DELETE,
            edited_at=(
                _ts_datetime(revision_ts)
                if SlackEventType(normalized.event_type) is SlackEventType.EDIT
                else None
            ),
            deleted_at=(
                _ts_datetime(revision_ts)
                if SlackEventType(normalized.event_type) is SlackEventType.DELETE
                else None
            ),
            raw_payload=normalized.raw_payload,
        )
        outbox_id: uuid.UUID | None = None
        quiet_job_id: uuid.UUID | None = work_item.quiet_job_id
        if revision_id is not None:
            if SlackEventType(normalized.event_type) is SlackEventType.ROOT:
                work_item.root_seen = True
                if work_item.permalink is None:
                    work_item.permalink = normalized.permalink
            self._apply_source_change(work_item=work_item, revision_ts=revision_ts)
            quiet_job_id = self._schedule_quiet_job(work_item)
            context = audit or _slack_audit(normalized)
            append_audit(
                self.session,
                workspace_id=self.workspace_id,
                entity_type="SLACK_WORK_ITEM",
                entity_id=work_item.id,
                action=f"{SlackEventType(normalized.event_type).value}_RECEIVED",
                before=before,
                after={
                    **_work_item_snapshot(work_item),
                    "messageId": str(message.id),
                    "revisionId": str(revision_id),
                    "eventId": normalized.event_id,
                },
                context=context,
            )
            outbox_id = self._append_outbox(
                work_item=work_item,
                event_type="SLACK_MESSAGE_REVISION_RECEIVED",
                payload={
                    "workItemId": str(work_item.id),
                    "threadId": str(thread.id),
                    "messageId": str(message.id),
                    "revisionId": str(revision_id),
                    "sourceVersion": work_item.source_version,
                    "slackEventId": normalized.event_id,
                    "kind": SlackEventType(normalized.event_type).value,
                    "quietDueAt": (
                        work_item.quiet_due_at.isoformat()
                        if work_item.quiet_due_at is not None
                        else None
                    ),
                },
            )
        now = datetime.now(UTC)
        delivery.status = SlackEventStatus.PROCESSED.value
        delivery.processed_at = now
        delivery.retry_at = None
        delivery.error = None
        work_item.updated_at = now
        self.session.flush()
        return SlackIngestResult(
            event_id=delivery.id,
            work_item_id=work_item.id,
            thread_id=thread.id,
            message_id=message.id,
            revision_id=revision_id,
            outbox_id=outbox_id,
            quiet_job_id=quiet_job_id,
            duplicate=False,
        )

    receive_event = ingest_event
    store_event = ingest_event

    def list_revisions(self, *, work_item_id: uuid.UUID) -> list[SlackMessageRevision]:
        return list(
            self.session.scalars(
                select(SlackMessageRevision)
                .join(SlackMessage, SlackMessage.id == SlackMessageRevision.message_id)
                .join(SlackThread, SlackThread.id == SlackMessage.thread_id)
                .join(SlackWorkItem, SlackWorkItem.id == SlackThread.slack_work_item_id)
                .where(
                    SlackWorkItem.id == work_item_id,
                    SlackWorkItem.workspace_id == self.workspace_id,
                )
                .order_by(
                    SlackMessage.message_ts_key,
                    SlackMessageRevision.revision,
                    SlackMessageRevision.id,
                )
            )
        )

    get_message_revisions = list_revisions

    def latest_revisions(self, *, work_item_id: uuid.UUID) -> list[SlackMessageRevision]:
        ranked = (
            select(
                SlackMessageRevision.id.label("revision_id"),
                func.row_number()
                .over(
                    partition_by=SlackMessageRevision.message_id,
                    order_by=(
                        SlackMessageRevision.revision.desc(),
                        SlackMessageRevision.id.desc(),
                    ),
                )
                .label("position"),
            )
            .join(SlackMessage, SlackMessage.id == SlackMessageRevision.message_id)
            .join(SlackThread, SlackThread.id == SlackMessage.thread_id)
            .where(SlackThread.slack_work_item_id == work_item_id)
            .subquery()
        )
        return list(
            self.session.scalars(
                select(SlackMessageRevision)
                .join(ranked, ranked.c.revision_id == SlackMessageRevision.id)
                .join(SlackMessage, SlackMessage.id == SlackMessageRevision.message_id)
                .where(ranked.c.position == 1)
                .order_by(SlackMessage.message_ts_key, SlackMessageRevision.id)
            )
        )

    def store_parse_result(
        self,
        *,
        work_item_id: uuid.UUID,
        source_version: int,
        parser_version: str,
        status: SlackParseStatus | str,
        result: Mapping[str, Any],
        missing_fields: Sequence[str] = (),
        confidence: Decimal | float | str | None = None,
        error: str | None = None,
        audit: AuditContext | None = None,
    ) -> SlackParseResult:
        work_item = self._get_work_item(work_item_id=work_item_id, for_update=True)
        if source_version < 1 or source_version > work_item.source_version:
            raise ValueError("source_version must identify an existing Slack source snapshot")
        parsed_status = SlackParseStatus(status)
        if parsed_status is SlackParseStatus.PENDING:
            raise ValueError("a stored parse result cannot be PENDING")
        clean_parser = _nonblank(parser_version, "parser_version", 100)
        clean_error = _optional_nonblank(error, "error", 4_000)
        if parsed_status is SlackParseStatus.FAILED and clean_error is None:
            raise ValueError("failed parse results require an error")
        if parsed_status is not SlackParseStatus.FAILED and clean_error is not None:
            raise ValueError("only failed parse results may store an error")
        confidence_value = Decimal(str(confidence)) if confidence is not None else None
        if confidence_value is not None and not Decimal(0) <= confidence_value <= Decimal(1):
            raise ValueError("confidence must be between zero and one")
        parse_id = uuid.uuid4()
        inserted_id = self.session.execute(
            insert(SlackParseResult)
            .values(
                id=parse_id,
                slack_work_item_id=work_item.id,
                source_version=source_version,
                parser_version=clean_parser,
                status=parsed_status.value,
                result=dict(result),
                missing_fields=list(missing_fields),
                confidence=confidence_value,
                error=clean_error,
            )
            .on_conflict_do_nothing(constraint="uq_slack_parse_results_source_parser")
            .returning(SlackParseResult.id)
        ).scalar_one_or_none()
        if inserted_id is None:
            return self.session.execute(
                select(SlackParseResult).where(
                    SlackParseResult.slack_work_item_id == work_item.id,
                    SlackParseResult.source_version == source_version,
                    SlackParseResult.parser_version == clean_parser,
                )
            ).scalar_one()
        stored = self.session.execute(
            select(SlackParseResult).where(SlackParseResult.id == inserted_id)
        ).scalar_one()
        if work_item.source_version == source_version:
            before = _work_item_snapshot(work_item)
            work_item.parse_status = parsed_status.value
            work_item.parse_result = dict(result)
            work_item.updated_at = datetime.now(UTC)
            append_audit(
                self.session,
                workspace_id=self.workspace_id,
                entity_type="SLACK_WORK_ITEM",
                entity_id=work_item.id,
                action="PARSE_RESULT_STORED",
                before=before,
                after=_work_item_snapshot(work_item),
                context=audit
                or AuditContext(
                    source_type=AuditSourceType.WORKER,
                    trace_id=f"slack_parse_{stored.id.hex}",
                    idempotency_key=f"slack-parse:{work_item.id}:{source_version}:{clean_parser}",
                ),
            )
            self._append_outbox(
                work_item=work_item,
                event_type="SLACK_PARSE_RESULT_STORED",
                payload={
                    "workItemId": str(work_item.id),
                    "parseResultId": str(stored.id),
                    "sourceVersion": source_version,
                    "parserVersion": clean_parser,
                    "status": parsed_status.value,
                },
            )
        self.session.flush()
        return stored

    def begin_thread_sync(self, *, thread_id: uuid.UUID) -> SlackThread:
        thread = self._get_thread(thread_id=thread_id, for_update=True)
        work_item = self._get_work_item(
            work_item_id=thread.slack_work_item_id,
            for_update=True,
        )
        channel = self._require_allowed_channel(
            slack_workspace_id=work_item.slack_workspace_id,
            channel_id=work_item.channel_id,
            event_says_private=False,
        )
        if channel.is_private and not channel.include_private:
            raise SlackPrivateChannelDeniedError(
                "private Slack channel thread sync is disabled"
            )
        if thread.sync_status == SlackThreadSyncStatus.DEAD.value:
            raise SlackThreadSyncError("a DEAD Slack thread sync must be explicitly reset")
        thread.sync_status = SlackThreadSyncStatus.RUNNING.value
        thread.sync_attempts += 1
        thread.retry_at = None
        thread.last_error = None
        thread.updated_at = datetime.now(UTC)
        self.session.flush()
        return thread

    def merge_thread_page(
        self,
        *,
        thread_id: uuid.UUID,
        messages: Sequence[SlackMessageSnapshot],
        next_cursor: str | None = None,
        watermark_ts: str | None = None,
        complete: bool = True,
        audit: AuditContext | None = None,
    ) -> ThreadSyncResult:
        thread = self._get_thread(thread_id=thread_id, for_update=True)
        work_item = self._get_work_item(
            work_item_id=thread.slack_work_item_id,
            for_update=True,
        )
        self._lock_aggregate(
            slack_workspace_id=work_item.slack_workspace_id,
            channel_id=work_item.channel_id,
            root_ts=work_item.message_ts,
        )
        inserted: list[uuid.UUID] = []
        ordered = sorted(messages, key=lambda item: _ts_key(item.revision_ts or item.message_ts))
        max_watermark = _max_ts(
            thread.sync_watermark_ts,
            (
                _canonical_ts(watermark_ts, "watermark_ts")
                if watermark_ts is not None
                else None
            ),
        )
        context = audit or AuditContext(
            source_type=AuditSourceType.WORKER,
            trace_id=f"slack_thread_sync_{thread.id.hex}",
        )
        for snapshot in ordered:
            message_ts = _canonical_ts(snapshot.message_ts, "message_ts")
            revision_ts = _canonical_ts(
                snapshot.revision_ts or snapshot.message_ts,
                "revision_ts",
            )
            message = self._get_or_create_message(
                thread=thread,
                delivery=None,
                message_ts=message_ts,
                author_id=snapshot.author_id,
                permalink=snapshot.permalink,
            )
            deleted_at = snapshot.deleted_at
            if snapshot.is_deleted and deleted_at is None:
                deleted_at = _ts_datetime(revision_ts)
            revision_id = self._insert_revision(
                message=message,
                source_event_id=None,
                source_kind="THREAD_SYNC",
                revision_ts=revision_ts,
                body=snapshot.body,
                is_deleted=snapshot.is_deleted,
                edited_at=snapshot.edited_at,
                deleted_at=deleted_at,
                raw_payload=snapshot.raw_payload,
            )
            if revision_id is None:
                max_watermark = _max_ts(max_watermark, revision_ts)
                continue
            before = _work_item_snapshot(work_item)
            inserted.append(revision_id)
            if message.is_root and not snapshot.is_deleted:
                work_item.root_seen = True
                if work_item.permalink is None:
                    work_item.permalink = snapshot.permalink
            self._apply_source_change(work_item=work_item, revision_ts=revision_ts)
            self._schedule_quiet_job(work_item)
            append_audit(
                self.session,
                workspace_id=self.workspace_id,
                entity_type="SLACK_WORK_ITEM",
                entity_id=work_item.id,
                action="THREAD_SYNC_REVISION_RECEIVED",
                before=before,
                after={
                    **_work_item_snapshot(work_item),
                    "messageId": str(message.id),
                    "revisionId": str(revision_id),
                },
                context=context,
            )
            self._append_outbox(
                work_item=work_item,
                event_type="SLACK_MESSAGE_REVISION_RECEIVED",
                payload={
                    "workItemId": str(work_item.id),
                    "threadId": str(thread.id),
                    "messageId": str(message.id),
                    "revisionId": str(revision_id),
                    "sourceVersion": work_item.source_version,
                    "kind": "THREAD_SYNC",
                },
            )
            max_watermark = _max_ts(max_watermark, revision_ts)

        clean_cursor = _optional_nonblank(next_cursor, "next_cursor")
        thread.sync_cursor = clean_cursor
        if max_watermark is not None:
            thread.sync_watermark_ts = max_watermark
            thread.sync_watermark_ts_key = _ts_key(max_watermark)
        now = datetime.now(UTC)
        if complete and clean_cursor is None:
            thread.sync_status = SlackThreadSyncStatus.SYNCED.value
            thread.last_synced_at = now
            thread.sync_attempts = 0
        else:
            thread.sync_status = SlackThreadSyncStatus.PENDING.value
        thread.retry_at = None
        thread.last_error = None
        thread.updated_at = now
        work_item.updated_at = now
        self.session.flush()
        return ThreadSyncResult(
            thread_id=thread.id,
            inserted_revision_ids=tuple(inserted),
            watermark_ts=thread.sync_watermark_ts,
            next_cursor=thread.sync_cursor,
            status=SlackThreadSyncStatus(thread.sync_status),
        )

    sync_thread = merge_thread_page
    merge_thread_snapshot = merge_thread_page

    def record_thread_sync_failure(
        self,
        *,
        thread_id: uuid.UUID,
        error: str,
        retry_after: timedelta,
        enqueue_retry_job: bool = True,
    ) -> ThreadSyncFailurePlan:
        if retry_after < timedelta(0):
            raise ValueError("retry_after cannot be negative")
        clean_error = _nonblank(error, "error", 4_000)
        thread = self._get_thread(thread_id=thread_id, for_update=True)
        if thread.sync_status != SlackThreadSyncStatus.RUNNING.value:
            thread.sync_attempts += 1
        now = datetime.now(UTC)
        job_id: uuid.UUID | None = None
        if thread.sync_attempts >= thread.max_sync_attempts:
            thread.sync_status = SlackThreadSyncStatus.DEAD.value
            thread.retry_at = None
        else:
            thread.sync_status = SlackThreadSyncStatus.RETRY.value
            thread.retry_at = now + retry_after
            if enqueue_retry_job:
                job_id = JobRepository(self.session).enqueue(
                    job_type="SLACK_THREAD_SYNC",
                    payload={
                        "threadId": str(thread.id),
                        "attempt": thread.sync_attempts + 1,
                    },
                    run_after=thread.retry_at,
                    max_attempts=thread.max_sync_attempts,
                    idempotency_key=f"slack-thread-sync-retry:{thread.id}:{thread.sync_attempts}",
                )
        thread.last_error = clean_error
        thread.updated_at = now
        self.session.flush()
        return ThreadSyncFailurePlan(
            status=SlackThreadSyncStatus(thread.sync_status),
            attempt=thread.sync_attempts,
            max_attempts=thread.max_sync_attempts,
            retry_at=thread.retry_at,
            job_id=job_id,
        )

    fail_thread_sync = record_thread_sync_failure

    def reset_dead_thread_sync(self, *, thread_id: uuid.UUID) -> SlackThread:
        thread = self._get_thread(thread_id=thread_id, for_update=True)
        if thread.sync_status != SlackThreadSyncStatus.DEAD.value:
            raise SlackThreadSyncError("only a DEAD Slack thread sync can be reset")
        thread.sync_status = SlackThreadSyncStatus.PENDING.value
        thread.sync_attempts = 0
        thread.retry_at = None
        thread.last_error = None
        thread.updated_at = datetime.now(UTC)
        self.session.flush()
        return thread

    def enqueue_recent_thread_syncs(
        self,
        *,
        recent_since: datetime,
        synced_before: datetime,
        limit: int = 100,
    ) -> list[uuid.UUID]:
        if limit < 1:
            raise ValueError("limit must be positive")
        rows = self.session.execute(
            select(SlackThread, SlackWorkItem)
            .join(SlackWorkItem, SlackWorkItem.id == SlackThread.slack_work_item_id)
            .where(
                SlackWorkItem.workspace_id == self.workspace_id,
                SlackWorkItem.last_activity_at >= recent_since,
                SlackThread.sync_status.in_(
                    (
                        SlackThreadSyncStatus.PENDING.value,
                        SlackThreadSyncStatus.SYNCED.value,
                    )
                ),
                or_(
                    SlackThread.last_synced_at.is_(None),
                    SlackThread.last_synced_at < synced_before,
                ),
            )
            .order_by(SlackThread.last_synced_at.asc().nullsfirst(), SlackThread.id)
            .limit(limit)
            .with_for_update(skip_locked=True, of=SlackThread)
        ).all()
        jobs = JobRepository(self.session)
        job_ids: list[uuid.UUID] = []
        for thread, work_item in rows:
            generation = (
                thread.last_synced_at.isoformat()
                if thread.last_synced_at is not None
                else "never"
            )
            job_ids.append(
                jobs.enqueue(
                    job_type="SLACK_THREAD_SYNC",
                    payload={
                        "threadId": str(thread.id),
                        "workItemId": str(work_item.id),
                        "cursor": thread.sync_cursor,
                        "watermarkTs": thread.sync_watermark_ts,
                    },
                    idempotency_key=f"slack-thread-sync:{thread.id}:{generation}",
                    max_attempts=thread.max_sync_attempts,
                )
            )
            thread.sync_status = SlackThreadSyncStatus.PENDING.value
            thread.updated_at = datetime.now(UTC)
        return job_ids

    schedule_recent_thread_syncs = enqueue_recent_thread_syncs

    def quiet_job_is_current(
        self,
        *,
        work_item_id: uuid.UUID,
        job_id: uuid.UUID,
        source_version: int,
    ) -> bool:
        return bool(
            self.session.scalar(
                select(
                    (SlackWorkItem.quiet_job_id == job_id)
                    & (SlackWorkItem.source_version == source_version)
                    & (SlackWorkItem.quiet_due_at <= func.now())
                ).where(
                    SlackWorkItem.id == work_item_id,
                    SlackWorkItem.workspace_id == self.workspace_id,
                )
            )
        )

    def cancel_quiet_window(self, *, work_item_id: uuid.UUID) -> bool:
        work_item = self._get_work_item(work_item_id=work_item_id, for_update=True)
        if work_item.quiet_job_id is None:
            return False
        job = self.session.execute(
            select(BackgroundJob)
            .where(BackgroundJob.id == work_item.quiet_job_id)
            .with_for_update()
        ).scalar_one_or_none()
        if job is None:
            work_item.quiet_job_id = None
            return False
        if job.status not in (JobStatus.QUEUED.value, JobStatus.RETRY.value):
            return False
        now = datetime.now(UTC)
        job.status = JobStatus.SUCCEEDED.value
        job.completed_at = now
        job.updated_at = now
        job.locked_by = None
        job.locked_at = None
        job.heartbeat_at = None
        job.last_error = "superseded by explicit quiet-window cancellation"
        work_item.quiet_job_id = None
        work_item.updated_at = now
        self.session.flush()
        return True

    def _require_allowed_channel(
        self,
        *,
        slack_workspace_id: str,
        channel_id: str,
        event_says_private: bool,
    ) -> SlackChannelAllowlist:
        channel = self._channel(
            slack_workspace_id=slack_workspace_id,
            channel_id=channel_id,
            for_update=True,
            required=False,
        )
        if channel is None or not channel.enabled:
            raise SlackChannelDeniedError(
                f"Slack channel {slack_workspace_id}/{channel_id} is not allowlisted"
            )
        if event_says_private and not channel.include_private:
            raise SlackPrivateChannelDeniedError(
                f"private Slack channel {slack_workspace_id}/{channel_id} is denied"
            )
        return channel

    def _channel(
        self,
        *,
        slack_workspace_id: str,
        channel_id: str,
        for_update: bool,
        required: bool = True,
    ) -> SlackChannelAllowlist | None:
        statement = select(SlackChannelAllowlist).where(
            SlackChannelAllowlist.workspace_id == self.workspace_id,
            SlackChannelAllowlist.slack_workspace_id == slack_workspace_id,
            SlackChannelAllowlist.channel_id == channel_id,
        )
        if for_update:
            statement = statement.with_for_update()
        entry = self.session.execute(statement).scalar_one_or_none()
        if entry is None and required:
            raise SlackChannelDeniedError(
                f"Slack channel {slack_workspace_id}/{channel_id} is not allowlisted"
            )
        return entry

    def _duplicate_result(
        self,
        *,
        existing: SlackEvent,
        event: SlackEventInput,
    ) -> SlackIngestResult:
        revision = self.session.execute(
            select(SlackMessageRevision).where(
                SlackMessageRevision.source_event_id == existing.id
            )
        ).scalar_one_or_none()
        message: SlackMessage | None = None
        thread: SlackThread | None = None
        work_item: SlackWorkItem | None = None
        if revision is not None:
            message = self.session.execute(
                select(SlackMessage).where(SlackMessage.id == revision.message_id)
            ).scalar_one()
            thread = self.session.execute(
                select(SlackThread).where(SlackThread.id == message.thread_id)
            ).scalar_one()
            work_item = self.session.execute(
                select(SlackWorkItem).where(SlackWorkItem.id == thread.slack_work_item_id)
            ).scalar_one()
        else:
            root_ts = event.thread_ts or event.message_ts
            work_item = self._get_work_item_by_source(
                slack_workspace_id=event.slack_workspace_id,
                channel_id=event.channel_id,
                message_ts=root_ts,
                for_update=False,
            )
            if work_item is not None:
                thread = self.session.execute(
                    select(SlackThread).where(
                        SlackThread.slack_work_item_id == work_item.id
                    )
                ).scalar_one_or_none()
        return SlackIngestResult(
            event_id=existing.id,
            work_item_id=work_item.id if work_item is not None else None,
            thread_id=thread.id if thread is not None else None,
            message_id=message.id if message is not None else None,
            revision_id=revision.id if revision is not None else None,
            outbox_id=None,
            quiet_job_id=work_item.quiet_job_id if work_item is not None else None,
            duplicate=True,
        )

    def _get_work_item_by_source(
        self,
        *,
        slack_workspace_id: str,
        channel_id: str,
        message_ts: str,
        for_update: bool,
    ) -> SlackWorkItem | None:
        statement = select(SlackWorkItem).where(
            SlackWorkItem.workspace_id == self.workspace_id,
            SlackWorkItem.slack_workspace_id == slack_workspace_id,
            SlackWorkItem.channel_id == channel_id,
            SlackWorkItem.message_ts == message_ts,
        )
        if for_update:
            statement = statement.with_for_update()
        return self.session.execute(statement).scalar_one_or_none()

    def _get_work_item(
        self,
        *,
        work_item_id: uuid.UUID,
        for_update: bool,
    ) -> SlackWorkItem:
        statement = select(SlackWorkItem).where(
            SlackWorkItem.id == work_item_id,
            SlackWorkItem.workspace_id == self.workspace_id,
        )
        if for_update:
            statement = statement.with_for_update()
        work_item = self.session.execute(statement).scalar_one_or_none()
        if work_item is None:
            raise ResourceNotFoundError("SLACK_WORK_ITEM", work_item_id)
        return work_item

    def _get_thread(self, *, thread_id: uuid.UUID, for_update: bool) -> SlackThread:
        statement = (
            select(SlackThread)
            .join(SlackWorkItem, SlackWorkItem.id == SlackThread.slack_work_item_id)
            .where(
                SlackThread.id == thread_id,
                SlackWorkItem.workspace_id == self.workspace_id,
            )
        )
        if for_update:
            statement = statement.with_for_update(of=SlackThread)
        thread = self.session.execute(statement).scalar_one_or_none()
        if thread is None:
            raise ResourceNotFoundError("SLACK_THREAD", thread_id)
        return thread

    def _get_or_create_thread(self, work_item: SlackWorkItem) -> SlackThread:
        thread = self.session.execute(
            select(SlackThread).where(SlackThread.slack_work_item_id == work_item.id)
        ).scalar_one_or_none()
        if thread is not None:
            return thread
        thread = SlackThread(
            id=uuid.uuid4(),
            slack_work_item_id=work_item.id,
            thread_ts=work_item.message_ts,
            thread_ts_key=work_item.message_ts_key,
            sync_status=SlackThreadSyncStatus.PENDING.value,
            sync_attempts=0,
            max_sync_attempts=5,
        )
        self.session.add(thread)
        self.session.flush()
        return thread

    def _get_or_create_message(
        self,
        *,
        thread: SlackThread,
        delivery: SlackEvent | None,
        message_ts: str,
        author_id: str | None,
        permalink: str | None,
    ) -> SlackMessage:
        message = self.session.execute(
            select(SlackMessage).where(
                SlackMessage.thread_id == thread.id,
                SlackMessage.message_ts == message_ts,
            )
        ).scalar_one_or_none()
        if message is None:
            message = SlackMessage(
                id=uuid.uuid4(),
                thread_id=thread.id,
                message_ts=message_ts,
                message_ts_key=_ts_key(message_ts),
                author_id=_optional_nonblank(author_id, "author_id", 200),
                permalink=_optional_nonblank(permalink, "permalink"),
                is_root=message_ts == thread.thread_ts,
                first_event_id=delivery.id if delivery is not None else None,
            )
            self.session.add(message)
            self.session.flush()
            return message
        changed = False
        if message.author_id is None and author_id is not None:
            message.author_id = _nonblank(author_id, "author_id", 200)
            changed = True
        if message.permalink is None and permalink is not None:
            message.permalink = _nonblank(permalink, "permalink")
            changed = True
        if message.first_event_id is None and delivery is not None:
            message.first_event_id = delivery.id
            changed = True
        if changed:
            self.session.flush()
        return message

    def _insert_revision(
        self,
        *,
        message: SlackMessage,
        source_event_id: uuid.UUID | None,
        source_kind: str,
        revision_ts: str,
        body: str | None,
        is_deleted: bool,
        edited_at: datetime | None,
        deleted_at: datetime | None,
        raw_payload: Mapping[str, Any] | None,
    ) -> uuid.UUID | None:
        if is_deleted:
            body = None
            deleted_at = deleted_at or _ts_datetime(revision_ts)
        elif body is None:
            raise ValueError("non-delete Slack revisions require a body")
        revision_id = uuid.uuid4()
        return self.session.execute(
            insert(SlackMessageRevision)
            .values(
                id=revision_id,
                message_id=message.id,
                source_event_id=source_event_id,
                source_kind=source_kind,
                revision=_ts_key(revision_ts),
                revision_ts=revision_ts,
                body=body,
                is_deleted=is_deleted,
                edited_at=edited_at,
                deleted_at=deleted_at,
                raw_payload=dict(raw_payload or {}),
            )
            .on_conflict_do_nothing()
            .returning(SlackMessageRevision.id)
        ).scalar_one_or_none()

    def _apply_source_change(self, *, work_item: SlackWorkItem, revision_ts: str) -> None:
        revision_key = _ts_key(revision_ts)
        work_item.source_version += 1
        work_item.parse_status = SlackParseStatus.PENDING.value
        work_item.parse_result = None
        if work_item.last_activity_ts_key is None or revision_key > work_item.last_activity_ts_key:
            settings = self.session.execute(
                select(WorkspaceSettings).where(WorkspaceSettings.id == self.workspace_id)
            ).scalar_one()
            activity_at = _ts_datetime(revision_ts)
            work_item.last_activity_ts = revision_ts
            work_item.last_activity_ts_key = revision_key
            work_item.last_activity_at = activity_at
            work_item.quiet_due_at = activity_at + timedelta(
                minutes=settings.generation_quiet_minutes
            )

    def _schedule_quiet_job(self, work_item: SlackWorkItem) -> uuid.UUID:
        if work_item.quiet_due_at is None:
            raise RuntimeError("a source change must establish a quiet-window due time")
        payload = {
            "workItemId": str(work_item.id),
            "sourceVersion": work_item.source_version,
            "dueAt": work_item.quiet_due_at.isoformat(),
        }
        existing: BackgroundJob | None = None
        if work_item.quiet_job_id is not None:
            existing = self.session.execute(
                select(BackgroundJob)
                .where(BackgroundJob.id == work_item.quiet_job_id)
                .with_for_update()
            ).scalar_one_or_none()
        now = datetime.now(UTC)
        if existing is not None and existing.status in (
            JobStatus.QUEUED.value,
            JobStatus.RETRY.value,
        ):
            existing.payload = payload
            existing.run_after = work_item.quiet_due_at
            existing.status = JobStatus.QUEUED.value
            existing.attempts = 0
            existing.locked_by = None
            existing.locked_at = None
            existing.heartbeat_at = None
            existing.last_error = None
            existing.completed_at = None
            existing.updated_at = now
            return existing.id
        job_id = JobRepository(self.session).enqueue(
            job_type="SLACK_QUIET_WINDOW",
            payload=payload,
            run_after=work_item.quiet_due_at,
            max_attempts=5,
            idempotency_key=f"slack-quiet:{work_item.id}:{work_item.source_version}",
        )
        work_item.quiet_job_id = job_id
        return job_id

    def _append_outbox(
        self,
        *,
        work_item: SlackWorkItem,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> uuid.UUID:
        outbox_id = uuid.uuid4()
        self.session.add(
            OutboxEvent(
                id=outbox_id,
                workspace_id=self.workspace_id,
                aggregate_type="SLACK_WORK_ITEM",
                aggregate_id=work_item.id,
                event_type=event_type,
                aggregate_version=work_item.source_version,
                payload=dict(payload),
                status=OutboxStatus.READY.value,
                attempts=0,
            )
        )
        return outbox_id

    def _lock_aggregate(
        self,
        *,
        slack_workspace_id: str,
        channel_id: str,
        root_ts: str,
    ) -> None:
        key = f"{self.workspace_id}:{slack_workspace_id}:{channel_id}:{root_ts}"
        self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:aggregate_key, 0))"),
            {"aggregate_key": key},
        )


def _normalize_event(event: SlackEventInput) -> SlackEventInput:
    event_type = SlackEventType(event.event_type)
    event_id = _nonblank(event.event_id, "event_id", 255)
    slack_workspace_id = _nonblank(event.slack_workspace_id, "slack_workspace_id", 80)
    channel_id = _nonblank(event.channel_id, "channel_id", 80)
    event_ts = _canonical_ts(event.event_ts, "event_ts")
    message_ts = _canonical_ts(event.message_ts, "message_ts")
    thread_ts = (
        _canonical_ts(event.thread_ts, "thread_ts")
        if event.thread_ts is not None
        else None
    )
    revision_ts = (
        _canonical_ts(event.revision_ts, "revision_ts")
        if event.revision_ts is not None
        else None
    )
    if event_type is SlackEventType.REPLY and thread_ts is None:
        raise ValueError("reply events require thread_ts")
    if thread_ts is not None and _ts_key(message_ts) < _ts_key(thread_ts):
        raise ValueError("message_ts cannot precede thread_ts")
    if event_type is SlackEventType.ROOT and thread_ts not in (None, message_ts):
        raise ValueError("root events cannot point at a different thread_ts")
    if event_type is not SlackEventType.DELETE and event.body is None:
        raise ValueError("non-delete Slack events require a body")
    return SlackEventInput(
        event_id=event_id,
        slack_workspace_id=slack_workspace_id,
        channel_id=channel_id,
        event_type=event_type,
        event_ts=event_ts,
        message_ts=message_ts,
        body=None if event_type is SlackEventType.DELETE else event.body,
        thread_ts=thread_ts,
        revision_ts=revision_ts,
        author_id=_optional_nonblank(event.author_id, "author_id", 200),
        permalink=_optional_nonblank(event.permalink, "permalink"),
        channel_is_private=event.channel_is_private,
        raw_payload=dict(event.raw_payload or {}),
    )


def _canonical_ts(value: str, field: str) -> str:
    clean = value.strip()
    matched = _TS_PATTERN.fullmatch(clean)
    if matched is None:
        raise ValueError(f"{field} must be a Slack timestamp")
    fraction = (matched.group("fraction") or "").ljust(6, "0")
    try:
        Decimal(f"{matched.group('seconds')}.{fraction}")
    except InvalidOperation as exc:  # pragma: no cover - regex makes this defensive
        raise ValueError(f"{field} must be a Slack timestamp") from exc
    return f"{matched.group('seconds')}.{fraction}"


def _ts_key(value: str) -> int:
    return int(value.replace(".", ""))


def _ts_datetime(value: str) -> datetime:
    timestamp_key = _ts_key(value)
    seconds, microseconds = divmod(timestamp_key, 1_000_000)
    return datetime.fromtimestamp(seconds, tz=UTC).replace(microsecond=microseconds)


def _max_ts(left: str | None, right: str | None) -> str | None:
    if left is None:
        return right
    if right is None:
        return left
    return left if _ts_key(left) >= _ts_key(right) else right


def _work_item_snapshot(work_item: SlackWorkItem) -> dict[str, Any]:
    return {
        "id": str(work_item.id),
        "slackWorkspaceId": work_item.slack_workspace_id,
        "channelId": work_item.channel_id,
        "messageTs": work_item.message_ts,
        "rootSeen": work_item.root_seen,
        "sourceVersion": work_item.source_version,
        "parseStatus": work_item.parse_status,
        "lastActivityTs": work_item.last_activity_ts,
        "quietDueAt": (
            work_item.quiet_due_at.isoformat()
            if work_item.quiet_due_at is not None
            else None
        ),
        "quietJobId": (
            str(work_item.quiet_job_id) if work_item.quiet_job_id is not None else None
        ),
    }


def _slack_audit(event: SlackEventInput) -> AuditContext:
    return AuditContext(
        source_type=AuditSourceType.SLACK,
        external_actor_id=event.author_id,
        trace_id=f"slack_event_{event.event_id}",
        idempotency_key=f"slack-event:{event.slack_workspace_id}:{event.event_id}",
    )


def _nonblank(value: str, field: str, maximum: int | None = None) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{field} cannot be blank")
    if maximum is not None and len(clean) > maximum:
        raise ValueError(f"{field} cannot exceed {maximum} characters")
    return clean


def _optional_nonblank(
    value: str | None,
    field: str,
    maximum: int | None = None,
) -> str | None:
    if value is None:
        return None
    return _nonblank(value, field, maximum)
