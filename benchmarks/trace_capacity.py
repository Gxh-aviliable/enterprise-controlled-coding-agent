"""Reproducible synthetic single-writer TraceStore capacity probe (no real user data)."""

import argparse
import json
import os
import statistics
import tempfile
import time
from pathlib import Path

from enterprise_agent.observability.trace_store import TraceStore


def measure(counts=(100, 500, 2000)):
    previous = os.environ.get("WORKSPACE_BASE")
    results = []
    try:
        with tempfile.TemporaryDirectory(prefix="trace-capacity-") as directory:
            os.environ["WORKSPACE_BASE"] = directory
            store = TraceStore()
            for count in counts:
                trace_id = f"capacity-{count}"
                store.start_trace(user_id=90001, trace_id=trace_id, session_id="synthetic", request_summary="capacity")
                durations = []
                for index in range(count):
                    start = time.perf_counter()
                    store.record_event(
                        user_id=90001,
                        trace_id=trace_id,
                        event_type="tool",
                        name="read_file",
                        data={"path": f"module_{index % 40}.py", "preview": "synthetic " * 200},
                    )
                    durations.append((time.perf_counter() - start) * 1000)
                start = time.perf_counter()
                trace = store.get_trace(90001, trace_id)
                query_ms = (time.perf_counter() - start) * 1000
                size = store._path(90001, trace_id).stat().st_size
                results.append(
                    {
                        "event_count": len(trace["events"]),
                        "writes": count,
                        "bytes": size,
                        "write_p50_ms": statistics.median(durations),
                        "write_p95_ms": sorted(durations)[int(len(durations) * 0.95) - 1],
                        "query_ms": query_ms,
                        "projected_1000_tasks_bytes": size * 1000,
                    }
                )
    finally:
        if previous is None:
            os.environ.pop("WORKSPACE_BASE", None)
        else:
            os.environ["WORKSPACE_BASE"] = previous
    return {
        "synthetic": True,
        "single_writer": True,
        "results": results,
        "migration_trigger": "multiple API writers, or production p95 write >=50ms at observed task size",
        "note": "1000-task disk growth is a projection, not observed traffic.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = measure()
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
