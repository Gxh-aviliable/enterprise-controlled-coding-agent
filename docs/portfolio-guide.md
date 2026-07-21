# Portfolio handoff

## Before and after

| Dimension | Before hardening | Current portfolio baseline |
|---|---|---|
| Execution | Reactive LLM/tool loop; completion inferred from messages | Explicit parse/plan/execute/checkpoint/validate/summarize phases and six-state lifecycle |
| Tools | Heterogeneous string results and coarse sensitive list | Contract registry with schema, permission, risk, timeout, retry, idempotency, side effect, and normalized result |
| Recovery | Model retries and Redis checkpoints existed but policy was implicit | Idempotent retry policy, confirmation expiry, cancellation/process reaping, verification gate, truthful failed terminal state |
| Security | Workspace path guard plus first-command substring blacklist | Tenant/session ownership checks, compound-shell parsing, relative-path policy, sensitive-file denial, credential-free child env, atomic writes, redacted Trace |
| Observability | Logs and SSE events were fragmented | One Trace ID across graph/model/tool/HITL/budget/final events, replay UI, task APIs, and six aggregate metrics |
| Evaluation | Unit tests only; no task-level comparable evidence | Versioned 10-case suite, deterministic platform baseline, real-Agent backend, and delegation-only multi-Agent subset |
| Deployment | API/MySQL/Redis Compose; frontend manual; no durable Agent volumes | Four-service health-gated Compose, Nginx SSE proxy, durable workspace/Chroma/HF volumes, non-root API, CPU-only PyTorch |
| Frontend | 1.15 MB initial bundle warning | Lazy views and language-scoped highlighting; largest production JS chunk 76.99 kB |

## Completed

- Stages 1–5 MVPs: audit, architecture/backlog/capability matrix, smoke baseline, reliable lifecycle, tool contracts, HITL timeout/recovery, Trace/API/UI, benchmark harness, security hardening, Docker delivery, documentation, and interview materials.
- Verification baseline: 292 tests passed with zero skips; real in-process Chroma write/search integration and Ruff both passed.
- Offline platform benchmark: 10/10 final task assertions, 80.0% tool-call success, 84.8 ms average task duration, zero model tokens, 20.0% intervention rate, and one safety interception.
- Docker: API and frontend images built; API image self-check passed as UID 10001 with CPU-only PyTorch; an isolated four-service Compose run passed all health checks plus direct and Nginx-proxied API checks.
- Frontend: production build passed with no large-chunk warning; npm reported zero known vulnerabilities.
- Trace UI: a real browser session logged into the isolated stack and rendered six metrics, the run list, nine ordered events, HITL/safety states, and redacted details with no console warning/error.

## Deliberately incomplete or still to prove

- Real model single-Agent benchmark and the 3-case multi-Agent comparison are **TBD**. Sandbox networking failed, and elevated execution was not authorized because it would send synthetic benchmark/tool context to an external provider.
- Shell policy is not a kernel/container sandbox. A workspace script can still attempt OS/network access; production needs ephemeral task containers, resource quotas, seccomp/AppArmor, and egress policy.
- JSON Trace storage is a single-process baseline. Multi-replica deployment needs centralized SQL/ClickHouse/OpenTelemetry storage and distributed confirmation scheduling.
- Database creation still uses SQLAlchemy `create_all`; production needs Alembic migrations, backup/restore tests, and secret-manager integration.

## Résumé-ready project bullets

- Built an enterprise-controlled AI Coding Agent with LangGraph, FastAPI/SSE, Redis checkpoints, MySQL/JWT multi-tenant sessions, Chroma memory, and Vue 3, implementing a six-state task lifecycle and `parse → plan → execute → validate → summarize` repair loop.
- Designed a contract-driven tool runtime with JWT permission filtering, workspace isolation, human approval/expiry, idempotent retries, token/tool budgets, atomic file writes, compound-shell risk blocking, process-group cancellation, and post-edit verification gates.
- Implemented end-to-end observability using a unified Trace ID across graph nodes, model calls, tools, approvals, retries, tokens, latency, errors, and final status, plus FastAPI replay/metric endpoints and a Vue execution timeline.
- Established a 292-passing-test, zero-skip baseline and versioned 10-case benchmark; achieved 10/10 deterministic platform assertions, built non-root CPU-only Docker images, reduced the largest frontend chunk from ~1.15 MB to 76.99 kB, and cleared Python lint/npm vulnerability baselines.

Use the word “platform” in the benchmark bullet. Do not present 10/10 as model-backed Agent success until an actual Agent report exists.

## 20 likely interview questions and answer points

1. **Why LangGraph instead of a plain while-loop?**  Explicit nodes/edges make pause/resume, checkpointing, routing, and per-step Trace testable; the trade-off is graph/state migration complexity.
2. **What makes this a coding Agent rather than a chatbot?**  It closes the loop from repository inspection through planning, tool mutation, executable verification, and evidence-backed summary.
3. **How is task success defined?**  By deterministic terminal state and assertions, not fluent prose. Code-changing tasks require at least one relevant successful validation record.
4. **Why six task states?**  They separate queued, active, human-blocked, successful, failed, and cancelled outcomes; legal transitions prevent contradictory UI/checkpoint state.
5. **How does retry work?**  Model transient failures use bounded backoff; only contract-declared idempotent tools retry. File/process side effects are not blindly repeated.
6. **How do you resume after confirmation?**  LangGraph interrupt persists checkpoint state and deadline; the authenticated session owner approves/rejects, then `Command(resume=...)` continues from the checkpoint.
7. **What if the user never confirms?**  A timeout task resumes with deterministic rejection and records the decision; in multi-replica production the scheduler must move to a durable queue.
8. **How is tenant isolation enforced?**  JWT resolves user identity, MySQL verifies session ownership before Redis checkpoint access, and every file/tool path resolves under `user_<id>`.
9. **Can shell still escape the workspace?**  The policy blocks absolute/traversal/sensitive paths, nested shells, substitutions, inline code, destructive Git, and credential inheritance, but it is not a kernel boundary. Production needs per-task containers and egress control.
10. **Why sanitize the subprocess environment?**  Otherwise `env`, child scripts, error output, or tools could leak model keys/JWT/database credentials into workspace output and model context.
11. **Why atomic file writes?**  Write-to-temp plus `fsync`/`os.replace` prevents timeout/crash from leaving a partially written source file while preserving existing mode bits.
12. **What is in a Trace?**  Trace ID, request summary, state/phase, node/model/tool events, redacted args/results, latency, token counts, retries, approvals, budgets, errors, and terminal result.
13. **How do you prevent Trace from becoming a data leak?**  Store bounded summaries rather than full prompts, recursively redact credential-like keys and bearer/API-key patterns, and scope files/API reads to the authenticated workspace.
14. **Why local JSON Trace instead of LangSmith/OpenTelemetry?**  It provides reproducible, vendor-neutral local evidence. It is an adapter baseline; centralized storage is the explicit next production step.
15. **Why is tool success only 80% when tasks are 10/10?**  The recovery case intentionally has a failed test before repair, and safety cases intentionally reject calls. Those expected failures reduce tool-call success while final task assertions pass.
16. **Is the 10/10 benchmark an Agent score?**  No. It is the deterministic platform backend. The model-backed single/multi cells remain TBD until approved execution produces raw reports.
17. **Why disable multi-Agent by default?**  Delegation adds tokens, latency, coordination failures, and a larger permission surface. Only three discovery-heavy cases are marked suitable for comparison after a single-Agent baseline exists.
18. **How do you control cost?**  Per-task token, round, and tool-call budgets; bounded tool output; context micro-compaction/full compaction; and Trace metrics for model/tool latency and usage.
19. **What Docker issue did you find?**  Transitive PyTorch resolved CUDA/NVIDIA wheels on Linux. Pinning the official CPU index removed CUDA/Triton dependencies; the API image builds at ~464.5 MB and runs as UID 10001.
20. **What would you do next with one week?**  Run approved single/multi model benchmarks, add Alembic, move Trace/confirmation scheduling to a durable backend, and execute shell jobs in ephemeral rootless containers with CPU/memory/time/network limits.
