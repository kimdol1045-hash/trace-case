"""Typed settings and configuration-only startup validation."""

from __future__ import annotations

import ipaddress
import re
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import AnyHttpUrl, Field, PostgresDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ONTOLOGY_EMBEDDING_DIMENSION = 1024


class SlackMode(StrEnum):
    DISABLED = "disabled"
    HTTP = "http"
    SOCKET = "socket"


class Settings(BaseSettings):
    """Process settings.

    Database access and the encryption key are always required. Integration
    credentials are conditionally required only when their feature is enabled,
    so a local backend can boot without contacting external services.
    """

    model_config = SettingsConfigDict(
        env_prefix="TC_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )

    environment: Literal["local", "staging", "production"] = "local"
    database_url: PostgresDsn
    encryption_master_key: SecretStr

    slack_mode: SlackMode = SlackMode.DISABLED
    slack_signing_secret: SecretStr | None = None
    slack_app_token: SecretStr | None = None
    slack_bot_token: SecretStr | None = None
    slack_workspace_id: str | None = None
    slack_allowlist_channel_ids: tuple[str, ...] = ()
    public_slack_base_url: AnyHttpUrl | None = None
    public_web_base_url: AnyHttpUrl | None = None
    slack_signature_tolerance_seconds: int = Field(default=300, ge=0, le=3_600)
    slack_api_base_url: AnyHttpUrl = AnyHttpUrl("https://slack.com/api")
    slack_web_api_max_rate_limit_retries: int = Field(default=2, ge=0, le=20)
    slack_web_api_max_provider_retries: int = Field(default=2, ge=0, le=20)
    slack_web_api_retry_base_seconds: float = Field(default=0.25, ge=0, le=300)
    slack_recent_resync_lookback_days: int = Field(default=7, ge=1, le=90)
    slack_recent_resync_interval_seconds: int = Field(default=3_600, ge=60, le=86_400)
    slack_generation_notifications_enabled: bool = False
    slack_notification_breaker_open: bool = False
    slack_notification_delivery_lease_seconds: int = Field(default=60, ge=5, le=900)
    slack_notification_reconcile_window_seconds: int = Field(default=86_400, ge=300, le=604_800)
    slack_notification_retry_base_seconds: int = Field(default=5, ge=1, le=3_600)
    slack_notification_retry_max_seconds: int = Field(default=3_600, ge=1, le=86_400)

    object_storage_enabled: bool = False
    object_storage_provider: Literal["R2", "MINIO"] = "R2"
    object_storage_endpoint_url: AnyHttpUrl | None = None
    object_storage_browser_endpoint_url: AnyHttpUrl | None = None
    object_storage_bucket: str | None = None
    object_storage_region: str = "auto"
    object_storage_access_key_id: str | None = None
    object_storage_secret_access_key: SecretStr | None = None
    object_storage_presign_ttl_seconds: int = Field(default=300, ge=60, le=900)
    object_storage_max_file_bytes: int = Field(default=50 * 1024 * 1024, ge=1, le=50 * 1024 * 1024)
    object_storage_clamav_host: str | None = None
    object_storage_clamav_port: int = Field(default=3310, ge=1, le=65_535)
    object_storage_clamav_timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    object_storage_temp_directory: Path | None = None
    object_storage_storage_budget_bytes: int = Field(default=10 * 1024**3, ge=1)
    object_storage_class_a_request_budget: int = Field(default=1_000_000, ge=1)
    object_storage_class_b_request_budget: int = Field(default=10_000_000, ge=1)
    object_storage_storage_usd_per_gb_month: float = Field(default=0.0, ge=0)
    object_storage_class_a_usd_per_million: float = Field(default=0.0, ge=0)
    object_storage_class_b_usd_per_million: float = Field(default=0.0, ge=0)
    object_storage_allowed_mime_types: tuple[str, ...] = (
        "application/json",
        "application/pdf",
        "application/gzip",
        "application/zip",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
        "text/csv",
        "text/markdown",
        "text/plain",
        "video/mp4",
        "video/webm",
    )

    ollama_base_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:11434")
    generation_model: str = "qwen3:8b"
    embedding_model: str = "bge-m3"
    embedding_dimension: int = ONTOLOGY_EMBEDDING_DIMENSION
    light_rag_workspace: str = "tc"

    # Versioned LightRAG routing is an independent, credentialed opt-in.  The
    # parent process owns only this typed configuration; the isolated runtime
    # factory passes the PostgreSQL credential directly to its child process.
    light_rag_enabled: bool = False
    light_rag_authority_id: str | None = None
    light_rag_auxiliary_target_digest: str | None = None
    light_rag_provider_id: str | None = None
    light_rag_postgres_host: str | None = None
    light_rag_postgres_port: int = Field(default=5432, ge=1, le=65_535)
    light_rag_postgres_user: str | None = None
    light_rag_postgres_password: SecretStr | None = None
    light_rag_postgres_database: str | None = None
    light_rag_postgres_max_connections: int = Field(default=12, ge=1, le=1_000)
    light_rag_postgres_ssl_mode: Literal[
        "disable", "allow", "prefer", "require", "verify-ca", "verify-full"
    ] = "require"
    light_rag_apache_age_available: bool = False
    light_rag_runtime_bootstrap: str | None = None
    light_rag_working_directory_root: Path | None = None
    light_rag_startup_timeout_seconds: float = Field(default=20.0, gt=0, le=600)
    light_rag_operation_timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    light_rag_shutdown_timeout_seconds: float = Field(default=5.0, gt=0, le=600)

    # Slack knowledge projection is independently opt-in.  Merely configuring
    # Slack or the credentialed generation provider must never construct a
    # local-LLM transport or make an Ollama request.
    automatic_knowledge_projection_enabled: bool = False
    knowledge_extraction_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    knowledge_extraction_response_max_bytes: int = Field(
        default=1024 * 1024, ge=1, le=4 * 1024 * 1024
    )

    # T07 automatic generation is an explicit, credentialed opt-in.  These
    # settings are deliberately separate from the local Ollama extraction
    # model above: enabling one must never silently enable the other.
    automatic_generation_enabled: bool = False
    automatic_generation_provider_id: str | None = None
    automatic_generation_provider_endpoint: AnyHttpUrl | None = None
    automatic_generation_provider_model_id: str | None = None
    automatic_generation_provider_auth_token: SecretStr | None = None
    automatic_generation_provider_auth_header: str = "Authorization"
    automatic_generation_provider_auth_scheme: str = "Bearer"
    automatic_generation_provider_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    automatic_generation_provider_response_max_bytes: int = Field(
        default=1024 * 1024, ge=1, le=4 * 1024 * 1024
    )

    # T08 uses a narrow no-login authority: opaque principals are paired by
    # ordinal with pre-provisioned SHA-256 token hashes. Raw tokens are never
    # configuration values and are accepted only in the mutation request.
    quality_reviewer_principals: tuple[str, ...] = ()
    quality_reviewer_token_hashes: tuple[SecretStr, ...] = ()
    quality_review_source_seal_key: SecretStr | None = None
    quality_review_source_seal_key_id: str = "quality-review-source-v1"

    job_max_attempts: int = Field(default=5, ge=1, le=100)
    job_retry_base_seconds: int = Field(default=5, ge=0, le=86_400)
    job_retry_max_seconds: int = Field(default=3_600, ge=0, le=604_800)
    job_stale_after_seconds: int = Field(default=300, ge=1, le=86_400)
    worker_metrics_path: Path | None = None

    @field_validator("encryption_master_key")
    @classmethod
    def validate_master_key(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("encryption master key must contain at least 32 characters")
        return value

    @field_validator("quality_review_source_seal_key")
    @classmethod
    def validate_quality_review_source_seal_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < 32:
            raise ValueError("quality review source seal key must contain at least 32 characters")
        return value

    @field_validator("quality_review_source_seal_key_id")
    @classmethod
    def validate_quality_review_source_seal_key_id(cls, value: str) -> str:
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9._:-]{0,127}", value) is None:
            raise ValueError("quality review source seal key ID must be opaque")
        return value

    @field_validator("object_storage_allowed_mime_types")
    @classmethod
    def validate_object_storage_mime_allowlist(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({mime.lower().strip() for mime in value if mime.strip()}))
        if not normalized:
            raise ValueError("object storage MIME allowlist must not be empty")
        if any("/" not in mime for mime in normalized):
            raise ValueError("object storage MIME allowlist contains an invalid MIME type")
        return normalized

    @field_validator("worker_metrics_path")
    @classmethod
    def validate_worker_metrics_path(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        if not value.is_absolute() or value.suffix != ".prom":
            raise ValueError("worker metrics path must be an absolute .prom file")
        return value

    @field_validator("light_rag_postgres_password")
    @classmethod
    def validate_light_rag_postgres_password(
        cls, value: SecretStr | None
    ) -> SecretStr | None:
        if value is not None and not value.get_secret_value():
            raise ValueError("LightRAG PostgreSQL password must not be empty")
        return value

    @field_validator("light_rag_working_directory_root")
    @classmethod
    def validate_light_rag_working_directory_root(
        cls, value: Path | None
    ) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("LightRAG working directory root must be absolute")
        return value

    @field_validator("public_web_base_url")
    @classmethod
    def validate_public_web_base_url(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        """Accept only a stable, credential-free public product-Web authority."""

        if value is None:
            return None
        host = value.host.casefold() if value.host is not None else ""
        if value.scheme != "https":
            raise ValueError("public Web base URL must use HTTPS")
        if value.username is not None or value.password is not None:
            raise ValueError("public Web base URL must not contain credentials")
        if value.query is not None or value.fragment is not None:
            raise ValueError("public Web base URL must not contain a query or fragment")
        if any(character in str(value) for character in "<>|\\\r\n\t "):
            raise ValueError("public Web base URL must not contain Slack markup delimiters")
        if host == "localhost" or host.endswith(".localhost"):
            raise ValueError("public Web base URL must not use localhost")
        if host.endswith(".trycloudflare.com") or host == "trycloudflare.com":
            raise ValueError("temporary tunnel URLs are not trusted product-Web authorities")
        try:
            address = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            address = None
        if address is not None and (
            address.is_loopback
            or address.is_private
            or address.is_link_local
            or address.is_unspecified
        ):
            raise ValueError("public Web base URL must not use a local network address")
        return value

    @model_validator(mode="after")
    def validate_integrations_and_models(self) -> Self:
        if self.embedding_dimension != ONTOLOGY_EMBEDDING_DIMENSION:
            raise ValueError(
                "embedding dimension does not match ontology migration: "
                f"expected {ONTOLOGY_EMBEDDING_DIMENSION}"
            )
        if self.job_retry_max_seconds < self.job_retry_base_seconds:
            raise ValueError("job retry maximum must be greater than or equal to its base")
        if self.slack_notification_retry_max_seconds < self.slack_notification_retry_base_seconds:
            raise ValueError(
                "Slack notification retry maximum must be greater than or equal to its base"
            )

        reviewer_count = len(self.quality_reviewer_principals)
        reviewer_hash_count = len(self.quality_reviewer_token_hashes)
        if reviewer_count or reviewer_hash_count:
            if reviewer_count != reviewer_hash_count or reviewer_count < 2:
                raise ValueError(
                    "quality review authority requires at least two paired principals and token hashes"
                )
            if len(set(self.quality_reviewer_principals)) != reviewer_count:
                raise ValueError("quality review principals must be distinct")
            if any(
                re.fullmatch(r"[A-Za-z][A-Za-z0-9._:-]{0,127}", principal) is None
                for principal in self.quality_reviewer_principals
            ):
                raise ValueError("quality review principals must be opaque")
            hashes = [value.get_secret_value() for value in self.quality_reviewer_token_hashes]
            if any(re.fullmatch(r"sha256:[a-f0-9]{64}", value) is None for value in hashes):
                raise ValueError("quality reviewer token hashes must be SHA-256 digests")
            if len(set(hashes)) != reviewer_hash_count:
                raise ValueError("quality reviewer token hashes must be distinct")

        if self.automatic_generation_enabled:
            self._require(
                "Automatic generation provider",
                automatic_generation_provider_id=(self.automatic_generation_provider_id),
                automatic_generation_provider_endpoint=(
                    self.automatic_generation_provider_endpoint
                ),
                automatic_generation_provider_model_id=(
                    self.automatic_generation_provider_model_id
                ),
                automatic_generation_provider_auth_token=(
                    self.automatic_generation_provider_auth_token
                ),
            )

        if self.automatic_knowledge_projection_enabled:
            self._require(
                "Automatic knowledge projection",
                ollama_base_url=self.ollama_base_url,
                generation_model=self.generation_model,
                embedding_model=self.embedding_model,
            )

        light_rag_activation_values = (
            self.light_rag_authority_id,
            self.light_rag_auxiliary_target_digest,
            self.light_rag_provider_id,
            self.light_rag_postgres_host,
            self.light_rag_postgres_user,
            self.light_rag_postgres_password,
            self.light_rag_postgres_database,
            self.light_rag_runtime_bootstrap,
            self.light_rag_working_directory_root,
        )
        if not self.light_rag_enabled and (
            self.light_rag_apache_age_available
            or any(value is not None for value in light_rag_activation_values)
        ):
            raise ValueError(
                "LightRAG runtime settings require light_rag_enabled=true"
            )
        if self.light_rag_enabled:
            self._require(
                "LightRAG runtime",
                light_rag_authority_id=self.light_rag_authority_id,
                light_rag_auxiliary_target_digest=(
                    self.light_rag_auxiliary_target_digest
                ),
                light_rag_provider_id=self.light_rag_provider_id,
                light_rag_postgres_host=self.light_rag_postgres_host,
                light_rag_postgres_user=self.light_rag_postgres_user,
                light_rag_postgres_password=self.light_rag_postgres_password,
                light_rag_postgres_database=self.light_rag_postgres_database,
                light_rag_runtime_bootstrap=self.light_rag_runtime_bootstrap,
                light_rag_working_directory_root=(
                    self.light_rag_working_directory_root
                ),
            )
            if not self.light_rag_apache_age_available:
                raise ValueError(
                    "LightRAG runtime requires confirmed Apache AGE capability"
                )

        if self.slack_mode is SlackMode.HTTP:
            self._require(
                "HTTP Slack mode",
                slack_signing_secret=self.slack_signing_secret,
                slack_bot_token=self.slack_bot_token,
                slack_workspace_id=self.slack_workspace_id,
                public_slack_base_url=self.public_slack_base_url,
            )
        elif self.slack_mode is SlackMode.SOCKET:
            self._require(
                "Socket Slack mode",
                slack_app_token=self.slack_app_token,
                slack_bot_token=self.slack_bot_token,
                slack_workspace_id=self.slack_workspace_id,
            )

        object_storage_values = {
            "object_storage_endpoint_url": self.object_storage_endpoint_url,
            "object_storage_bucket": self.object_storage_bucket,
            "object_storage_access_key_id": self.object_storage_access_key_id,
            "object_storage_secret_access_key": self.object_storage_secret_access_key,
        }
        if self.object_storage_enabled or any(
            value is not None for value in object_storage_values.values()
        ):
            self._require("Object storage", **object_storage_values)

        if self.environment == "production":
            if self.slack_mode is SlackMode.DISABLED:
                raise ValueError("Slack integration cannot be disabled in production")
            if not self.slack_allowlist_channel_ids:
                raise ValueError("production requires at least one Slack allowlist channel")
            if not self.object_storage_enabled or self.object_storage_provider != "R2":
                raise ValueError("production requires private R2 object storage")
            if self.object_storage_clamav_host is None:
                raise ValueError("production object storage requires fail-closed ClamAV scanning")
            self._require(
                "Production quality review",
                quality_review_source_seal_key=self.quality_review_source_seal_key,
            )
            if reviewer_count < 2:
                raise ValueError(
                    "production quality review requires at least two configured reviewers"
                )
        return self

    @staticmethod
    def _require(feature: str, **values: object) -> None:
        missing = [name for name, value in values.items() if value is None or value == ""]
        if missing:
            raise ValueError(f"{feature} requires: {', '.join(sorted(missing))}")

    @property
    def sqlalchemy_database_url(self) -> str:
        return str(self.database_url)

    @property
    def quality_reviewer_credentials(self) -> tuple[tuple[str, str], ...]:
        """Return principal/hash pairs without ever materializing raw reviewer tokens."""

        return tuple(
            (principal, token_hash.get_secret_value())
            for principal, token_hash in zip(
                self.quality_reviewer_principals,
                self.quality_reviewer_token_hashes,
                strict=True,
            )
        )


def load_settings() -> Settings:
    """Load and validate a fresh settings object from the environment."""

    return Settings()  # type: ignore[call-arg]
