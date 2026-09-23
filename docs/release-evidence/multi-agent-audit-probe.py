"""Offline diagnostic of the 2026-09-11 multi-agent implementation.

Run from repository root with:
    PYTHONPATH=. .venv/bin/python docs/release-evidence/multi-agent-audit-probe.py

Assertions deliberately identify existing defects, not desired acceptance behavior.
All model calls are stubbed; writes and teammate tasks use a temporary workspace.
The parent write tool is a spy, not an actual file mutation. All spawned tasks
are awaited during cleanup. No Docker, Redis, HTTP, or server access is needed.
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch


async def main():
    with tempfile.TemporaryDirectory(prefix="multi-agent-audit-") as temp:
        os.environ["WORKSPACE_BASE"] = temp
        from langchain_core.messages import AIMessage

        from enterprise_agent.config.settings import settings
        from enterprise_agent.core.agent import nodes
        from enterprise_agent.core.agent.tools import subagent, team
        from enterprise_agent.core.agent.tools.contracts import normalize_tool_result, resolve_tool_risk
        from enterprise_agent.core.agent.tools.task import TaskManager
        from enterprise_agent.core.agent.tools.workspace import set_current_session_id, set_current_user_id
        from enterprise_agent.observability.trace_store import get_trace_store

        async def no_cancel():
            return False

        async def no_parent_cancel(_state):
            return None

        class FailingModel:
            async def ainvoke(self, _messages):
                raise RuntimeError("controlled audit failure")

        class EmptyModel:
            async def ainvoke(self, _messages):
                return AIMessage(content="")

        results = {}
        with patch("enterprise_agent.core.execution.interrupt_control.is_current_task_cancel_requested", no_cancel):
            with patch.object(subagent, "get_llm", lambda: FailingModel()):
                failed = await subagent.delegate_task.ainvoke({"role": "reviewer", "prompt": "audit"})
            with patch.object(subagent, "get_llm", lambda: EmptyModel()):
                empty = await subagent.delegate_task.ainvoke({"role": "reviewer", "prompt": "audit"})
            results["normalization"] = []
            for raw in [
                failed,
                empty,
                "Error initializing LLM: controlled",
                "Subagent cancelled by the parent task Stop request.",
            ]:
                record = normalize_tool_result(
                    tool_name="delegate_task", tool_call_id="audit", raw_result=raw, duration_ms=0, attempt_count=1
                )
                assert record.ok, "Current behavior changed; review audit finding"
                results["normalization"].append({"raw": raw, "ok": record.ok, "status": record.status.value})

            write_calls = []

            class FakeWrite:
                name = "write_file"

                async def ainvoke(self, args):
                    write_calls.append(args["path"])
                    return "Successfully wrote audit.py"

            executable = [subagent.delegate_task, FakeWrite()]
            state = {
                "session_id": "audit-session",
                "trace_id": "audit-trace",
                "user_id": 901,
                "permissions": ["tools:advanced", "tools:basic"],
                "execution_mode": "multi_agent",
                "task_status": "running",
                "messages": [],
                "pending_tool_calls": [
                    {"id": "delegate", "name": "delegate_task", "args": {"role": "reviewer", "prompt": "audit"}},
                    {"id": "write", "name": "write_file", "args": {"path": "audit.py", "content": "audit"}},
                ],
            }
            get_trace_store().start_trace(
                trace_id="audit-trace",
                session_id="audit-session",
                user_id=901,
                request_summary="controlled audit",
                mode="multi_agent",
            )
            with (
                patch.object(settings, "ENABLE_MULTI_AGENT", True),
                patch.object(nodes, "ALL_TOOLS", executable),
                patch.object(nodes, "get_tools_for_permissions", lambda *args, **kwargs: executable),
                patch.object(nodes, "_tool_cancel_request", no_parent_cancel),
                patch.object(subagent, "get_llm", lambda: FailingModel()),
            ):
                executed = await nodes.tool_executor_node(state)
            assert write_calls == ["audit.py"]
            results["failed_delegate_opens_write_gate"] = {
                "fake_write_invoked": True,
                "records": [
                    {"tool": r["tool_name"], "ok": r["ok"], "error_code": r["error_code"]}
                    for r in executed["tool_execution_records"]
                ],
            }

        manager = team.TeammateManager(Path(temp) / "duplicate-runner")

        async def dormant_loop(_self):
            await asyncio.Event().wait()

        with patch.object(team.TeammateRunner, "_run_loop", dormant_loop):
            await manager.spawn("reviewer", "review", "audit")
            original = manager.runners["reviewer"]
            try:
                duplicate_result = await manager.spawn("reviewer", "review", "duplicate")
                replacement = manager.runners["reviewer"]
                await replacement.stop()
                assert original is not replacement and not original.task.done()
                results["duplicate_spawn"] = {
                    "response": duplicate_result,
                    "handle_replaced": True,
                    "original_still_running_after_replacement_stop": True,
                }
            finally:
                await original.stop()
                assert original.task.done()

        set_current_user_id(902)
        set_current_session_id("conversation-A")
        manager_a, bus_a = team.get_teammate_manager(), team.get_message_bus()
        await bus_a.send("reviewer", "lead", "A conversation evidence")
        set_current_session_id("conversation-B")
        manager_b, bus_b = team.get_teammate_manager(), team.get_message_bus()
        drained = await bus_b.read_inbox("lead")
        assert manager_a is manager_b and bus_a is bus_b and len(drained) == 1
        results["cross_session_namespace"] = {"same_manager": True, "same_bus": True, "B_drained_A_message": True}

        board = TaskManager(Path(temp) / "board")
        tid = json.loads(board.create("audit task"))["id"]
        board.claim(tid, "agent-A")
        board.claim(tid, "agent-B")
        assert json.loads(board.get(tid))["owner"] == "agent-B"
        results["claim_overwrites_owner"] = True
        results["read_only_risk_labels"] = {
            command: resolve_tool_risk("bash", {"command": command}).value
            for command in ["pytest -q", "python3 -m py_compile sample.py", "npm test"]
        }
        print(json.dumps(results, ensure_ascii=False, indent=2))


asyncio.run(main())
