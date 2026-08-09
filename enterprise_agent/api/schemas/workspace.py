"""Validated contracts for authenticated workspace file editing."""

from pydantic import BaseModel, Field, field_validator


class WorkspaceFileWriteRequest(BaseModel):
    """Replace an existing UTF-8 text file using optimistic concurrency."""

    path: str = Field(..., min_length=1, max_length=4096)
    # Bound the parsed request as well as the encoded bytes checked by the
    # service. This prevents obviously oversized browser drafts from reaching
    # the filesystem layer.
    content: str = Field(..., max_length=1_048_576)
    expected_sha256: str = Field(..., min_length=64, max_length=64)

    @field_validator("expected_sha256")
    @classmethod
    def validate_expected_sha256(cls, value: str) -> str:
        normalized = value.strip().lower()
        if any(character not in "0123456789abcdef" for character in normalized):
            raise ValueError("expected_sha256 must be a 64-character hexadecimal digest")
        return normalized
