from __future__ import annotations

import asyncio

import pytest

from tc_backend.knowledge import (
    CandidateRelation,
    CandidateRelationKind,
    KnowledgeCandidateValidator,
    KnowledgeNodeType,
    KnowledgeStateClassifier,
    KnowledgeStatus,
    KnowledgeValidationError,
    OllamaKnowledgeExtractionProvider,
    ProviderEvidence,
    ProviderExtraction,
    ProviderKnowledgeCandidate,
    SlackKnowledgeExtractor,
    SlackSourceBundle,
    SlackSourceRevision,
    SourceRevisionOperation,
)


def _revision(
    revision_id: str,
    message_id: str,
    revision_ts: str,
    body: str | None,
    *,
    operation: SourceRevisionOperation = SourceRevisionOperation.CREATE,
) -> SlackSourceRevision:
    return SlackSourceRevision(
        source_revision_id=revision_id,
        message_id=message_id,
        revision_ts=revision_ts,
        author_id="U_TEST",
        body=body,
        permalink=f"https://synthetic.invalid/{revision_id}",
        operation=operation,
    )


def _bundle(*revisions: SlackSourceRevision) -> SlackSourceBundle:
    return SlackSourceBundle(
        workspace_id="T_TEST",
        channel_id="C_TEST",
        thread_ts="100.000001",
        revisions=tuple(revisions),
    )


def _candidate(
    candidate_id: str,
    node_type: KnowledgeNodeType,
    content: str,
    topic_key: str,
    revision_id: str,
    excerpt: str,
    *,
    relations: tuple[CandidateRelation, ...] = (),
    explicitly_retracted: bool = False,
) -> ProviderKnowledgeCandidate:
    return ProviderKnowledgeCandidate(
        candidate_id=candidate_id,
        node_type=node_type,
        content=content,
        topic_key=topic_key,
        confidence=0.91,
        evidence=(ProviderEvidence(revision_id, excerpt),),
        relations=relations,
        explicitly_retracted=explicitly_retracted,
    )


def _extraction(*candidates: ProviderKnowledgeCandidate) -> ProviderExtraction:
    return ProviderExtraction(
        provider="fake-local-provider",
        model="fake-model",
        schema_version="knowledge-extraction-v1",
        candidates=tuple(candidates),
    )


def _classify(
    bundle: SlackSourceBundle, *candidates: ProviderKnowledgeCandidate
) -> dict[str, object]:
    validated = KnowledgeCandidateValidator().validate(bundle, _extraction(*candidates))
    return {
        item.candidate.candidate_id: item
        for item in KnowledgeStateClassifier().classify(bundle, validated.candidates)
    }


def test_ollama_adapter_decodes_one_grounded_structured_call() -> None:
    bundle = _bundle(
        _revision(
            "rev-ollama",
            "100.000001",
            "100.000001",
            "결제 승인은 실패 잠금 정책을 따라야 한다.",
        )
    )

    class FakeTransport:
        calls = 0

        async def extract(self, **request: object) -> dict[str, object]:
            self.calls += 1
            assert request["model"] == "qwen-test"
            assert "rev-ollama" in str(request["prompt"])
            return {
                "model": "qwen-test",
                "message": {
                    "content": (
                        '{"schemaVersion":"knowledge-extraction/1.0.0",'
                        '"candidates":[{"candidateId":"payment-lock",'
                        '"nodeType":"DECISION","content":"결제 승인은 실패 잠금 정책을 따라야 한다.",'
                        '"topicKey":"payment-lock","confidence":0.99,'
                        '"evidence":[{"sourceRevisionId":"rev-ollama",'
                        '"excerpt":"결제 승인은 실패 잠금 정책을 따라야 한다."}],'
                        '"relations":[],"explicitlyRetracted":false}]}'
                    )
                },
            }

    transport = FakeTransport()
    provider = OllamaKnowledgeExtractionProvider(
        transport,  # type: ignore[arg-type]
        host="http://127.0.0.1:11434",
        model="qwen-test",
    )

    result = asyncio.run(SlackKnowledgeExtractor(provider).extract(bundle))

    assert transport.calls == 1
    assert result.provider == "ollama"
    assert result.schema_version == "knowledge-extraction/1.0.0"
    assert result.active[0].candidate.candidate_id == "payment-lock"


def test_provider_protocol_extracts_all_controlled_types_with_grounded_evidence() -> None:
    bundle = _bundle(
        _revision(
            "rev-1",
            "100.000001",
            "100.000001",
            (
                "요구사항은 인증번호 5분이다. 최종 결정은 5회 실패 시 잠금이다. "
                "제약은 30분 후 해제다. 관리자 해제가 필요한가?"
            ),
        )
    )
    candidates = (
        _candidate(
            "requirement",
            KnowledgeNodeType.REQUIREMENT,
            "인증번호는 5분간 유효하다.",
            "otp-expiry",
            "rev-1",
            "요구사항은 인증번호 5분이다.",
        ),
        _candidate(
            "decision",
            KnowledgeNodeType.DECISION,
            "5회 실패 시 잠근다.",
            "otp-lock",
            "rev-1",
            "최종 결정은 5회 실패 시 잠금이다.",
        ),
        _candidate(
            "constraint",
            KnowledgeNodeType.CONSTRAINT,
            "잠금은 30분 후 해제한다.",
            "otp-unlock",
            "rev-1",
            "제약은 30분 후 해제다.",
        ),
        _candidate(
            "question",
            KnowledgeNodeType.OPEN_QUESTION,
            "관리자 해제가 필요한가?",
            "otp-admin-unlock",
            "rev-1",
            "관리자 해제가 필요한가?",
        ),
    )

    class FakeProvider:
        async def extract(self, received: SlackSourceBundle) -> ProviderExtraction:
            assert received is bundle
            return _extraction(*reversed(candidates))

    result = asyncio.run(SlackKnowledgeExtractor(FakeProvider()).extract(bundle))

    assert {item.candidate.node_type for item in result.candidates} == set(KnowledgeNodeType)
    assert {item.status for item in result.candidates} == {KnowledgeStatus.ACTIVE}
    assert [item.candidate.candidate_id for item in result.candidates] == [
        "constraint",
        "decision",
        "question",
        "requirement",
    ]
    assert result.active == result.candidates


def test_validator_rejects_hallucinated_or_unknown_evidence() -> None:
    bundle = _bundle(_revision("rev-1", "100.1", "100.1", "원문에 있는 사실"))
    hallucinated = _candidate(
        "candidate",
        KnowledgeNodeType.REQUIREMENT,
        "근거 없는 사실",
        "topic",
        "rev-1",
        "원문에 없는 인용",
    )
    with pytest.raises(KnowledgeValidationError, match="exact normalized source span"):
        KnowledgeCandidateValidator().validate(bundle, _extraction(hallucinated))

    unknown = _candidate(
        "candidate",
        KnowledgeNodeType.REQUIREMENT,
        "사실",
        "topic",
        "rev-unknown",
        "사실",
    )
    with pytest.raises(KnowledgeValidationError, match="unknown revision"):
        KnowledgeCandidateValidator().validate(bundle, _extraction(unknown))


def test_explicit_reversal_supersedes_only_the_older_candidate() -> None:
    bundle = _bundle(
        _revision("rev-old", "100.1", "100.1", "주소는 즉시 영구 삭제한다."),
        _revision(
            "rev-new",
            "100.2",
            "100.2",
            "영구 삭제는 취소한다. 최종 결정은 soft delete로 30일 보관한다.",
        ),
    )
    old = _candidate(
        "hard-delete",
        KnowledgeNodeType.DECISION,
        "주소는 즉시 영구 삭제한다.",
        "address-delete-policy",
        "rev-old",
        "주소는 즉시 영구 삭제한다.",
    )
    new = _candidate(
        "soft-delete",
        KnowledgeNodeType.DECISION,
        "주소는 soft delete로 30일 보관한다.",
        "address-delete-policy",
        "rev-new",
        "최종 결정은 soft delete로 30일 보관한다.",
        relations=(CandidateRelation(CandidateRelationKind.SUPERSEDES, "hard-delete"),),
    )

    classified = _classify(bundle, new, old)

    assert classified["hard-delete"].status is KnowledgeStatus.SUPERSEDED
    assert classified["hard-delete"].superseded_by_candidate_ids == ("soft-delete",)
    assert classified["soft-delete"].status is KnowledgeStatus.ACTIVE


def test_unresolved_conflict_marks_both_candidates_ambiguous() -> None:
    bundle = _bundle(
        _revision("rev-email", "100.1", "100.1", "알림은 이메일로 보낸다."),
        _revision("rev-slack", "100.2", "100.2", "알림은 Slack에만 보낸다."),
    )
    email = _candidate(
        "email",
        KnowledgeNodeType.DECISION,
        "알림은 이메일로 보낸다.",
        "failure-alert-channel",
        "rev-email",
        "알림은 이메일로 보낸다.",
    )
    slack = _candidate(
        "slack",
        KnowledgeNodeType.DECISION,
        "알림은 Slack에만 보낸다.",
        "failure-alert-channel",
        "rev-slack",
        "알림은 Slack에만 보낸다.",
    )

    classified = _classify(bundle, email, slack)

    assert classified["email"].status is KnowledgeStatus.AMBIGUOUS
    assert classified["slack"].status is KnowledgeStatus.AMBIGUOUS
    assert classified["email"].conflicting_candidate_ids == ("slack",)
    assert classified["slack"].conflicting_candidate_ids == ("email",)


def test_cyclic_reversal_output_is_rejected_instead_of_arbitrarily_classified() -> None:
    bundle = _bundle(
        _revision("rev", "100.1", "100.1", "정책 A를 정책 B로 바꾼다."),
    )
    first = _candidate(
        "first",
        KnowledgeNodeType.DECISION,
        "정책 A",
        "policy",
        "rev",
        "정책 A",
        relations=(CandidateRelation(CandidateRelationKind.SUPERSEDES, "second"),),
    )
    second = _candidate(
        "second",
        KnowledgeNodeType.DECISION,
        "정책 B",
        "policy",
        "rev",
        "정책 B",
        relations=(CandidateRelation(CandidateRelationKind.SUPERSEDES, "first"),),
    )

    with pytest.raises(KnowledgeValidationError, match="cycle"):
        KnowledgeCandidateValidator().validate(bundle, _extraction(first, second))


def test_latest_delete_revision_retracts_knowledge_without_losing_old_evidence() -> None:
    bundle = _bundle(
        _revision("rev-created", "100.1", "100.1", "인증번호는 5분간 유효하다."),
        _revision(
            "rev-deleted",
            "100.1",
            "100.2",
            None,
            operation=SourceRevisionOperation.DELETE,
        ),
    )
    candidate = _candidate(
        "otp-expiry",
        KnowledgeNodeType.REQUIREMENT,
        "인증번호는 5분간 유효하다.",
        "otp-expiry",
        "rev-created",
        "인증번호는 5분간 유효하다.",
    )

    classified = _classify(bundle, candidate)["otp-expiry"]

    assert classified.status is KnowledgeStatus.RETRACTED
    assert classified.reason == "source_message_deleted"
    assert classified.candidate.evidence[0].source_revision_id == "rev-created"


def test_explicit_retraction_relation_has_priority_over_conflict() -> None:
    bundle = _bundle(
        _revision("rev-old", "100.1", "100.1", "이 정책으로 진행한다."),
        _revision("rev-new", "100.2", "100.2", "이 정책은 철회한다."),
    )
    old = _candidate(
        "old",
        KnowledgeNodeType.DECISION,
        "이 정책으로 진행한다.",
        "policy",
        "rev-old",
        "이 정책으로 진행한다.",
    )
    withdrawal = _candidate(
        "withdrawal",
        KnowledgeNodeType.DECISION,
        "이 정책은 철회한다.",
        "policy",
        "rev-new",
        "이 정책은 철회한다.",
        relations=(CandidateRelation(CandidateRelationKind.RETRACTS, "old"),),
        explicitly_retracted=True,
    )

    classified = _classify(bundle, old, withdrawal)

    assert classified["old"].status is KnowledgeStatus.RETRACTED
    assert classified["old"].retracted_by_candidate_ids == ("withdrawal",)
    assert classified["withdrawal"].status is KnowledgeStatus.RETRACTED
