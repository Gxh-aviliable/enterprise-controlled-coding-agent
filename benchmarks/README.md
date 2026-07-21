# Mini Claude Code Benchmark

`v1/cases.json` is a versioned, self-contained coding-agent suite. Every case creates a fresh temporary workspace and carries its own fixtures, prompt, deterministic platform steps, assertions, category, and delegation-suitability flag.

## What the two backends mean

- `platform`: offline and deterministic. It validates workspace isolation, tools, policies, task lifecycle, confirmation/recovery, trace collection, and evaluators. It does **not** measure LLM reasoning quality.
- `agent`: invokes the configured model through the real in-memory LangGraph, tools, confirmation interrupts, verification gate, and Trace pipeline. It is the source of Agent task-success/token/latency claims.

Run the reproducible infrastructure baseline:

```bash
uv run python -m benchmarks.run --backend platform --mode single
```

Run the real single-Agent baseline only when synthetic benchmark content is approved for the configured model endpoint:

```bash
uv run python -m benchmarks.run --backend agent --mode single
```

Only after a single-Agent report exists, run the smaller delegation-suitable subset with multi-Agent tools exposed:

```bash
uv run python -m benchmarks.run --backend agent --mode multi
```

Reports are written as raw JSON plus a human-readable Markdown summary under `benchmarks/results/`. Connection/provider failures are reported as `infrastructure_error` and excluded from task-success-rate denominators instead of being misrepresented as Agent-quality failures.

## V1 coverage

| Area | Cases |
|---|---:|
| Repository understanding | 2 |
| File read/write | 2 |
| Bug fix | 1 |
| Shell/test validation | 1 |
| Failure recovery | 1 |
| Safety refusal | 2 |
| Confirmation interruption/resume | 1 |

Three cases are marked `delegation_suitable`. Multi-Agent mode intentionally excludes the remaining cases so that delegation is tested only where there is at least a plausible parallel-information benefit.

## Result interpretation

The checked-in platform report is expected to show one failed validation followed by recovery, two policy/permission failures, and successful final assertions. Consequently, tool-call success can be below 100% while task success remains 100%. Average token count is zero because the platform backend makes no model calls.

The benchmark is intentionally small and synthetic. It is useful as a regression and portfolio proof, not as a claim of production-grade coding-agent generality. Add cases without rewriting old results: create a new suite version when assertions or semantics change.

## Memory recall benchmark

`v1/memory_recall_cases.json` is a separate six-case retrieval suite. It runs the configured local sentence-transformer against an ephemeral Chroma collection, applies the production Active/disabled/relevance gates, and does **not** call DeepSeek or any chat model:

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
uv run python -m benchmarks.memory_recall
```

It reports Recall@3, Precision@3, mean reciprocal rank, negative false-injection rate, forbidden injections, Trace-field coverage, and injected-token-budget compliance. The checked-in 2026-07-20 report is a real local-embedding run on a small synthetic dataset. It proves retrieval/filter behavior only:

- an injected record may still be ignored or misapplied by the model;
- the `current_instruction_override` case records retrieval but marks behavioral compliance `not_measured`;
- model-backed memory application requires a separate Agent benchmark and explicit endpoint authorization.

The first diagnostic run before Chinese reranking passed 5/6 under the initial case gate but had only 27.78% Precision@3 and a 100% negative false-injection rate. The evaluator now also fails any positive case that injects an unexpected record. That failure motivated the language-aware lexical rerank and relative cutoff; the retained post-fix report passes 6/6 with no unexpected, negative, or forbidden injection.
