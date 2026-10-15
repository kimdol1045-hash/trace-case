from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from tc_backend.db.audit import AuditContext
from tc_backend.db.cases import CaseRepository
from tc_backend.db.cases import TestCaseContent as CaseContent
from tc_backend.db.cases import TestStepInput as StepInput
from tc_backend.db.cycles import CycleRepository
from tc_backend.db.folders import FolderRepository
from tc_backend.db.issues import IssueRepository
from tc_backend.db.models import (
    AuditSourceType,
    ExecutionPlatform,
    ExecutionStatus,
    KnowledgeEdge,
    KnowledgeEmbedding,
    KnowledgeEvidence,
    KnowledgeNode,
    KnowledgeOwnerType,
    KnowledgeProjectionEvent,
    KnowledgeSourceKind,
    KnowledgeSourceRecord,
    OutboxEvent,
    SlackMessage,
    SlackMessageRevision,
)
from tc_backend.db.models import (
    TestCase as CaseRow,
)
from tc_backend.db.slack import SlackEventInput, SlackRepository
from tc_backend.knowledge.embedding import EmbeddingBatch
from tc_backend.knowledge.extraction import SlackKnowledgeExtractor
from tc_backend.knowledge.lightrag import (
    LightRAGAuxiliaryIndex,
    LightRAGCallResult,
    LightRAGCallStatus,
    LightRAGPostgresConnectionSettings,
    LightRAGSearchCandidate,
    LightRAGStorageConfig,
    LightRAGUnavailableError,
)
from tc_backend.knowledge.models import (
    KnowledgeNodeType,
    ProviderEvidence,
    ProviderExtraction,
    ProviderKnowledgeCandidate,
    SlackSourceBundle,
    SlackSourceRevision,
)
from tc_backend.knowledge.rebuild import (
    PROJECTION_RESET_TABLES,
    REBUILD_CONFIRMATION,
    KnowledgeProjectionRebuilder,
    UnsafeKnowledgeRebuild,
    authoritative_ledger_digest,
    canonical_knowledge_snapshot,
)
from tc_backend.repositories.knowledge_repository import (
    BGE_M3_DIMENSION,
    EvidenceInput,
    KnowledgeRepository,
    SourceRevisionConflictError,
)
from tc_backend.services.knowledge_service import HybridSearchResult, KnowledgeService
from tc_backend.worker.knowledge_consumer import KnowledgeOutboxConsumer

pytestmark = pytest.mark.postgres
BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def knowledge_engine() -> Iterator[Engine]:
    value = os.getenv("TC_TEST_DATABASE_URL")
    if not value:
        pytest.skip("TC_TEST_DATABASE_URL is required for PostgreSQL knowledge tests")
    url = make_url(value)
    if not url.drivername.startswith("postgresql"):
        pytest.fail("TC_TEST_DATABASE_URL must use PostgreSQL")
    if not url.database or "test" not in url.database.lower():
        pytest.fail("knowledge tests require a disposable database containing 'test'")
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", value.replace("%", "%%"))
    command.upgrade(config, "head")
    engine = create_engine(value, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_knowledge_database(knowledge_engine: Engine) -> Iterator[None]:
    _reset_database(knowledge_engine)
    yield
    _reset_database(knowledge_engine)


def _reset_database(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE knowledge_projection_events, knowledge_embeddings, "
                "knowledge_evidence, knowledge_edges, knowledge_nodes, "
                "knowledge_source_records, slack_parse_results, slack_message_revisions, "
                "slack_messages, slack_threads, slack_work_items, slack_events, "
                "slack_channel_allowlist, drive_reconciliation_records, "
                "drive_usage_snapshots, integration_settings, attachments, comments, "
                "issue_links, issue_steps, issues, outbox_events, execution_attempts, "
                "cycle_items, test_cycles, folder_aliases, audit_logs, folders, "
                "test_case_version_tags, test_case_tags, test_steps, test_case_versions, "
                "test_cases, tags, work_items, workspace_key_counters, "
                "workspace_settings, background_jobs CASCADE"
            )
        )
        connection.execute(
            text(
                "INSERT INTO workspace_settings "
                "(id, singleton_key, team_name, case_prefix, issue_prefix, "
                "default_environment, generation_quiet_minutes, version) "
                "VALUES ('00000000-0000-7000-8000-000000000100'::uuid, 1, "
                "'QA Team', 'TC', 'ISSUE', 'staging', 10, 1)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO workspace_key_counters (workspace_id, entity_type, next_value) "
                "VALUES ('00000000-0000-7000-8000-000000000100'::uuid, 'TEST_CASE', 1), "
                "('00000000-0000-7000-8000-000000000100'::uuid, 'ISSUE', 1)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO folders "
                "(id, workspace_id, name, normalized_name, sort_order, version, system_kind) "
                "VALUES ('00000000-0000-7000-8000-000000000101'::uuid, "
                "'00000000-0000-7000-8000-000000000100'::uuid, "
                "'AI 생성함', 'ai 생성함', 2147483647, 1, 'AI_GENERATED_INBOX')"
            )
        )


def _audit(label: str) -> AuditContext:
    return AuditContext(
        source_type=AuditSourceType.API,
        actor_label="knowledge-postgres-test",
        reason=label,
        trace_id=f"knowledge_{label}",
    )


def _content(summary: str) -> CaseContent:
    return CaseContent(
        summary=summary,
        precondition="테스트 계정이 준비되어 있다",
        steps=(StepInput(action="기능을 실행한다", expected_result="정상 완료된다"),),
        assignee_label="QA",
        tag_names=("knowledge",),
    )


def _case_library(
    sessions: sessionmaker[Session], *, work_item_key: str = "WORK-KNOWLEDGE"
) -> tuple[uuid.UUID, uuid.UUID]:
    with sessions.begin() as session:
        category = FolderRepository(session).create(
            name="Knowledge",
            parent_id=None,
            sort_order=0,
            audit=_audit("category"),
        )
        work_item = CaseRepository(session).create_work_item(
            work_item_key=work_item_key,
            category_id=category.id,
            summary="Knowledge projection",
            audit=_audit("work-item"),
        )
        case = CaseRepository(session).create_case(
            work_item_id=work_item.id,
            content=_content("결제 승인 회귀 테스트"),
            change_reason="initial",
            audit=_audit("case"),
        )
        return work_item.id, case.test_case.id


def _evidence(
    label: str,
    *,
    entity_id: uuid.UUID | None = None,
    kind: KnowledgeSourceKind = KnowledgeSourceKind.LEDGER_ENTITY,
) -> EvidenceInput:
    return EvidenceInput.from_content(
        source_kind=kind,
        entity_type="TEST_FIXTURE",
        entity_id=entity_id or uuid.uuid4(),
        revision_key="1",
        content=label,
        recorded_at=datetime.now(UTC),
    )


def test_database_forces_grounded_nodes_and_v1_relation_pairs(
    knowledge_engine: Engine,
) -> None:
    sessions = sessionmaker(knowledge_engine, expire_on_commit=False)
    with pytest.raises(DBAPIError, match="requires evidence"):
        with sessions.begin() as session:
            session.add(
                KnowledgeNode(
                    id=uuid.uuid4(),
                    ontology_schema_id=uuid.UUID("00000000-0000-7000-8000-000000000001"),
                    node_type="REQUIREMENT",
                    status="ACTIVE",
                    label="orphan",
                    properties={},
                    recorded_at=datetime.now(UTC),
                )
            )

    with pytest.raises(DBAPIError, match="does not allow"):
        with sessions.begin() as session:
            repository = KnowledgeRepository(session)
            requirement = repository.create_node(
                node_type="REQUIREMENT",
                label="requirement",
                evidence=_evidence("requirement"),
            )
            feature = repository.create_node(
                node_type="FEATURE",
                label="feature",
                evidence=_evidence("feature"),
            )
            session.add(
                KnowledgeEdge(
                    id=uuid.uuid4(),
                    ontology_schema_id=requirement.ontology_schema_id,
                    source_node_id=requirement.id,
                    predicate="FOUND_BY",
                    target_node_id=feature.id,
                    status="ACTIVE",
                    properties={},
                    recorded_at=datetime.now(UTC),
                )
            )
            session.flush()

    with pytest.raises(DBAPIError, match="requires evidence"):
        with sessions.begin() as session:
            repository = KnowledgeRepository(session)
            source = repository.create_node(
                node_type="REQUIREMENT",
                label="source requirement",
                evidence=_evidence("source requirement"),
            )
            target = repository.create_node(
                node_type="REQUIREMENT",
                label="target requirement",
                evidence=_evidence("target requirement"),
            )
            session.add(
                KnowledgeEdge(
                    id=uuid.uuid4(),
                    ontology_schema_id=source.ontology_schema_id,
                    source_node_id=source.id,
                    predicate="REFINES",
                    target_node_id=target.id,
                    status="ACTIVE",
                    properties={},
                    recorded_at=datetime.now(UTC),
                )
            )


def test_source_revision_is_append_only_and_every_evidence_has_source_fk(
    knowledge_engine: Engine,
) -> None:
    sessions = sessionmaker(knowledge_engine, expire_on_commit=False)
    source_id = uuid.uuid4()
    with sessions.begin() as session:
        repository = KnowledgeRepository(session)
        source = repository.register_source(_evidence("immutable", entity_id=source_id))
        repeated = repository.register_source(_evidence("immutable", entity_id=source_id))
        assert repeated.id == source.id
        with pytest.raises(SourceRevisionConflictError):
            repository.register_source(_evidence("changed", entity_id=source_id))

        node = repository.create_node(
            node_type="DECISION",
            label="근거가 있는 결정",
            evidence=_evidence("immutable", entity_id=source_id),
        )
        row = session.scalar(
            select(KnowledgeEvidence).where(KnowledgeEvidence.knowledge_node_id == node.id)
        )
        assert row is not None
        assert row.source_record_id == source.id
        assert session.get(KnowledgeSourceRecord, row.source_record_id) is not None


def test_test_case_change_and_outbox_are_atomic_then_replay_is_idempotent(
    knowledge_engine: Engine,
) -> None:
    sessions = sessionmaker(knowledge_engine, expire_on_commit=False)
    work_item_id, committed_case_id = _case_library(sessions)

    rolled_back_case_id: uuid.UUID | None = None
    with pytest.raises(RuntimeError, match="force rollback"):
        with sessions.begin() as session:
            rolled_back = CaseRepository(session).create_case(
                work_item_id=work_item_id,
                content=_content("rollback case"),
                change_reason="rollback",
                audit=_audit("rollback"),
            )
            rolled_back_case_id = rolled_back.test_case.id
            session.flush()
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(OutboxEvent.aggregate_id == rolled_back_case_id)
                )
                == 1
            )
            raise RuntimeError("force rollback")

    assert rolled_back_case_id is not None
    with sessions() as session:
        assert session.get(CaseRow, rolled_back_case_id) is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.aggregate_id == rolled_back_case_id)
            )
            == 0
        )
        event = session.execute(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_type == "TEST_CASE",
                OutboxEvent.aggregate_id == committed_case_id,
            )
        ).scalar_one()
        event_id = event.id

    with sessions.begin() as session:
        repository = KnowledgeRepository(session)
        first = repository.project_outbox_event(event_id)
        assert not first.replayed

    with sessions.begin() as session:
        replay = KnowledgeRepository(session).project_outbox_event(event_id)
        assert replay.replayed
        assert replay.node_ids == first.node_ids

    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(KnowledgeProjectionEvent)) == 1
        assert session.scalar(select(func.count()).select_from(KnowledgeNode)) == 1
        assert session.scalar(select(func.count()).select_from(KnowledgeEvidence)) == 1
        source = session.execute(select(KnowledgeSourceRecord)).scalar_one()
        assert source.source_kind == KnowledgeSourceKind.LEDGER_ENTITY.value
        event = session.get(OutboxEvent, event_id)
        assert event is not None and event.status == "PUBLISHED"
        assert event.processed_at is not None


def test_execution_and_issue_outbox_project_typed_nodes_and_edges(
    knowledge_engine: Engine,
) -> None:
    sessions = sessionmaker(knowledge_engine, expire_on_commit=False)
    _, case_id = _case_library(sessions, work_item_key="WORK-EXECUTION")
    with sessions.begin() as session:
        cycle = CycleRepository(session).create(
            name="Knowledge cycle",
            starts_at=date(2026, 8, 21),
            scope_type="WORK_ITEM",
            scope_value="WORK-EXECUTION",
            audit=_audit("cycle"),
        )
        cycle_id = cycle.cycle.id
        item_id = cycle.items[0].id
    with sessions.begin() as session:
        CycleRepository(session).start(
            cycle_id=cycle_id,
            base_resource_version=1,
            audit=_audit("cycle-start"),
        )
    with sessions.begin() as session:
        attempt = CycleRepository(session).start_attempt(
            cycle_item_id=item_id,
            platform=ExecutionPlatform.WEB,
            audit=_audit("attempt-start"),
        )
        attempt_id = attempt.id
    with sessions.begin() as session:
        CycleRepository(session).complete_attempt(
            attempt_id=attempt_id,
            status=ExecutionStatus.FAIL,
            actual_result="HTTP 500",
            note="trace-500",
            audit=_audit("attempt-complete"),
        )
    with sessions.begin() as session:
        issue = IssueRepository(session).create_from_attempt(
            attempt_id=attempt_id,
            summary="결제 승인 실패",
            audit=_audit("issue"),
        )
        issue_id = issue.issue.id

    with sessions.begin() as session:
        execution_event = session.execute(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_type == "EXECUTION_ATTEMPT",
                OutboxEvent.aggregate_id == attempt_id,
            )
        ).scalar_one()
        issue_event = session.execute(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_type == "ISSUE",
                OutboxEvent.aggregate_id == issue_id,
            )
        ).scalar_one()
        repository = KnowledgeRepository(session)
        execution_result = repository.project_outbox_event(execution_event.id)
        issue_result = repository.project_outbox_event(issue_event.id)
        assert len(execution_result.node_ids) == 2
        assert len(execution_result.edge_ids) == 1
        assert issue_result.node_ids
        assert issue_result.edge_ids

    with sessions() as session:
        nodes = {
            (node.node_type, node.entity_type, node.entity_id)
            for node in session.scalars(select(KnowledgeNode))
        }
        assert ("TEST_CASE", "TEST_CASE", case_id) in nodes
        assert ("EXECUTION", "EXECUTION_ATTEMPT", attempt_id) in nodes
        assert ("ISSUE", "ISSUE", issue_id) in nodes
        assert set(session.scalars(select(KnowledgeEdge.predicate))) == {
            "INSTANCE_OF",
            "FOUND_BY",
        }
        assert session.scalar(select(func.count()).select_from(KnowledgeProjectionEvent)) == 2


def test_bge_m3_content_dedup_and_pgvector_cosine_search(
    knowledge_engine: Engine,
) -> None:
    sessions = sessionmaker(knowledge_engine, expire_on_commit=False)
    first_vector = [1.0, *([0.0] * (BGE_M3_DIMENSION - 1))]
    second_vector = [0.0, 1.0, *([0.0] * (BGE_M3_DIMENSION - 2))]
    with sessions.begin() as session:
        repository = KnowledgeRepository(session)
        first_node = repository.create_node(
            node_type="REQUIREMENT",
            label="first",
            evidence=_evidence("first"),
        )
        second_node = repository.create_node(
            node_type="REQUIREMENT",
            label="second",
            evidence=_evidence("second"),
        )
        first = repository.put_embedding(
            owner_type=KnowledgeOwnerType.NODE,
            owner_id=first_node.id,
            model_version="bge-m3:latest@2026-08-21",
            content="first requirement",
            embedding=first_vector,
        )
        duplicate = repository.put_embedding(
            owner_type=KnowledgeOwnerType.NODE,
            owner_id=first_node.id,
            model_version="bge-m3:latest@2026-08-21",
            content="first requirement",
            embedding=second_vector,
        )
        repository.put_embedding(
            owner_type=KnowledgeOwnerType.NODE,
            owner_id=second_node.id,
            model_version="bge-m3:latest@2026-08-21",
            content="second requirement",
            embedding=second_vector,
        )
        assert duplicate.id == first.id
        results = repository.search_embeddings(
            first_vector,
            model_version="bge-m3:latest@2026-08-21",
        )
        assert [result.owner_id for result in results] == [first_node.id, second_node.id]
        assert results[0].cosine_distance == pytest.approx(0.0)
        assert results[0].score == pytest.approx(1.0)

    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(KnowledgeEmbedding)) == 2
        vector_type = session.scalar(
            text(
                "SELECT format_type(a.atttypid, a.atttypmod) "
                "FROM pg_attribute AS a "
                "WHERE a.attrelid = 'knowledge_embeddings'::regclass "
                "AND a.attname = 'embedding'"
            )
        )
        assert vector_type == "vector(1024)"


SLACK_KNOWLEDGE_WORKSPACE = "T_KNOWLEDGE"
SLACK_KNOWLEDGE_CHANNEL = "C_KNOWLEDGE"


class _BundleCandidateProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.bundles: list[SlackSourceBundle] = []

    async def extract(self, bundle: SlackSourceBundle) -> ProviderExtraction:
        self.calls += 1
        self.bundles.append(bundle)
        latest_body_by_message: dict[str, SlackSourceRevision] = {}
        message_order: list[str] = []
        for revision in bundle.revisions:
            if revision.message_id not in latest_body_by_message:
                message_order.append(revision.message_id)
            if revision.body is not None:
                latest_body_by_message[revision.message_id] = revision
        candidates = tuple(
            ProviderKnowledgeCandidate(
                candidate_id=f"message-{message_id}",
                node_type=KnowledgeNodeType.REQUIREMENT,
                content=revision.body,
                topic_key=f"message:{message_id}",
                confidence=0.99,
                evidence=(
                    ProviderEvidence(
                        source_revision_id=revision.source_revision_id,
                        excerpt=revision.body,
                    ),
                ),
            )
            for message_id in message_order
            if (revision := latest_body_by_message.get(message_id)) is not None
            and revision.body is not None
        )
        return ProviderExtraction(
            provider="postgres-test",
            model="deterministic-bundle-v1",
            schema_version="knowledge-extraction-v1",
            candidates=candidates,
        )


class _DeterministicEmbeddingProvider:
    async def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch:
        return EmbeddingBatch(
            model="bge-m3:test",
            dimension=BGE_M3_DIMENSION,
            vectors=tuple(
                tuple([float(index + 1), *([0.0] * (BGE_M3_DIMENSION - 1))])
                for index, _ in enumerate(texts)
            ),
            content_hashes=tuple(
                hashlib.sha256(value.encode("utf-8")).hexdigest() for value in texts
            ),
        )


class _QueryEmbeddingProvider:
    async def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch:
        assert len(texts) == 1
        return EmbeddingBatch(
            model="bge-m3:test",
            dimension=BGE_M3_DIMENSION,
            vectors=(tuple([1.0, *([0.0] * (BGE_M3_DIMENSION - 1))]),),
            content_hashes=(hashlib.sha256(texts[0].encode("utf-8")).hexdigest(),),
        )


class _StaticLightRAG:
    def __init__(self, record_ids: tuple[str, ...] = (), *, unavailable: bool = False) -> None:
        self.record_ids = record_ids
        self.unavailable = unavailable

    async def search(self, query: str) -> LightRAGCallResult[LightRAGSearchCandidate]:
        if self.unavailable:
            return LightRAGCallResult(
                status=LightRAGCallStatus.UNAVAILABLE,
                failure=LightRAGUnavailableError("injected outage", retryable=True),
            )
        return LightRAGCallResult(
            status=LightRAGCallStatus.OK,
            value=LightRAGSearchCandidate(
                context=query,
                postgres_record_ids=self.record_ids,
            ),
        )


class _FaultInjectingLightRAGCore:
    """A separate auxiliary transport boundary; it never owns PostgreSQL rows."""

    def __init__(self) -> None:
        self.context = "no auxiliary hints"
        self.query_error: Exception | None = None
        self.query_delay_seconds = 0.0
        self.initialize_error: Exception | None = None
        self.query_calls = 0
        self.initialize_calls = 0
        self.finalize_calls = 0

    async def initialize_storages(self) -> None:
        self.initialize_calls += 1
        if self.initialize_error is not None:
            raise self.initialize_error

    async def ainsert(
        self,
        input: str | list[str],
        split_by_character: str | None = None,
        split_by_character_only: bool = False,
        ids: str | list[str] | None = None,
        file_paths: str | list[str] | None = None,
        track_id: str | None = None,
    ) -> str:
        return "unused-failover-track"

    async def aquery(
        self,
        query: str,
        param: object,
        system_prompt: str | None = None,
    ) -> str | AsyncIterator[str]:
        self.query_calls += 1
        await asyncio.sleep(self.query_delay_seconds)
        if self.query_error is not None:
            raise self.query_error
        return self.context

    async def finalize_storages(self) -> None:
        self.finalize_calls += 1


def _fault_injecting_light_rag(
    core: _FaultInjectingLightRAGCore,
    *,
    query_timeout_seconds: float = 0.01,
) -> LightRAGAuxiliaryIndex:
    config = LightRAGStorageConfig(
        workspace="tc-hybrid-failover-test",
        postgres=LightRAGPostgresConnectionSettings(
            host="separate-auxiliary.test",
            port=5432,
            user="lightrag_test",
            password="test-only",
            database="lightrag_auxiliary_test",
        ),
    )
    return LightRAGAuxiliaryIndex(
        core,
        query_param_factory=lambda mode: {"mode": mode},
        storage_config=config,
        query_timeout_seconds=query_timeout_seconds,
    )


def _allow_slack_knowledge(sessions: sessionmaker[Session]) -> None:
    with sessions.begin() as session:
        SlackRepository(session).put_channel(
            slack_workspace_id=SLACK_KNOWLEDGE_WORKSPACE,
            channel_id=SLACK_KNOWLEDGE_CHANNEL,
            channel_name="knowledge-source",
        )


def _slack_knowledge_event(
    event_id: str,
    event_type: str,
    event_ts: str,
    message_ts: str,
    body: str | None,
    *,
    thread_ts: str | None = None,
    revision_ts: str | None = None,
) -> SlackEventInput:
    return SlackEventInput(
        event_id=event_id,
        slack_workspace_id=SLACK_KNOWLEDGE_WORKSPACE,
        channel_id=SLACK_KNOWLEDGE_CHANNEL,
        event_type=event_type,
        event_ts=event_ts,
        message_ts=message_ts,
        body=body,
        thread_ts=thread_ts,
        revision_ts=revision_ts,
        author_id="U_KNOWLEDGE",
        permalink=(
            "https://example.slack.com/archives/"
            f"{SLACK_KNOWLEDGE_CHANNEL}/p{message_ts.replace('.', '')}"
        ),
        raw_payload={"event_id": event_id},
    )


def _knowledge_consumer(
    sessions: sessionmaker[Session], provider: _BundleCandidateProvider
) -> KnowledgeOutboxConsumer:
    return KnowledgeOutboxConsumer(
        session_factory=sessions,
        embedding_provider=_DeterministicEmbeddingProvider(),
        extractor=SlackKnowledgeExtractor(provider),
    )


def test_slack_root_and_thread_bundle_citations_are_retrievable(
    knowledge_engine: Engine,
) -> None:
    sessions = sessionmaker(knowledge_engine, expire_on_commit=False)
    _allow_slack_knowledge(sessions)
    with sessions.begin() as session:
        root = SlackRepository(session).ingest_event(
            _slack_knowledge_event(
                "Ev-knowledge-root",
                "ROOT",
                "1725000000.000100",
                "1725000000.000000",
                "Root requirement: retain immutable audit history.",
            )
        )
    with sessions.begin() as session:
        reply = SlackRepository(session).ingest_event(
            _slack_knowledge_event(
                "Ev-knowledge-reply",
                "REPLY",
                "1725000001.000100",
                "1725000001.000000",
                "Thread requirement: citations must deep-link to Slack.",
                thread_ts="1725000000.000000",
            )
        )
    assert root.outbox_id is not None and reply.outbox_id is not None

    provider = _BundleCandidateProvider()
    result = asyncio.run(_knowledge_consumer(sessions, provider).process_event(reply.outbox_id))
    assert not result.stale
    assert provider.calls == 1
    bundle = provider.bundles[0]
    assert [(item.is_root, item.message_ts, item.operation.value) for item in bundle.revisions] == [
        (True, "1725000000.000000", "CREATE"),
        (False, "1725000001.000000", "CREATE"),
    ]

    with sessions() as session:
        work_node = session.execute(
            select(KnowledgeNode).where(KnowledgeNode.entity_type == "SLACK_WORK_ITEM")
        ).scalar_one()
        candidates = tuple(
            session.scalars(
                select(KnowledgeNode)
                .where(KnowledgeNode.entity_type == "SLACK_KNOWLEDGE_CANDIDATE")
                .order_by(KnowledgeNode.label)
            )
        )
        service = KnowledgeService(session)
        work_citations = service.evidence_for("NODE", work_node.id)
        candidate_citations = tuple(
            citation
            for candidate in candidates
            for citation in service.evidence_for("NODE", candidate.id)
        )
        source_revision_ids = set(session.scalars(select(SlackMessageRevision.id)))
        assert len(candidates) == 2
        assert {item.source_id for item in work_citations} == source_revision_ids
        assert {item.source_id for item in candidate_citations} == source_revision_ids
        assert all(item.permalink for item in (*work_citations, *candidate_citations))
        assert work_node.properties["messageCount"] == 2
        assert work_node.properties["revisionCount"] == 2
        assert work_node.properties["content"].index("[ROOT") < work_node.properties[
            "content"
        ].index("[REPLY")


def test_slack_edits_and_deletes_keep_evidence_but_retract_current_knowledge(
    knowledge_engine: Engine,
) -> None:
    sessions = sessionmaker(knowledge_engine, expire_on_commit=False)
    _allow_slack_knowledge(sessions)
    consumer_provider = _BundleCandidateProvider()
    consumer = _knowledge_consumer(sessions, consumer_provider)
    with sessions.begin() as session:
        created = SlackRepository(session).ingest_event(
            _slack_knowledge_event(
                "Ev-policy-created",
                "ROOT",
                "1725000100.000100",
                "1725000100.000000",
                "Release policy requires one approval.",
            )
        )
    assert created.outbox_id is not None
    first = asyncio.run(consumer.process_event(created.outbox_id))

    with sessions.begin() as session:
        edited = SlackRepository(session).ingest_event(
            _slack_knowledge_event(
                "Ev-policy-edited",
                "EDIT",
                "1725000101.000100",
                "1725000100.000000",
                "Release policy requires two approvals.",
                revision_ts="1725000101.000000",
            )
        )
    assert edited.outbox_id is not None
    second = asyncio.run(consumer.process_event(edited.outbox_id))
    assert second.node_ids == first.node_ids

    with sessions.begin() as session:
        deleted = SlackRepository(session).ingest_event(
            _slack_knowledge_event(
                "Ev-policy-deleted",
                "DELETE",
                "1725000102.000100",
                "1725000100.000000",
                None,
                revision_ts="1725000102.000000",
            )
        )
    assert deleted.outbox_id is not None
    third = asyncio.run(consumer.process_event(deleted.outbox_id))
    assert third.node_ids == first.node_ids

    with sessions() as session:
        candidate = session.execute(
            select(KnowledgeNode).where(KnowledgeNode.entity_type == "SLACK_KNOWLEDGE_CANDIDATE")
        ).scalar_one()
        evidence = KnowledgeService(session).evidence_for("NODE", candidate.id)
        revisions = tuple(
            session.scalars(select(SlackMessageRevision).order_by(SlackMessageRevision.revision))
        )
        active_nodes, _, _ = KnowledgeService(session).list_nodes()
        assert candidate.status == "RETRACTED"
        assert candidate.properties["classificationReason"] == "source_message_deleted"
        assert candidate.valid_from is not None and candidate.valid_to is not None
        assert candidate.valid_from < candidate.valid_to
        assert candidate.id not in {node.id for node in active_nodes}
        assert [revision.body for revision in revisions] == [
            "Release policy requires one approval.",
            "Release policy requires two approvals.",
            None,
        ]
        assert {item.source_id for item in evidence} == {revision.id for revision in revisions}
        assert session.scalar(select(func.count()).select_from(KnowledgeSourceRecord)) == 3


def test_slack_duplicate_and_out_of_order_projection_has_one_authoritative_graph(
    knowledge_engine: Engine,
) -> None:
    sessions = sessionmaker(knowledge_engine, expire_on_commit=False)
    _allow_slack_knowledge(sessions)
    events = (
        _slack_knowledge_event(
            "Ev-order-root",
            "ROOT",
            "1725000200.000100",
            "1725000200.000000",
            "Root ordering requirement.",
        ),
        _slack_knowledge_event(
            "Ev-order-reply",
            "REPLY",
            "1725000201.000100",
            "1725000201.000000",
            "Reply ordering requirement version one.",
            thread_ts="1725000200.000000",
        ),
        _slack_knowledge_event(
            "Ev-order-edit",
            "EDIT",
            "1725000202.000100",
            "1725000201.000000",
            "Reply ordering requirement version two.",
            thread_ts="1725000200.000000",
            revision_ts="1725000202.000000",
        ),
    )
    outbox_ids: list[uuid.UUID] = []
    work_item_id: uuid.UUID | None = None
    for event in events:
        with sessions.begin() as session:
            result = SlackRepository(session).ingest_event(event)
            assert result.outbox_id is not None
            outbox_ids.append(result.outbox_id)
            work_item_id = result.work_item_id
    assert work_item_id is not None
    with sessions.begin() as session:
        SlackRepository(session).store_parse_result(
            work_item_id=work_item_id,
            source_version=3,
            parser_version="projection-test-v1",
            status="PARSED",
            result={"developmentItem": "ordered"},
        )
        equivalent_version_event = session.execute(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == work_item_id,
                OutboxEvent.event_type == "SLACK_PARSE_RESULT_STORED",
            )
        ).scalar_one()
        equivalent_version_event_id = equivalent_version_event.id

    provider = _BundleCandidateProvider()
    consumer = _knowledge_consumer(sessions, provider)
    newest = asyncio.run(consumer.process_event(outbox_ids[2]))
    oldest = asyncio.run(consumer.process_event(outbox_ids[0]))
    middle = asyncio.run(consumer.process_event(outbox_ids[1]))
    replay = asyncio.run(consumer.process_event(outbox_ids[2]))
    equivalent = asyncio.run(consumer.process_event(equivalent_version_event_id))
    assert not newest.stale
    assert oldest.stale and middle.stale and equivalent.stale
    assert replay.replayed
    assert provider.calls == 1

    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(KnowledgeNode)) == 3
        assert session.scalar(select(func.count()).select_from(KnowledgeSourceRecord)) == 3
        assert session.scalar(select(func.count()).select_from(KnowledgeProjectionEvent)) == 3
        assert session.scalar(select(func.count()).select_from(SlackMessage)) == 2
        assert session.scalar(select(func.count()).select_from(SlackMessageRevision)) == 3
        assert session.scalar(select(func.count()).select_from(KnowledgeEvidence)) == 7
        checkpoints = tuple(
            session.scalars(
                select(KnowledgeProjectionEvent).order_by(
                    KnowledgeProjectionEvent.aggregate_version
                )
            )
        )
        assert [item.aggregate_version for item in checkpoints] == [1, 2, 3]
        assert [item.result["stale"] for item in checkpoints] == [True, True, False]


def test_full_rebuild_converges_with_incremental_and_preserves_source_ledgers(
    knowledge_engine: Engine,
) -> None:
    sessions = sessionmaker(knowledge_engine, expire_on_commit=False)
    _, case_id = _case_library(sessions, work_item_key="WORK-REBUILD")
    with sessions.begin() as session:
        cycle = CycleRepository(session).create(
            name="Rebuild convergence cycle",
            starts_at=date(2026, 8, 26),
            scope_type="WORK_ITEM",
            scope_value="WORK-REBUILD",
            audit=_audit("rebuild-cycle"),
        )
        cycle_id = cycle.cycle.id
        item_id = cycle.items[0].id
    with sessions.begin() as session:
        CycleRepository(session).start(
            cycle_id=cycle_id,
            base_resource_version=1,
            audit=_audit("rebuild-cycle-start"),
        )
    with sessions.begin() as session:
        attempt = CycleRepository(session).start_attempt(
            cycle_item_id=item_id,
            platform=ExecutionPlatform.WEB,
            audit=_audit("rebuild-attempt-start"),
        )
        attempt_id = attempt.id
    with sessions.begin() as session:
        CycleRepository(session).complete_attempt(
            attempt_id=attempt_id,
            status=ExecutionStatus.FAIL,
            actual_result="rebuild HTTP 500",
            note="rebuild trace",
            audit=_audit("rebuild-attempt-complete"),
        )
    with sessions.begin() as session:
        issue = IssueRepository(session).create_from_attempt(
            attempt_id=attempt_id,
            summary="Rebuild projection failure",
            audit=_audit("rebuild-issue"),
        )
        issue_id = issue.issue.id

    _allow_slack_knowledge(sessions)
    slack_events = (
        _slack_knowledge_event(
            "Ev-rebuild-create",
            "ROOT",
            "1725000300.000100",
            "1725000300.000000",
            "Rebuild policy requires one approval.",
        ),
        _slack_knowledge_event(
            "Ev-rebuild-edit",
            "EDIT",
            "1725000301.000100",
            "1725000300.000000",
            "Rebuild policy requires two approvals.",
            revision_ts="1725000301.000000",
        ),
        _slack_knowledge_event(
            "Ev-rebuild-delete",
            "DELETE",
            "1725000302.000100",
            "1725000300.000000",
            None,
            revision_ts="1725000302.000000",
        ),
    )
    for slack_event in slack_events:
        with sessions.begin() as session:
            result = SlackRepository(session).ingest_event(slack_event)
            assert result.outbox_id is not None

    provider = _BundleCandidateProvider()
    consumer = _knowledge_consumer(sessions, provider)

    async def drain_incremental() -> None:
        while await consumer.run_once() is not None:
            pass

    asyncio.run(drain_incremental())
    with sessions() as session:
        incremental_snapshot = canonical_knowledge_snapshot(session)
        incremental_node_ids = set(session.scalars(select(KnowledgeNode.id)))
        source_digest = authoritative_ledger_digest(session)
        outbox_state = tuple(
            session.execute(
                select(
                    OutboxEvent.id,
                    OutboxEvent.status,
                    OutboxEvent.attempts,
                    OutboxEvent.processed_at,
                    OutboxEvent.payload,
                ).order_by(OutboxEvent.id)
            )
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(KnowledgeNode)
                .where(
                    KnowledgeNode.entity_type == "TEST_CASE",
                    KnowledgeNode.entity_id == case_id,
                )
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(KnowledgeNode)
                .where(
                    KnowledgeNode.entity_type == "EXECUTION_ATTEMPT",
                    KnowledgeNode.entity_id == attempt_id,
                )
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(KnowledgeNode)
                .where(
                    KnowledgeNode.entity_type == "ISSUE",
                    KnowledgeNode.entity_id == issue_id,
                )
            )
            == 1
        )

    rebuilder = KnowledgeProjectionRebuilder(
        session_factory=sessions,
        consumer=consumer,
    )
    plan = rebuilder.plan()
    assert plan.current_snapshot.digest == incremental_snapshot.digest
    assert plan.source_ledger_digest == source_digest
    assert tuple(plan.as_dict()["writeScope"]) == PROJECTION_RESET_TABLES
    assert "outbox_events" not in PROJECTION_RESET_TABLES
    assert plan.event_status_counts == {"PUBLISHED": len(plan.events)}

    with pytest.raises(UnsafeKnowledgeRebuild, match="outbox changed after preview"):
        asyncio.run(
            rebuilder.apply(
                confirmation=REBUILD_CONFIRMATION,
                expected_event_order_digest="0" * 64,
                expected_source_ledger_digest=plan.source_ledger_digest,
            )
        )
    with sessions() as session:
        assert canonical_knowledge_snapshot(session).digest == incremental_snapshot.digest
        assert authoritative_ledger_digest(session) == source_digest

    report = asyncio.run(
        rebuilder.apply(
            confirmation=REBUILD_CONFIRMATION,
            expected_event_order_digest=plan.event_order_digest,
            expected_source_ledger_digest=plan.source_ledger_digest,
        )
    )
    assert report.before_digest == incremental_snapshot.digest
    assert report.after_snapshot.digest == incremental_snapshot.digest
    assert report.after_snapshot.payload == incremental_snapshot.payload
    assert report.source_ledger_digest == source_digest

    with sessions() as session:
        assert set(session.scalars(select(KnowledgeNode.id))).isdisjoint(incremental_node_ids)
        assert authoritative_ledger_digest(session) == source_digest
        assert (
            tuple(
                session.execute(
                    select(
                        OutboxEvent.id,
                        OutboxEvent.status,
                        OutboxEvent.attempts,
                        OutboxEvent.processed_at,
                        OutboxEvent.payload,
                    ).order_by(OutboxEvent.id)
                )
            )
            == outbox_state
        )

    second_plan = rebuilder.plan()
    second_report = asyncio.run(
        rebuilder.apply(
            confirmation=REBUILD_CONFIRMATION,
            expected_event_order_digest=second_plan.event_order_digest,
            expected_source_ledger_digest=second_plan.source_ledger_digest,
        )
    )
    assert second_report.before_digest == incremental_snapshot.digest
    assert second_report.after_snapshot.digest == incremental_snapshot.digest
    assert second_report.source_ledger_digest == source_digest
    assert provider.calls == 3


def test_hybrid_search_merges_grounded_signals_with_stable_filtered_fallback(
    knowledge_engine: Engine,
) -> None:
    sessions = sessionmaker(knowledge_engine, expire_on_commit=False)
    now = datetime.now(UTC)
    with sessions.begin() as session:
        repository = KnowledgeRepository(session)
        feature = repository.create_node(
            node_type="FEATURE",
            label="Password reset",
            evidence=_evidence("feature"),
        )
        exact = repository.create_node(
            node_type="REQUIREMENT",
            label="Password reset exact requirement",
            evidence=_evidence("exact"),
        )
        vector = repository.create_node(
            node_type="CONSTRAINT",
            label="Semantic-only constraint",
            evidence=_evidence("vector"),
        )
        graph = repository.create_node(
            node_type="TEST_CASE",
            label="Graph-only regression case",
            evidence=_evidence("graph"),
        )
        test_case_evidence = EvidenceInput.from_content(
            source_kind=KnowledgeSourceKind.LEDGER_ENTITY,
            entity_type="TEST_CASE_VERSION",
            entity_id=uuid.uuid4(),
            revision_key="1",
            content="test case source",
            recorded_at=now,
        )
        light_rag = repository.create_node(
            node_type="RISK",
            label="Auxiliary-only risk",
            evidence=test_case_evidence,
        )
        combined = repository.create_node(
            node_type="DECISION",
            label="Password reset combined decision",
            evidence=_evidence("combined"),
        )
        repository.add_evidence(node_id=combined.id, evidence=test_case_evidence)
        future = repository.create_node(
            node_type="REQUIREMENT",
            label="Password reset future requirement",
            evidence=_evidence("future"),
            valid_from=now + timedelta(days=1),
        )
        expired = repository.create_node(
            node_type="CONSTRAINT",
            label="Password reset invalid active constraint",
            evidence=_evidence("expired"),
            valid_from=now - timedelta(days=2),
            valid_to=now - timedelta(days=1),
        )
        retracted = repository.create_node(
            node_type="RISK",
            label="Password reset retracted risk",
            evidence=_evidence("retracted"),
            status="RETRACTED",
            valid_from=now - timedelta(days=2),
            valid_to=now - timedelta(days=1),
        )
        repository.create_edge(
            source_node_id=graph.id,
            predicate="COVERS",
            target_node_id=feature.id,
            evidence=_evidence("graph edge"),
        )
        repository.create_edge(
            source_node_id=combined.id,
            predicate="APPLIES_TO",
            target_node_id=feature.id,
            evidence=_evidence("combined edge"),
        )
        query_vector = tuple([1.0, *([0.0] * (BGE_M3_DIMENSION - 1))])
        for node in (vector, combined):
            repository.put_embedding(
                owner_type=KnowledgeOwnerType.NODE,
                owner_id=node.id,
                model_version="hybrid-test-v1",
                embedding=query_vector,
                content=node.label,
            )
        light_rag_evidence_id = KnowledgeService(session).evidence_for("NODE", light_rag.id)[0].id

    unresolved_id = uuid.uuid4()
    hints = (
        str(light_rag.id),
        str(light_rag.id),
        str(light_rag_evidence_id),
        str(combined.id),
        str(unresolved_id),
    )

    async def search(
        session: Session,
        light_index: _StaticLightRAG,
        *,
        source_types: tuple[str, ...] = (),
        node_types: tuple[str, ...] = (),
        statuses: tuple[str, ...] = ("ACTIVE",),
        limit: int = 200,
        cursor: str | None = None,
    ):
        return await KnowledgeService(
            session,
            embedding_provider=_QueryEmbeddingProvider(),
            light_rag=light_index,  # type: ignore[arg-type]
        ).hybrid_search(
            query="Password reset",
            statuses=statuses,
            source_types=source_types,
            node_types=node_types,
            feature_id=feature.id if statuses == ("ACTIVE",) else None,
            limit=limit,
            cursor=cursor,
        )

    with sessions() as session:
        result = asyncio.run(search(session, _StaticLightRAG(hints)))
        by_id = {match.node.id: match for match in result.matches}
        assert set(by_id) == {feature.id, exact.id, vector.id, graph.id, light_rag.id, combined.id}
        assert len(result.matches) == len({match.node.id for match in result.matches})
        assert future.id not in by_id
        assert expired.id not in by_id
        assert retracted.id not in by_id

        assert {"EXACT_TEXT", "VECTOR", "FEATURE_PROXIMITY", "LIGHTRAG"}.issubset(
            by_id[combined.id].signals
        )
        assert "EXACT_TEXT" in by_id[exact.id].signals
        assert not {"VECTOR", "FEATURE_PROXIMITY", "LIGHTRAG"}.intersection(by_id[exact.id].signals)
        assert "VECTOR" in by_id[vector.id].signals
        assert not {"EXACT_TEXT", "FEATURE_PROXIMITY", "LIGHTRAG"}.intersection(
            by_id[vector.id].signals
        )
        assert "FEATURE_PROXIMITY" in by_id[graph.id].signals
        assert not {"EXACT_TEXT", "VECTOR", "LIGHTRAG"}.intersection(by_id[graph.id].signals)
        assert "LIGHTRAG" in by_id[light_rag.id].signals
        assert not {"EXACT_TEXT", "VECTOR", "FEATURE_PROXIMITY"}.intersection(
            by_id[light_rag.id].signals
        )
        assert [match.node.id for match in result.matches] == [
            match.node.id
            for match in sorted(result.matches, key=lambda item: (-item.score, item.node.id.hex))
        ]

        test_case_only = asyncio.run(
            search(
                session,
                _StaticLightRAG(hints),
                source_types=("TEST_CASE_VERSION",),
            )
        )
        assert {match.node.id for match in test_case_only.matches} == {
            light_rag.id,
            combined.id,
        }
        risk_only = asyncio.run(search(session, _StaticLightRAG(hints), node_types=("RISK",)))
        assert [match.node.id for match in risk_only.matches] == [light_rag.id]
        historical = asyncio.run(
            search(
                session,
                _StaticLightRAG(hints),
                statuses=("RETRACTED",),
            )
        )
        assert [match.node.id for match in historical.matches] == [retracted.id]

        first_page = asyncio.run(search(session, _StaticLightRAG(hints), limit=2))
        assert first_page.next_cursor is not None
        second_page = asyncio.run(
            search(
                session,
                _StaticLightRAG(hints),
                cursor=first_page.next_cursor,
            )
        )
        assert [match.node.id for match in (*first_page.matches, *second_page.matches)] == [
            match.node.id for match in result.matches
        ]

        no_hints = asyncio.run(search(session, _StaticLightRAG()))
        unresolvable = asyncio.run(
            search(session, _StaticLightRAG((str(unresolved_id), str(unresolved_id))))
        )
        unavailable = asyncio.run(search(session, _StaticLightRAG(unavailable=True)))
        expected_ids = [match.node.id for match in no_hints.matches]
        assert [match.node.id for match in unresolvable.matches] == expected_ids
        assert [match.node.id for match in unavailable.matches] == expected_ids
        assert all("LIGHTRAG" not in match.signals for match in unresolvable.matches)
        assert all("LIGHTRAG" not in match.signals for match in unavailable.matches)
        assert no_hints.degraded is False
        assert unavailable.degradation_reasons == ("LIGHTRAG_UNAVAILABLE",)


def test_hybrid_search_survives_auxiliary_transport_failure_timeout_and_recovery(
    knowledge_engine: Engine,
) -> None:
    """Keep real PostgreSQL available while the separate LightRAG boundary fails."""

    sessions = sessionmaker(knowledge_engine, expire_on_commit=False)
    now = datetime.now(UTC)
    with sessions.begin() as session:
        repository = KnowledgeRepository(session)
        feature = repository.create_node(
            node_type="FEATURE",
            label="Password reset",
            evidence=_evidence("failover feature"),
            recorded_at=now,
        )
        exact = repository.create_node(
            node_type="REQUIREMENT",
            label="Password reset authoritative requirement",
            evidence=_evidence("failover exact"),
            recorded_at=now,
        )
        vector = repository.create_node(
            node_type="CONSTRAINT",
            label="Authoritative semantic constraint",
            evidence=_evidence("failover vector"),
            recorded_at=now,
        )
        graph = repository.create_node(
            node_type="TEST_CASE",
            label="Authoritative graph case",
            evidence=_evidence("failover graph"),
            recorded_at=now,
        )
        hinted = repository.create_node(
            node_type="RISK",
            label="Auxiliary current risk",
            evidence=_evidence("failover hint"),
            recorded_at=now,
        )
        future_hint = repository.create_node(
            node_type="RISK",
            label="Auxiliary future risk",
            evidence=_evidence("failover future hint"),
            recorded_at=now,
            valid_from=now + timedelta(days=1),
        )
        retracted_hint = repository.create_node(
            node_type="RISK",
            label="Auxiliary retracted risk",
            evidence=_evidence("failover retracted hint"),
            status="RETRACTED",
            recorded_at=now,
            valid_from=now - timedelta(days=1),
            valid_to=now,
        )
        repository.create_edge(
            source_node_id=graph.id,
            predicate="COVERS",
            target_node_id=feature.id,
            evidence=_evidence("failover graph edge"),
            recorded_at=now,
        )
        query_vector = tuple([1.0, *([0.0] * (BGE_M3_DIMENSION - 1))])
        repository.put_embedding(
            owner_type=KnowledgeOwnerType.NODE,
            owner_id=vector.id,
            model_version="failover-test-v1",
            embedding=query_vector,
            content=vector.label,
        )

    core = _FaultInjectingLightRAGCore()
    adapter = _fault_injecting_light_rag(core)
    assert (
        asyncio.run(adapter.initialize(apache_age_available=True)).status is LightRAGCallStatus.OK
    )
    hint_context = "\n".join(
        (
            str(hinted.id),
            str(future_hint.id),
            str(retracted_hint.id),
            str(uuid.uuid4()),
        )
    )

    async def search(session: Session) -> HybridSearchResult:
        return await KnowledgeService(
            session,
            embedding_provider=_QueryEmbeddingProvider(),
            light_rag=adapter,
        ).hybrid_search(
            query="Password reset",
            statuses=("ACTIVE",),
            source_types=(),
            node_types=(),
            feature_id=feature.id,
            limit=200,
            cursor=None,
        )

    def fallback_signature(result: HybridSearchResult) -> tuple[tuple[object, ...], ...]:
        matches = result.matches
        return tuple((match.node.id, match.signals, match.feature_distance) for match in matches)

    core.context = "no auxiliary UUID hints"
    with sessions() as session:
        baseline = asyncio.run(search(session))
    assert baseline.degraded is False
    assert [match.node.id for match in baseline.matches] == [
        match.node.id
        for match in sorted(
            baseline.matches,
            key=lambda item: (-item.score, item.node.id.hex),
        )
    ]
    assert {match.node.id for match in baseline.matches} == {
        feature.id,
        exact.id,
        vector.id,
        graph.id,
    }

    core.context = hint_context
    with sessions() as session:
        normal = asyncio.run(search(session))
    normal_by_id = {match.node.id: match for match in normal.matches}
    assert normal.degraded is False
    assert "LIGHTRAG" in normal_by_id[hinted.id].signals
    assert future_hint.id not in normal_by_id
    assert retracted_hint.id not in normal_by_id
    with sessions() as session:
        for match in normal.matches:
            assert session.get(KnowledgeNode, match.node.id) is not None
            assert session.get(KnowledgeEvidence, match.evidence.id) is not None

    core.query_error = ConnectionError("auxiliary pool severed")
    with sessions() as session:
        failed = asyncio.run(search(session))
    assert failed.degraded is True
    assert failed.degradation_reasons == ("LIGHTRAG_UNAVAILABLE",)
    assert fallback_signature(failed) == fallback_signature(baseline)
    assert [match.score for match in failed.matches] == pytest.approx(
        [match.score for match in baseline.matches], abs=1e-6
    )
    assert all("LIGHTRAG" not in match.signals for match in failed.matches)

    async def concurrent_fallbacks() -> tuple[HybridSearchResult, HybridSearchResult]:
        with sessions() as first_session, sessions() as second_session:
            first, second = await asyncio.gather(
                search(first_session),
                search(second_session),
            )
            return first, second

    concurrent = asyncio.run(concurrent_fallbacks())
    assert all(fallback_signature(result) == fallback_signature(baseline) for result in concurrent)
    assert all(result.degradation_reasons == ("LIGHTRAG_UNAVAILABLE",) for result in concurrent)

    core.query_error = None
    core.query_delay_seconds = 0.05
    with sessions() as session:
        timed_out = asyncio.run(search(session))
    assert fallback_signature(timed_out) == fallback_signature(baseline)
    assert timed_out.degradation_reasons == ("LIGHTRAG_UNAVAILABLE",)
    assert all("LIGHTRAG" not in match.signals for match in timed_out.matches)

    core.query_delay_seconds = 0.0
    with sessions() as session:
        automatically_recovered = asyncio.run(search(session))
    assert (
        "LIGHTRAG"
        in {match.node.id: match for match in automatically_recovered.matches}[hinted.id].signals
    )
    assert automatically_recovered.degraded is False

    core.initialize_error = ConnectionError("replacement auxiliary pool unavailable")
    failed_reinitialize = asyncio.run(adapter.initialize(apache_age_available=True))
    calls_before_unready_search = core.query_calls
    with sessions() as session:
        unready = asyncio.run(search(session))
    assert failed_reinitialize.status is LightRAGCallStatus.UNAVAILABLE
    assert core.query_calls == calls_before_unready_search
    assert fallback_signature(unready) == fallback_signature(baseline)
    assert all("LIGHTRAG" not in match.signals for match in unready.matches)

    core.initialize_error = None
    assert (
        asyncio.run(adapter.initialize(apache_age_available=True)).status is LightRAGCallStatus.OK
    )
    with sessions() as session:
        reinitialized = asyncio.run(search(session))
    assert reinitialized.degraded is False
    assert (
        "LIGHTRAG" in {match.node.id: match for match in reinitialized.matches}[hinted.id].signals
    )
    assert asyncio.run(adapter.finalize()).status is LightRAGCallStatus.OK
