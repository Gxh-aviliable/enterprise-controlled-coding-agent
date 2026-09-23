"""Trusted independent TTL janitor. Only touches this deployment's labeled runs."""

import json
import logging
import os
import time
from pathlib import Path

from enterprise_agent.sandbox.engine import Engine
from enterprise_agent.sandbox.storage import remove_snapshot

logger = logging.getLogger(__name__)


def reap(engine, deployment, staging_root=None):
    removed = []
    containers = engine.containers(deployment)
    live_names = set()
    for container in containers:
        labels = container.get("Labels", {})
        name = labels.get("enterprise.execution", "")
        live_names.add(name)
        if labels.get("enterprise.sandbox") != deployment:
            continue
        try:
            expired = float(labels["enterprise.deadline"]) < time.time()
        except (KeyError, ValueError):
            continue
        # Active executors need exited containers briefly for logs/exit status.
        if expired:
            engine.remove(container["Id"])
            removed.append(container["Id"])
            live_names.discard(name)
    if staging_root:
        root = Path(staging_root)
        if root.exists() and not root.is_symlink():
            for entry in root.iterdir():
                # Only UUID directories created by this executor, after 24 hours.
                suffix = entry.name.removeprefix("agent-sandbox-")
                if (
                    entry.name.startswith("agent-sandbox-")
                    and len(suffix) == 32
                    and all(c in "0123456789abcdef" for c in suffix)
                    and entry.name not in live_names
                    and not entry.is_symlink()
                    and entry.is_dir()
                    and time.time() - entry.stat().st_mtime > 86400
                ):
                    remove_snapshot(entry)
    return removed


def main():
    logging.basicConfig(level=logging.INFO)
    engine = Engine(os.getenv("SANDBOX_DOCKER_SOCKET", "/var/run/docker.sock"))
    deployment = os.getenv("SANDBOX_DEPLOYMENT", "enterprise-agent")
    while True:
        try:
            removed = reap(engine, deployment, os.getenv("SANDBOX_STAGING_BASE"))
            if removed:
                logger.info("Sandbox resources reclaimed: %s", json.dumps(removed))
        except Exception:
            logger.exception("Sandbox reaper failed; retrying in 5 seconds")
        time.sleep(5)


if __name__ == "__main__":
    main()
