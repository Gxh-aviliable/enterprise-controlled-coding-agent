from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from enterprise_agent.auth.jwt_handler import jwt_handler
from enterprise_agent.auth.permissions import get_role_permissions
from enterprise_agent.db.mysql import async_session_factory
from enterprise_agent.models.user import User

security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> int:
    """Get current user ID from JWT token.

    Verifies JWT and checks user is_active in database.

    Args:
        credentials: HTTP Bearer credentials

    Returns:
        User ID

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

    return payload.sub


async def get_current_user_permissions(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> list:
    """Get current permissions from the active database role.

    The JWT authenticates the caller, but authorization is derived from the
    current user row. This makes promotion, demotion, and account disabling
    effective immediately instead of trusting stale permission claims until
    the token expires.
    """
    payload = jwt_handler.verify_token(credentials.credentials)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    async with async_session_factory() as db:
        result = await db.execute(select(User).where(User.id == payload.sub))
        user = result.scalar_one_or_none()
        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found or disabled",
                headers={"WWW-Authenticate": "Bearer"},
            )

    role = "admin" if user.is_superuser else "free"
    return [permission.value for permission in get_role_permissions(role)]


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
