#!/usr/bin/env python3
"""Reproducible, dependency-free Ollama candidate benchmark.

The script talks only to the configured Ollama HTTP endpoint. It does not pull
models, use credentials, or call a paid/external inference API.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_PROMPT = (
    "다음 요구사항에서 테스트케이스 1개를 JSON 한 줄로 작성하세요. "
    "요구사항: 저장 버튼은 필수 제목이 비어 있으면 비활성화된다. "
    '키는 title, precondition, steps, expected_result만 사용하세요.'
)
EMBED_INPUTS = [
    "저장 버튼은 필수 제목이 비어 있으면 비활성화된다.",
    "제목을 입력하면 저장 버튼을 누를 수 있다.",
    "실패한 테스트 실행에서 내부 이슈를 생성한다.",
    "Slack thread의 결정 변경 이력을 보존한다.",
]


class BenchmarkError(RuntimeError):
    """A benchmark precondition or HTTP call failed."""


@dataclass(frozen=True)
class Client:
    base_url: str
    timeout_s: float

    def request(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise BenchmarkError(f"{path} failed: {exc}") from exc

    def stream_generate(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=data,
            headers={
                "Accept": "application/x-ndjson",
                "Content-Type": "application/json",
            },
        )
        started = time.perf_counter()
        first_output_s: float | None = None
        response_headers_s: float | None = None
        final: dict[str, Any] = {}
        output_parts: list[str] = []
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                response_headers_s = time.perf_counter() - started
                for raw_line in response:
                    if not raw_line.strip():
                        continue
                    event = json.loads(raw_line)
                    if event.get("error"):
                        raise BenchmarkError(str(event["error"]))
                    chunk = str(event.get("response") or event.get("thinking") or "")
                    if chunk:
                        output_parts.append(chunk)
                        if first_output_s is None:
                            first_output_s = time.perf_counter() - started
                    if event.get("done"):
                        final = event
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise BenchmarkError(f"/api/generate failed: {exc}") from exc

        wall_s = time.perf_counter() - started
        eval_count = integer_or_none(final.get("eval_count"))
        eval_duration_s = ns_to_s(final.get("eval_duration"))
        return {
            "response_headers_s": rounded(response_headers_s),
            "first_output_s": rounded(first_output_s),
            "wall_s": rounded(wall_s),
            "ollama_total_s": rounded(ns_to_s(final.get("total_duration"))),
            "load_s": rounded(ns_to_s(final.get("load_duration"))),
            "prompt_eval_s": rounded(ns_to_s(final.get("prompt_eval_duration"))),
            "generation_s": rounded(eval_duration_s),
            "prompt_tokens": integer_or_none(final.get("prompt_eval_count")),
            "generated_tokens": eval_count,
            "tokens_per_second": rounded(
                eval_count / eval_duration_s
                if eval_count is not None
                and eval_count >= 2
                and eval_duration_s is not None
                and eval_duration_s >= 0.01
                else None
            ),
            "done_reason": final.get("done_reason"),
            "output_chars": len("".join(output_parts)),
        }


def ns_to_s(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value) / 1_000_000_000
    return None


def integer_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def rounded(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None else None


def percentile(values: Iterable[float | None], fraction: float) -> float | None:
    clean = sorted(value for value in values if value is not None)
    if not clean:
        return None
    index = (len(clean) - 1) * fraction
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return rounded(clean[lower])
    return rounded(clean[lower] + (clean[upper] - clean[lower]) * (index - lower))


def model_matches(installed_name: str, requested_name: str) -> bool:
    if installed_name == requested_name:
        return True
    return installed_name.removesuffix(":latest") == requested_name.removesuffix(
        ":latest"
    )


def find_installed_model(models: list[dict[str, Any]], requested: str) -> dict[str, Any] | None:
    return next(
        (
            model
            for model in models
            if model_matches(str(model.get("name", "")), requested)
        ),
        None,
    )


def model_summary(show: dict[str, Any]) -> dict[str, Any]:
    info = show.get("model_info") or {}
    selected_info = {
        key: value
        for key, value in info.items()
        if key.endswith(
            (
                ".context_length",
                ".embedding_length",
                ".block_count",
                ".attention.head_count",
                ".attention.head_count_kv",
            )
        )
        or key in {"general.architecture", "general.parameter_count"}
    }
    return {
        "details": show.get("details") or {},
        "capabilities": show.get("capabilities") or [],
        "model_info": selected_info,
        "context_length": max(
            (
                int(value)
                for key, value in info.items()
                if key.endswith(".context_length") and isinstance(value, (int, float))
            ),
            default=None,
        ),
        "embedding_length": max(
            (
                int(value)
                for key, value in info.items()
                if key.endswith(".embedding_length")
                and isinstance(value, (int, float))
            ),
            default=None,
        ),
    }


def total_memory_bytes() -> int | None:
    try:
        if platform.system() == "Darwin":
            return int(
                subprocess.check_output(
                    ["sysctl", "-n", "hw.memsize"], text=True
                ).strip()
            )
        if platform.system() == "Linux":
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return None


def available_memory_bytes() -> int | None:
    try:
        if platform.system() == "Linux":
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
        if platform.system() == "Darwin":
            output = subprocess.check_output(["vm_stat"], text=True)
            page_match = re.search(r"page size of (\d+) bytes", output)
            if not page_match:
                return None
            pages = 0
            for label in (
                "Pages free",
                "Pages inactive",
                "Pages speculative",
                "Pages purgeable",
            ):
                match = re.search(rf"^{re.escape(label)}:\s+(\d+)\.", output, re.MULTILINE)
                if match:
                    pages += int(match.group(1))
            return pages * int(page_match.group(1))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return None


def cpu_model() -> str:
    try:
        if platform.system() == "Darwin":
            return subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"], text=True
            ).strip()
        if platform.system() == "Linux":
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return platform.processor() or "unknown"


def ollama_process_rss_bytes() -> int | None:
    try:
        output = subprocess.check_output(
            ["ps", "-axo", "pid=,rss=,command="], text=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    total_kib = 0
    for line in output.splitlines():
        parts = line.strip().split(maxsplit=2)
        if len(parts) != 3:
            continue
        command = parts[2].lower()
        if "ollama" not in command or "benchmark_ollama.py" in command:
            continue
        try:
            total_kib += int(parts[1])
        except ValueError:
            continue
    return total_kib * 1024


def loaded_models(client: Client) -> list[dict[str, Any]]:
    result = client.request("/api/ps")
    return [
        {
            "name": model.get("name"),
            "size_bytes": integer_or_none(model.get("size")),
            "size_vram_bytes": integer_or_none(model.get("size_vram")),
            "context_length": integer_or_none(model.get("context_length")),
            "expires_at": model.get("expires_at"),
        }
        for model in result.get("models", [])
    ]


def memory_sample(client: Client) -> dict[str, Any]:
    return {
        "host_available_bytes_estimate": available_memory_bytes(),
        "ollama_process_rss_bytes": ollama_process_rss_bytes(),
        "loaded_models": loaded_models(client),
    }


def unload_generation_model(client: Client, model: str) -> None:
    client.request(
        "/api/generate",
        {"model": model, "prompt": "", "stream": False, "keep_alive": 0},
    )


def unload_embedding_model(client: Client, model: str) -> None:
    client.request(
        "/api/embed",
        {"model": model, "input": "", "keep_alive": 0},
    )


def run_generation(
    client: Client,
    model: str,
    num_ctx: int,
    num_predict: int,
    warm_runs: int,
) -> dict[str, Any]:
    unload_generation_model(client, model)
    payload = {
        "model": model,
        "prompt": DEFAULT_PROMPT,
        "stream": True,
        "think": False,
        "format": "json",
        "keep_alive": "5m",
        "options": {
            "num_ctx": num_ctx,
            "num_predict": num_predict,
            "temperature": 0,
            "seed": 42,
        },
    }
    print(f"benchmarking cold generation: {model}", file=sys.stderr)
    cold = client.stream_generate(payload)
    warm: list[dict[str, Any]] = []
    for index in range(warm_runs):
        print(
            f"benchmarking warm generation {index + 1}/{warm_runs}: {model}",
            file=sys.stderr,
        )
        warm.append(client.stream_generate(payload))
    return {
        "cold": cold,
        "warm_runs": warm,
        "warm_p50_first_output_s": percentile(
            (run["first_output_s"] for run in warm), 0.5
        ),
        "warm_p95_first_output_s": percentile(
            (run["first_output_s"] for run in warm), 0.95
        ),
        "warm_p50_wall_s": percentile((run["wall_s"] for run in warm), 0.5),
        "warm_p50_tokens_per_second": rounded(
            statistics.median(
                run["tokens_per_second"]
                for run in warm
                if run["tokens_per_second"] is not None
            )
            if any(run["tokens_per_second"] is not None for run in warm)
            else None
        ),
    }


def run_embedding(client: Client, model: str, warm_runs: int) -> dict[str, Any]:
    unload_embedding_model(client, model)
    payload = {
        "model": model,
        "input": EMBED_INPUTS,
        "truncate": False,
        "keep_alive": "5m",
    }
    runs: list[dict[str, Any]] = []
    for index in range(warm_runs + 1):
        label = "cold" if index == 0 else f"warm {index}/{warm_runs}"
        print(f"benchmarking {label} embedding: {model}", file=sys.stderr)
        started = time.perf_counter()
        result = client.request("/api/embed", payload)
        wall_s = time.perf_counter() - started
        embeddings = result.get("embeddings") or []
        dimensions = sorted(
            {len(vector) for vector in embeddings if isinstance(vector, list)}
        )
        runs.append(
            {
                "wall_s": rounded(wall_s),
                "ollama_total_s": rounded(ns_to_s(result.get("total_duration"))),
                "load_s": rounded(ns_to_s(result.get("load_duration"))),
                "prompt_eval_s": rounded(
                    ns_to_s(result.get("prompt_eval_duration"))
                ),
                "input_count": len(embeddings),
                "dimensions": dimensions,
                "prompt_tokens": integer_or_none(result.get("prompt_eval_count")),
            }
        )
    return {
        "cold": runs[0],
        "warm_runs": runs[1:],
        "warm_p50_wall_s": percentile(
            (run["wall_s"] for run in runs[1:]), 0.5
        ),
    }


def run_context_smoke(
    client: Client, model: str, num_ctx: int, repetitions: int
) -> dict[str, Any]:
    print(
        f"running near-{num_ctx} context smoke with {repetitions} repeated tokens",
        file=sys.stderr,
    )
    result = client.stream_generate(
        {
            "model": model,
            "prompt": " test" * repetitions,
            "raw": True,
            "stream": True,
            "think": False,
            "keep_alive": "5m",
            "options": {
                "num_ctx": num_ctx,
                "num_predict": 1,
                "temperature": 0,
                "seed": 42,
            },
        }
    )
    minimum_tokens = math.floor(num_ctx * 0.90)
    result["requested_num_ctx"] = num_ctx
    result["prompt_repetitions"] = repetitions
    result["minimum_pass_tokens"] = minimum_tokens
    result["passed"] = bool(
        result["prompt_tokens"] is not None
        and result["prompt_tokens"] >= minimum_tokens
    )
    return result


def fallback_triggers(report: dict[str, Any], args: argparse.Namespace) -> list[str]:
    triggers: list[str] = []
    generation_model = report["models"]["generation"]
    embedding_model = report["models"]["embedding"]
    generation = report["benchmarks"]["generation"]
    embedding = report["benchmarks"]["embedding"]
    total_memory = report["host"]["memory_total_bytes"]

    if generation_model["context_length"] is None or generation_model[
        "context_length"
    ] < args.num_ctx:
        triggers.append(f"generation context metadata is below {args.num_ctx}")
    if generation["cold"]["first_output_s"] is None or generation["cold"][
        "first_output_s"
    ] > args.max_cold_first_output_s:
        triggers.append(
            f"cold first output exceeds {args.max_cold_first_output_s:.1f}s"
        )
    if generation["warm_p50_first_output_s"] is None or generation[
        "warm_p50_first_output_s"
    ] > args.max_warm_first_output_s:
        triggers.append(
            f"warm p50 first output exceeds {args.max_warm_first_output_s:.1f}s"
        )
    if generation["warm_p50_wall_s"] is None or generation[
        "warm_p50_wall_s"
    ] > args.max_generation_wall_s:
        triggers.append(
            f"warm p50 generation exceeds {args.max_generation_wall_s:.1f}s"
        )
    if embedding_model["embedding_length"] != args.expected_embedding_dimension:
        triggers.append(
            "embedding dimension differs from "
            f"{args.expected_embedding_dimension}; migration/reindex is required"
        )
    observed_dimensions = embedding["cold"]["dimensions"]
    if observed_dimensions != [args.expected_embedding_dimension]:
        triggers.append(
            f"observed embedding dimensions are {observed_dimensions}, not "
            f"[{args.expected_embedding_dimension}]"
        )
    if total_memory:
        largest_loaded_set = max(
            (
                sum(model.get("size_bytes") or 0 for model in sample["loaded_models"])
                for sample in report["memory_samples"].values()
            ),
            default=0,
        )
        fraction = largest_loaded_set / total_memory
        report["decision"]["peak_loaded_model_fraction"] = rounded(fraction)
        if fraction > args.max_loaded_model_fraction:
            triggers.append(
                "loaded model allocation exceeds "
                f"{args.max_loaded_model_fraction:.0%} of host memory"
            )
    context_smoke = report["benchmarks"].get("context_smoke")
    if context_smoke is not None and not context_smoke["passed"]:
        triggers.append("near-32k runtime context smoke did not reach the pass token count")
    if (
        context_smoke is not None
        and context_smoke["wall_s"] is not None
        and context_smoke["wall_s"] > args.max_context_smoke_wall_s
    ):
        triggers.append(
            "near-32k context smoke exceeds "
            f"{args.max_context_smoke_wall_s:.1f}s"
        )
    return triggers


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
    )
    parser.add_argument("--generation-model", default="qwen3:8b")
    parser.add_argument("--embedding-model", default="bge-m3")
    parser.add_argument("--num-ctx", type=int, default=32768)
    parser.add_argument("--num-predict", type=int, default=96)
    parser.add_argument("--warm-runs", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--context-smoke", action="store_true")
    parser.add_argument("--context-repetitions", type=int, default=30000)
    parser.add_argument("--expected-embedding-dimension", type=int, default=1024)
    parser.add_argument("--max-cold-first-output-s", type=float, default=15.0)
    parser.add_argument("--max-warm-first-output-s", type=float, default=5.0)
    parser.add_argument("--max-generation-wall-s", type=float, default=120.0)
    parser.add_argument("--max-context-smoke-wall-s", type=float, default=120.0)
    parser.add_argument("--max-loaded-model-fraction", type=float, default=0.75)
    parser.add_argument(
        "--output",
        type=Path,
        help="also write the JSON report to this path",
    )
    args = parser.parse_args()
    if args.warm_runs < 1:
        parser.error("--warm-runs must be at least 1")
    if args.num_ctx < 1 or args.num_predict < 1:
        parser.error("--num-ctx and --num-predict must be positive")
    return args


def main() -> int:
    args = parse_args()
    client = Client(args.base_url.rstrip("/"), args.timeout)
    started = datetime.now(timezone.utc)

    version = client.request("/api/version")
    tags = client.request("/api/tags")
    installed_models = tags.get("models") or []
    generation_tag = find_installed_model(installed_models, args.generation_model)
    embedding_tag = find_installed_model(installed_models, args.embedding_model)
    if generation_tag is None:
        raise BenchmarkError(f"generation model is not installed: {args.generation_model}")
    if embedding_tag is None:
        raise BenchmarkError(f"embedding model is not installed: {args.embedding_model}")

    generation_show = model_summary(
        client.request("/api/show", {"model": args.generation_model, "verbose": False})
    )
    embedding_show = model_summary(
        client.request("/api/show", {"model": args.embedding_model, "verbose": False})
    )
    report: dict[str, Any] = {
        "schema_version": 1,
        "started_at": started.isoformat(),
        "finished_at": None,
        "configuration": {
            "base_url": args.base_url,
            "generation_model": args.generation_model,
            "embedding_model": args.embedding_model,
            "num_ctx": args.num_ctx,
            "num_predict": args.num_predict,
            "warm_runs": args.warm_runs,
            "context_smoke": args.context_smoke,
            "context_repetitions": args.context_repetitions,
            "timeout_s": args.timeout,
        },
        "host": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "cpu": cpu_model(),
            "logical_cpus": os.cpu_count(),
            "memory_total_bytes": total_memory_bytes(),
        },
        "ollama": {
            "version": version.get("version"),
            "health": "ok",
        },
        "models": {
            "generation": {
                "requested": args.generation_model,
                "installed_name": generation_tag.get("name"),
                "digest": generation_tag.get("digest"),
                "disk_size_bytes": generation_tag.get("size"),
                **generation_show,
            },
            "embedding": {
                "requested": args.embedding_model,
                "installed_name": embedding_tag.get("name"),
                "digest": embedding_tag.get("digest"),
                "disk_size_bytes": embedding_tag.get("size"),
                **embedding_show,
            },
        },
        "memory_samples": {"before": memory_sample(client)},
        "benchmarks": {},
        "decision": {
            "thresholds": {
                "max_cold_first_output_s": args.max_cold_first_output_s,
                "max_warm_first_output_s": args.max_warm_first_output_s,
                "max_generation_wall_s": args.max_generation_wall_s,
                "max_context_smoke_wall_s": args.max_context_smoke_wall_s,
                "max_loaded_model_fraction": args.max_loaded_model_fraction,
                "expected_embedding_dimension": args.expected_embedding_dimension,
            },
            "fallback_triggers": [],
            "peak_loaded_model_fraction": None,
        },
    }

    report["benchmarks"]["generation"] = run_generation(
        client,
        args.generation_model,
        args.num_ctx,
        args.num_predict,
        args.warm_runs,
    )
    report["memory_samples"]["after_generation"] = memory_sample(client)
    report["benchmarks"]["embedding"] = run_embedding(
        client, args.embedding_model, args.warm_runs
    )
    report["memory_samples"]["after_embedding"] = memory_sample(client)
    if args.context_smoke:
        report["benchmarks"]["context_smoke"] = run_context_smoke(
            client,
            args.generation_model,
            args.num_ctx,
            args.context_repetitions,
        )
        report["memory_samples"]["after_context_smoke"] = memory_sample(client)

    report["decision"]["fallback_triggers"] = fallback_triggers(report, args)
    report["decision"]["status"] = (
        "candidate-pass"
        if not report["decision"]["fallback_triggers"]
        else "fallback-review"
    )
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BenchmarkError as exc:
        print(f"benchmark failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
