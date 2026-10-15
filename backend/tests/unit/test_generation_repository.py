from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from tc_backend.db.models import (
    GenerationJob,
    KnowledgeEdge,
    KnowledgeEvidence,
    KnowledgeNode,
    KnowledgeSourceRecord,
)
from tc_backend.repositories.generation_repository import (
    ContextEntry,
    GenerationRepository,
    UngroundedGenerationContext,
    _GroundedCandidate,
    _rank_context_entries,
    _walk_active_graph,
    generation_context_digest,
)


def _uuid(value: int) -> uuid.UUID:
    return uuid.UUID(int=value)


def _node(
    value: int,
    node_type: str,
    *,
    recorded_at: datetime,
) -> KnowledgeNode:
    return KnowledgeNode(
        id=_uuid(value),
        ontology_schema_id=_uuid(900),
        node_type=node_type,
        status="ACTIVE",
        label=f"{node_type}-{value}",
        properties={},
        recorded_at=recorded_at,
    )


def _edge(value: int, source: KnowledgeNode, target: KnowledgeNode) -> KnowledgeEdge:
    return KnowledgeEdge(
        id=_uuid(value),
        ontology_schema_id=_uuid(900),
        source_node_id=source.id,
        predicate="DISCUSSES",
        target_node_id=target.id,
        status="ACTIVE",
        properties={},
        recorded_at=source.recorded_at,
    )


def _source(value: int, *, recorded_at: datetime) -> KnowledgeSourceRecord:
    return KnowledgeSourceRecord(
        id=_uuid(value),
        source_kind="SOURCE_REVISION",
        entity_type="SLACK_MESSAGE_REVISION",
        entity_id=_uuid(value + 100),
        revision_key=str(value),
        content_hash=f"hash-{value}",
        recorded_at=recorded_at,
    )


def _evidence(
    value: int,
    source: KnowledgeSourceRecord,
    *,
    node: KnowledgeNode | None = None,
    edge: KnowledgeEdge | None = None,
) -> KnowledgeEvidence:
    return KnowledgeEvidence(
        id=_uuid(value),
        ontology_schema_id=_uuid(900),
        knowledge_node_id=node.id if node is not None else None,
        knowledge_edge_id=edge.id if edge is not None else None,
        source_record_id=source.id,
        source_type=source.entity_type,
        source_id=source.entity_id,
        source_revision=source.revision_key,
        permalink=source.permalink,
        excerpt_hash=source.content_hash,
        recorded_at=source.recorded_at,
    )


def test_active_graph_walk_is_deterministic_and_does_not_cross_another_work_item() -> None:
    now = datetime(2026, 8, 26, tzinfo=UTC)
    root = _node(1, "WORK_ITEM", recorded_at=now)
    decision = _node(2, "DECISION", recorded_at=now)
    other_root = _node(3, "WORK_ITEM", recorded_at=now)
    foreign_decision = _node(4, "DECISION", recorded_at=now)
    edges = (
        _edge(13, other_root, foreign_decision),
        _edge(12, decision, other_root),
        _edge(11, root, decision),
    )
    nodes = {item.id: item for item in (root, decision, other_root, foreign_decision)}

    forward = _walk_active_graph(root.id, nodes_by_id=nodes, edges=edges, max_depth=6)
    reverse = _walk_active_graph(
        root.id, nodes_by_id=nodes, edges=tuple(reversed(edges)), max_depth=6
    )

    assert forward == reverse == {root.id: 0, decision.id: 1}


def test_context_rank_is_stable_and_keeps_all_root_source_revisions() -> None:
    now = datetime(2026, 8, 26, tzinfo=UTC)
    root = _node(1, "WORK_ITEM", recorded_at=now)
    decision = _node(2, "DECISION", recorded_at=now + timedelta(seconds=1))
    old_source = _source(101, recorded_at=now)
    new_source = _source(102, recorded_at=now + timedelta(seconds=1))
    decision_source = _source(103, recorded_at=now + timedelta(seconds=2))
    candidates = (
        _GroundedCandidate(
            evidence=_evidence(203, decision_source, node=decision),
            source=decision_source,
            node=decision,
            edge=None,
            distance=1,
            priority=10,
            reason="ACTIVE_DECISION",
        ),
        _GroundedCandidate(
            evidence=_evidence(202, new_source, node=root),
            source=new_source,
            node=root,
            edge=None,
            distance=0,
            priority=0,
            reason="SLACK_WORK_ITEM_SOURCE",
        ),
        _GroundedCandidate(
            evidence=_evidence(201, old_source, node=root),
            source=old_source,
            node=root,
            edge=None,
            distance=0,
            priority=0,
            reason="SLACK_WORK_ITEM_SOURCE",
        ),
    )

    ranked = _rank_context_entries(candidates)
    reranked = _rank_context_entries(tuple(reversed(candidates)))

    assert ranked == reranked
    assert [entry.rank for entry in ranked] == [1, 2, 3]
    assert [entry.source_revision for entry in ranked[:2]] == ["101", "102"]
    assert ranked[2].reason == "ACTIVE_DECISION"


def test_context_digest_covers_provider_model_schema_and_ranked_snapshot() -> None:
    job = GenerationJob(
        id=_uuid(1),
        slack_work_item_id=_uuid(2),
        source_version=3,
        generation_version=4,
        status="QUEUED",
        provider="local",
        model="qwen3:8b",
        prompt_version="prompt-v1",
        schema_version="generation-v1",
        ontology_schema_id=_uuid(900),
    )
    entry = ContextEntry(
        knowledge_evidence_id=_uuid(10),
        node_id=_uuid(11),
        edge_id=None,
        source_record_id=_uuid(12),
        source_kind="SOURCE_REVISION",
        source_entity_type="SLACK_MESSAGE_REVISION",
        source_entity_id=_uuid(13),
        source_revision="1724000000.000001",
        source_content_hash="a" * 64,
        source_permalink="https://synthetic.invalid/thread/1",
        rank=1,
        reason="SLACK_WORK_ITEM_SOURCE",
        node_snapshot={"status": "ACTIVE", "content": "current"},
        edge_snapshot=None,
    )

    digest = generation_context_digest(job, (entry,))

    assert len(digest) == 64
    assert digest == generation_context_digest(job, (entry,))
    assert digest != generation_context_digest(job, (replace(entry, rank=2),))
    job.model = "different-model"
    assert digest != generation_context_digest(job, (entry,))


def test_evidence_validation_fails_closed_on_missing_or_mismatched_revision() -> None:
    now = datetime(2026, 8, 26, tzinfo=UTC)
    node = _node(1, "DECISION", recorded_at=now)
    source = _source(101, recorded_at=now)
    evidence = _evidence(201, source, node=node)

    GenerationRepository._validate_evidence(evidence, source, _uuid(900))

    evidence.source_revision = None
    with pytest.raises(UngroundedGenerationContext, match="lacks a source revision"):
        GenerationRepository._validate_evidence(evidence, source, _uuid(900))

    evidence.source_revision = source.revision_key
    evidence.excerpt_hash = "different"
    with pytest.raises(UngroundedGenerationContext, match="provenance mismatch"):
        GenerationRepository._validate_evidence(evidence, source, _uuid(900))
