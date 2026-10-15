from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import urllib.error
from email.message import Message
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit

import pytest
from tc_api.main import create_app  # type: ignore[import-untyped]

SCRIPT = Path(__file__).resolve().parents[1] / "uat_evidence.py"
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MANIFEST = REPOSITORY_ROOT / "deploy" / "operations" / "phase-f-uat-manifest.json"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("phase_f_uat_evidence", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


uat = _load()


def _manifest() -> dict[str, Any]:
    return uat.validate_manifest(
        {
            "schemaVersion": 1,
            "manifestId": "pf-sf1-t11-test",
            "taskId": "PF-SF1-T11",
            "originPolicy": {
                "scheme": "https",
                "redirects": "DENY",
                "allowedMethods": ["GET"],
                "authentication": "OPTIONAL_BEARER_ENV",
                "timeoutSeconds": 2,
                "maxResponseBytes": 4096,
            },
            "workflows": [
                {
                    "id": "sample-workflow",
                    "title": "Sample workflow",
                    "acceptance": ["Owner verifies the sample."],
                }
            ],
            "automatedChecks": [
                {
                    "id": "api-sample",
                    "title": "Sample API",
                    "workflowIds": ["sample-workflow"],
                    "request": {"method": "GET", "path": "/api/sample?limit=1"},
                    "expect": {
                        "statusCodes": [200],
                        "contentType": "application/json",
                        "requiredHeaders": {
                            "cache-control": "no-store",
                            "x-content-type-options": "nosniff",
                        },
                        "body": {
                            "type": "JSON",
                            "requiredKeys": ["items"],
                            "requiredTypes": {"items": "array"},
                            "equals": {},
                            "requiredStrings": [],
                        },
                    },
                }
            ],
            "manualChecks": [
                {
                    "id": "owner-sample",
                    "title": "Owner sample",
                    "workflowIds": ["sample-workflow"],
                    "ownerRole": "QA owner",
                    "steps": ["Perform the sample."],
                    "requiredArtifactKinds": ["OWNER_NOTE", "SCREENSHOT"],
                    "defaultStatus": "NOT_RUN",
                }
            ],
        }
    )


class _Response:
    def __init__(self, body: bytes, *, status: int = 200) -> None:
        self.status = status
        self._body = body
        self.headers = Message()
        self.headers["Content-Type"] = "application/json; charset=utf-8"
        self.headers["Content-Length"] = str(len(body))
        self.headers["Cache-Control"] = "no-store"
        self.headers["X-Content-Type-Options"] = "nosniff"

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, amount: int) -> bytes:
        return self._body[:amount]


class _Opener:
    def __init__(self, response: _Response | BaseException) -> None:
        self.response = response
        self.requests: list[Any] = []

    def open(self, request: Any, *, timeout: int) -> _Response:
        assert timeout == 2
        self.requests.append(request)
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def test_repository_manifest_is_valid_and_covers_every_board_workflow() -> None:
    manifest = uat.validate_manifest(uat._load_json(MANIFEST))
    assert len(manifest["workflows"]) == 15
    assert len(manifest["automatedChecks"]) == 33
    assert len(manifest["manualChecks"]) == 15
    covered = {
        workflow_id
        for check in manifest["manualChecks"]
        for workflow_id in check["workflowIds"]
    }
    assert covered == {workflow["id"] for workflow in manifest["workflows"]}
    assert {check["defaultStatus"] for check in manifest["manualChecks"]} == {
        "NOT_RUN"
    }


def test_manifest_api_gets_match_the_current_openapi_surface() -> None:
    manifest = uat.validate_manifest(uat._load_json(MANIFEST))
    paths = create_app(validate_database=False).openapi()["paths"]
    off_schema = {"/api/ready"}
    declared = {
        urlsplit(check["request"]["path"]).path
        for check in manifest["automatedChecks"]
        if check["request"]["path"].startswith("/api/")
    }
    for path in sorted(declared - off_schema):
        assert path in paths
        assert "get" in paths[path]


@pytest.mark.parametrize(
    "origin",
    [
        "http://uat.example.test",
        "https://uat.example.test/",
        "https://user:secret@uat.example.test",
        "https://UAT.example.test",
        "https://uat.example.test:443",
        "https://uat.example.test?token=secret",
        "https://uat.example.test#fragment",
    ],
)
def test_exact_origin_rejects_noncanonical_or_credential_bearing_values(
    origin: str,
) -> None:
    with pytest.raises(uat.SafetyError):
        uat.validate_origin(origin)
    assert uat.validate_origin("https://uat.example.test") == "https://uat.example.test"


@pytest.mark.parametrize(
    "path",
    [
        "https://other.example.test/api/sample",
        "//other.example.test/api/sample",
        "/api/../secret",
        "/api/sample?access_token=secret",
        "/api/sample?z=1&a=2",
        "/api/sample?a=1&a=2",
    ],
)
def test_manifest_paths_are_same_origin_sorted_and_credential_free(path: str) -> None:
    with pytest.raises(uat.SafetyError):
        uat._validate_request_path(path, context="test.path")


def test_collector_hashes_body_uses_get_and_never_persists_token_or_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(uat, "ROOT", tmp_path)
    manifest = _manifest()
    secret = "bearer-secret-that-must-not-leak"
    body = b'{"items":[],"privateValue":"response-secret-that-must-not-leak"}'
    opener = _Opener(_Response(body))
    output = tmp_path / "evidence.json"

    evidence, report = uat.collect(
        manifest,
        origin="https://uat.example.test",
        output=output,
        token=secret,
        opener=opener,
    )

    assert report["ok"] is True
    assert report["pfSf1T11Status"] == "NOT_RUN"
    request = opener.requests[0]
    assert request.get_method() == "GET"
    assert request.full_url == "https://uat.example.test/api/sample?limit=1"
    assert request.get_header("Authorization") == f"Bearer {secret}"
    serialized = output.read_text(encoding="utf-8")
    assert secret not in serialized
    assert "response-secret-that-must-not-leak" not in serialized
    assert evidence["automatedEvidence"][0]["bodySha256"] == hashlib.sha256(body).hexdigest()
    assert evidence["manualEvidence"][0]["status"] == "NOT_RUN"


def test_redirect_is_rejected_without_recording_location() -> None:
    manifest = _manifest()
    location = "https://attacker.example.test/collect?token=secret"
    headers = Message()
    headers["Location"] = location
    error = urllib.error.HTTPError(
        "https://uat.example.test/api/sample?limit=1",
        302,
        "Found",
        headers,
        None,
    )
    result = uat._response_result(
        manifest["automatedChecks"][0],
        origin="https://uat.example.test",
        opener=_Opener(error),
        timeout=2,
        maximum_bytes=4096,
        token="secret",
    )
    assert result["status"] == "FAIL"
    assert result["errorCode"] == "REDIRECT_REJECTED"
    assert location not in json.dumps(result)
    assert uat._NoRedirect().redirect_request(None, None, 302, "", {}, location) is None


def test_missing_manual_evidence_stays_not_run_even_when_automation_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(uat, "ROOT", tmp_path)
    manifest = _manifest()
    evidence, _ = uat.collect(
        manifest,
        origin="https://uat.example.test",
        output=tmp_path / "evidence.json",
        token=None,
        opener=_Opener(_Response(b'{"items":[]}')),
    )
    report = uat.validate_evidence(
        evidence, manifest, origin="https://uat.example.test"
    )
    assert report["automated"]["status"] == "PASS"
    assert report["manual"]["status"] == "NOT_RUN"
    assert report["pfSf1T11Status"] == "NOT_RUN"
    assert report["complete"] is False


def test_hashed_owner_artifacts_are_required_before_completion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    evidence_root = tmp_path / "docs" / "phase-f" / "evidence"
    evidence_root.mkdir(parents=True)
    monkeypatch.setattr(uat, "ROOT", tmp_path)
    monkeypatch.setattr(uat, "EVIDENCE_ROOT", evidence_root)
    manifest = _manifest()
    evidence, _ = uat.collect(
        manifest,
        origin="https://uat.example.test",
        output=evidence_root / "collection.json",
        token=None,
        opener=_Opener(_Response(b'{"items":[]}')),
    )
    note = evidence_root / "owner-note.txt"
    screenshot = evidence_root / "screenshot.png"
    note.write_text("Owner performed the approved disposable UAT.", encoding="utf-8")
    screenshot.write_bytes(b"fake-png-evidence")
    evidence["manualEvidence"][0] = {
        "artifacts": [
            {
                "kind": "OWNER_NOTE",
                "path": "docs/phase-f/evidence/owner-note.txt",
                "sha256": hashlib.sha256(note.read_bytes()).hexdigest(),
            },
            {
                "kind": "SCREENSHOT",
                "path": "docs/phase-f/evidence/screenshot.png",
                "sha256": hashlib.sha256(screenshot.read_bytes()).hexdigest(),
            },
        ],
        "checkId": "owner-sample",
        "owner": "QA Owner",
        "performedAt": "2026-09-01T09:00:00Z",
        "status": "PASS",
    }

    report = uat.validate_evidence(
        evidence, manifest, origin="https://uat.example.test"
    )

    assert report["complete"] is True
    assert report["pfSf1T11Status"] == "PASS"
    evidence["manualEvidence"][0]["artifacts"][0]["sha256"] = "0" * 64
    with pytest.raises(uat.SafetyError, match="digest"):
        uat.validate_evidence(evidence, manifest, origin="https://uat.example.test")


def test_manifest_and_evidence_reject_unknown_or_duplicate_fields(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["unexpected"] = True
    with pytest.raises(uat.SafetyError, match="unknown"):
        uat.validate_manifest(manifest)

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schemaVersion":1,"schemaVersion":1}', encoding="utf-8")
    with pytest.raises(uat.SafetyError, match="strict UTF-8 JSON"):
        uat._load_json(duplicate)


def test_exclusive_output_refuses_overwrite(tmp_path: Path) -> None:
    output = tmp_path / "evidence.json"
    output.write_text("owner data", encoding="utf-8")
    with pytest.raises(FileExistsError):
        uat._write_exclusive(output, {"replacement": True})
    assert output.read_text(encoding="utf-8") == "owner data"
