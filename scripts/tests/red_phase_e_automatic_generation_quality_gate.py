"""PE-SE2-T08 focused RED for the intentionally absent production gate.

The default ``test*.py`` discovery does not collect this file. Run it directly
after implementing the evaluator, backend release seam, and protected release
workflow. Contract/schema fixtures are green controls and are not RED causes.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_offline_generation_quality_evaluator_cli_exists() -> None:
    evaluator = ROOT / "scripts/evaluate_automatic_generation_quality.py"
    assert evaluator.is_file(), (
        "RED T08: add the deterministic offline automatic-generation quality evaluator "
        "with JSON, JUnit, and Markdown outputs plus exit codes 0/1/2"
    )


def test_generation_quality_release_gate_service_exists() -> None:
    assert importlib.util.find_spec("tc_backend.generation.quality_gate") is not None, (
        "RED T08: add the production release-gate seam that validates sealed "
        "root/job/result/apply/suggestion provenance and refuses synthetic certification"
    )


def test_package_command_and_protected_release_workflow_exist() -> None:
    package_document = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    scripts = package_document.get("scripts", {})
    workflow = ROOT / ".github/workflows/automatic-generation-quality-release.yml"
    missing: list[str] = []
    if "generation:quality:eval" not in scripts:
        missing.append("package script generation:quality:eval")
    if not workflow.is_file():
        missing.append("protected automatic-generation-quality-release workflow")
    assert not missing, f"RED T08: add CI/release production seams: {', '.join(missing)}"
