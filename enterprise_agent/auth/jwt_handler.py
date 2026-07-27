import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

from enterprise_agent.config.settings import settings


class TokenPayload(BaseModel):
    """JWT token payload"""
    sub: int  # user_id
    exp: datetime
    iat: datetime
    type: str  # 'access' or 'refresh'
    permissions: Optional[list] = None
    jti: Optional[str] = None  # JWT ID for token rotation
    ver: int = 0  # Server-side revocation generation


class JWTHandler:
    """JWT token handler for authentication"""

    def __init__(self):
        self.secret_key = settings.JWT_SECRET_KEY
        self.algorithm = settings.JWT_ALGORITHM
        self.pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

    def create_tokens(
        self,
        user_id: int,
        permissions: list = None,
        auth_version: int = 0,
    ) -> Dict[str, Any]:
        """Create access token and refresh token

        Args:
            user_id: User ID
            permissions: List of permission strings

        Returns:
            Dict with access_token, refresh_token, token_type, expires_in
        """
        now = datetime.now(timezone.utc)

        access_payload = {
            "sub": user_id,
            "iat": now,
            "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
            "type": "access",
            "permissions": permissions or [],
            "ver": auth_version,
        }

        refresh_payload = {
            "sub": user_id,
            "iat": now,
            "exp": now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
            "type": "refresh",
            "jti": str(uuid.uuid4()),
            "ver": auth_version,
        }

        return {
            "access_token": jwt.encode(access_payload, self.secret_key, algorithm=self.algorithm),
            "refresh_token": jwt.encode(refresh_payload, self.secret_key, algorithm=self.algorithm),
            "token_type": "bearer",
            "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
        }

    def verify_token(self, token: str, token_type: str = "access") -> Optional[TokenPayload]:
        """Verify and decode token

        Args:
            token: JWT token string
            token_type: Expected token type ('access' or 'refresh')

        Returns:
            TokenPayload if valid, None otherwise
        """
        try:
            payload = jwt.decode(
                token, self.secret_key, algorithms=[self.algorithm],
                options={"verify_sub": False}
            )
            if payload.get("type") != token_type:
                return None
            payload["exp"] = datetime.fromtimestamp(payload["exp"], timezone.utc)
            payload["iat"] = datetime.fromtimestamp(payload["iat"], timezone.utc)
            return TokenPayload(**payload)
        except JWTError:
            return None

    def decode_token_unverified(self, token: str) -> Optional[Dict[str, Any]]:
        """Decode token without verification (for extracting jti)."""
        try:
            return jwt.get_unverified_claims(token)
        except JWTError:
            return None

    def hash_password(self, password: str) -> str:
        """Hash password using bcrypt"""
        return self.pwd_context.hash(password)

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify password against hash"""
        return self.pwd_context.verify(plain_password, hashed_password)


# Global instance
jwt_handler = JWTHandler()
