# Portfolio acceptance audit

Audit date: 2026-07-15. “Proven” requires an inspected artifact or executed check. “Partial” means the implementation exists but the original acceptance scope still has an unmeasured external-model path. Platform benchmark results are never treated as model intelligence results.

## Final delivery requirements

| Requirement | Status | Authoritative evidence | Remaining proof |
|---|---|---|---|
| 1. Repository understanding → plan → tool edit → validation → report | Partial | Explicit lifecycle in `enterprise_agent/core/agent/graph.py`; lifecycle tests; `edit.fix_subtract` and `recovery.fail_fix_pass` in `benchmarks/v1/cases.json`; checked-in platform report | Run the same assertions through the configured external LLM backend |
| 2. Task state machine, retry/recovery, dangerous-operation HITL | Proven locally | Six-state transition tests; confirmation expiry/resume/cancel tests; process-group tests; platform confirmation, failure-recovery and safety cases | Cross-process crash chaos test is a production enhancement, not part of the local MVP proof |
| 3. Unified Trace for model/tool/time/token/error/result | Proven locally | Trace store and integration tests; `/tasks` API tests; browser-verified six metrics, nine-event replay, HITL/block/redaction rendering | Distributed storage and a real provider run remain operational extensions |
| 4. Versioned reproducible evaluation across required categories | Proven for platform, partial for Agent | `benchmarks/v1/cases.json` has 10 cases across understanding, bug fix, file I/O, shell validation, recovery, safety and interruption; raw JSON/Markdown report checked in | Model-backed single and multi reports are TBD |
| 5. Success/tool success/time/token/intervention/safety metrics | Proven locally | `TraceStore.aggregate_metrics`, `/tasks/metrics`, tests, Trace UI and platform report | Real-model token/latency values remain TBD |
| 6. One-command Docker, README, architecture, demo and résumé evidence | Proven | `scripts/docker_smoke_test.sh`; four healthy services plus direct/proxied API checks; `README.md`, `ARCHITECTURE.md`, `docs/demo-script.md`, `docs/portfolio-guide.md` | Production migration/backup/secret-manager work is explicitly outside the local MVP |
| 7. Reliable single baseline before selective multi-Agent comparison | Partial | Multi-Agent disabled by default; 10-case platform single baseline; only three cases marked `delegation_suitable` | Authorized external-model single 10-case and multi 3-case comparison |

## Stage acceptance

| Stage | Status | Evidence summary |
|---|---|---|
| 1. Audit and baseline | Proven | Architecture, capability matrix, risk backlog, local smoke 7/7, 292 tests with zero skips, reproducible quick start |
| 2. Reliable execution loop | Partial | Lifecycle/contracts/HITL/recovery implementation and 10/10 deterministic platform tasks pass; autonomous model execution is unmeasured |
| 3. Observability and cost control | Proven locally | Unified Trace, budgets/compaction, replay API/UI, aggregate metrics, browser E2E |
| 4. Evaluation and single/multi comparison | Partial | Versioned runner and platform baseline complete; real-model comparison requires external data-transfer authorization |
| 5. Security and engineering delivery | Proven as a local baseline | Path/shell/secret/atomic-write controls, non-root CPU-only image, four-service health run, browser replay, documentation and demo assets |

## Latest reproducible evidence

- `.venv/bin/python -m pytest -q`: `292 passed in 7.37s`, zero skipped.
- `.venv/bin/ruff check enterprise_agent tests benchmarks scripts`: zero findings.
- Platform benchmark artifact: `benchmarks/results/20260715T125211Z-platform-single.json`, 10/10 final assertions, 80.0% tool-call success, 84.8 ms mean duration, 20.0% intervention rate, one safety interception and zero model tokens.
- `scripts/docker_smoke_test.sh`: API, Vue/Nginx, MySQL and Redis healthy; API direct and Nginx-proxied health passed.
- Browser E2E: synthetic local login and Trace replay passed; metrics, run list, ordered events, HITL/safety states and redacted detail rendered with zero console warnings/errors.
- API image: UID 10001, `torch 2.13.0+cpu`, CUDA false and application import successful.

## Blocking authorization

The remaining model-backed run would transmit synthetic benchmark prompts and generated tool context to the configured external provider. It has not been executed or fabricated. To authorize it, reply:

> 我授权将 benchmarks/v1 的合成用例及执行时产生的工具上下文发送到当前配置的外部模型 API，并运行 single 10 个用例和 multi 3 个用例。
