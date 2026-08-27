# Portfolio v1.0 Release Evidence

This document is the human-readable companion to
[`portfolio-v1.0.json`](portfolio-v1.0.json). The JSON manifest is the
machine-checked source of truth.

## Release identity

- Evaluated source commit: `1d637c5753e93c72989c3fdae2ab5edf50e078eb`
- Intended release tag: `portfolio-v1.0`
- Tag target: the final `master` release integration commit after the evidence
  commit has been merged through `develop`
- The tag target is deliberately not the evaluated source commit: adding the
  generated evidence necessarily creates a new commit, and branch integration
  may add merge metadata. Runtime claims remain bound to the clean evaluated
  commit above; the release commit contains the resulting reports and docs.
- Agent suite SHA-256:
  `f63a76cd2be94db2439649fdeb8f735c243887cc8218dbf5edff22a6d4e3d64b`
- `uv.lock` SHA-256:
  `63a40b42f43e9a8edb39377417808126b34fa61e2ccc0e092305c67ffb3465bb`

## Verified results

| Layer | Result | Additional evidence | Canonical artifacts |
|---|---:|---|---|
| Real Agent, single | 23/30 | Easy 7/10, Medium 10/10, Hard 6/10; tool success 77.53%; infrastructure/system errors 0; official valid | [JSON](../../benchmarks/results/20260827T181517Z-agent-single.json) / [Markdown](../../benchmarks/results/20260827T181517Z-agent-single.md) |
| Platform, single | 30/30 | Deterministic offline run; tool success 89.39%; infrastructure/system errors 0 | [JSON](../../benchmarks/results/20260827T182126Z-platform-single.json) / [Markdown](../../benchmarks/results/20260827T182126Z-platform-single.md) |
| Memory recall | 6/6 | Recall@3, Precision@3, and MRR 1.0; negative false-injection rate 0 | [JSON](../../benchmarks/results/20260827T182146Z-memory-recall.json) / [Markdown](../../benchmarks/results/20260827T182146Z-memory-recall.md) |

## Deterministic release checks

All checks below passed on the evaluated source commit before the generated
evidence was added:

| Check | Result |
|---|---:|
| `uv run --frozen pytest -q` | 731 passed |
| `uv run --frozen ruff check .` | 0 findings |
| `uv run --frozen python scripts/smoke_test.py` | passed |
| `npm test --prefix frontend` | 97 passed |
| `npm run build --prefix frontend` | passed |
| `docker compose -f docker/docker-compose.yml config --quiet` | passed |

The official Agent run used provider `deepseek`, model
`deepseek-v4-flash`, and the canonical `mini-claude-code-v2` suite. Its
publishability guard confirmed a clean, unchanged source commit, all 30 cases,
no skipped cases, and no provider-infrastructure or runner-system errors.
Task failures remain in the result instead of being hidden or retried into a
higher headline score.

Two non-official diagnostic repeats then reran the same five historical failure
IDs on the unchanged source commit. They passed [1/5](../../benchmarks/results/20260827T181737Z-agent-single.md)
and [0/5](../../benchmarks/results/20260827T181934Z-agent-single.md), respectively.
Both reports record a dirty worktree because the official report already
existed as an untracked artifact; neither run is eligible to replace or
recalculate the official 23/30 result.

## Integrity check

Run the standard-library checker from the repository root:

```bash
python scripts/check_release_evidence.py
```

It verifies report and Markdown hashes, suite and lockfile hashes, the Agent
official guard and source commit, model identity, all three aggregate results,
and the per-difficulty Agent/Platform counts. The same command runs in the
quality workflow.

## Interpretation boundaries

- The raw Agent JSON contains local host absolute paths from the recorded macOS
  run. It is evidence, not a portable replay bundle.
- Benchmark workspaces were temporary. Tool artifact files referenced by paths
  in the report were not permanently retained after the process exited.
- Memory 6/6 measures retrieval and filtering only; it does not prove that a
  chat model correctly applied an injected memory.
- Platform 30/30 is a deterministic offline platform/evaluator result, not an
  LLM or autonomous Agent capability score.
