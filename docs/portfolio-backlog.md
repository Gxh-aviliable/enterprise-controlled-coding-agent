# Portfolio Hardening Backlog

This backlog is ordered by risk and dependency, not by feature novelty. Each item must ship with tests, documentation, and recorded verification.

## P0 — Correctness and tenant safety

| ID | Work item | Minimum acceptance evidence | Stage |
|---|---|---|---:|
| P0-1 | Add durable task-run state machine with validated transitions | Unit tests for all legal/illegal transitions and cancellation | 2 |
| P0-2 | Verify session ownership on invoke, resume, cancel, confirm and pending-confirm routes | Cross-user API tests return 404/403 without reading checkpoints | 2/5 |
| P0-3 | Replace process-global SSE suppression state with per-stream state | Concurrent-stream regression test | 2 |
| P0-4 | Fix sync/async graph API misuse in confirm/pending-confirm routes | Route tests with async graph doubles | 2 |
| P0-5 | Define a single tool contract: schema, risk, timeout, retry, idempotency, normalized result | Contract tests cover every registered tool | 2 |
| P0-6 | Enforce JWT tool permissions when binding and executing tools | A user without shell permission cannot request or execute shell | 2/5 |
| P0-7 | Add confirmation expiry/rejection recovery | Deterministic timeout test; task reaches failed/cancelled state | 2 |

## P1 — Reliable execution and observability

| ID | Work item | Minimum acceptance evidence | Stage |
|---|---|---|---:|
| P1-1 | Represent parse → plan → execute → checkpoint → verify → summarize phases | Trace for a modifying task contains ordered phase events | 2 |
| P1-2 | Add verification gate after code changes | Modified task either records a validation command or reports unverified | 2 |
| P1-3 | Add one trace ID across API, graph, model and tool events | Trace API returns timings, tokens, retries, errors and result | 3 |
| P1-4 | Add task detail/trace replay API and minimal workbench page | One completed and one failed task can be replayed | 3 |
| P1-5 | Add token budget, tool-call limit and context-trimming events | Budget-exhaustion tests and trace reason | 3 |
| P1-6 | Normalize error categories and recovery decisions | Tests for transient, validation, policy and fatal errors | 2/3 |
| P1-7 | Make background processes cancellable and cleanly reaped | No leaked process/thread in timeout/cancel tests | 2 |

## P2 — Evaluation and proof

| ID | Work item | Minimum acceptance evidence | Stage |
|---|---|---|---:|
| P2-1 | Version benchmark case schema and deterministic fixtures | Schema validation and fixture reset tests | 4 |
| P2-2 | Add cases for repository understanding, bug fix, file IO, shell validation, safety, interruption/recovery | At least 10 reproducible cases across all six categories | 4 |
| P2-3 | Emit success rate, tool success, steps, latency, tokens, intervention and safety blocks | Machine-readable JSON plus Markdown report | 4 |
| P2-4 | Run single-agent baseline | README contains dated environment/model/config and raw artifact link | 4 |
| P2-5 | Run multi-agent only on delegation-suitable cases | Cost/quality comparison and failed-case discussion | 4 |

## P3 — Delivery and portfolio presentation

| ID | Work item | Minimum acceptance evidence | Stage |
|---|---|---|---:|
| P3-1 | Harden shell policy and secret redaction | Safety benchmark plus redaction tests | 5 |
| P3-2 | Add full-stack Compose, durable volumes and health checks | Fresh clone can reach UI/API and run smoke task | 5 |
| P3-3 | Copy shared skills into image and add schema migrations | Container skill smoke test; migration upgrade test | 5 |
| P3-4 | Finish README, before/after diagram, limitations and real result table | New-developer walkthrough | 5 |
| P3-5 | Add 3–5 minute demo script and failure fallback | Timed rehearsal checklist | 5 |
| P3-6 | Reduce frontend initial bundle or document trade-off | Build has no unexplained large-chunk warning | 5 |

## Deferred by design

- More agent roles, autonomous teammate spawning, and complex multi-agent routing are deferred until the single-agent benchmark is stable.
- Container/microVM command isolation is preferred over claiming a blacklist is a complete sandbox.
- Production-grade distributed tracing backends are optional; the project first needs a self-contained local trace baseline.

## Stage-one exit checklist

- [x] Architecture and execution path documented.
- [x] Current capability matrix distinguishes partial and missing behavior.
- [x] Risk-ranked backlog created.
- [x] Baseline tests executed and failures fixed without a rewrite.
- [ ] Service-backed smoke task from a fresh clone is timed end to end.
- [ ] README quick start is independently reproduced on a clean machine/container.
