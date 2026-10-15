from __future__ import annotations

import importlib.util
import json
import sys
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "object_storage_recovery.py"


def _load() -> ModuleType:
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("phase_f_object_recovery", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


recovery = _load()


def _args(tmp_path: Path) -> Namespace:
    run = "abc123"
    return Namespace(
        command="recover",
        target_name=f"tc-phase-f-pf-sf1-t08-wave7-{run}-disposable",
        target_class="disposable",
        recovery_point=f"pf-sf1-t08-wave7-{run}",
        source_database_name=(
            f"tc_phase_f_pf_sf1_t08_wave7_{run}_source_test_disposable"
        ),
        recovery_database_name=(
            f"tc_phase_f_pf_sf1_t08_wave7_{run}_recovery_test_disposable"
        ),
        source_bucket=f"tc-phase-f-pf-sf1-t08-wave7-{run}-source-disposable",
        recovery_bucket=f"tc-phase-f-pf-sf1-t08-wave7-{run}-recovery-disposable",
        object_prefix=f"pf-sf1-t08-wave7/{run}/objects/",
        dump_file=str(tmp_path / f"pf-sf1-t08-wave7-{run}.dump"),
        temp_directory=str(tmp_path / f"pf-sf1-t08-wave7-{run}-temp"),
        manifest_output=str(
            recovery.EVIDENCE_ROOT / f"pf-sf1-t08-wave7-{run}-manifest.json"
        ),
        output=str(recovery.EVIDENCE_ROOT / f"pf-sf1-t08-wave7-{run}-recovery.json"),
        admin_database_url_env="TC_PHASE_F_ADMIN_DATABASE_URL",
        endpoint_url="http://127.0.0.1:59000",
        minio_access_key_env="TC_PHASE_F_MINIO_ACCESS_KEY",
        minio_secret_key_env="TC_PHASE_F_MINIO_SECRET_KEY",
        clamav_port=53310,
        execute=False,
        confirm=None,
    )


def test_preview_is_exact_and_does_not_read_credentials_or_mutate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    args = _args(tmp_path)
    monkeypatch.setattr(
        recovery,
        "_required_environment",
        lambda _name: pytest.fail("preview read a credential"),
    )
    monkeypatch.setattr(
        recovery,
        "_create_database",
        lambda *a, **k: pytest.fail("preview mutated a database"),
    )

    result = recovery._run_recovery(args)

    assert result["mode"] == "preview"
    assert result["provider"] == "MINIO"
    assert result["safety"]["productionOperations"] == "NOT_RUN"
    assert args.source_database_name in json.dumps(result)
    assert args.recovery_bucket in json.dumps(result)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_database_name", "tc"),
        ("recovery_database_name", "tc_phase_f_wave7_test_disposable"),
        ("source_bucket", "tc-attachments"),
        ("recovery_bucket", "tc-phase-f-pf-sf1-t08-wave7-live-recovery-disposable"),
        ("object_prefix", "shared/objects/"),
        ("endpoint_url", "https://account.r2.cloudflarestorage.com"),
    ],
)
def test_unsafe_or_ambiguous_resource_boundaries_fail_closed(
    tmp_path: Path, field: str, value: str
) -> None:
    args = _args(tmp_path)
    setattr(args, field, value)
    with pytest.raises(recovery.SafetyError):
        recovery._run_recovery(args)


def test_fixture_covers_each_owner_and_mime_twice_and_forces_pagination() -> None:
    objects = recovery._fixture("pf-sf1-t08-wave7/unit/objects/")

    assert len(objects) == 12
    assert len({item["objectKey"] for item in objects}) == 12
    for owner_type in recovery.EXPECTED_OWNER_TYPES:
        for mime_type in recovery.FIXTURE_MIME_TYPES:
            assert sum(
                item["ownerType"] == owner_type and item["mimeType"] == mime_type
                for item in objects
            ) == 2
    assert len(objects) > recovery.PAGE_LIMIT


def test_contract_digest_detects_manifest_tamper() -> None:
    payload = recovery._seal_contract(
        {"schemaVersion": recovery.MANIFEST_SCHEMA, "value": {"count": 12}}
    )
    assert recovery._verify_contract(
        payload, expected_schema=recovery.MANIFEST_SCHEMA
    ) == payload

    payload["value"]["count"] = 11
    with pytest.raises(recovery.SafetyError, match="digest mismatch"):
        recovery._verify_contract(payload, expected_schema=recovery.MANIFEST_SCHEMA)


class _InventoryAdapter:
    def __init__(self, pages: dict[str | None, object], heads: dict[str, object]) -> None:
        self.pages = pages
        self.heads = heads

    def list_objects(self, *, prefix: str, cursor: str | None, limit: int) -> object:
        assert prefix == ""
        assert limit == recovery.PAGE_LIMIT
        return self.pages[cursor]

    def head(self, *, key: str) -> object:
        return self.heads[key]


def _stored(key: str, size: int) -> object:
    from tc_backend.infrastructure.object_storage import StoredObject

    return StoredObject(key, size, datetime(2026, 9, 4, tzinfo=UTC))


def _head(key: str, size: int, mime: str, sha256: str) -> object:
    from tc_backend.infrastructure.object_storage import ObjectMetadata

    return ObjectMetadata(key, mime, size, sha256)


def _page(items: tuple[object, ...], cursor: str | None) -> object:
    from tc_backend.infrastructure.object_storage import StoredObjectPage

    return StoredObjectPage(items, cursor)


def test_object_inventory_detects_duplicate_keys_and_cursor_cycles() -> None:
    duplicate = _InventoryAdapter(
        {
            None: _page((_stored("a", 1), _stored("b", 1), _stored("c", 1)), "next"),
            "next": _page((_stored("a", 1),), None),
        },
        {},
    )
    with pytest.raises(recovery.SafetyError, match="duplicate key"):
        recovery._object_inventory(duplicate)

    cycle = _InventoryAdapter(
        {
            None: _page((_stored("a", 1), _stored("b", 1), _stored("c", 1)), "next"),
            "next": _page((_stored("d", 1), _stored("e", 1), _stored("f", 1)), "next"),
        },
        {},
    )
    with pytest.raises(recovery.SafetyError, match="cursor cycle"):
        recovery._object_inventory(cycle)


@pytest.mark.parametrize("field", ["sizeBytes", "mimeType", "sha256", "objectKey"])
def test_inventory_comparison_detects_missing_corrupt_or_unexpected_objects(
    field: str,
) -> None:
    objects = recovery._fixture("pf-sf1-t08-wave7/unit/objects/")
    entries = [
        {
            "objectKey": item["objectKey"],
            "sizeBytes": item["sizeBytes"],
            "mimeType": item["mimeType"],
            "sha256": item["sha256"],
        }
        for item in objects
    ]
    entries.sort(key=lambda item: str(item["objectKey"]))
    if field == "sizeBytes":
        entries[0][field] += 1
    elif field == "mimeType":
        entries[0][field] = "application/octet-stream"
    elif field == "sha256":
        entries[0][field] = "0" * 64
    else:
        entries[0][field] = "pf-sf1-t08-wave7/unit/objects/orphan.txt"
    with pytest.raises(recovery.SafetyError, match="differs"):
        recovery._assert_inventory_matches_fixture({"entries": entries}, objects)


def test_full_byte_sample_is_nonempty_and_covers_every_owner_and_mime() -> None:
    objects = recovery._fixture("pf-sf1-t08-wave7/unit/objects/")
    selected = recovery._select_sample(objects)

    assert selected
    assert {item["ownerType"] for item in selected} == set(
        recovery.EXPECTED_OWNER_TYPES
    )
    assert {item["mimeType"] for item in selected} == set(
        recovery.FIXTURE_MIME_TYPES
    )
