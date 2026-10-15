from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from tc_api.knowledge_models import (
    HybridSearchDegradationReasonValue,
    HybridSearchResponse,
)
from tc_api.knowledge_serializers import hybrid_item
from tc_api.routers.knowledge import _hybrid_rank_offset
from tc_backend.db.models import KnowledgeEvidence, KnowledgeNode
from tc_backend.services.knowledge_service import HybridSearchMatch

EXECUTED_AT = datetime(2026, 8, 21, 2, tzinfo=UTC)


def _match(
    *,
    signals: tuple[str, ...],
    feature_distance: int | None,
    score: float,
    status: str = "ACTIVE",
) -> HybridSearchMatch:
    schema_id = uuid.UUID("00000000-0000-7000-8000-000000000001")
    node_id = uuid.uuid4()
    node = KnowledgeNode(
        id=node_id,
        ontology_schema_id=schema_id,
        node_type="DECISION",
        status=status,
        label="Password reset expiry",
        entity_type=None,
        entity_id=None,
        properties={"content": "Password reset codes expire after five minutes."},
        recorded_at=EXECUTED_AT - timedelta(hours=1),
        valid_from=EXECUTED_AT - timedelta(days=1),
        valid_to=None if status in {"ACTIVE", "AMBIGUOUS"} else EXECUTED_AT,
        created_at=EXECUTED_AT - timedelta(days=1),
        updated_at=EXECUTED_AT,
    )
    evidence = KnowledgeEvidence(
        id=uuid.uuid4(),
        ontology_schema_id=schema_id,
        knowledge_node_id=node_id,
        knowledge_edge_id=None,
        source_record_id=uuid.uuid4(),
        source_type="MANUAL",
        source_id=uuid.uuid4(),
        source_revision=None,
        permalink=None,
        excerpt_hash="sha256:" + "a" * 64,
        recorded_at=node.recorded_at,
        created_at=node.recorded_at,
    )
    return HybridSearchMatch(
        node=node,
        evidence=evidence,
        score=score,
        signals=signals,
        feature_distance=feature_distance,
    )


def _item(
    *,
    rank: int,
    score: float,
    signals: tuple[str, ...],
    feature_distance: int | None,
    degraded: bool = False,
    degradation_reasons: list[HybridSearchDegradationReasonValue] | None = None,
    status: str = "ACTIVE",
):
    return hybrid_item(
        cast(Session, None),
        _match(
            signals=signals,
            feature_distance=feature_distance,
            score=score,
            status=status,
        ),
        rank=rank,
        degraded=degraded,
        degradation_reasons=degradation_reasons or [],
        executed_at=EXECUTED_AT,
    )


@pytest.mark.parametrize(
    ("signals", "feature_distance", "included", "excluded"),
    [
        (
            ("TYPED_GRAPH", "RECENCY"),
            None,
            ("typed graph candidate", "feature proximity did not contribute", "recency contributed"),
            ("exact query text", "vector similarity", "LightRAG availability"),
        ),
        (
            ("TYPED_GRAPH", "EXACT_TEXT", "VECTOR", "RECENCY"),
            None,
            ("exact query text matched", "vector similarity contributed"),
            ("selected-feature distance", "LightRAG availability"),
        ),
        (
            (
                "TYPED_GRAPH",
                "EXACT_TEXT",
                "VECTOR",
                "FEATURE_PROXIMITY",
                "LIGHTRAG",
                "RECENCY",
            ),
            2,
            (
                "exact query text matched",
                "vector similarity contributed",
                "selected-feature distance was 2 hops",
                "LightRAG availability was recorded as an auxiliary signal",
            ),
            ("feature proximity did not contribute",),
        ),
    ],
)
def test_hybrid_reason_is_deterministic_and_uses_only_actual_signals(
    signals: tuple[str, ...],
    feature_distance: int | None,
    included: tuple[str, ...],
    excluded: tuple[str, ...],
) -> None:
    first = _item(
        rank=3,
        score=0.81234,
        signals=signals,
        feature_distance=feature_distance,
    )
    second = _item(
        rank=3,
        score=0.81234,
        signals=signals,
        feature_distance=feature_distance,
    )

    assert first.reason.summary == second.reason.summary
    assert first.reason.summary.startswith("Rank 3 with score 0.812:")
    assert "2026-08-21T01:00:00+00:00 (3600 seconds old)" in first.reason.summary
    for phrase in included:
        assert phrase in first.reason.summary
    for phrase in excluded:
        assert phrase not in first.reason.summary


def test_hybrid_item_exposes_non_active_status_null_distance_and_typed_degradation() -> None:
    item = _item(
        rank=1,
        score=0.71,
        signals=("TYPED_GRAPH", "EXACT_TEXT", "RECENCY"),
        feature_distance=None,
        degraded=True,
        degradation_reasons=["LIGHTRAG_UNAVAILABLE", "VECTOR_UNAVAILABLE"],
        status="RETRACTED",
    )

    payload = item.model_dump(mode="json", by_alias=True)
    assert payload["status"] == "RETRACTED"
    assert payload["featureDistance"] is None
    assert payload["degraded"] is True
    assert payload["degradationReasons"] == [
        "LIGHTRAG_UNAVAILABLE",
        "VECTOR_UNAVAILABLE",
    ]
    assert payload["source"] == payload["citation"]["source"]
    assert payload["recency"] == {
        "basisAt": "2026-08-21T01:00:00Z",
        "ageSeconds": 3600,
        "score": pytest.approx(1 / (1 + 3600 / 2_592_000)),
    }


def test_hybrid_item_rejects_feature_and_degradation_signal_contradictions() -> None:
    with pytest.raises(ValidationError, match="FEATURE_PROXIMITY"):
        _item(
            rank=1,
            score=0.8,
            signals=("TYPED_GRAPH", "FEATURE_PROXIMITY", "RECENCY"),
            feature_distance=None,
        )
    with pytest.raises(ValidationError, match="LIGHTRAG"):
        _item(
            rank=1,
            score=0.8,
            signals=("TYPED_GRAPH", "LIGHTRAG", "RECENCY"),
            feature_distance=None,
            degraded=True,
            degradation_reasons=["LIGHTRAG_UNAVAILABLE"],
        )


def test_hybrid_response_enforces_absolute_pagination_ranks_and_score_order() -> None:
    third = _item(
        rank=3,
        score=0.82,
        signals=("TYPED_GRAPH", "VECTOR", "RECENCY"),
        feature_distance=None,
    )
    fourth = _item(
        rank=4,
        score=0.76,
        signals=("TYPED_GRAPH", "EXACT_TEXT", "RECENCY"),
        feature_distance=None,
    )
    response = HybridSearchResponse(
        query="password",
        items=[third, fourth],
        next_cursor=None,
        total=4,
        degraded=False,
        degradation_reasons=[],
        executed_at=EXECUTED_AT,
    )
    assert [item.rank for item in response.items] == [3, 4]

    base = response.model_dump()
    rank_gap = {**base, "items": [third.model_dump(), {**fourth.model_dump(), "rank": 5}]}
    with pytest.raises(ValidationError, match="ranks must be consecutive"):
        HybridSearchResponse.model_validate(rank_gap)
    score_inversion = {
        **base,
        "items": [third.model_dump(), {**fourth.model_dump(), "score": 0.99}],
    }
    with pytest.raises(ValidationError, match="scores must be non-increasing"):
        HybridSearchResponse.model_validate(score_inversion)


def test_search_cursor_offset_keeps_ranks_stable_across_pages() -> None:
    cursor = base64.urlsafe_b64encode(json.dumps({"offset": 2}).encode()).decode().rstrip("=")
    assert _hybrid_rank_offset(None) == 0
    assert _hybrid_rank_offset(cursor) == 2
    with pytest.raises(ValueError, match="knowledge cursor is invalid"):
        _hybrid_rank_offset("not-a-cursor")
