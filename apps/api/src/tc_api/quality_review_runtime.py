"""Secret-bearing runtime assembly for the narrow T08 reviewer authority."""

from __future__ import annotations

from dataclasses import dataclass

from tc_backend.config import Settings
from tc_backend.generation.quality_review import ReviewerAuthority


@dataclass(frozen=True, slots=True)
class QualityReviewRuntime:
    reviewer_authority: ReviewerAuthority
    source_seal_key: bytes
    source_seal_key_id: str

    @classmethod
    def from_settings(cls, settings: Settings) -> QualityReviewRuntime | None:
        source_key = settings.quality_review_source_seal_key
        credentials = settings.quality_reviewer_credentials
        if source_key is None and not credentials:
            return None
        if source_key is None or len(credentials) < 2:
            return None
        return cls(
            reviewer_authority=ReviewerAuthority(credentials),
            source_seal_key=source_key.get_secret_value().encode("utf-8"),
            source_seal_key_id=settings.quality_review_source_seal_key_id,
        )
