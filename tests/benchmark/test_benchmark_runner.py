"""Versioned benchmark schema and deterministic baseline tests."""

import json

from benchmarks.run import _sanitized_endpoint, load_suite, render_markdown, run_suite


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
    metadata = report["run_metadata"]
    assert metadata["code"]["commit"]
    assert metadata["code"]["branch"]
    assert isinstance(metadata["code"]["dirty"], bool)
    assert len(metadata["suite"]["sha256"]) == 64
    assert len(metadata["dependencies"]["uv_lock_sha256"]) == 64
    assert len(metadata["suite"]["selected_case_ids"]) == 10
    assert metadata["model"] is None
    assert "Reproducibility manifest" in render_markdown(report)


async def test_multi_mode_selects_only_delegation_suitable_cases():
    suite = load_suite()
    expected = sum(case.get("delegation_suitable", False) for case in suite["cases"])
    report = await run_suite(backend="platform", mode="multi", write_artifacts=False)
    assert report["summary"]["executed"] == expected
    assert len(report["run_metadata"]["suite"]["selected_case_ids"]) == expected


def test_endpoint_metadata_never_contains_credentials_or_query_values():
    endpoint = _sanitized_endpoint(
        "https://username:password@api.example.test:8443/anthropic/?api_key=secret#fragment"
    )

    assert endpoint == {
        "scheme": "https",
        "host": "api.example.test",
        "port": 8443,
        "path": "/anthropic",
    }
    assert "password" not in json.dumps(endpoint)
    assert "secret" not in json.dumps(endpoint)
