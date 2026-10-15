"""Conservative parser for the four observable fields in free-form Slack work items."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from tc_backend.infrastructure.slack.models import (
    SLACK_WORK_ITEM_FIELDS,
    SlackSourceMessage,
    SlackWorkItemParseResult,
)

_ALIASES: dict[str, tuple[str, ...]] = {
    "developmentItem": (
        "개발건",
        "개발 건",
        "개발 요청",
        "작업명",
        "일감 제목",
        "제목",
    ),
    "requestedMenu": (
        "요청 메뉴",
        "대상 메뉴",
        "대상 화면",
        "메뉴 경로",
        "메뉴",
    ),
    "featureDescription": (
        "기능 설명",
        "기능설명",
        "기능 개요",
        "현재 기능",
        "배경",
    ),
    "requestDetails": (
        "요청 내용",
        "요청내용",
        "상세 요청",
        "상세내용",
        "요구사항",
    ),
}
_ALIAS_TO_FIELD = {
    alias.replace(" ", "").casefold(): field_name
    for field_name, aliases in _ALIASES.items()
    for alias in aliases
}
_ALTERNATION = "|".join(
    re.escape(alias)
    for alias in sorted(
        (alias for aliases in _ALIASES.values() for alias in aliases),
        key=len,
        reverse=True,
    )
)
_LEADING_MARKUP = re.compile(r"^\s*(?:[-*•]\s*)?(?:#{1,6}\s*)?")
_LABEL_AT_START = re.compile(
    rf"^\s*(?:[-*•]\s*)?(?:[*_`]*)\s*(?P<label>{_ALTERNATION})"
    rf"\s*(?:[*_`]*)\s*(?P<separator>[:：=]|[-–—]\s+|은\s+|는\s+)\s*(?P<value>.*?)\s*$",
    re.IGNORECASE,
)
_INLINE_LABEL = re.compile(
    rf"(?P<label>{_ALTERNATION})\s*(?:(?:은|는|이|가)\s+|[:：=]\s*)",
    re.IGNORECASE,
)
_STANDALONE_WORK_ITEM_MARKER = re.compile(r"^\s*(?:\[일감\]|일감)\s*$")
_PREFIXED_TITLE = re.compile(r"^\s*\[일감\]\s*(?P<title>\S.+?)\s*$")
_UNKNOWN_LABEL = re.compile(r"^\s*(?:[-*•]\s*)?[^:：]{1,30}[:：]\s*\S")


@dataclass(frozen=True, slots=True)
class _Extraction:
    values: dict[str, str]
    field_revisions: dict[str, str]


class SlackWorkItemParser:
    """Extract observed values only; never synthesize a missing business fact."""

    def parse(self, messages: Sequence[SlackSourceMessage]) -> SlackWorkItemParseResult:
        if not messages:
            raise ValueError("Slack work item parser requires at least one source revision")

        values: dict[str, str] = {}
        field_revisions: dict[str, str] = {}
        for message in messages:
            if not message.message_revision_id.strip():
                raise ValueError("Slack source message revision ID must not be empty")
            extracted = _extract_message(message)
            for field_name, value in extracted.values.items():
                values[field_name] = value
                field_revisions[field_name] = message.message_revision_id

        missing = tuple(field for field in SLACK_WORK_ITEM_FIELDS if field not in values)
        contributing_revisions = frozenset(field_revisions.values())
        source_revisions = tuple(
            dict.fromkeys(
                message.message_revision_id
                for message in messages
                if message.message_revision_id in contributing_revisions
            )
        )
        if not source_revisions:
            # The contract requires the attempted source even when no field was
            # recognized. Its content remains absent and all four fields remain missing.
            source_revisions = (messages[0].message_revision_id,)
        observed_count = len(SLACK_WORK_ITEM_FIELDS) - len(missing)
        confidence = 0.0 if observed_count == 0 else round(0.50 + 0.12 * observed_count, 2)
        return SlackWorkItemParseResult(
            development_item=values.get("developmentItem"),
            requested_menu=values.get("requestedMenu"),
            feature_description=values.get("featureDescription"),
            request_details=values.get("requestDetails"),
            missing_fields=missing,
            confidence=confidence,
            source_message_revisions=source_revisions,
        )


def _extract_message(message: SlackSourceMessage) -> _Extraction:
    lines = [line.strip() for line in message.body.replace("\r\n", "\n").split("\n")]
    values: dict[str, str] = {}
    field_revisions: dict[str, str] = {}
    active_field: str | None = None
    active_parts: list[str] = []

    def flush() -> None:
        nonlocal active_field, active_parts
        if active_field is not None:
            value = _clean_value(" ".join(active_parts))
            if value:
                values[active_field] = value
                field_revisions[active_field] = message.message_revision_id
        active_field = None
        active_parts = []

    for line in lines:
        if not line:
            flush()
            continue
        match = _LABEL_AT_START.match(line)
        if match is not None:
            flush()
            field_name = _field_for_alias(match.group("label"))
            inline_values = _extract_inline_fields(
                f"{match.group('label')}: {match.group('value')}"
            )
            if len(inline_values) > 1:
                values.update(inline_values)
                field_revisions.update(
                    {key: message.message_revision_id for key in inline_values}
                )
            else:
                active_field = field_name
                active_parts = [match.group("value")]
            continue

        inline_values = _extract_inline_fields(line)
        if inline_values:
            flush()
            values.update(inline_values)
            field_revisions.update({key: message.message_revision_id for key in inline_values})
            continue
        if _UNKNOWN_LABEL.match(line):
            flush()
            continue
        if active_field is not None:
            active_parts.append(_LEADING_MARKUP.sub("", line))
    flush()

    if "developmentItem" not in values:
        title = _extract_title(lines, has_structured_field=bool(values))
        if title is not None:
            values["developmentItem"] = title
            field_revisions["developmentItem"] = message.message_revision_id
    return _Extraction(values=values, field_revisions=field_revisions)


def _extract_inline_fields(line: str) -> dict[str, str]:
    matches = list(_INLINE_LABEL.finditer(line))
    if not matches:
        return {}
    values: dict[str, str] = {}
    for index, match in enumerate(matches):
        value_start = match.end()
        value_end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
        value = _clean_sentence_span(line[value_start:value_end])
        if value:
            values[_field_for_alias(match.group("label"))] = value
    return values


def _extract_title(lines: list[str], *, has_structured_field: bool) -> str | None:
    non_empty = [line for line in lines if line]
    if not non_empty:
        return None
    first = non_empty[0]
    prefixed = _PREFIXED_TITLE.match(first)
    if prefixed is not None:
        return _clean_value(prefixed.group("title")) or None
    if _STANDALONE_WORK_ITEM_MARKER.match(first):
        if len(non_empty) < 2:
            return None
        candidate = non_empty[1]
    else:
        candidate = first
    candidate = _LEADING_MARKUP.sub("", candidate)
    if (
        has_structured_field
        and _LABEL_AT_START.match(candidate) is None
        and _INLINE_LABEL.search(candidate) is None
        and 1 <= len(candidate) <= 120
        and not candidate.endswith((".", "。", "다.", "요."))
    ):
        return _clean_value(candidate) or None
    return None


def _field_for_alias(alias: str) -> str:
    return _ALIAS_TO_FIELD[alias.replace(" ", "").casefold()]


def _clean_sentence_span(value: str) -> str:
    cleaned = _clean_value(value)
    cleaned = re.sub(r"\s*(?:이고|이며|하며|입니다|이다)\s*[,.]?\s*$", "", cleaned)
    return cleaned.strip()


def _clean_value(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^[*_`]+|[*_`]+$", "", value).strip()
    value = re.sub(r"\s+", " ", value)
    return value.strip(" \t-–—")
