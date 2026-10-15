from __future__ import annotations

from pathlib import Path

import pytest

from tc_backend.config import Settings, SlackMode
from tc_backend.infrastructure.slack.config import (
    SlackRuntimeState,
    inspect_slack_runtime,
    require_slack_runtime,
)
from tc_backend.infrastructure.slack.errors import SlackIntegrationDisabled, SlackSetupRequired

ROOT = Path(__file__).resolve().parents[4]
VALID_VALUES = {
    "database_url": "postgresql+psycopg://tc:tc@localhost:5432/tc",
    "encryption_master_key": "x" * 32,
}


def test_disabled_and_setup_required_are_distinct_without_claiming_live_success() -> None:
    disabled = Settings(_env_file=None, **VALID_VALUES)
    assert inspect_slack_runtime(disabled).state is SlackRuntimeState.DISABLED
    assert disabled.slack_generation_notifications_enabled is False
    with pytest.raises(SlackIntegrationDisabled):
        require_slack_runtime(disabled)

    incomplete = disabled.model_copy(update={"slack_mode": SlackMode.HTTP})
    boundary = inspect_slack_runtime(incomplete)
    assert boundary.state is SlackRuntimeState.SETUP_REQUIRED
    assert "slack_signing_secret" in boundary.missing_settings
    with pytest.raises(SlackSetupRequired):
        require_slack_runtime(incomplete)


def test_configured_boundary_only_means_local_values_are_present_and_masks_secrets() -> None:
    settings = Settings(
        _env_file=None,
        **(
            VALID_VALUES
            | {
                "slack_mode": "socket",
                "slack_app_token": "xapp-test",
                "slack_bot_token": "xoxb-test",
                "slack_workspace_id": "T123",
            }
        ),
    )

    assert inspect_slack_runtime(settings).state is SlackRuntimeState.CONFIGURED
    runtime = require_slack_runtime(settings)
    assert runtime.mode is SlackMode.SOCKET
    assert "xapp-test" not in repr(runtime)
    assert "xoxb-test" not in repr(runtime)


def test_manifest_has_only_public_channel_collection_permissions() -> None:
    manifest = (ROOT / "deploy/slack/app-manifest.yaml").read_text(encoding="utf-8")

    assert "- channels:history" in manifest
    assert "- message.channels" in manifest
    assert "groups:history" not in manifest
    assert "message.groups" not in manifest
    assert "socket_mode_enabled: false" in manifest
    assert "- chat:write" in manifest
    assert "chat:write.public" not in manifest
    assert "tc_generation_terminal_notification_v1" in manifest


def test_public_web_base_url_rejects_untrusted_authorities() -> None:
    trusted = Settings(
        _env_file=None,
        **(VALID_VALUES | {"public_web_base_url": "https://tc.example/app"}),
    )
    assert str(trusted.public_web_base_url) == "https://tc.example/app"

    for untrusted in (
        "http://tc.example",
        "https://user:secret@tc.example",
        "https://tc.example?redirect=https://evil.example",
        "https://localhost",
        "https://127.0.0.1",
        "https://preview.trycloudflare.com",
    ):
        with pytest.raises(ValueError):
            Settings(
                _env_file=None,
                **(VALID_VALUES | {"public_web_base_url": untrusted}),
            )


def test_install_runbook_separates_http_socket_and_live_evidence() -> None:
    runbook = (ROOT / "deploy/slack/README.md").read_text(encoding="utf-8")
    example = (ROOT / "deploy/slack/http.env.example").read_text(encoding="utf-8")

    assert "TC_SLACK_MODE=http" in runbook
    assert "TC_SLACK_MODE=socket" in runbook
    assert "POST /integrations/slack/events" in runbook
    assert "connections:write" in runbook
    assert "channel은 기본 거부" in runbook
    assert "live App 설치나 자동 수집 성공을 주장하지 않는다" in runbook
    assert "TC_SLACK_MODE=disabled" in example
    assert "TC_SLACK_GENERATION_NOTIFICATIONS_ENABLED=false" in example
