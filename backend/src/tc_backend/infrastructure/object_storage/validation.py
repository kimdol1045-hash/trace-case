"""Quarantine verification for direct-to-object-storage uploads."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from tc_backend.infrastructure.object_storage.errors import (
    ChecksumMismatch,
    FileSizeMismatch,
    FileTooLarge,
    MimeContentMismatch,
    MimeTypeNotAllowed,
    UnsafeFilename,
)
from tc_backend.infrastructure.object_storage.scanner import MalwareScanner, require_clean_scan

_SNIFF_BYTES = 64 * 1024
_TEXT_EQUIVALENTS = frozenset({"text/plain", "text/markdown", "text/csv"})
_UNSAFE_ASCII_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True, slots=True)
class VerifiedObject:
    size_bytes: int
    sha256: str
    detected_mime_type: str


def normalize_filename(filename: str) -> str:
    normalized = unicodedata.normalize("NFKC", filename).strip()
    if not normalized or normalized in {".", ".."}:
        raise UnsafeFilename("attachment filename must not be empty or relative")
    if len(normalized.encode("utf-8")) > 255:
        raise UnsafeFilename("attachment filename exceeds 255 UTF-8 bytes")
    if any(character in normalized for character in ("/", "\\", "\x00")):
        raise UnsafeFilename("attachment filename must not contain path semantics")
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in normalized):
        raise UnsafeFilename("attachment filename contains a control character")
    if len(normalized) >= 2 and normalized[0].isalpha() and normalized[1] == ":":
        raise UnsafeFilename("attachment filename must not contain a drive prefix")
    return normalized


def attachment_content_disposition(filename: str) -> str:
    """Build an ASCII header with a UTF-8 filename and no header/path semantics."""

    normalized = normalize_filename(filename)
    ascii_name = unicodedata.normalize("NFKD", normalized).encode(
        "ascii", "ignore"
    ).decode("ascii")
    fallback = _UNSAFE_ASCII_FILENAME.sub("_", ascii_name).strip("._")
    if not fallback:
        fallback = "download"
    fallback = fallback[:160]
    encoded = quote(normalized, safe="!#$&+-.^_`|~")
    return f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{encoded}'


def validate_declaration(
    *,
    filename: str,
    mime_type: str,
    size_bytes: int,
    sha256: str,
    allowed_mime_types: Iterable[str],
    max_file_bytes: int,
) -> tuple[str, str, str]:
    safe_filename = normalize_filename(filename)
    normalized_mime = mime_type.lower().strip()
    if normalized_mime not in frozenset(allowed_mime_types):
        raise MimeTypeNotAllowed(f"attachment MIME type is not allowed: {normalized_mime}")
    if size_bytes <= 0:
        raise FileSizeMismatch("attachment size must be positive")
    if size_bytes > max_file_bytes:
        raise FileTooLarge(f"attachment exceeds the {max_file_bytes}-byte limit")
    normalized_sha256 = sha256.strip()
    if len(normalized_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in normalized_sha256
    ):
        raise ChecksumMismatch("attachment checksum must be a lowercase SHA-256 digest")
    return safe_filename, normalized_mime, normalized_sha256


def verify_quarantined_object(
    chunks: Iterable[bytes],
    *,
    expected_mime_type: str,
    expected_size_bytes: int,
    expected_sha256: str,
    max_file_bytes: int,
    scanner: MalwareScanner | None,
    temp_directory: Path | None = None,
) -> VerifiedObject:
    """Download, checksum, sniff, and scan before metadata makes an object readable."""

    temp_path: Path | None = None
    try:
        descriptor, raw_path = tempfile.mkstemp(
            prefix="tc-object-quarantine-",
            suffix=".upload",
            dir=temp_directory,
        )
        os.close(descriptor)
        temp_path = Path(raw_path)
        temp_path.chmod(0o600)
        digest = hashlib.sha256()
        observed_size = 0
        prefix = bytearray()
        with temp_path.open("wb") as target:
            for chunk in chunks:
                if not isinstance(chunk, bytes):
                    raise FileSizeMismatch("object download stream must yield bytes")
                observed_size += len(chunk)
                if observed_size > max_file_bytes:
                    raise FileTooLarge(
                        f"uploaded object exceeds the {max_file_bytes}-byte limit"
                    )
                if len(prefix) < _SNIFF_BYTES:
                    prefix.extend(chunk[: _SNIFF_BYTES - len(prefix)])
                digest.update(chunk)
                target.write(chunk)
        if observed_size != expected_size_bytes:
            raise FileSizeMismatch(
                f"uploaded object size {observed_size} did not match {expected_size_bytes}"
            )
        observed_sha256 = digest.hexdigest()
        if observed_sha256 != expected_sha256:
            raise ChecksumMismatch("uploaded object bytes did not match the claimed SHA-256")
        detected = sniff_mime_type(bytes(prefix))
        compatible_text = {detected, expected_mime_type}.issubset(_TEXT_EQUIVALENTS)
        if detected != expected_mime_type and not compatible_text:
            raise MimeContentMismatch(
                f"declared MIME {expected_mime_type} did not match detected {detected}"
            )
        if expected_mime_type == "application/json":
            try:
                with temp_path.open("r", encoding="utf-8") as source:
                    json.load(source)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise MimeContentMismatch(
                    "declared JSON attachment did not contain valid UTF-8 JSON"
                ) from error
        require_clean_scan(scanner, temp_path)
        return VerifiedObject(observed_size, observed_sha256, detected)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def sniff_mime_type(prefix: bytes) -> str:
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if prefix.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if prefix.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if prefix.startswith(b"%PDF-"):
        return "application/pdf"
    if prefix.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return "application/zip"
    if prefix.startswith(b"\x1f\x8b"):
        return "application/gzip"
    if len(prefix) >= 12 and prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP":
        return "image/webp"
    if len(prefix) >= 12 and prefix[4:8] == b"ftyp":
        return "video/mp4"
    if prefix.startswith(b"\x1aE\xdf\xa3") and b"webm" in prefix[:64].lower():
        return "video/webm"
    if prefix:
        try:
            text = prefix.decode("utf-8")
        except UnicodeDecodeError:
            return "application/octet-stream"
        if "\x00" in text:
            return "application/octet-stream"
        stripped = text.lstrip("\ufeff\t\r\n ")
        if stripped.startswith(("{", "[")):
            return "application/json"
        return "text/plain"
    return "application/octet-stream"
