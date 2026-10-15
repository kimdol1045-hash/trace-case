"""Stable identifiers and enum-like constants owned by the database boundary."""

from __future__ import annotations

import uuid

DEFAULT_WORKSPACE_ID = uuid.UUID("00000000-0000-7000-8000-000000000100")
AI_INBOX_FOLDER_ID = uuid.UUID("00000000-0000-7000-8000-000000000101")
AI_INBOX_NAME = "AI 생성함"
AI_INBOX_NORMALIZED_NAME = "ai 생성함"
AI_INBOX_SYSTEM_KIND = "AI_GENERATED_INBOX"

KEY_ENTITY_TYPES = ("TEST_CASE", "ISSUE")
AUDIT_SOURCE_TYPES = ("UI", "API", "SYSTEM", "SLACK", "WORKER")
