"""Seed one bounded, credential-free quality-review candidate for local Wave 10 UAT."""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from tc_backend.db.audit import AuditContext
from tc_backend.db.cases import CaseRepository
from tc_backend.db.constants import AI_INBOX_FOLDER_ID, DEFAULT_WORKSPACE_ID
from tc_backend.db.jobs import JobRepository
from tc_backend.db.models import (
    AuditSourceType,
    BackgroundJob,
    GenerationJob,
    GenerationMaterializationSchedule,
    GenerationVersionSet,
    SlackMessage,
    SlackMessageRevision,
)
from tc_backend.db.slack import SlackEventInput, SlackRepository
from tc_backend.generation.application import (
    GenerationApplicationService,
    automatic_apply_command,
)
from tc_backend.generation.quality_gate import canonical_digest
from tc_backend.generation.quality_review import (
    GenerationQualityReviewService,
    ReviewerAuthority,
)
from tc_backend.generation.semantic_duplicates import semantic_duplicate_policy_seal
from tc_backend.repositories.generation_repository import GenerationRepository
from tc_backend.repositories.knowledge_repository import EvidenceInput, KnowledgeRepository
from tc_backend.db.models import KnowledgeSourceKind


def _arm_materialization(session: Session, job: GenerationJob) -> None:
    background_id = JobRepository(session).enqueue(
        job_type="AUTOMATIC_GENERATION_MATERIALIZE",
        payload={"generationJobId": str(job.id)},
        max_attempts=20,
        idempotency_key=f"wave10-quality-materialize:{job.id}",
    )
    claim = JobRepository(session).claim_next(
        worker_id="wave10-quality-worker",
        stale_after=timedelta(minutes=5),
        job_types=("AUTOMATIC_GENERATION_MATERIALIZE",),
    )
    if claim is None or claim.id != background_id:
        raise RuntimeError("quality-review seed could not claim its exact materialization job")
    now = datetime.now(UTC)
    session.add(
        GenerationMaterializationSchedule(
            id=uuid.UUID("30000000-0000-0000-0000-000000000001"),
            workspace_id=DEFAULT_WORKSPACE_ID,
            generation_job_id=job.id,
            root_job_id=job.root_job_id,
            background_job_id=background_id,
            mode="AUTOMATIC_APPLY",
            state="LEASED",
            not_before=now,
            lease_owner="wave10-quality-worker",
            lease_token=uuid.UUID("30000000-0000-0000-0000-000000000002"),
            lease_expires_at=now + timedelta(minutes=5),
            resource_version=2,
        )
    )
    session.flush()
    if session.get(BackgroundJob, background_id) is None:
        raise RuntimeError("quality-review seed materialization job disappeared")


def _seed_operational_root(database_url: str) -> uuid.UUID:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    event = SlackEventInput(
        event_id="EvWave10QualityCandidateRoot",
        slack_workspace_id="WAVE10_QUALITY_LOCAL",
        channel_id="CWAVE10QUALITY",
        event_type="ROOT",
        event_ts="1788751000.000001",
        message_ts="1788751000.000001",
        thread_ts=None,
        revision_ts=None,
        body="Wave 10 deterministic disposable quality candidate",
        author_id="U_WAVE10_QUALITY_LOCAL",
        permalink="https://wave10.invalid/local-quality-root",
        channel_is_private=False,
        raw_payload={
            "team_id": "WAVE10_QUALITY_LOCAL",
            "api_app_id": "AWAVE10QUALITY",
            "type": "event_callback",
            "event_id": "EvWave10QualityCandidateRoot",
            "event_time": 1788751000,
            "event": {
                "type": "message",
                "user": "U_WAVE10_QUALITY_LOCAL",
                "text": "Wave 10 deterministic disposable quality candidate",
                "ts": "1788751000.000001",
                "event_ts": "1788751000.000001",
                "channel": "CWAVE10QUALITY",
                "channel_type": "channel",
            },
        },
    )
    with sessions.begin() as session:
        slack = SlackRepository(session)
        slack.put_channel(
            slack_workspace_id=event.slack_workspace_id,
            channel_id=event.channel_id,
            channel_name="Wave 10 quality local",
        )
        ingested = slack.ingest_event(event)
        if ingested.work_item_id is None or ingested.revision_id is None:
            raise RuntimeError("quality-review seed Slack root did not materialize")
        work_item_id = ingested.work_item_id
        revision_id = ingested.revision_id

    with sessions.begin() as session:
        revision, message = session.execute(
            select(SlackMessageRevision, SlackMessage)
            .join(SlackMessage, SlackMessage.id == SlackMessageRevision.message_id)
            .where(SlackMessageRevision.id == revision_id)
        ).one()
        source_evidence = EvidenceInput.from_content(
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
            label="Wave 10 local quality candidate",
            entity_type="SLACK_WORK_ITEM",
            entity_id=work_item_id,
            properties={"sourceVersion": 1, "content": "Wave 10 local quality candidate"},
            recorded_at=datetime(2026, 9, 7, tzinfo=UTC),
            evidence=source_evidence,
        )
        evidence_id = f"knowledge_node_{node.id.hex}"
        generated = {
            "outputSchemaVersion": "1.0.0",
            "cases": [
                {
                    "summary": f"isolated {name} path " + f"{name} " * 16,
                    "steps": [
                        {
                            "sequence": 1,
                            "precondition": f"{name} precondition " * 12,
                            "action": f"{name} action " * 18,
                        },
                        {"sequence": 2, "action": f"{name} finish " * 18},
                    ],
                    "caseExpectedResult": f"{name} expected outcome " * 16,
                    "priority": "HIGH",
                    "labels": ["generated", f"quality-{name}"],
                    "evidenceNodeIds": [evidence_id],
                    "generationReason": f"deterministic local {name} coverage",
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
            prompt_version="wave10-quality-safe-v1",
        )
        policy = semantic_duplicate_policy_seal()
        identity = {
            "generationPolicyVersion": "generation-policy-v1",
            "generationPolicyDigest": canonical_digest({"policy": "wave10-local"}),
            "promptVersion": "wave10-quality-safe-v1",
            "promptDigest": canonical_digest({"prompt": "wave10-quality-safe-v1"}),
            "providerId": "LOCAL_DISABLED",
            "modelId": "NO_EXTERNAL_CALL",
            "modelRevision": "wave10-local-v1",
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
            id=uuid.UUID("30000000-0000-0000-0000-000000000003"),
            version=1,
            synthetic=False,
            reviewed=False,
            identity=identity,
            identity_digest=identity_digest,
            cohort_id="wave10-quality-local",
            cohort_digest=canonical_digest({"cohort": "wave10-local"}),
            population_membership_digest=canonical_digest({"population": "wave10-local"}),
            regression_report_digest=canonical_digest({"regression": "wave10-local"}),
            artifact_manifest_digest=canonical_digest({"manifest": "wave10-local"}),
            live_attestation_digest=None,
            created_by="wave10-local-seed",
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
            provider="LOCAL_DISABLED",
            model="NO_EXTERNAL_CALL",
            prompt_version="wave10-quality-safe-v1",
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
        repository.succeed_execution(job.id, generated)
        session.flush()
        _arm_materialization(session, job)
        application = GenerationApplicationService(session)
        draft = application.plan(job.id)
        parent = cast(dict[str, object], draft.placement["parent"])
        if parent["decision"] == "CREATE_NEW":
            CaseRepository(session).create_work_item(
                work_item_key=str(parent["expectedWorkItemKey"]),
                category_id=uuid.UUID(hex=str(parent["categoryId"]).removeprefix("folder_")),
                summary=str(parent["expectedSummary"]),
                audit=AuditContext(
                    source_type=AuditSourceType.API,
                    actor_label="Wave 10 quality seed",
                    trace_id="wave10-quality-parent",
                    idempotency_key="wave10-quality-parent",
                ),
            )
            draft = application.plan(job.id)
        application.apply(
            automatic_apply_command(draft),
            audit=AuditContext(
                source_type=AuditSourceType.API,
                actor_label="Wave 10 quality seed",
                trace_id="wave10-quality-apply",
                idempotency_key="wave10-quality-apply",
            ),
        )
        root_id = job.id
    engine.dispose()
    return root_id


def main() -> None:
    database_url = os.environ["TC_DATABASE_URL"]
    root_id = _seed_operational_root(database_url)
    principals = json.loads(os.environ["TC_QUALITY_REVIEWER_PRINCIPALS"])
    hashes = json.loads(os.environ["TC_QUALITY_REVIEWER_TOKEN_HASHES"])
    if not isinstance(principals, list) or not isinstance(hashes, list):
        raise RuntimeError("quality-review authority shape mismatch")
    authority = ReviewerAuthority(tuple(zip(map(str, principals), map(str, hashes), strict=True)))
    engine = create_engine(database_url)
    with Session(engine) as session, session.begin():
        candidate = GenerationQualityReviewService(
            session,
            reviewer_authority=authority,
            source_seal_key=os.environ["TC_QUALITY_REVIEW_SOURCE_SEAL_KEY"].encode("utf-8"),
            source_seal_key_id=os.environ["TC_QUALITY_REVIEW_SOURCE_SEAL_KEY_ID"],
        ).import_candidate(
            idempotency_key="wave10-quality-review-import",
            root_job_ids=(root_id,),
            capture_environment="staging",
            report_outputs={
                "json": "artifacts/wave10-quality.json",
                "junit": "artifacts/wave10-quality.xml",
                "markdown": "artifacts/wave10-quality.md",
            },
        )
        if not candidate.candidate_key.startswith("review-candidate:"):
            raise RuntimeError("quality-review seed produced an invalid candidate key")
    engine.dispose()
    print(json.dumps({"status": "SEEDED", "qualityReviewCandidates": 1}, sort_keys=True))


if __name__ == "__main__":
    main()
