"""Benchmark the Sprint 2 case library on a disposable PostgreSQL database."""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))

from tc_api.main import create_app  # noqa: E402
from tc_backend.db.constants import AI_INBOX_FOLDER_ID, DEFAULT_WORKSPACE_ID  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Load the real Sprint 2 ledger schema with synthetic cases, then measure "
            "tree, list, search, compound-filter and deep-offset API/database queries. "
            "The database name must contain 'test' because the fixture truncates ledger tables."
        )
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("TC_TEST_DATABASE_URL"),
        help="Disposable PostgreSQL URL (defaults to TC_TEST_DATABASE_URL).",
    )
    parser.add_argument("--test-cases", type=int, default=100_000)
    parser.add_argument("--categories", type=int, default=20)
    parser.add_argument("--work-items", type=int, default=1_000)
    parser.add_argument("--tags", type=int, default=20)
    parser.add_argument("--deleted-every", type=int, default=20)
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--target-ms", type=float, default=1_000.0)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> str:
    database_url = args.database_url
    if not isinstance(database_url, str) or not database_url:
        raise SystemExit("--database-url or TC_TEST_DATABASE_URL is required")
    url = make_url(database_url)
    if not url.drivername.startswith("postgresql"):
        raise SystemExit("case benchmark requires PostgreSQL")
    if not url.database or "test" not in url.database.lower():
        raise SystemExit("case benchmark requires a disposable database name containing 'test'")
    positive = {
        "test-cases": args.test_cases,
        "categories": args.categories,
        "work-items": args.work_items,
        "tags": args.tags,
        "deleted-every": args.deleted_every,
        "runs": args.runs,
    }
    invalid = [name for name, value in positive.items() if value < 1]
    if invalid or args.warmups < 0 or args.target_ms <= 0:
        raise SystemExit(f"benchmark arguments must be positive: {', '.join(invalid)}")
    if args.work_items < args.categories:
        raise SystemExit("work-items must be greater than or equal to categories")
    if args.tags < 2:
        raise SystemExit("tags must be at least 2 for the compound ALL-label fixture")
    return database_url


def migrate(database_url: str) -> None:
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")


def load_fixture(engine: Engine, args: argparse.Namespace) -> dict[str, int]:
    active_cases = args.test_cases - (args.test_cases // args.deleted_every)
    with engine.begin() as connection:
        version = int(connection.execute(text("SHOW server_version_num")).scalar_one())
        if version < 160_000:
            raise SystemExit("case benchmark requires PostgreSQL 16 or newer")
        connection.execute(
            text(
                "TRUNCATE TABLE test_case_version_tags, test_case_tags, test_steps, "
                "test_case_versions, test_cases, tags, work_items, folder_aliases, "
                "audit_logs, folders, workspace_key_counters, workspace_settings, "
                "background_jobs CASCADE"
            )
        )
        connection.execute(
            text(
                "INSERT INTO workspace_settings "
                "(id, singleton_key, team_name, case_prefix, issue_prefix, "
                "default_environment, generation_quiet_minutes, version) VALUES "
                "(:workspace_id, 1, 'Sprint 2 Benchmark', 'TC', 'ISSUE', 'staging', 10, 1)"
            ),
            {"workspace_id": DEFAULT_WORKSPACE_ID},
        )
        connection.execute(
            text(
                "INSERT INTO workspace_key_counters (workspace_id, entity_type, next_value) "
                "VALUES (:workspace_id, 'TEST_CASE', :next_case), "
                "(:workspace_id, 'ISSUE', 1)"
            ),
            {"workspace_id": DEFAULT_WORKSPACE_ID, "next_case": args.test_cases + 1},
        )
        connection.execute(
            text(
                "INSERT INTO folders "
                "(id, workspace_id, parent_id, name, normalized_name, sort_order, "
                "version, system_kind) VALUES "
                "(:folder_id, :workspace_id, NULL, 'AI 생성함', 'ai 생성함', "
                "2147483647, 1, 'AI_GENERATED_INBOX')"
            ),
            {"folder_id": AI_INBOX_FOLDER_ID, "workspace_id": DEFAULT_WORKSPACE_ID},
        )
        connection.execute(
            text(
                "INSERT INTO folders "
                "(id, workspace_id, parent_id, name, normalized_name, sort_order, version) "
                "SELECT md5('sprint2-category-' || i)::uuid, :workspace_id, NULL, "
                "'Benchmark category ' || i, 'benchmark category ' || i, i - 1, 1 "
                "FROM generate_series(1, :category_count) AS i"
            ),
            {"workspace_id": DEFAULT_WORKSPACE_ID, "category_count": args.categories},
        )
        connection.execute(
            text(
                "INSERT INTO work_items "
                "(id, workspace_id, category_id, work_item_key, summary, description, "
                "owner_label, labels, version) "
                "SELECT md5('sprint2-work-item-' || i)::uuid, :workspace_id, "
                "md5('sprint2-category-' || (((i - 1) % :category_count) + 1))::uuid, "
                "'WORK-' || lpad(i::text, 6, '0'), 'Benchmark work item ' || i, '', "
                "'benchmark-owner-' || (i % 10), '[]'::jsonb, 1 "
                "FROM generate_series(1, :work_item_count) AS i"
            ),
            {
                "workspace_id": DEFAULT_WORKSPACE_ID,
                "category_count": args.categories,
                "work_item_count": args.work_items,
            },
        )
        connection.execute(
            text(
                "INSERT INTO tags (id, workspace_id, name, normalized_name) "
                "SELECT md5('sprint2-tag-' || i)::uuid, :workspace_id, "
                "'benchmark-tag-' || i, 'benchmark-tag-' || i "
                "FROM generate_series(1, :tag_count) AS i"
            ),
            {"workspace_id": DEFAULT_WORKSPACE_ID, "tag_count": args.tags},
        )
        connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
        connection.execute(
            text(
                "INSERT INTO test_cases "
                "(id, workspace_id, work_item_id, category_id, case_key, current_version_id, "
                "status, sort_order, version, deleted_at, created_at, updated_at) "
                "SELECT md5('sprint2-case-' || i)::uuid, :workspace_id, "
                "md5('sprint2-work-item-' || (((i - 1) % :work_item_count) + 1))::uuid, "
                "md5('sprint2-category-' || ((((i - 1) % :work_item_count) % :category_count) + 1))::uuid, "
                "'TC-' || lpad(i::text, 6, '0'), md5('sprint2-version-' || i)::uuid, "
                "CASE WHEN i % 10 = 0 THEN 'DEPRECATED' ELSE 'ACTIVE' END, "
                "((i - 1) / :work_item_count)::integer, 1, "
                "CASE WHEN i % :deleted_every = 0 THEN now() ELSE NULL END, "
                "now() - ((:case_count - i) || ' seconds')::interval, "
                "now() - ((:case_count - i) || ' seconds')::interval "
                "FROM generate_series(1, :case_count) AS i"
            ),
            {
                "workspace_id": DEFAULT_WORKSPACE_ID,
                "work_item_count": args.work_items,
                "category_count": args.categories,
                "deleted_every": args.deleted_every,
                "case_count": args.test_cases,
            },
        )
        connection.execute(
            text(
                "INSERT INTO test_case_versions "
                "(id, test_case_id, version_number, status, summary, precondition, "
                "expected_result_mode, expected_result, priority, assignee_label, "
                "source_type, source_metadata, change_reason, created_at) "
                "SELECT md5('sprint2-version-' || i)::uuid, "
                "md5('sprint2-case-' || i)::uuid, 1, "
                "CASE WHEN i % 10 = 0 THEN 'DEPRECATED' ELSE 'ACTIVE' END, "
                "'Benchmark case ' || lpad(i::text, 6, '0') || ' unique-' || lpad(i::text, 6, '0'), "
                "'A benchmark account is ready', 'PER_STEP', NULL, "
                "(ARRAY['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'])[(i % 4) + 1], "
                "'benchmark-owner-' || (i % 10), 'API', '{\"fixture\":\"sprint-2\"}'::jsonb, "
                "'Sprint 2 benchmark fixture', "
                "now() - ((:case_count - i) || ' seconds')::interval "
                "FROM generate_series(1, :case_count) AS i"
            ),
            {"case_count": args.test_cases},
        )
        connection.execute(
            text(
                "INSERT INTO test_steps "
                "(id, test_case_version_id, sequence, action, expected_result) "
                "SELECT md5('sprint2-step-' || i)::uuid, md5('sprint2-version-' || i)::uuid, "
                "1, 'Execute benchmark action ' || i, 'Benchmark result ' || i "
                "FROM generate_series(1, :case_count) AS i"
            ),
            {"case_count": args.test_cases},
        )
        for table_name, owner_column in (
            ("test_case_tags", "test_case_id"),
            ("test_case_version_tags", "test_case_version_id"),
        ):
            owner_prefix = "sprint2-case-" if table_name == "test_case_tags" else "sprint2-version-"
            connection.execute(
                text(
                    f"INSERT INTO {table_name} ({owner_column}, tag_id) "
                    f"SELECT md5('{owner_prefix}' || i)::uuid, "
                    "md5('sprint2-tag-' || tag_index)::uuid "
                    "FROM generate_series(1, :case_count) AS i "
                    "CROSS JOIN LATERAL unnest(ARRAY["
                    "((i - 1) % :tag_count) + 1, (i % :tag_count) + 1"
                    "]) AS labels(tag_index)"
                ),
                {"case_count": args.test_cases, "tag_count": args.tags},
            )
        connection.execute(
            text(
                "ANALYZE folders; ANALYZE work_items; ANALYZE test_cases; "
                "ANALYZE test_case_versions; ANALYZE test_steps; ANALYZE tags; "
                "ANALYZE test_case_tags; ANALYZE test_case_version_tags"
            )
        )
        counts = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM test_cases) AS cases, "
                "(SELECT count(*) FROM test_cases WHERE deleted_at IS NULL) AS active, "
                "(SELECT count(*) FROM test_case_versions) AS versions, "
                "(SELECT count(*) FROM test_steps) AS steps"
            )
        ).mappings().one()
    actual = {name: int(value) for name, value in counts.items()}
    expected = {
        "cases": args.test_cases,
        "active": active_cases,
        "versions": args.test_cases,
        "steps": args.test_cases,
    }
    if actual != expected:
        raise RuntimeError(f"fixture count mismatch: expected {expected}, got {actual}")
    return actual


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def measure(
    action: Callable[[], int],
    *,
    warmups: int,
    runs: int,
    target_ms: float,
) -> dict[str, Any]:
    for _ in range(warmups):
        action()
    durations: list[float] = []
    result_count = 0
    for _ in range(runs):
        started = time.perf_counter()
        result_count = action()
        durations.append((time.perf_counter() - started) * 1_000)
    p95_ms = percentile(durations, 0.95)
    return {
        "resultCount": result_count,
        "runs": runs,
        "p50Ms": round(statistics.median(durations), 2),
        "p95Ms": round(p95_ms, 2),
        "maxMs": round(max(durations), 2),
        "targetMs": target_ms,
        "meetsTarget": p95_ms <= target_ms,
    }


def api_scenarios(engine: Engine, args: argparse.Namespace) -> dict[str, dict[str, Any]]:
    category_id = f"folder_{_fixture_uuid(engine, 'sprint2-category-1')}"
    work_item_id = f"work_item_{_fixture_uuid(engine, 'sprint2-work-item-1')}"
    app = create_app(validate_database=False, database_engine=engine)
    with TestClient(app) as client:
        def request(path: str, params: Any = None) -> int:
            response = client.get(path, params=params)
            if response.status_code != 200:
                raise RuntimeError(f"GET {response.url} failed: {response.status_code} {response.text}")
            body = response.json()
            return int(body.get("total", len(body.get("items", []))))

        scenarios: dict[str, Callable[[], int]] = {
            "tree": lambda: request("/api/folders/tree"),
            "listWorkItemOrder": lambda: request("/api/test-cases", {"limit": 50}),
            "listUpdatedDesc": lambda: request(
                "/api/test-cases", {"limit": 50, "sort": "UPDATED_AT_DESC"}
            ),
            "searchKeyRare": lambda: request(
                "/api/test-cases", {"limit": 50, "search": "tc-099991"}
            ),
            "searchSummaryRare": lambda: request(
                "/api/test-cases", {"limit": 50, "search": "unique-099991"}
            ),
            "searchSummaryCommon": lambda: request(
                "/api/test-cases", {"limit": 50, "search": "benchmark"}
            ),
            "compoundFilter": lambda: request(
                "/api/test-cases",
                [
                    ("categoryIds", category_id),
                    ("workItemIds", work_item_id),
                    ("statuses", "ACTIVE"),
                    ("priorities", "MEDIUM"),
                    ("labels", "benchmark-tag-1"),
                    ("labels", "benchmark-tag-2"),
                    ("labelMatch", "ALL"),
                    ("limit", "50"),
                ],
            ),
        }
        return {
            name: measure(
                action,
                warmups=args.warmups,
                runs=args.runs,
                target_ms=args.target_ms,
            )
            for name, action in scenarios.items()
        }


def _fixture_uuid(engine: Engine, seed: str) -> str:
    with engine.connect() as connection:
        value = connection.scalar(text("SELECT md5(:seed)::uuid"), {"seed": seed})
    if value is None:
        raise RuntimeError("failed to generate fixture identifier")
    return value.hex


def explain_facts(engine: Engine, args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    sql = text(
        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "
        "SELECT tc.id FROM test_cases tc "
        "JOIN test_case_versions v ON v.id = tc.current_version_id "
        "WHERE tc.workspace_id = :workspace_id AND tc.deleted_at IS NULL "
        "AND (lower(tc.case_key) LIKE '%unique-099991%' "
        "OR lower(v.summary) LIKE '%unique-099991%') "
        "ORDER BY tc.case_key, tc.id LIMIT 51"
    )
    deep_offset_sql = text(
        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "
        "SELECT tc.id FROM test_cases tc "
        "WHERE tc.workspace_id = :workspace_id AND tc.deleted_at IS NULL "
        "ORDER BY tc.case_key, tc.id OFFSET :offset_rows LIMIT 50"
    )
    with engine.connect() as connection:
        search_plan = connection.execute(sql, {"workspace_id": DEFAULT_WORKSPACE_ID}).scalar_one()[0]
        deep_plan = connection.execute(
            deep_offset_sql,
            {"workspace_id": DEFAULT_WORKSPACE_ID, "offset_rows": max(0, args.test_cases - 5_000)},
        ).scalar_one()[0]
    search_nodes = sorted(set(_plan_nodes(search_plan)))
    deep_nodes = sorted(set(_plan_nodes(deep_plan)))
    facts = {
        "rareSummarySearch": {
            "executionMs": round(float(search_plan["Execution Time"]), 2),
            "nodeTypes": search_nodes,
        },
        "deepOffset": {
            "offsetRows": max(0, args.test_cases - 5_000),
            "executionMs": round(float(deep_plan["Execution Time"]), 2),
            "nodeTypes": deep_nodes,
        },
    }
    suggestions: list[str] = []
    if "Seq Scan:test_case_versions" in search_nodes or "Seq Scan:test_cases" in search_nodes:
        suggestions.append(
            "Enable pg_trgm and benchmark GIN indexes on lower(test_cases.case_key) and "
            "lower(test_case_versions.summary); because the predicate ORs columns across a join, "
            "also evaluate UNION-based candidate IDs or a transactionally maintained current-search projection."
        )
    if facts["deepOffset"]["executionMs"] > 100 or args.test_cases >= 100_000:
        suggestions.append(
            "Replace the encoded offset cursor with a keyset cursor carrying the selected sort tuple; "
            "OFFSET cost grows with page depth even when the first-page p95 target passes."
        )
    suggestions.append(
        "Keep the existing category/deleted, work-item/sort/deleted, version and tag join indexes; "
        "re-run EXPLAIN under production-like concurrent and cold-cache load before adding write-costly indexes."
    )
    return facts, suggestions


def _plan_nodes(plan: dict[str, Any]) -> list[str]:
    node = plan["Plan"] if "Plan" in plan else plan
    relation = node.get("Relation Name")
    label = str(node.get("Node Type", "unknown"))
    result = [f"{label}:{relation}" if relation else label]
    for child in node.get("Plans", []):
        result.extend(_plan_nodes(child))
    return result


def database_facts(engine: Engine) -> dict[str, str]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT current_setting('server_version') AS server_version, "
                "(SELECT extversion FROM pg_extension WHERE extname = 'vector') AS vector_version, "
                "current_database() AS database_name"
            )
        ).mappings().one()
    return {name: str(value) for name, value in row.items()}


def main() -> None:
    args = parse_args()
    database_url = validate_args(args)
    migrate(database_url)
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        fixture = load_fixture(engine, args)
        scenarios = api_scenarios(engine, args)
        explains, suggestions = explain_facts(engine, args)
        report: dict[str, Any] = {
            "fixture": {
                **fixture,
                "categories": args.categories,
                "workItems": args.work_items,
                "tags": args.tags,
            },
            "measurement": {
                "warmups": args.warmups,
                "runs": args.runs,
                "targetMs": args.target_ms,
                "scenarios": scenarios,
                "allMeetTarget": all(item["meetsTarget"] for item in scenarios.values()),
            },
            "explain": explains,
            "indexSuggestions": suggestions,
            "database": database_facts(engine),
            "limitations": [
                "Warm-cache, single-process TestClient measurements are not production concurrency evidence.",
                "Deep pagination is an EXPLAIN measurement and is excluded from the first-page p95 gate.",
            ],
        }
        rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
        print(rendered)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(f"{rendered}\n", encoding="utf-8")
        if not report["measurement"]["allMeetTarget"]:
            raise SystemExit(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
