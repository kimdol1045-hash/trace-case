from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from tc_api.main import create_app
from tc_api.slack_runtime import SlackApiRuntime
from tc_backend.config import Settings, SlackMode
from tc_backend.db.jobs import JobRepository
from tc_backend.db.models import (
    BackgroundJob,
    SlackMessageRevision,
    SlackThread,
    SlackWorkItem,
)
from tc_backend.db.slack import SlackRepository
from tc_backend.infrastructure.slack.config import SlackRuntimeState
from tc_backend.infrastructure.slack.errors import SlackRateLimitError
from tc_backend.infrastructure.slack.http_transport import SlackWebApiHttpTransport
from tc_backend.infrastructure.slack.receivers import (
    SlackHttpEventsReceiver,
    SlackSocketEnvelopeReceiver,
)
from tc_backend.infrastructure.slack.signature import SlackRequestVerifier, sign_slack_request
from tc_backend.infrastructure.slack.testing import DeterministicFakeSlackTransport
from tc_worker.main import Worker
from tc_worker.slack_runtime import SlackWorkerRuntime

pytestmark = pytest.mark.postgres
_SECRET = "sprint-5-test-signing-secret"
_NOW = 1_800_000_000


def test_sprint5_slack_deterministic_postgresql_end_to_end(
    clean_api_database: Engine,
) -> None:
    runtime = SlackApiRuntime(
        state=SlackRuntimeState.CONFIGURED,
        mode=SlackMode.HTTP,
        workspace_id="TQA123",
        app_id="AQA123",
        http_receiver=SlackHttpEventsReceiver(
            SlackRequestVerifier(_SECRET, clock=lambda: _NOW)
        ),
    )
    application = create_app(
        validate_database=False,
        database_engine=clean_api_database,
        slack_runtime=runtime,
    )
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    fake = DeterministicFakeSlackTransport()
    slack_worker = SlackWorkerRuntime.with_transport(session_factory=sessions, transport=fake)
    worker = Worker(
        session_factory=sessions,
        settings=Settings(
            _env_file=None,
            database_url=str(clean_api_database.url),
            encryption_master_key="sprint-5-test-encryption-key-0001",
            job_retry_base_seconds=0,
            job_retry_max_seconds=0,
        ),
        worker_id="sprint-5-e2e-worker",
        handlers=slack_worker.handlers(),
    )

    with TestClient(application) as client:
        verification = {
            "token": "legacy-ingress-only",
            "challenge": "challenge-only-response",
            "type": "url_verification",
        }
        bad_signature = client.post(
            "/integrations/slack/events",
            content=json.dumps(verification).encode(),
            headers={
                "X-Slack-Request-Timestamp": str(_NOW),
                "X-Slack-Signature": "v0=invalid",
            },
        )
        assert bad_signature.status_code == 401
        stale = _post_event(client, verification, timestamp=_NOW - 301)
        assert stale.status_code == 401
        verified = _post_event(client, verification)
        assert verified.json() == {"challenge": "challenge-only-response"}

        shared_payload = _event(
            event_id="EvSharedNormalization001",
            event_ts="1800000000.000010",
            message_ts="1800000000.000010",
            text="shared normalization",
        )
        shared_raw = json.dumps(shared_payload, separators=(",", ":")).encode()
        assert runtime.http_receiver is not None
        http_received = runtime.http_receiver.receive(
            shared_raw,
            headers={
                "X-Slack-Request-Timestamp": str(_NOW),
                "X-Slack-Signature": sign_slack_request(
                    signing_secret=_SECRET,
                    timestamp=_NOW,
                    raw_body=shared_raw,
                ),
            },
        )
        socket_received = SlackSocketEnvelopeReceiver().receive(
            {
                "envelope_id": "socket-shared-normalization-1",
                "type": "events_api",
                "payload": shared_payload,
            }
        )
        assert socket_received.event == http_received.event

        allowlist = client.put(
            "/api/integrations/slack/allowlist",
            json={
                "idempotencyKey": "allowlist-sprint5-001",
                "workspaceId": "TQA123",
                "expectedVersion": 1,
                "channels": [{"channelId": "CQA123", "visibility": "PUBLIC"}],
            },
        )
        assert allowlist.status_code == 200, allowlist.text
        status = client.get("/api/integrations/slack/status")
        assert status.json()["connectionStatus"] == "DEGRADED"
        assert status.json()["privateDefaultDeny"] is True
        assert "no per-user login" in status.headers["X-TC-ACL-Warning"]

        private = _event(
            event_id="EvPrivate001",
            event_ts="1800000000.000050",
            message_ts="1800000000.000050",
            text="private must be denied",
        )
        private_event = private["event"]
        assert isinstance(private_event, dict)
        private_event["channel"] = "GQA123"
        private_event["channel_type"] = "group"
        private_ack = _post_event(client, private).json()
        assert private_ack["outcome"] == "IGNORED"
        assert private_ack["ignoredReason"] == "PRIVATE_DEFAULT_DENY"

        reply = _event(
            event_id="EvReply001",
            event_ts="1800000000.000200",
            message_ts="1800000000.000200",
            thread_ts="1800000000.000100",
            text="선도착 reply",
        )
        acknowledgements = [_post_event(client, reply).json() for _ in range(3)]
        assert [ack["outcome"] for ack in acknowledgements] == [
            "ACCEPTED",
            "DUPLICATE_EVENT_ID",
            "DUPLICATE_EVENT_ID",
        ]
        assert acknowledgements[1]["enqueuedJobIds"] == []
        assert worker.run_once()

        root = _event(
            event_id="EvRoot001",
            event_ts="1800000000.000100",
            message_ts="1800000000.000100",
            text=(
                "개발건: 결제 승인\n요청 메뉴: 결제 > 승인\n"
                "기능 설명: 카드 승인 처리\n요청 내용: 승인 성공 케이스 추가"
            ),
        )
        root_ack = _post_event(client, root).json()
        assert root_ack["outcome"] == "ACCEPTED"
        assert worker.run_once()

        with sessions() as session:
            item = session.scalar(select(SlackWorkItem))
            assert item is not None
            source_version = item.source_version
            item_id = f"slack_work_item_{item.id.hex}"
            item_uuid = item.id

        before_parse = client.get(f"/api/slack-work-items/{item_id}").json()
        quiet_payload = {
            "idempotencyKey": "quiet-window-sprint5-001",
            "workItemId": item_id,
            "lastThreadChangedAt": before_parse["thread"]["latestChangedAt"],
            "quietWindowSeconds": 900,
            "sourceRevisionId": before_parse["revisions"][-1]["id"],
            "expectedGeneration": source_version,
        }
        rescheduled = client.post(
            f"/api/slack-work-items/{item_id}/quiet-window",
            json=quiet_payload,
        )
        assert rescheduled.status_code == 200, rescheduled.text
        assert rescheduled.json()["outcome"] == "RESCHEDULED"
        unchanged = client.post(
            f"/api/slack-work-items/{item_id}/quiet-window",
            json=quiet_payload,
        )
        assert unchanged.json()["outcome"] == "UNCHANGED"
        quiet_job_id = unchanged.json()["job"]["id"]
        cancelled = client.post(
            f"/api/slack-jobs/{quiet_job_id}/cancel",
            json={
                "idempotencyKey": "quiet-cancel-sprint5-001",
                "jobId": quiet_job_id,
                "expectedGeneration": source_version,
                "reason": "MANUAL_CANCEL",
            },
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["cancelled"] is True
        assert cancelled.json()["job"]["state"] == "CANCELLED"

        with sessions.begin() as session:
            JobRepository(session).enqueue(
                job_type="WORK_ITEM_PARSE",
                payload={"workItemId": str(item_uuid), "sourceVersion": source_version},
                run_after=datetime.now(UTC) - timedelta(seconds=1),
                idempotency_key=f"sprint5-e2e-parse:{item_uuid}:{source_version}",
            )
        assert worker.run_once()
        detail = client.get(f"/api/slack-work-items/{item_id}")
        assert detail.status_code == 200, detail.text
        parsed = detail.json()["parse"]
        assert parsed["status"] == "COMPLETE"
        assert parsed["result"]["missingFields"] == []
        assert {
            "developmentItem",
            "requestedMenu",
            "featureDescription",
            "requestDetails",
        }.issubset(parsed["result"])

        # A later delete may arrive before an earlier edit. Both immutable
        # revisions survive and the provider timestamps determine current state.
        deleted_ack = _post_event(client, _deleted_event()).json()
        assert deleted_ack["outcome"] == "ACCEPTED"
        assert worker.run_once()
        edit_ack = _post_event(client, _changed_event()).json()
        assert edit_ack["outcome"] == "ACCEPTED"
        assert worker.run_once()
        reordered = client.get(f"/api/slack-work-items/{item_id}").json()
        reply_revisions = [
            revision
            for revision in reordered["revisions"]
            if revision["messageId"] == reordered["messages"][1]["id"]
        ]
        assert [revision["kind"] for revision in reply_revisions] == [
            "CREATED",
            "EDITED",
            "DELETED",
        ]
        assert reordered["messages"][1]["deleted"] is True
        event_log = client.get("/api/slack-events").json()
        assert {item["normalizedEvent"]["action"] for item in event_log["items"]} == {
            "ROOT",
            "REPLY",
            "EDIT",
            "DELETE",
        }
        assert "legacy-ingress-only" not in json.dumps(event_log)

        fake.add_message(
            channel_id="CQA123",
            thread_ts="1800000000.000100",
            message_ts="1800000001.000100",
            text="누락된 reply",
        )
        thread_id = uuid.UUID(detail.json()["workItem"]["threadId"].removeprefix("slack_thread_"))
        slack_worker.sync_thread({"threadId": str(thread_id)})
        repaired = client.get(f"/api/slack-work-items/{item_id}").json()
        assert [message["position"] for message in repaired["messages"]] == [
            "ROOT",
            "REPLY",
            "REPLY",
        ]

        page_forms: list[dict[str, list[str]]] = []

        def paginated_provider(request: httpx.Request) -> httpx.Response:
            form = parse_qs(request.content.decode())
            page_forms.append(form)
            assert request.url.path.endswith("/conversations.replies")
            if "cursor" not in form:
                return httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "messages": [{
                            "type": "message",
                            "channel": "CQA123",
                            "user": "U_PAGE_1",
                            "text": "pagination reply one",
                            "ts": "1800000002.000100",
                            "thread_ts": "1800000000.000100",
                        }],
                        "response_metadata": {"next_cursor": "page-two"},
                    },
                )
            assert form["cursor"] == ["page-two"]
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "messages": [{
                        "type": "message",
                        "channel": "CQA123",
                        "user": "U_PAGE_2",
                        "text": "pagination reply two",
                        "ts": "1800000003.000100",
                        "thread_ts": "1800000000.000100",
                    }],
                    "response_metadata": {"next_cursor": ""},
                },
            )

        with httpx.Client(transport=httpx.MockTransport(paginated_provider)) as provider_client:
            paginated_worker = SlackWorkerRuntime.with_transport(
                session_factory=sessions,
                transport=SlackWebApiHttpTransport(
                    bot_token="xoxb-" + "deterministic-test",
                    client=provider_client,
                    max_rate_limit_retries=0,
                    max_provider_retries=0,
                ),
            )
            paginated_worker.sync_thread({"threadId": str(thread_id)})
        assert len(page_forms) == 2
        assert page_forms[1]["cursor"] == ["page-two"]
        paginated_detail = client.get(f"/api/slack-work-items/{item_id}").json()
        assert [message["position"] for message in paginated_detail["messages"]] == [
            "ROOT",
            "REPLY",
            "REPLY",
            "REPLY",
            "REPLY",
        ]

        sync_request = client.post(
            f"/api/slack-work-items/{item_id}/sync-thread",
            json={
                "idempotencyKey": "sync-thread-sprint5-dead-001",
                "workspaceId": "TQA123",
                "mode": "INCREMENTAL",
                "sinceCursor": paginated_detail["thread"]["syncCursor"],
            },
        )
        assert sync_request.status_code == 200, sync_request.text
        assert worker.run_once()
        scheduled = slack_worker.schedule_recent_thread_syncs(
            recent_since=datetime(2020, 1, 1, tzinfo=UTC),
            synced_before=datetime.now(UTC) + timedelta(minutes=1),
        )
        repeated_schedule = slack_worker.schedule_recent_thread_syncs(
            recent_since=datetime(2020, 1, 1, tzinfo=UTC),
            synced_before=datetime.now(UTC) + timedelta(minutes=1),
        )
        assert len(scheduled) == 1
        assert repeated_schedule == scheduled
        for attempt in range(5):
            fake.queue_failure(
                SlackRateLimitError(
                    "provider quota",
                    retry_after_seconds=7,
                )
            )
            assert worker.run_once()
            if attempt < 4:
                with sessions.begin() as session:
                    session.execute(
                        update(BackgroundJob)
                        .where(
                            BackgroundJob.job_type == "SLACK_THREAD_SYNC",
                            BackgroundJob.status == "RETRY",
                        )
                        .values(run_after=datetime.now(UTC) - timedelta(seconds=1))
                    )

        dead_jobs = client.get("/api/slack-jobs", params={"state": "DEAD"})
        assert dead_jobs.status_code == 200
        assert len(dead_jobs.json()["items"]) == 1
        dead_job = dead_jobs.json()["items"][0]
        assert dead_job["jobType"] == "THREAD_INCREMENTAL_SYNC"
        assert dead_job["state"] == "DEAD"
        assert dead_job["retry"]["reason"] == "RATE_LIMIT"
        assert dead_job["retry"]["lastHttpStatus"] == 429

    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(SlackWorkItem)) == 1
        assert session.scalar(select(func.count()).select_from(BackgroundJob).where(
            BackgroundJob.job_type == "SLACK_EVENT_PROCESS"
        )) == 4
        assert session.scalar(select(func.count()).select_from(SlackMessageRevision)) == 7
        thread = session.get(SlackThread, thread_id)
        assert thread is not None
        assert thread.sync_status == "DEAD"
        assert thread.retry_at is None


def test_socket_mode_uses_the_same_registration_and_worker_pipeline(
    clean_api_database: Engine,
) -> None:
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    worker = SlackWorkerRuntime(session_factory=sessions)
    with sessions() as session, session.begin():
        SlackRepository(session).put_channel(
            slack_workspace_id="TQA123",
            channel_id="CQA123",
        )
    received = SlackSocketEnvelopeReceiver().receive(
        {
            "envelope_id": "socket-envelope-1",
            "type": "events_api",
            "payload": _event(
                event_id="EvSocket001",
                event_ts="1800000010.000100",
                message_ts="1800000010.000100",
                text="개발건: Socket mode",
            ),
        }
    )
    event_id = worker.register_normalized_event(received.event)
    assert event_id is not None
    worker.process_event({"eventLogId": str(event_id)})

    application = create_app(
        validate_database=False,
        database_engine=clean_api_database,
        slack_runtime=SlackApiRuntime(
            state=SlackRuntimeState.CONFIGURED,
            mode=SlackMode.SOCKET,
            workspace_id="TQA123",
            app_id="AQA123",
        ),
    )
    with TestClient(application) as client:
        events = client.get("/api/slack-events").json()["items"]
        assert events[0]["ingressSource"] == "SOCKET_MODE"
        assert events[0]["processingState"] == "PROCESSED"
        status = client.get("/api/integrations/slack/status").json()
        assert status["ingressMode"] == "SOCKET_MODE"
        assert status["callbackState"] == "NOT_APPLICABLE"


def _event(
    *,
    event_id: str,
    event_ts: str,
    message_ts: str,
    text: str,
    thread_ts: str | None = None,
) -> dict[str, object]:
    return {
        "token": "legacy-ingress-only",
        "team_id": "TQA123",
        "api_app_id": "AQA123",
        "type": "event_callback",
        "event_id": event_id,
        "event_time": int(event_ts.split(".", 1)[0]),
        "event": {
            "type": "message",
            "user": "UQA123",
            "text": text,
            "ts": message_ts,
            "event_ts": event_ts,
            "channel": "CQA123",
            "channel_type": "channel",
            **({"thread_ts": thread_ts} if thread_ts else {}),
        },
    }


def _changed_event() -> dict[str, object]:
    return {
        "token": "legacy-ingress-only",
        "team_id": "TQA123",
        "api_app_id": "AQA123",
        "type": "event_callback",
        "event_id": "EvEdit001",
        "event_time": _NOW,
        "event": {
            "type": "message",
            "subtype": "message_changed",
            "hidden": True,
            "channel": "CQA123",
            "ts": "1800000000.000300",
            "event_ts": "1800000000.000300",
            "channel_type": "channel",
            "message": {
                "type": "message",
                "user": "UQA123",
                "text": "늦게 도착한 과거 edit",
                "ts": "1800000000.000200",
                "thread_ts": "1800000000.000100",
                "edited": {"user": "UQA123", "ts": "1800000000.000300"},
            },
            "previous_message": {
                "type": "message",
                "user": "UQA123",
                "text": "선도착 reply",
                "ts": "1800000000.000200",
                "thread_ts": "1800000000.000100",
            },
        },
    }


def _deleted_event() -> dict[str, object]:
    return {
        "token": "legacy-ingress-only",
        "team_id": "TQA123",
        "api_app_id": "AQA123",
        "type": "event_callback",
        "event_id": "EvDelete001",
        "event_time": _NOW,
        "event": {
            "type": "message",
            "subtype": "message_deleted",
            "hidden": True,
            "channel": "CQA123",
            "deleted_ts": "1800000000.000200",
            "event_ts": "1800000000.000400",
            "channel_type": "channel",
            "previous_message": {
                "type": "message",
                "user": "UQA123",
                "text": "편집된 reply",
                "ts": "1800000000.000200",
                "thread_ts": "1800000000.000100",
            },
        },
    }


def _post_event(
    client: TestClient,
    payload: dict[str, object],
    *,
    timestamp: int = _NOW,
):
    raw = json.dumps(payload, separators=(",", ":")).encode()
    signature = sign_slack_request(
        signing_secret=_SECRET,
        timestamp=timestamp,
        raw_body=raw,
    )
    return client.post(
        "/integrations/slack/events",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": str(timestamp),
            "X-Slack-Signature": signature,
        },
    )
