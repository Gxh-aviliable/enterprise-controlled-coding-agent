import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.tools.workspace import get_user_workspace

BLOCKED_PATTERNS = [
    # Destructive file operations (any path)
    "rm -rf /", "rm -rf", "rm -r", "del /f", "del /s", "rmdir /s",
    # Recursive permission changes on root
    "chmod -R 777 /", "chmod -R 777 /*",
    # Privilege escalation
    "sudo ", "su -", "su root",
    # System control
    "shutdown", "reboot", "halt", "poweroff",
    # Filesystem destruction
    "mkfs", "dd if=", "format c:",
    # Fork bombs
    ":(){ :|:& };:",
    # Pipe to shell (block all variants including absolute paths)
    "| sh", "| bash", "| /bin/sh", "| /bin/bash",
    "| zsh", "| /bin/zsh", "| /usr/bin/sh", "| /usr/bin/bash",
    # Redirect output to critical paths
    "> /etc/", ">> /etc/",
    # Download-and-execute patterns
    "curl", "| sh", "wget", "| bash",
]

BLOCKED_BINARIES = {
    "rm", "sudo", "su", "shutdown", "reboot", "halt", "poweroff",
    "mkfs", "dd", "format", "fdisk",
    "chmod", "chown", "chgrp",
}


def validate_command(command: str) -> Optional[str]:
    """Return error message if command is dangerous, None if OK."""
    cmd_lower = command.lower().strip()

    # Check blocked patterns (substring match)
    for pattern in BLOCKED_PATTERNS:
        if pattern.lower() in cmd_lower:
            return f"Blocked: command contains '{pattern}'"

    # Check for rm/mv/cp targeting root filesystem
    import re
    destructive_on_root = re.search(
        r'(?:rm|mv|cp|move|del|erase)\s+.*?(?:/etc|/boot|/sys|/proc|C:\\\\Windows|/bin|/sbin|/usr|/var|/home|/root)',
        cmd_lower
    )
    if destructive_on_root and not re.search(r'(?:workspace|/tmp)', cmd_lower):
        return "Blocked: potentially destructive operation outside workspace"

    # Block base binary name
    try:
        parts = shlex.split(command)
        if parts:
            cmd_name = Path(parts[0]).name.lower()
            if cmd_name in BLOCKED_BINARIES:
                return f"Blocked: '{cmd_name}' is not allowed"
    except ValueError:
        pass

    return None


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
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")  # PEP 540: UTF-8 mode for Python 3.7+

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
