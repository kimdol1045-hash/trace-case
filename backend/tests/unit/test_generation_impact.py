from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass

from tc_backend.generation.impact import (
    CONFIG_SCHEMA_VERSION,
    IMPACT_SCHEMA_VERSION,
    canonical_contract_config,
    canonical_generation_impact,
    changed_fields,
)


@dataclass(frozen=True)
class _Entry:
    node_id: uuid.UUID
    source_kind: str
    source_entity_type: str
    source_content_hash: str


def _input(node_id: uuid.UUID) -> dict[str, object]:
    return {
        "inputSchemaVersion": "1.0.0",
        "requestedOutputSchemaVersion": "1.0.0",
        "requestId": "generation_request_volatile",
        "createdAt": "2026-08-27T00:00:00Z",
        "slackWorkItem": {
            "workItemId": "slack_work_item_volatile",
            "sourceVersion": 1,
            "sourceRevisionIds": ["slack_revision_volatile"],
            "developmentItem": "결제 승인",
            "requestedMenu": "결제 > 승인",
            "featureDescription": "카드 승인 정책",
            "requestDetails": "실패 잠금을 검증한다.",
            "openQuestions": [],
        },
        "activeKnowledge": [
            {
                "nodeId": f"knowledge_node_{node_id.hex}",
                "nodeType": "DECISION",
                "content": "결제 실패 잠금은 3회다.",
                "validFrom": "2026-08-27T00:00:00Z",
                "rank": 1,
            }
        ],
        "existingCases": [],
        "failedOrBlockedExecutions": [],
        "openIssues": [],
        "extraSources": [],
        "categoryCandidates": [
            {
                "categoryId": "folder_ai_inbox",
                "label": "AI 생성함",
                "score": 0.0,
                "selectionReason": "AI_GENERATED_INBOX_FALLBACK",
            }
        ],
        "generationPolicy": {
            "policyVersion": "generation_policy_v1",
            "batch": {
                "batchId": "generation_batch_volatile",
                "featureGroup": "카드 승인 정책",
                "batchNumber": 1,
                "isFinalBatch": True,
            },
            "minimumCaseCount": 5,
            "maximumCaseCount": 10,
            "expectedResultMode": "CASE",
            "prioritizeHappyPath": True,
            "includeApplicableCrud": True,
            "allowUnsupportedEdgeCases": False,
            "requireEvidenceReferences": True,
            "unknownFactHandling": "OPEN_QUESTION",
            "sensitiveDataHandling": "REDACT",
            "language": "ko-KR",
        },
    }


def test_generation_impact_excludes_transport_revision_and_job_churn() -> None:
    node_id = uuid.uuid4()
    baseline_input = _input(node_id)
    current_input = copy.deepcopy(baseline_input)
    current_input["requestId"] = "generation_request_different"
    current_input["createdAt"] = "2026-08-28T00:00:00Z"
    slack = current_input["slackWorkItem"]
    assert isinstance(slack, dict)
    slack["sourceVersion"] = 2
    slack["sourceRevisionIds"] = ["slack_revision_different"]
    policy = current_input["generationPolicy"]
    assert isinstance(policy, dict)
    batch = policy["batch"]
    assert isinstance(batch, dict)
    batch["batchId"] = "generation_batch_different"
    entries = (_Entry(node_id, "SOURCE_REVISION", "SLACK_MESSAGE_REVISION", "a" * 64),)

    baseline = canonical_generation_impact(baseline_input, entries)
    current = canonical_generation_impact(current_input, entries)

    assert baseline.fingerprint == current.fingerprint
    assert baseline.value["schemaVersion"] == IMPACT_SCHEMA_VERSION
    assert changed_fields(baseline.value, current.value) == []


def test_generation_impact_reports_four_field_and_evidence_content_changes() -> None:
    node_id = uuid.uuid4()
    generation_input = _input(node_id)
    baseline = canonical_generation_impact(
        generation_input,
        (_Entry(node_id, "SOURCE_REVISION", "SLACK_MESSAGE_REVISION", "a" * 64),),
    )
    current_input = copy.deepcopy(generation_input)
    slack = current_input["slackWorkItem"]
    assert isinstance(slack, dict)
    slack["requestDetails"] = "실패 잠금과 관리자 해제를 검증한다."
    current = canonical_generation_impact(
        current_input,
        (_Entry(node_id, "SOURCE_REVISION", "SLACK_MESSAGE_REVISION", "b" * 64),),
    )

    assert changed_fields(baseline.value, current.value) == [
        "slackWorkItem.requestDetails",
        "activeKnowledge.evidence",
    ]


def test_generation_contract_config_versions_supported_model_changes() -> None:
    generation_input = _input(uuid.uuid4())
    baseline = canonical_contract_config(
        generation_input=generation_input,
        ontology_version=1,
        prompt_version="generation_prompt_v1",
        extraction={
            "provider": "deterministic",
            "model": "extractor-v1",
            "schemaVersion": "knowledge-extraction-v1",
        },
    )
    current = canonical_contract_config(
        generation_input=generation_input,
        ontology_version=1,
        prompt_version="generation_prompt_v1",
        extraction={
            "provider": "deterministic",
            "model": "extractor-v2",
            "schemaVersion": "knowledge-extraction-v1",
        },
    )

    assert baseline.value["schemaVersion"] == CONFIG_SCHEMA_VERSION
    assert baseline.digest != current.digest
