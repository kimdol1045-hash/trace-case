"""Durable PostgreSQL control plane for verified LightRAG generations.

The repository owns only the auxiliary generation tables introduced by
``20260902_0030``.  It never reads or writes the authoritative QA/knowledge
ledger.  Callers using :class:`LightRAGGenerationRepository` own the enclosing
transaction; :class:`DurableLightRAGGenerationControlPlane` provides a small
transaction-owning facade that commits rejected transition evidence before
raising a typed error.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from tc_backend.db.models import (
    LightRAGActiveGenerationPointer,
    LightRAGGeneration,
    LightRAGGenerationTransitionEvent,
)

_IDENTIFIER: Final = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]*$")
_DIGEST: Final = re.compile(r"^(?:sha256:)?[a-f0-9]{64}$")
_RUNTIME_METADATA_FIELDS: Final = (
    "auxiliaryAuthorityId",
    "providerContractVersion",
    "providerDistribution",
    "providerVersion",
    "runtimeConfigDigest",
)


class LightRAGControlPlaneError(RuntimeError):
    """The request is malformed, conflicts with replay, or ledger state is corrupt."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class LightRAGTransitionRejected(LightRAGControlPlaneError):
    """A valid transition request failed closed and left durable evidence."""

    def __init__(self, event: LightRAGGenerationTransitionEvent) -> None:
        if event.outcome != "REJECTED" or event.error_code is None:
            raise ValueError("transition rejection requires a rejected event")
        super().__init__(event.error_code)
        self.event_id = event.id
        self.sequence = event.sequence
        self.replayed = False


@dataclass(frozen=True, slots=True)
class ActiveGenerationSnapshot:
    generation_id: str | None
    pointer_version: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ActiveGenerationBinding:
    """One atomic pointer-to-immutable-generation routing decision."""

    generation_id: str
    pointer_version: int
    pointer_updated_at: datetime
    authority_id: str
    provider_id: str
    provider_contract_version: str
    provider_distribution: str
    provider_version: str
    workspace: str
    auxiliary_target_digest: str
    runtime_config_digest: str
    manifest_digest: str
    corpus_digest: str
    source_ledger_digest: str
    projection_digest: str
    pgvector_digest: str
    document_count: int
    registration_digest: str

    @property
    def cache_key(self) -> tuple[int, str, str]:
        """Fence caches by pointer version, identity, and immutable receipt."""

        return (self.pointer_version, self.generation_id, self.registration_digest)


class LightRAGGenerationRepository:
    """Transaction-scoped immutable generation ledger and CAS pointer operations."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def register_generation(
        self,
        *,
        generation_id: str,
        provider_id: str,
        workspace: str,
        auxiliary_target_digest: str,
        manifest_digest: str,
        corpus_digest: str,
        source_ledger_digest: str,
        projection_digest: str,
        pgvector_digest: str,
        document_count: int,
        recorded_by: str,
        idempotency_key: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> LightRAGGeneration:
        generation = _identifier(generation_id, 255, "GENERATION_ID_INVALID")
        provider = _identifier(provider_id, 128, "PROVIDER_ID_INVALID")
        isolated_workspace = _identifier(workspace, 255, "WORKSPACE_INVALID")
        actor = _nonblank(recorded_by, 256, "ACTOR_ID_INVALID")
        key = _nonblank(idempotency_key, 256, "IDEMPOTENCY_KEY_INVALID")
        if isinstance(document_count, bool) or document_count <= 0:
            raise LightRAGControlPlaneError("DOCUMENT_COUNT_INVALID")
        safe_metadata = dict(metadata or {})
        runtime_metadata = _runtime_metadata(safe_metadata)
        safe_metadata.update(runtime_metadata)
        digests = {
            "auxiliaryTargetDigest": _digest(
                auxiliary_target_digest, "AUXILIARY_TARGET_DIGEST_INVALID"
            ),
            "manifestDigest": _digest(manifest_digest, "MANIFEST_DIGEST_INVALID"),
            "corpusDigest": _digest(corpus_digest, "CORPUS_DIGEST_INVALID"),
            "sourceLedgerDigest": _digest(source_ledger_digest, "SOURCE_LEDGER_DIGEST_INVALID"),
            "projectionDigest": _digest(projection_digest, "PROJECTION_DIGEST_INVALID"),
            "pgvectorDigest": _digest(pgvector_digest, "PGVECTOR_DIGEST_INVALID"),
        }
        request_digest = _canonical_digest(
            {
                "generationId": generation,
                "providerId": provider,
                "workspace": isolated_workspace,
                **digests,
                "documentCount": document_count,
                "metadata": safe_metadata,
                "recordedBy": actor,
            }
        )
        replay = self.session.scalar(
            select(LightRAGGeneration).where(LightRAGGeneration.registration_idempotency_key == key)
        )
        if replay is not None:
            if replay.registration_digest != request_digest:
                raise LightRAGControlPlaneError("IDEMPOTENCY_CONFLICT")
            return replay
        existing = self.session.get(LightRAGGeneration, generation)
        if existing is not None:
            raise LightRAGControlPlaneError("GENERATION_ALREADY_REGISTERED")
        target_workspace = self.session.scalar(
            select(LightRAGGeneration).where(
                LightRAGGeneration.auxiliary_target_digest == digests["auxiliaryTargetDigest"],
                LightRAGGeneration.workspace == isolated_workspace,
            )
        )
        if target_workspace is not None:
            raise LightRAGControlPlaneError("GENERATION_WORKSPACE_ALREADY_REGISTERED")
        record = LightRAGGeneration(
            generation_id=generation,
            provider_id=provider,
            workspace=isolated_workspace,
            auxiliary_target_digest=digests["auxiliaryTargetDigest"],
            manifest_digest=digests["manifestDigest"],
            corpus_digest=digests["corpusDigest"],
            source_ledger_digest=digests["sourceLedgerDigest"],
            projection_digest=digests["projectionDigest"],
            pgvector_digest=digests["pgvectorDigest"],
            document_count=document_count,
            verification_status="VERIFIED",
            metadata_json=safe_metadata,
            recorded_by=actor,
            registration_idempotency_key=key,
            registration_digest=request_digest,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def active_snapshot(self, *, lock: bool = False) -> ActiveGenerationSnapshot:
        pointer = self._pointer(lock=lock)
        return ActiveGenerationSnapshot(
            generation_id=pointer.active_generation_id,
            pointer_version=pointer.pointer_version,
            updated_at=pointer.updated_at,
        )

    def active_generation(self) -> ActiveGenerationBinding:
        """Resolve the singleton pointer and immutable metadata in one statement.

        The outer join is intentional: a missing target is reported as corruption
        instead of being indistinguishable from an absent singleton row.
        """

        row = self.session.execute(
            select(
                LightRAGActiveGenerationPointer.pointer_version,
                LightRAGActiveGenerationPointer.active_generation_id,
                LightRAGActiveGenerationPointer.updated_at,
                LightRAGGeneration,
            )
            .select_from(LightRAGActiveGenerationPointer)
            .outerjoin(
                LightRAGGeneration,
                LightRAGGeneration.generation_id
                == LightRAGActiveGenerationPointer.active_generation_id,
            )
            .where(LightRAGActiveGenerationPointer.scope == "GLOBAL")
        ).one_or_none()
        if row is None:
            raise LightRAGControlPlaneError("ACTIVE_GENERATION_POINTER_MISSING")
        pointer_version, active_generation_id, pointer_updated_at, generation = row
        if active_generation_id is None:
            if pointer_version != 0:
                raise LightRAGControlPlaneError("ACTIVE_GENERATION_POINTER_INVALID")
            raise LightRAGControlPlaneError("NO_ACTIVE_GENERATION")
        if pointer_version < 1:
            raise LightRAGControlPlaneError("ACTIVE_GENERATION_POINTER_INVALID")
        if generation is None or generation.generation_id != active_generation_id:
            raise LightRAGControlPlaneError("ACTIVE_GENERATION_LEDGER_CORRUPT")
        if generation.verification_status != "VERIFIED":
            raise LightRAGControlPlaneError("ACTIVE_GENERATION_NOT_VERIFIED")
        runtime_metadata = _runtime_metadata(generation.metadata_json)
        return ActiveGenerationBinding(
            generation_id=generation.generation_id,
            pointer_version=pointer_version,
            pointer_updated_at=pointer_updated_at,
            authority_id=runtime_metadata["auxiliaryAuthorityId"],
            provider_id=generation.provider_id,
            provider_contract_version=runtime_metadata["providerContractVersion"],
            provider_distribution=runtime_metadata["providerDistribution"],
            provider_version=runtime_metadata["providerVersion"],
            workspace=generation.workspace,
            auxiliary_target_digest=generation.auxiliary_target_digest,
            runtime_config_digest=runtime_metadata["runtimeConfigDigest"],
            manifest_digest=generation.manifest_digest,
            corpus_digest=generation.corpus_digest,
            source_ledger_digest=generation.source_ledger_digest,
            projection_digest=generation.projection_digest,
            pgvector_digest=generation.pgvector_digest,
            document_count=generation.document_count,
            registration_digest=generation.registration_digest,
        )

    def activate(
        self,
        generation_id: str,
        *,
        expected_generation_id: str | None,
        expected_pointer_version: int,
        actor_id: str,
        idempotency_key: str,
    ) -> LightRAGGenerationTransitionEvent:
        return self._transition(
            operation="ACTIVATE",
            generation_id=generation_id,
            expected_generation_id=expected_generation_id,
            expected_pointer_version=expected_pointer_version,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
        )

    def rollback(
        self,
        generation_id: str,
        *,
        expected_generation_id: str,
        expected_pointer_version: int,
        actor_id: str,
        idempotency_key: str,
    ) -> LightRAGGenerationTransitionEvent:
        return self._transition(
            operation="ROLLBACK",
            generation_id=generation_id,
            expected_generation_id=expected_generation_id,
            expected_pointer_version=expected_pointer_version,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
        )

    def record_transition_failure(
        self,
        *,
        operation: str,
        generation_id: str,
        expected_generation_id: str | None,
        expected_pointer_version: int,
        actor_id: str,
        idempotency_key: str,
    ) -> LightRAGGenerationTransitionEvent:
        """Persist a sanitized failure after the attempted transaction rolled back."""

        if operation not in {"ACTIVATE", "ROLLBACK"}:
            raise LightRAGControlPlaneError("TRANSITION_OPERATION_INVALID")
        target_id = _identifier(generation_id, 255, "GENERATION_ID_INVALID")
        expected_id = (
            _identifier(expected_generation_id, 255, "EXPECTED_GENERATION_ID_INVALID")
            if expected_generation_id is not None
            else None
        )
        if isinstance(expected_pointer_version, bool) or expected_pointer_version < 0:
            raise LightRAGControlPlaneError("EXPECTED_POINTER_VERSION_INVALID")
        actor = _nonblank(actor_id, 256, "ACTOR_ID_INVALID")
        key = _nonblank(idempotency_key, 256, "IDEMPOTENCY_KEY_INVALID")
        request_digest = _canonical_digest(
            {
                "operation": operation,
                "targetGenerationId": target_id,
                "expectedGenerationId": expected_id,
                "expectedPointerVersion": expected_pointer_version,
                "actorId": actor,
            }
        )
        pointer = self._pointer(lock=True)
        replay = self.session.scalar(
            select(LightRAGGenerationTransitionEvent).where(
                LightRAGGenerationTransitionEvent.idempotency_key == key
            )
        )
        if replay is not None:
            if replay.operation != operation or replay.request_digest != request_digest:
                raise LightRAGControlPlaneError("IDEMPOTENCY_CONFLICT")
            return replay
        return self._event(
            pointer,
            operation=operation,
            outcome="REJECTED",
            error_code=f"{operation}_FAILED",
            target_id=target_id,
            expected_id=expected_id,
            expected_version=expected_pointer_version,
            actor=actor,
            key=key,
            request_digest=request_digest,
        )

    def _transition(
        self,
        *,
        operation: str,
        generation_id: str,
        expected_generation_id: str | None,
        expected_pointer_version: int,
        actor_id: str,
        idempotency_key: str,
    ) -> LightRAGGenerationTransitionEvent:
        target_id = _identifier(generation_id, 255, "GENERATION_ID_INVALID")
        expected_id = (
            _identifier(expected_generation_id, 255, "EXPECTED_GENERATION_ID_INVALID")
            if expected_generation_id is not None
            else None
        )
        if isinstance(expected_pointer_version, bool) or expected_pointer_version < 0:
            raise LightRAGControlPlaneError("EXPECTED_POINTER_VERSION_INVALID")
        actor = _nonblank(actor_id, 256, "ACTOR_ID_INVALID")
        key = _nonblank(idempotency_key, 256, "IDEMPOTENCY_KEY_INVALID")
        request_digest = _canonical_digest(
            {
                "operation": operation,
                "targetGenerationId": target_id,
                "expectedGenerationId": expected_id,
                "expectedPointerVersion": expected_pointer_version,
                "actorId": actor,
            }
        )

        # Lock first: a concurrent transition using the same idempotency key
        # cannot miss the winner and race into the unique constraint.
        pointer = self._pointer(lock=True)
        replay = self.session.scalar(
            select(LightRAGGenerationTransitionEvent).where(
                LightRAGGenerationTransitionEvent.idempotency_key == key
            )
        )
        if replay is not None:
            if replay.operation != operation or replay.request_digest != request_digest:
                raise LightRAGControlPlaneError("IDEMPOTENCY_CONFLICT")
            return replay

        if (
            pointer.active_generation_id != expected_id
            or pointer.pointer_version != expected_pointer_version
        ):
            return self._event(
                pointer,
                operation=operation,
                outcome="REJECTED",
                error_code="STALE_ACTIVE_GENERATION",
                target_id=target_id,
                expected_id=expected_id,
                expected_version=expected_pointer_version,
                actor=actor,
                key=key,
                request_digest=request_digest,
            )

        target = self.session.get(LightRAGGeneration, target_id)
        if target is None:
            return self._event(
                pointer,
                operation=operation,
                outcome="REJECTED",
                error_code="UNKNOWN_GENERATION",
                target_id=target_id,
                expected_id=expected_id,
                expected_version=expected_pointer_version,
                actor=actor,
                key=key,
                request_digest=request_digest,
            )
        if target.verification_status != "VERIFIED":
            return self._event(
                pointer,
                operation=operation,
                outcome="REJECTED",
                error_code="GENERATION_NOT_VERIFIED",
                target_id=target_id,
                expected_id=expected_id,
                expected_version=expected_pointer_version,
                actor=actor,
                key=key,
                request_digest=request_digest,
            )
        current = (
            self.session.get(LightRAGGeneration, pointer.active_generation_id)
            if pointer.active_generation_id is not None
            else None
        )
        if pointer.active_generation_id is not None and current is None:
            raise LightRAGControlPlaneError("ACTIVE_GENERATION_LEDGER_CORRUPT")
        if (
            current is not None
            and target.auxiliary_target_digest != current.auxiliary_target_digest
        ):
            return self._event(
                pointer,
                operation=operation,
                outcome="REJECTED",
                error_code="AUXILIARY_TARGET_MISMATCH",
                target_id=target_id,
                expected_id=expected_id,
                expected_version=expected_pointer_version,
                actor=actor,
                key=key,
                request_digest=request_digest,
            )

        lifecycle_error = self._lifecycle_error(operation, target_id, pointer)
        if lifecycle_error is not None:
            return self._event(
                pointer,
                operation=operation,
                outcome="REJECTED",
                error_code=lifecycle_error,
                target_id=target_id,
                expected_id=expected_id,
                expected_version=expected_pointer_version,
                actor=actor,
                key=key,
                request_digest=request_digest,
            )

        observed_id = pointer.active_generation_id
        observed_version = pointer.pointer_version
        self.session.execute(text("SELECT set_config('tc.lightrag_pointer_write', 'on', true)"))
        pointer.active_generation_id = target_id
        pointer.pointer_version = observed_version + 1
        pointer.updated_at = datetime.now(UTC)
        self.session.flush()
        self.session.execute(text("SELECT set_config('tc.lightrag_pointer_write', 'off', true)"))
        self._after_pointer_update(operation)
        return self._event(
            pointer,
            operation=operation,
            outcome="APPLIED",
            error_code=None,
            target_id=target_id,
            expected_id=expected_id,
            expected_version=expected_pointer_version,
            actor=actor,
            key=key,
            request_digest=request_digest,
            observed_id=observed_id,
            observed_version=observed_version,
        )

    def _lifecycle_error(
        self,
        operation: str,
        target_id: str,
        pointer: LightRAGActiveGenerationPointer,
    ) -> str | None:
        if target_id == pointer.active_generation_id:
            return (
                "GENERATION_ALREADY_ACTIVE"
                if operation == "ACTIVATE"
                else "ROLLBACK_TARGET_IS_CURRENT"
            )
        prior_activation = self.session.scalar(
            select(LightRAGGenerationTransitionEvent.id).where(
                LightRAGGenerationTransitionEvent.outcome == "APPLIED",
                LightRAGGenerationTransitionEvent.after_generation_id == target_id,
            )
        )
        if operation == "ACTIVATE":
            return "ACTIVATION_TARGET_NOT_FRESH" if prior_activation is not None else None
        if pointer.active_generation_id is None:
            return "NO_ACTIVE_GENERATION"
        current_transition = self.session.scalar(
            select(LightRAGGenerationTransitionEvent)
            .where(
                LightRAGGenerationTransitionEvent.outcome == "APPLIED",
                LightRAGGenerationTransitionEvent.committed_pointer_version
                == pointer.pointer_version,
                LightRAGGenerationTransitionEvent.after_generation_id
                == pointer.active_generation_id,
            )
            .limit(1)
        )
        if current_transition is None:
            raise LightRAGControlPlaneError("ACTIVE_GENERATION_HISTORY_CORRUPT")
        if prior_activation is None or current_transition.observed_generation_id != target_id:
            return "ROLLBACK_TARGET_NOT_PREDECESSOR"
        return None

    def _pointer(self, *, lock: bool) -> LightRAGActiveGenerationPointer:
        statement = select(LightRAGActiveGenerationPointer).where(
            LightRAGActiveGenerationPointer.scope == "GLOBAL"
        )
        if lock:
            statement = statement.with_for_update()
        pointer = self.session.scalar(statement)
        if pointer is None:
            raise LightRAGControlPlaneError("ACTIVE_GENERATION_POINTER_MISSING")
        return pointer

    def _event(
        self,
        pointer: LightRAGActiveGenerationPointer,
        *,
        operation: str,
        outcome: str,
        error_code: str | None,
        target_id: str,
        expected_id: str | None,
        expected_version: int,
        actor: str,
        key: str,
        request_digest: str,
        observed_id: str | None = None,
        observed_version: int | None = None,
    ) -> LightRAGGenerationTransitionEvent:
        if observed_version is None:
            before_id = pointer.active_generation_id
            before_version = pointer.pointer_version
        else:
            # A successful first activation legitimately observed a null pointer;
            # the explicit version distinguishes that from the default path.
            before_id = observed_id
            before_version = observed_version
        after_id = target_id if outcome == "APPLIED" else before_id
        committed_version = before_version + 1 if outcome == "APPLIED" else before_version
        receipt_digest = _canonical_digest(
            {
                "operation": operation,
                "outcome": outcome,
                "errorCode": error_code,
                "targetGenerationId": target_id,
                "expectedGenerationId": expected_id,
                "observedGenerationId": before_id,
                "afterGenerationId": after_id,
                "expectedPointerVersion": expected_version,
                "observedPointerVersion": before_version,
                "committedPointerVersion": committed_version,
                "actorId": actor,
                "idempotencyKey": key,
                "requestDigest": request_digest,
            }
        )
        event = LightRAGGenerationTransitionEvent(
            id=uuid.uuid4(),
            operation=operation,
            outcome=outcome,
            error_code=error_code,
            target_generation_id=target_id,
            expected_generation_id=expected_id,
            observed_generation_id=before_id,
            after_generation_id=after_id,
            expected_pointer_version=expected_version,
            observed_pointer_version=before_version,
            committed_pointer_version=committed_version,
            actor_id=actor,
            idempotency_key=key,
            request_digest=request_digest,
            receipt_digest=receipt_digest,
        )
        self.session.execute(text("SELECT set_config('tc.lightrag_audit_write', 'on', true)"))
        self.session.add(event)
        self.session.flush()
        self.session.execute(text("SELECT set_config('tc.lightrag_audit_write', 'off', true)"))
        return event

    def _after_pointer_update(self, operation: str) -> None:
        """Fault-injection seam; failures roll back pointer and receipt together."""


class DurableLightRAGGenerationControlPlane:
    """Own transactions and preserve rejected-transition evidence before raising."""

    def __init__(
        self,
        engine: Engine,
        *,
        repository_type: type[LightRAGGenerationRepository] = LightRAGGenerationRepository,
    ) -> None:
        self._sessions = sessionmaker(engine, expire_on_commit=False)
        self._repository_type = repository_type

    def register_generation(self, **kwargs: Any) -> LightRAGGeneration:
        with self._sessions.begin() as session:
            return self._repository_type(session).register_generation(**kwargs)

    def active_snapshot(self) -> ActiveGenerationSnapshot:
        with self._sessions() as session:
            return self._repository_type(session).active_snapshot()

    def active_generation(self) -> ActiveGenerationBinding:
        with self._sessions() as session:
            return self._repository_type(session).active_generation()

    def activate(
        self,
        generation_id: str,
        *,
        expected_generation_id: str | None,
        expected_pointer_version: int,
        actor_id: str,
        idempotency_key: str,
    ) -> LightRAGGenerationTransitionEvent:
        try:
            with self._sessions.begin() as session:
                event = self._repository_type(session).activate(
                    generation_id,
                    expected_generation_id=expected_generation_id,
                    expected_pointer_version=expected_pointer_version,
                    actor_id=actor_id,
                    idempotency_key=idempotency_key,
                )
        except LightRAGControlPlaneError:
            raise
        except Exception:
            event = self._record_failure(
                operation="ACTIVATE",
                generation_id=generation_id,
                expected_generation_id=expected_generation_id,
                expected_pointer_version=expected_pointer_version,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
            )
            if event.outcome == "APPLIED":
                return event
            raise LightRAGTransitionRejected(event) from None
        if event.outcome == "REJECTED":
            raise LightRAGTransitionRejected(event)
        return event

    def rollback(
        self,
        generation_id: str,
        *,
        expected_generation_id: str,
        expected_pointer_version: int,
        actor_id: str,
        idempotency_key: str,
    ) -> LightRAGGenerationTransitionEvent:
        try:
            with self._sessions.begin() as session:
                event = self._repository_type(session).rollback(
                    generation_id,
                    expected_generation_id=expected_generation_id,
                    expected_pointer_version=expected_pointer_version,
                    actor_id=actor_id,
                    idempotency_key=idempotency_key,
                )
        except LightRAGControlPlaneError:
            raise
        except Exception:
            event = self._record_failure(
                operation="ROLLBACK",
                generation_id=generation_id,
                expected_generation_id=expected_generation_id,
                expected_pointer_version=expected_pointer_version,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
            )
            if event.outcome == "APPLIED":
                return event
            raise LightRAGTransitionRejected(event) from None
        if event.outcome == "REJECTED":
            raise LightRAGTransitionRejected(event)
        return event

    def _record_failure(
        self,
        *,
        operation: str,
        generation_id: str,
        expected_generation_id: str | None,
        expected_pointer_version: int,
        actor_id: str,
        idempotency_key: str,
    ) -> LightRAGGenerationTransitionEvent:
        with self._sessions.begin() as session:
            # Use the base repository here so a fault-injection subclass cannot
            # prevent the sanitized evidence transaction from being written.
            return LightRAGGenerationRepository(session).record_transition_failure(
                operation=operation,
                generation_id=generation_id,
                expected_generation_id=expected_generation_id,
                expected_pointer_version=expected_pointer_version,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
            )


def _runtime_metadata(value: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(value, Mapping) or any(
        field not in value for field in _RUNTIME_METADATA_FIELDS
    ):
        raise LightRAGControlPlaneError("RUNTIME_METADATA_INVALID")
    return {
        "auxiliaryAuthorityId": _identifier(
            value["auxiliaryAuthorityId"], 128, "RUNTIME_METADATA_INVALID"
        ),
        "providerContractVersion": _nonblank(
            value["providerContractVersion"], 128, "RUNTIME_METADATA_INVALID"
        ),
        "providerDistribution": _nonblank(
            value["providerDistribution"], 128, "RUNTIME_METADATA_INVALID"
        ),
        "providerVersion": _nonblank(
            value["providerVersion"], 64, "RUNTIME_METADATA_INVALID"
        ),
        "runtimeConfigDigest": _digest(
            value["runtimeConfigDigest"], "RUNTIME_METADATA_INVALID"
        ),
    }


def _canonical_digest(value: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise LightRAGControlPlaneError("METADATA_NOT_CANONICAL_JSON") from error
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _identifier(value: str, maximum: int, code: str) -> str:
    if not isinstance(value, str) or len(value) > maximum or not _IDENTIFIER.fullmatch(value):
        raise LightRAGControlPlaneError(code)
    return value


def _nonblank(value: str, maximum: int, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise LightRAGControlPlaneError(code)
    return value


def _digest(value: str, code: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise LightRAGControlPlaneError(code)
    return value if value.startswith("sha256:") else f"sha256:{value}"


__all__ = [
    "ActiveGenerationBinding",
    "ActiveGenerationSnapshot",
    "DurableLightRAGGenerationControlPlane",
    "LightRAGControlPlaneError",
    "LightRAGGenerationRepository",
    "LightRAGTransitionRejected",
]
