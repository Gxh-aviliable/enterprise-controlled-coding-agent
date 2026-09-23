"""Compare frozen locating runs, plus bounded project-context cost on the same fixtures."""

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path

from benchmarks.run import _percentile, load_suite
from enterprise_agent.core.agent.project_context import render_project_context

TARGETS = {
    "easy.understanding.entrypoint": {"src/main.py"},
    "easy.understanding.call_chain": {"api.py"},
    "easy.files.read_config": {"config/service.toml"},
    "easy.understanding.test_command": {"README.md", "pyproject.toml"},
}


def observations(path):
    report = json.loads(path.read_text())
    rows = []
    for case in report["results"]:
        tools = [e for e in case["trace"]["events"] if e["type"] == "tool"]
        reads = [e["data"].get("args_summary", {}).get("path") for e in tools if e["name"] == "read_file"]
        correct = next(
            (
                i
                for i, e in enumerate(tools, 1)
                if e["name"] == "read_file" and e["data"].get("args_summary", {}).get("path") in TARGETS[case["id"]]
            ),
            None,
        )
        rows.append(
            {
                "id": case["id"],
                "status": case["status"],
                "calls_until_target_read": correct,
                "read_calls": len(reads),
                "repeated_paths": len(reads) - len(set(reads)),
                "tool_calls": len(tools),
                "tokens": case["trace"]["metrics"]["total_tokens"],
                "duration_ms": case["duration_ms"],
            }
        )
    return {
        "source": report["run_metadata"]["source_snapshot"],
        "cases": rows,
        "token_median": statistics.median(r["tokens"] for r in rows),
        "p95_duration_ms": _percentile([r["duration_ms"] for r in rows], 0.95),
        "passed": sum(r["status"] == "passed" for r in rows),
    }


def measure(before, after):
    profiles = []
    for case in load_suite()["cases"]:
        if case["id"] not in TARGETS:
            continue
        with tempfile.TemporaryDirectory(prefix="context-profile-") as directory:
            root = Path(directory)
            for relative, content in case["setup_files"].items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            times = []
            for _ in range(100):
                start = time.perf_counter()
                text = render_project_context(root)
                times.append((time.perf_counter() - start) * 1000)
            profiles.append(
                {
                    "id": case["id"],
                    "bytes": len(text.encode()),
                    "p95_render_ms": _percentile(times, 0.95),
                    "cross_task_cache_hits": 0,
                    "iterations": 100,
                }
            )
    return {
        "before": observations(before),
        "experiment": observations(after),
        "profiles": profiles,
        "decision": (
            "Reverted the three-line locating prompt experiment: no token benefit and one extra failure. "
            "No new index/cache/parser dependency."
        ),
        "limits": [
            "Four small synthetic tasks, one model sample per version; not statistical proof.",
            "Repeated paths may be necessary; no automatic suppression of changed content.",
            "Existing bounded per-round project snapshot and artifact/version checks retained.",
            "Cross-task content cache not added; profiling does not justify its invalidation/security overhead.",
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(measure(args.before, args.after), ensure_ascii=False, indent=2) + "\n")
