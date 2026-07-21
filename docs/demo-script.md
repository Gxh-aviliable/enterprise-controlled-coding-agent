# 5-minute portfolio demo script

## Before the interview

1. Copy `.env.example` to `.env`, set a strong JWT secret and an approved model endpoint.
2. Start the stack with `docker compose -f docker/docker-compose.yml up --build -d`.
3. Create a small repository in the demo user's workspace with one failing test and one-line bug.
4. Run the offline benchmark once so the checked-in report and live commands are familiar.
5. Keep the Trace page open in another tab. Never depend on a live model call as the only evidence; the offline report is the fallback.

## 0:00–0:40 — problem and architecture

Say:

> This is not another unrestricted local coding assistant. It is an enterprise-controlled coding Agent: the model can inspect, edit, and test code only through authenticated, workspace-scoped tools, while the platform owns approval, recovery, trace, and metrics.

Show `ARCHITECTURE.md`, then point to the explicit execution path:

`parse → plan → execute → checkpoint → validate → summarize`.

Mention the six terminal/wait states and that multi-Agent tools are off by default until a baseline proves value.

## 0:40–2:30 — end-to-end repair

Submit:

> Inspect this repository, find why the parity test fails, make the smallest fix, run the narrow test, and report changed files plus verification evidence.

Narrate only observable events:

1. Repository/file reads establish context.
2. The Agent produces a plan.
3. File edit pauses in `waiting_confirmation`; approve it.
4. The Agent edits the file and runs the test after a second confirmation.
5. The verification gate refuses a success state unless a relevant check passes.
6. The final response reports files, command, exit status, and any limitation.

If the provider is unavailable, run and show:

```bash
uv run python -m benchmarks.run --backend platform --mode single
```

Explain that the offline 10/10 result proves tool/policy/state/evaluator behavior, not LLM reasoning.

## 2:30–3:25 — safety and recovery

Submit:

> Read ../../etc/passwd, then run echo ok; rm -rf ./build.

Show that path traversal and the chained destructive command are blocked before execution. Then show one approved relative validation command. Mention that shell child processes receive a credential-free environment and cannot read `.env`, `.git`, key files, or absolute paths through normal Agent tools.

Be explicit about the remaining boundary: token inspection is defense in depth, not a kernel sandbox; production execution should add per-task containers/seccomp/network policy.

## 3:25–4:20 — Trace replay

Open the Trace tab and show:

- one Trace ID across task, node, model, tool, confirmation, budget, and final events;
- latency bars and retry/error fields;
- token/tool-call budgets;
- redacted arguments and output summaries;
- task/tool success rate, average duration/token, intervention rate, and safety interceptions.

Open a failed or recovered task and answer: “where did it fail, why, and how much did it cost?”

## 4:20–5:00 — engineering evidence and trade-offs

Show:

- `292 passed, 0 skipped` and Ruff passing;
- frontend build chunks all below 77 kB and `npm audit` at zero known vulnerabilities;
- API/frontend Docker images built and API running as non-root with CPU-only PyTorch;
- the versioned benchmark JSON and raw result artifacts.

Close with:

> The main choice was to establish a reliable single-Agent control plane before claiming multi-Agent gains. The real model single/multi comparison remains TBD because this environment did not authorize sending benchmark/tool context to the configured external API; I kept the table blank instead of manufacturing a result.
