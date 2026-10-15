from __future__ import annotations

import pytest

from tc_backend.generation.quality_review import (
    QualityReviewError,
    ReviewerAuthority,
    hash_reviewer_token,
)


def _authority() -> ReviewerAuthority:
    return ReviewerAuthority(
        (
            ("quality-principal-alpha", hash_reviewer_token("alpha-review-token")),
            ("quality-principal-beta", hash_reviewer_token("beta-review-token")),
        )
    )


def test_reviewer_tokens_map_only_to_allowlisted_opaque_principals() -> None:
    authority = _authority()

    assert authority.resolve("alpha-review-token") == "quality-principal-alpha"
    assert authority.resolve("beta-review-token") == "quality-principal-beta"


@pytest.mark.parametrize("token", [None, "", "wrong", "quality-principal-alpha"])
def test_missing_invalid_and_principal_spoof_tokens_fail_without_echo(token: str | None) -> None:
    authority = _authority()

    with pytest.raises(QualityReviewError) as exc_info:
        authority.resolve(token)

    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "QUALITY_REVIEWER_UNAUTHORIZED"
    assert not token or token not in str(exc_info.value)


def test_authority_requires_two_distinct_principals_and_hashes() -> None:
    digest = hash_reviewer_token("alpha-review-token")

    with pytest.raises(QualityReviewError, match="QUALITY_REVIEW_AUTHORITY_NOT_CONFIGURED"):
        ReviewerAuthority((("quality-principal-alpha", digest),))
    with pytest.raises(QualityReviewError, match="QUALITY_REVIEW_AUTHORITY_INVALID"):
        ReviewerAuthority(
            (
                ("quality-principal-alpha", digest),
                ("quality-principal-alpha", hash_reviewer_token("beta-review-token")),
            )
        )
    with pytest.raises(QualityReviewError, match="QUALITY_REVIEW_AUTHORITY_INVALID"):
        ReviewerAuthority(
            (
                ("quality-principal-alpha", digest),
                ("quality-principal-beta", digest),
            )
        )
