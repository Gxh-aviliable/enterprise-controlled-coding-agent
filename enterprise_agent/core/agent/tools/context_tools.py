"""Context management tools for manual compression and transcript handling.

Provides:
- compress: Manually trigger context compression
- list_transcripts: List saved conversation transcripts
- get_transcript: Load a specific transcript
"""

from langchain_core.tools import tool

from enterprise_agent.core.agent.context import get_context_manager, get_transcript_manager

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
def get_transcript(filename: str) -> str:
    """Load and display a compression backup transcript file.

    These are operational compression artifacts, NOT user long-term memory.
    For long-term memory queries, use `search_memory` instead.

    Args:
        filename: Transcript filename (e.g., 'transcript_xxx.jsonl')

    Returns:
        Transcript content summary or full content
    """
    tm = get_transcript_manager()
    path = tm.transcript_dir / filename

    if not path.exists():
        available = [t["filename"] for t in tm.list_transcripts()]
        return f"Transcript not found: {filename}\nAvailable: {', '.join(available) or 'none'}"

    messages = tm.load(path)

    if not messages:
        return f"Empty transcript: {filename}"

    # Format for display
    lines = []
    for msg in messages[:50]:  # Show first 50 messages
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        preview = content[:200] if len(content) > 200 else content
        lines.append(f"[{role}] {preview}")

    total = len(messages)
    shown = min(50, total)

    result = f"Transcript: {filename} ({total} messages, showing {shown})\n\n"
    result += "\n".join(lines)

    if total > 50:
        result += f"\n\n... ({total - 50} more messages)"

    return result


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