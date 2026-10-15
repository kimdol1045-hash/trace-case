from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tc_backend.config import ONTOLOGY_EMBEDDING_DIMENSION, Settings
from tc_backend.generation.quality_review import hash_reviewer_token

VALID_VALUES = {
    "database_url": "postgresql+psycopg://tc:tc@localhost:5432/tc",
    "encryption_master_key": "x" * 32,
}
ROOT = Path(__file__).resolve().parents[3]


def test_minimal_local_settings_are_valid() -> None:
    settings = Settings(_env_file=None, **VALID_VALUES)

    assert settings.environment == "local"
    assert settings.embedding_dimension == ONTOLOGY_EMBEDDING_DIMENSION
    assert settings.sqlalchemy_database_url.startswith("postgresql+psycopg://")


def test_environment_examples_match_settings_names_and_json_collection_contract() -> None:
    backend_example = (ROOT / "backend/.env.example").read_text(encoding="utf-8")
    slack_example = (ROOT / "deploy/slack/http.env.example").read_text(encoding="utf-8")

    assert "TC_LIGHT_RAG_WORKSPACE=tc" in backend_example
    assert "TC_LIGHTRAG_WORKSPACE=" not in backend_example
    assert "TC_AUTOMATIC_KNOWLEDGE_PROJECTION_ENABLED=false" in backend_example
    assert "TC_KNOWLEDGE_EXTRACTION_TIMEOUT_SECONDS=60" in backend_example
    assert "TC_KNOWLEDGE_EXTRACTION_RESPONSE_MAX_BYTES=1048576" in backend_example
    assert "TC_QUALITY_REVIEWER_PRINCIPALS=[]" in backend_example
    assert "TC_QUALITY_REVIEWER_TOKEN_HASHES=[]" in backend_example

    allowlist_line = next(
        line
        for line in slack_example.splitlines()
        if line.startswith("TC_SLACK_ALLOWLIST_CHANNEL_IDS=")
    )
    allowlist = json.loads(allowlist_line.split("=", 1)[1])
    assert allowlist == ["C_REPLACE_ME"]
    settings = Settings(
        _env_file=None,
        **(VALID_VALUES | {"slack_allowlist_channel_ids": allowlist}),
    )
    assert settings.slack_allowlist_channel_ids == ("C_REPLACE_ME",)


@pytest.mark.parametrize("missing", ["database_url", "encryption_master_key"])
def test_required_setting_missing_fails(missing: str) -> None:
    values = VALID_VALUES | {missing: None}

    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_short_encryption_key_fails_without_exposing_secret() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None, **(VALID_VALUES | {"encryption_master_key": "too-short"}))

    assert "at least 32" in str(exc_info.value)


def test_http_slack_mode_requires_its_secret_bundle() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None, **(VALID_VALUES | {"slack_mode": "http"}))

    message = str(exc_info.value)
    assert "HTTP Slack mode requires" in message
    assert "slack_signing_secret" in message


def test_partial_object_storage_configuration_fails() -> None:
    with pytest.raises(ValidationError, match="Object storage requires"):
        Settings(
            _env_file=None,
            **(VALID_VALUES | {"object_storage_endpoint_url": "https://r2.example.test"}),
        )


def test_object_storage_size_cannot_exceed_hard_limit() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            **(VALID_VALUES | {"object_storage_max_file_bytes": 50 * 1024 * 1024 + 1}),
        )


def test_embedding_dimension_must_match_migration() -> None:
    with pytest.raises(ValidationError, match="embedding dimension does not match"):
        Settings(_env_file=None, **(VALID_VALUES | {"embedding_dimension": 768}))


def test_quality_review_configuration_requires_two_distinct_hashed_credentials() -> None:
    with pytest.raises(ValidationError, match="at least two paired"):
        Settings(
            _env_file=None,
            **(
                VALID_VALUES
                | {
                    "quality_reviewer_principals": ("quality-principal-alpha",),
                    "quality_reviewer_token_hashes": (hash_reviewer_token("alpha-review-token"),),
                }
            ),
        )

    settings = Settings(
        _env_file=None,
        **(
            VALID_VALUES
            | {
                "quality_reviewer_principals": (
                    "quality-principal-alpha",
                    "quality-principal-beta",
                ),
                "quality_reviewer_token_hashes": (
                    hash_reviewer_token("alpha-review-token"),
                    hash_reviewer_token("beta-review-token"),
                ),
                "quality_review_source_seal_key": "s" * 32,
            }
        ),
    )
    assert [principal for principal, _ in settings.quality_reviewer_credentials] == [
        "quality-principal-alpha",
        "quality-principal-beta",
    ]
    assert "alpha-review-token" not in repr(settings)


def test_production_requires_enabled_allowlisted_slack() -> None:
    with pytest.raises(ValidationError, match="cannot be disabled"):
        Settings(_env_file=None, **(VALID_VALUES | {"environment": "production"}))


def test_production_requires_private_r2_and_fail_closed_scanner() -> None:
    production = VALID_VALUES | {
        "environment": "production",
        "slack_mode": "http",
        "slack_signing_secret": "signing",
        "slack_bot_token": "bot",
        "slack_workspace_id": "workspace",
        "public_slack_base_url": "https://slack.example.test",
        "slack_allowlist_channel_ids": ("channel",),
    }
    with pytest.raises(ValidationError, match="private R2"):
        Settings(_env_file=None, **production)

    storage = production | {
        "object_storage_enabled": True,
        "object_storage_endpoint_url": "https://r2.example.test",
        "object_storage_bucket": "private",
        "object_storage_access_key_id": "access",
        "object_storage_secret_access_key": "secret",
    }
    with pytest.raises(ValidationError, match="ClamAV"):
        Settings(_env_file=None, **storage)
