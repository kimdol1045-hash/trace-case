from __future__ import annotations

from dataclasses import replace

import pytest

from tc_backend.generation.deduplication import (
    DeduplicationDetector,
    DeduplicationStep,
    ExistingCaseInput,
    GeneratedCaseInput,
    ReuseReason,
)
from tc_backend.generation.semantic_duplicates import (
    SemanticDuplicateSealError,
    build_sealed_semantic_duplicate_input,
    validate_sealed_semantic_duplicate_input,
    with_semantic_vector,
)


def _generated(summary: str) -> GeneratedCaseInput:
    return GeneratedCaseInput(
        summary=summary,
        steps=(DeduplicationStep(action="저장 버튼을 누른다"),),
        case_expected_result="변경 내용이 저장된다",
    )


def _existing(summary: str) -> ExistingCaseInput:
    return ExistingCaseInput(
        case_id="case_existing",
        case_key="TC-000001",
        summary=summary,
        steps=(DeduplicationStep(action="저장 버튼을 누른다"),),
        case_expected_result="변경 내용이 저장된다",
    )


def test_offline_vectors_are_deterministic_and_drive_semantic_reuse() -> None:
    generated = (_generated("프로필 변경 사항 저장 성공"),)
    existing = (_existing("프로필 변경 사항 저장 성공 여부"),)
    arguments = {
        "source_workspace_id": "workspace_00000000000000000000000000000001",
        "source_workspace_version": 7,
        "context_digest": "1" * 64,
        "result_digest": "2" * 64,
    }
    first = build_sealed_semantic_duplicate_input(generated, existing, **arguments)
    replay = build_sealed_semantic_duplicate_input(generated, existing, **arguments)

    assert first == replay
    assert first.policy.dimensions == len(first.generated_vectors[0])
    assert first.public_seal()["threshold"] == first.policy.threshold
    assert first.public_seal()["providerId"] == "local-deterministic"
    assert first.public_seal()["modelId"] == "sha256-char-ngram-feature-hash"
    assert first.public_seal()["modelRevision"] == "2"
    existing_with_vector = with_semantic_vector(existing[0], first.existing_vectors[0][1])
    result = DeduplicationDetector(semantic_threshold=first.policy.threshold).detect(
        (
            replace(generated[0], semantic_vector=first.generated_vectors[0]),
        ),
        (existing_with_vector,),
    )
    assert result[0].reuse_candidates[0].reason is ReuseReason.SEMANTIC


def test_receipt_cross_validates_workspace_version_and_detects_vector_tamper() -> None:
    receipt = build_sealed_semantic_duplicate_input(
        (_generated("계정 저장"),),
        (_existing("사용자 계정 저장"),),
        source_workspace_id="workspace_00000000000000000000000000000001",
        source_workspace_version=3,
        context_digest="3" * 64,
        result_digest="4" * 64,
    )
    with pytest.raises(SemanticDuplicateSealError, match="source seal mismatch"):
        validate_sealed_semantic_duplicate_input(
            receipt,
            source_workspace_id=receipt.source_workspace_id,
            source_workspace_version=4,
            context_digest=receipt.context_digest,
            result_digest=receipt.result_digest,
        )
    tampered = replace(
        receipt,
        generated_vectors=((1.0, *receipt.generated_vectors[0][1:]),),
    )
    with pytest.raises(SemanticDuplicateSealError, match="vector digest mismatch"):
        validate_sealed_semantic_duplicate_input(
            tampered,
            source_workspace_id=receipt.source_workspace_id,
            source_workspace_version=receipt.source_workspace_version,
            context_digest=receipt.context_digest,
            result_digest=receipt.result_digest,
        )


def test_unicode_normalization_and_order_are_bound_by_the_production_vector_seal() -> None:
    arguments = {
        "source_workspace_id": "workspace_00000000000000000000000000000001",
        "source_workspace_version": 8,
        "context_digest": "5" * 64,
        "result_digest": "6" * 64,
    }
    normalized = build_sealed_semantic_duplicate_input(
        (_generated("ＡＢＣ 프로필 저장"),), (), **arguments
    )
    canonical = build_sealed_semantic_duplicate_input(
        (_generated("ABC 프로필 저장"),), (), **arguments
    )
    assert normalized.generated_vectors == canonical.generated_vectors
    assert normalized.generated_vector_digests == canonical.generated_vector_digests

    ordered = GeneratedCaseInput(
        summary="프로필 저장",
        steps=(
            DeduplicationStep(action="이름을 입력한다"),
            DeduplicationStep(action="저장 버튼을 누른다"),
        ),
        case_expected_result="변경 내용이 저장된다",
    )
    reordered = replace(ordered, steps=tuple(reversed(ordered.steps)))
    ordered_seal = build_sealed_semantic_duplicate_input((ordered,), (), **arguments)
    reordered_seal = build_sealed_semantic_duplicate_input((reordered,), (), **arguments)
    assert ordered_seal.generated_vector_digests != reordered_seal.generated_vector_digests
