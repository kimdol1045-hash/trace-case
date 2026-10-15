from __future__ import annotations

import math
import unicodedata

import pytest

from tc_backend.generation.mapping import (
    AmbiguousMappingError,
    CategoryMappingCandidate,
    CategoryMappingRequest,
    CreateNewParentDecision,
    GenerationTargetMapper,
    InvalidSemanticVectorError,
    MappingMethod,
    MappingReviewReason,
    MissingUncategorizedCategoryError,
    ParentWorkItemCandidate,
    ParentWorkItemMappingRequest,
    ReuseParentDecision,
    SlackSourceIdentity,
    normalize_mapping_text,
)


def _category(
    category_id: str,
    name: str,
    *,
    aliases: tuple[str, ...] = (),
    vector: tuple[float, ...] | None = None,
    active: bool = True,
    fallback: bool = False,
) -> CategoryMappingCandidate:
    return CategoryMappingCandidate(
        category_id=category_id,
        canonical_name=name,
        aliases=aliases,
        semantic_vector=vector,
        active=active,
        is_system_uncategorized=fallback,
    )


def _source(message_ts: str = "1787706000.000001") -> SlackSourceIdentity:
    return SlackSourceIdentity(
        workspace_id="T_TEST",
        channel_id="C_TEST",
        message_ts=message_ts,
    )


def _parent_request(
    *,
    source_identity: SlackSourceIdentity | None = None,
    key: str = "SLACK-100001",
    title: str = "비밀번호 재설정 개선",
    category_id: str = "category-members",
    vector: tuple[float, ...] | None = None,
) -> ParentWorkItemMappingRequest:
    return ParentWorkItemMappingRequest(
        source_identity=source_identity or _source(),
        source_key=key,
        title=title,
        selected_category_id=category_id,
        semantic_vector=vector,
    )


def _parent(
    work_item_id: str,
    *,
    key: str,
    title: str,
    category_id: str = "category-existing",
    aliases: tuple[str, ...] = (),
    source_identity: SlackSourceIdentity | None = None,
    vector: tuple[float, ...] | None = None,
    active: bool = True,
) -> ParentWorkItemCandidate:
    return ParentWorkItemCandidate(
        work_item_id=work_item_id,
        work_item_key=key,
        title=title,
        category_id=category_id,
        aliases=aliases,
        slack_source_identity=source_identity,
        semantic_vector=vector,
        active=active,
    )


def test_korean_mapping_normalization_is_nfkc_whitespace_and_case_folded() -> None:
    decomposed = unicodedata.normalize("NFD", "회원 계정관리")

    assert normalize_mapping_text(f"  {decomposed}  ") == "회원 계정관리"
    assert normalize_mapping_text(" ＡI   생성함 ") == "ai 생성함"


def test_category_prefers_exact_canonical_name_then_exact_alias() -> None:
    candidates = (
        _category("account", "회원 > 계정관리", aliases=("마이페이지",)),
        _category("fallback", "미분류", fallback=True),
    )
    mapper = GenerationTargetMapper()

    exact = mapper.map_category(
        CategoryMappingRequest(requested_name="  회원 >  계정관리 "), candidates
    )
    alias = mapper.map_category(CategoryMappingRequest(requested_name="마이페이지"), candidates)

    assert exact == exact.__class__(
        category_id="account", method=MappingMethod.EXACT_NAME, score=1.0
    )
    assert alias.method is MappingMethod.EXACT_ALIAS
    assert alias.category_id == "account"
    assert alias.review_reason is None


def test_category_rejects_cross_candidate_exact_alias_ownership() -> None:
    candidates = (
        _category("canonical-owner", "마이페이지"),
        _category("alias-owner", "회원", aliases=("  마이페이지 ",)),
        _category("fallback", "미분류", fallback=True),
    )

    with pytest.raises(AmbiguousMappingError, match="alias-owner, canonical-owner"):
        GenerationTargetMapper().map_category(
            CategoryMappingRequest(requested_name="마이페이지"), candidates
        )


def test_category_ignores_deleted_candidates_and_uses_existing_system_fallback() -> None:
    result = GenerationTargetMapper().map_category(
        CategoryMappingRequest(requested_name="삭제된 메뉴"),
        (
            _category("deleted", "삭제된 메뉴", active=False),
            _category("deleted-fallback", "미분류", active=False, fallback=True),
            _category("fallback", "미분류", fallback=True),
        ),
    )

    assert result.category_id == "fallback"
    assert result.method is MappingMethod.UNCATEGORIZED_FALLBACK
    assert result.score is None
    assert result.review_reason is MappingReviewReason.CATEGORY_NO_SEMANTIC_INPUT


def test_category_fallback_requires_one_existing_active_system_category() -> None:
    request = CategoryMappingRequest(requested_name="없는 메뉴")

    with pytest.raises(MissingUncategorizedCategoryError, match="required"):
        GenerationTargetMapper().map_category(request, (_category("ordinary", "회원"),))
    with pytest.raises(AmbiguousMappingError, match="multiple active"):
        GenerationTargetMapper().map_category(
            request,
            (
                _category("fallback-a", "미분류", fallback=True),
                _category("fallback-b", "미분류", fallback=True),
            ),
        )


def test_category_semantic_threshold_and_stable_tie_order_are_explicit() -> None:
    mapper = GenerationTargetMapper(category_semantic_threshold=0.90)
    tied = mapper.map_category(
        CategoryMappingRequest(requested_name=None, semantic_vector=(1.0, 0.0)),
        (
            _category("z-category", "Z", vector=(1.0, 0.0)),
            _category("a-category", "A", vector=(1.0, 0.0)),
            _category("fallback", "미분류", fallback=True),
        ),
    )

    assert tied.category_id == "a-category"
    assert tied.method is MappingMethod.SEMANTIC
    assert tied.score == 1.0
    assert tied.review_reason is MappingReviewReason.CATEGORY_SEMANTIC_TIE

    below = mapper.map_category(
        CategoryMappingRequest(requested_name="unknown", semantic_vector=(1.0, 0.0)),
        (
            _category("other", "다른 메뉴", vector=(0.0, 1.0)),
            _category("fallback", "미분류", fallback=True),
        ),
    )
    assert below.category_id == "fallback"
    assert below.score == 0.0
    assert below.review_reason is MappingReviewReason.CATEGORY_SEMANTIC_BELOW_THRESHOLD


def test_successful_category_semantic_match_precedes_fallback_resolution() -> None:
    result = GenerationTargetMapper().map_category(
        CategoryMappingRequest(requested_name=None, semantic_vector=(1.0e300, 0.0)),
        (_category("semantic", "의미 후보", vector=(1.0e300, 0.0)),),
    )

    assert result.category_id == "semantic"
    assert result.method is MappingMethod.SEMANTIC
    assert result.score == 1.0


@pytest.mark.parametrize(
    ("query", "candidate"),
    [
        ((math.nan, 1.0), (1.0, 0.0)),
        ((math.inf, 1.0), (1.0, 0.0)),
        ((0.0, 0.0), (1.0, 0.0)),
        ((1.0, 0.0), (math.nan, 0.0)),
        ((1.0, 0.0), (1.0,)),
    ],
)
def test_category_rejects_non_finite_zero_and_mismatched_vectors(
    query: tuple[float, ...], candidate: tuple[float, ...]
) -> None:
    with pytest.raises(InvalidSemanticVectorError):
        GenerationTargetMapper().map_category(
            CategoryMappingRequest(requested_name="unknown", semantic_vector=query),
            (
                _category("candidate", "후보", vector=candidate),
                _category("fallback", "미분류", fallback=True),
            ),
        )


def test_parent_reuses_exact_slack_source_before_title_and_ignores_deleted_source() -> None:
    source = _source()
    result = GenerationTargetMapper().map_parent_work_item(
        _parent_request(source_identity=source, title="다른 제목"),
        (
            _parent(
                "deleted",
                key="OLD-1",
                title="삭제됨",
                source_identity=source,
                active=False,
            ),
            _parent(
                "active",
                key="OTHER-1",
                title="다른 제목",
                source_identity=source,
            ),
        ),
    )

    assert isinstance(result, ReuseParentDecision)
    assert result.work_item_id == "active"
    assert result.method is MappingMethod.SLACK_SOURCE_IDENTITY
    assert result.category_id == "category-existing"


def test_parent_normalized_key_precedes_title_and_alias() -> None:
    result = GenerationTargetMapper().map_parent_work_item(
        _parent_request(key=" ＳＬＡＣＫ－１００００１ ", title="별칭 제목"),
        (
            _parent("key-owner", key="slack-100001", title="키 소유자"),
            _parent(
                "alias-owner",
                key="OTHER-2",
                title="다른 제목",
                aliases=("별칭 제목",),
            ),
        ),
    )

    assert isinstance(result, ReuseParentDecision)
    assert result.work_item_id == "key-owner"
    assert result.method is MappingMethod.EXACT_KEY


def test_parent_rejects_ambiguous_primary_and_title_alias_ownership() -> None:
    source = _source()
    with pytest.raises(AmbiguousMappingError, match="Slack source/key"):
        GenerationTargetMapper().map_parent_work_item(
            _parent_request(source_identity=source, key="MATCH-1"),
            (
                _parent("source-owner", key="OTHER-1", title="A", source_identity=source),
                _parent("key-owner", key="match-1", title="B"),
            ),
        )

    with pytest.raises(AmbiguousMappingError, match="title/alias"):
        GenerationTargetMapper().map_parent_work_item(
            _parent_request(title="회원 개선"),
            (
                _parent("title-owner", key="OTHER-1", title="회원 개선"),
                _parent(
                    "alias-owner",
                    key="OTHER-2",
                    title="계정 개선",
                    aliases=("회원 개선",),
                ),
            ),
        )


def test_parent_exact_korean_title_and_alias_are_normalized() -> None:
    decomposed_title = unicodedata.normalize("NFD", "비밀번호 재설정 개선")
    mapper = GenerationTargetMapper()
    candidates = (
        _parent("title", key="OTHER-1", title=decomposed_title),
        _parent("alias", key="OTHER-2", title="계정 개선", aliases=("마이페이지 개선",)),
    )

    title = mapper.map_parent_work_item(_parent_request(), candidates)
    alias = mapper.map_parent_work_item(_parent_request(title=" 마이페이지   개선 "), candidates)

    assert isinstance(title, ReuseParentDecision)
    assert title.work_item_id == "title"
    assert title.method is MappingMethod.EXACT_TITLE
    assert isinstance(alias, ReuseParentDecision)
    assert alias.work_item_id == "alias"
    assert alias.method is MappingMethod.EXACT_ALIAS


def test_parent_semantic_selection_has_threshold_and_stable_tie_order() -> None:
    result = GenerationTargetMapper(parent_semantic_threshold=1.0).map_parent_work_item(
        _parent_request(vector=(1.0, 0.0)),
        (
            _parent("z-parent", key="OTHER-Z", title="Z", vector=(1.0, 0.0)),
            _parent("a-parent", key="OTHER-A", title="A", vector=(1.0, 0.0)),
        ),
    )

    assert isinstance(result, ReuseParentDecision)
    assert result.work_item_id == "a-parent"
    assert result.method is MappingMethod.SEMANTIC
    assert result.score == 1.0
    assert result.review_reason is MappingReviewReason.PARENT_SEMANTIC_TIE


def test_parent_miss_returns_create_decision_with_source_title_and_selected_category() -> None:
    source = _source()
    request = _parent_request(
        source_identity=source,
        key="SLACK-NEW",
        title="신규 결제 개선",
        category_id="category-payments",
        vector=(1.0, 0.0),
    )
    result = GenerationTargetMapper(parent_semantic_threshold=0.9).map_parent_work_item(
        request,
        (_parent("far", key="OTHER", title="다른 일감", vector=(0.0, 1.0)),),
    )

    assert isinstance(result, CreateNewParentDecision)
    assert result.method is MappingMethod.CREATE_NEW_PARENT
    assert result.source_identity == source
    assert result.source_key == "SLACK-NEW"
    assert result.title == "신규 결제 개선"
    assert result.category_id == "category-payments"
    assert result.score == 0.0
    assert result.review_reason is MappingReviewReason.PARENT_SEMANTIC_BELOW_THRESHOLD


def test_parent_with_no_active_semantic_candidate_returns_explicit_create_decision() -> None:
    result = GenerationTargetMapper().map_parent_work_item(
        _parent_request(vector=(1.0, 0.0)),
        (
            _parent(
                "deleted",
                key="SLACK-100001",
                title="비밀번호 재설정 개선",
                vector=(1.0, 0.0),
                active=False,
            ),
        ),
    )

    assert isinstance(result, CreateNewParentDecision)
    assert result.score is None
    assert result.review_reason is MappingReviewReason.PARENT_NO_SEMANTIC_CANDIDATE


def test_parent_rejects_invalid_or_mismatched_semantic_vectors() -> None:
    with pytest.raises(InvalidSemanticVectorError, match="dimension"):
        GenerationTargetMapper().map_parent_work_item(
            _parent_request(vector=(1.0, 0.0)),
            (_parent("candidate", key="OTHER", title="Other", vector=(1.0,)),),
        )
