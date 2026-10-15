from __future__ import annotations

import copy
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, func, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tc_api.main import create_app
from tc_backend.db.models import (
    ApiIdempotencyReceipt,
    AuditLog,
    GenerationApplyOutcome,
    OutboxEvent,
    TestCaseGenerationManagementEvent as ManagementEvent,
    TestCaseVersion as LedgerTestCaseVersion,
)
from tc_backend.repositories.generation_repository import GenerationRepository
from test_generation_apply_api_postgresql import (
    ApplySeed,
    _apply_request,
    _seed_apply_job,
)

pytestmark = pytest.mark.postgres

_BACKEND_ROOT = Path(__file__).resolve().parents[3] / "backend"

_MANAGEMENT_COLUMNS = {
    "generation_managed",
    "generation_lineage_id",
    "generation_created_outcome_id",
    "generation_baseline_version_id",
    "generation_latest_version_id",
    "generation_released_at",
    "generation_release_reason",
    "generation_release_actor",
    "generation_release_mutation",
    "generation_released_from_version_id",
    "generation_released_by_version_id",
}


def _client(engine: Engine, *, raise_server_exceptions: bool = False) -> TestClient:
    return TestClient(
        create_app(validate_database=False, database_engine=engine),
        raise_server_exceptions=raise_server_exceptions,
    )


def _apply_generation(
    engine: Engine,
    *,
    with_human_reuse: bool,
    key: str,
) -> tuple[ApplySeed, dict[str, Any]]:
    seed = _seed_apply_job(engine, with_human_reuse=with_human_reuse)
    with _client(engine) as client:
        response = client.post(
            f"/api/generation-jobs/generation_job_{seed.job_id.hex}/apply",
            headers={"Idempotency-Key": key},
            json=_apply_request(seed, key=key),
        )
    assert response.status_code == 201, response.text
    return seed, response.json()


def _case_id(outcome: dict[str, Any]) -> str:
    return str(outcome["case"]["id"])


def _created_cases(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in receipt["cases"] if row["outcome"] == "CREATED"]


def _detail(engine: Engine, case_id: str, *, include_deleted: bool = False) -> dict[str, Any]:
    with _client(engine) as client:
        response = client.get(
            f"/api/test-cases/{case_id}",
            params={"includeDeleted": str(include_deleted).lower()},
        )
    assert response.status_code == 200, response.text
    return response.json()


def _update_payload(
    detail: dict[str, Any],
    *,
    change_reason: str,
    **changes: Any,
) -> dict[str, Any]:
    current = detail["currentVersion"]
    payload: dict[str, Any] = {
        "baseVersion": detail["version"],
        "summary": current["summary"],
        "precondition": current["precondition"],
        "expectedResultMode": current["expectedResultMode"],
        "priority": current["priority"],
        "labels": current["labels"],
        "steps": current["steps"],
        "source": current["source"],
        "changeReason": change_reason,
    }
    if "ownerLabel" in current:
        payload["ownerLabel"] = current["ownerLabel"]
    if "caseExpectedResult" in current:
        payload["caseExpectedResult"] = current["caseExpectedResult"]
    if current["expectedResultMode"] == "CASE":
        payload["generationReason"] = "Grounded generated case retained for T04 mutation."
    payload.update(changes)
    return payload


def _row_counts(engine: Engine, case_id: str) -> dict[str, int]:
    decoded = uuid.UUID(case_id.removeprefix("case_"))
    with Session(engine) as session:
        return {
            "versions": int(
                session.scalar(
                    select(func.count())
                    .select_from(LedgerTestCaseVersion)
                    .where(LedgerTestCaseVersion.test_case_id == decoded)
                )
                or 0
            ),
            "audits": int(
                session.scalar(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.entity_type == "TEST_CASE", AuditLog.entity_id == decoded)
                )
                or 0
            ),
            "outbox": int(
                session.scalar(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(
                        OutboxEvent.aggregate_type == "TEST_CASE",
                        OutboxEvent.aggregate_id == decoded,
                    )
                )
                or 0
            ),
            "managementEvents": int(
                session.scalar(
                    select(func.count())
                    .select_from(ManagementEvent)
                    .where(ManagementEvent.test_case_id == decoded)
                )
                or 0
            ),
        }


def _create_successor_job(engine: Engine, seed: ApplySeed) -> uuid.UUID:
    with Session(engine) as session, session.begin():
        repository = GenerationRepository(session)
        persisted = repository.create_next_job_with_context(
            slack_work_item_id=seed.slack_work_item_id
        )
        repository.begin_execution(persisted.job.id)
        repository.succeed_execution(persisted.job.id, copy.deepcopy(seed.result))
        return persisted.job.id


def _apply_from_plan(client: TestClient, job_id: uuid.UUID, *, key: str) -> Any:
    encoded = f"generation_job_{job_id.hex}"
    plan = client.get(f"/api/generation-jobs/{encoded}/apply-plan")
    assert plan.status_code == 200, plan.text
    body = plan.json()
    return client.post(
        f"/api/generation-jobs/{encoded}/apply",
        headers={"Idempotency-Key": key},
        json={
            "idempotencyKey": key,
            "expectedContextDigest": body["expectedContextDigest"],
            "expectedResultDigest": body["expectedResultDigest"],
            "placement": body["placement"],
        },
    )


def test_pe_se2_t04_schema_installs_created_only_one_way_management_guards(
    clean_api_database: Engine,
) -> None:
    """RED: T03 head has no management state or immutable release guard."""

    inspector = inspect(clean_api_database)
    columns = {row["name"]: row for row in inspector.get_columns("test_cases")}
    assert _MANAGEMENT_COLUMNS <= columns.keys(), (
        "PE-SE2-T04 requires complete generated lineage and release fields on test_cases"
    )
    assert columns["generation_managed"]["nullable"] is False
    assert str(columns["generation_release_actor"]["type"]) == "JSONB"
    receipt_columns = {
        row["name"]: row
        for row in inspector.get_columns("api_idempotency_receipts")
    }
    assert {
        "workspace_id",
        "idempotency_key",
        "operation",
        "request_hash",
        "response",
        "created_at",
    } == receipt_columns.keys()

    unique_columns = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("test_cases")
    }
    assert ("generation_lineage_id",) in unique_columns
    assert ("generation_created_outcome_id",) in unique_columns

    with clean_api_database.connect() as connection:
        triggers = set(
            connection.scalars(
                text(
                    "SELECT tgname FROM pg_trigger "
                    "WHERE tgrelid = 'test_cases'::regclass AND NOT tgisinternal"
                )
            )
        )
    assert {
        "trg_test_cases_generation_management_shape",
        "trg_test_cases_generation_management_links",
        "trg_test_cases_generation_management_one_way",
    } <= triggers
    with clean_api_database.connect() as connection:
        receipt_triggers = set(
            connection.scalars(
                text(
                    "SELECT tgname FROM pg_trigger WHERE tgrelid = "
                    "'api_idempotency_receipts'::regclass AND NOT tgisinternal"
                )
            )
        )
    assert receipt_triggers == {"trg_api_idempotency_receipts_immutable"}
    with clean_api_database.connect() as connection:
        event_triggers = set(
            connection.scalars(
                text(
                    "SELECT tgname FROM pg_trigger WHERE tgrelid = "
                    "'test_case_generation_management_events'::regclass "
                    "AND NOT tgisinternal"
                )
            )
        )
    assert event_triggers == {
        "trg_test_case_generation_management_events_immutable",
        "trg_test_case_generation_management_events_links",
    }


def test_pe_se2_t04_only_created_apply_initializes_management_and_reuse_never_promotes(
    clean_api_database: Engine,
) -> None:
    """CREATED is the authority; exact human REUSED and direct SLACK_AI are not."""

    seed, receipt = _apply_generation(
        clean_api_database,
        with_human_reuse=True,
        key="pe-se2-t04-created-reused-001",
    )
    assert receipt["cases"][0]["outcome"] == "REUSED"
    reused_id = _case_id(receipt["cases"][0])
    reused = _detail(clean_api_database, reused_id)
    assert reused["generationManagement"] == {"managed": False}

    created_outcome = receipt["cases"][1]
    created_id = _case_id(created_outcome)
    created = _detail(clean_api_database, created_id)
    management = created["generationManagement"]
    version_id = created["currentVersion"]["id"]
    decoded = uuid.UUID(created_id.removeprefix("case_"))
    with Session(clean_api_database) as session:
        outcome_id = session.scalar(
            select(GenerationApplyOutcome.id).where(
                GenerationApplyOutcome.test_case_id == decoded,
                GenerationApplyOutcome.outcome == "CREATED",
            )
        )
    assert outcome_id is not None
    assert management == {
        "managed": True,
        "lineageId": management["lineageId"],
        "createdOutcomeId": f"generation_apply_outcome_{outcome_id.hex}",
        "baselineGeneratedVersionId": version_id,
        "latestGeneratedVersionId": version_id,
    }
    assert management["lineageId"].startswith("generation_lineage_")

    with _client(clean_api_database) as client:
        replay = client.post(
            f"/api/generation-jobs/generation_job_{seed.job_id.hex}/apply",
            headers={"Idempotency-Key": "pe-se2-t04-created-reused-001"},
            json=_apply_request(seed, key="pe-se2-t04-created-reused-001"),
        )
    assert replay.status_code == 201, replay.text
    assert replay.json() == receipt
    with Session(clean_api_database) as session:
        assert (
            session.scalar(
                select(func.count()).select_from(ManagementEvent)
            )
            == len(_created_cases(receipt))
        )

    direct_payload = {
        "workItemId": created["workItemId"],
        "status": "ACTIVE",
        "summary": "Caller-claimed worker case is not managed",
        "precondition": "A caller controls only request metadata.",
        "expectedResultMode": "CASE",
        "caseExpectedResult": "The case stays unmanaged.",
        "priority": "MEDIUM",
        "labels": ["spoof-proof"],
        "steps": [{"sequence": 1, "action": "Submit a direct public create."}],
        "source": {
            "type": "WORKER",
            "generationJobId": f"generation_job_{seed.job_id.hex}",
            "evidenceNodeIds": ["knowledge_node_00000000000070008000000000000001"],
        },
        "generationReason": "The request claims generation provenance.",
    }
    with _client(clean_api_database) as client:
        direct = client.post("/api/test-cases", json=direct_payload)
    assert direct.status_code == 201, direct.text
    assert direct.json()["generationManagement"] == {"managed": False}
    direct_case_id = uuid.UUID(direct.json()["id"].removeprefix("case_"))
    direct_version_id = uuid.UUID(
        direct.json()["currentVersion"]["id"].removeprefix("case_version_")
    )
    forged_outcome_id = uuid.uuid4()
    forged_lineage_id = uuid.uuid4()
    with pytest.raises(DBAPIError, match="generation management"):
        with clean_api_database.begin() as connection:
            apply_id = connection.scalar(
                text(
                    "SELECT id FROM generation_applies WHERE generation_job_id = :job_id"
                ),
                {"job_id": seed.job_id},
            )
            assert apply_id is not None
            connection.execute(
                text(
                    "INSERT INTO generation_apply_outcomes "
                    "(id, generation_apply_id, generated_case_index, exact_content_key, "
                    "outcome, test_case_id, test_case_version_id, match_reason, preserved, "
                    "snapshot) VALUES (:outcome_id, :apply_id, 9, :exact_key, 'CREATED', "
                    ":case_id, :version_id, NULL, false, '{}'::jsonb)"
                ),
                {
                    "outcome_id": forged_outcome_id,
                    "apply_id": apply_id,
                    "exact_key": "a" * 64,
                    "case_id": direct_case_id,
                    "version_id": direct_version_id,
                },
            )
            connection.execute(
                text(
                    "SELECT set_config("
                    "'tc.generation_management_operation', 'initialize', true)"
                )
            )
            connection.execute(
                text(
                    "UPDATE test_cases SET generation_managed = true, "
                    "generation_lineage_id = :lineage_id, "
                    "generation_created_outcome_id = :outcome_id, "
                    "generation_baseline_version_id = :version_id, "
                    "generation_latest_version_id = :version_id WHERE id = :case_id"
                ),
                {
                    "lineage_id": forged_lineage_id,
                    "outcome_id": forged_outcome_id,
                    "version_id": direct_version_id,
                    "case_id": direct_case_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO test_case_generation_management_events "
                    "(workspace_id, test_case_id, event_type, lineage_id, "
                    "created_outcome_id, baseline_generated_version_id, "
                    "latest_generated_version_id) VALUES "
                    "('00000000-0000-7000-8000-000000000100'::uuid, :case_id, "
                    "'INITIALIZED', :lineage_id, :outcome_id, :version_id, :version_id)"
                ),
                {
                    "case_id": direct_case_id,
                    "lineage_id": forged_lineage_id,
                    "outcome_id": forged_outcome_id,
                    "version_id": direct_version_id,
                },
            )
    with _client(clean_api_database) as client:
        direct_deleted = client.request(
            "DELETE",
            f"/api/test-cases/{direct.json()['id']}",
            json={
                "baseResourceVersion": direct.json()["resourceVersion"],
                "reason": "Legacy-shaped SLACK_AI is still human-owned.",
                "source": {"type": "SYSTEM"},
            },
        )
    assert direct_deleted.status_code == 200, direct_deleted.text
    assert direct_deleted.json()["testCase"]["generationManagement"] == {"managed": False}


def test_pe_se2_t04_management_events_reject_spoofing_update_and_delete(
    clean_api_database: Engine,
) -> None:
    """The event ledger is append-only and every inserted receipt must match case state."""

    _, receipt = _apply_generation(
        clean_api_database,
        with_human_reuse=False,
        key="pe-se2-t04-event-ledger-seed-001",
    )
    case_id = uuid.UUID(_case_id(_created_cases(receipt)[0]).removeprefix("case_"))
    with clean_api_database.connect() as connection:
        case_row = connection.execute(
            text(
                "SELECT workspace_id, generation_lineage_id, "
                "generation_created_outcome_id, generation_baseline_version_id, "
                "generation_latest_version_id FROM test_cases WHERE id = :case_id"
            ),
            {"case_id": case_id},
        ).mappings().one()
        initialized_event_id = connection.scalar(
            text(
                "SELECT id FROM test_case_generation_management_events "
                "WHERE test_case_id = :case_id AND event_type = 'INITIALIZED'"
            ),
            {"case_id": case_id},
        )
    assert initialized_event_id is not None

    with pytest.raises(DBAPIError, match="append-only"):
        with clean_api_database.begin() as connection:
            connection.execute(
                text(
                    "UPDATE test_case_generation_management_events "
                    "SET occurred_at = occurred_at + interval '1 second' WHERE id = :event_id"
                ),
                {"event_id": initialized_event_id},
            )
    with pytest.raises(DBAPIError, match="append-only"):
        with clean_api_database.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM test_case_generation_management_events WHERE id = :event_id"
                ),
                {"event_id": initialized_event_id},
            )
    with pytest.raises(DBAPIError, match="generation management release event is invalid"):
        with clean_api_database.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO test_case_generation_management_events "
                    "(workspace_id, test_case_id, event_type, lineage_id, "
                    "created_outcome_id, baseline_generated_version_id, "
                    "latest_generated_version_id, released_at, release_reason, "
                    "release_actor, release_mutation, released_from_version_id, occurred_at) "
                    "VALUES (:workspace_id, :case_id, 'RELEASED', :lineage_id, "
                    ":outcome_id, :baseline_id, :latest_id, now(), 'forged release', "
                    "'{\"type\":\"API\"}'::jsonb, 'SOFT_DELETE', :latest_id, now())"
                ),
                {
                    "workspace_id": case_row["workspace_id"],
                    "case_id": case_id,
                    "lineage_id": case_row["generation_lineage_id"],
                    "outcome_id": case_row["generation_created_outcome_id"],
                    "baseline_id": case_row["generation_baseline_version_id"],
                    "latest_id": case_row["generation_latest_version_id"],
                },
            )


def test_pe_se2_t04_every_semantic_content_dimension_releases_management(
    clean_api_database: Engine,
) -> None:
    """Summary, priority/labels, results, and step add/delete/reorder are human meaning."""

    _, receipt = _apply_generation(
        clean_api_database,
        with_human_reuse=False,
        key="pe-se2-t04-semantic-matrix-seed-001",
    )
    case_ids = [_case_id(row) for row in _created_cases(receipt)]
    before = [_detail(clean_api_database, case_id) for case_id in case_ids]
    reversed_plus_added = list(reversed(copy.deepcopy(before[4]["currentVersion"]["steps"])))
    reversed_plus_added.append(
        {
            "sequence": len(reversed_plus_added) + 1,
            "action": "Execute the human-added final verification step.",
        }
    )
    for sequence, step in enumerate(reversed_plus_added, start=1):
        step["sequence"] = sequence
    deleted_and_changed = copy.deepcopy(before[3]["currentVersion"]["steps"][:1])
    deleted_and_changed[0]["action"] = "Execute the human-corrected single step."
    mutations = (
        {
            "summary": "Human-corrected generated summary",
            "status": "DEPRECATED",
            "ownerLabel": "QA owner",
        },
        {"priority": "CRITICAL", "labels": ["human", "critical"]},
        {
            "precondition": "A human-confirmed precondition applies.",
            "caseExpectedResult": "A human-confirmed result is observed.",
        },
        {"steps": deleted_and_changed},
        {"steps": reversed_plus_added},
    )

    with _client(clean_api_database) as client:
        for index, (case_id, original, changes) in enumerate(
            zip(case_ids, before, mutations, strict=True)
        ):
            response = client.patch(
                f"/api/test-cases/{case_id}",
                json=_update_payload(
                    original,
                    change_reason=f"Human semantic matrix mutation {index}.",
                    **changes,
                ),
            )
            assert response.status_code == 200, response.text
            assert response.json()["version"] == 2
            assert response.json()["generationManagement"]["managed"] is False
            assert (
                response.json()["generationManagement"]["releaseMutation"]
                == "CONTENT_VERSION"
            )


def test_pe_se2_t04_human_semantic_patch_releases_atomically_and_replays_once(
    clean_api_database: Engine,
) -> None:
    """A public WORKER-spoofed PATCH is still an attributable human/API release."""

    _, receipt = _apply_generation(
        clean_api_database,
        with_human_reuse=False,
        key="pe-se2-t04-human-release-seed-001",
    )
    case_id = _case_id(_created_cases(receipt)[0])
    before = _detail(clean_api_database, case_id)
    baseline_id = before["currentVersion"]["id"]
    payload = _update_payload(
        before,
        change_reason="QA corrected the generated priority.",
        priority="CRITICAL",
    )
    key = "pe-se2-t04-human-release-001"
    headers = {
        "Idempotency-Key": key,
        "X-Actor-Label": "QA Kim",
        "X-External-Actor-Id": "qa-42",
    }
    with _client(clean_api_database) as client:
        changed = client.patch(f"/api/test-cases/{case_id}", json=payload, headers=headers)
        assert changed.status_code == 200, changed.text
        resource = changed.json()
        management = resource["generationManagement"]
        assert resource["version"] == 2
        assert management["managed"] is False
        assert management["baselineGeneratedVersionId"] == baseline_id
        assert management["latestGeneratedVersionId"] == baseline_id
        assert management["releasedFromVersionId"] == baseline_id
        assert management["releasedByVersionId"] == resource["currentVersion"]["id"]
        assert management["releaseReason"] == payload["changeReason"]
        assert management["releaseMutation"] == "CONTENT_VERSION"
        assert management["releaseActor"] == {
            "type": "API",
            "actorLabel": "QA Kim",
            "externalActorId": "qa-42",
        }
        assert management["releasedAt"] is not None

        committed_counts = _row_counts(clean_api_database, case_id)
        replay = client.patch(f"/api/test-cases/{case_id}", json=payload, headers=headers)
        assert replay.status_code == 200
        assert replay.json() == resource
        assert _row_counts(clean_api_database, case_id) == committed_counts

        stale_payload = {**payload, "summary": resource["currentVersion"]["summary"]}
        stale = client.patch(f"/api/test-cases/{case_id}", json=stale_payload)
        assert stale.status_code == 409
        assert stale.json()["code"] == "VERSION_CONFLICT"
        assert _row_counts(clean_api_database, case_id) == committed_counts

    decoded = uuid.UUID(case_id.removeprefix("case_"))
    with Session(clean_api_database) as session:
        release_audits = session.scalars(
            select(AuditLog).where(
                AuditLog.entity_type == "TEST_CASE",
                AuditLog.entity_id == decoded,
                AuditLog.action == "GENERATION_MANAGEMENT_RELEASED",
            )
        ).all()
    assert len(release_audits) == 1
    assert release_audits[0].source_type == "API"
    assert release_audits[0].actor_label == "QA Kim"
    assert release_audits[0].external_actor_id == "qa-42"
    assert release_audits[0].reason == payload["changeReason"]
    assert release_audits[0].before is not None
    assert release_audits[0].after is not None
    assert release_audits[0].before["generationManaged"] is True
    assert release_audits[0].after["generationManaged"] is False
    assert (
        release_audits[0].before["generationLineageId"]
        == release_audits[0].after["generationLineageId"]
    )
    with Session(clean_api_database) as session:
        events = session.scalars(
            select(ManagementEvent)
            .where(ManagementEvent.test_case_id == decoded)
            .order_by(ManagementEvent.occurred_at)
        ).all()
    assert [event.event_type for event in events] == ["INITIALIZED", "RELEASED"]
    assert events[1].release_actor == {
        "type": "API",
        "actorLabel": "QA Kim",
        "externalActorId": "qa-42",
    }
    assert events[1].released_from_version_id == events[0].latest_generated_version_id


def test_pe_se2_t04_semantic_noop_and_same_parent_reorder_keep_management(
    clean_api_database: Engine,
) -> None:
    """Content equality is a total no-op; presentation-only reorder is not release."""

    _, receipt = _apply_generation(
        clean_api_database,
        with_human_reuse=False,
        key="pe-se2-t04-noop-seed-001",
    )
    case_id = _case_id(_created_cases(receipt)[0])
    before = _detail(clean_api_database, case_id)
    before_counts = _row_counts(clean_api_database, case_id)
    no_op_payload = _update_payload(
        before,
        change_reason="No semantic change after review.",
    )

    update_key = "pe-se2-t04-noop-update-001"
    move_key = "pe-se2-t04-noop-move-001"
    with _client(clean_api_database) as client:
        no_op = client.patch(
            f"/api/test-cases/{case_id}",
            headers={"Idempotency-Key": update_key},
            json=no_op_payload,
        )
        assert no_op.status_code == 200, no_op.text
        assert no_op.json() == before
        assert _row_counts(clean_api_database, case_id) == before_counts
        replayed_no_op = client.patch(
            f"/api/test-cases/{case_id}",
            headers={"Idempotency-Key": update_key},
            json=no_op_payload,
        )
        assert replayed_no_op.status_code == 200, replayed_no_op.text
        assert replayed_no_op.json() == before
        assert _row_counts(clean_api_database, case_id) == before_counts

        exact_move = client.post(
            f"/api/test-cases/{case_id}/move",
            headers={"Idempotency-Key": move_key},
            json={
                "baseResourceVersion": before["resourceVersion"],
                "targetWorkItemId": before["workItemId"],
                "targetSortOrder": before["sortOrder"],
                "changeReason": "Submitting the exact current presentation position.",
                "source": before["currentVersion"]["source"],
            },
        )
        assert exact_move.status_code == 200, exact_move.text
        assert exact_move.json()["testCase"] == before
        assert "auditId" not in exact_move.json()
        assert _row_counts(clean_api_database, case_id) == before_counts
        replayed_move = client.post(
            f"/api/test-cases/{case_id}/move",
            headers={"Idempotency-Key": move_key},
            json={
                "baseResourceVersion": before["resourceVersion"],
                "targetWorkItemId": before["workItemId"],
                "targetSortOrder": before["sortOrder"],
                "changeReason": "Submitting the exact current presentation position.",
                "source": before["currentVersion"]["source"],
            },
        )
        assert replayed_move.status_code == 200, replayed_move.text
        assert replayed_move.json() == exact_move.json()
        assert _row_counts(clean_api_database, case_id) == before_counts

        reordered = client.post(
            f"/api/test-cases/{case_id}/move",
            json={
                "baseResourceVersion": before["resourceVersion"],
                "targetWorkItemId": before["workItemId"],
                "targetSortOrder": 4,
                "changeReason": "Move generated case lower in the same presentation list.",
                "source": {"type": "UI", "actorLabel": "QA Kim"},
            },
        )
    assert reordered.status_code == 200, reordered.text
    reordered_case = reordered.json()["testCase"]
    assert reordered_case["resourceVersion"] == before["resourceVersion"] + 1
    assert reordered_case["version"] == before["version"]
    assert reordered_case["currentVersion"]["id"] == before["currentVersion"]["id"]
    assert reordered_case["generationManagement"]["managed"] is True
    assert "releasedAt" not in reordered_case["generationManagement"]
    with Session(clean_api_database) as session:
        receipts = session.scalars(
            select(ApiIdempotencyReceipt)
            .where(ApiIdempotencyReceipt.idempotency_key.in_([update_key, move_key]))
            .order_by(ApiIdempotencyReceipt.idempotency_key)
        ).all()
    assert [receipt.operation for receipt in receipts] == [
        "MOVE_TEST_CASE",
        "UPDATE_TEST_CASE",
    ]
    with pytest.raises(DBAPIError, match="API idempotency receipts are append-only"):
        with clean_api_database.begin() as connection:
            connection.execute(
                text(
                    "UPDATE api_idempotency_receipts SET response = '{}'::jsonb "
                    "WHERE idempotency_key = :key"
                ),
                {"key": update_key},
            )
    with pytest.raises(DBAPIError, match="API idempotency receipts are append-only"):
        with clean_api_database.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM api_idempotency_receipts "
                    "WHERE idempotency_key = :key"
                ),
                {"key": move_key},
            )


def test_pe_se2_t04_parent_move_restore_delete_and_permanent_history_semantics(
    clean_api_database: Engine,
) -> None:
    """Semantic/lifecycle human intent releases once and can never revive management."""

    seed, receipt = _apply_generation(
        clean_api_database,
        with_human_reuse=False,
        key="pe-se2-t04-lifecycle-seed-001",
    )
    created = _created_cases(receipt)
    content_case_id = _case_id(created[0])
    move_case_id = _case_id(created[1])
    delete_case_id = _case_id(created[2])
    permanent_case_id = _case_id(created[3])
    move_before = _detail(clean_api_database, move_case_id)

    with _client(clean_api_database) as client:
        target = client.post(
            "/api/work-items",
            json={
                "key": "WORK-904",
                "categoryId": move_before["categoryId"],
                "summary": "Human-selected parent",
            },
        )
        assert target.status_code == 201, target.text
        moved = client.post(
            f"/api/test-cases/{move_case_id}/move",
            json={
                "baseResourceVersion": move_before["resourceVersion"],
                "targetWorkItemId": target.json()["id"],
                "targetSortOrder": 0,
                "changeReason": "QA changed the owning work item.",
                "source": {"type": "UI", "actorLabel": "QA Kim"},
            },
        )
        assert moved.status_code == 200, moved.text
        moved_management = moved.json()["testCase"]["generationManagement"]
        assert moved_management["managed"] is False
        assert moved_management["releaseMutation"] == "PARENT_MOVE"
        assert "releasedByVersionId" not in moved_management

        content_before = _detail(clean_api_database, content_case_id)
        content_changed = client.patch(
            f"/api/test-cases/{content_case_id}",
            json=_update_payload(
                content_before,
                change_reason="QA revised generated labels before restoring.",
                labels=[*content_before["currentVersion"]["labels"], "human-reviewed"],
            ),
        )
        assert content_changed.status_code == 200, content_changed.text
        first_release = content_changed.json()["generationManagement"]
        restored_version = client.post(
            f"/api/test-cases/{content_case_id}/restore/1",
            json={
                "baseVersion": 2,
                "changeReason": "Restore generated content without restoring ownership.",
                "source": {"type": "UI", "actorLabel": "QA Kim"},
            },
        )
        assert restored_version.status_code == 200, restored_version.text
        restored_management = restored_version.json()["testCase"]["generationManagement"]
        assert restored_management == first_release
        assert restored_version.json()["testCase"]["version"] == 3

        delete_before = _detail(clean_api_database, delete_case_id)
        deleted = client.request(
            "DELETE",
            f"/api/test-cases/{delete_case_id}",
            json={
                "baseResourceVersion": delete_before["resourceVersion"],
                "reason": "QA removed the generated case.",
                "source": {"type": "UI", "actorLabel": "QA Kim"},
            },
        )
        assert deleted.status_code == 200, deleted.text
        deleted_case = deleted.json()["testCase"]
        deleted_management = deleted_case["generationManagement"]
        assert deleted_management["managed"] is False
        assert deleted_management["releaseMutation"] == "SOFT_DELETE"
        deleted_successor = _create_successor_job(clean_api_database, seed)
        deleted_plan = client.get(
            f"/api/generation-jobs/generation_job_{deleted_successor.hex}/apply-plan"
        )
        assert deleted_plan.status_code == 200, deleted_plan.text
        assert all(
            placement.get("testCaseId") != delete_case_id
            for placement in deleted_plan.json()["placement"]["cases"]
        )
        restored_deleted = client.post(
            f"/api/test-cases/{delete_case_id}/restore",
            json={
                "baseResourceVersion": deleted_case["resourceVersion"],
                "reason": "QA restored visibility, not generation ownership.",
                "source": {"type": "UI", "actorLabel": "QA Kim"},
            },
        )
        assert restored_deleted.status_code == 200, restored_deleted.text
        assert restored_deleted.json()["testCase"]["generationManagement"] == (
            deleted_management
        )

        permanent_before = _detail(clean_api_database, permanent_case_id)
        soft_deleted = client.request(
            "DELETE",
            f"/api/test-cases/{permanent_case_id}",
            json={
                "baseResourceVersion": permanent_before["resourceVersion"],
                "reason": "Prepare a referenced generated case for permanent deletion.",
                "source": {"type": "UI", "actorLabel": "QA Kim"},
            },
        )
        assert soft_deleted.status_code == 200, soft_deleted.text
        permanent = client.request(
            "DELETE",
            f"/api/test-cases/{permanent_case_id}/permanent",
            json={
                "baseResourceVersion": soft_deleted.json()["testCase"]["resourceVersion"],
                "reason": "Historical generation provenance must win.",
                "source": {"type": "UI", "actorLabel": "QA Kim"},
            },
        )
    assert permanent.status_code == 409
    assert permanent.json()["code"] == "PERMANENT_DELETE_CONFLICT"


def test_pe_se2_t04_released_exact_reuse_never_overwrites_or_reacquires(
    clean_api_database: Engine,
) -> None:
    """A later exact REUSED apply preserves the human version and released state."""

    seed, receipt = _apply_generation(
        clean_api_database,
        with_human_reuse=False,
        key="pe-se2-t04-reuse-after-release-seed-001",
    )
    case_id = _case_id(_created_cases(receipt)[0])
    before = _detail(clean_api_database, case_id)
    with _client(clean_api_database) as client:
        changed = client.patch(
            f"/api/test-cases/{case_id}",
            json=_update_payload(
                before,
                change_reason="QA changed priority without changing the exact dedup key.",
                priority="CRITICAL",
            ),
        )
    assert changed.status_code == 200, changed.text
    human = changed.json()
    assert human["generationManagement"]["managed"] is False

    successor = _create_successor_job(clean_api_database, seed)
    with _client(clean_api_database) as client:
        applied = _apply_from_plan(
            client,
            successor,
            key="pe-se2-t04-reuse-after-release-001",
        )
    assert applied.status_code == 201, applied.text
    reused = next(
        row
        for row in applied.json()["cases"]
        if row["case"]["id"] == case_id
    )
    assert reused["outcome"] == "REUSED"
    assert reused["ledgerMutation"] == "NONE"
    assert reused["preserved"] is True
    assert _detail(clean_api_database, case_id) == human

    decoded = uuid.UUID(case_id.removeprefix("case_"))
    with pytest.raises(DBAPIError, match="generation management"):
        with clean_api_database.begin() as connection:
            connection.execute(
                text(
                    "UPDATE test_cases SET generation_managed = true "
                    "WHERE id = :case_id"
                ),
                {"case_id": decoded},
            )


def test_pe_se2_t04_concurrent_human_and_reused_apply_linearize_without_overwrite(
    clean_api_database: Engine,
) -> None:
    """The apply may win or conflict, but the committed human version is final and released."""

    seed, receipt = _apply_generation(
        clean_api_database,
        with_human_reuse=False,
        key="pe-se2-t04-concurrency-seed-001",
    )
    case_id = _case_id(_created_cases(receipt)[0])
    before = _detail(clean_api_database, case_id)
    successor = _create_successor_job(clean_api_database, seed)
    with _client(clean_api_database) as planning_client:
        encoded = f"generation_job_{successor.hex}"
        plan_response = planning_client.get(f"/api/generation-jobs/{encoded}/apply-plan")
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()
    apply_key = "pe-se2-t04-concurrency-apply-001"
    apply_body = {
        "idempotencyKey": apply_key,
        "expectedContextDigest": plan["expectedContextDigest"],
        "expectedResultDigest": plan["expectedResultDigest"],
        "placement": plan["placement"],
    }
    human_payload = _update_payload(
        before,
        change_reason="Concurrent QA priority correction.",
        priority="CRITICAL",
    )
    barrier = Barrier(2)

    def human_write() -> int:
        barrier.wait()
        with _client(clean_api_database) as client:
            return client.patch(f"/api/test-cases/{case_id}", json=human_payload).status_code

    def automatic_apply() -> int:
        barrier.wait()
        with _client(clean_api_database) as client:
            return client.post(
                f"/api/generation-jobs/generation_job_{successor.hex}/apply",
                headers={"Idempotency-Key": apply_key},
                json=apply_body,
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        human_future = executor.submit(human_write)
        apply_future = executor.submit(automatic_apply)
        human_status = human_future.result()
        apply_status = apply_future.result()

    assert human_status == 200
    assert apply_status in {201, 409}
    final = _detail(clean_api_database, case_id)
    assert final["version"] == 2
    assert final["currentVersion"]["priority"] == "CRITICAL"
    assert final["generationManagement"]["managed"] is False
    assert _row_counts(clean_api_database, case_id)["versions"] == 2


def test_pe_se2_t04_0022_backfill_is_created_only_and_fail_closed(
    clean_api_database: Engine,
    api_postgres_url: str,
) -> None:
    """Unchanged CREATED cases are managed; divergence and unproven rows are not."""

    seed, receipt = _apply_generation(
        clean_api_database,
        with_human_reuse=True,
        key="pe-se2-t04-backfill-seed-001",
    )
    created_ids = [_case_id(row) for row in _created_cases(receipt)]
    reused_id = _case_id(receipt["cases"][0])
    changed_id, deleted_id, *unchanged_ids = created_ids
    parent = _detail(clean_api_database, changed_id)
    direct_payload = {
        "workItemId": parent["workItemId"],
        "status": "ACTIVE",
        "summary": "Legacy caller-claimed SLACK_AI row",
        "precondition": "The row has no CREATED materialization proof.",
        "expectedResultMode": "CASE",
        "caseExpectedResult": "Migration leaves the row unmanaged.",
        "priority": "MEDIUM",
        "labels": ["legacy-unproven"],
        "steps": [{"sequence": 1, "action": "Inspect migration provenance."}],
        "source": {
            "type": "WORKER",
            "generationJobId": f"generation_job_{seed.job_id.hex}",
            "evidenceNodeIds": ["knowledge_node_00000000000070008000000000000001"],
        },
        "generationReason": "Caller metadata is not authority.",
    }
    with _client(clean_api_database) as client:
        direct = client.post("/api/test-cases", json=direct_payload)
        assert direct.status_code == 201, direct.text
        changed_before = _detail(clean_api_database, changed_id)
        changed = client.patch(
            f"/api/test-cases/{changed_id}",
            json=_update_payload(
                changed_before,
                change_reason="Legacy QA changed generated labels.",
                labels=[*changed_before["currentVersion"]["labels"], "human"],
            ),
        )
        assert changed.status_code == 200, changed.text
        deleted_before = _detail(clean_api_database, deleted_id)
        deleted = client.request(
            "DELETE",
            f"/api/test-cases/{deleted_id}",
            json={
                "baseResourceVersion": deleted_before["resourceVersion"],
                "reason": "Legacy QA soft-deleted the generated case.",
                "source": {"type": "UI"},
            },
        )
        assert deleted.status_code == 200, deleted.text

    config = Config(str(_BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", api_postgres_url.replace("%", "%%"))
    command.downgrade(config, "20260827_0022")
    try:
        assert "generation_managed" not in {
            column["name"] for column in inspect(clean_api_database).get_columns("test_cases")
        }
        command.upgrade(config, "head")
        decoded_ids = {
            value: uuid.UUID(value.removeprefix("case_"))
            for value in [*created_ids, reused_id, direct.json()["id"]]
        }
        with clean_api_database.connect() as connection:
            rows = {
                str(row["id"]): row
                for row in connection.execute(
                    text(
                        "SELECT id, generation_managed, generation_lineage_id, "
                        "generation_release_reason, generation_release_actor, "
                        "generation_release_mutation FROM test_cases WHERE id = ANY(:ids)"
                    ),
                    {"ids": list(decoded_ids.values())},
                ).mappings()
            }
            event_counts = {
                str(row["test_case_id"]): (row["initialized"], row["released"])
                for row in connection.execute(
                    text(
                        "SELECT test_case_id, "
                        "count(*) FILTER (WHERE event_type = 'INITIALIZED') AS initialized, "
                        "count(*) FILTER (WHERE event_type = 'RELEASED') AS released "
                        "FROM test_case_generation_management_events "
                        "GROUP BY test_case_id"
                    )
                ).mappings()
            }

        for case_id in unchanged_ids:
            row = rows[str(decoded_ids[case_id])]
            assert row["generation_managed"] is True
            assert row["generation_lineage_id"] is not None
            assert row["generation_release_reason"] is None
            assert event_counts[str(decoded_ids[case_id])] == (1, 0)
        for case_id in (changed_id, deleted_id):
            row = rows[str(decoded_ids[case_id])]
            assert row["generation_managed"] is False
            assert row["generation_lineage_id"] is not None
            assert row["generation_release_reason"] == (
                "LEGACY_HUMAN_OR_UNKNOWN_DIVERGENCE"
            )
            assert row["generation_release_actor"] == {
                "type": "SYSTEM",
                "actorLabel": "PE-SE2-T04 migration",
            }
            assert row["generation_release_mutation"] == (
                "LEGACY_HUMAN_OR_UNKNOWN_DIVERGENCE"
            )
            assert event_counts[str(decoded_ids[case_id])] == (1, 1)
        for case_id in (reused_id, direct.json()["id"]):
            row = rows[str(decoded_ids[case_id])]
            assert row["generation_managed"] is False
            assert row["generation_lineage_id"] is None
            assert str(decoded_ids[case_id]) not in event_counts
    finally:
        command.upgrade(config, "head")


def test_pe_se2_t04_0023_aborts_contradictory_legacy_created_provenance(
    clean_api_database: Engine,
    api_postgres_url: str,
) -> None:
    """Migration never chooses an invented CREATED link for an existing case."""

    seed, receipt = _apply_generation(
        clean_api_database,
        with_human_reuse=False,
        key="pe-se2-t04-contradictory-backfill-seed-001",
    )
    generated = _detail(clean_api_database, _case_id(_created_cases(receipt)[0]))
    with _client(clean_api_database) as client:
        direct = client.post(
            "/api/test-cases",
            json={
                "workItemId": generated["workItemId"],
                "status": "ACTIVE",
                "summary": "Contradictory legacy SLACK_AI case",
                "precondition": "The version was not created by generation apply.",
                "expectedResultMode": "CASE",
                "caseExpectedResult": "Migration aborts.",
                "priority": "MEDIUM",
                "labels": ["contradictory"],
                "steps": [{"sequence": 1, "action": "Attempt a forged link."}],
                "source": {
                    "type": "WORKER",
                    "generationJobId": f"generation_job_{seed.job_id.hex}",
                    "evidenceNodeIds": [
                        "knowledge_node_00000000000070008000000000000001"
                    ],
                },
                "generationReason": "Caller metadata cannot establish CREATED provenance.",
            },
        )
    assert direct.status_code == 201, direct.text
    direct_case_id = uuid.UUID(direct.json()["id"].removeprefix("case_"))
    direct_version_id = uuid.UUID(
        direct.json()["currentVersion"]["id"].removeprefix("case_version_")
    )

    config = Config(str(_BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", api_postgres_url.replace("%", "%%"))
    command.downgrade(config, "20260827_0022")
    try:
        with clean_api_database.begin() as connection:
            apply_id = connection.scalar(
                text(
                    "SELECT id FROM generation_applies WHERE generation_job_id = :job_id"
                ),
                {"job_id": seed.job_id},
            )
            assert apply_id is not None
            connection.execute(
                text(
                    "INSERT INTO generation_apply_outcomes "
                    "(generation_apply_id, generated_case_index, exact_content_key, outcome, "
                    "test_case_id, test_case_version_id, match_reason, preserved, snapshot) "
                    "VALUES (:apply_id, 9, :exact_key, 'CREATED', :case_id, :version_id, "
                    "NULL, false, '{}'::jsonb)"
                ),
                {
                    "apply_id": apply_id,
                    "exact_key": "b" * 64,
                    "case_id": direct_case_id,
                    "version_id": direct_version_id,
                },
            )
        with pytest.raises(
            DBAPIError,
            match="generation management legacy CREATED provenance is contradictory",
        ):
            command.upgrade(config, "head")
    finally:
        command.downgrade(config, "20260826_0018")
        command.upgrade(config, "head")


def test_pe_se2_t04_release_fault_rolls_back_then_clean_retry_commits_once(
    clean_api_database: Engine,
) -> None:
    """Version, release, audit, outbox, and idempotency receipt share one transaction."""

    _, receipt = _apply_generation(
        clean_api_database,
        with_human_reuse=False,
        key="pe-se2-t04-rollback-seed-001",
    )
    case_id = _case_id(_created_cases(receipt)[0])
    before = _detail(clean_api_database, case_id)
    before_counts = _row_counts(clean_api_database, case_id)
    payload = _update_payload(
        before,
        change_reason="Commit all release effects together.",
        summary="Human summary that must roll back with the release",
    )
    key = "pe-se2-t04-rollback-release-001"
    injected = False

    def fail_late(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        nonlocal injected
        if not injected and "INSERT INTO outbox_events" in statement:
            injected = True
            raise RuntimeError("injected T04 release commit failure")

    event.listen(clean_api_database, "before_cursor_execute", fail_late)
    try:
        with _client(clean_api_database) as client:
            failed = client.patch(
                f"/api/test-cases/{case_id}",
                headers={"Idempotency-Key": key},
                json=payload,
            )
    finally:
        event.remove(clean_api_database, "before_cursor_execute", fail_late)
    assert injected
    assert failed.status_code == 500
    assert _detail(clean_api_database, case_id) == before
    assert _row_counts(clean_api_database, case_id) == before_counts

    with _client(clean_api_database) as client:
        retried = client.patch(
            f"/api/test-cases/{case_id}",
            headers={"Idempotency-Key": key},
            json=payload,
        )
    assert retried.status_code == 200, retried.text
    assert retried.json()["generationManagement"]["managed"] is False
    assert _row_counts(clean_api_database, case_id) == {
        "versions": before_counts["versions"] + 1,
        "audits": before_counts["audits"] + 2,
        "outbox": before_counts["outbox"] + 1,
        "managementEvents": before_counts["managementEvents"] + 1,
    }
