"""Run inside the trusted API image, with only temporary staging and source mounted.

This is a deployment path/UID smoke probe, not an execution image entrypoint.
"""

import json
import os

from enterprise_agent.core.agent.tools.shell import bash
from enterprise_agent.core.agent.tools.workspace import get_user_workspace, set_current_user_id

set_current_user_id(7001)
workspace = get_user_workspace()
(workspace / "probe.py").write_text(
    "from pathlib import Path\nPath('result.txt').write_text('container-controller-ok')\n"
)
result = json.loads(bash.invoke({"command": "python probe.py"}))
assert os.getuid() == 10001
assert result["exit_code"] == 0 and result["cleanup_confirmed"], result
assert (workspace / "result.txt").read_text() == "container-controller-ok"
assert (workspace / "result.txt").stat().st_uid == 10001
print(
    json.dumps(
        {
            "controller_uid": os.getuid(),
            "published_file_uid": 10001,
            "daemon_path_mapping": "verified",
            "result": result,
        },
        indent=2,
    )
)
