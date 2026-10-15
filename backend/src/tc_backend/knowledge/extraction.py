"""Extraction provider boundary and deterministic Slack evidence validation."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Protocol

import httpx

from tc_backend.knowledge.classification import KnowledgeStateClassifier
from tc_backend.knowledge.models import (
    CandidateRelation,
    CandidateRelationKind,
    KnowledgeEvidence,
    KnowledgeExtractionResult,
    KnowledgeNodeType,
    ProviderEvidence,
    ProviderExtraction,
    ProviderKnowledgeCandidate,
    SlackSourceBundle,
    SlackSourceRevision,
    SourceRevisionOperation,
    ValidatedCandidateRelation,
    ValidatedExtraction,
    ValidatedKnowledgeCandidate,
)


class KnowledgeExtractionError(RuntimeError):
    """Base error for the provider/validation extraction boundary."""


class KnowledgeExtractionProviderUnavailable(KnowledgeExtractionError):
    """The configured semantic extraction provider cannot currently run."""


class KnowledgeExtractionProviderError(KnowledgeExtractionError):
    """The provider ran but did not return a usable extraction response."""


class KnowledgeValidationError(KnowledgeExtractionError, ValueError):
    """Provider output is not safely grounded in the supplied Slack bundle."""


class KnowledgeExtractionProvider(Protocol):
    """Semantic candidate provider, normally backed by a local LLM.

    Implementations may infer candidates, but may not assign final lifecycle
    state.  Evidence validation and ACTIVE/AMBIGUOUS/SUPERSEDED/RETRACTED
    classification are deterministic application responsibilities.
    """

    async def extract(self, bundle: SlackSourceBundle) -> ProviderExtraction: ...


class OllamaKnowledgeExtractionTransport(Protocol):
    """Transport-only boundary for one structured Ollama extraction call."""

    async def extract(
        self,
        *,
        host: str,
        model: str,
        prompt: str,
        response_schema: Mapping[str, object],
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> Mapping[str, object]: ...


class HttpxOllamaKnowledgeExtractionTransport:
    """Make one non-streaming local Ollama chat request with a bounded body."""

    async def extract(
        self,
        *,
        host: str,
        model: str,
        prompt: str,
        response_schema: Mapping[str, object],
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> Mapping[str, object]:
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(
                    f"{host.rstrip('/')}/api/chat",
                    json={
                        "model": model,
                        "messages": [
                            {
                                "role": "system",
                                "content": _OLLAMA_EXTRACTION_SYSTEM_PROMPT,
                            },
                            {"role": "user", "content": prompt},
                        ],
                        "format": dict(response_schema),
                        "stream": False,
                        "options": {"temperature": 0},
                    },
                )
        except httpx.TransportError as exc:
            raise KnowledgeExtractionProviderUnavailable(
                "Ollama knowledge extraction endpoint is unavailable"
            ) from exc
        if len(response.content) > max_response_bytes:
            raise KnowledgeExtractionProviderError(
                "Ollama knowledge extraction response exceeds the configured limit"
            )
        if response.status_code >= 500:
            raise KnowledgeExtractionProviderUnavailable(
                f"Ollama knowledge extraction returned HTTP {response.status_code}"
            )
        try:
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPStatusError, ValueError) as exc:
            raise KnowledgeExtractionProviderError(
                "Ollama knowledge extraction response is invalid"
            ) from exc
        if not isinstance(payload, dict):
            raise KnowledgeExtractionProviderError(
                "Ollama knowledge extraction response must be an object"
            )
        return payload


class OllamaKnowledgeExtractionProvider:
    """Convert one grounded Slack bundle into validated provider candidates.

    This adapter remains dormant until an owning composition root explicitly
    enables it.  Candidate grounding and lifecycle state are still enforced by
    ``SlackKnowledgeExtractor`` after this untrusted response is decoded.
    """

    def __init__(
        self,
        transport: OllamaKnowledgeExtractionTransport,
        *,
        host: str,
        model: str,
        timeout_seconds: float = 60.0,
        max_response_bytes: int = 1024 * 1024,
    ) -> None:
        if not host.strip() or not model.strip():
            raise ValueError("Ollama extraction host and model must not be empty")
        if timeout_seconds <= 0 or max_response_bytes < 1:
            raise ValueError("Ollama extraction limits must be positive")
        self._transport = transport
        self._host = host.strip()
        self._model = model.strip()
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes

    async def extract(self, bundle: SlackSourceBundle) -> ProviderExtraction:
        payload = await self._transport.extract(
            host=self._host,
            model=self._model,
            prompt=_ollama_extraction_prompt(bundle),
            response_schema=_OLLAMA_EXTRACTION_SCHEMA,
            timeout_seconds=self._timeout_seconds,
            max_response_bytes=self._max_response_bytes,
        )
        response_model = payload.get("model")
        if response_model is not None and response_model != self._model:
            raise KnowledgeExtractionProviderError(
                "Ollama knowledge extraction model does not match the request"
            )
        message = payload.get("message")
        if not isinstance(message, Mapping):
            raise KnowledgeExtractionProviderError(
                "Ollama knowledge extraction response omitted message metadata"
            )
        content = message.get("content")
        if not isinstance(content, str):
            raise KnowledgeExtractionProviderError(
                "Ollama knowledge extraction response omitted structured content"
            )
        try:
            decoded = json.loads(content)
        except ValueError as exc:
            raise KnowledgeExtractionProviderError(
                "Ollama knowledge extraction content is not JSON"
            ) from exc
        return _decode_ollama_extraction(decoded, model=self._model)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(model={self._model!r})"


class KnowledgeCandidateValidator:
    """Validate, normalize, and deterministically order untrusted candidates."""

    def validate(
        self,
        bundle: SlackSourceBundle,
        extraction: ProviderExtraction,
    ) -> ValidatedExtraction:
        if not isinstance(extraction, ProviderExtraction):
            raise KnowledgeValidationError("provider output must be a ProviderExtraction")
        revisions = _validate_bundle(bundle)
        provider = _required_text(extraction.provider, "provider")
        model = _required_text(extraction.model, "model")
        schema_version = _required_text(extraction.schema_version, "schema_version")

        by_candidate_id: dict[str, object] = {}
        for raw in extraction.candidates:
            candidate_id = _required_text(raw.candidate_id, "candidate_id")
            if candidate_id in by_candidate_id:
                raise KnowledgeValidationError(f"duplicate candidate_id: {candidate_id}")
            by_candidate_id[candidate_id] = raw

        validated: list[ValidatedKnowledgeCandidate] = []
        for raw in extraction.candidates:
            candidate_id = raw.candidate_id.strip()
            try:
                node_type = KnowledgeNodeType(raw.node_type)
            except ValueError as exc:
                raise KnowledgeValidationError(
                    f"candidate {candidate_id} has unsupported node_type: {raw.node_type}"
                ) from exc
            content = _normalized_text(raw.content)
            topic_key = _normalized_text(raw.topic_key).casefold()
            if not content:
                raise KnowledgeValidationError(f"candidate {candidate_id} content is empty")
            if not topic_key:
                raise KnowledgeValidationError(f"candidate {candidate_id} topic_key is empty")
            if (
                isinstance(raw.confidence, bool)
                or not isinstance(raw.confidence, int | float)
                or not math.isfinite(raw.confidence)
                or not 0.0 <= raw.confidence <= 1.0
            ):
                raise KnowledgeValidationError(
                    f"candidate {candidate_id} confidence must be between 0 and 1"
                )
            if not isinstance(raw.explicitly_retracted, bool):
                raise KnowledgeValidationError(
                    f"candidate {candidate_id} explicitly_retracted must be boolean"
                )
            evidence = _validate_evidence(candidate_id, raw.evidence, revisions)
            relations = _validate_relations(
                candidate_id,
                raw.relations,
                candidate_ids=frozenset(by_candidate_id),
            )
            validated.append(
                ValidatedKnowledgeCandidate(
                    candidate_id=candidate_id,
                    node_type=node_type,
                    content=content,
                    topic_key=topic_key,
                    confidence=float(raw.confidence),
                    evidence=evidence,
                    relations=relations,
                    explicitly_retracted=raw.explicitly_retracted,
                )
            )

        ordered = tuple(sorted(validated, key=_candidate_sort_key))
        _validate_relation_semantics(ordered)
        return ValidatedExtraction(
            provider=provider,
            model=model,
            schema_version=schema_version,
            candidates=ordered,
        )


class SlackKnowledgeExtractor:
    """Compose one semantic provider with deterministic application controls."""

    def __init__(
        self,
        provider: KnowledgeExtractionProvider,
        *,
        validator: KnowledgeCandidateValidator | None = None,
        classifier: KnowledgeStateClassifier | None = None,
    ) -> None:
        self._provider = provider
        self._validator = validator or KnowledgeCandidateValidator()
        self._classifier = classifier or KnowledgeStateClassifier()

    async def extract(self, bundle: SlackSourceBundle) -> KnowledgeExtractionResult:
        try:
            provider_result = await self._provider.extract(bundle)
        except KnowledgeExtractionProviderUnavailable:
            raise
        except Exception as exc:
            raise KnowledgeExtractionProviderError(
                f"knowledge extraction provider failed: {exc.__class__.__name__}"
            ) from exc
        validated = self._validator.validate(bundle, provider_result)
        classified = self._classifier.classify(bundle, validated.candidates)
        return KnowledgeExtractionResult(
            provider=validated.provider,
            model=validated.model,
            schema_version=validated.schema_version,
            candidates=classified,
        )


_OLLAMA_EXTRACTION_SYSTEM_PROMPT = (
    "Extract only requirement, decision, constraint, and open-question facts from the "
    "provided Slack revision bundle. Cite exact sourceRevisionId values and exact "
    "verbatim excerpts; return JSON matching the supplied schema and do not invent facts."
)
_OLLAMA_EXTRACTION_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schemaVersion", "candidates"],
    "properties": {
        "schemaVersion": {"const": "knowledge-extraction/1.0.0"},
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "candidateId",
                    "nodeType",
                    "content",
                    "topicKey",
                    "confidence",
                    "evidence",
                    "relations",
                    "explicitlyRetracted",
                ],
                "properties": {
                    "candidateId": {"type": "string", "minLength": 1},
                    "nodeType": {
                        "enum": [
                            "REQUIREMENT",
                            "DECISION",
                            "CONSTRAINT",
                            "OPEN_QUESTION",
                        ]
                    },
                    "content": {"type": "string", "minLength": 1},
                    "topicKey": {"type": "string", "minLength": 1},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["sourceRevisionId", "excerpt"],
                            "properties": {
                                "sourceRevisionId": {"type": "string", "minLength": 1},
                                "excerpt": {"type": "string", "minLength": 1},
                            },
                        },
                    },
                    "relations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["kind", "targetCandidateId"],
                            "properties": {
                                "kind": {
                                    "enum": ["SUPERSEDES", "CONFLICTS_WITH", "RETRACTS"]
                                },
                                "targetCandidateId": {
                                    "type": "string",
                                    "minLength": 1,
                                },
                            },
                        },
                    },
                    "explicitlyRetracted": {"type": "boolean"},
                },
            },
        },
    },
}


def _ollama_extraction_prompt(bundle: SlackSourceBundle) -> str:
    revisions = [
        {
            "sourceRevisionId": revision.source_revision_id,
            "messageId": revision.message_id,
            "messageTs": revision.message_ts,
            "revisionTs": revision.revision_ts,
            "operation": revision.operation.value,
            "authorId": revision.author_id,
            "body": revision.body,
            "isRoot": revision.is_root,
        }
        for revision in bundle.revisions
    ]
    return json.dumps(
        {
            "workspaceId": bundle.workspace_id,
            "channelId": bundle.channel_id,
            "threadTs": bundle.thread_ts,
            "workItemId": bundle.work_item_id,
            "sourceVersion": bundle.source_version,
            "revisions": revisions,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _decode_ollama_extraction(value: object, *, model: str) -> ProviderExtraction:
    if not isinstance(value, Mapping):
        raise KnowledgeExtractionProviderError(
            "Ollama knowledge extraction content must be an object"
        )
    if set(value) != {"schemaVersion", "candidates"}:
        raise KnowledgeExtractionProviderError(
            "Ollama knowledge extraction content has unsupported fields"
        )
    schema_version = value.get("schemaVersion")
    raw_candidates = value.get("candidates")
    if schema_version != "knowledge-extraction/1.0.0" or not isinstance(
        raw_candidates, list
    ):
        raise KnowledgeExtractionProviderError(
            "Ollama knowledge extraction content has an unsupported schema"
        )
    candidates: list[ProviderKnowledgeCandidate] = []
    for index, candidate in enumerate(raw_candidates):
        if not isinstance(candidate, Mapping):
            raise KnowledgeExtractionProviderError(
                f"Ollama knowledge candidate {index} must be an object"
            )
        expected = {
            "candidateId",
            "nodeType",
            "content",
            "topicKey",
            "confidence",
            "evidence",
            "relations",
            "explicitlyRetracted",
        }
        if set(candidate) != expected:
            raise KnowledgeExtractionProviderError(
                f"Ollama knowledge candidate {index} has unsupported fields"
            )
        raw_evidence = candidate.get("evidence")
        raw_relations = candidate.get("relations")
        if not isinstance(raw_evidence, list) or not isinstance(raw_relations, list):
            raise KnowledgeExtractionProviderError(
                f"Ollama knowledge candidate {index} has invalid collections"
            )
        evidence: list[ProviderEvidence] = []
        for evidence_index, item in enumerate(raw_evidence):
            if not isinstance(item, Mapping) or set(item) != {
                "sourceRevisionId",
                "excerpt",
            }:
                raise KnowledgeExtractionProviderError(
                    "Ollama knowledge candidate evidence has unsupported fields"
                )
            evidence.append(
                ProviderEvidence(
                    source_revision_id=_provider_string(
                        item.get("sourceRevisionId"),
                        f"candidates[{index}].evidence[{evidence_index}].sourceRevisionId",
                    ),
                    excerpt=_provider_string(
                        item.get("excerpt"),
                        f"candidates[{index}].evidence[{evidence_index}].excerpt",
                    ),
                )
            )
        relations: list[CandidateRelation] = []
        for relation_index, item in enumerate(raw_relations):
            if not isinstance(item, Mapping) or set(item) != {
                "kind",
                "targetCandidateId",
            }:
                raise KnowledgeExtractionProviderError(
                    "Ollama knowledge candidate relation has unsupported fields"
                )
            relations.append(
                CandidateRelation(
                    kind=_provider_string(
                        item.get("kind"),
                        f"candidates[{index}].relations[{relation_index}].kind",
                    ),
                    target_candidate_id=_provider_string(
                        item.get("targetCandidateId"),
                        f"candidates[{index}].relations[{relation_index}].targetCandidateId",
                    ),
                )
            )
        confidence = candidate.get("confidence")
        explicitly_retracted = candidate.get("explicitlyRetracted")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, int | float)
            or not isinstance(explicitly_retracted, bool)
        ):
            raise KnowledgeExtractionProviderError(
                f"Ollama knowledge candidate {index} has invalid scalar fields"
            )
        candidates.append(
            ProviderKnowledgeCandidate(
                candidate_id=_provider_string(
                    candidate.get("candidateId"), f"candidates[{index}].candidateId"
                ),
                node_type=_provider_string(
                    candidate.get("nodeType"), f"candidates[{index}].nodeType"
                ),
                content=_provider_string(
                    candidate.get("content"), f"candidates[{index}].content"
                ),
                topic_key=_provider_string(
                    candidate.get("topicKey"), f"candidates[{index}].topicKey"
                ),
                confidence=float(confidence),
                evidence=tuple(evidence),
                relations=tuple(relations),
                explicitly_retracted=explicitly_retracted,
            )
        )
    return ProviderExtraction(
        provider="ollama",
        model=model,
        schema_version="knowledge-extraction/1.0.0",
        candidates=tuple(candidates),
    )


def _provider_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeExtractionProviderError(
            f"Ollama knowledge extraction {field} must be non-empty"
        )
    return value.strip()


def _validate_bundle(bundle: SlackSourceBundle) -> dict[str, SlackSourceRevision]:
    if not bundle.workspace_id.strip() or not bundle.channel_id.strip() or not bundle.thread_ts.strip():
        raise KnowledgeValidationError("Slack source bundle identity must not be empty")
    if not bundle.revisions:
        raise KnowledgeValidationError("Slack source bundle requires at least one revision")
    revisions: dict[str, SlackSourceRevision] = {}
    for revision in bundle.revisions:
        revision_id = _required_text(revision.source_revision_id, "source_revision_id")
        _required_text(revision.message_id, "message_id")
        _slack_timestamp(revision.revision_ts)
        if revision_id in revisions:
            raise KnowledgeValidationError(f"duplicate source_revision_id: {revision_id}")
        if not isinstance(revision.operation, SourceRevisionOperation):
            raise KnowledgeValidationError(
                f"revision {revision_id} has unsupported operation: {revision.operation}"
            )
        if revision.operation is SourceRevisionOperation.DELETE:
            if revision.body is not None and revision.body.strip():
                raise KnowledgeValidationError(
                    f"delete revision {revision_id} must not introduce a new body"
                )
        elif revision.body is None or not revision.body.strip():
            raise KnowledgeValidationError(f"revision {revision_id} body must not be empty")
        revisions[revision_id] = revision
    return revisions


def _validate_evidence(
    candidate_id: str,
    raw_evidence: Sequence[object],
    revisions: dict[str, SlackSourceRevision],
) -> tuple[KnowledgeEvidence, ...]:
    if not raw_evidence:
        raise KnowledgeValidationError(f"candidate {candidate_id} requires evidence")
    validated: list[KnowledgeEvidence] = []
    seen: set[tuple[str, str]] = set()
    for item in raw_evidence:
        source_revision_id = _required_text(
            getattr(item, "source_revision_id", ""), "evidence source_revision_id"
        )
        revision = revisions.get(source_revision_id)
        if revision is None:
            raise KnowledgeValidationError(
                f"candidate {candidate_id} cites unknown revision: {source_revision_id}"
            )
        excerpt = _normalized_text(getattr(item, "excerpt", ""))
        if not excerpt:
            raise KnowledgeValidationError(f"candidate {candidate_id} has empty evidence excerpt")
        source_body = _normalized_text(revision.body or "")
        if not source_body or excerpt.casefold() not in source_body.casefold():
            raise KnowledgeValidationError(
                f"candidate {candidate_id} evidence is not an exact normalized source span"
            )
        evidence_key = (source_revision_id, excerpt.casefold())
        if evidence_key in seen:
            raise KnowledgeValidationError(f"candidate {candidate_id} has duplicate evidence")
        seen.add(evidence_key)
        validated.append(
            KnowledgeEvidence(
                source_revision_id=source_revision_id,
                message_id=revision.message_id.strip(),
                revision_ts=revision.revision_ts,
                author_id=revision.author_id,
                permalink=revision.permalink,
                excerpt=excerpt,
            )
        )
    return tuple(sorted(validated, key=_evidence_sort_key))


def _validate_relations(
    candidate_id: str,
    raw_relations: Sequence[object],
    *,
    candidate_ids: frozenset[str],
) -> tuple[ValidatedCandidateRelation, ...]:
    validated: list[ValidatedCandidateRelation] = []
    seen: set[tuple[CandidateRelationKind, str]] = set()
    targets_by_kind: dict[str, CandidateRelationKind] = {}
    for relation in raw_relations:
        try:
            kind = CandidateRelationKind(getattr(relation, "kind", ""))
        except ValueError as exc:
            raise KnowledgeValidationError(
                f"candidate {candidate_id} has unsupported relation kind"
            ) from exc
        target = _required_text(
            getattr(relation, "target_candidate_id", ""), "target_candidate_id"
        )
        if target == candidate_id:
            raise KnowledgeValidationError(f"candidate {candidate_id} cannot relate to itself")
        if target not in candidate_ids:
            raise KnowledgeValidationError(
                f"candidate {candidate_id} relates to unknown candidate: {target}"
            )
        key = (kind, target)
        if key in seen:
            raise KnowledgeValidationError(f"candidate {candidate_id} has duplicate relation")
        previous_kind = targets_by_kind.get(target)
        if previous_kind is not None and previous_kind is not kind:
            raise KnowledgeValidationError(
                f"candidate {candidate_id} assigns incompatible relations to {target}"
            )
        seen.add(key)
        targets_by_kind[target] = kind
        validated.append(ValidatedCandidateRelation(kind=kind, target_candidate_id=target))
    return tuple(sorted(validated, key=lambda item: (item.kind.value, item.target_candidate_id)))


def _validate_relation_semantics(candidates: tuple[ValidatedKnowledgeCandidate, ...]) -> None:
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    transition_graph: dict[str, set[str]] = {
        candidate.candidate_id: set() for candidate in candidates
    }
    for candidate in candidates:
        for relation in candidate.relations:
            target = by_id[relation.target_candidate_id]
            if candidate.topic_key != target.topic_key:
                raise KnowledgeValidationError(
                    f"relation {candidate.candidate_id}->{target.candidate_id} crosses topic_key"
                )
            if relation.kind in {
                CandidateRelationKind.SUPERSEDES,
                CandidateRelationKind.RETRACTS,
            } and _candidate_revision_key(candidate) < _candidate_revision_key(target):
                raise KnowledgeValidationError(
                    f"{relation.kind.value} source {candidate.candidate_id} must be newer than target"
                )
            if relation.kind in {
                CandidateRelationKind.SUPERSEDES,
                CandidateRelationKind.RETRACTS,
            }:
                transition_graph[candidate.candidate_id].add(target.candidate_id)
    _reject_transition_cycles(transition_graph)


def _reject_transition_cycles(graph: dict[str, set[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(candidate_id: str) -> None:
        if candidate_id in visiting:
            raise KnowledgeValidationError("candidate transition relations contain a cycle")
        if candidate_id in visited:
            return
        visiting.add(candidate_id)
        for target_id in sorted(graph[candidate_id]):
            visit(target_id)
        visiting.remove(candidate_id)
        visited.add(candidate_id)

    for candidate_id in sorted(graph):
        visit(candidate_id)


def _candidate_sort_key(candidate: ValidatedKnowledgeCandidate) -> tuple[Decimal, str]:
    return (_candidate_revision_key(candidate), candidate.candidate_id)


def _candidate_revision_key(candidate: ValidatedKnowledgeCandidate) -> Decimal:
    return max(_slack_timestamp(item.revision_ts) for item in candidate.evidence)


def _evidence_sort_key(evidence: KnowledgeEvidence) -> tuple[Decimal, str, str]:
    return (
        _slack_timestamp(evidence.revision_ts),
        evidence.source_revision_id,
        evidence.excerpt,
    )


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeValidationError(f"{label} must not be empty")
    return value.strip()


def _normalized_text(value: object) -> str:
    if not isinstance(value, str):
        raise KnowledgeValidationError("provider text fields must be strings")
    normalized = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", " ", normalized).strip()


def _slack_timestamp(value: str) -> Decimal:
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise KnowledgeValidationError(f"invalid Slack revision timestamp: {value}") from exc
    if not result.is_finite() or result < 0:
        raise KnowledgeValidationError(f"invalid Slack revision timestamp: {value}")
    return result
