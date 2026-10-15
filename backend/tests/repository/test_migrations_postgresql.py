from __future__ import annotations

import json

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import DBAPIError

from tc_backend.startup import probe_database_capabilities, validate_database_capabilities

pytestmark = pytest.mark.postgres


def test_migrations_seed_schema_and_database_capabilities(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as connection:
        capabilities = probe_database_capabilities(connection)
        validate_database_capabilities(capabilities, expected_embedding_dimension=1024)
        schema = (
            connection.execute(
                text("SELECT version, name, definition FROM ontology_schemas WHERE active")
            )
            .mappings()
            .one()
        )
        workspace = (
            connection.execute(
                text(
                    "SELECT id, case_prefix, issue_prefix FROM workspace_settings "
                    "WHERE singleton_key = 1"
                )
            )
            .mappings()
            .one()
        )
        counters = (
            connection.execute(
                text(
                    "SELECT entity_type, next_value FROM workspace_key_counters "
                    "ORDER BY entity_type"
                )
            )
            .mappings()
            .all()
        )
        system_folder = (
            connection.execute(
                text(
                    "SELECT name, normalized_name, system_kind FROM folders "
                    "WHERE system_kind = 'AI_GENERATED_INBOX'"
                )
            )
            .mappings()
            .one()
        )
        cycle_columns = {
            column["name"] for column in inspect(connection).get_columns("test_cycles")
        }

    assert schema["version"] == 1
    assert schema["name"] == "qa-ontology-v1"
    assert "DECISION" in schema["definition"]["node_types"]
    assert "SUPERSEDES" in schema["definition"]["allowed_relations"]
    assert str(workspace["id"]) == "00000000-0000-7000-8000-000000000100"
    assert workspace["case_prefix"] == "TC"
    assert workspace["issue_prefix"] == "ISSUE"
    assert [(row["entity_type"], row["next_value"]) for row in counters] == [
        ("ISSUE", 1),
        ("TEST_CASE", 1),
    ]
    assert system_folder == {
        "name": "AI 생성함",
        "normalized_name": "ai 생성함",
        "system_kind": "AI_GENERATED_INBOX",
    }
    assert "deleted_at" in cycle_columns


def test_generation_apply_migration_installs_exactly_once_immutable_receipts(
    migrated_engine: Engine,
) -> None:
    with migrated_engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        receipt_constraints = set(
            connection.scalars(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'generation_applies'::regclass"
                )
            )
        )
        outcome_constraints = set(
            connection.scalars(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'generation_apply_outcomes'::regclass"
                )
            )
        )
        triggers = set(
            connection.scalars(
                text(
                    "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgrelid IN ("
                    "'generation_applies'::regclass, "
                    "'generation_apply_outcomes'::regclass)"
                )
            )
        )

    assert {"generation_applies", "generation_apply_outcomes"}.issubset(tables)
    assert {
        "uq_generation_applies_generation_job",
        "uq_generation_applies_idempotency_key",
        "ck_generation_applies_receipt_object",
    }.issubset(receipt_constraints)
    assert {
        "uq_generation_apply_outcomes_apply_index",
        "ck_generation_apply_outcomes_shape",
    }.issubset(outcome_constraints)
    assert triggers == {
        "trg_generation_applies_immutable",
        "trg_generation_apply_outcomes_immutable",
    }


def test_generation_quality_review_migration_installs_private_append_only_ledger(
    migrated_engine: Engine,
) -> None:
    tables_expected = {
        "generation_quality_candidates",
        "generation_quality_candidate_roots",
        "generation_quality_review_decisions",
        "generation_quality_approvals",
        "generation_quality_reviewer_authorities",
    }
    with migrated_engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        triggers = set(
            connection.scalars(
                text(
                    "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgrelid IN ("
                    "'generation_quality_candidates'::regclass, "
                    "'generation_quality_candidate_roots'::regclass, "
                    "'generation_quality_review_decisions'::regclass, "
                    "'generation_quality_approvals'::regclass, "
                    "'generation_quality_reviewer_authorities'::regclass)"
                )
            )
        )
        constraints = set(
            connection.scalars(
                text(
                    "SELECT conname FROM pg_constraint WHERE conrelid IN ("
                    "'generation_quality_candidates'::regclass, "
                    "'generation_quality_candidate_roots'::regclass, "
                    "'generation_quality_review_decisions'::regclass, "
                    "'generation_quality_approvals'::regclass)"
                )
            )
        )

    assert tables_expected.issubset(tables)
    assert {
        "trg_generation_quality_candidates_immutable",
        "trg_generation_quality_candidate_roots_immutable",
        "trg_generation_quality_candidate_roots_guard",
        "trg_generation_quality_review_decisions_immutable",
        "trg_generation_quality_review_decisions_guard",
        "trg_generation_quality_approvals_immutable",
        "trg_generation_quality_approvals_guard",
        "trg_generation_quality_reviewer_authorities_guard",
        "trg_generation_quality_reviewer_authorities_immutable",
    }.issubset(triggers)
    assert {
        "uq_generation_quality_candidates_workspace_idempotency",
        "uq_generation_quality_review_decisions_candidate_principal",
        "uq_generation_quality_review_decisions_principal_idempotency",
        "uq_generation_quality_approvals_candidate",
        "fk_generation_quality_approvals_decision_one",
        "fk_generation_quality_approvals_decision_two",
    }.issubset(constraints)


def test_empty_database_can_be_rebuilt_to_head(
    migrated_engine: Engine,
    alembic_config: Config,
) -> None:
    command.downgrade(alembic_config, "base")
    try:
        assert "background_jobs" not in inspect(migrated_engine).get_table_names()
        command.upgrade(alembic_config, "head")
        tables = set(inspect(migrated_engine).get_table_names())
        assert {
            "background_jobs",
            "ontology_schemas",
            "knowledge_nodes",
            "knowledge_edges",
            "knowledge_evidence",
            "knowledge_embeddings",
            "workspace_settings",
            "workspace_key_counters",
            "audit_logs",
            "folders",
            "folder_aliases",
            "work_items",
            "test_cases",
            "test_case_versions",
            "test_steps",
            "tags",
            "test_case_tags",
            "test_case_version_tags",
            "test_cycles",
            "cycle_items",
            "execution_attempts",
            "issues",
            "issue_steps",
            "issue_links",
            "comments",
            "attachments",
            "integration_settings",
            "drive_usage_snapshots",
            "drive_reconciliation_records",
            "outbox_events",
        }.issubset(tables)
    finally:
        command.upgrade(alembic_config, "head")


def test_e4_migration_rejects_legacy_candidates_until_explicit_reimport(
    migrated_engine: Engine,
    alembic_config: Config,
) -> None:
    command.downgrade(alembic_config, "20260901_0028")
    try:
        legacy_snapshot = {
            "artifactKind": "AUTOMATIC_GENERATION_QUALITY_EVALUATION_INPUT",
            "profile": "CANDIDATE",
            "dataset": {
                "kind": "OPERATIONAL_GOLDEN",
                "synthetic": False,
                "review": {"status": "UNREVIEWED"},
            },
            "liveGate": {"status": "NOT_RUN"},
        }
        with migrated_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO generation_quality_candidates ("
                    "id, workspace_id, candidate_key, import_idempotency_key, request_digest, "
                    "base_artifact_digest, base_dataset_fingerprint, "
                    "base_source_snapshot_fingerprint, base_snapshot_fingerprint, root_set_seal, "
                    "capture_environment, capture_revision, privacy_policy_version, "
                    "privacy_scan_status, snapshot, declared_counts, classification_distribution"
                    ") VALUES ("
                    "'00000000-0000-7000-8000-0000000002e7'::uuid, "
                    "'00000000-0000-7000-8000-000000000100'::uuid, :candidate_key, "
                    "'quality-review-legacy-migration', :digest, :digest, :digest, :digest, "
                    ":digest, :seal, 'staging', 'legacy-revision', 'legacy-policy', 'CLEAN', "
                    "CAST(:snapshot AS jsonb), '{}'::jsonb, '{}'::jsonb)"
                ),
                {
                    "candidate_key": f"quality-review-candidate:{'1' * 64}",
                    "digest": f"sha256:{'2' * 64}",
                    "seal": f"hmac-sha256:{'3' * 64}",
                    "snapshot": json.dumps(legacy_snapshot),
                },
            )
        with pytest.raises(DBAPIError, match="explicit re-import under E4"):
            command.upgrade(alembic_config, "head")
        command.downgrade(alembic_config, "20260831_0027")
        command.upgrade(alembic_config, "head")
    finally:
        command.downgrade(alembic_config, "20260831_0027")
        command.upgrade(alembic_config, "head")


def test_automatic_generation_migration_backfills_roots_retries_and_manual_jobs(
    migrated_engine: Engine,
    alembic_config: Config,
) -> None:
    workspace_id = "00000000-0000-7000-8000-000000000100"
    event_id = "00000000-0000-7000-8000-0000000002a1"
    work_item_id = "00000000-0000-7000-8000-0000000002a2"
    automatic_id = "00000000-0000-7000-8000-0000000002a3"
    manual_id = "00000000-0000-7000-8000-0000000002a4"
    retry_id = "00000000-0000-7000-8000-0000000002a5"
    with migrated_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO slack_channel_allowlist "
                "(workspace_id, slack_workspace_id, channel_id) "
                "VALUES (:workspace_id, 'T-MIGRATION', 'C-MIGRATION')"
            ),
            {"workspace_id": workspace_id},
        )
        connection.execute(
            text(
                "INSERT INTO slack_events "
                "(id, workspace_id, slack_workspace_id, channel_id, event_id, event_type, "
                "event_ts, event_ts_key, payload) VALUES "
                "(:event_id, :workspace_id, 'T-MIGRATION', 'C-MIGRATION', "
                "'Ev-MIGRATION', 'ROOT', '1724000000.000001', 1724000000000001, '{}'::jsonb)"
            ),
            {"event_id": event_id, "workspace_id": workspace_id},
        )
        connection.execute(
            text(
                "INSERT INTO slack_work_items "
                "(id, workspace_id, slack_workspace_id, channel_id, message_ts, "
                "message_ts_key, root_seen, source_version, created_event_id) VALUES "
                "(:work_item_id, :workspace_id, 'T-MIGRATION', 'C-MIGRATION', "
                "'1724000000.000001', 1724000000000001, true, 1, :event_id)"
            ),
            {
                "event_id": event_id,
                "work_item_id": work_item_id,
                "workspace_id": workspace_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO generation_jobs "
                "(id, slack_work_item_id, source_version, generation_version, "
                "generation_trigger, provider, model, prompt_version, schema_version, "
                "ontology_schema_id, root_job_id, retry_of_job_id, attempt_number) "
                "SELECT CAST(:automatic_id AS uuid), CAST(:work_item_id AS uuid), 1, 1, "
                "'AUTOMATIC', 'UNCONFIGURED', 'UNCONFIGURED', 'generation_prompt_v1', "
                "'1.0.0', id, CAST(:automatic_id AS uuid), NULL::uuid, 1 "
                "FROM ontology_schemas WHERE active "
                "UNION ALL "
                "SELECT CAST(:manual_id AS uuid), CAST(:work_item_id AS uuid), 1, 2, "
                "'MANUAL', 'UNCONFIGURED', 'UNCONFIGURED', 'generation_prompt_v1', "
                "'1.0.0', id, CAST(:manual_id AS uuid), NULL::uuid, 1 "
                "FROM ontology_schemas WHERE active "
                "UNION ALL "
                "SELECT CAST(:retry_id AS uuid), CAST(:work_item_id AS uuid), 1, 3, "
                "'AUTOMATIC', 'UNCONFIGURED', 'UNCONFIGURED', 'generation_prompt_v1', "
                "'1.0.0', id, CAST(:automatic_id AS uuid), "
                "CAST(:automatic_id AS uuid), 2 FROM ontology_schemas WHERE active"
            ),
            {
                "automatic_id": automatic_id,
                "manual_id": manual_id,
                "retry_id": retry_id,
                "work_item_id": work_item_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO audit_logs "
                '(workspace_id, entity_type, entity_id, action, "after", source_type, '
                "trace_id) VALUES (:workspace_id, 'GENERATION_JOB', :automatic_id, "
                "'AUTOMATIC_GENERATION_JOB_CREATED', '{}'::jsonb, 'WORKER', "
                "'automatic-generation-migration-backfill')"
            ),
            {"automatic_id": automatic_id, "workspace_id": workspace_id},
        )

    command.downgrade(alembic_config, "20260827_0020")
    try:
        command.upgrade(alembic_config, "head")
        with migrated_engine.connect() as connection:
            triggers = dict(
                connection.execute(
                    text(
                        "SELECT id::text, generation_trigger FROM generation_jobs "
                        "WHERE id IN (:automatic_id, :manual_id, :retry_id)"
                    ),
                    {
                        "automatic_id": automatic_id,
                        "manual_id": manual_id,
                        "retry_id": retry_id,
                    },
                )
                .tuples()
                .all()
            )
        assert triggers == {
            automatic_id: "AUTOMATIC",
            manual_id: "MANUAL",
            retry_id: "AUTOMATIC",
        }
    finally:
        command.upgrade(alembic_config, "head")


def test_generation_lifecycle_one_step_downgrade_restores_0017_guards(
    migrated_engine: Engine,
    alembic_config: Config,
) -> None:
    command.downgrade(alembic_config, "20260826_0017")
    try:
        with migrated_engine.connect() as connection:
            columns = {
                column["name"] for column in inspect(connection).get_columns("generation_jobs")
            }
            context_guard = connection.scalar(
                text(
                    "SELECT pg_get_functiondef('tc_validate_generation_context_grounding'::regproc)"
                )
            )
            job_guard = connection.scalar(
                text("SELECT pg_get_functiondef('tc_guard_generation_job_context_seal'::regproc)")
            )

        assert {
            "root_job_id",
            "retry_of_job_id",
            "attempt_number",
            "started_at",
            "completed_at",
        }.isdisjoint(columns)
        assert context_guard is not None
        assert "retry_source_id" not in context_guard
        assert "generation context accepts only current ACTIVE knowledge" in context_guard
        assert job_guard is not None
        assert "NEW.root_job_id" not in job_guard
        assert "NEW.context_sealed_at" in job_guard
    finally:
        command.upgrade(alembic_config, "head")


def test_populated_cycle_ledger_can_upgrade_to_multi_label_scope(
    migrated_engine: Engine,
    alembic_config: Config,
) -> None:
    command.downgrade(alembic_config, "20260821_0008")
    with migrated_engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM pg_constraint "
                    "WHERE conrelid = 'execution_attempts'::regclass "
                    "AND conname = 'ck_execution_attempts_blocked_note_required'"
                )
            )
            == 1
        )
    planned_id = "00000000-0000-7000-8000-000000000201"
    active_id = "00000000-0000-7000-8000-000000000202"
    completed_id = "00000000-0000-7000-8000-000000000203"
    try:
        with migrated_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO test_cycles "
                    "(id, workspace_id, name, description, starts_at, status, scope_type, "
                    "scope_value, resource_version, run_number, completed_at) VALUES "
                    "(:planned_id, '00000000-0000-7000-8000-000000000100'::uuid, "
                    "'Planned regression', '', now(), 'PLANNED', 'WORK_ITEM', 'WI-1', 1, 1, NULL), "
                    "(:active_id, '00000000-0000-7000-8000-000000000100'::uuid, "
                    "'Active regression', '', now(), 'PLANNED', 'LABEL', 'smoke', 1, 1, NULL), "
                    "(:completed_id, '00000000-0000-7000-8000-000000000100'::uuid, "
                    "'Completed regression', '', now(), 'PLANNED', 'LABEL', 'release', "
                    "1, 1, NULL)"
                ),
                {
                    "planned_id": planned_id,
                    "active_id": active_id,
                    "completed_id": completed_id,
                },
            )
            connection.execute(
                text(
                    "UPDATE test_cycles SET status = 'IN_PROGRESS', resource_version = 2 "
                    "WHERE id IN (:active_id, :completed_id)"
                ),
                {"active_id": active_id, "completed_id": completed_id},
            )
            connection.execute(
                text(
                    "UPDATE test_cycles SET status = 'COMPLETED', resource_version = 3, "
                    "completed_at = now() WHERE id = :completed_id"
                ),
                {"completed_id": completed_id},
            )

        command.upgrade(alembic_config, "head")

        with migrated_engine.connect() as connection:
            cycles = (
                connection.execute(
                    text(
                        "SELECT id::text, status, scope_value, scope_values, resource_version "
                        "FROM test_cycles ORDER BY id"
                    )
                )
                .mappings()
                .all()
            )
            blocked_note_constraints = connection.scalar(
                text(
                    "SELECT count(*) FROM pg_constraint "
                    "WHERE conrelid = 'execution_attempts'::regclass "
                    "AND conname = 'ck_execution_attempts_blocked_note_required'"
                )
            )

        assert blocked_note_constraints == 0
        assert cycles == [
            {
                "id": planned_id,
                "status": "PLANNED",
                "scope_value": "WI-1",
                "scope_values": ["WI-1"],
                "resource_version": 1,
            },
            {
                "id": active_id,
                "status": "IN_PROGRESS",
                "scope_value": "smoke",
                "scope_values": ["smoke"],
                "resource_version": 2,
            },
            {
                "id": completed_id,
                "status": "COMPLETED",
                "scope_value": "release",
                "scope_values": ["release"],
                "resource_version": 3,
            },
        ]
    finally:
        command.upgrade(alembic_config, "head")


def test_case_ledger_migration_installs_physical_invariant_guards(
    migrated_engine: Engine,
) -> None:
    with migrated_engine.connect() as connection:
        triggers = set(
            connection.scalars(
                text(
                    "SELECT tgname FROM pg_trigger "
                    "WHERE NOT tgisinternal AND tgrelid IN ("
                    "'folders'::regclass, 'work_items'::regclass, 'test_cases'::regclass, "
                    "'test_case_versions'::regclass, 'test_steps'::regclass, "
                    "'test_case_tags'::regclass, 'test_case_version_tags'::regclass)"
                )
            )
        )
        constraints = set(
            connection.scalars(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid IN ('test_cases'::regclass, 'test_case_versions'::regclass)"
                )
            )
        )
        active_sort_generation = connection.scalar(
            text(
                "SELECT generation_expression FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'test_cases' "
                "AND column_name = 'active_sort_order'"
            )
        )

    assert {
        "trg_work_items_category_root",
        "trg_folders_protect_projected_category",
        "trg_test_case_versions_immutable",
        "trg_test_steps_immutable",
        "trg_test_case_versions_shape",
        "trg_test_cases_current_version",
        "trg_test_case_tags_projection",
    }.issubset(triggers)
    assert {
        "fk_test_cases_work_item_ownership",
        "fk_test_cases_current_version_same_case",
        "uq_test_cases_active_work_item_sort",
        "uq_test_case_versions_case_number",
    }.issubset(constraints)
    assert active_sort_generation is not None and "deleted_at" in active_sort_generation


def test_cycle_ledger_migration_installs_physical_invariant_guards(
    migrated_engine: Engine,
) -> None:
    with migrated_engine.connect() as connection:
        triggers = set(
            connection.scalars(
                text(
                    "SELECT tgname FROM pg_trigger "
                    "WHERE NOT tgisinternal AND tgrelid IN ("
                    "'test_cycles'::regclass, 'cycle_items'::regclass, "
                    "'execution_attempts'::regclass)"
                )
            )
        )
        constraints = set(
            connection.scalars(
                text(
                    "SELECT conname FROM pg_constraint WHERE conrelid IN ("
                    "'test_cycles'::regclass, 'cycle_items'::regclass, "
                    "'execution_attempts'::regclass)"
                )
            )
        )

    assert {
        "trg_test_cycles_guard",
        "trg_cycle_items_planned_only",
        "trg_execution_attempts_append_only",
    }.issubset(triggers)
    assert {
        "uq_cycle_items_cycle_case",
        "fk_cycle_items_pinned_version_same_case",
        "uq_execution_attempts_item_platform_number",
        "ck_execution_attempts_fail_actual_result_required",
    }.issubset(constraints)
    assert "ck_execution_attempts_blocked_note_required" not in constraints


def test_issue_and_drive_migration_installs_physical_invariant_guards(
    migrated_engine: Engine,
) -> None:
    with migrated_engine.connect() as connection:
        triggers = set(
            connection.scalars(
                text(
                    "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgrelid IN ("
                    "'issues'::regclass, 'issue_steps'::regclass, 'issue_links'::regclass, "
                    "'comments'::regclass, 'attachments'::regclass, "
                    "'integration_settings'::regclass, 'drive_usage_snapshots'::regclass)"
                )
            )
        )
        constraints = set(
            connection.scalars(
                text(
                    "SELECT conname FROM pg_constraint WHERE conrelid IN ("
                    "'issues'::regclass, 'issue_steps'::regclass, 'issue_links'::regclass, "
                    "'attachments'::regclass, 'integration_settings'::regclass, "
                    "'drive_usage_snapshots'::regclass, 'outbox_events'::regclass)"
                )
            )
        )

    assert {
        "trg_issues_guard",
        "trg_issues_require_steps",
        "trg_issue_steps_require_one",
        "trg_issue_links_append_only",
        "trg_comments_append_only",
        "trg_attachments_guard",
        "trg_integration_settings_guard",
        "trg_drive_usage_snapshots_immutable",
    }.issubset(triggers)
    assert {
        "uq_issues_workspace_key",
        "fk_issue_links_pinned_version_same_case",
        "ck_issue_links_attempt_requires_cycle_item",
        "uq_attachments_provider_object_key",
        "uq_integration_settings_workspace_provider",
        "ck_drive_usage_snapshots_warning_matches_usage",
        "uq_outbox_events_aggregate_event_version",
    }.issubset(constraints)
