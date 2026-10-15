from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import Engine, func, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from tc_backend.db.models import (
    BackgroundJob,
    GenerationJob,
    GenerationSlackNotificationIntent,
    GenerationSlackNotificationReceipt,
)
from tc_backend.generation.errors import GenerationError, GenerationErrorCode
from tc_backend.generation.lifecycle import GenerationJobLifecycle
from tc_backend.generation.slack_notifications import GenerationSlackNotificationService
from tc_backend.repositories.generation_repository import GenerationRepository
from test_phase_e_automatic_generation_postgresql import (
    _complete_current_automatic_generation,
)


pytestmark = pytest.mark.postgres


_TABLES = {
    "generation_slack_notification_intents",
    "generation_slack_notification_delivery_attempts",
    "generation_slack_notification_delivery_observations",
    "generation_slack_notification_receipts",
}

_INTENT_COLUMNS = {
    "id",
    "workspace_id",
    "slack_work_item_id",
    "generation_root_job_id",
    "generation_job_id",
    "generation_apply_id",
    "generation_change_assessment_id",
    "source_version",
    "attempt_number",
    "terminal_status",
    "notification_kind",
    "delivery_disposition",
    "suppression_reason",
    "slack_workspace_id",
    "channel_id",
    "root_message_ts",
    "thread_ts",
    "template_version",
    "locale",
    "message_snapshot",
    "message_text",
    "message_digest",
    "link_mode",
    "ui_url",
    "notification_key",
    "client_message_id",
    "metadata",
    "created_at",
}

_ATTEMPT_COLUMNS = {
    "id",
    "intent_id",
    "background_job_id",
    "background_job_attempt",
    "operation",
    "worker_id",
    "lease_token",
    "lease_expires_at",
    "request_digest",
    "prepared_at",
}

_OBSERVATION_COLUMNS = {
    "id",
    "attempt_id",
    "classification",
    "provider_code",
    "http_status",
    "retry_after_seconds",
    "safe_diagnostic",
    "observed_at",
}

_RECEIPT_COLUMNS = {
    "id",
    "intent_id",
    "attempt_id",
    "terminal_outcome",
    "proof",
    "reason",
    "slack_workspace_id",
    "channel_id",
    "root_message_ts",
    "message_ts",
    "client_message_id",
    "message_digest",
    "acknowledged_at",
    "recorded_at",
}


def _require_tables(engine: Engine) -> None:
    actual = set(inspect(engine).get_table_names())
    assert _TABLES <= actual, (
        "PE-SE2-T06 requires append-only intent/attempt/observation/receipt "
        f"ledgers; missing {sorted(_TABLES - actual)}"
    )


def test_pe_se2_t06_schema_installs_complete_append_only_notification_ledgers(
    clean_api_database: Engine,
) -> None:
    """RED: migration head 0024 has none of the T06 durable ledgers."""

    _require_tables(clean_api_database)
    inspector = inspect(clean_api_database)

    expected = {
        "generation_slack_notification_intents": _INTENT_COLUMNS,
        "generation_slack_notification_delivery_attempts": _ATTEMPT_COLUMNS,
        "generation_slack_notification_delivery_observations": _OBSERVATION_COLUMNS,
        "generation_slack_notification_receipts": _RECEIPT_COLUMNS,
    }
    for table, columns in expected.items():
        actual = {row["name"] for row in inspector.get_columns(table)}
        assert actual == columns, f"{table} columns drifted: {sorted(actual ^ columns)}"

    intent_types = {
        row["name"]: str(row["type"])
        for row in inspector.get_columns("generation_slack_notification_intents")
    }
    assert intent_types["message_snapshot"] == "JSONB"
    assert intent_types["metadata"] == "JSONB"
    assert intent_types["client_message_id"] == "UUID"


def test_pe_se2_t06_schema_enforces_one_intent_and_one_terminal_receipt(
    clean_api_database: Engine,
) -> None:
    """RED: database uniqueness must backstop enqueue/worker races."""

    _require_tables(clean_api_database)
    inspector = inspect(clean_api_database)
    intent_uniques = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints(
            "generation_slack_notification_intents"
        )
    }
    receipt_uniques = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints(
            "generation_slack_notification_receipts"
        )
    }

    assert ("generation_job_id",) in intent_uniques
    assert ("notification_key",) in intent_uniques
    assert ("client_message_id",) in intent_uniques
    assert ("intent_id",) in receipt_uniques
    assert (
        "slack_workspace_id",
        "channel_id",
        "message_ts",
    ) in receipt_uniques


def test_pe_se2_t06_schema_guards_immutability_and_cross_ledger_links(
    clean_api_database: Engine,
) -> None:
    """RED: every notification fact must reject UPDATE/DELETE and forged links."""

    _require_tables(clean_api_database)
    with clean_api_database.connect() as connection:
        trigger_rows = connection.execute(
            text(
                "SELECT c.relname, t.tgname "
                "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                "WHERE c.relname IN ("
                "'generation_slack_notification_intents', "
                "'generation_slack_notification_delivery_attempts', "
                "'generation_slack_notification_delivery_observations', "
                "'generation_slack_notification_receipts'"
                ") AND NOT t.tgisinternal"
            )
        ).all()
    actual = {(str(table), str(trigger)) for table, trigger in trigger_rows}

    expected = {
        (
            "generation_slack_notification_intents",
            "trg_generation_slack_notification_intents_immutable",
        ),
        (
            "generation_slack_notification_intents",
            "trg_generation_slack_notification_intents_links",
        ),
        (
            "generation_slack_notification_delivery_attempts",
            "trg_generation_slack_notification_delivery_attempts_immutable",
        ),
        (
            "generation_slack_notification_delivery_attempts",
            "trg_generation_slack_notification_delivery_attempts_links",
        ),
            (
                "generation_slack_notification_delivery_observations",
                # PostgreSQL identifier names are limited to 63 bytes.
                "trg_generation_slack_notification_delivery_observations_immutab",
            ),
        (
            "generation_slack_notification_delivery_observations",
            "trg_generation_slack_notification_delivery_observations_links",
        ),
        (
            "generation_slack_notification_receipts",
            "trg_generation_slack_notification_receipts_immutable",
        ),
        (
            "generation_slack_notification_receipts",
            "trg_generation_slack_notification_receipts_links",
        ),
    }
    assert expected <= actual, f"missing T06 database guards: {sorted(expected - actual)}"


class _PermanentFailureRunner:
    def run(self, _context: Any) -> Any:
        raise GenerationError(
            GenerationErrorCode.AUTH_REJECTED,
            "stable failure; provider body and credentials are absent",
            retryable=False,
        )


class _RetryableFailureRunner:
    def run(self, _context: Any) -> Any:
        raise GenerationError(
            GenerationErrorCode.TIMEOUT,
            "stable timeout",
            retryable=True,
        )


def test_terminal_failure_and_notification_intent_commit_together(
    clean_api_database: Engine,
) -> None:
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    _, _, _, job_id = _complete_current_automatic_generation(sessions)

    with sessions.begin() as session:
        GenerationJobLifecycle(
            GenerationRepository(session), _PermanentFailureRunner()
        ).execute(job_id)

    with sessions() as session:
        intent = session.scalar(
            select(GenerationSlackNotificationIntent).where(
                GenerationSlackNotificationIntent.generation_job_id == job_id
            )
        )
        assert intent is not None
        assert intent.notification_kind == "FAILURE"
        assert intent.message_snapshot["errorCode"] == "GENERATION_AUTH_REJECTED"
        assert intent.message_snapshot["retryable"] is False
        assert "credentials" not in intent.message_text.casefold()
        assert session.scalar(
            select(func.count())
            .select_from(BackgroundJob)
            .where(
                BackgroundJob.job_type
                == "SLACK_GENERATION_NOTIFICATION_DELIVERY"
            )
        ) == 1
        assert session.scalar(
            select(func.count())
            .select_from(GenerationSlackNotificationReceipt)
            .where(GenerationSlackNotificationReceipt.intent_id == intent.id)
        ) == 0


def test_retryable_failure_does_not_publish_before_final_exhaustion(
    clean_api_database: Engine,
) -> None:
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    _, _, _, job_id = _complete_current_automatic_generation(sessions)

    with sessions.begin() as session:
        GenerationJobLifecycle(
            GenerationRepository(session), _RetryableFailureRunner()
        ).execute(job_id)

    with sessions() as session:
        assert session.scalar(
            select(func.count())
            .select_from(GenerationSlackNotificationIntent)
            .where(GenerationSlackNotificationIntent.generation_job_id == job_id)
        ) == 0

    # T07 does not yet provide a durable exhaustion receipt. A direct T06
    # caller therefore cannot override retryability with an in-memory claim.
    with sessions.begin() as session:
        assert GenerationSlackNotificationService(session).capture_final_failure(
            job_id
        ) is None


def test_permanent_failure_fact_intent_and_delivery_job_rollback_together(
    clean_api_database: Engine,
) -> None:
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    _, _, _, job_id = _complete_current_automatic_generation(sessions)

    with pytest.raises(RuntimeError, match="rollback terminal capture"):
        with sessions.begin() as session:
            GenerationJobLifecycle(
                GenerationRepository(session), _PermanentFailureRunner()
            ).execute(job_id)
            assert session.get(GenerationJob, job_id).status == "FAILED"  # type: ignore[union-attr]
            assert session.scalar(
                select(func.count())
                .select_from(GenerationSlackNotificationIntent)
                .where(GenerationSlackNotificationIntent.generation_job_id == job_id)
            ) == 1
            assert session.scalar(
                select(func.count())
                .select_from(BackgroundJob)
                .where(
                    BackgroundJob.job_type
                    == "SLACK_GENERATION_NOTIFICATION_DELIVERY"
                )
            ) == 1
            raise RuntimeError("rollback terminal capture")

    with sessions() as session:
        assert session.get(GenerationJob, job_id).status == "QUEUED"  # type: ignore[union-attr]
        assert session.scalar(
            select(func.count())
            .select_from(GenerationSlackNotificationIntent)
            .where(GenerationSlackNotificationIntent.generation_job_id == job_id)
        ) == 0
        assert session.scalar(
            select(func.count())
            .select_from(BackgroundJob)
            .where(
                BackgroundJob.job_type == "SLACK_GENERATION_NOTIFICATION_DELIVERY"
            )
        ) == 0


def test_notification_ledgers_reject_raw_update_and_delete(
    clean_api_database: Engine,
) -> None:
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    _, _, _, job_id = _complete_current_automatic_generation(sessions)
    with sessions.begin() as session:
        GenerationJobLifecycle(
            GenerationRepository(session), _PermanentFailureRunner()
        ).execute(job_id)
    with sessions() as session:
        intent_id = session.scalar(
            select(GenerationSlackNotificationIntent.id).where(
                GenerationSlackNotificationIntent.generation_job_id == job_id
            )
        )
        assert intent_id is not None

    for statement in (
        "UPDATE generation_slack_notification_intents "
        "SET message_text = 'forged' WHERE id = :intent_id",
        "DELETE FROM generation_slack_notification_intents WHERE id = :intent_id",
    ):
        with pytest.raises(DBAPIError):
            with clean_api_database.begin() as connection:
                connection.execute(text(statement), {"intent_id": intent_id})
