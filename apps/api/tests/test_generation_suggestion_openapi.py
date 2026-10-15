from __future__ import annotations

from tc_api.main import create_app


def _schema() -> dict[str, object]:
    return create_app(validate_database=False).openapi()


def test_pe_se2_t05_openapi_exposes_read_apply_ignore_but_no_public_create() -> None:
    """RED until the additive T05 FastAPI surface exists."""

    schema = _schema()
    paths = schema["paths"]
    operations = {
        "/api/test-cases/{caseId}/generation-suggestions": {
            "get": "listTestCaseGenerationSuggestions"
        },
        "/api/generation-suggestions/{suggestionId}": {
            "get": "getGenerationSuggestion"
        },
        "/api/generation-suggestions/{suggestionId}/apply": {
            "post": "applyGenerationSuggestion"
        },
        "/api/generation-suggestions/{suggestionId}/ignore": {
            "post": "ignoreGenerationSuggestion"
        },
    }
    for path, methods in operations.items():
        assert path in paths, f"PE-SE2-T05 route missing: {path}"
        assert set(paths[path]) == set(methods)
        for method, operation_id in methods.items():
            assert paths[path][method]["operationId"] == operation_id

    collection = paths["/api/test-cases/{caseId}/generation-suggestions"]
    assert "post" not in collection
    detail = paths["/api/generation-suggestions/{suggestionId}"]
    assert not ({"post", "put", "patch", "delete"} & detail.keys())

    for path in (
        "/api/generation-suggestions/{suggestionId}/apply",
        "/api/generation-suggestions/{suggestionId}/ignore",
    ):
        operation = paths[path]["post"]
        header = next(
            parameter
            for parameter in operation["parameters"]
            if parameter["name"] == "Idempotency-Key"
        )
        assert header["required"] is True
        assert set(operation["responses"]) == {"200", "404", "409", "422", "500"}


def test_pe_se2_t05_openapi_fixes_immutable_diff_and_resolution_shapes() -> None:
    """The generated client must receive the exact T05 resource vocabulary."""

    schemas = _schema()["components"]["schemas"]
    expected_properties = {
        "GenerationSuggestionResource": {
            "id",
            "testCaseId",
            "generationLineageId",
            "state",
            "baseline",
            "proposal",
            "diff",
            "source",
            "resolution",
            "supersededBySuggestionId",
            "createdAt",
        },
        "GenerationSuggestionBaselineResource": {
            "caseResourceVersion",
            "caseVersionId",
            "caseVersion",
            "snapshot",
            "digest",
        },
        "GenerationSuggestionDiffResource": {
            "schemaVersion",
            "digest",
            "changedFields",
            "changes",
        },
        "GenerationSuggestionSourceResource": {
            "suggestionTargetId",
            "generationJobId",
            "rootGenerationJobId",
            "generationChangeAssessmentId",
            "slackWorkItemId",
            "sourceVersion",
            "generationVersion",
            "generatedCaseIndex",
            "contextDigest",
            "resultDigest",
            "exactContentKey",
        },
        "GenerationSuggestionResolutionResource": {
            "id",
            "decision",
            "actor",
            "reason",
            "observedCaseResourceVersion",
            "observedCaseVersionId",
            "appliedCaseVersionId",
            "auditId",
            "decidedAt",
        },
        "ApplyGenerationSuggestionRequest": {
            "idempotencyKey",
            "expectedCaseResourceVersion",
            "expectedCaseVersionId",
            "changeReason",
        },
        "IgnoreGenerationSuggestionRequest": {"idempotencyKey", "reason"},
    }
    for name, properties in expected_properties.items():
        assert name in schemas, f"PE-SE2-T05 OpenAPI component missing: {name}"
        assert set(schemas[name]["properties"]) == properties

    suggestion = schemas["GenerationSuggestionResource"]
    assert set(suggestion["properties"]["state"]["enum"]) == {
        "OPEN",
        "APPLIED",
        "IGNORED",
        "SUPERSEDED",
    }
    resolution = schemas["GenerationSuggestionResolutionResource"]
    assert set(resolution["properties"]["decision"]["enum"]) == {
        "APPLIED",
        "IGNORED",
    }


def test_pe_se2_t05_ordinary_generation_apply_remains_created_or_reused_only() -> None:
    """A suggestion target job is a separate protocol, never an apply outcome union."""

    schemas = _schema()["components"]["schemas"]
    assert "SuggestedGenerationCaseOutcomeResource" not in schemas
    apply_receipt = schemas["GenerationApplyReceiptResource"]
    case_items = apply_receipt["properties"]["cases"]["items"]["oneOf"]
    assert {
        item["$ref"].removeprefix("#/components/schemas/") for item in case_items
    } == {
        "CreatedGenerationCaseOutcomeResource",
        "ReusedGenerationCaseOutcomeResource",
    }
    counts = schemas["GenerationApplyCountsResource"]
    assert set(counts["properties"]) == {"generated", "created", "reused"}
