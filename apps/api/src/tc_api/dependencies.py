"""FastAPI dependencies for request-scoped database sessions."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from tc_api.object_storage_runtime import ObjectStorageRuntime
from tc_api.quality_review_runtime import QualityReviewRuntime
from tc_api.slack_runtime import SlackApiRuntime
from tc_backend.knowledge.embedding import EmbeddingProvider
from tc_backend.knowledge.lightrag import LightRAGIndex
from tc_backend.generation.lifecycle import GenerationJobRunner
from tc_backend.generation.application import GenerationApplyFault


class DatabaseUnavailableError(RuntimeError):
    pass


def get_session(request: Request) -> Iterator[Session]:
    sessions: sessionmaker[Session] | None = getattr(
        request.app.state, "sessions", None
    )
    if sessions is None:
        raise DatabaseUnavailableError("database session factory is unavailable")
    with sessions() as session:
        yield session


SessionDependency = Annotated[Session, Depends(get_session)]


def get_object_storage_runtime(request: Request) -> ObjectStorageRuntime:
    runtime = getattr(request.app.state, "object_storage_runtime", None)
    if not isinstance(runtime, ObjectStorageRuntime):
        raise RuntimeError("Object storage runtime is unavailable")
    return runtime


ObjectStorageRuntimeDependency = Annotated[
    ObjectStorageRuntime, Depends(get_object_storage_runtime)
]


def get_slack_runtime(request: Request) -> SlackApiRuntime:
    runtime = getattr(request.app.state, "slack_runtime", None)
    if not isinstance(runtime, SlackApiRuntime):
        raise RuntimeError("Slack runtime is unavailable")
    return runtime


SlackRuntimeDependency = Annotated[SlackApiRuntime, Depends(get_slack_runtime)]


def get_knowledge_embedding_provider(request: Request) -> EmbeddingProvider | None:
    return getattr(request.app.state, "knowledge_embedding_provider", None)


KnowledgeEmbeddingDependency = Annotated[
    EmbeddingProvider | None, Depends(get_knowledge_embedding_provider)
]


def get_knowledge_light_rag(request: Request) -> LightRAGIndex | None:
    return getattr(request.app.state, "knowledge_light_rag", None)


KnowledgeLightRAGDependency = Annotated[
    LightRAGIndex | None, Depends(get_knowledge_light_rag)
]


def get_generation_runner(request: Request) -> GenerationJobRunner | None:
    return getattr(request.app.state, "generation_runner", None)


GenerationRunnerDependency = Annotated[
    GenerationJobRunner | None, Depends(get_generation_runner)
]


def get_generation_apply_fault(request: Request) -> GenerationApplyFault | None:
    return getattr(request.app.state, "generation_apply_fault", None)


GenerationApplyFaultDependency = Annotated[
    GenerationApplyFault | None, Depends(get_generation_apply_fault)
]


def get_quality_review_runtime(request: Request) -> QualityReviewRuntime:
    runtime = getattr(request.app.state, "quality_review_runtime", None)
    if not isinstance(runtime, QualityReviewRuntime):
        from tc_backend.generation.quality_review import QualityReviewError

        raise QualityReviewError(
            "QUALITY_REVIEW_AUTHORITY_NOT_CONFIGURED", status_code=503
        )
    return runtime


QualityReviewRuntimeDependency = Annotated[
    QualityReviewRuntime, Depends(get_quality_review_runtime)
]
