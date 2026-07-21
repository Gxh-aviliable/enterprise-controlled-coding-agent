"""Smoke-test entrypoint regression."""

import subprocess
import sys
from pathlib import Path


def test_local_smoke_script_completes_without_external_services():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "smoke_test.py")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert '"status": "ok"' in result.stdout
    assert '"external_services_tested": false' in result.stdout
