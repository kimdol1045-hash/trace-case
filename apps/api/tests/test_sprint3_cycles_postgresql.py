from __future__ import annotations

import uuid
from time import perf_counter

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from tc_api.main import create_app

pytestmark = pytest.mark.postgres


def _case_payload(work_item_id: str) -> dict[str, object]:
    return {
        "workItemId": work_item_id,
        "status": "ACTIVE",
        "summary": "Permanent-delete dependency case",
        "precondition": "A cycle can be created.",
        "expectedResultMode": "PER_STEP",
        "priority": "HIGH",
        "labels": ["retention"],
        "steps": [
            {
                "sequence": 1,
                "action": "Run the retained case.",
                "expectedResult": "The result is recorded.",
            }
        ],
        "source": {"type": "API"},
    }


def test_cycle_snapshot_attempt_retry_completion_and_reopen_flow(
    clean_api_database: Engine,
) -> None:
    application = create_app(
        validate_database=False, database_engine=clean_api_database
    )
    with TestClient(application) as client:
        category = client.post(
            "/api/folders", json={"parentId": None, "name": "Checkout", "sortOrder": 0}
        ).json()["rootFolderId"]
        category_uuid = uuid.UUID(category.removeprefix("folder_"))
        work_item_uuid = uuid.uuid4()
        work_item_id = f"work_item_{work_item_uuid.hex}"
        with clean_api_database.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO work_items "
                    "(id, workspace_id, category_id, work_item_key, summary, description, "
                    "owner_label, labels, version) VALUES "
                    "(:id, '00000000-0000-7000-8000-000000000100', :category, "
                    "'WORK-301', 'Checkout', '', 'QA Kim', '[\"payments\"]'::jsonb, 1)"
                ),
                {"id": work_item_uuid, "category": category_uuid},
            )

        case_payload = {
            "workItemId": work_item_id,
            "status": "ACTIVE",
            "summary": "Checkout succeeds",
            "precondition": "A cart is ready.",
            "expectedResultMode": "PER_STEP",
            "priority": "HIGH",
            "ownerLabel": "QA Kim",
            "labels": ["payments", "smoke"],
            "steps": [
                {
                    "sequence": 1,
                    "precondition": "The payment sandbox is reachable.",
                    "action": "Pay.",
                    "expectedResult": "Order created.",
                }
            ],
            "source": {"type": "UI", "actorLabel": "QA Kim"},
        }
        case = client.post("/api/test-cases", json=case_payload).json()

        work_item_options = client.get(
            "/api/cycles/scope-options", params={"type": "WORK_ITEM"}
        )
        assert work_item_options.status_code == 200, work_item_options.text
        assert work_item_options.json()["items"] == [
            {
                "type": "WORK_ITEM",
                "workItemId": work_item_id,
                "workItemKey": "WORK-301",
                "summary": "Checkout",
                "ownerLabel": "QA Kim",
                "caseCount": 1,
            }
        ]
        label_options = client.get(
            "/api/cycles/scope-options", params={"type": "LABEL", "limit": 1}
        )
        assert label_options.status_code == 200, label_options.text
        assert label_options.json()["total"] == 2
        assert label_options.json()["nextCursor"] is not None
        second_label_page = client.get(
            "/api/cycles/scope-options",
            params={
                "type": "LABEL",
                "limit": 1,
                "cursor": label_options.json()["nextCursor"],
            },
        )
        assert second_label_page.status_code == 200, second_label_page.text
        assert {
            label_options.json()["items"][0]["label"],
            second_label_page.json()["items"][0]["label"],
        } == {"payments", "smoke"}

        work_item_preview = client.get(
            "/api/cycles/scope-preview",
            params={"scopeType": "WORK_ITEM", "workItemId": work_item_id},
        )
        assert work_item_preview.status_code == 200, work_item_preview.text
        assert work_item_preview.json()["items"][0]["testCaseId"] == case["id"]
        label_preview = client.get(
            "/api/cycles/scope-preview",
            params={"scopeType": "LABEL", "label": "payments"},
        )
        assert label_preview.status_code == 200, label_preview.text
        assert label_preview.json()["items"][0]["testCaseId"] == case["id"]
        assert len({item["testCaseId"] for item in label_preview.json()["items"]}) == 1

        union_preview = client.get(
            "/api/cycles/scope-preview",
            params=[
                ("scopeType", "LABEL"),
                ("labels", "payments"),
                ("labels", "smoke"),
            ],
        )
        assert union_preview.status_code == 200, union_preview.text
        assert union_preview.json()["scope"]["labels"] == ["payments", "smoke"]
        assert union_preview.json()["total"] == 1

        explicit_cycle = client.post(
            "/api/cycles",
            json={
                "name": "Explicit step execution",
                "description": "Start before recording results",
                "startDate": "2026-08-21",
                "scope": {"type": "LABEL", "labels": ["payments", "smoke"]},
            },
        )
        assert explicit_cycle.status_code == 201, explicit_cycle.text
        assert explicit_cycle.json()["scope"]["labels"] == ["payments", "smoke"]
        explicit_cycle_id = explicit_cycle.json()["id"]
        explicit_item_id = client.get(f"/api/cycles/{explicit_cycle_id}/items").json()[
            "items"
        ][0]["id"]
        empty_steps = client.get(
            f"/api/cycle-items/{explicit_item_id}/executions/WEB/steps"
        )
        assert empty_steps.status_code == 200, empty_steps.text
        assert empty_steps.json()["status"] == "UNEXECUTED"
        assert empty_steps.json()["steps"][0]["precondition"] == (
            "The payment sandbox is reachable."
        )
        planned_steps = client.patch(
            f"/api/cycle-items/{explicit_item_id}/executions/WEB/steps",
            json={
                "results": [
                    {"sequence": 1, "status": "PASS", "note": "Observed"}
                ],
                "executorLabel": "QA Kim",
            },
        )
        assert planned_steps.status_code == 409
        assert planned_steps.json()["code"] == "CYCLE_EXECUTION_NOT_STARTED"
        explicit_start = client.post(
            f"/api/cycles/{explicit_cycle_id}/start",
            json={"baseResourceVersion": 1, "actorLabel": "QA Kim"},
        )
        assert explicit_start.status_code == 200, explicit_start.text
        recorded_steps = client.patch(
            f"/api/cycle-items/{explicit_item_id}/executions/WEB/steps",
            json={
                "results": [
                    {"sequence": 1, "status": "PASS", "note": "Observed"}
                ],
                "executorLabel": "QA Kim",
            },
        )
        assert recorded_steps.status_code == 200, recorded_steps.text
        assert recorded_steps.json()["status"] == "PASS"
        assert recorded_steps.json()["recordedStepCount"] == 1
        assert client.get(f"/api/cycles/{explicit_cycle_id}").json()["status"] == (
            "IN_PROGRESS"
        )
        active_delete = client.request(
            "DELETE",
            f"/api/cycles/{explicit_cycle_id}",
            json={
                "baseResourceVersion": explicit_start.json()["cycle"]["resourceVersion"],
                "reason": "remove the active cycle without erasing its ledger",
            },
        )
        assert active_delete.status_code == 200, active_delete.text
        assert active_delete.json()["operation"] == "DELETE"
        assert active_delete.json()["deletedAt"] is not None
        assert client.get(f"/api/cycles/{explicit_cycle_id}").status_code == 404
        assert client.get(f"/api/cycles/{explicit_cycle_id}/items").status_code == 404
        with clean_api_database.connect() as connection:
            soft_deleted = connection.execute(
                text(
                    "SELECT c.deleted_at IS NOT NULL AS deleted, "
                    "count(DISTINCT i.id) AS items, count(DISTINCT a.id) AS attempts "
                    "FROM test_cycles c "
                    "LEFT JOIN cycle_items i ON i.cycle_id = c.id "
                    "LEFT JOIN execution_attempts a ON a.cycle_item_id = i.id "
                    "WHERE c.id = :cycle_id GROUP BY c.deleted_at"
                ),
                {"cycle_id": uuid.UUID(explicit_cycle_id.removeprefix("cycle_"))},
            ).mappings().one()
        assert soft_deleted == {"deleted": True, "items": 1, "attempts": 1}

        deleted_cycles = client.get(
            "/api/cycles", params={"deletedOnly": "true", "limit": 10}
        )
        assert deleted_cycles.status_code == 200, deleted_cycles.text
        deleted_item = next(
            item
            for item in deleted_cycles.json()["items"]
            if item["id"] == explicit_cycle_id
        )
        assert deleted_item["status"] == "IN_PROGRESS"
        assert deleted_item["deletedAt"] is not None
        assert all(
            item["id"] != explicit_cycle_id
            for item in client.get("/api/cycles").json()["items"]
        )
        restored = client.post(
            f"/api/cycles/{explicit_cycle_id}/restore",
            json={
                "baseResourceVersion": deleted_item["resourceVersion"],
                "reason": "restore the preserved execution ledger",
            },
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["operation"] == "RESTORE"
        assert restored.json()["cycle"]["status"] == "IN_PROGRESS"
        assert restored.json()["cycle"]["deletedAt"] is None
        assert client.get(f"/api/cycles/{explicit_cycle_id}").status_code == 200
        restore_active_again = client.post(
            f"/api/cycles/{explicit_cycle_id}/restore",
            json={
                "baseResourceVersion": restored.json()["cycle"]["resourceVersion"],
                "reason": "must reject an already active cycle",
            },
        )
        assert restore_active_again.status_code == 409
        assert restore_active_again.json()["code"] == "CYCLE_RESTORE_CONFLICT"

        cycle_payload = {
            "name": "August checkout",
            "description": "Pinned regression",
            "startDate": "2026-08-21",
            "scope": {
                "type": "WORK_ITEM",
                "workItemId": work_item_id,
                "workItemKey": "WORK-301",
            },
        }
        created = client.post(
            "/api/cycles",
            json=cycle_payload,
            headers={"Idempotency-Key": "cycle-create-301"},
        )
        assert created.status_code == 201, created.text
        cycle = created.json()
        assert (
            client.post(
                "/api/cycles",
                json=cycle_payload,
                headers={"Idempotency-Key": "cycle-create-301"},
            ).json()
            == cycle
        )
        cycle_id = cycle["id"]
        unfiltered_cycles = client.get("/api/cycles", params={"limit": 1})
        planned_cycles = client.get(
            "/api/cycles", params={"statuses": "PLANNED", "limit": 1}
        )
        assert unfiltered_cycles.status_code == planned_cycles.status_code == 200
        assert unfiltered_cycles.json()["statusCounts"] == {
            "PLANNED": 1,
            "IN_PROGRESS": 1,
            "COMPLETED": 0,
        }
        assert planned_cycles.json()["statusCounts"] == unfiltered_cycles.json()[
            "statusCounts"
        ]
        assert planned_cycles.json()["total"] == 1
        planned_item = client.get(f"/api/cycles/{cycle_id}/items").json()["items"][0]
        cycle_item_id = planned_item["id"]
        initial_pinned_version_id = planned_item["pinnedVersionId"]

        planned_progress = client.get(f"/api/cycles/{cycle_id}/progress")
        assert planned_progress.status_code == 409
        assert planned_progress.json()["code"] == "CYCLE_EXECUTION_NOT_STARTED"
        prestart_attempt = client.post(
            f"/api/cycle-items/{cycle_item_id}/attempts", json={"platform": "WEB"}
        )
        assert prestart_attempt.status_code == 409
        assert prestart_attempt.json()["code"] == "CYCLE_EXECUTION_NOT_STARTED"

        update_payload = {
            **{
                key: value
                for key, value in case_payload.items()
                if key not in {"workItemId", "status"}
            },
            "baseVersion": 1,
            "summary": "Checkout succeeds exactly once",
            "changeReason": "Clarify duplicate protection.",
        }
        version_two = client.patch(f"/api/test-cases/{case['id']}", json=update_payload)
        assert version_two.status_code == 200, version_two.text
        version_two_id = version_two.json()["currentVersion"]["id"]
        refreshed_item = client.post(
            f"/api/cycle-items/{cycle_item_id}/snapshot/refresh",
            json={
                "baseResourceVersion": 1,
                "expectedPinnedVersionId": initial_pinned_version_id,
                "targetVersionId": version_two_id,
                "confirm": True,
                "reason": "Pin the clarified version before execution.",
            },
        )
        assert refreshed_item.status_code == 200, refreshed_item.text
        assert refreshed_item.json()["cycleItem"]["pinnedVersionId"] == version_two_id
        assert {
            field["field"]
            for field in refreshed_item.json()["diff"]["fieldChanges"]
        } >= {"summary"}
        assert (
            refreshed_item.json()["previousPinnedVersionId"]
            == initial_pinned_version_id
        )
        assert client.get(f"/api/cycles/{cycle_id}").json()["resourceVersion"] == 2

        version_three_payload = {
            **update_payload,
            "baseVersion": 2,
            "summary": "Checkout succeeds with a receipt",
            "changeReason": "Document the receipt requirement.",
        }
        version_three = client.patch(
            f"/api/test-cases/{case['id']}", json=version_three_payload
        )
        assert version_three.status_code == 200, version_three.text
        version_three_id = version_three.json()["currentVersion"]["id"]
        refreshed_cycle = client.post(
            f"/api/cycles/{cycle_id}/snapshot/refresh",
            json={
                "baseResourceVersion": 2,
                "confirm": True,
                "reason": "Explicitly refresh all planned snapshots.",
            },
        )
        assert refreshed_cycle.status_code == 200, refreshed_cycle.text
        assert refreshed_cycle.json()["resourceVersion"] == 3
        assert (
            client.get(f"/api/cycles/{cycle_id}/items").json()["items"][0][
                "pinnedVersionId"
            ]
            == version_three_id
        )

        stale_update = client.patch(
            f"/api/cycles/{cycle_id}",
            json={"baseResourceVersion": 2, "name": "Stale name"},
        )
        assert stale_update.status_code == 409
        assert stale_update.json()["code"] == "VERSION_CONFLICT"

        planned_item_id = client.get(f"/api/cycles/{cycle_id}/items").json()["items"][0]["id"]
        planned_result = client.patch(
            f"/api/cycle-items/{planned_item_id}/executions/WEB/steps",
            json={"results": [{"sequence": 1, "status": "PASS"}]},
        )
        assert planned_result.status_code == 409
        assert planned_result.json()["code"] == "CYCLE_EXECUTION_NOT_STARTED"

        started = client.post(
            f"/api/cycles/{cycle_id}/start",
            json={"baseResourceVersion": 3, "actorLabel": "QA Lee"},
        )
        assert started.status_code == 200, started.text
        started_body = started.json()
        assert started_body["cycle"]["status"] == "IN_PROGRESS"
        assert started_body["cycle"]["startedAt"] is not None
        item = started_body["items"][0]
        cycle_item_id = item["id"]
        pinned_version_id = item["pinnedVersionId"]
        assert pinned_version_id == version_three_id

        version_four_payload = {
            **version_three_payload,
            "baseVersion": 3,
            "summary": "Checkout succeeds and sends email",
            "changeReason": "Add post-start email behavior.",
        }
        version_four = client.patch(
            f"/api/test-cases/{case['id']}", json=version_four_payload
        )
        assert version_four.status_code == 200, version_four.text
        latest_version_id = version_four.json()["currentVersion"]["id"]

        pinned = client.get(f"/api/cycles/{cycle_id}/items").json()["items"][0]
        assert pinned["pinnedVersionId"] == pinned_version_id
        assert pinned["pinnedVersion"]["summary"] == "Checkout succeeds with a receipt"
        assert pinned["latestVersion"]["versionId"] == latest_version_id
        assert pinned["isLatestVersion"] is False
        refresh_locked = client.post(
            f"/api/cycle-items/{cycle_item_id}/snapshot/refresh",
            json={
                "baseResourceVersion": started_body["cycle"]["resourceVersion"],
                "expectedPinnedVersionId": pinned_version_id,
                "targetVersionId": latest_version_id,
                "confirm": True,
                "reason": "Try refresh after start.",
            },
        )
        assert refresh_locked.status_code == 409
        assert refresh_locked.json()["code"] == "CYCLE_SCOPE_LOCKED"
        scope_locked = client.patch(
            f"/api/cycles/{cycle_id}/scope",
            json={
                "baseResourceVersion": started_body["cycle"]["resourceVersion"],
                "scope": cycle_payload["scope"],
                "reason": "Try changing locked scope.",
            },
        )
        assert scope_locked.status_code == 409
        assert scope_locked.json()["code"] == "CYCLE_SCOPE_LOCKED"

        active_details = client.patch(
            f"/api/cycles/{cycle_id}",
            json={
                "baseResourceVersion": started_body["cycle"]["resourceVersion"],
                "name": "Checkout active regression",
                "description": "Updated while execution remains in progress.",
            },
        )
        assert active_details.status_code == 200, active_details.text
        active_cycle = active_details.json()
        assert active_cycle["status"] == "IN_PROGRESS"
        assert active_cycle["name"] == "Checkout active regression"
        assert active_cycle["description"] == "Updated while execution remains in progress."
        assert active_cycle["resourceVersion"] == started_body["cycle"]["resourceVersion"] + 1
        assert client.get(f"/api/cycles/{cycle_id}/items").json()["items"][0][
            "pinnedVersionId"
        ] == pinned_version_id

        attempt = client.post(
            f"/api/cycle-items/{cycle_item_id}/attempts",
            json={"platform": "WEB", "executorLabel": "QA Lee", "note": "Chrome"},
        )
        assert attempt.status_code == 201, attempt.text
        attempt_body = attempt.json()
        assert attempt_body["attemptNo"] == 1 and attempt_body["status"] == "UNEXECUTED"
        duplicate_attempt = client.post(
            f"/api/cycle-items/{cycle_item_id}/attempts", json={"platform": "WEB"}
        )
        assert duplicate_attempt.status_code == 409
        assert duplicate_attempt.json()["code"] == "EXECUTION_PLATFORM_DUPLICATE"

        missing_actual = client.post(
            f"/api/execution-attempts/{attempt_body['id']}/result",
            json={"status": "FAIL"},
        )
        assert missing_actual.status_code == 422
        assert missing_actual.json()["code"] == "ACTUAL_RESULT_REQUIRED"
        failed = client.post(
            f"/api/execution-attempts/{attempt_body['id']}/result",
            json={"status": "FAIL", "actualResult": "HTTP 500"},
        )
        assert failed.status_code == 200, failed.text

        retry = client.post(
            f"/api/cycle-items/{cycle_item_id}/attempts/retry",
            json={"previousAttemptId": attempt_body["id"], "note": "Retry fixed build"},
        )
        assert retry.status_code == 201, retry.text
        assert retry.json()["previousAttempt"]["actualResult"] == "HTTP 500"
        assert retry.json()["attempt"]["attemptNo"] == 2
        retried_id = retry.json()["attempt"]["id"]
        passed = client.post(
            f"/api/execution-attempts/{retried_id}/result", json={"status": "PASS"}
        )
        assert passed.status_code == 200, passed.text

        web_history = client.get(
            f"/api/cycle-items/{cycle_item_id}/attempts", params={"platforms": "WEB"}
        )
        assert web_history.status_code == 200, web_history.text
        assert [row["attemptNo"] for row in web_history.json()["items"]] == [2, 1]
        assert web_history.json()["items"][1]["actualResult"] == "HTTP 500"
        assert web_history.json()["latestAttemptIds"] == [retried_id]
        assert [row["isLatest"] for row in web_history.json()["items"]] == [True, False]

        aos_attempt = client.post(
            f"/api/cycle-items/{cycle_item_id}/attempts",
            json={"platform": "AOS", "executorLabel": "QA Lee"},
        )
        assert aos_attempt.status_code == 201, aos_attempt.text
        blocked = client.post(
            f"/api/execution-attempts/{aos_attempt.json()['id']}/result",
            json={"status": "BLOCKED"},
        )
        assert blocked.status_code == 200, blocked.text
        assert blocked.json()["note"] is None

        progress = client.get(f"/api/cycles/{cycle_id}/progress")
        assert progress.status_code == 200, progress.text
        progress_body = progress.json()
        assert progress_body["excludesPlanned"] is True
        assert progress_body["latestAttemptOnly"] is True
        assert progress_body["overall"]["values"]["counts"] == {
            "total": 3,
            "UNEXECUTED": 1,
            "PASS": 1,
            "FAIL": 0,
            "BLOCKED": 1,
        }
        web_bucket = next(
            row for row in progress_body["byPlatform"] if row["key"] == "WEB"
        )
        assert web_bucket["values"]["counts"]["PASS"] == 1
        assert web_bucket["values"]["counts"]["FAIL"] == 0

        overall = client.get(f"/api/cycles/{cycle_id}/progress/overall")
        platforms = client.get(f"/api/cycles/{cycle_id}/progress/platform")
        owners = client.get(f"/api/cycles/{cycle_id}/progress/owner")
        work_items = client.get(f"/api/cycles/{cycle_id}/progress/work-item")
        labels = client.get(f"/api/cycles/{cycle_id}/progress/label")
        for response in (overall, platforms, owners, work_items, labels):
            assert response.status_code == 200, response.text
        assert (
            overall.json()["values"]["counts"]
            == progress_body["overall"]["values"]["counts"]
        )
        assert [row["key"] for row in platforms.json()] == ["WEB", "AOS", "IOS"]
        assert owners.json()[0]["key"] == "QA Kim"
        assert work_items.json()[0]["key"] == work_item_id
        assert {row["key"] for row in labels.json()} == {"payments", "smoke"}

        filtered = client.get(
            f"/api/cycles/{cycle_id}/progress",
            params=[("platforms", "WEB"), ("labels", "payments")],
        )
        assert filtered.status_code == 200, filtered.text
        assert filtered.json()["overall"]["values"]["counts"] == {
            "total": 1,
            "UNEXECUTED": 0,
            "PASS": 1,
            "FAIL": 0,
            "BLOCKED": 0,
        }

        warning = client.post(
            f"/api/cycles/{cycle_id}/complete",
            json={
                "baseResourceVersion": active_cycle["resourceVersion"],
                "acknowledgeIncomplete": False,
            },
        )
        assert warning.status_code == 422
        assert warning.json()["code"] == "CYCLE_INCOMPLETE_WARNING"
        completed = client.post(
            f"/api/cycles/{cycle_id}/complete",
            json={
                "baseResourceVersion": active_cycle["resourceVersion"],
                "acknowledgeIncomplete": True,
            },
        )
        assert completed.status_code == 200, completed.text
        completed_body = completed.json()
        assert completed_body["cycle"]["status"] == "COMPLETED"
        assert completed_body["warnings"][0]["unexecutedCount"] == 1

        completed_details = client.patch(
            f"/api/cycles/{cycle_id}",
            json={
                "baseResourceVersion": completed_body["cycle"]["resourceVersion"],
                "name": "Completed cycles stay locked",
            },
        )
        assert completed_details.status_code == 409
        assert completed_details.json()["code"] == "CYCLE_EXECUTION_LOCKED"

        locked_result = client.post(
            f"/api/execution-attempts/{retried_id}/result", json={"status": "PASS"}
        )
        assert locked_result.status_code == 409
        assert locked_result.json()["code"] == "CYCLE_EXECUTION_LOCKED"
        locked_attempt = client.post(
            f"/api/cycle-items/{cycle_item_id}/attempts", json={"platform": "IOS"}
        )
        assert locked_attempt.status_code == 409
        assert locked_attempt.json()["code"] == "CYCLE_EXECUTION_LOCKED"

        reopened = client.post(
            f"/api/cycles/{cycle_id}/reopen",
            json={
                "baseResourceVersion": completed_body["cycle"]["resourceVersion"],
                "reason": "Verify the hotfix.",
            },
        )
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["cycle"]["status"] == "IN_PROGRESS"
        reopened_progress = client.get(f"/api/cycles/{cycle_id}/progress")
        assert reopened_progress.status_code == 200, reopened_progress.text
        assert reopened_progress.json()["overall"] == progress_body["overall"]

        with clean_api_database.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO test_cases "
                    "(id, workspace_id, work_item_id, category_id, case_key, "
                    "current_version_id, status, sort_order, version) "
                    "SELECT md5('a2-case-' || i)::uuid, "
                    "'00000000-0000-7000-8000-000000000100'::uuid, :work_item_id, "
                    ":category_id, 'TC-' || lpad(i::text, 6, '0'), "
                    "md5('a2-version-' || i)::uuid, 'ACTIVE', i - 1, 1 "
                    "FROM generate_series(2, 1000) AS i"
                ),
                {"work_item_id": work_item_uuid, "category_id": category_uuid},
            )
            connection.execute(
                text(
                    "INSERT INTO test_case_versions "
                    "(id, test_case_id, version_number, status, summary, precondition, "
                    "expected_result_mode, expected_result, priority, assignee_label, "
                    "source_type, source_metadata, change_reason) "
                    "SELECT md5('a2-version-' || i)::uuid, md5('a2-case-' || i)::uuid, "
                    "1, 'ACTIVE', 'Queue case ' || i, 'Ready', 'PER_STEP', NULL, "
                    "'MEDIUM', 'QA Kim', 'API', '{}'::jsonb, 'A2 1000-row fixture' "
                    "FROM generate_series(2, 1000) AS i"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO test_steps "
                    "(id, test_case_version_id, sequence, precondition, action, expected_result) "
                    "SELECT md5('a2-step-' || i)::uuid, md5('a2-version-' || i)::uuid, "
                    "1, 'Ready', 'Execute queue case', 'Passes' "
                    "FROM generate_series(2, 1000) AS i"
                )
            )

        large_cycle = client.post(
            "/api/cycles",
            json={
                "name": "A2 1,000-case execution queue",
                "description": "Cursor pagination performance fixture",
                "startDate": "2026-08-24",
                "scope": {
                    "type": "WORK_ITEM",
                    "workItemId": work_item_id,
                    "workItemKey": "WORK-301",
                },
            },
        )
        assert large_cycle.status_code == 201, large_cycle.text
        started_at = perf_counter()
        first_queue_page = client.get(
            f"/api/cycles/{large_cycle.json()['id']}/items", params={"limit": 100}
        )
        elapsed = perf_counter() - started_at
        assert first_queue_page.status_code == 200, first_queue_page.text
        assert first_queue_page.json()["total"] == 1000
        assert len(first_queue_page.json()["items"]) == 100
        assert first_queue_page.json()["nextCursor"] is not None
        assert elapsed < 2.0


def test_cycle_permanent_delete_blocks_case_then_removes_execution_ledger(
    clean_api_database: Engine,
) -> None:
    application = create_app(validate_database=False, database_engine=clean_api_database)
    with TestClient(application) as client:
        category = client.post(
            "/api/folders",
            json={"parentId": None, "name": "Purge cycles", "sortOrder": 0},
        ).json()["rootFolderId"]
        work_item = client.post(
            "/api/work-items",
            json={
                "key": "WORK-902",
                "categoryId": category,
                "summary": "Cycle retention",
                "labels": ["retention"],
            },
        ).json()
        test_case = client.post(
            "/api/test-cases", json=_case_payload(work_item["id"])
        ).json()
        cycle = client.post(
            "/api/cycles",
            json={
                "name": "Cycle pending purge",
                "description": "Referential-integrity fixture",
                "startDate": "2026-08-25",
                "scope": {
                    "type": "WORK_ITEM",
                    "workItemId": work_item["id"],
                    "workItemKey": work_item["key"],
                },
            },
        ).json()
        cycle_item_id = client.get(f"/api/cycles/{cycle['id']}/items").json()[
            "items"
        ][0]["id"]
        linked_issue = client.post(
            "/api/issues",
            json={
                "idempotencyKey": "cycle-purge-issue-001",
                "summary": "Evidence linked to a cycle item",
                "precondition": "The cycle item exists.",
                "steps": [
                    {
                        "sequence": 1,
                        "action": "Inspect the cycle item.",
                        "expectedResult": "Evidence remains linked.",
                    }
                ],
                "expectedResult": "Evidence remains linked.",
                "actualResult": "The cycle is pending purge.",
                "severity": "MEDIUM",
                "initialLinks": [
                    {"type": "CYCLE_ITEM", "cycleItemId": cycle_item_id}
                ],
            },
        )
        assert linked_issue.status_code == 201, linked_issue.text

        active_cycle_purge = client.request(
            "DELETE",
            f"/api/cycles/{cycle['id']}/permanent",
            json={"baseResourceVersion": 1, "reason": "Must soft-delete first."},
        )
        assert active_cycle_purge.status_code == 409
        assert active_cycle_purge.json()["code"] == "PERMANENT_DELETE_CONFLICT"

        deleted_case = client.request(
            "DELETE",
            f"/api/test-cases/{test_case['id']}",
            json={
                "baseResourceVersion": 1,
                "reason": "Case retention cleanup.",
                "source": {"type": "API"},
            },
        ).json()["testCase"]
        blocked_case = client.request(
            "DELETE",
            f"/api/test-cases/{test_case['id']}/permanent",
            json={
                "baseResourceVersion": deleted_case["resourceVersion"],
                "reason": "Still pinned by a cycle.",
                "source": {"type": "API"},
            },
        )
        assert blocked_case.status_code == 409, blocked_case.text
        assert blocked_case.json()["details"]["references"] == {
            "cycleItems": 1,
            "issueLinks": 1,
        }

        deleted_cycle = client.request(
            "DELETE",
            f"/api/cycles/{cycle['id']}",
            json={"baseResourceVersion": 1, "reason": "Cycle retention cleanup."},
        )
        assert deleted_cycle.status_code == 200, deleted_cycle.text
        assert client.get(f"/api/cycles/{cycle['id']}").status_code == 404
        deleted_detail = client.get(
            f"/api/cycles/{cycle['id']}", params={"includeDeleted": "true"}
        )
        assert deleted_detail.status_code == 200, deleted_detail.text
        assert deleted_detail.json()["deletedAt"] is not None
        assert client.get(f"/api/cycles/{cycle['id']}/items").status_code == 404
        deleted_items = client.get(
            f"/api/cycles/{cycle['id']}/items",
            params={"includeDeleted": "true"},
        )
        assert deleted_items.status_code == 200, deleted_items.text
        assert deleted_items.json()["items"][0]["id"] == cycle_item_id

        blocked_cycle = client.request(
            "DELETE",
            f"/api/cycles/{cycle['id']}/permanent",
            json={
                "baseResourceVersion": deleted_detail.json()["resourceVersion"],
                "reason": "Cycle retention period expired.",
            },
        )
        assert blocked_cycle.status_code == 409, blocked_cycle.text
        assert blocked_cycle.json()["details"]["references"] == {"issueLinks": 1}

        deleted_issue = client.request(
            "DELETE",
            f"/api/issues/{linked_issue.json()['id']}",
            json={
                "baseResourceVersion": linked_issue.json()["resourceVersion"],
                "reason": "Remove linked evidence.",
            },
        )
        assert deleted_issue.status_code == 200, deleted_issue.text
        assert client.get(
            f"/api/issues/{linked_issue.json()['id']}/links"
        ).status_code == 404
        deleted_issue_links = client.get(
            f"/api/issues/{linked_issue.json()['id']}/links",
            params={"includeDeleted": "true"},
        )
        assert deleted_issue_links.status_code == 200, deleted_issue_links.text
        assert deleted_issue_links.json()["total"] == 1
        purged_issue = client.request(
            "DELETE",
            f"/api/issues/{linked_issue.json()['id']}/permanent",
            json={
                "baseResourceVersion": deleted_issue.json()["resourceVersion"],
                "reason": "Linked evidence retention period expired.",
            },
        )
        assert purged_issue.status_code == 200, purged_issue.text

        purged_cycle = client.request(
            "DELETE",
            f"/api/cycles/{cycle['id']}/permanent",
            json={
                "baseResourceVersion": deleted_detail.json()["resourceVersion"],
                "reason": "Cycle retention period expired.",
            },
        )
        assert purged_cycle.status_code == 200, purged_cycle.text
        assert purged_cycle.json()["operation"] == "PERMANENT_DELETE"
        assert client.get(
            f"/api/cycles/{cycle['id']}", params={"includeDeleted": "true"}
        ).status_code == 404

        purged_case = client.request(
            "DELETE",
            f"/api/test-cases/{test_case['id']}/permanent",
            json={
                "baseResourceVersion": deleted_case["resourceVersion"],
                "reason": "Cycle reference was removed.",
                "source": {"type": "API"},
            },
        )
        assert purged_case.status_code == 200, purged_case.text

    decoded_cycle = uuid.UUID(cycle["id"].removeprefix("cycle_"))
    with clean_api_database.connect() as connection:
        counts = connection.execute(
            text(
                "SELECT "
                "(SELECT count(*) FROM test_cycles WHERE id = :id) AS cycles, "
                "(SELECT count(*) FROM cycle_items WHERE cycle_id = :id) AS items, "
                "(SELECT count(*) FROM audit_logs WHERE entity_type = 'TEST_CYCLE' "
                "AND entity_id = :id AND action = 'PERMANENT_DELETE') AS audits"
            ),
            {"id": decoded_cycle},
        ).mappings().one()
    assert counts == {"cycles": 0, "items": 0, "audits": 1}
