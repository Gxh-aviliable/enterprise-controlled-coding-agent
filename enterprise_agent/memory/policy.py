"""Deterministic admission policy for durable agent memory.

The vector store is not a transcript archive.  This module decides whether a
completed task produced reusable engineering knowledge before any document is
written to long-term memory.  Keeping this gate deterministic makes rejected
writes explainable and testable without another model call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from enterprise_agent.config.settings import settings

MEMORY_SCHEMA_VERSION = 2
ACTIVE_QUALITY_STATUS = "active"
LEGACY_QUALITY_STATUS = "legacy"

_EXPLICIT_MEMORY_MARKERS = (
    "请记住",
    "记住这个",
    "加入长期记忆",
    "保存到长期记忆",
    "以后记得",
    "remember this",
    "remember that",
    "save this to memory",
)

_DURABLE_PREFERENCE_MARKERS = (
    "我偏好",
    "我喜欢",
    "我不喜欢",
    "我习惯",
    "我通常",
    "我一直",
    "以后请",
    "以后都",
    "从现在开始",
    "默认使用",
    "每次都",
    "不要再",
    "始终使用",
    "长期使用",
    "i prefer",
    "i like",
    "i dislike",
    "i usually",
    "i always",
    "from now on",
    "by default",
    "always use",
    "never use",
)

_STRONG_PERSISTENCE_MARKERS = (
    "请记住",
    "记住这个",
    "以后",
    "从现在开始",
    "每次",
    "始终",
    "长期",
    "remember",
    "from now on",
    "always",
    "never",
)

_ONE_OFF_MARKERS = (
    "这次",
    "本次",
    "当前任务",
    "这个任务",
    "这篇",
    "临时",
    "for this task",
    "this time",
    "in this task",
    "in this story",
)

_ENGINEERING_PATTERN = re.compile(
    r"("
    r"代码|仓库|项目|工程|接口|架构|数据库|部署|测试|修复|调试|文件|命令|"
    r"依赖|配置|服务|容器|docker|git|api|bug|test|build|deploy|"
    r"repository|code|file|shell|python|javascript|typescript|vue|fastapi|"
    r"langgraph|redis|mysql|chroma|readme|\.py\b|\.js\b|\.ts\b|\.vue\b"
    r")",
    re.IGNORECASE,
)

# These tools only orchestrate the current run.  Calling them is not durable
# evidence that the repository or project knowledge changed.
_OPERATIONAL_TOOLS = {
    "list_skills",
    "load_skill",
    "todo_update",
    "task_create",
    "task_update",
    "task_list",
    "claim_task",
    "send_message",
    "spawn_teammate",
    "delegate_task",
    "team_status",
    "search_memory",
}


@dataclass(frozen=True)
class MemoryAdmissionDecision:
    """Explainable result of the long-term-memory admission gate."""

    accepted: bool
    memory_type: str
    reason: str


def _contains_any(text: str, markers: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def has_explicit_memory_intent(user_request: str) -> bool:
    """Return whether the user explicitly asked for durable storage."""
    return _contains_any(user_request or "", _EXPLICIT_MEMORY_MARKERS)


def has_durable_pattern_signal(user_request: str) -> bool:
    """Return whether a request contains evidence of a lasting preference.

    A one-off instruction such as "write this story as fantasy" is not a user
    preference.  It is accepted only when accompanied by explicit persistence
    language such as "from now on", "by default", or "remember".
    """
    text = user_request or ""
    if not _contains_any(text, _DURABLE_PREFERENCE_MARKERS):
        return False
    if _contains_any(text, _ONE_OFF_MARKERS) and not _contains_any(
        text, _STRONG_PERSISTENCE_MARKERS
    ):
        return False
    return True


def memory_quality_status(metadata: dict[str, Any] | None) -> str:
    """Classify stored metadata without mutating legacy Chroma documents."""
    metadata = metadata or {}
    if (
        metadata.get("schema_version") == MEMORY_SCHEMA_VERSION
        and metadata.get("quality_status") == ACTIVE_QUALITY_STATUS
    ):
        return ACTIVE_QUALITY_STATUS
    return LEGACY_QUALITY_STATUS


def _record_succeeded(record: dict[str, Any]) -> bool:
    if record.get("ok") is True:
        return True
    return str(record.get("status", "")).lower() in {"succeeded", "success", "done"}


class MemoryAdmissionPolicy:
    """Conservative policy for automatic task-outcome memory."""

    def decide(
        self,
        *,
        user_request: str,
        task_status: str,
        importance: float,
        tool_execution_records: list[dict[str, Any]] | None = None,
        changed_files: list[str] | None = None,
        validation_results: list[dict[str, Any]] | None = None,
    ) -> MemoryAdmissionDecision:
        """Decide whether one finalized task should become durable memory."""
        request = (user_request or "").strip()
        if task_status != "succeeded":
            return MemoryAdmissionDecision(False, "task_outcome", "task_not_succeeded")

        if has_explicit_memory_intent(request):
            return MemoryAdmissionDecision(True, "user_note", "explicit_user_request")

        if not _ENGINEERING_PATTERN.search(request):
            return MemoryAdmissionDecision(False, "task_outcome", "non_engineering_task")

        records = tool_execution_records or []
        successful_tools = {
            str(record.get("tool_name") or record.get("name") or "")
            for record in records
            if _record_succeeded(record)
        }
        substantive_tools = successful_tools - _OPERATIONAL_TOOLS - {""}
        successful_validation = any(
            item.get("ok") is True for item in (validation_results or [])
        )
        durable_evidence = bool(changed_files) or successful_validation or bool(substantive_tools)
        if not durable_evidence:
            return MemoryAdmissionDecision(False, "task_outcome", "no_durable_evidence")

        threshold = settings.MEMORY_ADMISSION_MIN_IMPORTANCE
        if importance < threshold:
            return MemoryAdmissionDecision(
                False,
                "task_outcome",
                f"importance_below_{threshold:.2f}",
            )

        return MemoryAdmissionDecision(True, "task_outcome", "verified_engineering_outcome")
