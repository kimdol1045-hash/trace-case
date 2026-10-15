"""Exercise the live API → presigned PUT → private download/delete path."""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import UTC, datetime

import httpx


def main() -> None:
    api = os.getenv("TC_SMOKE_API", "http://127.0.0.1:8000").rstrip("/")
    keep_attachment = os.getenv("TC_SMOKE_KEEP_ATTACHMENT") == "1"
    content = b"TC private object storage smoke\n"
    sha256 = hashlib.sha256(content).hexdigest()
    key = f"object-storage-smoke-{time.time_ns()}"
    with httpx.Client(timeout=30) as client:
        issues = client.get(f"{api}/api/issues", params={"limit": 1})
        issues.raise_for_status()
        issue_items = issues.json()["items"]
        created_issue = not issue_items
        if created_issue:
            issue = client.post(
                f"{api}/api/issues",
                json={
                    "idempotencyKey": f"{key}-issue",
                    "summary": "Object-storage live smoke",
                    "precondition": "The private storage stack is ready.",
                    "steps": [
                        {
                            "sequence": 1,
                            "action": "Upload and restore a private attachment.",
                            "expectedResult": "The attachment checksum is verified.",
                        }
                    ],
                    "expectedResult": "The attachment checksum is verified.",
                    "actualResult": "Verification is pending.",
                    "severity": "MEDIUM",
                },
            )
            issue.raise_for_status()
            issue_id = issue.json()["id"]
        else:
            issue_id = issue_items[0]["id"]
        session = client.post(
            f"{api}/api/issues/{issue_id}/attachment-upload-sessions",
            headers={"Idempotency-Key": key},
            json={
                "idempotencyKey": key,
                "filename": "object-storage-smoke.txt",
                "declaredMimeType": "text/plain",
                "declaredSizeBytes": len(content),
                "clientSha256": sha256,
            },
        )
        session.raise_for_status()
        upload_session = session.json()
        uploaded = client.put(
            upload_session["upload"]["url"],
            headers=upload_session["upload"]["headers"],
            content=content,
        )
        uploaded.raise_for_status()
        completed = client.post(
            f"{api}/api/attachment-upload-sessions/complete",
            headers={"Idempotency-Key": key},
            json={"idempotencyKey": key, "uploadToken": upload_session["uploadToken"]},
        )
        completed.raise_for_status()
        attachment = completed.json()["attachment"]
        downloaded = client.get(f"{api}{attachment['proxyPath']}")
        downloaded.raise_for_status()
        if downloaded.content != content:
            raise RuntimeError("downloaded attachment did not match uploaded bytes")
        storage_status = client.get(f"{api}/api/integrations/object-storage/status")
        storage_status.raise_for_status()
        metadata = client.post(
            f"{api}/api/integrations/object-storage/verify-metadata",
            json={"dryRun": True, "limit": 100},
        )
        metadata.raise_for_status()
        restore = client.post(
            f"{api}/api/integrations/object-storage/restore-sample",
            json={"sampleSize": 10, "maxBytesPerObject": 1048576},
        )
        restore.raise_for_status()
        observations = client.get(f"{api}/api/integrations/object-storage/observations")
        observations.raise_for_status()
        legacy = client.get(f"{api}/api/integrations/object-storage/legacy-attachments")
        legacy.raise_for_status()
        orphan_dry_run = client.post(
            f"{api}/api/integrations/object-storage/reconcile-orphans",
            json={
                "idempotencyKey": key,
                "dryRun": True,
                "olderThan": datetime.now(UTC).isoformat(),
                "limit": 100,
            },
        )
        orphan_dry_run.raise_for_status()
        storage_delete_state = "PRESERVED_FOR_RESTORE_DRILL"
        if not keep_attachment:
            deleted = client.request(
                "DELETE",
                f"{api}/api/attachments/{attachment['id']}",
                json={
                    "baseResourceVersion": attachment["resourceVersion"],
                    "reason": "Object storage smoke cleanup",
                },
            )
            deleted.raise_for_status()
            storage_delete_state = deleted.json()["storageDeleteState"]
        print(
            json.dumps(
                {
                    "issueId": issue_id,
                    "createdIssue": created_issue,
                    "attachmentId": attachment["id"],
                    "attachmentResourceVersion": attachment["resourceVersion"],
                    "sha256Verified": attachment["sha256"] == sha256,
                    "privateDownloadVerified": True,
                    "metadataConsistent": metadata.json()["consistentCount"] >= 1,
                    "restoreVerified": restore.json()["verifiedCount"] >= 1,
                    "storageStatus": storage_status.json()["status"],
                    "warningLevel": observations.json()["warning"]["level"],
                    "legacyPolicy": legacy.json()["policy"],
                    "orphanDryRunFailures": orphan_dry_run.json()["failedCount"],
                    "storageDeleteState": storage_delete_state,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
