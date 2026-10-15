"""Export the FastAPI schema with deterministic JSON formatting."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))

from tc_api.main import app  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output: Path = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

