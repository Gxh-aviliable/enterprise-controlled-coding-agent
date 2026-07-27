from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from enterprise_agent.auth.jwt_handler import jwt_handler
from enterprise_agent.auth.permissions import get_role_permissions
from enterprise_agent.db.mysql import async_session_factory
from enterprise_agent.models.user import User

security = HTTPBearer()


async def get_current_user_record(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> User:
    """Return the active database user authenticated by the access token.

    Authentication comes from the JWT, while authorization-sensitive fields
    always come from the live database row.

    Args:
        credentials: HTTP Bearer credentials

    Returns:
        Active user record

    Raises:
        HTTPException: If token is invalid or user is disabled
    """
    payload = jwt_handler.verify_token(credentials.credentials)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Verify user still exists and is active
    async with async_session_factory() as db:
        result = await db.execute(select(User).where(User.id == payload.sub))
        user = result.scalar_one_or_none()
        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found or disabled",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if int(getattr(payload, "ver", 0)) != int(getattr(user, "auth_version", 0) or 0):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication session has been revoked",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return user


async def get_current_user(user: User = Depends(get_current_user_record)) -> int:
    """Return the authenticated user's ID for existing route compatibility."""
    return user.id


async def get_current_user_permissions(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> list:
    """Get current permissions from the active database role.

    The JWT authenticates the caller, but authorization is derived from the
    current user row. This makes promotion, demotion, and account disabling
    effective immediately instead of trusting stale permission claims until
    the token expires.
    """
    user = await get_current_user_record(credentials)
    role = "admin" if user.is_superuser else "free"
    return [permission.value for permission in get_role_permissions(role)]


def require_admin(required_permission: str):
    """Require an active superuser with a specific live admin permission."""

    async def check_admin(user: User = Depends(get_current_user_record)) -> User:
        role = "admin" if user.is_superuser else "free"
        permissions = [permission.value for permission in get_role_permissions(role)]
        if not user.is_superuser or required_permission not in permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{required_permission}' required",
            )
        return user

    return check_admin


def require_permission(required_permission: str):
    """Dependency factory for permission checking.

    Args:
        required_permission: Required permission string

    Returns:
        Dependency function
    """
    async def check_permission(
        permissions: list = Depends(get_current_user_permissions)
    ):
        if required_permission not in permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{required_permission}' required"
            )
        return True
    return check_permission
