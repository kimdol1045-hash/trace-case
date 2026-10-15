"""Fail-closed malware scanning for private object-storage attachments."""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from tc_backend.infrastructure.object_storage.errors import (
    MalwareDetected,
    MalwareScannerUnavailable,
)


class ScanVerdict(StrEnum):
    CLEAN = "CLEAN"
    INFECTED = "INFECTED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class ScanResult:
    verdict: ScanVerdict
    signature: str | None = None
    detail: str | None = None


class MalwareScanner(Protocol):
    def scan(self, path: Path) -> ScanResult: ...


def require_clean_scan(scanner: MalwareScanner | None, path: Path) -> None:
    """Only an explicit CLEAN verdict releases a quarantined object."""

    if scanner is None:
        raise MalwareScannerUnavailable(
            "malware scanner is unavailable; uploaded object remains quarantined"
        )
    try:
        result = scanner.scan(path)
    except Exception as error:
        raise MalwareScannerUnavailable(
            "malware scanner request failed; uploaded object remains quarantined"
        ) from error
    if result.verdict is ScanVerdict.INFECTED:
        suffix = f": {result.signature}" if result.signature else ""
        raise MalwareDetected(f"malware scanner blocked the attachment{suffix}")
    if result.verdict is not ScanVerdict.CLEAN:
        raise MalwareScannerUnavailable(
            result.detail or "malware scanner did not return a clean verdict"
        )


class ClamAVInstreamScanner:
    """Minimal ClamAV daemon adapter using the length-framed INSTREAM command."""

    def __init__(
        self,
        *,
        host: str,
        port: int = 3310,
        timeout_seconds: float = 10.0,
        chunk_bytes: int = 64 * 1024,
    ) -> None:
        if not host or not 1 <= port <= 65_535:
            raise ValueError("valid ClamAV host and port are required")
        if timeout_seconds <= 0 or chunk_bytes <= 0:
            raise ValueError("ClamAV timeout and chunk size must be positive")
        self._host = host
        self._port = port
        self._timeout_seconds = timeout_seconds
        self._chunk_bytes = chunk_bytes

    def scan(self, path: Path) -> ScanResult:
        try:
            with socket.create_connection(
                (self._host, self._port), timeout=self._timeout_seconds
            ) as connection:
                connection.settimeout(self._timeout_seconds)
                connection.sendall(b"zINSTREAM\0")
                with path.open("rb") as source:
                    while chunk := source.read(self._chunk_bytes):
                        connection.sendall(struct.pack("!I", len(chunk)))
                        connection.sendall(chunk)
                connection.sendall(struct.pack("!I", 0))
                response = connection.recv(4096).rstrip(b"\0\r\n").decode(
                    "utf-8", errors="replace"
                )
        except OSError as error:
            return ScanResult(
                ScanVerdict.UNAVAILABLE,
                detail=f"ClamAV unavailable: {error.__class__.__name__}",
            )
        if response.endswith("OK"):
            return ScanResult(ScanVerdict.CLEAN)
        if response.endswith("FOUND"):
            signature = response.removeprefix("stream:").removesuffix("FOUND").strip()
            return ScanResult(ScanVerdict.INFECTED, signature=signature or None)
        return ScanResult(
            ScanVerdict.UNAVAILABLE,
            detail="ClamAV returned an unrecognized scan response",
        )
