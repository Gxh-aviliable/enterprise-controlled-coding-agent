"""Uniform metadata and normalized result contract for Agent tools."""

from __future__ import annotations

import json
import shlex
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from enterprise_agent.config.settings import settings


class RiskLevel(str, Enum):
    SAFE = "safe"
    REVIEW = "review"
    DANGEROUS = "dangerous"


class ToolResultStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    BLOCKED = "blocked"
    TIMEOUT = "timeout"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ToolContract:
    name: str
    risk: RiskLevel
    timeout_seconds: int
    max_retries: int = 0
    idempotent: bool = False
    requires_confirmation: bool = False
    side_effect: str = "none"


@dataclass(frozen=True)
class ToolExecutionRecord:
    tool_name: str
    tool_call_id: str
    status: ToolResultStatus
    ok: bool
    output: str
    duration_ms: int
    attempt_count: int
    error_code: str | None = None
    exit_code: int | None = None
    artifact_path: str | None = None
    artifact_sha256: str | None = None
    artifact_bytes: int | None = None
    original_chars: int | None = None
    model_chars: int | None = None
    source_truncated: bool = False
    model_truncated: bool = False
    artifact_redacted: bool = False
    artifact_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


def _contract(
    name: str,
    *,
    risk: RiskLevel = RiskLevel.SAFE,
    timeout: int = 30,
    retries: int = 1,
    idempotent: bool = True,
    confirmation: bool = False,
    side_effect: str = "none",
) -> ToolContract:
    return ToolContract(
        name=name,
        risk=risk,
        timeout_seconds=timeout,
        max_retries=retries if idempotent else 0,
        idempotent=idempotent,
        requires_confirmation=confirmation,
        side_effect=side_effect,
    )


# One entry is required for every tool in ALL_TOOLS. Defaults are deliberately
# conservative for tools that mutate files, run processes, or coordinate agents.
TOOL_CONTRACTS = {
    # File tools
    "read_file": _contract("read_file"),
    "write_file": _contract(
        "write_file", risk=RiskLevel.REVIEW, idempotent=False,
        confirmation=True, side_effect="filesystem_write",
    ),
    "edit_file": _contract(
        "edit_file", risk=RiskLevel.REVIEW, idempotent=False,
        confirmation=True, side_effect="filesystem_write",
    ),
    "delete_paths": _contract(
        "delete_paths", risk=RiskLevel.DANGEROUS, idempotent=False,
        confirmation=True, side_effect="filesystem_delete",
    ),
    # Process tools
    "bash": _contract(
        "bash", risk=RiskLevel.REVIEW,
        timeout=settings.COMMAND_TIMEOUT_SECONDS + 5,
        idempotent=False, confirmation=True, side_effect="process",
    ),
    "background_run": _contract(
        "background_run", risk=RiskLevel.REVIEW,
        timeout=settings.COMMAND_TIMEOUT_SECONDS + 5,
        idempotent=False, confirmation=True, side_effect="background_process",
    ),
    "check_background": _contract("check_background"),
    # Operational task state
    "todo_update": _contract("todo_update", idempotent=False, side_effect="task_state"),
    "task_create": _contract(
        "task_create", risk=RiskLevel.REVIEW, idempotent=False,
        confirmation=True, side_effect="task_state",
    ),
    "task_get": _contract("task_get"),
    "task_update": _contract("task_update", idempotent=False, side_effect="task_state"),
    "task_list": _contract("task_list"),
    "claim_task": _contract("claim_task", idempotent=False, side_effect="task_state"),
    # Delegation and team coordination
    "task": _contract(
        "task", risk=RiskLevel.REVIEW,
        timeout=settings.AGENT_INVOKE_TIMEOUT_SECONDS,
        idempotent=False, confirmation=True, side_effect="subagent",
    ),
    "delegate_task": _contract(
        "delegate_task", risk=RiskLevel.REVIEW,
        timeout=settings.AGENT_INVOKE_TIMEOUT_SECONDS,
        idempotent=False, confirmation=True, side_effect="subagent",
    ),
    "spawn_teammate": _contract(
        "spawn_teammate", risk=RiskLevel.REVIEW,
        timeout=settings.AGENT_INVOKE_TIMEOUT_SECONDS,
        idempotent=False, confirmation=True, side_effect="subagent",
    ),
    "list_teammates": _contract("list_teammates"),
    "send_message": _contract(
        "send_message", risk=RiskLevel.REVIEW, idempotent=False,
        confirmation=True, side_effect="agent_message",
    ),
    "read_inbox": _contract("read_inbox"),
    "broadcast": _contract(
        "broadcast", risk=RiskLevel.REVIEW, idempotent=False,
        confirmation=True, side_effect="agent_message",
    ),
    "shutdown_request": _contract(
        "shutdown_request", risk=RiskLevel.REVIEW, idempotent=False,
        confirmation=True, side_effect="agent_control",
    ),
    "plan_approval": _contract(
        "plan_approval", risk=RiskLevel.REVIEW, idempotent=False,
        confirmation=True, side_effect="agent_control",
    ),
    "idle": _contract("idle", idempotent=False, side_effect="agent_control"),
    # Skills, context and memory
    "load_skill": _contract("load_skill"),
    "list_skills": _contract("list_skills"),
    "reload_skills": _contract("reload_skills"),
    "compress": _contract("compress", idempotent=False, side_effect="agent_state"),
    "list_transcripts": _contract("list_transcripts"),
    "get_transcript": _contract("get_transcript"),
    "read_tool_artifact": _contract("read_tool_artifact"),
    "context_status": _contract("context_status"),
    "search_memory": _contract("search_memory"),
}


SAFE_SHELL_BINARIES = {
    "pwd", "ls", "dir", "echo", "cat", "head", "tail", "wc", "find", "rg",
    "pytest", "ruff", "mypy", "npm", "pnpm", "yarn", "node", "python", "python3",
    "git", "go", "cargo", "make",
}

REVIEW_SHELL_TOKENS = {
    "install", "uninstall", "add", "remove", "commit", "push", "pull", "checkout",
    "switch", "merge", "rebase", "reset", "clean", "mv", "cp", "mkdir", "touch",
}

SAFE_PYTHON_MODULES = {"compileall", "py_compile", "pytest", "unittest"}


def get_tool_contract(tool_name: str) -> ToolContract:
    try:
        return TOOL_CONTRACTS[tool_name]
    except KeyError as exc:
        raise KeyError(f"No tool contract registered for {tool_name!r}") from exc


def validate_tool_contracts(tools: Iterable[Any]) -> None:
    """Raise when the executable registry and metadata registry diverge."""
    executable_names = {tool.name for tool in tools}
    contract_names = set(TOOL_CONTRACTS)
    missing = executable_names - contract_names
    stale = contract_names - executable_names
    if missing or stale:
        raise ValueError(
            f"Tool contract mismatch: missing={sorted(missing)}, stale={sorted(stale)}"
        )


def describe_tool(tool: Any) -> dict[str, Any]:
    """Return schema plus execution policy for API/trace presentation."""
    contract = get_tool_contract(tool.name)
    args_schema = getattr(tool, "args_schema", None)
    schema = args_schema.model_json_schema() if args_schema else {}
    data = asdict(contract)
    data["risk"] = contract.risk.value
    data["input_schema"] = schema
    return data


def resolve_tool_risk(tool_name: str, tool_args: dict[str, Any]) -> RiskLevel:
    """Resolve argument-sensitive risk while retaining conservative defaults."""
    contract = get_tool_contract(tool_name)
    if tool_name not in {"bash", "background_run"}:
        return contract.risk

    command = str(tool_args.get("command", ""))
    from enterprise_agent.core.agent.tools.shell import validate_command

    if validate_command(command):
        return RiskLevel.DANGEROUS

    # Shell control operators make a command harder to reason about. Keep it in
    # review even when the first executable is read-only.
    if any(operator in command for operator in (";", "&&", "||", "|", ">", "<")):
        return RiskLevel.REVIEW

    try:
        parts = shlex.split(command)
    except ValueError:
        return RiskLevel.REVIEW
    if not parts:
        return RiskLevel.REVIEW

    binary = Path(parts[0]).name.lower()
    args = parts[1:]
    lowered_args = {part.lower() for part in args}
    if binary in {"python", "python3"}:
        if not args:
            return RiskLevel.REVIEW
        if args[0].lower() in {"--version", "-v"} and len(args) == 1:
            return RiskLevel.SAFE
        if "-m" in args:
            module_index = args.index("-m") + 1
            module = args[module_index].lower() if module_index < len(args) else ""
            if module not in SAFE_PYTHON_MODULES:
                return RiskLevel.REVIEW
        else:
            # A workspace script can perform arbitrary filesystem operations;
            # direct interpreter execution must never inherit the SAFE label.
            return RiskLevel.REVIEW
    if binary == "node" and lowered_args not in ({"--version"}, {"-v"}):
        return RiskLevel.REVIEW
    if binary in SAFE_SHELL_BINARIES and not lowered_args.intersection(REVIEW_SHELL_TOKENS):
        return RiskLevel.SAFE
    return RiskLevel.REVIEW


def normalize_tool_result(
    *,
    tool_name: str,
    tool_call_id: str,
    raw_result: Any,
    duration_ms: int,
    attempt_count: int,
    display_output: str | None = None,
    artifact: dict[str, Any] | None = None,
    model_truncated: bool = False,
    artifact_error: str | None = None,
) -> ToolExecutionRecord:
    """Convert heterogeneous legacy tool output to one reliable result shape."""
    raw_output = str(raw_result)
    output = raw_output if display_output is None else display_output
    status = ToolResultStatus.SUCCESS
    error_code = None
    exit_code = None

    parsed = None
    if isinstance(raw_result, dict):
        parsed = raw_result
    elif isinstance(raw_result, str) and raw_result.lstrip().startswith("{"):
        try:
            parsed = json.loads(raw_result)
        except json.JSONDecodeError:
            parsed = None

    if isinstance(parsed, dict) and isinstance(parsed.get("exit_code"), int):
        exit_code = parsed["exit_code"]
        stderr = str(parsed.get("stderr", ""))
        if exit_code != 0:
            if "blocked:" in stderr.lower():
                status = ToolResultStatus.BLOCKED
                error_code = "policy_blocked"
            elif "timed out" in stderr.lower() or exit_code == -1:
                status = ToolResultStatus.TIMEOUT
                error_code = "tool_timeout"
            else:
                status = ToolResultStatus.ERROR
                error_code = "nonzero_exit"
    else:
        lowered = raw_output.lstrip().lower()
        if lowered.startswith("blocked:"):
            status = ToolResultStatus.BLOCKED
            error_code = "policy_blocked"
        elif lowered.startswith("error:") or lowered.startswith("error executing"):
            if "timeout" in lowered or "timed out" in lowered:
                status = ToolResultStatus.TIMEOUT
                error_code = "tool_timeout"
            else:
                status = ToolResultStatus.ERROR
                error_code = "tool_error"
        elif lowered.startswith("tool execution rejected"):
            status = ToolResultStatus.REJECTED
            error_code = "user_rejected"

    return ToolExecutionRecord(
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        status=status,
        ok=status == ToolResultStatus.SUCCESS,
        output=output,
        duration_ms=max(0, duration_ms),
        attempt_count=max(1, attempt_count),
        error_code=error_code,
        exit_code=exit_code,
        artifact_path=artifact.get("path") if artifact else None,
        artifact_sha256=artifact.get("sha256") if artifact else None,
        artifact_bytes=artifact.get("stored_bytes") if artifact else None,
        original_chars=artifact.get("original_chars") if artifact else len(raw_output),
        model_chars=len(output),
        source_truncated=bool(artifact.get("source_truncated")) if artifact else False,
        model_truncated=model_truncated,
        artifact_redacted=bool(artifact.get("redacted")) if artifact else False,
        artifact_error=artifact_error,
    )
