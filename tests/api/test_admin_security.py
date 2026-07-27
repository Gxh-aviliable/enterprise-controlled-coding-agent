"""Admin control-plane permission, grant and identity regressions."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from enterprise_agent.api.middleware.auth import require_admin
from enterprise_agent.api.routes.admin import _grant_is_active
from enterprise_agent.api.routes.auth import get_me
from enterprise_agent.auth.permissions import Permission


async def test_require_admin_rejects_regular_user():
    checker = require_admin(Permission.ADMIN_CONSOLE.value)
    with pytest.raises(HTTPException) as exc:
        await checker(SimpleNamespace(is_superuser=False))
    assert exc.value.status_code == 403


async def test_require_admin_returns_live_superuser():
    checker = require_admin(Permission.ADMIN_WORKSPACE_CONTENT.value)
    user = SimpleNamespace(id=1, is_superuser=True)
    assert await checker(user) is user


async def test_auth_me_uses_database_profile_not_jwt_display_claims():
    now = datetime.now(timezone.utc)
    result = await get_me(
        SimpleNamespace(
            id=7,
            username="admin-user",
            email="admin@example.com",
            full_name="Admin User",
            is_active=True,
            is_superuser=True,
            created_at=now,
            last_login_at=now,
        )
    )
    assert result.username == "admin-user"
    assert result.role == "admin"
    assert Permission.ADMIN_CONSOLE.value in result.permissions


def test_workspace_access_grant_is_actor_target_and_expiry_bound():
    grant = SimpleNamespace(
        actor_user_id=1,
        target_user_id=2,
        scope="workspace:content",
        revoked_at=None,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    assert _grant_is_active(grant, actor_id=1, target_user_id=2)
    assert not _grant_is_active(grant, actor_id=3, target_user_id=2)
    grant.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert not _grant_is_active(grant, actor_id=1, target_user_id=2)
