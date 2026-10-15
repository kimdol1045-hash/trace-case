"""Provider-neutral orchestration for sealed generation job attempts."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from tc_backend.db.models import GenerationJob, GenerationJobStatus
from tc_backend.generation.errors import GenerationError, GenerationErrorCode
from tc_backend.generation.reconstruction import GenerationInputUnavailable
from tc_backend.repositories.generation_repository import (
    GenerationJobStateConflict,
    GenerationRepository,
    PersistedGenerationContext,
)

_PROVIDER_CODES = frozenset(
    {
        GenerationErrorCode.UNSUPPORTED_SCHEMA_VERSION.value,
        GenerationErrorCode.AUTH_REJECTED.value,
        GenerationErrorCode.TIMEOUT.value,
        GenerationErrorCode.TRANSPORT_UNAVAILABLE.value,
        GenerationErrorCode.RATE_LIMITED.value,
        GenerationErrorCode.PROVIDER_REJECTED.value,
        GenerationErrorCode.PROVIDER_ERROR.value,
        GenerationErrorCode.INVALID_CONTENT_TYPE.value,
        GenerationErrorCode.RESPONSE_TOO_LARGE.value,
        GenerationErrorCode.MALFORMED_JSON.value,
        GenerationErrorCode.INVALID_RESPONSE.value,
    }
)


class GenerationSetupRequired(RuntimeError):
    """Execution is disabled until the unresolved T01 decisions are supplied."""

    def __init__(self, job_id: uuid.UUID) -> None:
        self.job_id = job_id
        super().__init__("Generation provider setup is required.")


@dataclass(frozen=True, slots=True)
class GenerationRunResult:
    payload: Mapping[str, object]
    attempts: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.payload, Mapping):
            raise ValueError("generation result payload must be an object")
        if not 1 <= self.attempts <= 5:
            raise ValueError("generation result attempts must be between one and five")


class GenerationJobRunner(Protocol):
    """Injected execution boundary; production deliberately supplies no runner."""

    def run(self, context: PersistedGenerationContext) -> GenerationRunResult: ...


class GenerationJobLifecycle:
    def __init__(
        self,
        repository: GenerationRepository,
        runner: GenerationJobRunner | None,
        *,
        public_web_base_url: str | None = None,
    ) -> None:
        self._repository = repository
        self._runner = runner
        self._public_web_base_url = public_web_base_url

    def execute(self, job_id: uuid.UUID) -> PersistedGenerationContext:
        job = self._repository.get_job(job_id, for_update=True)
        self._require_status(job, "EXECUTE", GenerationJobStatus.QUEUED.value)
        if self._runner is None:
            raise GenerationSetupRequired(job.id)
        reconstruction = self._repository.load_reconstruction(job.id)
        if reconstruction.reason is not None:
            raise GenerationInputUnavailable(job.id, reconstruction.reason)
        self._repository.begin_execution(job.id)
        context = self._repository.load_context(job.id)
        try:
            result = self._runner.run(context)
        except GenerationError as error:
            code = error.code.value
            if code not in _PROVIDER_CODES:
                code = GenerationErrorCode.PROVIDER_ERROR.value
            self._repository.fail_execution(
                job.id,
                code=code,
                message=str(error),
                retryable=error.retryable,
                attempts=min(max(error.attempts, 1), 5),
            )
            if not error.retryable:
                self._capture_final_failure(job.id)
        except Exception:
            self._repository.fail_execution(
                job.id,
                code=GenerationErrorCode.PROVIDER_ERROR.value,
                message="Generation provider failed.",
                retryable=False,
                attempts=1,
            )
            self._capture_final_failure(job.id)
        else:
            self._repository.succeed_execution(job.id, result.payload)
        return self._repository.load_context(job.id)

    def retry(self, source_job_id: uuid.UUID) -> PersistedGenerationContext:
        source = self._repository.get_job(source_job_id, for_update=True)
        self._require_status(source, "RETRY", GenerationJobStatus.FAILED.value)
        return self._repository.retry_job_with_context(source.id)

    def _capture_final_failure(self, job_id: uuid.UUID) -> None:
        from tc_backend.generation.slack_notifications import (
            GenerationSlackNotificationService,
        )

        GenerationSlackNotificationService(
            self._repository.session,
            public_web_base_url=self._public_web_base_url,
        ).capture_final_failure(job_id)

    @staticmethod
    def _require_status(job: GenerationJob, operation: str, allowed: str) -> None:
        if job.status != allowed:
            raise GenerationJobStateConflict(job.id, operation, job.status, (allowed,))
