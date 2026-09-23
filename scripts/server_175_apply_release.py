"""Apply the prepared 20260914 release on the existing 175 server only.

Run with `python3 server_175_apply_release.py --apply` after image validation.
No credentials or business rows are printed. Backups stay on the server.
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

OLD = Path("/home/ubuntu/mini-claude-code")
NEW = Path("/home/ubuntu/mini-claude-releases/20260914-github-mvp")
BACKUP = Path("/home/ubuntu/mini-claude-backup-20260914-github-mvp")


def compose(root, *args, **kwargs):
    command = ["docker", "compose", "--env-file", ".env", "-p", "mini-claude",
               "-f", "docker/docker-compose.yml", "-f", "docker/server.yml"]
    if root == NEW:
        command += ["-f", "docker/github-server.yml"]
    return subprocess.run(command + list(args), cwd=root, check=True, **kwargs)


def active_tasks():
    script = """import asyncio,json
from enterprise_agent.db.redis import redis_client
async def main():
 keys=[k async for k in redis_client.scan_iter(match='agent:active-session:*',count=100)]
 print(json.dumps({'active_sessions':len(keys)}))
 await redis_client.aclose()
asyncio.run(main())
"""
    result = subprocess.run(
        ["docker", "exec", "-i", "mini-claude-api-1", "/app/.venv/bin/python", "-"],
        input=script, text=True, capture_output=True, check=True,
    )
    assert json.loads(result.stdout)["active_sessions"] == 0, "Active sessions; leave old release running"
    executions = subprocess.check_output([
        "docker", "ps", "-q", "--filter", "label=enterprise.sandbox=mini-claude-175",
    ]).strip()
    assert not executions, "Active execution containers; leave old release running"


def backup_stream(name, command):
    path = BACKUP / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        result = subprocess.run(command, stdout=handle, stderr=subprocess.PIPE)
    assert result.returncode == 0, "Backup failed: " + name
    assert path.stat().st_size > 0
    return {"file": name, "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def schema_preflight():
    script = """import asyncio,json
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect
import enterprise_agent.models
from enterprise_agent.db.mysql import engine,Base
engine.echo=False
async def main():
 async with engine.connect() as c:
  def verify(conn):
   diffs=compare_metadata(MigrationContext.configure(conn,opts={'compare_type':True}),Base.metadata)
   allowed={('users','email'),('users','username'),('managed_shared_skills','name')}
   found=[]
   for diff in diffs:
    assert isinstance(diff,tuple) and diff[0]=='remove_index','Unexpected schema drift; stop before downtime'
    index=diff[1];key=(index.table.name,index.name)
    assert key in allowed,'Unexpected index drift; stop before downtime'
    indexes={i['name']:i for i in inspect(conn).get_indexes(index.table.name)}
    canonical=indexes.get('ix_'+index.table.name+'_'+index.name)
    assert canonical and canonical['unique'] and canonical['column_names']==[index.name]
    assert index.unique and [col.name for col in index.columns]==[index.name]
    found.append(key)
   return found
  print('SCHEMA_PREFLIGHT',json.dumps(await c.run_sync(verify)))
 await engine.dispose()
asyncio.run(main())
"""
    compose(NEW, "run", "--rm", "--no-deps", "--entrypoint", "/app/.venv/bin/python", "api", "-c", script)


def apply():
    assert sys.argv[1:] == ["--apply"], "Explicit --apply required"
    assert OLD.is_dir() and NEW.is_dir() and BACKUP.is_dir()
    assert not (BACKUP / "database.sql").exists(), "Prior cutover attempt exists; inspect it before retrying"
    manifest = json.loads((NEW / "release-manifest.json").read_text())
    assert all(hashlib.sha256((NEW / name).read_bytes()).hexdigest() == digest
               for name, digest in manifest["files"].items()), "Release source changed after validation"
    schema_preflight()
    active_tasks()
    api_stopped = False
    migration_attempted = False
    backups = []
    try:
        compose(OLD, "stop", "frontend")
        active_tasks()  # Close the public entry before the final activity check.
        compose(OLD, "stop", "api", "sandbox-reaper")
        api_stopped = True
        backups.append(backup_stream("database.sql", [
            "docker", "exec", "mini-claude-mysql-1", "sh", "-c",
            'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -uroot --single-transaction '
            '--quick --skip-lock-tables --no-tablespaces --set-gtid-purged=OFF '
            '--hex-blob --databases enterprise_agent',
        ]))
        subprocess.run(["docker", "exec", "mini-claude-redis-1", "redis-cli", "SAVE"], check=True)
        redis_values = {}
        for key in ["dir", "dbfilename"]:
            lines = subprocess.check_output([
                "docker", "exec", "mini-claude-redis-1", "redis-cli", "--raw", "CONFIG", "GET", key,
            ], text=True).splitlines()
            assert len(lines) == 2 and lines[0] == key, "Cannot resolve Redis snapshot configuration"
            redis_values[key] = lines[1]
        redis_dir = Path(redis_values["dir"])
        filename = redis_values["dbfilename"]
        assert redis_dir.is_absolute() and Path(filename).name == filename
        redis_snapshot = "mini-claude-redis-1:" + str(redis_dir / filename)
        subprocess.run(["docker", "cp", redis_snapshot, str(BACKUP / "redis.rdb")],
                       check=True)
        (BACKUP / "redis.rdb").chmod(0o600)
        backups.append(backup_stream("workspaces-and-memory.tar.gz", [
            "docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--memory", "128m", "--pids-limit", "32",
            "--mount", "type=volume,source=mini-claude_workspace_data,target=/data/workspaces,readonly",
            "--mount", "type=volume,source=mini-claude_chroma_data,target=/data/chroma,readonly",
            "--mount", "type=volume,source=mini-claude_managed_shared_skills,target=/data/shared-skills,readonly",
            "--entrypoint", "tar", "mini-claude-api:20260911-sandbox",
            "-czf", "-", "-C", "/data", "workspaces", "chroma", "shared-skills",
        ]))
        (BACKUP / "backup-manifest.json").write_text(json.dumps(backups, indent=2) + "\n")
        print("Consistent pre-cutover backups complete", flush=True)
        migration_attempted = True
        compose(NEW, "run", "--rm", "--no-deps", "--entrypoint", "/bin/sh", "api", "-c",
                "/app/.venv/bin/alembic upgrade head && /app/.venv/bin/alembic check")
        compose(NEW, "up", "-d", "--no-build", "--no-deps", "--wait", "--wait-timeout", "180",
                "api", "frontend", "sandbox-reaper")
        subprocess.run(["curl", "--fail", "--max-time", "15", "http://127.0.0.1:8082/api/health"], check=True)
        subprocess.run(["docker", "exec", "mini-claude-api-1", "/app/.venv/bin/alembic", "current"], check=True)
        link = Path("/home/ubuntu/mini-claude-current")
        assert not link.exists() or link.is_symlink(), "Current release alias is not a symlink"
        temporary_link = link.with_name("mini-claude-current.pending")
        temporary_link.symlink_to(NEW, target_is_directory=True)
        temporary_link.replace(link)
        print("CUTOVER_SUCCEEDED", str(NEW), flush=True)
    except BaseException:
        print("Cutover failed; restoring the previous application configuration", flush=True)
        if api_stopped:
            if migration_attempted:
                # This revision changes indexes only; never restore the whole database automatically.
                compose(NEW, "run", "--rm", "--no-deps", "--entrypoint", "/app/.venv/bin/alembic",
                        "api", "downgrade", "20260819_0004")
            compose(OLD, "up", "-d", "--no-build", "--no-deps", "--wait", "--wait-timeout", "180",
                    "api", "frontend", "sandbox-reaper")
        else:
            compose(OLD, "start", "frontend")
        raise


if __name__ == "__main__":
    apply()
