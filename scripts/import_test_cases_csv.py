"""Import a path/item/content CSV into the live test-case library.

The source spreadsheet does not have a header row. A row with a date and a
SOLU key starts a parent work item; following rows contain path, item, and
content in columns C, D, and E. Dry-run is the default. Live application
requires both ``--apply`` and the explicit reset confirmation phrase.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from tc_backend.config import load_settings
from tc_backend.db.workspaces import ensure_default_workspace

RESET_CONFIRMATION = "RESET-DEMO-DATA"
SOURCE_KEY_PATTERN = re.compile(r"^(SOLU)-(\d+)\s*(.*)$")

OPERATIONAL_TABLES = (
    "knowledge_projection_events",
    "knowledge_embeddings",
    "knowledge_evidence",
    "knowledge_edges",
    "knowledge_nodes",
    "knowledge_source_records",
    "slack_parse_results",
    "slack_message_revisions",
    "slack_messages",
    "slack_threads",
    "slack_work_items",
    "slack_events",
    "slack_channel_allowlist",
    "drive_reconciliation_records",
    "drive_usage_snapshots",
    "integration_settings",
    "attachments",
    "comments",
    "issue_links",
    "issue_steps",
    "issues",
    "outbox_events",
    "execution_step_results",
    "execution_attempts",
    "cycle_items",
    "test_cycles",
    "folder_aliases",
    "audit_logs",
    "folders",
    "test_case_version_tags",
    "test_case_tags",
    "test_steps",
    "test_case_versions",
    "test_cases",
    "tags",
    "work_items",
    "workspace_key_counters",
    "background_jobs",
)

TEXT_CORRECTIONS = {
    "세금 정구 정책": "세금 청구 정책",
    "어드민 주문 록록 조회 기간 변경 후 저장 시 정상 반영 확인": (
        "어드민 주문 목록 조회 기간 변경 후 저장 시 정상 반영 확인"
    ),
    "상품 기본 무게 변경 후 저장 시 정상 반영 확인변경 후 저장 시 정상 반영 확인": (
        "상품 기본 무게 변경 후 저장 시 정상 반영 확인"
    ),
    "증정이 장바구니 주문서 주문상세의 증정 옵셥이 서로 동일한지 확인": (
        "장바구니, 주문서, 주문 상세의 증정 옵션이 서로 동일한지 확인"
    ),
}


@dataclass(frozen=True, slots=True)
class StepDraft:
    source_content: str
    precondition: str
    action: str
    expected_result: str


@dataclass(slots=True)
class CaseDraft:
    summary: str
    path: str
    steps: list[StepDraft] = field(default_factory=list)


@dataclass(slots=True)
class WorkItemDraft:
    source_date: str
    source_key: str
    key: str
    summary: str
    domain: str | None = None
    path_label: str | None = None
    cases: OrderedDict[str, CaseDraft] = field(default_factory=OrderedDict)


@dataclass(frozen=True, slots=True)
class ImportPlan:
    work_items: tuple[WorkItemDraft, ...]

    @property
    def domains(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.domain or "" for item in self.work_items))

    @property
    def case_count(self) -> int:
        return sum(len(item.cases) for item in self.work_items)

    @property
    def step_count(self) -> int:
        return sum(len(case.steps) for item in self.work_items for case in item.cases.values())


REACT_STEP_CONTENT: dict[str, tuple[str, str]] = {
    "증정 수량이 구매수량/buy*get으로 맞는지 확인": (
        "N+M 대상 상품의 구매 수량을 buy 조건의 배수와 비배수로 각각 변경한다.",
        "증정 수량이 구매 수량을 기준으로 계산한 floor(구매 수량 / buy) × get 값과 일치한다.",
    ),
    "유료분은 정가 과금, 증정품은 무료인지 확인": (
        "N+M 대상 상품을 증정 조건 이상 담고 장바구니와 주문서의 상품 금액을 확인한다.",
        "유료 수량에는 정상 판매가가 적용되고 증정 수량의 상품 금액은 0원으로 계산된다.",
    ),
    "증정품에 다른 할인이 중복 적용되지 않는지 확인": (
        "다른 할인 대상이기도 한 N+M 상품을 증정 조건 이상 담고 할인 내역을 확인한다.",
        "증정 수량에는 쿠폰·프로모션 등 다른 할인이 중복 적용되지 않는다.",
    ),
    "총 상품 금액에 증정분이 포함돼 표시되되 최종 결제 금액은 유료분만 청구되는지": (
        "N+M 대상 상품을 증정 조건 이상 담고 주문서에서 총 상품 금액과 최종 결제 금액을 확인한다.",
        "총 상품 금액에는 증정 수량이 표시되고 최종 결제 금액에는 유료 수량의 금액만 포함된다.",
    ),
    "같은 상품 여러 옵션을 섞어 담으면 증정이 최저가 옵션에 배정되는지 확인": (
        "가격이 다른 옵션 여러 개를 N+M 증정 조건 이상 장바구니에 함께 담는다.",
        "증정 수량이 장바구니에 담긴 옵션 중 최저가 옵션에 배정된다.",
    ),
    "장바구니, 주문서, 주문 상세의 증정 옵션이 서로 동일한지 확인": (
        "N+M 주문을 생성하면서 장바구니, 주문서, 주문 상세의 증정 옵션 정보를 차례로 확인한다.",
        "장바구니, 주문서, 주문 상세에 표시된 증정 상품·옵션·수량이 모두 일치한다.",
    ),
    "실제 결제가 정상 완료되는지 확인": (
        "N+M 대상 상품으로 주문서를 작성하고 실제 테스트 결제를 완료한다.",
        "결제가 오류 없이 승인되고 주문이 한 번만 정상 생성된다.",
    ),
    "상품상세, 장바구니, 주문서, 주문상세 모두 화면의 금액이 일치하는지 확인": (
        "동일한 N+M 구성으로 상품 상세, 장바구니, 주문서, 주문 상세의 금액을 각각 확인한다.",
        "상품 상세부터 주문 상세까지 유료 금액·할인 금액·최종 금액이 동일한 계산 기준으로 일치한다.",
    ),
    "장바구니 금액과 주문서 금액이 서로 일치하는지 확인": (
        "상품을 장바구니에 담고 주문서로 이동해 상품 금액·할인·배송비·최종 금액을 비교한다.",
        "장바구니와 주문서의 상품 금액·할인·배송비·최종 결제 금액이 서로 일치한다.",
    ),
    "배송비가 무게 기준으로 정상 계산되고 sto앱과 같은 값으로 나오는지 확인": (
        "무게가 설정된 상품의 수량을 변경하고 배송지를 선택해 배송비를 계산한다.",
        "상품 총무게 기준 배송비가 정책대로 계산되며 STO 앱의 동일 조건 결과와 일치한다.",
    ),
    "무료배송 임계 경계가 장바구니와 결제창에서 동일하게 적용되는지 확인": (
        "주문 금액을 무료배송 기준 금액의 직전·동일·초과 값으로 각각 맞춰 장바구니와 결제창을 확인한다.",
        "무료배송 적용 여부가 경계값 정책에 맞고 장바구니와 결제창에서 동일하다.",
    ),
    "배송지 선택 전/후로 배송비가 올바르게 재계산되는지 확인": (
        "주문서에서 배송지를 선택하기 전의 배송비를 확인한 뒤 국가 또는 지역이 다른 배송지를 선택한다.",
        "배송지 선택 전후의 배송비가 각 배송 정책에 맞게 재계산되고 최종 금액에 즉시 반영된다.",
    ),
    "로그인 없이 비회원 구매 진입->배송지이메일 입력->결제 완료까지 정상 진행되는지 확인": (
        "로그아웃 상태에서 상품을 선택하고 비회원 구매로 진입해 배송지·이메일을 입력한 뒤 결제한다.",
        "로그인 요구나 서버 오류 없이 결제가 완료되고 비회원 주문이 생성된다.",
    ),
    "결제 완료 후 주문번호 + 휴대폰번호로 주문조회가 되는지 확인": (
        "비회원 결제를 완료한 뒤 생성된 주문번호와 주문 시 입력한 휴대폰 번호로 주문을 조회한다.",
        "본인 확인이 완료되고 해당 비회원 주문의 상품·금액·배송 정보가 표시된다.",
    ),
    "비회원은 적립금, 쿠폰이 적용되지 않는지 확인": (
        "비회원 주문서에서 적립금과 회원 전용 쿠폰의 노출 및 적용 가능 여부를 확인한다.",
        "적립금과 회원 전용 쿠폰이 노출 또는 적용되지 않고 최종 금액에도 반영되지 않는다.",
    ),
    "비회원 주문서 진입 시 크래시 없이 금액이 정상 표시되는지 확인": (
        "로그아웃 상태에서 상품을 담고 비회원 주문서에 진입한다.",
        "화면이 중단되거나 오류가 발생하지 않으며 상품 금액·할인·배송비·최종 금액이 정상 표시된다.",
    ),
    "배송지 미입력 상태로 결제 시도 시 배송지 입력해달라는 안내 후 결제가 막히는지 확인": (
        "비회원 주문서에서 배송지를 입력하지 않은 채 결제를 시도한다.",
        "배송지 입력 안내가 표시되고 결제 요청이 전송되지 않는다.",
    ),
    "신규 가입 혜택 배너/혜택가가 표시되는 테넌트라면 금액에 반영되는지 확인": (
        "신규 가입 혜택이 설정된 테넌트의 비회원 주문서에서 배너·혜택가와 최종 금액을 확인한다.",
        "혜택 안내와 적용 조건이 정확히 표시되고 비회원 적용 정책에 맞는 금액이 최종 결제 금액에 반영된다.",
    ),
}


def corrected(value: str) -> str:
    normalized = " ".join(value.strip().split())
    return TEXT_CORRECTIONS.get(normalized, normalized)


def parse_plan(path: Path) -> ImportPlan:
    work_items: list[WorkItemDraft] = []
    current: WorkItemDraft | None = None
    with path.open(encoding="utf-8-sig", newline="") as source:
        for row_number, row in enumerate(csv.reader(source), start=1):
            padded = [*row, *("" for _ in range(max(0, 5 - len(row))))]
            source_date, raw_path, raw_item, raw_content = (
                padded[0].strip(),
                padded[2].strip(),
                padded[3].strip(),
                padded[4].strip(),
            )
            header_match = SOURCE_KEY_PATTERN.match(raw_path)
            if source_date and header_match:
                suffix = header_match.group(3).strip()
                current = WorkItemDraft(
                    source_date=source_date,
                    source_key=f"{header_match.group(1)}-{header_match.group(2)}",
                    key=f"WORK-{header_match.group(2)}",
                    summary=suffix or raw_path,
                )
                work_items.append(current)
                continue
            if not raw_path and not raw_item and not raw_content:
                continue
            if current is None:
                raise ValueError(f"row {row_number}: content appears before a work-item header")
            if not raw_path or not raw_item or not raw_content:
                raise ValueError(f"row {row_number}: path, item, and content are all required")
            path_parts = [part.strip() for part in raw_path.split("/") if part.strip()]
            domain = path_parts[0]
            path_label = " / ".join(path_parts[1:]) if len(path_parts) > 1 else None
            if current.domain is not None and current.domain != domain:
                raise ValueError(f"row {row_number}: one work item cannot span multiple domains")
            current.domain = domain
            current.path_label = path_label
            item = corrected(raw_item)
            content = corrected(raw_content)
            case = current.cases.setdefault(item, CaseDraft(summary=item, path=raw_path))
            case.steps.append(build_step(domain, item, content))
    if not work_items:
        raise ValueError("the CSV does not contain a SOLU work-item header")
    for item in work_items:
        if item.domain is None or not item.cases:
            raise ValueError(f"{item.source_key}: no test cases were found")
    return ImportPlan(tuple(work_items))


def build_step(domain: str, item: str, content: str) -> StepDraft:
    if domain == "관리":
        precondition = f"관리자 권한으로 로그인하고 관리 > 마켓 정책 설정 화면의 {item} 영역에 진입한다."
        if content == "설정 값 목록 정상 조회 되는지 확인":
            return StepDraft(
                source_content=content,
                precondition=precondition,
                action="마켓 정책 설정 화면을 열고 설정 값 목록을 조회한다.",
                expected_result="설정 값 목록이 오류 없이 표시되고 각 정책 항목의 현재 값이 노출된다.",
            )
        suffix = " 변경 후 저장 시 정상 반영 확인"
        if not content.endswith(suffix):
            raise ValueError(f"unsupported management assertion: {content}")
        field_name = content[: -len(suffix)].strip()
        return StepDraft(
            source_content=content,
            precondition=precondition,
            action=f'"{field_name}" 설정을 기존 값과 다른 유효한 값으로 변경한 뒤 저장한다.',
            expected_result=(
                f'저장이 정상 완료되고 변경한 "{field_name}" 설정이 즉시 반영되며, '
                "화면을 새로고침해도 값이 유지된다."
            ),
        )
    preconditions = {
        "N+M": "N+M 혜택이 설정된 판매 가능 상품과 결제 가능한 테스트 계정을 준비한다.",
        "결제 금액 계산 + 배송비": (
            "구매 가능한 상품, 무게·무료배송 기준이 설정된 배송 정책, 결제 가능한 테스트 계정을 준비한다."
        ),
        "비회원 구매 결제": (
            "비회원 구매가 허용된 테넌트, 판매 가능한 상품, 사용 가능한 결제수단을 준비한다."
        ),
    }
    try:
        action, expected = REACT_STEP_CONTENT[content]
        precondition = preconditions[item]
    except KeyError as error:
        raise ValueError(f"unsupported React test assertion: {item} / {content}") from error
    return StepDraft(content, precondition, action, expected)


def reset_operational_data() -> None:
    engine = create_engine(load_settings().sqlalchemy_database_url, pool_pre_ping=True)
    table_list = ", ".join(OPERATIONAL_TABLES)
    try:
        with Session(engine) as session, session.begin():
            session.execute(text(f"TRUNCATE TABLE {table_list} CASCADE"))
            ensure_default_workspace(session)
    finally:
        engine.dispose()


def post(client: httpx.Client, path: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
    response = client.post(path, json=payload, headers={"Idempotency-Key": key})
    response.raise_for_status()
    return response.json()


def assert_no_active_attachments(client: httpx.Client) -> None:
    response = client.get("/api/attachments", params={"limit": 1})
    response.raise_for_status()
    active_count = int(response.json()["total"])
    if active_count:
        raise RuntimeError(
            "reset refused: active attachments must be backed up and deleted through the API first"
        )


def apply_plan(plan: ImportPlan, api_base: str) -> None:
    with httpx.Client(base_url=api_base.rstrip("/"), timeout=30.0) as client:
        assert_no_active_attachments(client)
        reset_operational_data()
        domain_ids: dict[str, str] = {}
        for sort_order, domain in enumerate(plan.domains):
            response = post(
                client,
                "/api/folders",
                {"parentId": None, "name": domain, "sortOrder": sort_order * 100},
                f"csv-import-folder-{sort_order + 1}",
            )
            domain_ids[domain] = response["rootFolderId"]
        for item_index, item in enumerate(plan.work_items, start=1):
            labels = [label for label in (item.path_label, item.source_key) if label]
            work_item = post(
                client,
                "/api/work-items",
                {
                    "key": item.key,
                    "categoryId": domain_ids[item.domain or ""],
                    "summary": item.summary,
                    "description": (
                        f"{item.source_date} 작성된 {item.source_key}의 테스트 항목을 CSV에서 이관했습니다. "
                        "원본의 과거 실행 상태와 실제 결과는 라이브러리 케이스에 포함하지 않았습니다."
                    ),
                    "labels": labels,
                },
                f"csv-import-work-item-{item_index}",
            )
            for case_index, case in enumerate(item.cases.values(), start=1):
                steps = [
                    {
                        "sequence": sequence,
                        "precondition": step.precondition,
                        "action": step.action,
                        "expectedResult": step.expected_result,
                    }
                    for sequence, step in enumerate(case.steps, start=1)
                ]
                post(
                    client,
                    "/api/test-cases",
                    {
                        "workItemId": work_item["id"],
                        "summary": case.summary,
                        "precondition": case.steps[0].precondition,
                        "expectedResultMode": "PER_STEP",
                        "priority": "MEDIUM",
                        "labels": labels,
                        "steps": steps,
                        "source": {"type": "UI", "actorLabel": "CSV 가져오기"},
                        "status": "ACTIVE",
                    },
                    f"csv-import-case-{item_index}-{case_index}",
                )


def print_plan(plan: ImportPlan) -> None:
    print(
        f"domains={len(plan.domains)} work_items={len(plan.work_items)} "
        f"test_cases={plan.case_count} steps={plan.step_count}"
    )
    for item in plan.work_items:
        print(f"- {item.domain} / {item.key} {item.summary} ({len(item.cases)} cases)")
        for case in item.cases.values():
            print(f"  - {case.summary}: {len(case.steps)} steps")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("csv_path", type=Path)
    result.add_argument("--api-base", default="http://127.0.0.1:8000")
    result.add_argument("--apply", action="store_true")
    result.add_argument("--confirm", default="")
    return result


def main() -> None:
    args = parser().parse_args()
    plan = parse_plan(args.csv_path.expanduser().resolve())
    print_plan(plan)
    if not args.apply:
        print("dry-run only; pass --apply --confirm RESET-DEMO-DATA to replace live demo data")
        return
    if args.confirm != RESET_CONFIRMATION:
        raise SystemExit(f"--apply requires --confirm {RESET_CONFIRMATION}")
    apply_plan(plan, args.api_base)
    print("import completed")


if __name__ == "__main__":
    main()
