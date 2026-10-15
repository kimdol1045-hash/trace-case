"""Production-composition adapter for the guarded Phase F projection rebuild."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from tc_backend.config import load_settings
from tc_backend.worker.knowledge_consumer import KnowledgeOutboxConsumer
from tc_worker.main import build_automatic_knowledge_consumer


def build_consumer(
    session_factory: sessionmaker[Session],
) -> KnowledgeOutboxConsumer:
    """Build the same opt-in Ollama consumer used by the worker entrypoint."""

    consumer = build_automatic_knowledge_consumer(
        load_settings(),
        session_factory=session_factory,
    )
    if consumer is None:
        raise RuntimeError(
            "TC_AUTOMATIC_KNOWLEDGE_PROJECTION_ENABLED must be true for a rebuild"
        )
    return consumer
