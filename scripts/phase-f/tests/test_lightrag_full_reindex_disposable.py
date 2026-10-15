from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tc_backend.knowledge.lightrag import OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID
from tc_backend.knowledge.lightrag_local_rehearsal import (
    build_network_free_components,
)

SCRIPT = Path(__file__).resolve().parents[1] / "lightrag_full_reindex_disposable.py"
PHASE_F = SCRIPT.parent
if str(PHASE_F) not in sys.path:
    sys.path.insert(0, str(PHASE_F))


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "phase_f_lightrag_full_reindex_disposable", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


wave8 = _load()


def _resources() -> object:
    return wave8.Resources("a1b2c3d4", 55448, 55449)


def test_preview_is_fail_closed_and_does_not_inspect_or_mutate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("preview crossed the mutation boundary")

    for name in (
        "_assert_no_collisions",
        "_assert_ports_free",
        "_current_database",
        "_command",
        "_write_json_evidence",
    ):
        monkeypatch.setattr(wave8, name, forbidden)
    args = argparse.Namespace(
        run_id="a1b2c3d4",
        source_port=55448,
        auxiliary_port=55449,
        temp_directory="/private/tmp/pf-sf1-t09-wave8-a1b2c3d4",
        dump_file=(
            "/private/tmp/pf-sf1-t09-wave8-a1b2c3d4/"
            "pf-sf1-t09-wave8-a1b2c3d4.dump"
        ),
        output="docs/phase-f/evidence/pf-sf1-t09-wave8-preview-never-created.json",
        execute=False,
        confirm=None,
    )

    preview = wave8._execute(args)

    assert preview["mode"] == "preview"
    assert preview["executes"] is False
    assert preview["releaseGates"] == "UNCHANGED_NOT_RUN"
    assert all(
        "pf-sf1-t09-wave8" in value or "pf_sf1_t09_wave8" in value
        for value in preview["mutates"]
    )


def test_execute_rejects_confirmation_mismatch_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        wave8,
        "_assert_no_collisions",
        lambda _resources: (_ for _ in ()).throw(
            AssertionError("execute mutation boundary crossed")
        ),
    )
    args = argparse.Namespace(
        run_id="a1b2c3d4",
        source_port=55448,
        auxiliary_port=55449,
        temp_directory="/private/tmp/pf-sf1-t09-wave8-a1b2c3d4",
        dump_file=(
            "/private/tmp/pf-sf1-t09-wave8-a1b2c3d4/"
            "pf-sf1-t09-wave8-a1b2c3d4.dump"
        ),
        output="docs/phase-f/evidence/pf-sf1-t09-wave8-never-created.json",
        execute=True,
        confirm="counterfeit confirmation",
    )

    with pytest.raises(wave8.SafetyError, match="differs from preview"):
        wave8._execute(args)


def test_generated_resources_comments_and_workspace_are_exact() -> None:
    resources = _resources()
    target = resources.target()

    assert resources.target_name == "tc-phase-f-pf-sf1-t09-wave8-a1b2c3d4-disposable"
    assert resources.source_database.endswith("_source_test_disposable")
    assert resources.auxiliary_database.endswith("_aux_test_disposable")
    assert "pf-sf1-t09-wave8" in target.authoritative_comment
    assert "pf-sf1-t09-wave8" in target.auxiliary_comment
    assert resources.seed_workspace == "pf-sf1-t09-wave8-a1b2c3d4-seed"
    assert wave8._workspace_graph_name(
        "pf-sf1-t09-wave8-a1b2c3d4-0123456789ab"
    ).endswith("_chunk_entity_relation")


def _semantic_graph_rows(
    *,
    node_ids: tuple[str, str, str] = ("1", "2", "3"),
    edge_id: str = "4",
    alice_description: str = "first node",
    edge_target: str = "BOB",
    edge_weight: float = 1.0,
    created_at: int = 1,
) -> tuple[tuple[tuple[object, ...], ...], tuple[tuple[object, ...], ...]]:
    properties = {
        "ALICE": {
            "entity_id": "ALICE",
            "description": alice_description,
            "entity_type": "PERSON",
            "created_at": created_at,
        },
        "BOB": {
            "entity_id": "BOB",
            "description": "second node",
            "entity_type": "PERSON",
            "created_at": created_at,
        },
        "CAROL": {
            "entity_id": "CAROL",
            "description": "third node",
            "entity_type": "PERSON",
            "created_at": created_at,
        },
    }
    nodes = tuple(
        (node_id, wave8._canonical_bytes(properties[entity]).decode())
        for node_id, entity in zip(node_ids, properties, strict=True)
    )
    edges = (
        (
            edge_id,
            wave8._canonical_bytes(properties["ALICE"]).decode(),
            wave8._canonical_bytes(properties[edge_target]).decode(),
            wave8._canonical_bytes(
                {
                    "created_at": created_at,
                    "description": "semantic relation",
                    "weight": edge_weight,
                }
            ).decode(),
        ),
    )
    return nodes, edges


def _inspection_with_graph_digest(digest: dict[str, str]) -> dict[str, object]:
    return {"graph": {"digest": digest}}


def test_semantic_graph_digest_omits_workspace_local_age_internal_ids() -> None:
    first_nodes, first_edges = _semantic_graph_rows()
    second_nodes, second_edges = _semantic_graph_rows(
        node_ids=("9001", "9002", "9003"), edge_id="9004", created_at=999
    )

    first = wave8._semantic_graph_digest(first_nodes, first_edges)
    second = wave8._semantic_graph_digest(second_nodes, second_edges)

    assert first == second
    agreement = wave8._assert_semantic_graph_digest_agreement(
        {
            "prior": _inspection_with_graph_digest(first),
            "cutover": _inspection_with_graph_digest(second),
            "failedActivation": _inspection_with_graph_digest(first),
        }
    )
    assert agreement == {
        "generationCount": 3,
        "equal": True,
        "version": "tc.lightrag.semantic-age-graph.v2",
        "sha256": first["sha256"],
    }


@pytest.mark.parametrize(
    "semantic_change",
    ("node_property", "edge_property", "edge_topology"),
)
def test_semantic_graph_agreement_rejects_content_or_topology_change(
    semantic_change: str,
) -> None:
    baseline_rows = _semantic_graph_rows()
    changed_rows = {
        "node_property": _semantic_graph_rows(alice_description="changed node"),
        "edge_property": _semantic_graph_rows(edge_weight=2.0),
        "edge_topology": _semantic_graph_rows(edge_target="CAROL"),
    }[semantic_change]
    baseline = wave8._semantic_graph_digest(*baseline_rows)
    changed = wave8._semantic_graph_digest(*changed_rows)

    assert changed != baseline
    with pytest.raises(
        wave8.VerificationError,
        match="semantic AGE graph differs across immutable generations",
    ):
        wave8._assert_semantic_graph_digest_agreement(
            {
                "prior": _inspection_with_graph_digest(baseline),
                "cutover": _inspection_with_graph_digest(baseline),
                "failedActivation": _inspection_with_graph_digest(changed),
            }
        )


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("authorityId", "tc-phase-f-pf-sf1-t09-wave7-a1b2c3d4-disposable"),
        ("providerId", "counterfeit-provider"),
        ("workspace", "pf-sf1-t09-wave8-a1b2c3d4-0123456789abcdef"),
        ("runtimeConfigDigest", "sha256:" + "A" * 64),
    ),
)
def test_network_free_bootstrap_rejects_attestation_mismatch(
    field: str,
    replacement: str,
) -> None:
    attestation = {
        "authorityId": "tc-phase-f-pf-sf1-t09-wave8-a1b2c3d4-disposable",
        "providerId": OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID,
        "workspace": "pf-sf1-t09-wave8-a1b2c3d4-0123456789ab",
        "runtimeConfigDigest": "sha256:" + "a" * 64,
    }
    attestation[field] = replacement

    with pytest.raises(ValueError, match="wave-8 bootstrap"):
        build_network_free_components(attestation)


def test_network_free_bootstrap_returns_only_reviewed_callables() -> None:
    result = build_network_free_components(
        {
            "authorityId": "tc-phase-f-pf-sf1-t09-wave8-a1b2c3d4-disposable",
            "providerId": OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID,
            "workspace": "pf-sf1-t09-wave8-a1b2c3d4-0123456789ab",
            "runtimeConfigDigest": "sha256:" + "a" * 64,
        }
    )

    assert set(result) == {"llm_model_func", "embedding_func"}
    assert result["embedding_func"].embedding_dim == 1024


def test_non_empty_auxiliary_target_is_rejected() -> None:
    wave8._require_empty_target(public_tables=0, graph_count=0)

    with pytest.raises(wave8.SafetyError, match="must start with no public tables"):
        wave8._require_empty_target(public_tables=1, graph_count=0)
    with pytest.raises(wave8.SafetyError, match="must start with no public tables"):
        wave8._require_empty_target(public_tables=0, graph_count=1)


def test_keyset_pagination_is_complete_and_monotonic() -> None:
    values = (("a",), ("b",), ("c",))

    def fetch(cursor: str | None, limit: int) -> tuple[tuple[str], ...]:
        selected = tuple(row for row in values if cursor is None or row[0] > cursor)
        return selected[:limit]

    rows, pages = wave8._fetch_keyset_pages(fetch, page_size=2)

    assert rows == values
    assert pages == 2


@pytest.mark.parametrize(
    "pages",
    (
        (("b",), ("a",)),
        (("a",), ("a",)),
        (("a",), ("b",), ("c",)),
    ),
)
def test_keyset_pagination_rejects_unordered_duplicate_or_oversize_pages(
    pages: tuple[tuple[str], ...],
) -> None:
    with pytest.raises(wave8.VerificationError):
        wave8._fetch_keyset_pages(lambda _cursor, _limit: pages, page_size=2)


def test_keyset_pagination_rejects_repeated_cursor_across_pages() -> None:
    with pytest.raises(wave8.VerificationError, match="cursor did not advance"):
        wave8._fetch_keyset_pages(
            lambda _cursor, _limit: (("same",),), page_size=1
        )


def test_evidence_digest_detects_tamper() -> None:
    sealed = wave8._seal({"schemaVersion": wave8.SCHEMA, "result": "PASS"})
    wave8._verify_seal(sealed, schema=wave8.SCHEMA)
    sealed["result"] = "NOT_RUN"

    with pytest.raises(wave8.SafetyError, match="digest mismatch"):
        wave8._verify_seal(sealed, schema=wave8.SCHEMA)


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("sourceDatabaseComment", "counterfeit-comment"),
        ("seedWorkspace", "pf-sf1-t09-wave8-a1b2c3d4-wrong"),
        ("project", "tc-pf-sf1-t09-wave8-counterfeit"),
    ),
)
def test_cleanup_rejects_target_comment_or_workspace_mismatch_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    replacement: str,
) -> None:
    resources = _resources()
    target = resources.public_dict()
    target[field] = replacement
    evidence = {
        "target": target,
        "mode": "LOCAL_DISPOSABLE_NETWORK_FREE_PASS",
    }
    monkeypatch.setattr(
        wave8,
        "_load_evidence",
        lambda _value: (wave8.EVIDENCE_ROOT / "unused.json", evidence, "0" * 64, {}),
    )
    monkeypatch.setattr(
        wave8,
        "_assert_owned_resources",
        lambda _resources: (_ for _ in ()).throw(
            AssertionError("cleanup mutation boundary crossed")
        ),
    )
    args = argparse.Namespace(
        rehearsal_evidence="unused.json",
        output="docs/phase-f/evidence/pf-sf1-t09-wave8-cleanup-never-created.json",
        run_id="a1b2c3d4",
        source_port=55448,
        auxiliary_port=55449,
        execute=True,
        confirm="unused",
    )

    with pytest.raises(wave8.SafetyError, match="differ from evidence"):
        wave8._cleanup(args)


def test_prior_evidence_hash_tamper_blocks_cleanup_preservation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    artifact = evidence_root / "prior.json"
    artifact.write_text('{"result":"PASS"}', encoding="utf-8")
    artifact.chmod(0o600)
    monkeypatch.setattr(wave8, "ROOT", tmp_path)
    monkeypatch.setattr(wave8, "EVIDENCE_ROOT", evidence_root)
    inventory = wave8._prior_evidence_inventory(exclude=set())
    artifact.write_text('{"result":"TAMPERED"}', encoding="utf-8")
    artifact.chmod(0o600)

    with pytest.raises(wave8.SafetyError, match="hash changed"):
        wave8._verify_prior_evidence(inventory)


def test_secret_safe_evidence_rejects_urls_corpus_and_credentials() -> None:
    for payload, forbidden in (
        ({"value": "postgresql://loopback/redacted"}, ()),
        ({"value": "forbidden corpus"}, ("forbidden corpus",)),
        ({"value": "credential-value"}, ("credential-value",)),
    ):
        with pytest.raises(wave8.SafetyError):
            wave8._secret_safe_payload(payload, forbidden)
