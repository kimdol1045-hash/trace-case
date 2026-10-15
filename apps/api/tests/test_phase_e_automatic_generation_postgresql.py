from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, func, inspect, select, text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tc_api.main import create_app
from tc_backend.db import models as db_models
from tc_backend.config import Settings
from tc_backend.db.jobs import ClaimedJob, JobRepository
from tc_backend.db.models import (
    AuditLog,
    BackgroundJob,
    GenerationContext,
    GenerationJob,
    KnowledgeNode,
    KnowledgeProjectionEvent,
    OutboxEvent,
    SlackParseResult,
    SlackWorkItem,
)
from tc_backend.db.slack import SlackEventInput, SlackRepository
from tc_backend.generation.lifecycle import GenerationJobLifecycle
from tc_backend.knowledge.embedding import EmbeddingBatch
from tc_backend.knowledge.extraction import SlackKnowledgeExtractor
from tc_backend.knowledge.models import (
    KnowledgeNodeType,
    ProviderEvidence,
    ProviderExtraction,
    ProviderKnowledgeCandidate,
    SlackSourceBundle,
)
from tc_backend.worker.knowledge_consumer import (
    AutomaticGenerationReceiptError,
    KnowledgeOutboxConsumer,
)
from tc_backend.repositories.generation_repository import GenerationRepository
from tc_worker.main import Worker
from tc_worker.slack_runtime import SlackWorkerRuntime

pytestmark = pytest.mark.postgres


class _DeterministicEmbedding:
    async def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        vectors: list[tuple[float, ...]] = []
        hashes: list[str] = []
        for text in texts:
            vector = [0.0] * 1024
            vector[0] = 1.0
            vectors.append(tuple(vector))
            hashes.append(hashlib.sha256(text.encode("utf-8")).hexdigest())
        return EmbeddingBatch(
            model="bge-m3:pe-se2-t01-contract",
            dimension=1024,
            vectors=tuple(vectors),
            content_hashes=tuple(hashes),
        )


class _FailingEmbedding:
    async def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        del texts
        raise ConnectionError("injected embedding outage")


class _ForbiddenExtraction:
    async def extract(self, bundle: SlackSourceBundle) -> ProviderExtraction:
        del bundle
        raise AssertionError("an existing projection must not be extracted again")


class _DeterministicExtraction:
    async def extract(self, bundle: SlackSourceBundle) -> ProviderExtraction:
        excerpt = "결제 승인은 실패 잠금 정책을 따라야 한다."
        revision = next(
            revision
            for revision in bundle.revisions
            if revision.body is not None and excerpt in revision.body
        )
        return ProviderExtraction(
            provider="deterministic-pe-se2-t01",
            model="knowledge-contract-v1",
            schema_version="knowledge-extraction-v1",
            candidates=(
                ProviderKnowledgeCandidate(
                    candidate_id="payment-lock-decision",
                    node_type=KnowledgeNodeType.DECISION,
                    content=excerpt,
                    topic_key="payment-lock-policy",
                    confidence=0.99,
                    evidence=(
                        ProviderEvidence(
                            source_revision_id=revision.source_revision_id,
                            excerpt=excerpt,
                        ),
                    ),
                ),
            ),
        )


def _seed_due_slack_source(
    sessions: sessionmaker[Session],
) -> tuple[uuid.UUID, uuid.UUID]:
    message_time = datetime.now(UTC) - timedelta(minutes=11)
    slack_timestamp = f"{int(message_time.timestamp())}.000100"
    with sessions.begin() as session:
        slack = SlackRepository(session)
        slack.put_channel(
            slack_workspace_id="T_PE_SE2_T01",
            channel_id="C_PE_SE2_T01",
            channel_name="automatic-generation-contract",
        )
        ingested = slack.ingest_event(
            SlackEventInput(
                event_id="EvPeSe2T01Root001",
                slack_workspace_id="T_PE_SE2_T01",
                channel_id="C_PE_SE2_T01",
                event_type="ROOT",
                event_ts=slack_timestamp,
                message_ts=slack_timestamp,
                body=(
                    "개발건: 결제 승인\n"
                    "요청 메뉴: 결제 > 승인\n"
                    "기능 설명: 카드 승인 정책\n"
                    "요청 내용: 결제 승인은 실패 잠금 정책을 따라야 한다."
                ),
                author_id="U_PE_SE2_T01",
                permalink=(
                    "https://synthetic.invalid/archives/C_PE_SE2_T01/"
                    f"p{slack_timestamp.replace('.', '')}"
                ),
                raw_payload={"event_id": "EvPeSe2T01Root001", "type": "message"},
            )
        )
        assert ingested.work_item_id is not None
        assert ingested.outbox_id is not None
        return ingested.work_item_id, ingested.outbox_id


def test_quiet_parse_enqueues_explicit_current_projection_handoff(
    clean_api_database: Engine,
) -> None:
    """RED until the parse transaction schedules the T01 projection/capture step."""

    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    work_item_id, _ = _seed_due_slack_source(sessions)
    payload = {"workItemId": str(work_item_id), "sourceVersion": 1}
    slack_runtime = SlackWorkerRuntime(session_factory=sessions)
    slack_runtime.finish_quiet_window(payload)
    slack_runtime.parse_work_item(payload)
    slack_runtime.parse_work_item(payload)

    with sessions() as session:
        parse_outbox_id = session.scalar(
            select(OutboxEvent.id).where(
                OutboxEvent.aggregate_type == "SLACK_WORK_ITEM",
                OutboxEvent.aggregate_id == work_item_id,
                OutboxEvent.aggregate_version == 1,
                OutboxEvent.event_type == "SLACK_PARSE_RESULT_STORED",
            )
        )
        assert parse_outbox_id is not None
        handoffs = tuple(
            session.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.job_type == "SLACK_AUTOMATIC_GENERATION"
                )
            )
        )
        assert len(handoffs) == 1, (
            "PE-SE2-T01 requires the parse transaction to enqueue an explicit "
            "projection/context-capture handoff"
        )
        handoff = handoffs[0]
        assert handoff.payload == {
            "outboxEventId": str(parse_outbox_id),
            "workItemId": str(work_item_id),
            "sourceVersion": 1,
        }
        assert (
            handoff.idempotency_key == f"slack-automatic-generation:{parse_outbox_id}"
        )


def test_quiet_handler_fences_a_superseded_job_attempt(
    clean_api_database: Engine,
) -> None:
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    work_item_id, _ = _seed_due_slack_source(sessions)
    payload = {"workItemId": str(work_item_id), "sourceVersion": 1}
    runtime = SlackWorkerRuntime(session_factory=sessions)
    with sessions.begin() as session:
        current = JobRepository(session).claim_next(
            worker_id="quiet-contract-worker",
            stale_after=timedelta(minutes=5),
            job_types=("SLACK_QUIET_WINDOW",),
        )
        assert current is not None

    stale = ClaimedJob(
        id=uuid.uuid4(),
        job_type="SLACK_QUIET_WINDOW",
        payload=payload,
        attempt=current.attempt,
        max_attempts=current.max_attempts,
        locked_by=current.locked_by,
    )
    runtime.finish_quiet_window(payload, stale)
    with sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(BackgroundJob)
                .where(BackgroundJob.job_type == "WORK_ITEM_PARSE")
            )
            == 0
        )

    runtime.finish_quiet_window(payload, current)
    with sessions.begin() as session:
        assert JobRepository(session).succeed(
            job_id=current.id,
            worker_id=current.locked_by,
            attempt=current.attempt,
        )
    with sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(BackgroundJob)
                .where(BackgroundJob.job_type == "WORK_ITEM_PARSE")
            )
            == 1
        )


def test_configured_worker_drives_quiet_parse_projection_and_queued_job(
    clean_api_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_execution_calls: list[uuid.UUID] = []

    def forbid_provider_execution(
        _lifecycle: GenerationJobLifecycle,
        job_id: uuid.UUID,
    ) -> None:
        provider_execution_calls.append(job_id)
        raise AssertionError("quiet-window orchestration must not execute a provider")

    monkeypatch.setattr(GenerationJobLifecycle, "execute", forbid_provider_execution)
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    work_item_id, _ = _seed_due_slack_source(sessions)
    consumer = KnowledgeOutboxConsumer(
        session_factory=sessions,
        embedding_provider=_DeterministicEmbedding(),
        extractor=SlackKnowledgeExtractor(_DeterministicExtraction()),
    )
    runtime = SlackWorkerRuntime(
        session_factory=sessions,
        automatic_generation_consumer=consumer,
    )
    worker = Worker(
        session_factory=sessions,
        settings=Settings(
            _env_file=None,
            database_url=str(clean_api_database.url),
            encryption_master_key="pe-se2-t01-worker-test-key-000001",
            job_retry_base_seconds=0,
            job_retry_max_seconds=0,
        ),
        worker_id="pe-se2-t01-worker",
        handlers=runtime.handlers(),
    )

    assert worker.run_once()
    assert worker.run_once()
    assert worker.run_once()
    assert not worker.run_once()

    with sessions() as session:
        background = tuple(
            session.scalars(select(BackgroundJob).order_by(BackgroundJob.created_at))
        )
        assert [job.job_type for job in background] == [
            "SLACK_QUIET_WINDOW",
            "WORK_ITEM_PARSE",
            "SLACK_AUTOMATIC_GENERATION",
            "AUTOMATIC_GENERATION_EXECUTE",
        ]
        assert all(job.status == "SUCCEEDED" for job in background[:3])
        assert background[-1].status == "QUEUED"
        generated = session.execute(select(GenerationJob)).scalar_one()
        assert generated.slack_work_item_id == work_item_id
        assert generated.status == "QUEUED"
        assert generated.provider == generated.model == "UNCONFIGURED"
        assert provider_execution_calls == []


def test_quiet_expiry_projection_atomically_seals_one_queued_generation_job(
    clean_api_database: Engine,
) -> None:
    """RED until PE-SE2-T01 joins the existing projection and generation seams."""

    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    work_item_id, _ = _seed_due_slack_source(sessions)
    slack_runtime = SlackWorkerRuntime(session_factory=sessions)
    payload = {"workItemId": str(work_item_id), "sourceVersion": 1}
    slack_runtime.finish_quiet_window(payload)
    slack_runtime.parse_work_item(payload)

    with sessions() as session:
        parse_result = session.scalar(
            select(SlackParseResult).where(
                SlackParseResult.slack_work_item_id == work_item_id,
                SlackParseResult.source_version == 1,
            )
        )
        assert parse_result is not None and parse_result.status == "PARSED"
        assert session.scalar(select(func.count()).select_from(GenerationJob)) == 0
        projection_event_id = session.scalar(
            select(OutboxEvent.id).where(
                OutboxEvent.aggregate_type == "SLACK_WORK_ITEM",
                OutboxEvent.aggregate_id == work_item_id,
                OutboxEvent.aggregate_version == 1,
                OutboxEvent.event_type == "SLACK_PARSE_RESULT_STORED",
            )
        )
        assert projection_event_id is not None

    consumer = KnowledgeOutboxConsumer(
        session_factory=sessions,
        embedding_provider=_DeterministicEmbedding(),
        extractor=SlackKnowledgeExtractor(_DeterministicExtraction()),
    )
    process_automatic_generation = getattr(
        consumer, "process_automatic_generation", None
    )
    assert callable(process_automatic_generation), (
        "PE-SE2-T01 requires a transaction-scoped knowledge projection plus generation "
        "context-capture entrypoint"
    )
    projected = asyncio.run(
        process_automatic_generation(
            outbox_event_id=projection_event_id,
            work_item_id=work_item_id,
            source_version=1,
        )
    )
    assert projected.aggregate_type == "SLACK_WORK_ITEM"
    assert projected.aggregate_version == 1
    assert not projected.stale

    with sessions() as session:
        jobs = tuple(
            session.scalars(select(GenerationJob).order_by(GenerationJob.created_at))
        )
        assert len(jobs) == 1, (
            "PE-SE2-T01 requires successful current Slack projection to append exactly one "
            "automatic generation job"
        )
        job = jobs[0]
        assert job.status == "QUEUED"
        assert job.source_version == 1
        assert job.generation_version == 1
        assert job.provider == "UNCONFIGURED"
        assert job.model == "UNCONFIGURED"
        assert job.context_sealed_at is not None
        assert job.context_item_count > 0
        assert job.context_digest is not None
        assert job.generation_input_capture_state == "SEALED"
        assert job.generation_input_schema_version == "1.0.0"
        assert job.generation_input_digest is not None
        assert job.result is None
        assert job.started_at is None
        assert job.completed_at is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(GenerationContext)
                .where(GenerationContext.generation_job_id == job.id)
            )
            == job.context_item_count
        )
        assert session.scalar(select(func.count()).select_from(db_models.TestCase)) == 0
        checkpoint = session.get(KnowledgeProjectionEvent, projection_event_id)
        assert checkpoint is not None
        assert checkpoint.result["generationJobId"] == str(job.id)
        audits = tuple(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.entity_type == "GENERATION_JOB",
                    AuditLog.entity_id == job.id,
                    AuditLog.action == "AUTOMATIC_GENERATION_JOB_CREATED",
                )
            )
        )
        assert len(audits) == 1
        assert audits[0].idempotency_key == (
            f"slack-automatic-generation:{projection_event_id}"
        )
        generation_job_id = job.id

    replay = asyncio.run(
        process_automatic_generation(
            outbox_event_id=projection_event_id,
            work_item_id=work_item_id,
            source_version=1,
        )
    )
    assert replay.replayed
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(GenerationJob)) == 1
        assert session.get(GenerationJob, generation_job_id) is not None
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "AUTOMATIC_GENERATION_JOB_CREATED")
            )
            == 1
        )


def test_existing_current_projection_is_reused_without_reextracting(
    clean_api_database: Engine,
) -> None:
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    work_item_id, source_outbox_id = _seed_due_slack_source(sessions)
    projection_consumer = KnowledgeOutboxConsumer(
        session_factory=sessions,
        embedding_provider=_DeterministicEmbedding(),
        extractor=SlackKnowledgeExtractor(_DeterministicExtraction()),
    )
    projected = asyncio.run(projection_consumer.process_event(source_outbox_id))
    assert not projected.stale

    payload = {"workItemId": str(work_item_id), "sourceVersion": 1}
    runtime = SlackWorkerRuntime(session_factory=sessions)
    runtime.finish_quiet_window(payload)
    runtime.parse_work_item(payload)
    with sessions() as session:
        outbox_event_id = session.scalar(
            select(OutboxEvent.id).where(
                OutboxEvent.aggregate_id == work_item_id,
                OutboxEvent.event_type == "SLACK_PARSE_RESULT_STORED",
            )
        )
        assert outbox_event_id is not None
    receipt_consumer = KnowledgeOutboxConsumer(
        session_factory=sessions,
        embedding_provider=None,
        extractor=SlackKnowledgeExtractor(_ForbiddenExtraction()),
    )
    replay = asyncio.run(
        receipt_consumer.process_automatic_generation(
            outbox_event_id=outbox_event_id,
            work_item_id=work_item_id,
            source_version=1,
        )
    )
    assert replay is not None and replay.replayed and not replay.stale
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(GenerationJob)) == 1
        checkpoint = session.get(KnowledgeProjectionEvent, source_outbox_id)
        parse_event = session.get(OutboxEvent, outbox_event_id)
        assert checkpoint is not None and "generationJobId" in checkpoint.result
        assert parse_event is not None and parse_event.status == "PUBLISHED"
        assert session.get(KnowledgeProjectionEvent, outbox_event_id) is None


def test_embedding_failure_rolls_back_projection_context_job_and_audit(
    clean_api_database: Engine,
) -> None:
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    work_item_id, _ = _seed_due_slack_source(sessions)
    payload = {"workItemId": str(work_item_id), "sourceVersion": 1}
    runtime = SlackWorkerRuntime(session_factory=sessions)
    runtime.finish_quiet_window(payload)
    runtime.parse_work_item(payload)
    with sessions() as session:
        outbox_event_id = session.scalar(
            select(OutboxEvent.id).where(
                OutboxEvent.aggregate_id == work_item_id,
                OutboxEvent.event_type == "SLACK_PARSE_RESULT_STORED",
            )
        )
        assert outbox_event_id is not None

    consumer = KnowledgeOutboxConsumer(
        session_factory=sessions,
        embedding_provider=_FailingEmbedding(),
        extractor=SlackKnowledgeExtractor(_DeterministicExtraction()),
    )
    with pytest.raises(ConnectionError, match="injected embedding outage"):
        asyncio.run(
            consumer.process_automatic_generation(
                outbox_event_id=outbox_event_id,
                work_item_id=work_item_id,
                source_version=1,
            )
        )

    with sessions() as session:
        event = session.get(OutboxEvent, outbox_event_id)
        assert event is not None
        assert event.status == "FAILED"
        assert event.attempts == 1
        assert "ConnectionError" in event.payload["knowledgeFailure"]
        assert session.scalar(select(func.count()).select_from(KnowledgeNode)) == 0
        assert (
            session.scalar(select(func.count()).select_from(KnowledgeProjectionEvent))
            == 0
        )


def test_post_seal_failure_rolls_back_projection_context_job_and_receipt(
    clean_api_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    work_item_id, _ = _seed_due_slack_source(sessions)
    payload = {"workItemId": str(work_item_id), "sourceVersion": 1}
    runtime = SlackWorkerRuntime(session_factory=sessions)
    runtime.finish_quiet_window(payload)
    runtime.parse_work_item(payload)
    with sessions() as session:
        outbox_event_id = session.scalar(
            select(OutboxEvent.id).where(
                OutboxEvent.aggregate_id == work_item_id,
                OutboxEvent.event_type == "SLACK_PARSE_RESULT_STORED",
            )
        )
        assert outbox_event_id is not None

    create_and_seal = GenerationRepository.get_or_create_automatic_job_with_context

    def fail_after_seal(
        repository: GenerationRepository,
        **kwargs: object,
    ) -> object:
        create_and_seal(repository, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError("injected failure after generation seal")

    monkeypatch.setattr(
        GenerationRepository,
        "get_or_create_automatic_job_with_context",
        fail_after_seal,
    )
    consumer = KnowledgeOutboxConsumer(
        session_factory=sessions,
        embedding_provider=_DeterministicEmbedding(),
        extractor=SlackKnowledgeExtractor(_DeterministicExtraction()),
    )
    with pytest.raises(RuntimeError, match="injected failure after generation seal"):
        asyncio.run(
            consumer.process_automatic_generation(
                outbox_event_id=outbox_event_id,
                work_item_id=work_item_id,
                source_version=1,
            )
        )

    with sessions() as session:
        event = session.get(OutboxEvent, outbox_event_id)
        assert event is not None and event.status == "FAILED"
        assert session.scalar(select(func.count()).select_from(KnowledgeNode)) == 0
        assert (
            session.scalar(select(func.count()).select_from(KnowledgeProjectionEvent))
            == 0
        )
        assert session.scalar(select(func.count()).select_from(GenerationJob)) == 0
        assert session.scalar(select(func.count()).select_from(GenerationContext)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "AUTOMATIC_GENERATION_JOB_CREATED")
            )
            == 0
        )
        assert session.scalar(select(func.count()).select_from(GenerationJob)) == 0
        assert session.scalar(select(func.count()).select_from(GenerationContext)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "AUTOMATIC_GENERATION_JOB_CREATED")
            )
            == 0
        )


def test_stale_automatic_handoff_is_terminal_without_projection_or_job(
    clean_api_database: Engine,
) -> None:
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    work_item_id, _ = _seed_due_slack_source(sessions)
    payload = {"workItemId": str(work_item_id), "sourceVersion": 1}
    runtime = SlackWorkerRuntime(session_factory=sessions)
    runtime.finish_quiet_window(payload)
    runtime.parse_work_item(payload)
    with sessions.begin() as session:
        parse_outbox_id = session.scalar(
            select(OutboxEvent.id).where(
                OutboxEvent.aggregate_id == work_item_id,
                OutboxEvent.event_type == "SLACK_PARSE_RESULT_STORED",
            )
        )
        item = session.get(SlackWorkItem, work_item_id)
        assert parse_outbox_id is not None and item is not None
        revision_time = datetime.now(UTC) - timedelta(minutes=1)
        revision_ts = f"{int(revision_time.timestamp())}.000200"
        SlackRepository(session).ingest_event(
            SlackEventInput(
                event_id="EvPeSe2T01Edit002",
                slack_workspace_id=item.slack_workspace_id,
                channel_id=item.channel_id,
                event_type="EDIT",
                event_ts=revision_ts,
                message_ts=item.message_ts,
                revision_ts=revision_ts,
                body="요청 내용: 변경된 결제 승인 정책",
                author_id="U_PE_SE2_T01",
                permalink=item.permalink,
                raw_payload={"event_id": "EvPeSe2T01Edit002", "type": "message"},
            )
        )

    consumer = KnowledgeOutboxConsumer(
        session_factory=sessions,
        embedding_provider=None,
        extractor=SlackKnowledgeExtractor(_ForbiddenExtraction()),
    )
    assert (
        asyncio.run(
            consumer.process_automatic_generation(
                outbox_event_id=parse_outbox_id,
                work_item_id=work_item_id,
                source_version=1,
            )
        )
        is None
    )
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(GenerationJob)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(KnowledgeProjectionEvent)
                .where(KnowledgeProjectionEvent.aggregate_version == 1)
            )
            == 0
        )


def _complete_current_automatic_generation(
    sessions: sessionmaker[Session],
) -> tuple[KnowledgeOutboxConsumer, uuid.UUID, uuid.UUID, uuid.UUID]:
    work_item_id, _ = _seed_due_slack_source(sessions)
    payload = {"workItemId": str(work_item_id), "sourceVersion": 1}
    runtime = SlackWorkerRuntime(session_factory=sessions)
    runtime.finish_quiet_window(payload)
    runtime.parse_work_item(payload)
    with sessions() as session:
        outbox_event_id = session.scalar(
            select(OutboxEvent.id).where(
                OutboxEvent.aggregate_type == "SLACK_WORK_ITEM",
                OutboxEvent.aggregate_id == work_item_id,
                OutboxEvent.aggregate_version == 1,
                OutboxEvent.event_type == "SLACK_PARSE_RESULT_STORED",
            )
        )
        assert outbox_event_id is not None
    consumer = KnowledgeOutboxConsumer(
        session_factory=sessions,
        embedding_provider=_DeterministicEmbedding(),
        extractor=SlackKnowledgeExtractor(_DeterministicExtraction()),
    )
    asyncio.run(
        consumer.process_automatic_generation(
            outbox_event_id=outbox_event_id,
            work_item_id=work_item_id,
            source_version=1,
        )
    )
    with sessions() as session:
        automatic_job_id = session.scalar(
            select(GenerationJob.id).where(
                GenerationJob.slack_work_item_id == work_item_id
            )
        )
        assert automatic_job_id is not None
    return consumer, work_item_id, outbox_event_id, automatic_job_id


def _remove_exact_generation_receipt(
    sessions: sessionmaker[Session], outbox_event_id: uuid.UUID
) -> None:
    with sessions.begin() as session:
        checkpoint = session.get(KnowledgeProjectionEvent, outbox_event_id)
        assert checkpoint is not None
        checkpoint.result = {
            key: value
            for key, value in checkpoint.result.items()
            if key != "generationJobId"
        }


def test_pe_se2_t02_migration_installs_trigger_and_automatic_source_uniqueness(
    clean_api_database: Engine,
) -> None:
    """RED: T02 needs persisted trigger provenance and an automatic-only backstop."""

    with clean_api_database.connect() as connection:
        columns = {
            column["name"] for column in inspect(connection).get_columns("generation_jobs")
        }
        constraints = set(
            connection.scalars(
                sql_text(
                    "SELECT conname FROM pg_constraint WHERE conrelid IN "
                    "('generation_jobs'::regclass, 'slack_work_items'::regclass)"
                )
            )
        )
        indexes = dict(
            connection.execute(
                sql_text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname = current_schema() "
                    "AND tablename = 'generation_jobs'"
                )
            ).tuples().all()
        )

    assert "generation_trigger" in columns
    assert {
        "uq_slack_work_items_workspace_channel_message",
        "uq_generation_jobs_work_item_generation_version",
        "ck_generation_jobs_generation_trigger_allowed",
    }.issubset(constraints)
    automatic_index = indexes.get("uq_generation_jobs_automatic_source_version")
    assert automatic_index is not None
    assert "UNIQUE INDEX" in automatic_index
    assert "slack_work_item_id, source_version" in automatic_index
    assert "generation_trigger" in automatic_index
    assert "AUTOMATIC" in automatic_index
    assert "retry_of_job_id IS NULL" in automatic_index


def test_pe_se2_t02_db_rejects_a_second_automatic_root_for_one_source_snapshot(
    clean_api_database: Engine,
) -> None:
    """RED: a different generation ordinal must not bypass automatic deduplication."""

    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    _, _, _, automatic_job_id = _complete_current_automatic_generation(sessions)
    with sessions() as session:
        automatic = session.get(GenerationJob, automatic_job_id)
        assert automatic is not None
        duplicate_id = uuid.uuid4()
        duplicate = GenerationJob(
            id=duplicate_id,
            slack_work_item_id=automatic.slack_work_item_id,
            source_version=automatic.source_version,
            generation_version=automatic.generation_version + 100,
            status="QUEUED",
            provider="UNCONFIGURED",
            model="UNCONFIGURED",
            prompt_version=automatic.prompt_version,
            schema_version=automatic.schema_version,
            ontology_schema_id=automatic.ontology_schema_id,
            root_job_id=duplicate_id,
            retry_of_job_id=None,
            attempt_number=1,
            generation_input_capture_state="LEGACY_NOT_CAPTURED",
            generation_trigger="AUTOMATIC",
        )

    with pytest.raises(IntegrityError) as raised:
        with sessions.begin() as session:
            session.add(duplicate)
            session.flush()

    diagnostic = getattr(raised.value.orig, "diag", None)
    assert getattr(diagnostic, "constraint_name", None) == (
        "uq_generation_jobs_automatic_source_version"
    )


def test_pe_se2_t02_missing_exact_receipt_reuses_canonical_automatic_job(
    clean_api_database: Engine,
) -> None:
    """RED: a projection-only/different receipt must find the canonical source job."""

    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    consumer, work_item_id, outbox_event_id, automatic_job_id = (
        _complete_current_automatic_generation(sessions)
    )
    _remove_exact_generation_receipt(sessions, outbox_event_id)

    replay = asyncio.run(
        consumer.process_automatic_generation(
            outbox_event_id=outbox_event_id,
            work_item_id=work_item_id,
            source_version=1,
        )
    )
    assert replay is not None and replay.replayed
    with sessions() as session:
        jobs = tuple(session.scalars(select(GenerationJob)))
        checkpoint = session.get(KnowledgeProjectionEvent, outbox_event_id)
        assert len(jobs) == 1
        assert checkpoint is not None
        assert checkpoint.result["generationJobId"] == str(automatic_job_id)
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "AUTOMATIC_GENERATION_JOB_CREATED")
            )
            == 1
        )


def test_pe_se2_t02_concurrent_missing_receipt_reuses_one_canonical_job(
    clean_api_database: Engine,
) -> None:
    """RED: canonical lookup plus work-item locking must converge under contention."""

    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    consumer, work_item_id, outbox_event_id, automatic_job_id = (
        _complete_current_automatic_generation(sessions)
    )
    _remove_exact_generation_receipt(sessions, outbox_event_id)
    barrier = Barrier(2)

    def replay() -> object:
        barrier.wait(timeout=5)
        return asyncio.run(
            consumer.process_automatic_generation(
                outbox_event_id=outbox_event_id,
                work_item_id=work_item_id,
                source_version=1,
            )
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = tuple(executor.map(lambda _: replay(), range(2)))

    assert len(receipts) == 2
    with sessions() as session:
        checkpoint = session.get(KnowledgeProjectionEvent, outbox_event_id)
        assert session.scalar(select(func.count()).select_from(GenerationJob)) == 1
        assert checkpoint is not None
        assert checkpoint.result["generationJobId"] == str(automatic_job_id)


def test_pe_se2_t02_receipt_repair_rollback_then_retry_reuses_the_same_job(
    clean_api_database: Engine,
) -> None:
    """RED: rollback may retry receipt repair, but must not allocate another job."""

    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    consumer, work_item_id, outbox_event_id, automatic_job_id = (
        _complete_current_automatic_generation(sessions)
    )
    _remove_exact_generation_receipt(sessions, outbox_event_id)
    injected = False

    def fail_first_commit(session: Session) -> None:
        nonlocal injected
        if not injected and session.get_bind() is clean_api_database:
            injected = True
            raise RuntimeError("injected T02 receipt-repair commit failure")

    event.listen(Session, "before_commit", fail_first_commit)
    try:
        with pytest.raises(
            RuntimeError, match="injected T02 receipt-repair commit failure"
        ):
            asyncio.run(
                consumer.process_automatic_generation(
                    outbox_event_id=outbox_event_id,
                    work_item_id=work_item_id,
                    source_version=1,
                )
            )
    finally:
        event.remove(Session, "before_commit", fail_first_commit)

    assert injected
    with sessions() as session:
        checkpoint = session.get(KnowledgeProjectionEvent, outbox_event_id)
        assert session.scalar(select(func.count()).select_from(GenerationJob)) == 1
        assert checkpoint is not None
        assert "generationJobId" not in checkpoint.result

    asyncio.run(
        consumer.process_automatic_generation(
            outbox_event_id=outbox_event_id,
            work_item_id=work_item_id,
            source_version=1,
        )
    )
    with sessions() as session:
        checkpoint = session.get(KnowledgeProjectionEvent, outbox_event_id)
        assert session.scalar(select(func.count()).select_from(GenerationJob)) == 1
        assert checkpoint is not None
        assert checkpoint.result["generationJobId"] == str(automatic_job_id)


def test_pe_se2_t02_manual_generation_remains_repeatable_for_the_same_snapshot(
    clean_api_database: Engine,
) -> None:
    """RED only on missing provenance: manual roots stay outside automatic uniqueness."""

    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    _, work_item_id, _, automatic_job_id = _complete_current_automatic_generation(sessions)
    work_item_api_id = f"slack_work_item_{work_item_id.hex}"
    with TestClient(
        create_app(validate_database=False, database_engine=clean_api_database)
    ) as client:
        first = client.post(
            "/api/generation-jobs",
            json={
                "idempotencyKey": "t02-manual-repeat-001",
                "slackWorkItemId": work_item_api_id,
            },
        )
        first_replay = client.post(
            "/api/generation-jobs",
            json={
                "idempotencyKey": "t02-manual-repeat-001",
                "slackWorkItemId": work_item_api_id,
            },
        )
        second = client.post(
            "/api/generation-jobs",
            json={
                "idempotencyKey": "t02-manual-repeat-002",
                "slackWorkItemId": work_item_api_id,
            },
        )
        assert first.status_code == first_replay.status_code == second.status_code == 201
        assert first.json() == first_replay.json()
        first_manual_id = uuid.UUID(first.json()["job"]["id"].removeprefix("generation_job_"))
        second_manual_id = uuid.UUID(
            second.json()["job"]["id"].removeprefix("generation_job_")
        )

    with sessions() as session:
        jobs = tuple(
            session.scalars(
                select(GenerationJob)
                .where(GenerationJob.slack_work_item_id == work_item_id)
                .order_by(GenerationJob.generation_version)
            )
        )
        assert [job.id for job in jobs] == [
            automatic_job_id,
            first_manual_id,
            second_manual_id,
        ]
        assert [job.generation_version for job in jobs] == [1, 2, 3]
        assert [job.generation_trigger for job in jobs] == [
            "AUTOMATIC",
            "MANUAL",
            "MANUAL",
        ]
        assert {job.source_version for job in jobs} == {1}


def test_pe_se2_t02_automatic_receipt_rejects_a_manual_job(
    clean_api_database: Engine,
) -> None:
    """RED: a sealed manual root is not a valid automatic-delivery receipt."""

    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    consumer, work_item_id, outbox_event_id, _ = _complete_current_automatic_generation(
        sessions
    )
    with sessions.begin() as session:
        manual = GenerationRepository(session).create_next_job_with_context(
            slack_work_item_id=work_item_id
        )
        manual_job_id = manual.job.id
        checkpoint = session.get(KnowledgeProjectionEvent, outbox_event_id)
        assert checkpoint is not None
        checkpoint.result = {**checkpoint.result, "generationJobId": str(manual_job_id)}

    with pytest.raises(
        AutomaticGenerationReceiptError,
        match="does not reference its sealed automatic root job",
    ):
        asyncio.run(
            consumer.process_automatic_generation(
                outbox_event_id=outbox_event_id,
                work_item_id=work_item_id,
                source_version=1,
            )
        )

    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(GenerationJob)) == 2


def test_pe_se2_t02_automatic_retry_inherits_trigger_without_becoming_a_root(
    clean_api_database: Engine,
) -> None:
    """An explicit retry keeps automatic provenance but not root uniqueness ownership."""

    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    _, work_item_id, _, automatic_job_id = _complete_current_automatic_generation(
        sessions
    )
    with sessions.begin() as session:
        repository = GenerationRepository(session)
        repository.begin_execution(automatic_job_id)
        repository.fail_execution(
            automatic_job_id,
            code="GENERATION_PROVIDER_ERROR",
            message="injected release-gate failure",
            retryable=True,
            attempts=1,
        )
    with sessions.begin() as session:
        retry = GenerationRepository(session).retry_job_with_context(automatic_job_id)
        retry_job_id = retry.job.id
        assert retry.job.generation_trigger == "AUTOMATIC"
        assert retry.job.root_job_id == automatic_job_id
        assert retry.job.retry_of_job_id == automatic_job_id
        assert retry.job.generation_version == 2

    with sessions() as session:
        jobs = tuple(
            session.scalars(
                select(GenerationJob)
                .where(GenerationJob.slack_work_item_id == work_item_id)
                .order_by(GenerationJob.generation_version)
            )
        )
        assert [job.id for job in jobs] == [automatic_job_id, retry_job_id]
        assert [job.generation_trigger for job in jobs] == ["AUTOMATIC", "AUTOMATIC"]
        assert [job.retry_of_job_id is None for job in jobs] == [True, False]
