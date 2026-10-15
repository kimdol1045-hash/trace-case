from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from scripts.evaluate_automatic_generation_quality import main as evaluate_main
from scripts.verify_automatic_generation_quality_artifacts import main as verify_main
from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from tc_backend.db.audit import AuditContext
from tc_backend.db.cases import CaseRepository
from tc_backend.db.constants import AI_INBOX_FOLDER_ID, DEFAULT_WORKSPACE_ID
from tc_backend.db.jobs import JobRepository
from tc_backend.db.models import (
    AuditSourceType,
    BackgroundJob,
    GenerationApply,
    GenerationApplyOutcome,
    GenerationContext,
    GenerationJob,
    GenerationMaterializationSchedule,
    GenerationVersionSet,
    KnowledgeEvidence,
    KnowledgeSourceKind,
    SlackMessage,
    SlackMessageRevision,
)
from tc_backend.db.slack import SlackEventInput, SlackRepository
from tc_backend.generation.application import (
    GenerationApplicationService,
    automatic_apply_command,
)
from tc_backend.generation.quality_gate import canonical_digest, evaluate_quality_snapshot
from tc_backend.generation.quality_snapshot import (
    QualitySnapshotExportError,
    export_quality_candidate_snapshot,
    render_candidate_snapshot,
)
from tc_backend.generation.semantic_duplicates import semantic_duplicate_policy_seal
from tc_backend.repositories.generation_repository import GenerationRepository
from tc_backend.repositories.knowledge_repository import EvidenceInput, KnowledgeRepository

pytestmark = pytest.mark.postgres

WORKSPACE = "T_ATTACK_WORKSPACE"
CHANNEL = "C_ATTACK_CHANNEL"
RAW_SLACK = "민감사용자 qa.owner@example.com sk-RAWSECRET123456789 https://private.invalid/source"
RAW_ACTION = "Bearer attack-token-value 호출 https://internal.invalid/api"
RAW_EXPECTED = "민감 결과 qa.owner@example.com"
RAW_MODEL = "https://private.endpoint.invalid/model"
RAW_PROVIDER = "sk-PROVIDERSECRET123456789"
RAW_PROMPT = "qa.owner@example.com"


def _arm_materialization(session: Session, job: GenerationJob) -> BackgroundJob:
    background_id = JobRepository(session).enqueue(
        job_type="AUTOMATIC_GENERATION_MATERIALIZE",
        payload={"generationJobId": str(job.id)},
        max_attempts=20,
        idempotency_key=f"automatic-generation-materialize:{job.id}",
    )
    claim = JobRepository(session).claim_next(
        worker_id="quality-export-worker",
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
            mode="AUTOMATIC_APPLY",
            state="LEASED",
            not_before=now,
            lease_owner="quality-export-worker",
            lease_token=uuid.uuid4(),
            lease_expires_at=now + timedelta(minutes=5),
            resource_version=2,
        )
    )
    session.flush()
    background = session.get(BackgroundJob, background_id)
    assert background is not None
    return background


def _seed_operational_apply(engine: Engine) -> uuid.UUID:
    sessions = sessionmaker(engine, expire_on_commit=False)
    event = SlackEventInput(
        event_id=f"Ev-quality-export-{uuid.uuid4().hex}",
        slack_workspace_id=WORKSPACE,
        channel_id=CHANNEL,
        event_type="ROOT",
        event_ts="1788200000.000100",
        message_ts="1788200000.000000",
        thread_ts=None,
        revision_ts=None,
        body=RAW_SLACK,
        author_id="U_PRIVATE_PERSON",
        permalink="https://private.invalid/slack/root",
        channel_is_private=False,
        raw_payload={"token": "xoxb-RAW-SLACK-TOKEN", "endpoint": RAW_MODEL},
    )
    with sessions.begin() as session:
        slack = SlackRepository(session)
        slack.put_channel(
            slack_workspace_id=WORKSPACE,
            channel_id=CHANNEL,
            channel_name="quality-private",
        )
        ingested = slack.ingest_event(event)
        assert ingested.work_item_id is not None and ingested.revision_id is not None
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
        node = KnowledgeRepository(session).create_node(
            node_type="WORK_ITEM",
            label=RAW_SLACK,
            entity_type="SLACK_WORK_ITEM",
            entity_id=work_item_id,
            properties={"sourceVersion": 1, "content": RAW_SLACK},
            recorded_at=datetime.now(UTC),
            evidence=evidence,
        )
        evidence_id = f"knowledge_node_{node.id.hex}"
        result = {
            "outputSchemaVersion": "1.0.0",
            "cases": [
                {
                    "summary": f"isolated {name} path " + f"{name} " * 16,
                    "steps": [
                        {
                            "sequence": 1,
                            "precondition": f"{name} precondition " + f"{name} " * 12,
                            "action": (
                                RAW_ACTION
                                if name == "alpha"
                                else f"{name} action " + f"{name} " * 18
                            ),
                        },
                        {"sequence": 2, "action": f"{name} finish " + f"{name} " * 18},
                    ],
                    "caseExpectedResult": (
                        RAW_EXPECTED
                        if name == "alpha"
                        else f"{name} outcome " + f"{name} " * 18
                    ),
                    "priority": "HIGH",
                    "labels": ["generated", f"path-{name}"],
                    "evidenceNodeIds": [evidence_id],
                    "generationReason": RAW_SLACK,
                    "inferenceOnly": False,
                    "openQuestions": [],
                }
                for name in ("alpha", "beta", "gamma", "delta", "epsilon")
            ],
            "categoryCandidate": {
                "categoryId": f"folder_{AI_INBOX_FOLDER_ID.hex}",
                "score": 1,
                "alternatives": [],
            },
        }
        repository = GenerationRepository(session)
        prepared = repository.prepare_automatic_generation(
            slack_work_item_id=work_item_id,
            source_version=1,
            prompt_version=RAW_PROMPT,
        )
        policy = semantic_duplicate_policy_seal()
        identity = {
            "generationPolicyVersion": "generation-policy-v1",
            "generationPolicyDigest": canonical_digest({"policy": "v1"}),
            "promptVersion": RAW_PROMPT,
            "promptDigest": canonical_digest({"prompt": RAW_PROMPT}),
            "providerId": RAW_PROVIDER,
            "modelId": RAW_MODEL,
            "modelRevision": "private-model-revision",
            "embeddingProviderId": policy.provider_id,
            "embeddingModelId": policy.model_id,
            "embeddingRevision": policy.model_revision,
            "embeddingDimensions": policy.dimensions,
            "semanticDuplicateAlgorithmVersion": policy.version,
            "semanticDuplicateThreshold": policy.threshold,
            "semanticDuplicatePolicyDigest": policy.digest,
            "exactDuplicateAlgorithmVersion": "exact-content-v1",
            "generationInputSchemaVersion": "1.0.0",
            "generationOutputSchemaVersion": "1.0.0",
            "suggestionSchemaVersion": "generation-suggestion/1.0.0",
            "qualityGateSchemaVersion": "automatic-generation-quality-gate/1.0.0",
            "ontologySchemaVersion": "ontology-v1",
        }
        identity_digest = canonical_digest(identity)
        version_set = GenerationVersionSet(
            id=uuid.uuid4(),
            version=1,
            synthetic=False,
            reviewed=False,
            identity=identity,
            identity_digest=identity_digest,
            cohort_id="quality-export-postgres",
            cohort_digest=canonical_digest({"cohort": 1}),
            population_membership_digest=canonical_digest({"population": 1}),
            regression_report_digest=canonical_digest({"regression": 1}),
            artifact_manifest_digest=canonical_digest({"manifest": 1}),
            live_attestation_digest=None,
            created_by="postgres-test",
        )
        session.add(version_set)
        session.flush()
        job = GenerationJob(
            id=prepared.job_id,
            slack_work_item_id=work_item_id,
            source_version=1,
            generation_version=1,
            generation_trigger="AUTOMATIC",
            status="QUEUED",
            provider=RAW_PROVIDER,
            model=RAW_MODEL,
            prompt_version=RAW_PROMPT,
            schema_version="1.0.0",
            ontology_schema_id=prepared.context.ontology_schema_id,
            root_job_id=prepared.job_id,
            retry_of_job_id=None,
            attempt_number=1,
            generation_input_capture_state="SEALED",
            generation_input_schema_version="1.0.0",
            generation_input=prepared.generation_input,
            generation_input_digest=prepared.generation_input_digest,
            version_set_id=version_set.id,
            version_set_digest=identity_digest,
        )
        session.add(job)
        session.flush()
        repository.persist_context(job.id, prepared.context)
        repository.begin_execution(job.id)
        repository.succeed_execution(job.id, result)
        session.flush()
        _arm_materialization(session, job)
        application = GenerationApplicationService(session)
        draft = application.plan(job.id)
        raw_parent = draft.placement["parent"]
        assert isinstance(raw_parent, dict)
        if raw_parent["decision"] == "CREATE_NEW":
            CaseRepository(session).create_work_item(
                work_item_key=str(raw_parent["expectedWorkItemKey"]),
                category_id=uuid.UUID(
                    hex=str(raw_parent["categoryId"]).removeprefix("folder_")
                ),
                summary=str(raw_parent["expectedSummary"]),
                audit=AuditContext(
                    source_type=AuditSourceType.API,
                    actor_label="quality-export-parent-authority",
                    trace_id="quality-export-parent-authority",
                    idempotency_key="quality-export-parent-authority",
                ),
            )
            draft = application.plan(job.id)
            assert isinstance(draft.placement["parent"], dict)
            assert draft.placement["parent"]["decision"] == "REUSE_EXISTING"
        application.apply(
            automatic_apply_command(draft),
            audit=AuditContext(
                source_type=AuditSourceType.API,
                actor_label="quality-export-postgres-test",
                trace_id="quality-export-postgres",
                idempotency_key=f"quality-export-{job.id}",
            ),
        )
        return job.id


def _authority_counts(engine: Engine) -> tuple[int, ...]:
    with Session(engine) as session:
        return tuple(
            int(session.scalar(select(func.count()).select_from(model)) or 0)
            for model in (
                GenerationJob,
                GenerationContext,
                GenerationApply,
                GenerationApplyOutcome,
                KnowledgeEvidence,
            )
        )


def _export_read_only(engine: Engine, root_id: uuid.UUID) -> tuple[bytes, bytes, dict[str, object]]:
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            assert connection.scalar(text("SHOW transaction_read_only")) == "on"
            with Session(bind=connection, expire_on_commit=False) as session:
                first = export_quality_candidate_snapshot(
                    session,
                    root_job_ids=(root_id,),
                    capture_environment="staging",
                    report_outputs={
                        "json": "artifacts/automatic-generation-quality.json",
                        "junit": "artifacts/automatic-generation-quality.junit.xml",
                        "markdown": "artifacts/automatic-generation-quality.md",
                    },
                )
                second = export_quality_candidate_snapshot(
                    session,
                    root_job_ids=(root_id,),
                    capture_environment="staging",
                    report_outputs=first["reportOutputs"],
                )
                return render_candidate_snapshot(first), render_candidate_snapshot(second), first
        finally:
            transaction.rollback()


def test_postgresql_snapshot_is_read_only_hash_only_deterministic_and_consumable(
    migrated_engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_id = _seed_operational_apply(migrated_engine)
    before = _authority_counts(migrated_engine)

    first, second, snapshot = _export_read_only(migrated_engine, root_id)

    assert first == second
    assert _authority_counts(migrated_engine) == before
    serialized = first.decode("utf-8")
    for forbidden in (
        RAW_SLACK,
        RAW_ACTION,
        RAW_EXPECTED,
        RAW_MODEL,
        RAW_PROVIDER,
        RAW_PROMPT,
        "xoxb-RAW-SLACK-TOKEN",
        "private.invalid",
    ):
        assert forbidden not in serialized
    assert snapshot["profile"] == "CANDIDATE"
    assert snapshot["dataset"]["review"]["status"] == "UNREVIEWED"
    assert snapshot["liveGate"]["status"] == "NOT_RUN"
    outcome = evaluate_quality_snapshot(snapshot)
    assert outcome.passed is True, (
        outcome.report["metrics"],
        outcome.report["checks"],
    )
    assert outcome.report["certification"] == {
        "releaseCertified": False,
        "status": "NOT_CERTIFIED_INELIGIBLE",
        "reasonCodes": ["CANDIDATE_PROFILE"],
        "liveRunId": None,
    }

    monkeypatch.chdir(tmp_path)
    (tmp_path / "artifacts").mkdir()
    snapshot_path = tmp_path / "candidate.json"
    snapshot_path.write_bytes(first)
    expected_hash = hashlib.sha256(first).hexdigest()
    assert (
        evaluate_main(
            [
                "--snapshot",
                str(snapshot_path),
                "--profile",
                "candidate",
                "--json-output",
                "artifacts/automatic-generation-quality.json",
                "--junit-output",
                "artifacts/automatic-generation-quality.junit.xml",
                "--markdown-output",
                "artifacts/automatic-generation-quality.md",
                "--expected-snapshot-sha256",
                expected_hash,
            ]
        )
        == 0
    )
    assert (
        verify_main(
            [
                "--json-report",
                "artifacts/automatic-generation-quality.json",
                "--junit-report",
                "artifacts/automatic-generation-quality.junit.xml",
                "--markdown-report",
                "artifacts/automatic-generation-quality.md",
                "--manifest-output",
                "artifacts/automatic-generation-quality.manifest.json",
                "--snapshot",
                str(snapshot_path),
                "--expected-snapshot-sha256",
                expected_hash,
            ]
        )
        == 0
    )


def test_postgresql_snapshot_fails_closed_on_malformed_knowledge_provenance(
    migrated_engine: Engine,
) -> None:
    root_id = _seed_operational_apply(migrated_engine)
    with migrated_engine.begin() as connection:
        connection.execute(update(KnowledgeEvidence).values(excerpt_hash="malformed"))

    with Session(migrated_engine) as session:
        with pytest.raises(QualitySnapshotExportError, match="EVIDENCE_EXCERPT_HASH_INVALID"):
            export_quality_candidate_snapshot(
                session,
                root_job_ids=(root_id,),
                capture_environment="staging",
                report_outputs={
                    "json": "artifacts/report.json",
                    "junit": "artifacts/report.xml",
                    "markdown": "artifacts/report.md",
                },
            )
