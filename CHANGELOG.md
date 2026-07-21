# Changelog

All notable project changes are recorded here. Benchmark and performance claims are included only after reproducible execution.

## [Unreleased]

### Added

- Internal admin-console development plan covering live authorization, user/quota operations, break-glass workspace access, versioned Shared Skill governance, audit evidence, UI structure, API contracts, data models, and phased acceptance criteria.
- Architecture baseline, current capability matrix, and risk-ranked portfolio hardening backlog.
- Dependency-light smoke test covering application imports, graph compilation, workspace isolation, file tools, safe shell execution, and dangerous-command rejection.
- Regression coverage for task-manager cache isolation across workspace changes.
- Validated six-state task lifecycle and explicit parse/plan/execute/checkpoint/validate/summarize graph phases.
- Uniform contract metadata for every registered tool: input schema, risk, timeout, retries, idempotency, confirmation policy, side-effect class, and normalized result records.
- Per-task token/tool-call budgets, code-change validation gate, and single-Agent-by-default configuration.
- Automatic confirmation expiry that resumes an interrupted graph with a deterministic rejection.
- Workspace-scoped redacted Trace store with atomic writes and events for LangGraph nodes, model summaries/tokens, tools, confirmations, budgets, context compaction, errors, and final results.
- Task detail, trace replay, and aggregate metric APIs under `/tasks`.
- Vue execution-trace page with a task index, six acceptance metrics, ordered execution spine, latency bars, and expandable redacted evidence.
- Versioned 10-case coding-agent benchmark with deterministic platform and real-Agent backends, machine-readable assertions, raw JSON/Markdown artifacts, and a delegation-suitable multi-Agent subset.
- Full-stack Compose delivery with Nginx/Vue, API, MySQL, Redis, health dependencies, durable workspace/Chroma/model-cache volumes, shared skills, and non-root API execution.
- Five-minute demo script plus portfolio handoff with before/after architecture, honest limitations, résumé bullets, and 20 interview Q&As.
- Requirement-by-requirement acceptance audit separating locally proven evidence from the unmeasured external-model path.
- macOS-to-Linux server deployment guide covering Git/image delivery, Docker Compose startup, HTTPS, persistent data, updates, rollback, code-server, and production-hardening gaps.
- Explicit per-request `single_agent` / `multi_agent` API mode, authenticated capabilities endpoint, and frontend mode selector.
- Real tool-free specialist delegation through `delegate_task(role, prompt)` for bounded planning, drafting, and review roles.
- Live database-derived authorization so account promotion, demotion, or disabling takes effect without trusting stale JWT permission claims.
- Per-task memory recall receipts with candidate IDs, semantic/lexical rank evidence, filter reasons, injected-token cost, and aggregate memory-injection metrics.
- Versioned six-case local-embedding memory benchmark covering direct/paraphrased recall, task outcomes, negative rejection, Legacy/disabled filtering, and current-instruction conflict.

### Fixed

- Task-manager cache no longer retains an obsolete workspace when the workspace base changes.
- Background commands now resolve and create their user workspace before the worker thread starts, avoiding cleanup/context races.
- Chat invoke/resume/cancel/confirm/pending-confirm routes now verify MySQL session ownership before accessing Redis checkpoints.
- Main-model tool binding and executor dispatch now enforce JWT tool permissions; the previous permission helper is no longer disconnected.
- SSE internal-output filtering is request-local instead of process-global, preventing concurrent sessions from suppressing each other's tokens.
- Confirmation routes use LangGraph's asynchronous checkpoint/invoke APIs correctly.
- Session cancellation terminates and reaps background process groups instead of only changing an in-memory flag.
- Shell policy now parses every compound command segment and blocks substitution, nested shells, inline code, absolute/traversal/sensitive paths, destructive Git, and direct transfer tools; child processes receive a credential-free environment.
- File writes/edits are atomic and Agent tools fail closed on `.env`, `.git`, SSH/cloud credential and private-key paths.
- Linux dependency resolution uses CPU-only PyTorch, removing CUDA/NVIDIA/Triton packages from the API image.
- Frontend views are lazy-loaded and highlighting includes only 15 relevant languages; the largest JS chunk fell from 1,145.69 kB to 76.99 kB.
- Updated DOMPurify, Vite, and the Vue plugin; full npm audit now reports zero known vulnerabilities.
- Frontend container health checks now use the IPv4 loopback explicitly, avoiding Alpine `localhost` resolving to an unserved IPv6 loopback address.
- Chroma pattern search now builds a valid `$and` filter when both tenant and pattern type are present; real in-process Chroma integration tests replace four skipped placeholders.
- API Docker layering now installs locked third-party dependencies before application source, uses a persistent uv build cache, and installs the local project offline so ordinary source edits do not invalidate the large dependency layer.
- Chat history, in-progress input, SSE state, and pending confirmations now survive Chat/File/Trace/Memory navigation; an unexpectedly remounted chat panel also reloads the selected session from the backend and ignores stale history responses.
- Unknown model tool calls now bypass risk classification safely and produce traced `unknown_tool` failures instead of crashing with a missing-contract exception.
- Permission-denied calls now produce `blocked/permission_denied` Trace records rather than being counted as successful tools.
- Failed/cancelled runs close open Todo items and persistent task records created by that run, preventing a failed graph from leaving operational work marked in progress.
- Multi-Agent requests fail explicitly when the server switch or advanced permission is absent; the Agent is instructed never to implement a fake collaboration simulator or confuse `task_create` with delegation.
- The frontend Reject All action now resumes the interrupted graph with an explicit rejection instead of only closing the modal and leaving the backend waiting for timeout.
- Shell/background HITL is now argument-sensitive: safe inspection, test, and build commands run without an interrupt; review-level commands still require approval; dangerous commands cannot be approved and are sent directly to the executor's policy block.
- The confirmation modal now displays the resolved risk and labels batch approval as `Approve Current Batch`, making its non-persistent scope explicit.
- Blocked background commands now normalize as `policy_blocked` instead of a generic tool error.
- Explicit Multi-Agent execution intent can no longer silently enter Single mode: the UI asks to switch, the API rejects mismatched requests, and Multi runs must complete a real `delegate_task` before mutation or success.
- HITL `GraphInterrupt` is now traced as normal `interrupted/waiting_confirmation` control flow instead of a node error.
- SSE tool completion now comes from normalized execution records in initial and resumed streams; frontend cards match by tool-call ID and no longer turn unresolved/failed calls green at `[DONE]`.
- The execution-mode selector now stays beside the prompt, and the frontend ships a build manifest/update banner plus an uncached SPA shell to prevent stale pre-deployment pages hiding new controls.
- Default runaway limits were tightened from 50 rounds/40 tools/120k tokens to 20 rounds/25 tools/48k tokens per task.
- Streaming output now follows the bottom only while the user remains near it; scrolling upward pauses auto-follow and exposes a `Latest` control instead of repeatedly stealing the reading position.
- Long-term memory persistence now runs after authoritative task finalization; failed, cancelled, unverified, non-engineering, and evidence-free tasks are rejected before summary/model cost.
- New schema-v2 memories carry type, task status, quality state, admission reason, source, trace, and execution mode; legacy records remain manageable but are quarantined from Agent context.
- One-off instructions no longer become durable preferences. Pattern extraction requires explicit persistence language and uses upsert with an evidence counter.
- Automatic recall now applies an Active-only relevance threshold and no longer falls back to injecting unrelated summaries when semantic search has no good match.
- The Memory page is now a quality ledger with Active/Legacy totals, quality filters, typed records, evidence counts, admission explanations, and collapsed raw summaries.
- Cached sentence-transformer models now load with `local_files_only` first; a network download is attempted only on a real cache miss and can be disabled for intranet deployments.
- API image source-only rebuilds no longer recursively `chown` the large dependency environment; the measured local rebuild dropped from several minutes to about 3.2 seconds.
- Same-session durable-memory requests now use the current API request instead of accidentally reusing the first user message in the Chat.
- Explicit `user_note` records are stored atomically and no longer expanded into a generated task narrative or duplicated into multiple inferred patterns.
- Recall now runs for every user task and is injected ephemerally instead of being persisted into conversation history.
- Chinese recall uses deterministic lexical reranking and a relative relevance cutoff, preventing the existing English-oriented embedding from broadly injecting every Active record.
- Memory Ledger separates stored, recalled, never-recalled, and quarantined records; Trace replay presents a candidate-to-injection receipt and states that injection is not proof of application.
- Deleting a source memory now removes its derived preferences before the parent record and returns an explicit cascade receipt; patterns without source provenance are dynamically quarantined.
- Model-initiated `search_memory` calls now enforce the same Active/relevance gates as automatic context retrieval, update retrieval counters, and emit the same `memory_retrieval` Trace receipt.
- Empty `search_memory` results now record zero injected characters/tokens instead of charging the explanatory “not found” tool text to memory cost.
- Memory Ledger totals now disclose the Task outcome/Preference split, tab badges show the current filtered counts, and an empty tab links to records counted in the other tab.

### Baseline verification

- Backend: 343 passed (2026-07-20); real Chroma v2 write/search, provenance quarantine, cascade deletion, task-boundary/tool recall, Chinese reranking, admission policy, offline-first embedding initialization, and full Ruff checks passed.
- Offline platform benchmark: 10/10 tasks passed; 80.0% tool-call success, 84.8 ms average task duration, 20.0% intervention rate, and 1 safety interception. This deterministic run uses no LLM and is not an Agent intelligence score.
- Frontend: production build passed with a 76.99 kB largest JS chunk; npm audit reported zero known vulnerabilities.
- Frontend regression suite: 16/16 tests passed, including history/view preservation, explicit mode propagation/escalation, disabled-capability behavior, HITL rejection, authoritative tool status mapping, user-controlled streaming scroll, Memory provenance/cascade evidence, and Recall receipt replay.
- Docker Compose config and API/frontend image builds passed. API self-check ran as UID 10001 with `torch 2.13.0+cpu`, CUDA false, and successful app import.
- An isolated four-service Compose startup passed with API, Vue/Nginx, MySQL, and Redis healthy; API direct health and the frontend `/api/health` reverse proxy both returned MySQL/Redis `ok`. `scripts/docker_smoke_test.sh` makes this check reproducible without using the default host ports.
- Browser replay passed against the rebuilt local stack using only synthetic local data: registration, seven metrics, candidate filtering, memory injection/token evidence, non-attribution warning, and zero console warnings/errors were verified.
- Local memory-recall benchmark initially exposed 27.78% Precision@3 and 100% negative false injection; after Chinese reranking, the retained run passed 6/6 with 100% Recall@3/Precision@3, MRR 1.0, zero negative false injection, and zero forbidden injection. This is retrieval evidence, not model-application evidence.

### Not yet measured

- Model-backed single-Agent task success, latency, token usage, and single-Agent versus multi-Agent comparison. User authorization for the synthetic DeepSeek benchmark exists, but no new external model run was made during this repair; metrics remain `TBD`.
