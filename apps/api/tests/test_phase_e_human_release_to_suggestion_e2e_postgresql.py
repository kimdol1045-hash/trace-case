from __future__ import annotations

import json
import time
import uuid
from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from tc_api.main import create_app
from tc_api.slack_runtime import SlackApiRuntime
from tc_backend.config import Settings, SlackMode
from tc_backend.db.jobs import JobRepository
from tc_backend.db.models import (
    BackgroundJob,
    GenerationApply,
    GenerationApplyOutcome,
    GenerationChangeAssessment,
    GenerationExecutionSchedule,
    GenerationJob,
    GenerationMaterializationSchedule,
    GenerationProviderCallObservation,
    GenerationProviderCallPermit,
    GenerationSlackNotificationIntent,
    GenerationSlackNotificationReceipt,
    GenerationSuggestion,
    GenerationSuggestionMaterialization,
    GenerationSuggestionResolution,
    GenerationSuggestionTarget,
    KnowledgeProjectionEvent,
    SlackEvent,
    SlackMessageRevision,
    SlackWorkItem,
    TestCase as LedgerTestCase,
    TestCaseGenerationManagementEvent as GenerationManagementEvent,
    TestCaseVersion as LedgerTestCaseVersion,
    TestCaseVersionTag as LedgerTestCaseVersionTag,
    TestStep as LedgerTestStep,
)
from tc_backend.generation.lifecycle import GenerationRunResult
from tc_backend.infrastructure.slack.config import SlackRuntimeState
from tc_backend.infrastructure.slack.receivers import SlackHttpEventsReceiver
from tc_backend.infrastructure.slack.signature import SlackRequestVerifier
from tc_backend.infrastructure.slack.testing import DeterministicFakeSlackTransport
from tc_worker.main import Worker, build_worker
from test_phase_e_generation_management_postgresql import _update_payload
from test_phase_e_root_to_slack_e2e_postgresql import (
    SIGNATURE_CLOCK,
    DeterministicEmbedding,
    DeterministicExtraction,
    DeterministicGenerationRunner,
    _drain_worker,
    _post_signed,
)

pytestmark = pytest.mark.postgres

ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = (
    ROOT
    / "evals/fixtures/automatic-generation-root-to-slack-e2e/"
    "signed-human-release-v1.json"
)
SUGGESTION_SCHEMA_VERSION = "generation-suggestion/1.0.0"


class DeterministicHumanReleaseRunner(DeterministicGenerationRunner):
    """T10's ordinary result plus one sealed T11 suggestion result."""

    def run(
        self,
        payload: Mapping[str, object],
        *,
        provider_attempt_number: int,
    ) -> GenerationRunResult:
        if payload.get("inputSchemaVersion") != SUGGESTION_SCHEMA_VERSION:
            return super().run(
                payload,
                provider_attempt_number=provider_attempt_number,
            )

        assert provider_attempt_number == 1
        frozen = deepcopy(dict(payload))
        self.calls.append(frozen)
        assert frozen["requestedOutputSchemaVersion"] == SUGGESTION_SCHEMA_VERSION
        targets = frozen["suggestionTargets"]
        base = frozen["baseGenerationInput"]
        assert isinstance(targets, list) and len(targets) == 1
        assert isinstance(base, dict)
        active_knowledge = base["activeKnowledge"]
        assert isinstance(active_knowledge, list) and active_knowledge
        evidence_node_id = active_knowledge[0]["nodeId"]
        target = targets[0]
        assert isinstance(evidence_node_id, str) and isinstance(target, dict)
        target_ref = target["suggestionTargetRef"]
        assert isinstance(target_ref, str)
        return GenerationRunResult(
            {
                "outputSchemaVersion": SUGGESTION_SCHEMA_VERSION,
                "suggestions": [
                    {
                        "suggestionTargetRef": target_ref,
                        "candidate": {
                            "summary": "결제 승인 실패 5회 잠금과 감사 사유 기록",
                            "steps": [
                                {
                                    "sequence": 1,
                                    "precondition": "승인 실패가 네 번 기록되어 있다.",
                                    "action": "다섯 번째 결제 승인 실패를 발생시킨다.",
                                }
                            ],
                            "caseExpectedResult": (
                                "계정이 잠기고 다섯 번째 실패 감사 사유가 기록된다."
                            ),
                            "priority": "CRITICAL",
                            "labels": ["결제", "자동생성", "잠금정책"],
                            "evidenceNodeIds": [evidence_node_id],
                            "generationReason": (
                                "변경된 Slack 요청의 5회 잠금과 감사 요구를 반영한다."
                            ),
                            "inferenceOnly": False,
                            "openQuestions": [],
                        },
                    }
                ],
            }
        )


def test_human_released_case_reaches_only_one_open_suggestion_and_thread_reply(
    clean_api_database: Engine,
) -> None:
    fixture = json.loads(FIXTURE_PATH.read_text())
    secret = fixture["signingSecret"]
    workspace_id = fixture["workspaceId"]
    channel_id = fixture["channelId"]
    root_ts = fixture["rootTs"]
    assert all(
        isinstance(value, str)
        for value in (secret, workspace_id, channel_id, root_ts)
    )
    api_runtime = SlackApiRuntime(
        state=SlackRuntimeState.CONFIGURED,
        mode=SlackMode.HTTP,
        workspace_id=workspace_id,
        app_id="A_PE_SE2_T11",
        http_receiver=SlackHttpEventsReceiver(
            SlackRequestVerifier(secret, clock=lambda: SIGNATURE_CLOCK)
        ),
    )
    application = create_app(
        validate_database=False,
        database_engine=clean_api_database,
        slack_runtime=api_runtime,
    )
    extraction = DeterministicExtraction()
    embedding = DeterministicEmbedding()
    generation = DeterministicHumanReleaseRunner()
    slack = DeterministicFakeSlackTransport()
    slack.lose_ack_after_accept_once()
    settings = Settings(
        _env_file=None,
        database_url=clean_api_database.url.render_as_string(hide_password=False),
        encryption_master_key="pe-se2-t11-production-composition-key",
        slack_mode="http",
        slack_signing_secret=secret,
        slack_bot_token="xoxb-pe-se2-t11-test",
        slack_workspace_id=workspace_id,
        public_slack_base_url="https://slack-ingress.example.test",
        public_web_base_url="https://tc.example.test",
        slack_generation_notifications_enabled=True,
        slack_notification_retry_base_seconds=1,
        slack_notification_retry_max_seconds=1,
        automatic_knowledge_projection_enabled=True,
        automatic_generation_enabled=True,
        automatic_generation_provider_id="pe-se2-t11-provider",
        automatic_generation_provider_endpoint="https://provider.example.test/generate",
        automatic_generation_provider_model_id="pe-se2-t11-model",
        automatic_generation_provider_auth_token="pe-se2-t11-provider-secret",
        job_retry_base_seconds=0,
        job_retry_max_seconds=0,
    )
    worker, worker_engine = build_worker(
        settings,
        knowledge_extraction_provider=extraction,
        knowledge_embedding_provider=embedding,
        generation_execution_runner=generation,
        slack_transport=slack,
    )
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)

    try:
        with TestClient(application) as client:
            allowlist = client.put(
                "/api/integrations/slack/allowlist",
                json={
                    "idempotencyKey": "pe-se2-t11-allowlist-001",
                    "workspaceId": workspace_id,
                    "expectedVersion": 1,
                    "channels": [{"channelId": channel_id, "visibility": "PUBLIC"}],
                },
            )
            assert allowlist.status_code == 200, allowlist.text

            initial = [
                _post_signed(client, event, secret=secret).json()
                for event in fixture["initialEvents"]
            ]
            assert [row["outcome"] for row in initial] == ["ACCEPTED", "ACCEPTED"]
            assert worker.run_once()
            assert worker.run_once()
            work_item_id = _force_quiet_window(sessions, expected_source_version=2)
            _drain_worker(worker, sessions)

            with sessions() as session:
                initial_job = session.scalar(
                    select(GenerationJob).order_by(GenerationJob.generation_version)
                )
                initial_outcome = session.scalar(
                    select(GenerationApplyOutcome).order_by(
                        GenerationApplyOutcome.generated_case_index
                    )
                )
                assert initial_job is not None and initial_outcome is not None
                case_id = initial_outcome.test_case_id
                assert initial_job.status == "SUCCEEDED"
                assert initial_job.generation_input_schema_version == "1.0.0"
                assert session.scalar(select(func.count()).select_from(LedgerTestCase)) == 5
                assert session.scalar(select(func.count()).select_from(GenerationApply)) == 1
                generated_case = session.get(LedgerTestCase, case_id)
                assert generated_case is not None
                generated_version = session.get(
                    LedgerTestCaseVersion,
                    generated_case.current_version_id,
                )
                assert generated_case.status == "ACTIVE"
                assert generated_case.generation_managed is True
                assert generated_version is not None
                assert generated_version.source_type == "SLACK_AI"

            encoded_case_id = f"case_{case_id.hex}"
            before_response = client.get(f"/api/test-cases/{encoded_case_id}")
            assert before_response.status_code == 200, before_response.text
            before = before_response.json()
            assert before["status"] == "ACTIVE"
            assert before["generationManagement"]["managed"] is True

            released_response = client.patch(
                f"/api/test-cases/{encoded_case_id}",
                headers={
                    "Idempotency-Key": "pe-se2-t11-human-release-001",
                    "X-Actor-Label": "QA Kim",
                    "X-External-Actor-Id": "qa-42",
                },
                json=_update_payload(
                    before,
                    change_reason=(
                        "QA owns the semantic priority of this generated regression case."
                    ),
                    priority="CRITICAL",
                ),
            )
            assert released_response.status_code == 200, released_response.text
            released = released_response.json()
            management = deepcopy(released["generationManagement"])
            assert released["version"] == before["version"] + 1
            assert management["managed"] is False
            assert management["lineageId"] == before["generationManagement"]["lineageId"]
            assert management["releasedAt"] is not None
            assert management["releaseMutation"] == "CONTENT_VERSION"
            protected_detail = deepcopy(released)
            protected_ledger = _case_ledger_snapshot(sessions, case_id)
            assert protected_ledger["managed"] is False
            assert protected_ledger["managementEvents"] == 2

            # The production Slack notifier enforces a one-second per-channel
            # post floor. Advance that real boundary before the second terminal
            # outcome so the deterministic fake observes both intended posts.
            time.sleep(1.05)
            changed = _post_signed(
                client,
                fixture["semanticChangeEvent"],
                secret=secret,
            )
            assert changed.status_code == 200, changed.text
            assert changed.json()["outcome"] == "ACCEPTED"
            assert worker.run_once()
            assert _force_quiet_window(
                sessions,
                expected_source_version=3,
            ) == work_item_id
            _drain_worker(worker, sessions)

            after_response = client.get(f"/api/test-cases/{encoded_case_id}")
            assert after_response.status_code == 200, after_response.text
            assert after_response.json() == protected_detail
            assert _case_ledger_snapshot(sessions, case_id) == protected_ledger

            suggestion_page = client.get(
                f"/api/test-cases/{encoded_case_id}/generation-suggestions"
            )
            assert suggestion_page.status_code == 200, suggestion_page.text
            suggestions = suggestion_page.json()["items"]
            assert len(suggestions) == 1
            suggestion = suggestions[0]
            assert suggestion["state"] == "OPEN"
            assert suggestion["testCaseId"] == encoded_case_id
            assert suggestion["generationLineageId"] == management["lineageId"]
            assert suggestion["baseline"]["caseVersion"] == released["version"]
            assert suggestion["baseline"]["caseResourceVersion"] == released[
                "resourceVersion"
            ]
            assert "summary" in suggestion["diff"]["changedFields"]
            suggestion_detail = client.get(
                f"/api/generation-suggestions/{suggestion['id']}"
            )
            assert suggestion_detail.status_code == 200, suggestion_detail.text
            assert suggestion_detail.json() == suggestion

            with sessions() as session:
                jobs = tuple(
                    session.scalars(
                        select(GenerationJob).order_by(GenerationJob.generation_version)
                    )
                )
                assessments = tuple(
                    session.scalars(
                        select(GenerationChangeAssessment).order_by(
                            GenerationChangeAssessment.current_source_version
                        )
                    )
                )
                assert len(jobs) == len(assessments) == 2
                suggestion_job = jobs[-1]
                assert suggestion_job.status == "SUCCEEDED"
                assert suggestion_job.source_version == 3
                assert suggestion_job.generation_version == 2
                assert suggestion_job.schema_version == SUGGESTION_SCHEMA_VERSION
                assert (
                    suggestion_job.generation_input_schema_version
                    == SUGGESTION_SCHEMA_VERSION
                )
                assert assessments[-1].outcome == "REGENERATE"
                assert assessments[-1].selected_generation_job_id == suggestion_job.id
                suggestion_job_id = suggestion_job.id
                targets = tuple(session.scalars(select(GenerationSuggestionTarget)))
                assert len(targets) == 1
                assert targets[0].test_case_id == case_id
                assert targets[0].baseline_case_version == released["version"]
                materialization = session.scalar(
                    select(GenerationSuggestionMaterialization)
                )
                assert materialization is not None
                assert materialization.candidate_count == 1
                assert materialization.suggestion_count == 1
                assert materialization.no_change_count == 0
                assert materialization.receipt["automaticOverwriteCount"] == 0
                suggestion_intent = session.scalar(
                    select(GenerationSlackNotificationIntent).where(
                        GenerationSlackNotificationIntent.generation_job_id
                        == suggestion_job_id
                    )
                )
                assert suggestion_intent is not None
                assert suggestion_intent.notification_kind == "SUCCESS_SUGGESTIONS"
                assert suggestion_intent.message_snapshot["suggestionCount"] == 1
                assert suggestion_intent.message_snapshot["automaticOverwriteCount"] == 0
                suggestion_receipt = session.scalar(
                    select(GenerationSlackNotificationReceipt).where(
                        GenerationSlackNotificationReceipt.intent_id
                        == suggestion_intent.id
                    )
                )
                assert suggestion_receipt is not None
                assert suggestion_receipt.terminal_outcome == "DELIVERED"
                assert suggestion_receipt.root_message_ts == root_ts
                notification_intent_id = suggestion_intent.id

            ordinary_plan = client.get(
                f"/api/generation-jobs/generation_job_{suggestion_job_id.hex}/apply-plan"
            )
            assert ordinary_plan.status_code == 409, ordinary_plan.text
            assert ordinary_plan.json()["code"] == "GENERATION_SUGGESTION_CONFLICT"
            assert ordinary_plan.json()["details"]["reason"] == (
                "SUGGESTION_JOB_NOT_WHOLE_BATCH_APPLICABLE"
            )

            bot_replies = [
                message
                for message in slack.conversations_replies(
                    channel_id=channel_id,
                    thread_ts=root_ts,
                )
                if message.bot_id is not None
            ]
            assert len(bot_replies) == 2
            assert bot_replies[-1].thread_ts == root_ts
            assert "변경 제안: 1건" in (bot_replies[-1].text or "")
            assert "자동 덮어쓰기: 0건" in (bot_replies[-1].text or "")

            before_replay = _immutable_cardinality_snapshot(sessions)
            assert _post_signed(
                client,
                fixture["semanticChangeEvent"],
                secret=secret,
            ).json()["outcome"] == "DUPLICATE_EVENT_ID"
            _redeliver_completed_job(
                worker,
                sessions,
                job_type="SLACK_AUTOMATIC_GENERATION",
                payload_match={"sourceVersion": 3},
            )
            _assert_enqueue_replay(
                sessions,
                job_type="AUTOMATIC_GENERATION_EXECUTE",
                payload_match={"generationJobId": str(suggestion_job_id)},
            )
            _redeliver_completed_job(
                worker,
                sessions,
                job_type="AUTOMATIC_GENERATION_MATERIALIZE",
                payload_match={"generationJobId": str(suggestion_job_id)},
            )
            _redeliver_completed_job(
                worker,
                sessions,
                job_type="SLACK_GENERATION_NOTIFICATION_DELIVERY",
                payload_match={"notificationIntentId": str(notification_intent_id)},
            )
            assert _immutable_cardinality_snapshot(sessions) == before_replay
            assert client.get(f"/api/test-cases/{encoded_case_id}").json() == (
                protected_detail
            )

        assert extraction.calls == 2
        assert embedding.calls == 2
        assert len(generation.calls) == 2
        assert generation.calls[0]["inputSchemaVersion"] == "1.0.0"
        assert generation.calls[1]["inputSchemaVersion"] == SUGGESTION_SCHEMA_VERSION
        call_names = [call[0] for call in slack.calls]
        assert call_names.count("chat.postMessage") == 2
        assert call_names.count("find.notification") == 1
        final = _immutable_cardinality_snapshot(sessions)
        assert final == {
            "slackEvents": 3,
            "sourceRevisions": 3,
            "projectionReceipts": 2,
            "changeAssessments": 2,
            "generationJobs": 2,
            "executionSchedules": 2,
            "providerPermits": 2,
            "providerObservations": 2,
            "materializationSchedules": 2,
            "generationApplies": 1,
            "applyOutcomes": 5,
            "cases": 5,
            "caseVersions": 6,
            "suggestionTargets": 1,
            "suggestions": 1,
            "suggestionMaterializations": 1,
            "suggestionResolutions": 0,
            "managementEvents": 6,
            "notificationIntents": 2,
            "notificationReceipts": 2,
        }
    finally:
        worker_engine.dispose()


def _force_quiet_window(
    sessions: sessionmaker[Session],
    *,
    expected_source_version: int,
) -> uuid.UUID:
    with sessions.begin() as session:
        item = session.scalar(select(SlackWorkItem))
        assert item is not None
        assert item.source_version == expected_source_version
        assert item.quiet_job_id is not None
        due = datetime.now(UTC) - timedelta(seconds=1)
        item.quiet_due_at = due
        session.execute(
            update(BackgroundJob)
            .where(BackgroundJob.id == item.quiet_job_id)
            .values(run_after=due)
        )
        return item.id


def _redeliver_completed_job(
    worker: Worker,
    sessions: sessionmaker[Session],
    *,
    job_type: str,
    payload_match: Mapping[str, object],
) -> None:
    with sessions.begin() as session:
        candidates = tuple(
            session.scalars(
                select(BackgroundJob)
                .where(BackgroundJob.job_type == job_type)
                .order_by(BackgroundJob.created_at, BackgroundJob.id)
            )
        )
        job = next(
            row
            for row in candidates
            if all(row.payload.get(key) == value for key, value in payload_match.items())
        )
        assert job.status == "SUCCEEDED"
        job.status = "RETRY"
        job.run_after = datetime.now(UTC) - timedelta(seconds=1)
        job.completed_at = None
        job.locked_by = None
        job.locked_at = None
        job.heartbeat_at = None
        job_id = job.id
    assert worker.run_once()
    with sessions() as session:
        replayed = session.get(BackgroundJob, job_id)
        assert replayed is not None
        assert replayed.status == "SUCCEEDED"


def _assert_enqueue_replay(
    sessions: sessionmaker[Session],
    *,
    job_type: str,
    payload_match: Mapping[str, object],
) -> None:
    with sessions.begin() as session:
        candidates = tuple(
            session.scalars(
                select(BackgroundJob)
                .where(BackgroundJob.job_type == job_type)
                .order_by(BackgroundJob.created_at, BackgroundJob.id)
            )
        )
        job = next(
            row
            for row in candidates
            if all(row.payload.get(key) == value for key, value in payload_match.items())
        )
        assert job.status == "SUCCEEDED"
        assert job.idempotency_key is not None
        replayed_id = JobRepository(session).enqueue(
            job_type=job.job_type,
            payload=job.payload,
            max_attempts=job.max_attempts,
            priority=job.priority,
            idempotency_key=job.idempotency_key,
        )
        assert replayed_id == job.id
        assert job.status == "SUCCEEDED"


def _case_ledger_snapshot(
    sessions: sessionmaker[Session],
    case_id: uuid.UUID,
) -> dict[str, object]:
    with sessions() as session:
        case = session.get(LedgerTestCase, case_id)
        assert case is not None
        version_ids = tuple(
            session.scalars(
                select(LedgerTestCaseVersion.id)
                .where(LedgerTestCaseVersion.test_case_id == case_id)
                .order_by(LedgerTestCaseVersion.version_number)
            )
        )
        return {
            "resourceVersion": case.version,
            "currentVersionId": case.current_version_id,
            "workItemId": case.work_item_id,
            "categoryId": case.category_id,
            "sortOrder": case.sort_order,
            "status": case.status,
            "deletedAt": case.deleted_at,
            "managed": case.generation_managed,
            "lineageId": case.generation_lineage_id,
            "createdOutcomeId": case.generation_created_outcome_id,
            "baselineVersionId": case.generation_baseline_version_id,
            "latestGeneratedVersionId": case.generation_latest_version_id,
            "releasedAt": case.generation_released_at,
            "releaseReason": case.generation_release_reason,
            "releaseActor": deepcopy(case.generation_release_actor),
            "releaseMutation": case.generation_release_mutation,
            "releasedFromVersionId": case.generation_released_from_version_id,
            "releasedByVersionId": case.generation_released_by_version_id,
            "versionIds": version_ids,
            "steps": int(
                session.scalar(
                    select(func.count())
                    .select_from(LedgerTestStep)
                    .where(LedgerTestStep.test_case_version_id.in_(version_ids))
                )
                or 0
            ),
            "versionLabels": int(
                session.scalar(
                    select(func.count())
                    .select_from(LedgerTestCaseVersionTag)
                    .where(LedgerTestCaseVersionTag.test_case_version_id.in_(version_ids))
                )
                or 0
            ),
            "managementEvents": int(
                session.scalar(
                    select(func.count())
                    .select_from(GenerationManagementEvent)
                    .where(GenerationManagementEvent.test_case_id == case_id)
                )
                or 0
            ),
        }


def _immutable_cardinality_snapshot(
    sessions: sessionmaker[Session],
) -> dict[str, int]:
    models = {
        "slackEvents": SlackEvent,
        "sourceRevisions": SlackMessageRevision,
        "projectionReceipts": KnowledgeProjectionEvent,
        "changeAssessments": GenerationChangeAssessment,
        "generationJobs": GenerationJob,
        "executionSchedules": GenerationExecutionSchedule,
        "providerPermits": GenerationProviderCallPermit,
        "providerObservations": GenerationProviderCallObservation,
        "materializationSchedules": GenerationMaterializationSchedule,
        "generationApplies": GenerationApply,
        "applyOutcomes": GenerationApplyOutcome,
        "cases": LedgerTestCase,
        "caseVersions": LedgerTestCaseVersion,
        "suggestionTargets": GenerationSuggestionTarget,
        "suggestions": GenerationSuggestion,
        "suggestionMaterializations": GenerationSuggestionMaterialization,
        "suggestionResolutions": GenerationSuggestionResolution,
        "managementEvents": GenerationManagementEvent,
        "notificationIntents": GenerationSlackNotificationIntent,
        "notificationReceipts": GenerationSlackNotificationReceipt,
    }
    with sessions() as session:
        return {
            name: int(session.scalar(select(func.count()).select_from(model)) or 0)
            for name, model in models.items()
        }
