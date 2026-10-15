"""Benchmark the Sprint 1 folder tree and count query on disposable PostgreSQL."""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from tc_backend.db.constants import AI_INBOX_FOLDER_ID, DEFAULT_WORKSPACE_ID  # noqa: E402
from tc_backend.db.folders import FolderRepository, FolderTreeNode  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a large synthetic folder/case fixture and measure FolderRepository.list_tree. "
            "The database name must contain 'test' because the fixture replaces Sprint 1 tables."
        )
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("TC_TEST_DATABASE_URL"),
        help="Disposable PostgreSQL URL (defaults to TC_TEST_DATABASE_URL).",
    )
    parser.add_argument("--root-folders", type=int, default=50)
    parser.add_argument("--sections-per-root", type=int, default=10)
    parser.add_argument("--leaves-per-section", type=int, default=10)
    parser.add_argument("--test-cases", type=int, default=100_000)
    parser.add_argument("--deleted-every", type=int, default=20)
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--target-ms", type=float, default=1_000.0)
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> str:
    database_url = args.database_url
    if not isinstance(database_url, str) or not database_url:
        raise SystemExit("--database-url or TC_TEST_DATABASE_URL is required")
    url = make_url(database_url)
    if not url.drivername.startswith("postgresql"):
        raise SystemExit("folder benchmark requires PostgreSQL")
    if not url.database or "test" not in url.database.lower():
        raise SystemExit("folder benchmark requires a disposable database name containing 'test'")
    positive_values = {
        "root-folders": args.root_folders,
        "sections-per-root": args.sections_per_root,
        "leaves-per-section": args.leaves_per_section,
        "test-cases": args.test_cases,
        "deleted-every": args.deleted_every,
        "runs": args.runs,
    }
    invalid = [name for name, value in positive_values.items() if value < 1]
    if invalid or args.warmups < 0 or args.target_ms <= 0:
        raise SystemExit(f"benchmark arguments must be positive: {', '.join(invalid)}")
    return database_url


def migrate(database_url: str) -> None:
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")


def load_fixture(engine: Engine, args: argparse.Namespace) -> tuple[int, int]:
    section_count = args.root_folders * args.sections_per_root
    leaf_count = section_count * args.leaves_per_section
    folder_count = args.root_folders + section_count + leaf_count + 1
    active_case_count = args.test_cases - (args.test_cases // args.deleted_every)

    with engine.begin() as connection:
        version = int(connection.execute(text("SHOW server_version_num")).scalar_one())
        if version < 160_000:
            raise SystemExit("folder benchmark requires PostgreSQL 16 or newer")
        connection.execute(text("DROP TABLE IF EXISTS test_cases"))
        connection.execute(text("TRUNCATE folder_aliases, audit_logs, folders CASCADE"))
        connection.execute(
            text(
                "INSERT INTO folders "
                "(id, workspace_id, parent_id, name, normalized_name, sort_order, version) "
                "SELECT md5('sprint1-benchmark-root-' || i)::uuid, :workspace_id, NULL, "
                "'Benchmark root ' || i, 'benchmark root ' || i, i, 1 "
                "FROM generate_series(1, :root_count) AS i"
            ),
            {"workspace_id": DEFAULT_WORKSPACE_ID, "root_count": args.root_folders},
        )
        connection.execute(
            text(
                "INSERT INTO folders "
                "(id, workspace_id, parent_id, name, normalized_name, sort_order, version) "
                "SELECT md5('sprint1-benchmark-section-' || i)::uuid, :workspace_id, "
                "md5('sprint1-benchmark-root-' || (((i - 1) / :per_root) + 1))::uuid, "
                "'Benchmark section ' || i, 'benchmark section ' || i, "
                "(i - 1) % :per_root, 1 "
                "FROM generate_series(1, :section_count) AS i"
            ),
            {
                "workspace_id": DEFAULT_WORKSPACE_ID,
                "per_root": args.sections_per_root,
                "section_count": section_count,
            },
        )
        connection.execute(
            text(
                "INSERT INTO folders "
                "(id, workspace_id, parent_id, name, normalized_name, sort_order, version) "
                "SELECT md5('sprint1-benchmark-leaf-' || i)::uuid, :workspace_id, "
                "md5('sprint1-benchmark-section-' || (((i - 1) / :per_section) + 1))::uuid, "
                "'Benchmark leaf ' || i, 'benchmark leaf ' || i, "
                "(i - 1) % :per_section, 1 "
                "FROM generate_series(1, :leaf_count) AS i"
            ),
            {
                "workspace_id": DEFAULT_WORKSPACE_ID,
                "per_section": args.leaves_per_section,
                "leaf_count": leaf_count,
            },
        )
        connection.execute(
            text(
                "INSERT INTO folders "
                "(id, workspace_id, parent_id, name, normalized_name, sort_order, version, "
                "system_kind) VALUES "
                "(:folder_id, :workspace_id, NULL, 'AI 생성함', 'ai 생성함', "
                "2147483647, 1, 'AI_GENERATED_INBOX')"
            ),
            {"folder_id": AI_INBOX_FOLDER_ID, "workspace_id": DEFAULT_WORKSPACE_ID},
        )
        connection.execute(
            text(
                "CREATE TABLE test_cases ("
                "id uuid PRIMARY KEY, folder_id uuid NOT NULL, deleted_at timestamptz NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO test_cases (id, folder_id, deleted_at) "
                "SELECT md5('sprint1-benchmark-case-' || i)::uuid, "
                "md5('sprint1-benchmark-leaf-' || (((i - 1) % :leaf_count) + 1))::uuid, "
                "CASE WHEN i % :deleted_every = 0 THEN now() ELSE NULL END "
                "FROM generate_series(1, :case_count) AS i"
            ),
            {
                "leaf_count": leaf_count,
                "deleted_every": args.deleted_every,
                "case_count": args.test_cases,
            },
        )
        connection.execute(
            text("CREATE INDEX ix_test_cases_folder_deleted ON test_cases (folder_id, deleted_at)")
        )
        connection.execute(text("ANALYZE folders"))
        connection.execute(text("ANALYZE test_cases"))
    return folder_count, active_case_count


def count_nodes(roots: list[FolderTreeNode]) -> int:
    count = 0
    stack = list(roots)
    while stack:
        node = stack.pop()
        count += 1
        stack.extend(node.children)
    return count


def validate_count_semantics(roots: list[FolderTreeNode], args: argparse.Namespace) -> int:
    section_count = args.root_folders * args.sections_per_root
    leaf_count = section_count * args.leaves_per_section
    expected_leaf_counts = [0] * leaf_count
    for case_index in range(1, args.test_cases + 1):
        if case_index % args.deleted_every != 0:
            expected_leaf_counts[(case_index - 1) % leaf_count] += 1

    max_depth = 0
    for root in roots:
        max_depth = max(max_depth, root.depth)
        if root.id == AI_INBOX_FOLDER_ID:
            if root.direct_test_case_count != 0 or root.subtree_test_case_count != 0:
                raise RuntimeError("system folder count must remain zero")
            continue
        if root.direct_test_case_count != 0:
            raise RuntimeError(f"root direct count mismatch: {root.name}")
        expected_root_count = 0
        for section in root.children:
            max_depth = max(max_depth, section.depth)
            if section.direct_test_case_count != 0:
                raise RuntimeError(f"section direct count mismatch: {section.name}")
            expected_section_count = 0
            for leaf in section.children:
                max_depth = max(max_depth, leaf.depth)
                leaf_index = int(leaf.name.removeprefix("Benchmark leaf "))
                expected_direct = expected_leaf_counts[leaf_index - 1]
                if leaf.direct_test_case_count != expected_direct:
                    raise RuntimeError(
                        f"leaf direct count mismatch: {leaf.name} expected "
                        f"{expected_direct}, got {leaf.direct_test_case_count}"
                    )
                if leaf.subtree_test_case_count != expected_direct:
                    raise RuntimeError(f"leaf subtree count mismatch: {leaf.name}")
                expected_section_count += expected_direct
            if section.subtree_test_case_count != expected_section_count:
                raise RuntimeError(f"section subtree count mismatch: {section.name}")
            expected_root_count += expected_section_count
        if root.subtree_test_case_count != expected_root_count:
            raise RuntimeError(f"root subtree count mismatch: {root.name}")
    return max_depth


def run_query(engine: Engine) -> list[FolderTreeNode]:
    with Session(engine) as session:
        return FolderRepository(session).list_tree()


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def database_facts(engine: Engine) -> dict[str, str]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT current_setting('server_version') AS server_version, "
                "(SELECT extversion FROM pg_extension WHERE extname = 'vector') AS vector_version"
            )
        ).mappings().one()
    return {
        "postgresVersion": str(row["server_version"]),
        "pgvectorVersion": str(row["vector_version"]),
    }


def main() -> None:
    args = parse_args()
    database_url = validate_args(args)
    migrate(database_url)
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        expected_folder_count, expected_active_cases = load_fixture(engine, args)
        for _ in range(args.warmups):
            run_query(engine)
        durations_ms: list[float] = []
        roots: list[FolderTreeNode] = []
        for _ in range(args.runs):
            started = time.perf_counter()
            roots = run_query(engine)
            durations_ms.append((time.perf_counter() - started) * 1_000)

        actual_folder_count = count_nodes(roots)
        actual_active_cases = sum(root.subtree_test_case_count for root in roots)
        if actual_folder_count != expected_folder_count:
            raise RuntimeError(
                f"folder count mismatch: expected {expected_folder_count}, got {actual_folder_count}"
            )
        if actual_active_cases != expected_active_cases:
            raise RuntimeError(
                "active case count mismatch: "
                f"expected {expected_active_cases}, got {actual_active_cases}"
            )
        max_depth = validate_count_semantics(roots, args)

        p95_ms = percentile(durations_ms, 0.95)
        report: dict[str, Any] = {
            "fixture": {
                "folderCount": actual_folder_count,
                "testCaseCount": args.test_cases,
                "activeTestCaseCount": actual_active_cases,
                "maxDepth": max_depth,
            },
            "measurement": {
                "warmups": args.warmups,
                "runs": args.runs,
                "p50Ms": round(statistics.median(durations_ms), 2),
                "p95Ms": round(p95_ms, 2),
                "maxMs": round(max(durations_ms), 2),
                "targetMs": args.target_ms,
                "meetsTarget": p95_ms <= args.target_ms,
            },
            "database": database_facts(engine),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        if p95_ms > args.target_ms:
            raise SystemExit(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
