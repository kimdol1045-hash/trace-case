"""Deterministic, privacy-safe preview of historical QA CSV corpora.

The historical files are retrieval inputs, not current product truth.  This
module therefore stops at a JSONL preview: it has no repository or database
dependency and deliberately exposes no apply/write mode.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

PREVIEW_SCHEMA_VERSION = "historical-qa-preview-v1"


class HistoricalCorpusError(ValueError):
    """Base class for historical-corpus preview errors."""


class HistoricalCorpusInputError(HistoricalCorpusError):
    """The input cannot be read as a safe UTF-8 CSV file."""


class HistoricalCorpusStructureError(HistoricalCorpusError):
    """No usable semantic QA records can be recovered from the CSV."""


@dataclass(frozen=True, slots=True)
class HistoricalCorpusRecord:
    source_file_fingerprint: str
    source_row_hash: str
    source_row_number: int
    normalized_path: str
    normalized_item: str
    normalized_content: str
    historical_results: dict[str, str]
    risk_metadata: dict[str, object]
    generation_context: dict[str, str]
    exact_duplicate_key: str
    is_exact_duplicate: bool
    duplicate_of_source_row: int | None

    def as_dict(self) -> dict[str, object]:
        return {
            "record_type": "historical_qa",
            "schema_version": PREVIEW_SCHEMA_VERSION,
            "source_file_fingerprint": self.source_file_fingerprint,
            "source_row_hash": self.source_row_hash,
            "source_row_number": self.source_row_number,
            "normalized_path": self.normalized_path,
            "normalized_item": self.normalized_item,
            "normalized_content": self.normalized_content,
            "historical_results": self.historical_results,
            "risk_metadata": self.risk_metadata,
            "generation_context": self.generation_context,
            "exact_duplicate_key": self.exact_duplicate_key,
            "is_exact_duplicate": self.is_exact_duplicate,
            "duplicate_of_source_row": self.duplicate_of_source_row,
        }


@dataclass(frozen=True, slots=True)
class HistoricalCorpusStats:
    source_file_fingerprint: str
    logical_row_count: int
    record_count: int
    unique_record_count: int
    exact_duplicate_count: int
    exact_duplicate_group_count: int
    header_row_count: int
    section_row_count: int
    ignored_row_count: int
    result_bearing_candidate_count: int
    result_bearing_record_count: int
    result_bearing_omissions_by_reason: dict[str, int]
    redactions_by_category: dict[str, int]

    def as_dict(self) -> dict[str, object]:
        return {
            "record_type": "summary",
            "schema_version": PREVIEW_SCHEMA_VERSION,
            "source_file_fingerprint": self.source_file_fingerprint,
            "logical_row_count": self.logical_row_count,
            "record_count": self.record_count,
            "unique_record_count": self.unique_record_count,
            "exact_duplicate_count": self.exact_duplicate_count,
            "exact_duplicate_group_count": self.exact_duplicate_group_count,
            "header_row_count": self.header_row_count,
            "section_row_count": self.section_row_count,
            "ignored_row_count": self.ignored_row_count,
            "result_bearing_candidate_count": self.result_bearing_candidate_count,
            "result_bearing_record_count": self.result_bearing_record_count,
            "result_bearing_omissions_by_reason": self.result_bearing_omissions_by_reason,
            "redactions_by_category": self.redactions_by_category,
            "near_duplicate_handling": "deferred_to_retrieval",
            "database_writes": 0,
        }


@dataclass(frozen=True, slots=True)
class HistoricalCorpusPreview:
    records: tuple[HistoricalCorpusRecord, ...]
    stats: HistoricalCorpusStats


_Role = Literal["path", "item", "content", "result", "notes", "risk", "sensitive"]


@dataclass(frozen=True, slots=True)
class _HeaderField:
    role: _Role
    key: str


@dataclass(frozen=True, slots=True)
class _HeaderLayout:
    fields: dict[int, _HeaderField]


@dataclass(frozen=True, slots=True)
class _ResultRowLayout:
    path_index: int
    item_index: int
    content_indices: tuple[int, ...]


@dataclass(slots=True)
class _ExtractedFields:
    path_parts: list[str] = field(default_factory=list)
    item_values: list[str] = field(default_factory=list)
    content_values: list[str] = field(default_factory=list)
    results: dict[str, str] = field(default_factory=dict)
    risk_labels: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    redaction_categories: set[str] = field(default_factory=set)
    consumed: set[int] = field(default_factory=set)
    explicit_content: bool = False


_PATH_ALIASES = {
    "path",
    "testpath",
    "menupath",
    "screenpath",
    "featurepath",
    "menu",
    "screen",
    "category",
    "section",
    "경로",
    "테스트경로",
    "메뉴경로",
    "화면경로",
    "기능경로",
    "메뉴",
    "화면",
    "카테고리",
    "섹션",
    "구간",
}
_ITEM_ALIASES = {
    "item",
    "testitem",
    "case",
    "testcase",
    "title",
    "feature",
    "항목",
    "테스트항목",
    "검증항목",
    "케이스",
    "테스트케이스",
    "제목",
    "기능",
    "기능명",
}
_CONTENT_ALIASES = {
    "content",
    "testcontent",
    "description",
    "detail",
    "details",
    "scenario",
    "assertion",
    "check",
    "expected",
    "expectedresult",
    "내용",
    "테스트내용",
    "검증내용",
    "확인내용",
    "상세",
    "상세내용",
    "시나리오",
    "기대결과",
    "체크내용",
}
_NOTES_ALIASES = {
    "note",
    "notes",
    "remark",
    "remarks",
    "comment",
    "comments",
    "memo",
    "비고",
    "참고",
    "메모",
    "코멘트",
    "결함",
}
_RISK_ALIASES = {
    "risk",
    "risklevel",
    "severity",
    "priority",
    "impact",
    "리스크",
    "위험",
    "위험도",
    "심각도",
    "중요도",
    "우선순위",
    "영향도",
}
_SENSITIVE_ALIASES = {
    "jira",
    "jirakey",
    "jiraurl",
    "ticket",
    "ticketkey",
    "issue",
    "issuekey",
    "author",
    "assignee",
    "reporter",
    "owner",
    "tester",
    "qa",
    "developer",
    "name",
    "date",
    "orderid",
    "customerid",
    "memberid",
    "작성자",
    "담당자",
    "검수자",
    "테스터",
    "개발자",
    "이름",
    "일자",
    "날짜",
    "주문번호",
    "주문id",
    "고객번호",
    "고객id",
    "회원번호",
    "회원id",
}
_RESULT_ALIASES = {
    "result",
    "status",
    "testresult",
    "qaresult",
    "executionresult",
    "결과",
    "상태",
    "테스트결과",
    "qa결과",
    "실행결과",
}

_PATH_SPLIT_RE = re.compile(r"\s*(?:-\s*>|=>|/|\\|>|→|›|»|::)+\s*")
_PATH_PREFIX_RE = re.compile(
    r"^(?:path|menu|screen|section|경로|메뉴|화면|섹션|구간)\s*[:=：]\s*",
    re.IGNORECASE,
)
_LABELED_CELL_RE = re.compile(r"^([^:=：]{1,32})\s*[:=：]\s*(.*)$", re.DOTALL)
_CONTENT_SIGNAL_RE = re.compile(
    r"확인|검증|테스트|노출|표시|입력|저장|등록|조회|변경|동작|완료|성공|실패|"
    r"given\b|when\b|then\b|verify\b|check\b|should\b|must\b",
    re.IGNORECASE,
)
_CONTEXT_METADATA_RE = re.compile(
    r"^(?:테스트\s*환경|결제\s*단말기|test\s*environment|device)\s*[:=：]",
    re.IGNORECASE,
)
_RESULT_TOKEN_RE = re.compile(
    r"^(?:pass(?:ed)?|fail(?:ed)?|ok|ng|error|n/?a|not[ _-]?run|unexecuted|"
    r"unavailable|cannot[ _-]?verify|blocked|skip(?:ped)?|통과|성공|실패|오류|"
    r"해당없음|미실행|확인불가|확인불가능|보류|차단)$",
    re.IGNORECASE,
)
_WORKFLOW_STATUS_TOKENS = {"qa완료", "qa중", "qa대기", "개발완료", "시작전"}
_RISK_TOKEN_RE = re.compile(
    r"^(?:critical|blocker|high|medium|low|major|minor|trivial|p[0-4]|s[0-4]|"
    r"긴급|높음|중간|낮음|상|중|하)$",
    re.IGNORECASE,
)

_JIRA_URL_RE = re.compile(
    r"https?://[^\s,;]+/(?:browse|issues?)/[A-Z][A-Z0-9]{1,15}-\d+(?:[^\s,;]*)?",
    re.IGNORECASE,
)
_JIRA_KEY_RE = re.compile(r"(?<![\w])(?:[A-Z][A-Z0-9]{1,15}-\d+)(?![\w-])")
_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9_.+-])[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    r"(?![A-Za-z0-9_.-])"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?82[- .]?)?(?:0?1[016789]|0\d{1,2})[- .]?\d{3,4}[- .]?\d{4}(?!\d)"
)
_IP_RE = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")
_UUID_RE = re.compile(
    r"(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}(?![0-9a-f])",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://[^\s,;]+", re.IGNORECASE)
_DATE_RE = re.compile(
    r"(?<!\d)(?:19|20)\d{2}(?:[./-]\d{1,2}[./-]\d{1,2}|년\s*\d{1,2}월\s*\d{1,2}일)(?!\d)"
)
_LABELED_PERSON_RE = re.compile(
    r"(?P<label>담당자|작성자|검수자|테스터|개발자|이름|assignee|reporter|author|tester|owner|name)"
    r"\s*[:=：]?\s*(?:[가-힣]{2,4}?|[A-Za-z][A-Za-z.'-]+(?:\s+[A-Za-z][A-Za-z.'-]+){0,2})"
    r"(?P<particle>[이가은는을를와과])?(?=\s|[,;.]|$)",
    re.IGNORECASE,
)
_LABELED_ID_RE = re.compile(
    r"(?P<label>주문(?:번호|\s*id)|고객(?:번호|\s*id)|회원(?:번호|\s*id)|"
    r"사용자(?:번호|\s*id)|계정(?:번호|\s*id)|order\s*id|customer\s*id|member\s*id|user\s*id)"
    r"\s*[:=#：]?\s*[A-Za-z0-9_-]{4,}",
    re.IGNORECASE,
)
_LONG_IDENTIFIER_RE = re.compile(r"(?<![\w])(?:\d{10,}|[A-Fa-f0-9]{20,})(?![\w])")


def normalize_historical_csv(path: Path) -> HistoricalCorpusPreview:
    """Read *path* and return a deterministic, write-free corpus preview."""

    raw = _read_source(path)
    fingerprint = _sha256(raw)
    rows = _read_csv_rows(raw)
    if not rows:
        raise HistoricalCorpusStructureError("CSV is empty")

    layout: _HeaderLayout | None = None
    result_row_layouts: dict[int, _ResultRowLayout] = {}
    in_section_preamble = False
    current_path = ""
    current_item = ""
    raw_records: list[tuple[int, list[str], str, str, str, _ExtractedFields, bool]] = []
    header_rows = 0
    section_rows = 0
    ignored_rows = 0
    result_bearing_candidates = 0
    result_bearing_records = 0
    result_bearing_omissions: Counter[str] = Counter()

    for row_number, row in rows:
        if not any(cell.strip() for cell in row):
            ignored_rows += 1
            continue
        result_cells = _execution_result_cells(row)
        detected_layout = _detect_header(row)
        if detected_layout is not None:
            layout = detected_layout
            in_section_preamble = False
            header_rows += 1
            continue
        if _is_issue_section_row(row, result_cells):
            result_row_layouts.clear()
            in_section_preamble = True
            current_path = ""
            current_item = ""
            section_rows += 1
            continue
        if in_section_preamble and not result_cells:
            section_rows += 1
            continue
        if _is_context_metadata_row(row, result_cells):
            section_rows += 1
            continue
        result_bearing = bool(result_cells)
        if result_bearing:
            in_section_preamble = False
            result_bearing_candidates += 1
        if _is_single_cell_context_row(row, layout):
            section_rows += 1
            continue

        result_row_layout = None
        if result_cells:
            result_column = result_cells[0][0]
            result_row_layout = _resolve_result_row_layout(
                row,
                result_column=result_column,
                known=result_row_layouts.get(result_column),
            )
            if result_row_layout is not None:
                result_row_layouts[result_column] = result_row_layout
        if result_row_layout is None:
            extracted = _extract_fields(row, layout)
        else:
            extracted = _extract_result_row_fields(
                row,
                header_layout=layout,
                result_layout=result_row_layout,
                result_cells=result_cells,
            )
        path_value = normalize_path(" / ".join(extracted.path_parts)) if extracted.path_parts else ""
        item_value = _sanitize_join(extracted.item_values, extracted.redaction_categories)
        content_value = _sanitize_join(extracted.content_values, extracted.redaction_categories)

        if path_value:
            current_path = path_value
        if item_value and not content_value and not _looks_like_content(item_value):
            current_item = item_value

        inferred_content = False
        if not content_value and item_value and (result_bearing or _looks_like_content(item_value)):
            content_value = item_value
            inferred_content = True
        if content_value and not item_value:
            item_value = current_item or _first_content_line(content_value)
        if item_value:
            current_item = item_value

        resolved_path = path_value or current_path
        resolved_item = item_value or current_item
        if content_value and resolved_path and resolved_item:
            if result_bearing:
                result_bearing_records += 1
            raw_records.append(
                (
                    row_number,
                    row,
                    resolved_path,
                    resolved_item,
                    content_value,
                    extracted,
                    inferred_content,
                )
            )
            continue
        if path_value or (item_value and not content_value):
            if result_bearing:
                result_bearing_omissions[
                    _missing_semantics_reason(resolved_path, resolved_item, content_value)
                ] += 1
            section_rows += 1
            continue
        if _row_has_qa_signal(row, extracted):
            missing = ", ".join(
                name
                for name, value in (
                    ("path", resolved_path),
                    ("item", resolved_item),
                    ("content", content_value),
                )
                if not value
            )
            raise HistoricalCorpusStructureError(
                f"logical row {row_number}: QA-like row is missing semantic field(s): {missing}"
            )
        if result_bearing:
            result_bearing_omissions["unclassified"] += 1
        ignored_rows += 1

    if not raw_records:
        raise HistoricalCorpusStructureError(
            "CSV contains no rows with recoverable path, item, and content semantics"
        )

    duplicate_first_rows: dict[str, int] = {}
    duplicate_counts: Counter[str] = Counter()
    redaction_counts: Counter[str] = Counter()
    records: list[HistoricalCorpusRecord] = []
    for row_number, row, path_value, item_value, content_value, extracted, inferred in raw_records:
        results = dict(sorted(extracted.results.items()))
        signals = _risk_signals(results, extracted.risk_labels)
        risk_metadata: dict[str, object] = {
            "labels": dict(sorted(extracted.risk_labels.items())),
            "notes": tuple(dict.fromkeys(extracted.notes)),
            "signals": signals,
            "redaction_categories": tuple(sorted(extracted.redaction_categories)),
            "content_inferred_from_item": inferred,
        }
        duplicate_key = _duplicate_key(path_value, item_value, content_value)
        duplicate_of = duplicate_first_rows.get(duplicate_key)
        if duplicate_of is None:
            duplicate_first_rows[duplicate_key] = row_number
        duplicate_counts[duplicate_key] += 1
        redaction_counts.update(extracted.redaction_categories)
        context = {"path": path_value, "item": item_value, "content": content_value}
        records.append(
            HistoricalCorpusRecord(
                source_file_fingerprint=fingerprint,
                source_row_hash=_source_row_hash(row),
                source_row_number=row_number,
                normalized_path=path_value,
                normalized_item=item_value,
                normalized_content=content_value,
                historical_results=results,
                risk_metadata=risk_metadata,
                generation_context=context,
                exact_duplicate_key=duplicate_key,
                is_exact_duplicate=duplicate_of is not None,
                duplicate_of_source_row=duplicate_of,
            )
        )

    duplicate_groups = sum(1 for count in duplicate_counts.values() if count > 1)
    exact_duplicates = sum(count - 1 for count in duplicate_counts.values() if count > 1)
    stats = HistoricalCorpusStats(
        source_file_fingerprint=fingerprint,
        logical_row_count=len(rows),
        record_count=len(records),
        unique_record_count=len(duplicate_counts),
        exact_duplicate_count=exact_duplicates,
        exact_duplicate_group_count=duplicate_groups,
        header_row_count=header_rows,
        section_row_count=section_rows,
        ignored_row_count=ignored_rows,
        result_bearing_candidate_count=result_bearing_candidates,
        result_bearing_record_count=result_bearing_records,
        result_bearing_omissions_by_reason=dict(sorted(result_bearing_omissions.items())),
        redactions_by_category=dict(sorted(redaction_counts.items())),
    )
    return HistoricalCorpusPreview(records=tuple(records), stats=stats)


def preview_jsonl(preview: HistoricalCorpusPreview) -> str:
    """Serialize records followed by one summary line with stable JSON ordering."""

    payloads = [record.as_dict() for record in preview.records]
    payloads.append(preview.stats.as_dict())
    return "\n".join(_stable_json(payload) for payload in payloads) + "\n"


def normalize_path(value: str) -> str:
    """Normalize Unicode, whitespace, and common breadcrumb separators."""

    normalized = _normalize_text(value)
    normalized = _PATH_PREFIX_RE.sub("", normalized)
    parts = [_normalize_inline(part).strip("-–— ") for part in _PATH_SPLIT_RE.split(normalized)]
    return " / ".join(part for part in parts if part)


def _read_source(path: Path) -> bytes:
    try:
        if not path.is_file():
            raise HistoricalCorpusInputError(f"CSV path is not a file: {path}")
        raw = path.read_bytes()
    except HistoricalCorpusInputError:
        raise
    except OSError as exc:
        raise HistoricalCorpusInputError(f"cannot read CSV: {exc}") from exc
    if not raw:
        raise HistoricalCorpusStructureError("CSV is empty")
    return raw


def _read_csv_rows(raw: bytes) -> list[tuple[int, list[str]]]:
    try:
        decoded = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HistoricalCorpusInputError("CSV must be valid UTF-8 (optional BOM is supported)") from exc
    if "\x00" in decoded:
        raise HistoricalCorpusInputError("CSV contains a NUL byte")
    reader = csv.reader(io.StringIO(decoded, newline=""), strict=True)
    try:
        return [(logical_row, list(row)) for logical_row, row in enumerate(reader, start=1)]
    except csv.Error as exc:
        raise HistoricalCorpusInputError(
            f"malformed CSV near physical line {reader.line_num}: {exc}"
        ) from exc


def _detect_header(row: list[str]) -> _HeaderLayout | None:
    nonempty = [(index, cell) for index, cell in enumerate(row) if cell.strip()]
    if len(nonempty) < 2:
        return None
    fields: dict[int, _HeaderField] = {}
    primary_roles: set[_Role] = set()
    for index, cell in nonempty:
        classified = _classify_header(cell)
        if classified is None:
            continue
        fields[index] = classified
        if classified.role in {"path", "item", "content"}:
            primary_roles.add(classified.role)
    if len(fields) / len(nonempty) < 0.6:
        return None
    if len(primary_roles) < 2 and not (primary_roles and len(fields) >= 3):
        return None
    return _HeaderLayout(fields)


def _classify_header(value: str) -> _HeaderField | None:
    label = _header_token(value)
    if not label:
        return None
    if label in _PATH_ALIASES or re.fullmatch(r"(?:depth|뎁스|단계)[1-9]", label):
        return _HeaderField("path", label)
    if label in _ITEM_ALIASES:
        return _HeaderField("item", label)
    if label in _CONTENT_ALIASES:
        return _HeaderField("content", label)
    if label in _NOTES_ALIASES:
        return _HeaderField("notes", "notes")
    if label in _RISK_ALIASES:
        return _HeaderField("risk", _risk_key(label))
    if label in _SENSITIVE_ALIASES or _looks_like_sensitive_header(label):
        return _HeaderField("sensitive", _sensitive_category(label))
    platform = _platform_from_header(label)
    if platform is not None:
        return _HeaderField("result", platform)
    if label in _RESULT_ALIASES or any(alias in label for alias in _RESULT_ALIASES):
        return _HeaderField("result", "UNSPECIFIED")
    return None


def _extract_fields(row: list[str], layout: _HeaderLayout | None) -> _ExtractedFields:
    extracted = _ExtractedFields()
    if layout is not None:
        for index, header in layout.fields.items():
            extracted.consumed.add(index)
            if index >= len(row) or not row[index].strip():
                continue
            _add_field(extracted, header, row[index])

    index = 0
    while index < len(row):
        if index in extracted.consumed or not row[index].strip():
            index += 1
            continue
        labeled = _parse_labeled_cell(row[index])
        if labeled is not None:
            header, value = labeled
            extracted.consumed.add(index)
            if value:
                if header.role == "sensitive" and _looks_like_content(value):
                    sanitized, categories = _sanitize(row[index])
                    extracted.redaction_categories.update(categories)
                    if sanitized:
                        extracted.content_values.append(sanitized)
                        extracted.explicit_content = True
                else:
                    _add_field(extracted, header, value)
            index += 1
            continue
        adjacent_header = _classify_header(row[index])
        if adjacent_header is not None and index + 1 < len(row) and row[index + 1].strip():
            extracted.consumed.update((index, index + 1))
            _add_field(extracted, adjacent_header, row[index + 1])
            index += 2
            continue
        index += 1

    remaining: list[tuple[int, str, bool]] = []
    for index, raw_value in enumerate(row):
        if index in extracted.consumed or not _normalize_text(raw_value):
            continue
        value, categories = _sanitize(raw_value)
        extracted.redaction_categories.update(categories)
        if value:
            remaining.append((index, value, _only_sensitive(raw_value)))

    for index, value, _ in remaining:
        if _looks_like_path(value) and not extracted.path_parts:
            extracted.path_parts.append(value)
            extracted.consumed.add(index)
            continue
        if _RESULT_TOKEN_RE.fullmatch(_normalize_inline(value)):
            _add_result(extracted.results, "UNSPECIFIED", _normalize_result(value))
            extracted.consumed.add(index)
            continue
        if _RISK_TOKEN_RE.fullmatch(_normalize_inline(value)):
            _add_unique(extracted.risk_labels, "RISK", _normalize_inline(value).upper())
            extracted.consumed.add(index)

    remaining_values = [
        value
        for index, value, sensitive_only in remaining
        if index not in extracted.consumed and not sensitive_only
    ]
    if not extracted.content_values:
        content_candidates = [value for value in remaining_values if _looks_like_content(value)]
        if content_candidates:
            content = max(content_candidates, key=lambda value: (len(value), value))
            extracted.content_values.append(content)
            extracted.explicit_content = True
            remaining_values.remove(content)
    if not extracted.item_values and remaining_values:
        extracted.item_values.append(remaining_values[0])
    return extracted


def _execution_result_cells(row: list[str]) -> tuple[tuple[int, str], ...]:
    return tuple(
        (index, _normalize_result(value))
        for index, value in enumerate(row)
        if value.strip() and _RESULT_TOKEN_RE.fullmatch(_normalize_inline(value))
    )


def _resolve_result_row_layout(
    row: list[str],
    *,
    result_column: int,
    known: _ResultRowLayout | None,
) -> _ResultRowLayout | None:
    workflow_status_indices = [
        index
        for index in range(min(result_column, len(row)))
        if _is_workflow_status(row[index])
    ]
    if not workflow_status_indices:
        return known

    # These historical tracking exports place three owner columns and one
    # workflow-status column after the semantic fields.  Platform-specific
    # result columns may be separated from that status by blank columns.  The
    # QA semantics vary by section: path/item, path/item/content, or the same
    # three-column shape shifted right by a section-label column.
    semantic_limit = workflow_status_indices[-1] - 3
    semantic_indices = tuple(
        index
        for index in range(max(semantic_limit, 0))
        if index < len(row) and row[index].strip()
    )
    if len(semantic_indices) >= 3:
        return _ResultRowLayout(
            path_index=semantic_indices[0],
            item_index=semantic_indices[1],
            content_indices=semantic_indices[2:],
        )
    if known is not None:
        known_indices = {known.path_index, known.item_index, *known.content_indices}
        if semantic_indices and set(semantic_indices).issubset(known_indices):
            return known
    if len(semantic_indices) == 2:
        return _ResultRowLayout(
            path_index=semantic_indices[0],
            item_index=semantic_indices[1],
            content_indices=(),
        )
    return known


def _extract_result_row_fields(
    row: list[str],
    *,
    header_layout: _HeaderLayout | None,
    result_layout: _ResultRowLayout,
    result_cells: tuple[tuple[int, str], ...],
) -> _ExtractedFields:
    extracted = _ExtractedFields()
    semantic_fields = (
        (result_layout.path_index, _HeaderField("path", "path")),
        (result_layout.item_index, _HeaderField("item", "item")),
        *((index, _HeaderField("content", "content")) for index in result_layout.content_indices),
    )
    for index, header_field in semantic_fields:
        extracted.consumed.add(index)
        if index < len(row) and row[index].strip():
            _add_field(extracted, header_field, row[index])

    for index, result in result_cells:
        extracted.consumed.add(index)
        header = header_layout.fields.get(index) if header_layout is not None else None
        key = header.key if header is not None and header.role == "result" else "UNSPECIFIED"
        _add_result(extracted.results, key, result)
    return extracted


def _is_workflow_status(value: str) -> bool:
    return _header_token(value) in _WORKFLOW_STATUS_TOKENS


def _add_field(extracted: _ExtractedFields, header: _HeaderField, raw_value: str) -> None:
    if header.role == "sensitive":
        extracted.redaction_categories.add(header.key)
        return
    value, categories = _sanitize(raw_value)
    extracted.redaction_categories.update(categories)
    if not value:
        return
    if header.role == "path":
        extracted.path_parts.append(value)
    elif header.role == "item":
        extracted.item_values.append(value)
    elif header.role == "content":
        extracted.content_values.append(value)
        extracted.explicit_content = True
    elif header.role == "result":
        _add_result(extracted.results, header.key, _normalize_result(value))
    elif header.role == "notes":
        extracted.notes.append(value)
    elif header.role == "risk":
        _add_unique(extracted.risk_labels, header.key, _normalize_inline(value))


def _parse_labeled_cell(value: str) -> tuple[_HeaderField, str] | None:
    match = _LABELED_CELL_RE.match(_normalize_text(value))
    if match is None:
        return None
    header = _classify_header(match.group(1))
    if header is None:
        return None
    return header, match.group(2).strip()


def _sanitize_join(values: list[str], categories: set[str]) -> str:
    sanitized: list[str] = []
    for value in values:
        clean, found = _sanitize(value)
        categories.update(found)
        if clean:
            sanitized.append(clean)
    return "\n".join(dict.fromkeys(sanitized))


def _sanitize(value: str) -> tuple[str, set[str]]:
    text = _normalize_text(value)
    categories: set[str] = set()

    def replace(pattern: re.Pattern[str], replacement: str, category: str) -> None:
        nonlocal text
        updated, count = pattern.subn(replacement, text)
        if count:
            categories.add(category)
            text = updated

    replace(_LABELED_ID_RE, r"\g<label>: [IDENTIFIER]", "identifier")
    replace(_JIRA_URL_RE, "[JIRA]", "jira")
    replace(_JIRA_KEY_RE, "[JIRA]", "jira")
    replace(_EMAIL_RE, "[EMAIL]", "email")
    replace(_PHONE_RE, "[PHONE]", "phone")
    replace(_IP_RE, "[IP_ADDRESS]", "ip_address")
    replace(_UUID_RE, "[IDENTIFIER]", "identifier")
    replace(_LABELED_PERSON_RE, r"\g<label>: [PERSON]\g<particle>", "person")
    replace(_DATE_RE, "[DATE]", "date")
    replace(_URL_RE, "[URL]", "url")
    replace(_LONG_IDENTIFIER_RE, "[IDENTIFIER]", "identifier")
    return _normalize_text(text), categories


def _only_sensitive(value: str) -> bool:
    sanitized, categories = _sanitize(value)
    if not categories:
        return False
    residue = re.sub(r"\[(?:JIRA|EMAIL|PHONE|IP_ADDRESS|IDENTIFIER|DATE|URL|PERSON)\]", "", sanitized)
    residue = re.sub(
        r"(?:담당자|작성자|검수자|테스터|개발자|이름|assignee|reporter|author|tester|owner|name)\s*:?",
        "",
        residue,
        flags=re.IGNORECASE,
    )
    return not residue.strip(" :-=,;/")


def _normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def _normalize_inline(value: str) -> str:
    return " ".join(_normalize_text(value).split())


def _header_token(value: str) -> str:
    return re.sub(r"[\s_\-:/()\[\].]+", "", _normalize_inline(value).casefold())


def _platform_from_header(label: str) -> str | None:
    candidates = (
        ("AOS", ("aos", "android", "안드로이드")),
        ("IOS", ("ios", "iphone", "ipad", "아이폰", "아이패드")),
        ("MOBILE_WEB", ("mobileweb", "mweb", "모바일웹")),
        ("WEB", ("web", "웹")),
        ("PC", ("pc", "desktop", "데스크톱")),
        ("APP", ("app", "앱")),
    )
    for platform, aliases in candidates:
        if any(alias in label for alias in aliases):
            return platform
    return None


def _risk_key(label: str) -> str:
    if "priority" in label or "우선" in label or "중요" in label:
        return "PRIORITY"
    if "severity" in label or "심각" in label:
        return "SEVERITY"
    if "impact" in label or "영향" in label:
        return "IMPACT"
    return "RISK"


def _sensitive_category(label: str) -> str:
    if any(value in label for value in ("jira", "ticket", "issue")):
        return "jira"
    if any(value in label for value in ("date", "일자", "날짜")):
        return "date"
    if any(value in label for value in ("id", "번호", "order", "customer", "member", "주문", "고객", "회원")):
        return "identifier"
    return "person"


def _looks_like_sensitive_header(label: str) -> bool:
    person_suffixes = (
        "담당자",
        "작성자",
        "검수자",
        "테스터",
        "개발자",
        "이름",
        "assignee",
        "reporter",
        "author",
        "tester",
        "owner",
        "name",
    )
    if label.endswith(person_suffixes):
        return True
    if any(marker in label for marker in ("jira", "ticketkey", "issuekey", "주문번호", "고객번호", "회원번호")):
        return True
    return label.endswith(("date", "createdat", "updatedat", "일자", "날짜"))


def _looks_like_path(value: str) -> bool:
    normalized = _normalize_text(value)
    if _URL_RE.search(normalized):
        return False
    if _PATH_PREFIX_RE.match(normalized):
        return True
    return bool(_PATH_SPLIT_RE.search(normalized)) and len(normalized) <= 240


def _looks_like_content(value: str) -> bool:
    normalized = _normalize_text(value)
    return bool(_CONTENT_SIGNAL_RE.search(normalized) or "\n" in normalized or len(normalized) >= 24)


def _is_single_cell_context_row(row: list[str], layout: _HeaderLayout | None) -> bool:
    nonempty = [(index, cell) for index, cell in enumerate(row) if cell.strip()]
    if len(nonempty) != 1:
        return False
    index, value = nonempty[0]
    if not _looks_like_content(value):
        return False
    if layout is None:
        return True
    field = layout.fields.get(index)
    return field is None or field.role not in {"path", "item", "content", "result"}


def _is_issue_section_row(
    row: list[str], result_cells: tuple[tuple[int, str], ...]
) -> bool:
    if result_cells:
        return False
    return any(_JIRA_KEY_RE.search(cell) or _JIRA_URL_RE.search(cell) for cell in row if cell.strip())


def _is_context_metadata_row(
    row: list[str], result_cells: tuple[tuple[int, str], ...]
) -> bool:
    nonempty = [cell for cell in row if cell.strip()]
    return bool(nonempty) and not result_cells and all(
        _CONTEXT_METADATA_RE.match(_normalize_inline(cell)) for cell in nonempty
    )


def _row_has_qa_signal(row: list[str], extracted: _ExtractedFields) -> bool:
    if extracted.results or extracted.risk_labels:
        return True
    return any(_looks_like_content(cell) for cell in row if cell.strip())


def _missing_semantics_reason(path: str, item: str, content: str) -> str:
    missing = [
        name
        for name, value in (("path", path), ("item", item), ("content", content))
        if not value
    ]
    return "missing_" + "_and_".join(missing) if missing else "unclassified"


def _first_content_line(value: str) -> str:
    first = value.split("\n", 1)[0]
    return first if len(first) <= 120 else first[:117].rstrip() + "..."


def _normalize_result(value: str) -> str:
    normalized = _normalize_inline(value)
    token = re.sub(r"[\s_-]+", "", normalized.casefold())
    if token in {"pass", "passed", "ok", "통과", "성공"}:
        return "PASS"
    if token in {"fail", "failed", "ng", "error", "실패", "오류"}:
        return "FAIL"
    if token in {"확인불가", "확인불가능", "unavailable", "cannotverify"}:
        return "UNAVAILABLE"
    if token in {"notrun", "unexecuted", "미실행"}:
        return "NOT_RUN"
    if token in {"n/a", "na", "해당없음", "skip", "skipped"}:
        return "NOT_APPLICABLE"
    if token in {"blocked", "보류", "차단"}:
        return "BLOCKED"
    return normalized


def _add_result(results: dict[str, str], key: str, value: str) -> None:
    _add_unique(results, key, value)


def _add_unique(target: dict[str, str], key: str, value: str) -> None:
    candidate = key
    suffix = 2
    while candidate in target:
        candidate = f"{key}_{suffix}"
        suffix += 1
    target[candidate] = value


def _risk_signals(results: dict[str, str], risk_labels: dict[str, str]) -> tuple[str, ...]:
    signals: set[str] = set()
    values = set(results.values())
    if "FAIL" in values:
        signals.add("historical_failure")
    if "UNAVAILABLE" in values:
        signals.add("historical_unavailable")
    if "BLOCKED" in values:
        signals.add("historical_blocked")
    if risk_labels:
        signals.add("explicit_risk_metadata")
    return tuple(sorted(signals))


def _duplicate_key(path: str, item: str, content: str) -> str:
    payload = {"content": content, "item": item, "path": path}
    return _sha256(_stable_json(payload).encode("utf-8"))


def _source_row_hash(row: list[str]) -> str:
    return _sha256(_stable_json(row).encode("utf-8"))


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
