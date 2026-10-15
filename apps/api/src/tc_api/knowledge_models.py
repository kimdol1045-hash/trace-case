"""Sprint 6 typed knowledge graph, search, explorer, and job models."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from tc_api.models import ApiModel, RequestModel

KnowledgeStatusValue = Literal["ACTIVE", "AMBIGUOUS", "SUPERSEDED", "RETRACTED"]
KnowledgeNodeTypeValue = Literal[
    "WORK_ITEM",
    "MENU",
    "FEATURE",
    "REQUIREMENT",
    "DECISION",
    "CONSTRAINT",
    "OPEN_QUESTION",
    "RISK",
    "TEST_CASE",
    "EXECUTION",
    "ISSUE",
    "RELEASE",
]
KnowledgePredicateValue = Literal[
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
]
KnowledgeSourceTypeValue = Literal[
    "SLACK_MESSAGE_REVISION",
    "TEST_CASE_VERSION",
    "EXECUTION_ATTEMPT",
    "ISSUE_SNAPSHOT",
    "MANUAL",
]
KnowledgeTargetTypeValue = Literal["NODE", "EDGE"]
HybridSearchSignalValue = Literal[
    "TYPED_GRAPH",
    "VECTOR",
    "LIGHTRAG",
    "EXACT_TEXT",
    "FEATURE_PROXIMITY",
    "RECENCY",
]
HybridSearchDegradationReasonValue = Literal[
    "LIGHTRAG_UNAVAILABLE",
    "VECTOR_UNAVAILABLE",
]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")]
NonBlank = Annotated[str, Field(min_length=1, max_length=10_000, pattern=r"\S")]


class KnowledgeSourceRef(ApiModel):
    source_type: KnowledgeSourceTypeValue
    source_entity_id: Identifier
    source_revision_id: Identifier | None


class KnowledgeCitation(ApiModel):
    source: KnowledgeSourceRef
    label: NonBlank
    uri: str | None = Field(max_length=2048)
    excerpt: Annotated[str, Field(min_length=1, max_length=2000, pattern=r"\S")] | None
    occurred_at: datetime
    deleted: bool


class KnowledgeEvidenceResource(ApiModel):
    id: Identifier
    subject_type: KnowledgeTargetTypeValue
    subject_id: Identifier
    source: KnowledgeSourceRef
    revision_ordinal: int = Field(ge=1)
    content_hash: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    citation: KnowledgeCitation
    captured_at: datetime


class KnowledgeNodeResource(ApiModel):
    id: Identifier
    node_type: KnowledgeNodeTypeValue
    canonical_key: NonBlank
    label: str = Field(min_length=1, max_length=500, pattern=r"\S")
    content: NonBlank
    status: KnowledgeStatusValue
    valid_from: datetime
    valid_to: datetime | None
    primary_source: KnowledgeSourceRef
    evidence_ids: list[Identifier] = Field(min_length=1)
    resource_version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class KnowledgeEdgeResource(ApiModel):
    id: Identifier
    from_node_id: Identifier
    predicate: KnowledgePredicateValue
    to_node_id: Identifier
    status: KnowledgeStatusValue
    valid_from: datetime
    valid_to: datetime | None
    primary_source: KnowledgeSourceRef
    evidence_ids: list[Identifier] = Field(min_length=1)
    resource_version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class KnowledgeNodePage(ApiModel):
    items: list[KnowledgeNodeResource] = Field(max_length=200)
    next_cursor: str | None
    total: int = Field(ge=0)


class KnowledgeNodeDetail(ApiModel):
    node: KnowledgeNodeResource
    scope_node_ids: list[Identifier]
    evidence: list[KnowledgeEvidenceResource] = Field(min_length=1)


class KnowledgeEvidencePage(ApiModel):
    items: list[KnowledgeEvidenceResource] = Field(max_length=1200)
    total: int = Field(ge=0)


class KnowledgeStatusPatchRequest(RequestModel):
    targetType: KnowledgeTargetTypeValue
    targetId: Identifier
    expectedVersion: int = Field(ge=1)
    status: KnowledgeStatusValue
    effectiveAt: datetime
    reason: NonBlank
    evidenceIds: list[Identifier] = Field(min_length=1)
    changedBy: NonBlank
    scopeNodeIds: list[Identifier] | SkipJsonSchema[None] = None
    validFrom: datetime | SkipJsonSchema[None] = None
    validTo: datetime | SkipJsonSchema[None] = None

    @model_validator(mode="after")
    def validate_unique_identifiers(self) -> Self:
        if len(self.evidenceIds) != len(set(self.evidenceIds)):
            raise ValueError("evidenceIds must contain unique values")
        if self.scopeNodeIds is not None and len(self.scopeNodeIds) != len(
            set(self.scopeNodeIds)
        ):
            raise ValueError("scopeNodeIds must contain unique values")
        return self


class KnowledgeStatusAuditResource(ApiModel):
    id: Identifier
    target_type: KnowledgeTargetTypeValue
    target_id: Identifier
    from_status: KnowledgeStatusValue
    to_status: KnowledgeStatusValue
    reason: NonBlank
    evidence_ids: list[Identifier] = Field(min_length=1)
    changed_by: NonBlank
    changed_at: datetime
    target_version: int = Field(ge=2)


class KnowledgeStatusPatchResponse(ApiModel):
    target: KnowledgeNodeResource | KnowledgeEdgeResource
    audit: KnowledgeStatusAuditResource
    projection_queued: bool


class KnowledgeStatusAuditPage(ApiModel):
    items: list[KnowledgeStatusAuditResource]
    total: int = Field(ge=0)


class HybridSearchRequest(RequestModel):
    query: str = Field(min_length=1, max_length=1000, pattern=r"\S")
    statuses: list[KnowledgeStatusValue] = Field(min_length=1)
    sourceTypes: list[KnowledgeSourceTypeValue]
    nodeTypes: list[KnowledgeNodeTypeValue]
    featureId: Identifier | None
    limit: int = Field(ge=1, le=200)
    cursor: str | None = Field(default=None, min_length=1, max_length=2048)

    @model_validator(mode="after")
    def validate_unique_filters(self) -> Self:
        for name in ("statuses", "sourceTypes", "nodeTypes"):
            values = getattr(self, name)
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must contain unique values")
        return self


class HybridSearchRecency(ApiModel):
    basis_at: datetime = Field(
        description="Authoritative node recordedAt timestamp used for recency scoring."
    )
    age_seconds: int = Field(
        ge=0,
        description="Whole seconds from basisAt to the response executedAt timestamp.",
    )
    score: float = Field(
        ge=0,
        le=1,
        description="Normalized recency component used by the hybrid ranker.",
    )


class HybridSearchResultReason(ApiModel):
    summary: NonBlank = Field(
        description="Deterministic human-readable explanation derived from this result's actual signals."
    )
    signals: list[HybridSearchSignalValue] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_signals(self) -> Self:
        if len(self.signals) != len(set(self.signals)):
            raise ValueError("reason signals must contain unique values")
        required = {"TYPED_GRAPH", "RECENCY"}
        if not required.issubset(self.signals):
            raise ValueError("reason signals must include TYPED_GRAPH and RECENCY")
        return self


class HybridSearchResultItem(ApiModel):
    rank: int = Field(
        ge=1,
        description="Absolute one-based rank across all pages for the same ordered search.",
    )
    node_id: Identifier
    node_type: KnowledgeNodeTypeValue
    label: str = Field(min_length=1, max_length=500, pattern=r"\S")
    content: NonBlank
    score: float = Field(ge=0, le=1)
    reason: HybridSearchResultReason
    status: KnowledgeStatusValue
    source: KnowledgeSourceRef
    feature_distance: int | None = Field(
        ge=0,
        description=(
            "Shortest typed-graph hop count from the selected or matched feature; null means "
            "feature proximity did not contribute because no feature scope/path was available."
        ),
    )
    recency: HybridSearchRecency
    degraded: bool
    degradation_reasons: list[HybridSearchDegradationReasonValue]
    citation: KnowledgeCitation

    @model_validator(mode="after")
    def validate_explainability(self) -> Self:
        if len(self.degradation_reasons) != len(set(self.degradation_reasons)):
            raise ValueError("degradationReasons must contain unique values")
        if self.degraded != bool(self.degradation_reasons):
            raise ValueError("degraded must match degradationReasons")
        has_feature_signal = "FEATURE_PROXIMITY" in self.reason.signals
        if has_feature_signal != (self.feature_distance is not None):
            raise ValueError(
                "FEATURE_PROXIMITY must be present exactly when featureDistance is not null"
            )
        unavailable_signals = {
            "LIGHTRAG_UNAVAILABLE": "LIGHTRAG",
            "VECTOR_UNAVAILABLE": "VECTOR",
        }
        for reason, signal in unavailable_signals.items():
            if reason in self.degradation_reasons and signal in self.reason.signals:
                raise ValueError(f"{signal} cannot be present when {reason} is reported")
        if self.source != self.citation.source:
            raise ValueError("result source must match citation source")
        return self


class HybridSearchResponse(ApiModel):
    query: str = Field(min_length=1, max_length=1000, pattern=r"\S")
    items: list[HybridSearchResultItem] = Field(max_length=200)
    next_cursor: str | None
    total: int = Field(ge=0)
    degraded: bool
    degradation_reasons: list[HybridSearchDegradationReasonValue]
    executed_at: datetime

    @model_validator(mode="after")
    def validate_rank_and_degradation_alignment(self) -> Self:
        if len(self.degradation_reasons) != len(set(self.degradation_reasons)):
            raise ValueError("degradationReasons must contain unique values")
        if self.degraded != bool(self.degradation_reasons):
            raise ValueError("degraded must match degradationReasons")
        if len(self.items) > self.total:
            raise ValueError("items cannot exceed total")
        if self.items:
            first_rank = self.items[0].rank
            expected_ranks = list(range(first_rank, first_rank + len(self.items)))
            if [item.rank for item in self.items] != expected_ranks:
                raise ValueError("item ranks must be consecutive and ordered")
            if self.items[-1].rank > self.total:
                raise ValueError("item rank cannot exceed total")
            if any(
                previous.score < current.score
                for previous, current in zip(self.items, self.items[1:])
            ):
                raise ValueError("item scores must be non-increasing by rank")
        for item in self.items:
            if item.degraded != self.degraded:
                raise ValueError("item degraded must match response degraded")
            if item.degradation_reasons != self.degradation_reasons:
                raise ValueError(
                    "item degradationReasons must match response degradationReasons"
                )
        return self


class OntologyExplorerRequest(RequestModel):
    rootNodeId: Identifier
    direction: Literal["OUTBOUND", "INBOUND", "BOTH"]
    predicates: list[KnowledgePredicateValue]
    statuses: list[KnowledgeStatusValue] = Field(min_length=1)
    maxDepth: int = Field(ge=1, le=5)
    limit: int = Field(ge=1, le=200)
    cursor: str | None = Field(default=None, min_length=1, max_length=2048)

    @model_validator(mode="after")
    def validate_unique_filters(self) -> Self:
        if len(self.predicates) != len(set(self.predicates)):
            raise ValueError("predicates must contain unique values")
        if len(self.statuses) != len(set(self.statuses)):
            raise ValueError("statuses must contain unique values")
        return self


class OntologyExplorerNode(ApiModel):
    node: KnowledgeNodeResource
    depth: int = Field(ge=0, le=5)


class OntologyExplorerResponse(ApiModel):
    root_node_id: Identifier
    nodes: list[OntologyExplorerNode] = Field(min_length=1, max_length=200)
    edges: list[KnowledgeEdgeResource] = Field(max_length=1000)
    evidence: list[KnowledgeEvidenceResource] = Field(max_length=1200)
    truncated: bool
    next_cursor: str | None


class KnowledgeProjectionJobRequest(RequestModel):
    idempotencyKey: str = Field(min_length=1, max_length=255, pattern=r"\S")
    limit: int = Field(default=100, ge=1, le=1000)


class KnowledgeReindexJobRequest(RequestModel):
    idempotencyKey: str = Field(min_length=1, max_length=255, pattern=r"\S")
    nodeIds: list[Identifier]


class KnowledgeJobResponse(ApiModel):
    id: Identifier
    job_type: Literal["KNOWLEDGE_PROJECTION", "KNOWLEDGE_REINDEX"]
    status: Literal["QUEUED", "RUNNING", "RETRY", "SUCCEEDED", "DEAD"]
