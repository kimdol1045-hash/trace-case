from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from tc_api.main import create_app

pytestmark = pytest.mark.postgres


def _work_item_id(value: uuid.UUID) -> str:
    return f"work_item_{value.hex}"


def _manual_payload(work_item_id: str) -> dict[str, object]:
    return {
        "workItemId": work_item_id,
        "status": "ACTIVE",
        "summary": "Card approval creates an order",
        "precondition": "A signed-in customer has an item in the cart.",
        "expectedResultMode": "PER_STEP",
        "priority": "HIGH",
        "ownerLabel": "QA Kim",
        "labels": ["payment", "smoke"],
        "steps": [
            {
                "sequence": 1,
                "precondition": "The payment processor is available.",
                "action": "Submit payment.",
                "expectedResult": "An order number is displayed.",
            }
        ],
        "source": {"type": "UI", "actorLabel": "QA Kim"},
    }


def test_case_search_binds_sql_injection_text_without_schema_side_effects(
    clean_api_database: Engine,
) -> None:
    application = create_app(validate_database=False, database_engine=clean_api_database)
    with TestClient(application) as client:
        category = client.post(
            "/api/folders",
            json={"parentId": None, "name": "Security", "sortOrder": 0},
        ).json()["rootFolderId"]
        work_item = client.post(
            "/api/work-items",
            json={
                "key": "WORK-901",
                "categoryId": category,
                "summary": "Security regression target",
            },
        )
        assert work_item.status_code == 201, work_item.text
        created = client.post(
            "/api/test-cases",
            json=_manual_payload(work_item.json()["id"]),
        )
        assert created.status_code == 201, created.text

        injection = "x%'); DROP TABLE test_cases; SELECT pg_sleep(1); --"
        attacked = client.get("/api/test-cases", params={"search": injection})
        assert attacked.status_code == 200, attacked.text
        assert attacked.json()["total"] == 0
        assert client.get("/api/test-cases").json()["total"] == 1

    with clean_api_database.connect() as connection:
        assert connection.scalar(text("SELECT to_regclass('public.test_cases')")) == (
            "test_cases"
        )


def test_work_item_crud_persists_projection_conflicts_and_audit(
    clean_api_database: Engine,
) -> None:
    application = create_app(validate_database=False, database_engine=clean_api_database)
    with TestClient(application) as client:
        orders = client.post(
            "/api/folders",
            json={"parentId": None, "name": "Orders", "sortOrder": 0},
        ).json()["rootFolderId"]
        refunds = client.post(
            "/api/folders",
            json={"parentId": None, "name": "Refunds", "sortOrder": 1},
        ).json()["rootFolderId"]
        child = client.post(
            "/api/folders",
            json={"parentId": orders, "name": "Child", "sortOrder": 0},
        ).json()["rootFolderId"]

        payload = {
            "key": "WORK-531",
            "categoryId": orders,
            "summary": "Card payment reliability",
            "description": "Persisted scope",
            "ownerLabel": "QA Kim",
            "labels": ["payment", "smoke"],
        }
        created = client.post(
            "/api/work-items",
            json=payload,
            headers={"Idempotency-Key": "create-work-531"},
        )
        assert created.status_code == 201, created.text
        resource = created.json()
        assert resource["resourceVersion"] == 1
        assert resource["description"] == "Persisted scope"
        assert resource["ownerLabel"] == "QA Kim"

        assert client.get("/api/work-items").json()["items"] == [resource]
        duplicate = client.post("/api/work-items", json=payload)
        assert duplicate.status_code == 409
        assert duplicate.json()["code"] == "WORK_ITEM_KEY_CONFLICT"
        wrong_category = client.post(
            "/api/work-items",
            json={**payload, "key": "WORK-532", "categoryId": child},
        )
        assert wrong_category.status_code == 409
        assert wrong_category.json()["code"] == "WORK_ITEM_CATEGORY_CONFLICT"

        case = client.post(
            "/api/test-cases",
            json=_manual_payload(resource["id"]),
        )
        assert case.status_code == 201, case.text
        case_id = case.json()["id"]
        detail = client.get("/api/work-items/WORK-531")
        assert detail.status_code == 200, detail.text
        assert detail.json()["testCases"][0]["id"] == case_id

        update_payload = {
            "baseResourceVersion": 1,
            "categoryId": refunds,
            "summary": "Refund payment reliability",
            "description": "Reload-safe scope",
            "ownerLabel": "QA Lee",
            "labels": ["refund", "regression"],
            "changeReason": "Move ownership and revise scope",
        }
        updated = client.patch("/api/work-items/WORK-531", json=update_payload)
        assert updated.status_code == 200, updated.text
        assert updated.json()["resourceVersion"] == 2
        assert updated.json()["categoryId"] == refunds
        assert updated.json()["labels"] == ["refund", "regression"]

        reloaded = client.get("/api/work-items/WORK-531").json()
        assert reloaded["workItem"] == updated.json()
        assert reloaded["testCases"][0]["categoryId"] == refunds
        assert reloaded["testCases"][0]["resourceVersion"] == 2
        assert client.get(f"/api/test-cases/{case_id}").json()["categoryId"] == refunds

        stale = client.patch("/api/work-items/WORK-531", json=update_payload)
        assert stale.status_code == 409
        assert stale.json()["code"] == "VERSION_CONFLICT"
        assert stale.json()["details"]["actualVersion"] == 2

        audits = client.get(
            "/api/audit-logs",
            params={"entityType": "WORK_ITEM", "entityId": resource["id"]},
        ).json()
        assert [entry["action"] for entry in audits["items"]] == ["UPDATE", "CREATE"]


def test_work_item_reads_accept_persisted_non_work_prefix_keys(
    clean_api_database: Engine,
) -> None:
    application = create_app(validate_database=False, database_engine=clean_api_database)
    with TestClient(application) as client:
        category = client.post(
            "/api/folders",
            json={"parentId": None, "name": "Live smoke", "sortOrder": 0},
        ).json()["rootFolderId"]
        rejected_create = client.post(
            "/api/work-items",
            json={
                "key": "LIVE-301",
                "categoryId": category,
                "summary": "Sprint 3 live smoke",
            },
        )
        assert rejected_create.status_code == 422
        category_id = uuid.UUID(category.removeprefix("folder_"))
        work_item_id = uuid.uuid4()
        with clean_api_database.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO work_items "
                    "(id, workspace_id, category_id, work_item_key, summary, description, "
                    "labels, version) VALUES "
                    "(:id, '00000000-0000-7000-8000-000000000100', :category_id, "
                    "'LIVE-301', 'Sprint 3 live smoke', '', '[]'::jsonb, 1)"
                ),
                {"id": work_item_id, "category_id": category_id},
            )

        listing = client.get(
            "/api/work-items", params={"limit": 200, "offset": 0}
        )
        assert listing.status_code == 200, listing.text
        assert listing.json()["items"][0]["key"] == "LIVE-301"

        detail = client.get("/api/work-items/LIVE-301")
        assert detail.status_code == 200, detail.text
        assert detail.json()["workItem"]["key"] == "LIVE-301"


def test_case_crud_versions_search_copy_move_and_lifecycle(
    clean_api_database: Engine,
) -> None:
    application = create_app(validate_database=False, database_engine=clean_api_database)
    with TestClient(application) as client:
        category = client.post(
            "/api/folders",
            json={"parentId": None, "name": "Orders", "sortOrder": 0},
        ).json()["rootFolderId"]
        category_uuid = uuid.UUID(category.removeprefix("folder_"))
        first_work_item = uuid.uuid4()
        second_work_item = uuid.uuid4()
        with clean_api_database.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO work_items "
                    "(id, workspace_id, category_id, work_item_key, summary, description, "
                    "owner_label, labels, version) VALUES "
                    "(:first, '00000000-0000-7000-8000-000000000100', :category, "
                    "'WORK-101', 'Checkout reliability', '', NULL, '[]'::jsonb, 1), "
                    "(:second, '00000000-0000-7000-8000-000000000100', :category, "
                    "'WORK-102', 'Refund reliability', '', NULL, '[]'::jsonb, 1)"
                ),
                {"first": first_work_item, "second": second_work_item, "category": category_uuid},
            )

        create_payload = _manual_payload(_work_item_id(first_work_item))
        headers = {"Idempotency-Key": "create-case-001"}
        created = client.post("/api/test-cases", json=create_payload, headers=headers)
        assert created.status_code == 201, created.text
        resource = created.json()
        assert resource["key"] == "TC-000001"
        assert resource["version"] == resource["resourceVersion"] == 1
        assert resource["currentVersion"]["source"] == {
            "type": "UI",
            "actorLabel": "QA Kim",
        }
        assert resource["currentVersion"]["steps"][0]["precondition"] == (
            "The payment processor is available."
        )
        assert client.post("/api/test-cases", json=create_payload, headers=headers).json() == resource
        case_id = resource["id"]

        duplicate_labels = client.post(
            "/api/test-cases",
            json={**create_payload, "labels": ["Smoke", " smoke "]},
        )
        assert duplicate_labels.status_code == 409
        assert duplicate_labels.json()["code"] == "LABEL_CONFLICT"

        invalid_mode = client.post(
            "/api/test-cases",
            json={
                **create_payload,
                "expectedResultMode": "CASE",
                "caseExpectedResult": "Order is created.",
                "generationReason": "Generated coverage.",
                "steps": [{"sequence": 1, "action": "Submit payment."}],
            },
        )
        assert invalid_mode.status_code == 409
        assert invalid_mode.json()["code"] == "EXPECTED_RESULT_MODE_INVALID"

        listed = client.get(
            "/api/test-cases",
            params=[("search", "tc-000001 card"), ("labels", "Smoke"), ("labelMatch", "ALL")],
        )
        assert listed.status_code == 200, listed.text
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["workItemKey"] == "WORK-101"
        invalid_cursor = client.get("/api/test-cases", params={"cursor": "not-a-cursor"})
        assert invalid_cursor.status_code == 422
        assert invalid_cursor.json()["code"] == "CURSOR_INVALID"

        update_payload = {
            **{key: value for key, value in create_payload.items() if key not in {"workItemId", "status"}},
            "baseVersion": 1,
            "summary": "Card approval creates exactly one order",
            "priority": "CRITICAL",
            "labels": ["payment", "regression"],
            "steps": [
                {
                    "sequence": 1,
                    "precondition": "The payment processor and fraud service are available.",
                    "action": "Submit payment.",
                    "expectedResult": "An order number is displayed.",
                }
            ],
            "changeReason": "Add duplicate submission coverage.",
        }
        updated = client.patch(f"/api/test-cases/{case_id}", json=update_payload)
        assert updated.status_code == 200, updated.text
        assert updated.json()["version"] == updated.json()["resourceVersion"] == 2
        assert updated.json()["currentVersion"]["steps"][0]["precondition"] == (
            "The payment processor and fraud service are available."
        )

        stale = client.patch(f"/api/test-cases/{case_id}", json=update_payload)
        assert stale.status_code == 409
        assert stale.json()["code"] == "VERSION_CONFLICT"
        assert stale.json()["details"]["actualVersion"] == 2

        versions = client.get(f"/api/test-cases/{case_id}/versions", params={"limit": 1})
        assert versions.status_code == 200
        assert versions.json()["total"] == 2
        assert versions.json()["nextCursor"] is not None
        page_two = client.get(
            f"/api/test-cases/{case_id}/versions",
            params={"limit": 1, "cursor": versions.json()["nextCursor"]},
        )
        assert page_two.json()["items"][0]["version"] == 2
        diff = client.get(
            f"/api/test-cases/{case_id}/versions/diff",
            params={"fromVersion": 1, "toVersion": 2},
        )
        assert diff.status_code == 200, diff.text
        assert {item["field"] for item in diff.json()["fieldChanges"]} >= {
            "summary",
            "priority",
            "labels",
        }

        moved = client.post(
            f"/api/test-cases/{case_id}/move",
            json={
                "baseResourceVersion": 2,
                "targetWorkItemId": _work_item_id(second_work_item),
                "targetSortOrder": 0,
                "changeReason": "Move to refunds.",
                "source": {"type": "UI"},
            },
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["testCase"]["resourceVersion"] == 3
        assert moved.json()["testCase"]["version"] == 2

        copy_payload = {
            "sourceVersion": 1,
            "targetWorkItemId": _work_item_id(second_work_item),
            "targetSortOrder": 1,
            "summary": "Copied checkout regression",
            "changeReason": "Reuse checkout coverage.",
            "source": {"type": "UI"},
        }
        copied = client.post(
            f"/api/test-cases/{case_id}/copy",
            headers={"Idempotency-Key": "copy-case-001"},
            json=copy_payload,
        )
        assert copied.status_code == 201, copied.text
        copy_resource = copied.json()["testCase"]
        assert copy_resource["version"] == copy_resource["resourceVersion"] == 1
        assert copy_resource["currentVersion"]["summary"] == "Copied checkout regression"
        assert copied.json()["copiedFrom"]["version"] == 1
        assert client.post(
            f"/api/test-cases/{case_id}/copy",
            headers={"Idempotency-Key": "copy-case-001"},
            json=copy_payload,
        ).json() == copied.json()

        deleted = client.request(
            "DELETE",
            f"/api/test-cases/{copy_resource['id']}",
            json={
                "baseResourceVersion": 1,
                "reason": "Temporary cleanup.",
                "source": {"type": "API"},
            },
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["testCase"]["deletedAt"] is not None
        assert client.get("/api/test-cases").json()["total"] == 1
        assert client.get(
            "/api/test-cases", params={"includeDeleted": "true"}
        ).json()["total"] == 2
        deleted_only = client.get(
            "/api/test-cases", params={"deletedOnly": "true"}
        ).json()
        assert deleted_only["total"] == 1
        assert [item["id"] for item in deleted_only["items"]] == [copy_resource["id"]]

        restored_deleted = client.post(
            f"/api/test-cases/{copy_resource['id']}/restore",
            json={
                "baseResourceVersion": 2,
                "reason": "Cleanup was accidental.",
                "source": {"type": "API"},
            },
        )
        assert restored_deleted.status_code == 200, restored_deleted.text
        assert restored_deleted.json()["testCase"]["deletedAt"] is None

        restored_version = client.post(
            f"/api/test-cases/{case_id}/restore/1",
            json={
                "baseVersion": 2,
                "changeReason": "Restore approved script.",
                "source": {"type": "UI"},
            },
        )
        assert restored_version.status_code == 200, restored_version.text
        assert restored_version.json()["testCase"]["version"] == 3
        assert restored_version.json()["testCase"]["resourceVersion"] == 4
        assert restored_version.json()["restoredFromVersion"] == 1


def test_deleted_case_detail_and_permanent_delete_remove_owned_history(
    clean_api_database: Engine,
) -> None:
    application = create_app(validate_database=False, database_engine=clean_api_database)
    with TestClient(application) as client:
        category = client.post(
            "/api/folders",
            json={"parentId": None, "name": "Purge cases", "sortOrder": 0},
        ).json()["rootFolderId"]
        work_item = client.post(
            "/api/work-items",
            json={
                "key": "WORK-901",
                "categoryId": category,
                "summary": "Case retention",
            },
        ).json()
        created = client.post(
            "/api/test-cases", json=_manual_payload(work_item["id"])
        ).json()
        case_id = created["id"]

        active_purge = client.request(
            "DELETE",
            f"/api/test-cases/{case_id}/permanent",
            json={
                "baseResourceVersion": 1,
                "reason": "Must soft-delete first.",
                "source": {"type": "API"},
            },
        )
        assert active_purge.status_code == 409
        assert active_purge.json()["code"] == "PERMANENT_DELETE_CONFLICT"

        deleted = client.request(
            "DELETE",
            f"/api/test-cases/{case_id}",
            json={
                "baseResourceVersion": 1,
                "reason": "Retention cleanup.",
                "source": {"type": "API"},
            },
        ).json()["testCase"]
        assert client.get(f"/api/test-cases/{case_id}").status_code == 409
        detail = client.get(
            f"/api/test-cases/{case_id}", params={"includeDeleted": "true"}
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["deletedAt"] == deleted["deletedAt"]

        purged = client.request(
            "DELETE",
            f"/api/test-cases/{case_id}/permanent",
            json={
                "baseResourceVersion": deleted["resourceVersion"],
                "reason": "Retention period expired.",
                "source": {"type": "API"},
            },
        )
        assert purged.status_code == 200, purged.text
        assert purged.json()["operation"] == "PERMANENT_DELETE"
        assert purged.json()["deletedAttachmentCount"] == 0
        assert client.get(
            f"/api/test-cases/{case_id}", params={"includeDeleted": "true"}
        ).status_code == 404

    decoded = uuid.UUID(case_id.removeprefix("case_"))
    with clean_api_database.connect() as connection:
        counts = connection.execute(
            text(
                "SELECT "
                "(SELECT count(*) FROM test_cases WHERE id = :id) AS cases, "
                "(SELECT count(*) FROM test_case_versions WHERE test_case_id = :id) AS versions, "
                "(SELECT count(*) FROM audit_logs WHERE entity_type = 'TEST_CASE' "
                "AND entity_id = :id AND action = 'PERMANENT_DELETE') AS audits"
            ),
            {"id": decoded},
        ).mappings().one()
    assert counts == {"cases": 0, "versions": 0, "audits": 1}


def test_category_projection_is_canonical_across_filters_and_crud(
    clean_api_database: Engine,
) -> None:
    application = create_app(validate_database=False, database_engine=clean_api_database)
    with TestClient(application) as client:
        payments = client.post(
            "/api/folders",
            json={"parentId": None, "name": "Payments", "sortOrder": 0},
        ).json()
        empty = client.post(
            "/api/folders",
            json={"parentId": None, "name": "Empty category", "sortOrder": 1},
        ).json()
        work_item = client.post(
            "/api/work-items",
            json={
                "key": "WORK-531",
                "categoryId": payments["rootFolderId"],
                "summary": "Payment reliability",
                "description": "Canonical parent projection",
                "ownerLabel": "QA Kim",
                "labels": ["payment"],
            },
        ).json()
        created_case = client.post(
            "/api/test-cases",
            json=_manual_payload(work_item["id"]),
        )
        assert created_case.status_code == 201, created_case.text

        def root_category(category_id: str) -> dict[str, object] | None:
            items = client.get("/api/folders/tree").json()["items"]
            return next((item for item in items if item["id"] == category_id), None)

        before = root_category(payments["rootFolderId"])
        assert before is not None
        assert before["directTestCaseCount"] == 1
        assert client.get("/api/work-items").json()["items"] == [work_item]

        filtered = client.get(
            "/api/test-cases",
            params={
                "search": "does-not-match",
                "categoryIds": payments["rootFolderId"],
            },
        )
        assert filtered.status_code == 200, filtered.text
        assert filtered.json()["total"] == 0
        after_filter = root_category(payments["rootFolderId"])
        assert after_filter is not None
        assert after_filter["directTestCaseCount"] == 1
        assert client.get("/api/work-items").json()["items"] == [work_item]

        renamed = client.patch(
            f"/api/folders/{payments['rootFolderId']}",
            json={"baseVersion": payments["rootVersion"], "name": "Card payments"},
        )
        assert renamed.status_code == 200, renamed.text
        renamed_category = root_category(payments["rootFolderId"])
        assert renamed_category is not None
        assert renamed_category["name"] == "Card payments"

        deleted_empty = client.request(
            "DELETE",
            f"/api/folders/{empty['rootFolderId']}",
            json={
                "baseVersion": empty["rootVersion"],
                "mode": "SUBTREE",
                "reason": "Remove unused category",
            },
        )
        assert deleted_empty.status_code == 200, deleted_empty.text
        assert root_category(empty["rootFolderId"]) is None

        blocked = client.request(
            "DELETE",
            f"/api/folders/{payments['rootFolderId']}",
            json={
                "baseVersion": renamed.json()["rootVersion"],
                "mode": "SUBTREE",
                "reason": "Must not remove canonical content",
            },
        )
        assert blocked.status_code == 409
        assert blocked.json()["code"] == "FOLDER_NOT_EMPTY"
        still_present = root_category(payments["rootFolderId"])
        assert still_present is not None
        assert still_present["directTestCaseCount"] == 1


def test_same_parent_reorder_and_cross_parent_move_are_atomic_and_audited(
    clean_api_database: Engine,
) -> None:
    application = create_app(validate_database=False, database_engine=clean_api_database)
    with TestClient(application) as client:
        category = client.post(
            "/api/folders",
            json={"parentId": None, "name": "Payments", "sortOrder": 0},
        ).json()["rootFolderId"]
        source = client.post(
            "/api/work-items",
            json={"key": "WORK-601", "categoryId": category, "summary": "Source", "labels": []},
        ).json()
        target = client.post(
            "/api/work-items",
            json={"key": "WORK-602", "categoryId": category, "summary": "Target", "labels": []},
        ).json()
        first_work_item_page = client.get(
            "/api/work-items", params={"limit": 1, "offset": 0}
        ).json()
        second_work_item_page = client.get(
            "/api/work-items", params={"limit": 1, "offset": 1}
        ).json()
        assert first_work_item_page["total"] == second_work_item_page["total"] == 2
        assert {
            first_work_item_page["items"][0]["id"],
            second_work_item_page["items"][0]["id"],
        } == {source["id"], target["id"]}

        source_cases = [
            client.post(
                "/api/test-cases",
                json={**_manual_payload(source["id"]), "summary": f"Source case {index}"},
            ).json()
            for index in range(1, 4)
        ]
        target_case = client.post(
            "/api/test-cases",
            json={**_manual_payload(target["id"]), "summary": "Target case"},
        ).json()

        reordered = client.post(
            f"/api/test-cases/{source_cases[2]['id']}/move",
            json={
                "baseResourceVersion": 1,
                "targetWorkItemId": source["id"],
                "targetSortOrder": 0,
                "changeReason": "Move third case to the front",
                "source": {"type": "UI", "actorLabel": "QA reorder test"},
            },
        )
        assert reordered.status_code == 200, reordered.text
        assert reordered.json()["testCase"]["sortOrder"] == 0

        moved = client.post(
            f"/api/test-cases/{source_cases[0]['id']}/move",
            json={
                "baseResourceVersion": 1,
                "targetWorkItemId": target["id"],
                "targetSortOrder": 0,
                "changeReason": "Move first case to target",
                "source": {"type": "UI", "actorLabel": "QA move test"},
            },
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["testCase"]["workItemId"] == target["id"]
        assert moved.json()["testCase"]["sortOrder"] == 0

        def case_order(work_item_id: str) -> list[str]:
            response = client.get(
                "/api/test-cases",
                params={"workItemIds": work_item_id, "limit": 50},
            )
            assert response.status_code == 200, response.text
            return [item["id"] for item in response.json()["items"]]

        expected_source = [source_cases[2]["id"], source_cases[1]["id"]]
        expected_target = [source_cases[0]["id"], target_case["id"]]
        assert case_order(source["id"]) == expected_source
        assert case_order(target["id"]) == expected_target

        stale = client.post(
            f"/api/test-cases/{source_cases[0]['id']}/move",
            json={
                "baseResourceVersion": 1,
                "targetWorkItemId": source["id"],
                "targetSortOrder": 0,
                "changeReason": "Stale move must roll back",
                "source": {"type": "UI"},
            },
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "VERSION_CONFLICT"
        assert case_order(source["id"]) == expected_source
        assert case_order(target["id"]) == expected_target

        reorder_audit = client.get(
            "/api/audit-logs",
            params={"entityType": "TEST_CASE", "entityId": source_cases[2]["id"]},
        ).json()["items"]
        move_audit = client.get(
            "/api/audit-logs",
            params={"entityType": "TEST_CASE", "entityId": source_cases[0]["id"]},
        ).json()["items"]
        assert reorder_audit[0]["action"] == "REORDER"
        assert {entry["action"] for entry in reorder_audit[1:]} == {
            "CREATE",
            "ALLOCATE_KEY",
        }
        assert move_audit[0]["action"] == "MOVE"
        assert {entry["action"] for entry in move_audit[1:]} == {
            "CREATE",
            "ALLOCATE_KEY",
        }
        assert move_audit[0]["before"]["workItemId"] == str(
            uuid.UUID(source["id"].removeprefix("work_item_"))
        )
        assert move_audit[0]["after"]["workItemId"] == str(
            uuid.UUID(target["id"].removeprefix("work_item_"))
        )
