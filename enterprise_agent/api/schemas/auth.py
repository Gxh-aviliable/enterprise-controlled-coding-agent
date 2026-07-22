from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class UserRegister(BaseModel):
    """User registration request"""
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=100)
    full_name: Optional[str] = Field(None, max_length=100)


class UserLogin(BaseModel):
    """User login request"""
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    """Token response for login/register"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class TokenRefresh(BaseModel):
    """Token refresh request"""
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    """Forgot password verification-code request."""
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Password reset request with emailed verification code."""
    email: EmailStr
    code: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=8, max_length=100)


class MessageResponse(BaseModel):
    """Generic status message response."""
    message: str


class UserMeResponse(BaseModel):
    """Authenticated user's live profile and database-derived permissions."""

    id: int
    username: str
    email: EmailStr
    full_name: Optional[str] = None
    role: str
    is_active: bool
    is_superuser: bool
    permissions: list[str]
    created_at: datetime
    last_login_at: Optional[datetime] = None
