"""Runtime security configuration tests."""

import pytest

from enterprise_agent.config.settings import Settings


@pytest.mark.parametrize(
    "secret",
    ["", "change-me-in-production", "your-secret-key-change-in-production", "short"],
)
def test_runtime_rejects_placeholder_or_short_jwt_secrets(secret):
    candidate = Settings(JWT_SECRET_KEY=secret, DEBUG=False)
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        candidate.validate_runtime_security()


def test_runtime_accepts_strong_secret():
    candidate = Settings(JWT_SECRET_KEY="a" * 32, DEBUG=False)
    candidate.validate_runtime_security()


def test_debug_mode_can_boot_for_local_diagnostics_without_secret():
    candidate = Settings(JWT_SECRET_KEY="", DEBUG=True)
    candidate.validate_runtime_security()


def test_runtime_rejects_missing_model_context_boundary():
    candidate = Settings(
        JWT_SECRET_KEY="a" * 32,
        DEBUG=False,
        MODEL_CONTEXT_WINDOW_TOKENS=0,
    )
    with pytest.raises(RuntimeError, match="MODEL_CONTEXT_WINDOW_TOKENS"):
        candidate.validate_runtime_security()


def test_default_context_and_cumulative_budget_policy():
    fields = Settings.model_fields

    assert fields["MODEL_CONTEXT_WINDOW_TOKENS"].default == 1_000_000
    assert fields["CONTEXT_COMPRESSION_RATIO"].default == 0.8
    assert fields["TASK_TOKEN_BUDGET"].default == 4_000_000
    assert fields["SESSION_TOKEN_BUDGET"].default == 0


def test_runtime_accepts_zero_as_disabled_token_budget():
    candidate = Settings(
        JWT_SECRET_KEY="a" * 32,
        DEBUG=False,
        TASK_TOKEN_BUDGET=0,
        SESSION_TOKEN_BUDGET=0,
    )

    candidate.validate_runtime_security()


@pytest.mark.parametrize("field", ["TASK_TOKEN_BUDGET", "SESSION_TOKEN_BUDGET"])
def test_runtime_rejects_negative_token_budget(field):
    candidate = Settings(
        JWT_SECRET_KEY="a" * 32,
        DEBUG=False,
        **{field: -1},
    )

    with pytest.raises(RuntimeError, match="must be non-negative"):
        candidate.validate_runtime_security()
