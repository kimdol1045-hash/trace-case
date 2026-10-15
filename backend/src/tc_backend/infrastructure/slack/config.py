"""Explicit disabled/setup-required boundary for Slack runtime composition."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from tc_backend.config import Settings, SlackMode
from tc_backend.infrastructure.slack.errors import (
    SlackIntegrationDisabled,
    SlackSetupRequired,
)


class SlackRuntimeState(StrEnum):
    DISABLED = "disabled"
    SETUP_REQUIRED = "setup_required"
    CONFIGURED = "configured"


@dataclass(frozen=True, slots=True)
class SlackRuntimeBoundary:
    state: SlackRuntimeState
    missing_settings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SlackRuntimeConfig:
    """Locally validated configuration; it does not claim live credential success."""

    mode: SlackMode
    workspace_id: str
    allowlist_channel_ids: tuple[str, ...]
    bot_token: str = field(repr=False)
    signing_secret: str | None = field(default=None, repr=False)
    app_token: str | None = field(default=None, repr=False)
    public_base_url: str | None = None
    public_web_base_url: str | None = None
    signature_tolerance_seconds: int = 300
    api_base_url: str = "https://slack.com/api"
    max_rate_limit_retries: int = 2
    max_provider_retries: int = 2
    retry_base_seconds: float = 0.25
    generation_notifications_enabled: bool = False
    notification_breaker_open: bool = False
    notification_delivery_lease_seconds: int = 60
    notification_reconcile_window_seconds: int = 86_400
    notification_retry_base_seconds: int = 5
    notification_retry_max_seconds: int = 3_600


def inspect_slack_runtime(settings: Settings) -> SlackRuntimeBoundary:
    if settings.slack_mode is SlackMode.DISABLED:
        return SlackRuntimeBoundary(SlackRuntimeState.DISABLED)
    missing = _missing_settings(settings)
    if missing:
        return SlackRuntimeBoundary(SlackRuntimeState.SETUP_REQUIRED, missing)
    return SlackRuntimeBoundary(SlackRuntimeState.CONFIGURED)


def require_slack_runtime(settings: Settings) -> SlackRuntimeConfig:
    """Compose only local config, keeping disabled and external setup explicit."""

    boundary = inspect_slack_runtime(settings)
    if boundary.state is SlackRuntimeState.DISABLED:
        raise SlackIntegrationDisabled(
            "Slack integration is disabled; select HTTP or Socket Mode to configure it"
        )
    if boundary.state is SlackRuntimeState.SETUP_REQUIRED:
        raise SlackSetupRequired(
            "Slack Setup required: configure " + ", ".join(boundary.missing_settings)
        )
    # The boundary above proves these common values are present. Secret values
    # are exposed only at this final composition point and excluded from repr.
    if settings.slack_bot_token is None or settings.slack_workspace_id is None:
        raise SlackSetupRequired("Slack Setup required: common credential bundle is missing")
    signing_secret = (
        settings.slack_signing_secret.get_secret_value()
        if settings.slack_signing_secret is not None
        else None
    )
    app_token = (
        settings.slack_app_token.get_secret_value()
        if settings.slack_app_token is not None
        else None
    )
    return SlackRuntimeConfig(
        mode=settings.slack_mode,
        workspace_id=settings.slack_workspace_id,
        allowlist_channel_ids=settings.slack_allowlist_channel_ids,
        bot_token=settings.slack_bot_token.get_secret_value(),
        signing_secret=signing_secret,
        app_token=app_token,
        public_base_url=(
            str(settings.public_slack_base_url)
            if settings.public_slack_base_url is not None
            else None
        ),
        public_web_base_url=(
            str(settings.public_web_base_url)
            if settings.public_web_base_url is not None
            else None
        ),
        signature_tolerance_seconds=settings.slack_signature_tolerance_seconds,
        api_base_url=str(settings.slack_api_base_url),
        max_rate_limit_retries=settings.slack_web_api_max_rate_limit_retries,
        max_provider_retries=settings.slack_web_api_max_provider_retries,
        retry_base_seconds=settings.slack_web_api_retry_base_seconds,
        generation_notifications_enabled=settings.slack_generation_notifications_enabled,
        notification_breaker_open=settings.slack_notification_breaker_open,
        notification_delivery_lease_seconds=(
            settings.slack_notification_delivery_lease_seconds
        ),
        notification_reconcile_window_seconds=(
            settings.slack_notification_reconcile_window_seconds
        ),
        notification_retry_base_seconds=settings.slack_notification_retry_base_seconds,
        notification_retry_max_seconds=settings.slack_notification_retry_max_seconds,
    )


def _missing_settings(settings: Settings) -> tuple[str, ...]:
    common: list[tuple[str, object]] = [
        ("slack_bot_token", settings.slack_bot_token),
        ("slack_workspace_id", settings.slack_workspace_id),
    ]
    mode_specific = (
        [
            ("slack_signing_secret", settings.slack_signing_secret),
            ("public_slack_base_url", settings.public_slack_base_url),
        ]
        if settings.slack_mode is SlackMode.HTTP
        else [("slack_app_token", settings.slack_app_token)]
    )
    return tuple(
        name
        for name, value in (*common, *mode_specific)
        if value is None or value == ""
    )
