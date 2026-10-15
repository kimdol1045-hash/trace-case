from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from tc_api.main import create_app
from tc_backend.db.audit import AuditContext
from tc_backend.db.cases import CaseRepository
from tc_backend.db.cases import TestCaseContent as CaseContent
from tc_backend.db.cases import TestStepInput as StepInput
from tc_backend.db.cycles import CycleRepository
from tc_backend.db.folders import FolderRepository
from tc_backend.db.issues import IssueRepository
from tc_backend.db.models import (
    AuditSourceType,
    BackgroundJob,
    ExecutionPlatform,
    ExecutionStatus,
    KnowledgeNode,
    KnowledgeProjectionEvent,
    OutboxEvent,
)
from tc_backend.db.slack import SlackEventInput, SlackRepository
from tc_backend.knowledge.embedding import EmbeddingBatch
from tc_backend.knowledge.lightrag import LightRAGAuxiliaryIndex
from tc_backend.knowledge.extraction import SlackKnowledgeExtractor
from tc_backend.knowledge.models import (
    KnowledgeNodeType,
    ProviderEvidence,
    ProviderExtraction,
    ProviderKnowledgeCandidate,
    SlackSourceBundle,
)
from tc_backend.repositories.knowledge_repository import EvidenceInput, KnowledgeRepository
from tc_backend.worker.knowledge_consumer import (
    KnowledgeJobConsumer,
    KnowledgeOutboxConsumer,
)

pytestmark = pytest.mark.postgres
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")


class DeterministicBgeM3:
    async def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        vectors = []
        hashes = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            vector = [0.0] * 1024
            vector[0] = 1.0
            vector[1] = digest[0] / 2550.0
            vectors.append(tuple(vector))
            hashes.append(hashlib.sha256(text.encode()).hexdigest())
        return EmbeddingBatch(
            model="bge-m3:test-v1",
            dimension=1024,
            vectors=tuple(vectors),
            content_hashes=tuple(hashes),
        )


class DeterministicSlackExtraction:
    async def extract(self, bundle: SlackSourceBundle) -> ProviderExtraction:
        revision = bundle.revisions[0]
        assert revision.body is not None
        excerpt = "Password reset links expire after five minutes."
        return ProviderExtraction(
            provider="deterministic-e2e",
            model="knowledge-test-v1",
            schema_version="knowledge-extraction-v1",
            candidates=(
                ProviderKnowledgeCandidate(
                    candidate_id="reset-expiry-decision",
                    node_type=KnowledgeNodeType.DECISION,
                    content=excerpt,
                    topic_key="password-reset-expiry",
                    confidence=0.99,
                    evidence=(ProviderEvidence(revision.source_revision_id, excerpt),),
                ),
            ),
        )


def test_slack_outbox_runs_extraction_projection_embedding_and_idempotent_index(
    clean_api_database: Engine,
) -> None:
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    with sessions.begin() as session:
        slack = SlackRepository(session)
        slack.put_channel(
            slack_workspace_id="T_KNOWLEDGE_E2E",
            channel_id="C_KNOWLEDGE_E2E",
            channel_name="knowledge-e2e",
        )
        received = slack.ingest_event(
            SlackEventInput(
                event_id="EvKnowledgeExtraction001",
                slack_workspace_id="T_KNOWLEDGE_E2E",
                channel_id="C_KNOWLEDGE_E2E",
                event_type="ROOT",
                event_ts="1800001000.000001",
                message_ts="1800001000.000001",
                body="Password reset links expire after five minutes.",
                author_id="U_KNOWLEDGE_E2E",
                permalink="https://synthetic.invalid/archives/C_KNOWLEDGE_E2E/p1800001000000001",
            )
        )
        assert received.outbox_id is not None
        outbox_id = received.outbox_id

    consumer = KnowledgeOutboxConsumer(
        session_factory=sessions,
        embedding_provider=DeterministicBgeM3(),
        extractor=SlackKnowledgeExtractor(DeterministicSlackExtraction()),
        light_rag=LightRAGAuxiliaryIndex(None),
    )
    first = asyncio.run(consumer.process_event(outbox_id))
    replay = asyncio.run(consumer.process_event(outbox_id))
    assert not first.replayed
    assert replay.replayed
    assert replay.node_ids == first.node_ids
    with sessions() as session:
        nodes = tuple(
            session.scalars(
                select(KnowledgeNode).where(KnowledgeNode.id.in_(first.node_ids))
            )
        )
        assert {node.node_type for node in nodes} == {"WORK_ITEM", "DECISION"}
        assert session.get(KnowledgeProjectionEvent, outbox_id) is not None
        event = session.get(OutboxEvent, outbox_id)
        assert event is not None and event.status == "PUBLISHED"


def test_knowledge_api_real_postgresql_projection_search_and_degraded_fallback(
    clean_api_database: Engine,
) -> None:
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    provider = DeterministicBgeM3()
    unavailable_light_rag = LightRAGAuxiliaryIndex(None)
    outbox_consumer = KnowledgeOutboxConsumer(
        session_factory=sessions,
        embedding_provider=provider,
        light_rag=unavailable_light_rag,
    )

    with sessions.begin() as session:
        folder = FolderRepository(session).create(
            name="Knowledge API",
            parent_id=None,
            sort_order=0,
            audit=_audit("folder"),
        )
        work_item = CaseRepository(session).create_work_item(
            work_item_key="WORK-KNOWLEDGE-API",
            category_id=folder.id,
            summary="Password reset",
            audit=_audit("work-item"),
        )
        case = CaseRepository(session).create_case(
            work_item_id=work_item.id,
            content=CaseContent(
                summary="Password reset regression case",
                precondition="A user exists",
                steps=(
                    StepInput(
                        action="Request a reset link",
                        expected_result="A reset link is issued",
                    ),
                ),
                assignee_label="QA",
                tag_names=("knowledge",),
            ),
            change_reason="knowledge API projection",
            audit=_audit("case"),
        )
        event_id = session.scalar(
            select(OutboxEvent.id).where(
                OutboxEvent.aggregate_type == "TEST_CASE",
                OutboxEvent.aggregate_id == case.test_case.id,
            )
        )
        assert event_id is not None

    first = asyncio.run(outbox_consumer.process_event(event_id))
    replay = asyncio.run(outbox_consumer.process_event(event_id))
    assert not first.replayed
    assert replay.replayed
    assert replay.node_ids == first.node_ids

    now = datetime.now(UTC)
    with sessions.begin() as session:
        repository = KnowledgeRepository(session)
        case_node = session.get(KnowledgeNode, first.node_ids[0])
        assert case_node is not None
        feature = repository.create_node(
            node_type="FEATURE",
            label="Password reset",
            entity_type="FEATURE",
            entity_id=uuid.uuid4(),
            properties={"content": "Password reset feature", "resourceVersion": 1},
            recorded_at=now,
            valid_from=now,
            evidence=_evidence("password reset feature"),
        )
        decision = repository.create_node(
            node_type="DECISION",
            label="Reset links expire after five minutes",
            properties={
                "content": "Password reset links expire after five minutes.",
                "resourceVersion": 1,
            },
            recorded_at=now,
            valid_from=now,
            evidence=_evidence("five minute decision"),
        )
        execution = repository.create_node(
            node_type="EXECUTION",
            label="Password reset iOS FAIL",
            properties={"content": "Password reset execution failed on iOS", "status": "FAIL"},
            recorded_at=now,
            valid_from=now,
            evidence=_evidence("execution failure"),
        )
        issue = repository.create_node(
            node_type="ISSUE",
            label="Expired reset link accepted",
            properties={"content": "Issue found by failed password reset execution"},
            recorded_at=now,
            valid_from=now,
            evidence=_evidence("issue snapshot"),
        )
        retracted = repository.create_node(
            node_type="DECISION",
            label="Old reset policy",
            status="RETRACTED",
            properties={"content": "Password reset links never expire"},
            recorded_at=now,
            valid_to=now,
            evidence=_evidence("retracted policy"),
        )
        repository.create_edge(
            source_node_id=decision.id,
            predicate="APPLIES_TO",
            target_node_id=feature.id,
            recorded_at=now,
            evidence=_evidence("decision feature edge"),
        )
        repository.create_edge(
            source_node_id=case_node.id,
            predicate="VERIFIES",
            target_node_id=decision.id,
            recorded_at=now,
            evidence=_evidence("case decision edge"),
        )
        repository.create_edge(
            source_node_id=execution.id,
            predicate="INSTANCE_OF",
            target_node_id=case_node.id,
            recorded_at=now,
            evidence=_evidence("execution case edge"),
        )
        repository.create_edge(
            source_node_id=issue.id,
            predicate="FOUND_BY",
            target_node_id=execution.id,
            recorded_at=now,
            evidence=_evidence("issue execution edge"),
        )
        feature_id = f"knowledge_{feature.id.hex}"
        decision_id = f"knowledge_{decision.id.hex}"
        retracted_id = f"knowledge_{retracted.id.hex}"

    application = create_app(
        validate_database=False,
        database_engine=clean_api_database,
        knowledge_embedding_provider=provider,
        knowledge_light_rag=unavailable_light_rag,
    )
    job_consumer = KnowledgeJobConsumer(
        session_factory=sessions,
        outbox_consumer=outbox_consumer,
        embedding_provider=provider,
        light_rag=unavailable_light_rag,
        worker_id="knowledge-api-e2e",
    )
    with TestClient(application) as client:
        reindex = client.post(
            "/api/knowledge/jobs/reindex",
            json={
                "idempotencyKey": "knowledge-e2e-reindex-v1",
                "nodeIds": [],
            },
        )
        assert reindex.status_code == 202, reindex.text
        assert asyncio.run(job_consumer.run_once()) is not None

        active_page = client.get("/api/knowledge/nodes", params={"statuses": "ACTIVE"})
        assert active_page.status_code == 200, active_page.text
        active_ids = {item["id"] for item in active_page.json()["items"]}
        assert feature_id in active_ids
        assert retracted_id not in active_ids
        assert all(_IDENTIFIER.fullmatch(item) for item in active_ids)

        detail = client.get(f"/api/knowledge/nodes/{decision_id}")
        assert detail.status_code == 200, detail.text
        evidence_id = detail.json()["evidence"][0]["id"]
        assert _IDENTIFIER.fullmatch(evidence_id)
        evidence = client.get(f"/api/knowledge/nodes/{decision_id}/evidence")
        assert evidence.status_code == 200
        assert evidence.json()["items"][0]["contentHash"].startswith("sha256:")

        patch = client.patch(
            "/api/knowledge/status",
            json={
                "targetType": "NODE",
                "targetId": decision_id,
                "expectedVersion": 1,
                "status": "ACTIVE",
                "effectiveAt": now.isoformat(),
                "reason": "Confirm feature scope and validity",
                "evidenceIds": [evidence_id],
                "changedBy": "qa-knowledge-admin",
                "scopeNodeIds": [feature_id],
                "validFrom": now.isoformat(),
            },
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["target"]["resourceVersion"] == 2
        assert patch.json()["projectionQueued"] is True
        history = client.get(f"/api/knowledge/nodes/{decision_id}/audit")
        assert history.json()["items"][0]["targetVersion"] == 2
        detail = client.get(f"/api/knowledge/nodes/{decision_id}").json()
        assert detail["scopeNodeIds"] == [feature_id]

        search = client.post(
            "/api/knowledge/search",
            json={
                "query": "Password reset",
                "statuses": ["ACTIVE"],
                "sourceTypes": [],
                "nodeTypes": [],
                "featureId": feature_id,
                "limit": 50,
                "cursor": None,
            },
        )
        assert search.status_code == 200, search.text
        body = search.json()
        assert body["degraded"] is True
        assert body["degradationReasons"] == ["LIGHTRAG_UNAVAILABLE"]
        assert {"TEST_CASE", "EXECUTION", "ISSUE"}.issubset(
            {item["nodeType"] for item in body["items"]}
        )
        assert all(item["status"] == "ACTIVE" for item in body["items"])
        assert retracted_id not in {item["nodeId"] for item in body["items"]}
        assert any("VECTOR" in item["reason"]["signals"] for item in body["items"])
        assert [item["rank"] for item in body["items"]] == list(
            range(1, len(body["items"]) + 1)
        )
        assert [item["score"] for item in body["items"]] == sorted(
            [item["score"] for item in body["items"]], reverse=True
        )
        assert all(
            item["degradationReasons"] == body["degradationReasons"]
            for item in body["items"]
        )
        assert all(
            item["reason"]["summary"].startswith(
                f'Rank {item["rank"]} with score {item["score"]:.3f}:'
            )
            for item in body["items"]
        )
        assert all(
            (item["featureDistance"] is not None)
            == ("FEATURE_PROXIMITY" in item["reason"]["signals"])
            for item in body["items"]
        )

        first_page = client.post(
            "/api/knowledge/search",
            json={
                "query": "Password reset",
                "statuses": ["ACTIVE"],
                "sourceTypes": [],
                "nodeTypes": [],
                "featureId": feature_id,
                "limit": 2,
                "cursor": None,
            },
        ).json()
        assert first_page["nextCursor"] is not None
        second_page = client.post(
            "/api/knowledge/search",
            json={
                "query": "Password reset",
                "statuses": ["ACTIVE"],
                "sourceTypes": [],
                "nodeTypes": [],
                "featureId": feature_id,
                "limit": 2,
                "cursor": first_page["nextCursor"],
            },
        ).json()
        assert [item["rank"] for item in first_page["items"]] == [1, 2]
        assert [item["rank"] for item in second_page["items"]] == [3, 4]
        assert first_page["items"][-1]["score"] >= second_page["items"][0]["score"]

        inactive_search = client.post(
            "/api/knowledge/search",
            json={
                "query": "Password reset links never expire",
                "statuses": ["RETRACTED"],
                "sourceTypes": [],
                "nodeTypes": ["DECISION"],
                "featureId": None,
                "limit": 10,
                "cursor": None,
            },
        )
        assert inactive_search.status_code == 200, inactive_search.text
        inactive_body = inactive_search.json()
        assert [item["nodeId"] for item in inactive_body["items"]] == [retracted_id]
        assert inactive_body["items"][0]["status"] == "RETRACTED"
        assert inactive_body["items"][0]["featureDistance"] is None
        assert "FEATURE_PROXIMITY" not in inactive_body["items"][0]["reason"]["signals"]
        assert "feature proximity did not contribute" in inactive_body["items"][0][
            "reason"
        ]["summary"]

        explorer = client.post(
            "/api/knowledge/explorer",
            json={
                "rootNodeId": feature_id,
                "direction": "BOTH",
                "predicates": [],
                "statuses": ["ACTIVE"],
                "maxDepth": 5,
                "limit": 50,
                "cursor": None,
            },
        )
        assert explorer.status_code == 200, explorer.text
        assert [item["node"]["nodeType"] for item in explorer.json()["nodes"]] == [
            "FEATURE",
            "DECISION",
            "TEST_CASE",
            "EXECUTION",
            "ISSUE",
        ]

        projection = client.post(
            "/api/knowledge/jobs/projection",
            json={"idempotencyKey": "knowledge-e2e-projection-v1", "limit": 100},
        )
        assert projection.status_code == 202
        while asyncio.run(job_consumer.run_once()) is not None:
            pass

    with sessions() as session:
        jobs = tuple(
            session.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.job_type.in_(("KNOWLEDGE_PROJECTION", "KNOWLEDGE_REINDEX"))
                )
            )
        )
        assert jobs and all(job.status == "SUCCEEDED" for job in jobs)


def test_knowledge_api_resolves_every_qa_ledger_uri_and_unavailable_sources_safely(
    clean_api_database: Engine,
) -> None:
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    with sessions.begin() as session:
        folder = FolderRepository(session).create(
            name="Knowledge citation navigation",
            parent_id=None,
            sort_order=0,
            audit=_audit("citation-folder"),
        )
        work_item = CaseRepository(session).create_work_item(
            work_item_key="WORK-CITATION-NAV",
            category_id=folder.id,
            summary="Citation navigation",
            audit=_audit("citation-work-item"),
        )
        case = CaseRepository(session).create_case(
            work_item_id=work_item.id,
            content=CaseContent(
                summary="Authoritative citation case",
                precondition="A ledger source exists",
                steps=(
                    StepInput(
                        action="Open the source",
                        expected_result="The authoritative ledger opens",
                    ),
                ),
                assignee_label="QA",
                tag_names=("citation",),
            ),
            change_reason="citation navigation fixture",
            audit=_audit("citation-case"),
        )
        case_key = case.test_case.case_key
        case_id = case.test_case.id

    with sessions.begin() as session:
        cycle = CycleRepository(session).create(
            name="Citation navigation cycle",
            starts_at=date(2026, 8, 26),
            scope_type="WORK_ITEM",
            scope_value="WORK-CITATION-NAV",
            audit=_audit("citation-cycle"),
        )
        cycle_id = cycle.cycle.id
        item_id = cycle.items[0].id
    with sessions.begin() as session:
        CycleRepository(session).start(
            cycle_id=cycle_id,
            base_resource_version=1,
            audit=_audit("citation-cycle-start"),
        )
    with sessions.begin() as session:
        attempt = CycleRepository(session).start_attempt(
            cycle_item_id=item_id,
            platform=ExecutionPlatform.IOS,
            audit=_audit("citation-attempt-start"),
        )
        attempt_id = attempt.id
    with sessions.begin() as session:
        CycleRepository(session).complete_attempt(
            attempt_id=attempt_id,
            status=ExecutionStatus.FAIL,
            actual_result="Citation navigation failed",
            note="Retained execution context",
            audit=_audit("citation-attempt-complete"),
        )
    with sessions.begin() as session:
        issue = IssueRepository(session).create_from_attempt(
            attempt_id=attempt_id,
            summary="Citation navigation issue",
            audit=_audit("citation-issue"),
        )
        issue_id = issue.issue.id
        issue_key = issue.issue.issue_key

    now = datetime.now(UTC)
    with sessions.begin() as session:
        repository = KnowledgeRepository(session)
        for aggregate_type, aggregate_id in (
            ("EXECUTION_ATTEMPT", attempt_id),
            ("ISSUE", issue_id),
        ):
            event = session.execute(
                select(OutboxEvent)
                .where(
                    OutboxEvent.aggregate_type == aggregate_type,
                    OutboxEvent.aggregate_id == aggregate_id,
                )
                .order_by(OutboxEvent.aggregate_version.desc())
                .limit(1)
            ).scalar_one()
            repository.project_outbox_event(event.id)
        unresolved = repository.create_node(
            node_type="EXECUTION",
            label="Retained unresolved execution",
            properties={"content": "The source context was permanently removed."},
            recorded_at=now,
            evidence=EvidenceInput.from_content(
                source_kind="LEDGER_ENTITY",
                entity_type="EXECUTION_ATTEMPT",
                entity_id=uuid.uuid4(),
                revision_key="1",
                content="unresolved execution evidence",
                recorded_at=now,
            ),
        )
        deleted_slack = repository.create_node(
            node_type="DECISION",
            label="Deleted Slack decision",
            properties={
                "content": "Only immutable deletion evidence remains.",
                "classificationReason": "source_message_deleted",
            },
            recorded_at=now,
            evidence=EvidenceInput.from_content(
                source_kind="SOURCE_REVISION",
                entity_type="SLACK_MESSAGE_REVISION",
                entity_id=uuid.uuid4(),
                revision_key="2",
                content="deleted Slack tombstone",
                recorded_at=now,
                permalink="https://synthetic.invalid/archives/C_CITATION/p1800000000000001",
            ),
        )
        node_ids = {
            node.entity_type: f"knowledge_{node.id.hex}"
            for node in session.scalars(
                select(KnowledgeNode).where(
                    KnowledgeNode.entity_type.in_(
                        ("TEST_CASE", "EXECUTION_ATTEMPT", "ISSUE")
                    )
                )
            )
        }
        unresolved_id = f"knowledge_{unresolved.id.hex}"
        deleted_slack_id = f"knowledge_{deleted_slack.id.hex}"

    with sessions.begin() as session:
        CaseRepository(session).soft_delete(
            test_case_id=case_id,
            base_version=1,
            audit=_audit("citation-case-delete"),
        )
        IssueRepository(session).soft_delete(
            issue_id=issue_id,
            base_resource_version=1,
            audit=_audit("citation-issue-delete"),
        )

    application = create_app(
        validate_database=False,
        database_engine=clean_api_database,
        knowledge_embedding_provider=DeterministicBgeM3(),
        knowledge_light_rag=LightRAGAuxiliaryIndex(None),
    )
    with TestClient(application) as client:
        citations = {
            source_type: client.get(f"/api/knowledge/nodes/{node_id}").json()[
                "evidence"
            ][0]["citation"]
            for source_type, node_id in node_ids.items()
        }
        public_issue_key = f"{issue_key.rpartition('-')[0]}-{int(issue_key.rpartition('-')[2])}"
        assert citations["TEST_CASE"]["uri"] == f"/test-cases/{case_key}"
        assert citations["EXECUTION_ATTEMPT"]["uri"] == (
            f"/cycles/cycle_{cycle_id.hex}/execute?"
            f"caseKey={case_key}&platform=IOS&attempt=1&run=1"
        )
        assert citations["ISSUE"]["uri"] == f"/issues/{public_issue_key}"
        assert citations["TEST_CASE"]["deleted"] is False
        assert citations["ISSUE"]["deleted"] is False
        assert client.get(f"/api/knowledge/nodes/{unresolved_id}").json()["evidence"][
            0
        ]["citation"]["uri"] is None
        tombstone = client.get(
            f"/api/knowledge/nodes/{deleted_slack_id}"
        ).json()["evidence"][0]["citation"]
        assert tombstone["deleted"] is True
        assert tombstone["excerpt"] is None
        assert tombstone["uri"] == (
            "https://synthetic.invalid/archives/C_CITATION/p1800000000000001"
        )


def _audit(reason: str) -> AuditContext:
    return AuditContext(
        source_type=AuditSourceType.API,
        actor_label="knowledge-api-e2e",
        reason=reason,
        trace_id=f"knowledge_api_{reason}",
    )


def _evidence(label: str) -> EvidenceInput:
    return EvidenceInput.from_content(
        source_kind="LEDGER_ENTITY",
        entity_type="MANUAL",
        entity_id=uuid.uuid4(),
        revision_key="1",
        content=label,
        recorded_at=datetime.now(UTC),
    )
