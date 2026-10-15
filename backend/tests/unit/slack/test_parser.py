from __future__ import annotations

from tc_backend.infrastructure.slack.models import SlackSourceMessage
from tc_backend.infrastructure.slack.parser import SlackWorkItemParser


def test_labeled_fields_extract_exactly_the_four_contract_values() -> None:
    parsed = SlackWorkItemParser().parse(
        [
            SlackSourceMessage(
                "rev-1",
                """[일감]
개발건: 비밀번호 재설정 개선
요청 메뉴: 회원 > 계정관리 > 비밀번호
기능 설명: 이메일 인증 후 비밀번호를 변경한다.
요청내용: 인증번호 유효시간을 5분으로 변경한다.
담당자: QA 팀""",
            )
        ]
    ).as_dict()

    assert parsed == {
        "developmentItem": "비밀번호 재설정 개선",
        "requestedMenu": "회원 > 계정관리 > 비밀번호",
        "featureDescription": "이메일 인증 후 비밀번호를 변경한다.",
        "requestDetails": "인증번호 유효시간을 5분으로 변경한다.",
        "missingFields": [],
        "confidence": 0.98,
        "sourceMessageRevisions": ["rev-1"],
    }


def test_aliases_and_multiline_sections_are_supported() -> None:
    parsed = SlackWorkItemParser().parse(
        [
            SlackSourceMessage(
                "rev-alias",
                """작업명 - CSV 내보내기
대상 화면: 회원 > 권한
기능 개요: 사용자 목록을 내려받는다.
상세 요청:
- 관리자에게만 버튼을 노출한다.
- UTF-8 CSV를 사용한다.""",
            )
        ]
    ).as_dict()

    assert parsed["developmentItem"] == "CSV 내보내기"
    assert parsed["requestedMenu"] == "회원 > 권한"
    assert parsed["requestDetails"] == "관리자에게만 버튼을 노출한다. UTF-8 CSV를 사용한다."


def test_sentence_form_and_plain_title_are_conservative() -> None:
    sentence = SlackWorkItemParser().parse(
        [
            SlackSourceMessage(
                "rev-sentence",
                (
                    "개발건은 로그인 잠금 정책이며 요청 메뉴는 회원 > 로그인이고 "
                    "기능 설명은 반복 실패 시 계정을 보호하며 요청 내용은 5회 실패 시 잠근다."
                ),
            )
        ]
    ).as_dict()
    assert sentence["developmentItem"] == "로그인 잠금 정책"
    assert sentence["requestedMenu"] == "회원 > 로그인"
    assert sentence["featureDescription"] == "반복 실패 시 계정을 보호"
    assert sentence["requestDetails"] == "5회 실패 시 잠근다."

    title = SlackWorkItemParser().parse(
        [SlackSourceMessage("rev-title", "주문 취소 조건 정리\n메뉴: 주문 > 취소·환불")]
    ).as_dict()
    assert title["developmentItem"] == "주문 취소 조건 정리"
    assert title["requestedMenu"] == "주문 > 취소·환불"
    assert title["missingFields"] == ["featureDescription", "requestDetails"]


def test_missing_values_are_not_created_and_sources_track_contributors() -> None:
    parsed = SlackWorkItemParser().parse(
        [
            SlackSourceMessage("rev-root", "개발건: 로그인 문구 개선"),
            SlackSourceMessage("rev-reply", "요구사항: 원인을 구분해 노출하지 않는다."),
        ]
    ).as_dict()

    assert "requestedMenu" not in parsed
    assert "featureDescription" not in parsed
    assert parsed["missingFields"] == ["requestedMenu", "featureDescription"]
    assert parsed["sourceMessageRevisions"] == ["rev-root", "rev-reply"]
