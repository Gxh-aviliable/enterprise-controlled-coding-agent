"""One synthetic real-model Docker task, not a benchmark score or production probe.

PYTHONPATH=. .venv/bin/python scripts/shell_policy_demo.py
"""

import asyncio
import json
import os
import tempfile
import uuid
from pathlib import Path

from benchmarks.run import run_agent_case
from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.tool_artifacts import ToolArtifactStore
from enterprise_agent.observability.release import source_manifest
from enterprise_agent.sandbox.executor import DockerExecutor, shutdown_executions

CASE = {
    "id": "shell-policy.synthetic", "category": "bug_fix", "difficulty": "easy",
    "title": "Docker syntax then repair a synthetic arithmetic function", "timeout_seconds": 180,
    "prompt": (
        "This is an isolated synthetic acceptance task. First use bash to execute exactly "
        "python -c \"print(1 + 1)\" and confirm output 2. Then read math_utils.py and tests/test_math_utils.py, "
        "fix subtract with the smallest edit, and run exactly "
        "cd /workspace && python -m pytest -q 2>&1. Do not change tests. "
        "Finish with actual results. Use single-agent tools only."
    ),
    "setup_files": {
        "math_utils.py": "def subtract(a, b):\n    return a + b\n",
        "tests/test_math_utils.py": (
            "from math_utils import subtract\ndef test_subtract(): assert subtract(7, 4) == 3\n"
        ),
    },
    "protected_files": ["tests/test_math_utils.py"],
    "assertions": [
        {"type": "file_contains", "path": "math_utils.py", "values": ["return a - b"]},
        {"type": "workspace_changes_exact", "values": ["math_utils.py"]},
        {"type": "protected_files_unchanged"}, {"type": "task_status", "value": "succeeded"},
    ],
}


async def main():
    output = Path("docs/release-evidence/shell-policy-20260923-agent.json")
    manifest = source_manifest(Path.cwd())
    if not settings.get_effective_api_key():
        result = {"status": "not_run", "reason": "No configured model API key"}
    else:
        settings.ENABLE_MULTI_AGENT = False
        settings.ENABLE_LONG_TERM_MEMORY = False
        settings.ENABLE_TOOL_CONFIRMATION = True
        settings.AGENT_EXECUTOR = "docker"
        settings.MAX_AGENT_ROUNDS = 8
        settings.TASK_TOKEN_BUDGET = 40000
        settings.MAX_TOOL_CALLS_PER_TASK = 12
        # Keep full executor envelopes for diagnostics; trace previews are bounded.
        settings.MICROCOMPACT_MIN_CHARS = 1
        settings.SANDBOX_DEPLOYMENT = "shell-policy-demo-" + uuid.uuid4().hex[:8]
        with tempfile.TemporaryDirectory(prefix="shell-policy-agent-") as directory:
            root = Path(directory).resolve()
            os.environ["WORKSPACE_BASE"] = str(root / "workspaces")
            settings.SANDBOX_STAGING_BASE = str(root / "stage")
            settings.SANDBOX_HOST_STAGING_BASE = str(root / "stage")
            try:
                result = await run_agent_case(CASE, 923, "single")
            finally:
                shutdown_executions()
            events = result.get("trace", {}).get("events", [])
            artifacts = ToolArtifactStore(user_id=20923)
            executions = [json.loads(artifacts.read_range(
                e["data"]["artifact_path"], expected_sha256=e["data"]["artifact_sha256"],
            )["content"]) for e in events if e.get("type") == "tool" and e.get("name") == "bash"
                and e["data"].get("artifact_path")]
            result["executions"] = executions
            result["docker_checks"] = {
                "real_containers": len(executions) >= 2 and all(
                    e.get("executor") == "docker" and e.get("container_id") for e in executions),
                "cleaned": bool(executions) and all(e.get("cleanup_confirmed") for e in executions),
                "no_containers_remain": not DockerExecutor().engine.containers(settings.SANDBOX_DEPLOYMENT),
                "approval_resumed": result.get("confirmation_resumes", 0) >= 2,
                "inline_then_tests": len(executions) >= 2 and
                executions[0].get("receipt", {}).get("command") == 'python -c "print(1 + 1)"' and
                executions[0].get("stdout") == "2" and executions[0].get("exit_code") == 0 and
                any(e.get("receipt", {}).get("ok") and
                    e.get("receipt", {}).get("command") ==
                    "cd /workspace && python -m pytest -q 2>&1" for e in executions),
            }
    result.update(source_sha256=manifest["source_sha256"], model=settings.get_effective_model_id(),
                  qualification="synthetic-diagnostic-not-benchmark-score")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: result.get(k) for k in (
        "status", "task_status", "duration_ms", "confirmation_resumes", "docker_checks", "task_budget",
        "infrastructure_error", "system_error", "reason",
    )}, ensure_ascii=False))
    if result.get("status") != "passed" or not all(result.get("docker_checks", {}).values()):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
