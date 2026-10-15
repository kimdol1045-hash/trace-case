"""Replay an external CSV as an idempotent Phase B API UAT.

Dry-run is the default. Pass ``--apply`` to create/update cycles, record the
CSV-derived WEB results, create two linked issues, upload evidence, and prove
the completion/reopen lifecycle. The source CSV must be supplied explicitly
and remains outside the repository. This script never resets library data.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

DEFAULT_API = "http://127.0.0.1:8000"
ACTOR = "CSV Phase B replay"
BLOCKED_ACTION_FRAGMENT = "배송지를 선택하기 전의 배송비"
BLOCKED_NOTE = "일본 마켓만 제공되어 국가 변경에 따른 배송비 재계산을 확인할 수 없음"
TARGET_WORK_ITEMS = ("WORK-5371", "WORK-6651")

ISSUE_SPECS = (
    {
        "idempotencyKey": "phase-b-shipping-environment-v1",
        "severity": "MEDIUM",
        "summary": "[Phase B UAT] 국가별 배송비 재계산 검증 환경 필요",
        "precondition": "서로 다른 배송 정책을 가진 국가를 선택할 수 있는 QA 환경이 준비되어 있다.",
        "action": "주문서에서 배송지를 일본 외 국가로 변경하고 배송비와 최종 결제 금액을 확인한다.",
        "expectedResult": "배송 국가 정책에 맞춰 배송비와 최종 결제 금액이 즉시 재계산된다.",
        "actualResult": BLOCKED_NOTE,
        "filename": "shipping-recalculation-blocked.txt",
        "finalStatus": "CLOSED",
    },
    {
        "idempotencyKey": "phase-b-shipping-test-data-v1",
        "severity": "LOW",
        "summary": "[Phase B UAT] 다국가 배송비 검증 데이터 보강",
        "precondition": "국가별 배송비 정책과 주소 테스트 데이터가 문서화되어 있다.",
        "action": "일본과 비교 국가의 주소·배송 정책·예상 배송비 조합을 검증한다.",
        "expectedResult": "두 국가 이상의 재현 가능한 입력값과 예상 배송비가 제공된다.",
        "actualResult": "일본 데이터만 있어 국가 변경 경계값을 교차 검증할 수 없다.",
        "filename": "multi-market-test-data-request.txt",
        "finalStatus": "IN_PROGRESS",
    },
)


class UatError(RuntimeError):
    """Raised when the live Phase B acceptance contract is not satisfied."""


class Api:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(timeout=30.0)

    def close(self) -> None:
        self.client.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        last_error: Exception | None = None
        for attempt in range(8):
            try:
                response = self.client.request(
                    method,
                    f"{self.base_url}{path}",
                    json=payload,
                    headers=headers,
                )
                if response.status_code not in expected:
                    raise UatError(
                        f"{method} {path} -> {response.status_code}: {response.text}"
                    )
                return response.json() if response.content else None
            except (httpx.ConnectError, httpx.ReadError) as error:
                last_error = error
                if attempt == 7:
                    break
                time.sleep(0.5 * (attempt + 1))
        raise UatError(f"{method} {path} failed after retries: {last_error}")

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def mutation(
        self,
        method: str,
        path: str,
        payload: dict[str, Any],
        key: str,
        *,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        return self.request(
            method,
            path,
            payload=payload,
            idempotency_key=key,
            expected=expected,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply the UAT mutations")
    parser.add_argument("--api", default=DEFAULT_API, help="API base URL")
    parser.add_argument(
        "--csv",
        type=Path,
        required=True,
        help="source CSV path (kept outside the repository)",
    )
    return parser.parse_args()


def csv_result_counts(path: Path) -> Counter[str]:
    if not path.is_file():
        raise UatError(f"CSV not found: {path}")
    counts: Counter[str] = Counter()
    with path.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.reader(source):
            result = row[9].strip() if len(row) > 9 else ""
            if result:
                counts[result] += 1
    if counts != Counter({"Pass": 72, "확인불가": 1}):
        raise UatError(f"unexpected CSV result distribution: {dict(counts)}")
    return counts


def page_items(api: Api, path: str) -> list[dict[str, Any]]:
    separator = "&" if "?" in path else "?"
    page = api.get(f"{path}{separator}limit=200")
    return list(page["items"])


def get_cycle(api: Api, cycle_id: str) -> dict[str, Any]:
    return api.get(f"/api/cycles/{cycle_id}")


def ensure_cycle(
    api: Api,
    cycles: list[dict[str, Any]],
    work_item: dict[str, Any],
    *,
    apply: bool,
) -> dict[str, Any] | None:
    existing = next(
        (
            cycle
            for cycle in cycles
            if cycle["scope"]["type"] == "WORK_ITEM"
            and cycle["scope"]["workItemId"] == work_item["id"]
        ),
        None,
    )
    if existing:
        return get_cycle(api, existing["id"])
    if not apply:
        return None
    key = work_item["key"]
    return api.mutation(
        "POST",
        "/api/cycles",
        {
            "name": f"[Phase B UAT] {work_item['summary']}",
            "description": f"{key} CSV 이관 결과를 실제 QA 흐름으로 검증하는 Phase B 사이클",
            "startDate": date.today().isoformat(),
            "scope": {
                "type": "WORK_ITEM",
                "workItemId": work_item["id"],
                "workItemKey": key,
            },
        },
        f"phase-b-cycle-{key.lower()}-v1",
        expected=(201,),
    )


def ensure_started(api: Api, cycle: dict[str, Any], *, apply: bool) -> dict[str, Any]:
    if cycle["status"] != "PLANNED" or not apply:
        return cycle
    result = api.mutation(
        "POST",
        f"/api/cycles/{cycle['id']}/start",
        {"baseResourceVersion": cycle["resourceVersion"], "actorLabel": ACTOR},
        f"phase-b-start-{cycle['id']}-v1",
    )
    return result["cycle"]


def desired_results(item: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for step in item["pinnedVersion"]["steps"]:
        blocked = BLOCKED_ACTION_FRAGMENT in step["action"]
        result: dict[str, Any] = {
            "sequence": step["sequence"],
            "status": "BLOCKED" if blocked else "PASS",
        }
        if blocked:
            result["note"] = BLOCKED_NOTE
        results.append(result)
    return results


def execution_matches(execution: dict[str, Any], desired: list[dict[str, Any]]) -> bool:
    current = {
        step["sequence"]: (step["status"], step.get("note"))
        for step in execution["steps"]
    }
    expected = {
        step["sequence"]: (step["status"], step.get("note")) for step in desired
    }
    return current == expected


def record_cycle(
    api: Api, cycle: dict[str, Any], *, apply: bool
) -> tuple[Counter[str], dict[str, Any] | None]:
    items = page_items(api, f"/api/cycles/{cycle['id']}/items")
    counts: Counter[str] = Counter()
    blocked_execution: dict[str, Any] | None = None
    for item in items:
        desired = desired_results(item)
        counts.update(result["status"] for result in desired)
        execution = api.get(f"/api/cycle-items/{item['id']}/executions/WEB/steps")
        if apply and not execution_matches(execution, desired):
            execution = api.request(
                "PATCH",
                f"/api/cycle-items/{item['id']}/executions/WEB/steps",
                payload={"results": desired, "executorLabel": ACTOR},
            )
        if any(result["status"] == "BLOCKED" for result in desired):
            blocked_execution = execution
    return counts, blocked_execution


def find_uat_issue(api: Api, summary: str) -> dict[str, Any] | None:
    query = urlencode({"search": summary, "limit": 50})
    page = api.get(f"/api/issues?{query}")
    match = next((item for item in page["items"] if item["summary"] == summary), None)
    return api.get(f"/api/issues/{match['id']}") if match else None


def update_issue_fields(
    api: Api, issue: dict[str, Any], spec: dict[str, str]
) -> dict[str, Any]:
    if issue["summary"] == spec["summary"]:
        return issue
    return api.mutation(
        "PATCH",
        f"/api/issues/{issue['id']}",
        {
            "baseResourceVersion": issue["resourceVersion"],
            "summary": spec["summary"],
            "precondition": spec["precondition"],
            "steps": [
                {
                    "sequence": 1,
                    "action": spec["action"],
                    "expectedResult": spec["expectedResult"],
                }
            ],
            "expectedResult": spec["expectedResult"],
            "actualResult": spec["actualResult"],
            "assigneeLabel": "QA 운영",
            "reason": "Phase B CSV UAT 재현 정보 정리",
        },
        f"phase-b-fields-{issue['id']}-v1",
    )


def transition_issue(api: Api, issue: dict[str, Any], target: str) -> dict[str, Any]:
    paths = {
        "OPEN": ("IN_PROGRESS",),
        "IN_PROGRESS": ("RESOLVED",),
        "RESOLVED": ("CLOSED",),
    }
    rank = {"OPEN": 0, "IN_PROGRESS": 1, "RESOLVED": 2, "CLOSED": 3}
    while issue["status"] != target:
        if (
            issue["status"] in {"REOPENED", "CLOSED"}
            or rank[issue["status"]] > rank[target]
        ):
            return issue
        next_status = paths[issue["status"]][0]
        issue = api.request(
            "PATCH",
            f"/api/issues/{issue['id']}",
            payload={
                "baseResourceVersion": issue["resourceVersion"],
                "status": next_status,
                "reason": "Phase B 이슈 상태 전이 검증",
            },
        )
    return issue


def ensure_attachment(
    api: Api, issue: dict[str, Any], evidence_path: Path
) -> dict[str, Any]:
    query = urlencode(
        [("ownerTypes", "ISSUE"), ("issueIds", issue["id"]), ("limit", "50")]
    )
    existing = next(
        (
            item
            for item in api.get(f"/api/attachments?{query}")["items"]
            if item["filename"] == evidence_path.name
        ),
        None,
    )
    if existing:
        return existing
    content = evidence_path.read_bytes()
    checksum = hashlib.sha256(content).hexdigest()
    key = f"phase-b-attachment-{issue['id']}-{evidence_path.stem}-v1"
    session = api.mutation(
        "POST",
        f"/api/issues/{issue['id']}/attachment-upload-sessions",
        {
            "idempotencyKey": key,
            "filename": evidence_path.name,
            "declaredMimeType": "text/plain",
            "declaredSizeBytes": len(content),
            "clientSha256": checksum,
        },
        key,
        expected=(201,),
    )
    upload = session["upload"]
    response = httpx.put(
        upload["url"], content=content, headers=upload["headers"], timeout=30
    )
    if response.status_code not in (200, 201, 204):
        raise UatError(f"presigned PUT -> {response.status_code}: {response.text}")
    completed = api.mutation(
        "POST",
        "/api/attachment-upload-sessions/complete",
        {"idempotencyKey": key, "uploadToken": session["uploadToken"]},
        key,
        expected=(201,),
    )
    return completed["attachment"]


def ensure_issues(
    api: Api,
    blocked_execution: dict[str, Any],
    evidence_dir: Path,
    *,
    apply: bool,
) -> list[dict[str, Any]]:
    if not apply:
        return []
    attempt_id = blocked_execution.get("attemptId")
    if not attempt_id:
        raise UatError("blocked WEB execution did not produce an attemptId")
    completed: list[dict[str, Any]] = []
    for spec in ISSUE_SPECS:
        issue = find_uat_issue(api, spec["summary"])
        if issue is None:
            issue = api.mutation(
                "POST",
                f"/api/execution-attempts/{attempt_id}/issues",
                {
                    "idempotencyKey": spec["idempotencyKey"],
                    "severity": spec["severity"],
                    "assigneeLabel": "QA 운영",
                },
                spec["idempotencyKey"],
                expected=(201,),
            )
        issue = update_issue_fields(api, issue, spec)
        ensure_attachment(api, issue, evidence_dir / spec["filename"])
        issue = transition_issue(api, issue, spec["finalStatus"])
        completed.append(issue)
    return completed


def complete_and_prove_reopen(
    api: Api, cycle: dict[str, Any], *, prove_reopen: bool, apply: bool
) -> dict[str, Any]:
    if not apply:
        return cycle
    if cycle["status"] == "IN_PROGRESS":
        result = api.mutation(
            "POST",
            f"/api/cycles/{cycle['id']}/complete",
            {
                "baseResourceVersion": cycle["resourceVersion"],
                "acknowledgeIncomplete": True,
                "actorLabel": ACTOR,
            },
            f"phase-b-complete-{cycle['id']}-v1",
        )
        cycle = result["cycle"]
        if prove_reopen:
            result = api.mutation(
                "POST",
                f"/api/cycles/{cycle['id']}/reopen",
                {
                    "baseResourceVersion": cycle["resourceVersion"],
                    "reason": "Phase B 완료 후 재오픈 전이 검증",
                    "actorLabel": ACTOR,
                },
                f"phase-b-reopen-{cycle['id']}-v1",
            )
            cycle = result["cycle"]
            result = api.mutation(
                "POST",
                f"/api/cycles/{cycle['id']}/complete",
                {
                    "baseResourceVersion": cycle["resourceVersion"],
                    "acknowledgeIncomplete": True,
                    "actorLabel": ACTOR,
                },
                f"phase-b-complete-{cycle['id']}-v2",
            )
            cycle = result["cycle"]
    return cycle


def ledger_counts(api: Api, cycles: list[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for cycle in cycles:
        for item in page_items(api, f"/api/cycles/{cycle['id']}/items"):
            execution = api.get(f"/api/cycle-items/{item['id']}/executions/WEB/steps")
            counts.update(step["status"] for step in execution["steps"])
    return counts


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    source_csv = args.csv if args.csv.is_absolute() else root / args.csv
    evidence_dir = root / "docs" / "phase-b" / "evidence"
    source_counts = csv_result_counts(source_csv)
    api = Api(args.api)
    try:
        api.get("/api/health")
        work_items = {item["key"]: item for item in page_items(api, "/api/work-items")}
        missing = set(TARGET_WORK_ITEMS).difference(work_items)
        if missing:
            raise UatError(f"missing imported work items: {sorted(missing)}")
        cycles = page_items(api, "/api/cycles")
        selected: list[dict[str, Any]] = []
        for key in TARGET_WORK_ITEMS:
            cycle = ensure_cycle(api, cycles, work_items[key], apply=args.apply)
            if cycle is not None:
                selected.append(ensure_started(api, cycle, apply=args.apply))

        planned_counts: Counter[str] = Counter()
        blocked_execution: dict[str, Any] | None = None
        if args.apply:
            for cycle in selected:
                counts, blocked = record_cycle(api, cycle, apply=True)
                planned_counts.update(counts)
                blocked_execution = blocked or blocked_execution
        else:
            planned_counts.update(
                {"PASS": source_counts["Pass"], "BLOCKED": source_counts["확인불가"]}
            )

        if planned_counts != Counter({"PASS": 72, "BLOCKED": 1}):
            raise UatError(f"unexpected planned results: {dict(planned_counts)}")

        issues: list[dict[str, Any]] = []
        if blocked_execution is not None:
            issues = ensure_issues(
                api,
                blocked_execution,
                evidence_dir,
                apply=args.apply,
            )

        if args.apply:
            selected = [
                complete_and_prove_reopen(
                    api,
                    cycle,
                    prove_reopen=index == 0,
                    apply=True,
                )
                for index, cycle in enumerate(selected)
            ]
            actual_counts = ledger_counts(api, selected)
            if actual_counts != Counter({"PASS": 72, "BLOCKED": 1}):
                raise UatError(f"unexpected ledger results: {dict(actual_counts)}")
        else:
            actual_counts = Counter()

        overview = api.get("/api/dashboard/overview")
        reports = api.get("/api/reports/cycles?limit=200")
        output = {
            "mode": "apply" if args.apply else "dry-run",
            "sourceCsv": str(source_csv),
            "sourceResultCounts": dict(source_counts),
            "plannedStepResults": dict(planned_counts),
            "ledgerStepResults": dict(actual_counts),
            "cycles": [
                {
                    "id": cycle["id"],
                    "name": cycle["name"],
                    "status": cycle["status"],
                    "workItemKey": cycle["scope"].get("workItemKey"),
                    "itemCount": cycle["itemCount"],
                    "resourceVersion": cycle["resourceVersion"],
                }
                for cycle in selected
            ],
            "issues": [
                {
                    "id": issue["id"],
                    "key": issue["key"],
                    "summary": issue["summary"],
                    "severity": issue["severity"],
                    "status": issue["status"],
                }
                for issue in issues
            ],
            "overview": overview,
            "reports": reports,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    finally:
        api.close()


if __name__ == "__main__":
    raise SystemExit(main())
