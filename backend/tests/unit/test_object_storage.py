from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from tc_backend.infrastructure.object_storage import (
    ChecksumMismatch,
    FileSizeMismatch,
    FileTooLarge,
    MalwareDetected,
    MalwareScannerUnavailable,
    MimeContentMismatch,
    MimeTypeNotAllowed,
    ObjectStorageTransportError,
    S3CompatibleObjectStorage,
    ScanResult,
    ScanVerdict,
    UnsafeFilename,
    attachment_content_disposition,
    normalize_filename,
    validate_declaration,
    verify_quarantined_object,
)


class FakeScanner:
    def __init__(self, verdict: ScanVerdict = ScanVerdict.CLEAN) -> None:
        self.verdict = verdict

    def scan(self, path: Path) -> ScanResult:
        assert path.stat().st_mode & 0o077 == 0
        return ScanResult(self.verdict, signature="EICAR" if self.verdict == "INFECTED" else None)


def test_presigned_put_is_method_and_metadata_bound() -> None:
    adapter = S3CompatibleObjectStorage(
        endpoint_url="http://minio:9000",
        browser_endpoint_url="http://127.0.0.1:9000",
        bucket="tc-attachments",
        region="us-east-1",
        access_key_id="tc-local",
        secret_access_key="local-secret-key",
    )
    url, headers, expires_at = adapter.presign_put(
        key="attachments/issue/abc",
        content_type="text/plain",
        sha256="a" * 64,
        expires_seconds=300,
        now=datetime(2026, 8, 24, 5, 0, tzinfo=UTC),
    )

    parts = urlsplit(url)
    query = parse_qs(parts.query)
    assert parts.netloc == "127.0.0.1:9000"
    assert parts.path == "/tc-attachments/attachments/issue/abc"
    assert query["X-Amz-Expires"] == ["300"]
    assert query["X-Amz-SignedHeaders"] == ["content-type;host;x-amz-meta-sha256"]
    assert len(query["X-Amz-Signature"][0]) == 64
    assert headers == {"content-type": "text/plain", "x-amz-meta-sha256": "a" * 64}
    assert expires_at == datetime(2026, 8, 24, 5, 5, tzinfo=UTC)


@pytest.mark.parametrize(
    "filename",
    [
        ".",
        "..",
        "   ",
        "../evidence.txt",
        "folder/evidence.txt",
        "folder\\evidence.txt",
        "C:evidence.txt",
        "bad\x00.txt",
        "hidden\u202e.txt",
        "..／evidence.txt",
        "a" * 256,
    ],
)
def test_unsafe_filename_forms_are_rejected(filename: str) -> None:
    with pytest.raises(UnsafeFilename):
        normalize_filename(filename)


def test_declaration_normalizes_unicode_and_requires_allowed_mime_and_sha256() -> None:
    filename, mime_type, checksum = validate_declaration(
        filename="  evidence.txt  ",
        mime_type="TEXT/PLAIN",
        size_bytes=4,
        sha256="a" * 64,
        allowed_mime_types=("text/plain",),
        max_file_bytes=10,
    )
    assert (filename, mime_type, checksum) == ("evidence.txt", "text/plain", "a" * 64)
    with pytest.raises(ChecksumMismatch):
        validate_declaration(
            filename="evidence.txt",
            mime_type="text/plain",
            size_bytes=4,
            sha256="A" * 64,
            allowed_mime_types=("text/plain",),
            max_file_bytes=10,
        )
    with pytest.raises(MimeTypeNotAllowed):
        validate_declaration(
            filename="evidence.exe",
            mime_type="application/x-msdownload",
            size_bytes=4,
            sha256="a" * 64,
            allowed_mime_types=("text/plain",),
            max_file_bytes=10,
        )
    with pytest.raises(FileSizeMismatch):
        validate_declaration(
            filename="empty.txt",
            mime_type="text/plain",
            size_bytes=0,
            sha256="a" * 64,
            allowed_mime_types=("text/plain",),
            max_file_bytes=10,
        )
    with pytest.raises(FileTooLarge):
        validate_declaration(
            filename="large.txt",
            mime_type="text/plain",
            size_bytes=11,
            sha256="a" * 64,
            allowed_mime_types=("text/plain",),
            max_file_bytes=10,
        )


def test_attachment_content_disposition_is_ascii_rfc5987_and_injection_safe() -> None:
    header = attachment_content_disposition('보고서 "final".txt')

    assert header.startswith('attachment; filename="final_.txt"; filename*=UTF-8\'\'')
    assert "%EB%B3%B4%EA%B3%A0%EC%84%9C%20%22final%22.txt" in header
    assert header.isascii()
    assert "\r" not in header
    assert "\n" not in header

    with pytest.raises(UnsafeFilename):
        attachment_content_disposition("report.txt\r\nX-Evil: injected")


def test_quarantine_verifies_actual_bytes_mime_checksum_and_clean_scan() -> None:
    content = b'{"ok": true}'
    import hashlib

    verified = verify_quarantined_object(
        (content,),
        expected_mime_type="application/json",
        expected_size_bytes=len(content),
        expected_sha256=hashlib.sha256(content).hexdigest(),
        max_file_bytes=1024,
        scanner=FakeScanner(),
    )
    assert verified.sha256 == hashlib.sha256(content).hexdigest()
    assert verified.detected_mime_type == "application/json"

    for binary, mime_type in (
        (b"\x1f\x8b\x08\x00gzip", "application/gzip"),
        (b"\x1aE\xdf\xa3\x01webm", "video/webm"),
    ):
        verified_binary = verify_quarantined_object(
            (binary,),
            expected_mime_type=mime_type,
            expected_size_bytes=len(binary),
            expected_sha256=hashlib.sha256(binary).hexdigest(),
            max_file_bytes=1024,
            scanner=FakeScanner(),
        )
        assert verified_binary.detected_mime_type == mime_type

    with pytest.raises(MimeContentMismatch):
        verify_quarantined_object(
            (content,),
            expected_mime_type="application/pdf",
            expected_size_bytes=len(content),
            expected_sha256=hashlib.sha256(content).hexdigest(),
            max_file_bytes=1024,
            scanner=FakeScanner(),
        )
    with pytest.raises(MalwareDetected):
        verify_quarantined_object(
            (content,),
            expected_mime_type="application/json",
            expected_size_bytes=len(content),
            expected_sha256=hashlib.sha256(content).hexdigest(),
            max_file_bytes=1024,
            scanner=FakeScanner(ScanVerdict.INFECTED),
        )
    with pytest.raises(MalwareScannerUnavailable):
        verify_quarantined_object(
            (content,),
            expected_mime_type="application/json",
            expected_size_bytes=len(content),
            expected_sha256=hashlib.sha256(content).hexdigest(),
            max_file_bytes=1024,
            scanner=None,
        )


def test_list_objects_parses_s3_v2_page(monkeypatch: pytest.MonkeyPatch) -> None:
    response = httpx.Response(
        200,
        content=(
            b"<ListBucketResult xmlns='http://s3.amazonaws.com/doc/2006-03-01/'>"
            b"<IsTruncated>true</IsTruncated><NextContinuationToken>next</NextContinuationToken>"
            b"<Contents><Key>quarantine/a</Key><LastModified>2026-08-24T05:00:00Z</LastModified>"
            b"<Size>4</Size></Contents></ListBucketResult>"
        ),
    )
    observed: list[str] = []

    def fake_request(method: str, url: str, **kwargs: object) -> httpx.Response:
        del kwargs
        assert method == "GET"
        assert "list-type=2" in url
        return response

    monkeypatch.setattr(httpx, "request", fake_request)
    adapter = S3CompatibleObjectStorage(
        endpoint_url="http://minio:9000",
        browser_endpoint_url=None,
        bucket="tc-attachments",
        region="us-east-1",
        access_key_id="tc-local",
        secret_access_key="local-secret-key",
        request_observer=observed.append,
    )
    page = adapter.list_objects(prefix="quarantine/", limit=10)
    assert page.items[0].key == "quarantine/a"
    assert page.items[0].size_bytes == 4
    assert page.next_cursor == "next"
    assert observed == ["CLASS_A_LIST"]


def test_list_objects_rejects_xml_entities(monkeypatch: pytest.MonkeyPatch) -> None:
    response = httpx.Response(
        200,
        content=(
            b'<!DOCTYPE x [<!ENTITY injected "quarantine/forged">]>'
            b"<ListBucketResult><IsTruncated>false</IsTruncated>"
            b"<Contents><Key>&injected;</Key><LastModified>2026-08-24T05:00:00Z</LastModified>"
            b"<Size>4</Size></Contents></ListBucketResult>"
        ),
    )

    monkeypatch.setattr(httpx, "request", lambda *args, **kwargs: response)
    adapter = S3CompatibleObjectStorage(
        endpoint_url="http://minio:9000",
        browser_endpoint_url=None,
        bucket="tc-attachments",
        region="us-east-1",
        access_key_id="tc-local",
        secret_access_key="local-secret-key",
    )

    with pytest.raises(ObjectStorageTransportError, match="object list response was invalid"):
        adapter.list_objects(prefix="quarantine/", limit=10)


def test_bucket_lifecycle_and_inventory_are_provider_neutral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            httpx.Response(
                200,
                content=(
                    b"<ListAllMyBucketsResult xmlns='http://s3.amazonaws.com/doc/2006-03-01/'>"
                    b"<Buckets><Bucket><Name>z-private</Name></Bucket>"
                    b"<Bucket><Name>a-private</Name></Bucket></Buckets>"
                    b"</ListAllMyBucketsResult>"
                ),
            ),
            httpx.Response(404),
            httpx.Response(200),
            httpx.Response(200),
            httpx.Response(204),
            httpx.Response(404),
        ]
    )
    requests: list[tuple[str, str]] = []

    def fake_request(method: str, url: str, **kwargs: object) -> httpx.Response:
        del kwargs
        requests.append((method, url))
        return next(responses)

    monkeypatch.setattr(httpx, "request", fake_request)
    adapter = S3CompatibleObjectStorage(
        endpoint_url="http://minio:9000",
        browser_endpoint_url=None,
        bucket="wave7-private",
        region="us-east-1",
        access_key_id="tc-local",
        secret_access_key="local-secret-key",
    )

    assert adapter.list_buckets() == ("a-private", "z-private")
    assert adapter.bucket_exists() is False
    adapter.create_bucket()
    assert adapter.bucket_exists() is True
    adapter.delete_bucket()
    assert adapter.bucket_exists() is False
    assert [method for method, _ in requests] == ["GET", "HEAD", "PUT", "HEAD", "DELETE", "HEAD"]
