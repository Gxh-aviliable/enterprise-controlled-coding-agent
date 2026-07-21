# Current Capability Matrix

Baseline: 2026-07-15. “Partial” means code exists but an important control, integration test, or operational proof is missing.

| Area | Capability | Status | Evidence | Main gap |
|---|---|---:|---|---|
| Agent | LangGraph stateful tool loop | Available | Explicit phase nodes and graph compile test | Model-backed benchmark still pending |
| Agent | Model transient retry | Available | Retry logic plus model trace integration test | Provider-specific error taxonomy can expand |
| Agent | Tool transient retry | Available | Contract registry and normalization tests | Distributed/process crash recovery not yet benchmarked |
| Agent | Failure recovery | Available | RedisSaver checkpoints, bounded retry, resume/cancel, confirmation expiry and benchmark recovery case | Cross-process crash chaos test pending |
| Agent | Verification after edit | Available | Code-change verification gate and lifecycle/benchmark tests | Real model-backed repository benchmark pending |
| Agent | Token/round limits | Available | per-task token/tool-call budgets, compaction, max rounds and trace events | Model-backed cost tuning pending |
| HITL | Sensitive tool pause/resume | Available | Interrupt, expiry, ownership and async route tests | Multi-process timeout scheduling needs production backend |
| Tools | File read/write/edit | Available | atomic replace, sensitive-path denial, traversal and verification tests | Legacy model-facing strings remain for compatibility |
| Tools | Shell execution | Partial | compound parser, relative/sensitive path policy, sanitized env, timeout and safety tests | Policy is defense in depth, not a process/kernel sandbox |
| Tools | Operational tasks/todos | Available | file task board and 32+ tests | Status vocabulary differs from execution state machine |
| Tools | Background commands | Available | process-group cancellation/reaping and regression tests | Container-level resource isolation remains pending |
| Tools | Skills | Available | shared/personal skill loader tests and Docker image copy | Shared-skill governance/signing pending |
| Multi-agent | Subagent and team tools | Partial | unit-level prompt/message-bus tests | No benchmark proves benefit; no integrated safety/cost evidence |
| Memory | Redis conversation checkpoints | Available | graph configuration | Requires Redis Stack and lacks operational replay UI |
| Memory | Chroma semantic memory | Available | real in-process Chroma write/search integration tests plus API | Production embedding still has a first-run model download |
| Auth | JWT login/refresh | Available | auth and permission-contract tests | Streaming entitlement policy remains coarse |
| Isolation | Workspace traversal prevention | Available | path escape and API tests | Shell process is not filesystem/network sandboxed |
| Isolation | Session ownership | Available | checkpoint route ownership regression tests | External penetration test pending |
| Observability | Unified task trace | Available | Trace store, graph/model/tool integration and API tests | Single-process JSON backend; distributed adapter pending |
| Observability | Core metrics | Available | `/tasks/metrics` aggregation tests | Real benchmark population pending |
| Evaluation | Unit/integration tests | Available | 292 passed, 0 skipped | External model E2E remains unmeasured |
| Evaluation | Versioned coding benchmark | Available | `benchmarks/v1/cases.json`, runner tests and checked-in platform report | Real single/multi-Agent reports require approved model access |
| Deployment | Full-stack Compose | Available | Isolated four-service health run, direct/proxied API checks, persistent volumes, reproducible smoke script | Schema migrations and operational backup/restore pending |
| UX | Vue workbench, SSE and trace replay | Available | production build plus browser-verified login, metric/list/replay, HITL, block and redaction rendering | Automated cross-browser/responsive E2E pending |
| Portfolio | README and handoff | Available | real platform table, demo script, before/after, résumé bullets and interview Q&A | Model-backed demo numbers remain TBD |

## Test coverage shape

- Strongest: file/shell/task/skill/team helper behavior and auth/workspace API helpers.
- Moderate: graph routing, state shape, chat history serialization.
- Weakest: real model execution, Redis checkpoint crash recovery, disk-level Chroma restart persistence, automated cross-browser E2E, and process-level shell isolation.

## Baseline claims allowed in a résumé

- Implemented a FastAPI + LangGraph coding-agent prototype with Redis checkpointing, MySQL authentication/session metadata, Chroma semantic memory, SSE streaming, workspace-scoped tools, and LangGraph interrupts.
- Maintained a 292-passing-test, zero-skip, zero-Ruff-finding local baseline as of 2026-07-15.

The deterministic platform runner produced 10/10 final task assertions, 80.0% tool-call success, 84.8 ms average duration, 20.0% human-intervention rate, and one safety interception. These values prove the harness/tool/state/policy path only. Claims about model-backed Agent success, model latency/token cost, or multi-Agent improvement remain **TBD** until external model execution is explicitly approved and completed.
