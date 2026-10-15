"""Fail-closed, read-only PF-SF1-T11 UAT evidence collector.

The collector sends only manifest-declared GET requests to one exact HTTPS
origin. Redirects are rejected, response bodies are reduced to bounded hashes
and shape assertions, and optional bearer credentials are read only from a
named environment variable. Owner-performed evidence is deliberately separate
and remains NOT_RUN until an owner records hashed local artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import parse_qsl, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "deploy" / "operations" / "phase-f-uat-manifest.json"
EVIDENCE_ROOT = ROOT / "docs" / "phase-f" / "evidence"
COLLECTOR_VERSION = "pf-sf1-t11-uat-evidence/1.0.0"
SAFE_ENV_PATTERN = re.compile(r"^TC_PHASE_F_[A-Z0-9_]+$")
IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9-]{2,79}$")
DIGEST_PATTERN = re.compile(r"^[a-f0-9]{64}$")
SENSITIVE_QUERY_PATTERN = re.compile(
    r"(?:token|secret|signature|credential|password|api[-_]?key|access[-_]?key|x-amz)",
    re.IGNORECASE,
)
ARTIFACT_KINDS = frozenset({"AUDIT_LOG", "JSON", "OWNER_NOTE", "SCREENSHOT", "VIDEO"})
MANUAL_STATUSES = frozenset({"NOT_RUN", "PASS", "FAIL"})
RESPONSE_TYPES = frozenset({"array", "boolean", "integer", "null", "number", "object", "string"})


class SafetyError(RuntimeError):
    """The evidence request or artifact fails the UAT safety contract."""


class _DuplicateKeyError(ValueError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle, object_pairs_hook=_pairs_without_duplicates)
    except FileNotFoundError as error:
        raise SafetyError(f"JSON file does not exist: {path}") from error
    except (json.JSONDecodeError, UnicodeDecodeError, _DuplicateKeyError) as error:
        raise SafetyError(f"JSON file is not strict UTF-8 JSON: {path}") from error
    if not isinstance(value, dict):
        raise SafetyError("top-level JSON value must be an object")
    return value


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _require_keys(
    value: Mapping[str, Any],
    required: set[str],
    *,
    context: str,
    optional: set[str] | frozenset[str] = frozenset(),
) -> None:
    missing = sorted(required - set(value))
    extra = sorted(set(value) - required - optional)
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unknown " + ", ".join(extra))
        raise SafetyError(f"{context} has invalid keys: {'; '.join(details)}")


def _require_string(value: Any, *, context: str, maximum: int = 500) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise SafetyError(f"{context} must be a non-blank string of at most {maximum} characters")
    if any(ord(character) < 32 for character in value):
        raise SafetyError(f"{context} must not contain control characters")
    return value


def _require_string_list(
    value: Any,
    *,
    context: str,
    minimum: int = 1,
    maximum: int = 50,
) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise SafetyError(f"{context} must contain between {minimum} and {maximum} strings")
    result = [
        _require_string(item, context=f"{context}[{index}]")
        for index, item in enumerate(value)
    ]
    if len(result) != len(set(result)):
        raise SafetyError(f"{context} must not contain duplicates")
    return result


def _identifier(value: Any, *, context: str) -> str:
    result = _require_string(value, context=context, maximum=80)
    if not IDENTIFIER_PATTERN.fullmatch(result):
        raise SafetyError(f"{context} must be a lowercase kebab-case identifier")
    return result


def validate_origin(value: str) -> str:
    if value != value.strip() or any(ord(character) < 33 for character in value):
        raise SafetyError("origin must not contain whitespace or control characters")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise SafetyError("origin has an invalid port") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise SafetyError("origin must be an exact credential-free HTTPS origin without path, query, or fragment")
    if parsed.hostname != parsed.hostname.casefold() or parsed.hostname.endswith("."):
        raise SafetyError("origin hostname must be lowercase and must not end with a dot")
    if port == 443:
        raise SafetyError("origin must omit the default HTTPS port")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    normalized = f"https://{host}" + (f":{port}" if port is not None else "")
    if value != normalized:
        raise SafetyError(f"origin must use canonical form: {normalized}")
    return normalized


def _validate_request_path(value: Any, *, context: str) -> str:
    path = _require_string(value, context=context, maximum=1000)
    parsed = urlsplit(path)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or not parsed.path.startswith("/")
        or parsed.path.startswith("//")
        or "\\" in path
    ):
        raise SafetyError(f"{context} must be a same-origin absolute path")
    decoded_path = unquote(parsed.path)
    if any(segment in {".", ".."} for segment in decoded_path.split("/")):
        raise SafetyError(f"{context} must not contain dot segments")
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if any(not key or not value for key, value in pairs):
        raise SafetyError(f"{context} query parameters must have non-blank keys and values")
    if pairs != sorted(pairs) or len({key for key, _ in pairs}) != len(pairs):
        raise SafetyError(f"{context} query parameters must be unique and sorted")
    if any(SENSITIVE_QUERY_PATTERN.search(key) for key, _ in pairs):
        raise SafetyError(f"{context} contains a credential-like query parameter")
    return path


def _validate_body_contract(value: Any, *, context: str, content_type: str) -> None:
    if not isinstance(value, dict):
        raise SafetyError(f"{context} must be an object")
    _require_keys(
        value,
        {"equals", "requiredKeys", "requiredStrings", "requiredTypes", "type"},
        context=context,
    )
    body_type = value["type"]
    if body_type not in {"JSON", "TEXT"}:
        raise SafetyError(f"{context}.type must be JSON or TEXT")
    required_keys = _require_string_list(
        value["requiredKeys"], context=f"{context}.requiredKeys", minimum=0
    )
    required_strings = _require_string_list(
        value["requiredStrings"], context=f"{context}.requiredStrings", minimum=0
    )
    equals = value["equals"]
    required_types = value["requiredTypes"]
    if not isinstance(equals, dict) or not isinstance(required_types, dict):
        raise SafetyError(f"{context}.equals and requiredTypes must be objects")
    if body_type == "JSON":
        if content_type != "application/json" or required_strings:
            raise SafetyError(f"{context} JSON contract must use application/json and no requiredStrings")
        if any(not isinstance(key, str) or not key for key in equals):
            raise SafetyError(f"{context}.equals keys must be non-blank strings")
        for key, expected_type in required_types.items():
            if not isinstance(key, str) or expected_type not in RESPONSE_TYPES:
                raise SafetyError(f"{context}.requiredTypes has an invalid entry")
        if not set(equals).issubset(required_keys) or not set(required_types).issubset(required_keys):
            raise SafetyError(f"{context} assertions must reference requiredKeys")
    else:
        if not content_type.startswith("text/html") or required_keys or equals or required_types:
            raise SafetyError(f"{context} TEXT contract may contain only requiredStrings")
        if not required_strings:
            raise SafetyError(f"{context} TEXT contract requires at least one marker")


def validate_manifest(value: dict[str, Any]) -> dict[str, Any]:
    _require_keys(
        value,
        {
            "automatedChecks",
            "manifestId",
            "manualChecks",
            "originPolicy",
            "schemaVersion",
            "taskId",
            "workflows",
        },
        context="manifest",
    )
    if value["schemaVersion"] != 1 or value["taskId"] != "PF-SF1-T11":
        raise SafetyError("manifest must be schemaVersion 1 for PF-SF1-T11")
    _identifier(value["manifestId"], context="manifest.manifestId")

    policy = value["originPolicy"]
    if not isinstance(policy, dict):
        raise SafetyError("manifest.originPolicy must be an object")
    _require_keys(
        policy,
        {
            "allowedMethods",
            "authentication",
            "maxResponseBytes",
            "redirects",
            "scheme",
            "timeoutSeconds",
        },
        context="manifest.originPolicy",
    )
    if (
        policy["scheme"] != "https"
        or policy["redirects"] != "DENY"
        or policy["allowedMethods"] != ["GET"]
        or policy["authentication"] != "OPTIONAL_BEARER_ENV"
        or not isinstance(policy["timeoutSeconds"], int)
        or isinstance(policy["timeoutSeconds"], bool)
        or not 1 <= policy["timeoutSeconds"] <= 30
        or not isinstance(policy["maxResponseBytes"], int)
        or isinstance(policy["maxResponseBytes"], bool)
        or not 1024 <= policy["maxResponseBytes"] <= 4 * 1024 * 1024
    ):
        raise SafetyError("manifest.originPolicy violates the read-only HTTPS safety contract")

    workflows = value["workflows"]
    if not isinstance(workflows, list) or not workflows:
        raise SafetyError("manifest.workflows must be a non-empty array")
    workflow_ids: list[str] = []
    for index, workflow in enumerate(workflows):
        context = f"manifest.workflows[{index}]"
        if not isinstance(workflow, dict):
            raise SafetyError(f"{context} must be an object")
        _require_keys(workflow, {"acceptance", "id", "title"}, context=context)
        workflow_ids.append(_identifier(workflow["id"], context=f"{context}.id"))
        _require_string(workflow["title"], context=f"{context}.title", maximum=200)
        _require_string_list(workflow["acceptance"], context=f"{context}.acceptance")
    if len(workflow_ids) != len(set(workflow_ids)):
        raise SafetyError("manifest workflow IDs must be unique")
    known_workflows = set(workflow_ids)

    automated = value["automatedChecks"]
    if not isinstance(automated, list) or not automated:
        raise SafetyError("manifest.automatedChecks must be a non-empty array")
    automated_ids: list[str] = []
    for index, check in enumerate(automated):
        context = f"manifest.automatedChecks[{index}]"
        if not isinstance(check, dict):
            raise SafetyError(f"{context} must be an object")
        _require_keys(check, {"expect", "id", "request", "title", "workflowIds"}, context=context)
        automated_ids.append(_identifier(check["id"], context=f"{context}.id"))
        _require_string(check["title"], context=f"{context}.title", maximum=200)
        refs = _require_string_list(check["workflowIds"], context=f"{context}.workflowIds")
        if not set(refs).issubset(known_workflows):
            raise SafetyError(f"{context} references an unknown workflow")
        request = check["request"]
        if not isinstance(request, dict):
            raise SafetyError(f"{context}.request must be an object")
        _require_keys(request, {"method", "path"}, context=f"{context}.request")
        if request["method"] != "GET":
            raise SafetyError(f"{context}.request method must be GET")
        _validate_request_path(request["path"], context=f"{context}.request.path")
        expect = check["expect"]
        if not isinstance(expect, dict):
            raise SafetyError(f"{context}.expect must be an object")
        _require_keys(
            expect,
            {"body", "contentType", "requiredHeaders", "statusCodes"},
            context=f"{context}.expect",
        )
        statuses = expect["statusCodes"]
        if (
            not isinstance(statuses, list)
            or not statuses
            or any(not isinstance(status, int) or isinstance(status, bool) or not 200 <= status <= 299 for status in statuses)
            or statuses != sorted(set(statuses))
        ):
            raise SafetyError(f"{context}.expect.statusCodes must be unique sorted 2xx codes")
        content_type = _require_string(expect["contentType"], context=f"{context}.expect.contentType", maximum=100).casefold()
        if content_type not in {"application/json", "text/html"}:
            raise SafetyError(f"{context}.expect.contentType is not allowed")
        headers = expect["requiredHeaders"]
        if not isinstance(headers, dict) or not headers:
            raise SafetyError(f"{context}.expect.requiredHeaders must be a non-empty object")
        for name, header_value in headers.items():
            if not isinstance(name, str) or name != name.casefold() or not re.fullmatch(r"[a-z0-9-]+", name):
                raise SafetyError(f"{context}.expect.requiredHeaders has an invalid name")
            _require_string(header_value, context=f"{context}.expect.requiredHeaders.{name}", maximum=500)
        _validate_body_contract(expect["body"], context=f"{context}.expect.body", content_type=content_type)
    if len(automated_ids) != len(set(automated_ids)):
        raise SafetyError("manifest automated check IDs must be unique")

    manual = value["manualChecks"]
    if not isinstance(manual, list) or not manual:
        raise SafetyError("manifest.manualChecks must be a non-empty array")
    manual_ids: list[str] = []
    manually_covered: set[str] = set()
    for index, check in enumerate(manual):
        context = f"manifest.manualChecks[{index}]"
        if not isinstance(check, dict):
            raise SafetyError(f"{context} must be an object")
        _require_keys(
            check,
            {"defaultStatus", "id", "ownerRole", "requiredArtifactKinds", "steps", "title", "workflowIds"},
            context=context,
        )
        manual_ids.append(_identifier(check["id"], context=f"{context}.id"))
        _require_string(check["title"], context=f"{context}.title", maximum=200)
        refs = _require_string_list(check["workflowIds"], context=f"{context}.workflowIds")
        if not set(refs).issubset(known_workflows):
            raise SafetyError(f"{context} references an unknown workflow")
        manually_covered.update(refs)
        _require_string(check["ownerRole"], context=f"{context}.ownerRole", maximum=100)
        _require_string_list(check["steps"], context=f"{context}.steps", maximum=30)
        kinds = _require_string_list(
            check["requiredArtifactKinds"], context=f"{context}.requiredArtifactKinds"
        )
        if not set(kinds).issubset(ARTIFACT_KINDS) or "OWNER_NOTE" not in kinds:
            raise SafetyError(f"{context} artifact kinds must be supported and include OWNER_NOTE")
        if check["defaultStatus"] != "NOT_RUN":
            raise SafetyError(f"{context}.defaultStatus must be NOT_RUN")
    if len(manual_ids) != len(set(manual_ids)) or set(manual_ids) & set(automated_ids):
        raise SafetyError("all automated/manual check IDs must be globally unique")
    if manually_covered != known_workflows:
        raise SafetyError("every workflow must have explicit owner-performed manual evidence")
    return value


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    raise SafetyError("response contains an unsupported JSON value")


def _response_result(
    check: Mapping[str, Any],
    *,
    origin: str,
    opener: Any,
    timeout: int,
    maximum_bytes: int,
    token: str | None,
) -> dict[str, Any]:
    check_id = str(check["id"])
    request_contract = check["request"]
    expect = check["expect"]
    path = str(request_contract["path"])
    request_headers = {
        "Accept": str(expect["contentType"]),
        "Accept-Encoding": "identity",
        "User-Agent": COLLECTOR_VERSION,
    }
    if token is not None:
        request_headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(origin + path, headers=request_headers, method="GET")
    status_code: int | None = None
    try:
        with opener.open(request, timeout=timeout) as response:
            status_code = int(response.status)
            if status_code not in expect["statusCodes"]:
                return _failed_result(check_id, path, status_code, "HTTP_STATUS_MISMATCH")
            declared_length = response.headers.get("Content-Length")
            if declared_length is not None:
                try:
                    if int(declared_length) > maximum_bytes:
                        return _failed_result(check_id, path, status_code, "RESPONSE_TOO_LARGE")
                except ValueError:
                    return _failed_result(check_id, path, status_code, "INVALID_CONTENT_LENGTH")
            body = response.read(maximum_bytes + 1)
            if len(body) > maximum_bytes:
                return _failed_result(check_id, path, status_code, "RESPONSE_TOO_LARGE")
            content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().casefold()
            if content_type != expect["contentType"]:
                return _failed_result(check_id, path, status_code, "CONTENT_TYPE_MISMATCH")
            for name, expected_value in expect["requiredHeaders"].items():
                if response.headers.get(name) != expected_value:
                    return _failed_result(check_id, path, status_code, "REQUIRED_HEADER_MISMATCH")
            contract = expect["body"]
            if contract["type"] == "JSON":
                try:
                    payload = json.loads(body, object_pairs_hook=_pairs_without_duplicates)
                except (json.JSONDecodeError, UnicodeDecodeError, _DuplicateKeyError):
                    return _failed_result(check_id, path, status_code, "INVALID_JSON")
                if not isinstance(payload, dict):
                    return _failed_result(check_id, path, status_code, "JSON_SHAPE_MISMATCH")
                if not set(contract["requiredKeys"]).issubset(payload):
                    return _failed_result(check_id, path, status_code, "JSON_REQUIRED_KEY_MISSING")
                if any(payload.get(key) != expected for key, expected in contract["equals"].items()):
                    return _failed_result(check_id, path, status_code, "JSON_VALUE_MISMATCH")
                if any(_json_type(payload.get(key)) != expected for key, expected in contract["requiredTypes"].items()):
                    return _failed_result(check_id, path, status_code, "JSON_TYPE_MISMATCH")
            else:
                try:
                    text = body.decode("utf-8")
                except UnicodeDecodeError:
                    return _failed_result(check_id, path, status_code, "INVALID_UTF8")
                if any(marker not in text for marker in contract["requiredStrings"]):
                    return _failed_result(check_id, path, status_code, "TEXT_MARKER_MISSING")
            return {
                "bodySha256": hashlib.sha256(body).hexdigest(),
                "byteLength": len(body),
                "checkId": check_id,
                "contentType": content_type,
                "errorCode": None,
                "method": "GET",
                "path": path,
                "status": "PASS",
                "statusCode": status_code,
            }
    except urllib.error.HTTPError as error:
        code = int(error.code)
        error.close()
        return _failed_result(
            check_id,
            path,
            code,
            "REDIRECT_REJECTED" if 300 <= code <= 399 else "HTTP_STATUS_MISMATCH",
        )
    except (TimeoutError, urllib.error.URLError, OSError):
        return _failed_result(check_id, path, status_code, "REQUEST_FAILED")


def _failed_result(
    check_id: str, path: str, status_code: int | None, error_code: str
) -> dict[str, Any]:
    return {
        "bodySha256": None,
        "byteLength": None,
        "checkId": check_id,
        "contentType": None,
        "errorCode": error_code,
        "method": "GET",
        "path": path,
        "status": "FAIL",
        "statusCode": status_code,
    }


def _manual_not_run(check: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "artifacts": [],
        "checkId": check["id"],
        "owner": None,
        "performedAt": None,
        "status": "NOT_RUN",
    }


def _safe_output_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    if path.suffix != ".json":
        raise SafetyError("evidence output must use a .json filename")
    parent = path.parent.resolve()
    evidence_root = EVIDENCE_ROOT.resolve()
    if evidence_root not in {parent, *parent.parents}:
        raise SafetyError("evidence output must be under docs/phase-f/evidence")
    if not parent.is_dir():
        raise SafetyError("evidence output parent directory does not exist")
    if path.exists() or path.is_symlink():
        raise SafetyError("evidence output already exists; overwrite is forbidden")
    return parent / path.name


def _write_exclusive(path: Path, value: object) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def collect(
    manifest: dict[str, Any],
    *,
    origin: str,
    output: Path,
    token: str | None,
    opener: Any | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    policy = manifest["originPolicy"]
    active_opener = opener or urllib.request.build_opener(_NoRedirect())
    automated = [
        _response_result(
            check,
            origin=origin,
            opener=active_opener,
            timeout=int(policy["timeoutSeconds"]),
            maximum_bytes=int(policy["maxResponseBytes"]),
            token=token,
        )
        for check in manifest["automatedChecks"]
    ]
    evidence = {
        "automatedEvidence": automated,
        "collectedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "collectorVersion": COLLECTOR_VERSION,
        "evidenceKind": "PF_SF1_T11_UAT",
        "manifestId": manifest["manifestId"],
        "manifestSha256": _digest(manifest),
        "manualEvidence": [_manual_not_run(check) for check in manifest["manualChecks"]],
        "origin": origin,
        "schemaVersion": 1,
        "taskId": "PF-SF1-T11",
    }
    _write_exclusive(output, evidence)
    passed = sum(result["status"] == "PASS" for result in automated)
    report = {
        "automated": {"failed": len(automated) - passed, "passed": passed, "total": len(automated)},
        "complete": False,
        "manual": {"notRun": len(manifest["manualChecks"]), "passed": 0, "total": len(manifest["manualChecks"])},
        "ok": passed == len(automated),
        "origin": origin,
        "output": str(output.relative_to(ROOT)),
        "pfSf1T11Status": "NOT_RUN",
    }
    return evidence, report


def _parse_time(value: Any, *, context: str) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise SafetyError(f"{context} must be an RFC3339 UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise SafetyError(f"{context} is not a valid timestamp") from error
    if parsed.tzinfo is None:
        raise SafetyError(f"{context} must include a timezone")


def _artifact_path(value: Any, *, context: str) -> Path:
    relative = _require_string(value, context=context, maximum=500)
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise SafetyError(f"{context} must be a repository-relative evidence path")
    unresolved = ROOT / candidate
    if unresolved.is_symlink():
        raise SafetyError(f"{context} must not name a symlink")
    path = unresolved.resolve()
    evidence_root = EVIDENCE_ROOT.resolve()
    if evidence_root not in {path.parent, *path.parent.parents}:
        raise SafetyError(f"{context} must be under docs/phase-f/evidence")
    if not path.is_file():
        raise SafetyError(f"{context} must name an existing non-symlink file")
    return path


def _validate_automated_evidence(
    value: Any, manifest: Mapping[str, Any]
) -> tuple[int, int]:
    checks = manifest["automatedChecks"]
    if not isinstance(value, list) or len(value) != len(checks):
        raise SafetyError("automatedEvidence must have exactly one result per manifest check")
    passed = 0
    for index, (result, check) in enumerate(zip(value, checks, strict=True)):
        context = f"evidence.automatedEvidence[{index}]"
        if not isinstance(result, dict):
            raise SafetyError(f"{context} must be an object")
        _require_keys(
            result,
            {"bodySha256", "byteLength", "checkId", "contentType", "errorCode", "method", "path", "status", "statusCode"},
            context=context,
        )
        if (
            result["checkId"] != check["id"]
            or result["method"] != "GET"
            or result["path"] != check["request"]["path"]
            or result["status"] not in {"PASS", "FAIL"}
        ):
            raise SafetyError(f"{context} does not match its manifest check")
        if result["status"] == "PASS":
            if (
                result["statusCode"] not in check["expect"]["statusCodes"]
                or result["contentType"] != check["expect"]["contentType"]
                or not isinstance(result["byteLength"], int)
                or isinstance(result["byteLength"], bool)
                or result["byteLength"] < 0
                or not isinstance(result["bodySha256"], str)
                or not DIGEST_PATTERN.fullmatch(result["bodySha256"])
                or result["errorCode"] is not None
            ):
                raise SafetyError(f"{context} has an invalid PASS receipt")
            passed += 1
        elif (
            result["bodySha256"] is not None
            or result["byteLength"] is not None
            or result["contentType"] is not None
            or not isinstance(result["errorCode"], str)
            or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,79}", result["errorCode"])
            or (result["statusCode"] is not None and (not isinstance(result["statusCode"], int) or isinstance(result["statusCode"], bool)))
        ):
            raise SafetyError(f"{context} has an invalid FAIL receipt")
    return passed, len(checks) - passed


def _validate_manual_evidence(value: Any, manifest: Mapping[str, Any]) -> dict[str, int]:
    checks = manifest["manualChecks"]
    if not isinstance(value, list) or len(value) != len(checks):
        raise SafetyError("manualEvidence must have exactly one result per manifest check")
    counts = {"failed": 0, "notRun": 0, "passed": 0}
    for index, (result, check) in enumerate(zip(value, checks, strict=True)):
        context = f"evidence.manualEvidence[{index}]"
        if not isinstance(result, dict):
            raise SafetyError(f"{context} must be an object")
        _require_keys(result, {"artifacts", "checkId", "owner", "performedAt", "status"}, context=context)
        status = result["status"]
        if result["checkId"] != check["id"] or status not in MANUAL_STATUSES:
            raise SafetyError(f"{context} does not match its manifest check")
        artifacts = result["artifacts"]
        if not isinstance(artifacts, list):
            raise SafetyError(f"{context}.artifacts must be an array")
        if status == "NOT_RUN":
            if result["owner"] is not None or result["performedAt"] is not None or artifacts:
                raise SafetyError(f"{context} NOT_RUN evidence must remain empty")
            counts["notRun"] += 1
            continue
        _require_string(result["owner"], context=f"{context}.owner", maximum=100)
        _parse_time(result["performedAt"], context=f"{context}.performedAt")
        if not artifacts:
            raise SafetyError(f"{context} executed evidence requires artifacts")
        observed_kinds: set[str] = set()
        observed_paths: set[str] = set()
        for artifact_index, artifact in enumerate(artifacts):
            artifact_context = f"{context}.artifacts[{artifact_index}]"
            if not isinstance(artifact, dict):
                raise SafetyError(f"{artifact_context} must be an object")
            _require_keys(artifact, {"kind", "path", "sha256"}, context=artifact_context)
            kind = artifact["kind"]
            if kind not in ARTIFACT_KINDS:
                raise SafetyError(f"{artifact_context}.kind is unsupported")
            path = _artifact_path(artifact["path"], context=f"{artifact_context}.path")
            declared_digest = artifact["sha256"]
            if not isinstance(declared_digest, str) or not DIGEST_PATTERN.fullmatch(declared_digest):
                raise SafetyError(f"{artifact_context}.sha256 must be a lowercase SHA-256 digest")
            actual_digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual_digest != declared_digest:
                raise SafetyError(f"{artifact_context} digest does not match the local file")
            observed_kinds.add(kind)
            artifact_path = str(artifact["path"])
            if artifact_path in observed_paths:
                raise SafetyError(f"{context} must not repeat artifact paths")
            observed_paths.add(artifact_path)
        if status == "PASS" and not set(check["requiredArtifactKinds"]).issubset(observed_kinds):
            raise SafetyError(f"{context} PASS is missing a required artifact kind")
        if status == "FAIL" and "OWNER_NOTE" not in observed_kinds:
            raise SafetyError(f"{context} FAIL requires an OWNER_NOTE artifact")
        counts["passed" if status == "PASS" else "failed"] += 1
    return counts


def validate_evidence(
    evidence: dict[str, Any], manifest: dict[str, Any], *, origin: str
) -> dict[str, Any]:
    _require_keys(
        evidence,
        {
            "automatedEvidence",
            "collectedAt",
            "collectorVersion",
            "evidenceKind",
            "manifestId",
            "manifestSha256",
            "manualEvidence",
            "origin",
            "schemaVersion",
            "taskId",
        },
        context="evidence",
    )
    if (
        evidence["schemaVersion"] != 1
        or evidence["evidenceKind"] != "PF_SF1_T11_UAT"
        or evidence["taskId"] != "PF-SF1-T11"
        or evidence["collectorVersion"] != COLLECTOR_VERSION
        or evidence["manifestId"] != manifest["manifestId"]
        or evidence["manifestSha256"] != _digest(manifest)
        or evidence["origin"] != origin
    ):
        raise SafetyError("evidence identity, manifest digest, collector, or exact origin does not match")
    _parse_time(evidence["collectedAt"], context="evidence.collectedAt")
    automated_passed, automated_failed = _validate_automated_evidence(
        evidence["automatedEvidence"], manifest
    )
    manual = _validate_manual_evidence(evidence["manualEvidence"], manifest)
    automated_status = "PASS" if automated_failed == 0 else "FAIL"
    if manual["notRun"]:
        manual_status = "NOT_RUN"
        task_status = "NOT_RUN"
    elif manual["failed"]:
        manual_status = "FAIL"
        task_status = "FAIL"
    else:
        manual_status = "PASS"
        task_status = "PASS" if automated_status == "PASS" else "FAIL"
    return {
        "automated": {
            "failed": automated_failed,
            "passed": automated_passed,
            "status": automated_status,
            "total": automated_passed + automated_failed,
        },
        "complete": task_status == "PASS",
        "manual": {
            **manual,
            "status": manual_status,
            "total": sum(manual.values()),
        },
        "manifestSha256": _digest(manifest),
        "ok": task_status == "PASS",
        "origin": origin,
        "pfSf1T11Status": task_status,
    }


def _token_from_environment(name: str | None) -> str | None:
    if name is None:
        return None
    if not SAFE_ENV_PATTERN.fullmatch(name):
        raise SafetyError("bearer token environment name must start with TC_PHASE_F_")
    value = os.getenv(name)
    if value is None or not value or value != value.strip() or "\n" in value or "\r" in value:
        raise SafetyError("bearer token environment value is missing or malformed")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect and validate read-only PF-SF1-T11 UAT evidence.")
    commands = parser.add_subparsers(dest="command", required=True)
    manifest = commands.add_parser("validate-manifest")
    manifest.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    collect_parser = commands.add_parser("collect")
    collect_parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    collect_parser.add_argument("--origin", required=True)
    collect_parser.add_argument("--output", required=True)
    collect_parser.add_argument("--bearer-token-env")
    evidence = commands.add_parser("validate-evidence")
    evidence.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    evidence.add_argument("--evidence", required=True)
    evidence.add_argument("--origin", required=True)
    return parser


def _print_error(error: SafetyError) -> NoReturn:
    print(json.dumps({"error": str(error), "ok": False}, sort_keys=True), file=sys.stderr)
    raise SystemExit(2)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        manifest_path = Path(args.manifest)
        manifest = validate_manifest(_load_json(manifest_path))
        if args.command == "validate-manifest":
            print(
                json.dumps(
                    {
                        "automatedCheckCount": len(manifest["automatedChecks"]),
                        "manifestId": manifest["manifestId"],
                        "manifestSha256": _digest(manifest),
                        "manualCheckCount": len(manifest["manualChecks"]),
                        "ok": True,
                        "taskId": manifest["taskId"],
                        "workflowCount": len(manifest["workflows"]),
                    },
                    sort_keys=True,
                )
            )
            return 0
        origin = validate_origin(args.origin)
        if args.command == "collect":
            output = _safe_output_path(args.output)
            token = _token_from_environment(args.bearer_token_env)
            _, report = collect(manifest, origin=origin, output=output, token=token)
            print(json.dumps(report, sort_keys=True))
            return 0 if report["ok"] else 1
        evidence = _load_json(Path(args.evidence))
        report = validate_evidence(evidence, manifest, origin=origin)
        print(json.dumps(report, sort_keys=True))
        return 0 if report["complete"] else 3
    except SafetyError as error:
        print(json.dumps({"error": str(error), "ok": False}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
