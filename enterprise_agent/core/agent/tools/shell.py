import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.tools.workspace import get_user_workspace, is_sensitive_agent_path

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
        return "Blocked: empty command"
    if "\n" in command or "\r" in command:
        return "Blocked: multi-line shell commands are not allowed"
    if "$" in command or "`" in command:
        return "Blocked: shell and command substitution are not allowed"

    cmd_lower = command.lower().strip()

    # Check blocked patterns (substring match)
    for pattern in BLOCKED_PATTERNS:
        if pattern.lower() in cmd_lower:
            return f"Blocked: command contains '{pattern}'"

    tokens, parse_error = _tokenize_command(command)
    if parse_error:
        return parse_error
    if any(token in {"<<", "<<<"} for token in tokens):
        return "Blocked: here-documents and here-strings are not allowed"
    known_operators = CONTROL_OPERATORS | REDIRECTION_OPERATORS
    unsupported = [
        token for token in tokens
        if token and all(char in ";&|<>" for char in token) and token not in known_operators
    ]
    if unsupported:
        return f"Blocked: unsupported shell operator '{unsupported[0]}'"

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
            return f"Blocked: '{base_name}' is not allowed"

        if index in positions:
            if token.startswith("/"):
                executable_parent = Path(token).parent
                if executable_parent not in TRUSTED_EXECUTABLE_DIRS:
                    return "Blocked: executable path is outside trusted system directories"
            continue

        if _is_assignment(token):
            _, value = token.split("=", 1)
            if _is_absolute_or_escape_path(value):
                return "Blocked: environment assignment contains an outside-workspace path"
            continue
        if _is_absolute_or_escape_path(token):
            return "Blocked: shell arguments must use workspace-relative paths"
        if is_sensitive_agent_path(token):
            return "Blocked: sensitive credential paths are not available to Agent shell commands"

    for position in positions:
        binary = Path(tokens[position]).name.lower()
        flags = set(tokens[position + 1:])
        if binary in INLINE_CODE_FLAGS and flags.intersection(INLINE_CODE_FLAGS[binary]):
            return f"Blocked: inline code execution via '{binary}' is not allowed"

    lowered_tokens = [token.lower() for token in tokens]
    for index, token in enumerate(lowered_tokens):
        if token != "git":
            continue
        git_args = lowered_tokens[index + 1:]
        if "clean" in git_args or ("reset" in git_args and "--hard" in git_args):
            return "Blocked: destructive git cleanup/reset is not allowed"

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


@tool
def bash(command: str) -> str:
    """Run shell command in workspace.

    Commands inherit PYTHONIOENCODING=utf-8 automatically to avoid
    UnicodeEncodeError on Windows (GBK console).

    Args:
        command: Shell command to execute using the host OS default shell

    Returns:
        JSON with stdout, stderr, exit_code fields for structured parsing
    """
    error = validate_command(command)
    if error:
        return json.dumps({"stdout": "", "stderr": error, "exit_code": 1}, ensure_ascii=False)

    try:
        workdir = get_user_workspace()
        # Auto-set UTF-8 encoding so Python tools don't crash on
        # Unicode characters (emoji, Chinese) in Windows GBK consoles
        env = _safe_subprocess_environment(workdir)

        result = subprocess.run(
            command,
            shell=True,
            cwd=workdir,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=settings.COMMAND_TIMEOUT_SECONDS,
            env=env,
        )
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()

        # Truncate long output
        max_chars = settings.TOOL_OUTPUT_MAX_CHARS
        if len(stdout) > max_chars:
            stdout = stdout[:max_chars] + f"\n... (truncated {len(stdout) - max_chars} chars)"
        if len(stderr) > max_chars:
            stderr = stderr[:max_chars] + f"\n... (truncated {len(stderr) - max_chars} chars)"

        output = json.dumps({
            "stdout": stdout if stdout else "(no output)",
            "stderr": stderr,
            "exit_code": result.returncode,
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
