#!/usr/bin/env python3
"""Export a deterministic E4-wrapped T08 review candidate from PostgreSQL."""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from tc_backend.generation.quality_snapshot import (
    QualitySnapshotExportError,
    export_quality_review_candidate,
)
from tc_backend.privacy.review_candidates import canonical_json_bytes


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Read-only PostgreSQL export of an E4-wrapped automatic-generation "
            "quality review CANDIDATE"
        )
    )
    result.add_argument("--root-job-id", action="append", required=True)
    result.add_argument("--snapshot-output", type=Path, required=True)
    result.add_argument("--capture-environment", choices=("staging", "production"), required=True)
    result.add_argument(
        "--json-output", default="artifacts/automatic-generation-quality.json"
    )
    result.add_argument(
        "--junit-output", default="artifacts/automatic-generation-quality.junit.xml"
    )
    result.add_argument(
        "--markdown-output", default="artifacts/automatic-generation-quality.md"
    )
    result.add_argument("--database-url-env", default="TC_DATABASE_URL")
    result.add_argument(
        "--source-seal-key-env", default="TC_QUALITY_REVIEW_SOURCE_SEAL_KEY"
    )
    result.add_argument(
        "--source-seal-key-id", default="quality-review-source-v1"
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    engine = None
    try:
        root_ids = tuple(_root_uuid(value) for value in arguments.root_job_id)
        snapshot_output = arguments.snapshot_output
        _preflight_snapshot_output(snapshot_output)
        database_url = os.environ.get(arguments.database_url_env)
        if not database_url:
            raise QualitySnapshotExportError("DATABASE_URL_ENV_MISSING")
        source_seal_key = os.environ.get(arguments.source_seal_key_env)
        if source_seal_key is None or len(source_seal_key.encode("utf-8")) < 32:
            raise QualitySnapshotExportError("SOURCE_SEAL_KEY_ENV_MISSING_OR_INVALID")
        parsed_url = make_url(database_url)
        if not parsed_url.drivername.startswith("postgresql"):
            raise QualitySnapshotExportError("DATABASE_URL_NOT_POSTGRESQL")

        engine = create_engine(database_url, poolclass=NullPool)
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            transaction = connection.begin()
            try:
                connection.execute(text("SET TRANSACTION READ ONLY"))
                with Session(bind=connection, expire_on_commit=False) as session:
                    _private_snapshot, envelope = export_quality_review_candidate(
                        session,
                        root_job_ids=root_ids,
                        capture_environment=arguments.capture_environment,
                        report_outputs={
                            "json": arguments.json_output,
                            "junit": arguments.junit_output,
                            "markdown": arguments.markdown_output,
                        },
                        key=source_seal_key.encode("utf-8"),
                        key_id=arguments.source_seal_key_id,
                    )
                    payload = canonical_json_bytes(envelope) + b"\n"
            finally:
                transaction.rollback()
        with snapshot_output.open("xb") as output:
            output.write(payload)
        print(
            "automatic-generation quality candidate export PASS "
            f"roots={len(root_ids)} envelope=review-candidate-envelope/1.0.0 "
            "attestation=NOT_RUN releaseCertified=false"
        )
        return 0
    except (QualitySnapshotExportError, OSError, UnicodeError, ValueError) as error:
        code = error.code if isinstance(error, QualitySnapshotExportError) else type(error).__name__
        print(f"automatic-generation quality candidate export error: {code}", file=sys.stderr)
        return 2
    except Exception as error:  # pragma: no cover - final fail-closed boundary
        print(
            "automatic-generation quality candidate export safety error: "
            f"{type(error).__name__}",
            file=sys.stderr,
        )
        return 2
    finally:
        if engine is not None:
            engine.dispose()


def _root_uuid(value: str) -> uuid.UUID:
    raw = value.removeprefix("generation_job_")
    try:
        return uuid.UUID(raw)
    except ValueError as error:
        raise QualitySnapshotExportError("ROOT_JOB_ID_INVALID") from error


def _preflight_snapshot_output(path: Path) -> None:
    if path.suffix != ".json":
        raise QualitySnapshotExportError("SNAPSHOT_OUTPUT_SUFFIX_INVALID")
    if path.exists():
        raise QualitySnapshotExportError("SNAPSHOT_OUTPUT_EXISTS")
    if not path.parent.is_dir():
        raise QualitySnapshotExportError("SNAPSHOT_OUTPUT_PARENT_MISSING")


if __name__ == "__main__":
    raise SystemExit(main())
