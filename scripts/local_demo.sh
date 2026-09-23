#!/usr/bin/env bash
# Local-only deployment; never replaces MySQL/Redis or their named volumes.
set -euo pipefail
export DEMO_PROJECT_ROOT
DEMO_PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd -P)
demo_source=${DEMO_SOURCE_ROOT:-"${DEMO_PROJECT_ROOT}"}
demo_env="$DEMO_PROJECT_ROOT/logs/local-demo/runtime.env"
if [[ ! -f "$demo_env" ]]; then
  echo "Missing private local demo configuration: $demo_env" >&2
  exit 1
fi
if [[ "${1:-}" == "build" ]]; then
  python3 "$DEMO_PROJECT_ROOT/enterprise_agent/observability/release.py" \
    --root "$demo_source" --output "$demo_source/enterprise_agent/_build_manifest.json"
fi
exec docker --context desktop-linux compose --project-name docker \
  --env-file "$DEMO_PROJECT_ROOT/.env" --env-file "$demo_env" \
  -f "$demo_source/docker/docker-compose.yml" \
  -f "$DEMO_PROJECT_ROOT/docker/local-demo.yml" "$@"
