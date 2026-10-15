"""Slack background-job handlers composed over the shared ledger pipeline."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tc_backend.db.jobs import ClaimedJob, JobRepository
from tc_backend.db.models import (
    BackgroundJob,
    JobStatus,
    OutboxEvent,
    SlackEvent,
    SlackParseStatus,
    SlackWorkItem,
)
from tc_backend.db.slack import SlackRepository
from tc_backend.infrastructure.slack.errors import SlackRateLimitError
from tc_backend.infrastructure.slack.models import (
    NormalizedSlackEvent,
    SlackSourceMessage,
)
from tc_backend.infrastructure.slack.normalization import normalize_events_api_payload
from tc_backend.infrastructure.slack.parser import SlackWorkItemParser
from tc_backend.infrastructure.slack.socket_mode import SlackSocketModeClient
from tc_backend.infrastructure.slack.sync import SlackConversationSynchronizer
from tc_backend.infrastructure.slack.transport import SlackWebApiTransport
from tc_backend.generation.slack_notifications import (
    GenerationSlackNotificationConflict,
    GenerationSlackNotificationDelivery,
    GenerationSlackNotificationRetry,
)
from tc_backend.slack_pipeline import (
    is_generation_notification_message,
    ledger_event,
    ledger_snapshot,
)
from tc_backend.worker.knowledge_consumer import (
    AutomaticGenerationNotReady,
    AutomaticGenerationPermanentError,
    KnowledgeOutboxConsumer,
)


class PermanentJobError(RuntimeError):
    """A malformed or invalid delivery must not consume retry attempts."""


class InvalidJobPayloadError(PermanentJobError, ValueError):
    """A background-job payload cannot satisfy its handler contract."""


class RetryAfterError(RuntimeError):
    """Tell the generic worker to retry at the provider-specified boundary."""

    def __init__(self, message: str, *, retry_after_seconds: float) -> None:
        super().__init__(message)
        self.retry_after_seconds = max(0.0, retry_after_seconds)


@dataclass(slots=True)
class SlackWorkerRuntime:
    session_factory: sessionmaker[Session]
    synchronizer: SlackConversationSynchronizer | None = None
    parser: SlackWorkItemParser = SlackWorkItemParser()
    automatic_generation_consumer: KnowledgeOutboxConsumer | None = None
    notification_delivery: GenerationSlackNotificationDelivery | None = None
    token_workspace_id: str | None = None

    @classmethod
    def with_transport(
        cls,
        *,
        session_factory: sessionmaker[Session],
        transport: SlackWebApiTransport,
        automatic_generation_consumer: KnowledgeOutboxConsumer | None = None,
        token_workspace_id: str | None = None,
        notification_delivery_lease_seconds: int = 60,
        notification_reconcile_window_seconds: int = 86_400,
        notification_retry_base_seconds: int = 5,
        notification_retry_max_seconds: int = 3_600,
        notification_breaker_open: bool = False,
        notification_delivery_enabled: bool = True,
    ) -> "SlackWorkerRuntime":
        return cls(
            session_factory=session_factory,
            synchronizer=SlackConversationSynchronizer(transport),
            automatic_generation_consumer=automatic_generation_consumer,
            token_workspace_id=token_workspace_id,
            notification_delivery=(
                GenerationSlackNotificationDelivery(
                    session_factory=session_factory,
                    transport=transport,
                    token_workspace_id=token_workspace_id,
                    lease_seconds=notification_delivery_lease_seconds,
                    reconcile_window_seconds=notification_reconcile_window_seconds,
                    retry_base_seconds=notification_retry_base_seconds,
                    retry_max_seconds=notification_retry_max_seconds,
                    breaker_open=notification_breaker_open,
                )
                if notification_delivery_enabled and token_workspace_id is not None
                else None
            ),
        )

    def handlers(self) -> dict[str, Any]:
        handlers = {
            "SLACK_EVENT_PROCESS": self.process_event,
            "SLACK_THREAD_SYNC": self.sync_thread,
            "SLACK_QUIET_WINDOW": self.finish_quiet_window,
            "WORK_ITEM_PARSE": self.parse_work_item,
        }
        if self.automatic_generation_consumer is not None:
            handlers["SLACK_AUTOMATIC_GENERATION"] = self.automatic_generation
        if self.notification_delivery is not None:
            handlers["SLACK_GENERATION_NOTIFICATION_DELIVERY"] = (
                self.deliver_generation_notification
            )
        return handlers

    def schedule_recent_thread_syncs(
        self,
        *,
        recent_since: datetime,
        synced_before: datetime,
        limit: int = 100,
    ) -> list[uuid.UUID]:
        """Idempotently enqueue stale recent threads at the scheduler boundary."""

        with self.session_factory() as session, session.begin():
            return SlackRepository(session).enqueue_recent_thread_syncs(
                recent_since=recent_since,
                synced_before=synced_before,
                limit=limit,
            )

    def register_normalized_event(
        self, event: NormalizedSlackEvent
    ) -> uuid.UUID | None:
        """Socket Mode calls the same registration pipeline as HTTP Events."""

        if (
            self.token_workspace_id is not None
            and event.workspace_id != self.token_workspace_id
        ):
            return None
        normalized = ledger_event(event, ingress_source="SOCKET_MODE")
        if normalized is None:
            return None
        with self.session_factory() as session, session.begin():
            return SlackRepository(session).register_event(normalized).event_id

    async def receive_socket_once(
        self, client: SlackSocketModeClient
    ) -> uuid.UUID | None:
        """Acknowledge one Socket Mode envelope and register it through the shared path."""

        received = await client.receive_one()
        return self.register_normalized_event(received.event)

    def process_event(
        self,
        payload: Mapping[str, Any],
        _claim: ClaimedJob | None = None,
    ) -> None:
        event_id = _payload_uuid(payload, "eventLogId")
        try:
            with self.session_factory() as session:
                delivery = session.get(SlackEvent, event_id)
                if delivery is None:
                    raise LookupError(f"Slack event does not exist: {event_id}")
                raw_payload = dict(delivery.payload)
            normalized = normalize_events_api_payload(raw_payload)
            event = ledger_event(normalized)
            if event is not None and event.permalink is None and self.synchronizer is not None:
                event = replace(
                    event,
                    permalink=self.synchronizer.get_permalink(
                        channel_id=event.channel_id,
                        message_ts=event.thread_ts or event.message_ts,
                    ),
                )
            with self.session_factory() as session, session.begin():
                delivery = session.get(SlackEvent, event_id)
                if delivery is None:
                    raise LookupError(f"Slack event does not exist: {event_id}")
                if event is None:
                    delivery.status = "IGNORED"
                    delivery.processed_at = datetime.now(UTC)
                    return
                SlackRepository(session).process_registered_event(event)
        except Exception as error:
            with self.session_factory() as session, session.begin():
                delivery = session.get(SlackEvent, event_id)
                if delivery is not None and delivery.status != "PROCESSED":
                    delivery.status = "FAILED"
                    delivery.processed_at = None
                    delivery.retry_at = datetime.now(UTC)
                    delivery.error = f"{type(error).__name__}: {error}"[:4_000]
            raise

    def sync_thread(
        self,
        payload: Mapping[str, Any],
        _claim: ClaimedJob | None = None,
    ) -> None:
        if self.synchronizer is None:
            raise RuntimeError("Slack thread sync is SETUP_REQUIRED")
        thread_id = _payload_uuid(payload, "threadId")
        with self.session_factory() as session, session.begin():
            thread = SlackRepository(session).begin_thread_sync(thread_id=thread_id)
            item = session.get(SlackWorkItem, thread.slack_work_item_id)
            if item is None:  # pragma: no cover - protected by FK
                raise LookupError(
                    f"Slack work item does not exist: {thread.slack_work_item_id}"
                )
            channel_id = item.channel_id
            thread_ts = thread.thread_ts
            oldest = thread.sync_watermark_ts
            root_permalink = item.permalink
        try:
            if root_permalink is None:
                root_permalink = self.synchronizer.get_permalink(
                    channel_id=channel_id,
                    message_ts=thread_ts,
                )
            observed = self.synchronizer.sync_thread(
                channel_id=channel_id,
                thread_ts=thread_ts,
                oldest=oldest,
            )
        except SlackRateLimitError as error:
            retry_after = max(1.0, error.retry_after_seconds or 1.0)
            with self.session_factory() as session, session.begin():
                SlackRepository(session).record_thread_sync_failure(
                    thread_id=thread_id,
                    error=f"SlackRateLimitError 429: {error}",
                    retry_after=timedelta(seconds=retry_after),
                    enqueue_retry_job=False,
                )
            raise RetryAfterError(
                f"SlackRateLimitError 429: {error}",
                retry_after_seconds=retry_after,
            ) from error
        except Exception as error:
            with self.session_factory() as session, session.begin():
                SlackRepository(session).record_thread_sync_failure(
                    thread_id=thread_id,
                    error=f"{type(error).__name__}: {error}",
                    retry_after=timedelta(seconds=5),
                    enqueue_retry_job=False,
                )
            raise
        with self.session_factory() as session, session.begin():
            SlackRepository(session).merge_thread_page(
                thread_id=thread_id,
                messages=[
                    ledger_snapshot(message, root_permalink=root_permalink)
                    for message in observed.new_revisions
                    if not is_generation_notification_message(message)
                ],
                watermark_ts=observed.high_watermark,
                complete=True,
            )

    def finish_quiet_window(
        self,
        payload: Mapping[str, Any],
        claim: ClaimedJob | None = None,
    ) -> None:
        work_item_id = _payload_uuid(payload, "workItemId")
        source_version = _payload_int(payload, "sourceVersion")
        with self.session_factory() as session, session.begin():
            item = session.execute(
                select(SlackWorkItem)
                .where(SlackWorkItem.id == work_item_id)
                .with_for_update()
            ).scalar_one_or_none()
            if item is None:
                raise PermanentJobError(
                    f"Slack work item does not exist: {work_item_id}"
                )
            if item.source_version != source_version:
                return
            if claim is not None:
                active_job = session.execute(
                    select(BackgroundJob)
                    .where(BackgroundJob.id == claim.id)
                    .with_for_update()
                ).scalar_one_or_none()
                if (
                    item.quiet_job_id != claim.id
                    or active_job is None
                    or active_job.job_type != "SLACK_QUIET_WINDOW"
                    or active_job.status != JobStatus.RUNNING.value
                    or active_job.locked_by != claim.locked_by
                    or active_job.attempts != claim.attempt
                ):
                    return
            if item.quiet_due_at is not None and item.quiet_due_at > datetime.now(UTC):
                raise RetryAfterError(
                    "Slack quiet window has not elapsed",
                    retry_after_seconds=(
                        item.quiet_due_at - datetime.now(UTC)
                    ).total_seconds(),
                )
            JobRepository(session).enqueue(
                job_type="WORK_ITEM_PARSE",
                payload={
                    "workItemId": str(item.id),
                    "sourceVersion": source_version,
                },
                idempotency_key=f"slack-parse:{item.id}:{source_version}",
            )

    def parse_work_item(
        self,
        payload: Mapping[str, Any],
        _claim: ClaimedJob | None = None,
    ) -> None:
        work_item_id = _payload_uuid(payload, "workItemId")
        source_version = _payload_int(payload, "sourceVersion")
        with self.session_factory() as session, session.begin():
            repository = SlackRepository(session)
            item = session.get(SlackWorkItem, work_item_id)
            if item is None:
                raise PermanentJobError(
                    f"Slack work item does not exist: {work_item_id}"
                )
            if item.source_version != source_version:
                return
            latest = repository.latest_revisions(work_item_id=work_item_id)
            messages = [
                SlackSourceMessage(
                    message_revision_id=f"slack_revision_{revision.id.hex}",
                    body=revision.body,
                    message_ts=revision.revision_ts,
                )
                for revision in latest
                if not revision.is_deleted and revision.body is not None
            ]
            if not messages:
                raise ValueError("Slack work item has no live source text to parse")
            result = self.parser.parse(messages)
            repository.store_parse_result(
                work_item_id=work_item_id,
                source_version=source_version,
                parser_version="slack-fields-v1",
                status=(
                    SlackParseStatus.NEEDS_REVIEW
                    if result.missing_fields
                    else SlackParseStatus.PARSED
                ),
                result=result.as_dict(),
                missing_fields=result.missing_fields,
                confidence=result.confidence,
            )
            outbox_event_id = session.scalar(
                select(OutboxEvent.id).where(
                    OutboxEvent.aggregate_type == "SLACK_WORK_ITEM",
                    OutboxEvent.aggregate_id == work_item_id,
                    OutboxEvent.aggregate_version == source_version,
                    OutboxEvent.event_type == "SLACK_PARSE_RESULT_STORED",
                )
            )
            if outbox_event_id is None:
                raise RuntimeError(
                    "current Slack parse result has no projection outbox event"
                )
            JobRepository(session).enqueue(
                job_type="SLACK_AUTOMATIC_GENERATION",
                payload={
                    "outboxEventId": str(outbox_event_id),
                    "workItemId": str(work_item_id),
                    "sourceVersion": source_version,
                },
                idempotency_key=f"slack-automatic-generation:{outbox_event_id}",
            )

    def automatic_generation(
        self,
        payload: Mapping[str, Any],
        _claim: ClaimedJob | None = None,
    ) -> None:
        outbox_event_id = _payload_uuid(payload, "outboxEventId")
        work_item_id = _payload_uuid(payload, "workItemId")
        source_version = _payload_int(payload, "sourceVersion")
        if self.automatic_generation_consumer is None:
            raise RetryAfterError(
                "Slack automatic generation is SETUP_REQUIRED",
                retry_after_seconds=60,
            )
        try:
            asyncio.run(
                self.automatic_generation_consumer.process_automatic_generation(
                    outbox_event_id=outbox_event_id,
                    work_item_id=work_item_id,
                    source_version=source_version,
                )
            )
        except AutomaticGenerationNotReady as error:
            raise RetryAfterError(
                str(error),
                retry_after_seconds=error.retry_after_seconds,
            ) from error
        except AutomaticGenerationPermanentError as error:
            raise InvalidJobPayloadError(str(error)) from error

    def deliver_generation_notification(
        self,
        payload: Mapping[str, Any],
        claim: ClaimedJob | None = None,
    ) -> None:
        intent_id = _payload_uuid(payload, "notificationIntentId")
        if claim is None:
            raise InvalidJobPayloadError(
                "notification delivery requires an owned background-job attempt"
            )
        if self.notification_delivery is None:
            raise RetryAfterError(
                "Slack generation notification delivery is SETUP_REQUIRED",
                retry_after_seconds=60,
            )
        try:
            self.notification_delivery.deliver(intent_id, claim)
        except GenerationSlackNotificationRetry as error:
            raise RetryAfterError(
                str(error),
                retry_after_seconds=error.retry_after_seconds,
            ) from error
        except GenerationSlackNotificationConflict as error:
            raise PermanentJobError(
                "Slack generation notification provenance is invalid"
            ) from error


def _payload_uuid(payload: Mapping[str, Any], key: str) -> uuid.UUID:
    value = payload.get(key)
    if not isinstance(value, str):
        raise InvalidJobPayloadError(f"Slack job payload requires {key}")
    try:
        return uuid.UUID(value)
    except ValueError as error:
        raise InvalidJobPayloadError(
            f"Slack job payload has an invalid {key}"
        ) from error


def _payload_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidJobPayloadError(f"Slack job payload requires integer {key}")
    if value < 1:
        raise InvalidJobPayloadError(f"Slack job payload requires positive {key}")
    return value
