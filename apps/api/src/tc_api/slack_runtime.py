"""Local Slack API composition without claiming external connectivity."""

from __future__ import annotations

from dataclasses import dataclass

from tc_backend.config import Settings, SlackMode
from tc_backend.infrastructure.slack.config import (
    SlackRuntimeState,
    inspect_slack_runtime,
    require_slack_runtime,
)
from tc_backend.infrastructure.slack.receivers import SlackHttpEventsReceiver
from tc_backend.infrastructure.slack.signature import SlackRequestVerifier


@dataclass(frozen=True, slots=True)
class SlackApiRuntime:
    state: SlackRuntimeState
    mode: SlackMode
    workspace_id: str | None = None
    app_id: str = "ASETUP"
    http_receiver: SlackHttpEventsReceiver | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> "SlackApiRuntime":
        boundary = inspect_slack_runtime(settings)
        if boundary.state is not SlackRuntimeState.CONFIGURED:
            return cls(
                state=boundary.state,
                mode=settings.slack_mode,
                workspace_id=settings.slack_workspace_id,
            )
        config = require_slack_runtime(settings)
        receiver = None
        if config.mode is SlackMode.HTTP and config.signing_secret is not None:
            receiver = SlackHttpEventsReceiver(
                SlackRequestVerifier(
                    config.signing_secret,
                    tolerance_seconds=config.signature_tolerance_seconds,
                )
            )
        return cls(
            state=SlackRuntimeState.CONFIGURED,
            mode=config.mode,
            workspace_id=config.workspace_id,
            http_receiver=receiver,
        )

    @classmethod
    def disabled(cls) -> "SlackApiRuntime":
        return cls(state=SlackRuntimeState.DISABLED, mode=SlackMode.DISABLED)
