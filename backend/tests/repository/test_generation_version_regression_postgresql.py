from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tc_backend.db.models import (
    GenerationBaselineActivation,
    GenerationBaselineApproval,
    GenerationBaselineAuditEvent,
    GenerationBaselineChangeRequest,
    GenerationBaselineRuntime,
    GenerationJob,
    GenerationVersionSet,
    OntologySchema,
)
from tc_backend.generation.quality_gate import canonical_digest
from tc_backend.generation.version_regression import (
    AtomicBaselineActivationService,
    BaselineGovernanceError,
    BaselineRollbackService,
    GenerationBaselineLedgerService,
    TwoPersonBaselineApprovalService,
)

pytestmark = pytest.mark.postgres


def _digest(byte: int) -> str:
    return "sha256:" + f"{byte:02x}" * 32


def _seed_authority(engine: Engine) -> dict[str, object]:
    ids = {name: uuid.uuid4() for name in ("v1", "v2", "v3", "v4", "synthetic")}
    manifests = {name: _digest(40 + index) for index, name in enumerate(ids, 1)}
    identity_digests: dict[str, str] = {}
    with Session(engine) as session, session.begin():
        for version, name in enumerate(ids, 1):
            identity = {"fixture": "T09_POSTGRES_ONLY", "version": version}
            identity_digest = canonical_digest(identity)
            identity_digests[name] = identity_digest
            session.add(
                GenerationVersionSet(
                    id=ids[name],
                    version=version,
                    synthetic=name == "synthetic",
                    reviewed=name != "synthetic",
                    identity=identity,
                    identity_digest=identity_digest,
                    cohort_id="postgres_fixture_cohort",
                    cohort_digest=_digest(60),
                    population_membership_digest=_digest(61),
                    regression_report_digest=_digest(70 + version),
                    artifact_manifest_digest=manifests[name],
                    live_attestation_digest=(
                        None if name == "synthetic" else _digest(80 + version)
                    ),
                    created_by="postgres-fixture",
                )
            )
        session.flush()
        for version, name in enumerate(ids, 1):
            session.add(
                GenerationBaselineChangeRequest(
                    id=uuid.uuid4(),
                    version_set_id=ids[name],
                    requester_principal_id=f"requester-{name}",
                    idempotency_key=f"change-{name}",
                    request_digest=_digest(90 + version),
                    expected_active_version_set_id=(None if name == "v1" else ids["v1"]),
                    expected_ledger_version=0 if name == "v1" else 1,
                    old_identity_digest=identity_digests["v1"],
                    proposed_identity_digest=identity_digests[name],
                    change_request={"fixture": name},
                    regression_input_digest=_digest(100 + version),
                    regression_report={"fixture": name},
                    regression_report_digest=_digest(110 + version),
                    compatibility_matrix={"fixture": name},
                    compatibility_matrix_digest=_digest(120 + version),
                    status="CONTRACT_ONLY" if name == "synthetic" else "CANDIDATE",
                )
            )
        session.flush()
        approvals: dict[str, GenerationBaselineApproval] = {}
        for index, name in enumerate(("v1", "v2", "v3"), 1):
            approval = GenerationBaselineApproval(
                id=uuid.uuid4(),
                version_set_id=ids[name],
                idempotency_key=f"approval-{name}",
                protected_manifest_digest=manifests[name],
                approver_one_principal_id=f"quality-{name}",
                approver_one_role="QUALITY_OWNER",
                approver_one_decision_digest=_digest(130 + index),
                approver_two_principal_id=f"release-{name}",
                approver_two_role="RELEASE_OWNER",
                approver_two_decision_digest=_digest(140 + index),
                approval_digest=_digest(150 + index),
            )
            approvals[name] = approval
            session.add(approval)
        session.flush()
        session.add(
            GenerationBaselineActivation(
                id=uuid.uuid4(),
                version_set_id=ids["v1"],
                previous_version_set_id=None,
                approval_id=approvals["v1"].id,
                principal_id="bootstrap-release-owner",
                idempotency_key="activate-v1-bootstrap",
                request_digest=_digest(160),
                protected_manifest_digest=manifests["v1"],
                expected_ledger_version=0,
                committed_ledger_version=1,
                receipt_digest=_digest(161),
            )
        )
        session.add(
            GenerationBaselineRuntime(
                scope="GLOBAL", ledger_version=1, active_version_set_id=ids["v1"]
            )
        )
    return {
        "ids": ids,
        "manifests": manifests,
        "identityDigests": identity_digests,
    }


def _activate(
    engine: Engine,
    *,
    version_set_id: uuid.UUID,
    manifest: str,
    active: uuid.UUID,
    key: str,
) -> tuple[str, str]:
    try:
        with Session(engine) as session, session.begin():
            receipt = AtomicBaselineActivationService(session).activate(
                version_set_id,
                expected_active_version_set_id=active,
                expected_ledger_version=1,
                protected_manifest_digest=manifest,
                principal_id=f"release-{key}",
                idempotency_key=key,
            )
            return "activated", str(receipt.id)
    except BaselineGovernanceError as error:
        return "rejected", error.code


def test_t09_migration_installs_columns_constraints_and_authority_guards(
    migrated_engine: Engine,
) -> None:
    with migrated_engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        columns = {column["name"] for column in inspect(connection).get_columns("generation_jobs")}
        triggers = set(
            connection.scalars(
                text(
                    "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND "
                    "(tgname LIKE 'trg_generation_baseline_%' OR "
                    "tgname = 'trg_generation_version_sets_immutable' OR "
                    "tgname = 'trg_generation_jobs_version_set_pin')"
                )
            )
        )

    assert {
        "generation_version_sets",
        "generation_baseline_change_requests",
        "generation_baseline_approvals",
        "generation_baseline_runtime",
        "generation_baseline_activations",
        "generation_baseline_rollbacks",
        "generation_baseline_audit_events",
    }.issubset(tables)
    assert {"version_set_id", "version_set_digest"}.issubset(columns)
    assert {
        "trg_generation_version_sets_immutable",
        "trg_generation_baseline_approvals_validate",
        "trg_generation_baseline_activations_validate",
        "trg_generation_baseline_runtime_guard",
        "trg_generation_jobs_version_set_pin",
    }.issubset(triggers)


def test_two_person_approval_and_postgresql_immutability_fail_closed(
    migrated_engine: Engine,
) -> None:
    seeded = _seed_authority(migrated_engine)
    ids = seeded["ids"]
    manifests = seeded["manifests"]
    assert isinstance(ids, dict) and isinstance(manifests, dict)

    with Session(migrated_engine) as session, session.begin():
        service = TwoPersonBaselineApprovalService(session)
        duplicate = [
            {"principalId": "same", "role": "QUALITY_OWNER", "decisionDigest": _digest(180)},
            {"principalId": "same", "role": "RELEASE_OWNER", "decisionDigest": _digest(181)},
        ]
        with pytest.raises(BaselineGovernanceError, match="TWO_PERSON_APPROVAL_INVALID"):
            service.approve(
                ids["v4"],
                approvals=duplicate,
                protected_manifest_digest=manifests["v4"],
                idempotency_key="approve-v4-duplicate",
            )
        requester = [
            {"principalId": "requester-v4", "role": "QUALITY_OWNER", "decisionDigest": _digest(182)},
            {"principalId": "release-v4", "role": "RELEASE_OWNER", "decisionDigest": _digest(183)},
        ]
        with pytest.raises(BaselineGovernanceError, match="TWO_PERSON_APPROVAL_INVALID"):
            service.approve(
                ids["v4"],
                approvals=requester,
                protected_manifest_digest=manifests["v4"],
                idempotency_key="approve-v4-self",
            )
        with pytest.raises(BaselineGovernanceError, match="BASELINE_NOT_APPROVABLE"):
            service.approve(
                ids["synthetic"],
                approvals=duplicate,
                protected_manifest_digest=manifests["synthetic"],
                idempotency_key="approve-synthetic",
            )
        with pytest.raises(BaselineGovernanceError, match="PROTECTED_MANIFEST_MISMATCH"):
            service.approve(
                ids["v4"],
                approvals=[
                    {"principalId": "quality-v4", "role": "QUALITY_OWNER", "decisionDigest": _digest(184)},
                    {"principalId": "release-v4", "role": "RELEASE_OWNER", "decisionDigest": _digest(185)},
                ],
                protected_manifest_digest=_digest(186),
                idempotency_key="approve-v4-forged-manifest",
            )
        valid = [
            {"principalId": "quality-v4", "role": "QUALITY_OWNER", "decisionDigest": _digest(184)},
            {"principalId": "release-v4", "role": "RELEASE_OWNER", "decisionDigest": _digest(185)},
        ]
        approval = service.approve(
            ids["v4"],
            approvals=valid,
            protected_manifest_digest=manifests["v4"],
            idempotency_key="approve-v4-valid",
        )
        assert service.approve(
            ids["v4"],
            approvals=valid,
            protected_manifest_digest=manifests["v4"],
            idempotency_key="approve-v4-valid",
        ).id == approval.id

    for statement in (
        "UPDATE generation_version_sets SET identity = jsonb_build_object('tampered', true) WHERE id = :id",
        "DELETE FROM generation_version_sets WHERE id = :id",
        "DELETE FROM generation_baseline_approvals WHERE version_set_id = :id",
        "DELETE FROM generation_baseline_runtime WHERE scope = 'GLOBAL'",
    ):
        with pytest.raises(DBAPIError):
            with migrated_engine.begin() as connection:
                connection.execute(text(statement), {"id": ids["v1"]})


def test_concurrent_activation_has_one_winner_replay_and_atomic_fault_rollback(
    migrated_engine: Engine,
) -> None:
    seeded = _seed_authority(migrated_engine)
    ids = seeded["ids"]
    manifests = seeded["manifests"]
    assert isinstance(ids, dict) and isinstance(manifests, dict)
    barrier = Barrier(2)

    def contender(name: str) -> tuple[str, str]:
        barrier.wait(timeout=10)
        return _activate(
            migrated_engine,
            version_set_id=ids[name],
            manifest=manifests[name],
            active=ids["v1"],
            key=f"activate-{name}",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(contender, ("v2", "v3")))

    assert sorted(result[0] for result in results) == ["activated", "rejected"]
    assert [result[1] for result in results if result[0] == "rejected"] == ["STALE_BASELINE"]
    winner = "v2" if results[0][0] == "activated" else "v3"
    loser = "v3" if winner == "v2" else "v2"
    winner_receipt = next(result[1] for result in results if result[0] == "activated")

    replay = _activate(
        migrated_engine,
        version_set_id=ids[winner],
        manifest=manifests[winner],
        active=ids["v1"],
        key=f"activate-{winner}",
    )
    assert replay == ("activated", winner_receipt)

    class FaultingActivationService(AtomicBaselineActivationService):
        def _event(self, *args: object, **kwargs: object) -> GenerationBaselineAuditEvent:
            raise RuntimeError("fixture fault after activation flush")

    with pytest.raises(RuntimeError, match="fixture fault"):
        with Session(migrated_engine) as session, session.begin():
            FaultingActivationService(session).activate(
                ids[loser],
                expected_active_version_set_id=ids[winner],
                expected_ledger_version=2,
                protected_manifest_digest=manifests[loser],
                principal_id="fault-fixture",
                idempotency_key="activate-fault-fixture",
            )
    with Session(migrated_engine) as session:
        runtime = session.get(GenerationBaselineRuntime, "GLOBAL")
        assert runtime is not None
        assert (runtime.active_version_set_id, runtime.ledger_version) == (ids[winner], 2)
        assert session.scalar(
            select(GenerationBaselineActivation).where(
                GenerationBaselineActivation.idempotency_key == "activate-fault-fixture"
            )
        ) is None


def _seed_slack_job_lineage(
    session: Session, *, root_id: uuid.UUID, retry_id: uuid.UUID, version_set_id: uuid.UUID, digest: str
) -> tuple[GenerationJob, GenerationJob]:
    workspace_id = "00000000-0000-7000-8000-000000000100"
    event_id = uuid.uuid4()
    work_item_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO slack_channel_allowlist "
            "(workspace_id, slack_workspace_id, channel_id) "
            "VALUES (:workspace, 'T-T09', 'C-T09')"
        ),
        {"workspace": workspace_id},
    )
    session.execute(
        text(
            "INSERT INTO slack_events "
            "(id, workspace_id, slack_workspace_id, channel_id, event_id, event_type, "
            "event_ts, event_ts_key, payload) VALUES "
            "(:id, :workspace, 'T-T09', 'C-T09', 'Ev-T09', 'ROOT', "
            "'1724000000.000001', 1724000000000001, '{}'::jsonb)"
        ),
        {"id": event_id, "workspace": workspace_id},
    )
    session.execute(
        text(
            "INSERT INTO slack_work_items "
            "(id, workspace_id, slack_workspace_id, channel_id, message_ts, "
            "message_ts_key, root_seen, source_version, created_event_id) VALUES "
            "(:id, :workspace, 'T-T09', 'C-T09', '1724000000.000001', "
            "1724000000000001, true, 1, :event)"
        ),
        {"id": work_item_id, "workspace": workspace_id, "event": event_id},
    )
    ontology_id = session.scalar(select(OntologySchema.id).where(OntologySchema.active))
    assert ontology_id is not None
    root = GenerationJob(
        id=root_id,
        slack_work_item_id=work_item_id,
        source_version=1,
        generation_version=1,
        generation_trigger="AUTOMATIC",
        provider="fixture-provider",
        model="fixture-model",
        prompt_version="fixture-v1",
        schema_version="1.0.0",
        ontology_schema_id=ontology_id,
        root_job_id=root_id,
        retry_of_job_id=None,
        attempt_number=1,
        version_set_id=version_set_id,
        version_set_digest=digest,
    )
    retry = GenerationJob(
        id=retry_id,
        slack_work_item_id=work_item_id,
        source_version=1,
        generation_version=2,
        generation_trigger="AUTOMATIC",
        provider="fixture-provider",
        model="fixture-model",
        prompt_version="fixture-v1",
        schema_version="1.0.0",
        ontology_schema_id=ontology_id,
        root_job_id=root_id,
        retry_of_job_id=root_id,
        attempt_number=2,
    )
    session.add_all((root, retry))
    session.flush()
    return root, retry


def test_in_flight_and_retry_pins_survive_activation_and_rollback_is_reproduced(
    migrated_engine: Engine,
) -> None:
    seeded = _seed_authority(migrated_engine)
    ids = seeded["ids"]
    manifests = seeded["manifests"]
    identity_digests = seeded["identityDigests"]
    assert isinstance(ids, dict) and isinstance(manifests, dict)
    assert isinstance(identity_digests, dict)
    assert _activate(
        migrated_engine,
        version_set_id=ids["v2"],
        manifest=manifests["v2"],
        active=ids["v1"],
        key="activate-v2-pin-test",
    )[0] == "activated"

    root_id, retry_id = uuid.uuid4(), uuid.uuid4()
    with Session(migrated_engine) as session, session.begin():
        root, retry = _seed_slack_job_lineage(
            session,
            root_id=root_id,
            retry_id=retry_id,
            version_set_id=ids["v1"],
            digest=identity_digests["v1"],
        )
        ledger = GenerationBaselineLedgerService(session)
        assert ledger.assert_current_job_pin(root).id == ids["v1"]
        assert ledger.capture_job_pin(retry).id == ids["v1"]
        assert (retry.version_set_id, retry.version_set_digest) == (
            ids["v1"],
            identity_digests["v1"],
        )

    with pytest.raises(DBAPIError):
        with migrated_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE generation_jobs SET version_set_id = :id, "
                    "version_set_digest = :digest WHERE id = :job"
                ),
                {
                    "id": ids["v2"],
                    "digest": identity_digests["v2"],
                    "job": retry_id,
                },
            )

    report = {
        "reproductionStatus": "VERIFIED",
        "targetIdentityDigest": identity_digests["v1"],
        "targetArtifactManifestDigest": manifests["v1"],
        "targetRegressionReportDigest": _digest(71),
        "targetCohortDigest": _digest(60),
        "targetPopulationMembershipDigest": _digest(61),
        "compatibilityPassed": True,
        "schemaMigrationPassed": True,
        "embeddingMigrationPassed": True,
        "restoredQualityMetrics": {
            "evidenceCoverage": 1,
            "retiredKnowledgeMisuseCount": 0,
            "duplicateCount": 0,
            "unresolvedAuthorityCount": 0,
            "provenanceFailureCount": 0,
            "invalidOrBlankResultCount": 0,
            "orderingFailureCount": 0,
        },
    }
    reproduction_digest = canonical_digest(report)
    with Session(migrated_engine) as session, session.begin():
        service = BaselineRollbackService(session)
        with pytest.raises(
            BaselineGovernanceError, match="ROLLBACK_TARGET_IS_FAILED_BASELINE"
        ):
            service.rollback(
                target_version_set_id=ids["v2"],
                expected_failed_version_set_id=ids["v2"],
                expected_ledger_version=2,
                target_artifact_manifest_digest=manifests["v2"],
                reproduction_report=report,
                reproduction_artifact_digest=reproduction_digest,
                principal_id="rollback-owner",
                idempotency_key="rollback-self",
            )
        with pytest.raises(
            BaselineGovernanceError, match="ROLLBACK_TARGET_NOT_ACTIVATED"
        ):
            service.rollback(
                target_version_set_id=ids["v4"],
                expected_failed_version_set_id=ids["v2"],
                expected_ledger_version=2,
                target_artifact_manifest_digest=manifests["v4"],
                reproduction_report=report,
                reproduction_artifact_digest=reproduction_digest,
                principal_id="rollback-owner",
                idempotency_key="rollback-never-activated",
            )
        with pytest.raises(
            BaselineGovernanceError, match="ROLLBACK_REPRODUCTION_DIGEST_MISMATCH"
        ):
            service.rollback(
                target_version_set_id=ids["v1"],
                expected_failed_version_set_id=ids["v2"],
                expected_ledger_version=2,
                target_artifact_manifest_digest=manifests["v1"],
                reproduction_report=report,
                reproduction_artifact_digest=_digest(200),
                principal_id="rollback-owner",
                idempotency_key="rollback-forged-proof",
            )

    class FaultingRollbackService(BaselineRollbackService):
        def _event(self, *args: object, **kwargs: object) -> GenerationBaselineAuditEvent:
            raise RuntimeError("fixture fault after rollback flush")

    with pytest.raises(RuntimeError, match="fixture fault"):
        with Session(migrated_engine) as session, session.begin():
            FaultingRollbackService(session).rollback(
                target_version_set_id=ids["v1"],
                expected_failed_version_set_id=ids["v2"],
                expected_ledger_version=2,
                target_artifact_manifest_digest=manifests["v1"],
                reproduction_report=report,
                reproduction_artifact_digest=reproduction_digest,
                principal_id="rollback-owner",
                idempotency_key="rollback-fault-fixture",
            )
    with Session(migrated_engine) as session:
        runtime = session.get(GenerationBaselineRuntime, "GLOBAL")
        assert runtime is not None
        assert (runtime.active_version_set_id, runtime.ledger_version) == (ids["v2"], 2)

    with Session(migrated_engine) as session, session.begin():
        service = BaselineRollbackService(session)
        receipt = service.rollback(
            target_version_set_id=ids["v1"],
            expected_failed_version_set_id=ids["v2"],
            expected_ledger_version=2,
            target_artifact_manifest_digest=manifests["v1"],
            reproduction_report=report,
            reproduction_artifact_digest=reproduction_digest,
            principal_id="rollback-owner",
            idempotency_key="rollback-v1",
        )
        assert receipt.committed_ledger_version == 3
        receipt_id = receipt.id

    with Session(migrated_engine) as session, session.begin():
        replay = BaselineRollbackService(session).rollback(
            target_version_set_id=ids["v1"],
            expected_failed_version_set_id=ids["v2"],
            expected_ledger_version=2,
            target_artifact_manifest_digest=manifests["v1"],
            reproduction_report=report,
            reproduction_artifact_digest=reproduction_digest,
            principal_id="rollback-owner",
            idempotency_key="rollback-v1",
        )
        assert replay.id == receipt_id
        ledger_versions = list(
            session.execute(
                select(
                    GenerationBaselineAuditEvent.event_type,
                    GenerationBaselineAuditEvent.expected_ledger_version,
                    GenerationBaselineAuditEvent.committed_ledger_version,
                ).where(
                    GenerationBaselineAuditEvent.event_type.in_(
                        ("BASELINE_ACTIVATED", "ROLLBACK_ACTIVATED")
                    )
                )
            ).tuples()
        )
        assert ("BASELINE_ACTIVATED", 1, 2) in ledger_versions
        assert ("ROLLBACK_ACTIVATED", 2, 3) in ledger_versions
