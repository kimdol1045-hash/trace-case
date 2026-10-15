"""Protocol-only child used to prove generation process isolation."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import BinaryIO

MAX_MESSAGE_BYTES = 4 * 1024 * 1024


def read_message(stream: BinaryIO) -> dict[str, object] | None:
    line = stream.readline(MAX_MESSAGE_BYTES + 1)
    if not line:
        return None
    value = json.loads(line)
    assert isinstance(value, dict)
    return value


def write_message(stream: BinaryIO, request_id: object, payload: object) -> None:
    stream.write(
        json.dumps(
            {"id": request_id, "payload": payload},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
    stream.flush()


def main() -> int:
    input_stream = sys.stdin.buffer
    output_stream = sys.stdout.buffer
    start = read_message(input_stream)
    if start is None:
        return 2
    request_id = start["id"]
    payload = start["payload"]
    assert isinstance(payload, dict)
    environment = payload["postgresEnvironment"]
    runtime = payload["runtime"]
    assert isinstance(environment, dict)
    assert isinstance(runtime, dict)
    for key, value in environment.items():
        assert isinstance(key, str)
        assert isinstance(value, str)
        os.environ[key] = value
    workspace = os.environ["POSTGRES_WORKSPACE"]
    working_directory = Path(str(runtime["workingDirectory"]))
    (working_directory / "child-started").write_text(workspace, encoding="utf-8")
    attestation = payload["attestation"]
    assert isinstance(attestation, dict)
    if str(runtime["bootstrap"]).endswith(":wrong_attestation"):
        attestation = {**attestation, "bindingDigest": "sha256:" + "f" * 64}
    write_message(
        output_stream,
        request_id,
        {"status": "ready", "attestation": attestation},
    )

    while True:
        request = read_message(input_stream)
        if request is None:
            return 0
        request_id = request["id"]
        operation = request["operation"]
        request_payload = request["payload"]
        assert isinstance(request_payload, dict)
        if operation == "shutdown":
            if str(runtime["bootstrap"]).endswith(":hang_shutdown"):
                time.sleep(5)
            write_message(output_stream, request_id, {"status": "ok"})
            return 0
        if operation == "search":
            query = str(request_payload["query"])
            if query.startswith("barrier:"):
                barrier = Path(query.removeprefix("barrier:"))
                barrier.mkdir(parents=True, exist_ok=True)
                (barrier / workspace).write_text("ready", encoding="utf-8")
                deadline = time.monotonic() + 3
                while len(tuple(barrier.iterdir())) < 2 and time.monotonic() < deadline:
                    time.sleep(0.01)
            write_message(
                output_stream,
                request_id,
                {
                    "status": "ok",
                    "value": {
                        "context": f"{workspace}:{query}",
                        "postgresRecordIds": [],
                    },
                },
            )
            continue
        if operation == "index_document":
            write_message(
                output_stream,
                request_id,
                {
                    "status": "ok",
                    "value": {
                        "postgresRecordId": request_payload["postgresRecordId"],
                        "trackId": f"{workspace}-track",
                    },
                },
            )
            continue
        write_message(output_stream, request_id, {"status": "error"})


if __name__ == "__main__":
    raise SystemExit(main())
