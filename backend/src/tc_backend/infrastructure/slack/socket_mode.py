"""Socket Mode connection composition built on the receiver protocol."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Protocol, cast

from tc_backend.infrastructure.slack.models import ReceivedSlackEnvelope
from tc_backend.infrastructure.slack.receivers import (
    SlackSocketModeReceiver,
    SocketModeConnection,
)
from tc_backend.infrastructure.slack.transport import SlackWebApiTransport


class SocketModeConnector(Protocol):
    def __call__(
        self,
        url: str,
    ) -> AbstractAsyncContextManager[SocketModeConnection]: ...


class SlackSocketModeClient:
    """Open a provider URL and receive one acked envelope.

    Long-running worker composition can reuse :class:`SlackSocketModeReceiver`
    against an already-open connection. This small client owns a one-envelope
    connection lifecycle for health checks and integration boundaries.
    """

    def __init__(
        self,
        *,
        web_api: SlackWebApiTransport,
        app_token: str,
        receiver: SlackSocketModeReceiver | None = None,
        connector: SocketModeConnector | None = None,
    ) -> None:
        if not app_token:
            raise ValueError("Slack app token must not be empty")
        self._web_api = web_api
        self._app_token = app_token
        self._receiver = receiver or SlackSocketModeReceiver()
        self._connector = connector or _websockets_connect

    async def receive_one(self) -> ReceivedSlackEnvelope:
        url = self._web_api.open_socket_mode_url(app_token=self._app_token)
        async with self._connector(url) as connection:
            return await self._receiver.receive_once(connection)


def _websockets_connect(url: str) -> AbstractAsyncContextManager[SocketModeConnection]:
    # Imported lazily so disabled/HTTP processes do not initialize WebSocket
    # machinery. `connect` implements the narrow context/recv/send protocol.
    from websockets.asyncio.client import connect

    return cast(
        AbstractAsyncContextManager[SocketModeConnection],
        connect(url, open_timeout=30, ping_interval=20, max_size=2 * 1024 * 1024),
    )
