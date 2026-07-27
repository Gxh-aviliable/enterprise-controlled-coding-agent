"""Validated request and response contracts for the admin control plane."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class UserStatusUpdate(BaseModel):
    is_active: bool
    reason: str = Field(..., min_length=3, max_length=500)


class AdminReasonRequest(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500)


class UserQuotaUpdate(BaseModel):
    daily_task_limit: Optional[int] = Field(None, ge=1, le=100_000)
    daily_token_limit: Optional[int] = Field(None, ge=1_000, le=1_000_000_000)
    monthly_token_limit: Optional[int] = Field(None, ge=1_000, le=10_000_000_000)
    concurrent_task_limit: Optional[int] = Field(None, ge=1, le=100)
    workspace_bytes_limit: Optional[int] = Field(None, ge=1_048_576, le=10_995_116_277_760)
    enabled: Optional[bool] = None
    reason: str = Field(..., min_length=3, max_length=500)
    expected_version: Optional[int] = Field(None, ge=1)


class AccessGrantCreate(BaseModel):
    target_user_id: int = Field(..., ge=1)
    scope: str = Field(default="workspace:content", pattern="^workspace:content$")
    reason: str = Field(..., min_length=8, max_length=500)
    ttl_minutes: int = Field(default=10, ge=5, le=30)


class SharedSkillDraftRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)
    description: str = Field(default="", max_length=500)
    content: str = Field(..., min_length=20, max_length=100_000)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        import re

        normalized = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", normalized):
            raise ValueError("Skill name must be a lowercase slug")
        return normalized


class SharedSkillPublishRequest(BaseModel):
    changelog: str = Field(..., min_length=3, max_length=500)
    expected_updated_at: Optional[datetime] = None


class SharedSkillRollbackRequest(BaseModel):
    version: int = Field(..., ge=1)
    reason: str = Field(..., min_length=3, max_length=500)


class AuditLogResponse(BaseModel):
    id: int
    actor_user_id: Optional[int]
    action: str
    target_type: str
    target_id: Optional[str]
    reason: Optional[str]
    before: Optional[dict[str, Any]]
    after: Optional[dict[str, Any]]
    outcome: str
    created_at: datetime
