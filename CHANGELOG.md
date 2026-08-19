# Changelog

All notable project changes are recorded here. Benchmark and performance claims are included only after reproducible execution.

## [Unreleased]

### Added

- Guarded Workspace Preview/Edit with sanitized Markdown rendering, syntax-highlighted code, 500-line incremental preview, dirty/save/discard states, `Cmd/Ctrl+S`, unsaved-navigation protection, and actionable inline VS Code errors. Browser writes use SHA-256 optimistic locking and atomic replacement, return a structured 409 conflict, and reject sensitive or Agent-owned paths, symlinks, binary files, new files, and UTF-8 files above 1 MiB.
- Redis-coordinated cooperative Pause/Continue with exact user/session/Trace control keys, a checkpointed `paused` lifecycle, safe-boundary pause gates, typed `user_pause` interrupts, duplicate-resume locking, refreshed-page status recovery, same-Trace continuation, and a separate terminal Stop/Cancel path. In-flight model and foreground-tool calls are not forcibly preempted; Pause takes effect at the next boundary.
- Workspace-scoped tool-output artifacts with sanitized content-addressed paths, redacted bounded evidence, SHA-256 receipts, atomic/private writes, authenticated UTF-8-safe range reads, Trace/SSE linkage, and independent model-preview limits.
- Schema-v2 context continuation packets that combine deterministic task facts (goal, status, failure, Todo, changed files, validation and tool receipts) with an explicitly supplemental model narrative.
- Whole-summary prompt/output limits, next-main-turn continuation budgets with growth headroom, conservative Unicode/high-entropy token estimation, and one-shot recovery for provider context-length errors.
- Fail-closed legacy child-Agent boundaries: autonomous subagents/teammates are read-only, non-safe tools return to the governed lead runtime, and team inbox names cannot traverse the Workspace.
- Source-grounded Chinese “backend Agent from zero” reading guide with a 20-station, code-block-by-code-block route covering startup, API/SSE, AgentState, LangGraph nodes and routing, model/tool loops, HITL recovery, validation, context budgets, memory, Trace, Single/Multi boundaries, tests, and a seven-day study path.
- Canonical documentation index and current-code walkthrough replacing four overlapping, line-number-sensitive beginner guides.
- Canonical benchmark artifact policy retaining one final platform report, one memory report, and one real single-Agent report.
- Secret-free benchmark reproducibility manifests recording Git commit/branch/dirty state, suite and lockfile hashes, selected cases, runtime, Agent limits, model identity, sanitized endpoint, and effective inference-default policy.
- Recoverable `delete_paths(paths, reason)` Agent tool with exact-path HITL, protected workspace/system paths, wildcard and overlap rejection, symlink-safe moves, rollback on partial failure, and per-operation recovery manifests under `.agent/trash/`.
- Persistent `session_token_count` enforcement with a configurable 1,000,000-token cumulative session budget, separate from per-task usage and context compaction.
- Admin Control Room MVP with live `/auth/me` authorization, user status and session/API-key revocation, quota configuration/usage, metadata-first workspace inspection, audited temporary content grants, cross-user task summaries/cancellation, audit search, and dependency/storage health.
- Alembic adoption migration that works for both clean databases and legacy `create_all()` installations, including the administrator schema and JWT `auth_version` revocation generation.
- Runtime product quota enforcement with Redis atomic concurrent leases, MySQL daily task settlement, Trace-derived daily/monthly token checks, structured 429 errors, and release on normal/error/SSE exit paths.
- Administrator-managed Shared Skill registry with validation/credential scanning, immutable versions, durable materialization, publish/rollback/retire actions, cache refresh, and runtime version/SHA-256 evidence.
- Vue administrator console with a persistent Access Scope Bar, fleet overview, user/quota operations, guarded workspace preview, Shared Skill editor/version controls, audit ledger, and system health.
- Current administrator-console reference covering live authorization, quotas, audited temporary workspace access, Shared Skill governance, API groups, and production boundaries.
- Architecture baseline, current capability matrix, canonical documentation index, and code walkthrough.
- Dependency-light smoke test covering application imports, graph compilation, workspace isolation, file tools, safe shell execution, and dangerous-command rejection.
- Regression coverage for task-manager cache isolation across workspace changes.
- Validated seven-state task lifecycle, including non-terminal `paused`, and explicit parse/plan/execute/checkpoint/validate/summarize graph phases.
- Uniform contract metadata for every registered tool: input schema, risk, timeout, retries, idempotency, confirmation policy, side-effect class, and normalized result records.
- Per-task token/tool-call budgets, code-change validation gate, and single-Agent-by-default configuration.
- Automatic confirmation expiry that resumes an interrupted graph with a deterministic rejection.
- Workspace-scoped redacted Trace store with atomic writes and events for LangGraph nodes, model summaries/tokens, tools, confirmations, budgets, context compaction, errors, and final results.
- Task detail, trace replay, and aggregate metric APIs under `/tasks`.
- Vue execution-trace page with a task index, six acceptance metrics, ordered execution spine, latency bars, and expandable redacted evidence.
- Versioned 10-case coding-agent benchmark with deterministic platform and real-Agent backends, machine-readable assertions, raw JSON/Markdown artifacts, and a delegation-suitable multi-Agent subset.
- Full-stack Compose delivery with Nginx/Vue, API, MySQL, Redis, health dependencies, durable workspace/Chroma/model-cache volumes, shared skills, and non-root API execution.
- Five-minute demo script plus portfolio guide with before/after architecture, honest limitations, résumé bullets, and 20 interview Q&As.
- macOS-to-Linux server deployment guide covering Git/image delivery, Docker Compose startup, HTTPS, persistent data, updates, rollback, code-server, and production-hardening gaps.
- Explicit per-request `single_agent` / `multi_agent` API mode, authenticated capabilities endpoint, and frontend mode selector.
- Real tool-free specialist delegation through `delegate_task(role, prompt)` for bounded planning, drafting, and review roles.
- Live database-derived authorization so account promotion, demotion, or disabling takes effect without trusting stale JWT permission claims.
- Per-task memory recall receipts with candidate IDs, semantic/lexical rank evidence, filter reasons, injected-token cost, and aggregate memory-injection metrics.
- Versioned six-case local-embedding memory benchmark covering direct/paraphrased recall, task outcomes, negative rejection, Legacy/disabled filtering, and current-instruction conflict.

### Changed

- The chat composer now exposes exactly one state-aware primary action: Send while idle, Stop while running, and Continue while paused. Cooperative Pause and paused-task Cancel remain available as labelled secondary controls in the execution bar, and late Pause responses can no longer revive an already cancelled UI state.
- Replaced the previously documented Pause target design with the implemented Redis `pause_requested` control flow, pre-interrupt `paused` checkpoint ordering, typed interrupt isolation, safe-boundary semantics, same-Trace authorization and explicit Stop/Cancel separation.
- Context reduction now runs artifact-backed microcompact before paid full summarization, uses a provider-window-aware effective threshold, and separates current active-context estimates from cumulative task/session token spend.
- Renamed the public GitHub repository from `my_mini_claude_code` to `enterprise-controlled-coding-agent` to match the README product identity, and updated current clone/directory examples.

### Fixed

- Multi-Agent tasks that validate Python files with `python -m py_compile` now record that successful command as verification evidence instead of exhausting the validation budget and ending as `failed`. The final LangGraph task state is now the single terminal source for Trace, SSE and MySQL projections; only real `succeeded` tasks emit `[DONE]`, terminal rows/traces resist late-event regression, and missing or non-terminal checkpoints fail closed.
- Streaming chat now treats any upward wheel/scroll movement as reading intent, so small macOS trackpad gestures are not immediately pulled back to the bottom by the next SSE token; `Latest` explicitly resumes following.
- Stop/Cancel now closes the cancelled Redis checkpoint turn with an idempotent Assistant tombstone (and cancelled ToolMessages when required), synchronizes the exact Trace's MySQL assistant row, resists late SSE status regression, and keeps administrator cancellation bound to the selected Trace. A stopped HumanMessage can remain available for conversational reference without being merged with the next request as `Human -> Human`.
- Immediate conversation references such as “刚才/上一条/previous message” now bypass cross-session Chroma recall. Chinese memory ranking enforces the semantic-distance hard gate, removes generic bigram evidence, requires meaningful lexical overlap, and injects accepted memory as system reference context instead of a synthetic HumanMessage.
- Full and manual context compression now use LangGraph's all-message removal sentinel before adding continuation messages, so `add_messages` no longer silently appends summaries while retaining the entire checkpoint history.
- Context transcripts are normalized, atomically written with unique safe names, exposed through workspace-relative handles, and resolved per current user instead of pinning a process-wide manager to the first tenant.
- Long structured Shell results are normalized before any preview limit, preserving non-zero exit status; microcompact never replaces an old tool body unless a real artifact receipt can be verified.
- Removed the unreferenced legacy Vue file manager, standalone 739-line prototype, empty utility package, obsolete Claude workflow rules, and duplicate pytest configuration source.
- Removed completed implementation plans, obsolete audits, misleading issue reports, raw LangSmith dumps, duplicate benchmark runs, and stale beginner guides from the active documentation tree.
- POSIX foreground/background commands now use an explicit Bash executable instead of the host's implicit `/bin/sh`; the Agent no longer receives the tenant's absolute workspace path, and policy failures provide actionable relative-path, captured-output, or `delete_paths` remediation without weakening existing blocks.
- Chat input now distinguishes IME candidate confirmation from an intentional send: composition state, Safari's post-`compositionend` Enter ordering, and legacy key-code 229 are guarded while ordinary Enter-to-send and Shift+Enter remain intact.
- Conversation refresh no longer hides every MySQL session whose 24-hour Redis checkpoint expired. User-visible messages now persist in MySQL `chat_messages`; startup migrates readable legacy checkpoints, list/history APIs expose explicit durability/gap status, and streaming, cancellation, failure, timeout, and HITL resume paths update one idempotent assistant record.
- Direct Python/Node workspace-script execution is no longer classified as safe Shell work; deletion must use the dedicated confirmed tool instead of attempting `rm`, inline code, or generated cleanup-script bypasses.
- Fresh Docker installations no longer run an administrator migration before the referenced core identity tables exist; existing MySQL volumes are adopted without recreating users/sessions.
- Password reset, account disabling, and explicit administrator revocation now invalidate all older access/refresh token generations instead of waiting for JWT expiry.
- Admin overview token totals now aggregate the complete retained Trace window instead of only five recent tasks per user.
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

- Real `deepseek-v4-flash` v2 single-Agent benchmark (clean commit `7562a95`): 25/30 tasks, with easy 9/10, medium 10/10, hard 6/10, 87.5% tool success, 11.524 s average duration, 32,342.23 average tokens, 73.3% human intervention, 15 safety interceptions, and zero infrastructure/system errors. V2 replaces the active v1 baseline but is not a strictly like-for-like score comparison because the suite and evaluator both changed.
- Current code verification (2026-08-10): backend 561 passed; Ruff passed; frontend 77/77 passed; production build, 9/9 local smoke and Compose configuration passed. Multi regression coverage proves real delegation, mutation, `py_compile` validation and terminal Trace/MySQL consistency without calling a third-party model. The prior Preview/Edit Docker and browser smoke remains valid for its recorded code state.
- Current code verification (2026-08-08): backend 442 passed; Ruff passed; frontend 23/23 passed; production build, Compose configuration and 7-check local smoke passed; the offline Platform benchmark remained 10/10. No third-party model was called for this verification.
- Current milestone (2026-07-23): backend 381 passed; Ruff passed; frontend 23/23 passed; production build passed with a 76.99 kB largest chunk; Compose configuration and local smoke passed.
- Historical v1 milestone — real `deepseek-chat` single-Agent benchmark: 8/10 tasks, 82.9% tool success, 5.285 s average duration, 19,339.9 average tokens, 50.0% human intervention, 6 safety interceptions, and 0 infrastructure errors. It is retained for chronology, not as the active baseline.
- Rebuilt API/frontend images passed against the existing MySQL volume; Alembic reached `20260722_0002`, 13 readable Redis messages migrated to MySQL, all 12 non-deleted user-1 sessions were returned after refresh (1 durable, 11 honestly expired), and all four Compose services were healthy.
- Live API authorization smoke: users.id=1 administrator overview returned 200 with `metadata_only`; an active non-admin user returned 403. Browser regular-user smoke showed no administrator entry and zero warning/error logs.
- Offline platform benchmark: 10/10 tasks passed; 80.0% tool-call success, 84.8 ms average task duration, 20.0% intervention rate, and 1 safety interception. This deterministic run uses no LLM and is not an Agent intelligence score.
- Frontend: production build passed with a 76.99 kB largest JS chunk; npm audit reported zero known vulnerabilities.
- Docker Compose config and API/frontend image builds passed. API self-check ran as UID 10001 with `torch 2.13.0+cpu`, CUDA false, and successful app import.
- An isolated four-service Compose startup passed with API, Vue/Nginx, MySQL, and Redis healthy; API direct health and the frontend `/api/health` reverse proxy both returned MySQL/Redis `ok`. `scripts/docker_smoke_test.sh` makes this check reproducible without using the default host ports.
- Browser replay passed against the rebuilt local stack using only synthetic local data: registration, seven metrics, candidate filtering, memory injection/token evidence, non-attribution warning, and zero console warnings/errors were verified.
- Local memory-recall benchmark initially exposed 27.78% Precision@3 and 100% negative false injection; after Chinese reranking, the retained run passed 6/6 with 100% Recall@3/Precision@3, MRR 1.0, zero negative false injection, and zero forbidden injection. This is retrieval evidence, not model-application evidence.

### Not yet measured

- The 6-case delegation-suitable single-Agent versus multi-Agent comparison remains unexecuted. No multi-Agent benefit is claimed.
