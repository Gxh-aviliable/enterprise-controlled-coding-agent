"""Context management for conversation compression.

Implements:
- Microcompact: Clear old tool results to prevent output bloat
- Auto compact: Summarize when token threshold exceeded, save transcript
- Token estimation: Estimate tokens from messages
- Transcript persistence: Save conversation history before compression
"""

import copy
import json
import logging
import math
import os
import re
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.llm_factory import get_llm
from enterprise_agent.core.agent.tool_artifacts import (
    ARTIFACT_READ_MAX_BYTES,
    ToolArtifactStore,
    read_utf8_range,
    safe_path_component,
)


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


class ContextCompressionError(RuntimeError):
    """Summary generation failed after the recovery transcript was persisted."""

    def __init__(self, message: str, *, transcript_path: str):
        super().__init__(message)
        self.transcript_path = transcript_path


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
        if self.transcript_dir.exists() and self.transcript_dir.is_symlink():
            raise ValueError("Transcript directory cannot be a symlink")
        self.transcript_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.transcript_dir.resolve().is_relative_to(self.workdir.resolve()):
            raise ValueError("Transcript directory escapes the user workspace")
        try:
            os.chmod(self.transcript_dir, 0o700)
        except OSError:
            pass

    @staticmethod
    def _serialize_message(message: Any) -> Dict[str, Any]:
        """Convert dict and LangChain messages to one stable JSONL schema."""
        if isinstance(message, dict):
            role = message.get("role", "unknown")
            content = message.get("content", "")
            message_id = message.get("id")
            tool_call_id = message.get("tool_call_id")
            tool_calls = message.get("tool_calls")
            artifact = message.get("artifact")
        else:
            role = getattr(message, "type", getattr(message, "role", "unknown"))
            content = getattr(message, "content", "")
            message_id = getattr(message, "id", None)
            tool_call_id = getattr(message, "tool_call_id", None)
            tool_calls = getattr(message, "tool_calls", None)
            artifact = getattr(message, "artifact", None)

        role = {"human": "user", "ai": "assistant"}.get(str(role), str(role))
        record: Dict[str, Any] = {
            "schema_version": 1,
            "role": role,
            "content": content,
        }
        if message_id:
            record["id"] = str(message_id)
        if tool_call_id:
            record["tool_call_id"] = str(tool_call_id)
        if tool_calls:
            record["tool_calls"] = tool_calls
        if artifact:
            record["artifact"] = artifact
        return record

    def save(self, messages: List[Dict], session_id: str = None) -> Path:
        """Save messages to transcript file.

        Args:
            messages: List of conversation messages
            session_id: Optional session identifier

        Returns:
            Path to saved transcript file
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        filename = f"transcript_{timestamp}_{uuid.uuid4().hex[:8]}"
        if session_id:
            safe_session = safe_path_component(session_id, "session")
            filename = f"transcript_{safe_session}_{timestamp}_{uuid.uuid4().hex[:8]}"
        filename += ".jsonl"

        path = self.transcript_dir / filename
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.transcript_dir,
                prefix=f".{filename}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                for msg in messages:
                    serialized = self._serialize_message(msg)
                    handle.write(json.dumps(serialized, ensure_ascii=False, default=str) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
                temporary_name = handle.name
            try:
                os.chmod(temporary_name, 0o600)
            except OSError:
                pass
            os.replace(temporary_name, path)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        finally:
            if temporary_name and os.path.exists(temporary_name):
                os.unlink(temporary_name)

        return path

    def relative_path(self, path: Path) -> str:
        """Return a workspace-relative handle without exposing server paths."""
        return path.resolve().relative_to(self.workdir.resolve()).as_posix()

    def load(self, path: Path) -> List[Dict]:
        """Load messages from transcript file.

        Args:
            path: Path to transcript file

        Returns:
            List of messages
        """
        resolved = path.resolve()
        if (
            not resolved.is_relative_to(self.transcript_dir.resolve())
            or resolved.parent != self.transcript_dir.resolve()
            or not resolved.name.startswith("transcript_")
            or resolved.suffix != ".jsonl"
        ):
            raise ValueError("Invalid transcript path")
        if not resolved.exists():
            return []

        messages = []
        with open(resolved, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    messages.append(json.loads(line))
        return messages

    def resolve_handle(self, handle: str) -> Path:
        """Resolve a filename or workspace-relative transcript handle."""
        raw = str(handle or "")
        if not raw or "\\" in raw:
            raise ValueError("invalid_transcript_handle")
        candidate = Path(raw)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("invalid_transcript_handle")
        if len(candidate.parts) == 1:
            filename = candidate.name
        elif len(candidate.parts) == 2 and candidate.parts[0] == TRANSCRIPT_DIR_NAME:
            filename = candidate.parts[1]
        else:
            raise ValueError("invalid_transcript_handle")
        if not filename.startswith("transcript_") or not filename.endswith(".jsonl"):
            raise ValueError("invalid_transcript_handle")
        path = self.transcript_dir / filename
        if path.is_symlink():
            raise ValueError("transcript_symlink_rejected")
        resolved = path.resolve()
        if resolved.parent != self.transcript_dir.resolve():
            raise ValueError("invalid_transcript_handle")
        if not resolved.is_file():
            raise FileNotFoundError(filename)
        return resolved

    def read_range(
        self,
        handle: str,
        *,
        offset_bytes: int = 0,
        limit_bytes: int = ARTIFACT_READ_MAX_BYTES,
    ) -> Dict[str, Any]:
        """Read a bounded UTF-8 page from an operational transcript backup."""
        path = self.resolve_handle(handle)
        page = read_utf8_range(
            path,
            offset_bytes=offset_bytes,
            limit_bytes=limit_bytes,
        )
        return {
            "path": self.relative_path(path),
            **page,
        }

    def list_transcripts(self) -> List[Dict]:
        """List all saved transcripts.

        Returns:
            List of transcript metadata
        """
        transcripts = []
        for f in self.transcript_dir.glob("transcript_*.jsonl"):
            if f.is_symlink():
                continue
            stat = f.stat()
            transcripts.append({
                "path": self.relative_path(f),
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
        # A default manager must be resolved at call time.  Binding it here
        # would pin this process-wide ContextManager to the first user's
        # workspace and break tenant isolation.
        self._transcript_manager_override = transcript_manager

    @property
    def token_threshold(self) -> int:
        configured_window = max(0, int(settings.MODEL_CONTEXT_WINDOW_TOKENS))
        if configured_window:
            ratio = min(0.95, max(0.1, float(settings.CONTEXT_COMPRESSION_RATIO)))
            return min(settings.TOKEN_THRESHOLD, max(1, int(configured_window * ratio)))
        return settings.TOKEN_THRESHOLD

    @property
    def transcript_manager(self) -> TranscriptManager:
        if self._transcript_manager_override is not None:
            return self._transcript_manager_override
        return get_transcript_manager()

    def estimate_tokens(self, messages: List[Any]) -> int:
        """Estimate token count from messages.

        Prefer the configured model's local tokenizer when it exposes one. The
        cross-provider fallback is deliberately conservative for CJK, emoji,
        and high-entropy identifiers instead of assuming every non-CJK string
        is English prose at four characters per token.
        - Message overhead: ~4 tokens per message (role marker, formatting)

        Args:
            messages: List of messages (can be dict or LangChain message objects)

        Returns:
            Estimated token count
        """

        total_tokens = 0
        for msg in messages:
            try:
                text = json.dumps(
                    TranscriptManager._serialize_message(msg),
                    ensure_ascii=False,
                    default=str,
                )
            except Exception:
                text = str(msg)
            total_tokens += 4  # Message overhead (role marker, formatting)

            provider_count = 0
            count_tokens = getattr(self.llm, "get_num_tokens", None)
            if callable(count_tokens):
                try:
                    provider_count = max(0, int(count_tokens(text)))
                except Exception:
                    # Some providers expose the BaseChatModel method without a
                    # bundled tokenizer. The deterministic fallback below is
                    # always available and never requires network access.
                    provider_count = 0

            cjk_chars = 0
            ascii_chars = 0
            other_unicode_bytes = 0
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
                elif cp < 128:
                    ascii_chars += 1
                else:
                    # UTF-8 byte count is a conservative BPE upper bound for
                    # emoji, combining marks, and other multilingual symbols.
                    other_unicode_bytes += len(ch.encode("utf-8"))

            fallback_count = (
                math.ceil(cjk_chars * 1.5)
                + math.ceil(ascii_chars / 3)
                + other_unicode_bytes
            )
            # Random hashes/base64 are much less compressible than prose. Add
            # an adjustment only for diverse long ASCII runs so repeated test
            # data such as "aaaa..." is not needlessly charged at 1 char/token.
            for match in re.finditer(r"[A-Za-z0-9+/=_-]{32,}", text):
                run = match.group(0)
                if len(set(run)) >= 8:
                    fallback_count += max(
                        0,
                        math.ceil(len(run) * 0.75) - math.ceil(len(run) / 3),
                    )

            total_tokens += max(provider_count, fallback_count)

        return max(total_tokens, 1)

    @staticmethod
    def _tool_message_parts(message: Any) -> tuple[bool, str, str, Dict[str, Any] | None]:
        if isinstance(message, dict):
            return (
                message.get("role") == "tool",
                message.get("content", ""),
                str(message.get("tool_call_id") or message.get("id") or "tool"),
                message.get("artifact"),
            )
        return (
            getattr(message, "type", "") == "tool",
            getattr(message, "content", ""),
            str(getattr(message, "tool_call_id", None) or getattr(message, "id", None) or "tool"),
            getattr(message, "artifact", None),
        )

    @staticmethod
    def _apply_tool_compaction(
        message: Any,
        placeholder: str,
        artifact: Dict[str, Any],
    ) -> Any:
        compacted = copy.deepcopy(message)
        if isinstance(compacted, dict):
            compacted["content"] = placeholder
            compacted["artifact"] = artifact
        else:
            compacted.content = placeholder
            compacted.artifact = artifact
        return compacted

    def microcompact_with_report(
        self,
        messages: List[Any],
        keep_last: int = 6,
        *,
        trace_id: str | None = None,
        user_id: int | None = None,
    ) -> Dict[str, Any]:
        """Compact old tool payloads only after recoverable evidence exists."""
        compacted_messages = list(messages)
        changed_messages: List[Any] = []
        artifact_paths: List[str] = []
        artifact_errors: List[str] = []
        tool_count = 0
        cleared_chars = 0

        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            is_tool, content, tool_call_id, artifact = self._tool_message_parts(message)
            if not is_tool:
                continue
            tool_count += 1
            if (
                tool_count <= keep_last
                or not isinstance(content, str)
                or len(content) <= settings.MICROCOMPACT_MIN_CHARS
            ):
                continue
            if content.startswith("[tool output compacted;"):
                continue

            receipt: Dict[str, Any] | None = None
            store = ToolArtifactStore(user_id=user_id)
            if isinstance(artifact, dict):
                valid, validation_error = store.validate_receipt(artifact)
                if valid:
                    receipt = artifact
                else:
                    # A message carrying a stale/tampered receipt may already
                    # contain only a model preview. Never relabel that preview
                    # as if it were the original evidence.
                    artifact_errors.append(
                        f"{tool_call_id}: {validation_error or 'artifact_invalid'}"
                    )
                    continue
            else:
                try:
                    legacy_call_id = tool_call_id
                    if legacy_call_id == "tool":
                        legacy_call_id = f"legacy-{index}"
                    saved = store.save(
                        content,
                        trace_id=trace_id or "context-recovery",
                        tool_call_id=legacy_call_id,
                        recovered_from_message=True,
                        limited_original=True,
                    )
                    receipt = saved.to_dict()
                except Exception as exc:
                    # Fail open for context size but fail closed for evidence:
                    # leave the original message untouched rather than claim it
                    # can be recovered from a path that does not exist.
                    logging.warning(
                        "Failed to persist recovery artifact for %s: %s",
                        tool_call_id,
                        exc,
                    )
                    artifact_errors.append(f"{tool_call_id}: artifact_write_failed")
                    continue

            placeholder = (
                f'[tool output compacted; artifact: {receipt["path"]}; '
                f'sha256={receipt["sha256"]}; original_chars={receipt["original_chars"]}]'
            )
            replacement = self._apply_tool_compaction(message, placeholder, receipt)
            # A receipt has a fixed schema cost. Never call this compaction if
            # replacing a small payload would increase the active model context.
            if self.estimate_tokens([replacement]) >= self.estimate_tokens([message]):
                continue
            compacted_messages[index] = replacement
            changed_messages.append(replacement)
            artifact_paths.append(str(receipt["path"]))
            cleared_chars += max(0, len(content) - len(placeholder))

        return {
            "messages": compacted_messages,
            "changed_messages": changed_messages,
            "compacted_count": len(changed_messages),
            "cleared_chars": cleared_chars,
            "artifact_paths": artifact_paths,
            "artifact_errors": artifact_errors,
            "tokens_before": self.estimate_tokens(messages),
            "tokens_after": self.estimate_tokens(compacted_messages),
        }

    def microcompact(
        self,
        messages: List[Any],
        keep_last: int = 6,
        *,
        trace_id: str | None = None,
        user_id: int | None = None,
    ) -> List[Any]:
        """Clear old tool result content to prevent output bloat.

        Handles both dict messages (``{"role": "tool", "content": "..."}``)
        and LangChain ToolMessage objects. Preserves the most recent
        ``keep_last`` tool results.

        Args:
            messages: List of messages (dicts or LangChain objects)
            keep_last: Number of recent tool results to keep

        Returns:
            A copied message list with eligible old tool bodies replaced.
        """
        return self.microcompact_with_report(
            messages,
            keep_last,
            trace_id=trace_id,
            user_id=user_id,
        )["messages"]

    def microcompact_langchain(
        self,
        messages: List[Any],
        keep_last: int = 6,
        *,
        trace_id: str | None = None,
        user_id: int | None = None,
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
        return self.microcompact(
            messages,
            keep_last,
            trace_id=trace_id,
            user_id=user_id,
        )

    @staticmethod
    def build_continuity_snapshot(
        state: Dict[str, Any] | None,
        *,
        transcript_path: str,
    ) -> Dict[str, Any]:
        """Extract authoritative task facts without asking the summarizer to infer them."""
        state = state or {}
        tool_records = []
        for record in state.get("tool_execution_records", [])[-20:]:
            tool_records.append({
                "tool_name": record.get("tool_name"),
                "tool_call_id": record.get("tool_call_id"),
                "status": record.get("status"),
                "ok": record.get("ok"),
                "error_code": record.get("error_code"),
                "exit_code": record.get("exit_code"),
                "artifact_path": record.get("artifact_path"),
                "artifact_sha256": record.get("artifact_sha256"),
            })
        previous_summary = state.get("context_summary") or ""
        if isinstance(previous_summary, str) and previous_summary:
            try:
                previous_packet = json.loads(previous_summary)
            except json.JSONDecodeError:
                previous_summary = previous_summary[-10_000:]
            else:
                # Do not recursively embed an earlier packet inside every new
                # packet. Exact task facts are rebuilt from AgentState; only
                # retain the prior narrative as an additional hint.
                previous_summary = str(previous_packet.get("model_summary", ""))[-10_000:]

        return {
            "objective": state.get("current_user_request", ""),
            "task": {
                "trace_id": state.get("trace_id"),
                "task_status": state.get("task_status"),
                "execution_phase": state.get("execution_phase"),
                "failure_reason": state.get("failure_reason"),
                "current_task": state.get("current_task"),
            },
            "plan": {
                "todos": state.get("todos", []),
                "has_open_todos": state.get("has_open_todos", False),
            },
            "evidence": {
                "changed_files": state.get("changed_files", []),
                "validation_results": state.get("validation_results", []),
                "recent_tool_records": tool_records,
                "transcript_path": transcript_path,
            },
            "budgets_before_compression": {
                "round_count": state.get("round_count", 0),
                "tool_call_count": state.get("tool_call_count", 0),
                "task_token_count": state.get("task_token_count", 0),
                "session_token_count": state.get("session_token_count", 0),
            },
            "previous_model_summary": previous_summary,
        }

    def _summary_input_token_budget(self) -> int:
        """Return a conservative whole-prompt budget with output headroom."""
        configured_window = max(0, int(settings.MODEL_CONTEXT_WINDOW_TOKENS))
        configured_cap = max(256, int(settings.CONTEXT_SUMMARY_MAX_TOKENS))
        if configured_window:
            reserve = min(
                max(256, configured_window // 4),
                max(256, int(settings.CONTEXT_SUMMARY_OUTPUT_RESERVE_TOKENS)),
            )
            available = max(256, configured_window - reserve)
            configured_cap = min(configured_cap, available)
        # The estimator is deliberately lightweight rather than provider-tokenizer
        # exact, so leave a 10% guard band around the whole summary request.
        return max(256, int(configured_cap * 0.9))

    @staticmethod
    def _compact_payload(value: Any, *, string_limit: int, list_limit: int) -> Any:
        """Recursively bound a redacted state snapshot without breaking JSON."""
        from enterprise_agent.observability.trace_store import redact_text

        if isinstance(value, dict):
            return {
                str(key): ContextManager._compact_payload(
                    item,
                    string_limit=string_limit,
                    list_limit=list_limit,
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [
                ContextManager._compact_payload(
                    item,
                    string_limit=string_limit,
                    list_limit=list_limit,
                )
                for item in value[:list_limit]
            ]
        if isinstance(value, str):
            return redact_text(value, limit=string_limit)
        return value

    def _payload_tokens(self, payload: Any) -> int:
        return self.estimate_tokens([{
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, default=str),
        }])

    def _fit_durable_state(
        self,
        durable_state: Dict[str, Any],
        token_limit: int,
    ) -> Dict[str, Any]:
        """Fit authoritative state into its allocation and mark any degradation."""
        from enterprise_agent.observability.trace_store import redact_text, redact_value

        target = max(64, int(token_limit))
        safe_state = redact_value(durable_state, limit=4_000)
        current_task = safe_state.get("task", {}).get("current_task")
        if (
            isinstance(current_task, dict)
            and current_task.get("request") == safe_state.get("objective")
        ):
            current_task = dict(current_task)
            current_task.pop("request", None)
            safe_state["task"] = {**safe_state["task"], "current_task": current_task}
        if self._payload_tokens(safe_state) <= target:
            return safe_state

        for string_limit, list_limit in (
            (2_000, 20),
            (1_000, 10),
            (500, 8),
            (250, 5),
            (100, 3),
            (40, 2),
        ):
            candidate = self._compact_payload(
                safe_state,
                string_limit=string_limit,
                list_limit=list_limit,
            )
            candidate["continuity_snapshot_truncated"] = True
            if self._payload_tokens(candidate) <= target:
                return candidate

        minimal = {
            "objective": redact_text(str(safe_state.get("objective", "")), limit=80),
            "task": {
                key: self._compact_payload(
                    safe_state.get("task", {}).get(key),
                    string_limit=80,
                    list_limit=1,
                )
                for key in ("trace_id", "task_status", "execution_phase", "failure_reason")
            },
            "evidence": {
                "transcript_path": safe_state.get("evidence", {}).get("transcript_path"),
            },
            "continuity_snapshot_truncated": True,
        }
        if self._payload_tokens(minimal) <= target:
            return minimal
        return {
            "evidence": {
                "transcript_path": safe_state.get("evidence", {}).get("transcript_path"),
            },
            "continuity_snapshot_truncated": True,
        }

    def _fit_summary_record(
        self,
        record: Dict[str, Any],
        *,
        token_limit: int,
        max_chars: int,
    ) -> tuple[str, int] | None:
        """Return one valid JSON record that fits, preferring head/tail content."""
        from enterprise_agent.observability.trace_store import redact_value

        safe_record = redact_value(record, limit=4_000)
        encoded = json.dumps(safe_record, ensure_ascii=False, default=str)
        if len(encoded) <= max_chars:
            cost = self._payload_tokens(encoded)
            if cost <= token_limit:
                return encoded, cost

        content = str(safe_record.get("content", ""))
        base = {
            "schema_version": safe_record.get("schema_version", 1),
            "role": safe_record.get("role", "unknown"),
            "id": safe_record.get("id"),
            "tool_call_id": safe_record.get("tool_call_id"),
            "artifact": safe_record.get("artifact"),
            "summary_input_truncated": True,
        }
        if self._payload_tokens(base) > token_limit:
            return None

        # The candidate cost is not strictly monotonic at the full-content
        # boundary: truncated candidates carry a marker that the full candidate
        # does not. Try the complete body first so the binary search cannot
        # discard a small record that fits only without that marker.
        full_candidate = {**base, "content": content}
        full_candidate_text = json.dumps(
            full_candidate,
            ensure_ascii=False,
            default=str,
        )
        full_candidate_cost = self._payload_tokens(full_candidate_text)
        if full_candidate_cost <= token_limit and len(full_candidate_text) <= max_chars:
            return full_candidate_text, full_candidate_cost

        best: tuple[str, int] | None = None
        low = 0
        high = min(max(0, len(content) - 1), max_chars)
        while low <= high:
            keep = (low + high) // 2
            if keep < len(content):
                head_size = keep * 3 // 5
                tail_size = keep - head_size
                body = content[:head_size]
                body += "…[summary-input-truncated]…"
                if tail_size:
                    body += content[-tail_size:]
            else:
                body = content
            candidate = {**base, "content": body}
            candidate_text = json.dumps(candidate, ensure_ascii=False, default=str)
            candidate_cost = self._payload_tokens(candidate_text)
            if candidate_cost <= token_limit:
                best = candidate_text, candidate_cost
                low = keep + 1
            else:
                high = keep - 1
        return best

    def _recent_summary_input(
        self,
        messages: List[Any],
        max_chars: int,
        *,
        token_limit: int,
    ) -> str:
        """Select newest redacted records without ever exceeding the allocation."""
        if token_limit <= 0:
            return ""
        selected: List[str] = []
        used_tokens = 0
        for message in reversed(messages):
            fitted = self._fit_summary_record(
                TranscriptManager._serialize_message(message),
                token_limit=token_limit - used_tokens,
                max_chars=max_chars,
            )
            if fitted is None:
                continue
            record, record_tokens = fitted
            selected.append(record)
            used_tokens += record_tokens
            if used_tokens >= token_limit:
                break
        return "\n".join(reversed(selected))

    async def auto_compact(
        self,
        messages: List[Any],
        session_id: str = None,
        continuity_state: Dict[str, Any] | None = None,
        continuation_token_budget: int | None = None,
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
        if self._transcript_manager_override is not None:
            transcript_manager = self._transcript_manager_override
        else:
            from enterprise_agent.core.agent.tools.workspace import get_user_workspace

            user_id = (continuity_state or {}).get("user_id")
            transcript_manager = TranscriptManager(get_user_workspace(user_id))
        transcript_file = transcript_manager.save(messages, session_id)
        transcript_path = transcript_manager.relative_path(transcript_file)
        if continuation_token_budget is not None and int(continuation_token_budget) < 256:
            raise ContextCompressionError(
                "Runtime prompt leaves no safe room for a continuation packet.",
                transcript_path=transcript_path,
            )

        raw_durable_state = self.build_continuity_snapshot(
            continuity_state,
            transcript_path=transcript_path,
        )
        summary_input_budget = self._summary_input_token_budget()

        def build_summary_prompt(state_payload: Dict[str, Any], recent_text: str) -> str:
            return f"""Summarize the recent conversation for context continuity.
The durable state block is authoritative. Do not contradict it or invent completed work.
Preserve decisions, code changes, failures, validation evidence, open todos, risks, and the next concrete action.

Authoritative durable state:
{json.dumps(state_payload, ensure_ascii=False, default=str)}

Recent conversation records:
{recent_text or '(omitted; use the transcript handle when details are needed)'}

Return a concise operational narrative. Do not omit unresolved failures or pending work."""

        fixed_prompt_tokens = self.estimate_tokens([{
            "role": "user",
            "content": build_summary_prompt({}, ""),
        }])
        durable_budget = max(
            64,
            min(
                int(summary_input_budget * 0.65),
                summary_input_budget - fixed_prompt_tokens - 32,
            ),
        )
        durable_state = self._fit_durable_state(raw_durable_state, durable_budget)
        prompt_without_recent = build_summary_prompt(durable_state, "")
        prompt_without_recent_tokens = self.estimate_tokens([{
            "role": "user",
            "content": prompt_without_recent,
        }])
        recent_budget = max(0, summary_input_budget - prompt_without_recent_tokens - 32)
        messages_text = self._recent_summary_input(
            messages,
            settings.CONTEXT_SUMMARY_TRIGGER_CHARS,
            token_limit=recent_budget,
        )
        summary_prompt = build_summary_prompt(durable_state, messages_text)
        summary_prompt_estimated_tokens = self.estimate_tokens([{
            "role": "user",
            "content": summary_prompt,
        }])
        if summary_prompt_estimated_tokens > summary_input_budget:
            # Estimator overhead can differ when records are combined. The
            # transcript already exists, so dropping recent excerpts is safe.
            messages_text = ""
            summary_prompt = build_summary_prompt(durable_state, messages_text)
            summary_prompt_estimated_tokens = self.estimate_tokens([{
                "role": "user",
                "content": summary_prompt,
            }])
        if summary_prompt_estimated_tokens > summary_input_budget:
            durable_state = self._fit_durable_state(
                raw_durable_state,
                max(64, durable_budget // 2),
            )
            summary_prompt = build_summary_prompt(durable_state, "")
            summary_prompt_estimated_tokens = self.estimate_tokens([{
                "role": "user",
                "content": summary_prompt,
            }])
        if summary_prompt_estimated_tokens > summary_input_budget:
            # Final deterministic safety packet for exceptionally tiny windows.
            durable_state = {
                "evidence": {"transcript_path": transcript_path},
                "continuity_snapshot_truncated": True,
            }
            summary_prompt = build_summary_prompt(durable_state, "")
            summary_prompt_estimated_tokens = self.estimate_tokens([{
                "role": "user",
                "content": summary_prompt,
            }])
        if summary_prompt_estimated_tokens > summary_input_budget:
            raise ContextCompressionError(
                "Minimum context-summary prompt exceeds its configured input budget.",
                transcript_path=transcript_path,
            )

        # Generate summary. LangChain's supported chat providers accept
        # ``max_tokens`` through Runnable.bind; this turns the output reserve
        # into an actual provider request limit instead of input-side arithmetic.
        summary_output_limit = max(
            1,
            min(
                int(settings.CONTEXT_SUMMARY_OUTPUT_RESERVE_TOKENS),
                int(settings.MODEL_CONTEXT_WINDOW_TOKENS)
                - summary_prompt_estimated_tokens,
            ),
        )
        bind = getattr(self.llm, "bind", None)
        summary_llm = bind(max_tokens=summary_output_limit) if callable(bind) else self.llm
        summary_started = time.perf_counter()
        try:
            response = await summary_llm.ainvoke([
                {"role": "user", "content": summary_prompt}
            ])
        except Exception as exc:
            raise ContextCompressionError(
                f"Context summary model call failed: {exc}",
                transcript_path=transcript_path,
            ) from exc
        summary_duration_ms = int((time.perf_counter() - summary_started) * 1000)
        model_summary = _extract_text(response.content).strip() or "No additional narrative summary was produced."
        summary_usage = getattr(response, "usage_metadata", {}) or {}
        summary_input_tokens = int(summary_usage.get("input_tokens", 0) or 0)
        summary_output_tokens = int(summary_usage.get("output_tokens", 0) or 0)
        summary_usage_tokens = int(summary_usage.get("total_tokens", 0) or 0)
        if summary_input_tokens <= 0:
            summary_input_tokens = self.estimate_tokens([
                {"role": "user", "content": summary_prompt}
            ])
        if summary_output_tokens <= 0:
            summary_output_tokens = self.estimate_tokens([response])
        if summary_usage_tokens <= 0:
            summary_usage_tokens = summary_input_tokens + summary_output_tokens

        if continuation_token_budget is None:
            continuation_token_budget = max(256, self.token_threshold // 2)
        continuation_token_budget = int(continuation_token_budget)
        if continuation_token_budget < 256:
            raise ContextCompressionError(
                "Runtime prompt leaves no safe room for a continuation packet.",
                transcript_path=transcript_path,
            )

        continuation_message_id = f"context-compressed-{uuid.uuid4().hex}"

        def build_compressed_messages(packet_text: str) -> List[Dict[str, str]]:
            return [{
                "id": continuation_message_id,
                "role": "user",
                "content": f"""<context_compressed schema_version="2" transcript="{transcript_path}">

Authoritative continuation packet:
{packet_text}

## IMPORTANT: DO NOT STOP HERE
You are in the middle of a task. Continue from durable_state.plan and the next action above.
Do NOT summarize or repeat this content. Take the next concrete action now.
Use verified artifact handles or the available transcript backup only when prior details are needed.
</context_compressed>"""
            }]

        def encode_continuation(
            state_payload: Dict[str, Any],
            summary_text: str,
            *,
            truncated: bool,
        ) -> tuple[str, List[Dict[str, str]], int]:
            packet = {
                "schema_version": 2,
                "durable_state": state_payload,
                "model_summary": summary_text,
            }
            if truncated:
                packet["continuation_packet_truncated"] = True
            encoded = json.dumps(packet, ensure_ascii=False, indent=2, default=str)
            packet_messages = build_compressed_messages(encoded)
            return encoded, packet_messages, self.estimate_tokens(packet_messages)

        continuation_state = self._fit_durable_state(
            raw_durable_state,
            max(64, int(continuation_token_budget * 0.7)),
        )
        state_was_truncated = bool(
            continuation_state.get("continuity_snapshot_truncated")
        )
        encoded_packet, compressed_messages, continuation_message_tokens = (
            encode_continuation(
                continuation_state,
                model_summary,
                truncated=state_was_truncated,
            )
        )
        continuation_packet_truncated = state_was_truncated

        if continuation_message_tokens > continuation_token_budget:
            continuation_packet_truncated = True
            best: tuple[str, List[Dict[str, str]], int] | None = None
            low = 0
            high = len(model_summary)
            while low <= high:
                keep = (low + high) // 2
                if keep < len(model_summary):
                    head_size = keep * 3 // 5
                    tail_size = keep - head_size
                    bounded_summary = model_summary[:head_size]
                    bounded_summary += "…[model-summary-truncated]…"
                    if tail_size:
                        bounded_summary += model_summary[-tail_size:]
                else:
                    bounded_summary = model_summary
                candidate = encode_continuation(
                    continuation_state,
                    bounded_summary,
                    truncated=True,
                )
                if candidate[2] <= continuation_token_budget:
                    best = candidate
                    low = keep + 1
                else:
                    high = keep - 1
            if best is not None:
                encoded_packet, compressed_messages, continuation_message_tokens = best

        if continuation_message_tokens > continuation_token_budget:
            continuation_state = {
                "evidence": {"transcript_path": transcript_path},
                "continuity_snapshot_truncated": True,
            }
            encoded_packet, compressed_messages, continuation_message_tokens = (
                encode_continuation(
                    continuation_state,
                    "Model summary omitted to respect the next-turn context budget.",
                    truncated=True,
                )
            )
            continuation_packet_truncated = True

        if continuation_message_tokens > continuation_token_budget:
            raise ContextCompressionError(
                "Minimum continuation packet exceeds the next model-turn budget.",
                transcript_path=transcript_path,
            )

        decoded_packet = json.loads(encoded_packet)
        if decoded_packet.get("durable_state") != json.loads(
            json.dumps(continuation_state, ensure_ascii=False, default=str)
        ):
            raise ValueError("Context continuity packet failed integrity validation")

        return {
            "compressed_messages": compressed_messages,
            "context_summary": encoded_packet,
            "transcript_path": transcript_path,
            "token_count_reset": self.estimate_tokens(compressed_messages),
            "summary_usage_tokens": summary_usage_tokens,
            "summary_input_tokens": summary_input_tokens,
            "summary_output_tokens": summary_output_tokens,
            "summary_duration_ms": summary_duration_ms,
            "summary_schema_version": 2,
            "summary_input_budget": summary_input_budget,
            "summary_prompt_estimated_tokens": summary_prompt_estimated_tokens,
            "summary_output_token_limit": summary_output_limit,
            "continuity_snapshot_truncated": bool(
                continuation_state.get("continuity_snapshot_truncated")
            ),
            "continuation_token_budget": continuation_token_budget,
            "continuation_message_tokens": continuation_message_tokens,
            "continuation_packet_truncated": continuation_packet_truncated,
        }

    async def manual_compress(
        self,
        messages: List[Any],
        session_id: str = None,
        continuity_state: Dict[str, Any] | None = None,
        continuation_token_budget: int | None = None,
    ) -> Dict[str, Any]:
        """Manually triggered compression.

        Same as auto_compact but always executes regardless of threshold.

        Args:
            messages: List of messages
            session_id: Session identifier

        Returns:
            Compression result
        """
        return await self.auto_compact(
            messages,
            session_id,
            continuity_state,
            continuation_token_budget,
        )


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


def get_transcript_manager() -> TranscriptManager:
    """Resolve a fresh manager for the current user's workspace.

    The object is intentionally not cached: tests, CLI jobs and concurrent
    requests can change the active workspace context while the process lives.
    """
    return TranscriptManager()
