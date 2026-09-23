"""Six canonical delegation cases, three paired repeats; all outcomes retained."""

import asyncio
import json
from datetime import datetime, timezone

from benchmarks.run import RESULTS_DIR, ROOT, SUITE_PATH, load_suite, run_suite
from enterprise_agent.observability.release import source_manifest


async def main():
    cases = {c["id"] for c in load_suite(SUITE_PATH)["cases"] if c.get("delegation_suitable")}
    assert len(cases) == 6
    frozen = source_manifest(ROOT)["source_sha256"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = RESULTS_DIR / f"{stamp}-paired-index.json"
    index = {
        "qualification": "diagnostic-snapshot",
        "source_sha256": frozen,
        "design": "6 cases x 3 pairs x 2 modes; order S/M, M/S, S/M; original task budgets/auto approval",
        "runs": [],
        "complete": False,
    }
    for repeat in range(1, 4):
        for mode in ("single", "multi") if repeat % 2 else ("multi", "single"):
            if source_manifest(ROOT)["source_sha256"] != frozen:
                raise RuntimeError("Paired source changed; stop without discarding previous results")
            result = await run_suite(backend="agent", mode=mode, case_ids=cases)
            index["runs"].append(
                {"repeat": repeat, "mode": mode, "artifacts": result["artifact_paths"], "summary": result["summary"]}
            )
            path.write_text(json.dumps(index, indent=2) + "\n")
            print(json.dumps(index["runs"][-1]), flush=True)
    index["complete"] = True
    path.write_text(json.dumps(index, indent=2) + "\n")
    print(path)


if __name__ == "__main__":
    asyncio.run(main())
