from __future__ import annotations

import hashlib
import os
import time

import httpx
import pytest

from tc_backend.infrastructure.object_storage import (
    ClamAVInstreamScanner,
    MalwareDetected,
    S3CompatibleObjectStorage,
    verify_quarantined_object,
)


def _configuration() -> tuple[str, str, str, str, str, int]:
    endpoint = os.getenv("TC_TEST_MINIO_URL")
    if endpoint is None:
        pytest.skip("TC_TEST_MINIO_URL is required for the opt-in MinIO integration test")
    return (
        endpoint,
        os.getenv("TC_TEST_MINIO_ACCESS_KEY", "tc_local_access"),
        os.getenv("TC_TEST_MINIO_SECRET_KEY", "tc_local_secret_change_me"),
        os.getenv("TC_TEST_MINIO_BUCKET", "tc-attachments"),
        os.getenv("TC_TEST_CLAMAV_HOST", "127.0.0.1"),
        int(os.getenv("TC_TEST_CLAMAV_PORT", "53310")),
    )


def _put(adapter: S3CompatibleObjectStorage, key: str, content: bytes) -> None:
    digest = hashlib.sha256(content).hexdigest()
    url, headers, _ = adapter.presign_put(
        key=key,
        content_type="text/plain",
        sha256=digest,
        expires_seconds=60,
    )
    response = httpx.put(url, headers=headers, content=content, timeout=30)
    response.raise_for_status()


def test_private_minio_lifecycle_and_fail_closed_clamav_scan() -> None:
    endpoint, access_key, secret_key, bucket, scanner_host, scanner_port = _configuration()
    adapter = S3CompatibleObjectStorage(
        endpoint_url=endpoint,
        browser_endpoint_url=endpoint,
        bucket=bucket,
        region="us-east-1",
        access_key_id=access_key,
        secret_access_key=secret_key,
    )
    scanner = ClamAVInstreamScanner(host=scanner_host, port=scanner_port, timeout_seconds=30)
    prefix = f"integration/object-storage/{time.time_ns()}"
    clean_key = f"{prefix}/clean.txt"
    infected_key = f"{prefix}/eicar.txt"
    clean = b"TC MinIO and ClamAV integration sample\n"
    eicar = (
        b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    )
    try:
        _put(adapter, clean_key, clean)
        clean_digest = hashlib.sha256(clean).hexdigest()
        metadata = adapter.head(key=clean_key)
        assert metadata.size_bytes == len(clean)
        assert metadata.content_type == "text/plain"
        assert metadata.sha256 == clean_digest
        assert b"".join(adapter.download(key=clean_key)) == clean
        verified = verify_quarantined_object(
            adapter.download(key=clean_key),
            expected_mime_type="text/plain",
            expected_size_bytes=len(clean),
            expected_sha256=clean_digest,
            max_file_bytes=1024,
            scanner=scanner,
        )
        assert verified.sha256 == clean_digest
        page = adapter.list_objects(prefix=prefix, limit=10)
        assert clean_key in {item.key for item in page.items}

        _put(adapter, infected_key, eicar)
        with pytest.raises(MalwareDetected):
            verify_quarantined_object(
                adapter.download(key=infected_key),
                expected_mime_type="text/plain",
                expected_size_bytes=len(eicar),
                expected_sha256=hashlib.sha256(eicar).hexdigest(),
                max_file_bytes=1024,
                scanner=scanner,
            )

        anonymous = httpx.get(f"{endpoint}/{bucket}/{clean_key}", timeout=10)
        assert anonymous.status_code in {401, 403}
    finally:
        adapter.delete(key=clean_key)
        adapter.delete(key=infected_key)
