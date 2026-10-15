from __future__ import annotations

import uuid

import pytest

from tc_backend.services.knowledge_service import (
    _hybrid_score,
    _hybrid_signals,
    _uuid_hints,
)


@pytest.mark.parametrize(
    ("overrides", "expected_score", "expected_signal"),
    (
        ({"exact": True}, 0.40, "EXACT_TEXT"),
        ({"vector_score": 0.5}, 0.18, "VECTOR"),
        ({"feature_distance": 1}, 0.13, "FEATURE_PROXIMITY"),
        ({"light_rag": True}, 0.14, "LIGHTRAG"),
    ),
)
def test_hybrid_ranker_weights_each_retrieval_signal_independently(
    overrides: dict[str, object], expected_score: float, expected_signal: str
) -> None:
    values: dict[str, object] = {
        "exact": False,
        "vector_score": None,
        "feature_distance": None,
        "light_rag": False,
        "recency_score": 0.0,
    }
    values.update(overrides)

    score = _hybrid_score(**values)  # type: ignore[arg-type]
    signals = _hybrid_signals(
        exact=bool(values["exact"]),
        vector_score=values["vector_score"],  # type: ignore[arg-type]
        feature_distance=values["feature_distance"],  # type: ignore[arg-type]
        light_rag=bool(values["light_rag"]),
    )

    assert score == expected_score
    assert expected_signal in signals
    assert {"TYPED_GRAPH", "RECENCY"}.issubset(signals)


def test_hybrid_ranker_combines_all_weights_once_and_clamps_inputs() -> None:
    assert (
        _hybrid_score(
            exact=True,
            vector_score=2.0,
            feature_distance=0,
            light_rag=True,
            recency_score=2.0,
        )
        == 1.0
    )
    assert _hybrid_signals(
        exact=True,
        vector_score=1.0,
        feature_distance=0,
        light_rag=True,
    ) == (
        "TYPED_GRAPH",
        "EXACT_TEXT",
        "VECTOR",
        "FEATURE_PROXIMITY",
        "LIGHTRAG",
        "RECENCY",
    )


def test_uuid_hints_ignore_invalid_values_and_deduplicate_without_reordering() -> None:
    first = uuid.UUID("018f3d5c-2f37-7ad4-8c9e-1a2b3c4d5e6f")
    second = uuid.UUID("018f3d5c-2f37-7ad4-9c9e-1a2b3c4d5e70")

    assert _uuid_hints((str(first), "not-a-postgres-id", str(first), str(second))) == (
        first,
        second,
    )
