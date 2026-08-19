"""Context management tools for manual compression and transcript handling.

Provides:
- compress: Manually trigger context compression
- list_transcripts: List saved conversation transcripts
- get_transcript: Load a specific transcript
"""

import json

from langchain_core.tools import tool

from enterprise_agent.core.agent.context import get_context_manager, get_transcript_manager
from enterprise_agent.core.agent.tool_artifacts import (
    ARTIFACT_READ_MAX_BYTES,
    ToolArtifactStore,
)

# === Tool Definitions ===

@tool
def compress() -> str:
    """Manually trigger context compression.

    This will:
    1. Save current conversation to transcript file
    2. Generate a summary via LLM
    3. Replace context with compressed summary

    Use when context is getting too long or you want to reset
    while preserving important information.

    Returns:
        Compression status and transcript path
    """
    # This tool triggers compression in the graph flow
    # The actual compression is handled by manual_compress_node
    return "Compression requested. The context will be compressed after this response."


@tool
def list_transcripts() -> str:
    """List all saved conversation compression backup transcripts.

    CRITICAL: These are COMPRESSION BACKUP files saved during context compression,
    NOT user long-term memory. Do NOT use this tool when the user asks about their
    memory, preferences, history, or "what do you remember about me". These transcripts
    are operational artifacts for debugging compression behavior. For long-term memory
    queries, use `search_memory` instead — it searches the ChromaDB vector database
    where actual user memories (conversations, preferences, decisions) are stored.

    Returns:
        Formatted list of transcript files with timestamps
    """
    tm = get_transcript_manager()
    transcripts = tm.list_transcripts()

    if not transcripts:
        return "No transcripts saved yet."

    lines = []
    for t in transcripts:
        size_kb = t["size"] / 1024
        lines.append(f"- {t['filename']} ({size_kb:.1f} KB, {t['created']})")

    return "Saved transcripts:\n" + "\n".join(lines)


@tool
def get_transcript(
    filename: str,
    offset_bytes: int = 0,
    limit_bytes: int = ARTIFACT_READ_MAX_BYTES,
) -> str:
    """Read one bounded page of a compression backup transcript.

    These are operational compression artifacts, NOT user long-term memory.
    For long-term memory queries, use `search_memory` instead.

    Args:
        filename: Filename or ``.transcripts/transcript_xxx.jsonl`` handle.
        offset_bytes: Zero-based byte offset from the previous page receipt.
        limit_bytes: Target page bytes, capped by the server.

    Returns:
        JSON page metadata and raw JSONL content. Follow ``next_offset_bytes``
        until ``eof`` to recover all currently available backup text.
    """
    tm = get_transcript_manager()
    try:
        return json.dumps(
            tm.read_range(
                filename,
                offset_bytes=offset_bytes,
                limit_bytes=limit_bytes,
            ),
            ensure_ascii=False,
        )
    except ValueError as exc:
        return f"Error: Transcript read rejected ({exc})"
    except FileNotFoundError:
        available = [item["path"] for item in tm.list_transcripts()]
        return f"Error: Transcript not found. Available: {', '.join(available) or 'none'}"
    except OSError:
        return "Error: Transcript read failed"


@tool
def read_tool_artifact(
    path: str,
    sha256: str,
    offset_bytes: int = 0,
    limit_bytes: int = ARTIFACT_READ_MAX_BYTES,
) -> str:
    """Read one bounded range of restricted tool-output evidence.

    Only paths under the authenticated user's ``.agent/tool-artifacts`` root
    are accepted. Use ``next_offset_bytes`` to page through a stored artifact.
    An artifact can be redacted or source-truncated; it is debugging evidence,
    not a promise that unlimited raw bytes were retained.

    Args:
        path: Workspace-relative artifact path from a tool receipt.
        sha256: Expected SHA-256 from the same receipt; mismatches are rejected.
        offset_bytes: Zero-based byte offset.
        limit_bytes: Bytes to return, capped by the server.
    """
    try:
        return ToolArtifactStore().read_range_json(
            path,
            expected_sha256=sha256,
            offset_bytes=offset_bytes,
            limit_bytes=limit_bytes,
        )
    except ValueError as exc:
        return f"Error: Artifact read rejected ({exc})"
    except OSError:
        return "Error: Artifact read failed"


@tool
def context_status() -> str:
    """Get current context status.

    Shows token estimate and compression threshold info.

    Returns:
        Context status information
    """
    ctx_mgr = get_context_manager()
    tm = get_transcript_manager()

    transcripts = tm.list_transcripts()
    transcript_count = len(transcripts)

    threshold = ctx_mgr.token_threshold
    latest_transcript = transcripts[0] if transcripts else None

    result = f"""Context Status:
- Token Threshold: {threshold}
- Transcripts Saved: {transcript_count}
"""

    if latest_transcript:
        result += f"- Latest Transcript: {latest_transcript['filename']} ({latest_transcript['created']})\n"

    return result
