"""Create an offline acceptance ZIP without executing any Skill code."""

import sys
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1] / "docs/demo-fixtures/skills/quality-demo"
target = Path(sys.argv[1] if len(sys.argv) > 1 else "quality-demo.zip")
with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            archive.write(path, "quality-demo/" + path.relative_to(root).as_posix())
print(target)
