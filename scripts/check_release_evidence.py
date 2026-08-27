#!/usr/bin/env python3
"""Validate the checked-in Portfolio v1.0 release evidence.

The checker intentionally uses only the Python standard library so it can run
locally and in CI without model credentials, external services, or downloads.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs" / "release-evidence" / "portfolio-v1.0.json"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_LIMITATION_IDS = {
    "agent-host-paths",
    "ephemeral-tool-artifacts",
    "memory-application-not-measured",
    "platform-not-model-quality",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repo_path(raw_path: str, label: str, errors: list[str]) -> Path | None:
    candidate = (ROOT / raw_path).resolve()
    if not candidate.is_relative_to(ROOT):
        errors.append(f"{label}: path escapes the repository: {raw_path!r}")
        return None
    if not candidate.is_file():
        errors.append(f"{label}: file does not exist: {raw_path!r}")
        return None
    return candidate


def _load_json(path: Path, label: str, errors: list[str]) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"{label}: cannot read valid UTF-8 JSON: {exc}")
        return None


def _validate_subset(
    actual: Any,
    expected: Any,
    label: str,
    errors: list[str],
) -> None:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            errors.append(f"{label}: expected an object, got {type(actual).__name__}")
            return
        for key, expected_value in expected.items():
            if key not in actual:
                errors.append(f"{label}.{key}: missing field")
                continue
            _validate_subset(actual[key], expected_value, f"{label}.{key}", errors)
        return

    if type(actual) is not type(expected) or actual != expected:
        errors.append(f"{label}: expected {expected!r}, got {actual!r}")


def _expect(actual: Any, expected: Any, label: str, errors: list[str]) -> None:
    if type(actual) is not type(expected) or actual != expected:
        errors.append(f"{label}: expected {expected!r}, got {actual!r}")


def _verify_artifact(
    entry: dict[str, Any],
    label: str,
    errors: list[str],
    *,
    parse_json: bool = False,
) -> Any | None:
    raw_path = entry.get("path")
    expected_hash = entry.get("sha256")
    if not isinstance(raw_path, str):
        errors.append(f"{label}.path: expected a string")
        return None
    if not isinstance(expected_hash, str) or not SHA256_PATTERN.fullmatch(expected_hash):
        errors.append(f"{label}.sha256: expected a lowercase SHA-256 digest")
        return None

    path = _repo_path(raw_path, label, errors)
    if path is None:
        return None
    actual_hash = _sha256(path)
    if actual_hash != expected_hash:
        errors.append(f"{label}: SHA-256 mismatch; expected {expected_hash}, got {actual_hash}")
    if parse_json:
        return _load_json(path, label, errors)
    return None


def _validate_source_file(
    entry: dict[str, Any],
    label: str,
    errors: list[str],
) -> None:
    _verify_artifact(entry, label, errors)


def _validate_result_rows(
    report: dict[str, Any],
    label: str,
    errors: list[str],
) -> None:
    results = report.get("results")
    summary = report.get("summary")
    if not isinstance(results, list) or not isinstance(summary, dict):
        errors.append(f"{label}: report must contain results and summary")
        return

    ids = [row.get("id") for row in results if isinstance(row, dict)]
    if len(ids) != len(results) or len(ids) != len(set(ids)):
        errors.append(f"{label}.results: case IDs must be present and unique")
    _expect(len(results), summary.get("case_count"), f"{label}.results count", errors)

    statuses = Counter(row.get("status") for row in results if isinstance(row, dict))
    _expect(statuses["passed"], summary.get("passed"), f"{label}.passed rows", errors)
    _expect(statuses["failed"], summary.get("failed"), f"{label}.failed rows", errors)
    _expect(
        statuses["infrastructure_error"],
        summary.get("infrastructure_errors"),
        f"{label}.infrastructure rows",
        errors,
    )
    _expect(
        statuses["system_error"],
        summary.get("system_errors"),
        f"{label}.system-error rows",
        errors,
    )
    _expect(statuses["skipped"], summary.get("skipped"), f"{label}.skipped rows", errors)


def _validate_agent_or_platform(
    report: dict[str, Any],
    evidence: dict[str, Any],
    source: dict[str, Any],
    label: str,
    errors: list[str],
) -> None:
    expected = evidence.get("expected")
    if not isinstance(expected, dict):
        errors.append(f"evidence.{label}.expected: expected an object")
        return
    _validate_subset(report, expected, f"evidence.{label}", errors)
    _validate_result_rows(report, f"evidence.{label}", errors)

    metadata = report.get("run_metadata")
    if not isinstance(metadata, dict):
        errors.append(f"evidence.{label}.run_metadata: expected an object")
        return
    code = metadata.get("code", {})
    suite = metadata.get("suite", {})
    dependencies = metadata.get("dependencies", {})
    _expect(
        code.get("commit"),
        source["evaluated_commit"],
        f"evidence.{label}.run_metadata.code.commit",
        errors,
    )
    _expect(code.get("dirty"), False, f"evidence.{label}.run_metadata.code.dirty", errors)
    _expect(
        suite.get("id"),
        source["agent_suite"]["id"],
        f"evidence.{label}.run_metadata.suite.id",
        errors,
    )
    _expect(
        suite.get("sha256"),
        source["agent_suite"]["sha256"],
        f"evidence.{label}.run_metadata.suite.sha256",
        errors,
    )
    selected_case_ids = suite.get("selected_case_ids")
    if not isinstance(selected_case_ids, list):
        errors.append(f"evidence.{label}.run_metadata.suite.selected_case_ids: expected a list")
    else:
        _expect(
            len(selected_case_ids),
            30,
            f"evidence.{label}.run_metadata.suite.selected case count",
            errors,
        )
        if len(selected_case_ids) != len(set(selected_case_ids)):
            errors.append(f"evidence.{label}.run_metadata.suite.selected_case_ids: IDs must be unique")
    _expect(
        dependencies.get("uv_lock_sha256"),
        source["dependency_lock"]["sha256"],
        f"evidence.{label}.run_metadata.dependencies.uv_lock_sha256",
        errors,
    )

    if label == "agent":
        _expect(code.get("official_run"), True, "evidence.agent official source", errors)
        model = metadata.get("model", {})
        _expect(model.get("provider"), "deepseek", "evidence.agent model provider", errors)
        _expect(
            model.get("model_id"),
            "deepseek-v4-flash",
            "evidence.agent model ID",
            errors,
        )
        _expect(
            report.get("official"),
            {"requested": True, "valid": True},
            "evidence.agent official guard",
            errors,
        )
    else:
        _expect(code.get("official_run"), False, "evidence.platform official source", errors)
        _expect(metadata.get("model"), None, "evidence.platform model", errors)


def _validate_manifest(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    _expect(manifest.get("schema_version"), "1.0", "schema_version", errors)

    release = manifest.get("release")
    source = manifest.get("source")
    evidence = manifest.get("evidence")
    if not isinstance(release, dict):
        errors.append("release: expected an object")
        return errors
    if not isinstance(source, dict):
        errors.append("source: expected an object")
        return errors
    if not isinstance(evidence, dict):
        errors.append("evidence: expected an object")
        return errors

    _expect(release.get("tag"), "portfolio-v1.0", "release.tag", errors)
    _expect(
        release.get("tag_target"),
        "final_master_release_commit",
        "release.tag_target",
        errors,
    )
    _expect(
        release.get("evaluated_commit_is_tag_target"),
        False,
        "release.evaluated_commit_is_tag_target",
        errors,
    )
    _expect(
        release.get("target_branches"),
        ["develop", "master"],
        "release.target_branches",
        errors,
    )

    evaluated_commit = source.get("evaluated_commit")
    if not isinstance(evaluated_commit, str) or not COMMIT_PATTERN.fullmatch(evaluated_commit):
        errors.append("source.evaluated_commit: expected a lowercase 40-character commit ID")

    verification = manifest.get("verification")
    if not isinstance(verification, dict):
        errors.append("verification: expected an object")
    else:
        _validate_subset(
            verification,
            {
                "evaluated_commit": evaluated_commit,
                "checks": {
                    "backend_tests": {"status": "passed", "passed": 731},
                    "ruff": {"status": "passed", "findings": 0},
                    "smoke": {"status": "passed"},
                    "frontend_tests": {"status": "passed", "passed": 97},
                    "frontend_build": {"status": "passed"},
                    "compose_config": {"status": "passed"},
                },
            },
            "verification",
            errors,
        )

    for key in ("agent_suite", "memory_suite", "dependency_lock"):
        entry = source.get(key)
        if not isinstance(entry, dict):
            errors.append(f"source.{key}: expected an object")
            continue
        _validate_source_file(entry, f"source.{key}", errors)

    for label in ("agent", "platform", "memory"):
        entry = evidence.get(label)
        if not isinstance(entry, dict):
            errors.append(f"evidence.{label}: expected an object")
            continue
        for artifact_kind in ("json", "markdown"):
            artifact = entry.get(artifact_kind)
            if not isinstance(artifact, dict):
                errors.append(f"evidence.{label}.{artifact_kind}: expected an object")

    if errors:
        return errors

    reports: dict[str, dict[str, Any]] = {}
    for label in ("agent", "platform", "memory"):
        entry = evidence[label]
        parsed = _verify_artifact(
            entry["json"],
            f"evidence.{label}.json",
            errors,
            parse_json=True,
        )
        _verify_artifact(entry["markdown"], f"evidence.{label}.markdown", errors)
        if isinstance(parsed, dict):
            reports[label] = parsed
        else:
            errors.append(f"evidence.{label}.json: top-level JSON value must be an object")

    if "agent" in reports:
        _validate_agent_or_platform(reports["agent"], evidence["agent"], source, "agent", errors)
    if "platform" in reports:
        _validate_agent_or_platform(reports["platform"], evidence["platform"], source, "platform", errors)
    if "memory" in reports:
        memory_expected = evidence["memory"].get("expected")
        if isinstance(memory_expected, dict):
            _validate_subset(reports["memory"], memory_expected, "evidence.memory", errors)
        memory_report = reports["memory"].get("report", {})
        memory_cases = memory_report.get("cases")
        if not isinstance(memory_cases, list):
            errors.append("evidence.memory.report.cases: expected a list")
        else:
            _expect(len(memory_cases), 6, "evidence.memory case count", errors)
            if not all(case.get("passed") is True for case in memory_cases):
                errors.append("evidence.memory.report.cases: every case must pass")

    diagnostics = evidence.get("diagnostics")
    if not isinstance(diagnostics, list) or len(diagnostics) != 2:
        errors.append("evidence.diagnostics: expected two diagnostic runs")
    else:
        for index, diagnostic in enumerate(diagnostics, start=1):
            label = f"evidence.diagnostics[{index}]"
            if not isinstance(diagnostic, dict):
                errors.append(f"{label}: expected an object")
                continue
            report = _verify_artifact(
                diagnostic.get("json", {}),
                f"{label}.json",
                errors,
                parse_json=True,
            )
            _verify_artifact(diagnostic.get("markdown", {}), f"{label}.markdown", errors)
            if not isinstance(report, dict):
                errors.append(f"{label}.json: top-level JSON value must be an object")
                continue
            _validate_subset(
                report.get("summary"),
                diagnostic.get("expected"),
                f"{label}.summary",
                errors,
            )
            _expect(report.get("official"), {"requested": False, "valid": False}, f"{label}.official", errors)
            code = report.get("run_metadata", {}).get("code", {})
            _expect(code.get("commit"), source["evaluated_commit"], f"{label}.commit", errors)
            _expect(code.get("dirty"), True, f"{label}.dirty", errors)
            selected = report.get("run_metadata", {}).get("suite", {}).get("selected_case_ids")
            if not isinstance(selected, list):
                errors.append(f"{label}.selected_case_ids: expected a list")
            else:
                _expect(len(selected), 5, f"{label}.selected case count", errors)

    limitations = manifest.get("limitations")
    if not isinstance(limitations, list):
        errors.append("limitations: expected a list")
    else:
        limitation_ids = {item.get("id") for item in limitations if isinstance(item, dict)}
        _expect(
            limitation_ids,
            REQUIRED_LIMITATION_IDS,
            "limitations IDs",
            errors,
        )
        for item in limitations:
            if not isinstance(item, dict) or not str(item.get("statement", "")).strip():
                errors.append("limitations: every entry must include a non-empty statement")

    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="Manifest path; relative paths are resolved from the repository root",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = args.manifest
    if not manifest_path.is_absolute():
        manifest_path = ROOT / manifest_path
    manifest_path = manifest_path.resolve()
    if not manifest_path.is_relative_to(ROOT):
        print("Release evidence manifest must be inside the repository.", file=sys.stderr)
        return 2
    if not manifest_path.is_file():
        print(f"Release evidence manifest does not exist: {manifest_path}", file=sys.stderr)
        return 2

    manifest = _load_json(manifest_path, "manifest", [])
    if not isinstance(manifest, dict):
        print("Release evidence manifest must be a valid JSON object.", file=sys.stderr)
        return 2

    errors = _validate_manifest(manifest)
    if errors:
        print("Release evidence validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("Release evidence verified: portfolio-v1.0; Agent 23/30, Platform 30/30, Memory 6/6.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
