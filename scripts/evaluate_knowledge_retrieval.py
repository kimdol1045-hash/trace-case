"""Evaluate synthetic fixtures or an explicit operational retrieval snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, cast

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "evals/fixtures/knowledge-retrieval/manifest.json"
INACTIVE_STATUSES = ("AMBIGUOUS", "SUPERSEDED", "RETRACTED")
RELEVANCE_CATEGORIES = (
    "EXISTING_TEST_CASE",
    "FAILED_EXECUTION",
    "ISSUE",
)
RELEVANCE_NODE_TYPES = {
    "EXISTING_TEST_CASE": "TEST_CASE",
    "FAILED_EXECUTION": "EXECUTION",
    "ISSUE": "ISSUE",
}
EVALUATOR_VERSION = "2.0.0"
LIFECYCLE_STATUSES = ("ACTIVE", *INACTIVE_STATUSES)
NODE_TYPES = (
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
)
EXECUTION_OUTCOMES = ("PASSED", "FAILED", "BLOCKED", "SKIPPED", "ERROR")


class FixtureError(ValueError):
    """A retrieval fixture is malformed or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class SourceRef:
    source_type: str
    source_entity_id: str
    source_revision_id: str

    def as_dict(self) -> dict[str, str]:
        return {
            "sourceType": self.source_type,
            "sourceEntityId": self.source_entity_id,
            "sourceRevisionId": self.source_revision_id,
        }


@dataclass(frozen=True, slots=True)
class Citation:
    source: SourceRef
    label: str
    excerpt: str
    uri: str | None = None


@dataclass(frozen=True, slots=True)
class Document:
    node_id: str
    node_type: str
    label: str
    content: str
    status: str
    evidence_ids: tuple[str, ...]
    source: SourceRef
    citation: Citation
    embedding: tuple[float, ...]
    execution_outcome: str | None = None


@dataclass(frozen=True, slots=True)
class QueryCase:
    query_id: str
    text: str
    statuses: tuple[str, ...]
    embedding: tuple[float, ...]
    relevant_node_ids: tuple[str, ...]
    light_rag_candidate_node_ids: tuple[str, ...]
    relevance_category: str | None = None
    relevant_node_types: Mapping[str, str] = field(default_factory=dict)
    relevant_execution_outcomes: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Gates:
    min_recall_at_k: Mapping[int, float]
    min_active_evidence_coverage: float
    max_inactive_misuse_count: int
    min_citation_source_revision_link_rate: float
    max_duplicate_count: int
    min_reason_rank_completeness: float
    min_degraded_fallback_retention: float
    max_fallback_contract_failures: int
    required_relevance_categories: tuple[str, ...] = ()
    min_required_category_recall: float = 0.0


@dataclass(frozen=True, slots=True)
class EvaluationDataset:
    dataset_id: str
    description: str
    k_values: tuple[int, ...]
    fallback_k: int
    expected_recall_at_k: Mapping[int, float]
    expected_recall_rationale: str
    gates: Gates
    documents: tuple[Document, ...]
    queries: tuple[QueryCase, ...]
    synthetic: bool = True
    evaluation_mode: str = "synthetic"
    threshold_profile: str = "candidate"
    review_status: str = "UNREVIEWED"
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    label_set_id: str | None = None
    provenance: Mapping[str, object] = field(default_factory=dict)
    dataset_fingerprint: str = ""
    captured_results_fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class RetrievalItem:
    document: Document
    rank: int
    score: float
    reason_summary: str
    reason_signals: tuple[str, ...]
    degraded: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "nodeId": self.document.node_id,
            "nodeType": self.document.node_type,
            "executionOutcome": self.document.execution_outcome,
            "rank": self.rank,
            "score": self.score,
            "reason": {
                "summary": self.reason_summary,
                "signals": list(self.reason_signals),
            },
            "status": self.document.status,
            "evidenceIds": list(self.document.evidence_ids),
            "source": self.document.source.as_dict(),
            "citation": {
                "source": self.document.citation.source.as_dict(),
                "label": self.document.citation.label,
                "uri": self.document.citation.uri,
                "excerpt": self.document.citation.excerpt,
            },
            "degraded": self.degraded,
        }


@dataclass(frozen=True, slots=True)
class RetrievalResponse:
    items: tuple[RetrievalItem, ...]
    degraded: bool
    degradation_reasons: tuple[str, ...]


class Retriever(Protocol):
    def search(
        self,
        query: QueryCase,
        documents: Sequence[Document],
        *,
        limit: int,
        light_rag_available: bool,
    ) -> RetrievalResponse: ...


class DeterministicRetriever:
    """Pure local test double for typed-graph, embedding, and LightRAG signals."""

    def search(
        self,
        query: QueryCase,
        documents: Sequence[Document],
        *,
        limit: int,
        light_rag_available: bool,
    ) -> RetrievalResponse:
        allowed_statuses = set(query.statuses)
        light_rag_candidates = set(query.light_rag_candidate_node_ids)
        query_terms = _terms(query.text)
        ranked: list[tuple[float, Document, tuple[str, ...]]] = []
        for document in documents:
            if document.status not in allowed_statuses:
                continue
            vector_score = _cosine(query.embedding, document.embedding)
            document_terms = _terms(f"{document.label} {document.content}")
            term_score = _jaccard(query_terms, document_terms)
            light_rag_hit = (
                light_rag_available and document.node_id in light_rag_candidates
            )
            if vector_score <= 0 and term_score <= 0 and not light_rag_hit:
                continue
            score = 0.75 * vector_score + 0.15 * term_score
            signals = ["TYPED_GRAPH"]
            if vector_score > 0:
                signals.append("VECTOR")
            if term_score > 0:
                signals.append("EXACT_TEXT")
            if light_rag_hit:
                score += 0.1
                signals.append("LIGHTRAG")
            signals.append("RECENCY")
            ranked.append((min(1.0, score), document, tuple(signals)))
        ranked.sort(key=lambda item: (-item[0], item[1].node_id))
        degraded = not light_rag_available
        items = tuple(
            RetrievalItem(
                document=document,
                rank=index,
                score=round(score, 8),
                reason_summary=(
                    "Deterministic typed-graph and fake-embedding ranking signals."
                ),
                reason_signals=signals,
                degraded=degraded,
            )
            for index, (score, document, signals) in enumerate(ranked[:limit], start=1)
        )
        return RetrievalResponse(
            items=items,
            degraded=degraded,
            degradation_reasons=("LIGHTRAG_UNAVAILABLE",) if degraded else (),
        )


class SnapshotRetriever:
    """Replay explicitly supplied result sets; never contacts a service or model."""

    def __init__(
        self, responses: Mapping[tuple[str, bool], RetrievalResponse]
    ) -> None:
        self._responses = responses

    def search(
        self,
        query: QueryCase,
        documents: Sequence[Document],
        *,
        limit: int,
        light_rag_available: bool,
    ) -> RetrievalResponse:
        del documents, limit
        key = (query.query_id, light_rag_available)
        try:
            return self._responses[key]
        except KeyError as exc:
            raise FixtureError(
                f"result snapshot has no {'baseline' if light_rag_available else 'degraded'} response for {query.query_id}"
            ) from exc


@dataclass(frozen=True, slots=True)
class QueryEvaluation:
    query_id: str
    relevant_node_ids: tuple[str, ...]
    baseline: RetrievalResponse
    degraded: RetrievalResponse
    recall_at_k: Mapping[int, float]
    fallback_contract_passed: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "queryId": self.query_id,
            "relevantNodeIds": list(self.relevant_node_ids),
            "recallAtK": {str(key): value for key, value in self.recall_at_k.items()},
            "baseline": {
                "degraded": self.baseline.degraded,
                "degradationReasons": list(self.baseline.degradation_reasons),
                "items": [item.as_dict() for item in self.baseline.items],
            },
            "lightRagUnavailable": {
                "degraded": self.degraded.degraded,
                "degradationReasons": list(self.degraded.degradation_reasons),
                "items": [item.as_dict() for item in self.degraded.items],
            },
            "fallbackContractPassed": self.fallback_contract_passed,
        }


@dataclass(frozen=True, slots=True)
class GateCheck:
    metric: str
    actual: float | int
    operator: str
    threshold: float | int
    passed: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "metric": self.metric,
            "actual": self.actual,
            "operator": self.operator,
            "threshold": self.threshold,
            "passed": self.passed,
        }


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    dataset: EvaluationDataset
    queries: tuple[QueryEvaluation, ...]
    recall_at_k: Mapping[int, float]
    active_evidence_covered: int
    active_evidence_total: int
    inactive_misuse_by_status: Mapping[str, int]
    citation_revision_linked: int
    citation_revision_total: int
    duplicate_count: int
    reason_rank_complete: int
    reason_rank_total: int
    fallback_relevant_retained: int
    fallback_relevant_total: int
    fallback_contract_failures: int
    relevance_recall_by_category: Mapping[str, float]
    results_fingerprint: str
    checks: tuple[GateCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def active_evidence_coverage(self) -> float:
        return _rate(self.active_evidence_covered, self.active_evidence_total)

    @property
    def citation_source_revision_link_rate(self) -> float:
        return _rate(self.citation_revision_linked, self.citation_revision_total)

    @property
    def reason_rank_completeness(self) -> float:
        return _rate(self.reason_rank_complete, self.reason_rank_total)

    @property
    def degraded_fallback_retention(self) -> float:
        return _rate(self.fallback_relevant_retained, self.fallback_relevant_total)

    @property
    def inactive_misuse_count(self) -> int:
        return sum(self.inactive_misuse_by_status.values())

    @property
    def release_certified(self) -> bool:
        return self.dataset.threshold_profile == "release" and self.passed

    def as_dict(self) -> dict[str, object]:
        return {
            "artifactKind": "EVALUATION_REPORT",
            "schemaVersion": "1.0.0",
            "datasetId": self.dataset.dataset_id,
            "synthetic": self.dataset.synthetic,
            "evaluationMode": self.dataset.evaluation_mode,
            "thresholdProfile": self.dataset.threshold_profile,
            "scope": (
                "local deterministic synthetic gate; not production validation"
                if self.dataset.synthetic
                else "offline evaluation of an explicitly supplied result snapshot"
            ),
            "fingerprints": {
                "dataset": self.dataset.dataset_fingerprint,
                "results": self.results_fingerprint,
            },
            "review": {
                "status": self.dataset.review_status,
                "reviewedBy": self.dataset.reviewed_by,
                "reviewedAt": self.dataset.reviewed_at,
                "labelSetId": self.dataset.label_set_id,
            },
            "provenance": dict(self.dataset.provenance),
            "evaluator": {
                "version": EVALUATOR_VERSION,
                "python": platform.python_version(),
                "implementation": "scripts/evaluate_knowledge_retrieval.py",
            },
            "queryCount": len(self.queries),
            "metrics": {
                "recallAtK": {
                    str(key): value for key, value in self.recall_at_k.items()
                },
                "activeEvidenceCoverage": {
                    "covered": self.active_evidence_covered,
                    "total": self.active_evidence_total,
                    "rate": self.active_evidence_coverage,
                },
                "inactiveMisuseCount": {
                    **dict(self.inactive_misuse_by_status),
                    "total": self.inactive_misuse_count,
                },
                "citationSourceRevisionLinkRate": {
                    "linked": self.citation_revision_linked,
                    "total": self.citation_revision_total,
                    "rate": self.citation_source_revision_link_rate,
                },
                "duplicateCount": self.duplicate_count,
                "reasonRankCompleteness": {
                    "complete": self.reason_rank_complete,
                    "total": self.reason_rank_total,
                    "rate": self.reason_rank_completeness,
                },
                "degradedFallbackRetention": {
                    "retained": self.fallback_relevant_retained,
                    "total": self.fallback_relevant_total,
                    "rate": self.degraded_fallback_retention,
                },
                "fallbackContractFailures": self.fallback_contract_failures,
                "relevanceRecallByCategory": dict(
                    self.relevance_recall_by_category
                ),
            },
            "fixtureExpectation": {
                "recallAtK": {
                    str(key): value
                    for key, value in self.dataset.expected_recall_at_k.items()
                },
                "rationale": self.dataset.expected_recall_rationale,
            },
            "gate": {
                "passed": self.passed,
                "checks": [check.as_dict() for check in self.checks],
            },
            "certification": {
                "releaseCertified": self.release_certified,
                "status": (
                    "RELEASE_CERTIFIED"
                    if self.release_certified
                    else "NOT_RELEASE_CERTIFIED"
                ),
            },
            "queries": [query.as_dict() for query in self.queries],
        }


def load_dataset(
    manifest_path: Path, *, profile: str = "candidate"
) -> EvaluationDataset:
    manifest = _load_object(manifest_path)
    _expect(manifest, "fixtureKind", "MANIFEST", str(manifest_path))
    schema_version = _required_str(manifest, "schemaVersion", str(manifest_path))
    if schema_version not in {"1.0.0", "1.1.0"}:
        raise FixtureError(
            f"{manifest_path}.schemaVersion must equal '1.0.0' or '1.1.0'"
        )
    if _required_bool(manifest, "synthetic", str(manifest_path)) is not True:
        raise FixtureError("retrieval evaluation manifest must be synthetic")
    schema_name = _required_str(manifest, "fixtureSchema", str(manifest_path))
    schema_path = manifest_path.parent / schema_name
    if not schema_path.is_file():
        raise FixtureError(f"fixture schema does not exist: {schema_path}")

    k_values = tuple(
        sorted(_positive_int_list(manifest, "kValues", str(manifest_path)))
    )
    fallback_k = _required_positive_int(manifest, "fallbackK", str(manifest_path))
    if fallback_k < max(k_values):
        raise FixtureError("fallbackK must be at least the largest kValues entry")
    expected_metrics = _required_object(manifest, "expectedMetrics", str(manifest_path))
    expected_recall = _rate_by_k(expected_metrics, "recallAtK", "expectedMetrics")
    expected_rationale = _required_str(expected_metrics, "rationale", "expectedMetrics")
    gates = _gates_for_profile(manifest, profile, str(manifest_path))
    if set(expected_recall) != set(k_values) or set(gates.min_recall_at_k) != set(
        k_values
    ):
        raise FixtureError("expected and gate recall@k keys must exactly match kValues")

    fixture_names = _string_list(manifest, "fixtures", str(manifest_path), nonempty=True)
    documents: list[Document] = []
    queries: list[QueryCase] = []
    fixture_payloads: list[dict[str, object]] = []
    for fixture_name in fixture_names:
        fixture_path = manifest_path.parent / fixture_name
        fixture = _load_object(fixture_path)
        fixture_payloads.append(fixture)
        _expect(fixture, "fixtureKind", "DATASET", str(fixture_path))
        if _required_bool(fixture, "synthetic", str(fixture_path)) is not True:
            raise FixtureError(f"retrieval fixture must be synthetic: {fixture_path}")
        privacy = _required_object(fixture, "privacy", str(fixture_path))
        for privacy_key in (
            "containsProductionData",
            "containsPersonNames",
            "containsJiraUrls",
        ):
            if _required_bool(privacy, privacy_key, f"{fixture_path}:privacy"):
                raise FixtureError(f"{fixture_path}:privacy.{privacy_key} must be false")
        documents.extend(_parse_documents(fixture, fixture_path))
        queries.extend(_parse_queries(fixture, fixture_path))

    _validate_dataset(documents, queries, k_values)
    return EvaluationDataset(
        dataset_id=_required_str(manifest, "datasetId", str(manifest_path)),
        description=_required_str(manifest, "description", str(manifest_path)),
        k_values=k_values,
        fallback_k=fallback_k,
        expected_recall_at_k=expected_recall,
        expected_recall_rationale=expected_rationale,
        gates=gates,
        documents=tuple(documents),
        queries=tuple(queries),
        synthetic=True,
        evaluation_mode="synthetic",
        threshold_profile=profile,
        provenance={
            "runtime": {
                "serviceVersion": f"evaluator-{EVALUATOR_VERSION}",
                "revision": "local-fixture",
            },
            "model": {
                "embeddingModel": "deterministic-fixture-vector",
                "rerankerModel": "not-used",
            },
            "search": {
                "implementation": "DeterministicRetriever",
                "indexRevision": "fixture-inline",
                "configurationRevision": "weights-v1",
            },
        },
        dataset_fingerprint=_fingerprint(
            {"manifest": manifest, "fixtures": fixture_payloads}
        ),
    )


def load_snapshot(
    snapshot_path: Path,
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    profile: str = "candidate",
) -> tuple[EvaluationDataset, SnapshotRetriever]:
    """Load a previously captured operational result set without network access."""

    manifest = _load_object(manifest_path)
    _expect(manifest, "fixtureKind", "MANIFEST", str(manifest_path))
    gates = _gates_for_profile(manifest, profile, str(manifest_path))
    snapshot = _load_object(snapshot_path)
    _reject_unknown_keys(
        snapshot,
        {"fixtureKind", "schemaVersion", "synthetic", "dataset", "provenance", "results"},
        str(snapshot_path),
    )
    _expect(snapshot, "fixtureKind", "RESULT_SNAPSHOT", str(snapshot_path))
    _expect(snapshot, "schemaVersion", "1.0.0", str(snapshot_path))
    if _required_bool(snapshot, "synthetic", str(snapshot_path)):
        raise FixtureError("result snapshot mode requires synthetic=false")

    dataset_payload = _required_object(snapshot, "dataset", str(snapshot_path))
    _reject_unknown_keys(
        dataset_payload,
        {"datasetId", "description", "kValues", "fallbackK", "review", "queries"},
        "snapshot.dataset",
    )
    k_values = tuple(
        sorted(_positive_int_list(dataset_payload, "kValues", "snapshot.dataset"))
    )
    fallback_k = _required_positive_int(
        dataset_payload, "fallbackK", "snapshot.dataset"
    )
    if fallback_k < max(k_values):
        raise FixtureError("snapshot.dataset.fallbackK must be at least max(kValues)")
    if set(gates.min_recall_at_k) != set(k_values):
        raise FixtureError(
            "selected threshold profile recall@k keys must match snapshot kValues"
        )

    review = _optional_object(dataset_payload, "review", "snapshot.dataset")
    _reject_unknown_keys(
        review,
        {"status", "reviewedBy", "reviewedAt", "labelSetId"},
        "snapshot.dataset.review",
    )
    review_status = _optional_str(review, "status") or "UNREVIEWED"
    if review_status not in {"UNREVIEWED", "REVIEWED"}:
        raise FixtureError("snapshot.dataset.review.status is unsupported")
    queries = _parse_snapshot_queries(dataset_payload, snapshot_path)
    query_ids = [query.query_id for query in queries]
    if len(query_ids) != len(set(query_ids)):
        raise FixtureError("snapshot queryId values must be unique")

    raw_results = _required_list(
        snapshot, "results", str(snapshot_path), nonempty=True
    )
    responses: dict[tuple[str, bool], RetrievalResponse] = {}
    result_query_ids: list[str] = []
    documents_by_id: dict[str, Document] = {}
    for index, raw_result in enumerate(raw_results):
        context = f"{snapshot_path}:results[{index}]"
        result = _as_object(raw_result, context)
        _reject_unknown_keys(
            result, {"queryId", "baseline", "degraded"}, context
        )
        query_id = _required_str(result, "queryId", context)
        result_query_ids.append(query_id)
        baseline = _parse_snapshot_response(
            _required_object(result, "baseline", context),
            f"{context}.baseline",
            documents_by_id,
        )
        degraded = _parse_snapshot_response(
            _required_object(result, "degraded", context),
            f"{context}.degraded",
            documents_by_id,
        )
        responses[(query_id, True)] = baseline
        responses[(query_id, False)] = degraded
    if len(result_query_ids) != len(set(result_query_ids)):
        raise FixtureError("snapshot results must contain one entry per queryId")
    if set(result_query_ids) != set(query_ids):
        raise FixtureError("snapshot result queryIds must exactly match dataset queryIds")

    _validate_snapshot_relevance(queries)
    provenance = _parse_snapshot_provenance(snapshot, snapshot_path)
    dataset = EvaluationDataset(
        dataset_id=_required_str(dataset_payload, "datasetId", "snapshot.dataset"),
        description=_required_str(
            dataset_payload, "description", "snapshot.dataset"
        ),
        k_values=k_values,
        fallback_k=fallback_k,
        expected_recall_at_k={},
        expected_recall_rationale=(
            "Operational snapshots use independently reviewed relevance labels; "
            "no synthetic exact-recall assertion is applied."
        ),
        gates=gates,
        documents=tuple(documents_by_id.values()),
        queries=tuple(queries),
        synthetic=False,
        evaluation_mode="result-snapshot",
        threshold_profile=profile,
        review_status=review_status,
        reviewed_by=_optional_str(review, "reviewedBy"),
        reviewed_at=_optional_str(review, "reviewedAt"),
        label_set_id=_optional_str(review, "labelSetId"),
        provenance=provenance,
        dataset_fingerprint=_fingerprint(dataset_payload),
        captured_results_fingerprint=_fingerprint(raw_results),
    )
    return dataset, SnapshotRetriever(responses)


def _parse_snapshot_queries(
    payload: Mapping[str, object], snapshot_path: Path
) -> list[QueryCase]:
    raw_queries = _required_list(
        payload, "queries", f"{snapshot_path}:dataset", nonempty=True
    )
    queries: list[QueryCase] = []
    for index, raw_query in enumerate(raw_queries):
        context = f"{snapshot_path}:dataset.queries[{index}]"
        item = _as_object(raw_query, context)
        _reject_unknown_keys(
            item,
            {"queryId", "text", "statuses", "relevanceCategory", "relevantNodes"},
            context,
        )
        category = _required_str(item, "relevanceCategory", context)
        if category not in RELEVANCE_CATEGORIES:
            raise FixtureError(f"{context}.relevanceCategory is unsupported: {category}")
        raw_relevant = _required_list(item, "relevantNodes", context, nonempty=True)
        relevant_ids: list[str] = []
        relevant_types: dict[str, str] = {}
        relevant_outcomes: dict[str, str] = {}
        for relevant_index, raw_node in enumerate(raw_relevant):
            relevant_context = f"{context}.relevantNodes[{relevant_index}]"
            node = _as_object(raw_node, relevant_context)
            _reject_unknown_keys(
                node,
                {"nodeId", "nodeType", "executionOutcome"},
                relevant_context,
            )
            node_id = _required_str(node, "nodeId", relevant_context)
            if node_id in relevant_types:
                raise FixtureError(f"{context}.relevantNodes nodeId values must be unique")
            relevant_ids.append(node_id)
            relevant_types[node_id] = _required_str(
                node, "nodeType", relevant_context
            )
            outcome = _optional_str(node, "executionOutcome")
            if outcome is not None:
                relevant_outcomes[node_id] = outcome
        statuses = tuple(_string_list(item, "statuses", context, nonempty=True))
        invalid_statuses = set(statuses).difference(LIFECYCLE_STATUSES)
        if invalid_statuses:
            raise FixtureError(
                f"{context}.statuses has unsupported values: {sorted(invalid_statuses)}"
            )
        queries.append(
            QueryCase(
                query_id=_required_str(item, "queryId", context),
                text=_required_str(item, "text", context),
                statuses=statuses,
                embedding=(1.0, 0.0),
                relevant_node_ids=tuple(relevant_ids),
                light_rag_candidate_node_ids=(),
                relevance_category=category,
                relevant_node_types=relevant_types,
                relevant_execution_outcomes=relevant_outcomes,
            )
        )
    return queries


def _parse_snapshot_response(
    payload: Mapping[str, object],
    context: str,
    documents_by_id: dict[str, Document],
) -> RetrievalResponse:
    degraded = _required_bool(payload, "degraded", context)
    _reject_unknown_keys(
        payload, {"degraded", "degradationReasons", "items"}, context
    )
    reasons = tuple(_string_list(payload, "degradationReasons", context))
    raw_items = _required_list(payload, "items", context)
    items: list[RetrievalItem] = []
    for index, raw_item in enumerate(raw_items):
        item_context = f"{context}.items[{index}]"
        item = _as_object(raw_item, item_context)
        _reject_unknown_keys(
            item,
            {
                "nodeId",
                "nodeType",
                "executionOutcome",
                "status",
                "evidenceIds",
                "source",
                "citation",
                "rank",
                "score",
                "reason",
                "degraded",
            },
            item_context,
        )
        citation_object = _required_object(item, "citation", item_context)
        _reject_unknown_keys(
            citation_object,
            {"source", "label", "uri", "excerpt"},
            f"{item_context}.citation",
        )
        source = _parse_source(_required_object(item, "source", item_context), item_context)
        citation = Citation(
            source=_parse_source(
                _required_object(citation_object, "source", item_context),
                item_context,
            ),
            label=_required_str(citation_object, "label", item_context),
            excerpt=_required_str(citation_object, "excerpt", item_context),
            uri=_nullable_str(citation_object, "uri", item_context),
        )
        node_type = _required_str(item, "nodeType", item_context)
        if node_type not in NODE_TYPES:
            raise FixtureError(f"{item_context}.nodeType is unsupported: {node_type}")
        status = _required_str(item, "status", item_context)
        if status not in LIFECYCLE_STATUSES:
            raise FixtureError(f"{item_context}.status is unsupported: {status}")
        execution_outcome = _optional_str(item, "executionOutcome")
        if (
            execution_outcome is not None
            and execution_outcome not in EXECUTION_OUTCOMES
        ):
            raise FixtureError(
                f"{item_context}.executionOutcome is unsupported: {execution_outcome}"
            )
        document = Document(
            node_id=_required_str(item, "nodeId", item_context),
            node_type=node_type,
            label=citation.label,
            content=citation.excerpt,
            status=status,
            evidence_ids=tuple(_string_list(item, "evidenceIds", item_context)),
            source=source,
            citation=citation,
            embedding=(1.0, 0.0),
            execution_outcome=execution_outcome,
        )
        previous = documents_by_id.get(document.node_id)
        if previous is not None and _document_identity(previous) != _document_identity(
            document
        ):
            raise FixtureError(
                f"snapshot node metadata changes between result sets: {document.node_id}"
            )
        documents_by_id.setdefault(document.node_id, document)
        reason = _required_object(item, "reason", item_context)
        _reject_unknown_keys(
            reason, {"summary", "signals"}, f"{item_context}.reason"
        )
        items.append(
            RetrievalItem(
                document=document,
                rank=_required_positive_int(item, "rank", item_context),
                score=_required_number(item, "score", item_context),
                reason_summary=_string_value(reason, "summary", item_context),
                reason_signals=tuple(_string_list(reason, "signals", item_context)),
                degraded=_required_bool(item, "degraded", item_context),
            )
        )
    return RetrievalResponse(items=tuple(items), degraded=degraded, degradation_reasons=reasons)


def _validate_snapshot_relevance(queries: Sequence[QueryCase]) -> None:
    for query in queries:
        category = query.relevance_category
        if category is None:
            raise FixtureError(f"snapshot query lacks relevance category: {query.query_id}")
        expected_type = RELEVANCE_NODE_TYPES[category]
        for node_id in query.relevant_node_ids:
            if query.relevant_node_types.get(node_id) != expected_type:
                raise FixtureError(
                    f"query {query.query_id} category {category} requires relevant node type {expected_type}: {node_id}"
                )
            if category == "FAILED_EXECUTION" and query.relevant_execution_outcomes.get(
                node_id
            ) != "FAILED":
                raise FixtureError(
                    f"query {query.query_id} failed-execution labels must declare executionOutcome=FAILED: {node_id}"
                )


def _document_identity(document: Document) -> tuple[object, ...]:
    return (
        document.node_type,
        document.status,
        document.execution_outcome,
        document.evidence_ids,
        document.source,
        document.citation,
    )


def _parse_snapshot_provenance(
    snapshot: Mapping[str, object], snapshot_path: Path
) -> dict[str, object]:
    raw = _optional_object(snapshot, "provenance", str(snapshot_path))
    _reject_unknown_keys(
        raw,
        {"capturedAt", "environment", "runtime", "model", "search"},
        f"{snapshot_path}.provenance",
    )
    result: dict[str, object] = {}
    for key in ("capturedAt", "environment"):
        value = _optional_str(raw, key)
        if value is not None:
            result[key] = value
    section_keys = {
        "runtime": ("serviceVersion", "revision"),
        "model": ("embeddingModel", "rerankerModel"),
        "search": (
            "implementation",
            "indexRevision",
            "configurationRevision",
        ),
    }
    for section, allowed_keys in section_keys.items():
        section_payload = _optional_object(raw, section, "snapshot.provenance")
        _reject_unknown_keys(
            section_payload,
            set(allowed_keys),
            f"snapshot.provenance.{section}",
        )
        normalized: dict[str, object] = {}
        for key in allowed_keys:
            value = _optional_str(section_payload, key)
            if value is not None:
                normalized[key] = value
        if normalized or section in raw:
            result[section] = normalized
    return result


def evaluate_dataset(
    dataset: EvaluationDataset, retriever: Retriever | None = None
) -> EvaluationReport:
    selected_retriever = retriever or DeterministicRetriever()
    limit = max(dataset.fallback_k, max(dataset.k_values))
    query_evaluations: list[QueryEvaluation] = []
    all_responses: list[tuple[QueryCase, RetrievalResponse]] = []
    fallback_relevant_retained = 0
    fallback_relevant_total = 0
    fallback_contract_failures = 0

    for query in dataset.queries:
        baseline = selected_retriever.search(
            query,
            dataset.documents,
            limit=limit,
            light_rag_available=True,
        )
        degraded = selected_retriever.search(
            query,
            dataset.documents,
            limit=limit,
            light_rag_available=False,
        )
        relevant = set(query.relevant_node_ids)
        recall_at_k = {
            k: len(_matched_relevant_ids(query, baseline.items[:k])) / len(relevant)
            for k in dataset.k_values
        }
        retained = _matched_relevant_ids(
            query, degraded.items[: dataset.fallback_k]
        )
        fallback_relevant_retained += len(retained)
        fallback_relevant_total += len(relevant)
        baseline_relevant = _matched_relevant_ids(
            query, baseline.items[: dataset.fallback_k]
        )
        fallback_contract_passed = (
            not baseline.degraded
            and not baseline.degradation_reasons
            and all(not item.degraded for item in baseline.items)
            and degraded.degraded
            and "LIGHTRAG_UNAVAILABLE" in degraded.degradation_reasons
            and all(item.degraded for item in degraded.items)
            and all("LIGHTRAG" not in item.reason_signals for item in degraded.items)
            and baseline_relevant.issubset(retained)
        )
        if not fallback_contract_passed:
            fallback_contract_failures += 1
        query_evaluations.append(
            QueryEvaluation(
                query_id=query.query_id,
                relevant_node_ids=query.relevant_node_ids,
                baseline=baseline,
                degraded=degraded,
                recall_at_k=recall_at_k,
                fallback_contract_passed=fallback_contract_passed,
            )
        )
        all_responses.extend(((query, baseline), (query, degraded)))

    recall_at_k = {
        k: sum(item.recall_at_k[k] for item in query_evaluations)
        / len(query_evaluations)
        for k in dataset.k_values
    }
    active_evidence_covered = 0
    active_evidence_total = 0
    inactive_misuse_by_status = {status: 0 for status in INACTIVE_STATUSES}
    citation_revision_linked = 0
    citation_revision_total = 0
    duplicate_count = 0
    reason_rank_complete = 0
    reason_rank_total = 0

    for query, response in all_responses:
        ids = _node_ids(response.items)
        duplicate_count += len(ids) - len(set(ids))
        allowed = set(query.statuses)
        for expected_rank, item in enumerate(response.items, start=1):
            document = item.document
            if document.status == "ACTIVE":
                active_evidence_total += 1
                if document.evidence_ids:
                    active_evidence_covered += 1
            if document.status in INACTIVE_STATUSES and document.status not in allowed:
                inactive_misuse_by_status[document.status] += 1
            citation_revision_total += 1
            if (
                bool(document.source.source_revision_id.strip())
                and document.source == document.citation.source
            ):
                citation_revision_linked += 1
            reason_rank_total += 1
            if (
                item.rank == expected_rank
                and bool(item.reason_summary.strip())
                and bool(item.reason_signals)
            ):
                reason_rank_complete += 1

    active_evidence_coverage = _rate(
        active_evidence_covered, active_evidence_total
    )
    inactive_misuse_count = sum(inactive_misuse_by_status.values())
    citation_link_rate = _rate(citation_revision_linked, citation_revision_total)
    reason_rank_completeness = _rate(reason_rank_complete, reason_rank_total)
    fallback_retention = _rate(
        fallback_relevant_retained, fallback_relevant_total
    )
    relevance_recall_by_category: dict[str, float] = {}
    for category in RELEVANCE_CATEGORIES:
        category_queries = [
            (query, evaluation)
            for query, evaluation in zip(dataset.queries, query_evaluations)
            if query.relevance_category == category
        ]
        relevant_total = sum(len(query.relevant_node_ids) for query, _ in category_queries)
        relevant_found = sum(
            len(
                _matched_relevant_ids(
                    query, evaluation.baseline.items[: dataset.fallback_k]
                )
            )
            for query, evaluation in category_queries
        )
        relevance_recall_by_category[category] = _rate(
            relevant_found, relevant_total
        )
    checks: list[GateCheck] = []
    for k in dataset.k_values:
        checks.append(
            _minimum_check(
                f"recall@{k}", recall_at_k[k], dataset.gates.min_recall_at_k[k]
            )
        )
        if k in dataset.expected_recall_at_k:
            checks.append(
                _equality_check(
                    f"fixtureExpectedRecall@{k}",
                    recall_at_k[k],
                    dataset.expected_recall_at_k[k],
                )
            )
    checks.extend(
        (
            _minimum_check(
                "activeEvidenceCoverage",
                active_evidence_coverage,
                dataset.gates.min_active_evidence_coverage,
            ),
            _maximum_check(
                "inactiveMisuseCount",
                inactive_misuse_count,
                dataset.gates.max_inactive_misuse_count,
            ),
            _minimum_check(
                "citationSourceRevisionLinkRate",
                citation_link_rate,
                dataset.gates.min_citation_source_revision_link_rate,
            ),
            _maximum_check(
                "duplicateCount",
                duplicate_count,
                dataset.gates.max_duplicate_count,
            ),
            _minimum_check(
                "reasonRankCompleteness",
                reason_rank_completeness,
                dataset.gates.min_reason_rank_completeness,
            ),
            _minimum_check(
                "degradedFallbackRetention",
                fallback_retention,
                dataset.gates.min_degraded_fallback_retention,
            ),
            _maximum_check(
                "fallbackContractFailures",
                fallback_contract_failures,
                dataset.gates.max_fallback_contract_failures,
            ),
        )
    )
    for category in dataset.gates.required_relevance_categories:
        checks.append(
            _minimum_check(
                f"relevanceRecall:{category}",
                relevance_recall_by_category.get(category, 0.0),
                dataset.gates.min_required_category_recall,
            )
        )
    if dataset.threshold_profile == "release":
        checks.extend(
            (
                _truth_check("releaseDatasetNonSynthetic", not dataset.synthetic),
                _truth_check(
                    "releaseDatasetReviewed",
                    dataset.review_status == "REVIEWED",
                ),
                _truth_check(
                    "releaseReviewMetadataComplete",
                    bool(dataset.reviewed_by)
                    and bool(dataset.reviewed_at)
                    and bool(dataset.label_set_id),
                ),
                _truth_check(
                    "releaseRuntimeProvenance",
                    _provenance_section_complete(
                        dataset.provenance,
                        "runtime",
                        ("serviceVersion", "revision"),
                    ),
                ),
                _truth_check(
                    "releaseModelProvenance",
                    _provenance_section_complete(
                        dataset.provenance,
                        "model",
                        ("embeddingModel", "rerankerModel"),
                    ),
                ),
                _truth_check(
                    "releaseSearchProvenance",
                    _provenance_section_complete(
                        dataset.provenance,
                        "search",
                        (
                            "implementation",
                            "indexRevision",
                            "configurationRevision",
                        ),
                    ),
                ),
            )
        )
    normalized_results = {
        "queries": [evaluation.as_dict() for evaluation in query_evaluations]
    }
    results_fingerprint = (
        dataset.captured_results_fingerprint or _fingerprint(normalized_results)
    )
    return EvaluationReport(
        dataset=dataset,
        queries=tuple(query_evaluations),
        recall_at_k=recall_at_k,
        active_evidence_covered=active_evidence_covered,
        active_evidence_total=active_evidence_total,
        inactive_misuse_by_status=inactive_misuse_by_status,
        citation_revision_linked=citation_revision_linked,
        citation_revision_total=citation_revision_total,
        duplicate_count=duplicate_count,
        reason_rank_complete=reason_rank_complete,
        reason_rank_total=reason_rank_total,
        fallback_relevant_retained=fallback_relevant_retained,
        fallback_relevant_total=fallback_relevant_total,
        fallback_contract_failures=fallback_contract_failures,
        relevance_recall_by_category=relevance_recall_by_category,
        results_fingerprint=results_fingerprint,
        checks=tuple(checks),
    )


def evaluate_manifest(
    manifest_path: Path = DEFAULT_MANIFEST,
    retriever: Retriever | None = None,
    *,
    profile: str = "candidate",
) -> EvaluationReport:
    return evaluate_dataset(load_dataset(manifest_path, profile=profile), retriever)


def evaluate_snapshot(
    snapshot_path: Path,
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    profile: str = "candidate",
) -> EvaluationReport:
    dataset, retriever = load_snapshot(
        snapshot_path, manifest_path=manifest_path, profile=profile
    )
    return evaluate_dataset(dataset, retriever)


def render_text(report: EvaluationReport) -> str:
    recall = ", ".join(
        f"recall@{k}={report.recall_at_k[k]:.3f}" for k in report.dataset.k_values
    )
    misuse = ", ".join(
        f"{status}={count}" for status, count in report.inactive_misuse_by_status.items()
    )
    failed = [check.metric for check in report.checks if not check.passed]
    gate_label = (
        "Knowledge retrieval synthetic gate"
        if report.dataset.synthetic
        else "Knowledge retrieval result-snapshot gate"
    )
    scope = (
        "Scope: deterministic local fixture only; this does not validate production Slack, embeddings, or LightRAG."
        if report.dataset.synthetic
        else "Scope: offline replay of explicit captured results; the evaluator made no network calls."
    )
    return "\n".join(
        (
            f"{gate_label}: {'PASS' if report.passed else 'FAIL'}",
            (
                f"Dataset: {report.dataset.dataset_id} "
                f"(synthetic={str(report.dataset.synthetic).lower()}, "
                f"queries={len(report.queries)}, profile={report.dataset.threshold_profile})"
            ),
            scope,
            f"Dataset fingerprint: {report.dataset.dataset_fingerprint}",
            f"Results fingerprint: {report.results_fingerprint}",
            f"Recall: {recall}",
            (
                "ACTIVE evidence coverage: "
                f"{report.active_evidence_covered}/{report.active_evidence_total} "
                f"({report.active_evidence_coverage:.1%})"
            ),
            f"Inactive misuse: {report.inactive_misuse_count} ({misuse})",
            (
                "Citation source revision links: "
                f"{report.citation_revision_linked}/{report.citation_revision_total} "
                f"({report.citation_source_revision_link_rate:.1%})"
            ),
            f"Duplicate results: {report.duplicate_count}",
            (
                "Reason/rank completeness: "
                f"{report.reason_rank_complete}/{report.reason_rank_total} "
                f"({report.reason_rank_completeness:.1%})"
            ),
            (
                "LightRAG degraded fallback retention: "
                f"{report.fallback_relevant_retained}/{report.fallback_relevant_total} "
                f"({report.degraded_fallback_retention:.1%}); "
                f"contract failures={report.fallback_contract_failures}"
            ),
            "Release certified: " + ("yes" if report.release_certified else "no"),
            "Failed gates: " + (", ".join(failed) if failed else "none"),
        )
    )


def render_markdown(report: EvaluationReport) -> str:
    status = "PASS" if report.passed else "FAIL"
    lines = [
        "# Knowledge retrieval evaluation report",
        "",
        f"- Gate: **{status}**",
        f"- Dataset: `{report.dataset.dataset_id}`",
        f"- Mode: `{report.dataset.evaluation_mode}`",
        f"- Synthetic: `{str(report.dataset.synthetic).lower()}`",
        f"- Threshold profile: `{report.dataset.threshold_profile}`",
        f"- Release certified: `{'yes' if report.release_certified else 'no'}`",
        f"- Dataset fingerprint: `{report.dataset.dataset_fingerprint}`",
        f"- Results fingerprint: `{report.results_fingerprint}`",
        "",
        "## Dataset review",
        "",
        f"- Status: `{report.dataset.review_status}`",
        f"- Reviewer: `{report.dataset.reviewed_by or 'missing'}`",
        f"- Reviewed at: `{report.dataset.reviewed_at or 'missing'}`",
        f"- Label set: `{report.dataset.label_set_id or 'missing'}`",
        "",
        "## Gate checks",
        "",
        "| Metric | Actual | Operator | Threshold | Result |",
        "| --- | ---: | :---: | ---: | :---: |",
    ]
    lines.extend(
        f"| `{check.metric}` | {check.actual} | {check.operator} | {check.threshold} | {'PASS' if check.passed else 'FAIL'} |"
        for check in report.checks
    )
    lines.extend(
        [
            "",
            "## Provenance",
            "",
            "```json",
            json.dumps(
                report.dataset.provenance,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "```",
            "",
            "## Query results",
            "",
            "| Query | Recall | Baseline items | Degraded items | Fallback contract |",
            "| --- | --- | ---: | ---: | :---: |",
        ]
    )
    for query in report.queries:
        recall = ", ".join(
            f"@{k}={value:.3f}" for k, value in query.recall_at_k.items()
        )
        lines.append(
            f"| `{query.query_id}` | {recall} | {len(query.baseline.items)} | "
            f"{len(query.degraded.items)} | "
            f"{'PASS' if query.fallback_contract_passed else 'FAIL'} |"
        )
    lines.extend(
        [
            "",
            "> This report evaluates only the supplied snapshot. It is not proof that "
            "a live service, model, or index was contacted during this run.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--snapshot",
        type=Path,
        help="evaluate an explicit non-synthetic result snapshot (never fetched)",
    )
    parser.add_argument(
        "--profile",
        choices=("candidate", "release"),
        default="candidate",
        help="threshold and certification profile",
    )
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--json-output", type=Path, help="write machine JSON here")
    parser.add_argument("--markdown-output", type=Path, help="write Markdown here")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        manifest_path = cast(Path, args.manifest)
        snapshot_path = cast(Path | None, args.snapshot)
        profile = cast(str, args.profile)
        json_output = cast(Path | None, args.json_output)
        markdown_output = cast(Path | None, args.markdown_output)
        if snapshot_path is not None and (
            json_output is None or markdown_output is None
        ):
            raise FixtureError(
                "snapshot mode requires both --json-output and --markdown-output"
            )
        if (
            json_output is not None
            and markdown_output is not None
            and json_output.resolve() == markdown_output.resolve()
        ):
            raise FixtureError("JSON and Markdown output paths must be different")
        input_paths = {manifest_path.resolve()}
        if snapshot_path is not None:
            input_paths.add(snapshot_path.resolve())
        for output_path in (json_output, markdown_output):
            if output_path is not None and output_path.resolve() in input_paths:
                raise FixtureError("report output paths must not overwrite input files")
        report = (
            evaluate_snapshot(
                snapshot_path,
                manifest_path=manifest_path,
                profile=profile,
            )
            if snapshot_path is not None
            else evaluate_manifest(manifest_path, profile=profile)
        )
        machine_json = json.dumps(
            report.as_dict(), ensure_ascii=False, indent=2, sort_keys=True
        )
        if json_output is not None:
            _write_output(json_output, machine_json + "\n")
        if markdown_output is not None:
            _write_output(markdown_output, render_markdown(report))
    except (FixtureError, OSError, json.JSONDecodeError) as exc:
        print(f"knowledge retrieval evaluation error: {exc}", file=sys.stderr)
        return 2
    if cast(bool, args.json):
        print(machine_json)
    else:
        print(render_text(report))
    return 0 if report.passed else 1


def _parse_documents(payload: Mapping[str, object], fixture_path: Path) -> list[Document]:
    raw_documents = _required_list(payload, "documents", str(fixture_path), nonempty=True)
    documents: list[Document] = []
    for index, raw in enumerate(raw_documents):
        context = f"{fixture_path}:documents[{index}]"
        item = _as_object(raw, context)
        citation_object = _required_object(item, "citation", context)
        if citation_object.get("uri") is not None:
            raise FixtureError(f"{context}.citation.uri must be null in synthetic fixtures")
        documents.append(
            Document(
                node_id=_required_str(item, "nodeId", context),
                node_type=_required_str(item, "nodeType", context),
                label=_required_str(item, "label", context),
                content=_required_str(item, "content", context),
                status=_required_str(item, "status", context),
                evidence_ids=tuple(
                    _string_list(item, "evidenceIds", context, nonempty=True)
                ),
                source=_parse_source(_required_object(item, "source", context), context),
                citation=Citation(
                    source=_parse_source(
                        _required_object(citation_object, "source", context), context
                    ),
                    label=_required_str(citation_object, "label", context),
                    excerpt=_required_str(citation_object, "excerpt", context),
                    uri=None,
                ),
                embedding=tuple(_number_list(item, "embedding", context)),
            )
        )
    return documents


def _parse_queries(payload: Mapping[str, object], fixture_path: Path) -> list[QueryCase]:
    raw_queries = _required_list(payload, "queries", str(fixture_path), nonempty=True)
    queries: list[QueryCase] = []
    for index, raw in enumerate(raw_queries):
        context = f"{fixture_path}:queries[{index}]"
        item = _as_object(raw, context)
        queries.append(
            QueryCase(
                query_id=_required_str(item, "queryId", context),
                text=_required_str(item, "text", context),
                statuses=tuple(_string_list(item, "statuses", context, nonempty=True)),
                embedding=tuple(_number_list(item, "embedding", context)),
                relevant_node_ids=tuple(
                    _string_list(item, "relevantNodeIds", context, nonempty=True)
                ),
                light_rag_candidate_node_ids=tuple(
                    _string_list(item, "lightRagCandidateNodeIds", context)
                ),
            )
        )
    return queries


def _validate_dataset(
    documents: Sequence[Document], queries: Sequence[QueryCase], k_values: Sequence[int]
) -> None:
    if not documents or not queries:
        raise FixtureError("retrieval dataset requires documents and queries")
    document_ids = [document.node_id for document in documents]
    query_ids = [query.query_id for query in queries]
    if len(document_ids) != len(set(document_ids)):
        raise FixtureError("document nodeId values must be unique")
    if len(query_ids) != len(set(query_ids)):
        raise FixtureError("queryId values must be unique")
    documents_by_id = {document.node_id: document for document in documents}
    dimensions = {len(document.embedding) for document in documents}
    dimensions.update(len(query.embedding) for query in queries)
    if len(dimensions) != 1 or next(iter(dimensions)) < 2:
        raise FixtureError("all deterministic embeddings must use one dimension >= 2")
    if any(_magnitude(document.embedding) == 0 for document in documents):
        raise FixtureError("document embeddings must be non-zero")
    for query in queries:
        if _magnitude(query.embedding) == 0:
            raise FixtureError(f"query embedding must be non-zero: {query.query_id}")
        referenced = set(query.relevant_node_ids) | set(
            query.light_rag_candidate_node_ids
        )
        missing = referenced.difference(documents_by_id)
        if missing:
            raise FixtureError(
                f"query {query.query_id} references unknown nodes: {sorted(missing)}"
            )
        for node_id in query.relevant_node_ids:
            if documents_by_id[node_id].status not in query.statuses:
                raise FixtureError(
                    f"query {query.query_id} marks an out-of-scope node relevant: {node_id}"
                )
    if max(k_values) < 1:
        raise FixtureError("kValues must be positive")


def _parse_source(payload: Mapping[str, object], context: str) -> SourceRef:
    _reject_unknown_keys(
        payload,
        {"sourceType", "sourceEntityId", "sourceRevisionId"},
        f"{context}.source",
    )
    return SourceRef(
        source_type=_required_str(payload, "sourceType", context),
        source_entity_id=_required_str(payload, "sourceEntityId", context),
        source_revision_id=_required_str(payload, "sourceRevisionId", context),
    )


def _gates_for_profile(
    manifest: Mapping[str, object], profile: str, context: str
) -> Gates:
    if profile not in {"candidate", "release"}:
        raise FixtureError(f"unsupported threshold profile: {profile}")
    profiles = _optional_object(manifest, "thresholdProfiles", context)
    if profiles:
        gates_object = _required_object(profiles, profile, f"{context}.thresholdProfiles")
        gates_context = f"thresholdProfiles.{profile}"
    elif profile == "candidate":
        gates_object = _required_object(manifest, "defaultGates", context)
        gates_context = "defaultGates"
    else:
        raise FixtureError("release profile is not defined by this manifest")

    categories = (
        tuple(
            _string_list(
                gates_object,
                "requiredRelevanceCategories",
                gates_context,
            )
        )
        if "requiredRelevanceCategories" in gates_object
        else ()
    )
    unsupported = set(categories).difference(RELEVANCE_CATEGORIES)
    if unsupported:
        raise FixtureError(
            f"{gates_context}.requiredRelevanceCategories has unsupported values: {sorted(unsupported)}"
        )
    min_category_recall = (
        _required_rate(
            gates_object,
            "minRequiredCategoryRecall",
            gates_context,
        )
        if "minRequiredCategoryRecall" in gates_object
        else 0.0
    )
    if profile == "release" and (
        set(categories) != set(RELEVANCE_CATEGORIES) or min_category_recall <= 0
    ):
        raise FixtureError(
            "release profile must require all operational relevance categories with positive recall"
        )
    return Gates(
        min_recall_at_k=_rate_by_k(gates_object, "minRecallAtK", gates_context),
        min_active_evidence_coverage=_required_rate(
            gates_object, "minActiveEvidenceCoverage", gates_context
        ),
        max_inactive_misuse_count=_required_nonnegative_int(
            gates_object, "maxInactiveMisuseCount", gates_context
        ),
        min_citation_source_revision_link_rate=_required_rate(
            gates_object, "minCitationSourceRevisionLinkRate", gates_context
        ),
        max_duplicate_count=_required_nonnegative_int(
            gates_object, "maxDuplicateCount", gates_context
        ),
        min_reason_rank_completeness=_required_rate(
            gates_object, "minReasonRankCompleteness", gates_context
        ),
        min_degraded_fallback_retention=_required_rate(
            gates_object, "minDegradedFallbackRetention", gates_context
        ),
        max_fallback_contract_failures=_required_nonnegative_int(
            gates_object, "maxFallbackContractFailures", gates_context
        ),
        required_relevance_categories=categories,
        min_required_category_recall=min_category_recall,
    )


def _load_object(path: Path) -> dict[str, object]:
    value = cast(object, json.loads(path.read_text(encoding="utf-8")))
    return _as_object(value, str(path))


def _as_object(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise FixtureError(f"{context} must be a JSON object")
    return cast(dict[str, object], value)


def _reject_unknown_keys(
    payload: Mapping[str, object], allowed: set[str], context: str
) -> None:
    unknown = set(payload).difference(allowed)
    if unknown:
        raise FixtureError(f"{context} has unsupported fields: {sorted(unknown)}")


def _required_object(
    payload: Mapping[str, object], key: str, context: str
) -> dict[str, object]:
    if key not in payload:
        raise FixtureError(f"{context}.{key} is required")
    return _as_object(payload[key], f"{context}.{key}")


def _optional_object(
    payload: Mapping[str, object], key: str, context: str
) -> dict[str, object]:
    if key not in payload or payload[key] is None:
        return {}
    return _as_object(payload[key], f"{context}.{key}")


def _required_list(
    payload: Mapping[str, object], key: str, context: str, *, nonempty: bool = False
) -> list[object]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise FixtureError(f"{context}.{key} must be an array")
    if nonempty and not value:
        raise FixtureError(f"{context}.{key} must not be empty")
    return cast(list[object], value)


def _required_str(payload: Mapping[str, object], key: str, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise FixtureError(f"{context}.{key} must be a non-blank string")
    return value


def _string_value(payload: Mapping[str, object], key: str, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise FixtureError(f"{context}.{key} must be a string")
    return value


def _optional_str(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise FixtureError(f"{key} must be a non-blank string or null")
    return value


def _nullable_str(
    payload: Mapping[str, object], key: str, context: str
) -> str | None:
    if key not in payload:
        raise FixtureError(f"{context}.{key} is required")
    value = payload[key]
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise FixtureError(f"{context}.{key} must be a non-blank string or null")
    return value


def _required_bool(payload: Mapping[str, object], key: str, context: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise FixtureError(f"{context}.{key} must be a boolean")
    return value


def _required_rate(payload: Mapping[str, object], key: str, context: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise FixtureError(f"{context}.{key} must be a number")
    result = float(value)
    if not 0 <= result <= 1:
        raise FixtureError(f"{context}.{key} must be between 0 and 1")
    return result


def _required_number(payload: Mapping[str, object], key: str, context: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise FixtureError(f"{context}.{key} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise FixtureError(f"{context}.{key} must be finite")
    return result


def _required_positive_int(payload: Mapping[str, object], key: str, context: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise FixtureError(f"{context}.{key} must be a positive integer")
    return value


def _required_nonnegative_int(
    payload: Mapping[str, object], key: str, context: str
) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FixtureError(f"{context}.{key} must be a non-negative integer")
    return value


def _string_list(
    payload: Mapping[str, object], key: str, context: str, *, nonempty: bool = False
) -> list[str]:
    values = _required_list(payload, key, context, nonempty=nonempty)
    if not all(isinstance(value, str) and value.strip() for value in values):
        raise FixtureError(f"{context}.{key} must contain non-blank strings")
    result = cast(list[str], values)
    if len(result) != len(set(result)):
        raise FixtureError(f"{context}.{key} must contain unique values")
    return result


def _number_list(payload: Mapping[str, object], key: str, context: str) -> list[float]:
    values = _required_list(payload, key, context, nonempty=True)
    if any(isinstance(value, bool) or not isinstance(value, int | float) for value in values):
        raise FixtureError(f"{context}.{key} must contain only numbers")
    return [float(cast(int | float, value)) for value in values]


def _positive_int_list(payload: Mapping[str, object], key: str, context: str) -> list[int]:
    values = _required_list(payload, key, context, nonempty=True)
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in values):
        raise FixtureError(f"{context}.{key} must contain positive integers")
    result = cast(list[int], values)
    if len(result) != len(set(result)):
        raise FixtureError(f"{context}.{key} must contain unique values")
    return result


def _rate_by_k(
    payload: Mapping[str, object], key: str, context: str
) -> dict[int, float]:
    raw = _required_object(payload, key, context)
    if not raw:
        raise FixtureError(f"{context}.{key} must not be empty")
    result: dict[int, float] = {}
    for raw_k, raw_rate in raw.items():
        try:
            k = int(raw_k)
        except ValueError as exc:
            raise FixtureError(f"{context}.{key} has invalid k: {raw_k}") from exc
        if k < 1 or str(k) != raw_k:
            raise FixtureError(f"{context}.{key} has invalid k: {raw_k}")
        if isinstance(raw_rate, bool) or not isinstance(raw_rate, int | float):
            raise FixtureError(f"{context}.{key}.{raw_k} must be a number")
        rate = float(raw_rate)
        if not 0 <= rate <= 1:
            raise FixtureError(f"{context}.{key}.{raw_k} must be between 0 and 1")
        result[k] = rate
    return result


def _expect(
    payload: Mapping[str, object], key: str, expected: object, context: str
) -> None:
    if payload.get(key) != expected:
        raise FixtureError(f"{context}.{key} must equal {expected!r}")


def _terms(value: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]+", value.casefold()))


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left.intersection(right)) / len(left.union(right))


def _magnitude(vector: Sequence[float]) -> float:
    return math.sqrt(sum(value * value for value in vector))


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise FixtureError("embedding dimensions do not match")
    denominator = _magnitude(left) * _magnitude(right)
    if denominator == 0:
        return 0.0
    return max(0.0, min(1.0, sum(a * b for a, b in zip(left, right)) / denominator))


def _node_ids(items: Sequence[RetrievalItem]) -> list[str]:
    return [item.document.node_id for item in items]


def _matched_relevant_ids(
    query: QueryCase, items: Sequence[RetrievalItem]
) -> set[str]:
    relevant = set(query.relevant_node_ids)
    matched: set[str] = set()
    for item in items:
        document = item.document
        if document.node_id not in relevant:
            continue
        expected_type = query.relevant_node_types.get(document.node_id)
        if expected_type is not None and document.node_type != expected_type:
            continue
        expected_outcome = query.relevant_execution_outcomes.get(document.node_id)
        if (
            expected_outcome is not None
            and document.execution_outcome != expected_outcome
        ):
            continue
        matched.add(document.node_id)
    return matched


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _minimum_check(metric: str, actual: float | int, threshold: float | int) -> GateCheck:
    return GateCheck(metric, actual, ">=", threshold, actual >= threshold)


def _maximum_check(metric: str, actual: float | int, threshold: float | int) -> GateCheck:
    return GateCheck(metric, actual, "<=", threshold, actual <= threshold)


def _equality_check(metric: str, actual: float, threshold: float) -> GateCheck:
    return GateCheck(metric, actual, "==", threshold, math.isclose(actual, threshold))


def _truth_check(metric: str, passed: bool) -> GateCheck:
    return GateCheck(metric, int(passed), "==", 1, passed)


def _provenance_section_complete(
    provenance: Mapping[str, object], section: str, required_keys: Sequence[str]
) -> bool:
    value = provenance.get(section)
    if not isinstance(value, dict):
        return False
    return all(
        isinstance(value.get(key), str) and bool(cast(str, value[key]).strip())
        for key in required_keys
    )


def _fingerprint(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _write_output(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
