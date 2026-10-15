from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest

from tc_backend.generation import (
    ContextEvidence,
    EvidenceStatus,
    GenerationContext,
    GenerationErrorCode,
    GenerationValidationError,
    GenerationValidator,
    UnsupportedClaimPolicy,
    ValidationIssueCode,
)


def _case(index: int, *, evidence_ids: list[str] | None = None) -> dict[str, object]:
    return {
        "summary": f"로그인 성공 경로 {chr(ord('A') + index)}",
        "steps": [
            {"sequence": 1, "precondition": "활성 계정", "action": "로그인을 시도한다"},
            {"sequence": 2, "action": "대시보드를 확인한다"},
        ],
        "caseExpectedResult": "대시보드가 표시된다",
        "priority": "HIGH",
        "labels": ["login", f"path-{index}"],
        "evidenceNodeIds": evidence_ids or ["node-active"],
        "generationReason": "로그인 요구사항을 검증한다",
        "inferenceOnly": False,
        "openQuestions": [],
    }


def _payload(count: int = 5) -> dict[str, object]:
    return {
        "outputSchemaVersion": "1.0.0",
        "cases": [_case(index) for index in range(count)],
        "categoryCandidate": {
            "categoryId": "folder_auth",
            "score": 0.9,
            "alternatives": [{"categoryId": "folder_general", "score": 0.1}],
        },
    }


def _context() -> GenerationContext:
    return GenerationContext(
        evidence=(
            ContextEvidence(
                "node-active",
                EvidenceStatus.ACTIVE,
                "로그인 성공 경로와 활성 계정에서 대시보드 표시를 검증한다. 5회 실패 시 잠근다.",
            ),
            ContextEvidence("node-old", EvidenceStatus.SUPERSEDED, "오래된 로그인 정책"),
        ),
        category_ids=frozenset({"folder_auth", "folder_general"}),
    )


def test_validates_canonical_five_to_ten_case_shape_and_context_ids() -> None:
    result = GenerationValidator().validate(_payload(), _context())

    assert len(result.cases) == 5
    assert result.cases[0].steps[1].sequence == 2
    assert result.cases[0].evidence_node_ids == ("node-active",)
    assert result.category_candidate.category_id == "folder_auth"
    assert result.requires_review is False


@pytest.mark.parametrize("count", [0, 4, 11])
def test_rejects_case_counts_outside_batch_contract(count: int) -> None:
    with pytest.raises(GenerationValidationError) as exc_info:
        GenerationValidator().validate(_payload(count), _context())
    assert exc_info.value.code is GenerationErrorCode.INVALID_RESPONSE


def test_rejects_missing_extra_and_wrong_type_fields() -> None:
    missing = _payload()
    del missing["cases"][0]["caseExpectedResult"]  # type: ignore[index]
    with pytest.raises(GenerationValidationError, match="missing required fields"):
        GenerationValidator().validate(missing, _context())

    extra = _payload()
    extra["cases"][0]["model"] = "do-not-trust-me"  # type: ignore[index]
    with pytest.raises(GenerationValidationError, match="unsupported fields"):
        GenerationValidator().validate(extra, _context())

    wrong = _payload()
    wrong["cases"][0]["inferenceOnly"] = 1  # type: ignore[index]
    with pytest.raises(GenerationValidationError, match="boolean"):
        GenerationValidator().validate(wrong, _context())


def test_rejects_non_contiguous_steps_and_duplicate_labels() -> None:
    sequence = _payload()
    sequence["cases"][0]["steps"][1]["sequence"] = 3  # type: ignore[index]
    with pytest.raises(GenerationValidationError, match="contiguous one-based"):
        GenerationValidator().validate(sequence, _context())

    labels = _payload()
    labels["cases"][0]["labels"] = ["same", "same"]  # type: ignore[index]
    with pytest.raises(GenerationValidationError, match="unique"):
        GenerationValidator().validate(labels, _context())


def test_rejects_normalized_duplicate_summaries_labels_and_categories() -> None:
    summaries = _payload()
    summaries["cases"][1]["summary"] = "  로그인 성공 경로 a  "  # type: ignore[index]
    with pytest.raises(GenerationValidationError, match="summaries.*unique normalized"):
        GenerationValidator().validate(summaries, _context())

    labels = _payload()
    labels["cases"][0]["labels"] = ["LOGIN", "login"]  # type: ignore[index]
    with pytest.raises(GenerationValidationError, match="unique normalized"):
        GenerationValidator().validate(labels, _context())

    categories = _payload()
    categories["categoryCandidate"]["alternatives"] = [  # type: ignore[index]
        {"categoryId": "folder_auth", "score": 0.1}
    ]
    with pytest.raises(GenerationValidationError, match="category IDs.*unique"):
        GenerationValidator().validate(categories, _context())


@pytest.mark.parametrize(
    ("node_id", "message"),
    [("node-missing", "missing context evidence"), ("node-old", "inactive context evidence")],
)
def test_rejects_missing_or_inactive_evidence(node_id: str, message: str) -> None:
    payload = _payload()
    payload["cases"][0]["evidenceNodeIds"] = [node_id]  # type: ignore[index]

    with pytest.raises(GenerationValidationError, match=message):
        GenerationValidator().validate(payload, _context())


def test_rejects_context_duplicate_evidence_and_invented_category_ids() -> None:
    duplicate_context = GenerationContext(
        evidence=(
            ContextEvidence("same", "ACTIVE", "first"),
            ContextEvidence("same", "ACTIVE", "second"),
        ),
        category_ids=frozenset({"folder_auth"}),
    )
    with pytest.raises(GenerationValidationError, match="duplicate evidence"):
        GenerationValidator().validate(_payload(), duplicate_context)

    invented = _payload()
    invented["categoryCandidate"]["categoryId"] = "folder_invented"  # type: ignore[index]
    with pytest.raises(GenerationValidationError, match="outside the supplied context"):
        GenerationValidator().validate(invented, _context())


def test_unsupported_numeric_and_policy_claims_are_deterministically_flagged() -> None:
    payload = _payload()
    first = payload["cases"][0]  # type: ignore[index]
    first["caseExpectedResult"] = "99회 실패하면 반드시 계정을 영구 삭제한다"

    result = GenerationValidator().validate(payload, _context())

    assert result.requires_review is True
    assert [issue.code for issue in result.issues] == [
        ValidationIssueCode.UNSUPPORTED_NUMERIC_CLAIM,
        ValidationIssueCode.UNSUPPORTED_POLICY_CLAIM,
    ]
    assert result.issues[0].claim == "99회"
    assert result.issues[0].field == "cases[0].caseExpectedResult"


def test_supported_numeric_claim_is_not_flagged_and_explicit_claim_allowlist_is_honored() -> None:
    payload = _payload()
    first = payload["cases"][0]  # type: ignore[index]
    first["caseExpectedResult"] = "5회 실패 시 잠근다"
    assert GenerationValidator().validate(payload, _context()).issues == ()

    first["caseExpectedResult"] = "관리자는 반드시 계정을 검토한다"
    context = GenerationContext(
        evidence=_context().evidence,
        category_ids=_context().category_ids,
        supported_claims=frozenset({"관리자는 반드시 계정을 검토한다"}),
    )
    assert GenerationValidator().validate(payload, context).issues == ()


def test_reject_policy_fails_closed_on_unsupported_claim() -> None:
    payload = deepcopy(_payload())
    payload["cases"][0]["generationReason"] = "데이터는 반드시 77년 보관한다"  # type: ignore[index]

    with pytest.raises(GenerationValidationError, match="unsupported numeric or policy"):
        GenerationValidator(
            unsupported_claim_policy=UnsupportedClaimPolicy.REJECT
        ).validate(payload, _context())


def test_canonical_versioned_fixture_is_accepted_without_claim_flags() -> None:
    repository_root = Path(__file__).parents[3]
    fixture_path = (
        repository_root
        / "packages/contracts/generation/fixtures/1.0.0/output.grounded.json"
    )
    payload = cast(dict[str, object], json.loads(fixture_path.read_text(encoding="utf-8")))
    context = GenerationContext(
        evidence=(
            ContextEvidence(
                "work_item_password_reset",
                "ACTIVE",
                "이메일 인증 후 비밀번호 변경. 인증번호 유효시간은 5분이고 5회 오류 시 잠근다.",
            ),
            ContextEvidence(
                "test_case_password_reset_happy_path",
                "ACTIVE",
                "유효한 이메일 인증번호로 비밀번호를 재설정하고 완료 안내를 표시한다.",
            ),
            ContextEvidence(
                "decision_password_code_expiry",
                "ACTIVE",
                "비밀번호 재설정 인증번호는 발급 후 5분 동안 유효하다.",
            ),
            ContextEvidence(
                "decision_password_attempt_lock",
                "ACTIVE",
                "잘못된 인증번호를 5회 입력하면 해당 인증 요청을 잠근다.",
            ),
            ContextEvidence(
                "execution_password_lock_bypass",
                "FAIL",
                "다섯 번째 오류 후 새 인증번호를 요청하면 잠금이 우회되었다.",
            ),
            ContextEvidence(
                "issue_node_password_lock_bypass",
                "OPEN",
                "인증번호 재발급으로 5회 오류 잠금을 우회할 수 있다.",
            ),
            ContextEvidence(
                "extra_design_password_lock_state",
                "PROVIDED",
                "잠금 상태에서는 인증번호 입력 필드를 비활성화하고 잠금 안내 문구를 표시한다.",
            ),
        ),
        category_ids=frozenset({"folder_members", "folder_uncategorized"}),
    )

    result = GenerationValidator().validate(payload, context)

    assert result.output_schema_version == "1.0.0"
    assert len(result.cases) == 5
    assert result.issues == ()
