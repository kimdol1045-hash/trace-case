from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
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
from tc_backend.db.models import (
    BackgroundJob,
    GenerationApply,
    GenerationApplyOutcome,
    GenerationExecutionSchedule,
    GenerationJob,
    GenerationProviderCallObservation,
    GenerationProviderCallPermit,
    GenerationSlackNotificationIntent,
    GenerationSlackNotificationReceipt,
    KnowledgeEmbedding,
    KnowledgeEvidence,
    KnowledgeNode,
    KnowledgeProjectionEvent,
    SlackEvent,
    SlackMessageRevision,
    SlackWorkItem,
    TestCase as LedgerTestCase,
    TestCaseVersion as LedgerTestCaseVersion,
)
from tc_backend.generation.lifecycle import GenerationRunResult
from tc_backend.infrastructure.slack.config import SlackRuntimeState
from tc_backend.infrastructure.slack.receivers import SlackHttpEventsReceiver
from tc_backend.infrastructure.slack.signature import (
    SlackRequestVerifier,
    sign_slack_request,
)
from tc_backend.infrastructure.slack.testing import DeterministicFakeSlackTransport
from tc_backend.knowledge.embedding import EmbeddingBatch
from tc_backend.knowledge.models import (
    KnowledgeNodeType,
    ProviderEvidence,
    ProviderExtraction,
    ProviderKnowledgeCandidate,
    SlackSourceBundle,
)
from tc_worker.main import Worker, build_worker

pytestmark = pytest.mark.postgres

ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = (
    ROOT
    / "evals/fixtures/automatic-generation-root-to-slack-e2e/signed-thread-v1.json"
)
SIGNATURE_CLOCK = 2_000_000_000


class DeterministicExtraction:
    def __init__(self) -> None:
        self.calls = 0

    async def extract(self, bundle: SlackSourceBundle) -> ProviderExtraction:
        self.calls += 1
        excerpt = "결제 승인은 실패 잠금 정책을 따라야 한다."
        revision = next(
            item
            for item in bundle.revisions
            if item.body is not None and excerpt in item.body
        )
        return ProviderExtraction(
            provider="pe-se2-t10-deterministic",
            model="knowledge-e2e-v1",
            schema_version="knowledge-extraction/1.0.0",
            candidates=(
                ProviderKnowledgeCandidate(
                    candidate_id="payment-approval-lock",
                    node_type=KnowledgeNodeType.DECISION,
                    content=excerpt,
                    topic_key="payment-approval-lock",
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


class DeterministicEmbedding:
    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        self.calls += 1
        vectors: list[tuple[float, ...]] = []
        hashes: list[str] = []
        for index, text in enumerate(texts):
            vector = [0.0] * 1024
            vector[index % 1024] = 1.0
            vectors.append(tuple(vector))
            hashes.append(hashlib.sha256(text.encode()).hexdigest())
        return EmbeddingBatch(
            model="bge-m3:pe-se2-t10",
            dimension=1024,
            vectors=tuple(vectors),
            content_hashes=tuple(hashes),
        )


class DeterministicGenerationRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run(
        self,
        payload: Mapping[str, object],
        *,
        provider_attempt_number: int,
    ) -> GenerationRunResult:
        assert provider_attempt_number == 1
        frozen = deepcopy(dict(payload))
        self.calls.append(frozen)
        slack_item = frozen["slackWorkItem"]
        categories = frozen["categoryCandidates"]
        assert isinstance(slack_item, dict) and isinstance(categories, list)
        evidence_node_id = slack_item["evidenceNodeId"]
        category = categories[0]
        assert isinstance(evidence_node_id, str) and isinstance(category, dict)
        category_id = category["categoryId"]
        assert isinstance(category_id, str)
        cases = [
            {
                "summary": f"결제 승인 자동 회귀 경로 {name}",
                "steps": [
                    {
                        "sequence": 1,
                        "precondition": "승인 요청이 준비되어 있다.",
                        "action": f"결제 승인 {name} 경로를 실행한다.",
                    }
                ],
                "caseExpectedResult": f"결제 승인 {name} 경로 결과가 기록된다.",
                "priority": "HIGH",
                "labels": ["결제", "자동생성", name],
                "evidenceNodeIds": [evidence_node_id],
                "generationReason": "현재 Slack 일감 근거로 회귀 흐름을 검증한다.",
                "inferenceOnly": False,
                "openQuestions": [],
            }
            for name in ("정상", "거절", "재시도", "취소", "복구")
        ]
        return GenerationRunResult(
            {
                "outputSchemaVersion": "1.0.0",
                "cases": cases,
                "categoryCandidate": {
                    "categoryId": category_id,
                    "score": 1.0,
                    "alternatives": [],
                },
            }
        )


def test_signed_root_thread_reaches_one_case_batch_and_one_reconciled_slack_reply(
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
        app_id="A_PE_SE2_T10",
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
    generation = DeterministicGenerationRunner()
    slack = DeterministicFakeSlackTransport()
    slack.lose_ack_after_accept_once()
    settings = Settings(
        _env_file=None,
        database_url=clean_api_database.url.render_as_string(hide_password=False),
        encryption_master_key="pe-se2-t10-production-composition-key",
        slack_mode="http",
        slack_signing_secret=secret,
        slack_bot_token="xoxb-" + "pe-se2-t10-test",
        slack_workspace_id=workspace_id,
        public_slack_base_url="https://slack-ingress.example.test",
        public_web_base_url="https://tc.example.test",
        slack_generation_notifications_enabled=True,
        slack_notification_retry_base_seconds=1,
        slack_notification_retry_max_seconds=1,
        automatic_knowledge_projection_enabled=True,
        automatic_generation_enabled=True,
        automatic_generation_provider_id="pe-se2-t10-provider",
        automatic_generation_provider_endpoint="https://provider.example.test/generate",
        automatic_generation_provider_model_id="pe-se2-t10-model",
        automatic_generation_provider_auth_token="pe-se2-t10-provider-secret",
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
    assert "SLACK_AUTOMATIC_GENERATION" in worker.handlers
    assert "AUTOMATIC_GENERATION_EXECUTE" in worker.handlers
    assert "AUTOMATIC_GENERATION_MATERIALIZE" in worker.handlers
    assert "SLACK_GENERATION_NOTIFICATION_DELIVERY" in worker.handlers
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)

    try:
        with TestClient(application) as client:
            allowlist = client.put(
                "/api/integrations/slack/allowlist",
                json={
                    "idempotencyKey": "pe-se2-t10-allowlist-001",
                    "workspaceId": workspace_id,
                    "expectedVersion": 1,
                    "channels": [{"channelId": channel_id, "visibility": "PUBLIC"}],
                },
            )
            assert allowlist.status_code == 200, allowlist.text

            acknowledgements = [
                _post_signed(client, event, secret=secret).json()
                for event in fixture["events"]
            ]
            duplicate = _post_signed(
                client, fixture["events"][0], secret=secret
            ).json()
            assert [item["outcome"] for item in acknowledgements] == [
                "ACCEPTED",
                "ACCEPTED",
            ]
            assert duplicate["outcome"] == "DUPLICATE_EVENT_ID"

            assert worker.run_once()
            assert worker.run_once()
            with sessions.begin() as session:
                item = session.scalar(select(SlackWorkItem))
                assert item is not None
                assert item.source_version == 2
                assert item.permalink == fixture["permalink"]
                assert item.quiet_job_id is not None
                item.quiet_due_at = datetime.now(UTC) - timedelta(seconds=1)
                session.execute(
                    update(BackgroundJob)
                    .where(BackgroundJob.id == item.quiet_job_id)
                    .values(run_after=datetime.now(UTC) - timedelta(seconds=1))
                )
                work_item_id = item.id

            _drain_worker(worker, sessions)

            with sessions() as session:
                generation_job = session.scalar(select(GenerationJob))
                intent = session.scalar(select(GenerationSlackNotificationIntent))
                notification_job = session.scalar(
                    select(BackgroundJob).where(
                        BackgroundJob.job_type
                        == "SLACK_GENERATION_NOTIFICATION_DELIVERY"
                    )
                )
                assert generation_job is not None
                assert intent is not None
                assert notification_job is not None
                generation_job_id = generation_job.id
                notification_payload = dict(notification_job.payload)
                notification_attempt = notification_job.attempts

            api_work_item = client.get(
                f"/api/slack-work-items/slack_work_item_{work_item_id.hex}"
            )
            assert api_work_item.status_code == 200
            assert api_work_item.json()["parse"]["status"] == "COMPLETE"
            api_generation = client.get(
                "/api/generation-jobs",
                params={"slackWorkItemId": f"slack_work_item_{work_item_id.hex}"},
            )
            assert api_generation.status_code == 200
            assert api_generation.json()["items"][0]["status"] == "SUCCEEDED"
            reconstruction = client.get(
                f"/api/generation-jobs/generation_job_{generation_job_id.hex}/reconstruction"
            )
            assert reconstruction.status_code == 200
            assert reconstruction.json()["availability"] == "AVAILABLE"

            before_replay = _cardinality_snapshot(sessions)
            worker.handlers["SLACK_AUTOMATIC_GENERATION"](
                _automatic_payload(sessions, work_item_id),
                None,
            )
            worker.handlers["SLACK_GENERATION_NOTIFICATION_DELIVERY"](
                notification_payload,
                _replay_claim(
                    notification_job.id,
                    notification_payload,
                    notification_attempt,
                    worker.worker_id,
                ),
            )
            assert _post_signed(
                client, fixture["events"][1], secret=secret
            ).json()["outcome"] == "DUPLICATE_EVENT_ID"
            assert _cardinality_snapshot(sessions) == before_replay

            provider_reply = next(
                message
                for message in slack.conversations_replies(
                    channel_id=channel_id,
                    thread_ts=root_ts,
                )
                if message.bot_id is not None
            )
            before_self_reply = _source_snapshot(sessions, work_item_id)
            self_reply = {
                "type": "event_callback",
                "event_id": "EvPeSe2T10SelfReply001",
                "team_id": workspace_id,
                "event_time": 2_000_000_001,
                "event": dict(provider_reply.raw_payload),
            }
            ignored = _post_signed(client, self_reply, secret=secret).json()
            assert ignored["outcome"] == "IGNORED"
            assert _source_snapshot(sessions, work_item_id) == before_self_reply

        snapshot = _cardinality_snapshot(sessions)
        assert snapshot == {
            "workItems": 1,
            "sourceRevisions": 2,
            "knowledgeNodes": 2,
            "knowledgeEvidence": 4,
            "knowledgeEmbeddings": 2,
            "projectionReceipts": 1,
            "generationRoots": 1,
            "generationSchedules": 1,
            "providerPermits": 1,
            "providerObservations": 1,
            "generationApplies": 1,
            "applyOutcomes": 5,
            "cases": 5,
            "caseVersions": 5,
            "notificationIntents": 1,
            "notificationReceipts": 1,
        }
        with sessions() as session:
            assert set(
                session.scalars(select(LedgerTestCaseVersion.source_type))
            ) == {"SLACK_AI"}
            assert set(session.scalars(select(LedgerTestCase.status))) == {"ACTIVE"}
            receipt = session.scalar(select(GenerationSlackNotificationReceipt))
            assert receipt is not None
            assert receipt.terminal_outcome == "DELIVERED"
            assert receipt.proof == "THREAD_RECONCILIATION"
            assert session.scalar(select(func.count()).select_from(SlackEvent)) == 2
        assert extraction.calls == 1
        assert embedding.calls == 1
        assert len(generation.calls) == 1
        call_names = [call[0] for call in slack.calls]
        assert call_names.count("chat.postMessage") == 1
        assert call_names.count("find.notification") == 1
    finally:
        worker_engine.dispose()


def _post_signed(
    client: TestClient,
    payload: object,
    *,
    secret: str,
):
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    return client.post(
        "/integrations/slack/events",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": str(SIGNATURE_CLOCK),
            "X-Slack-Signature": sign_slack_request(
                signing_secret=secret,
                timestamp=SIGNATURE_CLOCK,
                raw_body=raw,
            ),
        },
    )


def _drain_worker(
    worker: Worker,
    sessions: sessionmaker[Session],
) -> None:
    for _ in range(40):
        if worker.run_once():
            continue
        with sessions.begin() as session:
            advanced = session.execute(
                update(BackgroundJob)
                .where(BackgroundJob.status == "RETRY")
                .values(run_after=datetime.now(UTC) - timedelta(seconds=1))
            ).rowcount
        if not advanced:
            return
    raise AssertionError("production worker did not drain the PE-SE2-T10 job chain")


def _automatic_payload(
    sessions: sessionmaker[Session],
    work_item_id: uuid.UUID,
) -> dict[str, object]:
    with sessions() as session:
        projection = session.scalar(select(KnowledgeProjectionEvent))
        item = session.get(SlackWorkItem, work_item_id)
        assert projection is not None and item is not None
        return {
            "outboxEventId": str(projection.outbox_event_id),
            "workItemId": str(work_item_id),
            "sourceVersion": item.source_version,
        }


def _replay_claim(
    job_id: uuid.UUID,
    payload: dict[str, object],
    attempt: int,
    worker_id: str,
):
    from tc_backend.db.jobs import ClaimedJob

    return ClaimedJob(
        id=job_id,
        job_type="SLACK_GENERATION_NOTIFICATION_DELIVERY",
        payload=payload,
        attempt=attempt,
        max_attempts=5,
        locked_by=worker_id,
    )


def _cardinality_snapshot(sessions: sessionmaker[Session]) -> dict[str, int]:
    models = {
        "workItems": SlackWorkItem,
        "sourceRevisions": SlackMessageRevision,
        "knowledgeNodes": KnowledgeNode,
        "knowledgeEvidence": KnowledgeEvidence,
        "knowledgeEmbeddings": KnowledgeEmbedding,
        "projectionReceipts": KnowledgeProjectionEvent,
        "generationRoots": GenerationJob,
        "generationSchedules": GenerationExecutionSchedule,
        "providerPermits": GenerationProviderCallPermit,
        "providerObservations": GenerationProviderCallObservation,
        "generationApplies": GenerationApply,
        "applyOutcomes": GenerationApplyOutcome,
        "cases": LedgerTestCase,
        "caseVersions": LedgerTestCaseVersion,
        "notificationIntents": GenerationSlackNotificationIntent,
        "notificationReceipts": GenerationSlackNotificationReceipt,
    }
    with sessions() as session:
        return {
            name: int(session.scalar(select(func.count()).select_from(model)) or 0)
            for name, model in models.items()
        }


def _source_snapshot(
    sessions: sessionmaker[Session], work_item_id: uuid.UUID
) -> tuple[int, int, int]:
    with sessions() as session:
        item = session.get(SlackWorkItem, work_item_id)
        assert item is not None
        return (
            item.source_version,
            int(session.scalar(select(func.count()).select_from(SlackMessageRevision)) or 0),
            int(session.scalar(select(func.count()).select_from(GenerationJob)) or 0),
        )
