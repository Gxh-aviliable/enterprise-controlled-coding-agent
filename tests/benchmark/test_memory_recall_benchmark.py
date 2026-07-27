"""Deterministic tests for the memory-recall benchmark schema and evaluator."""

from benchmarks.memory_recall import (
    evaluate_memory_observations,
    load_memory_suite,
)


def test_memory_recall_suite_covers_positive_negative_filter_and_conflict_cases():
    suite = load_memory_suite()
    categories = {case["category"] for case in suite["cases"]}
    assert {
        "direct_recall",
        "paraphrase_recall",
        "task_outcome_recall",
        "negative_recall",
        "conflict_recall",
    }.issubset(categories)
    assert any(record["quality_status"] == "legacy" for record in suite["records"])
    assert any(record["retrieval_enabled"] is False for record in suite["records"])


def test_memory_recall_evaluator_reports_perfect_observations_without_claiming_behavior():
    suite = load_memory_suite()
    observations = [{
        "case_id": case["id"],
        "candidate_ids": case["relevant_ids"],
        "injected_ids": case["relevant_ids"],
        "injected_tokens": 50 if case["relevant_ids"] else 0,
    } for case in suite["cases"]]

    report = evaluate_memory_observations(suite, observations)

    assert report["passed"] == report["case_count"]
    assert report["metrics"]["recall_at_3"] == 1.0
    assert report["metrics"]["precision_at_3"] == 1.0
    assert report["metrics"]["negative_false_injection_rate"] == 0.0
    conflict = next(case for case in report["cases"] if case["category"] == "conflict_recall")
    assert conflict["behavior_status"] == "not_measured"


def test_memory_recall_evaluator_exposes_false_injection_and_budget_failure():
    suite = load_memory_suite()
    observations = [{
        "case_id": case["id"],
        "candidate_ids": ["legacy_fiction"],
        "injected_ids": ["legacy_fiction"],
        "injected_tokens": suite["max_injected_tokens"] + 1,
    } for case in suite["cases"]]

    report = evaluate_memory_observations(suite, observations)

    assert report["passed"] == 0
    assert report["metrics"]["forbidden_injections"] > 0
    assert report["metrics"]["negative_false_injection_rate"] == 1.0
    assert report["metrics"]["token_budget_compliance"] == 0.0


def test_memory_recall_evaluator_fails_positive_case_with_irrelevant_extra_injection():
    suite = load_memory_suite()
    observations = [{
        "case_id": case["id"],
        "candidate_ids": case["relevant_ids"],
        "injected_ids": case["relevant_ids"],
        "injected_tokens": 10,
    } for case in suite["cases"]]
    target = next(
        observation
        for observation in observations
        if observation["case_id"] == "direct_python_preference"
    )
    target["injected_ids"] = ["python_workflow", "frontend_stack"]

    report = evaluate_memory_observations(suite, observations)

    failed = next(
        case for case in report["cases"]
        if case["id"] == "direct_python_preference"
    )
    assert failed["passed"] is False
    assert failed["unexpected"] == ["frontend_stack"]
