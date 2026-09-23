"""Administrator-owned runtime probes. No workspace, network or model commands."""

import re
import shlex
import threading
import time
import uuid

from enterprise_agent.sandbox.engine import SandboxError

_CACHE = {}
_LOCK = threading.Lock()
TOOLS = frozenset({"python", "python3", "node", "npm", "pnpm", "yarn", "pytest", "ruff", "mypy", "go", "cargo", "make"})
MODULES = frozenset({"pytest", "ruff", "mypy"})
PROBE = """for tool in python python3 node npm pnpm yarn pytest ruff mypy go cargo make; do
if command -v "$tool" >/dev/null 2>&1; then printf '%s\\n' "$tool"; fi
done
if command -v python >/dev/null 2>&1; then
python -I -c "import importlib.util
for m in ('pytest','ruff','mypy'):
 if importlib.util.find_spec(m): print('module:'+m)"
fi"""


class EnvironmentUnavailableError(SandboxError):
    pass


def requirements(command):
    try:
        args = shlex.split(command)
    except ValueError:
        return set()
    if len(args) > 3 and args[0] == "cd" and args[2] == "&&":
        args = args[3:]
    if args[:1] == ["PYTHONDONTWRITEBYTECODE=1"]:
        args = args[1:]
    if not args or args[0] not in TOOLS:
        return set()
    needed = {args[0]}
    if args[0] in {"python", "python3"} and "-m" in args:
        index = args.index("-m") + 1
        if args[index : index + 1] and args[index] in MODULES:
            needed.add("module:" + args[index])
    if args[0] in {"npm", "pnpm", "yarn"}:
        needed.add("node")
    return needed


def check_runtime(executor, request, image):
    needed = requirements(request.command)
    if not needed:
        return {"status": "not_applicable", "image_id": image["Id"]}
    # Digest-keyed capabilities are independent of the user's project content.
    key = (executor.engine.socket_path, image["Id"])
    with _LOCK:
        capabilities = _CACHE.get(key)
    if capabilities is None:
        name = "agent-preflight-" + uuid.uuid4().hex
        spec = executor.spec(request, request.workspace, name, 10)
        spec.update(Image=image["Id"], WorkingDir="/tmp", NetworkDisabled=True)
        spec["HostConfig"]["NetworkMode"] = "none"
        spec["HostConfig"]["Mounts"] = []
        spec["Cmd"][-1] = PROBE
        capture = None
        try:
            response = executor.engine.request("POST", f"/containers/create?name={name}", spec)
            if response.get("Warnings"):
                raise EnvironmentUnavailableError("Runtime probe constraints were not accepted")
            capture = executor.engine.attach(name, 4096, 10)
            executor.engine.request("POST", f"/containers/{name}/start")
            deadline = time.monotonic() + 12
            while True:
                state = executor.engine.request("GET", f"/containers/{name}/json")["State"]
                if not state["Running"]:
                    if state["ExitCode"] != 0:
                        raise EnvironmentUnavailableError("Administrator runtime probe failed")
                    break
                if time.monotonic() >= deadline:
                    raise EnvironmentUnavailableError("Administrator runtime probe timed out")
                time.sleep(0.05)
            streams, _ = capture.finish()
            capabilities = frozenset(streams[0].splitlines()) & (TOOLS | {"module:" + m for m in MODULES})
        finally:
            if capture:
                capture.close()
            executor.engine.remove(name)
        with _LOCK:
            if len(_CACHE) >= 32:
                _CACHE.clear()
            _CACHE[key] = capabilities
    # Installable packages may exist in the workspace's persistent dependency
    # mounts; an image-only probe cannot declare them missing.
    installable = {"pytest", "ruff", "mypy", "pnpm", "yarn"} | {"module:" + m for m in MODULES}
    missing = sorted(needed - capabilities - installable)
    if missing:
        raise EnvironmentUnavailableError("Missing administrator-provided runtime/dependency: " + ", ".join(missing))
    return {
        "status": "available",
        "image_id": image["Id"],
        "required": sorted(needed),
        "available": sorted(capabilities),
        "probe_scope": "empty container; no workspace mounts",
    }


def dependency_diagnostic(result):
    """Label runtime-reported dependency failures, not proof of a code defect.

    Project output is untrusted: this annotation never authorizes installs,
    networking, replay, or success. Arbitrary dynamic imports cannot be proven
    statically; administrators inspect this alongside declared dependencies.
    """
    if result.get("exit_code") in (0, None) or result.get("error_code"):
        return None
    output = str(result.get("stderr", "")) + "\n" + str(result.get("stdout", ""))
    match = re.search(
        r"(?:ModuleNotFoundError: No module named|Cannot find (?:module|package)) ['\"]([^'\"]{1,120})", output
    )
    if match:
        return {
            "category": "dependency_reported_missing",
            "name": match.group(1),
            "source": "runtime output",
            "remediation": "Install the missing project dependency with pip or npm, then rerun the command.",
        }
    return None
