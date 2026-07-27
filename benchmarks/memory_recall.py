"""Run the versioned, offline long-term-memory retrieval benchmark.

This runner measures the configured local embedding and the same eligibility
rules used by the Agent. It does not call an LLM and therefore cannot claim
that an injected memory changed the final model response.
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import chromadb

from enterprise_agent.db.chroma import get_embedding_function
from enterprise_agent.memory.long_term import rank_memory_candidates

ROOT = Path(__file__).resolve().parents[1]
SUITE_PATH = ROOT / "benchmarks" / "v1" / "memory_recall_cases.json"
RESULTS_DIR = ROOT / "benchmarks" / "results"


def load_memory_suite(path: Path = SUITE_PATH) -> dict[str, Any]:
    suite = json.loads(path.read_text(encoding="utf-8"))
    if suite.get("schema_version") != "1.0":
        raise ValueError("Unsupported memory benchmark schema version")
    record_ids = [record["id"] for record in suite.get("records", [])]
    case_ids = [case["id"] for case in suite.get("cases", [])]
    if len(record_ids) != len(set(record_ids)):
        raise ValueError("Memory benchmark record IDs must be unique")
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Memory benchmark case IDs must be unique")
    known = set(record_ids)
    for case in suite.get("cases", []):
        referenced = set(case.get("relevant_ids", [])) | set(case.get("forbidden_ids", []))
        if not referenced.issubset(known):
            raise ValueError(f"Case {case['id']} references an unknown memory record")
    return suite


def _estimate_tokens(records: list[dict[str, Any]]) -> int:
    return sum(max(1, len(record["content"]) // 4) for record in records)


def evaluate_memory_observations(
    suite: dict[str, Any],
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute retrieval metrics from case observations.

    The evaluator is separated from embedding execution so metric behavior is
    deterministic and unit-testable.
    """
    by_case = {item["case_id"]: item for item in observations}
    case_results = []
    relevant_total = 0
    relevant_retrieved = 0
    injected_total = 0
    relevant_injected = 0
    reciprocal_ranks = []
    negative_cases = 0
    negative_false_injections = 0
    forbidden_injections = 0
    trace_complete = 0
    token_budget_passes = 0

    for case in suite["cases"]:
        observation = by_case.get(case["id"], {})
        injected_ids = observation.get("injected_ids", [])
        candidate_ids = observation.get("candidate_ids", [])
        relevant = set(case.get("relevant_ids", []))
        allowed = set(case.get("allowed_ids", []))
        forbidden = set(case.get("forbidden_ids", []))
        injected_set = set(injected_ids)
        hits = relevant & injected_set
        forbidden_hits = forbidden & injected_set
        unexpected_hits = injected_set - relevant - allowed
        relevant_total += len(relevant)
        relevant_retrieved += len(hits)
        injected_total += len(injected_ids)
        relevant_injected += len(hits)
        forbidden_injections += len(forbidden_hits)

        if relevant:
            ranks = [
                candidate_ids.index(memory_id) + 1
                for memory_id in relevant
                if memory_id in candidate_ids
            ]
            reciprocal_ranks.append(1 / min(ranks) if ranks else 0.0)
        else:
            negative_cases += 1
            negative_false_injections += int(bool(injected_ids))

        trace_ok = all(
            key in observation
            for key in ("candidate_ids", "injected_ids", "injected_tokens")
        )
        trace_complete += int(trace_ok)
        within_budget = (
            int(observation.get("injected_tokens", 0))
            <= int(suite["max_injected_tokens"])
        )
        token_budget_passes += int(within_budget)
        case_results.append({
            "id": case["id"],
            "category": case["category"],
            "passed": (
                hits == relevant
                and not forbidden_hits
                and not unexpected_hits
                and (bool(relevant) or not injected_ids)
                and within_budget
                and trace_ok
            ),
            "expected": sorted(relevant),
            "injected": injected_ids,
            "missing": sorted(relevant - injected_set),
            "unexpected": sorted(unexpected_hits),
            "forbidden_injected": sorted(forbidden_hits),
            "injected_tokens": int(observation.get("injected_tokens", 0)),
            "behavior_status": (
                "not_measured"
                if case.get("behavior_assertion")
                else "not_applicable"
            ),
        })

    case_count = len(suite["cases"])
    return {
        "suite_id": suite["suite_id"],
        "case_count": case_count,
        "passed": sum(result["passed"] for result in case_results),
        "metrics": {
            "recall_at_3": round(relevant_retrieved / relevant_total, 4) if relevant_total else 1.0,
            "precision_at_3": round(relevant_injected / injected_total, 4) if injected_total else 1.0,
            "mean_reciprocal_rank": round(statistics.mean(reciprocal_ranks), 4) if reciprocal_ranks else 1.0,
            "negative_false_injection_rate": (
                round(negative_false_injections / negative_cases, 4)
                if negative_cases
                else 0.0
            ),
            "forbidden_injections": forbidden_injections,
            "trace_coverage": round(trace_complete / case_count, 4) if case_count else 1.0,
            "token_budget_compliance": (
                round(token_budget_passes / case_count, 4) if case_count else 1.0
            ),
        },
        "limitations": [
            "Measures local semantic retrieval and eligibility filtering only.",
            "Injected memory is not proof that the model applied it.",
            "Current-instruction override behavior requires a real Agent run.",
        ],
        "cases": case_results,
    }


def run_embedding_benchmark(suite: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run cases against an ephemeral Chroma collection and local embeddings."""
    client = chromadb.EphemeralClient()
    collection = client.create_collection(
        name="memory_recall_benchmark",
        embedding_function=get_embedding_function(),
    )
    records = suite["records"]
    collection.add(
        ids=[record["id"] for record in records],
        documents=[record["content"] for record in records],
        metadatas=[{
            "quality_status": record["quality_status"],
            "retrieval_enabled": record["retrieval_enabled"],
            "memory_type": record["memory_type"],
        } for record in records],
    )
    records_by_id = {record["id"]: record for record in records}
    observations = []

    for case in suite["cases"]:
        result = collection.query(
            query_texts=[case["query"]],
            n_results=len(records),
            include=["distances", "metadatas"],
        )
        ordered = []
        for index, memory_id in enumerate(result["ids"][0]):
            metadata = result["metadatas"][0][index]
            distance = float(result["distances"][0][index])
            ordered.append({
                "memory_id": memory_id,
                "rank": index + 1,
                "distance": round(distance, 6),
                "_search_text": records_by_id[memory_id]["content"],
                "_rejection_reasons": [
                    *(
                        ["quality_not_active"]
                        if metadata.get("quality_status") != "active"
                        else []
                    ),
                    *(
                        ["retrieval_disabled"]
                        if metadata.get("retrieval_enabled", True) is not True
                        else []
                    ),
                ],
            })
        ordered = rank_memory_candidates(
            ordered,
            query=case["query"],
            max_distance=float(suite["threshold"]),
            include_rejected=True,
            n_results=len(records),
        )
        injected = [item["memory_id"] for item in ordered if item["eligible"]][: int(suite["top_k"])]
        observations.append({
            "case_id": case["id"],
            "candidate_ids": [item["memory_id"] for item in ordered],
            "candidates": ordered,
            "injected_ids": injected,
            "injected_tokens": _estimate_tokens([records_by_id[item] for item in injected]),
        })

    return evaluate_memory_observations(suite, observations), observations


def write_report(report: dict[str, Any], observations: list[dict[str, Any]]) -> tuple[Path, Path]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = RESULTS_DIR / f"{stamp}-memory-recall.json"
    markdown_path = RESULTS_DIR / f"{stamp}-memory-recall.md"
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "report": report,
        "observations": observations,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    metrics = report["metrics"]
    lines = [
        "# Memory Recall Benchmark",
        "",
        f"- Suite: `{report['suite_id']}`",
        f"- Cases: {report['passed']}/{report['case_count']} passed",
        f"- Recall@3: {metrics['recall_at_3']:.1%}",
        f"- Precision@3: {metrics['precision_at_3']:.1%}",
        f"- MRR: {metrics['mean_reciprocal_rank']:.3f}",
        f"- Negative false-injection rate: {metrics['negative_false_injection_rate']:.1%}",
        f"- Forbidden injections: {metrics['forbidden_injections']}",
        f"- Trace coverage: {metrics['trace_coverage']:.1%}",
        f"- Token-budget compliance: {metrics['token_budget_compliance']:.1%}",
        "",
        "## Cases",
        "",
        "| Case | Category | Result | Injected | Missing | Unexpected |",
        "|---|---|---:|---|---|---|",
    ]
    for case in report["cases"]:
        lines.append(
            f"| {case['id']} | {case['category']} | "
            f"{'PASS' if case['passed'] else 'FAIL'} | "
            f"{', '.join(case['injected']) or 'none'} | "
            f"{', '.join(case['missing']) or 'none'} | "
            f"{', '.join(case['unexpected']) or 'none'} |"
        )
    lines.extend([
        "",
        "## Interpretation boundary",
        "",
        "This report measures local embedding retrieval and policy filtering. "
        "It does not prove that the chat model used an injected record, and it "
        "does not score current-instruction override behavior.",
    ])
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=SUITE_PATH)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    suite = load_memory_suite(args.suite)
    report, observations = run_embedding_benchmark(suite)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not args.no_write:
        json_path, markdown_path = write_report(report, observations)
        print(f"Wrote {json_path}")
        print(f"Wrote {markdown_path}")


if __name__ == "__main__":
    main()
