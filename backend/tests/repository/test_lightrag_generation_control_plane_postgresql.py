from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tc_backend.db.models import LightRAGGenerationTransitionEvent
from tc_backend.repositories.lightrag_generation_repository import (
    DurableLightRAGGenerationControlPlane,
    LightRAGGenerationRepository,
    LightRAGTransitionRejected,
)

pytestmark = pytest.mark.postgres


def _digest(seed: int) -> str:
    return "sha256:" + f"{seed:064x}"


def _register(
    control: DurableLightRAGGenerationControlPlane,
    name: str,
    seed: int,
) -> None:
    control.register_generation(
        generation_id=name,
        provider_id="official-lightrag",
        workspace=f"tc_shadow_{name}",
        auxiliary_target_digest=_digest(1),
        manifest_digest=_digest(seed),
        corpus_digest=_digest(seed + 100),
        source_ledger_digest=_digest(seed + 200),
        projection_digest=_digest(seed + 300),
        pgvector_digest=_digest(seed + 400),
        document_count=seed,
        recorded_by="release-owner",
        idempotency_key=f"register-{name}",
        metadata={
            "auxiliaryAuthorityId": "authority-test",
            "providerContractVersion": "tc-lightrag-generation-provider/v1",
            "providerDistribution": "lightrag-hku",
            "providerVersion": "1.5.6",
            "runtimeConfigDigest": _digest(2),
        },
    )


def test_control_plane_schema_is_isolated_guarded_and_append_only(
    migrated_engine: Engine,
) -> None:
    with migrated_engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        foreign_targets = set(
            connection.scalars(
                text(
                    "SELECT confrelid::regclass::text FROM pg_constraint "
                    "WHERE contype = 'f' AND conrelid IN ("
                    "'lightrag_generations'::regclass, "
                    "'lightrag_active_generation_pointer'::regclass, "
                    "'lightrag_generation_transition_events'::regclass)"
                )
            )
        )
        triggers = set(
            connection.scalars(
                text(
                    "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgrelid IN ("
                    "'lightrag_generations'::regclass, "
                    "'lightrag_active_generation_pointer'::regclass, "
                    "'lightrag_generation_transition_events'::regclass)"
                )
            )
        )

    assert {
        "lightrag_generations",
        "lightrag_active_generation_pointer",
        "lightrag_generation_transition_events",
    }.issubset(tables)
    assert foreign_targets <= {"lightrag_generations"}
    assert triggers == {
        "trg_lightrag_generations_immutable",
        "trg_lightrag_active_generation_pointer_guard",
        "trg_lightrag_generation_transition_events_immutable",
        "trg_lightrag_generation_transition_events_insert_guard",
    }

    control = DurableLightRAGGenerationControlPlane(migrated_engine)
    _register(control, "generation_v1", 10)
    control.activate(
        "generation_v1",
        expected_generation_id=None,
        expected_pointer_version=0,
        actor_id="release-owner",
        idempotency_key="activate-v1",
    )
    for statement in (
        "UPDATE lightrag_generations SET document_count = 99 "
        "WHERE generation_id = 'generation_v1'",
        "DELETE FROM lightrag_generation_transition_events "
        "WHERE idempotency_key = 'activate-v1'",
        "UPDATE lightrag_active_generation_pointer SET pointer_version = 2 "
        "WHERE scope = 'GLOBAL'",
    ):
        with pytest.raises(DBAPIError):
            with migrated_engine.begin() as connection:
                connection.execute(text(statement))


def test_concurrent_activation_has_one_winner_and_exact_replay(
    migrated_engine: Engine,
) -> None:
    control = DurableLightRAGGenerationControlPlane(migrated_engine)
    for index, name in enumerate(("generation_v1", "generation_v2", "generation_v3"), 10):
        _register(control, name, index)
    control.activate(
        "generation_v1",
        expected_generation_id=None,
        expected_pointer_version=0,
        actor_id="release-owner",
        idempotency_key="activate-v1",
    )
    barrier = Barrier(2)

    def contender(name: str) -> tuple[str, str, object]:
        barrier.wait(timeout=10)
        try:
            event = DurableLightRAGGenerationControlPlane(migrated_engine).activate(
                name,
                expected_generation_id="generation_v1",
                expected_pointer_version=1,
                actor_id="release-owner",
                idempotency_key=f"activate-{name}",
            )
            return ("APPLIED", name, event.id)
        except LightRAGTransitionRejected as error:
            return (error.code, name, error.event_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(contender, ("generation_v2", "generation_v3")))

    assert sorted(result[0] for result in results) == [
        "APPLIED",
        "STALE_ACTIVE_GENERATION",
    ]
    winner = next(result for result in results if result[0] == "APPLIED")
    loser = next(result for result in results if result[0] != "APPLIED")
    assert control.active_snapshot().generation_id == winner[1]
    assert control.active_snapshot().pointer_version == 2

    replay = control.activate(
        winner[1],
        expected_generation_id="generation_v1",
        expected_pointer_version=1,
        actor_id="release-owner",
        idempotency_key=f"activate-{winner[1]}",
    )
    assert replay.id == winner[2]
    with migrated_engine.connect() as connection:
        failed = connection.execute(
            text(
                "SELECT outcome, error_code, after_generation_id, "
                "committed_pointer_version FROM lightrag_generation_transition_events "
                "WHERE id = :id"
            ),
            {"id": loser[2]},
        ).mappings().one()
    assert failed == {
        "outcome": "REJECTED",
        "error_code": "STALE_ACTIVE_GENERATION",
        "after_generation_id": winner[1],
        "committed_pointer_version": 2,
    }


def test_unknown_stale_and_invalid_rollback_fail_closed_with_visible_evidence(
    migrated_engine: Engine,
) -> None:
    control = DurableLightRAGGenerationControlPlane(migrated_engine)
    _register(control, "generation_v1", 10)
    _register(control, "generation_v2", 20)
    control.activate(
        "generation_v1",
        expected_generation_id=None,
        expected_pointer_version=0,
        actor_id="release-owner",
        idempotency_key="activate-v1",
    )

    attempts = (
        (
            "unknown-activation",
            lambda: control.activate(
                "generation_unknown",
                expected_generation_id="generation_v1",
                expected_pointer_version=1,
                actor_id="release-owner",
                idempotency_key="unknown-activation",
            ),
            "UNKNOWN_GENERATION",
        ),
        (
            "stale-activation",
            lambda: control.activate(
                "generation_v2",
                expected_generation_id=None,
                expected_pointer_version=0,
                actor_id="release-owner",
                idempotency_key="stale-activation",
            ),
            "STALE_ACTIVE_GENERATION",
        ),
        (
            "invalid-rollback",
            lambda: control.rollback(
                "generation_v2",
                expected_generation_id="generation_v1",
                expected_pointer_version=1,
                actor_id="release-owner",
                idempotency_key="invalid-rollback",
            ),
            "ROLLBACK_TARGET_NOT_PREDECESSOR",
        ),
    )
    event_ids = []
    for _, invoke, expected_code in attempts:
        with pytest.raises(LightRAGTransitionRejected, match=expected_code) as raised:
            invoke()
        assert raised.value.code == expected_code
        event_ids.append(raised.value.event_id)
        assert control.active_snapshot().generation_id == "generation_v1"
        assert control.active_snapshot().pointer_version == 1

    with Session(migrated_engine) as session:
        evidence = session.scalars(
            select(LightRAGGenerationTransitionEvent)
            .where(LightRAGGenerationTransitionEvent.id.in_(event_ids))
            .order_by(LightRAGGenerationTransitionEvent.sequence)
        ).all()
    assert [event.outcome for event in evidence] == ["REJECTED"] * 3
    assert [event.error_code for event in evidence] == [
        "UNKNOWN_GENERATION",
        "STALE_ACTIVE_GENERATION",
        "ROLLBACK_TARGET_NOT_PREDECESSOR",
    ]
    assert all(event.after_generation_id == "generation_v1" for event in evidence)


def test_activation_and_rollback_faults_are_atomic_and_rollback_replays(
    migrated_engine: Engine,
) -> None:
    control = DurableLightRAGGenerationControlPlane(migrated_engine)
    _register(control, "generation_v1", 10)
    _register(control, "generation_v2", 20)

    class FaultingRepository(LightRAGGenerationRepository):
        fault_operation = "ACTIVATE"

        def _after_pointer_update(self, operation: str) -> None:
            if operation == self.fault_operation:
                raise RuntimeError(f"injected {operation.lower()} failure")

    faulting_control = DurableLightRAGGenerationControlPlane(
        migrated_engine, repository_type=FaultingRepository
    )
    with pytest.raises(LightRAGTransitionRejected, match="ACTIVATE_FAILED") as activation_error:
        faulting_control.activate(
            "generation_v1",
            expected_generation_id=None,
            expected_pointer_version=0,
            actor_id="release-owner",
            idempotency_key="activation-fault",
        )
    assert activation_error.value.code == "ACTIVATE_FAILED"
    assert control.active_snapshot().generation_id is None
    assert control.active_snapshot().pointer_version == 0

    control.activate(
        "generation_v1",
        expected_generation_id=None,
        expected_pointer_version=0,
        actor_id="release-owner",
        idempotency_key="activate-v1",
    )
    control.activate(
        "generation_v2",
        expected_generation_id="generation_v1",
        expected_pointer_version=1,
        actor_id="release-owner",
        idempotency_key="activate-v2",
    )
    FaultingRepository.fault_operation = "ROLLBACK"
    with pytest.raises(LightRAGTransitionRejected, match="ROLLBACK_FAILED") as rollback_error:
        faulting_control.rollback(
            "generation_v1",
            expected_generation_id="generation_v2",
            expected_pointer_version=2,
            actor_id="release-owner",
            idempotency_key="rollback-fault",
        )
    assert rollback_error.value.code == "ROLLBACK_FAILED"
    assert control.active_snapshot().generation_id == "generation_v2"
    assert control.active_snapshot().pointer_version == 2

    rollback = control.rollback(
        "generation_v1",
        expected_generation_id="generation_v2",
        expected_pointer_version=2,
        actor_id="release-owner",
        idempotency_key="rollback-v1",
    )
    assert rollback.outcome == "APPLIED"
    assert control.active_snapshot().generation_id == "generation_v1"
    assert control.active_snapshot().pointer_version == 3
    replay = control.rollback(
        "generation_v1",
        expected_generation_id="generation_v2",
        expected_pointer_version=2,
        actor_id="release-owner",
        idempotency_key="rollback-v1",
    )
    assert replay.id == rollback.id
    with migrated_engine.connect() as connection:
        failures = connection.execute(
            text(
                "SELECT error_code, outcome, after_generation_id, "
                "committed_pointer_version "
                "FROM lightrag_generation_transition_events "
                "WHERE idempotency_key IN ('activation-fault', 'rollback-fault') "
                "ORDER BY sequence"
            )
        ).mappings().all()
    assert failures == [
        {
            "error_code": "ACTIVATE_FAILED",
            "outcome": "REJECTED",
            "after_generation_id": None,
            "committed_pointer_version": 0,
        },
        {
            "error_code": "ROLLBACK_FAILED",
            "outcome": "REJECTED",
            "after_generation_id": "generation_v2",
            "committed_pointer_version": 2,
        },
    ]


def test_used_control_plane_blocks_downgrade_without_touching_authoritative_ledger(
    migrated_engine: Engine,
    alembic_config: Config,
) -> None:
    control = DurableLightRAGGenerationControlPlane(migrated_engine)
    with migrated_engine.connect() as connection:
        authoritative_before = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM test_cases), "
                "(SELECT count(*) FROM knowledge_nodes), "
                "(SELECT count(*) FROM knowledge_edges), "
                "(SELECT count(*) FROM knowledge_evidence), "
                "(SELECT count(*) FROM audit_logs)"
            )
        ).one()

    _register(control, "generation_v1", 10)
    control.activate(
        "generation_v1",
        expected_generation_id=None,
        expected_pointer_version=0,
        actor_id="release-owner",
        idempotency_key="activate-v1",
    )

    with pytest.raises(DBAPIError, match="control-plane evidence exists"):
        command.downgrade(alembic_config, "20260901_0029")

    with migrated_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260902_0030"
        authoritative_after = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM test_cases), "
                "(SELECT count(*) FROM knowledge_nodes), "
                "(SELECT count(*) FROM knowledge_edges), "
                "(SELECT count(*) FROM knowledge_evidence), "
                "(SELECT count(*) FROM audit_logs)"
            )
        ).one()
    assert authoritative_after == authoritative_before
