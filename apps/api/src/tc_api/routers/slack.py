"""Slack Events ingress and Sprint 5 operational resources."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, cast

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import select

from tc_api.dependencies import SessionDependency, SlackRuntimeDependency
from tc_api.ids import decode_identifier, encode_identifier
from tc_api.slack_models import (
    CancelQuietWindowJobRequest,
    CancelQuietWindowJobResponse,
    GetSlackWorkItemResponse,
    NormalizedSlackMessageEvent,
    SlackAllowlistPageResponse,
    SlackChannelAccessResource,
    SlackEventAckResponse,
    SlackEventLogPageResponse,
    SlackEventLogResource,
    SlackIntegrationStatusResource,
    SlackJobPageResponse,
    SlackJobResource,
    SlackMessageResource,
    SlackMessageRevisionResource,
    SlackParseResource,
    SlackThreadResource,
    SlackUrlVerificationResponse,
    SlackWorkItemPageResponse,
    SlackWorkItemResource,
    ThreadSyncRequest,
    ThreadSyncResponse,
    UpsertQuietWindowJobRequest,
    UpsertQuietWindowJobResponse,
    UpdateSlackAllowlistRequest,
    UpdateSlackAllowlistResponse,
)
from tc_backend.config import SlackMode
from tc_backend.db.constants import DEFAULT_WORKSPACE_ID
from tc_backend.db.errors import (
    ResourceNotFoundError,
    SlackChannelDeniedError,
    SlackPrivateChannelDeniedError,
)
from tc_backend.db.jobs import JobRepository
from tc_backend.db.models import (
    BackgroundJob,
    JobStatus,
    SlackChannelAllowlist,
    SlackEvent,
    SlackMessage,
    SlackMessageRevision,
    SlackParseResult,
    SlackThread,
    SlackWorkItem,
)
from tc_backend.db.slack import SlackRepository
from tc_backend.infrastructure.slack.config import SlackRuntimeState
from tc_backend.infrastructure.slack.errors import (
    SlackIntegrationDisabled,
    SlackSetupRequired,
)
from tc_backend.infrastructure.slack.models import SlackNormalizedEventType
from tc_backend.infrastructure.slack.normalization import normalize_events_api_payload
from tc_backend.slack_pipeline import ledger_event, redact_slack_payload

router = APIRouter(tags=["slack"])
_ACL_WARNING = (
    "This team tool has no per-user login; anyone with application access can read "
    "allowlisted Slack content. Private channels remain denied by default."
)


@router.post(
    "/integrations/slack/events",
    operation_id="receiveSlackEvent",
    response_model=SlackEventAckResponse | SlackUrlVerificationResponse,
    description="Verifies the exact raw Slack body before JSON decoding and acknowledges quickly.",
)
async def receive_slack_event(
    request: Request,
    session: SessionDependency,
    runtime: SlackRuntimeDependency,
) -> SlackEventAckResponse | SlackUrlVerificationResponse:
    if runtime.state is SlackRuntimeState.DISABLED:
        raise SlackIntegrationDisabled("Slack event ingress is disabled")
    if runtime.state is not SlackRuntimeState.CONFIGURED or runtime.http_receiver is None:
        raise SlackSetupRequired("Slack HTTP Events requires a signing secret and credentials")
    raw_body = await request.body()
    received = runtime.http_receiver.receive(raw_body, headers=request.headers)
    if received.event.normalized_type is SlackNormalizedEventType.URL_VERIFICATION:
        return SlackUrlVerificationResponse(challenge=received.event.challenge or "")
    if (
        runtime.workspace_id is not None
        and received.event.workspace_id != runtime.workspace_id
    ):
        return _ignored_ack("TOKEN_WORKSPACE_MISMATCH")
    normalized = ledger_event(received.event)
    if normalized is None:
        return _ignored_ack("UNSUPPORTED_MESSAGE_SUBTYPE")
    try:
        with session.begin():
            registration = SlackRepository(session).register_event(normalized)
    except SlackPrivateChannelDeniedError:
        return _ignored_ack("PRIVATE_DEFAULT_DENY")
    except SlackChannelDeniedError:
        return _ignored_ack(
            "PRIVATE_DEFAULT_DENY"
            if normalized.channel_is_private
            else "CHANNEL_NOT_ALLOWLISTED"
        )
    event_log_id = encode_identifier("slack_event", registration.event_id)
    if registration.duplicate:
        return SlackEventAckResponse(
            outcome=(
                "DUPLICATE_EVENT_ID"
                if registration.duplicate_event_id
                else "DUPLICATE_MESSAGE_IDENTITY"
            ),
            event_log_id=None,
            duplicate_of_event_log_id=event_log_id,
            ignored_reason=None,
            enqueued_job_ids=[],
        )
    return SlackEventAckResponse(
        outcome="ACCEPTED",
        event_log_id=event_log_id,
        duplicate_of_event_log_id=None,
        ignored_reason=None,
        enqueued_job_ids=[encode_identifier("slack_job", registration.processing_job_id)],
    )


@router.get(
    "/api/integrations/slack/status",
    operation_id="getSlackIntegrationStatus",
    response_model=SlackIntegrationStatusResource,
    description=_ACL_WARNING,
)
def get_slack_status(
    session: SessionDependency,
    runtime: SlackRuntimeDependency,
    response: Response,
) -> SlackIntegrationStatusResource:
    response.headers["X-TC-ACL-Warning"] = _ACL_WARNING
    rows = list(
        session.scalars(
            select(SlackChannelAllowlist).where(
                SlackChannelAllowlist.workspace_id == DEFAULT_WORKSPACE_ID,
                SlackChannelAllowlist.enabled.is_(True),
            )
        )
    )
    last_delivery = session.scalar(
        select(SlackEvent)
        .where(SlackEvent.workspace_id == DEFAULT_WORKSPACE_ID)
        .order_by(SlackEvent.received_at.desc())
        .limit(1)
    )
    now = datetime.now(UTC)
    configured = runtime.state is SlackRuntimeState.CONFIGURED
    mode = "SOCKET_MODE" if runtime.mode is SlackMode.SOCKET else "HTTP_EVENTS"
    return SlackIntegrationStatusResource(
        workspace_id=runtime.workspace_id or _workspace_from_rows(rows) or "TLOCAL",
        app_id=(
            str(last_delivery.payload.get("api_app_id"))
            if last_delivery is not None
            and isinstance(last_delivery.payload.get("api_app_id"), str)
            else runtime.app_id
        ),
        connection_status=(
            "DEGRADED"
            if configured
            else "DISCONNECTED"
            if runtime.state is SlackRuntimeState.DISABLED
            else "SETUP_REQUIRED"
        ),
        ingress_mode=cast(Any, mode),
        callback_state=(
            "NOT_APPLICABLE"
            if mode == "SOCKET_MODE"
            else "PENDING"
            if configured
            else "PENDING"
        ),
        installed_at=None,
        last_event_at=last_delivery.received_at if last_delivery is not None else None,
        allowlisted_public_channel_count=sum(not row.is_private for row in rows),
        explicitly_allowed_private_channel_count=sum(
            row.is_private and row.include_private for row in rows
        ),
        resource_version=_allowlist_version(rows),
        updated_at=max((row.updated_at for row in rows), default=now),
    )


@router.get(
    "/api/integrations/slack/allowlist",
    operation_id="listSlackAllowlist",
    response_model=SlackAllowlistPageResponse,
    description=_ACL_WARNING,
)
def list_slack_allowlist(
    session: SessionDependency,
    response: Response,
    workspace_id: Annotated[str | None, Query(alias="workspaceId")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
) -> SlackAllowlistPageResponse:
    response.headers["X-TC-ACL-Warning"] = _ACL_WARNING
    statement = select(SlackChannelAllowlist).where(
        SlackChannelAllowlist.workspace_id == DEFAULT_WORKSPACE_ID
    )
    if workspace_id is not None:
        statement = statement.where(SlackChannelAllowlist.slack_workspace_id == workspace_id)
    rows = list(session.scalars(statement.order_by(SlackChannelAllowlist.channel_id).limit(limit)))
    return SlackAllowlistPageResponse(items=[_channel_resource(row) for row in rows])


@router.put(
    "/api/integrations/slack/allowlist",
    operation_id="updateSlackAllowlist",
    response_model=UpdateSlackAllowlistResponse,
    description=_ACL_WARNING,
)
def update_slack_allowlist(
    payload: UpdateSlackAllowlistRequest,
    session: SessionDependency,
    response: Response,
) -> UpdateSlackAllowlistResponse:
    response.headers["X-TC-ACL-Warning"] = _ACL_WARNING
    now = datetime.now(UTC)
    requested = {channel.channelId for channel in payload.channels}
    with session.begin():
        existing = list(
            session.scalars(
                select(SlackChannelAllowlist)
                .where(
                    SlackChannelAllowlist.workspace_id == DEFAULT_WORKSPACE_ID,
                    SlackChannelAllowlist.slack_workspace_id == payload.workspaceId,
                )
                .with_for_update()
            )
        )
        if payload.expectedVersion != _allowlist_version(existing):
            raise ValueError("expectedVersion does not match the Slack allowlist version")
        repository = SlackRepository(session)
        for row in existing:
            if row.channel_id not in requested:
                row.enabled = False
                row.updated_at = now
        for channel in payload.channels:
            repository.put_channel(
                slack_workspace_id=payload.workspaceId,
                channel_id=channel.channelId,
                is_private=channel.visibility == "PRIVATE",
                include_private=channel.visibility == "PRIVATE",
                enabled=True,
            )
    all_rows = list(
        session.scalars(
            select(SlackChannelAllowlist)
            .where(
                SlackChannelAllowlist.workspace_id == DEFAULT_WORKSPACE_ID,
                SlackChannelAllowlist.slack_workspace_id == payload.workspaceId,
            )
            .order_by(SlackChannelAllowlist.channel_id)
        )
    )
    rows = [row for row in all_rows if row.enabled]
    return UpdateSlackAllowlistResponse(
        workspace_id=payload.workspaceId,
        resource_version=max(2, _allowlist_version(all_rows)),
        channels=[_channel_resource(row) for row in rows],
        updated_at=max((row.updated_at for row in rows), default=now),
    )


@router.delete(
    "/api/integrations/slack/allowlist/{channel_id}",
    operation_id="deleteSlackAllowlistChannel",
    response_model=SlackChannelAccessResource,
    description=_ACL_WARNING,
)
def delete_slack_allowlist_channel(
    channel_id: str,
    session: SessionDependency,
    runtime: SlackRuntimeDependency,
) -> SlackChannelAccessResource:
    workspace_id = runtime.workspace_id or "TLOCAL"
    with session.begin():
        row = SlackRepository(session).set_channel_enabled(
            slack_workspace_id=workspace_id,
            channel_id=channel_id,
            enabled=False,
        )
    return _channel_resource(row)


@router.get(
    "/api/slack-work-items",
    operation_id="listSlackWorkItems",
    response_model=SlackWorkItemPageResponse,
)
def list_slack_work_items(
    session: SessionDependency,
    channel_id: Annotated[str | None, Query(alias="channelId")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> SlackWorkItemPageResponse:
    statement = (
        select(SlackWorkItem, SlackThread)
        .join(SlackThread, SlackThread.slack_work_item_id == SlackWorkItem.id)
        .where(SlackWorkItem.workspace_id == DEFAULT_WORKSPACE_ID)
    )
    if channel_id is not None:
        statement = statement.where(SlackWorkItem.channel_id == channel_id)
    rows = session.execute(
        statement.order_by(SlackWorkItem.updated_at.desc(), SlackWorkItem.id).limit(limit)
    ).all()
    return SlackWorkItemPageResponse(
        items=[_work_item_resource(session, item, thread) for item, thread in rows]
    )


@router.get(
    "/api/slack-work-items/{work_item_id}",
    operation_id="getSlackWorkItem",
    response_model=GetSlackWorkItemResponse,
)
def get_slack_work_item(
    work_item_id: str,
    session: SessionDependency,
) -> GetSlackWorkItemResponse:
    item_id = decode_identifier(work_item_id, prefix="slack_work_item", field="workItemId")
    item = session.get(SlackWorkItem, item_id)
    if item is None or item.workspace_id != DEFAULT_WORKSPACE_ID:
        raise ResourceNotFoundError("SLACK_WORK_ITEM", item_id)
    thread = session.scalar(select(SlackThread).where(SlackThread.slack_work_item_id == item.id))
    if thread is None:
        raise ResourceNotFoundError("SLACK_THREAD", item.id)
    messages = list(
        session.scalars(
            select(SlackMessage)
            .where(SlackMessage.thread_id == thread.id)
            .order_by(SlackMessage.message_ts_key, SlackMessage.id)
        )
    )
    revisions = list(
        session.scalars(
            select(SlackMessageRevision)
            .join(SlackMessage, SlackMessage.id == SlackMessageRevision.message_id)
            .where(SlackMessage.thread_id == thread.id)
            .order_by(
                SlackMessage.message_ts_key,
                SlackMessageRevision.revision,
                SlackMessageRevision.id,
            )
        )
    )
    events = {
        event.id: event
        for event in session.scalars(
            select(SlackEvent).where(
                SlackEvent.id.in_(
                    [revision.source_event_id for revision in revisions if revision.source_event_id]
                )
            )
        )
    }
    revision_resources = _revision_resources(revisions, messages, events)
    revision_by_id = {resource.id: resource for resource in revision_resources}
    message_resources = _message_resources(item, thread, messages, revisions, revision_by_id)
    parse = session.scalar(
        select(SlackParseResult)
        .where(SlackParseResult.slack_work_item_id == item.id)
        .order_by(SlackParseResult.source_version.desc(), SlackParseResult.created_at.desc())
        .limit(1)
    )
    return GetSlackWorkItemResponse(
        work_item=_work_item_resource(session, item, thread, parse=parse),
        thread=_thread_resource(item, thread, len(messages)),
        messages=message_resources,
        revisions=revision_resources,
        parse=_parse_resource(item, parse) if parse is not None else None,
    )


@router.get(
    "/api/slack-work-items/{work_item_id}/messages",
    operation_id="listSlackWorkItemMessages",
    response_model=list[SlackMessageResource],
)
def list_slack_work_item_messages(
    work_item_id: str,
    session: SessionDependency,
) -> list[SlackMessageResource]:
    return get_slack_work_item(work_item_id, session).messages


@router.get(
    "/api/slack-events",
    operation_id="listSlackEvents",
    response_model=SlackEventLogPageResponse,
)
def list_slack_events(
    session: SessionDependency,
    state: Annotated[str | None, Query(alias="processingState")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> SlackEventLogPageResponse:
    statement = select(SlackEvent).where(SlackEvent.workspace_id == DEFAULT_WORKSPACE_ID)
    if state is not None:
        reverse_states = {
            "RECEIVED": "RECEIVED",
            "PROCESSED": "PROCESSED",
            "FAILED_RETRYABLE": "FAILED",
            "DEAD": "FAILED",
        }
        statement = statement.where(SlackEvent.status == reverse_states.get(state, state))
    rows = list(
        session.scalars(statement.order_by(SlackEvent.received_at.desc()).limit(limit))
    )
    return SlackEventLogPageResponse(items=[_event_resource(row) for row in rows])


@router.post(
    "/api/slack-work-items/{work_item_id}/sync-thread",
    operation_id="syncSlackThread",
    response_model=ThreadSyncResponse,
)
def sync_slack_thread(
    work_item_id: str,
    payload: ThreadSyncRequest,
    session: SessionDependency,
) -> ThreadSyncResponse:
    item_id = decode_identifier(work_item_id, prefix="slack_work_item", field="workItemId")
    started = datetime.now(UTC)
    with session.begin():
        thread = session.scalar(
            select(SlackThread).where(SlackThread.slack_work_item_id == item_id)
        )
        if thread is None:
            raise ResourceNotFoundError("SLACK_WORK_ITEM", item_id)
        JobRepository(session).enqueue(
            job_type="SLACK_THREAD_SYNC",
            payload={"threadId": str(thread.id), "mode": payload.mode},
            idempotency_key=f"slack-thread-sync:{payload.idempotencyKey}",
        )
    completed = datetime.now(UTC)
    return ThreadSyncResponse(
        sync_id=encode_identifier("slack_sync", uuid.uuid5(uuid.NAMESPACE_URL, payload.idempotencyKey)),
        mode=payload.mode,
        threads_scanned=1,
        next_cursor=payload.sinceCursor,
        started_at=started,
        completed_at=completed,
    )


@router.post(
    "/api/slack-work-items/{work_item_id}/quiet-window",
    operation_id="upsertSlackQuietWindow",
    response_model=UpsertQuietWindowJobResponse,
)
def upsert_slack_quiet_window(
    work_item_id: str,
    payload: UpsertQuietWindowJobRequest,
    session: SessionDependency,
) -> UpsertQuietWindowJobResponse:
    item_id = decode_identifier(work_item_id, prefix="slack_work_item", field="workItemId")
    payload_item_id = decode_identifier(
        payload.workItemId, prefix="slack_work_item", field="workItemId"
    )
    if item_id != payload_item_id:
        raise ValueError("path workItemId must match the request body")
    with session.begin():
        item = session.execute(
            select(SlackWorkItem).where(SlackWorkItem.id == item_id).with_for_update()
        ).scalar_one_or_none()
        if item is None:
            raise ResourceNotFoundError("SLACK_WORK_ITEM", item_id)
        if payload.expectedGeneration not in {0, item.source_version}:
            raise ValueError("expectedGeneration does not match the Slack work item")
        due_at = payload.lastThreadChangedAt + timedelta(seconds=payload.quietWindowSeconds)
        existing = session.get(BackgroundJob, item.quiet_job_id) if item.quiet_job_id else None
        if existing is not None and existing.status in {
            JobStatus.QUEUED.value,
            JobStatus.RETRY.value,
        }:
            unchanged = existing.run_after == due_at and existing.payload.get(
                "sourceRevisionId"
            ) == payload.sourceRevisionId
            existing.run_after = due_at
            existing.payload = {
                "workItemId": str(item.id),
                "sourceVersion": item.source_version,
                "dueAt": due_at.isoformat(),
                "sourceRevisionId": payload.sourceRevisionId,
            }
            existing.status = JobStatus.QUEUED.value
            existing.attempts = 0
            existing.last_error = None
            existing.updated_at = datetime.now(UTC)
            stored_job: BackgroundJob | None = existing
            outcome = "UNCHANGED" if unchanged else "RESCHEDULED"
        else:
            job_id = JobRepository(session).enqueue(
                job_type="SLACK_QUIET_WINDOW",
                payload={
                    "workItemId": str(item.id),
                    "sourceVersion": item.source_version,
                    "dueAt": due_at.isoformat(),
                    "sourceRevisionId": payload.sourceRevisionId,
                },
                run_after=due_at,
                idempotency_key=f"slack-quiet-api:{payload.idempotencyKey}",
            )
            item.quiet_job_id = job_id
            stored_job = session.get(BackgroundJob, job_id)
            outcome = "CREATED"
        item.last_activity_at = payload.lastThreadChangedAt
        item.quiet_due_at = due_at
        session.flush()
    if stored_job is None:  # pragma: no cover - enqueue always returns a stored row
        raise RuntimeError("Slack quiet-window job was not stored")
    resource = _job_resource(session, stored_job)
    if resource is None:  # pragma: no cover - payload holds the work item
        raise RuntimeError("Slack quiet-window job cannot be serialized")
    return UpsertQuietWindowJobResponse(outcome=cast(Any, outcome), job=resource)


@router.post(
    "/api/slack-jobs/{job_id}/cancel",
    operation_id="cancelSlackQuietWindow",
    response_model=CancelQuietWindowJobResponse,
)
def cancel_slack_quiet_window(
    job_id: str,
    payload: CancelQuietWindowJobRequest,
    session: SessionDependency,
) -> CancelQuietWindowJobResponse:
    resolved_id = decode_identifier(job_id, prefix="slack_job", field="jobId")
    body_id = decode_identifier(payload.jobId, prefix="slack_job", field="jobId")
    if resolved_id != body_id:
        raise ValueError("path jobId must match the request body")
    with session.begin():
        job = session.execute(
            select(BackgroundJob).where(BackgroundJob.id == resolved_id).with_for_update()
        ).scalar_one_or_none()
        if job is None or job.job_type != "SLACK_QUIET_WINDOW":
            raise ResourceNotFoundError("SLACK_JOB", resolved_id)
        work_item_id = _payload_uuid(job.payload, "workItemId")
        if payload.expectedGeneration != int(job.payload.get("sourceVersion", -1)):
            raise ValueError("expectedGeneration does not match the quiet-window job")
        cancelled = SlackRepository(session).cancel_quiet_window(
            work_item_id=work_item_id
        )
    resource = _job_resource(session, job)
    if resource is None:  # pragma: no cover - payload holds the work item
        raise RuntimeError("Slack quiet-window job cannot be serialized")
    return CancelQuietWindowJobResponse(cancelled=cancelled, job=resource)


@router.get(
    "/api/slack-jobs",
    operation_id="listSlackJobs",
    response_model=SlackJobPageResponse,
)
def list_slack_jobs(
    session: SessionDependency,
    state: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> SlackJobPageResponse:
    statement = select(BackgroundJob).where(
        BackgroundJob.job_type.in_(("SLACK_QUIET_WINDOW", "SLACK_THREAD_SYNC"))
    )
    if state is not None:
        states = {
            "PENDING": JobStatus.QUEUED.value,
            "RETRY_WAIT": JobStatus.RETRY.value,
        }
        statement = statement.where(BackgroundJob.status == states.get(state, state))
    rows = list(
        session.scalars(statement.order_by(BackgroundJob.updated_at.desc()).limit(limit))
    )
    resources = [resource for row in rows if (resource := _job_resource(session, row))]
    return SlackJobPageResponse(items=resources)


def _ignored_ack(reason: str) -> SlackEventAckResponse:
    return SlackEventAckResponse(
        outcome="IGNORED",
        event_log_id=None,
        duplicate_of_event_log_id=None,
        ignored_reason=reason,
        enqueued_job_ids=[],
    )


def _workspace_from_rows(rows: list[SlackChannelAllowlist]) -> str | None:
    return rows[0].slack_workspace_id if rows else None


def _allowlist_version(rows: list[SlackChannelAllowlist]) -> int:
    if not rows:
        return 1
    return max(2, max(int(row.updated_at.timestamp() * 1_000_000) for row in rows))


def _channel_resource(row: SlackChannelAllowlist) -> SlackChannelAccessResource:
    allowed = row.enabled and (not row.is_private or row.include_private)
    if row.is_private:
        source = "PRIVATE_EXPLICIT_ALLOW" if allowed else "PRIVATE_DEFAULT_DENY"
    else:
        source = "PUBLIC_ALLOWLIST" if allowed else "PUBLIC_NOT_ALLOWLISTED"
    return SlackChannelAccessResource(
        workspace_id=row.slack_workspace_id,
        channel_id=row.channel_id,
        channel_name=row.channel_name,
        visibility="PRIVATE" if row.is_private else "PUBLIC",
        collection_allowed=allowed,
        policy_source=cast(Any, source),
        app_membership="JOINED" if allowed else "UNKNOWN",
        updated_at=row.updated_at,
    )


def _work_item_resource(
    session: Any,
    item: SlackWorkItem,
    thread: SlackThread,
    *,
    parse: SlackParseResult | None = None,
) -> SlackWorkItemResource:
    if parse is None:
        parse = session.scalar(
            select(SlackParseResult)
            .where(SlackParseResult.slack_work_item_id == item.id)
            .order_by(SlackParseResult.source_version.desc(), SlackParseResult.created_at.desc())
            .limit(1)
        )
    root_deleted = session.scalar(
        select(SlackMessageRevision.is_deleted)
        .join(SlackMessage, SlackMessage.id == SlackMessageRevision.message_id)
        .where(SlackMessage.thread_id == thread.id, SlackMessage.is_root.is_(True))
        .order_by(SlackMessageRevision.revision.desc())
        .limit(1)
    )
    status = "DELETED" if root_deleted else "ACTIVE" if item.root_seen else "ROOT_PENDING"
    return SlackWorkItemResource(
        id=encode_identifier("slack_work_item", item.id),
        workspace_id=item.slack_workspace_id,
        channel_id=item.channel_id,
        root_message_ts=item.message_ts,
        thread_id=encode_identifier("slack_thread", thread.id),
        status=cast(Any, status),
        latest_parse_id=(encode_identifier("slack_parse", parse.id) if parse else None),
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _thread_resource(
    item: SlackWorkItem, thread: SlackThread, message_count: int
) -> SlackThreadResource:
    parse_state = {
        "PARSED": "COMPLETE",
        "NEEDS_REVIEW": "NEEDS_INPUT",
        "FAILED": "FAILED",
    }.get(item.parse_status, "QUIET_WINDOW" if item.quiet_due_at else "PENDING")
    return SlackThreadResource(
        id=encode_identifier("slack_thread", thread.id),
        work_item_id=encode_identifier("slack_work_item", item.id),
        root_message_ts=thread.thread_ts,
        root_resolved=item.root_seen,
        message_count=max(1, message_count),
        latest_changed_at=item.last_activity_at or item.updated_at,
        last_synced_at=thread.last_synced_at,
        sync_cursor=thread.sync_cursor,
        parse_state=cast(Any, parse_state),
    )


def _revision_resources(
    revisions: list[SlackMessageRevision],
    messages: list[SlackMessage],
    events: dict[uuid.UUID, SlackEvent],
) -> list[SlackMessageRevisionResource]:
    message_by_id = {message.id: message for message in messages}
    previous_by_message: dict[uuid.UUID, str] = {}
    ordinal_by_message: dict[uuid.UUID, int] = {}
    resources: list[SlackMessageRevisionResource] = []
    for revision in revisions:
        message = message_by_id[revision.message_id]
        ordinal = ordinal_by_message.get(message.id, 0) + 1
        ordinal_by_message[message.id] = ordinal
        encoded_id = encode_identifier("slack_revision", revision.id)
        source = events.get(revision.source_event_id) if revision.source_event_id else None
        event_id = source.event_id if source else f"EvSYNC{revision.id.hex}"
        kind = "DELETED" if revision.is_deleted else "CREATED" if ordinal == 1 else "EDITED"
        occurred_at = _slack_datetime(revision.revision_ts)
        resources.append(
            SlackMessageRevisionResource(
                id=encoded_id,
                message_id=encode_identifier("slack_message", message.id),
                revision=ordinal,
                kind=cast(Any, kind),
                body=revision.body,
                author_id=message.author_id,
                source_event_id=event_id,
                source_event_time=int(Decimal(revision.revision_ts)),
                occurred_at=occurred_at,
                observed_at=revision.received_at,
                previous_revision_id=previous_by_message.get(message.id),
            )
        )
        previous_by_message[message.id] = encoded_id
    return resources


def _message_resources(
    item: SlackWorkItem,
    thread: SlackThread,
    messages: list[SlackMessage],
    revisions: list[SlackMessageRevision],
    resources: dict[str, SlackMessageRevisionResource],
) -> list[SlackMessageResource]:
    by_message: dict[uuid.UUID, list[SlackMessageRevision]] = {}
    for revision in revisions:
        by_message.setdefault(revision.message_id, []).append(revision)
    result: list[SlackMessageResource] = []
    for message in messages:
        candidates = by_message.get(message.id, [])
        if not candidates:
            continue
        latest = max(candidates, key=lambda revision: (revision.revision, revision.id))
        latest_resource = resources[encode_identifier("slack_revision", latest.id)]
        result.append(
            SlackMessageResource(
                id=encode_identifier("slack_message", message.id),
                thread_id=encode_identifier("slack_thread", thread.id),
                workspace_id=item.slack_workspace_id,
                channel_id=item.channel_id,
                message_ts=message.message_ts,
                thread_ts=thread.thread_ts,
                position="ROOT" if message.is_root else "REPLY",
                current_revision_id=latest_resource.id,
                revision_count=len(candidates),
                deleted=latest.is_deleted,
                first_observed_at=min(revision.received_at for revision in candidates),
                latest_changed_at=latest_resource.occurred_at,
            )
        )
    return result


def _parse_resource(item: SlackWorkItem, parse: SlackParseResult) -> SlackParseResource:
    status = {
        "PARSED": "COMPLETE",
        "NEEDS_REVIEW": "NEEDS_INPUT",
        "FAILED": "FAILED",
    }[parse.status]
    result = dict(parse.result)
    result.setdefault("missingFields", list(parse.missing_fields))
    if parse.confidence is not None:
        result.setdefault("confidence", float(parse.confidence))
    source_ids = list(result.get("sourceMessageRevisions", []))
    if not source_ids:
        source_ids = [encode_identifier("slack_revision", parse.id)]
    return SlackParseResource(
        id=encode_identifier("slack_parse", parse.id),
        work_item_id=encode_identifier("slack_work_item", item.id),
        status=cast(Any, status),
        result=None if status == "FAILED" else result,
        open_questions=[f"Please provide {field}." for field in parse.missing_fields],
        source_revision_ids=source_ids,
        created_at=parse.created_at,
        completed_at=parse.created_at,
        error_code=parse.error if status == "FAILED" else None,
    )


def _event_resource(event: SlackEvent) -> SlackEventLogResource:
    stored_payload = dict(event.payload)
    raw = json.dumps(stored_payload, sort_keys=True, separators=(",", ":")).encode()
    payload = redact_slack_payload(stored_payload)
    normalized = ledger_event(normalize_events_api_payload(stored_payload))
    if normalized is None:  # pragma: no cover - SlackEvent rows only store message events
        raise RuntimeError("Slack event ledger contains a non-message payload")
    thread_ts = normalized.thread_ts or normalized.message_ts
    occurred_ts = normalized.revision_ts or normalized.event_ts
    normalized_resource = NormalizedSlackMessageEvent(
        event_id=normalized.event_id,
        workspace_id=normalized.slack_workspace_id,
        channel_id=normalized.channel_id,
        message_ts=normalized.message_ts,
        thread_ts=thread_ts,
        action=cast(Any, event.event_type),
        position="REPLY" if thread_ts != normalized.message_ts else "ROOT",
        author_id=normalized.author_id,
        text=normalized.body,
        source_event_time=int(Decimal(normalized.event_ts)),
        occurred_at=_slack_datetime(occurred_ts),
        normalized_at=event.received_at,
    )
    processing = {
        "PROCESSED": "PROCESSED",
        "FAILED": "FAILED_RETRYABLE",
    }.get(event.status, "RECEIVED")
    return SlackEventLogResource(
        id=encode_identifier("slack_event", event.id),
        event_id=event.event_id,
        workspace_id=event.slack_workspace_id,
        channel_id=event.channel_id,
        ingress_source=cast(
            Any,
            payload.pop("_tcIngressSource", "HTTP_EVENTS"),
        ),
        payload_sha256=hashlib.sha256(raw).hexdigest(),
        normalized_event=normalized_resource,
        processing_state=cast(Any, processing),
        received_at=event.received_at,
        processed_at=event.processed_at,
    )


def _job_resource(session: Any, job: BackgroundJob) -> SlackJobResource | None:
    work_item: SlackWorkItem | None = None
    if job.job_type == "SLACK_QUIET_WINDOW":
        raw_id = job.payload.get("workItemId")
        if isinstance(raw_id, str):
            work_item = session.get(SlackWorkItem, uuid.UUID(raw_id))
        job_type = "QUIET_WINDOW_PARSE"
    else:
        raw_thread = job.payload.get("threadId")
        thread = session.get(SlackThread, uuid.UUID(raw_thread)) if isinstance(raw_thread, str) else None
        if thread is not None:
            work_item = session.get(SlackWorkItem, thread.slack_work_item_id)
        job_type = (
            "THREAD_RECENT_RESYNC"
            if job.payload.get("mode") == "RECENT_RESYNC"
            else "THREAD_INCREMENTAL_SYNC"
        )
    if work_item is None:
        return None
    state = {
        "QUEUED": "PENDING",
        "RETRY": "RETRY_WAIT",
    }.get(job.status, job.status)
    if job.status == JobStatus.SUCCEEDED.value and job.last_error and "cancellation" in job.last_error:
        state = "CANCELLED"
    retry = None
    if state in {"RETRY_WAIT", "DEAD"}:
        message = job.last_error or "Slack worker failed"
        rate_limited = "RateLimit" in message or "429" in message
        from tc_api.slack_models import RetryStateResource

        retry = RetryStateResource(
            attempt=max(1, job.attempts),
            max_attempts=job.max_attempts,
            reason="RATE_LIMIT" if rate_limited else "INTERNAL_ERROR",
            last_http_status=429 if rate_limited else None,
            retry_after_seconds=(
                max(1, int((job.run_after - datetime.now(UTC)).total_seconds()))
                if rate_limited and state == "RETRY_WAIT"
                else 1 if rate_limited else None
            ),
            next_attempt_at=job.run_after if state == "RETRY_WAIT" else None,
            last_error_code="SLACK_RATE_LIMIT" if rate_limited else "SLACK_JOB_FAILED",
            last_error_message=message,
        )
    return SlackJobResource(
        id=encode_identifier("slack_job", job.id),
        workspace_id=work_item.slack_workspace_id,
        channel_id=work_item.channel_id,
        work_item_id=encode_identifier("slack_work_item", work_item.id),
        job_type=cast(Any, job_type),
        state=cast(Any, state),
        attempt=job.attempts,
        max_attempts=job.max_attempts,
        run_after=None if state == "DEAD" else job.run_after,
        retry=retry,
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
    )


def _slack_datetime(timestamp: str) -> datetime:
    return datetime.fromtimestamp(float(Decimal(timestamp)), tz=UTC)


def _payload_uuid(payload: dict[str, Any], key: str) -> uuid.UUID:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(f"Slack payload requires {key}")
    return uuid.UUID(value)
