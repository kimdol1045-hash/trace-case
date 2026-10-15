"""Deterministic, versioned automatic-generation impact projections.

The complete Generation Input remains the provider/audit authority.  This module
derives a deliberately smaller comparison projection which excludes transport,
revision, job, rank, and timestamp identities while retaining every value that
can change generated TestCase meaning.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from tc_backend.generation.reconstruction import canonical_json

IMPACT_SCHEMA_VERSION = "generation-impact/1.0.0"
CONFIG_SCHEMA_VERSION = "generation-impact-config/1.0.0"

CHANGED_FIELD_ORDER = (
    "slackWorkItem.developmentItem",
    "slackWorkItem.requestedMenu",
    "slackWorkItem.featureDescription",
    "slackWorkItem.requestDetails",
    "slackWorkItem.openQuestions",
    "activeKnowledge.requirements",
    "activeKnowledge.decisions",
    "activeKnowledge.constraints",
    "activeKnowledge.evidence",
    "retrievalInputs.existingCases",
    "retrievalInputs.failedOrBlockedExecutions",
    "retrievalInputs.openIssues",
    "retrievalInputs.extraSources",
    "categoryCandidates",
    "generationPolicy",
    "contractConfig",
)

_SPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class ImpactProjection:
    value: dict[str, Any]
    fingerprint: str


@dataclass(frozen=True, slots=True)
class ContractConfigProjection:
    value: dict[str, Any]
    digest: str


def canonical_generation_impact(
    generation_input: Mapping[str, Any],
    context_entries: Iterable[object],
) -> ImpactProjection:
    """Return the supported semantic projection and its canonical SHA-256."""

    slack = _mapping(generation_input.get("slackWorkItem"))
    active = _active_knowledge(generation_input, context_entries)
    projection = {
        "schemaVersion": IMPACT_SCHEMA_VERSION,
        "slackWorkItem": {
            field: _text_or_none(slack.get(field))
            for field in (
                "developmentItem",
                "requestedMenu",
                "featureDescription",
                "requestDetails",
            )
        }
        | {
            "openQuestions": _semantic_set(
                _text(value)
                for value in _list(slack.get("openQuestions"))
                if isinstance(value, str) and _text(value)
            )
        },
        "activeKnowledge": active,
        "retrievalInputs": {
            "existingCases": _semantic_set(
                _existing_case(value)
                for value in _object_list(generation_input.get("existingCases"))
            ),
            "failedOrBlockedExecutions": _semantic_set(
                _execution(value, generation_input)
                for value in _object_list(
                    generation_input.get("failedOrBlockedExecutions")
                )
            ),
            "openIssues": _semantic_set(
                _issue(value) for value in _object_list(generation_input.get("openIssues"))
            ),
            "extraSources": _semantic_set(
                _extra_source(value)
                for value in _object_list(generation_input.get("extraSources"))
            ),
        },
        "categoryCandidates": _semantic_set(
            {
                "categoryId": _text(value.get("categoryId")),
                "label": _text(value.get("label")),
                "score": value.get("score"),
                "selectionReason": _text(value.get("selectionReason")),
            }
            for value in _object_list(generation_input.get("categoryCandidates"))
        ),
        "generationPolicy": _generation_policy(generation_input.get("generationPolicy")),
    }
    return ImpactProjection(value=projection, fingerprint=_digest(projection))


def canonical_contract_config(
    *,
    generation_input: Mapping[str, Any],
    ontology_version: int,
    prompt_version: str,
    extraction: Mapping[str, Any],
) -> ContractConfigProjection:
    """Return the separately versioned deterministic generation contract config."""

    policy = _mapping(generation_input.get("generationPolicy"))
    value = {
        "schemaVersion": CONFIG_SCHEMA_VERSION,
        "impactSchemaVersion": IMPACT_SCHEMA_VERSION,
        "canonicalizerVersion": "generation-impact-canonical-json-v1",
        "inputSchemaVersion": _text(generation_input.get("inputSchemaVersion")),
        "outputSchemaVersion": _text(
            generation_input.get("requestedOutputSchemaVersion")
        ),
        "ontologyVersion": ontology_version,
        "promptVersion": _text(prompt_version),
        "generationPolicyVersion": _text(policy.get("policyVersion")),
        "retrievalPolicyVersion": "grounded-generation-context-v1",
        "extractor": {
            "provider": _text(extraction.get("provider")),
            "model": _text(extraction.get("model")),
            "schemaVersion": _text(extraction.get("schemaVersion")),
        },
        "validatorVersion": "generation-input-validator-v1",
        "classifierVersion": "knowledge-extraction-classifier-v1",
    }
    return ContractConfigProjection(value=value, digest=_digest(value))


def changed_fields(
    baseline: Mapping[str, Any], current: Mapping[str, Any]
) -> list[str]:
    """Describe semantic section changes in the fixed contract order."""

    baseline_slack = _mapping(baseline.get("slackWorkItem"))
    current_slack = _mapping(current.get("slackWorkItem"))
    baseline_active = _mapping(baseline.get("activeKnowledge"))
    current_active = _mapping(current.get("activeKnowledge"))
    baseline_retrieval = _mapping(baseline.get("retrievalInputs"))
    current_retrieval = _mapping(current.get("retrievalInputs"))
    comparisons: dict[str, tuple[object, object]] = {
        **{
            f"slackWorkItem.{field}": (
                baseline_slack.get(field),
                current_slack.get(field),
            )
            for field in (
                "developmentItem",
                "requestedMenu",
                "featureDescription",
                "requestDetails",
                "openQuestions",
            )
        },
        **{
            f"activeKnowledge.{field}": (
                baseline_active.get(field),
                current_active.get(field),
            )
            for field in ("requirements", "decisions", "constraints", "evidence")
        },
        **{
            f"retrievalInputs.{field}": (
                baseline_retrieval.get(field),
                current_retrieval.get(field),
            )
            for field in (
                "existingCases",
                "failedOrBlockedExecutions",
                "openIssues",
                "extraSources",
            )
        },
        "categoryCandidates": (
            baseline.get("categoryCandidates"),
            current.get("categoryCandidates"),
        ),
        "generationPolicy": (
            baseline.get("generationPolicy"),
            current.get("generationPolicy"),
        ),
    }
    return [field for field in CHANGED_FIELD_ORDER if field in comparisons and comparisons[field][0] != comparisons[field][1]]


def _active_knowledge(
    generation_input: Mapping[str, Any], context_entries: Iterable[object]
) -> dict[str, Any]:
    items = _object_list(generation_input.get("activeKnowledge"))
    by_node: dict[str, set[tuple[str, str, str]]] = {}
    for entry in context_entries:
        node_id = getattr(entry, "node_id", None)
        if node_id is None:
            continue
        by_node.setdefault(_uuid_key(node_id), set()).add(
            (
                _text(getattr(entry, "source_kind", "")),
                _text(getattr(entry, "source_entity_type", "")),
                _text(getattr(entry, "source_content_hash", "")),
            )
        )
    content: dict[str, list[str]] = {
        "requirements": [],
        "decisions": [],
        "constraints": [],
    }
    evidence: list[dict[str, Any]] = []
    names = {
        "REQUIREMENT": "requirements",
        "DECISION": "decisions",
        "CONSTRAINT": "constraints",
    }
    for item in items:
        section = names.get(str(item.get("nodeType") or "").upper())
        if section is None:
            continue
        normalized_content = _text(item.get("content"))
        content[section].append(normalized_content)
        node_id = _uuid_key(item.get("nodeId"))
        citations = [
            {
                "sourceKind": source_kind,
                "sourceEntityType": entity_type,
                "sourceContentHash": content_hash,
            }
            for source_kind, entity_type, content_hash in sorted(by_node.get(node_id, set()))
        ]
        evidence.append(
            {
                "nodeType": str(item.get("nodeType") or "").upper(),
                "content": normalized_content,
                "citations": citations,
            }
        )
    return {
        **{key: _semantic_set(values) for key, values in content.items()},
        "evidence": _semantic_set(evidence),
    }


def _existing_case(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "caseKey": _text(value.get("caseKey")),
        "summary": _text(value.get("summary")),
        "precondition": _text_or_none(value.get("precondition")),
        "steps": [
            {
                "sequence": step.get("sequence"),
                "precondition": _text_or_none(step.get("precondition")),
                "action": _text(step.get("action")),
                "expectedResult": _text(step.get("expectedResult")),
            }
            for step in _object_list(value.get("steps"))
        ],
        "expectedResultMode": _text(value.get("expectedResultMode")),
        "caseExpectedResult": _text_or_none(value.get("caseExpectedResult")),
        "priority": _text(value.get("priority")),
        "labels": _semantic_set(
            _text(label) for label in _list(value.get("labels")) if isinstance(label, str)
        ),
    }


def _execution(
    value: Mapping[str, Any], generation_input: Mapping[str, Any]
) -> dict[str, Any]:
    issue_keys = {
        str(issue.get("issueId")): _text(issue.get("issueKey"))
        for issue in _object_list(generation_input.get("openIssues"))
    }
    return {
        "status": _text(value.get("status")),
        "platform": _text_or_none(value.get("platform")),
        "actualResult": _text(value.get("actualResult")),
        "linkedIssues": _semantic_set(
            issue_keys.get(str(issue_id), "")
            for issue_id in _list(value.get("linkedIssueIds"))
            if issue_keys.get(str(issue_id))
        ),
    }


def _issue(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "issueKey": _text(value.get("issueKey")),
        "status": _text(value.get("status")),
        "severity": _text(value.get("severity")),
        "summary": _text(value.get("summary")),
        "expectedResult": _text(value.get("expectedResult")),
        "actualResult": _text(value.get("actualResult")),
    }


def _extra_source(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "sourceType": _text(value.get("sourceType")),
        "title": _text(value.get("title")),
        "content": _text(value.get("content")),
        "uri": _text_or_none(value.get("uri")),
        "contentHash": _text(value.get("contentHash")),
        "providedByUser": bool(value.get("providedByUser")),
    }


def _generation_policy(raw: object) -> dict[str, Any]:
    value = _mapping(raw)
    batch = _mapping(value.get("batch"))
    return {
        "batch": {
            "featureGroup": _text(batch.get("featureGroup")),
            "batchNumber": batch.get("batchNumber"),
            "isFinalBatch": batch.get("isFinalBatch"),
        },
        **{
            field: value.get(field)
            for field in (
                "minimumCaseCount",
                "maximumCaseCount",
                "expectedResultMode",
                "prioritizeHappyPath",
                "includeApplicableCrud",
                "allowUnsupportedEdgeCases",
                "requireEvidenceReferences",
                "unknownFactHandling",
                "sensitiveDataHandling",
                "language",
            )
        },
    }


def _semantic_set(values: Iterable[Any]) -> list[Any]:
    materialized = list(values)
    return sorted(materialized, key=_canonical_json)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_json(value: object) -> str:
    return canonical_json(value)


def _text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return _SPACE.sub(" ", unicodedata.normalize("NFKC", value)).strip()


def _text_or_none(value: object) -> str | None:
    normalized = _text(value)
    return normalized or None


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _object_list(value: object) -> list[Mapping[str, Any]]:
    return [item for item in _list(value) if isinstance(item, Mapping)]


def _uuid_key(value: object) -> str:
    raw = str(value or "")
    if "_" in raw:
        raw = raw.rsplit("_", 1)[-1]
    return raw.replace("-", "").lower()
