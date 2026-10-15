from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from tc_api.main import create_app

pytestmark = pytest.mark.postgres


def test_issue_and_report_flow_has_no_google_drive_runtime_dependency(
    clean_api_database: Engine,
) -> None:
    application = create_app(
        validate_database=False,
        database_engine=clean_api_database,
    )
    with TestClient(application) as client:
        issue = client.post(
            "/api/issues",
            json={
                "idempotencyKey": "manual-issue-001",
                "summary": "Private attachment regression",
                "precondition": "The issue ledger is available.",
                "steps": [
                    {
                        "sequence": 1,
                        "action": "Open the issue.",
                        "expectedResult": "The issue is visible.",
                    }
                ],
                "expectedResult": "The issue is visible.",
                "actualResult": "The issue is not visible.",
                "severity": "HIGH",
            },
        )
        assert issue.status_code == 201, issue.text
        issue_id = issue.json()["id"]
        comment = client.post(
            f"/api/issues/{issue_id}/comments",
            json={
                "idempotencyKey": "manual-comment-001",
                "body": "R2 migration is being verified.",
            },
        )
        assert comment.status_code == 201, comment.text
        issue_page = client.get("/api/issues", params={"limit": 10})
        assert issue_page.status_code == 200, issue_page.text
        assert [item["id"] for item in issue_page.json()["items"]] == [issue_id]
        assert client.get("/integrations/google-drive/authorize").status_code == 404
        assert client.get("/api/integrations/google-drive/status").status_code == 404


def test_deleted_issue_detail_restore_and_permanent_delete_lifecycle(
    clean_api_database: Engine,
) -> None:
    application = create_app(
        validate_database=False,
        database_engine=clean_api_database,
    )
    with TestClient(application) as client:
        created = client.post(
            "/api/issues",
            json={
                "idempotencyKey": "issue-purge-001",
                "summary": "Issue pending retention purge",
                "precondition": "The issue exists.",
                "steps": [
                    {
                        "sequence": 1,
                        "action": "Inspect the issue.",
                        "expectedResult": "The issue is visible.",
                    }
                ],
                "expectedResult": "The issue is visible.",
                "actualResult": "The issue is missing.",
                "severity": "MEDIUM",
            },
        ).json()
        issue_id = created["id"]
        comment = client.post(
            f"/api/issues/{issue_id}/comments",
            json={
                "idempotencyKey": "issue-purge-comment-001",
                "body": "Retain this audit evidence.",
            },
        )
        assert comment.status_code == 201, comment.text

        active_purge = client.request(
            "DELETE",
            f"/api/issues/{issue_id}/permanent",
            json={"baseResourceVersion": 1, "reason": "Must soft-delete first."},
        )
        assert active_purge.status_code == 409
        assert active_purge.json()["code"] == "PERMANENT_DELETE_CONFLICT"

        deleted = client.request(
            "DELETE",
            f"/api/issues/{issue_id}",
            json={"baseResourceVersion": 1, "reason": "Temporary cleanup."},
        ).json()
        assert client.get(f"/api/issues/{issue_id}").status_code == 404
        deleted_detail = client.get(
            f"/api/issues/{issue_id}", params={"includeDeleted": "true"}
        )
        assert deleted_detail.status_code == 200, deleted_detail.text
        assert deleted_detail.json()["deletedAt"] == deleted["deletedAt"]
        with pytest.raises(DBAPIError, match="checked capability"):
            with clean_api_database.begin() as connection:
                connection.execute(
                    text("DELETE FROM issues WHERE id = :id"),
                    {"id": uuid.UUID(issue_id.removeprefix("issue_"))},
                )
        deleted_page = client.get("/api/issues", params={"deletedOnly": "true"})
        assert deleted_page.status_code == 200, deleted_page.text
        assert deleted_page.json()["items"][0]["id"] == issue_id
        assert deleted_page.json()["items"][0]["deletedAt"] == deleted["deletedAt"]
        assert client.get(f"/api/issues/{issue_id}/links").status_code == 404
        links = client.get(
            f"/api/issues/{issue_id}/links", params={"includeDeleted": "true"}
        )
        comments = client.get(
            f"/api/issues/{issue_id}/comments", params={"includeDeleted": "true"}
        )
        history = client.get(
            f"/api/issues/{issue_id}/history", params={"includeDeleted": "true"}
        )
        assert links.status_code == comments.status_code == history.status_code == 200
        assert comments.json()["total"] == 1
        assert history.json()["total"] >= 3

        restored = client.post(
            f"/api/issues/{issue_id}/restore",
            headers={"Idempotency-Key": "issue-restore-001"},
            json={
                "baseResourceVersion": deleted["resourceVersion"],
                "reason": "Cleanup was accidental.",
            },
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["operation"] == "RESTORE"
        assert restored.json()["issue"]["deletedAt"] is None
        assert client.get(f"/api/issues/{issue_id}").status_code == 200
        restore_active = client.post(
            f"/api/issues/{issue_id}/restore",
            json={
                "baseResourceVersion": restored.json()["issue"]["resourceVersion"],
                "reason": "An active issue cannot be restored.",
            },
        )
        assert restore_active.status_code == 409
        assert restore_active.json()["code"] == "ISSUE_RESTORE_CONFLICT"

        deleted_again = client.request(
            "DELETE",
            f"/api/issues/{issue_id}",
            json={
                "baseResourceVersion": restored.json()["issue"]["resourceVersion"],
                "reason": "Retention cleanup approved.",
            },
        ).json()
        purged = client.request(
            "DELETE",
            f"/api/issues/{issue_id}/permanent",
            json={
                "baseResourceVersion": deleted_again["resourceVersion"],
                "reason": "Retention period expired.",
            },
        )
        assert purged.status_code == 200, purged.text
        assert purged.json()["operation"] == "PERMANENT_DELETE"
        assert purged.json()["deletedAttachmentCount"] == 0
        assert client.get(
            f"/api/issues/{issue_id}", params={"includeDeleted": "true"}
        ).status_code == 404

    decoded = uuid.UUID(issue_id.removeprefix("issue_"))
    with clean_api_database.connect() as connection:
        counts = connection.execute(
            text(
                "SELECT "
                "(SELECT count(*) FROM issues WHERE id = :id) AS issues, "
                "(SELECT count(*) FROM issue_steps WHERE issue_id = :id) AS steps, "
                "(SELECT count(*) FROM comments WHERE owner_type = 'ISSUE' "
                "AND owner_id = :id) AS comments, "
                "(SELECT count(*) FROM audit_logs WHERE entity_type = 'ISSUE' "
                "AND entity_id = :id AND action = 'PERMANENT_DELETE') AS audits, "
                "(SELECT count(*) FROM outbox_events WHERE aggregate_type = 'ISSUE' "
                "AND aggregate_id = :id) AS outbox"
            ),
            {"id": decoded},
        ).mappings().one()
    assert counts == {
        "issues": 0,
        "steps": 0,
        "comments": 0,
        "audits": 1,
        "outbox": 4,
    }
