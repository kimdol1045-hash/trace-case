from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from tc_api.main import create_app
from tc_backend.db.audit import AuditContext
from tc_backend.db.cases import (
    CaseRepository,
    TestCaseContent as LedgerCaseContent,
    TestStepInput as LedgerStepInput,
)
from tc_backend.db.constants import AI_INBOX_FOLDER_ID, DEFAULT_WORKSPACE_ID
from tc_backend.db.jobs import JobRepository
from tc_backend.db.models import (
    AuditLog,
    AuditSourceType,
    BackgroundJob,
    ExpectedResultMode,
    GenerationApply,
    GenerationApplyOutcome,
    GenerationContext,
    GenerationJob,
    GenerationMaterializationSchedule,
    GenerationSlackNotificationDeliveryObservation,
    GenerationSlackNotificationIntent,
    GenerationSlackNotificationReceipt,
    KnowledgeNode,
    KnowledgeSourceKind,
    OutboxEvent,
    SlackMessage,
    SlackMessageRevision,
    SlackThread,
    SlackWorkItem,
    Tag,
    TestCase as LedgerTestCase,
    TestCaseGenerationManagementEvent as ManagementEvent,
    TestCaseSourceType as LedgerTestCaseSourceType,
    TestCaseVersion as LedgerTestCaseVersion,
    TestCaseVersionTag as LedgerTestCaseVersionTag,
    TestCaseTag as LedgerTestCaseTag,
    TestStep as LedgerTestStep,
    WorkItem,
    WorkspaceKeyCounter,
)
from tc_backend.db.slack import SlackEventInput, SlackRepository
from tc_backend.generation.application import ApplyWriteStage, canonical_sha256
from tc_backend.generation.deduplication import (
    DeduplicationStep,
    GeneratedCaseInput,
    exact_content_key,
)
from tc_backend.generation.slack_notifications import (
    GenerationSlackNotificationDelivery,
    GenerationSlackNotificationRetry,
)
from tc_backend.infrastructure.slack.errors import SlackRateLimitError
from tc_backend.infrastructure.slack.normalization import normalize_events_api_payload
from tc_backend.infrastructure.slack.testing import DeterministicFakeSlackTransport
from tc_backend.slack_pipeline import ledger_event
from tc_backend.repositories.generation_repository import GenerationRepository
from tc_backend.repositories.knowledge_repository import (
    EvidenceInput,
    KnowledgeRepository,
)
from tc_worker.slack_runtime import SlackWorkerRuntime

pytestmark = pytest.mark.postgres


@dataclass(frozen=True, slots=True)
class ApplySeed:
    job_id: uuid.UUID
    slack_work_item_id: uuid.UUID
    context_digest: str
    result: dict[str, object]
    parent_id: uuid.UUID | None
    reused_case_id: uuid.UUID | None
    reused_version_id: uuid.UUID | None


class ProviderCallTripwire:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, context: Any) -> Any:
        self.calls += 1
        raise AssertionError("apply must not invoke the generation provider boundary")


def _arm_automatic_materialization(
    session: Session,
    job: GenerationJob,
    *,
    worker_id: str = "test-automatic-materializer",
) -> BackgroundJob:
    """Seed the real leased worker authority for legacy direct-apply tests."""

    background_id = JobRepository(session).enqueue(
        job_type="AUTOMATIC_GENERATION_MATERIALIZE",
        payload={"generationJobId": str(job.id)},
        max_attempts=20,
        idempotency_key=f"automatic-generation-materialize:{job.id}",
    )
    claim = JobRepository(session).claim_next(
        worker_id=worker_id,
        stale_after=timedelta(minutes=5),
        job_types=("AUTOMATIC_GENERATION_MATERIALIZE",),
    )
    assert claim is not None and claim.id == background_id
    now = datetime.now(UTC)
    session.add(
        GenerationMaterializationSchedule(
            id=uuid.uuid4(),
            workspace_id=DEFAULT_WORKSPACE_ID,
            generation_job_id=job.id,
            root_job_id=job.root_job_id,
            background_job_id=background_id,
            mode=(
                "AUTOMATIC_SUGGESTIONS"
                if job.schema_version == "generation-suggestion/1.0.0"
                else "AUTOMATIC_APPLY"
            ),
            state="LEASED",
            not_before=now,
            lease_owner=worker_id,
            lease_token=uuid.uuid4(),
            lease_expires_at=now + timedelta(minutes=5),
            resource_version=2,
        )
    )
    session.flush()
    background = session.get(BackgroundJob, background_id)
    assert background is not None
    return background


def test_comparison_reads_only_sealed_history_and_committed_receipt(
    clean_api_database: Engine,
) -> None:
    seed = _seed_apply_job(
        clean_api_database,
        with_human_reuse=True,
        with_comparison_context=True,
    )
    assert seed.reused_case_id is not None
    job_id = f"generation_job_{seed.job_id.hex}"
    provider = ProviderCallTripwire()
    sessions = sessionmaker(clean_api_database, expire_on_commit=False)
    with sessions.begin() as session:
        queued = GenerationRepository(session).create_next_job_with_context(
            slack_work_item_id=seed.slack_work_item_id
        )
        queued_id = f"generation_job_{queued.job.id.hex}"

    with TestClient(
        create_app(
            validate_database=False,
            database_engine=clean_api_database,
            generation_runner=provider,
        )
    ) as client:
        missing = client.get(
            f"/api/generation-jobs/generation_job_{uuid.uuid4().hex}/comparison"
        )
        assert missing.status_code == 404
        assert missing.json()["code"] == "RESOURCE_NOT_FOUND"

        before_read = _comparison_row_counts(clean_api_database)
        initial = client.get(f"/api/generation-jobs/{job_id}/comparison")
        assert initial.status_code == 200
        assert _comparison_row_counts(clean_api_database) == before_read
        initial_body = initial.json()
        assert initial_body["policy"] == {
            "sourceBasis": "SEALED_GENERATION_JOB",
            "readOnly": True,
            "liveSourceSubstitution": False,
            "generationInputReconstructed": False,
            "providerCallAttempted": False,
            "mutationPerformed": False,
        }
        assert initial_body["slackSource"]["permalink"] == {
            "state": "PRESENT",
            "value": "https://synthetic.invalid/generation/apply/root",
            "reason": None,
        }
        thread_evidence = initial_body["slackSource"]["threadEvidence"]
        assert thread_evidence["availability"] == "AVAILABLE"
        assert thread_evidence["reason"] is None
        assert [
            (
                item["index"],
                item["role"],
                item["operation"],
                item["body"]["value"],
                item["authorId"]["value"],
            )
            for item in thread_evidence["items"]
        ] == [
            (
                0,
                "ROOT",
                "CREATE",
                "Generated paths are grounded in this active Slack item.",
                "U_GENERATION_APPLY",
            )
        ]
        assert [field["field"] for field in initial_body["slackSource"]["fields"]] == [
            "DEVELOPMENT_ITEM",
            "REQUESTED_MENU",
            "FEATURE_DESCRIPTION",
            "REQUEST_DETAILS",
        ]
        assert [
            field["value"]["value"] for field in initial_body["slackSource"]["fields"]
        ] == [
            "Checkout rollout",
            "Payments > Checkout",
            "Generate regression cases from the sealed request.",
            "Retain evidence links and candidate order.",
        ]
        assert all(
            field["sourceReferences"] for field in initial_body["slackSource"]["fields"]
        )
        assert initial_body["activeKnowledge"]["availability"] == "AVAILABLE"
        assert [
            (
                item["nodeType"],
                item["content"]["value"],
                item["selectionReason"],
            )
            for item in initial_body["activeKnowledge"]["items"]
        ] == [
            (
                "DECISION",
                "Comparison must retain the policy active at the seal.",
                "ACTIVE_DECISION",
            ),
            (
                "CONSTRAINT",
                "Comparison must never consult live Slack, knowledge, or case state.",
                "ACTIVE_CONSTRAINT",
            ),
        ]
        assert initial_body["existingCases"]["availability"] == "AVAILABLE"
        existing_case = initial_body["existingCases"]["items"][0]
        assert existing_case["testCaseId"] == f"case_{seed.reused_case_id.hex}"
        assert existing_case["testCaseVersionId"] == (
            f"case_version_{seed.reused_version_id.hex}"
        )
        assert existing_case["caseKey"]["state"] == "PRESENT"
        assert existing_case["content"]["state"] == "PRESENT"
        assert existing_case["content"]["reason"] is None
        assert existing_case["content"]["value"] == {
            "summary": "Generated path alpha",
            "precondition": "Signed in",
            "steps": [
                {
                    "sequence": 1,
                    "precondition": "Signed in",
                    "action": "Open flow alpha",
                    "expectedResult": None,
                },
                {
                    "sequence": 2,
                    "precondition": None,
                    "action": "Complete flow alpha",
                    "expectedResult": None,
                },
            ],
            "expectedResultMode": "CASE",
            "caseExpectedResult": "Flow alpha completes",
            "priority": "HIGH",
            "labels": ["generated", "alpha"],
        }
        result_cases = seed.result["cases"]
        assert isinstance(result_cases, list)
        candidates = initial_body["generation"]["candidates"]
        assert [row["index"] for row in candidates] == list(range(5))
        assert [row["candidate"] for row in candidates] == result_cases
        assert all(row["apply"]["state"] == "NOT_APPLIED" for row in candidates)
        expected_edge_order = [
            (candidate_index, evidence_index)
            for candidate_index, candidate in enumerate(result_cases)
            if isinstance(candidate, dict)
            for evidence_index, _ in enumerate(candidate["evidenceNodeIds"])
        ]
        assert [
            (edge["candidateIndex"], edge["evidenceIndex"])
            for edge in initial_body["referenceEdges"]["items"]
        ] == expected_edge_order
        assert all(
            edge["target"]["state"] == "RESOLVED"
            and edge["target"]["contextReferences"]
            for edge in initial_body["referenceEdges"]["items"]
        )
        assert {
            edge["target"]["sourceKind"]
            for edge in initial_body["referenceEdges"]["items"]
        } == {"SLACK_WORK_ITEM", "ACTIVE_KNOWLEDGE", "EXISTING_CASE"}
        assert initial_body["apply"] == {"state": "NOT_APPLIED", "receipt": None}

        initial_reconstruction = client.get(
            f"/api/generation-jobs/{job_id}/reconstruction"
        )
        assert initial_reconstruction.status_code == 200
        reconstruction_body = initial_reconstruction.json()
        assert reconstruction_body["availability"] == "AVAILABLE"
        assert reconstruction_body["input"]["slackWorkItem"]["developmentItem"] == (
            "Checkout rollout"
        )
        assert [
            item["nodeType"] for item in reconstruction_body["input"]["activeKnowledge"]
        ] == ["DECISION", "CONSTRAINT"]
        reconstructed_case = reconstruction_body["input"]["existingCases"][0]
        assert reconstructed_case["testCaseId"] == f"case_{seed.reused_case_id.hex}"
        assert reconstructed_case["steps"] == existing_case["content"]["value"]["steps"]
        assert reconstructed_case["labels"] == ["alpha", "generated"]
        assert provider.calls == 0

        plan = client.get(f"/api/generation-jobs/{job_id}/apply-plan")
        assert plan.status_code == 200
        plan_body = plan.json()
        apply_key = "generation-comparison-apply-001"
        applied = client.post(
            f"/api/generation-jobs/{job_id}/apply",
            headers={"Idempotency-Key": apply_key},
            json={
                "idempotencyKey": apply_key,
                "expectedContextDigest": plan_body["expectedContextDigest"],
                "expectedResultDigest": plan_body["expectedResultDigest"],
                "placement": plan_body["placement"],
            },
        )
        assert applied.status_code == 201

        after_apply_counts = _comparison_row_counts(clean_api_database)
        compared = client.get(f"/api/generation-jobs/{job_id}/comparison")
        assert compared.status_code == 200
        assert _comparison_row_counts(clean_api_database) == after_apply_counts
        historical_body = compared.json()
        receipt = applied.json()
        assert historical_body["apply"]["state"] == "APPLIED"
        assert historical_body["apply"]["receipt"] == {
            key: value
            for key, value in receipt.items()
            if key not in {"jobStatus", "cases"}
        }
        linked = historical_body["generation"]["candidates"]
        assert [row["apply"]["outcome"] for row in linked] == receipt["cases"]
        assert linked[0]["apply"]["outcome"]["outcome"] == "REUSED"
        assert linked[0]["apply"]["outcome"]["case"]["currentVersionId"] == (
            f"case_version_{seed.reused_version_id.hex}"
        )
        assert all(row["apply"]["applyId"] == receipt["id"] for row in linked)

    with sessions.begin() as session:
        SlackRepository(session).ingest_event(
            SlackEventInput(
                event_id=f"Ev-generation-comparison-edit-{uuid.uuid4().hex}",
                slack_workspace_id="T_GENERATION_APPLY",
                channel_id="C_GENERATION_APPLY",
                event_type="EDIT",
                event_ts="1787700201.000100",
                message_ts="1787700200.000000",
                revision_ts="1787700201.000000",
                body="Live Slack content changed after the generation seal.",
                author_id="U_GENERATION_APPLY_NEW",
                permalink="https://synthetic.invalid/generation/apply/live-edit",
                raw_payload={"type": "message_changed"},
            )
        )
        root_node_id = session.scalar(
            select(GenerationContext.node_id)
            .where(GenerationContext.generation_job_id == seed.job_id)
            .order_by(GenerationContext.rank)
            .limit(1)
        )
        assert root_node_id is not None
        sealed_node_ids = tuple(
            node_id
            for node_id in session.scalars(
                select(GenerationContext.node_id).where(
                    GenerationContext.generation_job_id == seed.job_id,
                    GenerationContext.node_id.is_not(None),
                )
            )
            if node_id is not None
        )
        session.execute(
            update(KnowledgeNode)
            .where(KnowledgeNode.id == root_node_id)
            .values(
                properties={
                    "sourceVersion": 999,
                    "content": "Live knowledge projection changed after the seal.",
                    "developmentItem": "This live value must never appear.",
                }
            )
        )
        session.execute(
            update(KnowledgeNode)
            .where(
                KnowledgeNode.id.in_(sealed_node_ids),
                KnowledgeNode.node_type.in_(("DECISION", "CONSTRAINT")),
            )
            .values(
                properties={
                    "content": "Live decision or constraint changed after the seal."
                }
            )
        )
        CaseRepository(session).update_case(
            test_case_id=seed.reused_case_id,
            base_version=1,
            content=LedgerCaseContent(
                summary="Live human case changed after apply",
                precondition="Current state only",
                steps=(LedgerStepInput(action="Exercise current live behavior"),),
                expected_result_mode=ExpectedResultMode.CASE,
                expected_result="Current behavior differs from the sealed receipt",
                source_type=LedgerTestCaseSourceType.MANUAL,
            ),
            change_reason="Prove comparison ignores the current case version.",
            audit=AuditContext(
                source_type=AuditSourceType.API,
                trace_id="trace_generation_comparison_live_change",
                idempotency_key="generation-comparison-live-change-001",
            ),
        )

    after_live_change = _comparison_row_counts(clean_api_database)
    with TestClient(
        create_app(
            validate_database=False,
            database_engine=clean_api_database,
            generation_runner=provider,
        )
    ) as client:
        stable = client.get(f"/api/generation-jobs/{job_id}/comparison")
        stable_reconstruction = client.get(
            f"/api/generation-jobs/{job_id}/reconstruction"
        )
    assert stable.status_code == 200
    assert stable.json() == historical_body
    assert stable_reconstruction.status_code == 200
    assert stable_reconstruction.json() == reconstruction_body
    assert _comparison_row_counts(clean_api_database) == after_live_change
    assert provider.calls == 0

    before_queued_read = _comparison_row_counts(clean_api_database)
    with TestClient(
        create_app(
            validate_database=False,
            database_engine=clean_api_database,
            generation_runner=provider,
        )
    ) as client:
        queued_response = client.get(f"/api/generation-jobs/{queued_id}/comparison")
    assert queued_response.status_code == 200
    assert queued_response.json()["generation"] == {
        "availability": "EMPTY",
        "outputSchemaVersion": "1.0.0",
        "resultDigest": None,
        "categoryCandidate": None,
        "candidates": [],
        "reason": "JOB_NOT_SUCCEEDED",
    }
    assert queued_response.json()["referenceEdges"] == {
        "availability": "EMPTY",
        "items": [],
        "reason": "NO_GENERATED_CANDIDATES",
    }
    assert _comparison_row_counts(clean_api_database) == before_queued_read

    with sessions.begin() as session:
        repository = GenerationRepository(session)
        repository.begin_execution(queued.job.id)
        repository.succeed_execution(queued.job.id, {"invalid": "stored result"})
    before_conflict_read = _comparison_row_counts(clean_api_database)
    with TestClient(
        create_app(
            validate_database=False,
            database_engine=clean_api_database,
            generation_runner=provider,
        )
    ) as client:
        invalid = client.get(f"/api/generation-jobs/{queued_id}/comparison")
    assert invalid.status_code == 409
    assert invalid.json()["code"] == "GENERATION_STORED_RESULT_INVALID"
    assert invalid.json()["details"]["stage"] == "SCHEMA"
    assert _comparison_row_counts(clean_api_database) == before_conflict_read
    assert provider.calls == 0


def test_apply_creates_and_reuses_without_mutating_the_human_case(
    clean_api_database: Engine,
) -> None:
    seed = _seed_apply_job(clean_api_database, with_human_reuse=True)
    assert seed.reused_case_id is not None
    assert seed.reused_version_id is not None
    request = _apply_request(seed, key="generation-apply-success-001")
    job_id = f"generation_job_{seed.job_id.hex}"
    provider = ProviderCallTripwire()

    with TestClient(
        create_app(
            validate_database=False,
            database_engine=clean_api_database,
            generation_runner=provider,
        )
    ) as client:
        planned = client.get(f"/api/generation-jobs/{job_id}/apply-plan")
        assert planned.status_code == 200
        plan_body = planned.json()
        assert plan_body == {
            "generationJobId": job_id,
            "jobStatus": "SUCCEEDED",
            "expectedContextDigest": request["expectedContextDigest"],
            "expectedResultDigest": request["expectedResultDigest"],
            "placement": request["placement"],
        }
        created = client.post(
            f"/api/generation-jobs/{job_id}/apply",
            headers={"Idempotency-Key": str(request["idempotencyKey"])},
            json=request,
        )
        replayed = client.post(
            f"/api/generation-jobs/{job_id}/apply",
            headers={"Idempotency-Key": str(request["idempotencyKey"])},
            json=request,
        )
        assert created.status_code == 201
        assert replayed.status_code == 201
        assert replayed.json() == created.json()
        body = created.json()
        assert body["jobStatus"] == "SUCCEEDED"
        assert body["transactionStatus"] == "COMMITTED"
        assert body["counts"] == {"generated": 5, "created": 4, "reused": 1}
        reused = body["cases"][0]
        assert reused["outcome"] == "REUSED"
        assert reused["ledgerMutation"] == "NONE"
        assert reused["preserved"] is True
        assert reused["case"]["id"] == f"case_{seed.reused_case_id.hex}"
        created_cases = body["cases"][1:]
        assert [row["generatedCaseIndex"] for row in body["cases"]] == list(range(5))
        assert [row["case"]["key"] for row in created_cases] == [
            "TC-000002",
            "TC-000003",
            "TC-000004",
            "TC-000005",
        ]
        assert created_cases[0]["case"]["currentVersion"]["labels"] == [
            "generated",
            "beta",
        ]
        assert created_cases[0]["case"]["currentVersion"]["steps"] == [
            {"sequence": 1, "precondition": "Signed in", "action": "Open flow beta"},
            {"sequence": 2, "precondition": None, "action": "Complete flow beta"},
        ]
        source = created_cases[0]["case"]["currentVersion"]["source"]
        assert source["sourceType"] == "SLACK_AI"
        assert source["generationJobId"] == job_id
        assert source["contextDigest"] == seed.context_digest
        assert source["resultDigest"] == canonical_sha256(seed.result)
        result_cases = seed.result["cases"]
        assert isinstance(result_cases, list)
        second_result = result_cases[1]
        assert isinstance(second_result, dict)
        assert source["evidenceNodeIds"] == second_result["evidenceNodeIds"]
        assert source["sourceRevisionIds"]

        second_key = {**request, "idempotencyKey": "generation-apply-success-002"}
        conflict = client.post(
            f"/api/generation-jobs/{job_id}/apply",
            headers={"Idempotency-Key": str(second_key["idempotencyKey"])},
            json=second_key,
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "GENERATION_JOB_ALREADY_APPLIED"
        assert conflict.json()["details"]["applyId"] == body["id"]
        committed_plan = client.get(f"/api/generation-jobs/{job_id}/apply-plan")
        assert committed_plan.status_code == 409
        assert committed_plan.json()["code"] == "GENERATION_JOB_ALREADY_APPLIED"
        assert committed_plan.json()["details"]["applyId"] == body["id"]
    assert provider.calls == 0

    with Session(clean_api_database) as session:
        assert session.scalar(select(func.count()).select_from(GenerationApply)) == 1
        assert (
            session.scalar(select(func.count()).select_from(GenerationApplyOutcome))
            == 5
        )
        assert session.scalar(select(func.count()).select_from(LedgerTestCase)) == 5
        assert (
            session.scalar(select(func.count()).select_from(LedgerTestCaseVersion)) == 5
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(LedgerTestCaseVersion)
                .where(LedgerTestCaseVersion.test_case_id == seed.reused_case_id)
            )
            == 1
        )
        human = session.get(LedgerTestCaseVersion, seed.reused_version_id)
        assert human is not None
        assert human.source_type == LedgerTestCaseSourceType.MANUAL.value
        created_case_ids = [
            uuid.UUID(hex=row["case"]["id"].removeprefix("case_"))
            for row in created_cases
        ]
        created_version_ids = [
            uuid.UUID(
                hex=row["case"]["currentVersion"]["id"].removeprefix("case_version_")
            )
            for row in created_cases
        ]
        persisted_cases = list(
            session.scalars(
                select(LedgerTestCase)
                .where(LedgerTestCase.id.in_(created_case_ids))
                .order_by(LedgerTestCase.case_key)
            )
        )
        assert len(persisted_cases) == 4
        assert all(
            row.status == "ACTIVE" and row.version == 1 for row in persisted_cases
        )
        persisted_versions = list(
            session.scalars(
                select(LedgerTestCaseVersion)
                .where(LedgerTestCaseVersion.id.in_(created_version_ids))
                .order_by(LedgerTestCaseVersion.summary)
            )
        )
        assert len(persisted_versions) == 4
        assert all(
            row.version_number == 1
            and row.status == "ACTIVE"
            and row.source_type == LedgerTestCaseSourceType.SLACK_AI.value
            and row.expected_result_mode == ExpectedResultMode.CASE.value
            for row in persisted_versions
        )
        beta_version_id = created_version_ids[0]
        assert list(
            session.execute(
                select(
                    LedgerTestStep.sequence,
                    LedgerTestStep.precondition,
                    LedgerTestStep.action,
                )
                .where(LedgerTestStep.test_case_version_id == beta_version_id)
                .order_by(LedgerTestStep.sequence)
            )
        ) == [
            (1, "Signed in", "Open flow beta"),
            (2, None, "Complete flow beta"),
        ]
        assert set(
            session.scalars(
                select(Tag.name)
                .join(
                    LedgerTestCaseVersionTag, LedgerTestCaseVersionTag.tag_id == Tag.id
                )
                .where(LedgerTestCaseVersionTag.test_case_version_id == beta_version_id)
            )
        ) == {"generated", "beta"}
        beta = session.get(LedgerTestCaseVersion, beta_version_id)
        assert beta is not None
        assert beta.source_metadata == source
        assert beta.generation_reason == second_result["generationReason"]
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action == "APPLY_GENERATION_JOB",
                    AuditLog.idempotency_key == request["idempotencyKey"],
                )
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(
                    OutboxEvent.aggregate_type == "GENERATION_APPLY",
                    OutboxEvent.event_type == "GENERATION_APPLY_COMMITTED",
                )
            )
            == 1
        )
        assert session.get(GenerationJob, seed.job_id).status == "SUCCEEDED"  # type: ignore[union-attr]


def test_automatic_apply_atomically_captures_and_delivers_one_safe_reply(
    clean_api_database: Engine,
) -> None:
    seed = _seed_apply_job(
        clean_api_database,
        with_human_reuse=True,
        automatic=True,
    )
    request = _apply_request(seed, key="generation-automatic-notification-001")
    job_id = f"generation_job_{seed.job_id.hex}"

    with TestClient(
        create_app(
            validate_database=False,
            database_engine=clean_api_database,
            public_web_base_url="https://tc.example/app",
        )
    ) as client:
        response = client.post(
            f"/api/generation-jobs/{job_id}/apply",
            headers={"Idempotency-Key": str(request["idempotencyKey"])},
            json=request,
        )
    assert response.status_code == 201

    sessions = sessionmaker(clean_api_database, expire_on_commit=False)
    with sessions.begin() as session:
        intent = session.scalar(
            select(GenerationSlackNotificationIntent).where(
                GenerationSlackNotificationIntent.generation_job_id == seed.job_id
            )
        )
        assert intent is not None
        assert intent.notification_kind == "SUCCESS_APPLIED"
        assert intent.delivery_disposition == "POST"
        assert intent.message_snapshot["createdCount"] == 4
        assert intent.message_snapshot["reusedCount"] == 1
        assert intent.message_snapshot["link"] == {
            "linkMode": "ABSOLUTE_HTTPS",
            "uiUrl": f"https://tc.example/app/generation/jobs/{job_id}",
        }
        assert "permalink" not in intent.message_text.casefold()
        intent_id = intent.id
        claim = JobRepository(session).claim_next(
            worker_id="t06-delivery-worker",
            stale_after=timedelta(minutes=5),
            job_types=("SLACK_GENERATION_NOTIFICATION_DELIVERY",),
        )
        assert claim is not None

    transport = DeterministicFakeSlackTransport()
    delivery = GenerationSlackNotificationDelivery(
        session_factory=sessions,
        transport=transport,
        token_workspace_id="T_GENERATION_APPLY",
    )
    delivery.deliver(intent_id, claim)
    # A replay after the durable receipt must be a provider-free no-op.
    delivery.deliver(intent_id, claim)

    with sessions() as session:
        receipt = session.scalar(
            select(GenerationSlackNotificationReceipt).where(
                GenerationSlackNotificationReceipt.intent_id == intent_id
            )
        )
        assert receipt is not None
        assert receipt.terminal_outcome == "DELIVERED"
        assert receipt.proof == "POST_ACK"
    assert [call[0] for call in transport.calls].count("chat.postMessage") == 1


def test_automatic_apply_cannot_fabricate_materialization_worker_authority(
    clean_api_database: Engine,
) -> None:
    seed = _seed_apply_job(
        clean_api_database,
        with_human_reuse=False,
        automatic=True,
        arm_automatic=False,
    )
    request = _apply_request(seed, key="generation-automatic-no-worker-001")
    job_id = f"generation_job_{seed.job_id.hex}"

    with TestClient(
        create_app(validate_database=False, database_engine=clean_api_database),
        raise_server_exceptions=False,
    ) as client:
        response = client.post(
            f"/api/generation-jobs/{job_id}/apply",
            headers={"Idempotency-Key": str(request["idempotencyKey"])},
            json=request,
        )
    assert response.status_code == 500
    assert response.json()["code"] == "GENERATION_APPLY_TRANSACTION_FAILED"
    assert response.json()["details"]["stage"] == "NOTIFICATION"

    with Session(clean_api_database) as session:
        assert session.scalar(
            select(func.count())
            .select_from(GenerationApply)
            .where(GenerationApply.generation_job_id == seed.job_id)
        ) == 0
        assert session.scalar(
            select(func.count())
            .select_from(BackgroundJob)
            .where(
                BackgroundJob.job_type == "AUTOMATIC_GENERATION_MATERIALIZE",
            )
        ) == 0
        assert session.scalar(
            select(func.count())
            .select_from(GenerationSlackNotificationIntent)
            .where(GenerationSlackNotificationIntent.generation_job_id == seed.job_id)
        ) == 0


def test_self_notification_changes_neither_events_nor_thread_sync_source_state(
    clean_api_database: Engine,
) -> None:
    seed = _seed_apply_job(
        clean_api_database,
        with_human_reuse=False,
        automatic=True,
    )
    request = _apply_request(seed, key="generation-automatic-self-filter-001")
    with TestClient(
        create_app(validate_database=False, database_engine=clean_api_database)
    ) as client:
        response = client.post(
            f"/api/generation-jobs/generation_job_{seed.job_id.hex}/apply",
            headers={"Idempotency-Key": str(request["idempotencyKey"])},
            json=request,
        )
    assert response.status_code == 201

    sessions = sessionmaker(clean_api_database, expire_on_commit=False)
    with sessions.begin() as session:
        intent = session.scalar(
            select(GenerationSlackNotificationIntent).where(
                GenerationSlackNotificationIntent.generation_job_id == seed.job_id
            )
        )
        thread_id = session.scalar(
            select(SlackThread.id).where(
                SlackThread.slack_work_item_id == seed.slack_work_item_id
            )
        )
        claim = JobRepository(session).claim_next(
            worker_id="t06-self-filter-delivery",
            stale_after=timedelta(minutes=5),
            job_types=("SLACK_GENERATION_NOTIFICATION_DELIVERY",),
        )
        assert intent is not None and thread_id is not None and claim is not None

    transport = DeterministicFakeSlackTransport()
    GenerationSlackNotificationDelivery(
        session_factory=sessions,
        transport=transport,
        token_workspace_id="T_GENERATION_APPLY",
    ).deliver(intent.id, claim)
    provider_reply = transport.conversations_replies(
        channel_id=intent.channel_id,
        thread_ts=intent.thread_ts,
    )[0]
    event_payload = {
        "type": "event_callback",
        "event_id": "Ev-t06-self-filter-001",
        "team_id": intent.slack_workspace_id,
        "event_time": 1_900_000_001,
        "event": dict(provider_reply.raw_payload),
    }
    assert ledger_event(normalize_events_api_payload(event_payload)) is None

    with sessions() as session:
        before = {
            "sourceVersion": session.get(
                SlackWorkItem, seed.slack_work_item_id
            ).source_version,  # type: ignore[union-attr]
            "outbox": session.scalar(select(func.count()).select_from(OutboxEvent)),
            "generation": session.scalar(
                select(func.count()).select_from(GenerationJob)
            ),
        }
    SlackWorkerRuntime.with_transport(
        session_factory=sessions,
        transport=transport,
        token_workspace_id="T_GENERATION_APPLY",
        notification_delivery_enabled=False,
    ).sync_thread({"threadId": str(thread_id)})
    with sessions() as session:
        after = {
            "sourceVersion": session.get(
                SlackWorkItem, seed.slack_work_item_id
            ).source_version,  # type: ignore[union-attr]
            "outbox": session.scalar(select(func.count()).select_from(OutboxEvent)),
            "generation": session.scalar(
                select(func.count()).select_from(GenerationJob)
            ),
        }
    assert after == before


def test_response_loss_reconciles_the_provider_reply_without_a_second_post(
    clean_api_database: Engine,
) -> None:
    seed = _seed_apply_job(
        clean_api_database,
        with_human_reuse=False,
        automatic=True,
    )
    request = _apply_request(seed, key="generation-automatic-ack-loss-001")
    job_id = f"generation_job_{seed.job_id.hex}"
    with TestClient(
        create_app(validate_database=False, database_engine=clean_api_database)
    ) as client:
        response = client.post(
            f"/api/generation-jobs/{job_id}/apply",
            headers={"Idempotency-Key": str(request["idempotencyKey"])},
            json=request,
        )
    assert response.status_code == 201

    sessions = sessionmaker(clean_api_database, expire_on_commit=False)
    with sessions.begin() as session:
        intent_id = session.scalar(
            select(GenerationSlackNotificationIntent.id).where(
                GenerationSlackNotificationIntent.generation_job_id == seed.job_id
            )
        )
        assert intent_id is not None
        first_claim = JobRepository(session).claim_next(
            worker_id="t06-ack-loss-worker-1",
            stale_after=timedelta(minutes=5),
            job_types=("SLACK_GENERATION_NOTIFICATION_DELIVERY",),
        )
        assert first_claim is not None

    transport = DeterministicFakeSlackTransport()
    transport.lose_ack_after_accept_once()
    delivery = GenerationSlackNotificationDelivery(
        session_factory=sessions,
        transport=transport,
        token_workspace_id="T_GENERATION_APPLY",
    )
    with pytest.raises(GenerationSlackNotificationRetry):
        delivery.deliver(intent_id, first_claim)
    with sessions.begin() as session:
        plan = JobRepository(session).fail(
            job_id=first_claim.id,
            worker_id=first_claim.locked_by,
            error="WRITE_AMBIGUOUS",
            base_seconds=0,
            max_seconds=0,
            attempt=first_claim.attempt,
        )
        assert plan is not None
    with sessions.begin() as session:
        second_claim = JobRepository(session).claim_next(
            worker_id="t06-ack-loss-worker-2",
            stale_after=timedelta(minutes=5),
            job_types=("SLACK_GENERATION_NOTIFICATION_DELIVERY",),
        )
        assert second_claim is not None

    delivery.deliver(intent_id, second_claim)

    with sessions() as session:
        receipt = session.scalar(
            select(GenerationSlackNotificationReceipt).where(
                GenerationSlackNotificationReceipt.intent_id == intent_id
            )
        )
        assert receipt is not None
        assert receipt.terminal_outcome == "DELIVERED"
        assert receipt.proof == "THREAD_RECONCILIATION"
    call_names = [call[0] for call in transport.calls]
    assert call_names.count("chat.postMessage") == 1
    assert call_names.count("find.notification") == 1


def test_crash_after_post_permit_never_blind_reposts_and_finishes_with_receipt(
    clean_api_database: Engine,
) -> None:
    seed = _seed_apply_job(
        clean_api_database,
        with_human_reuse=False,
        automatic=True,
    )
    request = _apply_request(seed, key="generation-automatic-pre-call-crash-001")
    with TestClient(
        create_app(validate_database=False, database_engine=clean_api_database)
    ) as client:
        response = client.post(
            f"/api/generation-jobs/generation_job_{seed.job_id.hex}/apply",
            headers={"Idempotency-Key": str(request["idempotencyKey"])},
            json=request,
        )
    assert response.status_code == 201

    sessions = sessionmaker(clean_api_database, expire_on_commit=False)
    with sessions.begin() as session:
        intent_id = session.scalar(
            select(GenerationSlackNotificationIntent.id).where(
                GenerationSlackNotificationIntent.generation_job_id == seed.job_id
            )
        )
        delivery_job = session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.job_type == "SLACK_GENERATION_NOTIFICATION_DELIVERY"
            )
        )
        assert intent_id is not None and delivery_job is not None
        delivery_job.attempts = 10
        first_claim = JobRepository(session).claim_next(
            worker_id="t06-pre-call-crash-1",
            stale_after=timedelta(minutes=5),
            job_types=("SLACK_GENERATION_NOTIFICATION_DELIVERY",),
        )
        assert first_claim is not None and first_claim.attempt == 11

    transport = DeterministicFakeSlackTransport()
    delivery = GenerationSlackNotificationDelivery(
        session_factory=sessions,
        transport=transport,
        token_workspace_id="T_GENERATION_APPLY",
    )
    prepared_id, operation = delivery._prepare(intent_id, first_claim)
    assert prepared_id is not None and operation == "POST"
    # Simulate a crash after the permit commit but before invoking the provider.
    with sessions.begin() as session:
        assert JobRepository(session).fail(
            job_id=first_claim.id,
            worker_id=first_claim.locked_by,
            error="process_lost_before_provider_call",
            base_seconds=0,
            max_seconds=0,
            attempt=first_claim.attempt,
        ) is not None
    with sessions.begin() as session:
        final_claim = JobRepository(session).claim_next(
            worker_id="t06-pre-call-crash-2",
            stale_after=timedelta(minutes=5),
            job_types=("SLACK_GENERATION_NOTIFICATION_DELIVERY",),
        )
        assert final_claim is not None and final_claim.attempt == final_claim.max_attempts

    delivery.deliver(intent_id, final_claim)
    with sessions() as session:
        receipt = session.scalar(
            select(GenerationSlackNotificationReceipt).where(
                GenerationSlackNotificationReceipt.intent_id == intent_id
            )
        )
        assert receipt is not None
        assert receipt.terminal_outcome == "DEAD"
        assert receipt.reason == "AMBIGUOUS_UNRESOLVED"
    assert [call[0] for call in transport.calls].count("chat.postMessage") == 0
    assert [call[0] for call in transport.calls].count("find.notification") == 1


def test_final_rate_limit_attempt_records_retry_after_and_terminal_receipt(
    clean_api_database: Engine,
) -> None:
    seed = _seed_apply_job(
        clean_api_database,
        with_human_reuse=False,
        automatic=True,
    )
    request = _apply_request(seed, key="generation-automatic-rate-exhaustion-001")
    with TestClient(
        create_app(validate_database=False, database_engine=clean_api_database)
    ) as client:
        response = client.post(
            f"/api/generation-jobs/generation_job_{seed.job_id.hex}/apply",
            headers={"Idempotency-Key": str(request["idempotencyKey"])},
            json=request,
        )
    assert response.status_code == 201

    sessions = sessionmaker(clean_api_database, expire_on_commit=False)
    with sessions.begin() as session:
        intent_id = session.scalar(
            select(GenerationSlackNotificationIntent.id).where(
                GenerationSlackNotificationIntent.generation_job_id == seed.job_id
            )
        )
        delivery_job = session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.job_type == "SLACK_GENERATION_NOTIFICATION_DELIVERY"
            )
        )
        assert intent_id is not None and delivery_job is not None
        delivery_job.attempts = delivery_job.max_attempts - 1
        claim = JobRepository(session).claim_next(
            worker_id="t06-final-rate-limit",
            stale_after=timedelta(minutes=5),
            job_types=("SLACK_GENERATION_NOTIFICATION_DELIVERY",),
        )
        assert claim is not None and claim.attempt == claim.max_attempts

    transport = DeterministicFakeSlackTransport()
    transport.queue_failure(
        SlackRateLimitError("rate limited", retry_after_seconds=17)
    )
    GenerationSlackNotificationDelivery(
        session_factory=sessions,
        transport=transport,
        token_workspace_id="T_GENERATION_APPLY",
    ).deliver(intent_id, claim)

    with sessions() as session:
        receipt = session.scalar(
            select(GenerationSlackNotificationReceipt).where(
                GenerationSlackNotificationReceipt.intent_id == intent_id
            )
        )
        observation = session.scalar(
            select(GenerationSlackNotificationDeliveryObservation)
            .join(
                GenerationSlackNotificationReceipt,
                GenerationSlackNotificationReceipt.attempt_id
                == GenerationSlackNotificationDeliveryObservation.attempt_id,
            )
            .where(GenerationSlackNotificationReceipt.intent_id == intent_id)
        )
        assert receipt is not None and observation is not None
        assert receipt.reason == "RATE_LIMIT_EXHAUSTED"
        assert observation.classification == "RATE_LIMITED"
        assert observation.retry_after_seconds == 17


def test_final_attempt_ack_loss_reconciles_before_finishing_the_job(
    clean_api_database: Engine,
) -> None:
    seed = _seed_apply_job(
        clean_api_database,
        with_human_reuse=False,
        automatic=True,
    )
    request = _apply_request(seed, key="generation-automatic-final-ack-loss-001")
    with TestClient(
        create_app(validate_database=False, database_engine=clean_api_database)
    ) as client:
        response = client.post(
            f"/api/generation-jobs/generation_job_{seed.job_id.hex}/apply",
            headers={"Idempotency-Key": str(request["idempotencyKey"])},
            json=request,
        )
    assert response.status_code == 201

    sessions = sessionmaker(clean_api_database, expire_on_commit=False)
    with sessions.begin() as session:
        intent_id = session.scalar(
            select(GenerationSlackNotificationIntent.id).where(
                GenerationSlackNotificationIntent.generation_job_id == seed.job_id
            )
        )
        delivery_job = session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.job_type == "SLACK_GENERATION_NOTIFICATION_DELIVERY"
            )
        )
        assert intent_id is not None and delivery_job is not None
        delivery_job.attempts = delivery_job.max_attempts - 1
        claim = JobRepository(session).claim_next(
            worker_id="t06-final-ack-loss",
            stale_after=timedelta(minutes=5),
            job_types=("SLACK_GENERATION_NOTIFICATION_DELIVERY",),
        )
        assert claim is not None and claim.attempt == claim.max_attempts

    transport = DeterministicFakeSlackTransport()
    transport.lose_ack_after_accept_once()
    GenerationSlackNotificationDelivery(
        session_factory=sessions,
        transport=transport,
        token_workspace_id="T_GENERATION_APPLY",
    ).deliver(intent_id, claim)

    with sessions() as session:
        receipt = session.scalar(
            select(GenerationSlackNotificationReceipt).where(
                GenerationSlackNotificationReceipt.intent_id == intent_id
            )
        )
        assert receipt is not None
        assert receipt.terminal_outcome == "DELIVERED"
        assert receipt.proof == "THREAD_RECONCILIATION"
    call_names = [call[0] for call in transport.calls]
    assert call_names.count("chat.postMessage") == 1
    assert call_names.count("find.notification") == 1


def test_raw_dead_receipt_cannot_claim_auth_failure_from_ack_observation(
    clean_api_database: Engine,
) -> None:
    seed = _seed_apply_job(
        clean_api_database,
        with_human_reuse=False,
        automatic=True,
    )
    request = _apply_request(seed, key="generation-automatic-forged-dead-001")
    with TestClient(
        create_app(validate_database=False, database_engine=clean_api_database)
    ) as client:
        response = client.post(
            f"/api/generation-jobs/generation_job_{seed.job_id.hex}/apply",
            headers={"Idempotency-Key": str(request["idempotencyKey"])},
            json=request,
        )
    assert response.status_code == 201

    sessions = sessionmaker(clean_api_database, expire_on_commit=False)
    with sessions.begin() as session:
        intent = session.scalar(select(GenerationSlackNotificationIntent))
        claim = JobRepository(session).claim_next(
            worker_id="t06-forged-dead",
            stale_after=timedelta(minutes=5),
            job_types=("SLACK_GENERATION_NOTIFICATION_DELIVERY",),
        )
        assert intent is not None and claim is not None
        intent_id = intent.id

    delivery = GenerationSlackNotificationDelivery(
        session_factory=sessions,
        transport=DeterministicFakeSlackTransport(),
        token_workspace_id="T_GENERATION_APPLY",
    )
    attempt_id, operation = delivery._prepare(intent_id, claim)
    assert attempt_id is not None and operation == "POST"

    with pytest.raises(DBAPIError):
        with sessions.begin() as session:
            current = session.get(GenerationSlackNotificationIntent, intent_id)
            assert current is not None
            session.add(
                GenerationSlackNotificationDeliveryObservation(
                    id=uuid.uuid4(),
                    attempt_id=attempt_id,
                    classification="ACKNOWLEDGED",
                    safe_diagnostic="ACK_VALID",
                )
            )
            session.add(
                GenerationSlackNotificationReceipt(
                    id=uuid.uuid4(),
                    intent_id=intent_id,
                    attempt_id=attempt_id,
                    terminal_outcome="DEAD",
                    reason="AUTH_REJECTED",
                    slack_workspace_id=current.slack_workspace_id,
                    channel_id=current.channel_id,
                    root_message_ts=current.root_message_ts,
                    client_message_id=current.client_message_id,
                    message_digest=current.message_digest,
                )
            )


def test_source_change_after_capture_suppresses_delivery_without_slack_call(
    clean_api_database: Engine,
) -> None:
    seed = _seed_apply_job(
        clean_api_database,
        with_human_reuse=False,
        automatic=True,
    )
    request = _apply_request(seed, key="generation-automatic-stale-source-001")
    job_id = f"generation_job_{seed.job_id.hex}"
    with TestClient(
        create_app(validate_database=False, database_engine=clean_api_database)
    ) as client:
        response = client.post(
            f"/api/generation-jobs/{job_id}/apply",
            headers={"Idempotency-Key": str(request["idempotencyKey"])},
            json=request,
        )
    assert response.status_code == 201

    sessions = sessionmaker(clean_api_database, expire_on_commit=False)
    with sessions.begin() as session:
        intent_id = session.scalar(
            select(GenerationSlackNotificationIntent.id).where(
                GenerationSlackNotificationIntent.generation_job_id == seed.job_id
            )
        )
        assert intent_id is not None
        ingested = SlackRepository(session).ingest_event(
            SlackEventInput(
                event_id="Ev-generation-notification-stale-001",
                slack_workspace_id="T_GENERATION_APPLY",
                channel_id="C_GENERATION_APPLY",
                event_type="REPLY",
                event_ts="1787700201.000100",
                message_ts="1787700201.000000",
                thread_ts="1787700200.000000",
                body="A new source reply supersedes the sealed generation source.",
                author_id="U_GENERATION_APPLY",
                raw_payload={"type": "message"},
            )
        )
        assert ingested.work_item_id == seed.slack_work_item_id
        claim = JobRepository(session).claim_next(
            worker_id="t06-stale-source-worker",
            stale_after=timedelta(minutes=5),
            job_types=("SLACK_GENERATION_NOTIFICATION_DELIVERY",),
        )
        assert claim is not None

    transport = DeterministicFakeSlackTransport()
    GenerationSlackNotificationDelivery(
        session_factory=sessions,
        transport=transport,
        token_workspace_id="T_GENERATION_APPLY",
    ).deliver(intent_id, claim)

    with sessions() as session:
        receipt = session.scalar(
            select(GenerationSlackNotificationReceipt).where(
                GenerationSlackNotificationReceipt.intent_id == intent_id
            )
        )
        assert receipt is not None
        assert receipt.terminal_outcome == "SUPPRESSED"
        assert receipt.reason == "SUPPRESSED_STALE_SOURCE"
    assert transport.calls == []


def test_private_access_revocation_suppresses_delivery_without_slack_call(
    clean_api_database: Engine,
) -> None:
    seed = _seed_apply_job(
        clean_api_database,
        with_human_reuse=False,
        automatic=True,
    )
    request = _apply_request(seed, key="generation-automatic-private-revoked-001")
    job_id = f"generation_job_{seed.job_id.hex}"
    with TestClient(
        create_app(validate_database=False, database_engine=clean_api_database)
    ) as client:
        response = client.post(
            f"/api/generation-jobs/{job_id}/apply",
            headers={"Idempotency-Key": str(request["idempotencyKey"])},
            json=request,
        )
    assert response.status_code == 201

    sessions = sessionmaker(clean_api_database, expire_on_commit=False)
    with sessions.begin() as session:
        intent_id = session.scalar(
            select(GenerationSlackNotificationIntent.id).where(
                GenerationSlackNotificationIntent.generation_job_id == seed.job_id
            )
        )
        assert intent_id is not None
        SlackRepository(session).put_channel(
            slack_workspace_id="T_GENERATION_APPLY",
            channel_id="C_GENERATION_APPLY",
            is_private=True,
            include_private=False,
        )
        claim = JobRepository(session).claim_next(
            worker_id="t10-private-revoked-worker",
            stale_after=timedelta(minutes=5),
            job_types=("SLACK_GENERATION_NOTIFICATION_DELIVERY",),
        )
        assert claim is not None

    transport = DeterministicFakeSlackTransport()
    GenerationSlackNotificationDelivery(
        session_factory=sessions,
        transport=transport,
        token_workspace_id="T_GENERATION_APPLY",
    ).deliver(intent_id, claim)

    with sessions() as session:
        receipt = session.scalar(
            select(GenerationSlackNotificationReceipt).where(
                GenerationSlackNotificationReceipt.intent_id == intent_id
            )
        )
        assert receipt is not None
        assert receipt.terminal_outcome == "SUPPRESSED"
        assert receipt.reason == "SUPPRESSED_CONFIGURATION"
    assert transport.calls == []


def test_apply_rejects_semantic_reuse_outside_the_recomputed_candidate_set(
    clean_api_database: Engine,
) -> None:
    seed = _seed_apply_job(clean_api_database, with_human_reuse=True)
    assert seed.parent_id is not None
    sessions = sessionmaker(clean_api_database, expire_on_commit=False)
    with sessions.begin() as session:
        unrelated = CaseRepository(session).create_case(
            work_item_id=seed.parent_id,
            content=LedgerCaseContent(
                summary="Unrelated manual regression",
                precondition="A different product area is open",
                steps=(LedgerStepInput(action="Exercise unrelated flow"),),
                expected_result_mode=ExpectedResultMode.CASE,
                expected_result="The unrelated flow completes",
                source_type=LedgerTestCaseSourceType.MANUAL,
            ),
            change_reason="Unrelated human-authored baseline.",
            audit=AuditContext(
                source_type=AuditSourceType.API,
                trace_id="trace_seed_unrelated_human_case",
                idempotency_key="seed-unrelated-human-case-001",
            ),
        )
        unrelated_case_id = unrelated.test_case.id
        unrelated_version_id = unrelated.current_version.id
    request = _apply_request(seed, key="generation-apply-semantic-forgery-001")
    placement = request["placement"]
    assert isinstance(placement, dict)
    cases = placement["cases"]
    assert isinstance(cases, list)
    first = cases[0]
    assert isinstance(first, dict)
    first.update(
        {
            "decision": "REUSE_EXISTING",
            "testCaseId": f"case_{unrelated_case_id.hex}",
            "expectedResourceVersion": 1,
            "expectedCurrentVersionId": f"case_version_{unrelated_version_id.hex}",
            "expectedCurrentVersion": 1,
            "matchReason": "SEMANTIC",
        }
    )
    before = _ledger_observation(clean_api_database)
    job_id = f"generation_job_{seed.job_id.hex}"
    with TestClient(
        create_app(validate_database=False, database_engine=clean_api_database)
    ) as client:
        response = client.post(
            f"/api/generation-jobs/{job_id}/apply",
            headers={"Idempotency-Key": str(request["idempotencyKey"])},
            json=request,
        )
    assert response.status_code == 409
    assert response.json()["code"] == "GENERATION_PLACEMENT_CONFLICT"
    assert response.json()["details"]["reason"] == "REUSE_CANDIDATE_NOT_ELIGIBLE"
    assert response.json()["details"]["ledgerWriteStarted"] is False
    assert _ledger_observation(clean_api_database) == before


@pytest.mark.parametrize(
    ("failure_stage", "failure_index"),
    [("CREATE_VERSION", 2), ("OUTBOX", None)],
)
def test_apply_fault_rolls_back_every_row_and_preserves_next_case_key(
    clean_api_database: Engine,
    failure_stage: ApplyWriteStage,
    failure_index: int | None,
) -> None:
    seed = _seed_apply_job(clean_api_database, with_human_reuse=False)
    request = _apply_request(
        seed, key=f"generation-apply-rollback-{failure_stage.lower()}"
    )
    before = _ledger_observation(clean_api_database)

    def fault(stage: ApplyWriteStage, index: int | None) -> None:
        if stage == failure_stage and index == failure_index:
            raise RuntimeError("injected generation apply failure")

    job_id = f"generation_job_{seed.job_id.hex}"
    with TestClient(
        create_app(
            validate_database=False,
            database_engine=clean_api_database,
            generation_apply_fault=fault,
        ),
        raise_server_exceptions=False,
    ) as client:
        failed = client.post(
            f"/api/generation-jobs/{job_id}/apply",
            headers={"Idempotency-Key": str(request["idempotencyKey"])},
            json=request,
        )
        assert failed.status_code == 500
        assert failed.json()["code"] == "GENERATION_APPLY_TRANSACTION_FAILED"
        assert failed.json()["details"] == {
            "jobId": job_id,
            "stage": failure_stage,
            "transactionStatus": "ROLLED_BACK",
        }

    after = _ledger_observation(clean_api_database)
    assert {key: after[key] - before[key] for key in before} == {
        "workItems": 0,
        "testCases": 0,
        "testCaseVersions": 0,
        "testSteps": 0,
        "testCaseTags": 0,
        "testCaseVersionTags": 0,
        "generationApplyReceipts": 0,
        "generationApplyOutcomes": 0,
        "generationManagementEvents": 0,
        "keyAllocations": 0,
        "auditLogs": 0,
        "outboxEvents": 0,
    }

    with TestClient(
        create_app(validate_database=False, database_engine=clean_api_database)
    ) as client:
        retried = client.post(
            f"/api/generation-jobs/{job_id}/apply",
            headers={"Idempotency-Key": str(request["idempotencyKey"])},
            json=request,
        )
        assert retried.status_code == 201
        retry_body = retried.json()
        assert retry_body["parent"]["outcome"] == "CREATED"
        assert retry_body["parent"]["ledgerMutation"] == "CREATED_WORK_ITEM"
        assert retry_body["cases"][0]["case"]["key"] == "TC-000001"


def test_automatic_apply_notification_fault_rolls_back_apply_intent_and_job(
    clean_api_database: Engine,
) -> None:
    seed = _seed_apply_job(
        clean_api_database,
        with_human_reuse=False,
        automatic=True,
    )
    request = _apply_request(seed, key="generation-apply-notification-rollback")

    def fault(stage: ApplyWriteStage, index: int | None) -> None:
        if stage == "NOTIFICATION" and index is None:
            raise RuntimeError("injected notification capture failure")

    with TestClient(
        create_app(
            validate_database=False,
            database_engine=clean_api_database,
            generation_apply_fault=fault,
        ),
        raise_server_exceptions=False,
    ) as client:
        failed = client.post(
            f"/api/generation-jobs/generation_job_{seed.job_id.hex}/apply",
            headers={"Idempotency-Key": str(request["idempotencyKey"])},
            json=request,
        )
    assert failed.status_code == 500
    assert failed.json()["details"]["stage"] == "NOTIFICATION"

    sessions = sessionmaker(clean_api_database)
    with sessions() as session:
        assert session.scalar(
            select(func.count())
            .select_from(GenerationApply)
            .where(GenerationApply.generation_job_id == seed.job_id)
        ) == 0
        assert session.scalar(
            select(func.count())
            .select_from(GenerationSlackNotificationIntent)
            .where(GenerationSlackNotificationIntent.generation_job_id == seed.job_id)
        ) == 0
        assert session.scalar(
            select(func.count())
            .select_from(BackgroundJob)
            .where(
                BackgroundJob.job_type == "SLACK_GENERATION_NOTIFICATION_DELIVERY"
            )
        ) == 0


def test_apply_rejects_non_succeeded_and_stale_digest_without_ledger_writes(
    clean_api_database: Engine,
) -> None:
    seed = _seed_apply_job(clean_api_database, with_human_reuse=False)
    request = _apply_request(seed, key="generation-apply-stale-001")
    request["expectedResultDigest"] = "f" * 64
    before = _ledger_observation(clean_api_database)
    job_id = f"generation_job_{seed.job_id.hex}"
    with TestClient(
        create_app(validate_database=False, database_engine=clean_api_database)
    ) as client:
        valid_request = _apply_request(seed, key="generation-apply-header-001")
        missing_header = client.post(
            f"/api/generation-jobs/{job_id}/apply", json=valid_request
        )
        assert missing_header.status_code == 422
        assert missing_header.json()["code"] == "VALIDATION_FAILED"
        mismatched_header = client.post(
            f"/api/generation-jobs/{job_id}/apply",
            headers={"Idempotency-Key": "generation-apply-header-other"},
            json=valid_request,
        )
        assert mismatched_header.status_code == 422
        assert mismatched_header.json()["code"] == "VALIDATION_FAILED"

        stale = client.post(
            f"/api/generation-jobs/{job_id}/apply",
            headers={"Idempotency-Key": str(request["idempotencyKey"])},
            json=request,
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "GENERATION_PLACEMENT_CONFLICT"
        assert stale.json()["details"]["reason"] == "RESULT_DIGEST_MISMATCH"
        assert stale.json()["details"]["ledgerWriteStarted"] is False

        stale_category_request = copy.deepcopy(
            _apply_request(seed, key="generation-apply-category-stale-001")
        )
        placement = stale_category_request["placement"]
        assert isinstance(placement, dict)
        category = placement["category"]
        assert isinstance(category, dict)
        category["expectedResourceVersion"] = 2
        stale_category = client.post(
            f"/api/generation-jobs/{job_id}/apply",
            headers={"Idempotency-Key": str(stale_category_request["idempotencyKey"])},
            json=stale_category_request,
        )
        assert stale_category.status_code == 409
        assert stale_category.json()["code"] == "GENERATION_PLACEMENT_CONFLICT"
        assert stale_category.json()["details"]["reason"] == "CATEGORY_STALE"
        refreshed_plan = client.get(f"/api/generation-jobs/{job_id}/apply-plan")
        assert refreshed_plan.status_code == 200
        assert (
            refreshed_plan.json()["placement"]["category"]["expectedResourceVersion"]
            == 1
        )

        sessions = sessionmaker(clean_api_database, expire_on_commit=False)
        invalid_result = copy.deepcopy(seed.result)
        invalid_cases = invalid_result["cases"]
        assert isinstance(invalid_cases, list)
        invalid_first = invalid_cases[0]
        assert isinstance(invalid_first, dict)
        invalid_first["evidenceNodeIds"] = ["knowledge_node_not_in_context"]
        with sessions.begin() as session:
            invalid = GenerationRepository(session).create_next_job_with_context(
                slack_work_item_id=seed.slack_work_item_id
            )
            GenerationRepository(session).begin_execution(invalid.job.id)
            GenerationRepository(session).succeed_execution(
                invalid.job.id, invalid_result
            )
            invalid_id = f"generation_job_{invalid.job.id.hex}"
            invalid_context_digest = invalid.job.context_digest
            assert invalid_context_digest is not None
            queued = GenerationRepository(session).create_next_job_with_context(
                slack_work_item_id=seed.slack_work_item_id
            )
            queued_id = f"generation_job_{queued.job.id.hex}"
        invalid_request = copy.deepcopy(
            _apply_request(seed, key="generation-apply-evidence-invalid-001")
        )
        invalid_request["expectedContextDigest"] = invalid_context_digest
        invalid_request["expectedResultDigest"] = canonical_sha256(invalid_result)
        stored_invalid = client.post(
            f"/api/generation-jobs/{invalid_id}/apply",
            headers={"Idempotency-Key": str(invalid_request["idempotencyKey"])},
            json=invalid_request,
        )
        assert stored_invalid.status_code == 409
        assert stored_invalid.json()["code"] == "GENERATION_STORED_RESULT_INVALID"
        assert stored_invalid.json()["details"]["stage"] == "GROUNDING"
        assert (
            stored_invalid.json()["details"]["reason"]
            == "EVIDENCE_NOT_IN_SEALED_CONTEXT"
        )
        queued_request = {
            **request,
            "idempotencyKey": "generation-apply-queued-001",
            "expectedResultDigest": canonical_sha256(seed.result),
        }
        state_conflict = client.post(
            f"/api/generation-jobs/{queued_id}/apply",
            headers={"Idempotency-Key": str(queued_request["idempotencyKey"])},
            json=queued_request,
        )
        assert state_conflict.status_code == 409
        assert state_conflict.json()["code"] == "GENERATION_JOB_STATE_CONFLICT"
        assert state_conflict.json()["details"] == {
            "jobId": queued_id,
            "operation": "APPLY",
            "actualStatus": "QUEUED",
            "allowedStatuses": ["SUCCEEDED"],
            "ledgerWriteStarted": False,
        }
    assert _ledger_observation(clean_api_database) == before


def _seed_apply_job(
    engine: Engine,
    *,
    with_human_reuse: bool,
    with_comparison_context: bool = False,
    automatic: bool = False,
    arm_automatic: bool = True,
) -> ApplySeed:
    sessions = sessionmaker(engine, expire_on_commit=False)
    event = SlackEventInput(
        event_id=f"Ev-generation-apply-{uuid.uuid4().hex}",
        slack_workspace_id="T_GENERATION_APPLY",
        channel_id="C_GENERATION_APPLY",
        event_type="ROOT",
        event_ts="1787700200.000100",
        message_ts="1787700200.000000",
        thread_ts=None,
        revision_ts=None,
        body="Generated paths are grounded in this active Slack item.",
        author_id="U_GENERATION_APPLY",
        permalink="https://synthetic.invalid/generation/apply/root",
        channel_is_private=False,
        raw_payload={"type": "message"},
    )
    with sessions.begin() as session:
        slack = SlackRepository(session)
        slack.put_channel(
            slack_workspace_id=event.slack_workspace_id,
            channel_id=event.channel_id,
            channel_name="generation-apply",
        )
        ingested = slack.ingest_event(event)
        assert ingested.work_item_id is not None
        assert ingested.revision_id is not None
        slack_work_item_id = ingested.work_item_id
        revision_id = ingested.revision_id

    parent_id: uuid.UUID | None = None
    reused_case_id: uuid.UUID | None = None
    reused_version_id: uuid.UUID | None = None
    with sessions.begin() as session:
        revision, message = session.execute(
            select(SlackMessageRevision, SlackMessage)
            .join(SlackMessage, SlackMessage.id == SlackMessageRevision.message_id)
            .where(SlackMessageRevision.id == revision_id)
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
        root_properties: dict[str, object] = {
            "sourceVersion": 1,
            "content": revision.body,
        }
        if with_comparison_context:
            root_properties.update(
                {
                    "workspaceId": event.slack_workspace_id,
                    "channelId": event.channel_id,
                    "messageTs": event.message_ts,
                    "threadTs": event.message_ts,
                    "permalink": event.permalink,
                    "structuredFields": {
                        "developmentItem": "Checkout rollout",
                        "requestedMenu": "Payments > Checkout",
                        "featureDescription": (
                            "Generate regression cases from the sealed request."
                        ),
                        "requestDetails": (
                            "Retain evidence links and candidate order."
                        ),
                    },
                    "threadEvidence": [
                        {
                            "sourceRevisionId": f"slack_revision_{revision.id.hex}",
                            "messageId": f"slack_message_{message.id.hex}",
                            "messageTs": event.message_ts,
                            "revisionTs": revision.revision_ts,
                            "role": "ROOT",
                            "operation": "CREATE",
                            "authorId": message.author_id,
                            "body": revision.body,
                            "permalink": message.permalink,
                            "recordedAt": revision.received_at.isoformat(),
                        }
                    ],
                }
            )
        node = KnowledgeRepository(session).create_node(
            node_type="WORK_ITEM",
            label="Slack apply work item",
            entity_type="SLACK_WORK_ITEM",
            entity_id=slack_work_item_id,
            properties=root_properties,
            recorded_at=datetime.now(UTC),
            evidence=evidence,
        )
        generated = _generation_result(node.id)
        if with_human_reuse:
            case_repository = CaseRepository(session)
            audit = AuditContext(
                source_type=AuditSourceType.API,
                trace_id="trace_seed_human_case",
                idempotency_key="seed-human-case-001",
            )
            parent = case_repository.create_work_item(
                work_item_key="WORK-900",
                category_id=AI_INBOX_FOLDER_ID,
                summary="Slack apply work item",
                audit=audit,
            )
            generated_cases = generated["cases"]
            assert isinstance(generated_cases, list)
            first = generated_cases[0]
            assert isinstance(first, dict)
            aggregate = case_repository.create_case(
                work_item_id=parent.id,
                content=_case_content(first),
                change_reason="Human-authored baseline.",
                audit=audit,
            )
            parent_id = parent.id
            reused_case_id = aggregate.test_case.id
            reused_version_id = aggregate.current_version.id
            if with_comparison_context:
                decision = KnowledgeRepository(session).create_node(
                    node_type="DECISION",
                    label="Historical comparison policy",
                    properties={
                        "content": "Comparison must retain the policy active at the seal."
                    },
                    recorded_at=datetime.now(UTC),
                    evidence=evidence,
                )
                KnowledgeRepository(session).create_edge(
                    source_node_id=node.id,
                    predicate="DISCUSSES",
                    target_node_id=decision.id,
                    evidence=evidence,
                )
                constraint = KnowledgeRepository(session).create_node(
                    node_type="CONSTRAINT",
                    label="Historical comparison source boundary",
                    properties={
                        "content": (
                            "Comparison must never consult live Slack, knowledge, or case state."
                        )
                    },
                    recorded_at=datetime.now(UTC),
                    evidence=evidence,
                )
                KnowledgeRepository(session).create_edge(
                    source_node_id=node.id,
                    predicate="DISCUSSES",
                    target_node_id=constraint.id,
                    evidence=evidence,
                )
                case_evidence = EvidenceInput.from_content(
                    source_kind=KnowledgeSourceKind.LEDGER_ENTITY,
                    entity_type="TEST_CASE_VERSION",
                    entity_id=aggregate.current_version.id,
                    revision_key=str(aggregate.current_version.version_number),
                    content=str(first["summary"]),
                    recorded_at=aggregate.current_version.created_at,
                )
                case_node = KnowledgeRepository(session).create_node(
                    node_type="TEST_CASE",
                    label=str(first["summary"]),
                    entity_type="TEST_CASE",
                    entity_id=aggregate.test_case.id,
                    properties={
                        "currentVersionId": str(aggregate.current_version.id),
                        "summary": first["summary"],
                        "caseKey": aggregate.test_case.case_key,
                        "caseContent": {
                            "summary": first["summary"],
                            "precondition": "Signed in",
                            "steps": [
                                {
                                    "sequence": int(step["sequence"]),
                                    "precondition": step.get("precondition"),
                                    "action": step["action"],
                                    "expectedResult": None,
                                }
                                for step in first["steps"]
                                if isinstance(step, dict)
                            ],
                            "expectedResultMode": "CASE",
                            "caseExpectedResult": first["caseExpectedResult"],
                            "priority": first["priority"],
                            "labels": first["labels"],
                        },
                    },
                    recorded_at=datetime.now(UTC),
                    evidence=case_evidence,
                )
                KnowledgeRepository(session).create_edge(
                    source_node_id=node.id,
                    predicate="AFFECTS",
                    target_node_id=case_node.id,
                    evidence=case_evidence,
                )
                generated_cases = generated["cases"]
                assert isinstance(generated_cases, list)
                for index, candidate in enumerate(generated_cases):
                    assert isinstance(candidate, dict)
                    if index == 0:
                        candidate["evidenceNodeIds"] = [
                            f"knowledge_node_{node.id.hex}",
                            f"knowledge_node_{decision.id.hex}",
                        ]
                    elif index == 1:
                        candidate["evidenceNodeIds"] = [
                            f"knowledge_node_{case_node.id.hex}",
                            f"knowledge_node_{node.id.hex}",
                        ]
                    elif index == 2:
                        candidate["evidenceNodeIds"] = [
                            f"knowledge_node_{constraint.id.hex}",
                            f"knowledge_node_{node.id.hex}",
                        ]
        generation_repository = GenerationRepository(session)
        if automatic:
            persisted = generation_repository.get_or_create_automatic_job_with_context(
                slack_work_item_id=slack_work_item_id,
                source_version=1,
            ).persisted
        else:
            persisted = generation_repository.create_next_job_with_context(
                slack_work_item_id=slack_work_item_id
            )
        generation_repository.begin_execution(persisted.job.id)
        generation_repository.succeed_execution(persisted.job.id, generated)
        if automatic and arm_automatic:
            _arm_automatic_materialization(session, persisted.job)
        job_id = persisted.job.id
        context_digest = persisted.job.context_digest
        assert context_digest is not None
    return ApplySeed(
        job_id=job_id,
        slack_work_item_id=slack_work_item_id,
        context_digest=context_digest,
        result=generated,
        parent_id=parent_id,
        reused_case_id=reused_case_id,
        reused_version_id=reused_version_id,
    )


def _generation_result(node_id: uuid.UUID) -> dict[str, object]:
    evidence_id = f"knowledge_node_{node_id.hex}"
    names = ("alpha", "beta", "gamma", "delta", "epsilon")
    return {
        "outputSchemaVersion": "1.0.0",
        "cases": [
            {
                "summary": f"Generated path {name}",
                "steps": [
                    {
                        "sequence": 1,
                        "precondition": "Signed in",
                        "action": f"Open flow {name}",
                    },
                    {"sequence": 2, "action": f"Complete flow {name}"},
                ],
                "caseExpectedResult": f"Flow {name} completes",
                "priority": "HIGH",
                "labels": ["generated", name],
                "evidenceNodeIds": [evidence_id],
                "generationReason": "Grounded in the active Slack item.",
                "inferenceOnly": False,
                "openQuestions": [],
            }
            for name in names
        ],
        "categoryCandidate": {
            "categoryId": f"folder_{AI_INBOX_FOLDER_ID.hex}",
            "score": 1,
            "alternatives": [],
        },
    }


def _case_content(raw: dict[str, object]) -> LedgerCaseContent:
    steps = raw["steps"]
    assert isinstance(steps, list)
    labels = raw["labels"]
    assert isinstance(labels, list)
    return LedgerCaseContent(
        summary=str(raw["summary"]),
        precondition="Signed in",
        steps=tuple(
            LedgerStepInput(
                action=str(step["action"]),
                precondition=(
                    str(step["precondition"]) if "precondition" in step else None
                ),
            )
            for step in steps
            if isinstance(step, dict)
        ),
        expected_result_mode=ExpectedResultMode.CASE,
        expected_result=str(raw["caseExpectedResult"]),
        tag_names=tuple(str(value) for value in labels),
        source_type=LedgerTestCaseSourceType.MANUAL,
    )


def _apply_request(seed: ApplySeed, *, key: str) -> dict[str, object]:
    raw_cases = seed.result["cases"]
    assert isinstance(raw_cases, list)
    cases: list[dict[str, object]] = []
    for index, raw in enumerate(raw_cases):
        assert isinstance(raw, dict)
        steps = raw["steps"]
        assert isinstance(steps, list)
        content_key = exact_content_key(
            GeneratedCaseInput(
                summary=str(raw["summary"]),
                steps=tuple(
                    DeduplicationStep(
                        action=str(step["action"]),
                        precondition=(
                            str(step["precondition"])
                            if "precondition" in step
                            else None
                        ),
                    )
                    for step in steps
                    if isinstance(step, dict)
                ),
                case_expected_result=str(raw["caseExpectedResult"]),
            )
        )
        placement: dict[str, object] = {
            "generatedCaseIndex": index,
            "exactContentKey": content_key,
            "decision": "CREATE_NEW",
        }
        if index == 0 and seed.reused_case_id is not None:
            assert seed.reused_version_id is not None
            placement.update(
                {
                    "decision": "REUSE_EXISTING",
                    "testCaseId": f"case_{seed.reused_case_id.hex}",
                    "expectedResourceVersion": 1,
                    "expectedCurrentVersionId": f"case_version_{seed.reused_version_id.hex}",
                    "expectedCurrentVersion": 1,
                    "matchReason": "EXACT",
                }
            )
        cases.append(placement)
    if seed.parent_id is None:
        parent: dict[str, object] = {
            "decision": "CREATE_NEW",
            "expectedWorkItemKey": f"SLACK-{seed.slack_work_item_id.hex}",
            "expectedSummary": "Slack apply work item",
            "categoryId": f"folder_{AI_INBOX_FOLDER_ID.hex}",
            "method": "CREATE_NEW_PARENT",
        }
    else:
        parent = {
            "decision": "REUSE_EXISTING",
            "workItemId": f"work_item_{seed.parent_id.hex}",
            "expectedResourceVersion": 1,
            "expectedCategoryId": f"folder_{AI_INBOX_FOLDER_ID.hex}",
            "method": "EXACT_TITLE",
            "score": 1,
        }
    return {
        "idempotencyKey": key,
        "expectedContextDigest": seed.context_digest,
        "expectedResultDigest": canonical_sha256(seed.result),
        "placement": {
            "placementSchemaVersion": "1.0.0",
            "category": {
                "categoryId": f"folder_{AI_INBOX_FOLDER_ID.hex}",
                "expectedResourceVersion": 1,
                "method": "EXACT_NAME",
                "score": 1,
            },
            "parent": parent,
            "cases": cases,
        },
    }


def _ledger_observation(engine: Engine) -> dict[str, int]:
    with Session(engine) as session:

        def count(model: Any) -> int:
            return int(session.scalar(select(func.count()).select_from(model)) or 0)

        next_key = session.scalar(
            select(WorkspaceKeyCounter.next_value).where(
                WorkspaceKeyCounter.workspace_id == DEFAULT_WORKSPACE_ID,
                WorkspaceKeyCounter.entity_type == "TEST_CASE",
            )
        )
        return {
            "workItems": count(WorkItem),
            "testCases": count(LedgerTestCase),
            "testCaseVersions": count(LedgerTestCaseVersion),
            "testSteps": count(LedgerTestStep),
            "testCaseTags": count(LedgerTestCaseTag),
            "testCaseVersionTags": count(LedgerTestCaseVersionTag),
            "generationApplyReceipts": count(GenerationApply),
            "generationApplyOutcomes": count(GenerationApplyOutcome),
            "generationManagementEvents": count(ManagementEvent),
            "keyAllocations": int(next_key or 0),
            "auditLogs": count(AuditLog),
            "outboxEvents": count(OutboxEvent),
        }


def _comparison_row_counts(engine: Engine) -> dict[str, int]:
    counts = _ledger_observation(engine)
    with Session(engine) as session:
        counts.update(
            {
                "generationJobs": int(
                    session.scalar(select(func.count()).select_from(GenerationJob)) or 0
                ),
                "generationContexts": int(
                    session.scalar(select(func.count()).select_from(GenerationContext))
                    or 0
                ),
                "knowledgeNodes": int(
                    session.scalar(select(func.count()).select_from(KnowledgeNode)) or 0
                ),
                "slackMessages": int(
                    session.scalar(select(func.count()).select_from(SlackMessage)) or 0
                ),
                "slackMessageRevisions": int(
                    session.scalar(
                        select(func.count()).select_from(SlackMessageRevision)
                    )
                    or 0
                ),
            }
        )
    return counts
