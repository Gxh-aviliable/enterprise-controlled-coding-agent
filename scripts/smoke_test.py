#!/usr/bin/env python3
"""Dependency-light local smoke test for the coding-agent baseline.

This deliberately avoids MySQL, Redis, Chroma model downloads and a paid LLM.
It proves that a new Python environment can import the application, compile the
LangGraph topology, isolate a workspace, execute safe tools, and block a known
dangerous command. Service-backed/model-backed checks are separate integration
tests and must not be inferred from this result.
"""

import json
import os
import sys
import tempfile
from pathlib import Path


def run() -> dict:
    if sys.version_info < (3, 11):
        raise RuntimeError(f"Python 3.11+ required; found {sys.version.split()[0]}")

    # Settings are constructed at import time. Supply a safe development value
    # when the caller has not created .env yet.
    os.environ.setdefault("JWT_SECRET_KEY", "smoke-test-only-secret")

    with tempfile.TemporaryDirectory(prefix="mini-claude-smoke-") as tmpdir:
        os.environ["WORKSPACE_BASE"] = tmpdir

        from enterprise_agent.api.main import app
        from enterprise_agent.core.agent.graph import build_agent_graph
        from enterprise_agent.core.agent.tools.file_ops import read_file, write_file
        from enterprise_agent.core.agent.tools.shell import bash
        from enterprise_agent.core.agent.tools.workspace import set_current_user_id

        set_current_user_id(4242)
        write_result = write_file.invoke({"path": "smoke/hello.txt", "content": "smoke-ok\n"})
        read_result = read_file.invoke({"path": "smoke/hello.txt"})
        shell_result = json.loads(bash.invoke({"command": "echo shell-ok"}))
        blocked_result = json.loads(bash.invoke({"command": "rm -rf /"}))

        graph = build_agent_graph()
        graph_nodes = set(graph.get_graph().nodes)
        routes = {route.path for route in app.routes}
        workspace_file = Path(tmpdir) / "user_4242" / "smoke" / "hello.txt"

        checks = {
            "workspace_file_created": workspace_file.exists(),
            "file_write_verified": write_result.startswith("Wrote"),
            "file_read_verified": read_result == "smoke-ok",
            "safe_shell_verified": shell_result["exit_code"] == 0 and "shell-ok" in shell_result["stdout"],
            "dangerous_shell_blocked": blocked_result["exit_code"] != 0,
            "graph_compiled": {"init_context", "llm_call", "tool_executor"}.issubset(graph_nodes),
            "api_routes_loaded": {"/health", "/chat/stream", "/workspace/tree"}.issubset(routes),
        }

        if not all(checks.values()):
            failed = [name for name, passed in checks.items() if not passed]
            raise RuntimeError(f"Smoke checks failed: {failed}")

        return {
            "status": "ok",
            "python": sys.version.split()[0],
            "checks": checks,
            "external_services_tested": False,
            "model_call_tested": False,
        }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
