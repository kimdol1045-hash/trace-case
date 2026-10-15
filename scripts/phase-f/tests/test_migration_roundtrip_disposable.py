from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "migration_roundtrip_disposable.py"
PHASE_F = SCRIPT.parent
if str(PHASE_F) not in sys.path:
    sys.path.insert(0, str(PHASE_F))


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "phase_f_migration_roundtrip_disposable", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


wave9 = _load()


def _resources() -> Any:
    return wave9.Resources("a1b2c3d4", 55519)


def _rehearse_args(*, execute: bool = False, confirm: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(
        run_id="a1b2c3d4",
        port=55519,
        temp_directory="/private/tmp/pf-sf1-t10-wave9-a1b2c3d4",
        dump_file=(
            "/private/tmp/pf-sf1-t10-wave9-a1b2c3d4/"
            "pf-sf1-t10-wave9-a1b2c3d4.dump"
        ),
        output=(
            "docs/phase-f/evidence/"
            "pf-sf1-t10-wave9-a1b2c3d4-never-created.json"
        ),
        execute=execute,
        confirm=confirm,
    )


def test_preview_is_credential_free_non_mutating_and_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("preview crossed the mutation/credential boundary")

    for name in (
        "_assert_no_collisions",
        "_assert_port_free",
        "_current_database",
        "_image_id",
        "_docker_inventory",
        "_write_json_evidence",
        "_command",
    ):
        monkeypatch.setattr(wave9, name, forbidden)

    preview = wave9._execute(_rehearse_args())

    assert preview["mode"] == "preview"
    assert preview["executes"] is False
    assert preview["releaseGates"] == "UNCHANGED_NOT_RUN"
    assert preview["migration"]["head"] == "20260902_0030"
    assert preview["migration"]["immediatePredecessor"] == "20260901_0029"
    assert all(
        "pf-sf1-t10-wave9" in value.replace("_", "-")
        for value in preview["mutates"][:4]
    )


def test_execute_rejects_confirmation_before_any_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        wave9,
        "_assert_no_collisions",
        lambda _resources: (_ for _ in ()).throw(
            AssertionError("execute mutation boundary crossed")
        ),
    )
    with pytest.raises(wave9.SafetyError, match="differs from preview"):
        wave9._execute(_rehearse_args(execute=True, confirm="counterfeit"))


def test_generated_names_comments_and_loopback_port_are_exact() -> None:
    resources = _resources()
    assert resources.target_name == (
        "tc-phase-f-pf-sf1-t10-wave9-a1b2c3d4-disposable"
    )
    assert resources.database == (
        "tc_phase_f_pf_sf1_t10_wave9_a1b2c3d4_test_disposable"
    )
    assert resources.database_comment == (
        "tc-phase-f-rehearsal:disposable:"
        "tc-phase-f-pf-sf1-t10-wave9-a1b2c3d4-disposable"
    )
    assert resources.public_dict()["port"] == {
        "host": "127.0.0.1",
        "generated": 55519,
    }


@pytest.mark.parametrize("run_id", ("prod1234", "live5678", "main9999"))
def test_production_like_generated_target_is_rejected(run_id: str) -> None:
    with pytest.raises(wave9.SafetyError, match="production-like"):
        wave9.Resources(run_id, 55519)


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"actual_name": "foreign"}, "identity mismatch"),
        ({"server_version_num": 150_000}, "PostgreSQL 16"),
        ({"actual_comment": "counterfeit"}, "comment attestation mismatch"),
    ),
)
def test_target_identity_comment_and_version_mismatch_fail_closed(
    overrides: dict[str, object], message: str
) -> None:
    values: dict[str, object] = {
        "actual_name": "expected",
        "expected_name": "expected",
        "server_version_num": 160_000,
        "actual_comment": "exact-comment",
        "expected_comment": "exact-comment",
    }
    values.update(overrides)
    with pytest.raises(wave9.SafetyError, match=message):
        wave9._require_database_identity(**values)


@pytest.mark.parametrize(
    ("table_count", "extensions"),
    ((1, ()), (0, ("vector",)), (2, ("vector",))),
)
def test_nonempty_or_foreign_extension_target_is_rejected(
    table_count: int, extensions: tuple[str, ...]
) -> None:
    with pytest.raises(wave9.SafetyError, match="exactly empty"):
        wave9._require_empty_target(
            table_count=table_count, extensions=extensions
        )


@pytest.mark.parametrize(
    "counts",
    (
        {"generation_count": 1, "transition_count": 0, "pointer_count": 1, "pristine_pointer_count": 1},
        {"generation_count": 0, "transition_count": 1, "pointer_count": 1, "pristine_pointer_count": 1},
        {"generation_count": 0, "transition_count": 0, "pointer_count": 1, "pristine_pointer_count": 0},
        {"generation_count": 0, "transition_count": 0, "pointer_count": 2, "pristine_pointer_count": 1},
    ),
)
def test_nonpristine_head_data_loss_boundary_is_rejected(
    counts: dict[str, int]
) -> None:
    with pytest.raises(wave9.SafetyError, match="downgrade is unsafe"):
        wave9._require_pristine_control_plane(**counts)


class _FakeRevision:
    def __init__(self, revision: str, predecessors: tuple[str, ...]) -> None:
        self.revision = revision
        self._normalized_down_revisions = predecessors
        self.module = SimpleNamespace(upgrade=lambda: None, downgrade=lambda: None)
        self.path = str(
            wave9.ROOT
            / "backend/migrations/versions/20260902_0030_lightrag_generation_control_plane.py"
        )


class _FakeGraph:
    def __init__(
        self,
        *,
        heads: tuple[str, ...],
        predecessor: tuple[str, ...] = ("20260901_0029",),
        missing: frozenset[str] = frozenset(),
    ) -> None:
        self.heads = heads
        self.missing = missing
        self.head = _FakeRevision("20260902_0030", predecessor)
        self.previous = _FakeRevision("20260901_0029", ("20260901_0028",))

    def get_heads(self) -> list[str]:
        return list(self.heads)

    def get_revision(self, value: str) -> _FakeRevision | None:
        if value in self.missing:
            return None
        if value == "20260902_0030":
            return self.head
        if value == "20260901_0029":
            return self.previous
        return None

    def walk_revisions(self, *, base: str, head: str) -> list[_FakeRevision]:
        assert (base, head) == ("base", "heads")
        return [self.head, self.previous]


def test_real_migration_graph_has_one_exact_edge() -> None:
    edge = wave9._resolve_migration_edge()
    assert (edge.head, edge.predecessor) == (
        "20260902_0030",
        "20260901_0029",
    )
    assert len(edge.head_file_sha256) == 64


@pytest.mark.parametrize("heads", ((), ("20260902_0030", "20260901_0029")))
def test_multiple_or_missing_heads_are_rejected(heads: tuple[str, ...]) -> None:
    with pytest.raises(wave9.SafetyError, match="exactly one head"):
        wave9._resolve_migration_edge(_FakeGraph(heads=heads))


def test_unknown_head_is_rejected() -> None:
    with pytest.raises(wave9.SafetyError, match="unknown"):
        wave9._resolve_migration_edge(
            _FakeGraph(
                heads=("20260902_0030",), missing=frozenset({"20260902_0030"})
            )
        )


@pytest.mark.parametrize("predecessors", ((), ("20260901_0029", "20260901_0028")))
def test_ambiguous_or_missing_predecessor_is_rejected(
    predecessors: tuple[str, ...]
) -> None:
    with pytest.raises(wave9.SafetyError, match="exactly one immediate predecessor"):
        wave9._resolve_migration_edge(
            _FakeGraph(heads=("20260902_0030",), predecessor=predecessors)
        )


def test_unknown_predecessor_is_rejected() -> None:
    with pytest.raises(wave9.SafetyError, match="predecessor revision is unknown"):
        wave9._resolve_migration_edge(
            _FakeGraph(
                heads=("20260902_0030",),
                missing=frozenset({"20260901_0029"}),
            )
        )


def test_wrong_predecessor_after_downgrade_is_rejected() -> None:
    with pytest.raises(wave9.VerificationError, match="unexpected revision"):
        wave9._require_expected_revision(
            "20260901_0028", "20260901_0029", stage="downgrade"
        )


def test_surviving_data_invariant_excludes_only_head_tables_and_revision_bookkeeping() -> None:
    assert wave9.DOWNGRADE_DATA_EXCLUSIONS == wave9.HEAD_TABLES | {
        "alembic_version"
    }


@pytest.mark.parametrize(
    "revisions",
    ((), ("20260902_0030", "20260901_0029"), ("counterfeit",)),
)
def test_partial_multiple_or_unknown_database_revision_is_rejected(
    revisions: tuple[str, ...]
) -> None:
    with pytest.raises(wave9.SafetyError):
        wave9._validate_revision_values(revisions)


@pytest.mark.parametrize("operation", ("downgrade", "upgrade"))
def test_alembic_failure_is_explicit_and_secret_safe(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=1, stdout=None, stderr=b"credential-bearing detail"
        ),
    )
    database = wave9.DatabaseTarget(
        "tc_phase_f_test_disposable",
        "GENERATED_IN_MEMORY",
        "postgresql://user:hidden@127.0.0.1:55519/tc_phase_f_test_disposable",
        "postgresql://user:hidden@127.0.0.1:55519/tc_phase_f_test_disposable",
    )
    with pytest.raises(wave9.VerificationError, match=f"Alembic {operation} failed") as raised:
        wave9._alembic(database, operation, "20260901_0029")
    assert "credential-bearing" not in str(raised.value)
    assert "hidden" not in str(raised.value)


def _fingerprint(*, schema: str = "schema", data: str = "data") -> dict[str, object]:
    return {
        "schemaSha256": schema,
        "schemaObjectCounts": {"relations": 1},
        "schemaCategorySha256": {"relations": schema},
        "roleCatalog": {"digest": "roles"},
        "tables": [{"table": "safe_table", "rowCount": 1, "sha256": data}],
    }


@pytest.mark.parametrize(
    ("final", "normalized_final", "message"),
    (
        (_fingerprint(schema="changed"), {"digest": "data"}, "catalog differs"),
        (_fingerprint(data="changed"), {"digest": "data"}, "raw data drift"),
        (_fingerprint(), {"digest": "changed"}, "normalized data differs"),
    ),
)
def test_schema_or_data_mismatch_is_rejected(
    final: dict[str, object],
    normalized_final: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(wave9.VerificationError, match=message):
        wave9._require_final_equality(
            baseline=_fingerprint(),
            final=final,
            normalized_baseline={"digest": "data"},
            normalized_final=normalized_final,
        )


def test_only_pristine_pointer_seed_timestamp_raw_drift_is_normalized() -> None:
    baseline = _fingerprint()
    final = _fingerprint()
    baseline["tables"] = [
        {
            "table": "lightrag_active_generation_pointer",
            "rowCount": 1,
            "sha256": "before",
        }
    ]
    final["tables"] = [
        {
            "table": "lightrag_active_generation_pointer",
            "rowCount": 1,
            "sha256": "after",
        }
    ]
    assert wave9._require_final_equality(
        baseline=baseline,
        final=final,
        normalized_baseline={"digest": "same"},
        normalized_final={"digest": "same"},
    ) == ["lightrag_active_generation_pointer"]


def test_source_drift_is_rejected() -> None:
    expected = wave9._digest_record(
        {"fingerprint": "before"}, version="tc.postgresql-fingerprint.v1"
    )
    with pytest.raises(wave9.SafetyError, match="database state changed"):
        wave9._require_preservation(
            expected_source_digest=expected,
            actual_source_fingerprint={"fingerprint": "after"},
            expected_databases={"digest": "same"},
            actual_databases={"digest": "same"},
        )


def test_cleanup_preservation_rejects_docker_inventory_drift() -> None:
    source = {"fingerprint": "same"}
    with pytest.raises(wave9.SafetyError, match="Docker state changed"):
        wave9._require_preservation(
            expected_source_digest=wave9._digest_record(
                source, version="tc.postgresql-fingerprint.v1"
            ),
            actual_source_fingerprint=source,
            expected_databases={"digest": "same"},
            actual_databases={"digest": "same"},
            expected_docker={"digest": "before"},
            actual_docker={"digest": "after"},
        )


def test_unexpected_extra_project_resource_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = _resources()
    monkeypatch.setattr(
        wave9,
        "_labeled_resource_inventory",
        lambda _resources: {
            "containers": [resources.container, "unexpected"],
            "volumes": [resources.volume],
            "networks": [resources.network],
        },
    )
    with pytest.raises(wave9.SafetyError, match="unexpected resources"):
        wave9._assert_exact_project_inventory(resources)


def test_evidence_canonical_digest_detects_tamper() -> None:
    sealed = wave9._seal(
        {"schemaVersion": wave9.SCHEMA, "mode": "LOCAL_DISPOSABLE_PASS"}
    )
    wave9._verify_seal(sealed, schema=wave9.SCHEMA)
    sealed["mode"] = "NOT_RUN"
    with pytest.raises(wave9.SafetyError, match="canonical digest mismatch"):
        wave9._verify_seal(sealed, schema=wave9.SCHEMA)


def _write_valid_evidence(path: Path) -> None:
    wave9._write_json_evidence(
        path,
        wave9._seal(
            {"schemaVersion": wave9.SCHEMA, "mode": "LOCAL_DISPOSABLE_PASS"}
        ),
    )


def test_evidence_loader_rejects_symlink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    evidence = evidence_root / "evidence.json"
    _write_valid_evidence(evidence)
    alias = evidence_root / "alias.json"
    alias.symlink_to(evidence)
    monkeypatch.setattr(wave9, "EVIDENCE_ROOT", evidence_root)
    with pytest.raises(wave9.SafetyError, match="symlink"):
        wave9._load_evidence(str(alias))


def test_evidence_loader_rejects_hardlink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    evidence = evidence_root / "evidence.json"
    _write_valid_evidence(evidence)
    alias = evidence_root / "alias.json"
    os.link(evidence, alias)
    monkeypatch.setattr(wave9, "EVIDENCE_ROOT", evidence_root)
    with pytest.raises(wave9.SafetyError, match="exactly one hard link"):
        wave9._load_evidence(str(alias))


def test_evidence_output_refuses_overwrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import rehearse

    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    evidence = evidence_root / "evidence.json"
    evidence.write_text("{}", encoding="utf-8")
    evidence.chmod(0o600)
    monkeypatch.setattr(rehearse, "EVIDENCE_ROOT", evidence_root)
    with pytest.raises(wave9.SafetyError, match="overwrite"):
        wave9._safe_evidence_output(str(evidence))


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("databaseComment", "counterfeit-comment"),
        ("database", "foreign_database"),
        ("project", "tc-pf-sf1-t10-wave9-foreign"),
    ),
)
def test_cleanup_rejects_target_or_comment_mismatch_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    replacement: str,
) -> None:
    resources = _resources()
    target = resources.public_dict()
    target[field] = replacement
    monkeypatch.setattr(
        wave9,
        "_load_evidence",
        lambda _value: (
            wave9.EVIDENCE_ROOT / "unused.json",
            {"target": target, "mode": "LOCAL_DISPOSABLE_PASS"},
            "0" * 64,
            {},
        ),
    )
    monkeypatch.setattr(
        wave9,
        "_assert_owned_resources",
        lambda _resources: (_ for _ in ()).throw(
            AssertionError("cleanup mutation boundary crossed")
        ),
    )
    args = argparse.Namespace(
        rehearsal_evidence="unused.json",
        output=(
            "docs/phase-f/evidence/"
            "pf-sf1-t10-wave9-a1b2c3d4-cleanup-never-created.json"
        ),
        run_id="a1b2c3d4",
        port=55519,
        execute=True,
        confirm="unused",
    )
    with pytest.raises(wave9.SafetyError, match="differ from evidence"):
        wave9._cleanup(args)


def test_prior_evidence_hash_tamper_blocks_preservation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    artifact = evidence_root / "prior.json"
    artifact.write_text('{"result":"PASS"}', encoding="utf-8")
    artifact.chmod(0o600)
    monkeypatch.setattr(wave9, "ROOT", tmp_path)
    monkeypatch.setattr(wave9, "EVIDENCE_ROOT", evidence_root)
    inventory = wave9._prior_evidence_inventory(exclude=set())
    artifact.write_text('{"result":"TAMPERED"}', encoding="utf-8")
    artifact.chmod(0o600)
    with pytest.raises(wave9.SafetyError, match="hash changed"):
        wave9._verify_prior_evidence(inventory)


def test_secret_safe_evidence_rejects_url_secret_and_raw_fields() -> None:
    with pytest.raises(wave9.SafetyError, match="database URL"):
        wave9._secret_safe_payload({"value": "postgresql://loopback/redacted"}, ())
    with pytest.raises(wave9.SafetyError, match="secret value"):
        wave9._secret_safe_payload({"value": "opaque-value"}, ("opaque-value",))
    with pytest.raises(wave9.SafetyError, match="sensitive field"):
        wave9._secret_safe_payload({"rawRow": "omitted"}, ())
