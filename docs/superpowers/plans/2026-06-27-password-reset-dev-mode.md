# Password Reset Dev Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add forgot-password and reset-password support using Redis verification codes and development-mode logging.

**Architecture:** Auth routes orchestrate the reset flow. Redis stores short-lived codes; MySQL updates the password hash. SMTP is optional and falls back to logging.

**Tech Stack:** FastAPI, SQLAlchemy async sessions, Redis asyncio client, Pydantic, Vue 3, Vite.

## Global Constraints

- Do not reveal whether an email exists when requesting a reset code.
- Default reset code TTL is 600 seconds.
- Default reset code length is 6 digits.
- Development mode logs the code when SMTP is not configured.
- Frontend remains inside the existing login card.

---

### Task 1: Backend Reset API

**Files:**
- Modify: `enterprise_agent/config/settings.py`
- Modify: `enterprise_agent/api/schemas/auth.py`
- Modify: `enterprise_agent/api/routes/auth.py`
- Create: `enterprise_agent/auth/email.py`
- Test: `tests/api/test_auth_password_reset.py`

**Interfaces:**
- Produces: `POST /auth/forgot-password`
- Produces: `POST /auth/reset-password`
- Produces: `send_password_reset_code(email: str, code: str) -> Awaitable[bool]`

- [ ] Write failing route tests for request, invalid code, and successful reset.
- [ ] Implement Pydantic schemas.
- [ ] Add reset settings and email helper.
- [ ] Add auth routes using Redis TTL and constant-time code comparison.
- [ ] Run `uv run pytest tests/api/test_auth_password_reset.py -q`.

### Task 2: Frontend Reset Flow

**Files:**
- Modify: `frontend/src/api/client.js`
- Modify: `frontend/src/stores/auth.js`
- Modify: `frontend/src/components/LoginForm.vue`

**Interfaces:**
- Consumes: `api.forgotPassword(email)`
- Consumes: `api.resetPassword(email, code, new_password)`

- [ ] Add API client methods.
- [ ] Add auth store methods for reset loading/error handling.
- [ ] Add `forgot` and `reset` modes in `LoginForm.vue`.
- [ ] Keep layout compact and keyboard-friendly.
- [ ] Run `npm run build`.

### Task 3: Verification

**Files:**
- Verify all modified backend and frontend files.

- [ ] Run `uv run pytest -q`.
- [ ] Run targeted ruff on changed backend/test files.
- [ ] Run `npm run build`.
- [ ] Confirm `/health` stays healthy and frontend loads.

