#!/usr/bin/env bash
# Trusted control-plane deployment probe. Only a new temporary directory is chowned.
set -euo pipefail
project_root=$(cd "$(dirname "$0")/.." && pwd -P)
probe_output=${1:-/tmp/sandbox-controller.json}
probe_socket=${SANDBOX_DOCKER_SOCKET:-/var/run/docker.sock}
probe_image=${SANDBOX_API_TEST_IMAGE:-enterprise-agent-sandbox-api:verified-overlay}
probe_dir=$(mktemp -d "${TMPDIR:-/tmp}/sandbox-controller.XXXXXX")
probe_dir=$(cd "$probe_dir" && pwd -P)
probe_uid=$(id -u)
probe_gid=$(id -g)
cleanup_probe() {
  docker run --rm --user 0:0 --network none --read-only --cap-drop ALL \
    --cap-add CHOWN --cap-add FOWNER --memory 64m --pids-limit 32 \
    --mount "type=bind,source=$probe_dir,target=/probe" \
    --entrypoint /bin/chown enterprise-agent-sandbox:1 -R "$probe_uid:$probe_gid" /probe
  rmdir "$probe_dir"
}
trap cleanup_probe EXIT

docker run --rm --user 0:0 --network none --read-only --cap-drop ALL --cap-add CHOWN \
  --memory 64m --pids-limit 32 --mount "type=bind,source=$probe_dir,target=/probe" \
  --entrypoint /bin/chown enterprise-agent-sandbox:1 10001:10001 /probe

docker run --rm --user 10001:10001 --group-add "${DOCKER_SOCKET_GID:-0}" \
  --read-only --cap-drop ALL --security-opt no-new-privileges --network none \
  --memory 512m --pids-limit 128 --tmpfs /tmp:rw,size=128m \
  --mount "type=bind,source=$probe_dir,target=/sandbox-staging" \
  --mount "type=bind,source=$probe_socket,target=/var/run/docker.sock" \
  --mount "type=bind,source=$project_root/tests/sandbox/container_controller_probe.py,target=/app/probe.py,readonly" \
  -e AGENT_EXECUTOR=docker -e WORKSPACE_BASE=/tmp/workspaces \
  -e SANDBOX_DEPLOYMENT=controller-probe -e SANDBOX_STAGING_BASE=/sandbox-staging \
  -e "SANDBOX_HOST_STAGING_BASE=$probe_dir" -e SANDBOX_UID=10001 -e SANDBOX_GID=10001 \
  --entrypoint /app/.venv/bin/python "$probe_image" /app/probe.py > "$probe_output"
printf 'PASS: UID 10001 API container, daemon path mapping and published ownership. Evidence: %s\n' "$probe_output"
