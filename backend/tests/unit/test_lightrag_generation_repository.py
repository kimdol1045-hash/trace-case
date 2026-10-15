from __future__ import annotations

import traceback
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from tc_backend.db.models import LightRAGGeneration
from tc_backend.repositories.lightrag_generation_repository import (
    DurableLightRAGGenerationControlPlane,
    LightRAGControlPlaneError,
    LightRAGGenerationRepository,
    LightRAGTransitionRejected,
)


class _RegistrationSession:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def scalar(self, statement: object) -> None:
        return None

    def get(self, model: object, identity: object) -> None:
        return None

    def add(self, value: Any) -> None:
        self.added.append(value)

    def flush(self) -> None:
        return None


class _OneRowResult:
    def __init__(self, row: tuple[object, ...] | None) -> None:
        self.row = row

    def one_or_none(self) -> tuple[object, ...] | None:
        return self.row


class _ActiveGenerationSession:
    def __init__(self, row: tuple[object, ...] | None) -> None:
        self.row = row

    def execute(self, _statement: object) -> _OneRowResult:
        return _OneRowResult(self.row)


def _registration(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "generation_id": "tc_generation_001",
        "provider_id": "official-lightrag",
        "workspace": "tc_shadow_001",
        "auxiliary_target_digest": "01" * 32,
        "manifest_digest": "02" * 32,
        "corpus_digest": "03" * 32,
        "source_ledger_digest": "04" * 32,
        "projection_digest": "05" * 32,
        "pgvector_digest": "06" * 32,
        "document_count": 10,
        "recorded_by": "release-owner",
        "idempotency_key": "register-generation-001",
        "metadata": {
            "auxiliaryAuthorityId": "authority-test",
            "providerContractVersion": "tc-lightrag-generation-provider/v1",
            "providerDistribution": "lightrag-hku",
            "providerVersion": "1.5.6",
            "runtimeConfigDigest": "sha256:" + "08" * 32,
        },
    }
    value.update(overrides)
    return value


def test_registration_normalizes_bare_sha256_and_keeps_only_verified_metadata() -> None:
    session = _RegistrationSession()
    repository = LightRAGGenerationRepository(session)  # type: ignore[arg-type]

    generation = repository.register_generation(**_registration())  # type: ignore[arg-type]

    assert generation.verification_status == "VERIFIED"
    assert generation.auxiliary_target_digest == "sha256:" + "01" * 32
    assert generation.registration_digest.startswith("sha256:")
    assert generation.document_count == 10
    assert session.added == [generation]


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"generation_id": "../mutable"}, "GENERATION_ID_INVALID"),
        ({"manifest_digest": "latest"}, "MANIFEST_DIGEST_INVALID"),
        ({"document_count": 0}, "DOCUMENT_COUNT_INVALID"),
        ({"document_count": True}, "DOCUMENT_COUNT_INVALID"),
        ({"metadata": {"nan": float("nan")}}, "RUNTIME_METADATA_INVALID"),
    ],
)
def test_registration_rejects_unpinned_or_noncanonical_metadata(
    override: dict[str, object], code: str
) -> None:
    repository = LightRAGGenerationRepository(_RegistrationSession())  # type: ignore[arg-type]

    with pytest.raises(LightRAGControlPlaneError, match=code) as raised:
        repository.register_generation(**_registration(**override))  # type: ignore[arg-type]

    assert raised.value.code == code


def test_active_generation_resolves_pointer_and_metadata_in_one_statement() -> None:
    recorded_at = datetime(2026, 9, 2, tzinfo=UTC)
    generation = LightRAGGeneration(
        generation_id="tc_generation_001",
        provider_id="official-lightrag",
        workspace="tc_shadow_001",
        auxiliary_target_digest="sha256:" + "01" * 32,
        manifest_digest="sha256:" + "02" * 32,
        corpus_digest="sha256:" + "03" * 32,
        source_ledger_digest="sha256:" + "04" * 32,
        projection_digest="sha256:" + "05" * 32,
        pgvector_digest="sha256:" + "06" * 32,
        document_count=10,
        verification_status="VERIFIED",
        metadata_json={
            "auxiliaryAuthorityId": "authority-test",
            "providerContractVersion": "tc-lightrag-generation-provider/v1",
            "providerDistribution": "lightrag-hku",
            "providerVersion": "1.5.6",
            "runtimeConfigDigest": "sha256:" + "08" * 32,
        },
        recorded_by="release-owner",
        registration_idempotency_key="register-generation-001",
        registration_digest="sha256:" + "07" * 32,
    )
    repository = LightRAGGenerationRepository(
        _ActiveGenerationSession((4, generation.generation_id, recorded_at, generation))  # type: ignore[arg-type]
    )

    binding = repository.active_generation()

    assert binding.generation_id == generation.generation_id
    assert binding.pointer_version == 4
    assert binding.pointer_updated_at == recorded_at
    assert binding.authority_id == "authority-test"
    assert binding.provider_distribution == "lightrag-hku"
    assert binding.provider_version == "1.5.6"
    assert binding.runtime_config_digest == "sha256:" + "08" * 32
    assert binding.workspace == generation.workspace
    assert binding.cache_key == (4, generation.generation_id, generation.registration_digest)


@pytest.mark.parametrize(
    ("row", "code"),
    [
        (None, "ACTIVE_GENERATION_POINTER_MISSING"),
        ((0, None, datetime(2026, 9, 2, tzinfo=UTC), None), "NO_ACTIVE_GENERATION"),
        (
            (1, "tc_generation_missing", datetime(2026, 9, 2, tzinfo=UTC), None),
            "ACTIVE_GENERATION_LEDGER_CORRUPT",
        ),
    ],
)
def test_active_generation_fails_closed_for_absent_or_missing_routes(
    row: tuple[object, ...] | None, code: str
) -> None:
    repository = LightRAGGenerationRepository(
        _ActiveGenerationSession(row)  # type: ignore[arg-type]
    )

    with pytest.raises(LightRAGControlPlaneError, match=code) as raised:
        repository.active_generation()

    assert raised.value.code == code


def test_active_generation_rejects_legacy_row_without_runtime_attestation() -> None:
    generation = LightRAGGeneration(
        generation_id="tc_generation_legacy",
        provider_id="official-lightrag",
        workspace="tc_shadow_legacy",
        auxiliary_target_digest="sha256:" + "01" * 32,
        manifest_digest="sha256:" + "02" * 32,
        corpus_digest="sha256:" + "03" * 32,
        source_ledger_digest="sha256:" + "04" * 32,
        projection_digest="sha256:" + "05" * 32,
        pgvector_digest="sha256:" + "06" * 32,
        document_count=10,
        verification_status="VERIFIED",
        metadata_json={},
        recorded_by="release-owner",
        registration_idempotency_key="register-generation-legacy",
        registration_digest="sha256:" + "07" * 32,
    )
    repository = LightRAGGenerationRepository(
        _ActiveGenerationSession(
            (4, generation.generation_id, datetime.now(UTC), generation)
        )  # type: ignore[arg-type]
    )

    with pytest.raises(LightRAGControlPlaneError, match="RUNTIME_METADATA_INVALID"):
        repository.active_generation()


@pytest.mark.parametrize("operation", ["activate", "rollback"])
def test_transition_fault_traceback_suppresses_original_sensitive_cause(
    operation: str,
) -> None:
    secret = f"{operation}-credential-must-not-leak"

    class FailingTransaction:
        def __enter__(self) -> object:
            raise RuntimeError(secret)

        def __exit__(self, *_args: object) -> None:
            return None

    class FailingSessions:
        @staticmethod
        def begin() -> FailingTransaction:
            return FailingTransaction()

    class SanitizingControlPlane(DurableLightRAGGenerationControlPlane):
        def _record_failure(self, **kwargs: Any) -> Any:
            return SimpleNamespace(
                outcome="REJECTED",
                error_code=f"{str(kwargs['operation'])}_FAILED",
                id="event-redacted",
                sequence=1,
            )

    control = object.__new__(SanitizingControlPlane)
    control._sessions = FailingSessions()  # type: ignore[assignment]
    control._repository_type = LightRAGGenerationRepository
    invoke = getattr(control, operation)
    kwargs: dict[str, object] = {
        "generation_id": "generation-a",
        "expected_generation_id": None if operation == "activate" else "generation-b",
        "expected_pointer_version": 1,
        "actor_id": "release-owner",
        "idempotency_key": f"{operation}-failure",
    }

    with pytest.raises(LightRAGTransitionRejected) as raised:
        invoke(**kwargs)

    rendered = "".join(traceback.format_exception(raised.type, raised.value, raised.tb))
    assert secret not in rendered
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True
