"""At-least-once knowledge extraction/projection/embed/index consumers."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import InstrumentedAttribute

from tc_backend.db.audit import AuditContext, append_audit
from tc_backend.db.jobs import JobRepository
from tc_backend.db.models import (
    AuditSourceType,
    BackgroundJob,
    GenerationChangeAssessment,
    GenerationJob,
    GenerationTrigger,
    JobStatus,
    KnowledgeEvidence,
    KnowledgeNode,
    KnowledgeOwnerType,
    KnowledgeProjectionEvent,
    KnowledgeSourceKind,
    OutboxEvent,
    OutboxStatus,
    SlackParseStatus,
    SlackWorkItem,
    WorkspaceSettings,
)
from tc_backend.knowledge.embedding import EmbeddingProvider
from tc_backend.knowledge.extraction import SlackKnowledgeExtractor
from tc_backend.knowledge.lightrag import LightRAGIndex, LightRAGIndexDocument
from tc_backend.knowledge.models import (
    CandidateRelationKind,
    KnowledgeExtractionResult,
    SlackSourceBundle,
    SlackSourceRevision,
    SourceRevisionOperation,
)
from tc_backend.repositories.generation_impact_repository import (
    GenerationImpactRepository,
)
from tc_backend.repositories.generation_repository import (
    GenerationRepository,
    UngroundedGenerationContext,
)
from tc_backend.repositories.knowledge_repository import (
    EvidenceInput,
    InvalidKnowledgeRelation,
    KnowledgeRepository,
    ProjectionResult,
)
from tc_backend.services.knowledge_service import node_content

SessionFactory = Callable[[], Session]
SUPPORTED_KNOWLEDGE_AGGREGATES = (
    "SLACK_WORK_ITEM",
    "TEST_CASE",
    "EXECUTION_ATTEMPT",
    "ISSUE",
)
_KNOWLEDGE_JOB_TYPES = ("KNOWLEDGE_PROJECTION", "KNOWLEDGE_REINDEX")
KNOWLEDGE_REBUILD_LOCK_KEY = "tc:knowledge-projection:rebuild:v1"


class AutomaticGenerationPermanentError(RuntimeError):
    """An automatic-generation delivery cannot become valid by retrying unchanged."""


class AutomaticGenerationPayloadError(AutomaticGenerationPermanentError, ValueError):
    """The handoff does not identify the exact Slack projection boundary."""


class AutomaticGenerationReceiptError(AutomaticGenerationPermanentError):
    """A persisted automatic-generation receipt is missing or inconsistent."""


class AutomaticGenerationNotReady(RuntimeError):
    """The exact current projection boundary is temporarily unavailable."""

    def __init__(self, message: str, *, retry_after_seconds: float = 1.0) -> None:
        super().__init__(message)
        self.retry_after_seconds = max(0.0, retry_after_seconds)


def knowledge_event_order() -> tuple[InstrumentedAttribute[object], ...]:
    """Return the one canonical ordering used by incremental and full replay."""

    return (
        OutboxEvent.occurred_at,
        OutboxEvent.aggregate_type,
        OutboxEvent.aggregate_id,
        OutboxEvent.aggregate_version,
        OutboxEvent.event_type,
        OutboxEvent.id,
    )


class KnowledgeOutboxConsumer:
    """Project one outbox row atomically and deduplicate replay by checkpoint."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        embedding_provider: EmbeddingProvider | None,
        extractor: SlackKnowledgeExtractor | None = None,
        light_rag: LightRAGIndex | None = None,
        max_attempts: int = 5,
    ) -> None:
        self.session_factory = session_factory
        self.embedding_provider = embedding_provider
        self.extractor = extractor
        self.light_rag = light_rag
        self.max_attempts = max_attempts

    async def run_once(self) -> ProjectionResult | None:
        with self.session_factory() as session:
            event_id = session.scalar(
                select(OutboxEvent.id)
                .where(
                    OutboxEvent.aggregate_type.in_(SUPPORTED_KNOWLEDGE_AGGREGATES),
                    OutboxEvent.status.in_((OutboxStatus.READY.value, OutboxStatus.FAILED.value)),
                    OutboxEvent.available_at <= datetime.now(UTC),
                )
                .order_by(*knowledge_event_order())
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if event_id is None:
                return None
        return await self.process_event(event_id)

    async def process_event(self, event_id: uuid.UUID) -> ProjectionResult:
        try:
            with self.session_factory() as session, session.begin():
                self._lock_against_rebuild(session, shared=True)
                return await self._process_event_in_session(
                    session,
                    event_id,
                    update_outbox=True,
                    index_auxiliary=True,
                )
        except Exception as exc:
            self._mark_failed(event_id, exc)
            raise

    async def process_automatic_generation(
        self,
        *,
        outbox_event_id: uuid.UUID,
        work_item_id: uuid.UUID,
        source_version: int,
    ) -> ProjectionResult | None:
        """Project one current Slack bundle and atomically seal its queued job.

        The extraction and embedding providers are explicit constructor dependencies.
        This method never selects or invokes a generation provider.
        """

        if source_version < 1:
            raise AutomaticGenerationPayloadError("sourceVersion must be positive")
        try:
            with self.session_factory() as session, session.begin():
                self._lock_against_rebuild(session, shared=True)
                event = session.execute(
                    select(OutboxEvent).where(OutboxEvent.id == outbox_event_id).with_for_update()
                ).scalar_one_or_none()
                if event is None:
                    raise AutomaticGenerationPayloadError(
                        f"outbox event not found: {outbox_event_id}"
                    )
                if (
                    event.aggregate_type != "SLACK_WORK_ITEM"
                    or event.aggregate_id != work_item_id
                    or event.aggregate_version != source_version
                    or event.event_type != "SLACK_PARSE_RESULT_STORED"
                ):
                    raise AutomaticGenerationPayloadError(
                        "automatic-generation handoff does not match its Slack parse outbox event"
                    )
                unlocked_item = session.get(SlackWorkItem, work_item_id)
                if unlocked_item is None:
                    raise AutomaticGenerationPayloadError(
                        f"Slack work item not found: {work_item_id}"
                    )
                workspace = session.scalar(
                    select(WorkspaceSettings.id)
                    .where(WorkspaceSettings.id == unlocked_item.workspace_id)
                    .with_for_update()
                )
                if workspace is None:
                    raise AutomaticGenerationPayloadError(
                        f"Workspace not found: {unlocked_item.workspace_id}"
                    )
                item = session.execute(
                    select(SlackWorkItem).where(SlackWorkItem.id == work_item_id).with_for_update()
                ).scalar_one_or_none()
                if item is None:
                    raise AutomaticGenerationPayloadError(
                        f"Slack work item not found: {work_item_id}"
                    )
                impact_repository = GenerationImpactRepository(session)
                committed_assessment = impact_repository.get_assessment(
                    work_item_id, source_version
                )
                if committed_assessment is not None:
                    committed_checkpoint = session.scalar(
                        select(KnowledgeProjectionEvent).where(
                            KnowledgeProjectionEvent.aggregate_type == "SLACK_WORK_ITEM",
                            KnowledgeProjectionEvent.aggregate_id == work_item_id,
                            KnowledgeProjectionEvent.aggregate_version == source_version,
                        )
                    )
                    if committed_checkpoint is None:
                        raise AutomaticGenerationReceiptError(
                            "automatic-generation assessment has no projection receipt"
                        )
                    self._validate_generation_impact_receipt(
                        session,
                        assessment=committed_assessment,
                        checkpoint=committed_checkpoint,
                        repair_missing_job_receipt=True,
                    )
                    return KnowledgeRepository._checkpoint_result(
                        committed_checkpoint,
                        replayed=True,
                    )
                if item.source_version != source_version:
                    return None
                if item.quiet_due_at is None:
                    raise AutomaticGenerationPayloadError(
                        "current Slack source has no quiet-window boundary"
                    )
                now = datetime.now(UTC)
                if item.quiet_due_at > now:
                    raise AutomaticGenerationNotReady(
                        "Slack quiet window has not elapsed",
                        retry_after_seconds=(item.quiet_due_at - now).total_seconds(),
                    )
                if item.parse_status not in (
                    SlackParseStatus.PARSED.value,
                    SlackParseStatus.NEEDS_REVIEW.value,
                ):
                    raise AutomaticGenerationNotReady(
                        "current Slack source projection is waiting for a parse result"
                    )

                checkpoint = session.scalar(
                    select(KnowledgeProjectionEvent)
                    .where(
                        KnowledgeProjectionEvent.aggregate_type == "SLACK_WORK_ITEM",
                        KnowledgeProjectionEvent.aggregate_id == work_item_id,
                        KnowledgeProjectionEvent.aggregate_version == source_version,
                    )
                    .with_for_update()
                )
                if checkpoint is None:
                    result = await self._process_event_in_session(
                        session,
                        outbox_event_id,
                        update_outbox=True,
                        index_auxiliary=True,
                    )
                    checkpoint = session.scalar(
                        select(KnowledgeProjectionEvent)
                        .where(
                            KnowledgeProjectionEvent.aggregate_type == "SLACK_WORK_ITEM",
                            KnowledgeProjectionEvent.aggregate_id == work_item_id,
                            KnowledgeProjectionEvent.aggregate_version == source_version,
                        )
                        .with_for_update()
                    )
                    if checkpoint is None:
                        raise AutomaticGenerationNotReady(
                            "exact Slack knowledge projection checkpoint is not committed"
                        )
                else:
                    event.status = OutboxStatus.PUBLISHED.value
                    event.processed_at = checkpoint.processed_at
                    result = KnowledgeRepository._checkpoint_result(
                        checkpoint,
                        replayed=True,
                    )

                if checkpoint.result.get("stale") is not False or result.stale:
                    return result

                extraction = checkpoint.result.get("extraction")
                if not isinstance(extraction, dict):
                    raise AutomaticGenerationReceiptError(
                        "automatic-generation projection has no supported extraction config"
                    )
                prepared = GenerationRepository(session).prepare_automatic_generation(
                    slack_work_item_id=work_item_id,
                    source_version=source_version,
                )
                resolution = impact_repository.assess_current(
                    prepared=prepared,
                    extraction=extraction,
                )
                assessment = resolution.assessment
                if resolution.created_job and resolution.selected_job is not None:
                    append_audit(
                        session,
                        workspace_id=item.workspace_id,
                        entity_type="GENERATION_JOB",
                        entity_id=resolution.selected_job.id,
                        action="AUTOMATIC_GENERATION_JOB_CREATED",
                        before=None,
                        after=_automatic_generation_audit_snapshot(resolution.selected_job),
                        context=AuditContext(
                            source_type=AuditSourceType.WORKER,
                            trace_id=f"slack_automatic_generation_{outbox_event_id.hex}",
                            idempotency_key=(f"slack-automatic-generation:{outbox_event_id}"),
                        ),
                    )
                receipt = {
                    **checkpoint.result,
                    "generationChangeAssessmentId": str(assessment.id),
                    "generationOutcome": assessment.outcome,
                }
                if resolution.selected_job is not None:
                    receipt["generationJobId"] = str(resolution.selected_job.id)
                else:
                    receipt.pop("generationJobId", None)
                checkpoint.result = receipt
                session.flush()
                self._validate_generation_impact_receipt(
                    session,
                    assessment=assessment,
                    checkpoint=checkpoint,
                )
                return result
        except (
            AutomaticGenerationNotReady,
            AutomaticGenerationPermanentError,
        ):
            raise
        except Exception as exc:
            self._mark_failed(outbox_event_id, exc)
            raise

    @staticmethod
    def _validate_generation_impact_receipt(
        session: Session,
        *,
        assessment: GenerationChangeAssessment,
        checkpoint: KnowledgeProjectionEvent,
        repair_missing_job_receipt: bool = False,
    ) -> GenerationJob | None:
        if (
            checkpoint.aggregate_type != "SLACK_WORK_ITEM"
            or checkpoint.aggregate_id != assessment.slack_work_item_id
            or checkpoint.aggregate_version != assessment.current_source_version
            or checkpoint.result.get("generationChangeAssessmentId") != str(assessment.id)
            or checkpoint.result.get("generationOutcome") != assessment.outcome
        ):
            raise AutomaticGenerationReceiptError(
                "automatic-generation checkpoint does not reference its impact assessment"
            )
        expected_job_id = (
            assessment.selected_generation_job_id or assessment.reused_generation_job_id
        )
        raw_job_id = checkpoint.result.get("generationJobId")
        if expected_job_id is None:
            if raw_job_id is not None:
                raise AutomaticGenerationReceiptError(
                    "automatic-generation checkpoint does not reference its sealed automatic "
                    "root job"
                )
            return None
        if raw_job_id is None and repair_missing_job_receipt:
            checkpoint.result = {
                **checkpoint.result,
                "generationJobId": str(expected_job_id),
            }
            raw_job_id = expected_job_id
        if str(raw_job_id) != str(expected_job_id):
            raise AutomaticGenerationReceiptError(
                "automatic-generation checkpoint does not reference its sealed automatic root job"
            )
        job = session.get(GenerationJob, expected_job_id)
        if job is None:
            raise AutomaticGenerationReceiptError(
                "automatic-generation checkpoint does not reference its sealed automatic root job"
            )
        return KnowledgeOutboxConsumer._validate_automatic_generation_receipt(
            session,
            raw_job_id=expected_job_id,
            work_item_id=assessment.slack_work_item_id,
            source_version=job.source_version,
        )

    @staticmethod
    def _validate_automatic_generation_receipt(
        session: Session,
        *,
        raw_job_id: object,
        work_item_id: uuid.UUID,
        source_version: int,
    ) -> GenerationJob:
        try:
            job_id = uuid.UUID(str(raw_job_id))
        except (TypeError, ValueError) as error:
            raise AutomaticGenerationReceiptError(
                "automatic-generation checkpoint has an invalid generationJobId"
            ) from error
        job = session.get(GenerationJob, job_id)
        if (
            job is None
            or job.slack_work_item_id != work_item_id
            or job.source_version != source_version
            or job.generation_trigger != GenerationTrigger.AUTOMATIC.value
            or job.root_job_id != job.id
            or job.provider != "UNCONFIGURED"
            or job.model != "UNCONFIGURED"
            or job.context_sealed_at is None
            or job.context_item_count < 1
            or job.context_digest is None
            or job.generation_input_capture_state != "SEALED"
            or job.generation_input_schema_version not in {"1.0.0", "generation-suggestion/1.0.0"}
            or job.generation_input_digest is None
        ):
            raise AutomaticGenerationReceiptError(
                "automatic-generation checkpoint does not reference its sealed automatic root job"
            )
        try:
            persisted = GenerationRepository(session).load_context(job.id)
            _ = persisted.provider_input
        except UngroundedGenerationContext as error:
            raise AutomaticGenerationReceiptError(
                "automatic-generation checkpoint does not reference its sealed automatic root job"
            ) from error
        return job

    async def process_rebuild_event(
        self,
        session: Session,
        event_id: uuid.UUID,
    ) -> ProjectionResult:
        """Replay one event inside a rebuild-owned transaction.

        The caller must hold the exclusive ``KNOWLEDGE_REBUILD_LOCK_KEY``
        advisory transaction lock. Outbox state and auxiliary LightRAG state
        are deliberately not mutated by this PostgreSQL projection replay.
        """

        return await self._process_event_in_session(
            session,
            event_id,
            update_outbox=False,
            index_auxiliary=False,
        )

    async def _process_event_in_session(
        self,
        session: Session,
        event_id: uuid.UUID,
        *,
        update_outbox: bool,
        index_auxiliary: bool,
    ) -> ProjectionResult:
        event_statement = select(OutboxEvent).where(OutboxEvent.id == event_id)
        if update_outbox:
            event_statement = event_statement.with_for_update()
        event = session.execute(event_statement).scalar_one_or_none()
        if event is None:
            raise LookupError(f"outbox event not found: {event_id}")
        checkpoint = session.get(KnowledgeProjectionEvent, event_id)
        if checkpoint is not None:
            if update_outbox:
                event.status = OutboxStatus.PUBLISHED.value
                event.processed_at = checkpoint.processed_at
            return KnowledgeRepository._checkpoint_result(checkpoint, replayed=True)
        if event.aggregate_type == "SLACK_WORK_ITEM":
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:aggregate_key, 0))"),
                {"aggregate_key": (f"knowledge:{event.aggregate_type}:{event.aggregate_id}")},
            )
        checkpoint = session.get(KnowledgeProjectionEvent, event_id)
        if checkpoint is not None:
            if update_outbox:
                event.status = OutboxStatus.PUBLISHED.value
                event.processed_at = checkpoint.processed_at
            return KnowledgeRepository._checkpoint_result(checkpoint, replayed=True)
        if update_outbox:
            event.status = OutboxStatus.PROCESSING.value
            event.attempts += 1
        repository = KnowledgeRepository(session)
        if event.aggregate_type == "SLACK_WORK_ITEM":
            result = await self._project_slack(session, repository, event)
        else:
            result = repository.project_outbox_event(
                event.id,
                publish_outbox=update_outbox,
            )
        await self._embed_and_index(
            session,
            repository,
            result.node_ids,
            index_auxiliary=index_auxiliary,
        )
        if update_outbox:
            event.status = OutboxStatus.PUBLISHED.value
            event.processed_at = datetime.now(UTC)
        return result

    @staticmethod
    def _lock_against_rebuild(session: Session, *, shared: bool) -> None:
        function = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
        session.execute(
            text(f"SELECT {function}(hashtextextended(:lock_key, 0))"),
            {"lock_key": KNOWLEDGE_REBUILD_LOCK_KEY},
        )

    async def _project_slack(
        self,
        session: Session,
        repository: KnowledgeRepository,
        event: OutboxEvent,
    ) -> ProjectionResult:
        if self.extractor is None:
            raise RuntimeError("Slack knowledge extraction provider is not configured")
        bundle = repository.load_slack_source_bundle(event.aggregate_id)
        latest_checkpoint = session.scalar(
            select(KnowledgeProjectionEvent)
            .where(
                KnowledgeProjectionEvent.aggregate_type == event.aggregate_type,
                KnowledgeProjectionEvent.aggregate_id == event.aggregate_id,
            )
            .order_by(KnowledgeProjectionEvent.aggregate_version.desc())
            .limit(1)
        )
        if event.aggregate_version < bundle.source_version or (
            latest_checkpoint is not None
            and latest_checkpoint.aggregate_version >= event.aggregate_version
        ):
            return _record_slack_checkpoint(
                session,
                event,
                node_ids=(),
                edge_ids=(),
                stale=True,
            )
        extraction = await self.extractor.extract(bundle)
        nodes, edges = _persist_extraction(repository, event, bundle, extraction)
        return _record_slack_checkpoint(
            session,
            event,
            node_ids=tuple(node.id for node in nodes),
            edge_ids=tuple(edges),
            stale=False,
            metadata={
                "extraction": {
                    "provider": extraction.provider,
                    "model": extraction.model,
                    "schemaVersion": extraction.schema_version,
                },
            },
        )

    async def _embed_and_index(
        self,
        session: Session,
        repository: KnowledgeRepository,
        node_ids: tuple[uuid.UUID, ...],
        *,
        index_auxiliary: bool = True,
    ) -> None:
        nodes = tuple(
            node
            for node_id in node_ids
            if (node := session.get(KnowledgeNode, node_id)) is not None
        )
        if not nodes:
            return
        if self.embedding_provider is None:
            raise RuntimeError("knowledge embedding provider is not configured")
        contents = tuple(node_content(node) for node in nodes)
        batch = await self.embedding_provider.embed(contents)
        for node, vector, content_hash in zip(
            nodes, batch.vectors, batch.content_hashes, strict=True
        ):
            repository.put_embedding(
                owner_type=KnowledgeOwnerType.NODE,
                owner_id=node.id,
                model_version=batch.model,
                embedding=vector,
                content_hash=content_hash,
            )
            if index_auxiliary and self.light_rag is not None:
                evidence = session.scalar(
                    select(KnowledgeEvidence)
                    .where(KnowledgeEvidence.knowledge_node_id == node.id)
                    .order_by(KnowledgeEvidence.recorded_at, KnowledgeEvidence.id)
                    .limit(1)
                )
                source_uri = (
                    evidence.permalink
                    if evidence is not None and evidence.permalink
                    else f"postgresql://knowledge/nodes/{node.id}"
                )
                # LightRAG is auxiliary: an unavailable/error receipt must not
                # roll back authoritative graph + pgvector publication.
                await self.light_rag.index_document(
                    LightRAGIndexDocument(
                        postgres_record_id=str(node.id),
                        content=node_content(node),
                        source_uri=source_uri,
                    )
                )

    def _mark_failed(self, event_id: uuid.UUID, exc: Exception) -> None:
        with self.session_factory() as session, session.begin():
            event = session.execute(
                select(OutboxEvent).where(OutboxEvent.id == event_id).with_for_update()
            ).scalar_one_or_none()
            if event is None or event.status == OutboxStatus.PUBLISHED.value:
                return
            event.attempts += 1
            event.status = (
                OutboxStatus.DEAD.value
                if event.attempts >= self.max_attempts
                else OutboxStatus.FAILED.value
            )
            event.available_at = datetime.now(UTC)
            event.payload = {
                **event.payload,
                "knowledgeFailure": f"{exc.__class__.__name__}: {str(exc)[:500]}",
            }


class KnowledgeJobConsumer:
    """Run explicitly queued projection and reindex jobs."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        outbox_consumer: KnowledgeOutboxConsumer,
        embedding_provider: EmbeddingProvider,
        light_rag: LightRAGIndex | None = None,
        worker_id: str = "knowledge-worker",
    ) -> None:
        self.session_factory = session_factory
        self.outbox_consumer = outbox_consumer
        self.embedding_provider = embedding_provider
        self.light_rag = light_rag
        self.worker_id = worker_id

    async def run_once(self) -> uuid.UUID | None:
        with self.session_factory() as session, session.begin():
            job = session.execute(
                select(BackgroundJob)
                .where(
                    BackgroundJob.job_type.in_(_KNOWLEDGE_JOB_TYPES),
                    BackgroundJob.status.in_((JobStatus.QUEUED.value, JobStatus.RETRY.value)),
                    BackgroundJob.run_after <= datetime.now(UTC),
                )
                .order_by(BackgroundJob.priority.desc(), BackgroundJob.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            ).scalar_one_or_none()
            if job is None:
                return None
            job.status = JobStatus.RUNNING.value
            job.attempts += 1
            job.locked_by = self.worker_id
            job.locked_at = datetime.now(UTC)
            job.heartbeat_at = job.locked_at
            job_id, job_type, payload, attempt = (
                job.id,
                job.job_type,
                dict(job.payload),
                job.attempts,
            )
        try:
            if job_type == "KNOWLEDGE_PROJECTION":
                for _ in range(int(payload.get("limit", 100))):
                    if await self.outbox_consumer.run_once() is None:
                        break
            else:
                await self._reindex(payload)
            with self.session_factory() as session, session.begin():
                if not JobRepository(session).succeed(
                    job_id=job_id,
                    worker_id=self.worker_id,
                    attempt=attempt,
                ):
                    raise RuntimeError("knowledge job lost its worker lock")
            return job_id
        except Exception as exc:
            with self.session_factory() as session, session.begin():
                JobRepository(session).fail(
                    job_id=job_id,
                    worker_id=self.worker_id,
                    error=f"{exc.__class__.__name__}: {str(exc)[:1000]}",
                    base_seconds=1,
                    max_seconds=60,
                    attempt=attempt,
                    retry_after=timedelta(seconds=1),
                )
            raise

    async def _reindex(self, payload: dict[str, object]) -> None:
        raw_ids = payload.get("nodeIds")
        if isinstance(raw_ids, list):
            node_ids = tuple(uuid.UUID(str(value)) for value in raw_ids)
        elif payload.get("targetType") == "NODE" and payload.get("targetId"):
            node_ids = (uuid.UUID(str(payload["targetId"])),)
        else:
            node_ids = ()
        with self.session_factory() as session, session.begin():
            statement = select(KnowledgeNode)
            if node_ids:
                statement = statement.where(KnowledgeNode.id.in_(node_ids))
            nodes = tuple(session.scalars(statement.order_by(KnowledgeNode.id)))
            if not nodes:
                return
            batch = await self.embedding_provider.embed(tuple(node_content(node) for node in nodes))
            repository = KnowledgeRepository(session)
            for node, vector, content_hash in zip(
                nodes, batch.vectors, batch.content_hashes, strict=True
            ):
                repository.put_embedding(
                    owner_type=KnowledgeOwnerType.NODE,
                    owner_id=node.id,
                    model_version=batch.model,
                    embedding=vector,
                    content_hash=content_hash,
                )
                if self.light_rag is not None:
                    await self.light_rag.index_document(
                        LightRAGIndexDocument(
                            postgres_record_id=str(node.id),
                            content=node_content(node),
                            source_uri=f"postgresql://knowledge/nodes/{node.id}",
                        )
                    )


def _automatic_generation_audit_snapshot(job: GenerationJob) -> dict[str, object]:
    """Expose the sealed receipt without copying context bodies into the audit log."""

    return {
        "id": str(job.id),
        "slackWorkItemId": str(job.slack_work_item_id),
        "sourceVersion": job.source_version,
        "generationVersion": job.generation_version,
        "generationTrigger": job.generation_trigger,
        "status": job.status,
        "provider": job.provider,
        "model": job.model,
        "promptVersion": job.prompt_version,
        "schemaVersion": job.schema_version,
        "contextItemCount": job.context_item_count,
        "contextDigest": job.context_digest,
        "generationInputCaptureState": job.generation_input_capture_state,
        "generationInputSchemaVersion": job.generation_input_schema_version,
        "generationInputDigest": job.generation_input_digest,
    }


def _record_slack_checkpoint(
    session: Session,
    event: OutboxEvent,
    *,
    node_ids: tuple[uuid.UUID, ...],
    edge_ids: tuple[uuid.UUID, ...],
    stale: bool,
    metadata: dict[str, object] | None = None,
) -> ProjectionResult:
    """Record one aggregate-version result without duplicating an equivalent checkpoint."""

    equivalent = session.scalar(
        select(KnowledgeProjectionEvent).where(
            KnowledgeProjectionEvent.aggregate_type == event.aggregate_type,
            KnowledgeProjectionEvent.aggregate_id == event.aggregate_id,
            KnowledgeProjectionEvent.aggregate_version == event.aggregate_version,
        )
    )
    if equivalent is not None:
        return ProjectionResult(
            outbox_event_id=event.id,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            aggregate_version=event.aggregate_version,
            node_ids=(),
            edge_ids=(),
            replayed=True,
            stale=True,
        )
    result_payload: dict[str, object] = {
        "nodeIds": [str(node_id) for node_id in node_ids],
        "edgeIds": [str(edge_id) for edge_id in edge_ids],
        "stale": stale,
        **(metadata or {}),
    }
    checkpoint = KnowledgeProjectionEvent(
        outbox_event_id=event.id,
        aggregate_type=event.aggregate_type,
        aggregate_id=event.aggregate_id,
        aggregate_version=event.aggregate_version,
        result=result_payload,
        processed_at=datetime.now(UTC),
    )
    session.add(checkpoint)
    session.flush()
    return ProjectionResult(
        outbox_event_id=event.id,
        aggregate_type=event.aggregate_type,
        aggregate_id=event.aggregate_id,
        aggregate_version=event.aggregate_version,
        node_ids=node_ids,
        edge_ids=edge_ids,
        replayed=False,
        stale=stale,
    )


def _persist_extraction(
    repository: KnowledgeRepository,
    event: OutboxEvent,
    bundle: SlackSourceBundle,
    extraction: KnowledgeExtractionResult,
) -> tuple[tuple[KnowledgeNode, ...], tuple[uuid.UUID, ...]]:
    revision_by_id = {revision.source_revision_id: revision for revision in bundle.revisions}
    first_revision = bundle.revisions[0]
    work_evidence = _slack_evidence(first_revision, event.occurred_at)
    repository_nodes: list[KnowledgeNode] = []
    edge_ids: list[uuid.UUID] = []
    work_node = repository.create_node(
        node_type="WORK_ITEM",
        label=f"Slack thread {bundle.thread_ts}",
        status=_bundle_status(bundle),
        entity_type="SLACK_WORK_ITEM",
        entity_id=event.aggregate_id,
        properties={
            "content": _bundle_content(bundle),
            "workspaceId": bundle.workspace_id,
            "channelId": bundle.channel_id,
            "threadTs": bundle.thread_ts,
            "sourceVersion": bundle.source_version,
            "messageCount": len({revision.message_id for revision in bundle.revisions}),
            "revisionCount": len(bundle.revisions),
            "rootDeleted": _bundle_status(bundle) == "RETRACTED",
            "resourceVersion": 1,
        },
        recorded_at=event.occurred_at,
        evidence=work_evidence,
    )
    for revision in bundle.revisions[1:]:
        repository.add_evidence(
            node_id=work_node.id,
            evidence=_slack_evidence(revision, event.occurred_at),
        )
    repository_nodes.append(work_node)
    by_candidate: dict[str, KnowledgeNode] = {}
    for item in extraction.candidates:
        evidence_item = item.candidate.evidence[0]
        revision = revision_by_id[evidence_item.source_revision_id]
        inactive = item.status.value in {"SUPERSEDED", "RETRACTED"}
        node = repository.create_node(
            node_type=item.candidate.node_type.value,
            label=item.candidate.content[:500],
            status=item.status.value,
            entity_type="SLACK_KNOWLEDGE_CANDIDATE",
            entity_id=uuid.uuid5(
                uuid.NAMESPACE_URL, f"tc:slack:{event.aggregate_id}:{item.candidate.candidate_id}"
            ),
            properties={
                "content": item.candidate.content,
                "topicKey": item.candidate.topic_key,
                "confidence": item.candidate.confidence,
                "classificationReason": item.reason,
                "slackWorkItemId": str(event.aggregate_id),
                "sourceVersion": bundle.source_version,
                "sourceRevisionIds": [
                    evidence.source_revision_id for evidence in item.candidate.evidence
                ],
                "resourceVersion": 1,
            },
            recorded_at=event.occurred_at,
            valid_from=None if inactive else event.occurred_at,
            valid_to=event.occurred_at if inactive else None,
            evidence=_slack_evidence(revision, event.occurred_at),
        )
        for extra in item.candidate.evidence[1:]:
            repository.add_evidence(
                node_id=node.id,
                evidence=_slack_evidence(
                    revision_by_id[extra.source_revision_id], event.occurred_at
                ),
            )
        if item.reason == "source_message_deleted":
            cited_message_ids = {
                revision_by_id[evidence.source_revision_id].message_id
                for evidence in item.candidate.evidence
            }
            for tombstone in bundle.revisions:
                if (
                    tombstone.message_id in cited_message_ids
                    and tombstone.operation is SourceRevisionOperation.DELETE
                ):
                    repository.add_evidence(
                        node_id=node.id,
                        evidence=_slack_evidence(tombstone, event.occurred_at),
                    )
        by_candidate[item.candidate.candidate_id] = node
        repository_nodes.append(node)
        edge_ids.append(
            repository.create_edge(
                source_node_id=work_node.id,
                predicate="DISCUSSES",
                target_node_id=node.id,
                evidence=_slack_evidence(revision, event.occurred_at),
                recorded_at=event.occurred_at,
            ).id
        )
    for item in extraction.candidates:
        for relation in item.candidate.relations:
            if relation.kind is not CandidateRelationKind.SUPERSEDES:
                continue
            try:
                edge_ids.append(
                    repository.create_edge(
                        source_node_id=by_candidate[item.candidate.candidate_id].id,
                        predicate="SUPERSEDES",
                        target_node_id=by_candidate[relation.target_candidate_id].id,
                        evidence=_slack_evidence(
                            revision_by_id[item.candidate.evidence[0].source_revision_id],
                            event.occurred_at,
                        ),
                        recorded_at=event.occurred_at,
                    ).id
                )
            except InvalidKnowledgeRelation:
                # The extraction lifecycle remains in node properties when the
                # v1 ontology deliberately disallows that source/target pair.
                continue
    return tuple(repository_nodes), tuple(edge_ids)


def _slack_evidence(revision: SlackSourceRevision, recorded_at: datetime) -> EvidenceInput:
    return EvidenceInput.from_content(
        source_kind=KnowledgeSourceKind.SOURCE_REVISION,
        entity_type="SLACK_MESSAGE_REVISION",
        entity_id=uuid.UUID(revision.source_revision_id),
        revision_key=revision.revision_ts,
        content=(
            revision.body
            or f"deleted:{revision.message_ts or revision.message_id}:{revision.revision_ts}"
        ),
        recorded_at=revision.recorded_at or recorded_at,
        permalink=revision.permalink,
    )


def _bundle_status(bundle: SlackSourceBundle) -> str:
    root_revisions = tuple(revision for revision in bundle.revisions if revision.is_root)
    if not root_revisions:
        return "ACTIVE"
    return (
        "RETRACTED" if root_revisions[-1].operation is SourceRevisionOperation.DELETE else "ACTIVE"
    )


def _bundle_content(bundle: SlackSourceBundle) -> str:
    """Serialize the immutable source bundle in its authoritative ledger order."""

    lines: list[str] = []
    for revision in bundle.revisions:
        role = "ROOT" if revision.is_root else "REPLY"
        message_ts = revision.message_ts or revision.message_id
        header = (
            f"[{role} message={message_ts} revision={revision.revision_ts} "
            f"operation={revision.operation.value}]"
        )
        lines.append(f"{header}\n{revision.body if revision.body is not None else '<deleted>'}")
    return "\n\n".join(lines)
