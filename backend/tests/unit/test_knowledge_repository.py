from __future__ import annotations

import math

import pytest

from tc_backend.repositories.knowledge_repository import (
    ALLOWED_RELATIONS,
    BGE_M3_DIMENSION,
    InvalidKnowledgeRelation,
    content_sha256,
    validate_bge_m3_embedding,
    validate_v1_relation,
)


@pytest.mark.parametrize(
    ("predicate", "source", "target"),
    (
        ("AFFECTS", "ISSUE", "TEST_CASE"),
        ("TARGETS", "WORK_ITEM", "FEATURE"),
        ("DISCUSSES", "WORK_ITEM", "OPEN_QUESTION"),
        ("DERIVED_FROM", "TEST_CASE", "REQUIREMENT"),
        ("REFINES", "DECISION", "CONSTRAINT"),
        ("SUPERSEDES", "REQUIREMENT", "DECISION"),
        ("APPLIES_TO", "RISK", "RELEASE"),
        ("VERIFIES", "EXECUTION", "TEST_CASE"),
        ("COVERS", "TEST_CASE", "RISK"),
        ("INSTANCE_OF", "EXECUTION", "TEST_CASE"),
        ("FOUND_BY", "ISSUE", "EXECUTION"),
    ),
)
def test_v1_validator_accepts_declared_source_target_pairs(
    predicate: str, source: str, target: str
) -> None:
    validate_v1_relation(predicate, source, target)


@pytest.mark.parametrize(
    ("predicate", "source", "target"),
    (
        ("FOUND_BY", "TEST_CASE", "ISSUE"),
        ("INSTANCE_OF", "TEST_CASE", "EXECUTION"),
        ("DISCUSSES", "DECISION", "WORK_ITEM"),
        ("VERIFIES", "ISSUE", "REQUIREMENT"),
        ("UNKNOWN", "ISSUE", "EXECUTION"),
    ),
)
def test_v1_validator_rejects_undeclared_source_target_pairs(
    predicate: str, source: str, target: str
) -> None:
    with pytest.raises(InvalidKnowledgeRelation):
        validate_v1_relation(predicate, source, target)


def test_v1_relation_map_has_only_explicit_nonempty_sides() -> None:
    assert set(ALLOWED_RELATIONS) == {
        "AFFECTS",
        "TARGETS",
        "DISCUSSES",
        "DERIVED_FROM",
        "REFINES",
        "SUPERSEDES",
        "APPLIES_TO",
        "VERIFIES",
        "COVERS",
        "INSTANCE_OF",
        "FOUND_BY",
    }
    assert all(sources and targets for sources, targets in ALLOWED_RELATIONS.values())


def test_bge_m3_embedding_is_fixed_to_1024_finite_dimensions() -> None:
    vector = validate_bge_m3_embedding([0] * BGE_M3_DIMENSION)
    assert len(vector) == 1024
    assert all(isinstance(value, float) for value in vector)

    with pytest.raises(ValueError, match="1024 dimensions"):
        validate_bge_m3_embedding([0.0] * (BGE_M3_DIMENSION - 1))
    with pytest.raises(ValueError, match="finite"):
        validate_bge_m3_embedding([math.inf, *([0.0] * (BGE_M3_DIMENSION - 1))])


def test_content_hash_is_stable_sha256() -> None:
    assert content_sha256("동일 내용") == content_sha256("동일 내용".encode())
    assert len(content_sha256("동일 내용")) == 64
    assert content_sha256("동일 내용") != content_sha256("다른 내용")
