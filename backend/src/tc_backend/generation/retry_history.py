"""Durable automatic-generation execution, retry, and safe history protocol.

Every method is transaction-scoped and never commits.  Provider calls are made by
the worker only between :meth:`permit_provider_call` and
:meth:`observe_provider_call`, using separate committed transactions.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from tc_backend.db.constants import DEFAULT_WORKSPACE_ID
from tc_backend.db.jobs import JobRepository
from tc_backend.db.models import (
    BackgroundJob,
    GenerationAttemptEvent,
    GenerationExecutionSchedule,
    GenerationJob,
    GenerationJobStatus,
    GenerationMaterializationSchedule,
    GenerationProviderCallObservation,
    GenerationProviderCallPermit,
    GenerationRetryExhaustion,
    GenerationRetryRequest,
    GenerationSlackNotificationIntent,
    GenerationSlackNotificationReceipt,
    GenerationSuggestionMaterialization,
    SlackWorkItem,
    WorkspaceSettings,
)
from tc_backend.generation.errors import GenerationError, GenerationErrorCode
from tc_backend.repositories.generation_repository import GenerationRepository

AUTOMATIC_GENERATION_EXECUTE_JOB_TYPE = "AUTOMATIC_GENERATION_EXECUTE"
AUTOMATIC_GENERATION_MATERIALIZE_JOB_TYPE = "AUTOMATIC_GENERATION_MATERIALIZE"
RETRY_HISTORY_CONTRACT_VERSION = "automatic-generation-retry-history/1.0.0"
RETRY_POLICY_VERSION = "automatic-generation-retry-policy/1.0.0"
SERVER_TEAM_ACTOR_ID = "team_operator"

_ERROR_LABELS = {
    "GENERATION_CONFIG_INVALID": "생성 서비스 설정이 필요합니다",
    "GENERATION_UNSUPPORTED_SCHEMA_VERSION": "지원하지 않는 생성 형식입니다",
    "GENERATION_AUTH_REQUIRED": "생성 서비스 인증 설정이 필요합니다",
    "GENERATION_AUTH_REJECTED": "생성 서비스 인증이 거부되었습니다",
    "GENERATION_TIMEOUT": "생성 서비스 응답 시간이 초과되었습니다",
    "GENERATION_TRANSPORT_UNAVAILABLE": "생성 서비스에 연결할 수 없습니다",
    "GENERATION_RATE_LIMITED": "생성 서비스 요청 한도에 도달했습니다",
    "GENERATION_PROVIDER_REJECTED": "생성 서비스가 요청을 거부했습니다",
    "GENERATION_PROVIDER_ERROR": "생성 서비스 오류가 발생했습니다",
    "GENERATION_INVALID_CONTENT_TYPE": "생성 응답 형식이 올바르지 않습니다",
    "GENERATION_RESPONSE_TOO_LARGE": "생성 응답이 허용 크기를 초과했습니다",
    "GENERATION_MALFORMED_JSON": "생성 응답을 해석할 수 없습니다",
    "GENERATION_INVALID_RESPONSE": "생성 응답 검증에 실패했습니다",
    "GENERATION_SOURCE_STALE": "원본이 변경되어 다시 평가해야 합니다",
    "GENERATION_SOURCE_UNAVAILABLE": "원본을 사용할 수 없습니다",
    "GENERATION_WORK_ITEM_DELETED": "삭제된 작업 항목입니다",
    "GENERATION_EXECUTION_OUTCOME_UNKNOWN": "공급자 처리 결과를 확인해야 합니다",
    "GENERATION_WORKER_DELIVERY_EXHAUSTED": "작업 전달 결과를 확인해야 합니다",
}
_PUBLIC_ERROR_CODES = frozenset(_ERROR_LABELS)
_BIDI_CONTROLS = frozenset(chr(value) for value in range(0x202A, 0x202F)) | frozenset(
    chr(value) for value in range(0x2066, 0x206A)
)
_SECRET_LIKE = re.compile(
    r"(?i)(?:\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|"
    r"authorization|bearer|client[_ -]?secret|password|passwd|secret)\b\s*[:=]?\s*\S+|"
    r"\bbasic\s+[A-Za-z0-9+/=]{8,}|"
    r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|"
    r"\bsk-[A-Za-z0-9_-]{12,}\b|"
    r"\b(?:xox[baprs]-|xapp-)[A-Za-z0-9-]{10,}\b|"
    r"\bgh[pousr]_[A-Za-z0-9]{20,}\b|"
    r"\bAIza[A-Za-z0-9_-]{20,}\b|"
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b|"
    r"비밀번호\s*[:=]?\s*\S+|토큰\s*[:=]?\s*\S+)"
)


class RetryHistoryError(RuntimeError):
    """Safe deterministic domain error used by the HTTP boundary."""

    def __init__(
        self, code: str, *, status_code: int = 409, winner_job_id: uuid.UUID | None = None
    ) -> None:
        self.code = code
        self.status_code = status_code
        self.winner_job_id = winner_job_id
        super().__init__(code)


class GenerationLeaseLost(RetryHistoryError):
    def __init__(self) -> None:
        super().__init__("GENERATION_LEASE_LOST")


@dataclass(frozen=True, slots=True)
class RetryPolicySnapshot:
    provider_call_budget: int = 3
    generation_job_budget: int = 3
    version: str = RETRY_POLICY_VERSION

    def __post_init__(self) -> None:
        if not 1 <= self.provider_call_budget <= 5:
            raise ValueError("provider_call_budget must be between one and five")
        if not 1 <= self.generation_job_budget <= 1000:
            raise ValueError("generation_job_budget must be between one and 1000")

    @property
    def digest(self) -> str:
        return _digest(
            {
                "generationJobBudget": self.generation_job_budget,
                "providerCallBudget": self.provider_call_budget,
                "version": self.version,
            }
        )


@dataclass(frozen=True, slots=True)
class ProviderCallClaim:
    schedule_id: uuid.UUID
    job_id: uuid.UUID
    root_job_id: uuid.UUID
    lease_token: uuid.UUID
    provider_attempt_number: int
    permit_id: uuid.UUID
    request_digest: str


@dataclass(frozen=True, slots=True)
class OperatorRetryResult:
    request: GenerationRetryRequest
    job: GenerationJob
    schedule: GenerationExecutionSchedule
    replayed: bool


class RetryHistoryService:
    """PostgreSQL-backed automatic retry/finality/history authority."""

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

    def schedule_root(
        self,
        generation_job_id: uuid.UUID,
        *,
        policy: RetryPolicySnapshot | None = None,
        not_before: datetime | None = None,
        materialization_mode: Literal["AUTOMATIC_APPLY", "AUTOMATIC_SUGGESTIONS"] | None = None,
    ) -> GenerationExecutionSchedule:
        job, item = self._locked_scoped_job(generation_job_id)
        if job.generation_trigger != "AUTOMATIC" or job.root_job_id != job.id:
            raise RetryHistoryError("GENERATION_AUTOMATIC_OPERATION_FORBIDDEN")
        existing = self.session.scalar(
            select(GenerationExecutionSchedule).where(
                GenerationExecutionSchedule.generation_job_id == job.id
            )
        )
        if existing is not None:
            return existing
        from tc_backend.generation.version_regression import (
            GenerationBaselineLedgerService,
            VersionSetFenceError,
        )

        try:
            GenerationBaselineLedgerService(self.session).capture_job_pin(job)
        except VersionSetFenceError as error:
            raise RetryHistoryError("GENERATION_CONFIG_INVALID", status_code=422) from error
        effective_policy = policy or RetryPolicySnapshot()
        mode = materialization_mode or (
            "AUTOMATIC_SUGGESTIONS"
            if job.schema_version == "generation-suggestion/1.0.0"
            else "AUTOMATIC_APPLY"
        )
        effective_not_before = _aware(not_before or datetime.now(UTC))
        background_id = JobRepository(self.session).enqueue(
            job_type=AUTOMATIC_GENERATION_EXECUTE_JOB_TYPE,
            payload={"generationJobId": str(job.id)},
            max_attempts=100,
            run_after=effective_not_before,
            idempotency_key=f"automatic-generation-execute:{job.id}",
        )
        state = "READY" if effective_not_before <= datetime.now(UTC) else "NOT_BEFORE"
        schedule = GenerationExecutionSchedule(
            id=uuid.uuid4(),
            workspace_id=item.workspace_id,
            generation_job_id=job.id,
            root_job_id=job.root_job_id,
            background_job_id=background_id,
            state=state,
            not_before=effective_not_before,
            resource_version=1,
            provider_permit_count=0,
            worker_delivery_attempt_count=0,
            provider_call_budget=effective_policy.provider_call_budget,
            generation_job_budget=effective_policy.generation_job_budget,
            retry_policy_version=effective_policy.version,
            retry_policy_digest=effective_policy.digest,
            materialization_mode=mode,
        )
        self.session.add(schedule)
        self.session.flush()
        self._event(job, "INPUT_SEALED")
        self._event(job, "EXECUTION_QUEUED")
        return schedule

    def claim_execution(
        self,
        generation_job_id: uuid.UUID,
        *,
        background_job_id: uuid.UUID,
        background_attempt: int,
        worker_id: str,
        lease_seconds: int = 60,
    ) -> GenerationExecutionSchedule:
        if not worker_id.strip() or background_attempt < 1 or not 5 <= lease_seconds <= 900:
            raise ValueError("invalid automatic generation lease claim")
        job, _item = self._locked_scoped_job(generation_job_id)
        schedule = self._locked_schedule(job.id)
        now = datetime.now(UTC)
        if schedule.background_job_id != background_job_id:
            raise GenerationLeaseLost()
        delivery = self.session.get(BackgroundJob, background_job_id)
        if (
            delivery is None
            or delivery.status != "RUNNING"
            or delivery.locked_by != worker_id
            or delivery.attempts != background_attempt
        ):
            raise GenerationLeaseLost()
        if schedule.state == "LEASED":
            if schedule.lease_expires_at is not None and schedule.lease_expires_at > now:
                raise RetryHistoryError("GENERATION_ALREADY_LEASED")
            unresolved = self.session.scalar(
                select(GenerationProviderCallPermit.id)
                .outerjoin(
                    GenerationProviderCallObservation,
                    GenerationProviderCallObservation.permit_id == GenerationProviderCallPermit.id,
                )
                .where(
                    GenerationProviderCallPermit.execution_schedule_id == schedule.id,
                    GenerationProviderCallObservation.id.is_(None),
                )
                .limit(1)
            )
            if unresolved is not None:
                self._mark_outcome_unknown(job, schedule)
                return schedule
            last_observation = self.session.scalar(
                select(GenerationProviderCallObservation)
                .join(
                    GenerationProviderCallPermit,
                    GenerationProviderCallPermit.id
                    == GenerationProviderCallObservation.permit_id,
                )
                .where(
                    GenerationProviderCallPermit.execution_schedule_id == schedule.id
                )
                .order_by(
                    GenerationProviderCallPermit.provider_attempt_number.desc()
                )
                .limit(1)
            )
            if last_observation is not None and last_observation.outcome in {
                "SUCCEEDED",
                "AMBIGUOUS",
            }:
                # A success observation stores only the result digest.  If it
                # somehow committed without atomic result finalization, there
                # is no body to validate/fold and another provider call would
                # be a blind resend.
                self._mark_outcome_unknown(job, schedule)
                return schedule
            if last_observation is not None and (
                last_observation.outcome == "NON_RETRYABLE_FAILURE"
                or (
                    last_observation.outcome == "RETRYABLE_FAILURE"
                    and schedule.provider_permit_count
                    >= schedule.provider_call_budget
                )
            ):
                schedule.lease_owner = worker_id.strip()
                schedule.lease_token = uuid.uuid4()
                schedule.lease_expires_at = now + timedelta(seconds=lease_seconds)
                schedule.worker_delivery_attempt_count += 1
                schedule.resource_version += 1
                schedule.updated_at = now
                self.session.flush()
                self._event(job, "WORKER_LEASED")
                assert schedule.lease_token is not None
                self.finalize_failure(
                    schedule.id,
                    schedule.lease_token,
                    code=last_observation.error_code
                    or "GENERATION_PROVIDER_ERROR",
                    retryable=(
                        last_observation.outcome == "RETRYABLE_FAILURE"
                    ),
                    retry_after_seconds=last_observation.retry_after_seconds,
                )
                return schedule
        if schedule.state not in {"READY", "NOT_BEFORE", "LEASED"}:
            raise RetryHistoryError("GENERATION_EXECUTION_NOT_CLAIMABLE")
        if schedule.not_before > now:
            raise RetryHistoryError("GENERATION_EXECUTION_NOT_BEFORE")
        if job.status == GenerationJobStatus.QUEUED.value:
            GenerationRepository(self.session).begin_execution(job.id)
        elif job.status != GenerationJobStatus.RUNNING.value:
            raise RetryHistoryError("GENERATION_EXECUTION_NOT_CLAIMABLE")
        schedule.state = "LEASED"
        schedule.lease_owner = worker_id.strip()
        schedule.lease_token = uuid.uuid4()
        schedule.lease_expires_at = now + timedelta(seconds=lease_seconds)
        schedule.worker_delivery_attempt_count += 1
        schedule.resource_version += 1
        schedule.updated_at = now
        self.session.flush()
        self._event(job, "WORKER_LEASED")
        return schedule

    def heartbeat(
        self, schedule_id: uuid.UUID, lease_token: uuid.UUID, *, lease_seconds: int = 60
    ) -> bool:
        schedule = self.session.scalar(
            select(GenerationExecutionSchedule)
            .where(GenerationExecutionSchedule.id == schedule_id)
            .with_for_update()
        )
        now = datetime.now(UTC)
        if (
            schedule is None
            or schedule.state != "LEASED"
            or schedule.lease_token != lease_token
            or schedule.lease_expires_at is None
            or schedule.lease_expires_at <= now
        ):
            return False
        schedule.lease_expires_at = now + timedelta(seconds=lease_seconds)
        schedule.resource_version += 1
        schedule.updated_at = now
        self.session.flush()
        return True

    def permit_provider_call(
        self,
        schedule_id: uuid.UUID,
        lease_token: uuid.UUID,
    ) -> ProviderCallClaim:
        schedule = self._locked_owned_schedule(schedule_id, lease_token)
        job, item = self._locked_scoped_job(schedule.generation_job_id)
        from tc_backend.generation.version_regression import (
            GenerationBaselineLedgerService,
            VersionSetFenceError,
        )

        try:
            GenerationBaselineLedgerService(self.session).assert_current_job_pin(job)
        except VersionSetFenceError as error:
            raise RetryHistoryError("GENERATION_CONFIG_INVALID", status_code=422) from error
        if job.generation_input_digest is None:
            raise RetryHistoryError("GENERATION_INVALID_RESPONSE", status_code=422)
        unresolved = self.session.scalar(
            select(GenerationProviderCallPermit.id)
            .outerjoin(
                GenerationProviderCallObservation,
                GenerationProviderCallObservation.permit_id == GenerationProviderCallPermit.id,
            )
            .where(
                GenerationProviderCallPermit.execution_schedule_id == schedule.id,
                GenerationProviderCallObservation.id.is_(None),
            )
            .limit(1)
        )
        if unresolved is not None:
            self._mark_outcome_unknown(job, schedule)
            raise RetryHistoryError("GENERATION_EXECUTION_OUTCOME_UNKNOWN")
        number = schedule.provider_permit_count + 1
        if number > schedule.provider_call_budget:
            raise RetryHistoryError("GENERATION_PROVIDER_BUDGET_EXHAUSTED")
        permit = GenerationProviderCallPermit(
            id=uuid.uuid4(),
            workspace_id=item.workspace_id,
            generation_job_id=job.id,
            root_job_id=job.root_job_id,
            execution_schedule_id=schedule.id,
            provider_attempt_number=number,
            lease_token=lease_token,
            request_digest=job.generation_input_digest,
            provider_id=job.provider,
            model_id=job.model,
        )
        self.session.add(permit)
        schedule.provider_permit_count = number
        schedule.resource_version += 1
        schedule.updated_at = datetime.now(UTC)
        self.session.flush()
        self._event(job, "PROVIDER_ATTEMPT_STARTED", provider_attempt_number=number)
        return ProviderCallClaim(
            schedule_id=schedule.id,
            job_id=job.id,
            root_job_id=job.root_job_id,
            lease_token=lease_token,
            provider_attempt_number=number,
            permit_id=permit.id,
            request_digest=permit.request_digest,
        )

    def observe_provider_call(
        self,
        claim: ProviderCallClaim,
        *,
        outcome: Literal["SUCCEEDED", "RETRYABLE_FAILURE", "NON_RETRYABLE_FAILURE", "AMBIGUOUS"],
        error_code: str | None = None,
        safe_provider_code: str | None = None,
        http_status: int | None = None,
        retry_after_seconds: int | None = None,
        result: Mapping[str, object] | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> GenerationProviderCallObservation:
        schedule = self._locked_owned_schedule(claim.schedule_id, claim.lease_token)
        permit = self.session.scalar(
            select(GenerationProviderCallPermit).where(
                GenerationProviderCallPermit.id == claim.permit_id
            )
        )
        if (
            permit is None
            or permit.lease_token != claim.lease_token
            or permit.generation_job_id != claim.job_id
            or permit.provider_attempt_number != claim.provider_attempt_number
        ):
            raise GenerationLeaseLost()
        existing = self.session.scalar(
            select(GenerationProviderCallObservation).where(
                GenerationProviderCallObservation.permit_id == permit.id
            )
        )
        if existing is not None:
            return existing
        if error_code is not None and error_code not in _PUBLIC_ERROR_CODES:
            error_code = GenerationErrorCode.PROVIDER_ERROR.value
        observed = GenerationProviderCallObservation(
            id=uuid.uuid4(),
            workspace_id=permit.workspace_id,
            permit_id=permit.id,
            generation_job_id=permit.generation_job_id,
            root_job_id=permit.root_job_id,
            outcome=outcome,
            error_code=error_code,
            safe_provider_code=_safe_provider_code(safe_provider_code),
            http_status=http_status,
            retry_after_seconds=(
                min(max(retry_after_seconds, 0), 86_400)
                if retry_after_seconds is not None
                else None
            ),
            result_digest=_digest(result) if result is not None else None,
            started_at=_aware(started_at or permit.permitted_at),
            finished_at=_aware(finished_at or datetime.now(UTC)),
        )
        self.session.add(observed)
        self.session.flush()
        job = self.session.get(GenerationJob, schedule.generation_job_id)
        assert job is not None
        event_type = (
            "PROVIDER_ATTEMPT_SUCCEEDED" if outcome == "SUCCEEDED" else "PROVIDER_ATTEMPT_FAILED"
        )
        self._event(
            job,
            event_type,
            provider_attempt_number=permit.provider_attempt_number,
            error_code=error_code,
        )
        return observed

    def finalize_success(
        self,
        schedule_id: uuid.UUID,
        lease_token: uuid.UUID,
        result: Mapping[str, object],
    ) -> GenerationMaterializationSchedule:
        schedule = self._locked_owned_schedule(schedule_id, lease_token)
        job, item = self._locked_scoped_job(schedule.generation_job_id)
        observation = self.session.scalar(
            select(GenerationProviderCallObservation)
            .join(
                GenerationProviderCallPermit,
                GenerationProviderCallPermit.id == GenerationProviderCallObservation.permit_id,
            )
            .where(
                GenerationProviderCallPermit.execution_schedule_id == schedule.id,
                GenerationProviderCallObservation.outcome == "SUCCEEDED",
            )
            .order_by(GenerationProviderCallPermit.provider_attempt_number.desc())
            .limit(1)
        )
        if observation is None or observation.result_digest != _digest(result):
            raise RetryHistoryError("GENERATION_INVALID_RESPONSE", status_code=422)
        self._validate_result(job, result)
        GenerationRepository(self.session).succeed_execution(job.id, result)
        schedule.state = "TERMINAL"
        self._clear_lease(schedule)
        schedule.resource_version += 1
        schedule.updated_at = datetime.now(UTC)
        background_id = JobRepository(self.session).enqueue(
            job_type=AUTOMATIC_GENERATION_MATERIALIZE_JOB_TYPE,
            payload={"generationJobId": str(job.id)},
            max_attempts=20,
            idempotency_key=f"automatic-generation-materialize:{job.id}",
        )
        materialization = GenerationMaterializationSchedule(
            id=uuid.uuid4(),
            workspace_id=item.workspace_id,
            generation_job_id=job.id,
            root_job_id=job.root_job_id,
            background_job_id=background_id,
            mode=schedule.materialization_mode,
            state="QUEUED",
            not_before=datetime.now(UTC),
            resource_version=1,
        )
        self.session.add(materialization)
        self.session.flush()
        self._event(job, "JOB_SUCCEEDED")
        self._event(
            job,
            "SUGGESTION_SCHEDULED"
            if schedule.materialization_mode == "AUTOMATIC_SUGGESTIONS"
            else "APPLY_SCHEDULED",
        )
        return materialization

    def finalize_failure(
        self,
        schedule_id: uuid.UUID,
        lease_token: uuid.UUID,
        *,
        code: str,
        retryable: bool,
        retry_after_seconds: int | None = None,
        review_required: bool = False,
    ) -> GenerationJob | GenerationRetryExhaustion:
        schedule = self._locked_owned_schedule(schedule_id, lease_token)
        job, _item = self._locked_scoped_job(schedule.generation_job_id)
        safe_code = code if code in _PUBLIC_ERROR_CODES else "GENERATION_PROVIDER_ERROR"
        automatic_retry_remaining = (
            retryable
            and not review_required
            and job.attempt_number < schedule.generation_job_budget
        )
        GenerationRepository(self.session).fail_execution(
            job.id,
            code=safe_code,
            message=_ERROR_LABELS[safe_code],
            retryable=automatic_retry_remaining,
            attempts=max(1, schedule.provider_permit_count),
        )
        schedule.state = "TERMINAL"
        self._clear_lease(schedule)
        schedule.resource_version += 1
        schedule.updated_at = datetime.now(UTC)
        self.session.flush()
        self._event(job, "JOB_FAILED", error_code=safe_code)
        if automatic_retry_remaining:
            delay = max(
                min(3_600, 30 * (2 ** (job.attempt_number - 1))),
                min(max(retry_after_seconds or 0, 0), 86_400),
            )
            child = GenerationRepository(self.session).retry_job_with_context(job.id).job
            self._schedule_child(
                child,
                predecessor=schedule,
                not_before=datetime.now(UTC) + timedelta(seconds=delay),
            )
            self._event(child, "RETRY_SCHEDULED", error_code=safe_code)
            return child
        finality = (
            "TERMINAL_REVIEW_REQUIRED"
            if review_required
            else "TERMINAL_EXHAUSTED"
            if retryable
            else "TERMINAL_NON_RETRYABLE"
        )
        return self._record_exhaustion(job, schedule, finality=finality, code=safe_code)

    def materialize_succeeded_job(
        self,
        generation_job_id: uuid.UUID,
        *,
        background_job_id: uuid.UUID,
        background_attempt: int,
        worker_id: str,
    ) -> GenerationMaterializationSchedule:
        """Commit automatic apply/suggestions and Slack intent in one transaction."""

        workspace = self.session.scalar(
            select(WorkspaceSettings.id)
            .where(WorkspaceSettings.id == self.workspace_id)
            .with_for_update()
        )
        if workspace is None:
            raise RetryHistoryError("RESOURCE_NOT_FOUND", status_code=404)
        job, item = self._locked_scoped_job(generation_job_id)
        schedule = self.session.scalar(
            select(GenerationMaterializationSchedule)
            .where(GenerationMaterializationSchedule.generation_job_id == job.id)
            .with_for_update()
        )
        delivery = self.session.get(BackgroundJob, background_job_id)
        if (
            schedule is None
            or schedule.background_job_id != background_job_id
            or delivery is None
            or delivery.status != "RUNNING"
            or delivery.locked_by != worker_id
            or delivery.attempts != background_attempt
        ):
            raise GenerationLeaseLost()
        if schedule.state == "COMMITTED":
            return schedule
        if schedule.state not in {"QUEUED", "LEASED"}:
            return schedule
        token = uuid.uuid4()
        schedule.state = "LEASED"
        schedule.lease_owner = worker_id
        schedule.lease_token = token
        schedule.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
        schedule.resource_version += 1
        schedule.updated_at = datetime.now(UTC)
        self.session.flush()
        if not item.root_seen:
            schedule.state = "SKIPPED_SOURCE_UNAVAILABLE"
            schedule.terminal_error_code = "GENERATION_SOURCE_UNAVAILABLE"
            self._clear_materialization_lease(schedule)
            self._event(
                job,
                "MATERIALIZATION_SKIPPED",
                error_code="GENERATION_SOURCE_UNAVAILABLE",
            )
            return schedule
        if item.source_version != job.source_version:
            schedule.state = "SKIPPED_STALE_SOURCE"
            schedule.terminal_error_code = "GENERATION_SOURCE_STALE"
            self._clear_materialization_lease(schedule)
            self._event(
                job,
                "MATERIALIZATION_SKIPPED",
                error_code="GENERATION_SOURCE_STALE",
            )
            return schedule
        if schedule.mode == "AUTOMATIC_SUGGESTIONS":
            from tc_backend.generation.suggestions import GenerationSuggestionService

            GenerationSuggestionService(
                self.session,
                workspace_id=self.workspace_id,
                public_web_base_url=self.public_web_base_url,
            ).materialize_succeeded_job(job.id)
            receipt = self.session.scalar(
                select(GenerationSuggestionMaterialization).where(
                    GenerationSuggestionMaterialization.generation_job_id == job.id
                )
            )
            if receipt is None:
                raise RetryHistoryError("GENERATION_INVALID_RESPONSE")
            schedule.suggestion_materialization_id = receipt.id
            event_type = "SUGGESTION_COMMITTED"
            suggestion_materialization_id = receipt.id
            generation_apply_id = None
        else:
            from tc_backend.db.audit import AuditContext
            from tc_backend.db.models import AuditSourceType
            from tc_backend.generation.application import (
                GenerationApplicationService,
                automatic_apply_command,
            )

            application = GenerationApplicationService(
                self.session,
                workspace_id=self.workspace_id,
                public_web_base_url=self.public_web_base_url,
            )
            draft = application.plan(job.id)
            apply_receipt = application.apply(
                automatic_apply_command(draft),
                audit=AuditContext(
                    source_type=AuditSourceType.WORKER,
                    actor_label="QA Team",
                    external_actor_id=SERVER_TEAM_ACTOR_ID,
                    reason="Automatic generation materialization",
                    trace_id=f"automatic_generation_materialize_{job.id.hex}",
                    idempotency_key=f"automatic-generation-apply:{job.id}",
                ),
            )
            schedule.generation_apply_id = apply_receipt.id
            event_type = "APPLY_COMMITTED"
            generation_apply_id = apply_receipt.id
            suggestion_materialization_id = None
        # Automatic apply/suggestion capture commits this same authority with
        # its Slack intent before returning.  Avoid a second state/version
        # transition while retaining compatibility for non-notifying modes.
        if schedule.state != "COMMITTED":
            schedule.state = "COMMITTED"
            schedule.resource_version += 1
            schedule.updated_at = datetime.now(UTC)
            self._clear_materialization_lease(schedule)
        self.session.flush()
        self._event(
            job,
            event_type,
            generation_apply_id=generation_apply_id,
            suggestion_materialization_id=suggestion_materialization_id,
        )
        intent = self.session.scalar(
            select(GenerationSlackNotificationIntent).where(
                GenerationSlackNotificationIntent.generation_job_id == job.id
            )
        )
        if intent is not None:
            self._event(job, "SLACK_INTENT_CAPTURED", notification_intent_id=intent.id)
        return schedule

    def mark_materialization_review_required(
        self,
        generation_job_id: uuid.UUID,
        *,
        background_job_id: uuid.UUID,
        background_attempt: int,
        worker_id: str,
        code: str = "GENERATION_SOURCE_STALE",
    ) -> GenerationMaterializationSchedule:
        """Fold a deterministic human/source race after its failed tx rolled back."""

        job, _item = self._locked_scoped_job(generation_job_id)
        schedule = self.session.scalar(
            select(GenerationMaterializationSchedule)
            .where(GenerationMaterializationSchedule.generation_job_id == job.id)
            .with_for_update()
        )
        delivery = self.session.get(BackgroundJob, background_job_id)
        if (
            schedule is None
            or schedule.background_job_id != background_job_id
            or delivery is None
            or delivery.status != "RUNNING"
            or delivery.locked_by != worker_id
            or delivery.attempts != background_attempt
        ):
            raise GenerationLeaseLost()
        if schedule.state == "COMMITTED":
            return schedule
        schedule.state = "REVIEW_REQUIRED"
        schedule.terminal_error_code = (
            code if code in _PUBLIC_ERROR_CODES else "GENERATION_SOURCE_STALE"
        )
        self._clear_materialization_lease(schedule)
        schedule.resource_version += 1
        schedule.updated_at = datetime.now(UTC)
        self.session.flush()
        self._event(job, "MATERIALIZATION_SKIPPED", error_code=schedule.terminal_error_code)
        return schedule

    def provider_input_for_job(self, job_id: uuid.UUID) -> Mapping[str, object]:
        persisted = GenerationRepository(self.session).load_context(job_id)
        payload: dict[str, object] = copy.deepcopy(dict(persisted.provider_input))
        request = self.session.scalar(
            select(GenerationRetryRequest).where(GenerationRetryRequest.retry_job_id == job_id)
        )
        if request is not None:
            payload["operatorConditions"] = {
                "trust": "UNTRUSTED_ADVISORY_DATA",
                "text": request.normalized_additional_conditions,
                "digest": request.condition_digest,
            }
        return payload

    def retry_with_conditions(
        self,
        failed_job_id: uuid.UUID,
        *,
        idempotency_key: str,
        expected_root_job_id: uuid.UUID,
        expected_failed_job_id: uuid.UUID,
        expected_attempt_number: int,
        reason: str,
        additional_conditions: str,
    ) -> OperatorRetryResult:
        normalized_reason = _normalize_text(reason, field="reason", maximum=500)
        normalized_conditions = _normalize_text(
            additional_conditions, field="additionalConditions", maximum=1000
        )
        if _SECRET_LIKE.search(normalized_conditions):
            raise RetryHistoryError("VALIDATION_FAILED", status_code=422)
        condition_bytes = len(normalized_conditions.encode("utf-8", errors="strict"))
        if condition_bytes > 4000:
            raise RetryHistoryError("VALIDATION_FAILED", status_code=422)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,254}", idempotency_key):
            raise RetryHistoryError("VALIDATION_FAILED", status_code=422)
        if failed_job_id != expected_failed_job_id:
            raise RetryHistoryError("GENERATION_RETRY_STALE_VIEW")
        workspace = self.session.scalar(
            select(WorkspaceSettings)
            .where(WorkspaceSettings.id == self.workspace_id)
            .with_for_update()
        )
        if workspace is None:
            raise RetryHistoryError("RESOURCE_NOT_FOUND", status_code=404)
        request_digest = _digest(
            {
                "additionalConditions": normalized_conditions,
                "contextMode": "REUSE_SEALED",
                "expectedAttemptNumber": expected_attempt_number,
                "expectedFailedJobId": str(expected_failed_job_id),
                "expectedRootJobId": str(expected_root_job_id),
                "idempotencyKey": idempotency_key,
                "reason": normalized_reason,
            }
        )
        replay = self.session.scalar(
            select(GenerationRetryRequest).where(
                GenerationRetryRequest.actor_principal_id == SERVER_TEAM_ACTOR_ID,
                GenerationRetryRequest.idempotency_key == idempotency_key,
            )
        )
        if replay is not None:
            if replay.request_digest != request_digest:
                raise RetryHistoryError("IDEMPOTENCY_CONFLICT")
            replay_job = self.session.get(GenerationJob, replay.retry_job_id)
            replay_schedule = self.session.scalar(
                select(GenerationExecutionSchedule).where(
                    GenerationExecutionSchedule.generation_job_id == replay.retry_job_id
                )
            )
            assert replay_job is not None and replay_schedule is not None
            return OperatorRetryResult(replay, replay_job, replay_schedule, True)
        failed, item = self._locked_scoped_job(failed_job_id)
        root = self.session.scalar(
            select(GenerationJob).where(GenerationJob.id == expected_root_job_id).with_for_update()
        )
        latest = self.session.scalar(
            select(GenerationJob)
            .where(GenerationJob.root_job_id == expected_root_job_id)
            .order_by(GenerationJob.attempt_number.desc())
            .limit(1)
            .with_for_update()
        )
        if root is None or root.slack_work_item_id != item.id:
            raise RetryHistoryError("RESOURCE_NOT_FOUND", status_code=404)
        if (
            latest is None
            or latest.id != failed.id
            or failed.root_job_id != root.id
            or failed.attempt_number != expected_attempt_number
            or failed.status != "FAILED"
        ):
            raise RetryHistoryError("GENERATION_RETRY_STALE_VIEW")
        exhaustion = self.session.scalar(
            select(GenerationRetryExhaustion).where(
                GenerationRetryExhaustion.root_job_id == root.id
            )
        )
        if exhaustion is None:
            raise RetryHistoryError("GENERATION_RETRY_NOT_TERMINAL")
        winner = self.session.scalar(
            select(GenerationJob.id).where(GenerationJob.retry_of_job_id == failed.id)
        )
        if winner is not None:
            raise RetryHistoryError("GENERATION_RETRY_ALREADY_SCHEDULED", winner_job_id=winner)
        if not item.root_seen:
            raise RetryHistoryError("GENERATION_SOURCE_UNAVAILABLE")
        if item.source_version != failed.source_version:
            raise RetryHistoryError("GENERATION_SOURCE_STALE")
        if failed.context_digest is None or failed.generation_input_digest is None:
            raise RetryHistoryError("GENERATION_SOURCE_UNAVAILABLE")
        child = GenerationRepository(self.session).retry_job_with_context(failed.id).job
        root_schedule = self.session.scalar(
            select(GenerationExecutionSchedule)
            .where(GenerationExecutionSchedule.root_job_id == root.id)
            .order_by(GenerationExecutionSchedule.created_at)
            .limit(1)
        )
        policy = RetryPolicySnapshot(
            provider_call_budget=(root_schedule.provider_call_budget if root_schedule else 3),
            generation_job_budget=(
                root_schedule.generation_job_budget if root_schedule else 3
            ),
            version=root_schedule.retry_policy_version if root_schedule else RETRY_POLICY_VERSION,
        )
        active_schedule = self.session.scalar(
            select(GenerationExecutionSchedule)
            .where(
                GenerationExecutionSchedule.root_job_id == root.id,
                GenerationExecutionSchedule.state == "PROVIDER_OUTCOME_UNKNOWN",
            )
            .with_for_update()
        )
        if active_schedule is not None:
            active_schedule.state = "TERMINAL"
            active_schedule.resource_version += 1
            active_schedule.updated_at = datetime.now(UTC)
        schedule = self._schedule_child(
            child,
            predecessor=root_schedule,
            not_before=datetime.now(UTC),
            policy=policy,
        )
        request = GenerationRetryRequest(
            id=uuid.uuid4(),
            workspace_id=item.workspace_id,
            actor_principal_id=SERVER_TEAM_ACTOR_ID,
            actor_display_label=f"{workspace.team_name} 운영자",
            root_job_id=root.id,
            expected_failed_job_id=failed.id,
            expected_attempt_number=failed.attempt_number,
            source_version=failed.source_version,
            context_digest=failed.context_digest,
            input_digest=failed.generation_input_digest,
            context_mode="REUSE_SEALED",
            normalized_reason=normalized_reason,
            normalized_additional_conditions=normalized_conditions,
            condition_code_points=len(normalized_conditions),
            condition_utf8_bytes=condition_bytes,
            original_condition_digest=hashlib.sha256(
                additional_conditions.encode("utf-8", errors="strict")
            ).hexdigest(),
            condition_digest=hashlib.sha256(normalized_conditions.encode("utf-8")).hexdigest(),
            idempotency_key=idempotency_key,
            request_digest=request_digest,
            retry_job_id=child.id,
        )
        self.session.add(request)
        self.session.flush()
        self._event(child, "RETRY_SCHEDULED", retry_request_id=request.id)
        return OperatorRetryResult(request, child, schedule, False)

    def list_failures(
        self,
        *,
        limit: int = 25,
        actionability: str | None = None,
        finality: str | None = None,
        error_code: str | None = None,
        before: tuple[datetime, uuid.UUID] | None = None,
    ) -> tuple[tuple[dict[str, object], ...], tuple[datetime, uuid.UUID] | None]:
        if not 1 <= limit <= 100:
            raise RetryHistoryError("VALIDATION_FAILED", status_code=422)
        latest_lineage_job = aliased(GenerationJob)
        latest_lineage_status = (
            select(latest_lineage_job.status)
            .where(
                latest_lineage_job.root_job_id
                == GenerationRetryExhaustion.root_job_id
            )
            .order_by(latest_lineage_job.attempt_number.desc())
            .limit(1)
            .scalar_subquery()
        )
        statement = (
            select(GenerationRetryExhaustion, GenerationJob, SlackWorkItem)
            .join(
                GenerationJob,
                GenerationJob.id == GenerationRetryExhaustion.terminal_failed_job_id,
            )
            .join(SlackWorkItem, SlackWorkItem.id == GenerationJob.slack_work_item_id)
            .where(SlackWorkItem.workspace_id == self.workspace_id)
        )
        if finality is not None:
            statement = statement.where(GenerationRetryExhaustion.finality == finality)
        if error_code is not None:
            statement = statement.where(GenerationRetryExhaustion.error_code == error_code)
        if actionability == "AUTOMATIC_RETRY_SCHEDULED":
            statement = statement.where(GenerationRetryExhaustion.id.is_(None))
        elif actionability == "REASSESS_CURRENT_SOURCE":
            statement = statement.where(SlackWorkItem.source_version != GenerationJob.source_version)
        elif actionability == "REVIEW_ONLY":
            statement = statement.where(
                SlackWorkItem.source_version == GenerationJob.source_version,
                (
                    GenerationRetryExhaustion.finality
                    == "TERMINAL_REVIEW_REQUIRED"
                )
                | (latest_lineage_status != GenerationJobStatus.FAILED.value),
            )
        elif actionability == "OPERATOR_RETRY_ALLOWED":
            statement = statement.where(
                SlackWorkItem.source_version == GenerationJob.source_version,
                GenerationRetryExhaustion.finality != "TERMINAL_REVIEW_REQUIRED",
                latest_lineage_status == GenerationJobStatus.FAILED.value,
            )
        elif actionability is not None:
            raise RetryHistoryError("VALIDATION_FAILED", status_code=422)
        if before is not None:
            failed_at, failed_id = before
            statement = statement.where(
                (GenerationRetryExhaustion.exhausted_at < failed_at)
                | (
                    (GenerationRetryExhaustion.exhausted_at == failed_at)
                    & (GenerationJob.id < failed_id)
                )
            )
        rows = tuple(
            self.session.execute(
                statement.order_by(
                    GenerationRetryExhaustion.exhausted_at.desc(),
                    GenerationJob.id.desc(),
                ).limit(limit + 1)
            )
        )
        page = rows[:limit]
        items = tuple(self._failure_item(receipt, job, item) for receipt, job, item in page)
        next_value = (
            (page[-1][0].exhausted_at, page[-1][1].id) if len(rows) > limit and page else None
        )
        return items, next_value

    def failure_detail(self, failed_job_id: uuid.UUID) -> dict[str, object]:
        failed, item = self._scoped_job(failed_job_id)
        receipt = self.session.scalar(
            select(GenerationRetryExhaustion).where(
                GenerationRetryExhaustion.root_job_id == failed.root_job_id
            )
        )
        if receipt is None:
            raise RetryHistoryError("RESOURCE_NOT_FOUND", status_code=404)
        attempts = tuple(
            self.session.scalars(
                select(GenerationJob)
                .where(GenerationJob.root_job_id == failed.root_job_id)
                .order_by(GenerationJob.attempt_number)
            )
        )
        return {
            "contractVersion": RETRY_HISTORY_CONTRACT_VERSION,
            "failure": self._failure_item(receipt, failed, item),
            "attemptHistory": [self._attempt_snapshot(job) for job in attempts],
            "timeline": self._timeline(failed.root_job_id),
        }

    def attempt_history(self, failed_job_id: uuid.UUID) -> dict[str, object]:
        return self.failure_detail(failed_job_id)

    def _schedule_child(
        self,
        job: GenerationJob,
        *,
        predecessor: GenerationExecutionSchedule | None,
        not_before: datetime,
        policy: RetryPolicySnapshot | None = None,
    ) -> GenerationExecutionSchedule:
        item = self.session.get(SlackWorkItem, job.slack_work_item_id)
        if item is None or item.workspace_id != self.workspace_id:
            raise RetryHistoryError("RESOURCE_NOT_FOUND", status_code=404)
        effective = policy or RetryPolicySnapshot(
            provider_call_budget=predecessor.provider_call_budget if predecessor else 3,
            generation_job_budget=predecessor.generation_job_budget if predecessor else 3,
            version=predecessor.retry_policy_version if predecessor else RETRY_POLICY_VERSION,
        )
        digest = predecessor.retry_policy_digest if predecessor else effective.digest
        mode = (
            predecessor.materialization_mode
            if predecessor
            else (
                "AUTOMATIC_SUGGESTIONS"
                if job.schema_version == "generation-suggestion/1.0.0"
                else "AUTOMATIC_APPLY"
            )
        )
        from tc_backend.generation.version_regression import (
            GenerationBaselineLedgerService,
            VersionSetFenceError,
        )

        try:
            GenerationBaselineLedgerService(self.session).capture_job_pin(job)
        except VersionSetFenceError as error:
            raise RetryHistoryError("GENERATION_CONFIG_INVALID", status_code=422) from error
        background_id = JobRepository(self.session).enqueue(
            job_type=AUTOMATIC_GENERATION_EXECUTE_JOB_TYPE,
            payload={"generationJobId": str(job.id)},
            max_attempts=100,
            run_after=not_before,
            idempotency_key=f"automatic-generation-execute:{job.id}",
        )
        schedule = GenerationExecutionSchedule(
            id=uuid.uuid4(),
            workspace_id=item.workspace_id,
            generation_job_id=job.id,
            root_job_id=job.root_job_id,
            background_job_id=background_id,
            state="READY" if not_before <= datetime.now(UTC) else "NOT_BEFORE",
            not_before=not_before,
            resource_version=1,
            provider_permit_count=0,
            worker_delivery_attempt_count=0,
            provider_call_budget=effective.provider_call_budget,
            generation_job_budget=effective.generation_job_budget,
            retry_policy_version=effective.version,
            retry_policy_digest=digest,
            materialization_mode=mode,
        )
        self.session.add(schedule)
        self.session.flush()
        self._event(job, "INPUT_SEALED")
        self._event(job, "EXECUTION_QUEUED")
        return schedule

    def _record_exhaustion(
        self,
        job: GenerationJob,
        schedule: GenerationExecutionSchedule,
        *,
        finality: str,
        code: str,
    ) -> GenerationRetryExhaustion:
        existing = self.session.scalar(
            select(GenerationRetryExhaustion).where(
                GenerationRetryExhaustion.root_job_id == job.root_job_id
            )
        )
        if existing is not None:
            return existing
        receipt = GenerationRetryExhaustion(
            id=uuid.uuid4(),
            workspace_id=schedule.workspace_id,
            root_job_id=job.root_job_id,
            terminal_failed_job_id=job.id,
            finality=finality,
            retryable=finality == "TERMINAL_EXHAUSTED",
            provider_call_budget=schedule.provider_call_budget,
            generation_job_budget=schedule.generation_job_budget,
            error_code=code,
        )
        self.session.add(receipt)
        self.session.flush()
        from tc_backend.generation.slack_notifications import GenerationSlackNotificationService

        intent = GenerationSlackNotificationService(
            self.session,
            workspace_id=self.workspace_id,
            public_web_base_url=self.public_web_base_url,
        ).capture_final_failure(job.id)
        if intent is not None:
            self._event(job, "SLACK_INTENT_CAPTURED", notification_intent_id=intent.id)
        return receipt

    def _mark_outcome_unknown(
        self, job: GenerationJob, schedule: GenerationExecutionSchedule
    ) -> None:
        if job.status == GenerationJobStatus.RUNNING.value:
            GenerationRepository(self.session).fail_execution(
                job.id,
                code="GENERATION_EXECUTION_OUTCOME_UNKNOWN",
                message=_ERROR_LABELS["GENERATION_EXECUTION_OUTCOME_UNKNOWN"],
                retryable=False,
                attempts=max(1, schedule.provider_permit_count),
            )
        schedule.state = "PROVIDER_OUTCOME_UNKNOWN"
        self._clear_lease(schedule)
        schedule.resource_version += 1
        schedule.updated_at = datetime.now(UTC)
        self.session.flush()
        self._event(job, "JOB_FAILED", error_code="GENERATION_EXECUTION_OUTCOME_UNKNOWN")
        self._record_exhaustion(
            job,
            schedule,
            finality="TERMINAL_REVIEW_REQUIRED",
            code="GENERATION_EXECUTION_OUTCOME_UNKNOWN",
        )

    def _validate_result(self, job: GenerationJob, result: Mapping[str, object]) -> None:
        if (
            not isinstance(result, Mapping)
            or result.get("outputSchemaVersion") != job.schema_version
        ):
            raise RetryHistoryError("GENERATION_INVALID_RESPONSE", status_code=422)
        if job.schema_version == "generation-suggestion/1.0.0":
            suggestions = result.get("suggestions")
            if not isinstance(suggestions, list) or not suggestions:
                raise RetryHistoryError("GENERATION_INVALID_RESPONSE", status_code=422)
            return
        if job.schema_version != "1.0.0":
            raise RetryHistoryError("GENERATION_UNSUPPORTED_SCHEMA_VERSION", status_code=422)
        from tc_backend.generation.application import GenerationApplicationService

        GenerationApplicationService(self.session, workspace_id=self.workspace_id).validate_result(
            job.id, result
        )

    def _event(
        self,
        job: GenerationJob,
        event_type: str,
        *,
        provider_attempt_number: int | None = None,
        error_code: str | None = None,
        retry_request_id: uuid.UUID | None = None,
        generation_apply_id: uuid.UUID | None = None,
        suggestion_materialization_id: uuid.UUID | None = None,
        notification_intent_id: uuid.UUID | None = None,
        notification_receipt_id: uuid.UUID | None = None,
    ) -> GenerationAttemptEvent:
        item = self.session.get(SlackWorkItem, job.slack_work_item_id)
        if item is None or item.workspace_id != self.workspace_id:
            raise RetryHistoryError("RESOURCE_NOT_FOUND", status_code=404)
        sequence = (
            self.session.scalar(
                select(func.max(GenerationAttemptEvent.sequence)).where(
                    GenerationAttemptEvent.root_job_id == job.root_job_id
                )
            )
            or 0
        ) + 1
        event = GenerationAttemptEvent(
            id=uuid.uuid4(),
            workspace_id=item.workspace_id,
            root_job_id=job.root_job_id,
            generation_job_id=job.id,
            sequence=sequence,
            event_type=event_type,
            provider_attempt_number=provider_attempt_number,
            error_code=error_code,
            retry_request_id=retry_request_id,
            generation_apply_id=generation_apply_id,
            suggestion_materialization_id=suggestion_materialization_id,
            notification_intent_id=notification_intent_id,
            notification_receipt_id=notification_receipt_id,
        )
        self.session.add(event)
        self.session.flush()
        return event

    def _locked_scoped_job(self, job_id: uuid.UUID) -> tuple[GenerationJob, SlackWorkItem]:
        item = self.session.scalar(
            select(SlackWorkItem)
            .join(GenerationJob, GenerationJob.slack_work_item_id == SlackWorkItem.id)
            .where(GenerationJob.id == job_id, SlackWorkItem.workspace_id == self.workspace_id)
            .with_for_update()
        )
        if item is None:
            raise RetryHistoryError("RESOURCE_NOT_FOUND", status_code=404)
        job = self.session.scalar(
            select(GenerationJob).where(GenerationJob.id == job_id).with_for_update()
        )
        assert job is not None
        return job, item

    def _scoped_job(self, job_id: uuid.UUID) -> tuple[GenerationJob, SlackWorkItem]:
        row = self.session.execute(
            select(GenerationJob, SlackWorkItem)
            .join(SlackWorkItem, SlackWorkItem.id == GenerationJob.slack_work_item_id)
            .where(GenerationJob.id == job_id, SlackWorkItem.workspace_id == self.workspace_id)
        ).one_or_none()
        if row is None:
            raise RetryHistoryError("RESOURCE_NOT_FOUND", status_code=404)
        return row[0], row[1]

    def _locked_schedule(self, job_id: uuid.UUID) -> GenerationExecutionSchedule:
        schedule = self.session.scalar(
            select(GenerationExecutionSchedule)
            .where(GenerationExecutionSchedule.generation_job_id == job_id)
            .with_for_update()
        )
        if schedule is None:
            raise RetryHistoryError("GENERATION_EXECUTION_NOT_SCHEDULED")
        return schedule

    def _locked_owned_schedule(
        self, schedule_id: uuid.UUID, lease_token: uuid.UUID
    ) -> GenerationExecutionSchedule:
        schedule = self.session.scalar(
            select(GenerationExecutionSchedule)
            .where(GenerationExecutionSchedule.id == schedule_id)
            .with_for_update()
        )
        now = datetime.now(UTC)
        if (
            schedule is None
            or schedule.workspace_id != self.workspace_id
            or schedule.state != "LEASED"
            or schedule.lease_token != lease_token
            or schedule.lease_expires_at is None
            or schedule.lease_expires_at <= now
        ):
            raise GenerationLeaseLost()
        return schedule

    @staticmethod
    def _clear_lease(schedule: GenerationExecutionSchedule) -> None:
        schedule.lease_owner = None
        schedule.lease_token = None
        schedule.lease_expires_at = None

    @staticmethod
    def _clear_materialization_lease(
        schedule: GenerationMaterializationSchedule,
    ) -> None:
        schedule.lease_owner = None
        schedule.lease_token = None
        schedule.lease_expires_at = None

    def _failure_item(
        self,
        receipt: GenerationRetryExhaustion,
        job: GenerationJob,
        item: SlackWorkItem,
    ) -> dict[str, object]:
        latest_job = self.session.scalar(
            select(GenerationJob)
            .where(GenerationJob.root_job_id == job.root_job_id)
            .order_by(GenerationJob.attempt_number.desc())
            .limit(1)
        )
        latest_retry = self.session.scalar(
            select(GenerationJob)
            .join(
                GenerationRetryRequest,
                GenerationRetryRequest.retry_job_id == GenerationJob.id,
            )
            .where(GenerationJob.root_job_id == job.root_job_id)
            .order_by(GenerationJob.attempt_number.desc())
            .limit(1)
        )
        failed_job = (
            latest_job
            if latest_job is not None
            and latest_job.status == GenerationJobStatus.FAILED.value
            else job
        )
        return {
            "rootJobId": _identifier("generation_job", failed_job.root_job_id),
            "failedJobId": _identifier("generation_job", failed_job.id),
            "slackWorkItemId": _identifier("slack_work_item", item.id),
            "sourceVersion": failed_job.source_version,
            "jobAttemptNumber": failed_job.attempt_number,
            "failure": {
                "code": failed_job.error_code or receipt.error_code,
                "messageLabel": _ERROR_LABELS.get(
                    failed_job.error_code or receipt.error_code, "생성 실패"
                ),
                "retryable": receipt.retryable,
                "finality": receipt.finality,
                "providerAttemptCount": failed_job.error_attempts or 0,
                "failedAt": _iso(receipt.exhausted_at),
                "nextAttemptNotBefore": None,
                "exhaustionReceiptId": _identifier("generation_retry_exhaustion", receipt.id),
            },
            "actionability": (
                "REASSESS_CURRENT_SOURCE"
                if item.source_version != failed_job.source_version
                else "REVIEW_ONLY"
                if receipt.finality == "TERMINAL_REVIEW_REQUIRED"
                or latest_job is None
                or latest_job.status != GenerationJobStatus.FAILED.value
                else "OPERATOR_RETRY_ALLOWED"
            ),
            "latestRetryJobId": (
                _identifier("generation_job", latest_retry.id) if latest_retry else None
            ),
        }

    def _attempt_snapshot(self, job: GenerationJob) -> dict[str, object]:
        schedule = self.session.scalar(
            select(GenerationExecutionSchedule).where(
                GenerationExecutionSchedule.generation_job_id == job.id
            )
        )
        permits = tuple(
            self.session.scalars(
                select(GenerationProviderCallPermit)
                .where(GenerationProviderCallPermit.generation_job_id == job.id)
                .order_by(GenerationProviderCallPermit.provider_attempt_number)
            )
        )
        observations = {
            row.permit_id: row
            for row in self.session.scalars(
                select(GenerationProviderCallObservation).where(
                    GenerationProviderCallObservation.generation_job_id == job.id
                )
            )
        }
        exhaustion = self.session.scalar(
            select(GenerationRetryExhaustion).where(
                GenerationRetryExhaustion.terminal_failed_job_id == job.id
            )
        )
        next_schedule = self.session.scalar(
            select(GenerationExecutionSchedule)
            .join(GenerationJob, GenerationJob.id == GenerationExecutionSchedule.generation_job_id)
            .where(GenerationJob.retry_of_job_id == job.id)
        )
        materialization = self.session.scalar(
            select(GenerationMaterializationSchedule).where(
                GenerationMaterializationSchedule.generation_job_id == job.id
            )
        )
        intent = self.session.scalar(
            select(GenerationSlackNotificationIntent).where(
                GenerationSlackNotificationIntent.generation_job_id == job.id
            )
        )
        receipt = (
            self.session.scalar(
                select(GenerationSlackNotificationReceipt).where(
                    GenerationSlackNotificationReceipt.intent_id == intent.id
                )
            )
            if intent
            else None
        )
        retry_request = self.session.scalar(
            select(GenerationRetryRequest).where(GenerationRetryRequest.retry_job_id == job.id)
        )
        provider_attempts = []
        for permit in permits:
            observed = observations.get(permit.id)
            provider_attempts.append(
                {
                    "providerAttemptNumber": permit.provider_attempt_number,
                    "outcome": observed.outcome if observed else "AMBIGUOUS",
                    "errorCode": observed.error_code
                    if observed
                    else "GENERATION_EXECUTION_OUTCOME_UNKNOWN",
                    "safeProviderCode": observed.safe_provider_code if observed else None,
                    "httpStatus": observed.http_status if observed else None,
                    "startedAt": _iso(observed.started_at if observed else permit.permitted_at),
                    "finishedAt": _iso(observed.finished_at)
                    if observed and observed.finished_at
                    else None,
                }
            )
        failure = None
        if job.status == "FAILED" and job.error_code is not None:
            finality = (
                exhaustion.finality
                if exhaustion
                else "AUTOMATIC_RETRY_SCHEDULED"
                if next_schedule
                else "TERMINAL_REVIEW_REQUIRED"
            )
            failure = {
                "code": job.error_code,
                "messageLabel": _ERROR_LABELS.get(job.error_code, "생성 실패"),
                "retryable": bool(job.error_retryable),
                "finality": finality,
                "providerAttemptCount": job.error_attempts or 0,
                "failedAt": _iso(job.completed_at or job.updated_at),
                "nextAttemptNotBefore": _iso(next_schedule.not_before) if next_schedule else None,
                "exhaustionReceiptId": _identifier("generation_retry_exhaustion", exhaustion.id)
                if exhaustion
                else None,
            }
        materialization_state = "NOT_SCHEDULED"
        apply_id = None
        suggestion_id = None
        mode = schedule.materialization_mode if schedule else "AUTOMATIC_APPLY"
        if materialization:
            mapping = {
                "QUEUED": "QUEUED",
                "LEASED": "RUNNING",
                "COMMITTED": "COMMITTED",
                "SKIPPED_STALE_SOURCE": "SKIPPED_STALE_SOURCE",
                "SKIPPED_SOURCE_UNAVAILABLE": "SKIPPED_SOURCE_UNAVAILABLE",
                "REVIEW_REQUIRED": "REVIEW_REQUIRED",
                "FAILED": "FAILED",
            }
            materialization_state = mapping[materialization.state]
            apply_id = (
                _identifier("generation_apply", materialization.generation_apply_id)
                if materialization.generation_apply_id
                else None
            )
            suggestion_id = (
                _identifier(
                    "generation_suggestion_materialization",
                    materialization.suggestion_materialization_id,
                )
                if materialization.suggestion_materialization_id
                else None
            )
        notification_outcome = "NONE"
        if intent:
            notification_outcome = "PENDING"
            if receipt:
                notification_outcome = receipt.terminal_outcome
        return {
            "jobId": _identifier("generation_job", job.id),
            "rootJobId": _identifier("generation_job", job.root_job_id),
            "retryOfJobId": _identifier("generation_job", job.retry_of_job_id)
            if job.retry_of_job_id
            else None,
            "jobAttemptNumber": job.attempt_number,
            "retryOrigin": "ROOT"
            if job.retry_of_job_id is None
            else "OPERATOR_CONDITIONS"
            if retry_request
            else "AUTOMATIC_POLICY",
            "status": job.status,
            "executionState": schedule.state if schedule else "TERMINAL",
            "notBefore": _iso(schedule.not_before if schedule else job.created_at),
            "workerDeliveryAttemptCount": schedule.worker_delivery_attempt_count if schedule else 0,
            "input": {
                "contextPolicy": "REUSE_SEALED",
                "contextDigest": job.context_digest,
                "inputSchemaVersion": job.generation_input_schema_version,
                "inputDigest": job.generation_input_digest,
                "operatorConditions": (
                    {
                        "text": retry_request.normalized_additional_conditions,
                        "unicodeCodePoints": retry_request.condition_code_points,
                        "utf8Bytes": retry_request.condition_utf8_bytes,
                        "digest": retry_request.condition_digest,
                    }
                    if retry_request
                    else None
                ),
            },
            "providerAttempts": provider_attempts,
            "resultDigest": _digest(job.result) if job.result is not None else None,
            "failure": failure,
            "materialization": {
                "mode": mode,
                "state": materialization_state,
                "applyId": apply_id,
                "suggestionMaterializationId": suggestion_id,
            },
            "notification": {
                "intentId": _identifier("generation_slack_notification", intent.id)
                if intent
                else None,
                "receiptId": _identifier("generation_slack_notification_receipt", receipt.id)
                if receipt
                else None,
                "outcome": notification_outcome,
            },
            "createdAt": _iso(job.created_at),
            "startedAt": _iso(job.started_at) if job.started_at else None,
            "completedAt": _iso(job.completed_at) if job.completed_at else None,
        }

    def _timeline(self, root_job_id: uuid.UUID) -> list[dict[str, object]]:
        jobs = {
            job.id: job
            for job in self.session.scalars(
                select(GenerationJob).where(GenerationJob.root_job_id == root_job_id)
            )
        }
        events = tuple(
            self.session.scalars(
                select(GenerationAttemptEvent)
                .where(GenerationAttemptEvent.root_job_id == root_job_id)
                .order_by(GenerationAttemptEvent.sequence)
            )
        )
        return [
            {
                "sequence": event.sequence,
                "eventType": event.event_type,
                "jobId": _identifier("generation_job", event.generation_job_id),
                "jobAttemptNumber": jobs[event.generation_job_id].attempt_number,
                "providerAttemptNumber": event.provider_attempt_number,
                "errorCode": event.error_code,
                "occurredAt": _iso(event.occurred_at),
                "references": {
                    "retryRequestId": _identifier(
                        "generation_retry_request", event.retry_request_id
                    )
                    if event.retry_request_id
                    else None,
                    "applyId": _identifier("generation_apply", event.generation_apply_id)
                    if event.generation_apply_id
                    else None,
                    "suggestionMaterializationId": _identifier(
                        "generation_suggestion_materialization", event.suggestion_materialization_id
                    )
                    if event.suggestion_materialization_id
                    else None,
                    "notificationIntentId": _identifier(
                        "generation_slack_notification", event.notification_intent_id
                    )
                    if event.notification_intent_id
                    else None,
                    "notificationReceiptId": _identifier(
                        "generation_slack_notification_receipt", event.notification_receipt_id
                    )
                    if event.notification_receipt_id
                    else None,
                },
            }
            for event in events
        ]


def classify_generation_error(error: BaseException) -> tuple[str, bool]:
    """Collapse provider exceptions to a stable public code without raw detail."""

    if isinstance(error, GenerationError):
        code = error.code.value
        return (
            code if code in _PUBLIC_ERROR_CODES else "GENERATION_PROVIDER_ERROR",
            error.retryable,
        )
    return "GENERATION_PROVIDER_ERROR", False


def _normalize_text(value: str, *, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise RetryHistoryError("VALIDATION_FAILED", status_code=422)
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise RetryHistoryError("VALIDATION_FAILED", status_code=422) from error
    normalized = unicodedata.normalize(
        "NFKC", value.replace("\r\n", "\n").replace("\r", "\n")
    ).strip()
    if not normalized or len(normalized) > maximum:
        raise RetryHistoryError("VALIDATION_FAILED", status_code=422)
    if any(
        character == "\x00"
        or character in _BIDI_CONTROLS
        or (ord(character) < 32 and character != "\n")
        or 0x7F <= ord(character) <= 0x9F
        for character in normalized
    ):
        raise RetryHistoryError("VALIDATION_FAILED", status_code=422)
    return normalized


def _safe_provider_code(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().casefold()
    return normalized if re.fullmatch(r"[a-z0-9_]{1,80}", normalized) else None


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _identifier(prefix: str, value: uuid.UUID) -> str:
    return f"{prefix}_{value.hex}"


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _aware(value).isoformat().replace("+00:00", "Z")
