from __future__ import annotations

import json
from pathlib import Path

import pytest

from tc_backend.knowledge import (
    HistoricalCorpusInputError,
    HistoricalCorpusStructureError,
    normalize_historical_csv,
    normalize_path,
    preview_jsonl,
)

FIXTURE = Path(__file__).parent / "fixtures" / "historical_qa_synthetic.csv"


def test_semantic_layout_changes_multiline_and_platform_results_are_preserved() -> None:
    preview = normalize_historical_csv(FIXTURE)

    assert [record.source_row_number for record in preview.records] == [
        2,
        3,
        6,
        8,
        12,
        13,
        14,
        15,
    ]
    first, _, reordered, two_column = preview.records[:4]
    assert first.normalized_path == "결제 / 쿠폰"
    assert first.normalized_content == (
        "쿠폰을 선택하고\n주문 번호: [IDENTIFIER] 표시되는지 확인"
    )
    assert first.historical_results == {
        "AOS": "FAIL",
        "IOS": "UNAVAILABLE",
        "WEB": "PASS",
    }
    assert reordered.normalized_path == "상품 / 상세"
    assert reordered.normalized_item == "상품 정보"
    assert reordered.historical_results == {"IOS": "PASS"}
    assert reordered.risk_metadata["labels"] == {"RISK": "High"}
    assert two_column.normalized_content == two_column.normalized_item
    assert two_column.risk_metadata["content_inferred_from_item"] is True


def test_pii_jira_dates_and_identifiers_never_enter_generation_context(tmp_path: Path) -> None:
    path = tmp_path / "private.csv"
    path.write_text(
        "경로,항목,내용,WEB,비고,작성자,날짜\n"
        '"주문 > 조회","고객 주문","담당자: 홍길동이 qa.person@example.com 계정으로 '
        '2026-08-26 주문번호 ORDER-1234567890 조회","Fail",'
        '"https://jira.example.invalid/browse/SHOP-77",김하나,2026-08-26\n',
        encoding="utf-8",
    )

    preview = normalize_historical_csv(path)
    rendered = preview_jsonl(preview)
    context = preview.records[0].generation_context

    for secret in (
        "홍길동",
        "qa.person@example.com",
        "2026-08-26",
        "ORDER-1234567890",
        "SHOP-77",
        "김하나",
    ):
        assert secret not in rendered
        assert secret not in json.dumps(context, ensure_ascii=False)
    assert context["content"] == (
        "담당자: [PERSON]이 [EMAIL] 계정으로 [DATE] 주문번호: [IDENTIFIER] 조회"
    )
    assert preview.records[0].risk_metadata["redaction_categories"] == (
        "date",
        "email",
        "identifier",
        "jira",
        "person",
    )


def test_value_pattern_fallback_sanitizes_headerless_rows(tmp_path: Path) -> None:
    path = tmp_path / "headerless.csv"
    path.write_text(
        '"회원 > 프로필","담당자: 홍길동이 user@example.com으로 변경되는지 확인",Pass\n',
        encoding="utf-8",
    )

    record = normalize_historical_csv(path).records[0]

    assert record.normalized_path == "회원 / 프로필"
    assert record.normalized_item == record.normalized_content
    assert record.normalized_content == "담당자: [PERSON]이 [EMAIL]으로 변경되는지 확인"
    assert "홍길동" not in preview_jsonl(normalize_historical_csv(path))


def test_exact_duplicates_and_stats_are_deterministic() -> None:
    first = normalize_historical_csv(FIXTURE)
    second = normalize_historical_csv(FIXTURE)

    assert preview_jsonl(first) == preview_jsonl(second)
    assert first.stats.record_count == 8
    assert first.stats.unique_record_count == 7
    assert first.stats.exact_duplicate_count == 1
    assert first.stats.exact_duplicate_group_count == 1
    assert first.records[1].is_exact_duplicate is True
    assert first.records[1].duplicate_of_source_row == 2
    assert first.records[1].exact_duplicate_key == first.records[0].exact_duplicate_key
    assert first.stats.as_dict()["near_duplicate_handling"] == "deferred_to_retrieval"
    assert first.stats.as_dict()["database_writes"] == 0


def test_unmapped_single_cell_descriptions_are_private_section_context() -> None:
    preview = normalize_historical_csv(FIXTURE)
    rendered = preview_jsonl(preview)

    assert preview.stats.record_count == 8
    assert preview.stats.section_row_count == 4
    for context_only_value in (
        "POS-900",
        "복합 결제 시나리오",
        "홍길동",
        "qa-context@example.com",
        "카드/현금 결제 조합",
        "CUSTOMER-CONTEXT-7777",
        "synthetic-context@example.com",
        "DEVICE-CONTEXT-9000",
    ):
        assert context_only_value not in rendered


def test_result_bearing_workflow_rows_recover_section_specific_layouts() -> None:
    preview = normalize_historical_csv(FIXTURE)
    records = {record.source_row_number: record for record in preview.records}

    assert records[12].normalized_path == "판매 / 결제"
    assert records[12].normalized_content == "결제화면"
    assert records[12].historical_results == {"UNSPECIFIED": "PASS"}
    assert records[12].risk_metadata["content_inferred_from_item"] is True
    assert records[13].normalized_content == "오류 메시지가 표시되는지 확인"
    assert records[13].historical_results == {"UNSPECIFIED": "FAIL"}
    assert records[14].normalized_path == "설정 / 화면 설정"
    assert records[14].normalized_item == "화면설정"
    assert records[14].historical_results == {"UNSPECIFIED": "UNAVAILABLE"}
    assert records[15].historical_results == {"UNSPECIFIED": "BLOCKED"}
    assert preview.stats.result_bearing_candidate_count == 8
    assert preview.stats.result_bearing_record_count == 8
    assert preview.stats.result_bearing_omissions_by_reason == {}
    assert all("담당" not in value for record in records.values() for value in record.generation_context.values())


def test_result_bearing_summary_reports_omission_reason(tmp_path: Path) -> None:
    path = tmp_path / "result-omission.csv"
    path.write_text(
        "경로,항목,결과\n"
        '"회원 > 로그인",,Pass\n'
        '"회원 > 로그인","로그인 확인",Pass\n',
        encoding="utf-8",
    )

    stats = normalize_historical_csv(path).stats

    assert stats.result_bearing_candidate_count == 2
    assert stats.result_bearing_record_count == 1
    assert stats.result_bearing_omissions_by_reason == {"missing_item_and_content": 1}


def test_nfkc_path_and_whitespace_normalization() -> None:
    assert normalize_path("  결제　＞  주문 \\ 완료  ") == "결제 / 주문 / 완료"


def test_rejects_invalid_utf8_malformed_csv_and_missing_file(tmp_path: Path) -> None:
    invalid_utf8 = tmp_path / "invalid.csv"
    invalid_utf8.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(HistoricalCorpusInputError, match="valid UTF-8"):
        normalize_historical_csv(invalid_utf8)

    malformed = tmp_path / "malformed.csv"
    malformed.write_text('경로,항목,내용\n"결제,쿠폰,닫히지 않음', encoding="utf-8")
    with pytest.raises(HistoricalCorpusInputError, match="malformed CSV"):
        normalize_historical_csv(malformed)

    with pytest.raises(HistoricalCorpusInputError, match="not a file"):
        normalize_historical_csv(tmp_path / "missing.csv")


def test_rejects_qa_rows_without_a_recoverable_path(tmp_path: Path) -> None:
    path = tmp_path / "missing-path.csv"
    path.write_text(
        "항목,내용,결과\n쿠폰,쿠폰이 정상 적용되는지 확인,Pass\n",
        encoding="utf-8",
    )

    with pytest.raises(HistoricalCorpusStructureError, match="missing semantic field.*path"):
        normalize_historical_csv(path)


def test_jsonl_contains_records_then_machine_readable_summary() -> None:
    lines = preview_jsonl(normalize_historical_csv(FIXTURE)).splitlines()
    payloads = [json.loads(line) for line in lines]

    assert all(payload["record_type"] == "historical_qa" for payload in payloads[:-1])
    assert payloads[-1]["record_type"] == "summary"
    assert payloads[-1]["schema_version"] == "historical-qa-preview-v1"
    assert payloads[-1]["exact_duplicate_count"] == 1
    assert payloads[0]["source_file_fingerprint"].startswith("sha256:")
    assert payloads[0]["source_row_hash"].startswith("sha256:")
