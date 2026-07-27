import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.api.middleware.auth import get_current_user_record
from enterprise_agent.api.schemas.auth import (
    ForgotPasswordRequest,
    MessageResponse,
    ResetPasswordRequest,
    TokenRefresh,
    TokenResponse,
    UserLogin,
    UserMeResponse,
    UserRegister,
)
from enterprise_agent.auth.email import send_password_reset_code
from enterprise_agent.auth.jwt_handler import jwt_handler
from enterprise_agent.auth.permissions import get_role_permissions
from enterprise_agent.config.settings import settings
from enterprise_agent.db.mysql import get_db
from enterprise_agent.db.redis import redis_client
from enterprise_agent.models.user import User

router = APIRouter(prefix="/auth", tags=["auth"])

PASSWORD_RESET_GENERIC_MESSAGE = (
    "If the email exists, a verification code has been sent."
)


@router.get("/me", response_model=UserMeResponse)
async def get_me(user: User = Depends(get_current_user_record)):
    """Return the current user's live profile, role, and permissions."""
    role = "admin" if user.is_superuser else "free"
    permissions = [permission.value for permission in get_role_permissions(role)]
    return UserMeResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        role=role,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        permissions=permissions,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


def _normalize_email(email: str) -> str:
    """Normalize email for lookup and Redis key construction."""
    return email.strip().lower()


def _password_reset_key(email: str) -> str:
    """Build Redis key for password reset code."""
    return f"password_reset:{_normalize_email(email)}"


def _generate_reset_code() -> str:
    """Generate a zero-padded numeric reset code."""
    digits = max(1, settings.PASSWORD_RESET_CODE_LENGTH)
    upper_bound = 10 ** digits
    return f"{secrets.randbelow(upper_bound):0{digits}d}"


@router.post("/register", response_model=TokenResponse)
async def register(
    user_data: UserRegister,
    db: AsyncSession = Depends(get_db)
):
    """Register new user

    Args:
        user_data: Registration data
        db: Database session

    Returns:
        JWT tokens

    Raises:
        HTTPException: If username or email already exists
    """
    # Check if username exists
    result = await db.execute(select(User).where(User.username == user_data.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username already registered")

    # Check if email exists
    result = await db.execute(select(User).where(User.email == user_data.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    # Create user
    user = User(
        username=user_data.username,
        email=user_data.email,
        password_hash=jwt_handler.hash_password(user_data.password),
        full_name=user_data.full_name
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Return tokens with free role permissions
    permissions = [p.value for p in get_role_permissions("free")]
    return jwt_handler.create_tokens(user.id, permissions, user.auth_version)


@router.post("/login", response_model=TokenResponse)
async def login(
    login_data: UserLogin,
    db: AsyncSession = Depends(get_db)
):
    """Login user

    Args:
        login_data: Login credentials
        db: Database session

    Returns:
        JWT tokens

    Raises:
        HTTPException: If credentials are invalid
    """
    result = await db.execute(select(User).where(User.email == _normalize_email(str(login_data.email))))
    user = result.scalar_one_or_none()

    if not user or not jwt_handler.verify_password(login_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="User account is disabled")

    # Update last login time
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()

    # Get role permissions (admin if superuser, else free — matches registration)
    role = "admin" if user.is_superuser else "free"
    permissions = [p.value for p in get_role_permissions(role)]

    return jwt_handler.create_tokens(user.id, permissions, user.auth_version)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    refresh_data: TokenRefresh,
    db: AsyncSession = Depends(get_db)
):
    """Refresh access token

    Args:
        refresh_data: Refresh token
        db: Database session

    Returns:
        New JWT tokens

    Raises:
        HTTPException: If refresh token is invalid
    """
    # Extract jti from old token (unverified decode for blacklist check)
    old_claims = jwt_handler.decode_token_unverified(refresh_data.refresh_token)
    old_jti = old_claims.get("jti") if old_claims else None

    # Check if this refresh token has already been used (blacklisted)
    if old_jti:
        is_blacklisted = await redis_client.exists(f"token:blacklist:{old_jti}")
        if is_blacklisted:
            raise HTTPException(status_code=401, detail="Refresh token has been revoked")

    payload = jwt_handler.verify_token(refresh_data.refresh_token, "refresh")
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    result = await db.execute(select(User).where(User.id == payload.sub))
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or disabled")
    if int(payload.ver) != int(user.auth_version or 0):
        raise HTTPException(status_code=401, detail="Refresh token has been revoked")

    # Blacklist old refresh token (TTL = refresh token expiry)
    if old_jti:
        ttl_seconds = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600
        await redis_client.setex(f"token:blacklist:{old_jti}", ttl_seconds, "1")

    role = "admin" if user.is_superuser else "free"
    permissions = [p.value for p in get_role_permissions(role)]

    return jwt_handler.create_tokens(user.id, permissions, user.auth_version)


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(
    request: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Start password reset by sending/logging a verification code."""
    normalized_email = _normalize_email(str(request.email))

    result = await db.execute(
        select(User).where(User.email == normalized_email, User.is_active == True)  # noqa: E712
    )
    user = result.scalar_one_or_none()

    if user:
        code = _generate_reset_code()
        await redis_client.setex(
            _password_reset_key(normalized_email),
            settings.PASSWORD_RESET_CODE_TTL_SECONDS,
            code,
        )
        await send_password_reset_code(user.email, code)

    return MessageResponse(message=PASSWORD_RESET_GENERIC_MESSAGE)


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(
    request: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Reset password after verifying an emailed code."""
    normalized_email = _normalize_email(str(request.email))
    key = _password_reset_key(normalized_email)
    expected_code = await redis_client.get(key)

    if not expected_code or not secrets.compare_digest(str(expected_code), request.code):
        raise HTTPException(status_code=400, detail="Invalid or expired verification code")

    result = await db.execute(
        select(User).where(User.email == normalized_email, User.is_active == True)  # noqa: E712
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired verification code")

    user.password_hash = jwt_handler.hash_password(request.new_password)
    user.auth_version = int(user.auth_version or 0) + 1
    await db.commit()
    await redis_client.delete(key)

    return MessageResponse(message="Password has been reset successfully.")
