"""Versioned benchmark schema and deterministic baseline tests."""

from benchmarks.run import load_suite, run_suite


def test_v1_suite_has_required_categories_and_ten_cases():
    suite = load_suite()
    categories = {case["category"] for case in suite["cases"]}
    assert len(suite["cases"]) >= 10
    assert {
        "code_understanding",
        "bug_fix",
        "file_read_write",
        "shell_validation",
        "failure_recovery",
        "safety_refusal",
        "interruption_recovery",
    }.issubset(categories)
    assert sum(case.get("delegation_suitable", False) for case in suite["cases"]) >= 2


async def test_platform_baseline_is_reproducible_and_fully_passing():
    report = await run_suite(backend="platform", mode="single", write_artifacts=False)
    assert report["summary"]["executed"] == 10
    assert report["summary"]["passed"] == 10
    assert report["summary"]["task_success_rate"] == 1.0
    assert report["summary"]["safety_interceptions"] >= 1
    assert report["summary"]["human_intervention_rate"] > 0


async def test_multi_mode_selects_only_delegation_suitable_cases():
    suite = load_suite()
    expected = sum(case.get("delegation_suitable", False) for case in suite["cases"])
    report = await run_suite(backend="platform", mode="multi", write_artifacts=False)
    assert report["summary"]["executed"] == expected
