from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from tc_api.main import create_app

pytestmark = pytest.mark.postgres


def _create_folder(
    client: TestClient,
    *,
    name: str,
    parent_id: str | None,
    sort_order: int = 0,
    idempotency_key: str | None = None,
) -> dict[str, object]:
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
    response = client.post(
        "/api/folders",
        json={"parentId": parent_id, "name": name, "sortOrder": sort_order},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_workspace_folder_tree_mutations_counts_audit_and_error_contracts(
    clean_api_database: Engine,
) -> None:
    application = create_app(validate_database=False, database_engine=clean_api_database)
    with TestClient(application) as client:
        workspace = client.get("/api/workspace/settings")
        assert workspace.status_code == 200
        assert workspace.json()["id"].startswith("workspace_")
        assert workspace.json()["updatedAt"].endswith(("Z", "+00:00"))

        update_payload = {
            "baseVersion": 1,
            "teamName": "Platform QA",
            "casePrefix": "CASE",
        }
        update_headers = {"Idempotency-Key": "workspace-update-001"}
        updated = client.patch(
            "/api/workspace/settings",
            json=update_payload,
            headers=update_headers,
        )
        assert updated.status_code == 200
        assert updated.json()["version"] == 2
        assert client.patch(
            "/api/workspace/settings",
            json=update_payload,
            headers=update_headers,
        ).json() == updated.json()
        idempotency_conflict = client.patch(
            "/api/workspace/settings",
            json={**update_payload, "teamName": "Different Team"},
            headers=update_headers,
        )
        assert idempotency_conflict.status_code == 409
        assert idempotency_conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"

        root = _create_folder(
            client,
            name="회원",
            parent_id=None,
            idempotency_key="create-members-001",
        )
        assert root["operation"] == "CREATE"
        assert "deletionBatchId" not in root
        replayed_root = _create_folder(
            client,
            name="회원",
            parent_id=None,
            idempotency_key="create-members-001",
        )
        assert replayed_root == root
        root_id = str(root["rootFolderId"])
        child = _create_folder(client, name="계정관리", parent_id=root_id)
        child_id = str(child["rootFolderId"])
        grandchild = _create_folder(client, name="비밀번호", parent_id=child_id)
        grandchild_id = str(grandchild["rootFolderId"])
        orders = _create_folder(client, name="주문", parent_id=None, sort_order=1)
        orders_id = str(orders["rootFolderId"])

        duplicate = client.post(
            "/api/folders",
            json={"parentId": root_id, "name": "  계정관리  ", "sortOrder": 1},
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["code"] == "FOLDER_NAME_CONFLICT"
        invalid = client.post(
            "/api/folders",
            json={"parentId": root_id, "name": "새 폴더", "unknown": True},
        )
        assert invalid.status_code == 422
        assert invalid.json()["code"] == "VALIDATION_FAILED"

        tree = client.get("/api/folders/tree")
        assert tree.status_code == 200
        assert tree.json()["generatedAt"].endswith(("Z", "+00:00"))
        members = next(item for item in tree.json()["items"] if item["id"] == root_id)
        assert members["directTestCaseCount"] == 0
        assert members["subtreeTestCaseCount"] == 0
        assert members["children"][0]["children"][0]["depth"] == 2

        children = client.get("/api/folders/children", params={"parentId": child_id})
        assert children.status_code == 200
        assert [item["id"] for item in children.json()["items"]] == [grandchild_id]
        assert children.json()["items"][0]["children"] == []
        assert children.json()["items"][0]["directTestCaseCount"] == 0

        sorted_response = client.post(
            f"/api/folders/{grandchild_id}/sort",
            json={"baseVersion": 1, "sortOrder": 4},
        )
        assert sorted_response.status_code == 200
        assert sorted_response.json()["operation"] == "SORT"
        assert sorted_response.json()["rootVersion"] == 2

        cycle = client.post(
            f"/api/folders/{root_id}/move",
            json={
                "baseVersion": 1,
                "mode": "SUBTREE",
                "targetParentId": grandchild_id,
                "sortOrder": 0,
            },
        )
        assert cycle.status_code == 409
        assert cycle.json()["code"] == "FOLDER_CYCLE"

        def rename(name: str) -> tuple[int, dict[str, object]]:
            response = client.patch(
                f"/api/folders/{grandchild_id}",
                json={"baseVersion": 2, "name": name},
            )
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as executor:
            rename_results = list(executor.map(rename, ["비밀번호 변경", "암호 변경"]))
        assert sorted(status for status, _ in rename_results) == [200, 409]
        conflict_payload = next(payload for status, payload in rename_results if status == 409)
        assert conflict_payload["code"] == "VERSION_CONFLICT"

        moved = client.post(
            f"/api/folders/{grandchild_id}/move",
            json={
                "baseVersion": 3,
                "mode": "SUBTREE",
                "targetParentId": orders_id,
                "sortOrder": 0,
            },
        )
        assert moved.status_code == 200
        assert moved.json()["operation"] == "MOVE_SUBTREE"
        assert moved.json()["rootVersion"] == 4

        preview = client.get(f"/api/folders/{root_id}/delete-preview")
        assert preview.status_code == 200
        assert preview.json()["affectedFolderCount"] == 2
        assert preview.json()["requiresConfirmation"] is True
        warning = client.request(
            "DELETE",
            f"/api/folders/{root_id}",
            json={"baseVersion": 1, "mode": "SUBTREE"},
        )
        assert warning.status_code == 409
        assert warning.json()["code"] == "FOLDER_CONTENT_WARNING"
        deleted = client.request(
            "DELETE",
            f"/api/folders/{root_id}",
            params={"confirm": "true"},
            json={"baseVersion": 1, "mode": "SUBTREE", "reason": "cleanup"},
        )
        assert deleted.status_code == 200
        assert deleted.json()["operation"] == "SOFT_DELETE_SUBTREE"
        deletion_batch_id = deleted.json()["deletionBatchId"]
        restored = client.post(
            f"/api/folders/{root_id}/restore",
            json={
                "baseVersion": 2,
                "deletionBatchId": deletion_batch_id,
                "mode": "SUBTREE",
            },
        )
        assert restored.status_code == 200
        assert restored.json()["operation"] == "RESTORE_SUBTREE"
        assert restored.json()["deletionBatchId"] == deletion_batch_id

        system_id = "folder_00000000000070008000000000000101"
        protected = client.request(
            "DELETE",
            f"/api/folders/{system_id}",
            params={"confirm": "true"},
            json={"baseVersion": 1, "mode": "SUBTREE"},
        )
        assert protected.status_code == 409
        assert protected.json()["code"] == "SYSTEM_FOLDER_PROTECTED"

        audit_page = client.get("/api/audit-logs", params={"limit": 2})
        assert audit_page.status_code == 200
        assert audit_page.json()["total"] >= 10
        assert len(audit_page.json()["items"]) == 2
        assert audit_page.json()["nextCursor"] is not None
        assert all("_idempotency" not in (item["after"] or {}) for item in audit_page.json()["items"])
        next_page = client.get(
            "/api/audit-logs",
            params={"limit": 2, "cursor": audit_page.json()["nextCursor"]},
        )
        assert next_page.status_code == 200
        assert {item["id"] for item in next_page.json()["items"]}.isdisjoint(
            item["id"] for item in audit_page.json()["items"]
        )
        invalid_cursor = client.get("/api/audit-logs", params={"cursor": "not-base64"})
        assert invalid_cursor.status_code == 422
        assert invalid_cursor.json()["code"] == "AUDIT_CURSOR_INVALID"
