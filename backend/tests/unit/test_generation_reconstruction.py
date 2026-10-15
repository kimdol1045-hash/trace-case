from __future__ import annotations

import copy
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tc_backend.db.models import GenerationJob
from tc_backend.generation.reconstruction import (
    GENERATION_INPUT_CAPTURE_LIMIT,
    GenerationInputCaptureError,
    GenerationInputCaptureState,
    GenerationReconstructionReason,
    canonical_input_digest,
    reconstruct_generation_input,
    seal_generation_input,
)

ROOT = Path(__file__).resolve().parents[3]


def _fixture() -> dict[str, object]:
    value = json.loads(
        (ROOT / "packages/contracts/generation/fixtures/1.0.0/input.full-context.json").read_text()
    )
    assert isinstance(value, dict)
    return value


def _job(
    *,
    capture_state: str,
    input_schema_version: str | None,
    generation_input: dict[str, object] | None,
    generation_input_digest: str | None,
) -> GenerationJob:
    job_id = uuid.uuid4()
    now = datetime(2026, 8, 27, tzinfo=UTC)
    return GenerationJob(
        id=job_id,
        slack_work_item_id=uuid.uuid4(),
        source_version=1,
        generation_version=1,
        status="QUEUED",
        provider="UNCONFIGURED",
        model="UNCONFIGURED",
        prompt_version="generation_prompt_v1",
        schema_version="1.0.0",
        ontology_schema_id=uuid.uuid4(),
        context_item_count=1,
        context_digest="a" * 64,
        context_sealed_at=now,
        root_job_id=job_id,
        attempt_number=1,
        generation_input_capture_state=capture_state,
        generation_input_schema_version=input_schema_version,
        generation_input=generation_input,
        generation_input_digest=generation_input_digest,
    )


def test_canonical_digest_matches_contract_and_preserves_json_semantics() -> None:
    payload = _fixture()

    sealed, digest = seal_generation_input(payload)

    assert sealed == payload
    assert digest.value == "792de6c007dd69ef8e7f7e80b2038f2c41d9c5ce9d11e4c4207a2bce72731ab3"
    assert digest.canonical_byte_length == 5_556
    reordered = copy.deepcopy(payload)
    active = reordered["activeKnowledge"]
    assert isinstance(active, list)
    active.reverse()
    assert canonical_input_digest(reordered) != digest
    assert canonical_input_digest({"value": "é"}) != canonical_input_digest({"value": "e\u0301"})
    assert canonical_input_digest({"score": 0.000001}).canonical_byte_length == len(
        b'{"score":0.000001}'
    )


def test_sealing_rejects_an_actual_input_larger_than_the_response_limit() -> None:
    payload = _fixture()
    knowledge_template = payload["activeKnowledge"][0]
    case_template = payload["existingCases"][0]
    assert isinstance(knowledge_template, dict)
    assert isinstance(case_template, dict)
    payload["activeKnowledge"] = [
        {
            **copy.deepcopy(knowledge_template),
            "nodeId": f"knowledge_node_{index}",
            "content": "지" * 20_000,
            "rank": index + 1,
        }
        for index in range(500)
    ]
    payload["existingCases"] = [
        {
            **copy.deepcopy(case_template),
            "nodeId": f"knowledge_case_{index}",
            "testCaseId": f"case_{index}",
            "testCaseVersionId": f"case_version_{index}",
            "caseKey": f"CASE-{100_000 + index}",
            "steps": [
                {
                    "sequence": 1,
                    "precondition": None,
                    "action": "행" * 20_000,
                    "expectedResult": None,
                }
            ],
            "rank": index + 1,
        }
        for index in range(500)
    ]
    assert len(json.dumps(payload, ensure_ascii=False).encode()) > (GENERATION_INPUT_CAPTURE_LIMIT)

    with pytest.raises(GenerationInputCaptureError, match="canonical byte limit"):
        seal_generation_input(payload)


def test_runtime_input_validation_matches_public_json_types_and_platform_enum() -> None:
    aos_payload = _fixture()
    executions = aos_payload["failedOrBlockedExecutions"]
    assert isinstance(executions, list)
    assert isinstance(executions[0], dict)
    executions[0]["platform"] = "AOS"
    sealed, _ = seal_generation_input(aos_payload)
    assert sealed["failedOrBlockedExecutions"][0]["platform"] == "AOS"

    invalid_values = (
        ("failedOrBlockedExecutions", 0, "platform", "ANDROID"),
        (None, None, "createdAt", 1_787_700_000),
        (None, None, "createdAt", "2026-08-27T01:00:00"),
        ("slackWorkItem", None, "permalink", "not an absolute URI"),
        ("slackWorkItem", None, "sourceVersion", True),
    )
    for section, index, field, invalid in invalid_values:
        payload = _fixture()
        target: dict[str, object]
        if section is None:
            target = payload
        else:
            selected = payload[section]
            if index is not None:
                assert isinstance(selected, list)
                selected = selected[index]
            assert isinstance(selected, dict)
            target = selected
        target[field] = invalid
        with pytest.raises(GenerationInputCaptureError):
            seal_generation_input(payload)


def test_reconstruction_classifies_available_legacy_incomplete_and_unsupported() -> None:
    payload, digest = seal_generation_input(_fixture())
    available = reconstruct_generation_input(
        _job(
            capture_state=GenerationInputCaptureState.SEALED.value,
            input_schema_version="1.0.0",
            generation_input=payload,
            generation_input_digest=digest.value,
        ),
        context_valid=True,
    )
    assert available.availability == "AVAILABLE"
    assert available.reason is None
    assert available.generation_input == payload
    assert available.generation_input is not payload

    legacy = reconstruct_generation_input(
        _job(
            capture_state=GenerationInputCaptureState.LEGACY_NOT_CAPTURED.value,
            input_schema_version=None,
            generation_input=None,
            generation_input_digest=None,
        ),
        context_valid=True,
    )
    assert legacy.availability == "UNAVAILABLE"
    assert legacy.reason is GenerationReconstructionReason.LEGACY_INPUT_NOT_SEALED
    assert legacy.generation_input is None
    assert legacy.input_digest is None

    incomplete = reconstruct_generation_input(
        _job(
            capture_state=GenerationInputCaptureState.SEALED.value,
            input_schema_version="1.0.0",
            generation_input=payload,
            generation_input_digest="0" * 64,
        ),
        context_valid=True,
    )
    assert incomplete.reason is GenerationReconstructionReason.SEALED_INPUT_INCOMPLETE
    assert incomplete.generation_input is None
    assert incomplete.input_digest is None

    context_invalid = reconstruct_generation_input(available.job, context_valid=False)
    assert context_invalid.reason is GenerationReconstructionReason.SEALED_INPUT_INCOMPLETE

    malformed_version = reconstruct_generation_input(
        _job(
            capture_state=GenerationInputCaptureState.SEALED.value,
            input_schema_version="not-semver",
            generation_input=payload,
            generation_input_digest=digest.value,
        ),
        context_valid=True,
    )
    assert malformed_version.reason is GenerationReconstructionReason.SEALED_INPUT_INCOMPLETE
    assert malformed_version.recorded_input_schema_version is None

    unsupported_payload = copy.deepcopy(payload)
    unsupported_payload["inputSchemaVersion"] = "2.0.0"
    unsupported_digest = canonical_input_digest(unsupported_payload)
    unsupported = reconstruct_generation_input(
        _job(
            capture_state=GenerationInputCaptureState.SEALED.value,
            input_schema_version="2.0.0",
            generation_input=unsupported_payload,
            generation_input_digest=unsupported_digest.value,
        ),
        context_valid=True,
    )
    assert unsupported.reason is GenerationReconstructionReason.UNSUPPORTED_INPUT_SCHEMA_VERSION
    assert unsupported.generation_input is None
    assert unsupported.input_digest == unsupported_digest
