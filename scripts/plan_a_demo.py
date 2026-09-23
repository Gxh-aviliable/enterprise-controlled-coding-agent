"""Real-model Plan A demonstration using a synthetic fixture and Docker shell.

PYTHONPATH=. .venv/bin/python scripts/plan_a_demo.py
This is a diagnostic demo with an explicitly strengthened parallelism prompt,
not the official benchmark or a claim of quality/speed gains over Single mode.
"""

import asyncio
import copy
import json
import os
import tempfile
from pathlib import Path


def verify_demo(result):
    events = result["trace"]["events"]
    starts = [e for e in events if e["type"] == "child_task" and e["status"] == "running"]
    ends = [e for e in events if e["type"] == "child_task" and e["status"] == "succeeded"]
    writes = [e for e in events if e["type"] == "tool" and e["name"] == "edit_file" and e["status"] == "success"]
    checks = {
        "two_children_succeeded": len(starts) == len(ends) == 2,
        "actual_parallel_overlap": bool(starts and ends)
        and max(e["timestamp"] for e in starts) < min(e["timestamp"] for e in ends),
        "lead_write_after_children": bool(writes and ends)
        and min(e["timestamp"] for e in writes) > max(e["timestamp"] for e in ends),
        "same_snapshot": len({e["data"].get("snapshot_id") for e in ends}) == 1
        and all(e["data"].get("snapshot_id") for e in ends),
        "task_passed": result.get("status") == "passed",
    }
    return checks


async def main():
    from benchmarks.run import run_agent_case
    from enterprise_agent.config.settings import settings

    if not settings.get_effective_api_key():
        raise RuntimeError("Configure a model API key before running this real-model demo")
    case = next(
        c
        for c in json.loads(Path("benchmarks/v2/cases.json").read_text())["cases"]
        if c["id"] == "easy.edit.fix_subtract"
    )
    case = copy.deepcopy(case)
    case["prompt"] += (
        " Use Plan A: first issue TWO independent delegate_task calls in the SAME model response, "
        "both with profile=explore. Tell the first to read math_utils.py ONCE and report the fix; "
        "tell the second to read tests/test_math_utils.py ONCE and report the expected behavior. "
        "These exact paths are supplied: no directory listing, search or repeated reads are needed. "
        "Wait for both results, then make the edit yourself and run python -B -m pytest -q. "
        "Do not create task-board entries; give a concise evidence-based final report."
    )
    settings.ENABLE_MULTI_AGENT = True
    settings.ENABLE_LONG_TERM_MEMORY = False
    settings.AGENT_EXECUTOR = "docker"
    settings.SANDBOX_UID = os.getuid()
    settings.SANDBOX_GID = os.getgid()
    settings.MAX_AGENT_ROUNDS = 10
    settings.TASK_TOKEN_BUDGET = 150000
    with tempfile.TemporaryDirectory(prefix="plan-a-real-demo-") as root:
        root = str(Path(root).resolve())
        os.environ["WORKSPACE_BASE"] = str(Path(root) / "workspaces")
        settings.SANDBOX_STAGING_BASE = str(Path(root) / "stage")
        settings.SANDBOX_HOST_STAGING_BASE = settings.SANDBOX_STAGING_BASE
        settings.SANDBOX_DEPLOYMENT = "plan-a-demo"
        result = await run_agent_case(case, 1, "multi")
    checks = verify_demo(result)
    result["plan_a_checks"] = checks
    output = Path("docs/release-evidence/plan-a-real-model-demo.json")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: result.get(k)
                for k in [
                    "status",
                    "task_status",
                    "duration_ms",
                    "response_summary",
                    "infrastructure_error",
                    "system_error",
                ]
            },
            ensure_ascii=False,
        )
    )
    print("Evidence:", output)
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
