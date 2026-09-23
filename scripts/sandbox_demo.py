"""Deterministic real-container demo; no LLM, production data or API secrets needed.

Usage: SANDBOX_DOCKER_SOCKET=... .venv/bin/python scripts/sandbox_demo.py --output evidence.json
"""

import argparse
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="sandbox-demo-") as temporary:
        root = Path(temporary).resolve()
        os.environ["WORKSPACE_BASE"] = str(root / "workspaces")
        from enterprise_agent.config.settings import settings
        from enterprise_agent.core.agent.tools.file_ops import read_file, write_file
        from enterprise_agent.core.agent.tools.shell import validate_command
        from enterprise_agent.core.agent.tools.workspace import get_user_workspace, set_current_user_id
        from enterprise_agent.observability.trace_store import get_trace_store
        from enterprise_agent.sandbox.executor import ExecutionRequest, execute

        settings.AGENT_EXECUTOR = "docker"
        settings.SANDBOX_STAGING_BASE = str(root / "staging")
        settings.SANDBOX_HOST_STAGING_BASE = str(root / "staging")
        settings.SANDBOX_DEPLOYMENT = "demo-" + uuid.uuid4().hex[:12]
        set_current_user_id(9001)
        workspace = get_user_workspace()
        trace_id = "sandbox-demo-" + uuid.uuid4().hex[:12]
        store = get_trace_store()
        store.start_trace(
            user_id=9001,
            session_id="sandbox-demo",
            trace_id=trace_id,
            request_summary="Read project, fix add(), run tests, return evidence",
            mode="single",
        )
        evidence = {
            "mode": "real Docker + real file tools; deterministic operator-driven demo",
            "trace_id": trace_id,
            "image": settings.SANDBOX_IMAGE,
            "steps": [],
        }
        write_file.invoke({"path": "app.py", "content": "def add(a, b):\n    return a - b\n"})
        write_file.invoke(
            {"path": "test_app.py", "content": "from app import add\ndef test_add():\n    assert add(2, 3) == 5\n"}
        )
        evidence["read_before"] = read_file.invoke({"path": "app.py"})

        def command(text):
            assert validate_command(text) is None
            result = execute(ExecutionRequest(text, workspace, 9001, trace_id, 20), lambda: False)
            evidence["steps"].append({"command": text, "result": result})
            return result

        failed = command("python -m pytest -q")
        assert failed["exit_code"] == 1 and not failed.get("error_code"), failed
        write_file.invoke(
            {
                "path": "repair.py",
                "content": "from pathlib import Path\np=Path('app.py')\n"
                "p.write_text(p.read_text().replace('a - b', 'a + b'))\n",
            }
        )
        repaired = command("python repair.py")
        assert repaired["exit_code"] == 0 and not repaired.get("error_code"), repaired
        passed = command("python -m pytest -q")
        assert passed["exit_code"] == 0 and "1 passed" in passed["stdout"], passed
        assert all(s["result"]["cleanup_confirmed"] for s in evidence["steps"])
        evidence["read_after"] = read_file.invoke({"path": "app.py"})
        store.finish_trace(
            user_id=9001,
            trace_id=trace_id,
            status="succeeded",
            result_summary="Code repaired in isolated container; pytest passed; containers removed",
        )
        evidence["trace"] = json.loads((workspace / ".agent" / "traces" / f"{trace_id}.json").read_text())
        evidence["success"] = True
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
        print(f"PASS: real Docker read → fix → pytest. Evidence: {args.output}")


if __name__ == "__main__":
    main()
