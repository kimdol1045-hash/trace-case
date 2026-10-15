from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import Engine, event, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from tc_backend.db.models import (
    GenerationExecutionSchedule,
    GenerationContext,
    GenerationJob,
    OntologySchema,
    OutboxEvent,
    SlackWorkItem,
)
from tc_backend.db.audit import AuditContext
from tc_backend.db.constants import AI_INBOX_FOLDER_ID
from tc_backend.db.models import AuditSourceType
from tc_backend.generation.application import (
    GenerationApplicationService,
    automatic_apply_command,
)
from tc_backend.db.slack import SlackEventInput, SlackRepository
from tc_backend.knowledge.embedding import EmbeddingBatch
from tc_backend.knowledge.extraction import SlackKnowledgeExtractor
from tc_backend.knowledge.models import (
    CandidateRelation,
    CandidateRelationKind,
    KnowledgeNodeType,
    ProviderEvidence,
    ProviderExtraction,
    ProviderKnowledgeCandidate,
    SlackSourceBundle,
    SlackSourceRevision,
)
from tc_backend.repositories.generation_repository import GenerationRepository
from tc_backend.worker.knowledge_consumer import KnowledgeOutboxConsumer
from tc_worker.slack_runtime import SlackWorkerRuntime
from test_generation_apply_api_postgresql import _arm_automatic_materialization

pytestmark = pytest.mark.postgres

_LEDGER_TABLE = "generation_change_assessments"
_IMPACT_SCHEMA_VERSION = "generation-impact/1.0.0"
_CONFIG_SCHEMA_VERSION = "generation-impact-config/1.0.0"
_ROOT_BODY = (
    "개발건: 결제 승인\n"
    "요청 메뉴: 결제 > 승인\n"
    "기능 설명: 카드 승인 정책\n"
    "요청 내용: 결제 실패 잠금과 감사 기록을 검증한다.\n"
    "결정: 결제 실패 잠금은 3회다."
)
_OLD_DECISION = "결제 실패 잠금은 3회다."
_NEW_DECISION = "결제 실패 잠금은 5회다."


class _DeterministicEmbedding:
    async def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        vectors: list[tuple[float, ...]] = []
        hashes: list[str] = []
        for value in texts:
            vector = [0.0] * 1024
            vector[0] = 1.0
            vectors.append(tuple(vector))
            hashes.append(hashlib.sha256(value.encode("utf-8")).hexdigest())
        return EmbeddingBatch(
            model="bge-m3:pe-se2-t03-contract",
            dimension=1024,
            vectors=tuple(vectors),
            content_hashes=tuple(hashes),
        )


ExtractionStrategy = Callable[
    [SlackSourceBundle], tuple[ProviderKnowledgeCandidate, ...]
]


class _ExtractionProvider:
    def __init__(
        self,
        strategy: ExtractionStrategy,
        *,
        model: str = "impact-contract-v1",
    ) -> None:
        self._strategy = strategy
        self._model = model

    async def extract(self, bundle: SlackSourceBundle) -> ProviderExtraction:
        return ProviderExtraction(
            provider="deterministic-pe-se2-t03",
            model=self._model,
            schema_version="knowledge-extraction-v1",
            candidates=self._strategy(bundle),
        )


def _revision_containing(
    bundle: SlackSourceBundle,
    excerpt: str,
    *,
    newest: bool = True,
) -> SlackSourceRevision:
    matches = tuple(
        revision
        for revision in bundle.revisions
        if revision.body is not None and excerpt in revision.body
    )
    assert matches, f"fixture has no revision containing {excerpt!r}"
    return matches[-1 if newest else 0]


def _stable_decision(bundle: SlackSourceBundle) -> tuple[ProviderKnowledgeCandidate, ...]:
    revision = _revision_containing(bundle, _OLD_DECISION)
    return (
        ProviderKnowledgeCandidate(
            candidate_id="payment-lock-v1",
            node_type=KnowledgeNodeType.DECISION,
            content=_OLD_DECISION,
            topic_key="payment-lock-policy",
            confidence=0.99,
            evidence=(
                ProviderEvidence(
                    source_revision_id=revision.source_revision_id,
                    excerpt=_OLD_DECISION,
                ),
            ),
        ),
    )


def _superseding_decision(
    bundle: SlackSourceBundle,
) -> tuple[ProviderKnowledgeCandidate, ...]:
    old_revision = _revision_containing(bundle, _OLD_DECISION, newest=False)
    old = ProviderKnowledgeCandidate(
        candidate_id="payment-lock-v1",
        node_type=KnowledgeNodeType.DECISION,
        content=_OLD_DECISION,
        topic_key="payment-lock-policy",
        confidence=0.99,
        evidence=(
            ProviderEvidence(
                source_revision_id=old_revision.source_revision_id,
                excerpt=_OLD_DECISION,
            ),
        ),
    )
    new_revisions = tuple(
        revision
        for revision in bundle.revisions
        if revision.body is not None and _NEW_DECISION in revision.body
    )
    if not new_revisions:
        return (old,)
    new_revision = new_revisions[-1]
    return (
        old,
        ProviderKnowledgeCandidate(
            candidate_id="payment-lock-v2",
            node_type=KnowledgeNodeType.DECISION,
            content=_NEW_DECISION,
            topic_key="payment-lock-policy",
            confidence=0.99,
            evidence=(
                ProviderEvidence(
                    source_revision_id=new_revision.source_revision_id,
                    excerpt=_NEW_DECISION,
                ),
            ),
            relations=(
                CandidateRelation(
                    kind=CandidateRelationKind.SUPERSEDES,
                    target_candidate_id="payment-lock-v1",
                ),
            ),
        ),
    )


def _ambiguous_decision(
    bundle: SlackSourceBundle,
) -> tuple[ProviderKnowledgeCandidate, ...]:
    old = _stable_decision(bundle)[0]
    new_revision = _revision_containing(bundle, _NEW_DECISION)
    return (
        old,
        ProviderKnowledgeCandidate(
            candidate_id="payment-lock-conflict",
            node_type=KnowledgeNodeType.DECISION,
            content=_NEW_DECISION,
            topic_key="payment-lock-policy",
            confidence=0.99,
            evidence=(
                ProviderEvidence(
                    source_revision_id=new_revision.source_revision_id,
                    excerpt=_NEW_DECISION,
                ),
            ),
        ),
    )


def _consumer(
    sessions: sessionmaker[Session],
    strategy: ExtractionStrategy = _stable_decision,
    *,
    extractor_model: str = "impact-contract-v1",
) -> KnowledgeOutboxConsumer:
    return KnowledgeOutboxConsumer(
        session_factory=sessions,
        embedding_provider=_DeterministicEmbedding(),
        extractor=SlackKnowledgeExtractor(
            _ExtractionProvider(strategy, model=extractor_model)
        ),
    )


def _past_timestamp(offset_seconds: int = 0) -> str:
    value = datetime.now(UTC) - timedelta(hours=4) + timedelta(seconds=offset_seconds)
    return f"{int(value.timestamp())}.000100"


def _later_timestamp(root_timestamp: str, ordinal: int) -> str:
    return f"{Decimal(root_timestamp) + Decimal(ordinal):.6f}"


def _seed_source(
    sessions: sessionmaker[Session],
    *,
    suffix: str = "MAIN",
    body: str = _ROOT_BODY,
) -> tuple[uuid.UUID, uuid.UUID, str]:
    root_timestamp = _past_timestamp()
    with sessions.begin() as session:
        repository = SlackRepository(session)
        repository.put_channel(
            slack_workspace_id=f"T_PE_SE2_T03_{suffix}",
            channel_id=f"C_PE_SE2_T03_{suffix}",
            channel_name="automatic-impact-contract",
        )
        ingested = repository.ingest_event(
            SlackEventInput(
                event_id=f"EvPeSe2T03{suffix}Root001",
                slack_workspace_id=f"T_PE_SE2_T03_{suffix}",
                channel_id=f"C_PE_SE2_T03_{suffix}",
                event_type="ROOT",
                event_ts=root_timestamp,
                message_ts=root_timestamp,
                body=body,
                author_id="U_PE_SE2_T03",
                permalink=(
                    f"https://synthetic.invalid/archives/C_PE_SE2_T03_{suffix}/"
                    f"p{root_timestamp.replace('.', '')}"
                ),
                raw_payload={"delivery": f"root-{suffix}-001"},
            )
        )
        assert ingested.work_item_id is not None
        assert ingested.outbox_id is not None
        return ingested.work_item_id, ingested.outbox_id, root_timestamp


def _append_reply(
    sessions: sessionmaker[Session],
    *,
    work_item_id: uuid.UUID,
    ordinal: int,
    body: str,
    event_suffix: str,
) -> str:
    with sessions.begin() as session:
        item = session.get(SlackWorkItem, work_item_id)
        assert item is not None
        reply_timestamp = _later_timestamp(item.message_ts, ordinal)
        ingested = SlackRepository(session).ingest_event(
            SlackEventInput(
                event_id=f"EvPeSe2T03Reply{event_suffix}",
                slack_workspace_id=item.slack_workspace_id,
                channel_id=item.channel_id,
                event_type="REPLY",
                event_ts=reply_timestamp,
                message_ts=reply_timestamp,
                thread_ts=item.message_ts,
                revision_ts=reply_timestamp,
                body=body,
                author_id="U_PE_SE2_T03_REPLY",
                permalink=f"{item.permalink}?thread={ordinal}",
                raw_payload={"delivery": f"reply-{event_suffix}"},
            )
        )
        assert ingested.revision_id is not None
        return reply_timestamp


def _edit_root(
    sessions: sessionmaker[Session],
    *,
    work_item_id: uuid.UUID,
    ordinal: int,
    body: str,
    event_suffix: str,
) -> None:
    with sessions.begin() as session:
        item = session.get(SlackWorkItem, work_item_id)
        assert item is not None
        revision_timestamp = _later_timestamp(item.message_ts, ordinal)
        ingested = SlackRepository(session).ingest_event(
            SlackEventInput(
                event_id=f"EvPeSe2T03Edit{event_suffix}",
                slack_workspace_id=item.slack_workspace_id,
                channel_id=item.channel_id,
                event_type="EDIT",
                event_ts=revision_timestamp,
                message_ts=item.message_ts,
                revision_ts=revision_timestamp,
                body=body,
                author_id="U_PE_SE2_T03",
                permalink=item.permalink,
                raw_payload={"delivery": f"edit-{event_suffix}"},
            )
        )
        assert ingested.revision_id is not None


def _delete_reply(
    sessions: sessionmaker[Session],
    *,
    work_item_id: uuid.UUID,
    message_timestamp: str,
    ordinal: int,
) -> None:
    with sessions.begin() as session:
        item = session.get(SlackWorkItem, work_item_id)
        assert item is not None
        revision_timestamp = _later_timestamp(item.message_ts, ordinal)
        ingested = SlackRepository(session).ingest_event(
            SlackEventInput(
                event_id="EvPeSe2T03DeleteEvidence",
                slack_workspace_id=item.slack_workspace_id,
                channel_id=item.channel_id,
                event_type="DELETE",
                event_ts=revision_timestamp,
                message_ts=message_timestamp,
                thread_ts=item.message_ts,
                revision_ts=revision_timestamp,
                body=None,
                author_id="U_PE_SE2_T03_REPLY",
                permalink=f"{item.permalink}?deleted=1",
                raw_payload={"delivery": "delete-evidence"},
            )
        )
        assert ingested.revision_id is not None


def _ingest_distinct_duplicate_delivery(
    sessions: sessionmaker[Session],
    *,
    work_item_id: uuid.UUID,
    root_timestamp: str,
) -> None:
    with sessions.begin() as session:
        item = session.get(SlackWorkItem, work_item_id)
        assert item is not None
        delivery_timestamp = _later_timestamp(root_timestamp, 90)
        result = SlackRepository(session).ingest_event(
            SlackEventInput(
                event_id="EvPeSe2T03DistinctDelivery",
                slack_workspace_id=item.slack_workspace_id,
                channel_id=item.channel_id,
                event_type="ROOT",
                event_ts=delivery_timestamp,
                message_ts=root_timestamp,
                revision_ts=root_timestamp,
                body=_ROOT_BODY,
                author_id="U_PE_SE2_T03",
                permalink=item.permalink,
                raw_payload={"delivery": "distinct-transport"},
            )
        )
        assert not result.duplicate
        assert result.revision_id is None
        session.refresh(item)
        assert item.source_version == 1


def _ingest_late_old_root_revision(
    sessions: sessionmaker[Session],
    *,
    work_item_id: uuid.UUID,
) -> None:
    with sessions.begin() as session:
        item = session.get(SlackWorkItem, work_item_id)
        assert item is not None
        late_delivery_timestamp = _later_timestamp(item.message_ts, 120)
        old_revision_timestamp = f"{Decimal(item.message_ts) - Decimal(1):.6f}"
        result = SlackRepository(session).ingest_event(
            SlackEventInput(
                event_id="EvPeSe2T03LateOldRevision",
                slack_workspace_id=item.slack_workspace_id,
                channel_id=item.channel_id,
                event_type="EDIT",
                event_ts=late_delivery_timestamp,
                message_ts=item.message_ts,
                revision_ts=old_revision_timestamp,
                body="전송이 늦어진 과거 메모이며 현재 본문을 대체하지 않는다.",
                author_id="U_PE_SE2_T03",
                permalink=item.permalink,
                raw_payload={"delivery": "late-old-revision"},
            )
        )
        assert result.revision_id is not None
        session.refresh(item)
        assert item.source_version == 2


def _prepare_handoff(
    sessions: sessionmaker[Session],
    *,
    work_item_id: uuid.UUID,
    source_version: int,
) -> uuid.UUID:
    payload = {"workItemId": str(work_item_id), "sourceVersion": source_version}
    runtime = SlackWorkerRuntime(session_factory=sessions)
    runtime.finish_quiet_window(payload)
    runtime.parse_work_item(payload)
    with sessions() as session:
        outbox_event_id = session.scalar(
            select(OutboxEvent.id).where(
                OutboxEvent.aggregate_type == "SLACK_WORK_ITEM",
                OutboxEvent.aggregate_id == work_item_id,
                OutboxEvent.aggregate_version == source_version,
                OutboxEvent.event_type == "SLACK_PARSE_RESULT_STORED",
            )
        )
        assert outbox_event_id is not None
        return outbox_event_id


def _process_handoff(
    consumer: KnowledgeOutboxConsumer,
    *,
    outbox_event_id: uuid.UUID,
    work_item_id: uuid.UUID,
    source_version: int,
) -> object:
    return asyncio.run(
        consumer.process_automatic_generation(
            outbox_event_id=outbox_event_id,
            work_item_id=work_item_id,
            source_version=source_version,
        )
    )


def _process_current(
    sessions: sessionmaker[Session],
    consumer: KnowledgeOutboxConsumer,
    *,
    work_item_id: uuid.UUID,
    commit_baseline: bool = True,
) -> tuple[int, uuid.UUID]:
    with sessions() as session:
        item = session.get(SlackWorkItem, work_item_id)
        assert item is not None
        source_version = item.source_version
    outbox_event_id = _prepare_handoff(
        sessions,
        work_item_id=work_item_id,
        source_version=source_version,
    )
    _process_handoff(
        consumer,
        outbox_event_id=outbox_event_id,
        work_item_id=work_item_id,
        source_version=source_version,
    )
    if commit_baseline:
        _commit_test_materialization_baselines(sessions, work_item_id=work_item_id)
    return source_version, outbox_event_id


def _commit_test_materialization_baselines(
    sessions: sessionmaker[Session], *, work_item_id: uuid.UUID
) -> None:
    """Give T03 semantic-diff fixtures the committed baseline T07 now requires."""

    with sessions.begin() as session:
        roots = tuple(
            session.scalars(
                select(GenerationJob).where(
                    GenerationJob.slack_work_item_id == work_item_id,
                    GenerationJob.generation_trigger == "AUTOMATIC",
                    GenerationJob.retry_of_job_id.is_(None),
                    GenerationJob.status == "QUEUED",
                )
            )
        )
        for root in roots:
            evidence_node_id = session.scalar(
                select(GenerationContext.node_id)
                .where(
                    GenerationContext.generation_job_id == root.id,
                    GenerationContext.node_id.is_not(None),
                )
                .limit(1)
            )
            if evidence_node_id is None:
                continue
            result = {
                "outputSchemaVersion": "1.0.0",
                "cases": [
                    {
                        "summary": f"Committed baseline path {path_name}",
                        "steps": [
                            {
                                "sequence": 1,
                                "action": f"Verify committed baseline path {path_name}",
                            }
                        ],
                        "caseExpectedResult": "Baseline remains authoritative",
                        "priority": "MEDIUM",
                        "labels": ["t03", "baseline"],
                        "evidenceNodeIds": [f"knowledge_node_{evidence_node_id.hex}"],
                        "generationReason": "T03 fixture materialization authority.",
                        "inferenceOnly": False,
                        "openQuestions": [],
                    }
                    for path_name in ("alpha", "beta", "gamma", "delta", "epsilon")
                ],
                "categoryCandidate": {
                    "categoryId": f"folder_{AI_INBOX_FOLDER_ID.hex}",
                    "score": 1,
                    "alternatives": [],
                },
            }
            repository = GenerationRepository(session)
            repository.begin_execution(root.id)
            repository.succeed_execution(root.id, result)
            execution = session.scalar(
                select(GenerationExecutionSchedule).where(
                    GenerationExecutionSchedule.generation_job_id == root.id
                )
            )
            assert execution is not None
            execution.state = "TERMINAL"
            _arm_automatic_materialization(session, root)
            session.flush()
            application = GenerationApplicationService(session)
            draft = application.plan(root.id)
            application.apply(
                automatic_apply_command(draft),
                audit=AuditContext(
                    source_type=AuditSourceType.WORKER,
                    actor_label="QA Team",
                    external_actor_id="team:test",
                    reason="T03 committed baseline fixture",
                    trace_id=f"t03_baseline_{root.id.hex}",
                    idempotency_key=f"t03-materialized-baseline:{root.id}",
                ),
            )


def _require_ledger(connection: object) -> None:
    assert inspect(connection).has_table(_LEDGER_TABLE), (
        "PE-SE2-T03 requires append-only generation_change_assessments; the T02 head "
        "still regenerates every newer source_version"
    )


def _decision_rows(
    session: Session,
    work_item_id: uuid.UUID,
) -> tuple[dict[str, object], ...]:
    _require_ledger(session.connection())
    rows = session.execute(
        text(
            "SELECT id, slack_work_item_id, baseline_source_version, "
            "current_source_version, predecessor_assessment_id, "
            "materialization_baseline_generation_job_id, "
            "selected_generation_job_id, reused_generation_job_id, outcome, "
            "reason_code, changed_fields, fingerprint_schema_version, "
            "baseline_fingerprint, current_fingerprint, "
            "contract_config_schema_version, baseline_contract_config_digest, "
            "current_contract_config_digest, created_at "
            "FROM generation_change_assessments "
            "WHERE slack_work_item_id = :work_item_id "
            "ORDER BY current_source_version"
        ),
        {"work_item_id": work_item_id},
    ).mappings()
    return tuple(dict(row) for row in rows)


def _automatic_roots(
    session: Session,
    work_item_id: uuid.UUID,
) -> tuple[GenerationJob, ...]:
    return tuple(
        session.scalars(
            select(GenerationJob)
            .where(
                GenerationJob.slack_work_item_id == work_item_id,
                GenerationJob.generation_trigger == "AUTOMATIC",
                GenerationJob.retry_of_job_id.is_(None),
            )
            .order_by(GenerationJob.source_version, GenerationJob.generation_version)
        )
    )


def test_pe_se2_t03_schema_installs_append_only_source_impact_ledger(
    clean_api_database: Engine,
) -> None:
    """RED: T02 has no durable source-version impact decision ledger."""

    with clean_api_database.connect() as connection:
        _require_ledger(connection)
        columns = {
            column["name"]: column
            for column in inspect(connection).get_columns(_LEDGER_TABLE)
        }
        unique_constraints = {
            row["name"]
            for row in inspect(connection).get_unique_constraints(_LEDGER_TABLE)
        }
        check_constraint_rows = inspect(connection).get_check_constraints(
            _LEDGER_TABLE
        )
        check_constraints = {row["name"] for row in check_constraint_rows}
        changed_fields_constraint = next(
            str(row["sqltext"])
            for row in check_constraint_rows
            if row["name"]
            == "ck_generation_change_assessments_changed_fields_array"
        )
        indexes = {
            row["name"]: row for row in inspect(connection).get_indexes(_LEDGER_TABLE)
        }
        triggers = set(
            connection.scalars(
                text(
                    "SELECT trigger_name FROM information_schema.triggers "
                    "WHERE event_object_schema = current_schema() "
                    "AND event_object_table = :table_name"
                ),
                {"table_name": _LEDGER_TABLE},
            )
        )
        deferred_link_guard = connection.execute(
                text(
                    "SELECT tgdeferrable, tginitdeferred FROM pg_trigger "
                    "WHERE tgrelid = CAST(:table_name AS regclass) AND tgname = "
                "'trg_generation_change_assessments_validate_links'"
            ),
            {"table_name": _LEDGER_TABLE},
        ).one()
        changed_fields_invariants = tuple(
            connection.execute(
                text(
                    "SELECT "
                    "tc_generation_impact_changed_fields_valid("
                    "  '[\"slackWorkItem.requestDetails\", \"contractConfig\"]'::jsonb), "
                    "tc_generation_impact_changed_fields_valid("
                    "  '[\"contractConfig\", \"slackWorkItem.requestDetails\"]'::jsonb), "
                    "tc_generation_impact_changed_fields_valid("
                    "  '[\"contractConfig\", \"contractConfig\"]'::jsonb)"
                )
            ).one()
        )

    expected_columns = {
        "id",
        "slack_work_item_id",
        "predecessor_assessment_id",
        "baseline_source_version",
        "current_source_version",
        "materialization_baseline_generation_job_id",
        "selected_generation_job_id",
        "reused_generation_job_id",
        "outcome",
        "reason_code",
        "changed_fields",
        "fingerprint_schema_version",
        "baseline_fingerprint",
        "current_fingerprint",
        "contract_config_schema_version",
        "baseline_contract_config_digest",
        "current_contract_config_digest",
        "created_at",
    }
    assert expected_columns.issubset(columns)
    assert {
        "delivery_id",
        "slack_event_id",
        "outbox_event_id",
        "projection_event_id",
        "background_job_id",
        "job_attempt_id",
    }.isdisjoint(columns)
    assert "uq_generation_change_assessments_source_version" in unique_constraints
    assert "uq_generation_change_assessments_selected_job" in indexes
    assert {
        "ck_generation_change_assessments_outcome_allowed",
        "ck_generation_change_assessments_source_versions",
        "ck_generation_change_assessments_fingerprint_shape",
        "ck_generation_change_assessments_job_shape",
        "ck_generation_change_assessments_changed_fields_array",
    }.issubset(check_constraints)
    assert "changed_fields ?|" in changed_fields_constraint
    assert "$INITIAL_SOURCE" in changed_fields_constraint
    assert "$REVIEW_REQUIRED" in changed_fields_constraint
    assert {
        "trg_generation_change_assessments_immutable",
        "trg_generation_change_assessments_validate_links",
    }.issubset(triggers)
    assert deferred_link_guard == (True, True)
    assert changed_fields_invariants == (True, False, False)


def test_pe_se2_t03_initial_source_records_generate_and_selected_root(
    clean_api_database: Engine,
) -> None:
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    work_item_id, _, _ = _seed_source(sessions)
    _process_current(sessions, _consumer(sessions), work_item_id=work_item_id)

    with sessions() as session:
        roots = _automatic_roots(session, work_item_id)
        decisions = _decision_rows(session, work_item_id)
        assert len(roots) == len(decisions) == 1
        decision = decisions[0]
        assert decision["predecessor_assessment_id"] is None
        assert decision["baseline_source_version"] is None
        assert decision["materialization_baseline_generation_job_id"] is None
        assert decision["current_source_version"] == 1
        assert decision["outcome"] == "INITIAL_GENERATE"
        assert decision["reason_code"] == "FIRST_AUTOMATIC_SOURCE"
        assert decision["changed_fields"] == ["$INITIAL_SOURCE"]
        assert decision["fingerprint_schema_version"] == _IMPACT_SCHEMA_VERSION
        assert decision["contract_config_schema_version"] == _CONFIG_SCHEMA_VERSION
        assert decision["baseline_fingerprint"] is None
        assert decision["current_fingerprint"] is not None
        assert decision["baseline_contract_config_digest"] is None
        assert decision["current_contract_config_digest"] is not None
        assert decision["selected_generation_job_id"] == roots[0].id
        assert decision["reused_generation_job_id"] is None


def test_pe_se2_t03_v1_v2_skip_v3_change_keeps_assessment_and_job_baselines(
    clean_api_database: Engine,
) -> None:
    """v3 compares with the v2 assessment while materializing from the v1 root."""

    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    work_item_id, _, _ = _seed_source(sessions)
    consumer = _consumer(sessions)
    _process_current(sessions, consumer, work_item_id=work_item_id)
    with sessions() as session:
        baseline = _automatic_roots(session, work_item_id)[0]

    _edit_root(
        sessions,
        work_item_id=work_item_id,
        ordinal=10,
        body=_ROOT_BODY,
        event_suffix="SameTextNewRevision",
    )
    _process_current(sessions, consumer, work_item_id=work_item_id)

    with sessions() as session:
        roots = _automatic_roots(session, work_item_id)
        assert [root.id for root in roots] == [baseline.id], (
            "generation-equivalent source_version changes must not append an automatic root"
        )
        decisions = _decision_rows(session, work_item_id)
        assert [row["outcome"] for row in decisions] == ["INITIAL_GENERATE", "SKIP"]
        skipped = decisions[-1]
        assert skipped["predecessor_assessment_id"] == decisions[0]["id"]
        assert skipped["baseline_source_version"] == 1
        assert skipped["current_source_version"] == 2
        assert skipped["materialization_baseline_generation_job_id"] == baseline.id
        assert skipped["selected_generation_job_id"] is None
        assert skipped["reused_generation_job_id"] == baseline.id
        assert skipped["reason_code"] == "NO_GENERATION_RELEVANT_CHANGE"
        assert skipped["changed_fields"] == []
        assert skipped["baseline_fingerprint"] == skipped["current_fingerprint"]
        assert skipped["baseline_contract_config_digest"] == (
            skipped["current_contract_config_digest"]
        )

    _edit_root(
        sessions,
        work_item_id=work_item_id,
        ordinal=20,
        body=_ROOT_BODY.replace("감사 기록", "감사 기록과 관리자 해제"),
        event_suffix="V3Relevant",
    )
    _process_current(sessions, consumer, work_item_id=work_item_id)

    with sessions() as session:
        roots = _automatic_roots(session, work_item_id)
        decisions = _decision_rows(session, work_item_id)
        assert [root.source_version for root in roots] == [1, 3]
        assert [row["current_source_version"] for row in decisions] == [1, 2, 3]
        regenerated = decisions[-1]
        assert regenerated["predecessor_assessment_id"] == decisions[-2]["id"]
        assert regenerated["baseline_source_version"] == 2
        assert regenerated["materialization_baseline_generation_job_id"] == baseline.id
        assert regenerated["selected_generation_job_id"] == roots[-1].id
        assert regenerated["outcome"] == "REGENERATE"


def test_pe_se2_t03_relevant_four_field_change_regenerates(
    clean_api_database: Engine,
) -> None:
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    work_item_id, _, _ = _seed_source(sessions)
    consumer = _consumer(sessions)
    _process_current(sessions, consumer, work_item_id=work_item_id)
    with sessions() as session:
        baseline = _automatic_roots(session, work_item_id)[0]

    _edit_root(
        sessions,
        work_item_id=work_item_id,
        ordinal=20,
        body=_ROOT_BODY.replace(
            "결제 실패 잠금과 감사 기록을 검증한다.",
            "결제 실패 잠금, 감사 기록, 관리자 해제를 검증한다.",
        ),
        event_suffix="RelevantField",
    )
    _process_current(sessions, consumer, work_item_id=work_item_id)

    with sessions() as session:
        roots = _automatic_roots(session, work_item_id)
        decisions = _decision_rows(session, work_item_id)
        assert len(roots) == 2
        regenerated = decisions[-1]
        assert regenerated["outcome"] == "REGENERATE"
        assert regenerated["reason_code"] == "GENERATION_RELEVANT_FIELDS_CHANGED"
        assert regenerated["materialization_baseline_generation_job_id"] == baseline.id
        assert regenerated["selected_generation_job_id"] == roots[-1].id
        assert regenerated["reused_generation_job_id"] is None
        assert regenerated["baseline_fingerprint"] != regenerated["current_fingerprint"]
        assert regenerated["changed_fields"] == [
            "slackWorkItem.requestDetails",
            "activeKnowledge.evidence",
        ]


def test_pe_se2_t03_late_old_revision_is_transport_only_skip(
    clean_api_database: Engine,
) -> None:
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    work_item_id, _, _ = _seed_source(sessions)
    consumer = _consumer(sessions)
    _process_current(sessions, consumer, work_item_id=work_item_id)
    with sessions() as session:
        baseline = _automatic_roots(session, work_item_id)[0]

    _ingest_late_old_root_revision(sessions, work_item_id=work_item_id)
    _process_current(sessions, consumer, work_item_id=work_item_id)

    with sessions() as session:
        assert [root.id for root in _automatic_roots(session, work_item_id)] == [
            baseline.id
        ]
        assessments = _decision_rows(session, work_item_id)
        skipped = assessments[-1]
        assert skipped["outcome"] == "SKIP"
        assert skipped["reason_code"] == "NO_GENERATION_RELEVANT_CHANGE"
        assert skipped["predecessor_assessment_id"] == assessments[0]["id"]
        assert skipped["materialization_baseline_generation_job_id"] == baseline.id
        assert skipped["reused_generation_job_id"] == baseline.id
        assert skipped["changed_fields"] == []


def test_pe_se2_t03_supported_contract_config_change_regenerates(
    clean_api_database: Engine,
) -> None:
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    work_item_id, _, _ = _seed_source(sessions)
    _process_current(sessions, _consumer(sessions), work_item_id=work_item_id)
    with sessions() as session:
        baseline = _automatic_roots(session, work_item_id)[0]
    _edit_root(
        sessions,
        work_item_id=work_item_id,
        ordinal=25,
        body=_ROOT_BODY,
        event_suffix="ConfigDrift",
    )
    _process_current(
        sessions,
        _consumer(sessions, extractor_model="impact-contract-v2"),
        work_item_id=work_item_id,
    )

    with sessions() as session:
        roots = _automatic_roots(session, work_item_id)
        assessment = _decision_rows(session, work_item_id)[-1]
        assert [root.source_version for root in roots] == [1, 2]
        assert assessment["outcome"] == "REGENERATE"
        assert assessment["reason_code"] == "GENERATION_CONTRACT_CHANGED"
        assert assessment["changed_fields"] == ["contractConfig"]
        assert assessment["baseline_fingerprint"] == assessment["current_fingerprint"]
        assert assessment["baseline_contract_config_digest"] != (
            assessment["current_contract_config_digest"]
        )
        assert assessment["materialization_baseline_generation_job_id"] == baseline.id
        assert assessment["selected_generation_job_id"] == roots[-1].id


def test_pe_se2_t03_exact_and_distinct_delivery_replay_one_assessment(
    clean_api_database: Engine,
) -> None:
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    work_item_id, _, root_timestamp = _seed_source(sessions)
    consumer = _consumer(sessions)
    source_version, outbox_event_id = _process_current(
        sessions, consumer, work_item_id=work_item_id
    )
    _ingest_distinct_duplicate_delivery(
        sessions,
        work_item_id=work_item_id,
        root_timestamp=root_timestamp,
    )
    _process_handoff(
        consumer,
        outbox_event_id=outbox_event_id,
        work_item_id=work_item_id,
        source_version=source_version,
    )
    _process_handoff(
        consumer,
        outbox_event_id=outbox_event_id,
        work_item_id=work_item_id,
        source_version=source_version,
    )

    with sessions() as session:
        assert len(_automatic_roots(session, work_item_id)) == 1
        assert len(_decision_rows(session, work_item_id)) == 1


def test_pe_se2_t03_concurrent_current_version_converges_on_one_assessment_and_job(
    clean_api_database: Engine,
) -> None:
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    work_item_id, _, _ = _seed_source(sessions)
    consumer = _consumer(sessions)
    _process_current(sessions, consumer, work_item_id=work_item_id)
    _edit_root(
        sessions,
        work_item_id=work_item_id,
        ordinal=30,
        body=_ROOT_BODY.replace("감사 기록", "감사 기록과 알림"),
        event_suffix="Concurrent",
    )
    outbox_event_id = _prepare_handoff(
        sessions,
        work_item_id=work_item_id,
        source_version=2,
    )
    barrier = Barrier(2)

    def process() -> object:
        barrier.wait(timeout=5)
        return _process_handoff(
            consumer,
            outbox_event_id=outbox_event_id,
            work_item_id=work_item_id,
            source_version=2,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: process(), range(2)))
    assert len(results) == 2

    with sessions() as session:
        roots = _automatic_roots(session, work_item_id)
        decisions = _decision_rows(session, work_item_id)
        assert [root.source_version for root in roots] == [1, 2]
        assert [row["current_source_version"] for row in decisions] == [1, 2]


def test_pe_se2_t03_rollback_then_retry_commits_one_atomic_assessment(
    clean_api_database: Engine,
) -> None:
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    work_item_id, _, _ = _seed_source(sessions)
    consumer = _consumer(sessions)
    _process_current(sessions, consumer, work_item_id=work_item_id)
    _edit_root(
        sessions,
        work_item_id=work_item_id,
        ordinal=40,
        body=_ROOT_BODY.replace("감사 기록", "감사 기록과 보상 처리"),
        event_suffix="Rollback",
    )
    outbox_event_id = _prepare_handoff(
        sessions,
        work_item_id=work_item_id,
        source_version=2,
    )
    injected = False

    def fail_first_commit(session: Session) -> None:
        nonlocal injected
        if not injected and session.get_bind() is clean_api_database:
            injected = True
            raise RuntimeError("injected T03 assessment commit failure")

    event.listen(Session, "before_commit", fail_first_commit)
    try:
        with pytest.raises(RuntimeError, match="injected T03 assessment commit failure"):
            _process_handoff(
                consumer,
                outbox_event_id=outbox_event_id,
                work_item_id=work_item_id,
                source_version=2,
            )
    finally:
        event.remove(Session, "before_commit", fail_first_commit)

    assert injected
    with sessions() as session:
        assert [root.source_version for root in _automatic_roots(session, work_item_id)] == [1]

    _process_handoff(
        consumer,
        outbox_event_id=outbox_event_id,
        work_item_id=work_item_id,
        source_version=2,
    )
    with sessions() as session:
        assert [root.source_version for root in _automatic_roots(session, work_item_id)] == [1, 2]
        assert [
            row["current_source_version"] for row in _decision_rows(session, work_item_id)
        ] == [1, 2]


def test_pe_se2_t03_committed_replay_survives_advance_and_unseen_stale_is_noop(
    clean_api_database: Engine,
) -> None:
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    work_item_id, _, _ = _seed_source(sessions)
    consumer = _consumer(sessions)
    _, version_one_outbox = _process_current(
        sessions, consumer, work_item_id=work_item_id
    )
    _append_reply(
        sessions,
        work_item_id=work_item_id,
        ordinal=50,
        body="관련 없는 첫 번째 후속 메모",
        event_suffix="StaleV2",
    )
    version_two_outbox = _prepare_handoff(
        sessions,
        work_item_id=work_item_id,
        source_version=2,
    )
    _append_reply(
        sessions,
        work_item_id=work_item_id,
        ordinal=60,
        body="관련 없는 두 번째 후속 메모",
        event_suffix="StaleV3",
    )

    _process_handoff(
        consumer,
        outbox_event_id=version_one_outbox,
        work_item_id=work_item_id,
        source_version=1,
    )
    stale = _process_handoff(
        consumer,
        outbox_event_id=version_two_outbox,
        work_item_id=work_item_id,
        source_version=2,
    )
    assert stale is None

    with sessions() as session:
        assert [root.source_version for root in _automatic_roots(session, work_item_id)] == [1]
        decisions = _decision_rows(session, work_item_id)
        assert [row["current_source_version"] for row in decisions] == [1]


def test_pe_se2_t03_deleted_selected_evidence_requires_review_without_job(
    clean_api_database: Engine,
) -> None:
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    body_without_decision = _ROOT_BODY.replace(f"\n결정: {_OLD_DECISION}", "")
    work_item_id, _, _ = _seed_source(sessions, body=body_without_decision)
    decision_reply_ts = _append_reply(
        sessions,
        work_item_id=work_item_id,
        ordinal=70,
        body=f"결정: {_OLD_DECISION}",
        event_suffix="EvidenceSource",
    )
    consumer = _consumer(sessions)
    _process_current(sessions, consumer, work_item_id=work_item_id)
    _delete_reply(
        sessions,
        work_item_id=work_item_id,
        message_timestamp=decision_reply_ts,
        ordinal=80,
    )
    _process_current(sessions, consumer, work_item_id=work_item_id)

    with sessions() as session:
        roots = _automatic_roots(session, work_item_id)
        decision = _decision_rows(session, work_item_id)[-1]
        assert [root.source_version for root in roots] == [2]
        assert decision["outcome"] == "REVIEW_REQUIRED"
        assert decision["reason_code"] == "DELETED_OR_RETRACTED_EVIDENCE"
        assert decision["changed_fields"] == ["$REVIEW_REQUIRED"]
        assert decision["materialization_baseline_generation_job_id"] == roots[0].id
        assert decision["selected_generation_job_id"] is None
        assert decision["reused_generation_job_id"] is None


def test_pe_se2_t03_superseded_selected_evidence_regenerates(
    clean_api_database: Engine,
) -> None:
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    work_item_id, _, _ = _seed_source(sessions)
    consumer = _consumer(sessions, _superseding_decision)
    _process_current(sessions, consumer, work_item_id=work_item_id)
    _append_reply(
        sessions,
        work_item_id=work_item_id,
        ordinal=90,
        body=f"변경 결정: {_NEW_DECISION}",
        event_suffix="Superseding",
    )
    _process_current(sessions, consumer, work_item_id=work_item_id)

    with sessions() as session:
        decision = _decision_rows(session, work_item_id)[-1]
        assert [
            root.source_version for root in _automatic_roots(session, work_item_id)
        ] == [1, 2]
        assert decision["outcome"] == "REGENERATE"
        assert decision["changed_fields"] == [
            "activeKnowledge.decisions",
            "activeKnowledge.evidence",
        ]


def test_pe_se2_t03_ambiguous_current_knowledge_requires_review_without_job(
    clean_api_database: Engine,
) -> None:
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    work_item_id, _, _ = _seed_source(sessions)
    _process_current(sessions, _consumer(sessions), work_item_id=work_item_id)
    with sessions() as session:
        baseline = _automatic_roots(session, work_item_id)[0]
    _append_reply(
        sessions,
        work_item_id=work_item_id,
        ordinal=95,
        body=f"상충 결정: {_NEW_DECISION}",
        event_suffix="Ambiguous",
    )
    _process_current(
        sessions,
        _consumer(sessions, _ambiguous_decision),
        work_item_id=work_item_id,
    )

    with sessions() as session:
        assert [root.id for root in _automatic_roots(session, work_item_id)] == [
            baseline.id
        ]
        assessment = _decision_rows(session, work_item_id)[-1]
        assert assessment["outcome"] == "REVIEW_REQUIRED"
        assert assessment["reason_code"] == "AMBIGUOUS_KNOWLEDGE"
        assert assessment["changed_fields"] == ["$REVIEW_REQUIRED"]
        assert assessment["selected_generation_job_id"] is None
        assert assessment["reused_generation_job_id"] is None


def test_pe_se2_t03_unmaterialized_legacy_root_is_excluded_from_baseline(
    clean_api_database: Engine,
) -> None:
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    work_item_id, source_outbox_id, _ = _seed_source(sessions)
    consumer = _consumer(sessions)
    asyncio.run(consumer.process_event(source_outbox_id))
    legacy_job_id = uuid.uuid4()
    with sessions.begin() as session:
        ontology_id = session.scalar(
            select(OntologySchema.id).where(OntologySchema.active)
        )
        assert ontology_id is not None
        session.add(
            GenerationJob(
                id=legacy_job_id,
                slack_work_item_id=work_item_id,
                source_version=1,
                generation_version=1,
                generation_trigger="AUTOMATIC",
                status="QUEUED",
                provider="UNCONFIGURED",
                model="UNCONFIGURED",
                prompt_version="generation_prompt_v1",
                schema_version="1.0.0",
                ontology_schema_id=ontology_id,
                context_item_count=0,
                context_digest=None,
                context_sealed_at=None,
                generation_input_capture_state="LEGACY_NOT_CAPTURED",
                generation_input_schema_version=None,
                generation_input=None,
                generation_input_digest=None,
                root_job_id=legacy_job_id,
                retry_of_job_id=None,
                attempt_number=1,
            )
        )

    _append_reply(
        sessions,
        work_item_id=work_item_id,
        ordinal=100,
        body="비교 의미에는 영향을 주지 않는 후속 메모",
        event_suffix="LegacyBaseline",
    )
    _process_current(sessions, consumer, work_item_id=work_item_id)

    with sessions() as session:
        roots = _automatic_roots(session, work_item_id)
        decision = _decision_rows(session, work_item_id)[0]
        assert [root.source_version for root in roots] == [1, 2]
        assert decision["predecessor_assessment_id"] is None
        assert decision["materialization_baseline_generation_job_id"] is None
        assert decision["baseline_source_version"] is None
        assert decision["current_source_version"] == 2
        assert decision["outcome"] == "INITIAL_GENERATE"
        assert decision["reason_code"] == "FIRST_AUTOMATIC_SOURCE"
        assert decision["changed_fields"] == ["$INITIAL_SOURCE"]
        assert decision["baseline_fingerprint"] is None
        assert decision["current_fingerprint"] is not None
        assert decision["selected_generation_job_id"] == roots[1].id
        assert decision["reused_generation_job_id"] is None


def test_pe_se2_t03_predecessor_ignores_manual_roots_and_automatic_retry_children(
    clean_api_database: Engine,
) -> None:
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    work_item_id, _, _ = _seed_source(sessions)
    consumer = _consumer(sessions)
    _process_current(
        sessions,
        consumer,
        work_item_id=work_item_id,
        commit_baseline=False,
    )
    with sessions.begin() as session:
        repository = GenerationRepository(session)
        automatic_root = _automatic_roots(session, work_item_id)[0]
        repository.begin_execution(automatic_root.id)
        repository.fail_execution(
            automatic_root.id,
            code="GENERATION_PROVIDER_ERROR",
            message="injected predecessor-selection failure",
            retryable=True,
            attempts=1,
        )
    with sessions.begin() as session:
        retry = GenerationRepository(session).retry_job_with_context(automatic_root.id)
        retry_id = retry.job.id
    with sessions.begin() as session:
        manual = GenerationRepository(session).create_next_job_with_context(
            slack_work_item_id=work_item_id
        )
        manual_id = manual.job.id

    _edit_root(
        sessions,
        work_item_id=work_item_id,
        ordinal=110,
        body=_ROOT_BODY.replace("감사 기록", "감사 기록과 보안 알림"),
        event_suffix="Predecessor",
    )
    _process_current(sessions, consumer, work_item_id=work_item_id)

    with sessions() as session:
        assessments = _decision_rows(session, work_item_id)
        decision = assessments[-1]
        assert decision["predecessor_assessment_id"] == assessments[0]["id"]
        assert decision["materialization_baseline_generation_job_id"] is None
        assert automatic_root.id not in {retry_id, manual_id}
        assert decision["baseline_source_version"] == 1
        assert decision["current_source_version"] == 2


def test_pe_se2_t03_impact_assessment_is_database_immutable(
    clean_api_database: Engine,
) -> None:
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    work_item_id, _, _ = _seed_source(sessions)
    _process_current(sessions, _consumer(sessions), work_item_id=work_item_id)
    with sessions() as session:
        assessment_id = _decision_rows(session, work_item_id)[0]["id"]

    with pytest.raises(DBAPIError):
        with clean_api_database.begin() as connection:
            connection.execute(
                text(
                    "UPDATE generation_change_assessments "
                    "SET reason_code = 'NO_GENERATION_RELEVANT_CHANGE' WHERE id = :id"
                ),
                {"id": assessment_id},
            )
    with pytest.raises(DBAPIError):
        with clean_api_database.begin() as connection:
            connection.execute(
                text("DELETE FROM generation_change_assessments WHERE id = :id"),
                {"id": assessment_id},
            )

    with sessions() as session:
        decision = _decision_rows(session, work_item_id)[0]
        assert decision["outcome"] == "INITIAL_GENERATE"
