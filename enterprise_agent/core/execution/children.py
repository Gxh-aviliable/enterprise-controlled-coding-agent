"""Bounded, trace-owned child execution for Plan A. The lead alone mutates files."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.llm_factory import get_llm
from enterprise_agent.core.agent.message_content import extract_visible_text, normalize_signature_only_thinking_blocks
from enterprise_agent.core.execution.child_store import ChildStore
from enterprise_agent.core.execution.child_workspace import read_snapshot, snapshot_tools
from enterprise_agent.core.execution.interrupt_control import get_current_task_runner_identity, owns_current_task_runner

_ACTIVE: set[asyncio.Task] = set()
_OFFLINE_OWNER = uuid.uuid4().hex


@dataclass(frozen=True)
class ChildTaskRequest:
    user_id: int
    session_id: str
    trace_id: str
    tool_call_id: str
    role: str
    prompt: str
    profile: str = "analysis"


@dataclass
class ChildTaskResult:
    child_id: str
    parent_tool_call_id: str
    trace_id: str
    status: str = "failed"
    kind: str = "child_task_result"
    summary: str = ""
    error_code: str | None = None
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0
    duration_ms: int = 0
    snapshot_id: str | None = None
    evidence: list[dict] = field(default_factory=list)
    usage_estimated: bool = False


def succeeded(result):
    return (
        isinstance(result, dict)
        and result.get("kind") == "child_task_result"
        and result.get("status") == "succeeded"
        and bool(result.get("summary", "").strip())
        and bool(result.get("child_id"))
        and int(result.get("model_calls", 0)) > 0
    )


def has_successful_child(state):
    return any(
        succeeded(r) and r.get("trace_id") == state.get("trace_id", "") for r in state.get("child_tasks", {}).values()
    )


def _trace(state, event_type, name, status, data, duration=0):
    if state.get("trace_id") and state.get("user_id") is not None:
        from enterprise_agent.observability.trace_store import get_trace_store

        get_trace_store().record_event(
            user_id=state["user_id"],
            trace_id=state["trace_id"],
            event_type=event_type,
            name=name,
            status=status,
            data=data,
            duration_ms=duration,
        )


def _emit(state, result, role, status, *, record_trace=True):
    payload = {
        "child_id": result.child_id,
        "parent_tool_call_id": result.parent_tool_call_id,
        "profile_role": role,
        "status": status,
        "total_tokens": result.total_tokens,
        "snapshot_id": result.snapshot_id,
    }
    if record_trace:
        _trace(state, "child_task", role, status, payload, result.duration_ms)
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
    except RuntimeError:
        return
    metadata = {
        "id": result.child_id,
        "name": f"Agent · {role}",
        "parent_id": result.parent_tool_call_id,
        "child_id": result.child_id,
        "status": status if status in {"queued", "running"} else "success" if status == "succeeded" else "error",
        "child_status": status,
        "event_seq": 1 if status == "queued" else 2 if status == "running" else 3,
        "ok": status == "succeeded",
        "duration_ms": result.duration_ms,
        "error_code": result.error_code,
        "total_tokens": result.total_tokens,
    }
    if status == "running":
        writer({"event": "tool_start", "id": result.parent_tool_call_id, "name": "delegate_task", "status": "running"})
    if status in {"queued", "running"}:
        writer({"event": "tool_start", **metadata})
    else:
        writer({"event": "tool_result", "result": result.summary or result.error_code, **metadata})
        writer({"event": "tool_end", **metadata, "event_seq": 4})


async def shutdown_children():
    tasks = list(_ACTIVE)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def run_batch(state: dict, calls: list[dict]) -> dict[str, dict]:
    """Run only an approved contiguous delegation group; never write parent state."""
    runner = get_current_task_runner_identity()
    if runner and tuple(runner[:3]) != (state.get("user_id"), state.get("session_id"), state.get("trace_id")):
        raise RuntimeError("Child scope does not match parent runner")
    capacity = max(1, settings.CHILD_MAX_CONCURRENCY)
    semaphore = asyncio.Semaphore(capacity)
    # Reserve disjoint shares of the remaining cumulative task/session budget.
    limits = [settings.CHILD_TOKEN_BUDGET * len(calls)]
    for limit, used in [
        (settings.TASK_TOKEN_BUDGET, state.get("task_token_count", 0)),
        (settings.SESSION_TOKEN_BUDGET, state.get("session_token_count", 0)),
    ]:
        if limit:
            limits.append(max(0, limit - used))
    budget = min(limits) // max(1, len(calls))
    # Reserve at least half of remaining tool calls for lead verification/writes.
    tool_budget = min(
        8,
        max(0, settings.MAX_TOOL_CALLS_PER_TASK - state.get("tool_call_count", 0) - len(calls))
        // max(1, 2 * len(calls)),
    )
    snapshot = None
    snapshot_lock = asyncio.Lock()

    async def get_snapshot():
        nonlocal snapshot
        async with snapshot_lock:
            if snapshot is None:
                task = asyncio.create_task(asyncio.to_thread(read_snapshot, state.get("user_id")))
                try:
                    snapshot = await asyncio.shield(task)
                except asyncio.CancelledError:
                    # Snapshot is bounded I/O, not a process; join before releasing ownership.
                    await task
                    raise
            return snapshot

    async def cancelled():
        from enterprise_agent.core.agent.nodes import _tool_cancel_request

        return bool(await _tool_cancel_request(state)) or (runner is not None and not await owns_current_task_runner())

    async def execute(call):
        from enterprise_agent.core.agent.tools.subagent import SUBAGENT_SYSTEM_PROMPTS

        scope = [state.get("user_id"), state.get("session_id"), state.get("trace_id"), call["id"]]
        child_id = "child-" + hashlib.sha256(json.dumps(scope).encode()).hexdigest()[:32]
        result = ChildTaskResult(child_id, call["id"], state.get("trace_id", ""))
        args = call.get("args", {})
        args = args if isinstance(args, dict) else {}
        role = str(args.get("role") or "specialist")[:100]
        profile = args.get("profile", "analysis")
        start = time.monotonic()
        store = None
        payload = None
        owned = False
        try:
            if profile not in {"analysis", "explore"}:
                raise ValueError("invalid_child_profile")
            if not set(state.get("permissions", [])).intersection({"tools:all", "tools:advanced", "tools:subagent"}):
                raise ValueError("child_permission_denied")
            if profile == "explore" and not set(state.get("permissions", [])).intersection(
                {"tools:all", "tools:basic", "tools:file"}
            ):
                raise ValueError("child_file_permission_denied")
            request = ChildTaskRequest(
                state.get("user_id"),
                state.get("session_id"),
                state.get("trace_id"),
                call["id"],
                role,
                str(args.get("prompt", "")),
                profile,
            )
            if request.user_id is None or not request.session_id or not request.trace_id:
                raise ValueError("child_scope_required")
            prompt = request.prompt
            if not prompt.strip() or len(prompt) > 64000:
                raise ValueError("invalid_child_prompt")
            store = ChildStore(child_id, runner)
            payload = {
                "runner": runner,
                "owner": runner[-1] if runner else _OFFLINE_OWNER,
                "deadline": time.time() + settings.CHILD_TIMEOUT_SECONDS * (len(calls) + 1),
                "request_hash": hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest(),
                "initial_result": asdict(result),
            }
            old = await store.reserve(payload)
            if old:
                if old["request_hash"] != payload["request_hash"]:
                    raise ValueError("child_request_conflict")
                if old.get("result"):
                    _emit(state, ChildTaskResult(**old["result"]), role, old["result"]["status"], record_trace=False)
                    return old["result"]
                result.status, result.error_code = "interrupted", "child_interrupted"
                if old.get("owner") == payload["owner"] and old["deadline"] > time.time():
                    result.error_code = "child_already_running"
                else:
                    owned = True
                return asdict(result)
            owned = True
            _emit(state, result, role, "queued")
            async with semaphore:
                if await cancelled():
                    raise asyncio.CancelledError
                async with asyncio.timeout(settings.CHILD_TIMEOUT_SECONDS):
                    _emit(state, result, role, "running")
                    tools = []
                    if profile == "explore":
                        files, result.snapshot_id = await get_snapshot()
                        tools = snapshot_tools(files)
                    model = get_llm()
                    model = model.bind_tools(tools) if tools else model
                    messages = [
                        SystemMessage(content=SUBAGENT_SYSTEM_PROMPTS["Explore" if tools else "specialist"]),
                        HumanMessage(content=json.dumps({"role": role, "assignment": prompt}, ensure_ascii=False)),
                    ]
                    tool_map = {t.name: t for t in tools}
                    for round_index in range(settings.SUBAGENT_MAX_ROUNDS):
                        if await cancelled():
                            raise asyncio.CancelledError
                        estimated_input = sum(len(str(m.content)) for m in messages) // 3 + 256
                        remaining = budget - result.total_tokens - estimated_input
                        if remaining < 1:
                            result.status, result.error_code = "budget_exhausted", "child_token_budget"
                            break
                        bounded = model.bind(max_tokens=min(4096, remaining))
                        before = time.monotonic()
                        result.model_calls += 1
                        try:
                            response = await bounded.ainvoke(
                                messages,
                                config={"callbacks": [], "tags": ["child_model"], "metadata": {"child_id": child_id}},
                            )
                        except (Exception, asyncio.CancelledError) as exc:
                            _trace(
                                state,
                                "model",
                                "child_model",
                                "error",
                                {
                                    "child_id": child_id,
                                    "parent_tool_call_id": call["id"],
                                    "round": round_index + 1,
                                    "error_type": type(exc).__name__,
                                    "usage_unknown": True,
                                },
                                int((time.monotonic() - before) * 1000),
                            )
                            raise
                        usage = getattr(response, "usage_metadata", None) or {}
                        inp = int(usage.get("input_tokens") or estimated_input)
                        out = int(usage.get("output_tokens") or max(1, len(str(response.content)) // 3))
                        total = int(usage.get("total_tokens") or inp + out)
                        result.input_tokens += inp
                        result.output_tokens += out
                        result.total_tokens += total
                        result.usage_estimated |= not bool(usage)
                        _trace(
                            state,
                            "model",
                            "child_model",
                            "success",
                            {
                                "child_id": child_id,
                                "parent_tool_call_id": call["id"],
                                "round": round_index + 1,
                                "input_tokens": inp,
                                "output_tokens": out,
                                "total_tokens": total,
                                "usage_estimated": not bool(usage),
                            },
                            int((time.monotonic() - before) * 1000),
                        )
                        response = response.model_copy(
                            update={"content": normalize_signature_only_thinking_blocks(response.content)}
                        )
                        messages.append(response)
                        if result.total_tokens > budget:
                            result.status, result.error_code = "budget_exhausted", "child_token_budget"
                            break
                        from enterprise_agent.core.agent.nodes import (
                            _is_truncated_response,
                            _normalized_model_stop_reason,
                        )

                        if _is_truncated_response(_normalized_model_stop_reason(response, out)) or out >= min(
                            4096, remaining
                        ):
                            result.status, result.error_code = "failed", "child_incomplete_response"
                            break
                        if len(result.evidence) + len(response.tool_calls) > tool_budget:
                            result.status, result.error_code = "budget_exhausted", "child_tool_budget"
                            break
                        if not response.tool_calls:
                            result.summary = extract_visible_text(response.content).strip()
                            if len(result.summary) > 64000:
                                result.summary = ""
                                result.status, result.error_code = "failed", "child_output_limit"
                                break
                            result.status = "succeeded" if result.summary else "failed"
                            result.error_code = None if result.summary else "child_empty_result"
                            break
                        for invocation in response.tool_calls:
                            if await cancelled():
                                raise asyncio.CancelledError
                            name, inner_id = invocation["name"], invocation["id"]
                            before = time.monotonic()
                            try:
                                if name not in tool_map:
                                    raise ValueError("Tool is outside the child read-only capability")
                                work = asyncio.create_task(tool_map[name].ainvoke(invocation["args"]))
                                try:
                                    output = await asyncio.shield(work)
                                except asyncio.CancelledError:
                                    # No orphan thread work after terminal receipt/cleanup.
                                    await work
                                    raise
                            except Exception as exc:
                                output = {"error": str(exc)[:200]}
                            result.evidence.append(
                                {
                                    "tool": name,
                                    "tool_call_id": inner_id,
                                    "ok": "error" not in output,
                                    "path": invocation.get("args", {}).get("path"),
                                }
                            )
                            _trace(
                                state,
                                "tool",
                                name,
                                "error" if "error" in output else "success",
                                {
                                    "child_id": child_id,
                                    "parent_tool_call_id": call["id"],
                                    "tool_call_id": inner_id,
                                    "attempt_count": 1,
                                    "snapshot_id": result.snapshot_id,
                                    "input_summary": invocation.get("args", {}),
                                    "output_summary": json.dumps(output, ensure_ascii=False)[:1500],
                                },
                                int((time.monotonic() - before) * 1000),
                            )
                            messages.append(
                                ToolMessage(content=json.dumps(output, ensure_ascii=False), tool_call_id=inner_id)
                            )
                    else:
                        result.status, result.error_code = "budget_exhausted", "child_round_budget"
        except asyncio.CancelledError:
            result.status, result.error_code = "cancelled", "task_cancelled"
        except TimeoutError:
            result.status, result.error_code = "timed_out", "tool_timeout"
        except Exception as exc:
            result.status = "failed"
            result.error_code = str(exc) if isinstance(exc, ValueError) else "child_execution_failed"
        finally:
            result.duration_ms = int((time.monotonic() - start) * 1000)
            if owned:
                try:
                    if await store.finish(dict(payload, result=asdict(result))):
                        result.status, result.error_code = "cancelled", "task_cancelled"
                except Exception:
                    result.status, result.error_code = "interrupted", "child_persistence_failed"
                _emit(state, result, role, result.status)
        return asdict(result)

    tasks = [asyncio.create_task(execute(call)) for call in calls]
    _ACTIVE.update(tasks)
    try:
        pending = set(tasks)
        while pending:
            _, pending = await asyncio.wait(pending, timeout=0.1)
            if pending and await cancelled():
                for task in pending:
                    task.cancel()
        results = await asyncio.gather(*tasks)
        return dict(zip([call["id"] for call in calls], results, strict=True))
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        _ACTIVE.difference_update(tasks)
