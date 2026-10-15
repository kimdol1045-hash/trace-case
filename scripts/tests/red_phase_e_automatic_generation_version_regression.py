"""PE-SE2-T09 focused RED for intentionally absent production seams.

Default ``test*.py`` discovery does not collect this file. Run it directly only
when implementing the regression evaluator, immutable baseline ledger,
two-person approval/activation/rollback services, and protected release flow.
The T09 contract schema and deterministic fixtures are GREEN controls and are
never a reason for this RED to fail.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_version_regression_evaluator_cli_exists() -> None:
    evaluator = ROOT / "scripts/evaluate_automatic_generation_version_regression.py"
    assert evaluator.is_file(), (
        "RED T09: add the deterministic old/new operational-golden regression "
        "evaluator with strict report validation and exit codes 0/1/2"
    )


def test_baseline_ledger_approval_activation_and_rollback_services_exist() -> None:
    module_name = "tc_backend.generation.version_regression"
    assert importlib.util.find_spec(module_name) is not None, (
        "RED T09: add the production baseline ledger and regression service seam"
    )
    module = importlib.import_module(module_name)
    required = {
        "VersionRegressionEvaluator",
        "GenerationBaselineLedgerService",
        "TwoPersonBaselineApprovalService",
        "AtomicBaselineActivationService",
        "BaselineRollbackService",
    }
    missing = sorted(name for name in required if not hasattr(module, name))
    assert not missing, f"RED T09: add production governance seams: {', '.join(missing)}"


def test_version_regression_package_command_exists() -> None:
    package_document = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert "generation:version-regression:eval" in package_document.get("scripts", {}), (
        "RED T09: add package command generation:version-regression:eval"
    )


def test_protected_version_regression_release_workflow_exists() -> None:
    workflow = ROOT / ".github/workflows/automatic-generation-version-regression-release.yml"
    assert workflow.is_file(), (
        "RED T09: add the protected release workflow that requires approved operational "
        "old/new golden reports, live attestation, two-person approval, atomic activation, "
        "and a reproducible rollback target"
    )
