"""Run inside the local API container; temporary account is removed on exit.

docker exec -i docker-api-1 /app/.venv/bin/python - < scripts/local_demo_verify.py
Add --live-model after '-' to exercise the configured model through HTTP.
No passwords, access tokens, model keys or user content are printed.
"""

import asyncio
from pathlib import Path
import hashlib
import json
import secrets
import shutil
import sys
import time
import urllib.error
import urllib.request

from sqlalchemy import delete, select

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.tools.shell import bash
from enterprise_agent.core.agent.tools.workspace import get_user_workspace, set_current_user_id
from enterprise_agent.db.mysql import async_session_factory, engine
from enterprise_agent.models.user import User

engine.echo = False


def request(path, method="GET", data=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(
        "http://127.0.0.1:8000" + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers=headers, method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, {}


async def main():
    assert settings.MYSQL_HOST == "mysql", "This probe is only for the local Compose deployment"
    assert settings.AGENT_EXECUTOR == "docker"
    unique = secrets.token_hex(8)
    email = "demo-probe-" + unique + "@example.com"
    password = secrets.token_urlsafe(24)
    user_id, workspace = None, None
    evidence = {}
    try:
        for attempt in range(30):
            try:
                status, health = request("/health")
                if status == 200:
                    break
            except (OSError, ValueError):
                pass
            await asyncio.sleep(1)
        else:
            raise AssertionError("API did not become ready")
        evidence["health"] = health
        status, _ = request("/auth/register", "POST", {
            "username": "demo_probe_" + unique, "email": email, "password": password,
        })
        assert status == 200, ("register", status)
        async with async_session_factory() as db:
            user = (await db.execute(select(User).where(User.email == email))).scalar_one()
            user_id = user.id
        status, login = request("/auth/login", "POST", {"email": email, "password": password})
        assert status == 200
        free_token = login["access_token"]
        assert request("/github/settings", token=free_token)[0] == 404
        evidence["github_routes_removed_for_free_user"] = True
        # Elevate only this newly created throwaway account to test existing admin flows.
        async with async_session_factory() as db:
            user = await db.get(User, user_id)
            user.is_superuser = True
            await db.commit()
        status, login = request("/auth/login", "POST", {"email": email, "password": password})
        assert status == 200
        token = login["access_token"]
        status, capabilities = request("/chat/capabilities", token=token)
        assert status == 200 and "multi_agent" in capabilities["available_modes"]
        evidence["capabilities"] = capabilities
        status, _ = request("/github/settings", token=token)
        assert status == 404
        assert not getattr(settings, "GITHUB_ENABLED", False)
        assert not getattr(settings, "GITHUB_MASTER_KEY", "")
        evidence["github_routes_removed_for_admin"] = True
        set_current_user_id(user_id)
        workspace = get_user_workspace()
        (workspace / "demo.txt").write_text("before\n")
        status, _ = request("/workspace/write", "PUT", {
            "path": "demo.txt", "content": "after\n",
            "expected_sha256": hashlib.sha256(b"before\n").hexdigest(),
        }, token)
        assert status == 200, ("workspace_write", status)
        assert (workspace / "demo.txt").read_text() == "after\n"
        assert request("/workspace/read?path=demo.txt", token=token)[0] == 200
        assert request("/workspace/write", "PUT", {
            "path": "demo.txt", "content": "stale\n",
            "expected_sha256": hashlib.sha256(b"before\n").hexdigest(),
        }, token)[0] == 409
        evidence["file_edit_and_stale_write_rejection"] = True
        (workspace / "probe.py").write_text(
            "import os,socket\nfrom pathlib import Path\n"
            "assert os.getuid()==10001\n"
            "assert Path('demo.txt').read_text()=='after\\n'\n"
            "assert 'GITHUB_MASTER_KEY' not in os.environ\n"
            # Check configured networking without relying on an external site's uptime.
            f"assert (len(Path('/proc/net/route').read_text().splitlines()) > 1) == {settings.SANDBOX_NETWORK_ENABLED!r}\n"
            "Path('shell-result.txt').write_text('sandbox-ok')\nprint('sandbox-ok')\n"
        )
        result = json.loads(await asyncio.to_thread(bash.invoke, {"command": "python probe.py"}))
        assert result["exit_code"] == 0, ("sandbox", result.get("error_code"), result.get("stderr"))
        assert result["cleanup_confirmed"]
        assert (workspace / "shell-result.txt").read_text() == "sandbox-ok"
        result2 = json.loads(await asyncio.to_thread(bash.invoke, {"command": "cat shell-result.txt"}))
        assert result2["exit_code"] == 0 and "sandbox-ok" in result2["stdout"]
        evidence["real_shell"] = {"executor": result["executor"], "cleanup_confirmed": True,
                                  "network_enabled": settings.SANDBOX_NETWORK_ENABLED,
                                  "consecutive_calls_persist": True}
        (workspace / "installed_package.py").write_text("import cowsay\nassert cowsay.__file__.startswith('/opt/python-user/')\nprint('package-reused')\n")
        (workspace / "package.json").write_text('{"name":"deployment-probe","private":true}')
        (workspace / "installed.js").write_text("require('node:assert/strict').equal(require('semver').valid('1.2.3'),'1.2.3')")
        commands = ["python -m pip install --no-deps cowsay==6.1", "python installed_package.py", "npm install --save-exact semver@7.7.2", "node installed.js"]
        evidence["online_package_commands"] = []
        for command in commands:
            outcome = json.loads(await asyncio.to_thread(bash.invoke, {"command": command}))
            assert outcome["exit_code"] == 0 and not outcome.get("error_code"), (command, outcome.get("stderr"))
            assert not (Path(settings.SANDBOX_STAGING_BASE) / outcome["execution_id"]).exists(), "Snapshot cleanup incomplete"
            evidence["online_package_commands"].append({"command": command, "exit_code": outcome["exit_code"], "cleanup_confirmed": outcome["cleanup_confirmed"]})
        if "--live-model" in sys.argv:
            evidence["live_model"] = {}
            for mode in ["single_agent", "multi_agent"]:
                started = time.monotonic()
                status, response = request("/chat/completions", "POST", {
                    "content": "这是本地演示连通性检查。请只回复 DEMO_OK，不要调用任何工具。",
                    "mode": mode, "stream": False,
                }, token)
                evidence["live_model"][mode] = {
                    "http_status": status, "response_nonempty": bool(response.get("content")),
                    "elapsed_seconds": round(time.monotonic() - started, 2),
                }
                assert status == 200 and response.get("content"), ("live_model", mode, status)
                session_id = response["session_id"]
                assert request("/sessions/" + session_id + "/messages", token=token)[0] == 200
                trace_id = response.get("trace_id")
                if trace_id:
                    assert request("/tasks/" + trace_id + "/trace", token=token)[0] == 200
        evidence["snapshots_cleaned_after_online_commands"] = True
        evidence["passed"] = True
    finally:
        if user_id:
            async with async_session_factory() as db:
                await db.execute(delete(User).where(User.id == user_id, User.email == email))
                await db.commit()
            evidence["temporary_account_removed"] = True
        if workspace and workspace.exists():
            from enterprise_agent.sandbox.dependencies import dependency_directory
            from enterprise_agent.sandbox.storage import remove_snapshot
            deps = dependency_directory(workspace)
            if deps.exists():
                remove_snapshot(deps)
            shutil.rmtree(workspace)
        await engine.dispose()
        print(json.dumps(evidence, ensure_ascii=False, indent=2))


asyncio.run(main())
