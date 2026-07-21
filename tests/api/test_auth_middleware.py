"""Authentication middleware authorization-source regressions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from enterprise_agent.api.middleware import auth


class _SessionContext:
    def __init__(self, user):
        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        self.session = MagicMock()
        self.session.execute = AsyncMock(return_value=result)

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return False


async def test_permissions_follow_live_superuser_role_not_stale_jwt(monkeypatch):
    payload = SimpleNamespace(sub=7, permissions=["tools:basic"])
    user = SimpleNamespace(id=7, is_active=True, is_superuser=True)
    monkeypatch.setattr(auth.jwt_handler, "verify_token", lambda _token: payload)
    monkeypatch.setattr(auth, "async_session_factory", lambda: _SessionContext(user))

    permissions = await auth.get_current_user_permissions(
        SimpleNamespace(credentials="valid-token")
    )

    assert "tools:advanced" in permissions
    assert "tools:shell" in permissions


async def test_permissions_remove_stale_admin_claim_after_demotion(monkeypatch):
    payload = SimpleNamespace(sub=8, permissions=["tools:advanced", "admin:users"])
    user = SimpleNamespace(id=8, is_active=True, is_superuser=False)
    monkeypatch.setattr(auth.jwt_handler, "verify_token", lambda _token: payload)
    monkeypatch.setattr(auth, "async_session_factory", lambda: _SessionContext(user))

    permissions = await auth.get_current_user_permissions(
        SimpleNamespace(credentials="valid-token")
    )

    assert "tools:basic" in permissions
    assert "tools:advanced" not in permissions
    assert "admin:users" not in permissions
