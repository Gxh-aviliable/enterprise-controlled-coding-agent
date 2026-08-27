# Mini Claude Code Benchmark

`v2/cases.json` is the default coding-Agent suite. It contains 30 self-contained cases, split evenly into `easy`, `medium`, and `hard` (10 cases each). Every case runs in a fresh temporary workspace and defines its own fixtures, prompt, deterministic platform steps, assertions, category, protected paths, post-run checks, and delegation-suitability flag.

`v1/cases.json` remains available for historical compatibility and for reproducing the earlier 10-case reports. Because v2 expands both the workload and the evaluator, its score is a replacement baseline rather than a strictly like-for-like continuation of v1.

## What the two backends mean

- `platform`: offline and deterministic, and intentionally supports `single` mode only. It validates suite fixtures, workspace isolation, tools, selected policy blocks, scripted representative confirmation/recovery flows, Trace collection, and evaluators. It does **not** measure LLM reasoning quality or an exhaustive real-policy intervention rate.
- `agent`: invokes the configured model through the real in-memory LangGraph, tools, confirmation interrupts, verification gate, and Trace pipeline. It is the source of model task-success, token, and latency measurements.

These results are intentionally separate: `platform` passing 30/30 means the 30
fixtures, scripted tool paths, and deterministic assertions are internally
consistent. It never asks a model to produce a response, so it cannot exercise
provider finish reasons, token-limit truncation, thinking-only output, or model
decisions to stop too early. The current checked-in real-Agent v2 baseline is
23/30, not 30/30.

The 30-case Agent score measures completion of synthetic coding tasks. It does **not** by itself prove the HTTP/SSE Stop/Cancel control plane, durable cancellation, or cancel-and-replan semantics. Those behaviors are evidenced separately by deterministic API and integration tests, for example:

```bash
uv run pytest \
  tests/api/test_cancelled_turn_regression.py \
  tests/api/test_cancel_replan_context.py \
  tests/admin/test_task_cancellation.py -q
```

## Running the suites

Preflight the complete v2 workload with the deterministic backend before spending model tokens:

```bash
uv run python -m benchmarks.run \
  --suite v2 --backend platform --mode single --no-artifacts
```

After committing the suite and runner changes, run the complete real-model baseline from that clean commit:

```bash
uv run python -m benchmarks.run \
  --suite v2 --backend agent --mode single --official
```

Run a legacy v1 reproduction explicitly:

```bash
uv run python -m benchmarks.run \
  --suite v1 --backend agent --mode single
```

`--suite` accepts `v1`, `v2`, or an explicit `cases.json` path. The following filters can be repeated; combining them takes their intersection:

```bash
# One or more difficulty levels
uv run python -m benchmarks.run --level easy --level medium

# One or more exact category names
uv run python -m benchmarks.run --category bug_fix --category safety_refusal

# One or more exact case IDs
uv run python -m benchmarks.run --case easy.understanding.entrypoint
```

`--official` is the publishable v2 guard. It only accepts the canonical 30-case `agent` / `single` run with result artifacts enabled, fails closed before execution if Git cannot identify the commit or the worktree is dirty, pins the starting commit, rejects partial-suite filters, and checks the same commit and clean tree again after the cases finish. An official report is marked valid only when the run has no skipped cases, provider-infrastructure errors, or system errors.

Only after the single-Agent report exists should the smaller delegation-suitable subset be run with multi-Agent tools exposed:

```bash
uv run python -m benchmarks.run \
  --suite v2 --backend agent --mode multi
```

Unless `--no-artifacts` is supplied, reports are written as raw JSON plus a human-readable Markdown summary under `benchmarks/results/`. Connection/provider failures are recorded as `infrastructure_error` and excluded from task-success-rate denominators instead of being misrepresented as Agent-quality failures. Runner or Agent defects are recorded as `system_error` and count as task failures.

## V2 evaluator safeguards

- Before task assertions are scored, the Agent runner applies a terminal-integrity
  guard. A graph state marked `succeeded` is recorded as `system_error` when the
  provider stop reason indicates token truncation, the terminal assistant message
  has no visible text (including thinking-only output), pending/in-progress Todos
  remain, recovery counters were not cleared, or a task classified as requiring
  execution has no successful execution evidence. These are deterministic runner
  regressions; they do not increase the formal suite beyond 30 tasks and do not
  imply that the offline `platform` backend exercises real provider streaming.
- Workspace manifests hash the initial and final user-visible files, including type, content digest, size, mode, mtime, and ctime. Operational `.agent` artifacts are excluded. Assertions can require an exact added/modified/deleted path set.
- `protected_files` supports exact paths and glob patterns. A case that declares protected paths automatically receives final-state content/metadata integrity plus successful first-party `write_file` / `edit_file` / `delete_paths` Trace checks. This catches normal direct and shell touch-and-restore attempts, but it remains bounded benchmark evidence rather than a kernel-level filesystem-audit claim.
- `post_checks` run deterministic, argv-based commands after the Agent finishes. They use a minimal environment that excludes host `PYTHONPATH`, `PYTEST_ADDOPTS`, `NODE_OPTIONS`, and npm configuration, plus a private HOME/TMP, bounded timeout, captured output, and optional hidden fixtures injected after the final workspace manifest.
- Every report carries a secret-free reproducibility manifest: Git commit/branch/dirty state, suite and `uv.lock` SHA-256, exact selected case IDs, difficulty/category selection, Python/platform identity, Node/npm versions, timestamps, Agent limits, provider/model, sanitized endpoint, and inference-default policy. API keys, URL credentials, query strings, and fragments are never written to artifacts.

## V2 coverage

| Difficulty | Cases | Emphasis |
|---|---:|---|
| Easy | 10 | Repository reading, exact file/config edits, small fixes, test execution, basic safety refusal |
| Medium | 10 | Recovery loops, behavior-preserving edits, confirmation resume, background work, secret isolation |
| Hard | 10 | Multi-file reasoning, security hardening, refactors, cross-language contracts, hidden checks, large-output artifacts, cancel-and-replan workspace inspection |
| **Total** | **30** | Broad synthetic coding-Agent regression coverage |

Six v2 cases are marked `delegation_suitable`. Multi-Agent mode intentionally excludes the remaining cases so delegation is measured only where parallel information gathering is plausibly useful.

## Canonical checked-in reports

The active Agent baseline is the complete, valid v2 `--official` run on clean source commit `1d637c5753e93c72989c3fdae2ab5edf50e078eb`. `deepseek-v4-flash` passed 23/30 cases: easy 7/10, medium 10/10, and hard 6/10, with 77.53% tool success, zero provider-infrastructure errors and zero system errors. The evidence-only release commit adds these raw reports and documentation without changing the evaluated runtime source; the exact source/evidence relationship is recorded in [`docs/release-evidence/portfolio-v1.0.md`](../docs/release-evidence/portfolio-v1.0.md).

| Layer | Result | Canonical artifact | What it proves |
|---|---:|---|---|
| Platform single (v2) | 30/30; 89.39% tool success | [`20260827T182126Z-platform-single.md`](results/20260827T182126Z-platform-single.md) / [`JSON`](results/20260827T182126Z-platform-single.json) | Offline deterministic fixtures, scripted tools, policy, state and evaluator behavior; no LLM call |
| Memory recall (v1) | 6/6 | [`20260827T182146Z-memory-recall.md`](results/20260827T182146Z-memory-recall.md) / [`JSON`](results/20260827T182146Z-memory-recall.json) | Small synthetic-set retrieval, filtering and Trace-field coverage; not model application |
| DeepSeek V4 Flash Agent single (v2) | 23/30 (easy 7/10, medium 10/10, hard 6/10); 77.53% tool success | [`20260827T181517Z-agent-single.md`](results/20260827T181517Z-agent-single.md) / [`JSON`](results/20260827T181517Z-agent-single.json) | One complete valid official autonomous run with tokens, latency, deterministic assertions, 0 infrastructure errors and 0 system errors |

Historical artifacts remain immutable evidence for their recorded code states: Platform v1 passed [10/10](results/20260715T125211Z-platform-single.md), and the former `7562a95` Agent run passed [25/30](results/20260819T160324Z-agent-single.md). Neither is a current release result. The multi-Agent comparison remains unmeasured.

The Agent score is stochastic model evidence. Even though v2 includes a `cancel_replan` workspace-reconciliation case, it does not establish the HTTP/SSE Stop/Cancel protocol, durable cancellation, runner fencing, or cancel-and-replan control-plane semantics. Those claims rely on the deterministic API and integration tests listed above and must be reported separately from the 23/30 model score.

## Result interpretation

The platform backend should pass the same final case assertions without model calls, so its token count is zero. Tool-call success can still be below 100% when a case deliberately exercises failed validation, policy interception, or recovery before reaching the expected final state. Its human-intervention metric reflects only the suite's scripted confirmation examples; the Agent backend is the source of real typed-HITL counts.

Both suites are synthetic regression and portfolio evidence, not claims of production-grade coding-Agent generality. Preserve historical results when evaluator semantics change: add a new suite version and label cross-version comparisons accordingly.

## Memory recall benchmark

`v1/memory_recall_cases.json` is a separate six-case retrieval suite. It runs the configured local sentence-transformer against an ephemeral Chroma collection, applies the production Active/disabled/relevance gates, and does **not** call DeepSeek or any chat model:

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
uv run python -m benchmarks.memory_recall
```

It reports Recall@3, Precision@3, mean reciprocal rank, negative false-injection rate, forbidden injections, Trace-field coverage, and injected-token-budget compliance. The current checked-in [2026-08-27 report](results/20260827T182146Z-memory-recall.md) is a real local-embedding run on a small synthetic dataset. It proves retrieval/filter behavior only:

- an injected record may still be ignored or misapplied by the model;
- the `current_instruction_override` case records retrieval but marks behavioral compliance `not_measured`;
- model-backed memory application requires a separate Agent benchmark and explicit endpoint authorization.

The first diagnostic run before Chinese reranking passed 5/6 under the initial case gate but had only 27.78% Precision@3 and a 100% negative false-injection rate. The evaluator now also fails any positive case that injects an unexpected record. That failure motivated the language-aware lexical rerank and relative cutoff; the current post-fix report passes 6/6 with no unexpected, negative, or forbidden injection.
