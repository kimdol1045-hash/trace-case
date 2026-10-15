"""Normalize a historical QA CSV into a deterministic, privacy-safe JSONL preview.

This command is intentionally dry-run only. It never connects to or writes to a
database; near-duplicate and vector handling belong to the later retrieval stage.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tc_backend.knowledge import HistoricalCorpusError, normalize_historical_csv, preview_jsonl


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path, help="UTF-8 historical QA CSV to preview")
    parser.add_argument(
        "--output",
        type=Path,
        help="write JSONL to this file instead of stdout (still preview-only)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        preview = normalize_historical_csv(args.csv_path.expanduser().resolve())
        rendered = preview_jsonl(preview)
        if args.output is None:
            sys.stdout.write(rendered)
        else:
            args.output.expanduser().resolve().write_text(rendered, encoding="utf-8", newline="")
    except (HistoricalCorpusError, OSError) as exc:
        print(f"historical corpus preview failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
