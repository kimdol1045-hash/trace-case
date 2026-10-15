"""PE-SE2-T07 focused RED: production seams intentionally do not exist yet.

This file is deliberately named ``red_*.py`` so the default ``test*.py``
collection remains green. Run this exact file after implementing T07.
"""

from __future__ import annotations

import importlib.util
import inspect

from tc_api.main import create_app
from tc_backend.db import models as db_models
from tc_worker import main as worker_main


def test_retry_history_domain_service_exists() -> None:
    assert importlib.util.find_spec("tc_backend.generation.retry_history") is not None, (
        "RED T07: add the automatic retry/exhaustion/history service"
    )


def test_failure_queue_router_exists() -> None:
    assert importlib.util.find_spec("tc_api.routers.generation_failures") is not None, (
        "RED T07: add the authenticated failure queue/detail/retry router"
    )


def test_retry_authority_orm_models_exist() -> None:
    required_models = {
        "GenerationExecutionSchedule",
        "GenerationAttemptEvent",
        "GenerationProviderCallPermit",
        "GenerationProviderCallObservation",
        "GenerationRetryRequest",
        "GenerationRetryExhaustion",
        "GenerationMaterializationSchedule",
    }
    missing = sorted(name for name in required_models if not hasattr(db_models, name))
    assert not missing, f"RED T07: add production ORM models: {', '.join(missing)}"


def test_failure_queue_openapi_operations_exist() -> None:
    document = create_app(validate_database=False).openapi()
    required_operations = {
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
    missing = []
    for (method, path), operation_id in required_operations.items():
        operation = document.get("paths", {}).get(path, {}).get(method)
        if operation is None or operation.get("operationId") != operation_id:
            missing.append(f"{method.upper()} {path} ({operation_id})")
    assert not missing, f"RED T07: add production OpenAPI operations: {', '.join(missing)}"


def test_worker_registers_automatic_generation_execution_handler() -> None:
    required_symbols = {
        "AUTOMATIC_GENERATION_EXECUTE_JOB_TYPE",
        "build_automatic_generation_execution_handler",
    }
    missing = sorted(name for name in required_symbols if not hasattr(worker_main, name))
    signature = inspect.signature(worker_main.build_worker)
    if "generation_execution_runner" not in signature.parameters:
        missing.append("build_worker(..., generation_execution_runner=...) seam")
    assert not missing, f"RED T07: wire the production worker: {', '.join(missing)}"

