"""Tests for forgot-password and reset-password auth flow."""

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


class FakeRedis:
    def __init__(self, stored_code=None):
        self.stored_code = stored_code
        self.set_calls = []
        self.deleted = []

    async def setex(self, key, ttl, value):
        self.set_calls.append((key, ttl, value))
        self.stored_code = value

    async def get(self, key):
        return self.stored_code

    async def delete(self, key):
        self.deleted.append(key)


@pytest.mark.asyncio
async def test_forgot_password_stores_code_and_sends_when_user_exists(monkeypatch):
    from enterprise_agent.api.routes import auth as auth_routes
    from enterprise_agent.api.schemas.auth import ForgotPasswordRequest

    user = User(id=1, email="User@Example.com", is_active=True)
    fake_db = FakeDB(user)
    fake_redis = FakeRedis()
    sent = {}

    monkeypatch.setattr(auth_routes, "redis_client", fake_redis)
    monkeypatch.setattr(auth_routes, "_generate_reset_code", lambda: "123456")

    async def fake_send(email, code):
        sent["email"] = email
        sent["code"] = code
        return False

    monkeypatch.setattr(auth_routes, "send_password_reset_code", fake_send)

    response = await auth_routes.forgot_password(
        ForgotPasswordRequest(email="User@Example.com"),
        db=fake_db,
    )

    assert "If the email exists" in response.message
    assert fake_redis.set_calls == [("password_reset:user@example.com", 600, "123456")]
    assert sent == {"email": "User@Example.com", "code": "123456"}


@pytest.mark.asyncio
async def test_forgot_password_returns_generic_message_for_unknown_email(monkeypatch):
    from enterprise_agent.api.routes import auth as auth_routes
    from enterprise_agent.api.schemas.auth import ForgotPasswordRequest

    fake_redis = FakeRedis()
    monkeypatch.setattr(auth_routes, "redis_client", fake_redis)

    response = await auth_routes.forgot_password(
        ForgotPasswordRequest(email="missing@example.com"),
        db=FakeDB(None),
    )

    assert "If the email exists" in response.message
    assert fake_redis.set_calls == []


@pytest.mark.asyncio
async def test_reset_password_rejects_invalid_code(monkeypatch):
    from enterprise_agent.api.routes import auth as auth_routes
    from enterprise_agent.api.schemas.auth import ResetPasswordRequest

    monkeypatch.setattr(auth_routes, "redis_client", FakeRedis(stored_code="123456"))

    with pytest.raises(HTTPException) as exc:
        await auth_routes.reset_password(
            ResetPasswordRequest(
                email="user@example.com",
                code="000000",
                new_password="new-password-123",
            ),
            db=FakeDB(),
        )

    assert exc.value.status_code == 400
    assert "Invalid or expired" in exc.value.detail


@pytest.mark.asyncio
async def test_reset_password_updates_hash_and_deletes_code(monkeypatch):
    from enterprise_agent.api.routes import auth as auth_routes
    from enterprise_agent.api.schemas.auth import ResetPasswordRequest

    user = User(
        id=1,
        email="user@example.com",
        is_active=True,
        password_hash=jwt_handler.hash_password("old-password-123"),
    )
    fake_db = FakeDB(user)
    fake_redis = FakeRedis(stored_code="123456")
    monkeypatch.setattr(auth_routes, "redis_client", fake_redis)

    response = await auth_routes.reset_password(
        ResetPasswordRequest(
            email="user@example.com",
            code="123456",
            new_password="new-password-123",
        ),
        db=fake_db,
    )

    assert response.message == "Password has been reset successfully."
    assert fake_db.committed is True
    assert jwt_handler.verify_password("new-password-123", user.password_hash)
    assert fake_redis.deleted == ["password_reset:user@example.com"]
