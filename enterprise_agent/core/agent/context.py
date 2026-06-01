"""Context management for conversation compression.

Implements:
- Microcompact: Clear old tool results to prevent output bloat
- Auto compact: Summarize when token threshold exceeded, save transcript
- Token estimation: Estimate tokens from messages
- Transcript persistence: Save conversation history before compression
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.llm_factory import get_llm


def _extract_text(content: Any) -> str:
    """Extract plain text from LLM response, which may be str or content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif hasattr(block, "text"):
                parts.append(block.text)
        return "\n".join(parts) if parts else str(content)
    return str(content)

# Transcript storage directory
TRANSCRIPT_DIR_NAME = ".transcripts"


class TranscriptManager:
    """Manages conversation transcript persistence.

    Saves full conversation history before compression for later reference.
    """

    def __init__(self, workdir: Path = None):
        if workdir is None:
            from enterprise_agent.core.agent.tools.workspace import get_user_workspace
            workdir = get_user_workspace()
        self.workdir = workdir
        self.transcript_dir = self.workdir / TRANSCRIPT_DIR_NAME
        self.transcript_dir.mkdir(parents=True, exist_ok=True)

    def save(self, messages: List[Dict], session_id: str = None) -> Path:
        """Save messages to transcript file.

        Args:
            messages: List of conversation messages
            session_id: Optional session identifier

        Returns:
            Path to saved transcript file
        """
        timestamp = int(time.time())
        filename = f"transcript_{timestamp}"
        if session_id:
            filename = f"transcript_{session_id}_{timestamp}"
        filename += ".jsonl"

        path = self.transcript_dir / filename
        with open(path, "w", encoding="utf-8") as f:
            for msg in messages:
                # Convert message to serializable format
                if hasattr(msg, "to_json"):
                    serialized = msg.to_json()
                elif isinstance(msg, dict):
                    serialized = msg
                else:
                    serialized = {
                        "role": getattr(msg, "role", "unknown"),
                        "content": str(getattr(msg, "content", ""))
                    }
                f.write(json.dumps(serialized, default=str) + "\n")

        return path

    def load(self, path: Path) -> List[Dict]:
        """Load messages from transcript file.

        Args:
            path: Path to transcript file

        Returns:
            List of messages
        """
        if not path.exists():
            return []

        messages = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    messages.append(json.loads(line))
        return messages

    def list_transcripts(self) -> List[Dict]:
        """List all saved transcripts.

        Returns:
            List of transcript metadata
        """
        transcripts = []
        for f in self.transcript_dir.glob("transcript_*.jsonl"):
            stat = f.stat()
            transcripts.append({
                "path": str(f),
                "filename": f.name,
                "size": stat.st_size,
                "created": datetime.fromtimestamp(stat.st_mtime).isoformat()
            })
        return sorted(transcripts, key=lambda x: x["created"], reverse=True)


class ContextManager:
    """Manages conversation context compression.

    Provides:
    - Token estimation
    - Microcompact (tool result cleanup)
    - Auto compact (full summarization with transcript)
    """

    def __init__(
        self,
        llm=None,
        transcript_manager: TranscriptManager = None
    ):
        self.llm = llm or get_llm()
        if transcript_manager is None:
            transcript_manager = TranscriptManager()
        self.transcript_manager = transcript_manager
        self.token_threshold = settings.TOKEN_THRESHOLD

    def estimate_tokens(self, messages: List[Any]) -> int:
        """Estimate token count from messages.

        Uses weighted estimation:
        - CJK characters: ~1.5 tok/char (Chinese/Japanese/Korean ~2 tokens per char)
        - Other characters: ~4 tok/char (English ~4 chars per token)
        - Message overhead: ~4 tokens per message (role marker, formatting)

        Args:
            messages: List of messages (can be dict or LangChain message objects)

        Returns:
            Estimated token count
        """
        import unicodedata

        total_tokens = 0
        for msg in messages:
            text = ""
            if isinstance(msg, dict):
                text = json.dumps(msg, default=str)
            elif hasattr(msg, "content"):
                content = str(msg.content) if msg.content else ""
                text = content
                total_tokens += 4  # Message overhead (role marker, formatting)
            else:
                text = str(msg)

            cjk_chars = 0
            other_chars = 0
            for ch in text:
                # Check if character is in a CJK Unicode block
                cp = ord(ch)
                if (0x4E00 <= cp <= 0x9FFF or   # CJK Unified Ideographs
                    0x3400 <= cp <= 0x4DBF or   # CJK Unified Ideographs Extension A
                    0x20000 <= cp <= 0x2A6DF or # CJK Unified Ideographs Extension B
                    0x2A700 <= cp <= 0x2B73F or # CJK Unified Ideographs Extension C
                    0x2B740 <= cp <= 0x2B81F or # CJK Unified Ideographs Extension D
                    0x2B820 <= cp <= 0x2CEAF or # CJK Unified Ideographs Extension E
                    0xF900 <= cp <= 0xFAFF or   # CJK Compatibility Ideographs
                    0x2F800 <= cp <= 0x2FA1F or # CJK Compatibility Ideographs Supplement
                    0x3000 <= cp <= 0x303F or   # CJK Symbols and Punctuation
                    0xFF00 <= cp <= 0xFFEF or   # Halfwidth and Fullwidth Forms
                    0x3040 <= cp <= 0x309F or   # Hiragana
                    0x30A0 <= cp <= 0x30FF or   # Katakana
                    0xAC00 <= cp <= 0xD7AF):    # Hangul Syllables
                    cjk_chars += 1
                else:
                    other_chars += 1

            total_tokens += int(cjk_chars / 1.5) + int(other_chars / 4)

        return max(total_tokens, 1)

    def microcompact(self, messages: List[Any], keep_last: int = 6) -> List[Any]:
        """Clear old tool result content to prevent output bloat.

        Handles both dict messages (``{"role": "tool", "content": "..."}``)
        and LangChain ToolMessage objects. Preserves the most recent
        ``keep_last`` tool results.

        Args:
            messages: List of messages (dicts or LangChain objects)
            keep_last: Number of recent tool results to keep

        Returns:
            Modified messages list (in-place modification)
        """
        # Count tool messages from the end
        tool_count = 0

        for msg in reversed(messages):
            is_tool = False
            content = None

            if isinstance(msg, dict):
                is_tool = msg.get("role") == "tool"
                content = msg.get("content", "")
            elif hasattr(msg, "type") and getattr(msg, "type", "") == "tool":
                is_tool = True
                content = getattr(msg, "content", "")

            if not is_tool:
                continue

            tool_count += 1
            if tool_count > keep_last and isinstance(content, str) and len(content) > 100:
                if isinstance(msg, dict):
                    msg["content"] = "[cleared - see transcript for full output]"
                else:
                    msg.content = "[cleared - see transcript for full output]"

        return messages

    def microcompact_langchain(
        self,
        messages: List[Any],
        keep_last: int = 6
    ) -> List[Any]:
        """Microcompact for LangChain message objects.

        Delegates to :meth:`microcompact` which handles both dict and
        LangChain message objects directly.

        Args:
            messages: List of LangChain message objects or dicts
            keep_last: Number of recent tool results to keep

        Returns:
            Modified messages list
        """
        return self.microcompact(messages, keep_last)

    async def auto_compact(
        self,
        messages: List[Any],
        session_id: str = None
    ) -> Dict[str, Any]:
        """Perform full context compression.

        1. Save transcript to file
        2. Generate summary using LLM
        3. Return compressed state

        Args:
            messages: List of messages to compress
            session_id: Optional session identifier for transcript

        Returns:
            Dict with compressed messages, summary, and transcript path
        """
        # Save transcript first
        transcript_path = self.transcript_manager.save(messages, session_id)

        # Extract last portion for summarization
        # Take last 80KB of text to fit in summarization context
        messages_text = json.dumps(messages, default=str)
        if len(messages_text) > settings.CONTEXT_SUMMARY_TRIGGER_CHARS:
            messages_text = messages_text[-settings.CONTEXT_SUMMARY_TRIGGER_CHARS:]

        # Generate summary
        summary_prompt = f"""Summarize the following conversation for context continuity.
Preserve key information: decisions made, code written, files modified, current task status, and any important findings.

Conversation:
{messages_text}

Provide a concise summary that allows continuing the work seamlessly."""

        response = await self.llm.ainvoke([{"role": "user", "content": summary_prompt}])
        summary = _extract_text(response.content)

        # Return compressed state
        return {
            "compressed_messages": [
                {
                    "role": "user",
                    "content": f"""<context_compressed transcript="{transcript_path}">

Previous conversation summary:
{summary}

## IMPORTANT: DO NOT STOP HERE
You are in the middle of a task. This summary just restored your context.
Immediately continue working on the next pending step from the summary above.
Do NOT summarize or repeat this content — take the next concrete action NOW.
Use a tool to move forward. Reference the transcript file if needed for detailed history.
</context_compressed>"""
                }
            ],
            "context_summary": summary,
            "transcript_path": str(transcript_path),
            "token_count_reset": 0
        }

    async def manual_compress(
        self,
        messages: List[Any],
        session_id: str = None
    ) -> Dict[str, Any]:
        """Manually triggered compression.

        Same as auto_compact but always executes regardless of threshold.

        Args:
            messages: List of messages
            session_id: Session identifier

        Returns:
            Compression result
        """
        return await self.auto_compact(messages, session_id)


# Global singleton for ContextManager (stateless utility class, no isolation needed)
_context_manager_singleton: ContextManager = None


def get_context_manager() -> ContextManager:
    """Get ContextManager instance (singleton for efficiency).

    ContextManager is a stateless utility class - it only operates on
    messages passed to its methods. Messages are already isolated by
    session_id in AgentState (managed by RedisSaver), so no additional
    isolation is needed.
    """
    global _context_manager_singleton
    if _context_manager_singleton is None:
        _context_manager_singleton = ContextManager()
    return _context_manager_singleton


# Per-user instances cache for TranscriptManager (depends on workspace directory)
_transcript_managers: Dict[int, TranscriptManager] = {}


def get_transcript_manager() -> TranscriptManager:
    """Get or create TranscriptManager instance for current user."""
    from enterprise_agent.core.agent.tools.workspace import get_current_user_id
    user_id = get_current_user_id()

    if user_id not in _transcript_managers:
        _transcript_managers[user_id] = TranscriptManager()
    return _transcript_managers[user_id]