from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, func, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from tc_api.main import create_app
from tc_backend.db.constants import DEFAULT_WORKSPACE_ID
from tc_backend.db.models import (
    Folder,
    GenerationJob,
    KnowledgeSourceKind,
    KnowledgeNode,
    SlackMessage,
    SlackMessageRevision,
    SlackWorkItem,
    TestCase as LedgerTestCase,
    TestCaseVersion as LedgerTestCaseVersion,
)
from tc_backend.db.slack import SlackEventInput, SlackRepository
from tc_backend.generation.errors import (
    GenerationErrorCode,
    GenerationTransportError,
)
from tc_backend.generation.lifecycle import GenerationRunResult
from tc_backend.generation.reconstruction import canonical_input_digest
from tc_backend.generation.reconstruction import GenerationInputV1
from tc_backend.repositories.generation_repository import (
    GenerationRepository,
    PersistedGenerationContext,
)
from tc_backend.repositories.knowledge_repository import (
    EvidenceInput,
    KnowledgeRepository,
)

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[3]


class FailingRunner:
    calls = 0

    def run(self, context: PersistedGenerationContext) -> GenerationRunResult:
        self.calls += 1
        assert context.job.status == "RUNNING"
        raise GenerationTransportError(
            GenerationErrorCode.TIMEOUT,
            "Generation provider timed out.",
            retryable=True,
            attempts=3,
        )


class SuccessfulRunner:
    calls = 0

    def __init__(self) -> None:
        self.payload = json.loads(
            (
                ROOT
                / "packages/contracts/generation/fixtures/1.0.0/output.grounded.json"
            ).read_text()
        )
        self.inputs: list[dict[str, object]] = []

    def run(self, context: PersistedGenerationContext) -> GenerationRunResult:
        self.calls += 1
        assert context.job.status == "RUNNING"
        self.inputs.append(deepcopy(dict(context.provider_input)))
        return GenerationRunResult(self.payload, attempts=1)


class ReconstructionTripwireRunner:
    calls = 0

    def run(self, context: PersistedGenerationContext) -> GenerationRunResult:
        del context
        self.calls += 1
        raise AssertionError("reconstruction must not call the generation runner")


def test_generation_job_api_is_idempotent_fail_closed_and_appends_retry_lineage(
    clean_api_database: Engine,
) -> None:
    work_item_id = _seed_grounded_slack_work_item(clean_api_database)
    mutable_category_id = uuid.uuid4()
    with Session(clean_api_database) as session, session.begin():
        session.add(
            Folder(
                id=mutable_category_id,
                workspace_id=DEFAULT_WORKSPACE_ID,
                name="Mutable generation category",
                normalized_name="mutable generation category",
                sort_order=1,
                version=1,
            )
        )
    work_item_api_id = f"slack_work_item_{work_item_id.hex}"
    create_payload = {
        "idempotencyKey": "generation-create-001",
        "slackWorkItemId": work_item_api_id,
    }

    with TestClient(
        create_app(
            validate_database=False,
            database_engine=clean_api_database,
        )
    ) as client:
        created = client.post("/api/generation-jobs", json=create_payload)
        replayed = client.post("/api/generation-jobs", json=create_payload)
        assert created.status_code == 201
        assert replayed.status_code == 201
        assert replayed.json() == created.json()
        root = created.json()
        root_id = root["job"]["id"]
        assert root["job"]["status"] == "QUEUED"
        assert root["job"]["lineage"] == {
            "rootJobId": root_id,
            "retryOfJobId": None,
            "attemptNumber": 1,
        }
        assert root["job"]["context"]["generationVersion"] == 1
        assert root["job"]["failure"] is None
        assert root["job"]["result"] is None
        assert root["contextItems"]
        initial_reconstruction = client.get(
            f"/api/generation-jobs/{root_id}/reconstruction"
        )
        assert initial_reconstruction.status_code == 200
        initial_input = initial_reconstruction.json()["input"]
        assert [item["categoryId"] for item in initial_input["categoryCandidates"]][
            0
        ] == f"folder_{mutable_category_id.hex}"

        setup = client.post(
            f"/api/generation-jobs/{root_id}/execute",
            json={"idempotencyKey": "generation-execute-001"},
        )
        assert setup.status_code == 503
        assert setup.json()["code"] == "GENERATION_SETUP_REQUIRED"
        assert setup.json()["details"] == {
            "jobId": root_id,
            "setupState": "SETUP_REQUIRED",
            "blockingDecisions": [
                "PROVIDER_SELECTION",
                "ENDPOINT",
                "CREDENTIALS",
                "COST_APPROVAL",
            ],
            "externalCallAttempted": False,
            "manualCaseCreationAvailable": True,
        }
        assert (
            client.get(f"/api/generation-jobs/{root_id}").json()["job"]["status"]
            == "QUEUED"
        )

    failing = FailingRunner()
    with TestClient(
        create_app(
            validate_database=False,
            database_engine=clean_api_database,
            generation_runner=failing,
        )
    ) as client:
        failed = client.post(
            f"/api/generation-jobs/{root_id}/execute",
            json={"idempotencyKey": "generation-execute-002"},
        )
        failed_replay = client.post(
            f"/api/generation-jobs/{root_id}/execute",
            json={"idempotencyKey": "generation-execute-002"},
        )
        assert failed.status_code == 200
        assert failed_replay.json() == failed.json()
        assert failing.calls == 1
        assert failed.json()["job"]["status"] == "FAILED"
        assert failed.json()["job"]["failure"] == {
            "code": "GENERATION_TIMEOUT",
            "message": "Generation provider timed out.",
            "retryable": True,
            "attempts": 3,
            "occurredAt": failed.json()["job"]["completedAt"],
        }
        conflict = client.post(
            f"/api/generation-jobs/{root_id}/execute",
            json={"idempotencyKey": "generation-execute-003"},
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "GENERATION_JOB_STATE_CONFLICT"

    with Session(clean_api_database) as session, session.begin():
        live_slack = session.get(SlackWorkItem, work_item_id)
        assert live_slack is not None
        live_slack.parse_result = {
            "developmentItem": "Mutated live value",
            "missingFields": [],
        }
        live_slack.permalink = "https://synthetic.invalid/mutated/live"
        root_node = session.scalar(
            select(KnowledgeNode).where(
                KnowledgeNode.entity_type == "SLACK_WORK_ITEM",
                KnowledgeNode.entity_id == work_item_id,
            )
        )
        assert root_node is not None
        root_node.properties = {
            "sourceVersion": 1,
            "content": "Mutated live knowledge that must not leak.",
        }
        mutable_category = session.get(Folder, mutable_category_id)
        assert mutable_category is not None
        session.delete(mutable_category)

    before_stable_read = _reconstruction_row_counts(clean_api_database)
    with TestClient(
        create_app(validate_database=False, database_engine=clean_api_database)
    ) as client:
        stable_reconstruction = client.get(
            f"/api/generation-jobs/{root_id}/reconstruction"
        )
        assert stable_reconstruction.status_code == 200
        assert stable_reconstruction.json()["input"] == initial_input
    assert _reconstruction_row_counts(clean_api_database) == before_stable_read

    with TestClient(
        create_app(
            validate_database=False,
            database_engine=clean_api_database,
        )
    ) as client:
        queued_retry = client.post(
            f"/api/generation-jobs/{root_id}/retry",
            json={
                "idempotencyKey": "generation-retry-001",
                "contextMode": "REUSE_SEALED",
                "reason": "retry after timeout",
            },
        )
        assert queued_retry.status_code == 201
        queued_retry_body = queued_retry.json()
        retry_id = queued_retry_body["job"]["id"]
        assert queued_retry_body["job"]["status"] == "QUEUED"
        assert queued_retry_body["job"]["lineage"] == {
            "rootJobId": root_id,
            "retryOfJobId": root_id,
            "attemptNumber": 2,
        }
        assert queued_retry_body["job"]["context"]["generationVersion"] == 2
        assert [
            {key: value for key, value in item.items() if key != "id"}
            for item in queued_retry_body["contextItems"]
        ] == [
            {key: value for key, value in item.items() if key != "id"}
            for item in root["contextItems"]
        ]
        retry_reconstruction = client.get(
            f"/api/generation-jobs/{retry_id}/reconstruction"
        )
        assert retry_reconstruction.status_code == 200
        retry_input = retry_reconstruction.json()["input"]
        for field in (
            "slackWorkItem",
            "activeKnowledge",
            "existingCases",
            "failedOrBlockedExecutions",
            "openIssues",
            "extraSources",
            "categoryCandidates",
            "generationPolicy",
        ):
            assert retry_input[field] == initial_input[field]
        assert retry_input["requestId"] != initial_input["requestId"]
        assert retry_input["createdAt"] != initial_input["createdAt"]
        setup_retry_execute = client.post(
            f"/api/generation-jobs/{retry_id}/execute",
            json={"idempotencyKey": "generation-retry-execute-001"},
        )
        assert setup_retry_execute.status_code == 503
        assert setup_retry_execute.json()["code"] == "GENERATION_SETUP_REQUIRED"
        assert (
            client.get(f"/api/generation-jobs/{retry_id}").json()["job"]["status"]
            == "QUEUED"
        )

    successful = SuccessfulRunner()
    with TestClient(
        create_app(
            validate_database=False,
            database_engine=clean_api_database,
            generation_runner=successful,
        )
    ) as client:
        retry = client.post(
            f"/api/generation-jobs/{retry_id}/execute",
            json={"idempotencyKey": "generation-retry-execute-002"},
        )
        assert retry.status_code == 200
        retry_body = retry.json()
        assert successful.calls == 1
        assert len(successful.inputs) == 1
        assert retry_body["job"]["status"] == "SUCCEEDED"
        assert retry_body["job"]["lineage"] == {
            "rootJobId": root_id,
            "retryOfJobId": root_id,
            "attemptNumber": 2,
        }
        assert retry_body["job"]["context"]["generationVersion"] == 2
        assert retry_body["job"]["result"] == successful.payload
        reconstructed = client.get(f"/api/generation-jobs/{retry_id}/reconstruction")
        assert reconstructed.status_code == 200
        reconstruction_body = reconstructed.json()
        assert reconstruction_body["availability"] == "AVAILABLE"
        assert reconstruction_body["reason"] is None
        assert reconstruction_body["input"] == successful.inputs[0]
        assert (
            GenerationInputV1.model_validate(reconstruction_body["input"]).model_dump(
                mode="json", by_alias=True, exclude_none=False
            )
            == successful.inputs[0]
        )
        provider_boundary_digest = canonical_input_digest(successful.inputs[0])
        assert reconstruction_body["provenance"]["inputDigest"] == {
            "algorithm": "SHA-256",
            "canonicalization": "RFC8785",
            "value": provider_boundary_digest.value,
            "canonicalByteLength": provider_boundary_digest.canonical_byte_length,
        }
        repeated = client.get(f"/api/generation-jobs/{retry_id}/reconstruction")
        assert repeated.status_code == 200
        assert repeated.json() == reconstruction_body
        assert reconstruction_body["input"]["activeKnowledge"] == []
        assert reconstruction_body["input"]["existingCases"] == []
        assert reconstruction_body["input"]["slackWorkItem"]["developmentItem"] is None
        assert reconstruction_body["input"]["slackWorkItem"]["missingFields"] == [
            "developmentItem",
            "requestedMenu",
            "featureDescription",
            "requestDetails",
        ]
        assert reconstruction_body["input"]["categoryCandidates"]
        assert (
            reconstruction_body["input"]["generationPolicy"]["batch"]["isFinalBatch"]
            is True
        )
        assert successful.calls == 1
        page = client.get(
            "/api/generation-jobs",
            params={"slackWorkItemId": work_item_api_id, "limit": 1},
        )
        assert page.status_code == 200
        assert [item["id"] for item in page.json()["items"]] == [retry_id]
        assert page.json()["nextCursor"] is not None
        next_page = client.get(
            "/api/generation-jobs",
            params={
                "slackWorkItemId": work_item_api_id,
                "limit": 1,
                "cursor": page.json()["nextCursor"],
            },
        )
        assert [item["id"] for item in next_page.json()["items"]] == [root_id]
        missing = client.get(
            f"/api/generation-jobs/generation_job_{uuid.uuid4().hex}/reconstruction"
        )
        assert missing.status_code == 404
        assert missing.json()["code"] == "RESOURCE_NOT_FOUND"

    with Session(clean_api_database) as session:
        assert session.scalar(select(func.count()).select_from(GenerationJob)) == 2
        assert session.scalar(select(func.count()).select_from(LedgerTestCase)) == 0
        assert (
            session.scalar(select(func.count()).select_from(LedgerTestCaseVersion)) == 0
        )


def test_reconstruction_reports_stable_unavailable_states_without_side_effects(
    clean_api_database: Engine,
) -> None:
    work_item_id = _seed_grounded_slack_work_item(clean_api_database)
    sessions = sessionmaker(clean_api_database, expire_on_commit=False)
    with sessions.begin() as session:
        persisted = GenerationRepository(session).create_next_job_with_context(
            slack_work_item_id=work_item_id
        )
        job_id = persisted.job.id
        assert persisted.job.generation_input is not None
        original_input = deepcopy(persisted.job.generation_input)

    tripwire = ReconstructionTripwireRunner()
    api_job_id = f"generation_job_{job_id.hex}"
    with TestClient(
        create_app(
            validate_database=False,
            database_engine=clean_api_database,
            generation_runner=tripwire,
        )
    ) as client:
        before = _reconstruction_row_counts(clean_api_database)
        statements: list[str] = []

        def capture_statement(
            connection: object,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            executemany: bool,
        ) -> None:
            del connection, cursor, parameters, context, executemany
            statements.append(statement.lower())

        event.listen(clean_api_database, "before_cursor_execute", capture_statement)
        try:
            available = client.get(f"/api/generation-jobs/{api_job_id}/reconstruction")
        finally:
            event.remove(clean_api_database, "before_cursor_execute", capture_statement)
        assert available.status_code == 200
        assert available.json()["availability"] == "AVAILABLE"
        assert statements
        assert all(
            keyword not in statement
            for statement in statements
            for keyword in (" insert ", " update ", " delete ", " for update")
        )
        forbidden_sources = (
            "generation_applies",
            "generation_apply_outcomes",
            "slack_work_items",
            "slack_threads",
            "slack_messages",
            "slack_message_revisions",
            "slack_parse_results",
            "knowledge_nodes",
            "knowledge_edges",
            "knowledge_evidence",
            "knowledge_source_records",
            "test_cases",
            "test_case_versions",
            "test_steps",
            "test_case_version_tags",
            "tags",
            "work_items",
            "execution_attempts",
            "issues",
            "issue_links",
            "folders",
            "workspace_settings",
        )
        assert all(
            f"from {table}" not in statement and f"join {table}" not in statement
            for statement in statements
            for table in forbidden_sources
        )
        assert _reconstruction_row_counts(clean_api_database) == before
        assert tripwire.calls == 0

        unsupported_input = deepcopy(original_input)
        unsupported_input["inputSchemaVersion"] = "2.0.0"
        unsupported_digest = canonical_input_digest(unsupported_input)
        _replace_capture(
            clean_api_database,
            job_id=job_id,
            capture_state="SEALED",
            input_schema_version="2.0.0",
            generation_input=unsupported_input,
            generation_input_digest=unsupported_digest.value,
        )
        before = _reconstruction_row_counts(clean_api_database)
        unsupported = client.get(f"/api/generation-jobs/{api_job_id}/reconstruction")
        assert unsupported.status_code == 200
        assert unsupported.json()["reason"] == "UNSUPPORTED_INPUT_SCHEMA_VERSION"
        assert unsupported.json()["input"] is None
        assert unsupported.json()["provenance"]["inputDigest"]["value"] == (
            unsupported_digest.value
        )
        assert _reconstruction_row_counts(clean_api_database) == before
        assert tripwire.calls == 0
        unsupported_execute = client.post(
            f"/api/generation-jobs/{api_job_id}/execute",
            json={"idempotencyKey": "unsupported-generation-execute-001"},
        )
        assert unsupported_execute.status_code == 409
        assert unsupported_execute.json()["code"] == "GENERATION_INPUT_UNAVAILABLE"
        assert unsupported_execute.json()["details"]["reason"] == (
            "UNSUPPORTED_INPUT_SCHEMA_VERSION"
        )
        assert _reconstruction_row_counts(clean_api_database) == before
        assert tripwire.calls == 0

        _replace_capture(
            clean_api_database,
            job_id=job_id,
            capture_state="SEALED",
            input_schema_version="1.0.0",
            generation_input=original_input,
            generation_input_digest="0" * 64,
        )
        before = _reconstruction_row_counts(clean_api_database)
        incomplete = client.get(f"/api/generation-jobs/{api_job_id}/reconstruction")
        assert incomplete.status_code == 200
        assert incomplete.json()["reason"] == "SEALED_INPUT_INCOMPLETE"
        assert incomplete.json()["input"] is None
        assert incomplete.json()["provenance"]["inputDigest"] is None
        assert _reconstruction_row_counts(clean_api_database) == before
        assert tripwire.calls == 0
        incomplete_execute = client.post(
            f"/api/generation-jobs/{api_job_id}/execute",
            json={"idempotencyKey": "incomplete-generation-execute-001"},
        )
        assert incomplete_execute.status_code == 409
        assert incomplete_execute.json()["code"] == "GENERATION_INPUT_UNAVAILABLE"
        assert incomplete_execute.json()["details"]["reason"] == (
            "SEALED_INPUT_INCOMPLETE"
        )
        assert _reconstruction_row_counts(clean_api_database) == before
        assert tripwire.calls == 0

        _replace_capture(
            clean_api_database,
            job_id=job_id,
            capture_state="LEGACY_NOT_CAPTURED",
            input_schema_version=None,
            generation_input=None,
            generation_input_digest=None,
        )
        before = _reconstruction_row_counts(clean_api_database)
        legacy = client.get(f"/api/generation-jobs/{api_job_id}/reconstruction")
        assert legacy.status_code == 200
        assert legacy.json()["reason"] == "LEGACY_INPUT_NOT_SEALED"
        assert legacy.json()["recordedInputSchemaVersion"] is None
        assert legacy.json()["input"] is None
        assert legacy.json()["provenance"]["inputDigest"] is None
        assert _reconstruction_row_counts(clean_api_database) == before
        assert tripwire.calls == 0

        unavailable_execute = client.post(
            f"/api/generation-jobs/{api_job_id}/execute",
            json={"idempotencyKey": "legacy-generation-execute-001"},
        )
        assert unavailable_execute.status_code == 409
        assert unavailable_execute.json()["code"] == "GENERATION_INPUT_UNAVAILABLE"
        assert unavailable_execute.json()["details"] == {
            "jobId": api_job_id,
            "reason": "LEGACY_INPUT_NOT_SEALED",
            "externalCallAttempted": False,
        }
        assert tripwire.calls == 0


def _seed_grounded_slack_work_item(engine: Engine) -> uuid.UUID:
    sessions = sessionmaker(engine, expire_on_commit=False)
    event = SlackEventInput(
        event_id="Ev-generation-api-root",
        slack_workspace_id="T_GENERATION_API",
        channel_id="C_GENERATION_API",
        event_type="ROOT",
        event_ts="1787700100.000100",
        message_ts="1787700100.000000",
        thread_ts=None,
        revision_ts=None,
        body="비밀번호 재설정 흐름을 검증한다.",
        author_id="U_GENERATION_API",
        permalink="https://synthetic.invalid/generation/api/root",
        channel_is_private=False,
        raw_payload={"event_id": "Ev-generation-api-root", "type": "message"},
    )
    with sessions.begin() as session:
        slack = SlackRepository(session)
        slack.put_channel(
            slack_workspace_id="T_GENERATION_API",
            channel_id="C_GENERATION_API",
            channel_name="generation-api",
        )
        ingested = slack.ingest_event(event)
        assert ingested.work_item_id is not None
        work_item_id = ingested.work_item_id
        revision_id = ingested.revision_id

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
        KnowledgeRepository(session).create_node(
            node_type="WORK_ITEM",
            label="Slack generation API work item",
            entity_type="SLACK_WORK_ITEM",
            entity_id=work_item_id,
            properties={"sourceVersion": 1, "content": revision.body},
            recorded_at=datetime.now(UTC),
            evidence=evidence,
        )
    return work_item_id


def _replace_capture(
    engine: Engine,
    *,
    job_id: uuid.UUID,
    capture_state: str,
    input_schema_version: str | None,
    generation_input: dict[str, object] | None,
    generation_input_digest: str | None,
) -> None:
    """Test-only corruption/legacy seed that bypasses the immutable-row trigger."""

    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE generation_jobs DISABLE TRIGGER "
                "trg_generation_jobs_guard_context_seal"
            )
        )
        connection.execute(
            update(GenerationJob)
            .where(GenerationJob.id == job_id)
            .values(
                generation_input_capture_state=capture_state,
                generation_input_schema_version=input_schema_version,
                generation_input=generation_input,
                generation_input_digest=generation_input_digest,
            )
        )
        # T07 adds a deferred lineage trigger on generation_jobs. Drain valid
        # deferred events before altering the separate test-bypassed seal trigger.
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        connection.execute(
            text(
                "ALTER TABLE generation_jobs ENABLE TRIGGER "
                "trg_generation_jobs_guard_context_seal"
            )
        )


def _reconstruction_row_counts(engine: Engine) -> dict[str, int]:
    protected_tables = (
        "generation_jobs",
        "generation_contexts",
        "generation_applies",
        "generation_apply_outcomes",
        "work_items",
        "test_cases",
        "test_case_versions",
        "test_steps",
        "tags",
        "test_case_version_tags",
        "audit_logs",
        "outbox_events",
        "slack_work_items",
        "slack_threads",
        "slack_messages",
        "slack_message_revisions",
        "slack_parse_results",
        "knowledge_nodes",
        "knowledge_edges",
        "knowledge_evidence",
        "knowledge_source_records",
        "execution_attempts",
        "issues",
        "issue_links",
        "folders",
        "workspace_settings",
    )
    with Session(engine) as session:
        return {
            table: int(session.scalar(text(f"SELECT count(*) FROM {table}")) or 0)
            for table in protected_tables
        }
