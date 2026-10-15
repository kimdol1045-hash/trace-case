from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import urllib.error
import urllib.request
from argparse import Namespace
from email.message import Message
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit

import psycopg
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

SCRIPT = Path(__file__).resolve().parents[1] / "rehearse.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("phase_f_rehearse", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve postponed annotations through the registered module.
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rehearsal = _load()


def _complete_fingerprint() -> dict[str, object]:
    category_counts = {category: 0 for category in rehearsal.CATALOG_CATEGORIES}
    category_counts["sequences"] = 1
    category_digests = {
        category: chr(97 + index % 6) * 64
        for index, category in enumerate(sorted(rehearsal.CATALOG_CATEGORIES))
    }
    return {
        "catalogFingerprintVersion": rehearsal.CATALOG_FINGERPRINT_VERSION,
        "schemaSha256": "a" * 64,
        "schemaObjectCounts": category_counts,
        "schemaCategorySha256": category_digests,
        "dataSha256": "b" * 64,
        "tableCount": 1,
        "totalRows": 1,
        "tables": [
            {"table": "workspace_settings", "rowCount": 1, "sha256": "c" * 64}
        ],
    }


def _complete_backup_payload(evidence_path: Path, dump_file: Path) -> dict[str, object]:
    target = rehearsal.Target("tc-phase-f-source-disposable", "disposable")
    dump_sha = "d" * 64
    return {
        "ok": True,
        "mode": "executed",
        "executes": True,
        "task": "PF-SF1-T07",
        "action": "POSTGRES-BACKUP",
        "target": {"name": target.name, "class": target.target_class},
        "confirmationRequired": rehearsal._confirmation(
            "PF-SF1-T07", "POSTGRES-BACKUP", target
        ),
        "mutates": ["new custom-format dump outside the repository"],
        "reads": ["declared attested PostgreSQL source"],
        "steps": ["validate exact source and archive"],
        "safety": {
            "rejectsProductionLikeNames": True,
            "databaseCommentAttestationRequired": True,
            "credentialsPrinted": False,
        },
        "evidenceFile": str(evidence_path.relative_to(rehearsal.ROOT)),
        "rpoBoundary": "measured only; no production RPO promise",
        "preflight": {
            "databaseName": "tc_phase_f_source_test_disposable",
            "serverVersionNum": 160012,
            "attested": True,
            "databaseAttestation": target.database_attestation,
            "publicTableCount": 1,
            "alembicRevision": rehearsal.HEAD_REVISION,
        },
        "client": {
            "mode": "host",
            "composeService": None,
            "credentialsInArgv": False,
        },
        "dumpFile": str(dump_file),
        "dumpBytes": 20,
        "dumpSha256": dump_sha,
        "dumpFileSafety": {
            "singleOpenNoFollowFileDescriptor": True,
            "pathnameReopened": False,
            "regularFile": True,
            "permissionsAtMost0600": True,
            "singleLink": True,
            "identity": {
                "device": 1,
                "inode": 2,
                "size": 20,
                "mtimeNs": 3,
                "ctimeNs": 4,
                "modeOctal": "0600",
                "linkCount": 1,
            },
            "verifiedBeforeAndAfterStages": [
                "SHA-256",
                "pg_restore archive inventory",
            ],
        },
        "archive": {
            "validated": True,
            "entryCount": 7,
            "listingSha256": "e" * 64,
            "dumpBytes": 20,
            "dumpSha256": dump_sha,
        },
        "sourceFingerprint": _complete_fingerprint(),
        "recoveryPoint": {
            "snapshotId": "00000003-00000001-1",
            "capturedAtUtc": "2026-09-04T00:00:00+00:00",
            "walLsn": "0/1234567",
            "transactionSnapshot": "1:1:",
            "measuredAgeAtBackupCompletionSeconds": 0.25,
            "productionRpoPromise": "NOT_CLAIMED",
        },
        "timing": {
            "startedAtUtc": "2026-09-04T00:00:00+00:00",
            "finishedAtUtc": "2026-09-04T00:00:01+00:00",
            "backupSeconds": 1.0,
        },
    }


def test_migration_roundtrip_constants_follow_the_actual_one_step_head() -> None:
    backend_root = SCRIPT.parents[2] / "backend"
    migration_graph = ScriptDirectory.from_config(
        Config(str(backend_root / "alembic.ini"))
    )
    (head,) = migration_graph.get_heads()
    head_revision = migration_graph.get_revision(head)

    assert rehearsal.HEAD_REVISION == head
    assert rehearsal.ROLLBACK_REVISION == head_revision.down_revision


def test_preview_never_reads_database_environment_or_runs_commands(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("TC_PHASE_F_BENCHMARK_DATABASE_URL", raising=False)
    monkeypatch.setattr(
        rehearsal,
        "_command",
        lambda *args, **kwargs: pytest.fail("preview called a subprocess"),
    )
    result = rehearsal.main(
        [
            "benchmark-cases",
            "--target-name",
            "tc-phase-f-cases-disposable",
            "--target-class",
            "disposable",
            "--database-url-env",
            "TC_PHASE_F_BENCHMARK_DATABASE_URL",
            "--database-name",
            "tc_phase_f_cases_test_disposable",
        ]
    )
    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "preview"
    assert payload["executes"] is False
    assert payload["mutates"] == ["declared empty disposable PostgreSQL database"]
    assert payload["limitations"]


@pytest.mark.parametrize(
    ("name", "target_class"),
    [
        ("tc-phase-f-prod-disposable", "disposable"),
        ("tc-phase-f-recovery-production-staging-copy", "staging-copy"),
        ("staging", "staging-copy"),
        ("tc-phase-f-recovery-disposable", "staging-copy"),
    ],
)
def test_target_rejects_production_like_or_ambiguous_names(
    name: str, target_class: str
) -> None:
    with pytest.raises(rehearsal.SafetyError):
        rehearsal.Target(name, target_class)


def test_database_url_requires_exact_name_loopback_and_safe_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = rehearsal.Target("tc-phase-f-cases-disposable", "disposable")
    args = Namespace(
        database_name="tc_phase_f_cases_test_disposable",
        database_url_env="TC_PHASE_F_DATABASE_URL",
    )
    monkeypatch.setenv(
        "TC_PHASE_F_DATABASE_URL",
        "postgresql+psycopg://tc:secret@127.0.0.1:55432/tc_phase_f_cases_test_disposable",
    )
    database = rehearsal._database_target(args, target)
    assert database.expected_name == "tc_phase_f_cases_test_disposable"
    assert database.libpq_url.startswith("postgresql://")

    monkeypatch.setenv(
        "TC_PHASE_F_DATABASE_URL",
        "postgresql://tc:secret@db.example.test/tc_phase_f_cases_test_disposable",
    )
    with pytest.raises(rehearsal.SafetyError, match="loopback"):
        rehearsal._database_target(args, target)


def test_child_process_environment_drops_unrelated_database_and_phase_f_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PGPASSWORD", "ambient-database-secret")
    monkeypatch.setenv("TC_PHASE_F_API_TOKEN", "unrelated-api-secret")
    monkeypatch.setenv("TC_PHASE_F_R2_PRESIGNED_URL", "unrelated-signed-url")
    monkeypatch.setenv("TC_ENVIRONMENT", "staging")

    environment = rehearsal._clean_process_environment()

    assert "PGPASSWORD" not in environment
    assert "TC_PHASE_F_API_TOKEN" not in environment
    assert "TC_PHASE_F_R2_PRESIGNED_URL" not in environment
    assert environment["TC_ENVIRONMENT"] == "staging"


def test_compose_postgresql_client_inherits_secret_by_name_not_argv() -> None:
    database = rehearsal.DatabaseTarget(
        "tc_phase_f_restore_test_disposable",
        "TC_PHASE_F_DATABASE_URL",
        "postgresql://redacted",
        "postgresql://redacted",
        (
            ("PGDATABASE", "tc_phase_f_restore_test_disposable"),
            ("PGHOST", "127.0.0.1"),
            ("PGPASSWORD", "never-render-this-secret"),
            ("PGPORT", "55432"),
            ("PGUSER", "tc"),
        ),
    )
    client = rehearsal.PostgreSQLClient("compose-exec", (("docker", "docker"),))

    argv, environment = rehearsal._postgresql_command(
        client, "pg_dump", database, "--format=custom"
    )

    assert "never-render-this-secret" not in argv
    assert ("--env", "PGPASSWORD") in list(zip(argv, argv[1:], strict=False))
    assert environment["PGPASSWORD"] == "never-render-this-secret"
    assert environment["PGHOST"] == "127.0.0.1"
    assert environment["PGPORT"] == "5432"
    assert argv[-3:] == ["postgres", "pg_dump", "--format=custom"]


def test_exclusive_dump_writer_never_deletes_or_overwrites_existing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dump_file = tmp_path / "existing.dump"
    dump_file.write_bytes(b"keep-me")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("subprocess ran for an existing dump"),
    )

    with pytest.raises(FileExistsError):
        rehearsal._command_to_new_file(
            ["pg_dump"], dump_file, env={"PATH": "/usr/bin"}
        )

    assert dump_file.read_bytes() == b"keep-me"


def test_exclusive_dump_writer_creates_private_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dump_file = tmp_path / "new.dump"

    def write_dump(*args: object, **kwargs: object) -> None:
        output = kwargs["stdout"]
        assert hasattr(output, "write")
        output.write(b"custom-archive")

    monkeypatch.setattr(subprocess, "run", write_dump)

    rehearsal._command_to_new_file(
        ["pg_dump"], dump_file, env={"PATH": "/usr/bin"}
    )

    assert dump_file.read_bytes() == b"custom-archive"
    assert dump_file.stat().st_mode & 0o777 == 0o600


def test_failed_dump_cleanup_never_unlinks_a_replaced_inode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dump_file = tmp_path / "new.dump"

    def replace_then_fail(*args: object, **kwargs: object) -> None:
        del args, kwargs
        dump_file.unlink()
        dump_file.write_bytes(b"replacement-owned-by-someone-else")
        raise subprocess.CalledProcessError(1, ["pg_dump"])

    monkeypatch.setattr(subprocess, "run", replace_then_fail)

    with pytest.raises(subprocess.CalledProcessError):
        rehearsal._command_to_new_file(
            ["pg_dump"], dump_file, env={"PATH": "/usr/bin"}
        )

    assert dump_file.read_bytes() == b"replacement-owned-by-someone-else"


def test_json_evidence_is_private_and_never_overwrites(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    rehearsal._write_json_evidence(evidence, {"safe": True})
    assert evidence.stat().st_mode & 0o777 == 0o600
    original = evidence.read_bytes()

    with pytest.raises(FileExistsError):
        rehearsal._write_json_evidence(evidence, {"safe": False})

    assert evidence.read_bytes() == original


def test_backup_evidence_loader_rejects_symlink_before_resolve(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    evidence = evidence_root / "backup.json"
    dump_file = tmp_path / "source.dump"
    monkeypatch.setattr(rehearsal, "ROOT", tmp_path)
    monkeypatch.setattr(rehearsal, "EVIDENCE_ROOT", evidence_root)
    payload = _complete_backup_payload(evidence, dump_file)
    rehearsal._write_json_evidence(evidence, payload)
    alias = evidence_root / "alias.json"
    alias.symlink_to(evidence)

    with pytest.raises(rehearsal.SafetyError, match="symlink"):
        rehearsal._load_backup_evidence(str(alias))


def test_backup_evidence_loader_enforces_complete_bound_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    evidence = evidence_root / "backup.json"
    dump_file = tmp_path / "source.dump"
    monkeypatch.setattr(rehearsal, "ROOT", tmp_path)
    monkeypatch.setattr(rehearsal, "EVIDENCE_ROOT", evidence_root)
    payload = _complete_backup_payload(evidence, dump_file)
    rehearsal._write_json_evidence(evidence, payload)

    loaded_path, loaded, file_safety = rehearsal._load_backup_evidence(str(evidence))
    assert loaded_path == evidence
    assert loaded["archive"]["entryCount"] == 7
    assert file_safety["verifiedBeforeAndAfterStages"] == ["JSON read/parse"]

    invalid = evidence_root / "invalid.json"
    payload["archive"]["entryCount"] = 0  # type: ignore[index]
    payload["evidenceFile"] = str(invalid.relative_to(tmp_path))
    rehearsal._write_json_evidence(invalid, payload)
    with pytest.raises(rehearsal.SafetyError, match="archive inventory"):
        rehearsal._load_backup_evidence(str(invalid))


def test_backup_evidence_rejects_unbound_source_and_incomplete_fingerprint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    monkeypatch.setattr(rehearsal, "ROOT", tmp_path)
    monkeypatch.setattr(rehearsal, "EVIDENCE_ROOT", evidence_root)
    dump_file = tmp_path / "source.dump"

    unbound = evidence_root / "unbound.json"
    payload = _complete_backup_payload(unbound, dump_file)
    payload["preflight"]["databaseAttestation"] = "wrong"  # type: ignore[index]
    rehearsal._write_json_evidence(unbound, payload)
    with pytest.raises(rehearsal.SafetyError, match="preflight"):
        rehearsal._load_backup_evidence(str(unbound))

    incomplete = evidence_root / "incomplete.json"
    payload = _complete_backup_payload(incomplete, dump_file)
    del payload["sourceFingerprint"]["schemaCategorySha256"]  # type: ignore[index]
    rehearsal._write_json_evidence(incomplete, payload)
    with pytest.raises(rehearsal.SafetyError, match="sourceFingerprint"):
        rehearsal._load_backup_evidence(str(incomplete))


def test_backup_evidence_loader_rejects_same_inode_mutation_after_json_load(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    evidence = evidence_root / "backup.json"
    dump_file = tmp_path / "source.dump"
    monkeypatch.setattr(rehearsal, "ROOT", tmp_path)
    monkeypatch.setattr(rehearsal, "EVIDENCE_ROOT", evidence_root)
    rehearsal._write_json_evidence(
        evidence, _complete_backup_payload(evidence, dump_file)
    )
    inode_before = evidence.stat().st_ino
    original_json_load = rehearsal.json.load

    def mutate_after_json_load(*args: object, **kwargs: object) -> object:
        loaded = original_json_load(*args, **kwargs)
        with evidence.open("r+b") as mutated:
            first = mutated.read(1)
            mutated.seek(0)
            mutated.write(b"[" if first != b"[" else b"{")
            mutated.flush()
            os.fsync(mutated.fileno())
        assert evidence.stat().st_ino == inode_before
        return loaded

    monkeypatch.setattr(rehearsal.json, "load", mutate_after_json_load)

    with pytest.raises(rehearsal.SafetyError, match="JSON read/parse"):
        rehearsal._load_backup_evidence(str(evidence))


def test_pinned_dump_rejects_same_inode_mutation_after_sha(
    tmp_path: Path,
) -> None:
    dump_file = tmp_path / "source.dump"
    dump_file.write_bytes(b"custom-dump-fixture")
    dump_file.chmod(0o600)
    inode_before = dump_file.stat().st_ino

    with pytest.raises(rehearsal.SafetyError, match="SHA-256"):
        with rehearsal.PinnedReadFile(
            dump_file, label="database dump", require_nonempty=True
        ) as dump_source:

            def mutate_after_sha() -> str:
                digest = rehearsal._pinned_file_sha256(dump_source)
                with dump_file.open("r+b") as mutated:
                    mutated.seek(0)
                    mutated.write(b"X")
                    mutated.flush()
                    os.fsync(mutated.fileno())
                assert dump_file.stat().st_ino == inode_before
                return digest

            dump_source.run_stage("SHA-256", mutate_after_sha)


def test_pinned_reader_rejects_broad_mode_and_multiple_links(tmp_path: Path) -> None:
    broad = tmp_path / "broad.json"
    broad.write_text("{}", encoding="utf-8")
    broad.chmod(0o640)
    with pytest.raises(rehearsal.SafetyError, match="0600 or stricter"):
        rehearsal.PinnedReadFile(broad, label="backup evidence")

    linked = tmp_path / "linked.dump"
    linked.write_bytes(b"archive")
    linked.chmod(0o600)
    linked_alias = tmp_path / "linked-alias.dump"
    os.link(linked, linked_alias)
    with pytest.raises(rehearsal.SafetyError, match="exactly one hard link"):
        rehearsal.PinnedReadFile(linked, label="database dump")


def test_remote_staging_database_requires_verify_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = rehearsal.Target("tc-phase-f-migration-staging-copy", "staging-copy")
    args = Namespace(
        database_name="tc_phase_f_migration_staging_copy",
        database_url_env="TC_PHASE_F_DATABASE_URL",
    )
    base = (
        "postgresql://tc:secret@tc-stage-db.example.test:5432/"
        "tc_phase_f_migration_staging_copy"
    )
    monkeypatch.setenv("TC_PHASE_F_DATABASE_URL", base)
    with pytest.raises(rehearsal.SafetyError, match="verify-full"):
        rehearsal._database_target(args, target)

    monkeypatch.setenv("TC_PHASE_F_DATABASE_URL", f"{base}?sslmode=verify-full")
    database = rehearsal._database_target(args, target)
    assert ("PGSSLMODE", "verify-full") in database.connection_environment


def test_compose_fallback_fails_closed_for_remote_staging_without_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "must-not-appear-in-error"
    database = rehearsal.DatabaseTarget(
        "tc_phase_f_migration_staging_copy",
        "TC_PHASE_F_DATABASE_URL",
        f"postgresql://user:{secret}@tc-stage-db.example.test/db?sslmode=verify-full",
        f"postgresql://user:{secret}@tc-stage-db.example.test/db?sslmode=verify-full",
    )

    monkeypatch.setattr(
        rehearsal.shutil,
        "which",
        lambda name: "/usr/bin/docker" if name == "docker" else None,
    )
    monkeypatch.setattr(
        rehearsal,
        "_command",
        lambda *args, **kwargs: pytest.fail("remote target reached Compose fallback"),
    )

    with pytest.raises(rehearsal.SafetyError, match="loopback-only") as raised:
        rehearsal._resolve_postgresql_client(database)
    assert secret not in str(raised.value)
    assert "postgresql://" not in str(raised.value)


def test_database_preflight_requires_exact_comment_and_empty_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = rehearsal.Target("tc-phase-f-restore-disposable", "disposable")
    database = rehearsal.DatabaseTarget(
        "tc_phase_f_restore_test_disposable",
        "TC_PHASE_F_DATABASE_URL",
        "postgresql://redacted",
        "postgresql://redacted",
    )
    responses = iter(
        [
            [
                "tc_phase_f_restore_test_disposable\t160010\t"
                + target.database_attestation
            ],
            ["0"],
            ["f"],
        ]
    )
    monkeypatch.setattr(rehearsal, "_psql", lambda *args, **kwargs: next(responses))
    facts = rehearsal._database_preflight(database, target, require_empty=True)
    assert facts["attested"] is True
    assert facts["publicTableCount"] == 0

    responses = iter(
        [
            ["tc_phase_f_restore_test_disposable\t160010\twrong-comment"],
        ]
    )
    monkeypatch.setattr(rehearsal, "_psql", lambda *args, **kwargs: next(responses))
    with pytest.raises(rehearsal.SafetyError, match="attestation mismatch"):
        rehearsal._database_preflight(database, target, require_empty=True)


def test_current_revision_never_references_missing_alembic_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = rehearsal.DatabaseTarget(
        "tc_phase_f_restore_test_disposable",
        "TC_PHASE_F_DATABASE_URL",
        "postgresql://redacted",
        "postgresql://redacted",
    )
    statements: list[str] = []

    def psql(
        _database: object, sql: str, *, client: object | None = None
    ) -> list[str]:
        del client
        statements.append(sql)
        return ["f"]

    monkeypatch.setattr(rehearsal, "_psql", psql)

    assert rehearsal._current_revision(database) is None
    assert statements == [
        "SELECT to_regclass('public.alembic_version') IS NOT NULL;"
    ]


def test_current_revision_reads_existing_alembic_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = rehearsal.DatabaseTarget(
        "tc_phase_f_restore_test_disposable",
        "TC_PHASE_F_DATABASE_URL",
        "postgresql://redacted",
        "postgresql://redacted",
    )
    responses = iter([["t"], ["20260902_0030"]])
    monkeypatch.setattr(
        rehearsal, "_psql", lambda *args, **kwargs: next(responses)
    )

    assert rehearsal._current_revision(database) == "20260902_0030"


def test_fingerprint_comparison_fails_for_changed_catalog_category() -> None:
    source = _complete_fingerprint()
    restored = json.loads(json.dumps(source))
    assert all(rehearsal._compare_database_fingerprints(restored, source).values())

    restored["schemaSha256"] = "f" * 64
    restored["schemaCategorySha256"]["constraints"] = "f" * 64
    comparison = rehearsal._compare_database_fingerprints(restored, source)
    assert comparison["catalogEqual"] is False
    assert comparison["catalogCategoryDigestsEqual"] is False


def test_post_smoke_full_comparison_rejects_constraint_only_change() -> None:
    before_smoke = _complete_fingerprint()
    after_smoke = json.loads(json.dumps(before_smoke))
    after_smoke["schemaSha256"] = "f" * 64
    after_smoke["schemaCategorySha256"]["constraints"] = "f" * 64

    comparison = rehearsal._compare_database_fingerprints(
        after_smoke, before_smoke
    )

    assert comparison["catalogEqual"] is False
    assert comparison["catalogCategoryDigestsEqual"] is False
    assert comparison["dataEqual"] is True
    assert comparison["perTableEqual"] is True
    assert not all(comparison.values())


def test_catalog_fingerprint_detects_check_expression_index_and_sequence_changes() -> None:
    database_url = os.getenv("TC_PHASE_F_CATALOG_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TC_PHASE_F_CATALOG_TEST_DATABASE_URL is not set")
    parsed = urlsplit(database_url)
    if (
        parsed.hostname not in rehearsal.LOOPBACK_HOSTS
        or not parsed.path.endswith("_test_disposable")
    ):
        pytest.fail("catalog integration test requires an attested loopback test database")

    libpq_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(libpq_url, autocommit=True) as connection:
        identity = connection.execute(
            "SELECT current_database(), COALESCE(shobj_description(oid,'pg_database'),'') "
            "FROM pg_database WHERE datname=current_database()"
        ).fetchone()
        assert identity is not None
        database_name, attestation = str(identity[0]), str(identity[1])
        prefix = "tc-phase-f-rehearsal:disposable:"
        if not attestation.startswith(prefix):
            pytest.fail("catalog integration database lacks disposable attestation")
        target = rehearsal.Target(attestation.removeprefix(prefix), "disposable")
        rehearsal._validate_database_name(database_name, target)
        existing = connection.execute(
            "SELECT to_regclass('public.pf_sf1_t07_catalog_contract_probe'), "
            "to_regclass('public.pf_sf1_t07_catalog_contract_probe_seq')"
        ).fetchone()
        if existing != (None, None):
            pytest.fail("catalog integration probe objects already exist")
        try:
            connection.execute(
                "CREATE TABLE public.pf_sf1_t07_catalog_contract_probe ("
                "id bigint PRIMARY KEY, quantity integer NOT NULL, note text NOT NULL, "
                "CONSTRAINT pf_sf1_t07_catalog_probe_check CHECK (quantity > 0))"
            )
            connection.execute(
                "CREATE INDEX pf_sf1_t07_catalog_probe_expression_partial_idx "
                "ON public.pf_sf1_t07_catalog_contract_probe ((lower(note))) "
                "WHERE quantity > 0"
            )
            connection.execute(
                "CREATE SEQUENCE public.pf_sf1_t07_catalog_contract_probe_seq START 10"
            )
            connection.execute(
                "SELECT setval('public.pf_sf1_t07_catalog_contract_probe_seq',10,true)"
            )
            untouched = rehearsal._database_schema_fingerprint(connection)
            repeated = rehearsal._database_schema_fingerprint(connection)
            assert repeated == untouched

            connection.execute(
                "ALTER TABLE public.pf_sf1_t07_catalog_contract_probe "
                "DROP CONSTRAINT pf_sf1_t07_catalog_probe_check, "
                "ADD CONSTRAINT pf_sf1_t07_catalog_probe_check CHECK (quantity >= 0)"
            )
            changed_check = rehearsal._database_schema_fingerprint(connection)
            assert (
                changed_check["schemaCategorySha256"]["constraints"]
                != untouched["schemaCategorySha256"]["constraints"]
            )

            connection.execute(
                "ALTER TABLE public.pf_sf1_t07_catalog_contract_probe "
                "DROP CONSTRAINT pf_sf1_t07_catalog_probe_check, "
                "ADD CONSTRAINT pf_sf1_t07_catalog_probe_check CHECK (quantity > 0)"
            )
            connection.execute(
                "DROP INDEX public.pf_sf1_t07_catalog_probe_expression_partial_idx"
            )
            connection.execute(
                "CREATE INDEX pf_sf1_t07_catalog_probe_expression_partial_idx "
                "ON public.pf_sf1_t07_catalog_contract_probe ((upper(note))) "
                "WHERE quantity >= 0"
            )
            changed_index = rehearsal._database_schema_fingerprint(connection)
            assert (
                changed_index["schemaCategorySha256"]["indexes"]
                != untouched["schemaCategorySha256"]["indexes"]
            )

            connection.execute(
                "SELECT setval('public.pf_sf1_t07_catalog_contract_probe_seq',11,true)"
            )
            changed_sequence = rehearsal._database_schema_fingerprint(connection)
            assert (
                changed_sequence["schemaCategorySha256"]["sequences"]
                != changed_index["schemaCategorySha256"]["sequences"]
            )
        finally:
            connection.execute(
                "DROP TABLE IF EXISTS public.pf_sf1_t07_catalog_contract_probe"
            )
            connection.execute(
                "DROP SEQUENCE IF EXISTS public.pf_sf1_t07_catalog_contract_probe_seq"
            )


def test_execute_requires_task_and_target_specific_confirmation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(
        "TC_PHASE_F_DATABASE_URL",
        "postgresql://tc:secret@127.0.0.1:55432/tc_phase_f_cases_test_disposable",
    )
    result = rehearsal.main(
        [
            "benchmark-cases",
            "--target-name",
            "tc-phase-f-cases-disposable",
            "--target-class",
            "disposable",
            "--database-url-env",
            "TC_PHASE_F_DATABASE_URL",
            "--database-name",
            "tc_phase_f_cases_test_disposable",
            "--execute",
            "--confirm",
            "yes",
        ]
    )
    assert result == 2
    assert "RUN PF-SF1-T06 BENCHMARK-CASES" in capsys.readouterr().err


def test_restore_requires_empty_preflight_and_never_uses_clean(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dump_file = tmp_path / "source.dump"
    dump_file.write_bytes(b"custom-dump-fixture")
    dump_file.chmod(0o600)
    target = rehearsal.Target("tc-phase-f-restore-disposable", "disposable")
    database = rehearsal.DatabaseTarget(
        "tc_phase_f_restore_test_disposable",
        "TC_PHASE_F_RESTORE_DATABASE_URL",
        "postgresql://redacted",
        "postgresql://redacted",
        (("PGDATABASE", "tc_phase_f_restore_test_disposable"),),
    )
    monkeypatch.setattr(rehearsal, "_database_target", lambda *args: database)
    preflights: list[dict[str, object]] = []

    def preflight(*args: object, **kwargs: object) -> dict[str, object]:
        preflights.append(kwargs)
        return {
            "publicTableCount": 0,
            "alembicRevision": kwargs.get("expected_revision"),
        }

    monkeypatch.setattr(rehearsal, "_database_preflight", preflight)
    client = rehearsal.PostgreSQLClient(
        "host", (("psql", "psql"), ("pg_dump", "pg_dump"), ("pg_restore", "pg_restore"))
    )
    monkeypatch.setattr(rehearsal, "_resolve_postgresql_client", lambda _database: client)
    fingerprint = _complete_fingerprint()
    archive = {
        "validated": True,
        "entryCount": 1,
        "listingSha256": "e" * 64,
        "dumpBytes": dump_file.stat().st_size,
        "dumpSha256": "d" * 64,
    }
    dump_identity = rehearsal.FileIdentity.from_stat(dump_file.stat()).evidence()
    dump_file_safety = {
        "singleOpenNoFollowFileDescriptor": True,
        "pathnameReopened": False,
        "regularFile": True,
        "permissionsAtMost0600": True,
        "singleLink": True,
        "identity": dump_identity,
        "verifiedBeforeAndAfterStages": [
            "SHA-256",
            "pg_restore archive inventory",
        ],
    }
    backup_evidence_file_safety = {
        **dump_file_safety,
        "verifiedBeforeAndAfterStages": ["JSON read/parse"],
    }
    monkeypatch.setattr(
        rehearsal,
        "_load_backup_evidence",
        lambda _path: (
            rehearsal.EVIDENCE_ROOT / "test-backup.json",
            {
                "task": "PF-SF1-T07",
                "action": "POSTGRES-BACKUP",
                "mode": "executed",
                "target": {
                    "name": "tc-phase-f-source-disposable",
                    "class": "disposable",
                },
                "dumpFile": str(dump_file),
                "dumpBytes": dump_file.stat().st_size,
                "preflight": {
                    "databaseName": "tc_phase_f_source_test_disposable",
                    "alembicRevision": rehearsal.HEAD_REVISION,
                },
                "sourceFingerprint": fingerprint,
                "dumpSha256": "d" * 64,
                "dumpFileSafety": dump_file_safety,
                "archive": archive,
                "recoveryPoint": {
                    "capturedAtUtc": "2026-09-04T00:00:00Z",
                    "productionRpoPromise": "NOT_CLAIMED",
                },
            },
            backup_evidence_file_safety,
        ),
    )
    monkeypatch.setattr(
        rehearsal, "_pinned_file_sha256", lambda _source: "d" * 64
    )
    monkeypatch.setattr(
        rehearsal,
        "_archive_listing",
        lambda *_args, **_kwargs: archive,
    )
    monkeypatch.setattr(
        rehearsal, "_capture_database_fingerprint", lambda *args, **kwargs: fingerprint
    )
    monkeypatch.setattr(
        rehearsal,
        "_application_restore_smoke",
        lambda _database: {"inProcess": True, "readOnly": True},
    )
    calls: list[tuple[str, ...]] = []

    def command_from_pinned_file(
        argv: list[str],
        source: object,
        *,
        env: dict[str, str],
        capture: bool,
    ) -> subprocess.CompletedProcess[bytes]:
        del source, env, capture
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(
        rehearsal, "_command_from_pinned_file", command_from_pinned_file
    )
    args = Namespace(
        database_name="tc_phase_f_restore_test_disposable",
        database_url_env="TC_PHASE_F_RESTORE_DATABASE_URL",
        dump_file=str(dump_file),
        backup_evidence=str(tmp_path / "backup.json"),
        output=None,
        execute=True,
        confirm="RUN PF-SF1-T07 POSTGRES-RESTORE ON tc-phase-f-restore-disposable",
    )
    result = rehearsal._run_postgres_restore(args, target)
    assert preflights[0]["require_empty"] is True
    assert preflights[0]["client"] == client
    restore_call = calls[0]
    assert "--clean" not in restore_call
    assert restore_call[-2:] == (
        "--dbname",
        "tc_phase_f_restore_test_disposable",
    )
    assert result["postflight"]["alembicRevision"] == rehearsal.HEAD_REVISION
    assert result["postSmokeFingerprint"] == fingerprint
    assert all(result["postSmokeComparison"].values())


def test_r2_verification_binds_bucket_paginates_dry_run_and_samples_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[str, str, dict[str, object] | None]] = []
    responses = iter(
        [
            {
                "status": "READY",
                "provider": "R2",
                "bucket": "tc-phase-f-attachments-staging-copy",
                "privacy": "PRIVATE",
                "scanPolicy": "FAIL_CLOSED_CLAMAV",
            },
            {
                "dryRun": True,
                "scannedCount": 1,
                "consistentCount": 1,
                "mismatchCount": 0,
                "failedCount": 0,
                "nextCursor": "next-page",
            },
            {
                "dryRun": True,
                "scannedCount": 1,
                "consistentCount": 1,
                "mismatchCount": 0,
                "failedCount": 0,
                "nextCursor": None,
            },
            {"sampledCount": 2, "verifiedCount": 2, "failedCount": 0},
        ]
    )

    def http_json(
        method: str,
        url: str,
        *,
        payload: dict[str, object] | None,
        token: str | None,
    ) -> dict[str, Any]:
        assert token is None
        requests.append((method, url, payload))
        return next(responses)

    monkeypatch.setattr(rehearsal, "_http_json", http_json)
    target = rehearsal.Target("tc-phase-f-storage-staging-copy", "staging-copy")
    args = Namespace(
        api_base_url="https://tc-stage.example.test",
        bucket="tc-phase-f-attachments-staging-copy",
        api_token_env=None,
        sample_size=100,
        max_bytes=1024,
        execute=True,
        confirm="RUN PF-SF1-T08 R2-VERIFY ON tc-phase-f-storage-staging-copy",
    )
    result = rehearsal._run_r2_verify(args, target)
    assert result["metadata"] == {
        "scanned": 2,
        "consistent": 2,
        "mismatch": 0,
        "failed": 0,
        "pages": 2,
    }
    metadata_payloads = [
        payload for _, url, payload in requests if url.endswith("verify-metadata")
    ]
    assert metadata_payloads == [
        {"dryRun": True, "limit": 1000},
        {"dryRun": True, "limit": 1000, "cursor": "next-page"},
    ]


def test_r2_verification_fails_on_empty_population(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            {
                "status": "READY",
                "provider": "R2",
                "bucket": "tc-phase-f-attachments-staging-copy",
                "privacy": "PRIVATE",
                "scanPolicy": "FAIL_CLOSED_CLAMAV",
            },
            {
                "dryRun": True,
                "scannedCount": 0,
                "consistentCount": 0,
                "mismatchCount": 0,
                "failedCount": 0,
                "nextCursor": None,
            },
        ]
    )
    monkeypatch.setattr(
        rehearsal,
        "_http_json",
        lambda *args, **kwargs: next(responses),
    )
    target = rehearsal.Target("tc-phase-f-storage-staging-copy", "staging-copy")
    args = Namespace(
        api_base_url="https://tc-stage.example.test",
        bucket="tc-phase-f-attachments-staging-copy",
        api_token_env=None,
        sample_size=100,
        max_bytes=1024,
        execute=True,
        confirm="RUN PF-SF1-T08 R2-VERIFY ON tc-phase-f-storage-staging-copy",
    )
    with pytest.raises(rehearsal.SafetyError, match="at least one"):
        rehearsal._run_r2_verify(args, target)


def test_provider_neutral_verification_truthfully_accepts_only_local_disposable_minio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            {
                "status": "READY",
                "provider": "MINIO",
                "bucket": "tc-phase-f-pf-sf1-t08-wave7-unit-disposable",
                "privacy": "PRIVATE",
                "scanPolicy": "FAIL_CLOSED_CLAMAV",
            },
            {
                "dryRun": True,
                "scannedCount": 1,
                "consistentCount": 1,
                "mismatchCount": 0,
                "failedCount": 0,
                "nextCursor": None,
            },
            {"sampledCount": 1, "verifiedCount": 1, "failedCount": 0},
        ]
    )
    monkeypatch.setattr(
        rehearsal, "_http_json", lambda *args, **kwargs: next(responses)
    )
    target = rehearsal.Target(
        "tc-phase-f-pf-sf1-t08-wave7-unit-disposable", "disposable"
    )
    args = Namespace(
        provider="MINIO",
        api_base_url="http://127.0.0.1:8000",
        bucket="tc-phase-f-pf-sf1-t08-wave7-unit-disposable",
        api_token_env=None,
        sample_size=100,
        max_bytes=1024,
        execute=True,
        confirm=(
            "RUN PF-SF1-T08 OBJECT-STORAGE-VERIFY ON "
            "tc-phase-f-pf-sf1-t08-wave7-unit-disposable"
        ),
    )
    result = rehearsal._run_provider_verify(args, target)
    assert result["provider"] == "MINIO"
    assert result["status"]["provider"] == "MINIO"

    staging_target = rehearsal.Target(
        "tc-phase-f-pf-sf1-t08-wave7-unit-staging-copy", "staging-copy"
    )
    args.api_base_url = "https://tc-stage.example.test"
    args.bucket = "tc-phase-f-pf-sf1-t08-wave7-unit-staging-copy"
    with pytest.raises(rehearsal.SafetyError, match="disposable"):
        rehearsal._run_provider_verify(args, staging_target)


def test_http_json_rejects_redirect_without_disclosing_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "phase-f-bearer-secret"

    class RedirectingOpener:
        @staticmethod
        def open(request: urllib.request.Request, *, timeout: int) -> object:
            assert timeout == 30
            assert request.headers["Authorization"] == f"Bearer {secret}"
            raise urllib.error.HTTPError(
                request.full_url,
                302,
                "Found",
                Message(),
                None,
            )

    def build_opener(handler: object) -> RedirectingOpener:
        assert isinstance(handler, rehearsal._NoRedirectHandler)
        return RedirectingOpener()

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)
    with pytest.raises(rehearsal.SafetyError, match="HTTP 302") as raised:
        rehearsal._http_json(
            "GET",
            "https://tc-stage.example.test/api/status",
            payload=None,
            token=secret,
        )
    assert secret not in str(raised.value)


def test_knowledge_summary_omits_projection_rows_and_event_payloads() -> None:
    summary = rehearsal._knowledge_result_summary(
        {
            "mode": "dry-run",
            "writeScope": ["knowledge_nodes"],
            "preservedLedgers": ["slack_messages"],
            "eventCount": 1,
            "eventOrderDigest": "a" * 64,
            "sourceLedgerDigest": "b" * 64,
            "eventStatusCounts": {"PENDING": 1},
            "auxiliaryIndexIncluded": False,
            "transaction": {"atomic": True},
            "events": [{"payload": "sensitive event"}],
            "currentProjection": {
                "schemaVersion": "v1",
                "digest": "c" * 64,
                "counts": {"nodes": 1},
                "projection": {"nodes": [{"label": "sensitive label"}]},
            },
        }
    )
    rendered = json.dumps(summary)
    assert "sensitive event" not in rendered
    assert "sensitive label" not in rendered
    assert summary["sensitiveProjectionOmitted"] is True


def test_knowledge_runtime_requires_opt_in_pins_and_nonproduction_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = rehearsal.Target("tc-phase-f-knowledge-staging-copy", "staging-copy")
    monkeypatch.setenv("TC_AUTOMATIC_KNOWLEDGE_PROJECTION_ENABLED", "true")
    monkeypatch.setenv("TC_OLLAMA_BASE_URL", "http://ollama-stage.example.test:11434")
    monkeypatch.setenv("TC_GENERATION_MODEL", "qwen3:8b")
    monkeypatch.setenv("TC_EMBEDDING_MODEL", "bge-m3")
    runtime = rehearsal._validate_knowledge_runtime(target)
    assert runtime["embeddingModel"] == "bge-m3"

    monkeypatch.setenv("TC_OLLAMA_BASE_URL", "https://ollama-prod.example.test")
    with pytest.raises(rehearsal.SafetyError, match="production-like"):
        rehearsal._validate_knowledge_runtime(target)


def test_knowledge_apply_uses_runtime_guard_and_sanitizes_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = rehearsal.Target("tc-phase-f-knowledge-staging-copy", "staging-copy")
    database = rehearsal.DatabaseTarget(
        "tc_phase_f_knowledge_staging_copy",
        "TC_PHASE_F_KNOWLEDGE_DATABASE_URL",
        "postgresql://redacted",
        "postgresql://redacted",
    )
    monkeypatch.setattr(rehearsal, "_database_target", lambda *args: database)
    monkeypatch.setattr(
        rehearsal,
        "_database_preflight",
        lambda *args, **kwargs: {"alembicRevision": rehearsal.HEAD_REVISION},
    )
    monkeypatch.setattr(
        rehearsal,
        "_validate_knowledge_runtime",
        lambda target: {
            "ollamaOrigin": "http://ollama-stage.example.test:11434",
            "generationModel": "qwen3:8b",
            "embeddingModel": "bge-m3",
        },
    )
    subprocess_payload = {
        "mode": "apply",
        "writeScope": ["knowledge_nodes"],
        "preservedLedgers": ["slack_messages"],
        "eventCount": 1,
        "eventOrderDigest": "a" * 64,
        "sourceLedgerDigest": "b" * 64,
        "beforeDigest": "c" * 64,
        "afterProjection": {
            "schemaVersion": "v1",
            "digest": "d" * 64,
            "counts": {"nodes": 1},
            "projection": {"nodes": [{"label": "private"}]},
        },
    }
    monkeypatch.setattr(
        rehearsal,
        "_command",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, json.dumps(subprocess_payload), ""
        ),
    )
    args = Namespace(
        database_name="tc_phase_f_knowledge_staging_copy",
        database_url_env="TC_PHASE_F_KNOWLEDGE_DATABASE_URL",
        event_order_digest="a" * 64,
        source_ledger_digest="b" * 64,
        execute=True,
        confirm=("RUN PF-SF1-T09 KNOWLEDGE-APPLY ON tc-phase-f-knowledge-staging-copy"),
    )
    result = rehearsal._run_knowledge_apply(args, target)
    assert result["runtime"]["embeddingModel"] == "bge-m3"
    assert "private" not in json.dumps(result["rebuild"])


def test_cors_policy_accepts_exact_https_origin_and_rejects_wildcard(
    tmp_path: Path,
) -> None:
    target = rehearsal.Target("tc-phase-f-web-staging-copy", "staging-copy")
    policy = {
        "rules": [
            {
                "allowed": {
                    "origins": ["https://tc-stage.example.test"],
                    "methods": ["PUT", "GET", "HEAD"],
                    "headers": ["content-type", "x-amz-meta-sha256"],
                },
                "exposeHeaders": [
                    "etag",
                    "content-length",
                    "content-type",
                    "x-amz-meta-sha256",
                ],
                "maxAgeSeconds": 300,
            }
        ]
    }
    policy_path = tmp_path / "cors.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    args = Namespace(
        web_origin="https://tc-stage.example.test",
        policy_file=str(policy_path),
    )
    result = rehearsal._run_cors_policy(args, target)
    assert result["physicalBrowserEvidence"] == "NOT_RUN"

    policy["rules"][0]["allowed"]["origins"] = ["*"]
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    with pytest.raises(rehearsal.SafetyError, match="exact"):
        rehearsal._run_cors_policy(args, target)


def test_migration_roundtrip_uses_one_step_and_requires_digest_equality(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = rehearsal.Target("tc-phase-f-migration-staging-copy", "staging-copy")
    database = rehearsal.DatabaseTarget(
        "tc_phase_f_migration_staging_copy",
        "TC_PHASE_F_DATABASE_URL",
        "postgresql://redacted",
        "postgresql://redacted",
    )
    monkeypatch.setattr(rehearsal, "_database_target", lambda *args: database)
    monkeypatch.setattr(
        rehearsal,
        "_database_preflight",
        lambda *args, **kwargs: {"alembicRevision": rehearsal.HEAD_REVISION},
    )
    dumps = iter(
        [
            "CREATE TABLE x();",
            "INSERT INTO x VALUES (1);",
            "CREATE TABLE x();",
            "INSERT INTO x VALUES (1);",
        ]
    )
    monkeypatch.setattr(rehearsal, "_plain_dump", lambda *args, **kwargs: next(dumps))
    revisions = iter([rehearsal.ROLLBACK_REVISION])
    monkeypatch.setattr(rehearsal, "_current_revision", lambda *args: next(revisions))
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        rehearsal, "_alembic", lambda database, *args: calls.append(args)
    )
    args = Namespace(
        database_name="tc_phase_f_migration_staging_copy",
        database_url_env="TC_PHASE_F_DATABASE_URL",
        execute=True,
        confirm=(
            "RUN PF-SF1-T10 MIGRATION-ROUNDTRIP ON tc-phase-f-migration-staging-copy"
        ),
    )
    result = rehearsal._run_migration_roundtrip(args, target)
    assert calls == [
        ("downgrade", rehearsal.ROLLBACK_REVISION),
        ("upgrade", rehearsal.HEAD_REVISION),
    ]
    assert result["exactRoundTrip"] is True


def test_presigned_url_never_appears_in_cors_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_url = (
        "https://account.r2.cloudflarestorage.com/bucket/key?X-Amz-Signature=secret"
    )
    monkeypatch.setenv("TC_PHASE_F_R2_PRESIGNED_URL", secret_url)

    class Response:
        status = 204

        def __init__(self) -> None:
            self.headers = Message()
            self.headers["Access-Control-Allow-Origin"] = (
                "https://tc-stage.example.test"
            )
            self.headers["Access-Control-Allow-Methods"] = "PUT, GET, HEAD"
            self.headers["Access-Control-Allow-Headers"] = (
                "content-type, x-amz-meta-sha256"
            )

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(
        rehearsal, "_open_without_redirect", lambda *args, **kwargs: Response()
    )
    target = rehearsal.Target("tc-phase-f-web-staging-copy", "staging-copy")
    args = Namespace(
        web_origin="https://tc-stage.example.test",
        presigned_url_env="TC_PHASE_F_R2_PRESIGNED_URL",
        execute=True,
        confirm="RUN PF-SF1-T15 CORS-PREFLIGHT ON tc-phase-f-web-staging-copy",
    )
    result = rehearsal._run_cors_preflight(args, target)
    assert secret_url not in json.dumps(result)
    assert result["physicalBrowserEvidence"] == "NOT_RUN"
