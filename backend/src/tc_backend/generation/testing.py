"""Deterministic scripted transport for generation adapter tests."""

from __future__ import annotations

from collections.abc import Iterable

from tc_backend.generation.transport import (
    GenerationTransportRequest,
    GenerationTransportResponse,
)


class ScriptedGenerationTransport:
    """Return or raise scripted outcomes while retaining redacted requests."""

    def __init__(
        self,
        outcomes: Iterable[GenerationTransportResponse | Exception],
    ) -> None:
        self._outcomes = list(outcomes)
        self.requests: list[GenerationTransportRequest] = []

    async def send(self, request: GenerationTransportRequest) -> GenerationTransportResponse:
        self.requests.append(request)
        if not self._outcomes:
            raise AssertionError("scripted generation transport has no outcome")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


# Friendly short name for local tests and downstream adapter contract tests.
FakeGenerationTransport = ScriptedGenerationTransport

