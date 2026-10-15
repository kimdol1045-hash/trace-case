from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from tc_api.main import create_app

pytestmark = pytest.mark.postgres


def test_category_work_item_case_cycle_survives_application_reload(
    clean_api_database: Engine,
) -> None:
    """Exercise the Sprint A1 user chain through HTTP and a second app instance."""
    application = create_app(
        validate_database=False,
        database_engine=clean_api_database,
    )
    with TestClient(application) as client:
        category_response = client.post(
            "/api/folders",
            json={"parentId": None, "name": "Reload E2E", "sortOrder": 0},
            headers={"Idempotency-Key": "sprint-a1-category"},
        )
        assert category_response.status_code == 201, category_response.text
        category = category_response.json()

        work_item_response = client.post(
            "/api/work-items",
            json={
                "key": "WORK-901",
                "categoryId": category["rootFolderId"],
                "summary": "Reload-safe checkout",
                "description": "Sprint A1 full-chain regression",
                "ownerLabel": "QA E2E",
                "labels": ["sprint-a1", "reload"],
            },
            headers={"Idempotency-Key": "sprint-a1-work-item"},
        )
        assert work_item_response.status_code == 201, work_item_response.text
        work_item = work_item_response.json()

        case_response = client.post(
            "/api/test-cases",
            json={
                "workItemId": work_item["id"],
                "status": "ACTIVE",
                "summary": "Checkout remains canonical after reload",
                "precondition": "A cart exists.",
                "expectedResultMode": "PER_STEP",
                "priority": "HIGH",
                "ownerLabel": "QA E2E",
                "labels": ["sprint-a1", "reload"],
                "steps": [
                    {
                        "sequence": 1,
                        "precondition": "The payment sandbox is available.",
                        "action": "Complete checkout.",
                        "expectedResult": "The order is persisted.",
                    }
                ],
                "source": {"type": "UI", "actorLabel": "Sprint A1 Web E2E"},
            },
            headers={"Idempotency-Key": "sprint-a1-case"},
        )
        assert case_response.status_code == 201, case_response.text
        test_case = case_response.json()

        cycle_response = client.post(
            "/api/cycles",
            json={
                "name": "Sprint A1 reload cycle",
                "description": "Pinned through the Web creation contract",
                "startDate": "2026-08-24",
                "scope": {
                    "type": "WORK_ITEM",
                    "workItemId": work_item["id"],
                    "workItemKey": work_item["key"],
                },
            },
            headers={"Idempotency-Key": "sprint-a1-cycle"},
        )
        assert cycle_response.status_code == 201, cycle_response.text
        cycle = cycle_response.json()

    # A fresh ASGI application and HTTP client model a hard Web reload: no
    # reducer/provider state is available, so every assertion comes from PostgreSQL.
    reloaded_application = create_app(
        validate_database=False,
        database_engine=clean_api_database,
    )
    with TestClient(reloaded_application) as reloaded:
        tree = reloaded.get("/api/folders/tree")
        assert tree.status_code == 200, tree.text
        category_projection = next(
            item
            for item in tree.json()["items"]
            if item["id"] == category["rootFolderId"]
        )
        assert category_projection["name"] == "Reload E2E"
        assert category_projection["directTestCaseCount"] == 1

        work_item_detail = reloaded.get(f"/api/work-items/{work_item['key']}")
        assert work_item_detail.status_code == 200, work_item_detail.text
        assert work_item_detail.json()["workItem"] == work_item
        assert [item["key"] for item in work_item_detail.json()["testCases"]] == [
            test_case["key"]
        ]

        case_detail = reloaded.get(f"/api/test-cases/{test_case['id']}")
        assert case_detail.status_code == 200, case_detail.text
        assert case_detail.json()["key"] == test_case["key"]
        assert case_detail.json()["currentVersion"]["steps"][0]["action"] == (
            "Complete checkout."
        )

        cycle_detail = reloaded.get(f"/api/cycles/{cycle['id']}")
        assert cycle_detail.status_code == 200, cycle_detail.text
        assert cycle_detail.json()["name"] == "Sprint A1 reload cycle"
        cycle_items = reloaded.get(f"/api/cycles/{cycle['id']}/items")
        assert cycle_items.status_code == 200, cycle_items.text
        assert cycle_items.json()["total"] == 1
        assert cycle_items.json()["items"][0]["caseKey"] == test_case["key"]
        assert cycle_items.json()["items"][0]["pinnedVersion"]["version"] == 1
