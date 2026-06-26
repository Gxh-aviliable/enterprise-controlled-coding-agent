# Open Files in VSCode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an "Open in VSCode" action for workspace files with local and server Web VSCode modes.

**Architecture:** Backend resolves workspace paths and produces safe open URLs. Frontend calls a single API method and opens the returned URL, avoiding client-side path assumptions.

**Tech Stack:** FastAPI, Pydantic settings, Vue 3, Vite.

## Global Constraints

- Only files inside the authenticated user's workspace can be opened.
- Directories are rejected by the open-url endpoint.
- Local mode emits `vscode://file/...`.
- Web mode supports both URL template and base URL query-parameter construction.

---

### Task 1: Backend Open URL Endpoint

**Files:**
- Modify: `enterprise_agent/config/settings.py`
- Modify: `enterprise_agent/api/routes/workspace.py`
- Test: `tests/api/test_workspace_open_url.py`

**Interfaces:**
- Produces: `GET /workspace/open-url?path=<path>`
- Produces response: `{"mode": str, "url": str}`

- [ ] Write failing tests for local URL, web template URL, default web URL, and directory rejection.
- [ ] Add settings.
- [ ] Implement URL builder and route.
- [ ] Run `uv run pytest tests/api/test_workspace_open_url.py -q`.

### Task 2: Frontend Open Action

**Files:**
- Modify: `frontend/src/api/client.js`
- Modify: `frontend/src/components/FileViewer.vue`
- Modify: `frontend/src/components/TreeNode.vue`
- Modify: `frontend/src/components/FileTree.vue`

**Interfaces:**
- Consumes: `api.fetchOpenUrl(path)`
- Emits: `open` event from file tree nodes.

- [ ] Add API client method.
- [ ] Add FileViewer button.
- [ ] Add TreeNode context menu item for files.
- [ ] Wire FileTree to call API and open URL.
- [ ] Run `npm run build`.

### Task 3: Verification

- [ ] Run backend targeted tests.
- [ ] Run full backend tests.
- [ ] Run changed-file ruff.
- [ ] Run frontend build.
- [ ] Confirm `/health` and frontend home still respond locally.

