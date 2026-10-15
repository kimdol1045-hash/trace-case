"""Sprint 5 Slack HTTP resource models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from tc_api.models import ApiModel, RequestModel


class SlackUrlVerificationResponse(ApiModel):
    challenge: str


class SlackEventAckResponse(ApiModel):
    http_status: Literal[200] = 200
    acknowledged: Literal[True] = True
    outcome: Literal[
        "ACCEPTED",
        "DUPLICATE_EVENT_ID",
        "DUPLICATE_MESSAGE_IDENTITY",
        "IGNORED",
    ]
    event_log_id: str | None
    duplicate_of_event_log_id: str | None
    ignored_reason: str | None
    enqueued_job_ids: list[str]


class SlackIntegrationStatusResource(ApiModel):
    workspace_id: str
    app_id: str
    connection_status: Literal[
        "CONNECTED", "DEGRADED", "DISCONNECTED", "SETUP_REQUIRED"
    ]
    ingress_mode: Literal["HTTP_EVENTS", "SOCKET_MODE"]
    callback_state: Literal["VERIFIED", "PENDING", "FAILED", "NOT_APPLICABLE"]
    installed_at: datetime | None
    last_event_at: datetime | None
    private_default_deny: Literal[True] = True
    allowlisted_public_channel_count: int = Field(ge=0)
    explicitly_allowed_private_channel_count: int = Field(ge=0)
    resource_version: int = Field(ge=1)
    updated_at: datetime


class SlackChannelAccessResource(ApiModel):
    workspace_id: str
    channel_id: str
    channel_name: str | None
    visibility: Literal["PUBLIC", "PRIVATE"]
    collection_allowed: bool
    policy_source: Literal[
        "PUBLIC_ALLOWLIST",
        "PUBLIC_NOT_ALLOWLISTED",
        "PRIVATE_EXPLICIT_ALLOW",
        "PRIVATE_DEFAULT_DENY",
    ]
    app_membership: Literal["JOINED", "NOT_JOINED", "UNKNOWN"]
    updated_at: datetime


class SlackAllowlistPageResponse(ApiModel):
    items: list[SlackChannelAccessResource]
    next_cursor: str | None = None


class SlackAllowlistChannelInput(RequestModel):
    channelId: str = Field(pattern=r"^[CGD][A-Z0-9]{2,}$", max_length=64)
    visibility: Literal["PUBLIC", "PRIVATE"]
    confirmSharedWithWholeTeam: bool | None = None

    @model_validator(mode="after")
    def require_private_confirmation(self) -> "SlackAllowlistChannelInput":
        if self.visibility == "PRIVATE" and self.confirmSharedWithWholeTeam is not True:
            raise ValueError("private channels require confirmSharedWithWholeTeam=true")
        return self


class UpdateSlackAllowlistRequest(RequestModel):
    idempotencyKey: str = Field(min_length=8, max_length=255)
    workspaceId: str = Field(pattern=r"^T[A-Z0-9]{2,}$", max_length=64)
    expectedVersion: int = Field(ge=1)
    channels: list[SlackAllowlistChannelInput] = Field(max_length=500)

    @model_validator(mode="after")
    def unique_channels(self) -> "UpdateSlackAllowlistRequest":
        channel_ids = [channel.channelId for channel in self.channels]
        if len(channel_ids) != len(set(channel_ids)):
            raise ValueError("channelId values must be unique")
        return self


class UpdateSlackAllowlistResponse(ApiModel):
    workspace_id: str
    resource_version: int = Field(ge=2)
    channels: list[SlackChannelAccessResource]
    private_default_deny: Literal[True] = True
    updated_at: datetime


class NormalizedSlackMessageEvent(ApiModel):
    event_id: str
    workspace_id: str
    channel_id: str
    message_ts: str
    thread_ts: str
    action: Literal["ROOT", "REPLY", "EDIT", "DELETE"]
    position: Literal["ROOT", "REPLY"]
    author_id: str | None
    text: str | None
    source_event_time: int = Field(ge=0)
    occurred_at: datetime
    normalized_at: datetime


class SlackEventLogResource(ApiModel):
    id: str
    event_id: str
    workspace_id: str
    channel_id: str
    ingress_source: Literal["HTTP_EVENTS", "SOCKET_MODE", "THREAD_RESYNC"]
    payload_sha256: str
    credential_fields_redacted: Literal[True] = True
    normalized_event: NormalizedSlackMessageEvent
    delivery_outcome: Literal[
        "ACCEPTED", "DUPLICATE_EVENT_ID", "DUPLICATE_MESSAGE_IDENTITY"
    ] = "ACCEPTED"
    processing_state: Literal["RECEIVED", "PROCESSED", "FAILED_RETRYABLE", "DEAD"]
    duplicate_of_event_log_id: str | None = None
    received_at: datetime
    processed_at: datetime | None


class SlackEventLogPageResponse(ApiModel):
    items: list[SlackEventLogResource]
    next_cursor: str | None = None


class SlackWorkItemResource(ApiModel):
    id: str
    workspace_id: str
    channel_id: str
    root_message_ts: str
    thread_id: str
    status: Literal["ACTIVE", "ROOT_PENDING", "DELETED", "ARCHIVED"]
    latest_parse_id: str | None
    created_at: datetime
    updated_at: datetime


class SlackThreadResource(ApiModel):
    id: str
    work_item_id: str
    root_message_ts: str
    root_resolved: bool
    message_count: int = Field(ge=1)
    latest_changed_at: datetime
    last_synced_at: datetime | None
    sync_cursor: str | None
    parse_state: Literal[
        "PENDING", "QUIET_WINDOW", "READY", "COMPLETE", "NEEDS_INPUT", "FAILED"
    ]


class SlackMessageResource(ApiModel):
    id: str
    thread_id: str
    workspace_id: str
    channel_id: str
    message_ts: str
    thread_ts: str
    position: Literal["ROOT", "REPLY"]
    current_revision_id: str
    revision_count: int = Field(ge=1)
    deleted: bool
    first_observed_at: datetime
    latest_changed_at: datetime


class SlackMessageRevisionResource(ApiModel):
    id: str
    message_id: str
    revision: int = Field(ge=1)
    kind: Literal["CREATED", "EDITED", "DELETED"]
    body: str | None
    author_id: str | None
    source_event_id: str
    source_event_time: int = Field(ge=0)
    occurred_at: datetime
    observed_at: datetime
    previous_revision_id: str | None


class SlackParseResource(ApiModel):
    id: str
    work_item_id: str
    status: Literal["PENDING", "COMPLETE", "NEEDS_INPUT", "FAILED"]
    result: dict[str, Any] | None
    open_questions: list[str]
    source_revision_ids: list[str]
    created_at: datetime
    completed_at: datetime | None
    error_code: str | None


class GetSlackWorkItemResponse(ApiModel):
    work_item: SlackWorkItemResource
    thread: SlackThreadResource
    messages: list[SlackMessageResource]
    revisions: list[SlackMessageRevisionResource]
    parse: SlackParseResource | None


class SlackWorkItemPageResponse(ApiModel):
    items: list[SlackWorkItemResource]
    next_cursor: str | None = None


class ThreadSyncRequest(RequestModel):
    idempotencyKey: str = Field(min_length=8, max_length=255)
    workspaceId: str = Field(pattern=r"^T[A-Z0-9]{2,}$", max_length=64)
    mode: Literal["INCREMENTAL", "RECENT_RESYNC"] = "INCREMENTAL"
    sinceCursor: str | None = None
    lookbackDays: int | None = Field(default=None, ge=1, le=90)


class ThreadSyncResponse(ApiModel):
    sync_id: str
    mode: Literal["INCREMENTAL", "RECENT_RESYNC"]
    threads_scanned: int = 0
    messages_observed: int = 0
    revisions_inserted: int = 0
    duplicate_revisions: int = 0
    missing_replies_recovered: int = 0
    next_cursor: str | None = None
    started_at: datetime
    completed_at: datetime


class RetryStateResource(ApiModel):
    attempt: int = Field(ge=1)
    max_attempts: int = Field(ge=1)
    reason: Literal["RATE_LIMIT", "TRANSIENT_UPSTREAM", "TIMEOUT", "INTERNAL_ERROR"]
    last_http_status: int | None
    retry_after_seconds: int | None
    next_attempt_at: datetime | None
    last_error_code: str
    last_error_message: str


class SlackJobResource(ApiModel):
    id: str
    workspace_id: str
    channel_id: str
    work_item_id: str
    job_type: Literal[
        "QUIET_WINDOW_PARSE",
        "THREAD_INCREMENTAL_SYNC",
        "THREAD_RECENT_RESYNC",
    ]
    state: Literal["PENDING", "RUNNING", "RETRY_WAIT", "SUCCEEDED", "DEAD", "CANCELLED"]
    attempt: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    run_after: datetime | None
    retry: RetryStateResource | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class UpsertQuietWindowJobRequest(RequestModel):
    idempotencyKey: str = Field(min_length=8, max_length=255)
    workItemId: str = Field(pattern=r"^slack_work_item_[A-Za-z0-9_-]+$", max_length=128)
    lastThreadChangedAt: datetime
    quietWindowSeconds: int = Field(default=600, ge=60, le=3600)
    sourceRevisionId: str = Field(pattern=r"^slack_revision_[A-Za-z0-9_-]+$", max_length=128)
    expectedGeneration: int = Field(ge=0)


class UpsertQuietWindowJobResponse(ApiModel):
    outcome: Literal["CREATED", "RESCHEDULED", "UNCHANGED"]
    job: SlackJobResource


class CancelQuietWindowJobRequest(RequestModel):
    idempotencyKey: str = Field(min_length=8, max_length=255)
    jobId: str = Field(pattern=r"^slack_job_[A-Za-z0-9_-]+$", max_length=128)
    expectedGeneration: int = Field(ge=1)
    reason: Literal["NEW_THREAD_ACTIVITY", "WORK_ITEM_DELETED", "MANUAL_CANCEL"]


class CancelQuietWindowJobResponse(ApiModel):
    cancelled: bool
    job: SlackJobResource


class SlackJobPageResponse(ApiModel):
    items: list[SlackJobResource]
    next_cursor: str | None = None
