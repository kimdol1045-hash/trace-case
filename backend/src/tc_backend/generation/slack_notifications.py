"""Durable automatic-generation Slack terminal notification protocol.

Application services call the capture methods inside their existing transaction.
Delivery commits a short provider permit, releases PostgreSQL for the network call,
and commits only sanitized observations and receipts afterwards.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tc_backend.db.constants import DEFAULT_WORKSPACE_ID
from tc_backend.db.jobs import ClaimedJob, JobRepository
from tc_backend.db.models import (
    BackgroundJob,
    GenerationApply,
    GenerationApplyOutcome,
    GenerationExecutionSchedule,
    GenerationJob,
    GenerationJobStatus,
    GenerationMaterializationSchedule,
    GenerationRetryExhaustion,
    GenerationSlackNotificationDeliveryAttempt,
    GenerationSlackNotificationDeliveryObservation,
    GenerationSlackNotificationIntent,
    GenerationSlackNotificationReceipt,
    GenerationSuggestionMaterialization,
    GenerationTrigger,
    JobStatus,
    SlackChannelAllowlist,
    SlackMessage,
    SlackMessageRevision,
    SlackThread,
    SlackWorkItem,
)
from tc_backend.infrastructure.slack.errors import (
    SlackAmbiguousWriteError,
    SlackAuthError,
    SlackPermanentWriteError,
    SlackRateLimitError,
)
from tc_backend.infrastructure.slack.transport import SlackWebApiTransport

TEMPLATE_VERSION = "automatic-generation-slack-reply/1.0.0"
NOTIFICATION_JOB_TYPE = "SLACK_GENERATION_NOTIFICATION_DELIVERY"
_CLIENT_MESSAGE_NAMESPACE = uuid.UUID("8f9d2cb9-2864-53a5-a055-7494976cf06a")
_SAFE_PROVIDER_CODE = re.compile(r"^[a-z0-9_]{1,80}$")
_ERROR_LABELS = {
    "GENERATION_UNSUPPORTED_SCHEMA_VERSION": "지원하지 않는 결과 형식",
    "GENERATION_AUTH_REJECTED": "생성 서비스 인증 거부",
    "GENERATION_TIMEOUT": "생성 서비스 응답 시간 초과",
    "GENERATION_TRANSPORT_UNAVAILABLE": "생성 서비스 연결 실패",
    "GENERATION_RATE_LIMITED": "생성 서비스 요청 제한",
    "GENERATION_PROVIDER_REJECTED": "생성 서비스 요청 거부",
    "GENERATION_PROVIDER_ERROR": "생성 서비스 오류",
    "GENERATION_INVALID_CONTENT_TYPE": "잘못된 응답 형식",
    "GENERATION_RESPONSE_TOO_LARGE": "응답 크기 제한 초과",
    "GENERATION_MALFORMED_JSON": "잘못된 JSON 응답",
    "GENERATION_INVALID_RESPONSE": "검증할 수 없는 생성 결과",
}


class GenerationSlackNotificationConflict(RuntimeError):
    """Persisted authorities cannot support the requested immutable intent."""


class GenerationSlackNotificationRetry(RuntimeError):
    """A durable observation requires a later worker attempt."""

    def __init__(self, reason: str, *, retry_after_seconds: float) -> None:
        super().__init__(reason)
        self.retry_after_seconds = max(0.0, retry_after_seconds)


@dataclass(frozen=True, slots=True)
class _Destination:
    item: SlackWorkItem
    disposition: str


class GenerationSlackNotificationService:
    """Capture notification truth without committing or contacting Slack."""

    def __init__(
        self,
        session: Session,
        *,
        workspace_id: uuid.UUID = DEFAULT_WORKSPACE_ID,
        public_web_base_url: str | None = None,
    ) -> None:
        self.session = session
        self.workspace_id = workspace_id
        self.public_web_base_url = public_web_base_url

    def capture_applied_success(
        self, generation_apply_id: uuid.UUID
    ) -> GenerationSlackNotificationIntent | None:
        apply_row = self.session.scalar(
            select(GenerationApply)
            .where(GenerationApply.id == generation_apply_id)
            .with_for_update()
        )
        if apply_row is None:
            raise GenerationSlackNotificationConflict("generation apply does not exist")
        job = self._job(apply_row.generation_job_id)
        if not self._eligible_automatic(job):
            return None
        if job.status != GenerationJobStatus.SUCCEEDED.value:
            raise GenerationSlackNotificationConflict("apply job is not successful")
        self._ensure_materialization_authority(
            job,
            mode="AUTOMATIC_APPLY",
            generation_apply_id=apply_row.id,
        )
        outcomes = tuple(
            self.session.scalars(
                select(GenerationApplyOutcome)
                .where(GenerationApplyOutcome.generation_apply_id == apply_row.id)
                .order_by(GenerationApplyOutcome.generated_case_index)
            )
        )
        counts = cast(dict[str, Any], apply_row.receipt.get("counts"))
        created = sum(row.outcome == "CREATED" for row in outcomes)
        reused = sum(row.outcome == "REUSED" for row in outcomes)
        if not outcomes or counts != {
            "generated": len(outcomes),
            "created": created,
            "reused": reused,
        }:
            raise GenerationSlackNotificationConflict("apply receipt counts are inconsistent")
        parent = apply_row.receipt.get("parent")
        if not isinstance(parent, dict) or parent.get("outcome") not in {"CREATED", "REUSED"}:
            raise GenerationSlackNotificationConflict("apply parent receipt is invalid")
        message = render_applied_success(
            created_count=created,
            reused_count=reused,
            parent_outcome=cast(Literal["CREATED", "REUSED"], parent["outcome"]),
            ui_url=self._ui_url(job.id),
        )
        return self._capture(
            job=job,
            kind="SUCCESS_APPLIED",
            message=message,
            generation_apply_id=apply_row.id,
        )

    def capture_suggestion_success(
        self, materialization_id: uuid.UUID
    ) -> GenerationSlackNotificationIntent | None:
        materialization = self.session.scalar(
            select(GenerationSuggestionMaterialization)
            .where(GenerationSuggestionMaterialization.id == materialization_id)
            .with_for_update()
        )
        if materialization is None:
            raise GenerationSlackNotificationConflict("suggestion materialization does not exist")
        job = self._job(materialization.generation_job_id)
        if not self._eligible_automatic(job):
            return None
        if job.status != GenerationJobStatus.SUCCEEDED.value:
            raise GenerationSlackNotificationConflict(
                "suggestion materialization job is not successful"
            )
        self._ensure_materialization_authority(
            job,
            mode="AUTOMATIC_SUGGESTIONS",
            suggestion_materialization_id=materialization.id,
        )
        message = render_suggestion_success(
            suggestion_count=materialization.suggestion_count,
            ui_url=self._ui_url(job.id),
        )
        return self._capture(
            job=job,
            kind="SUCCESS_SUGGESTIONS",
            message=message,
            generation_change_assessment_id=(materialization.generation_change_assessment_id),
        )

    def capture_final_failure(
        self,
        generation_job_id: uuid.UUID,
    ) -> GenerationSlackNotificationIntent | None:
        job = self._job(generation_job_id)
        if not self._eligible_automatic(job):
            return None
        if job.status != GenerationJobStatus.FAILED.value:
            raise GenerationSlackNotificationConflict("generation job is not failed")
        exhaustion = self.session.scalar(
            select(GenerationRetryExhaustion).where(
                GenerationRetryExhaustion.root_job_id == job.root_job_id
            )
        )
        if exhaustion is None and job.error_retryable:
            return None
        if exhaustion is None:
            item = self.session.get(SlackWorkItem, job.slack_work_item_id)
            if item is None or item.workspace_id != self.workspace_id:
                raise GenerationSlackNotificationConflict("generation failure workspace is invalid")
            schedule = self.session.scalar(
                select(GenerationExecutionSchedule).where(
                    GenerationExecutionSchedule.generation_job_id == job.id
                )
            )
            exhaustion = GenerationRetryExhaustion(
                id=uuid.uuid4(),
                workspace_id=item.workspace_id,
                root_job_id=job.root_job_id,
                terminal_failed_job_id=job.id,
                finality="TERMINAL_NON_RETRYABLE",
                retryable=False,
                provider_call_budget=(schedule.provider_call_budget if schedule else 5),
                generation_job_budget=(
                    schedule.generation_job_budget if schedule else max(job.attempt_number, 1)
                ),
                error_code=job.error_code or "GENERATION_PROVIDER_ERROR",
            )
            self.session.add(exhaustion)
            self.session.flush()
        message = render_failure(
            attempt_number=job.attempt_number,
            error_code=job.error_code,
            # T06's terminal Slack contract means no automatic retry remains.
            # Operator conditional retry is advertised only by the T07 failure UI.
            retryable=False,
            ui_url=self._ui_url(job.id),
        )
        return self._capture(job=job, kind="FAILURE", message=message)

    def _ensure_materialization_authority(
        self,
        job: GenerationJob,
        *,
        mode: Literal["AUTOMATIC_APPLY", "AUTOMATIC_SUGGESTIONS"],
        generation_apply_id: uuid.UUID | None = None,
        suggestion_materialization_id: uuid.UUID | None = None,
    ) -> GenerationMaterializationSchedule:
        existing = self.session.scalar(
            select(GenerationMaterializationSchedule)
            .where(GenerationMaterializationSchedule.generation_job_id == job.id)
            .with_for_update()
        )
        if existing is None:
            # Materialization authority is created together with provider
            # finalization and must be claimed by the background worker.  A
            # notification path must never fabricate an already-successful
            # delivery merely because application data happens to exist.
            raise GenerationSlackNotificationConflict(
                "automatic generation materialization schedule is missing"
            )
        item = self.session.get(SlackWorkItem, job.slack_work_item_id)
        if item is None or item.workspace_id != self.workspace_id:
            raise GenerationSlackNotificationConflict(
                "generation materialization workspace is invalid"
            )
        if (
            existing.workspace_id != item.workspace_id
            or existing.root_job_id != job.root_job_id
            or existing.mode != mode
        ):
            raise GenerationSlackNotificationConflict(
                "generation materialization authority is inconsistent"
            )
        if existing.state == "COMMITTED":
            if (
                existing.generation_apply_id != generation_apply_id
                or existing.suggestion_materialization_id
                != suggestion_materialization_id
            ):
                raise GenerationSlackNotificationConflict(
                    "generation materialization receipt is inconsistent"
                )
            return existing
        now = datetime.now(UTC)
        background = self.session.get(BackgroundJob, existing.background_job_id)
        if (
            existing.state != "LEASED"
            or existing.lease_owner is None
            or existing.lease_token is None
            or existing.lease_expires_at is None
            or existing.lease_expires_at <= now
            or background is None
            or background.job_type != "AUTOMATIC_GENERATION_MATERIALIZE"
            or background.status != JobStatus.RUNNING.value
            or background.locked_by != existing.lease_owner
        ):
            raise GenerationSlackNotificationConflict(
                "automatic generation materialization lease is not owned"
            )
        existing.generation_apply_id = generation_apply_id
        existing.suggestion_materialization_id = suggestion_materialization_id
        existing.state = "COMMITTED"
        existing.lease_owner = None
        existing.lease_token = None
        existing.lease_expires_at = None
        existing.resource_version += 1
        existing.updated_at = now
        self.session.flush()
        return existing

    def _job(self, job_id: uuid.UUID) -> GenerationJob:
        job = self.session.scalar(
            select(GenerationJob).where(GenerationJob.id == job_id).with_for_update()
        )
        if job is None:
            raise GenerationSlackNotificationConflict("generation job does not exist")
        return job

    def _eligible_automatic(self, job: GenerationJob) -> bool:
        root = self.session.scalar(
            select(GenerationJob).where(GenerationJob.id == job.root_job_id).with_for_update()
        )
        if root is None:
            raise GenerationSlackNotificationConflict("generation root does not exist")
        return (
            job.generation_trigger == GenerationTrigger.AUTOMATIC.value
            and root.generation_trigger == GenerationTrigger.AUTOMATIC.value
            and root.slack_work_item_id == job.slack_work_item_id
            and root.source_version == job.source_version
        )

    def _capture(
        self,
        *,
        job: GenerationJob,
        kind: str,
        message: dict[str, Any],
        generation_apply_id: uuid.UUID | None = None,
        generation_change_assessment_id: uuid.UUID | None = None,
    ) -> GenerationSlackNotificationIntent:
        replay = self.session.scalar(
            select(GenerationSlackNotificationIntent).where(
                GenerationSlackNotificationIntent.generation_job_id == job.id
            )
        )
        if replay is not None:
            if replay.notification_kind != kind or replay.message_snapshot != message:
                raise GenerationSlackNotificationConflict(
                    "notification replay differs from the frozen intent"
                )
            return replay
        root_terminal = self.session.scalar(
            select(GenerationSlackNotificationIntent).where(
                GenerationSlackNotificationIntent.generation_root_job_id == job.root_job_id
            )
        )
        if root_terminal is not None:
            return root_terminal
        destination = _locked_destination(
            self.session,
            job=job,
            workspace_id=self.workspace_id,
            token_workspace_id=None,
        )
        intent_id = uuid.uuid4()
        status = job.status
        notification_key = ":".join(
            (TEMPLATE_VERSION, _identifier("generation_job", job.id), status)
        )
        client_message_id = uuid.uuid5(_CLIENT_MESSAGE_NAMESPACE, notification_key)
        metadata = {
            "event_type": "tc_generation_terminal_notification_v1",
            "event_payload": {
                "notification_id": _identifier("generation_slack_notification", intent_id),
                "generation_job_id": _identifier("generation_job", job.id),
                "outcome": kind,
                "contract_version": "1.0.0",
            },
        }
        intent = GenerationSlackNotificationIntent(
            id=intent_id,
            workspace_id=destination.item.workspace_id,
            slack_work_item_id=destination.item.id,
            generation_root_job_id=job.root_job_id,
            generation_job_id=job.id,
            generation_apply_id=generation_apply_id,
            generation_change_assessment_id=generation_change_assessment_id,
            source_version=job.source_version,
            attempt_number=job.attempt_number,
            terminal_status=status,
            notification_kind=kind,
            delivery_disposition=destination.disposition,
            suppression_reason=(
                None if destination.disposition == "POST" else destination.disposition
            ),
            slack_workspace_id=destination.item.slack_workspace_id,
            channel_id=destination.item.channel_id,
            root_message_ts=destination.item.message_ts,
            thread_ts=destination.item.message_ts,
            template_version=TEMPLATE_VERSION,
            locale="ko-KR",
            message_snapshot=message,
            message_text=cast(str, message["messageText"]),
            message_digest=cast(str, message["messageDigest"]),
            link_mode=cast(str, cast(dict[str, Any], message["link"])["linkMode"]),
            ui_url=cast(dict[str, Any], message["link"])["uiUrl"],
            notification_key=notification_key,
            client_message_id=client_message_id,
            message_metadata=metadata,
        )
        self.session.add(intent)
        self.session.flush()
        if destination.disposition == "POST":
            JobRepository(self.session).enqueue(
                job_type=NOTIFICATION_JOB_TYPE,
                payload={"notificationIntentId": str(intent.id)},
                max_attempts=12,
                idempotency_key=f"slack-generation-notification:{intent.id}",
            )
        else:
            self.session.add(
                GenerationSlackNotificationReceipt(
                    id=uuid.uuid4(),
                    intent_id=intent.id,
                    terminal_outcome="SUPPRESSED",
                    reason=destination.disposition,
                )
            )
        self.session.flush()
        return intent

    def _ui_url(self, job_id: uuid.UUID) -> str | None:
        if self.public_web_base_url is None:
            return None
        parsed = urlsplit(self.public_web_base_url)
        hostname = parsed.hostname
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or hostname is None
            or hostname.casefold() == "localhost"
            or hostname.casefold().endswith(".localhost")
            or hostname.casefold().endswith(".trycloudflare.com")
            or any(character in self.public_web_base_url for character in "<>|\\\r\n\t ")
            or _is_non_public_ip(hostname)
        ):
            raise GenerationSlackNotificationConflict(
                "public Web base URL is not a trusted HTTPS authority"
            )
        base_path = parsed.path.rstrip("/")
        path = f"{base_path}/generation/jobs/{_identifier('generation_job', job_id)}"
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


class GenerationSlackNotificationDelivery:
    """Network delivery protocol with committed permits and fail-safe ambiguity."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        transport: SlackWebApiTransport,
        token_workspace_id: str | None,
        workspace_id: uuid.UUID = DEFAULT_WORKSPACE_ID,
        lease_seconds: int = 60,
        reconcile_window_seconds: int = 86_400,
        retry_base_seconds: int = 5,
        retry_max_seconds: int = 3_600,
        breaker_open: bool = False,
    ) -> None:
        self.session_factory = session_factory
        self.transport = transport
        self.token_workspace_id = token_workspace_id
        self.workspace_id = workspace_id
        self.lease_seconds = lease_seconds
        self.reconcile_window_seconds = reconcile_window_seconds
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.breaker_open = breaker_open

    def deliver(self, intent_id: uuid.UUID, claim: ClaimedJob) -> None:
        attempt_id, operation = self._prepare(intent_id, claim)
        if attempt_id is None:
            return
        with self.session_factory() as read_session:
            intent = read_session.get(GenerationSlackNotificationIntent, intent_id)
            if intent is None:  # pragma: no cover - protected by attempt FK
                raise GenerationSlackNotificationConflict("notification intent disappeared")
            frozen = {
                "channel_id": intent.channel_id,
                "thread_ts": intent.thread_ts,
                "text": intent.message_text,
                "client_message_id": str(intent.client_message_id),
                "metadata": dict(intent.message_metadata),
                "notification_id": _identifier("generation_slack_notification", intent.id),
            }
            first_prepared_at = (
                read_session.scalar(
                    select(GenerationSlackNotificationDeliveryAttempt.prepared_at)
                    .where(
                        GenerationSlackNotificationDeliveryAttempt.intent_id == intent.id,
                        GenerationSlackNotificationDeliveryAttempt.operation == "POST",
                    )
                    .order_by(GenerationSlackNotificationDeliveryAttempt.prepared_at)
                    .limit(1)
                )
                or intent.created_at
            )
        if operation == "POST":
            self._post(attempt_id, intent_id, claim, frozen)
            return
        self._reconcile(
            attempt_id,
            intent_id,
            claim,
            frozen,
            first_prepared_at=first_prepared_at,
        )

    def _prepare(self, intent_id: uuid.UUID, claim: ClaimedJob) -> tuple[uuid.UUID | None, str]:
        with self.session_factory() as session, session.begin():
            intent = session.scalar(
                select(GenerationSlackNotificationIntent)
                .where(GenerationSlackNotificationIntent.id == intent_id)
                .with_for_update()
            )
            if intent is None:
                raise GenerationSlackNotificationConflict("notification intent does not exist")
            if (
                session.scalar(
                    select(GenerationSlackNotificationReceipt.id).where(
                        GenerationSlackNotificationReceipt.intent_id == intent.id
                    )
                )
                is not None
            ):
                return None, "POST"
            if self.breaker_open:
                self._suppress(session, intent, "SUPPRESSED_CONFIGURATION")
                return None, "POST"
            job = session.get(GenerationJob, intent.generation_job_id)
            if job is None:
                raise GenerationSlackNotificationConflict("intent job does not exist")
            destination = _locked_destination(
                session,
                job=job,
                workspace_id=self.workspace_id,
                token_workspace_id=self.token_workspace_id,
            )
            if destination.disposition != "POST":
                self._suppress(session, intent, destination.disposition)
                return None, "POST"
            unresolved_post = session.scalar(
                select(GenerationSlackNotificationDeliveryAttempt.id)
                .outerjoin(
                    GenerationSlackNotificationDeliveryObservation,
                    GenerationSlackNotificationDeliveryObservation.attempt_id
                    == GenerationSlackNotificationDeliveryAttempt.id,
                )
                .where(
                    GenerationSlackNotificationDeliveryAttempt.intent_id == intent.id,
                    GenerationSlackNotificationDeliveryAttempt.operation == "POST",
                    (
                        GenerationSlackNotificationDeliveryObservation.id.is_(None)
                        | GenerationSlackNotificationDeliveryObservation.classification.not_in(
                            ("DEFINITIVE_NO_DELIVERY", "RATE_LIMITED")
                        )
                    ),
                )
                .limit(1)
            )
            operation = "RECONCILE" if unresolved_post is not None else "POST"
            prepared_at = cast(datetime, session.scalar(select(func.now())))
            if operation == "POST":
                channel_key = f"{intent.slack_workspace_id}:{intent.channel_id}"
                session.execute(
                    select(func.pg_advisory_xact_lock(func.hashtextextended(channel_key, 0)))
                ).scalar_one()
                last_channel_post = session.scalar(
                    select(func.max(GenerationSlackNotificationDeliveryAttempt.prepared_at))
                    .join(
                        GenerationSlackNotificationIntent,
                        GenerationSlackNotificationIntent.id
                        == GenerationSlackNotificationDeliveryAttempt.intent_id,
                    )
                    .where(
                        GenerationSlackNotificationDeliveryAttempt.operation == "POST",
                        GenerationSlackNotificationIntent.slack_workspace_id
                        == intent.slack_workspace_id,
                        GenerationSlackNotificationIntent.channel_id == intent.channel_id,
                    )
                )
                channel_ready_at = (
                    last_channel_post + timedelta(seconds=1)
                    if last_channel_post is not None
                    else prepared_at
                )
                if channel_ready_at > prepared_at:
                    raise GenerationSlackNotificationRetry(
                        "Slack notification channel rate floor has not elapsed",
                        retry_after_seconds=(channel_ready_at - prepared_at).total_seconds(),
                    )
            request = _request_snapshot(intent)
            attempt = GenerationSlackNotificationDeliveryAttempt(
                id=uuid.uuid4(),
                intent_id=intent.id,
                background_job_id=claim.id,
                background_job_attempt=claim.attempt,
                operation=operation,
                worker_id=claim.locked_by,
                lease_token=uuid.uuid4(),
                lease_expires_at=prepared_at + timedelta(seconds=self.lease_seconds),
                request_digest=_digest(request),
                prepared_at=prepared_at,
            )
            session.add(attempt)
            session.flush()
            return attempt.id, operation

    def _post(
        self,
        attempt_id: uuid.UUID,
        intent_id: uuid.UUID,
        claim: ClaimedJob,
        frozen: dict[str, Any],
    ) -> None:
        try:
            acknowledged = self.transport.post_thread_reply(
                channel_id=cast(str, frozen["channel_id"]),
                thread_ts=cast(str, frozen["thread_ts"]),
                text=cast(str, frozen["text"]),
                client_message_id=cast(str, frozen["client_message_id"]),
                metadata=cast(dict[str, Any], frozen["metadata"]),
            )
        except SlackRateLimitError as error:
            retry_after = max(1, math.ceil(error.retry_after_seconds or 1))
            if claim.attempt >= claim.max_attempts:
                self._dead_after_error(
                    attempt_id,
                    intent_id,
                    claim,
                    provider_code="ratelimited",
                    http_status=429,
                    diagnostic="RATE_LIMITED",
                    reason="RATE_LIMIT_EXHAUSTED",
                    classification="RATE_LIMITED",
                    retry_after_seconds=retry_after,
                )
                return
            self._observe(
                attempt_id,
                claim,
                classification="RATE_LIMITED",
                provider_code="ratelimited",
                http_status=429,
                retry_after_seconds=retry_after,
                safe_diagnostic="RATE_LIMITED",
            )
            raise GenerationSlackNotificationRetry(
                "Slack notification is rate limited",
                retry_after_seconds=max(retry_after, self._backoff(claim.attempt)),
            ) from error
        except SlackAuthError as error:
            self._dead_after_error(
                attempt_id,
                intent_id,
                claim,
                provider_code=error.provider_code or "auth_rejected",
                http_status=error.status_code,
                diagnostic="AUTH_REJECTED",
                reason="AUTH_REJECTED",
            )
            return
        except SlackPermanentWriteError as error:
            self._dead_after_error(
                attempt_id,
                intent_id,
                claim,
                provider_code=error.provider_code,
                http_status=error.status_code,
                diagnostic="DESTINATION_REJECTED",
                reason="DESTINATION_REJECTED",
            )
            return
        except SlackAmbiguousWriteError as error:
            self._observe(
                attempt_id,
                claim,
                classification="AMBIGUOUS",
                provider_code=error.provider_code,
                http_status=error.status_code,
                safe_diagnostic="WRITE_AMBIGUOUS",
            )
            if claim.attempt >= claim.max_attempts:
                reconcile_attempt_id, operation = self._prepare(intent_id, claim)
                if reconcile_attempt_id is None:
                    return
                if operation != "RECONCILE":  # pragma: no cover - DB guard backstop
                    raise GenerationSlackNotificationConflict(
                        "final ambiguous write did not produce reconciliation permit"
                    ) from error
                self._reconcile(
                    reconcile_attempt_id,
                    intent_id,
                    claim,
                    frozen,
                    first_prepared_at=datetime.now(UTC),
                )
                return
            raise GenerationSlackNotificationRetry(
                "Slack notification write is ambiguous; reconciliation is required",
                retry_after_seconds=self._backoff(claim.attempt),
            ) from error
        if (
            acknowledged.channel_id != frozen["channel_id"]
            or acknowledged.message_ts == frozen["thread_ts"]
        ):
            self._dead_after_error(
                attempt_id,
                intent_id,
                claim,
                provider_code="destination_mismatch",
                http_status=None,
                diagnostic="RECONCILIATION_CONFLICT",
                reason="RECONCILIATION_CONFLICT",
                classification="AMBIGUOUS",
            )
            return
        self._delivered(
            attempt_id,
            intent_id,
            claim,
            message_ts=acknowledged.message_ts,
            proof="POST_ACK",
            classification="ACKNOWLEDGED",
            diagnostic="ACK_VALID",
        )

    def _reconcile(
        self,
        attempt_id: uuid.UUID,
        intent_id: uuid.UUID,
        claim: ClaimedJob,
        frozen: dict[str, Any],
        *,
        first_prepared_at: datetime,
    ) -> None:
        try:
            result = self.transport.find_thread_reply_by_notification(
                channel_id=cast(str, frozen["channel_id"]),
                thread_ts=cast(str, frozen["thread_ts"]),
                notification_id=cast(str, frozen["notification_id"]),
                client_message_id=cast(str, frozen["client_message_id"]),
                expected_text=cast(str, frozen["text"]),
            )
        except SlackRateLimitError as error:
            retry_after = max(1, math.ceil(error.retry_after_seconds or 1))
            if claim.attempt >= claim.max_attempts:
                self._dead_after_error(
                    attempt_id,
                    intent_id,
                    claim,
                    provider_code="ratelimited",
                    http_status=429,
                    diagnostic="RATE_LIMITED",
                    reason="RATE_LIMIT_EXHAUSTED",
                    classification="RATE_LIMITED",
                    retry_after_seconds=retry_after,
                )
                return
            self._observe(
                attempt_id,
                claim,
                classification="RATE_LIMITED",
                provider_code="ratelimited",
                http_status=429,
                retry_after_seconds=retry_after,
                safe_diagnostic="RATE_LIMITED",
            )
            raise GenerationSlackNotificationRetry(
                "Slack notification reconciliation is rate limited",
                retry_after_seconds=max(retry_after, self._backoff(claim.attempt)),
            ) from error
        except (SlackAuthError, SlackPermanentWriteError) as error:
            provider_code = getattr(error, "provider_code", None) or "auth_rejected"
            self._dead_after_error(
                attempt_id,
                intent_id,
                claim,
                provider_code=provider_code,
                http_status=getattr(error, "status_code", None),
                diagnostic="DESTINATION_REJECTED",
                reason="RECONCILIATION_UNAVAILABLE",
                classification="AMBIGUOUS",
            )
            return
        except Exception as error:
            if claim.attempt >= claim.max_attempts:
                self._dead_after_error(
                    attempt_id,
                    intent_id,
                    claim,
                    provider_code="reconciliation_unavailable",
                    http_status=None,
                    diagnostic="WRITE_AMBIGUOUS",
                    reason="AMBIGUOUS_UNRESOLVED",
                    classification="AMBIGUOUS",
                )
                return
            self._observe(
                attempt_id,
                claim,
                classification="AMBIGUOUS",
                provider_code="reconciliation_unavailable",
                safe_diagnostic="WRITE_AMBIGUOUS",
            )
            raise GenerationSlackNotificationRetry(
                "Slack notification reconciliation is temporarily unavailable",
                retry_after_seconds=self._backoff(claim.attempt),
            ) from error
        if len(result.matches) == 1:
            match = result.matches[0]
            if match.channel_id != frozen["channel_id"] or match.message_ts == frozen["thread_ts"]:
                self._dead_after_error(
                    attempt_id,
                    intent_id,
                    claim,
                    provider_code="destination_mismatch",
                    http_status=None,
                    diagnostic="RECONCILIATION_CONFLICT",
                    reason="RECONCILIATION_CONFLICT",
                    classification="AMBIGUOUS",
                )
                return
            self._delivered(
                attempt_id,
                intent_id,
                claim,
                message_ts=match.message_ts,
                proof="THREAD_RECONCILIATION",
                classification="RECONCILED_FOUND",
                diagnostic="RECONCILIATION_FOUND",
            )
            return
        if len(result.matches) > 1:
            self._dead_after_error(
                attempt_id,
                intent_id,
                claim,
                provider_code="duplicate_correlation",
                http_status=None,
                diagnostic="RECONCILIATION_CONFLICT",
                reason="RECONCILIATION_CONFLICT",
                classification="AMBIGUOUS",
            )
            return
        age = datetime.now(UTC) - first_prepared_at
        if (
            age >= timedelta(seconds=self.reconcile_window_seconds)
            or claim.attempt >= claim.max_attempts
        ):
            self._dead_after_error(
                attempt_id,
                intent_id,
                claim,
                provider_code="not_found_at_horizon",
                http_status=None,
                diagnostic="RECONCILIATION_NOT_FOUND",
                reason="AMBIGUOUS_UNRESOLVED",
                classification="RECONCILED_NOT_FOUND",
            )
            return
        self._observe(
            attempt_id,
            claim,
            classification="RECONCILED_NOT_FOUND",
            provider_code="not_found",
            safe_diagnostic="RECONCILIATION_NOT_FOUND",
        )
        raise GenerationSlackNotificationRetry(
            "Slack notification remains ambiguous inside the reconciliation window",
            retry_after_seconds=self._backoff(claim.attempt),
        )

    def _observe(
        self,
        attempt_id: uuid.UUID,
        claim: ClaimedJob,
        *,
        classification: str,
        provider_code: str | None,
        http_status: int | None = None,
        retry_after_seconds: int | None = None,
        safe_diagnostic: str | None = None,
    ) -> None:
        with self.session_factory() as session, session.begin():
            _require_claim(session, claim)
            session.add(
                GenerationSlackNotificationDeliveryObservation(
                    id=uuid.uuid4(),
                    attempt_id=attempt_id,
                    classification=classification,
                    provider_code=_provider_code(provider_code),
                    http_status=http_status,
                    retry_after_seconds=retry_after_seconds,
                    safe_diagnostic=safe_diagnostic,
                )
            )

    def _delivered(
        self,
        attempt_id: uuid.UUID,
        intent_id: uuid.UUID,
        claim: ClaimedJob,
        *,
        message_ts: str,
        proof: str,
        classification: str,
        diagnostic: str,
    ) -> None:
        with self.session_factory() as session, session.begin():
            _require_claim(session, claim)
            intent = session.get(GenerationSlackNotificationIntent, intent_id)
            if intent is None:
                raise GenerationSlackNotificationConflict("notification intent disappeared")
            now = datetime.now(UTC)
            session.add(
                GenerationSlackNotificationDeliveryObservation(
                    id=uuid.uuid4(),
                    attempt_id=attempt_id,
                    classification=classification,
                    safe_diagnostic=diagnostic,
                    observed_at=now,
                )
            )
            session.add(
                GenerationSlackNotificationReceipt(
                    id=uuid.uuid4(),
                    intent_id=intent.id,
                    attempt_id=attempt_id,
                    terminal_outcome="DELIVERED",
                    proof=proof,
                    slack_workspace_id=intent.slack_workspace_id,
                    channel_id=intent.channel_id,
                    root_message_ts=intent.root_message_ts,
                    message_ts=message_ts,
                    client_message_id=intent.client_message_id,
                    message_digest=intent.message_digest,
                    acknowledged_at=now,
                    recorded_at=now,
                )
            )

    def _dead_after_error(
        self,
        attempt_id: uuid.UUID,
        intent_id: uuid.UUID,
        claim: ClaimedJob,
        *,
        provider_code: str,
        http_status: int | None,
        diagnostic: str,
        reason: str,
        classification: str = "DEFINITIVE_NO_DELIVERY",
        retry_after_seconds: int | None = None,
    ) -> None:
        with self.session_factory() as session, session.begin():
            _require_claim(session, claim)
            intent = session.get(GenerationSlackNotificationIntent, intent_id)
            if intent is None:
                raise GenerationSlackNotificationConflict("notification intent disappeared")
            session.add(
                GenerationSlackNotificationDeliveryObservation(
                    id=uuid.uuid4(),
                    attempt_id=attempt_id,
                    classification=classification,
                    provider_code=_provider_code(provider_code),
                    http_status=http_status,
                    retry_after_seconds=retry_after_seconds,
                    safe_diagnostic=diagnostic,
                )
            )
            session.add(
                GenerationSlackNotificationReceipt(
                    id=uuid.uuid4(),
                    intent_id=intent.id,
                    attempt_id=attempt_id,
                    terminal_outcome="DEAD",
                    reason=reason,
                    slack_workspace_id=intent.slack_workspace_id,
                    channel_id=intent.channel_id,
                    root_message_ts=intent.root_message_ts,
                    client_message_id=intent.client_message_id,
                    message_digest=intent.message_digest,
                )
            )

    @staticmethod
    def _suppress(
        session: Session,
        intent: GenerationSlackNotificationIntent,
        reason: str,
    ) -> None:
        session.add(
            GenerationSlackNotificationReceipt(
                id=uuid.uuid4(),
                intent_id=intent.id,
                terminal_outcome="SUPPRESSED",
                reason=reason,
            )
        )

    def _backoff(self, attempt: int) -> float:
        return float(min(self.retry_max_seconds, self.retry_base_seconds * (2 ** (attempt - 1))))


def render_applied_success(
    *,
    created_count: int,
    reused_count: int,
    parent_outcome: Literal["CREATED", "REUSED"],
    ui_url: str | None,
) -> dict[str, Any]:
    if created_count < 0 or reused_count < 0:
        raise ValueError("apply counts cannot be negative")
    parent_label = "새로 등록" if parent_outcome == "CREATED" else "기존 일감 재사용(변경 없음)"
    text = "\n".join(
        (
            "✅ 자동 테스트케이스 생성이 완료되었습니다.",
            f"• 새 테스트케이스: {created_count}건",
            f"• 기존 테스트케이스 재사용(변경 없음): {reused_count}건",
            f"• 상위 일감: {parent_label}",
        )
    ) + _link_line(ui_url)
    message: dict[str, Any] = {
        "templateVersion": TEMPLATE_VERSION,
        "locale": "ko-KR",
        "outcome": "SUCCESS_APPLIED",
        "createdCount": created_count,
        "reusedCount": reused_count,
        "generatedCount": created_count + reused_count,
        "parentOutcome": parent_outcome,
        "link": _link(ui_url),
        "messageText": text,
    }
    message["messageDigest"] = _digest(message)
    return message


def render_suggestion_success(*, suggestion_count: int, ui_url: str | None) -> dict[str, Any]:
    if suggestion_count < 0:
        raise ValueError("suggestion count cannot be negative")
    count_line = (
        "• 변경 제안: 0건(변경 없음)"
        if suggestion_count == 0
        else f"• 변경 제안: {suggestion_count}건"
    )
    text = "\n".join(
        (
            "✅ 자동 테스트케이스 변경 제안이 준비되었습니다.",
            count_line,
            "• 자동 덮어쓰기: 0건",
        )
    ) + _link_line(ui_url)
    message: dict[str, Any] = {
        "templateVersion": TEMPLATE_VERSION,
        "locale": "ko-KR",
        "outcome": "SUCCESS_SUGGESTIONS",
        "suggestionCount": suggestion_count,
        "automaticOverwriteCount": 0,
        "link": _link(ui_url),
        "messageText": text,
    }
    message["messageDigest"] = _digest(message)
    return message


def render_failure(
    *,
    attempt_number: int,
    error_code: str | None,
    retryable: bool,
    ui_url: str | None,
) -> dict[str, Any]:
    if attempt_number < 1:
        raise ValueError("attempt number must be positive")
    safe_code = error_code if error_code in _ERROR_LABELS else "GENERATION_PROVIDER_ERROR"
    label = _ERROR_LABELS[safe_code]
    safe_retryable = retryable if error_code in _ERROR_LABELS else False
    text = "\n".join(
        (
            "⚠️ 자동 테스트케이스 생성에 실패했습니다.",
            f"• 생성 시도: {attempt_number}차",
            f"• 오류 코드: {label} ({safe_code})",
            f"• 재시도 가능: {'가능' if safe_retryable else '불가'}",
        )
    ) + _link_line(ui_url)
    message: dict[str, Any] = {
        "templateVersion": TEMPLATE_VERSION,
        "locale": "ko-KR",
        "outcome": "FAILURE",
        "attemptNumber": attempt_number,
        "errorCode": safe_code,
        "errorLabel": label,
        "retryable": safe_retryable,
        "link": _link(ui_url),
        "messageText": text,
    }
    message["messageDigest"] = _digest(message)
    return message


def _locked_destination(
    session: Session,
    *,
    job: GenerationJob,
    workspace_id: uuid.UUID,
    token_workspace_id: str | None,
) -> _Destination:
    item = session.scalar(
        select(SlackWorkItem).where(SlackWorkItem.id == job.slack_work_item_id).with_for_update()
    )
    if item is None or item.workspace_id != workspace_id:
        raise GenerationSlackNotificationConflict("Slack destination tenant is invalid")
    if item.source_version != job.source_version:
        return _Destination(item=item, disposition="SUPPRESSED_STALE_SOURCE")
    if token_workspace_id is not None and token_workspace_id != item.slack_workspace_id:
        return _Destination(item=item, disposition="SUPPRESSED_CONFIGURATION")
    allowlist = session.scalar(
        select(SlackChannelAllowlist)
        .where(
            SlackChannelAllowlist.workspace_id == item.workspace_id,
            SlackChannelAllowlist.slack_workspace_id == item.slack_workspace_id,
            SlackChannelAllowlist.channel_id == item.channel_id,
            SlackChannelAllowlist.enabled.is_(True),
        )
        .with_for_update()
    )
    if allowlist is None:
        return _Destination(item=item, disposition="SUPPRESSED_CONFIGURATION")
    if allowlist.is_private and not allowlist.include_private:
        return _Destination(item=item, disposition="SUPPRESSED_CONFIGURATION")
    threads = tuple(
        session.scalars(
            select(SlackThread).where(SlackThread.slack_work_item_id == item.id).with_for_update()
        )
    )
    if len(threads) != 1 or threads[0].thread_ts != item.message_ts or not item.root_seen:
        return _Destination(item=item, disposition="SUPPRESSED_SOURCE_UNAVAILABLE")
    roots = tuple(
        session.scalars(
            select(SlackMessage)
            .where(
                SlackMessage.thread_id == threads[0].id,
                SlackMessage.is_root.is_(True),
            )
            .with_for_update()
        )
    )
    if len(roots) != 1 or roots[0].message_ts != item.message_ts:
        return _Destination(item=item, disposition="SUPPRESSED_SOURCE_UNAVAILABLE")
    latest = session.scalar(
        select(SlackMessageRevision)
        .where(SlackMessageRevision.message_id == roots[0].id)
        .order_by(
            SlackMessageRevision.revision.desc(),
            SlackMessageRevision.id.desc(),
        )
        .limit(1)
        .with_for_update()
    )
    if latest is None or latest.is_deleted:
        return _Destination(item=item, disposition="SUPPRESSED_SOURCE_UNAVAILABLE")
    return _Destination(item=item, disposition="POST")


def _require_claim(session: Session, claim: ClaimedJob) -> None:
    active = session.scalar(
        select(BackgroundJob).where(BackgroundJob.id == claim.id).with_for_update()
    )
    if (
        active is None
        or active.job_type != NOTIFICATION_JOB_TYPE
        or active.status != JobStatus.RUNNING.value
        or active.locked_by != claim.locked_by
        or active.attempts != claim.attempt
    ):
        raise GenerationSlackNotificationConflict("notification job lease was lost")


def _request_snapshot(intent: GenerationSlackNotificationIntent) -> dict[str, Any]:
    return {
        "channel": intent.channel_id,
        "thread_ts": intent.thread_ts,
        "text": intent.message_text,
        "client_msg_id": str(intent.client_message_id),
        "reply_broadcast": False,
        "unfurl_links": False,
        "unfurl_media": False,
        "metadata": intent.message_metadata,
    }


def _link(ui_url: str | None) -> dict[str, str | None]:
    return {
        "linkMode": "ABSOLUTE_HTTPS" if ui_url is not None else "OMITTED",
        "uiUrl": ui_url,
    }


def _link_line(ui_url: str | None) -> str:
    return f"\n• 결과 보기: <{ui_url}|QA 도구에서 보기>" if ui_url else ""


def _digest(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _identifier(prefix: str, value: uuid.UUID) -> str:
    return f"{prefix}_{value.hex}"


def _provider_code(value: str | None) -> str | None:
    return value if value is not None and _SAFE_PROVIDER_CODE.fullmatch(value) else None


def _is_non_public_ip(hostname: str) -> bool:
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return not address.is_global
