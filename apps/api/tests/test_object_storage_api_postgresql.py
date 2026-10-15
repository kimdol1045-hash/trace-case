from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from tc_api.main import create_app
from tc_api.object_storage_runtime import ObjectStorageRuntime
from tc_backend.db.constants import DEFAULT_WORKSPACE_ID
from tc_backend.infrastructure.object_storage import (
    ObjectMetadata,
    ObjectNotFound,
    ScanResult,
    ScanVerdict,
    StoredObject,
    StoredObjectPage,
    ObjectStorageTransportError,
)

pytestmark = pytest.mark.postgres


class FakeScanner:
    verdict = ScanVerdict.CLEAN

    def scan(self, path: Path) -> ScanResult:
        assert path.exists()
        return ScanResult(self.verdict, signature="EICAR" if self.verdict == "INFECTED" else None)


class FakeObjectStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str, str, datetime]] = {}

    def presign_put(
        self,
        *,
        key: str,
        content_type: str,
        sha256: str,
        expires_seconds: int,
    ) -> tuple[str, dict[str, str], datetime]:
        return (
            f"https://private.invalid/{key}",
            {"content-type": content_type, "x-amz-meta-sha256": sha256},
            datetime.now(UTC) + timedelta(seconds=expires_seconds),
        )

    def put(
        self,
        *,
        key: str,
        content: bytes,
        content_type: str,
        sha256_metadata: str,
        modified_at: datetime | None = None,
    ) -> None:
        self.objects[key] = (
            content,
            content_type,
            sha256_metadata,
            modified_at or datetime.now(UTC),
        )

    def head(self, *, key: str) -> ObjectMetadata:
        if key not in self.objects:
            raise ObjectNotFound(key)
        content, content_type, sha256, _ = self.objects[key]
        return ObjectMetadata(key, content_type, len(content), sha256)

    def download(self, *, key: str) -> Iterator[bytes]:
        if key not in self.objects:
            raise ObjectNotFound(key)
        yield self.objects[key][0]

    def delete(self, *, key: str) -> None:
        self.objects.pop(key, None)

    def list_objects(
        self,
        *,
        prefix: str,
        cursor: str | None = None,
        limit: int = 1000,
    ) -> StoredObjectPage:
        del cursor
        items = tuple(
            StoredObject(key, len(content), modified_at)
            for key, (content, _mime, _sha256, modified_at) in sorted(self.objects.items())
            if key.startswith(prefix)
        )
        return StoredObjectPage(items[:limit], None)


class FailingDeleteObjectStorage(FakeObjectStorage):
    fail_deletes = True

    def delete(self, *, key: str) -> None:
        if self.fail_deletes:
            raise ObjectStorageTransportError("injected object-delete outage")
        super().delete(key=key)


def test_private_object_storage_lifecycle_operations_and_legacy_policy(
    clean_api_database: Engine,
) -> None:
    adapter = FakeObjectStorage()
    scanner = FakeScanner()
    runtime = ObjectStorageRuntime(
        adapter=adapter,  # type: ignore[arg-type]
        scanner=scanner,
        signing_secret=b"x" * 32,
        setup_error=None,
        provider="MINIO",
        bucket="tc-attachments",
        allowed_mime_types=("text/plain", "application/json"),
        storage_budget_bytes=10,
        class_a_request_budget=10,
        class_b_request_budget=10,
    )
    application = create_app(
        validate_database=False,
        database_engine=clean_api_database,
        object_storage_runtime=runtime,
    )
    with TestClient(application) as client:
        issue = client.post(
            "/api/issues",
            json={
                "idempotencyKey": "storage-issue-001",
                "summary": "Storage evidence",
                "precondition": "Private storage is ready.",
                "steps": [
                    {
                        "sequence": 1,
                        "action": "Upload evidence.",
                        "expectedResult": "Evidence is private.",
                    }
                ],
                "expectedResult": "Evidence is private.",
                "actualResult": "Evidence is missing.",
                "severity": "HIGH",
            },
        ).json()
        content = b"private evidence\n"
        checksum = hashlib.sha256(content).hexdigest()
        upload_key = "storage-upload-001"
        created_session = client.post(
            f"/api/issues/{issue['id']}/attachment-upload-sessions",
            headers={"Idempotency-Key": upload_key},
            json={
                "idempotencyKey": upload_key,
                "filename": "evidence.txt",
                "declaredMimeType": "text/plain",
                "declaredSizeBytes": len(content),
                "clientSha256": checksum,
            },
        )
        assert created_session.status_code == 201, created_session.text
        upload = created_session.json()
        assert upload["objectKey"].startswith("quarantine/attachments/")
        adapter.put(
            key=upload["objectKey"],
            content=content,
            content_type="text/plain",
            sha256_metadata=checksum,
        )
        completed = client.post(
            "/api/attachment-upload-sessions/complete",
            headers={"Idempotency-Key": upload_key},
            json={"idempotencyKey": upload_key, "uploadToken": upload["uploadToken"]},
        )
        assert completed.status_code == 201, completed.text
        attachment = completed.json()["attachment"]
        assert attachment["storageProvider"] == "R2"
        assert attachment["scanStatus"] == "CLEAN"
        download = client.get(attachment["proxyPath"])
        assert download.content == content
        assert download.headers["cache-control"] == "private, no-store"
        assert download.headers["content-disposition"] == (
            'attachment; filename="evidence.txt"; '
            "filename*=UTF-8''evidence.txt"
        )
        assert download.headers["content-length"] == str(len(content))
        assert download.headers["content-type"].startswith("text/plain")
        assert download.headers["x-content-type-options"] == "nosniff"

        status_response = client.get("/api/integrations/object-storage/status").json()
        assert status_response["status"] == "READY"
        assert status_response["privacy"] == "PRIVATE"
        assert status_response["uploadTransport"] == "PRESIGNED_PUT"
        assert status_response["downloadTransport"] == "BACKEND_PROXY"
        assert status_response["scanPolicy"] == "FAIL_CLOSED_CLAMAV"
        assert status_response["legacyRowPolicy"] == "MIGRATION_REQUIRED"
        observations = client.get("/api/integrations/object-storage/observations").json()
        assert observations["warning"]["level"] == "EXCEEDED"
        assert observations["estimateQuality"] == "PROCESS_LOCAL_CONFIGURED_ESTIMATE"

        metadata = client.post(
            "/api/integrations/object-storage/verify-metadata",
            json={"dryRun": False, "limit": 10},
        )
        assert metadata.status_code == 200, metadata.text
        assert metadata.json()["consistentCount"] == 1
        restore = client.post(
            "/api/integrations/object-storage/restore-sample",
            json={"sampleSize": 5, "maxBytesPerObject": 1024},
        )
        assert restore.status_code == 200, restore.text
        assert restore.json()["verifiedCount"] == 1

        content_bytes, content_type, _sha256_metadata, modified_at = adapter.objects[
            upload["objectKey"]
        ]
        adapter.objects[upload["objectKey"]] = (
            content_bytes,
            content_type,
            "f" * 64,
            modified_at,
        )
        mismatch_check = client.post(
            "/api/integrations/object-storage/verify-metadata",
            json={"dryRun": False, "limit": 10},
        )
        assert mismatch_check.status_code == 200, mismatch_check.text
        assert mismatch_check.json()["mismatchCount"] == 1
        adapter.objects[upload["objectKey"]] = (
            content_bytes,
            content_type,
            checksum,
            modified_at,
        )
        repaired_check = client.post(
            "/api/integrations/object-storage/verify-metadata",
            json={"dryRun": False, "limit": 10},
        )
        assert repaired_check.status_code == 200, repaired_check.text
        assert repaired_check.json()["consistentCount"] == 1

        orphan_key = f"quarantine/attachments/{DEFAULT_WORKSPACE_ID}/orphan"
        adapter.put(
            key=orphan_key,
            content=b"orphan",
            content_type="text/plain",
            sha256_metadata=hashlib.sha256(b"orphan").hexdigest(),
            modified_at=datetime.now(UTC) - timedelta(days=2),
        )
        orphan = client.post(
            "/api/integrations/object-storage/reconcile-orphans",
            json={
                "idempotencyKey": "storage-orphan-001",
                "dryRun": False,
                "olderThan": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
                "limit": 100,
            },
        )
        assert orphan.status_code == 200, orphan.text
        assert orphan.json()["deletedCount"] == 1
        assert orphan_key not in adapter.objects

        issue_uuid = uuid.UUID(issue["id"].removeprefix("issue_"))
        legacy_id = uuid.uuid4()
        with clean_api_database.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO attachments "
                    "(id, workspace_id, owner_type, owner_id, drive_file_id, object_key, "
                    "logical_path, file_name, checksum, mime_type, size_bytes, storage_provider) "
                    "VALUES (:id, '00000000-0000-7000-8000-000000000100', 'ISSUE', :owner, "
                    "'legacy-file', 'legacy-file', 'legacy/path', 'legacy.txt', :checksum, "
                    "'text/plain', 4, 'GOOGLE_DRIVE')"
                ),
                {"id": legacy_id, "owner": issue_uuid, "checksum": hashlib.sha256(b"old\n").hexdigest()},
            )
        inventory = client.get(
            "/api/integrations/object-storage/legacy-attachments"
        ).json()
        assert inventory["total"] == 1
        assert inventory["nextCursor"] is None
        assert inventory["items"][0]["migrationState"] == "MIGRATION_REQUIRED"
        legacy_content = client.get(f"/api/attachments/attachment_{legacy_id.hex}/content")
        assert legacy_content.status_code == 409
        assert legacy_content.json()["code"] == "LEGACY_ATTACHMENT_MIGRATION_REQUIRED"
        assert legacy_content.json()["details"] == {
            "attachmentId": f"attachment_{legacy_id.hex}",
            "migrationRequired": True,
        }

        listed = client.get(
            "/api/attachments", params={"issueIds": issue["id"]}
        ).json()["items"]
        latest = next(item for item in listed if item["id"] == attachment["id"])
        deleted = client.request(
            "DELETE",
            f"/api/attachments/{attachment['id']}",
            json={
                "baseResourceVersion": latest["resourceVersion"],
                "reason": "Storage lifecycle test cleanup",
            },
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["storageDeleteState"] == "DELETED"


def test_fail_closed_scan_and_actual_checksum_validation(clean_api_database: Engine) -> None:
    adapter = FakeObjectStorage()
    scanner = FakeScanner()
    runtime = ObjectStorageRuntime(
        adapter=adapter,  # type: ignore[arg-type]
        scanner=scanner,
        signing_secret=b"x" * 32,
        setup_error=None,
        allowed_mime_types=("text/plain",),
    )
    application = create_app(
        validate_database=False,
        database_engine=clean_api_database,
        object_storage_runtime=runtime,
    )
    with TestClient(application) as client:
        issue = client.post(
            "/api/issues",
            json={
                "idempotencyKey": "scan-issue-001",
                "summary": "Scan evidence",
                "precondition": "Scanner is ready.",
                "steps": [{"sequence": 1, "action": "Upload.", "expectedResult": "Blocked."}],
                "expectedResult": "Blocked.",
                "actualResult": "Pending.",
                "severity": "HIGH",
            },
        ).json()
        declared = b"declared\n"
        checksum = hashlib.sha256(declared).hexdigest()
        upload = client.post(
            f"/api/issues/{issue['id']}/attachment-upload-sessions",
            json={
                "idempotencyKey": "scan-upload-001",
                "filename": "scan.txt",
                "declaredMimeType": "text/plain",
                "declaredSizeBytes": len(declared),
                "clientSha256": checksum,
            },
        ).json()
        tampered = b"tampered\n"
        adapter.put(
            key=upload["objectKey"],
            content=tampered,
            content_type="text/plain",
            sha256_metadata=checksum,
        )
        mismatch = client.post(
            "/api/attachment-upload-sessions/complete",
            json={
                "idempotencyKey": "scan-upload-001",
                "uploadToken": upload["uploadToken"],
            },
        )
        assert mismatch.status_code == 422
        assert mismatch.json()["code"] == "ATTACHMENT_CHECKSUM_MISMATCH"
        assert upload["objectKey"] not in adapter.objects

        head_key = "scan-upload-head-metadata-001"
        head_mismatch = client.post(
            f"/api/issues/{issue['id']}/attachment-upload-sessions",
            json={
                "idempotencyKey": head_key,
                "filename": "head.txt",
                "declaredMimeType": "text/plain",
                "declaredSizeBytes": len(declared),
                "clientSha256": checksum,
            },
        ).json()
        adapter.put(
            key=head_mismatch["objectKey"],
            content=declared,
            content_type="text/plain",
            sha256_metadata="f" * 64,
        )
        head_response = client.post(
            "/api/attachment-upload-sessions/complete",
            json={
                "idempotencyKey": head_key,
                "uploadToken": head_mismatch["uploadToken"],
            },
        )
        assert head_response.status_code == 422
        assert head_response.json()["code"] == "ATTACHMENT_CHECKSUM_MISMATCH"
        assert head_mismatch["objectKey"] not in adapter.objects

        for suffix, verdict, expected_status, expected_code, should_remain in (
            ("infected", ScanVerdict.INFECTED, 422, "ATTACHMENT_MALWARE_DETECTED", False),
            ("unavailable", ScanVerdict.UNAVAILABLE, 503, "ATTACHMENT_SCAN_UNAVAILABLE", True),
        ):
            idempotency_key = f"scan-upload-{suffix}-001"
            quarantined = client.post(
                f"/api/issues/{issue['id']}/attachment-upload-sessions",
                json={
                    "idempotencyKey": idempotency_key,
                    "filename": f"{suffix}.txt",
                    "declaredMimeType": "text/plain",
                    "declaredSizeBytes": len(declared),
                    "clientSha256": checksum,
                },
            ).json()
            adapter.put(
                key=quarantined["objectKey"],
                content=declared,
                content_type="text/plain",
                sha256_metadata=checksum,
            )
            scanner.verdict = verdict
            response = client.post(
                "/api/attachment-upload-sessions/complete",
                json={
                    "idempotencyKey": idempotency_key,
                    "uploadToken": quarantined["uploadToken"],
                },
            )
            assert response.status_code == expected_status
            assert response.json()["code"] == expected_code
            assert (quarantined["objectKey"] in adapter.objects) is should_remain


def test_issue_permanent_delete_fails_closed_then_purges_attachment_object(
    clean_api_database: Engine,
) -> None:
    adapter = FailingDeleteObjectStorage()
    runtime = ObjectStorageRuntime(
        adapter=adapter,  # type: ignore[arg-type]
        scanner=FakeScanner(),
        signing_secret=b"p" * 32,
        setup_error=None,
        provider="MINIO",
        bucket="tc-attachments",
        allowed_mime_types=("text/plain",),
        storage_budget_bytes=10_000,
        class_a_request_budget=100,
        class_b_request_budget=100,
    )
    application = create_app(
        validate_database=False,
        database_engine=clean_api_database,
        object_storage_runtime=runtime,
    )
    with TestClient(application) as client:
        issue = client.post(
            "/api/issues",
            json={
                "idempotencyKey": "purge-storage-issue-001",
                "summary": "Purge private evidence",
                "precondition": "Private evidence exists.",
                "steps": [
                    {
                        "sequence": 1,
                        "action": "Delete the issue.",
                        "expectedResult": "The object is removed first.",
                    }
                ],
                "expectedResult": "The object is removed first.",
                "actualResult": "The issue is retained for cleanup.",
                "severity": "HIGH",
            },
        ).json()
        content = b"retention evidence\n"
        checksum = hashlib.sha256(content).hexdigest()
        upload_key = "purge-storage-upload-001"
        upload = client.post(
            f"/api/issues/{issue['id']}/attachment-upload-sessions",
            json={
                "idempotencyKey": upload_key,
                "filename": "retention.txt",
                "declaredMimeType": "text/plain",
                "declaredSizeBytes": len(content),
                "clientSha256": checksum,
            },
        ).json()
        adapter.put(
            key=upload["objectKey"],
            content=content,
            content_type="text/plain",
            sha256_metadata=checksum,
        )
        completed = client.post(
            "/api/attachment-upload-sessions/complete",
            json={
                "idempotencyKey": upload_key,
                "uploadToken": upload["uploadToken"],
            },
        )
        assert completed.status_code == 201, completed.text

        deleted = client.request(
            "DELETE",
            f"/api/issues/{issue['id']}",
            json={"baseResourceVersion": 1, "reason": "Retention cleanup."},
        ).json()
        failed = client.request(
            "DELETE",
            f"/api/issues/{issue['id']}/permanent",
            json={
                "baseResourceVersion": deleted["resourceVersion"],
                "reason": "Retention period expired.",
            },
        )
        assert failed.status_code == 503, failed.text
        assert failed.json()["code"] == "OBJECT_STORAGE_UNAVAILABLE"
        assert upload["objectKey"] in adapter.objects
        assert client.get(
            f"/api/issues/{issue['id']}", params={"includeDeleted": "true"}
        ).status_code == 200
        with clean_api_database.connect() as connection:
            failed_state = connection.execute(
                text(
                    "SELECT deletion_status FROM attachments "
                    "WHERE owner_id = :owner_id"
                ),
                {"owner_id": uuid.UUID(issue["id"].removeprefix("issue_"))},
            ).scalar_one()
        assert failed_state == "FAILED"

        adapter.fail_deletes = False
        purged = client.request(
            "DELETE",
            f"/api/issues/{issue['id']}/permanent",
            json={
                "baseResourceVersion": deleted["resourceVersion"],
                "reason": "Retry after storage recovery.",
            },
        )
        assert purged.status_code == 200, purged.text
        assert purged.json()["deletedAttachmentCount"] == 1
        assert upload["objectKey"] not in adapter.objects

    decoded = uuid.UUID(issue["id"].removeprefix("issue_"))
    with clean_api_database.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM attachments WHERE owner_id = :owner_id"),
            {"owner_id": decoded},
        ).scalar_one() == 0


def test_cycle_permanent_delete_purges_attempt_attachment_object(
    clean_api_database: Engine,
) -> None:
    adapter = FakeObjectStorage()
    runtime = ObjectStorageRuntime(
        adapter=adapter,  # type: ignore[arg-type]
        scanner=FakeScanner(),
        signing_secret=b"c" * 32,
        setup_error=None,
        provider="MINIO",
        bucket="tc-attachments",
        allowed_mime_types=("text/plain",),
        storage_budget_bytes=10_000,
        class_a_request_budget=100,
        class_b_request_budget=100,
    )
    application = create_app(
        validate_database=False,
        database_engine=clean_api_database,
        object_storage_runtime=runtime,
    )
    with TestClient(application) as client:
        category = client.post(
            "/api/folders",
            json={"parentId": None, "name": "Attempt purge", "sortOrder": 0},
        ).json()["rootFolderId"]
        work_item = client.post(
            "/api/work-items",
            json={
                "key": "WORK-903",
                "categoryId": category,
                "summary": "Attempt evidence retention",
            },
        ).json()
        test_case = client.post(
            "/api/test-cases",
            json={
                "workItemId": work_item["id"],
                "status": "ACTIVE",
                "summary": "Attempt with private evidence",
                "precondition": "The cycle is running.",
                "expectedResultMode": "PER_STEP",
                "priority": "HIGH",
                "labels": ["retention"],
                "steps": [
                    {
                        "sequence": 1,
                        "action": "Capture evidence.",
                        "expectedResult": "Evidence is private.",
                    }
                ],
                "source": {"type": "API"},
            },
        )
        assert test_case.status_code == 201, test_case.text
        cycle = client.post(
            "/api/cycles",
            json={
                "name": "Attempt evidence purge",
                "description": "Object deletion fixture",
                "startDate": "2026-08-25",
                "scope": {
                    "type": "WORK_ITEM",
                    "workItemId": work_item["id"],
                    "workItemKey": work_item["key"],
                },
            },
        ).json()
        item_id = client.get(f"/api/cycles/{cycle['id']}/items").json()["items"][0][
            "id"
        ]
        started = client.post(
            f"/api/cycles/{cycle['id']}/start",
            json={"baseResourceVersion": 1},
        )
        assert started.status_code == 200, started.text
        attempt = client.post(
            f"/api/cycle-items/{item_id}/attempts", json={"platform": "WEB"}
        )
        assert attempt.status_code == 201, attempt.text

        content = b"attempt evidence\n"
        checksum = hashlib.sha256(content).hexdigest()
        upload_key = "purge-attempt-upload-001"
        upload = client.post(
            f"/api/execution-attempts/{attempt.json()['id']}/attachment-upload-sessions",
            json={
                "idempotencyKey": upload_key,
                "filename": "attempt.txt",
                "declaredMimeType": "text/plain",
                "declaredSizeBytes": len(content),
                "clientSha256": checksum,
            },
        ).json()
        adapter.put(
            key=upload["objectKey"],
            content=content,
            content_type="text/plain",
            sha256_metadata=checksum,
        )
        completed = client.post(
            "/api/attachment-upload-sessions/complete",
            json={
                "idempotencyKey": upload_key,
                "uploadToken": upload["uploadToken"],
            },
        )
        assert completed.status_code == 201, completed.text

        current_cycle = client.get(f"/api/cycles/{cycle['id']}").json()
        deleted = client.request(
            "DELETE",
            f"/api/cycles/{cycle['id']}",
            json={
                "baseResourceVersion": current_cycle["resourceVersion"],
                "reason": "Retention cleanup.",
            },
        )
        assert deleted.status_code == 200, deleted.text
        deleted_detail = client.get(
            f"/api/cycles/{cycle['id']}", params={"includeDeleted": "true"}
        ).json()
        purged = client.request(
            "DELETE",
            f"/api/cycles/{cycle['id']}/permanent",
            json={
                "baseResourceVersion": deleted_detail["resourceVersion"],
                "reason": "Retention period expired.",
            },
        )
        assert purged.status_code == 200, purged.text
        assert purged.json()["deletedAttachmentCount"] == 1
        assert upload["objectKey"] not in adapter.objects

    decoded_attempt = uuid.UUID(attempt.json()["id"].removeprefix("attempt_"))
    with clean_api_database.connect() as connection:
        counts = connection.execute(
            text(
                "SELECT "
                "(SELECT count(*) FROM execution_attempts WHERE id = :id) AS attempts, "
                "(SELECT count(*) FROM attachments WHERE owner_id = :id) AS attachments"
            ),
            {"id": decoded_attempt},
        ).mappings().one()
    assert counts == {"attempts": 0, "attachments": 0}
