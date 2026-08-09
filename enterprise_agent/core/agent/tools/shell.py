import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.tools.workspace import (
    get_user_workspace,
    is_operational_agent_path,
    is_sensitive_agent_path,
)

BLOCKED_PATTERNS = [
    "rm -rf /",
    "del /f",
    "del /s",
    "rmdir /s",
    "chmod -r 777 /",
    "sudo ",
    "shutdown",
    "format c:",
    ":(){ :|:& };:",
]

BLOCKED_BINARIES = {
    "rm", "sudo", "su", "shutdown", "reboot", "halt", "poweroff",
    "mkfs", "dd", "format", "fdisk",
    "chmod", "chown", "chgrp",
    # Nested shells and inline dispatch bypass top-level token inspection.
    "sh", "bash", "zsh", "fish", "cmd", "cmd.exe", "powershell", "pwsh",
    "eval", "exec", "source", "xargs", "env", "printenv", "nohup",
    # Direct transfer tools can exfiltrate workspace data or download payloads.
    "curl", "wget", "nc", "netcat", "ssh", "scp", "sftp", "rsync",
}

TRUSTED_EXECUTABLE_DIRS = {
    Path("/bin"),
    Path("/usr/bin"),
    Path("/usr/local/bin"),
    Path("/opt/homebrew/bin"),
}

CONTROL_OPERATORS = {";", "&&", "||", "|"}
REDIRECTION_OPERATORS = {">", ">>", "<"}
INLINE_CODE_FLAGS = {
    "python": {"-c"},
    "python3": {"-c"},
    "node": {"-e", "--eval"},
    "ruby": {"-e"},
    "perl": {"-e"},
}

POSIX_BASH_LOCATIONS = (
    Path("/bin/bash"),
    Path("/usr/bin/bash"),
    Path("/usr/local/bin/bash"),
    Path("/opt/homebrew/bin/bash"),
)


def _blocked(reason: str, remediation: str | None = None) -> str:
    """Return a model-readable policy rejection with a safe next action."""
    message = f"Blocked: {reason}"
    if remediation:
        message += f" Remediation: {remediation}"
    return message


def _tokenize_command(command: str) -> tuple[list[str], Optional[str]]:
    """Tokenize POSIX shell control operators while preserving quoted text."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer), None
    except ValueError as exc:
        return [], f"Blocked: command cannot be parsed safely ({exc})"


def _is_assignment(token: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token))


def _is_absolute_or_escape_path(token: str) -> bool:
    """Shell paths must be workspace-relative; executables are handled separately."""
    if token.startswith(("/", "~", "\\\\")):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", token):
        return True
    normalized = token.replace("\\", "/")
    return ".." in Path(normalized).parts


def _command_positions(tokens: list[str]) -> set[int]:
    positions: set[int] = set()
    expects_command = True
    for index, token in enumerate(tokens):
        if token in CONTROL_OPERATORS:
            expects_command = True
            continue
        if token in REDIRECTION_OPERATORS:
            continue
        if expects_command and _is_assignment(token):
            continue
        if expects_command:
            positions.add(index)
            expects_command = False
    return positions


def validate_command(command: str) -> Optional[str]:
    """Fail closed on commands that can escape the reviewable workspace boundary."""
    if not command or not command.strip():
        return _blocked("empty command", "provide one workspace-scoped command")
    if "\n" in command or "\r" in command:
        return _blocked(
            "multi-line shell commands are not allowed.",
            "use one command per tool call, or create a reviewed script with write_file first.",
        )
    if "$" in command or "`" in command:
        return _blocked(
            "shell and command substitution are not allowed.",
            "run the producing command separately and pass an explicit relative value.",
        )

    cmd_lower = command.lower().strip()

    # Check blocked patterns (substring match)
    for pattern in BLOCKED_PATTERNS:
        if pattern.lower() in cmd_lower:
            return _blocked(f"command contains '{pattern}'.")

    tokens, parse_error = _tokenize_command(command)
    if parse_error:
        return parse_error
    if any(token in {"<<", "<<<"} for token in tokens):
        return _blocked(
            "here-documents and here-strings are not allowed.",
            "create the file with write_file/edit_file, then run it by relative path.",
        )
    if any(token.lower() in {"/dev/null", "nul"} for token in tokens):
        return _blocked(
            "discarding command output is not allowed.",
            "remove the null-device redirection; stdout and stderr are captured automatically.",
        )
    if any(token in {">&", "<&", "&>"} for token in tokens):
        return _blocked(
            "file-descriptor redirection is not allowed.",
            "remove forms such as '2>&1'; stdout and stderr are returned separately.",
        )
    known_operators = CONTROL_OPERATORS | REDIRECTION_OPERATORS
    unsupported = [
        token for token in tokens
        if token and all(char in ";&|<>" for char in token) and token not in known_operators
    ]
    if unsupported:
        return _blocked(
            f"unsupported shell operator '{unsupported[0]}'.",
            "split the operation into reviewable tool calls.",
        )

    positions = _command_positions(tokens)
    if not positions:
        return "Blocked: no executable command found"

    for index, token in enumerate(tokens):
        if token in CONTROL_OPERATORS or token in REDIRECTION_OPERATORS:
            continue
        base_name = Path(token).name.lower()

        # Dangerous dispatch names are rejected wherever they occur so that
        # constructs such as `find -exec rm` cannot hide a second executable.
        if base_name in BLOCKED_BINARIES:
            if base_name == "rm":
                return _blocked(
                    "'rm' is not allowed.",
                    "use delete_paths with exact workspace-relative targets and a reason.",
                )
            return _blocked(
                f"'{base_name}' is not allowed.",
                "choose a workspace-scoped tool that preserves review and audit evidence.",
            )

        if index in positions:
            if token.startswith("/"):
                executable_parent = Path(token).parent
                if executable_parent not in TRUSTED_EXECUTABLE_DIRS:
                    return _blocked(
                        "executable path is outside trusted system directories.",
                        "invoke an installed command by name or use a trusted system executable.",
                    )
            continue

        if _is_assignment(token):
            _, value = token.split("=", 1)
            if _is_absolute_or_escape_path(value):
                return _blocked(
                    "environment assignment contains an outside-workspace path.",
                    "use a workspace-relative value.",
                )
            continue
        if _is_absolute_or_escape_path(token):
            return _blocked(
                "absolute, home-relative, and parent-traversal arguments are not allowed.",
                "the shell already starts at workspace root; use '.', a filename, or a relative directory.",
            )
        if is_sensitive_agent_path(token):
            return _blocked(
                "sensitive credential paths are not available to Agent shell commands.",
                "continue without reading Agent, Git, environment, or credential metadata.",
            )
        if is_operational_agent_path(token):
            return _blocked(
                "Agent operational paths are not available to generic shell commands.",
                "use the dedicated task, team, transcript, or read_tool_artifact tool.",
            )

    for position in positions:
        binary = Path(tokens[position]).name.lower()
        flags = set(tokens[position + 1:])
        if binary in INLINE_CODE_FLAGS and flags.intersection(INLINE_CODE_FLAGS[binary]):
            return _blocked(
                f"inline code execution via '{binary}' is not allowed.",
                "create a reviewed workspace file first, then execute that relative file.",
            )

    lowered_tokens = [token.lower() for token in tokens]
    for index, token in enumerate(lowered_tokens):
        if token != "git":
            continue
        git_args = lowered_tokens[index + 1:]
        if "clean" in git_args or ("reset" in git_args and "--hard" in git_args):
            return _blocked(
                "destructive git cleanup/reset is not allowed.",
                "inspect git status and request an explicit, narrower recovery action.",
            )

    return None


def _safe_subprocess_environment(workdir: Path) -> dict[str, str]:
    """Expose only runtime essentials, never application/model credentials."""
    temp_dir = workdir / ".agent_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    # Keep the virtualenv path itself; resolving the interpreter symlink can
    # jump to the base runtime and lose project-installed commands/modules.
    runtime_bin = str(Path(sys.executable).absolute().parent)
    host_path = os.environ.get("PATH", "")
    allowed = {
        key: value
        for key in ("PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "LANG", "LC_ALL")
        if (value := os.environ.get(key))
    }
    allowed.update({
        "PATH": os.pathsep.join(part for part in (runtime_bin, host_path) if part),
        "HOME": str(workdir),
        "USERPROFILE": str(workdir),
        "TMPDIR": str(temp_dir),
        "TMP": str(temp_dir),
        "TEMP": str(temp_dir),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    })
    return allowed


def _shell_execution_kwargs() -> dict[str, object]:
    """Select one deterministic command interpreter for foreground/background runs."""
    if os.name == "nt":
        # subprocess uses COMSPEC (normally cmd.exe) when shell=True on Windows.
        return {"shell": True}

    for candidate in POSIX_BASH_LOCATIONS:
        if candidate.is_file():
            return {"shell": True, "executable": str(candidate)}
    raise RuntimeError(
        "Bash executable is unavailable; install bash in the API runtime before using shell tools"
    )


def _read_captured_stream(handle) -> tuple[str, bool, int]:
    """Read useful head/tail output from a file-backed subprocess stream."""
    handle.flush()
    handle.seek(0, os.SEEK_END)
    total_bytes = handle.tell()
    limit = max(1, settings.TOOL_SOURCE_CAPTURE_MAX_BYTES)
    handle.seek(0)
    if total_bytes <= limit:
        payload = handle.read()
        return payload.decode("utf-8", errors="replace").strip(), False, total_bytes

    marker = f"\n... [source capture clipped {total_bytes - limit} bytes] ...\n".encode()
    available = max(0, limit - len(marker))
    head_size = available * 3 // 5
    tail_size = available - head_size
    head = handle.read(head_size)
    handle.seek(max(0, total_bytes - tail_size))
    tail = handle.read(tail_size)
    payload = head + marker + tail
    return payload.decode("utf-8", errors="replace").strip(), True, total_bytes


@tool
def bash(command: str) -> str:
    """Run a command in workspace using Bash on POSIX or cmd.exe on Windows.

    Commands inherit PYTHONIOENCODING=utf-8 automatically to avoid
    UnicodeEncodeError on Windows (GBK console).

    Args:
        command: Workspace-relative shell command to execute

    Returns:
        JSON with stdout, stderr, exit_code fields for structured parsing
    """
    error = validate_command(command)
    if error:
        return json.dumps({
            "stdout": "",
            "stderr": error,
            "exit_code": 1,
            "error_code": "policy_blocked",
        }, ensure_ascii=False)

    try:
        workdir = get_user_workspace()
        # Auto-set UTF-8 encoding so Python tools don't crash on
        # Unicode characters (emoji, Chinese) in Windows GBK consoles
        env = _safe_subprocess_environment(workdir)

        with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(
            mode="w+b"
        ) as stderr_file:
            result = subprocess.run(
                command,
                cwd=workdir,
                stdout=stdout_file,
                stderr=stderr_file,
                timeout=settings.COMMAND_TIMEOUT_SECONDS,
                env=env,
                **_shell_execution_kwargs(),
            )
            stdout, stdout_truncated, stdout_bytes = _read_captured_stream(stdout_file)
            stderr, stderr_truncated, stderr_bytes = _read_captured_stream(stderr_file)

        # Preserve a complete structured envelope and exit code. Individual
        # streams are bounded at their source before entering API memory.
        output = json.dumps({
            "stdout": stdout if stdout else "(no output)",
            "stderr": stderr,
            "exit_code": result.returncode,
            "source_truncated": stdout_truncated or stderr_truncated,
            "stdout_original_bytes": stdout_bytes,
            "stderr_original_bytes": stderr_bytes,
        }, ensure_ascii=False)
        return output

    except subprocess.TimeoutExpired:
        return json.dumps({
            "stdout": "",
            "stderr": f"Command timed out ({settings.COMMAND_TIMEOUT_SECONDS}s limit)",
            "exit_code": -1,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "stdout": "",
            "stderr": str(e),
            "exit_code": -1,
        }, ensure_ascii=False)
