"""FastAPI application entrypoint."""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from tc_api.observability import collect_database_metrics, probe_database
from tc_api.object_storage_runtime import ObjectStorageRuntime
from tc_api.quality_review_runtime import QualityReviewRuntime
from tc_api.slack_runtime import SlackApiRuntime
from tc_api.errors import register_exception_handlers
from tc_api.models import HealthResponse
from tc_api.routers import (
    audit_router,
    cases_router,
    cycles_router,
    folders_router,
    generation_failures_router,
    generation_quality_review_router,
    generation_router,
    generation_suggestions_router,
    issues_router,
    knowledge_router,
    object_storage_router,
    reports_router,
    slack_router,
    workspace_router,
    work_items_router,
)
from tc_backend.config import load_settings
from tc_backend.knowledge.embedding import (
    BgeM3OllamaEmbeddingProvider,
    EmbeddingProvider,
    HttpxOllamaEmbeddingTransport,
)
from tc_backend.knowledge.lightrag import LightRAGIndex
from tc_backend.knowledge.lightrag_composition import (
    build_lightrag_generation_runtime_factory,
)
from tc_backend.knowledge.lightrag_runtime import (
    LightRAGGenerationRuntimeFactory,
    LightRAGRuntimeResolver,
    build_lightrag_runtime_resolver,
)
from tc_backend.observability import (
    MetricsRegistry,
    api_component,
    http_method,
    http_status_class,
    render_registries,
)
from tc_backend.startup import validate_startup
from tc_backend.generation.lifecycle import GenerationJobRunner
from tc_backend.generation.application import GenerationApplyFault

API_VERSION = "0.1.0"
_API_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
    "form-action 'none'; object-src 'none'"
)


def _apply_api_security_headers(response: Response, *, path: str) -> None:
    headers = {
        "Permissions-Policy": (
            "camera=(), display-capture=(), geolocation=(), microphone=(), "
            "payment=(), usb=()"
        ),
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
    }
    if path.startswith("/api/") or path == "/integrations/slack/events":
        headers["Cache-Control"] = "no-store"
        headers["Content-Security-Policy"] = _API_CONTENT_SECURITY_POLICY
    for name, value in headers.items():
        if name not in response.headers:
            response.headers[name] = value


def create_app(
    *,
    validate_database: bool = True,
    database_engine: Engine | None = None,
    object_storage_runtime: ObjectStorageRuntime | None = None,
    slack_runtime: SlackApiRuntime | None = None,
    knowledge_embedding_provider: EmbeddingProvider | None = None,
    knowledge_light_rag: LightRAGIndex | None = None,
    knowledge_light_rag_runtime_factory: LightRAGGenerationRuntimeFactory | None = None,
    generation_runner: GenerationJobRunner | None = None,
    generation_apply_fault: GenerationApplyFault | None = None,
    quality_review_runtime: QualityReviewRuntime | None = None,
    public_web_base_url: str | None = None,
    metrics_registry: MetricsRegistry | None = None,
    observability_requirements: Mapping[str, bool] | None = None,
) -> FastAPI:
    if (
        knowledge_light_rag is not None
        and knowledge_light_rag_runtime_factory is not None
    ):
        raise ValueError(
            "inject either a test LightRAG index or a generation runtime factory"
        )
    active_metrics = metrics_registry or MetricsRegistry()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        active_engine = database_engine
        active_knowledge_embedding = knowledge_embedding_provider
        active_knowledge_light_rag = knowledge_light_rag
        active_light_rag_factory = knowledge_light_rag_runtime_factory
        active_quality_review_runtime = quality_review_runtime
        owned_engine: Engine | None = None
        try:
            if validate_database:
                settings = load_settings()
                settings_light_rag_factory = (
                    build_lightrag_generation_runtime_factory(settings)
                )
                if (
                    settings_light_rag_factory is not None
                    and (
                        active_light_rag_factory is not None
                        or active_knowledge_light_rag is not None
                    )
                ):
                    raise ValueError(
                        "injecting a LightRAG runtime conflicts with enabled settings"
                    )
                active_light_rag_factory = (
                    active_light_rag_factory or settings_light_rag_factory
                )
                capabilities = await asyncio.to_thread(validate_startup, settings)
                application.state.database_capabilities = capabilities
                if active_engine is None:
                    owned_engine = create_engine(
                        settings.sqlalchemy_database_url, pool_pre_ping=True
                    )
                    active_engine = owned_engine
                application.state.object_storage_runtime = (
                    object_storage_runtime
                    or ObjectStorageRuntime.from_settings(settings)
                )
                application.state.slack_runtime = (
                    slack_runtime or SlackApiRuntime.from_settings(settings)
                )
                if active_knowledge_embedding is None:
                    active_knowledge_embedding = BgeM3OllamaEmbeddingProvider(
                        HttpxOllamaEmbeddingTransport(),
                        host=str(settings.ollama_base_url),
                        model=settings.embedding_model,
                        dimension=settings.embedding_dimension,
                    )
                active_public_web_base_url = public_web_base_url or (
                    str(settings.public_web_base_url)
                    if settings.public_web_base_url is not None
                    else None
                )
                if active_quality_review_runtime is None:
                    active_quality_review_runtime = QualityReviewRuntime.from_settings(
                        settings
                    )
                dependency_requirements = {
                    "generation": settings.automatic_generation_enabled,
                    "knowledge": settings.automatic_knowledge_projection_enabled,
                    "lightrag": settings.light_rag_enabled,
                    "object_storage": settings.object_storage_enabled,
                    "slack": settings.slack_mode.value != "disabled",
                }
                dependency_configured = {
                    "generation": settings.automatic_generation_enabled,
                    "knowledge": settings.automatic_knowledge_projection_enabled,
                    "lightrag": (
                        active_light_rag_factory is not None
                        or active_knowledge_light_rag is not None
                    ),
                }
            else:
                application.state.database_capabilities = None
                application.state.object_storage_runtime = (
                    object_storage_runtime or ObjectStorageRuntime()
                )
                application.state.slack_runtime = (
                    slack_runtime or SlackApiRuntime.disabled()
                )
                active_public_web_base_url = public_web_base_url
                dependency_requirements = {
                    "generation": False,
                    "knowledge": False,
                    "lightrag": False,
                    "object_storage": False,
                    "slack": False,
                }
                dependency_configured = {
                    "generation": generation_runner is not None,
                    "knowledge": active_knowledge_embedding is not None,
                    "lightrag": (
                        active_light_rag_factory is not None
                        or active_knowledge_light_rag is not None
                    ),
                }
            if observability_requirements is not None:
                dependency_requirements.update(
                    {
                        name: bool(required)
                        for name, required in observability_requirements.items()
                        if name in dependency_requirements
                    }
                )
            application.state.sessions = (
                sessionmaker(active_engine, expire_on_commit=False, class_=Session)
                if active_engine is not None
                else None
            )
            if active_light_rag_factory is not None:
                if active_engine is None:
                    raise RuntimeError(
                        "LightRAG generation routing requires the TC PostgreSQL engine"
                    )
                active_knowledge_light_rag = build_lightrag_runtime_resolver(
                    active_engine, active_light_rag_factory
                )
            application.state.knowledge_embedding_provider = active_knowledge_embedding
            application.state.knowledge_light_rag = active_knowledge_light_rag
            application.state.generation_runner = generation_runner
            application.state.generation_apply_fault = generation_apply_fault
            application.state.quality_review_runtime = active_quality_review_runtime
            application.state.public_web_base_url = active_public_web_base_url
            application.state.metrics = active_metrics
            application.state.observability_requirements = dependency_requirements
            application.state.observability_configured = dependency_configured
            active_metrics.set_gauge("tc_api_up", 1)
            yield
        finally:
            active_metrics.set_gauge("tc_api_up", 0)
            if isinstance(active_knowledge_light_rag, LightRAGRuntimeResolver):
                await active_knowledge_light_rag.finalize()
            if owned_engine is not None:
                owned_engine.dispose()

    application = FastAPI(
        title="TC API",
        summary="Test-case management platform API",
        version=API_VERSION,
        openapi_url="/api/openapi.json",
        docs_url="/docs",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(application)

    @application.middleware("http")
    async def observe_requests(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        component = api_component(request.url.path)
        method = http_method(request.method)
        started_at = time.perf_counter()
        try:
            response = await call_next(request)
        except BaseException:
            active_metrics.inc(
                "tc_api_http_requests_total",
                component=component,
                method=method,
                status_class="5xx",
            )
            active_metrics.observe(
                "tc_api_http_request_duration_seconds",
                time.perf_counter() - started_at,
                component=component,
                method=method,
            )
            raise
        active_metrics.inc(
            "tc_api_http_requests_total",
            component=component,
            method=method,
            status_class=http_status_class(response.status_code),
        )
        active_metrics.observe(
            "tc_api_http_request_duration_seconds",
            time.perf_counter() - started_at,
            component=component,
            method=method,
        )
        return response

    @application.middleware("http")
    async def trace_requests(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        candidate = request.headers.get("X-Trace-Id", "")
        request.state.trace_id = (
            candidate
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,127}", candidate)
            else f"trace_{uuid.uuid4().hex}"
        )
        response = await call_next(request)
        response.headers["X-Trace-Id"] = request.state.trace_id
        _apply_api_security_headers(response, path=request.url.path)
        return response

    @application.get(
        "/api/health",
        operation_id="getHealth",
        response_model=HealthResponse,
        tags=["system"],
    )
    async def health() -> HealthResponse:
        return HealthResponse(service="api", status="ok", version=API_VERSION)

    def readiness_observation(
        *, database_up: bool | None = None
    ) -> tuple[dict[str, object], int]:
        sessions: sessionmaker[Session] | None = getattr(
            application.state, "sessions", None
        )
        database_ready = (
            probe_database(sessions) if database_up is None else database_up
        )
        requirements: dict[str, bool] = getattr(
            application.state, "observability_requirements", {}
        )
        configured: dict[str, bool] = getattr(
            application.state, "observability_configured", {}
        )
        runtime: ObjectStorageRuntime = application.state.object_storage_runtime
        slack: SlackApiRuntime = application.state.slack_runtime
        ready = {
            "database": database_ready,
            "generation": (
                not requirements.get("generation", False)
                or configured.get("generation", False)
            ),
            "knowledge": (
                not requirements.get("knowledge", False)
                or configured.get("knowledge", False)
                and application.state.knowledge_embedding_provider is not None
            ),
            "lightrag": (
                not requirements.get("lightrag", False)
                or configured.get("lightrag", False)
                and application.state.knowledge_light_rag is not None
                and (
                    not isinstance(
                        application.state.knowledge_light_rag,
                        LightRAGRuntimeResolver,
                    )
                    or application.state.knowledge_light_rag.active_generation_ready()
                )
            ),
            "object_storage": (
                not requirements.get("object_storage", False)
                or runtime.adapter is not None
                and runtime.scanner is not None
            ),
            "slack": (
                not requirements.get("slack", False)
                or slack.state.value == "configured"
            ),
        }
        for dependency, is_ready in ready.items():
            active_metrics.set_gauge(
                "tc_dependency_ready", int(is_ready), dependency=dependency
            )
        degradation_reasons = [
            f"{dependency.upper()}_UNAVAILABLE"
            for dependency in (
                "generation",
                "knowledge",
                "lightrag",
                "object_storage",
                "slack",
            )
            if requirements.get(dependency, False) and not ready[dependency]
        ]
        if not database_ready:
            status_value = "not_ready"
            status_code = 503
            degradation_reasons.insert(0, "DATABASE_UNAVAILABLE")
        elif degradation_reasons:
            status_value = "degraded"
            status_code = 200
        else:
            status_value = "ready"
            status_code = 200
        active_metrics.set_gauge("tc_api_ready", int(database_ready))
        active_metrics.set_gauge("tc_api_degraded", int(bool(degradation_reasons)))
        dependency_states = {
            name: (
                "ready"
                if is_ready and requirements.get(name, name == "database")
                else "disabled"
                if is_ready
                else "unavailable"
            )
            for name, is_ready in ready.items()
        }
        return (
            {
                "service": "api",
                "status": status_value,
                "version": API_VERSION,
                "dependencies": dependency_states,
                "degradationReasons": degradation_reasons,
            },
            status_code,
        )

    @application.get("/api/ready", include_in_schema=False)
    def readiness() -> JSONResponse:
        payload, status_code = readiness_observation()
        return JSONResponse(
            payload,
            status_code=status_code,
            headers={"Cache-Control": "no-store"},
        )

    @application.get("/api/metrics", include_in_schema=False)
    def metrics() -> PlainTextResponse:
        sessions: sessionmaker[Session] | None = getattr(
            application.state, "sessions", None
        )
        database_metrics, database = collect_database_metrics(sessions)
        readiness_observation(database_up=database.up)
        runtime: ObjectStorageRuntime = application.state.object_storage_runtime
        storage = runtime.observation(storage_bytes=database.storage_bytes)
        storage_ready = runtime.adapter is not None and runtime.scanner is not None
        active_metrics.set_gauge("tc_object_storage_ready", int(storage_ready))
        active_metrics.set_counter(
            "tc_object_storage_requests_total",
            storage.class_a_requests,
            **{"class": "A"},
        )
        active_metrics.set_counter(
            "tc_object_storage_requests_total",
            storage.class_b_requests,
            **{"class": "B"},
        )
        for resource, numerator, denominator in (
            ("storage_bytes", storage.storage_bytes, storage.storage_budget_bytes),
            (
                "class_a_requests",
                storage.class_a_requests,
                storage.class_a_request_budget,
            ),
            (
                "class_b_requests",
                storage.class_b_requests,
                storage.class_b_request_budget,
            ),
        ):
            active_metrics.set_gauge(
                "tc_object_storage_budget_ratio",
                numerator / denominator,
                resource=resource,
            )
        return PlainTextResponse(
            render_registries(active_metrics, database_metrics),
            media_type="text/plain; version=0.0.4; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    application.include_router(workspace_router)
    application.include_router(audit_router)
    application.include_router(folders_router)
    application.include_router(generation_router)
    application.include_router(generation_failures_router)
    application.include_router(generation_quality_review_router)
    application.include_router(generation_suggestions_router)
    application.include_router(cases_router)
    application.include_router(work_items_router)
    application.include_router(cycles_router)
    application.include_router(issues_router)
    application.include_router(knowledge_router)
    application.include_router(object_storage_router)
    application.include_router(reports_router)
    application.include_router(slack_router)

    return application


app = create_app()
