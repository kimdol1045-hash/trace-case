"""Canonical opaque API identifier encoding at the HTTP boundary."""

from __future__ import annotations

import uuid


class IdentifierError(ValueError):
    def __init__(self, field: str, value: str, expected_prefix: str) -> None:
        self.field = field
        self.value = value
        self.expected_prefix = expected_prefix
        super().__init__(f"{field} must be a {expected_prefix}_ identifier")


def encode_identifier(prefix: str, value: uuid.UUID) -> str:
    return f"{prefix}_{value.hex}"


def decode_identifier(value: str, *, prefix: str, field: str) -> uuid.UUID:
    marker = f"{prefix}_"
    if not value.startswith(marker):
        raise IdentifierError(field, value, prefix)
    try:
        return uuid.UUID(value[len(marker) :])
    except (ValueError, AttributeError) as error:
        raise IdentifierError(field, value, prefix) from error


def encode_resource_identifier(resource_type: str, value: uuid.UUID) -> str:
    prefixes = {
        "AUDIT": "audit",
        "FOLDER": "folder",
        "FOLDER_ALIAS": "alias",
        "WORKSPACE": "workspace",
        "WORKSPACE_SETTINGS": "workspace",
        "TEST_CASE": "case",
        "TEST_CASE_VERSION": "case_version",
        "WORK_ITEM": "work_item",
        "TAG": "tag",
        "TEST_CYCLE": "cycle",
        "CYCLE_ITEM": "cycle_item",
        "EXECUTION_ATTEMPT": "attempt",
    }
    return encode_identifier(prefixes.get(resource_type, resource_type.lower()), value)


def decode_any_resource_identifier(value: str, *, field: str) -> uuid.UUID:
    if "_" not in value:
        raise IdentifierError(field, value, "resource")
    # Resource prefixes can themselves contain underscores (for example
    # work_item and case_version); the UUID is always the final segment.
    _, encoded = value.rsplit("_", 1)
    try:
        return uuid.UUID(encoded)
    except (ValueError, AttributeError) as error:
        raise IdentifierError(field, value, "resource") from error
