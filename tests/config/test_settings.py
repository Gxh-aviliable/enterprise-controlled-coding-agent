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
