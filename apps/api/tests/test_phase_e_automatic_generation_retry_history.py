"""Normal regression coverage for the PE-SE2-T07 production seams."""

from __future__ import annotations

import importlib.util
import inspect

from tc_api.main import create_app
from tc_backend.db import models as db_models
from tc_worker import main as worker_main


def test_retry_history_domain_service_exists() -> None:
    assert importlib.util.find_spec("tc_backend.generation.retry_history") is not None


def test_failure_queue_router_exists() -> None:
    assert importlib.util.find_spec("tc_api.routers.generation_failures") is not None


def test_retry_authority_orm_models_exist() -> None:
    required = {
        "GenerationExecutionSchedule",
        "GenerationAttemptEvent",
        "GenerationProviderCallPermit",
        "GenerationProviderCallObservation",
        "GenerationRetryRequest",
        "GenerationRetryExhaustion",
        "GenerationMaterializationSchedule",
    }
    assert not {name for name in required if not hasattr(db_models, name)}


def test_failure_queue_openapi_operations_exist() -> None:
    document = create_app(validate_database=False).openapi()
    expected = {
        ("get", "/api/generation-failures"): "list_generation_failures",
        ("get", "/api/generation-failures/{failed_job_id}"): "get_generation_failure",
        (
            "get",
            "/api/generation-failures/{failed_job_id}/attempt-history",
        ): "get_generation_attempt_history",
        (
            "post",
            "/api/generation-failures/{failed_job_id}/retry",
        ): "retry_automatic_generation_failure",
    }
    for (method, path), operation_id in expected.items():
        assert document["paths"][path][method]["operationId"] == operation_id


def test_worker_registers_owned_automatic_execution_seam() -> None:
    assert hasattr(worker_main, "AUTOMATIC_GENERATION_EXECUTE_JOB_TYPE")
    assert hasattr(worker_main, "build_automatic_generation_execution_handler")
    assert "generation_execution_runner" in inspect.signature(
        worker_main.build_worker
    ).parameters
