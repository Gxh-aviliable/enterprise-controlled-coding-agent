"""Provider-neutral helpers for replayable and user-visible message content."""

from __future__ import annotations

from typing import Any


def extract_visible_text(content: Any) -> str:
    """Return only provider-declared text, never reasoning/protocol reprs."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if content.get("type") == "text":
            return str(content.get("text") or "")
        return ""
    if isinstance(content, list):
        parts = [extract_visible_text(block) for block in content]
        return "\n".join(part for part in parts if part)
    if getattr(content, "type", None) == "text":
        return str(getattr(content, "text", "") or "")
    return ""


def normalize_signature_only_thinking_blocks(content: Any) -> Any:
    """Supply a missing empty thinking field required by replay APIs.

    Some Anthropic-compatible streams can emit a signature without a preceding
    thinking delta. Preserve every provider field and copy only when repair is
    required, so callers can safely replay the assistant message with tool calls.
    """
    if not isinstance(content, list):
        return content

    normalized = None
    for index, block in enumerate(content):
        if (
            isinstance(block, dict)
            and block.get("type") == "thinking"
            and "thinking" not in block
            and isinstance(block.get("signature"), str)
        ):
            if normalized is None:
                normalized = list(content)
            normalized[index] = {**block, "thinking": ""}

    return normalized if normalized is not None else content
