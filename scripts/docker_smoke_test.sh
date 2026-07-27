#!/usr/bin/env sh

set -eu

COMPOSE_FILE="${COMPOSE_FILE:-docker/docker-compose.yml}"
PROJECT_NAME="${COMPOSE_PROJECT_NAME:-mini-claude-smoke}"
WAIT_TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-300}"
KEEP_STACK="${KEEP_STACK:-0}"

export API_HOST_PORT="${API_HOST_PORT:-18000}"
export FRONTEND_HOST_PORT="${FRONTEND_HOST_PORT:-13000}"
export MYSQL_HOST_PORT="${MYSQL_HOST_PORT:-13307}"
export REDIS_HOST_PORT="${REDIS_HOST_PORT:-16379}"

if [ ! -f .env ]; then
    printf '%s\n' "Missing .env. Copy .env.example and replace placeholder secrets first." >&2
    exit 1
fi

cleanup() {
    if [ "$KEEP_STACK" != "1" ]; then
        docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" down
    fi
}

trap cleanup EXIT INT TERM

docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" config -q
docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" up \
    --build \
    --detach \
    --wait \
    --wait-timeout "$WAIT_TIMEOUT_SECONDS"

curl --fail --silent --show-error "http://127.0.0.1:${API_HOST_PORT}/health"
printf '\n'
curl --fail --silent --show-error "http://127.0.0.1:${FRONTEND_HOST_PORT}/healthz"
printf '\n'
curl --fail --silent --show-error "http://127.0.0.1:${FRONTEND_HOST_PORT}/api/health"
printf '\nDocker smoke test passed for project %s.\n' "$PROJECT_NAME"
