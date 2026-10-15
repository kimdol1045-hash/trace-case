from __future__ import annotations

import uuid
from typing import cast

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from tc_backend.db.folders import (
    AutoFolderCaseCounter,
    ZeroFolderCaseCounter,
    normalize_folder_name,
)


def test_folder_normalization_is_nfkc_whitespace_folded_and_casefolded() -> None:
    assert normalize_folder_name("  ＡI   생성함  ") == "ai 생성함"


def test_zero_case_counter_is_sprint_two_compatible() -> None:
    folder_ids = [uuid.uuid4(), uuid.uuid4()]

    assert ZeroFolderCaseCounter().count_direct(cast(Session, None), folder_ids) == {
        folder_ids[0]: 0,
        folder_ids[1]: 0,
    }


def test_auto_case_counter_sql_targets_soft_delete_compatible_shape() -> None:
    sql = str(AutoFolderCaseCounter._count_sql.compile(dialect=postgresql.dialect()))

    assert "FROM test_cases" in sql
    assert "deleted_at IS NULL" in sql
    assert "category_id IN" in sql
