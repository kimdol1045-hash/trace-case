"""Deleted detail, issue restore, and guarded permanent deletion.

Revision ID: 20260825_0016
Revises: 20260825_0015
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260825_0016"
down_revision: str | None = "20260825_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _replace_case_foreign_keys(permanent=True)
    _replace_drive_reconciliation_foreign_key(permanent=True)
    op.execute(_case_version_guard(permanent=True))
    op.execute(_issue_guard(permanent=True))
    op.execute(_cycle_guard(permanent=True))
    op.execute(_cycle_item_guard(permanent=True))
    op.execute(_execution_attempt_guard(permanent=True))
    op.execute(_execution_step_guard(permanent=True))
    op.execute(_issue_link_guard(permanent=True))
    op.execute(_comment_guard(permanent=True))
    op.execute(_attachment_guard(permanent=True))


def downgrade() -> None:
    op.execute(_attachment_guard(permanent=False))
    op.execute(_comment_guard(permanent=False))
    op.execute(_issue_link_guard(permanent=False))
    op.execute(_execution_step_guard(permanent=False))
    op.execute(_execution_attempt_guard(permanent=False))
    op.execute(_cycle_item_guard(permanent=False))
    op.execute(_cycle_guard(permanent=False))
    op.execute(_issue_guard(permanent=False))
    op.execute(_case_version_guard(permanent=False))
    _replace_drive_reconciliation_foreign_key(permanent=False)
    _replace_case_foreign_keys(permanent=False)


def _replace_case_foreign_keys(*, permanent: bool) -> None:
    constraints = (
        ("fk_test_cases_current_version_same_case", "test_cases"),
        ("fk_test_case_versions_restored_from_same_case", "test_case_versions"),
        ("fk_test_case_versions_test_case_id_test_cases", "test_case_versions"),
        ("fk_test_steps_test_case_version_id_test_case_versions", "test_steps"),
        ("fk_test_case_tags_test_case_id_test_cases", "test_case_tags"),
        ("fk_test_case_version_tags_version", "test_case_version_tags"),
    )
    for name, table in constraints:
        op.drop_constraint(name, table, type_="foreignkey")
    op.create_foreign_key(
        "fk_test_case_versions_test_case_id_test_cases",
        "test_case_versions",
        "test_cases",
        ["test_case_id"],
        ["id"],
        ondelete="CASCADE" if permanent else "RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_test_case_versions_restored_from_same_case",
        "test_case_versions",
        "test_case_versions",
        ["test_case_id", "restored_from_version_id"],
        ["test_case_id", "id"],
        ondelete="CASCADE" if permanent else "RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_test_cases_current_version_same_case",
        "test_cases",
        "test_case_versions",
        ["id", "current_version_id"],
        ["test_case_id", "id"],
        ondelete="NO ACTION" if permanent else "RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_test_steps_test_case_version_id_test_case_versions",
        "test_steps",
        "test_case_versions",
        ["test_case_version_id"],
        ["id"],
        ondelete="CASCADE" if permanent else "RESTRICT",
    )
    op.create_foreign_key(
        "fk_test_case_tags_test_case_id_test_cases",
        "test_case_tags",
        "test_cases",
        ["test_case_id"],
        ["id"],
        ondelete="CASCADE" if permanent else "RESTRICT",
    )
    op.create_foreign_key(
        "fk_test_case_version_tags_version",
        "test_case_version_tags",
        "test_case_versions",
        ["test_case_version_id"],
        ["id"],
        ondelete="CASCADE" if permanent else "RESTRICT",
    )


def _replace_drive_reconciliation_foreign_key(*, permanent: bool) -> None:
    op.drop_constraint(
        "fk_drive_reconciliation_records_attachment_id_attachments",
        "drive_reconciliation_records",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_drive_reconciliation_records_attachment_id_attachments",
        "drive_reconciliation_records",
        "attachments",
        ["attachment_id"],
        ["id"],
        ondelete="SET NULL" if permanent else "RESTRICT",
    )


def _case_version_guard(*, permanent: bool) -> str:
    allowance = (
        """
            IF TG_OP = 'DELETE'
               AND current_setting('tc.permanent_delete', true) = 'on' THEN
                RETURN OLD;
            END IF;
        """
        if permanent
        else ""
    )
    return f"""
        CREATE OR REPLACE FUNCTION tc_reject_case_version_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            {allowance}
            RAISE EXCEPTION '% is immutable', TG_TABLE_NAME
                USING ERRCODE = '55000';
        END
        $$
    """


def _issue_guard(*, permanent: bool) -> str:
    delete_branch = (
        """
                IF OLD.deleted_at IS NULL THEN
                    RAISE EXCEPTION 'only a soft-deleted issue can be permanently deleted'
                        USING ERRCODE = '55000';
                END IF;
                IF current_setting('tc.permanent_delete', true) IS DISTINCT FROM 'on' THEN
                    RAISE EXCEPTION 'issue permanent deletion requires checked capability'
                        USING ERRCODE = '55000';
                END IF;
                RETURN OLD;
        """
        if permanent
        else """
                RAISE EXCEPTION 'issues use soft delete' USING ERRCODE = '55000';
        """
    )
    deleted_branch = (
        """
            IF OLD.deleted_at IS NOT NULL THEN
                IF NEW.deleted_at IS NOT NULL THEN
                    RAISE EXCEPTION 'a soft-deleted issue may only be restored'
                        USING ERRCODE = '55000';
                END IF;
                IF ROW(NEW.id, NEW.workspace_id, NEW.issue_key, NEW.summary,
                       NEW.precondition, NEW.expected_result, NEW.actual_result,
                       NEW.source_note, NEW.status, NEW.severity, NEW.assignee_label,
                       NEW.created_at)
                   IS DISTINCT FROM
                   ROW(OLD.id, OLD.workspace_id, OLD.issue_key, OLD.summary,
                       OLD.precondition, OLD.expected_result, OLD.actual_result,
                       OLD.source_note, OLD.status, OLD.severity, OLD.assignee_label,
                       OLD.created_at) THEN
                    RAISE EXCEPTION 'restoration cannot mutate issue content or status'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END IF;
        """
        if permanent
        else """
            IF OLD.deleted_at IS NOT NULL THEN
                RAISE EXCEPTION 'deleted issues are immutable' USING ERRCODE = '55000';
            END IF;
        """
    )
    return f"""
        CREATE OR REPLACE FUNCTION tc_guard_issue_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                {delete_branch}
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'OPEN' OR NEW.resource_version <> 1
                   OR NEW.deleted_at IS NOT NULL THEN
                    RAISE EXCEPTION 'an issue must be created OPEN at resource version one'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.resource_version <> OLD.resource_version + 1 THEN
                RAISE EXCEPTION 'issue resource_version must increase by exactly one'
                    USING ERRCODE = '23514';
            END IF;
            {deleted_branch}
            IF ROW(NEW.id, NEW.workspace_id, NEW.issue_key, NEW.created_at)
               IS DISTINCT FROM ROW(OLD.id, OLD.workspace_id, OLD.issue_key, OLD.created_at) THEN
                RAISE EXCEPTION 'issue identity is immutable' USING ERRCODE = '55000';
            END IF;
            IF OLD.deleted_at IS NULL AND NEW.deleted_at IS NULL AND NEW.status <> OLD.status THEN
                IF NOT (
                    (OLD.status = 'OPEN' AND NEW.status = 'IN_PROGRESS') OR
                    (OLD.status = 'IN_PROGRESS' AND NEW.status = 'RESOLVED') OR
                    (OLD.status = 'RESOLVED' AND NEW.status IN ('CLOSED', 'REOPENED')) OR
                    (OLD.status = 'CLOSED' AND NEW.status = 'REOPENED') OR
                    (OLD.status = 'REOPENED' AND NEW.status = 'OPEN')
                ) THEN
                    RAISE EXCEPTION 'invalid issue status transition: % -> %', OLD.status, NEW.status
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            IF OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL
               AND NEW.status <> OLD.status THEN
                RAISE EXCEPTION 'soft delete cannot also change issue status'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $$
    """


def _cycle_guard(*, permanent: bool) -> str:
    delete_branch = (
        """
                IF OLD.deleted_at IS NULL THEN
                    RAISE EXCEPTION 'only a soft-deleted cycle can be permanently deleted'
                        USING ERRCODE = '55000';
                END IF;
                IF current_setting('tc.permanent_delete', true) IS DISTINCT FROM 'on' THEN
                    RAISE EXCEPTION 'cycle permanent deletion requires checked capability'
                        USING ERRCODE = '55000';
                END IF;
                RETURN OLD;
        """
        if permanent
        else """
                RAISE EXCEPTION 'cycles are soft-delete only' USING ERRCODE = '55000';
        """
    )
    return f"""
        CREATE OR REPLACE FUNCTION tc_guard_cycle_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'PLANNED' OR NEW.resource_version <> 1
                   OR NEW.run_number <> 1 OR NEW.completed_at IS NOT NULL
                   OR NEW.deleted_at IS NOT NULL THEN
                    RAISE EXCEPTION 'a cycle must be created in active PLANNED state'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
            IF TG_OP = 'DELETE' THEN
                {delete_branch}
            END IF;
            IF NEW.resource_version <> OLD.resource_version + 1 THEN
                RAISE EXCEPTION 'cycle resource_version must increase by exactly one'
                    USING ERRCODE = '23514';
            END IF;
            IF ROW(NEW.id, NEW.workspace_id, NEW.created_at)
               IS DISTINCT FROM ROW(OLD.id, OLD.workspace_id, OLD.created_at) THEN
                RAISE EXCEPTION 'cycle identity and creation audit timestamp are immutable'
                    USING ERRCODE = '55000';
            END IF;
            IF OLD.deleted_at IS NOT NULL THEN
                IF NEW.deleted_at IS NOT NULL THEN
                    RAISE EXCEPTION 'a soft-deleted cycle may only be restored'
                        USING ERRCODE = '55000';
                END IF;
                IF ROW(NEW.name, NEW.description, NEW.starts_at, NEW.status,
                       NEW.scope_type, NEW.scope_value, NEW.scope_values,
                       NEW.run_number, NEW.completed_at)
                   IS DISTINCT FROM
                   ROW(OLD.name, OLD.description, OLD.starts_at, OLD.status,
                       OLD.scope_type, OLD.scope_value, OLD.scope_values,
                       OLD.run_number, OLD.completed_at) THEN
                    RAISE EXCEPTION 'restoration cannot mutate cycle details or status'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.deleted_at IS NOT NULL THEN
                IF ROW(NEW.name, NEW.description, NEW.starts_at, NEW.status,
                       NEW.scope_type, NEW.scope_value, NEW.scope_values,
                       NEW.run_number, NEW.completed_at)
                   IS DISTINCT FROM
                   ROW(OLD.name, OLD.description, OLD.starts_at, OLD.status,
                       OLD.scope_type, OLD.scope_value, OLD.scope_values,
                       OLD.run_number, OLD.completed_at) THEN
                    RAISE EXCEPTION 'soft deletion cannot mutate cycle details or status'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END IF;
            IF OLD.status = 'PLANNED' AND NEW.status = 'PLANNED' THEN
                IF NEW.run_number <> OLD.run_number OR NEW.completed_at IS NOT NULL THEN
                    RAISE EXCEPTION 'invalid PLANNED cycle mutation' USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
            IF OLD.status <> 'PLANNED'
               AND ROW(NEW.scope_type, NEW.scope_value, NEW.scope_values)
                   IS DISTINCT FROM ROW(OLD.scope_type, OLD.scope_value, OLD.scope_values) THEN
                RAISE EXCEPTION 'cycle scope is locked after start' USING ERRCODE = '55000';
            END IF;
            IF OLD.status = 'IN_PROGRESS' AND NEW.status = 'IN_PROGRESS' THEN
                IF NEW.run_number <> OLD.run_number OR NEW.completed_at IS NOT NULL THEN
                    RAISE EXCEPTION 'invalid IN_PROGRESS cycle mutation' USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
            IF ROW(NEW.name, NEW.description, NEW.starts_at,
                   NEW.scope_type, NEW.scope_value, NEW.scope_values)
               IS DISTINCT FROM
               ROW(OLD.name, OLD.description, OLD.starts_at,
                   OLD.scope_type, OLD.scope_value, OLD.scope_values) THEN
                RAISE EXCEPTION 'cycle details cannot change during a status transition'
                    USING ERRCODE = '55000';
            END IF;
            IF OLD.status = 'PLANNED' AND NEW.status = 'IN_PROGRESS'
               AND NEW.run_number = OLD.run_number AND NEW.completed_at IS NULL THEN
                RETURN NEW;
            END IF;
            IF OLD.status = 'IN_PROGRESS' AND NEW.status = 'COMPLETED'
               AND NEW.run_number = OLD.run_number AND NEW.completed_at IS NOT NULL THEN
                RETURN NEW;
            END IF;
            IF OLD.status = 'COMPLETED' AND NEW.status = 'IN_PROGRESS'
               AND NEW.run_number = OLD.run_number + 1 AND NEW.completed_at IS NULL THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'invalid cycle status transition: % -> %', OLD.status, NEW.status
                USING ERRCODE = '23514';
        END
        $$
    """


def _permanent_allowance() -> str:
    return """
            IF TG_OP = 'DELETE'
               AND current_setting('tc.permanent_delete', true) = 'on' THEN
                RETURN OLD;
            END IF;
    """


def _cycle_item_guard(*, permanent: bool) -> str:
    allowance = _permanent_allowance() if permanent else ""
    return f"""
        CREATE OR REPLACE FUNCTION tc_guard_cycle_item_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE parent_cycle_id uuid; parent_status text;
        BEGIN
            {allowance}
            parent_cycle_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.cycle_id ELSE NEW.cycle_id END;
            SELECT status INTO parent_status FROM test_cycles WHERE id = parent_cycle_id;
            IF parent_status IS NULL AND TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            IF parent_status <> 'PLANNED' THEN
                RAISE EXCEPTION 'cycle snapshot is immutable after start' USING ERRCODE = '55000';
            END IF;
            IF TG_OP = 'UPDATE'
               AND ROW(NEW.id, NEW.cycle_id, NEW.test_case_id, NEW.created_at)
                   IS DISTINCT FROM ROW(OLD.id, OLD.cycle_id, OLD.test_case_id, OLD.created_at) THEN
                RAISE EXCEPTION 'cycle item snapshot identity is immutable' USING ERRCODE = '55000';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END $$
    """


def _execution_attempt_guard(*, permanent: bool) -> str:
    allowance = _permanent_allowance() if permanent else ""
    return f"""
        CREATE OR REPLACE FUNCTION tc_guard_execution_attempt_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE parent_status text; parent_run_number integer;
        BEGIN
            {allowance}
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'execution attempts are append-only' USING ERRCODE = '55000';
            END IF;
            SELECT c.status, c.run_number INTO parent_status, parent_run_number
              FROM cycle_items i JOIN test_cycles c ON c.id = i.cycle_id
             WHERE i.id = NEW.cycle_item_id;
            IF parent_status <> 'IN_PROGRESS' THEN
                RAISE EXCEPTION 'execution results require an IN_PROGRESS cycle' USING ERRCODE = '55000';
            END IF;
            IF NEW.cycle_run_number <> parent_run_number THEN
                RAISE EXCEPTION 'execution attempt run does not match the active cycle run'
                    USING ERRCODE = '23514';
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'UNEXECUTED' THEN
                    RAISE EXCEPTION 'an execution attempt must start before it can finish'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
            IF OLD.status <> 'UNEXECUTED' OR OLD.finished_at IS NOT NULL THEN
                RAISE EXCEPTION 'finished execution attempts are immutable' USING ERRCODE = '55000';
            END IF;
            IF NEW.status NOT IN ('PASS', 'FAIL', 'BLOCKED') THEN
                RAISE EXCEPTION 'an execution attempt can only be completed once'
                    USING ERRCODE = '55000';
            END IF;
            IF ROW(NEW.id, NEW.cycle_item_id, NEW.platform, NEW.attempt_no,
                   NEW.cycle_run_number, NEW.executor_label, NEW.started_at, NEW.created_at)
               IS DISTINCT FROM
               ROW(OLD.id, OLD.cycle_item_id, OLD.platform, OLD.attempt_no,
                   OLD.cycle_run_number, OLD.executor_label, OLD.started_at, OLD.created_at) THEN
                RAISE EXCEPTION 'execution attempt identity is immutable' USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END $$
    """


def _execution_step_guard(*, permanent: bool) -> str:
    allowance = _permanent_allowance() if permanent else ""
    return f"""
        CREATE OR REPLACE FUNCTION tc_guard_execution_step_result()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE parent_status text; pinned_version_id uuid;
                step_version_id uuid; step_sequence integer;
        BEGIN
            {allowance}
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'execution step results are immutable after removal';
            END IF;
            IF TG_OP = 'UPDATE' AND (
                NEW.execution_attempt_id <> OLD.execution_attempt_id
                OR NEW.test_step_id <> OLD.test_step_id
                OR NEW.sequence <> OLD.sequence OR NEW.recorded_at <> OLD.recorded_at
            ) THEN
                RAISE EXCEPTION 'execution step result identity is immutable';
            END IF;
            SELECT ea.status, ci.test_case_version_id INTO parent_status, pinned_version_id
              FROM execution_attempts ea JOIN cycle_items ci ON ci.id = ea.cycle_item_id
             WHERE ea.id = NEW.execution_attempt_id FOR UPDATE OF ea;
            IF parent_status IS NULL THEN RAISE EXCEPTION 'execution attempt does not exist'; END IF;
            IF parent_status <> 'UNEXECUTED' THEN
                RAISE EXCEPTION 'execution step results are immutable after attempt completion';
            END IF;
            SELECT ts.test_case_version_id, ts.sequence INTO step_version_id, step_sequence
              FROM test_steps ts WHERE ts.id = NEW.test_step_id;
            IF step_version_id IS NULL OR step_version_id <> pinned_version_id
               OR step_sequence <> NEW.sequence THEN
                RAISE EXCEPTION 'execution step result must reference the pinned step sequence';
            END IF;
            RETURN NEW;
        END $$
    """


def _issue_link_guard(*, permanent: bool) -> str:
    allowance = _permanent_allowance() if permanent else ""
    return f"""
        CREATE OR REPLACE FUNCTION tc_guard_issue_link_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE item_case_id uuid; item_version_id uuid; item_cycle_id uuid;
                attempt_item_id uuid; existing_cycle_id uuid;
                issue_workspace_id uuid; target_workspace_id uuid;
        BEGIN
            {allowance}
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'issue links are append-only' USING ERRCODE = '55000';
            END IF;
            SELECT workspace_id INTO issue_workspace_id FROM issues
             WHERE id = NEW.issue_id AND deleted_at IS NULL;
            IF issue_workspace_id IS NULL THEN
                RAISE EXCEPTION 'issue link owner must be an active issue' USING ERRCODE = '23503';
            END IF;
            IF NEW.execution_attempt_id IS NOT NULL THEN
                SELECT cycle_item_id INTO attempt_item_id FROM execution_attempts
                 WHERE id = NEW.execution_attempt_id;
                IF attempt_item_id IS NULL OR attempt_item_id <> NEW.cycle_item_id THEN
                    RAISE EXCEPTION 'execution attempt does not belong to the linked cycle item'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            IF NEW.cycle_item_id IS NOT NULL THEN
                SELECT ci.test_case_id, ci.test_case_version_id, ci.cycle_id, tc.workspace_id
                  INTO item_case_id, item_version_id, item_cycle_id, target_workspace_id
                  FROM cycle_items ci JOIN test_cycles tc ON tc.id = ci.cycle_id
                 WHERE ci.id = NEW.cycle_item_id;
                IF item_cycle_id IS NULL OR NEW.test_case_id IS DISTINCT FROM item_case_id THEN
                    RAISE EXCEPTION 'cycle item does not belong to the linked test case'
                        USING ERRCODE = '23514';
                END IF;
                IF target_workspace_id <> issue_workspace_id THEN
                    RAISE EXCEPTION 'issue link target belongs to a different workspace'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.test_case_version_id IS DISTINCT FROM item_version_id THEN
                    RAISE EXCEPTION 'issue link must preserve the cycle pinned case version'
                        USING ERRCODE = '23514';
                END IF;
                SELECT ci.cycle_id INTO existing_cycle_id
                  FROM issue_links link JOIN cycle_items ci ON ci.id = link.cycle_item_id
                 WHERE link.issue_id = NEW.issue_id LIMIT 1;
                IF existing_cycle_id IS NOT NULL AND existing_cycle_id <> item_cycle_id THEN
                    RAISE EXCEPTION 'an issue cannot link cycle evidence from a different cycle'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NOT EXISTS (
                SELECT 1 FROM test_cases
                 WHERE id = NEW.test_case_id AND workspace_id = issue_workspace_id
            ) THEN
                RAISE EXCEPTION 'issue link test case belongs to a different workspace'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$
    """


def _comment_guard(*, permanent: bool) -> str:
    allowance = _permanent_allowance() if permanent else ""
    return f"""
        CREATE OR REPLACE FUNCTION tc_guard_comment_append_only()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            {allowance}
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'comments are append-only' USING ERRCODE = '55000';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM issues WHERE id = NEW.owner_id
                 AND workspace_id = NEW.workspace_id AND deleted_at IS NULL
            ) THEN
                RAISE EXCEPTION 'comment owner must be an active workspace issue'
                    USING ERRCODE = '23503';
            END IF;
            RETURN NEW;
        END $$
    """


def _attachment_guard(*, permanent: bool) -> str:
    delete_branch = (
        """
                IF current_setting('tc.permanent_delete', true) = 'on'
                   AND OLD.deleted_at IS NOT NULL AND OLD.deletion_status = 'DELETED' THEN
                    RETURN OLD;
                END IF;
                RAISE EXCEPTION 'attachment metadata requires a confirmed object deletion'
                    USING ERRCODE = '55000';
        """
        if permanent
        else """
                RAISE EXCEPTION 'attachments use soft delete' USING ERRCODE = '55000';
        """
    )
    return f"""
        CREATE OR REPLACE FUNCTION tc_guard_attachment_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                {delete_branch}
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF NEW.resource_version <> 1 OR NEW.deleted_at IS NOT NULL
                   OR NEW.deletion_status <> 'ACTIVE' THEN
                    RAISE EXCEPTION 'invalid initial attachment state' USING ERRCODE = '23514';
                END IF;
                IF NEW.storage_provider = 'GOOGLE_DRIVE' AND NEW.drive_file_id IS NULL THEN
                    RAISE EXCEPTION 'Google Drive attachment requires drive_file_id'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.owner_type = 'ISSUE' AND NOT EXISTS (
                    SELECT 1 FROM issues WHERE id = NEW.owner_id
                     AND workspace_id = NEW.workspace_id AND deleted_at IS NULL
                ) THEN
                    RAISE EXCEPTION 'attachment issue owner was not found' USING ERRCODE = '23503';
                END IF;
                IF NEW.owner_type = 'EXECUTION_ATTEMPT' AND NOT EXISTS (
                    SELECT 1 FROM execution_attempts ea
                    JOIN cycle_items ci ON ci.id = ea.cycle_item_id
                    JOIN test_cycles tc ON tc.id = ci.cycle_id
                    WHERE ea.id = NEW.owner_id AND tc.workspace_id = NEW.workspace_id
                ) THEN
                    RAISE EXCEPTION 'attachment execution owner was not found'
                        USING ERRCODE = '23503';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.resource_version <> OLD.resource_version + 1 THEN
                RAISE EXCEPTION 'attachment resource_version must increase by exactly one'
                    USING ERRCODE = '23514';
            END IF;
            IF ROW(NEW.id, NEW.workspace_id, NEW.owner_type, NEW.owner_id, NEW.drive_file_id,
                   NEW.object_key, NEW.logical_path, NEW.file_name, NEW.checksum,
                   NEW.mime_type, NEW.size_bytes, NEW.storage_provider, NEW.created_at)
               IS DISTINCT FROM
               ROW(OLD.id, OLD.workspace_id, OLD.owner_type, OLD.owner_id, OLD.drive_file_id,
                   OLD.object_key, OLD.logical_path, OLD.file_name, OLD.checksum,
                   OLD.mime_type, OLD.size_bytes, OLD.storage_provider, OLD.created_at) THEN
                RAISE EXCEPTION 'attachment identity and content metadata are immutable'
                    USING ERRCODE = '55000';
            END IF;
            IF OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS DISTINCT FROM OLD.deleted_at THEN
                RAISE EXCEPTION 'attachment soft deletion cannot be reversed'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END $$
    """
