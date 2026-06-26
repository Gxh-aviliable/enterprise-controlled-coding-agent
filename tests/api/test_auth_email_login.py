"""Tests for email-based login."""

import pytest
from fastapi import HTTPException

from enterprise_agent.auth.jwt_handler import jwt_handler
from enterprise_agent.models.user import User


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeDB:
    def __init__(self, *results):
        self.results = list(results)
        self.committed = False

    async def execute(self, statement):
        if not self.results:
            raise AssertionError(f"Unexpected query: {statement}")
        return FakeResult(self.results.pop(0))

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_login_accepts_email_and_password():
    from enterprise_agent.api.routes.auth import login
    from enterprise_agent.api.schemas.auth import UserLogin

    user = User(
        id=1,
        username="DisplayName",
        email="user@example.com",
        is_active=True,
        is_superuser=False,
        password_hash=jwt_handler.hash_password("password-123"),
    )

    response = await login(
        UserLogin(email="USER@example.com", password="password-123"),
        db=FakeDB(user),
    )

    assert response["token_type"] == "bearer"
    assert response["access_token"]


def test_login_schema_no_longer_accepts_username_only():
    from pydantic import ValidationError

    from enterprise_agent.api.schemas.auth import UserLogin

    with pytest.raises(ValidationError):
        UserLogin(username="DisplayName", password="password-123")


@pytest.mark.asyncio
async def test_login_rejects_wrong_password_with_email():
    from enterprise_agent.api.routes.auth import login
    from enterprise_agent.api.schemas.auth import UserLogin

    user = User(
        id=1,
        username="DisplayName",
        email="user@example.com",
        is_active=True,
        password_hash=jwt_handler.hash_password("password-123"),
    )

    with pytest.raises(HTTPException) as exc:
        await login(
            UserLogin(email="user@example.com", password="wrong-password"),
            db=FakeDB(user),
        )

    assert exc.value.status_code == 401
