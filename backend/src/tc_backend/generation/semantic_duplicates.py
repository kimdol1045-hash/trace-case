"""Sealed deterministic semantic-duplicate input for production apply planning.

The vectorizer is deliberately local and credential-free.  Its receipt binds
the workspace/version, context/result digests, algorithm policy, dimensions,
threshold, and every generated/existing vector digest so an apply replay cannot
silently fall back to missing semantic inputs or a different policy.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from tc_backend.generation.deduplication import (
    DeduplicationStep,
    ExistingCaseInput,
    GeneratedCaseInput,
    SemanticVector,
)

SEMANTIC_DUPLICATE_POLICY_VERSION: Final = "offline-char-ngram-cosine-v2"
SEMANTIC_DUPLICATE_PROVIDER_ID: Final = "local-deterministic"
SEMANTIC_DUPLICATE_MODEL_ID: Final = "sha256-char-ngram-feature-hash"
SEMANTIC_DUPLICATE_MODEL_REVISION: Final = "2"
SEMANTIC_DUPLICATE_DIMENSIONS: Final = 256
SEMANTIC_DUPLICATE_THRESHOLD: Final = 0.85
_WHITESPACE = re.compile(r"\s+")


class SemanticDuplicateSealError(ValueError):
    """A semantic duplicate receipt is incomplete, stale, or tampered."""


@dataclass(frozen=True, slots=True)
class SemanticDuplicatePolicySeal:
    version: str
    provider_id: str
    model_id: str
    model_revision: str
    dimensions: int
    threshold: float
    digest: str


@dataclass(frozen=True, slots=True)
class SealedSemanticDuplicateInput:
    """In-memory vectors plus the immutable receipt used by apply provenance."""

    source_workspace_id: str
    source_workspace_version: int
    source_workspace_version_digest: str
    context_digest: str
    result_digest: str
    policy: SemanticDuplicatePolicySeal
    generated_vectors: tuple[SemanticVector, ...]
    generated_vector_digests: tuple[str, ...]
    existing_vectors: tuple[tuple[str, SemanticVector], ...]
    existing_vector_digests: tuple[tuple[str, str], ...]
    receipt_digest: str

    def public_seal(self) -> dict[str, object]:
        """Return hash-only policy provenance suitable for audit/receipt metadata."""

        return {
            "sourceWorkspaceId": self.source_workspace_id,
            "sourceWorkspaceVersion": self.source_workspace_version,
            "sourceWorkspaceVersionDigest": self.source_workspace_version_digest,
            "policyVersion": self.policy.version,
            "providerId": self.policy.provider_id,
            "modelId": self.policy.model_id,
            "modelRevision": self.policy.model_revision,
            "dimensions": self.policy.dimensions,
            "threshold": self.policy.threshold,
            "policyDigest": self.policy.digest,
            "receiptDigest": self.receipt_digest,
        }


def build_sealed_semantic_duplicate_input(
    generated_cases: Sequence[GeneratedCaseInput],
    existing_cases: Sequence[ExistingCaseInput],
    *,
    source_workspace_id: str,
    source_workspace_version: int,
    context_digest: str,
    result_digest: str,
) -> SealedSemanticDuplicateInput:
    """Vectorize a fixed projection and seal every policy and source dimension."""

    if not source_workspace_id or source_workspace_version < 1:
        raise SemanticDuplicateSealError("source workspace/version is invalid")
    if not _is_sha256(context_digest) or not _is_sha256(result_digest):
        raise SemanticDuplicateSealError("context/result digest is invalid")
    policy = semantic_duplicate_policy_seal()
    generated_vectors = tuple(_vectorize_case(case, policy.dimensions) for case in generated_cases)
    existing_values = tuple(sorted(existing_cases, key=lambda case: case.case_id))
    existing_vectors = tuple(
        (case.case_id, _vectorize_case(case, policy.dimensions)) for case in existing_values
    )
    generated_digests = tuple(_vector_digest(vector) for vector in generated_vectors)
    existing_digests = tuple(
        (case_id, _vector_digest(vector)) for case_id, vector in existing_vectors
    )
    workspace_digest = _digest(
        {
            "sourceWorkspaceId": source_workspace_id,
            "sourceWorkspaceVersion": source_workspace_version,
        }
    )
    envelope = {
        "sourceWorkspaceId": source_workspace_id,
        "sourceWorkspaceVersion": source_workspace_version,
        "sourceWorkspaceVersionDigest": workspace_digest,
        "contextDigest": context_digest,
        "resultDigest": result_digest,
        "policy": _policy_value(policy),
        "generatedVectorDigests": list(generated_digests),
        "existingVectorDigests": [
            {"caseId": case_id, "digest": digest}
            for case_id, digest in existing_digests
        ],
    }
    receipt = SealedSemanticDuplicateInput(
        source_workspace_id=source_workspace_id,
        source_workspace_version=source_workspace_version,
        source_workspace_version_digest=workspace_digest,
        context_digest=context_digest,
        result_digest=result_digest,
        policy=policy,
        generated_vectors=generated_vectors,
        generated_vector_digests=generated_digests,
        existing_vectors=existing_vectors,
        existing_vector_digests=existing_digests,
        receipt_digest=_digest(envelope),
    )
    validate_sealed_semantic_duplicate_input(
        receipt,
        source_workspace_id=source_workspace_id,
        source_workspace_version=source_workspace_version,
        context_digest=context_digest,
        result_digest=result_digest,
    )
    return receipt


def validate_sealed_semantic_duplicate_input(
    receipt: SealedSemanticDuplicateInput,
    *,
    source_workspace_id: str,
    source_workspace_version: int,
    context_digest: str,
    result_digest: str,
) -> None:
    """Cross-validate source/workspace/version/digests and detect tampering."""

    expected_policy = semantic_duplicate_policy_seal()
    if receipt.policy != expected_policy:
        raise SemanticDuplicateSealError("semantic duplicate policy seal mismatch")
    if (
        receipt.source_workspace_id != source_workspace_id
        or receipt.source_workspace_version != source_workspace_version
        or receipt.context_digest != context_digest
        or receipt.result_digest != result_digest
    ):
        raise SemanticDuplicateSealError("semantic duplicate source seal mismatch")
    expected_workspace_digest = _digest(
        {
            "sourceWorkspaceId": source_workspace_id,
            "sourceWorkspaceVersion": source_workspace_version,
        }
    )
    if receipt.source_workspace_version_digest != expected_workspace_digest:
        raise SemanticDuplicateSealError("source workspace version digest mismatch")
    if len(receipt.generated_vectors) != len(receipt.generated_vector_digests):
        raise SemanticDuplicateSealError("generated semantic vector inventory mismatch")
    if tuple(_vector_digest(vector) for vector in receipt.generated_vectors) != (
        receipt.generated_vector_digests
    ):
        raise SemanticDuplicateSealError("generated semantic vector digest mismatch")
    if tuple(
        (case_id, _vector_digest(vector)) for case_id, vector in receipt.existing_vectors
    ) != receipt.existing_vector_digests:
        raise SemanticDuplicateSealError("existing semantic vector digest mismatch")
    envelope = {
        "sourceWorkspaceId": receipt.source_workspace_id,
        "sourceWorkspaceVersion": receipt.source_workspace_version,
        "sourceWorkspaceVersionDigest": receipt.source_workspace_version_digest,
        "contextDigest": receipt.context_digest,
        "resultDigest": receipt.result_digest,
        "policy": _policy_value(receipt.policy),
        "generatedVectorDigests": list(receipt.generated_vector_digests),
        "existingVectorDigests": [
            {"caseId": case_id, "digest": digest}
            for case_id, digest in receipt.existing_vector_digests
        ],
    }
    if receipt.receipt_digest != _digest(envelope):
        raise SemanticDuplicateSealError("semantic duplicate receipt digest mismatch")


def semantic_duplicate_policy_seal() -> SemanticDuplicatePolicySeal:
    value = {
        "algorithmVersion": SEMANTIC_DUPLICATE_POLICY_VERSION,
        "providerId": SEMANTIC_DUPLICATE_PROVIDER_ID,
        "modelId": SEMANTIC_DUPLICATE_MODEL_ID,
        "modelRevision": SEMANTIC_DUPLICATE_MODEL_REVISION,
        "dimensions": SEMANTIC_DUPLICATE_DIMENSIONS,
        "threshold": SEMANTIC_DUPLICATE_THRESHOLD,
    }
    return SemanticDuplicatePolicySeal(
        version=SEMANTIC_DUPLICATE_POLICY_VERSION,
        provider_id=SEMANTIC_DUPLICATE_PROVIDER_ID,
        model_id=SEMANTIC_DUPLICATE_MODEL_ID,
        model_revision=SEMANTIC_DUPLICATE_MODEL_REVISION,
        dimensions=SEMANTIC_DUPLICATE_DIMENSIONS,
        threshold=SEMANTIC_DUPLICATE_THRESHOLD,
        digest=f"sha256:{_digest(value)}",
    )


def with_semantic_vector(
    case: ExistingCaseInput,
    vector: SemanticVector,
) -> ExistingCaseInput:
    """Copy an existing projection with its receipt-backed vector attached."""

    return ExistingCaseInput(
        case_id=case.case_id,
        case_key=case.case_key,
        summary=case.summary,
        steps=case.steps,
        case_expected_result=case.case_expected_result,
        status=case.status,
        deleted=case.deleted,
        deleted_at=case.deleted_at,
        semantic_vector=vector,
    )


def _vectorize_case(
    case: GeneratedCaseInput | ExistingCaseInput,
    dimensions: int,
) -> SemanticVector:
    features = _semantic_features(case.summary, case.steps, case.case_expected_result)
    vector = [0.0] * dimensions
    for feature in features:
        digest = hashlib.sha256(feature.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] & 1 == 0 else -1.0
        vector[index] += sign
    norm = math.hypot(*vector)
    if norm == 0.0:
        raise SemanticDuplicateSealError("semantic vector has zero magnitude")
    return tuple(value / norm for value in vector)


def _semantic_features(
    summary: str,
    steps: Sequence[DeduplicationStep],
    expected_result: str,
) -> tuple[str, ...]:
    fields = [("summary", summary)]
    for index, step in enumerate(steps, start=1):
        fields.append((f"step:{index}:precondition", step.precondition or ""))
        fields.append((f"step:{index}:action", step.action))
    fields.append(("case-expected", expected_result))
    return tuple(
        f"{scope}|{feature}"
        for scope, value in fields
        for feature in _features(_normalize(value))
    )


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return _WHITESPACE.sub(" ", normalized)


def _features(value: str) -> tuple[str, ...]:
    padded = f"^{value}$"
    features = [f"2:{padded[index:index + 2]}" for index in range(len(padded) - 1)]
    features.extend(
        f"3:{padded[index:index + 3]}" for index in range(max(0, len(padded) - 2))
    )
    return tuple(features or ("EMPTY",))


def _policy_value(policy: SemanticDuplicatePolicySeal) -> dict[str, object]:
    return {
        "version": policy.version,
        "providerId": policy.provider_id,
        "modelId": policy.model_id,
        "modelRevision": policy.model_revision,
        "dimensions": policy.dimensions,
        "threshold": policy.threshold,
        "digest": policy.digest,
    }


def _vector_digest(vector: SemanticVector) -> str:
    return _digest([float(value).hex() for value in vector])


def _digest(value: object) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
