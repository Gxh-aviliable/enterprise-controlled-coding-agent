"""20 versioned synthetic cases: retrieval, policy, real Chroma lifecycle, real Agent behavior."""

import argparse
import asyncio
import json
import os
import tempfile
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone

import chromadb
from langgraph.checkpoint.memory import InMemorySaver

from benchmarks.memory_recall import load_memory_suite, run_embedding_benchmark
from benchmarks.run import RESULTS_DIR, ROOT, _run_agent_graph, extract_response
from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.graph import build_simple_agent_graph
from enterprise_agent.core.agent.nodes import _drain_memory_flush_tasks
from enterprise_agent.db import chroma
from enterprise_agent.memory.long_term import _long_term_memory_cache, get_long_term_memory
from enterprise_agent.memory.policy import MemoryAdmissionPolicy
from enterprise_agent.observability.release import source_manifest
from enterprise_agent.observability.trace_store import get_trace_store


async def run(with_model=False):
    suite_path = ROOT / "benchmarks/memory-v2/cases.json"
    suite = json.loads(suite_path.read_text())
    source = source_manifest(ROOT)
    retrieval, observations = run_embedding_benchmark(load_memory_suite(ROOT / suite["retrieval_suite"]))
    cases = [{"id": c["id"], "layer": "retrieval", "passed": c["passed"], "detail": c} for c in retrieval["cases"]]
    policy = MemoryAdmissionPolicy()
    for c in suite["admission"]:
        decision = asdict(policy.decide(**c["input"]))
        cases.append(
            {"id": c["id"], "layer": "admission", "passed": decision["accepted"] == c["expected"], "decision": decision}
        )
    old_client = chroma._chroma_client
    old_workspace = os.environ.get("WORKSPACE_BASE")
    old_enabled = settings.ENABLE_LONG_TERM_MEMORY
    prior_cache = dict(_long_term_memory_cache)
    try:
        chroma._chroma_client = chromadb.EphemeralClient()
        _long_term_memory_cache.clear()
        settings.ENABLE_LONG_TERM_MEMORY = True
        with tempfile.TemporaryDirectory(prefix="memory-v2-synthetic-") as tmp:
            os.environ["WORKSPACE_BASE"] = tmp
            owner = get_long_term_memory(850001)
            other = get_long_term_memory(850002)
            first = await owner.store_task_summary("synthetic-old", "用户 Python 依赖管理默认使用 uv。")
            second = await owner.store_task_summary("synthetic-new", "用户 Python 依赖管理现改用 pip。")
            own_hits = await owner.search_conversations("Python 依赖管理", n_results=10)
            other_hits = await other.search_conversations("Python 依赖管理", n_results=10)
            cases.append(
                {
                    "id": "cross_user_retrieval",
                    "layer": "lifecycle",
                    "passed": bool(own_hits) and not other_hits,
                    "owner_hits": len(own_hits),
                    "other_hits": len(other_hits),
                }
            )
            denied = await other.delete_conversation(first)
            remaining = owner.conversations.get(ids=[first])["ids"]
            cases.append(
                {"id": "cross_user_delete_denied", "layer": "lifecycle", "passed": not denied and remaining == [first]}
            )
            pattern = await owner.store_pattern("preference", "python_package_manager", {"value": "uv"}, first)
            updated = await owner.store_pattern("preference", "python_package_manager", {"value": "pip"}, second)
            record = owner.patterns.get(ids=[pattern], include=["metadatas"])["metadatas"][0]
            cases.append(
                {
                    "id": "preference_update",
                    "layer": "lifecycle",
                    "passed": pattern == updated and json.loads(record["value_json"])["value"] == "pip",
                    "metadata": record,
                }
            )
            receipt = await owner.delete_conversation_with_dependents(first)
            cases.append(
                {
                    "id": "cascade_delete",
                    "layer": "lifecycle",
                    "passed": receipt is not None
                    and not owner.patterns.get(ids=[pattern])["ids"]
                    and not owner.conversations.get(ids=[first])["ids"],
                    "receipt": receipt,
                }
            )
            for index, c in enumerate(suite["behavior"]):
                if not with_model:
                    cases.append({"id": c["id"], "layer": "behavior", "passed": None, "status": "not_run"})
                    continue
                uid = 850100 + index
                session = "memory-behavior-" + uuid.uuid4().hex
                trace_id = "memory-" + uuid.uuid4().hex
                mem = get_long_term_memory(uid)
                memory_id = await mem.store_task_summary("synthetic-seed", c["memory"])
                store = get_trace_store()
                store.start_trace(user_id=uid, trace_id=trace_id, session_id=session, request_summary=c["query"])
                graph = build_simple_agent_graph(checkpointer=InMemorySaver())
                start = time.perf_counter()
                try:
                    state, _ = await _run_agent_graph(
                        graph=graph,
                        graph_input={
                            "user_id": uid,
                            "session_id": session,
                            "trace_id": trace_id,
                            "permissions": ["tools:basic"],
                            "execution_mode": "single_agent",
                            "task_status": "pending",
                            "messages": [{"role": "user", "content": c["query"]}],
                        },
                        config={"configurable": {"thread_id": session}},
                        case={},
                    )
                    response = extract_response(state.get("messages", [])).strip()
                    trace = store.get_trace(uid, trace_id)
                    injected = [
                        e["data"].get("injected_ids", [])
                        for e in trace["events"]
                        if e.get("type") == "memory" and e.get("name") == "memory_retrieval"
                    ]
                    cases.append(
                        {
                            "id": c["id"],
                            "layer": "behavior",
                            "passed": response == c["expected"]
                            and not any(secret in response for secret in c["forbidden"]),
                            "response": response,
                            "injected": injected,
                            "seed_memory_id": memory_id,
                            "seed_recalled": any(memory_id in row for row in injected),
                            "duration_ms": (time.perf_counter() - start) * 1000,
                            "trace": trace,
                        }
                    )
                except Exception as exc:
                    cases.append({"id": c["id"], "layer": "behavior", "passed": False, "error": str(exc)})
            await _drain_memory_flush_tasks()
    finally:
        chroma._chroma_client = old_client
        _long_term_memory_cache.clear()
        _long_term_memory_cache.update(prior_cache)
        settings.ENABLE_LONG_TERM_MEMORY = old_enabled
        if old_workspace is None:
            os.environ.pop("WORKSPACE_BASE", None)
        else:
            os.environ["WORKSPACE_BASE"] = old_workspace
    result = {
        "suite_id": suite["suite_id"],
        "source_sha256": source["source_sha256"],
        "qualification": "diagnostic-snapshot",
        "source_unchanged": source["source_sha256"] == source_manifest(ROOT)["source_sha256"],
        "case_count": len(cases),
        "cases": cases,
        "retrieval_observations": observations,
        "limitation": (
            "Retrieval hit and correct behavior are separate; "
            "correct response without recall does not prove memory use."
        ),
        "passed": sum(c["passed"] is True for c in cases),
        "not_run": sum(c["passed"] is None for c in cases),
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    RESULTS_DIR.mkdir(exist_ok=True)
    output = RESULTS_DIR / f"{stamp}-memory-governance.json"
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"path": str(output), "passed": result["passed"], "not_run": result["not_run"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-model", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.with_model))
