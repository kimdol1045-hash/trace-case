"""Deployment-owned R2/MinIO runtime and signed upload claim support."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any

from tc_backend.config import Settings
from tc_backend.db.constants import DEFAULT_WORKSPACE_ID
from tc_backend.db.models import AttachmentOwnerType
from tc_backend.infrastructure.object_storage import (
    ClamAVInstreamScanner,
    MalwareScanner,
    ObjectStorageSetupRequired,
    S3CompatibleObjectStorage,
    VerifiedObject,
    verify_quarantined_object,
)


@dataclass(frozen=True, slots=True)
class UploadClaim:
    version: int
    owner_type: str
    owner_id: str
    object_key: str
    file_name: str
    mime_type: str
    size_bytes: int
    sha256: str
    idempotency_key: str
    expires_at: int


@dataclass(frozen=True, slots=True)
class StorageObservation:
    storage_bytes: int
    class_a_requests: int
    class_b_requests: int
    storage_budget_bytes: int
    class_a_request_budget: int
    class_b_request_budget: int
    warning_level: str
    threshold_percent: int | None
    estimated_monthly_cost_usd: float
    estimate_quality: str
    observed_since: datetime


@dataclass(slots=True)
class ObjectStorageRuntime:
    adapter: S3CompatibleObjectStorage | None = None
    scanner: MalwareScanner | None = None
    signing_secret: bytes = b""
    setup_error: str | None = "Object storage is not configured"
    provider: str = "R2"
    bucket: str | None = None
    max_file_bytes: int = 50 * 1024 * 1024
    allowed_mime_types: tuple[str, ...] = ()
    presign_ttl_seconds: int = 300
    temp_directory: Path | None = None
    storage_budget_bytes: int = 10 * 1024**3
    class_a_request_budget: int = 1_000_000
    class_b_request_budget: int = 10_000_000
    storage_usd_per_gb_month: float = 0.0
    class_a_usd_per_million: float = 0.0
    class_b_usd_per_million: float = 0.0
    _class_a_requests: int = 0
    _class_b_requests: int = 0
    _observed_since: datetime = field(default_factory=lambda: datetime.now(UTC))
    _metrics_lock: Lock = field(default_factory=Lock)

    @classmethod
    def from_settings(cls, settings: Settings) -> "ObjectStorageRuntime":
        secret = settings.encryption_master_key.get_secret_value().encode()
        if not settings.object_storage_enabled:
            return cls(signing_secret=secret)
        endpoint = settings.object_storage_endpoint_url
        bucket = settings.object_storage_bucket
        access_key = settings.object_storage_access_key_id
        secret_key = settings.object_storage_secret_access_key
        if endpoint is None or bucket is None or access_key is None or secret_key is None:
            return cls(signing_secret=secret, setup_error="Object storage settings are incomplete")
        scanner: MalwareScanner | None = None
        if settings.object_storage_clamav_host is not None:
            scanner = ClamAVInstreamScanner(
                host=settings.object_storage_clamav_host,
                port=settings.object_storage_clamav_port,
                timeout_seconds=settings.object_storage_clamav_timeout_seconds,
            )
        runtime = cls(
            signing_secret=secret,
            scanner=scanner,
            setup_error=(
                None
                if scanner is not None
                else "Object storage malware scanner settings are incomplete"
            ),
            provider=settings.object_storage_provider,
            bucket=bucket,
            max_file_bytes=settings.object_storage_max_file_bytes,
            allowed_mime_types=settings.object_storage_allowed_mime_types,
            presign_ttl_seconds=settings.object_storage_presign_ttl_seconds,
            temp_directory=settings.object_storage_temp_directory,
            storage_budget_bytes=settings.object_storage_storage_budget_bytes,
            class_a_request_budget=settings.object_storage_class_a_request_budget,
            class_b_request_budget=settings.object_storage_class_b_request_budget,
            storage_usd_per_gb_month=settings.object_storage_storage_usd_per_gb_month,
            class_a_usd_per_million=settings.object_storage_class_a_usd_per_million,
            class_b_usd_per_million=settings.object_storage_class_b_usd_per_million,
        )
        runtime.adapter = S3CompatibleObjectStorage(
            endpoint_url=str(endpoint),
            browser_endpoint_url=(
                str(settings.object_storage_browser_endpoint_url)
                if settings.object_storage_browser_endpoint_url is not None
                else None
            ),
            bucket=bucket,
            region=settings.object_storage_region,
            access_key_id=access_key,
            secret_access_key=secret_key.get_secret_value(),
            request_observer=runtime.observe_request,
        )
        return runtime

    def require_adapter(self) -> S3CompatibleObjectStorage:
        if self.adapter is None:
            raise ObjectStorageSetupRequired(self.setup_error or "Object storage is unavailable")
        return self.adapter

    def require_upload_ready(self) -> S3CompatibleObjectStorage:
        adapter = self.require_adapter()
        if self.scanner is None:
            raise ObjectStorageSetupRequired(
                self.setup_error or "Object storage malware scanner is unavailable"
            )
        return adapter

    def verify_quarantined_upload(self, claim: UploadClaim) -> VerifiedObject:
        adapter = self.require_upload_ready()
        return verify_quarantined_object(
            adapter.download(key=claim.object_key),
            expected_mime_type=claim.mime_type,
            expected_size_bytes=claim.size_bytes,
            expected_sha256=claim.sha256,
            max_file_bytes=self.max_file_bytes,
            scanner=self.scanner,
            temp_directory=self.temp_directory,
        )

    def observe_request(self, request_kind: str) -> None:
        with self._metrics_lock:
            if request_kind.startswith("CLASS_A"):
                self._class_a_requests += 1
            elif request_kind.startswith("CLASS_B"):
                self._class_b_requests += 1

    def observe_direct_put(self) -> None:
        self.observe_request("CLASS_A_DIRECT_PUT")

    def observation(self, *, storage_bytes: int) -> StorageObservation:
        with self._metrics_lock:
            class_a_requests = self._class_a_requests
            class_b_requests = self._class_b_requests
        ratios = (
            storage_bytes / self.storage_budget_bytes,
            class_a_requests / self.class_a_request_budget,
            class_b_requests / self.class_b_request_budget,
        )
        warning_level, threshold_percent = _warning(max(ratios))
        gigabytes = storage_bytes / 1_000_000_000
        estimated_cost = (
            gigabytes * self.storage_usd_per_gb_month
            + class_a_requests / 1_000_000 * self.class_a_usd_per_million
            + class_b_requests / 1_000_000 * self.class_b_usd_per_million
        )
        return StorageObservation(
            storage_bytes=storage_bytes,
            class_a_requests=class_a_requests,
            class_b_requests=class_b_requests,
            storage_budget_bytes=self.storage_budget_bytes,
            class_a_request_budget=self.class_a_request_budget,
            class_b_request_budget=self.class_b_request_budget,
            warning_level=warning_level,
            threshold_percent=threshold_percent,
            estimated_monthly_cost_usd=round(estimated_cost, 6),
            estimate_quality="PROCESS_LOCAL_CONFIGURED_ESTIMATE",
            observed_since=self._observed_since,
        )

    def create_claim(
        self,
        *,
        owner_type: AttachmentOwnerType,
        owner_id: uuid.UUID,
        file_name: str,
        mime_type: str,
        size_bytes: int,
        sha256: str,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> tuple[UploadClaim, str]:
        issued_at = (now or datetime.now(UTC)).astimezone(UTC)
        digest = hmac.new(
            self._secret(),
            f"{owner_type.value}:{owner_id}:{idempotency_key}".encode(),
            hashlib.sha256,
        ).hexdigest()
        object_key = (
            f"quarantine/attachments/{DEFAULT_WORKSPACE_ID}/{owner_type.value.lower()}/"
            f"{owner_id}/{issued_at.year}/{digest}"
        )
        claim = UploadClaim(
            version=1,
            owner_type=owner_type.value,
            owner_id=str(owner_id),
            object_key=object_key,
            file_name=file_name,
            mime_type=mime_type,
            size_bytes=size_bytes,
            sha256=sha256,
            idempotency_key=idempotency_key,
            expires_at=int((issued_at + timedelta(seconds=self.presign_ttl_seconds)).timestamp()),
        )
        return claim, self._encode_claim(claim)

    def decode_claim(self, token: str, *, now: datetime | None = None) -> UploadClaim:
        try:
            payload_part, signature_part = token.split(".", 1)
            payload = _b64decode(payload_part)
            signature = _b64decode(signature_part)
        except (ValueError, UnicodeError) as error:
            raise ValueError("upload token is malformed") from error
        expected = hmac.new(self._secret(), payload, hashlib.sha256).digest()
        # Require the canonical unpadded base64url representation as well as
        # the decoded digest. Otherwise changing unused bits in the final
        # base64 character can produce a different token with identical bytes.
        if not hmac.compare_digest(signature_part, _b64encode(expected)) or not hmac.compare_digest(
            signature, expected
        ):
            raise ValueError("upload token signature is invalid")
        try:
            raw: Any = json.loads(payload)
            claim = UploadClaim(**raw)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("upload token payload is invalid") from error
        current = int((now or datetime.now(UTC)).timestamp())
        if claim.version != 1 or claim.expires_at < current:
            raise ValueError("upload token has expired")
        return claim

    def _encode_claim(self, claim: UploadClaim) -> str:
        payload = json.dumps(asdict(claim), sort_keys=True, separators=(",", ":")).encode()
        signature = hmac.new(self._secret(), payload, hashlib.sha256).digest()
        return f"{_b64encode(payload)}.{_b64encode(signature)}"

    def _secret(self) -> bytes:
        if len(self.signing_secret) < 32:
            raise ObjectStorageSetupRequired("Object storage signing secret is unavailable")
        return self.signing_secret


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _warning(ratio: float) -> tuple[str, int | None]:
    if ratio >= 1:
        return "EXCEEDED", 100
    if ratio >= 0.95:
        return "WARNING_95", 95
    if ratio >= 0.85:
        return "WARNING_85", 85
    if ratio >= 0.70:
        return "WARNING_70", 70
    return "NONE", None
