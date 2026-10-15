from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import Engine, inspect, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

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
    GenerationContext,
    KnowledgeNode,
    KnowledgeSourceKind,
    OutboxEvent,
    SlackMessage,
    SlackMessageRevision,
    SlackWorkItem,
)
from tc_backend.db.slack import SlackEventInput, SlackRepository
from tc_backend.generation import reconstruction as generation_reconstruction
from tc_backend.repositories.generation_repository import (
    GenerationRepository,
    UngroundedGenerationContext,
)
from tc_backend.repositories.knowledge_repository import EvidenceInput, KnowledgeRepository

pytestmark = pytest.mark.postgres

SLACK_WORKSPACE = "T_GENERATION"
CHANNEL = "C_GENERATION"


def _audit(label: str) -> AuditContext:
    return AuditContext(
        source_type=AuditSourceType.API,
        actor_label="generation-postgres-test",
        reason=label,
        trace_id=f"generation_{label}",
    )


def _slack_event() -> SlackEventInput:
    return SlackEventInput(
        event_id="Ev-generation-root",
        slack_workspace_id=SLACK_WORKSPACE,
        channel_id=CHANNEL,
        event_type="ROOT",
        event_ts="1787700000.000100",
        message_ts="1787700000.000000",
        thread_ts=None,
        revision_ts=None,
        body="결제 승인은 실패 잠금 정책을 따라야 한다.",
        author_id="U_GENERATION",
        permalink="https://synthetic.invalid/generation/root",
        channel_is_private=False,
        raw_payload={"event_id": "Ev-generation-root", "type": "message"},
    )


def _case_content() -> CaseContent:
    return CaseContent(
        summary="결제 승인 회귀 테스트",
        precondition="결제 테스트 계정이 준비되어 있다",
        steps=(StepInput(action="결제를 승인한다", expected_result="승인이 완료된다"),),
        assignee_label="QA",
        tag_names=("generation",),
    )


def test_context_builder_selects_grounded_current_history_and_seals_snapshot(
    migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    with sessions.begin() as session:
        category = FolderRepository(session).create(
            name="Generation",
            parent_id=None,
            sort_order=0,
            audit=_audit("category"),
        )
        work_item = CaseRepository(session).create_work_item(
            work_item_key="WORK-GENERATION",
            category_id=category.id,
            summary="결제 승인",
            audit=_audit("work-item"),
        )
        case = CaseRepository(session).create_case(
            work_item_id=work_item.id,
            content=_case_content(),
            change_reason="initial",
            audit=_audit("case"),
        )
        case_id = case.test_case.id

    with sessions.begin() as session:
        cycle = CycleRepository(session).create(
            name="Generation regression",
            starts_at=date(2026, 8, 26),
            scope_type="WORK_ITEM",
            scope_value="WORK-GENERATION",
            audit=_audit("cycle"),
        )
        cycle_id = cycle.cycle.id
        cycle_item_id = cycle.items[0].id
    with sessions.begin() as session:
        CycleRepository(session).start(
            cycle_id=cycle_id,
            base_resource_version=1,
            audit=_audit("cycle-start"),
        )
    with sessions.begin() as session:
        attempt = CycleRepository(session).start_attempt(
            cycle_item_id=cycle_item_id,
            platform=ExecutionPlatform.WEB,
            audit=_audit("attempt-start"),
        )
        attempt_id = attempt.id
    with sessions.begin() as session:
        CycleRepository(session).complete_attempt(
            attempt_id=attempt_id,
            status=ExecutionStatus.FAIL,
            actual_result="HTTP 500",
            note="승인 API 오류",
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
        repository = KnowledgeRepository(session)
        events = tuple(
            session.scalars(
                select(OutboxEvent)
                .where(OutboxEvent.aggregate_type.in_(("TEST_CASE", "EXECUTION_ATTEMPT", "ISSUE")))
                .order_by(OutboxEvent.occurred_at, OutboxEvent.id)
            )
        )
        for event in events:
            repository.project_outbox_event(event.id)

    with sessions.begin() as session:
        slack = SlackRepository(session)
        slack.put_channel(
            slack_workspace_id=SLACK_WORKSPACE,
            channel_id=CHANNEL,
            channel_name="generation",
        )
        ingested = slack.ingest_event(_slack_event())
        assert ingested.work_item_id is not None
        slack_work_item_id = ingested.work_item_id

    with sessions.begin() as session:
        revision, message = session.execute(
            select(SlackMessageRevision, SlackMessage)
            .join(SlackMessage, SlackMessage.id == SlackMessageRevision.message_id)
            .where(SlackMessageRevision.id == ingested.revision_id)
        ).one()
        evidence = EvidenceInput.from_content(
            source_kind=KnowledgeSourceKind.SOURCE_REVISION,
            entity_type="SLACK_MESSAGE_REVISION",
            entity_id=revision.id,
            revision_key=revision.revision_ts,
            content=revision.body or "",
            recorded_at=revision.received_at,
            permalink=message.permalink,
        )
        repository = KnowledgeRepository(session)
        root = repository.create_node(
            node_type="WORK_ITEM",
            label="Slack payment approval",
            entity_type="SLACK_WORK_ITEM",
            entity_id=slack_work_item_id,
            properties={"sourceVersion": 1, "content": revision.body},
            recorded_at=datetime.now(UTC),
            evidence=evidence,
        )
        decision = repository.create_node(
            node_type="DECISION",
            label="실패 잠금 정책",
            properties={"content": "결제 승인은 실패 잠금 정책을 따른다"},
            recorded_at=datetime.now(UTC),
            evidence=evidence,
        )
        decision_id = decision.id
        inactive = repository.create_node(
            node_type="DECISION",
            label="미확정 정책",
            status="AMBIGUOUS",
            properties={"content": "미확정"},
            recorded_at=datetime.now(UTC),
            evidence=evidence,
        )
        case_node = session.scalar(
            select(KnowledgeNode).where(
                KnowledgeNode.entity_type == "TEST_CASE",
                KnowledgeNode.entity_id == case_id,
            )
        )
        assert case_node is not None
        repository.create_edge(
            source_node_id=root.id,
            predicate="AFFECTS",
            target_node_id=case_node.id,
            evidence=evidence,
        )
        repository.create_edge(
            source_node_id=root.id,
            predicate="DISCUSSES",
            target_node_id=decision.id,
            evidence=evidence,
        )
        # An ACTIVE edge cannot make an inactive node eligible.
        repository.create_edge(
            source_node_id=root.id,
            predicate="DISCUSSES",
            target_node_id=inactive.id,
            evidence=evidence,
        )
        inactive_id = inactive.id

    with sessions.begin() as session:
        persisted = GenerationRepository(session).create_job_with_context(
            slack_work_item_id=slack_work_item_id,
            generation_version=1,
            provider="ollama",
            model="qwen3:8b",
            prompt_version="generation-prompt-v1",
            schema_version="1.0.0",
        )
        job_id = persisted.job.id
        reasons = {row.reason for row in persisted.entries}
        selected_node_ids = {row.node_id for row in persisted.entries if row.node_id}

        assert {
            "SLACK_WORK_ITEM_SOURCE",
            "ACTIVE_DECISION",
            "ACTIVE_TEST_CASE",
            "REGRESSION_FAIL",
            "OPEN_ISSUE",
        }.issubset(reasons)
        assert inactive_id not in selected_node_ids
        assert [row.rank for row in persisted.entries] == list(range(1, len(persisted.entries) + 1))
        assert all(
            (row.node_snapshot or row.edge_snapshot or {}).get("status") == "ACTIVE"
            for row in persisted.entries
        )
        assert persisted.job.context_item_count == len(persisted.entries)
        assert persisted.job.context_digest is not None
        assert persisted.job.context_sealed_at is not None
        assert persisted.job.generation_input_capture_state == "SEALED"
        assert persisted.job.generation_input_schema_version == "1.0.0"
        assert persisted.job.generation_input_digest is not None
        generation_input = persisted.job.generation_input
        assert generation_input is not None
        sealed_generation_input = deepcopy(generation_input)
        assert generation_input["slackWorkItem"]["sourceRevisionIds"]
        assert [item["nodeType"] for item in generation_input["activeKnowledge"]] == ["DECISION"]
        assert [item["testCaseId"] for item in generation_input["existingCases"]] == [
            f"case_{case_id.hex}"
        ]
        assert [item["status"] for item in generation_input["failedOrBlockedExecutions"]] == [
            "FAIL"
        ]
        assert [item["issueId"] for item in generation_input["openIssues"]] == [
            f"issue_{issue_id.hex}"
        ]
        assert generation_input["extraSources"] == []
        assert generation_input["categoryCandidates"]
        assert generation_input["generationPolicy"]["batch"]["isFinalBatch"] is True
        assert persisted.job.provider == "ollama"
        assert persisted.job.model == "qwen3:8b"
        assert persisted.job.schema_version == "1.0.0"

    with sessions() as session:
        rows_before_oversize = (
            int(session.scalar(text("SELECT count(*) FROM generation_jobs")) or 0),
            int(session.scalar(text("SELECT count(*) FROM generation_contexts")) or 0),
        )
    with monkeypatch.context() as scoped_patch:
        scoped_patch.setattr(
            generation_reconstruction,
            "GENERATION_INPUT_CAPTURE_LIMIT",
            1,
        )
        with pytest.raises(UngroundedGenerationContext, match="could not be sealed"):
            with sessions.begin() as session:
                GenerationRepository(session).create_job_with_context(
                    slack_work_item_id=slack_work_item_id,
                    generation_version=2,
                    provider="ollama",
                    model="qwen3:8b",
                    prompt_version="generation-prompt-v1",
                    schema_version="1.0.0",
                )
    with sessions() as session:
        assert (
            int(session.scalar(text("SELECT count(*) FROM generation_jobs")) or 0),
            int(session.scalar(text("SELECT count(*) FROM generation_contexts")) or 0),
        ) == rows_before_oversize

    with sessions.begin() as session:
        live_slack = session.get(SlackWorkItem, slack_work_item_id)
        assert live_slack is not None
        live_slack.parse_result = {"developmentItem": "mutated live Slack"}
        live_slack.permalink = "https://synthetic.invalid/generation/mutated"
        session.execute(
            update(KnowledgeNode)
            .where(KnowledgeNode.id == decision_id)
            .values(properties={"content": "mutated live knowledge"})
        )
        CaseRepository(session).update_case(
            test_case_id=case_id,
            base_version=1,
            content=CaseContent(
                summary="mutated live case",
                precondition="current state only",
                steps=(
                    StepInput(
                        action="exercise current live behavior",
                        expected_result="current live result",
                    ),
                ),
                assignee_label="Live QA",
                tag_names=("live",),
            ),
            change_reason="prove reconstruction ignores current case rows",
            audit=_audit("mutated-case"),
        )
        IssueRepository(session).soft_delete(
            issue_id=issue_id,
            base_resource_version=1,
            audit=_audit("deleted-issue"),
        )

    with sessions() as session:
        reconstructed = GenerationRepository(session).load_reconstruction(job_id)
        assert reconstructed.availability == "AVAILABLE"
        assert reconstructed.generation_input == sealed_generation_input

    with sessions() as session:
        loaded = GenerationRepository(session).load_context(job_id)
        assert loaded.job.context_digest is not None
        assert len(loaded.entries) == loaded.job.context_item_count
        assert (
            session.scalar(
                select(GenerationContext.node_id).where(
                    GenerationContext.node_snapshot["entityId"].as_string() == str(issue_id)
                )
            )
            is not None
        )

    with pytest.raises(DBAPIError, match="append-only"):
        with sessions.begin() as session:
            context_id = session.scalar(
                select(GenerationContext.id)
                .where(GenerationContext.generation_job_id == job_id)
                .limit(1)
            )
            assert context_id is not None
            session.execute(
                update(GenerationContext)
                .where(GenerationContext.id == context_id)
                .values(reason="tampered")
            )

    with pytest.raises(DBAPIError, match="input provenance is immutable"):
        with migrated_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE generation_jobs SET generation_input = "
                    "jsonb_set(generation_input, '{requestId}', "
                    "'\"generation_request_tampered\"'::jsonb) WHERE id = :job_id"
                ),
                {"job_id": job_id},
            )

    with pytest.raises(DBAPIError, match="already sealed"):
        with migrated_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO generation_contexts "
                    "(id, generation_job_id, knowledge_evidence_id, node_id, edge_id, "
                    "source_record_id, source_kind, source_entity_type, source_entity_id, "
                    "source_revision, source_content_hash, source_permalink, rank, reason, "
                    "node_snapshot, edge_snapshot) "
                    "SELECT gen_random_uuid(), generation_job_id, knowledge_evidence_id, "
                    "node_id, edge_id, source_record_id, source_kind, source_entity_type, "
                    "source_entity_id, source_revision, source_content_hash, source_permalink, "
                    "999, reason, node_snapshot, edge_snapshot "
                    "FROM generation_contexts WHERE generation_job_id = :job_id LIMIT 1"
                ),
                {"job_id": job_id},
            )

    with pytest.raises(DBAPIError, match="illegal generation job status transition"):
        with migrated_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE generation_jobs SET status = 'SUCCEEDED', started_at = now(), "
                    "completed_at = now(), result = '{}'::jsonb WHERE id = :job_id"
                ),
                {"job_id": job_id},
            )


def test_generation_context_migration_installs_grounding_and_immutability_guards(
    migrated_engine: Engine,
) -> None:
    with migrated_engine.connect() as connection:
        triggers = set(
            connection.scalars(
                text(
                    "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgrelid IN "
                    "('generation_jobs'::regclass, 'generation_contexts'::regclass)"
                )
            )
        )
        constraints = set(
            connection.scalars(
                text(
                    "SELECT conname FROM pg_constraint WHERE conrelid IN "
                    "('generation_jobs'::regclass, 'generation_contexts'::regclass)"
                )
            )
        )
        columns = {column["name"] for column in inspect(connection).get_columns("generation_jobs")}

    assert {
        "trg_generation_contexts_validate_grounding",
        "trg_generation_contexts_append_only",
        "trg_generation_jobs_guard_context_seal",
    }.issubset(triggers)
    assert {
        "uq_generation_jobs_work_item_generation_version",
        "uq_generation_contexts_job_rank",
        "uq_generation_contexts_job_evidence",
        "ck_generation_contexts_exactly_one_resource",
        "uq_generation_jobs_retry_of_job_id",
        "ck_generation_jobs_lineage_shape",
        "ck_generation_jobs_lifecycle_shape",
        "ck_generation_jobs_error_metadata_complete",
        "ck_generation_jobs_error_code_allowed",
        "ck_generation_jobs_generation_input_capture_complete",
        "ck_generation_jobs_generation_trigger_allowed",
    }.issubset(constraints)
    assert {
        "root_job_id",
        "retry_of_job_id",
        "attempt_number",
        "error_code",
        "error_retryable",
        "error_attempts",
        "started_at",
        "completed_at",
        "generation_input_capture_state",
        "generation_input_schema_version",
        "generation_input",
        "generation_input_digest",
        "generation_trigger",
    }.issubset(columns)
