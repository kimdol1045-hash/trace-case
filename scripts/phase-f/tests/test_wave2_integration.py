from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_repository_check_and_focused_entrypoints_cover_wave2_tools() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    scripts = package["scripts"]

    assert "scripts/phase-f/tests" in scripts["test:unit:py"]
    assert scripts["test:phase-f"].startswith(
        "node scripts/python.mjs -m pytest scripts/phase-f/tests"
    )
    assert "scripts/tests/test_phase_f_load_profile.py" in scripts["test:phase-f"]
    assert scripts["test:operations"].startswith(
        "node --test scripts/tests/validate-operations-runbook.test.mjs"
    )
    assert "node scripts/validate-operations-runbook.mjs" in scripts[
        "test:operations"
    ]
    assert scripts["phase-f:uat:validate"] == (
        "node scripts/python.mjs scripts/phase-f/uat_evidence.py validate-manifest"
    )
    for tool in (
        "scripts/phase-f/uat_evidence.py",
        "scripts/phase-f/lightrag_full_reindex.py",
        "scripts/phase-f/load_profile.py",
    ):
        assert tool in scripts["typecheck:py"]


def test_current_docs_keep_external_wave2_gates_open_and_use_current_contracts() -> None:
    roadmap = (ROOT / "docs/test-management-roadmap.md").read_text(encoding="utf-8")
    runbook = (ROOT / "deploy/operations/README.md").read_text(encoding="utf-8")
    readiness = (
        ROOT / "docs/phase-f/sprint-f1-recovery-performance-readiness.md"
    ).read_text(encoding="utf-8")

    for task_id in ("PF-SF1-T06", "PF-SF1-T09", "PF-SF1-T11", "PF-SF1-T12"):
        assert re.search(rf"^- \[ \] `{task_id}`", roadmap, re.MULTILINE)
    assert "pf-sf1-t09-lightrag-full-reindex.md" in readiness
    assert "sprint-f1-wave2-verification.md#limitations-and-next-gates" in runbook
    assert "| Performance |" in runbook
    assert "| UAT |" in runbook
