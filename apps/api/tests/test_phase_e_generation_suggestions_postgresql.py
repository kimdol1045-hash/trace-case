from __future__ import annotations

import copy
import importlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Barrier
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, func, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from tc_api.main import create_app
from tc_backend.db.models import (
    BackgroundJob,
    GenerationJob,
    GenerationSlackNotificationIntent,
    GenerationSuggestion,
    GenerationSuggestionMaterialization,
    GenerationSuggestionResolution,
)
from tc_backend.repositories.generation_repository import GenerationRepository
from test_generation_apply_api_postgresql import _arm_automatic_materialization
from test_phase_e_automatic_generation_impact_postgresql import (
    _ROOT_BODY,
    _automatic_roots,
    _consumer,
    _decision_rows,
    _edit_root,
    _process_current,
    _seed_source,
    _superseding_decision,
)
from test_phase_e_generation_management_postgresql import (
    _apply_from_plan,
    _apply_generation,
    _case_id,
    _create_successor_job,
    _created_cases,
    _detail,
    _row_counts,
    _update_payload,
)

pytestmark = pytest.mark.postgres


_TARGET_COLUMNS = {
    "id",
    "workspace_id",
    "slack_work_item_id",
    "suggestion_target_ref",
    "source_change_assessment_id",
    "selected_root_job_id",
    "test_case_id",
    "generation_lineage_id",
    "generation_created_outcome_id",
    "baseline_case_version_id",
    "baseline_case_version",
    "baseline_case_resource_version",
    "target_snapshot",
    "target_digest",
    "created_at",
}
_SUGGESTION_COLUMNS = {
    "id",
    "workspace_id",
    "test_case_id",
    "generation_lineage_id",
    "suggestion_target_id",
    "source_generation_job_id",
    "source_root_job_id",
    "source_change_assessment_id",
    "generated_case_index",
    "exact_content_key",
    "baseline_case_version_id",
    "baseline_case_version",
    "baseline_case_resource_version",
    "context_digest",
    "result_digest",
    "diff_schema_version",
    "baseline_snapshot",
    "proposed_snapshot",
    "diff_snapshot",
    "baseline_digest",
    "proposed_digest",
    "diff_digest",
    "supersedes_suggestion_id",
    "created_at",
}
_RESOLUTION_COLUMNS = {
    "id",
    "workspace_id",
    "suggestion_id",
    "decision",
    "actor",
    "reason",
    "observed_case_resource_version",
    "observed_case_version_id",
    "applied_case_version_id",
    "audit_id",
    "receipt",
    "decided_at",
}


@dataclass(frozen=True, slots=True)
class SuggestionSeed:
    case_id: str
    work_item_id: uuid.UUID
    source_job_id: uuid.UUID
    suggestion_id: str
    released_case: dict[str, Any]
    suggestion: dict[str, Any]


def _client(engine: Engine, *, raise_server_exceptions: bool = False) -> TestClient:
    return TestClient(
        create_app(validate_database=False, database_engine=engine),
        raise_server_exceptions=raise_server_exceptions,
    )


def _generated_case(index: int, evidence_node_id: str) -> dict[str, Any]:
    name = ("alpha", "beta", "gamma", "delta", "epsilon")[index % 5]
    return {
        "summary": f"Generated payment path {name}",
        "steps": [
            {
                "sequence": 1,
                "precondition": "Signed in",
                "action": f"Open payment flow {name}",
            }
        ],
        "caseExpectedResult": f"Payment flow {name} completes",
        "priority": "HIGH",
        "labels": ["generated", name],
        "evidenceNodeIds": [evidence_node_id],
        "generationReason": "현재 Slack 결정과 증거로부터 생성했다.",
        "inferenceOnly": False,
        "openQuestions": [],
    }


def _ordinary_result(job: GenerationJob) -> dict[str, Any]:
    generation_input = job.generation_input
    assert generation_input is not None
    evidence = str(generation_input["activeKnowledge"][0]["nodeId"])
    categories = generation_input["categoryCandidates"]
    return {
        "outputSchemaVersion": "1.0.0",
        "cases": [_generated_case(index, evidence) for index in range(1, 6)],
        "categoryCandidate": {
            "categoryId": str(categories[0]["categoryId"]),
            "score": 1,
            "alternatives": [
                {"categoryId": str(row["categoryId"]), "score": 0}
                for row in categories[1:]
            ],
        },
    }


def _release_automatic_case(
    engine: Engine,
    *,
    prefix: str,
) -> tuple[uuid.UUID, str, dict[str, Any]]:
    sessions = sessionmaker(engine, expire_on_commit=False, class_=Session)
    suffix = prefix.replace("-", "").upper()[-24:]
    work_item_id, _, _ = _seed_source(sessions, suffix=suffix)
    _process_current(
        sessions,
        _consumer(sessions),
        work_item_id=work_item_id,
        commit_baseline=False,
    )
    with sessions.begin() as session:
        initial = _automatic_roots(session, work_item_id)[0]
        result = _ordinary_result(initial)
        repository = GenerationRepository(session)
        repository.begin_execution(initial.id)
        repository.succeed_execution(initial.id, result)
        _arm_automatic_materialization(session, initial)

    with _client(engine) as client:
        applied = _apply_from_plan(client, initial.id, key=f"{prefix}-initial-apply")
    assert applied.status_code == 201, applied.text
    case_id = _case_id(_created_cases(applied.json())[0])
    before = _detail(engine, case_id)
    with _client(engine) as client:
        released = client.patch(
            f"/api/test-cases/{case_id}",
            headers={
                "Idempotency-Key": f"{prefix}-human-release",
                "X-Actor-Label": "QA Kim",
                "X-External-Actor-Id": "qa-42",
            },
            json=_update_payload(
                before,
                change_reason="QA owns this released generated case.",
                priority="CRITICAL",
            ),
        )
    assert released.status_code == 200, released.text
    assert released.json()["generationManagement"]["managed"] is False
    return work_item_id, case_id, released.json()


def _succeed_suggestion_job(
    engine: Engine,
    *,
    job: GenerationJob,
    case_id: str,
) -> None:
    envelope = job.generation_input
    assert job.generation_input_schema_version == "generation-suggestion/1.0.0", (
        "a selected T03 REGENERATE root with a released generated target must use "
        "the separate sealed suggestion input, not ordinary generation Input 1.0.0"
    )
    assert job.schema_version == "generation-suggestion/1.0.0"
    assert envelope is not None
    assert envelope["generationJobId"] == f"generation_job_{job.id.hex}"
    target = next(
        row for row in envelope["suggestionTargets"] if row["testCaseId"] == case_id
    )
    base_input = envelope["baseGenerationInput"]
    evidence = str(base_input["activeKnowledge"][0]["nodeId"])
    candidate = _generated_case(9, evidence)
    candidate["summary"] = "결제 실패 잠금은 5회다."
    result = {
        "outputSchemaVersion": "generation-suggestion/1.0.0",
        "suggestions": [
            {
                "suggestionTargetRef": target["suggestionTargetRef"],
                "candidate": candidate,
            }
        ],
    }
    with Session(engine) as session, session.begin():
        repository = GenerationRepository(session)
        repository.begin_execution(job.id)
        repository.succeed_execution(job.id, result)
    with _client(engine) as client:
        ordinary_plan = client.get(
            f"/api/generation-jobs/generation_job_{job.id.hex}/apply-plan"
        )
    assert ordinary_plan.status_code == 409
    assert ordinary_plan.json()["code"] == "GENERATION_SUGGESTION_CONFLICT"
    assert ordinary_plan.json()["details"]["reason"] == (
        "SUGGESTION_JOB_NOT_WHOLE_BATCH_APPLICABLE"
    )


def _materialize_succeeded_job(engine: Engine, job_id: uuid.UUID) -> str:
    module = importlib.import_module("tc_backend.generation.suggestions")
    service_type = module.GenerationSuggestionService
    with Session(engine) as session, session.begin():
        job = session.get(GenerationJob, job_id)
        assert job is not None
        _arm_automatic_materialization(session, job)
        suggestions = service_type(session).materialize_succeeded_job(job_id)
        assert len(suggestions) == 1
        intent = session.scalar(
            select(GenerationSlackNotificationIntent).where(
                GenerationSlackNotificationIntent.generation_job_id == job_id
            )
        )
        assert intent is not None
        assert intent.notification_kind == "SUCCESS_SUGGESTIONS"
        assert intent.message_snapshot["suggestionCount"] == len(suggestions)
        assert intent.message_snapshot["automaticOverwriteCount"] == 0
        return f"generation_suggestion_{suggestions[0].id.hex}"


def _materialize_suggestion(engine: Engine, *, prefix: str) -> SuggestionSeed:
    sessions = sessionmaker(engine, expire_on_commit=False, class_=Session)
    work_item_id, case_id, released = _release_automatic_case(
        engine,
        prefix=prefix,
    )
    _edit_root(
        sessions,
        work_item_id=work_item_id,
        ordinal=10,
        body=_ROOT_BODY.replace("3회", "5회"),
        event_suffix=f"{prefix.replace('-', '')}V2",
    )
    _process_current(
        sessions,
        _consumer(sessions, _superseding_decision),
        work_item_id=work_item_id,
        commit_baseline=False,
    )
    with sessions() as session:
        decisions = _decision_rows(session, work_item_id)
        assert decisions[-1]["outcome"] == "REGENERATE"
        source_job = _automatic_roots(session, work_item_id)[-1]
        source_job_id = source_job.id
    _succeed_suggestion_job(engine, job=source_job, case_id=case_id)

    suggestion_id = _materialize_succeeded_job(engine, source_job_id)
    with _client(engine) as client:
        response = client.get(f"/api/generation-suggestions/{suggestion_id}")
    assert response.status_code == 200, response.text
    return SuggestionSeed(
        case_id=case_id,
        work_item_id=work_item_id,
        source_job_id=source_job_id,
        suggestion_id=suggestion_id,
        released_case=released,
        suggestion=response.json(),
    )


def _suggestion_counts(engine: Engine, case_id: str) -> dict[str, int]:
    decoded = uuid.UUID(case_id.removeprefix("case_"))
    with engine.connect() as connection:
        return {
            "suggestions": int(
                connection.scalar(
                    text(
                        "SELECT count(*) FROM generation_suggestions "
                        "WHERE test_case_id = :case_id"
                    ),
                    {"case_id": decoded},
                )
                or 0
            ),
            "resolutions": int(
                connection.scalar(
                    text(
                        "SELECT count(*) FROM generation_suggestion_resolutions r "
                        "JOIN generation_suggestions s ON s.id = r.suggestion_id "
                        "WHERE s.test_case_id = :case_id"
                    ),
                    {"case_id": decoded},
                )
                or 0
            ),
        }


def _apply_payload(seed: SuggestionSeed, *, key: str) -> dict[str, Any]:
    baseline = seed.suggestion["baseline"]
    return {
        "idempotencyKey": key,
        "expectedCaseResourceVersion": baseline["caseResourceVersion"],
        "expectedCaseVersionId": baseline["caseVersionId"],
        "changeReason": "Apply the reviewed immutable generation suggestion.",
    }


def test_suggestion_materialization_intent_and_delivery_job_rollback_together(
    clean_api_database: Engine,
) -> None:
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    work_item_id, case_id, _ = _release_automatic_case(
        clean_api_database,
        prefix="t06-suggestion-rollback",
    )
    _edit_root(
        sessions,
        work_item_id=work_item_id,
        ordinal=10,
        body=_ROOT_BODY.replace("3회", "5회"),
        event_suffix="T06SuggestionRollbackV2",
    )
    _process_current(
        sessions,
        _consumer(sessions, _superseding_decision),
        work_item_id=work_item_id,
        commit_baseline=False,
    )
    with sessions() as session:
        source_job = _automatic_roots(session, work_item_id)[-1]
        source_job_id = source_job.id
    _succeed_suggestion_job(clean_api_database, job=source_job, case_id=case_id)
    with sessions() as session:
        initial_notification_jobs = session.scalar(
            select(func.count())
            .select_from(BackgroundJob)
            .where(
                BackgroundJob.job_type == "SLACK_GENERATION_NOTIFICATION_DELIVERY"
            )
        )
        assert initial_notification_jobs is not None

    service_type = importlib.import_module(
        "tc_backend.generation.suggestions"
    ).GenerationSuggestionService
    with pytest.raises(RuntimeError, match="rollback suggestion notification"):
        with sessions.begin() as session:
            job = session.get(GenerationJob, source_job_id)
            assert job is not None
            _arm_automatic_materialization(session, job)
            suggestions = service_type(session).materialize_succeeded_job(source_job_id)
            assert len(suggestions) == 1
            assert session.scalar(
                select(func.count())
                .select_from(GenerationSuggestionMaterialization)
                .where(
                    GenerationSuggestionMaterialization.generation_job_id
                    == source_job_id
                )
            ) == 1
            assert session.scalar(
                select(func.count())
                .select_from(GenerationSlackNotificationIntent)
                .where(
                    GenerationSlackNotificationIntent.generation_job_id
                    == source_job_id
                )
            ) == 1
            assert session.scalar(
                select(func.count())
                .select_from(BackgroundJob)
                .where(
                    BackgroundJob.job_type
                    == "SLACK_GENERATION_NOTIFICATION_DELIVERY"
                )
            ) == initial_notification_jobs + 1
            raise RuntimeError("rollback suggestion notification")

    with sessions() as session:
        assert session.scalar(
            select(func.count())
            .select_from(GenerationSuggestion)
            .where(GenerationSuggestion.source_generation_job_id == source_job_id)
        ) == 0
        assert session.scalar(
            select(func.count())
            .select_from(GenerationSuggestionMaterialization)
            .where(
                GenerationSuggestionMaterialization.generation_job_id == source_job_id
            )
        ) == 0
        assert session.scalar(
            select(func.count())
            .select_from(GenerationSlackNotificationIntent)
            .where(GenerationSlackNotificationIntent.generation_job_id == source_job_id)
        ) == 0
        assert session.scalar(
            select(func.count())
            .select_from(BackgroundJob)
            .where(
                BackgroundJob.job_type == "SLACK_GENERATION_NOTIFICATION_DELIVERY"
            )
        ) == initial_notification_jobs


def test_pe_se2_t05_schema_installs_target_suggestion_and_resolution_ledgers(
    clean_api_database: Engine,
) -> None:
    """RED: migration 0023 has none of the T05 ledgers or their guards."""

    inspector = inspect(clean_api_database)
    expected = {
        "generation_suggestion_targets": _TARGET_COLUMNS,
        "generation_suggestions": _SUGGESTION_COLUMNS,
        "generation_suggestion_resolutions": _RESOLUTION_COLUMNS,
    }
    for table_name, columns in expected.items():
        assert inspector.has_table(table_name), f"PE-SE2-T05 table missing: {table_name}"
        assert columns == {row["name"] for row in inspector.get_columns(table_name)}

    target_uniques = {
        tuple(row["column_names"])
        for row in inspector.get_unique_constraints("generation_suggestion_targets")
    }
    assert ("suggestion_target_ref",) in target_uniques
    assert ("source_change_assessment_id", "generation_lineage_id") in target_uniques
    suggestion_uniques = {
        tuple(row["column_names"])
        for row in inspector.get_unique_constraints("generation_suggestions")
    }
    assert ("suggestion_target_id",) in suggestion_uniques
    assert ("source_change_assessment_id", "generation_lineage_id") in suggestion_uniques
    resolution_uniques = {
        tuple(row["column_names"])
        for row in inspector.get_unique_constraints("generation_suggestion_resolutions")
    }
    assert ("suggestion_id",) in resolution_uniques

    with clean_api_database.connect() as connection:
        triggers = set(
            connection.scalars(
                text(
                    "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND "
                    "tgrelid IN ('generation_suggestion_targets'::regclass, "
                    "'generation_suggestions'::regclass, "
                    "'generation_suggestion_resolutions'::regclass)"
                )
            )
        )
    assert {
        "trg_generation_suggestion_targets_immutable",
        "trg_generation_suggestion_targets_links",
        "trg_generation_suggestions_immutable",
        "trg_generation_suggestions_links",
        "trg_generation_suggestions_one_open",
        "trg_generation_suggestion_resolutions_immutable",
        "trg_generation_suggestion_resolutions_links",
    } <= triggers


def test_pe_se2_t05_ordinary_reused_apply_never_becomes_a_suggestion(
    clean_api_database: Engine,
) -> None:
    """Control: exact/semantic REUSED is not target authority or a T05 side effect."""

    generation_seed, receipt = _apply_generation(
        clean_api_database,
        with_human_reuse=False,
        key="pe-se2-t05-ordinary-seed",
    )
    case_id = _case_id(_created_cases(receipt)[0])
    before = _detail(clean_api_database, case_id)
    with _client(clean_api_database) as client:
        released = client.patch(
            f"/api/test-cases/{case_id}",
            json=_update_payload(
                before,
                change_reason="Human release stays outside ordinary generation apply.",
                priority="CRITICAL",
            ),
        )
    assert released.status_code == 200, released.text
    successor = _create_successor_job(clean_api_database, generation_seed)
    with _client(clean_api_database) as client:
        applied = _apply_from_plan(client, successor, key="pe-se2-t05-ordinary-reuse")
    assert applied.status_code == 201, applied.text
    outcome = next(row for row in applied.json()["cases"] if row["case"]["id"] == case_id)
    assert outcome["outcome"] == "REUSED"
    assert "suggestionId" not in outcome
    assert _detail(clean_api_database, case_id) == released.json()


def test_pe_se2_t05_regenerate_uses_sealed_target_and_preserves_released_case(
    clean_api_database: Engine,
) -> None:
    assert inspect(clean_api_database).has_table("generation_suggestion_targets"), (
        "T05 must persist authority before exposing suggestionTargetRef"
    )
    seed = _materialize_suggestion(clean_api_database, prefix="pe-se2-t05-target")
    assert _detail(clean_api_database, seed.case_id) == seed.released_case
    assert seed.suggestion["state"] == "OPEN"
    assert seed.suggestion["testCaseId"] == seed.case_id
    assert seed.suggestion["generationLineageId"] == (
        seed.released_case["generationManagement"]["lineageId"]
    )
    assert seed.suggestion["source"]["generationJobId"] == (
        f"generation_job_{seed.source_job_id.hex}"
    )
    assert seed.suggestion["source"]["slackWorkItemId"] == (
        f"slack_work_{seed.work_item_id.hex}"
    )
    decoded_case_id = uuid.UUID(seed.case_id.removeprefix("case_"))
    with clean_api_database.connect() as connection:
        target = connection.execute(
            text(
                "SELECT workspace_id, slack_work_item_id, selected_root_job_id, "
                "test_case_id, generation_lineage_id, generation_created_outcome_id, "
                "baseline_case_version_id, baseline_case_version, "
                "baseline_case_resource_version, suggestion_target_ref "
                "FROM generation_suggestion_targets WHERE test_case_id = :case_id"
            ),
            {"case_id": decoded_case_id},
        ).mappings().one()
    management = seed.released_case["generationManagement"]
    assert target["slack_work_item_id"] == seed.work_item_id
    assert target["selected_root_job_id"] == seed.source_job_id
    assert target["test_case_id"] == decoded_case_id
    assert target["generation_lineage_id"] == uuid.UUID(
        management["lineageId"].removeprefix("generation_lineage_")
    )
    assert target["generation_created_outcome_id"] == uuid.UUID(
        management["createdOutcomeId"].removeprefix("generation_apply_outcome_")
    )
    assert target["baseline_case_version_id"] == uuid.UUID(
        seed.released_case["currentVersion"]["id"].removeprefix("case_version_")
    )
    assert target["baseline_case_version"] == seed.released_case["version"]
    assert target["baseline_case_resource_version"] == (
        seed.released_case["resourceVersion"]
    )
    assert target["suggestion_target_ref"].startswith("generation_suggestion_target_")


def test_pe_se2_t05_diff_snapshot_is_immutable_and_stale_apply_fails_closed(
    clean_api_database: Engine,
) -> None:
    seed = _materialize_suggestion(clean_api_database, prefix="pe-se2-t05-diff")
    original = copy.deepcopy(seed.suggestion)
    before = _detail(clean_api_database, seed.case_id)
    with _client(clean_api_database) as client:
        changed = client.patch(
            f"/api/test-cases/{seed.case_id}",
            json=_update_payload(
                before,
                change_reason="A later human change cannot rewrite a saved diff.",
                summary="Human changed this case after suggestion capture",
            ),
        )
        reread = client.get(f"/api/generation-suggestions/{seed.suggestion_id}")
        stale = client.post(
            f"/api/generation-suggestions/{seed.suggestion_id}/apply",
            headers={"Idempotency-Key": "pe-se2-t05-stale-apply"},
            json=_apply_payload(seed, key="pe-se2-t05-stale-apply"),
        )
    assert changed.status_code == 200, changed.text
    assert reread.status_code == 200, reread.text
    assert reread.json()["baseline"] == original["baseline"]
    assert reread.json()["proposal"] == original["proposal"]
    assert reread.json()["diff"] == original["diff"]
    assert reread.json()["source"] == original["source"]
    assert stale.status_code == 409
    assert stale.json()["details"]["reason"] == "CASE_VERSION_STALE"

    decoded = uuid.UUID(seed.suggestion_id.removeprefix("generation_suggestion_"))
    with pytest.raises(DBAPIError, match="generation suggestions are append-only"):
        with clean_api_database.begin() as connection:
            connection.execute(
                text(
                    "UPDATE generation_suggestions SET diff_snapshot = '{}'::jsonb "
                    "WHERE id = :id"
                ),
                {"id": decoded},
            )


def test_pe_se2_t05_apply_is_atomic_released_forever_and_idempotent(
    clean_api_database: Engine,
) -> None:
    seed = _materialize_suggestion(clean_api_database, prefix="pe-se2-t05-apply")
    before = _detail(clean_api_database, seed.case_id)
    before_counts = _row_counts(clean_api_database, seed.case_id)
    key = "pe-se2-t05-apply-decision"
    payload = _apply_payload(seed, key=key)
    injected = False

    def fail_on_resolution(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        nonlocal injected
        if not injected and "INSERT INTO generation_suggestion_resolutions" in statement:
            injected = True
            raise RuntimeError("injected T05 resolution failure")

    event.listen(clean_api_database, "before_cursor_execute", fail_on_resolution)
    try:
        with _client(clean_api_database) as client:
            failed = client.post(
                f"/api/generation-suggestions/{seed.suggestion_id}/apply",
                headers={"Idempotency-Key": key},
                json=payload,
            )
    finally:
        event.remove(clean_api_database, "before_cursor_execute", fail_on_resolution)
    assert injected
    assert failed.status_code == 500
    assert _detail(clean_api_database, seed.case_id) == before
    assert _row_counts(clean_api_database, seed.case_id) == before_counts

    with _client(clean_api_database) as client:
        applied = client.post(
            f"/api/generation-suggestions/{seed.suggestion_id}/apply",
            headers={"Idempotency-Key": key, "X-Actor-Label": "QA Kim"},
            json=payload,
        )
        replay = client.post(
            f"/api/generation-suggestions/{seed.suggestion_id}/apply",
            headers={"Idempotency-Key": key, "X-Actor-Label": "spoofed replay"},
            json=payload,
        )
        conflict = client.post(
            f"/api/generation-suggestions/{seed.suggestion_id}/apply",
            headers={"Idempotency-Key": key},
            json={**payload, "changeReason": "Different request under one key."},
        )
    assert applied.status_code == replay.status_code == 200
    assert replay.json() == applied.json()
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    after = applied.json()["testCase"]
    assert after["version"] == before["version"] + 1
    assert after["generationManagement"] == before["generationManagement"]
    assert after["generationManagement"]["managed"] is False
    assert applied.json()["resolution"]["decision"] == "APPLIED"
    assert _suggestion_counts(clean_api_database, seed.case_id) == {
        "suggestions": 1,
        "resolutions": 1,
    }
    resolution_id = uuid.UUID(
        applied.json()["resolution"]["id"].removeprefix(
            "generation_suggestion_resolution_"
        )
    )
    with pytest.raises(DBAPIError, match="suggestion resolutions are append-only"):
        with clean_api_database.begin() as connection:
            connection.execute(
                text(
                    "UPDATE generation_suggestion_resolutions "
                    "SET reason = 'rewritten' WHERE id = :id"
                ),
                {"id": resolution_id},
            )
    with pytest.raises(DBAPIError, match="suggestion resolutions are append-only"):
        with clean_api_database.begin() as connection:
            connection.execute(
                text("DELETE FROM generation_suggestion_resolutions WHERE id = :id"),
                {"id": resolution_id},
            )


def test_pe_se2_t05_ignore_appends_once_and_never_mutates_case(
    clean_api_database: Engine,
) -> None:
    seed = _materialize_suggestion(clean_api_database, prefix="pe-se2-t05-ignore")
    before = _detail(clean_api_database, seed.case_id)
    before_counts = _row_counts(clean_api_database, seed.case_id)
    key = "pe-se2-t05-ignore-decision"
    payload = {
        "idempotencyKey": key,
        "reason": "The human-authored behavior remains authoritative.",
    }
    with _client(clean_api_database) as client:
        ignored = client.post(
            f"/api/generation-suggestions/{seed.suggestion_id}/ignore",
            headers={"Idempotency-Key": key, "X-Actor-Label": "QA Kim"},
            json=payload,
        )
        replay = client.post(
            f"/api/generation-suggestions/{seed.suggestion_id}/ignore",
            headers={"Idempotency-Key": key},
            json=payload,
        )
        conflict = client.post(
            f"/api/generation-suggestions/{seed.suggestion_id}/ignore",
            headers={"Idempotency-Key": key},
            json={**payload, "reason": "A different ignored decision."},
        )
    assert ignored.status_code == replay.status_code == 200
    assert replay.json() == ignored.json()
    assert ignored.json()["resolution"]["decision"] == "IGNORED"
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert _detail(clean_api_database, seed.case_id) == before
    assert _row_counts(clean_api_database, seed.case_id) == before_counts
    assert _suggestion_counts(clean_api_database, seed.case_id) == {
        "suggestions": 1,
        "resolutions": 1,
    }


def test_pe_se2_t05_concurrent_apply_and_ignore_have_one_terminal_winner(
    clean_api_database: Engine,
) -> None:
    """Two production API writers linearize to one decision and one case policy."""

    seed = _materialize_suggestion(clean_api_database, prefix="pe-se2-t05-decision-race")
    before = _detail(clean_api_database, seed.case_id)
    before_counts = _row_counts(clean_api_database, seed.case_id)
    apply_key = "pe-se2-t05-decision-race-apply"
    ignore_key = "pe-se2-t05-decision-race-ignore"
    apply_payload = _apply_payload(seed, key=apply_key)
    ignore_payload = {
        "idempotencyKey": ignore_key,
        "reason": "The concurrent human decision keeps the released case unchanged.",
    }
    barrier = Barrier(2)

    def apply_write() -> tuple[int, dict[str, Any]]:
        with _client(clean_api_database) as client:
            barrier.wait()
            response = client.post(
                f"/api/generation-suggestions/{seed.suggestion_id}/apply",
                headers={"Idempotency-Key": apply_key, "X-Actor-Label": "Apply reviewer"},
                json=apply_payload,
            )
            return response.status_code, response.json()

    def ignore_write() -> tuple[int, dict[str, Any]]:
        with _client(clean_api_database) as client:
            barrier.wait()
            response = client.post(
                f"/api/generation-suggestions/{seed.suggestion_id}/ignore",
                headers={"Idempotency-Key": ignore_key, "X-Actor-Label": "Ignore reviewer"},
                json=ignore_payload,
            )
            return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        apply_future = executor.submit(apply_write)
        ignore_future = executor.submit(ignore_write)
        results = {
            "APPLIED": apply_future.result(),
            "IGNORED": ignore_future.result(),
        }

    winners = [decision for decision, result in results.items() if result[0] == 200]
    losers = [decision for decision, result in results.items() if result[0] == 409]
    assert len(winners) == len(losers) == 1, results
    winner = winners[0]
    loser_status, loser_body = results[losers[0]]
    assert loser_status == 409
    assert loser_body["code"] == "GENERATION_SUGGESTION_CONFLICT"
    assert loser_body["details"] == {
        "reason": "NOT_OPEN",
        "suggestionId": seed.suggestion_id,
    }

    winner_status, winner_body = results[winner]
    assert winner_status == 200
    assert winner_body["suggestion"]["state"] == winner
    assert winner_body["resolution"]["decision"] == winner
    terminal = winner_body["suggestion"]
    assert terminal["resolution"] == winner_body["resolution"]
    resolution = winner_body["resolution"]
    assert resolution["observedCaseResourceVersion"] == before["resourceVersion"]
    assert resolution["observedCaseVersionId"] == before["currentVersion"]["id"]

    after = _detail(clean_api_database, seed.case_id)
    after_counts = _row_counts(clean_api_database, seed.case_id)
    expected_count_delta = {
        "versions": 1 if winner == "APPLIED" else 0,
        "audits": 1 if winner == "APPLIED" else 0,
        "outbox": 1 if winner == "APPLIED" else 0,
        "managementEvents": 0,
    }
    assert {
        name: after_counts[name] - before_counts[name] for name in before_counts
    } == expected_count_delta
    assert after["generationManagement"] == before["generationManagement"]
    if winner == "APPLIED":
        assert winner_body["testCase"] == after
        assert after["resourceVersion"] == before["resourceVersion"] + 1
        assert after["version"] == before["version"] + 1
        assert after["currentVersion"]["id"] == resolution["appliedCaseVersionId"]
        assert after["currentVersion"]["version"] == before["currentVersion"]["version"] + 1
    else:
        assert after == before
        assert resolution["appliedCaseVersionId"] is None

    suggestion_uuid = uuid.UUID(
        seed.suggestion_id.removeprefix("generation_suggestion_")
    )
    with Session(clean_api_database) as session:
        resolution_rows = tuple(
            session.scalars(
                select(GenerationSuggestionResolution).where(
                    GenerationSuggestionResolution.suggestion_id == suggestion_uuid
                )
            )
        )
    assert len(resolution_rows) == 1
    assert resolution_rows[0].decision == winner
    assert _suggestion_counts(clean_api_database, seed.case_id) == {
        "suggestions": 1,
        "resolutions": 1,
    }

    with _client(clean_api_database) as client:
        if winner == "APPLIED":
            replay = client.post(
                f"/api/generation-suggestions/{seed.suggestion_id}/apply",
                headers={"Idempotency-Key": apply_key, "X-Actor-Label": "Replay spoof"},
                json=apply_payload,
            )
            opposite = client.post(
                f"/api/generation-suggestions/{seed.suggestion_id}/ignore",
                headers={"Idempotency-Key": "pe-se2-t05-decision-race-opposite"},
                json={
                    "idempotencyKey": "pe-se2-t05-decision-race-opposite",
                    "reason": "A terminal apply cannot be replaced by ignore.",
                },
            )
        else:
            replay = client.post(
                f"/api/generation-suggestions/{seed.suggestion_id}/ignore",
                headers={"Idempotency-Key": ignore_key, "X-Actor-Label": "Replay spoof"},
                json=ignore_payload,
            )
            opposite_payload = _apply_payload(
                seed, key="pe-se2-t05-decision-race-opposite"
            )
            opposite = client.post(
                f"/api/generation-suggestions/{seed.suggestion_id}/apply",
                headers={"Idempotency-Key": opposite_payload["idempotencyKey"]},
                json=opposite_payload,
            )
        terminal_read = client.get(
            f"/api/generation-suggestions/{seed.suggestion_id}"
        )
    assert replay.status_code == 200
    assert replay.json() == winner_body
    assert opposite.status_code == 409
    assert opposite.json()["code"] == "GENERATION_SUGGESTION_CONFLICT"
    assert opposite.json()["details"]["reason"] == "NOT_OPEN"
    assert terminal_read.status_code == 200
    assert terminal_read.json() == terminal
    assert _detail(clean_api_database, seed.case_id) == after
    assert _row_counts(clean_api_database, seed.case_id) == after_counts
    assert _suggestion_counts(clean_api_database, seed.case_id) == {
        "suggestions": 1,
        "resolutions": 1,
    }


def test_pe_se2_t05_deleted_then_restored_case_never_rebases_open_suggestion(
    clean_api_database: Engine,
) -> None:
    seed = _materialize_suggestion(clean_api_database, prefix="pe-se2-t05-restore")
    payload = _apply_payload(seed, key="pe-se2-t05-deleted-apply")
    with _client(clean_api_database) as client:
        deleted = client.request(
            "DELETE",
            f"/api/test-cases/{seed.case_id}",
            json={
                "baseResourceVersion": seed.released_case["resourceVersion"],
                "reason": "QA deleted the released generated case.",
                "source": {"type": "UI"},
            },
        )
        deleted_apply = client.post(
            f"/api/generation-suggestions/{seed.suggestion_id}/apply",
            headers={"Idempotency-Key": payload["idempotencyKey"]},
            json=payload,
        )
        restored = client.post(
            f"/api/test-cases/{seed.case_id}/restore",
            json={
                "baseResourceVersion": deleted.json()["testCase"]["resourceVersion"],
                "reason": "Restore visibility without rebasing the suggestion.",
                "source": {"type": "UI"},
            },
        )
        restored_apply = client.post(
            f"/api/generation-suggestions/{seed.suggestion_id}/apply",
            headers={"Idempotency-Key": "pe-se2-t05-restored-apply"},
            json={**payload, "idempotencyKey": "pe-se2-t05-restored-apply"},
        )
    assert deleted.status_code == restored.status_code == 200
    assert deleted_apply.status_code == restored_apply.status_code == 409
    assert deleted_apply.json()["details"]["reason"] == "CASE_DELETED"
    assert restored_apply.json()["details"]["reason"] == "CASE_VERSION_STALE"
    assert restored.json()["testCase"]["generationManagement"] == (
        seed.released_case["generationManagement"]
    )


def test_pe_se2_t05_public_source_spoof_and_mutation_routes_fail_closed(
    clean_api_database: Engine,
) -> None:
    seed = _materialize_suggestion(clean_api_database, prefix="pe-se2-t05-spoof")
    before = _detail(clean_api_database, seed.case_id)
    payload = _apply_payload(seed, key="pe-se2-t05-spoof-apply")
    with _client(clean_api_database) as client:
        create = client.post(
            f"/api/test-cases/{seed.case_id}/generation-suggestions",
            json={
                "sourceGenerationJobId": f"generation_job_{seed.source_job_id.hex}",
                "generationLineageId": before["generationManagement"]["lineageId"],
                "suggestionTargetRef": "generation_suggestion_target_spoofed",
                "source": {"type": "WORKER"},
            },
        )
        rewrite = client.patch(
            f"/api/generation-suggestions/{seed.suggestion_id}",
            json={"state": "APPLIED", "actor": {"type": "SYSTEM"}},
        )
        spoofed_apply = client.post(
            f"/api/generation-suggestions/{seed.suggestion_id}/apply",
            headers={"Idempotency-Key": payload["idempotencyKey"]},
            json={
                **payload,
                "source": {"type": "WORKER"},
                "actor": {"type": "SYSTEM"},
                "suggestionTargetRef": "generation_suggestion_target_spoofed",
            },
        )
    assert create.status_code == rewrite.status_code == 405
    assert spoofed_apply.status_code == 422
    assert _detail(clean_api_database, seed.case_id) == before
    assert _suggestion_counts(clean_api_database, seed.case_id) == {
        "suggestions": 1,
        "resolutions": 0,
    }


def test_pe_se2_t05_multiple_suggestions_use_immutable_supersession_edge(
    clean_api_database: Engine,
) -> None:
    first = _materialize_suggestion(clean_api_database, prefix="pe-se2-t05-super")
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    _edit_root(
        sessions,
        work_item_id=first.work_item_id,
        ordinal=20,
        body=_ROOT_BODY.replace("감사 기록", "감사 기록과 관리자 알림"),
        event_suffix="T05SupersedeV3",
    )
    _process_current(
        sessions,
        _consumer(sessions, _superseding_decision),
        work_item_id=first.work_item_id,
        commit_baseline=False,
    )
    with sessions() as session:
        source_job = _automatic_roots(session, first.work_item_id)[-1]
        second_job_id = source_job.id
    _succeed_suggestion_job(
        clean_api_database,
        job=source_job,
        case_id=first.case_id,
    )
    second_id = _materialize_succeeded_job(clean_api_database, second_job_id)

    with _client(clean_api_database) as client:
        old = client.get(f"/api/generation-suggestions/{first.suggestion_id}")
        current = client.get(f"/api/generation-suggestions/{second_id}")
        old_ignore = client.post(
            f"/api/generation-suggestions/{first.suggestion_id}/ignore",
            headers={"Idempotency-Key": "pe-se2-t05-old-ignore"},
            json={
                "idempotencyKey": "pe-se2-t05-old-ignore",
                "reason": "A superseded suggestion is terminal.",
            },
        )
    assert old.status_code == current.status_code == 200
    assert old.json()["state"] == "SUPERSEDED"
    assert old.json()["supersededBySuggestionId"] == second_id
    assert old.json()["resolution"] is None
    assert current.json()["state"] == "OPEN"
    assert old_ignore.status_code == 409
    assert old_ignore.json()["details"]["reason"] == "SUPERSEDED"
    assert _suggestion_counts(clean_api_database, first.case_id) == {
        "suggestions": 2,
        "resolutions": 0,
    }
