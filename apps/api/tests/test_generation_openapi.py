from __future__ import annotations

import json
from pathlib import Path

from tc_api.main import create_app
from tc_backend.generation.reconstruction import GENERATION_INPUT_CAPTURE_LIMIT

ROOT = Path(__file__).resolve().parents[3]


def test_generation_job_openapi_matches_manual_lifecycle_contract() -> None:
    schema = create_app(validate_database=False).openapi()
    operations = {
        "/api/generation-jobs": {
            "post": "createGenerationJob",
            "get": "listGenerationJobs",
        },
        "/api/generation-jobs/{jobId}": {"get": "getGenerationJob"},
        "/api/generation-jobs/{jobId}/comparison": {
            "get": "getGenerationJobComparison"
        },
        "/api/generation-jobs/{jobId}/reconstruction": {
            "get": "getGenerationJobReconstruction"
        },
        "/api/generation-jobs/{jobId}/execute": {"post": "executeGenerationJob"},
        "/api/generation-jobs/{jobId}/retry": {"post": "retryGenerationJob"},
        "/api/generation-jobs/{jobId}/apply-plan": {"get": "getGenerationJobApplyPlan"},
        "/api/generation-jobs/{jobId}/apply": {"post": "applyGenerationJob"},
    }
    for path, methods in operations.items():
        for method, operation_id in methods.items():
            assert schema["paths"][path][method]["operationId"] == operation_id

    list_parameters = {
        parameter["name"]
        for parameter in schema["paths"]["/api/generation-jobs"]["get"]["parameters"]
    }
    assert list_parameters == {"slackWorkItemId", "statuses", "cursor", "limit"}
    for path in (
        "/api/generation-jobs",
        "/api/generation-jobs/{jobId}/execute",
        "/api/generation-jobs/{jobId}/retry",
        "/api/generation-jobs/{jobId}/apply",
    ):
        method = "post"
        assert "Idempotency-Key" in {
            parameter["name"]
            for parameter in schema["paths"][path][method].get("parameters", [])
        }

    apply_operation = schema["paths"]["/api/generation-jobs/{jobId}/apply"]["post"]
    apply_header = next(
        parameter
        for parameter in apply_operation["parameters"]
        if parameter["name"] == "Idempotency-Key"
    )
    assert apply_header["required"] is True
    assert set(apply_operation["responses"]) == {"201", "404", "409", "422", "500"}
    apply_plan_operation = schema["paths"]["/api/generation-jobs/{jobId}/apply-plan"][
        "get"
    ]
    assert "Idempotency-Key" not in {
        parameter["name"] for parameter in apply_plan_operation["parameters"]
    }
    assert set(
        schema["components"]["schemas"]["GenerationApplyPlanResource"]["properties"]
    ) == {
        "generationJobId",
        "jobStatus",
        "expectedContextDigest",
        "expectedResultDigest",
        "placement",
    }

    public = json.loads(
        (ROOT / "packages/contracts/generation/job-api/1.0.0/schema.json").read_text()
    )["$defs"]
    actual = schema["components"]["schemas"]
    pairs = {
        "CreateGenerationJobRequest": "CreateGenerationJobRequest",
        "ExecuteGenerationJobRequest": "ExecuteGenerationJobRequest",
        "RetryGenerationJobRequest": "RetryGenerationJobRequest",
        "GenerationJobLineage": "GenerationJobLineage",
        "GenerationContextProvenanceResource": ("GenerationContextProvenanceResource"),
        "GenerationFailureResource": "GenerationFailureResource",
        "GenerationContextSourceResource": "GenerationContextSourceResource",
        "GenerationContextItemResource": "GenerationContextItemResource",
        "GenerationJobResource": "GenerationJobResource",
        "GenerationJobDetailResource": "GenerationJobDetailResource",
        "GenerationJobPageResponse": "GenerationJobPageResponse",
    }
    for public_name, actual_name in pairs.items():
        assert set(actual[actual_name].get("properties", {})) == set(
            public[public_name].get("properties", {})
        )
        assert set(actual[actual_name].get("required", [])) == set(
            public[public_name].get("required", [])
        )

    apply_pairs = {
        "ApplyGenerationJobRequest": "ApplyGenerationJobRequest",
        "GenerationPlacement": "GenerationPlacementRequest",
        "CategoryPlacement": "GenerationCategoryPlacementRequest",
        "ReuseParentPlacement": "ReuseGenerationParentPlacementRequest",
        "CreateParentPlacement": "CreateGenerationParentPlacementRequest",
        "CreateCasePlacement": "CreateGenerationCasePlacementRequest",
        "ReuseCasePlacement": "ReuseGenerationCasePlacementRequest",
        "AppliedTestStep": "AppliedTestStepResource",
        "GenerationSourceProvenance": "GenerationSourceProvenanceResource",
        "AppliedTestCaseVersion": "AppliedTestCaseVersionResource",
        "CreatedTestCaseResource": "CreatedAppliedTestCaseResource",
        "ReusedCaseReference": "ReusedAppliedTestCaseResource",
        "CreatedCaseOutcome": "CreatedGenerationCaseOutcomeResource",
        "ReusedCaseOutcome": "ReusedGenerationCaseOutcomeResource",
        "CreatedParentOutcome": "CreatedGenerationParentOutcomeResource",
        "ReusedParentOutcome": "ReusedGenerationParentOutcomeResource",
        "ApplyCounts": "GenerationApplyCountsResource",
        "GenerationApplyReceipt": "GenerationApplyReceiptResource",
    }
    apply_public = json.loads(
        (ROOT / "packages/contracts/generation/apply/1.0.0/schema.json").read_text()
    )["$defs"]
    for public_name, actual_name in apply_pairs.items():
        assert set(actual[actual_name].get("properties", {})) == set(
            apply_public[public_name].get("properties", {})
        )
        assert set(actual[actual_name].get("required", [])) == set(
            apply_public[public_name].get("required", [])
        )

    comparison_operation = schema["paths"]["/api/generation-jobs/{jobId}/comparison"][
        "get"
    ]
    assert set(comparison_operation["responses"]) == {"200", "404", "409", "422"}
    assert comparison_operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/GenerationComparisonResponse"}
    comparison_public = json.loads(
        (
            ROOT / "packages/contracts/generation/comparison/1.0.0/schema.json"
        ).read_text()
    )["$defs"]
    comparison_pairs = {
        "GenerationComparisonResponse": "GenerationComparisonResponse",
        "ComparisonJobResource": "ComparisonJobResource",
        "ComparisonReadPolicy": "ComparisonReadPolicyResource",
        "SourceReference": "ComparisonSourceReferenceResource",
        "SlackStructuredField": "SlackStructuredFieldResource",
        "SlackRevisionEvidence": "SlackRevisionEvidenceResource",
        "SlackSourceComparison": "SlackSourceComparisonResource",
        "ActiveKnowledgeComparison": "ActiveKnowledgeComparisonResource",
        "ExistingCaseComparison": "ExistingCaseComparisonResource",
        "ExistingCaseContent": "ExistingCaseContentResource",
        "ExistingCaseStep": "ExistingCaseStepResource",
        "SupplementalEvidenceComparison": "SupplementalEvidenceComparisonResource",
        "GeneratedCandidateComparison": "GeneratedCandidateComparisonResource",
        "CandidateReferenceEdge": "CandidateReferenceEdgeResource",
        "ResolvedReferenceTarget": "ResolvedCandidateReferenceTargetResource",
        "UnavailableReferenceTarget": "UnavailableCandidateReferenceTargetResource",
        "ApplyReceiptHeader": "ApplyReceiptHeaderResource",
    }
    for public_name, actual_name in comparison_pairs.items():
        assert set(actual[actual_name].get("properties", {})) == set(
            comparison_public[public_name].get("properties", {})
        )
        assert set(actual[actual_name].get("required", [])) == set(
            comparison_public[public_name].get("required", [])
        )

    generation_output_public = json.loads(
        (
            ROOT / "packages/contracts/ai-test-case-generation-output.schema.json"
        ).read_text()
    )["$defs"]
    generation_output_pairs = {
        "TestStep": "GeneratedTestStepResource",
        "GeneratedTestCase": "GeneratedTestCaseResource",
        "CategoryAlternative": "GenerationCategoryAlternativeResource",
        "CategoryCandidate": "GenerationCategoryCandidateResource",
    }
    for public_name, actual_name in generation_output_pairs.items():
        assert set(actual[actual_name].get("properties", {})) == set(
            generation_output_public[public_name].get("properties", {})
        )
        assert set(actual[actual_name].get("required", [])) == set(
            generation_output_public[public_name].get("required", [])
        )
    assert actual["GeneratedCandidateComparisonResource"]["properties"][
        "candidate"
    ] == {"$ref": "#/components/schemas/GeneratedTestCaseResource"}
    assert actual["GenerationResultComparisonResource"]["properties"][
        "categoryCandidate"
    ]["anyOf"][0] == {
        "$ref": "#/components/schemas/GenerationCategoryCandidateResource"
    }
    assert set(
        actual["UnavailableCandidateReferenceTargetResource"]["properties"][
            "sourceKind"
        ]["enum"]
    ) == set(comparison_public["ReferenceSourceKind"]["enum"])
    assert (
        "UNKNOWN"
        not in actual["ResolvedCandidateReferenceTargetResource"]["properties"][
            "sourceKind"
        ]["enum"]
    )

    reconstruction_operation = schema["paths"][
        "/api/generation-jobs/{jobId}/reconstruction"
    ]["get"]
    assert set(reconstruction_operation["responses"]) == {"200", "404", "422"}
    assert reconstruction_operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/GenerationReconstructionResponse"}
    reconstruction_public = json.loads(
        (
            ROOT / "packages/contracts/generation/reconstruction/1.0.0/schema.json"
        ).read_text()
    )["$defs"]
    reconstruction_pairs = {
        "InputDigestProvenance": "GenerationInputDigestResource",
        "ReconstructionReadPolicy": "GenerationReconstructionReadPolicyResource",
    }
    for public_name, actual_name in reconstruction_pairs.items():
        assert set(actual[actual_name].get("properties", {})) == set(
            reconstruction_public[public_name].get("properties", {})
        )
        assert set(actual[actual_name].get("required", [])) == set(
            reconstruction_public[public_name].get("required", [])
        )
    public_length = reconstruction_public["InputDigestProvenance"]["properties"][
        "canonicalByteLength"
    ]
    api_length = actual["GenerationInputDigestResource"]["properties"][
        "canonicalByteLength"
    ]
    assert public_length["maximum"] == GENERATION_INPUT_CAPTURE_LIMIT
    assert api_length["maximum"] == GENERATION_INPUT_CAPTURE_LIMIT

    input_public_document = json.loads(
        (ROOT / "packages/contracts/generation/input/1.0.0/schema.json").read_text()
    )
    input_public_definitions = input_public_document["$defs"]
    input_surface_pairs = {
        "GenerationInputV1": input_public_document,
        "SlackWorkItemInput": input_public_definitions["SlackWorkItem"],
        "ActiveKnowledgeInput": input_public_definitions["ActiveKnowledgeItem"],
        "ExistingCaseStepInput": input_public_definitions["ExistingCaseStep"],
        "ExistingCaseInput": input_public_definitions["ExistingCase"],
        "FailedOrBlockedExecutionInput": input_public_definitions[
            "FailedOrBlockedExecution"
        ],
        "OpenIssueInput": input_public_definitions["OpenIssue"],
        "ExtraSourceInput": input_public_definitions["ExtraSource"],
        "CategoryCandidateInput": input_public_definitions["CategoryCandidate"],
        "GenerationBatchInput": input_public_definitions["GenerationBatch"],
        "GenerationPolicyInput": input_public_definitions["GenerationPolicy"],
    }
    for actual_name, public_definition in input_surface_pairs.items():
        assert set(actual[actual_name].get("properties", {})) == set(
            public_definition.get("properties", {})
        )
        assert set(actual[actual_name].get("required", [])) == set(
            public_definition.get("required", [])
        )
    state_enums = json.loads(
        (ROOT / "packages/contracts/state-enums.schema.json").read_text()
    )["$defs"]
    assert set(
        actual["FailedOrBlockedExecutionInput"]["properties"]["platform"]["enum"]
    ) == set(state_enums["ExecutionPlatform"]["enum"])
    assert actual["SlackWorkItemInput"]["properties"]["permalink"]["format"] == ("uri")
    assert actual["GenerationReconstructionResponse"]["properties"]["input"]["anyOf"][
        0
    ] == {"$ref": "#/components/schemas/GenerationInputV1"}
