from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tc_api.object_storage_runtime import ObjectStorageRuntime
from tc_backend.db.models import AttachmentOwnerType


def test_upload_claim_round_trip_and_deterministic_object_key() -> None:
    runtime = ObjectStorageRuntime(signing_secret=b"x" * 32, setup_error=None)
    now = datetime(2026, 8, 24, 5, 0, tzinfo=UTC)
    owner_id = uuid.uuid4()
    claim, token = runtime.create_claim(
        owner_type=AttachmentOwnerType.ISSUE,
        owner_id=owner_id,
        file_name="evidence.txt",
        mime_type="text/plain",
        size_bytes=4,
        sha256="a" * 64,
        idempotency_key="attachment-upload-001",
        now=now,
    )
    replay, _ = runtime.create_claim(
        owner_type=AttachmentOwnerType.ISSUE,
        owner_id=owner_id,
        file_name="evidence.txt",
        mime_type="text/plain",
        size_bytes=4,
        sha256="a" * 64,
        idempotency_key="attachment-upload-001",
        now=now,
    )

    assert claim.object_key == replay.object_key
    assert runtime.decode_claim(token, now=now) == claim


def test_upload_claim_rejects_tampering_and_expiration() -> None:
    runtime = ObjectStorageRuntime(signing_secret=b"x" * 32, setup_error=None)
    now = datetime(2026, 8, 24, 5, 0, tzinfo=UTC)
    _, token = runtime.create_claim(
        owner_type=AttachmentOwnerType.ISSUE,
        owner_id=uuid.uuid4(),
        file_name="evidence.txt",
        mime_type="text/plain",
        size_bytes=4,
        sha256="a" * 64,
        idempotency_key="attachment-upload-001",
        now=now,
    )

    with pytest.raises(ValueError, match="signature"):
        runtime.decode_claim(token[:-1] + ("A" if token[-1] != "A" else "B"), now=now)
    with pytest.raises(ValueError, match="expired"):
        runtime.decode_claim(token, now=now + timedelta(minutes=6))


@pytest.mark.parametrize(
    ("storage_bytes", "expected"),
    [(69, "NONE"), (70, "WARNING_70"), (85, "WARNING_85"), (95, "WARNING_95"), (100, "EXCEEDED")],
)
def test_storage_observation_thresholds(storage_bytes: int, expected: str) -> None:
    runtime = ObjectStorageRuntime(
        signing_secret=b"x" * 32,
        storage_budget_bytes=100,
        class_a_request_budget=100,
        class_b_request_budget=100,
        storage_usd_per_gb_month=1.0,
    )
    assert runtime.observation(storage_bytes=storage_bytes).warning_level == expected


def test_storage_observation_counts_request_classes_without_secrets() -> None:
    runtime = ObjectStorageRuntime(
        signing_secret=b"x" * 32,
        storage_usd_per_gb_month=2,
        class_a_usd_per_million=3,
        class_b_usd_per_million=4,
    )
    runtime.observe_request("CLASS_A_LIST")
    runtime.observe_request("CLASS_B_GET")
    runtime.observe_direct_put()
    observation = runtime.observation(storage_bytes=1_000_000_000)
    assert observation.class_a_requests == 2
    assert observation.class_b_requests == 1
    assert observation.estimated_monthly_cost_usd == 2.00001
    assert observation.estimate_quality == "PROCESS_LOCAL_CONFIGURED_ESTIMATE"
