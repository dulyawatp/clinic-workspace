#!/usr/bin/env bash
# Reproducible bring-up of the isolated local SIT stack.
#
# Every command, its exit status and timestamp are appended to the private
# deployment log ($MARLEY_SIT_STATE_DIR/deployment.log, mode 600). Output is
# mirrored to the terminal; no secret is printed by these commands.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
STATE_DIR="${MARLEY_SIT_STATE_DIR:-$HOME/.local/state/marley-sit}"
COMPOSE_FILE="$REPO_ROOT/deploy/local-sit/compose.yaml"
COMPOSE=(docker compose -p marley-sit -f "$COMPOSE_FILE")
LOG="$STATE_DIR/deployment.log"

umask 077
mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"
touch "$LOG"
chmod 600 "$LOG"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"$LOG"; }

run() {
  log "RUN $*"
  set +e
  "$@" 2>&1 | tee -a "$LOG"
  local rc=${PIPESTATUS[0]}
  set -e
  log "EXIT $rc $*"
  if (( rc != 0 )); then
    echo "command failed (exit $rc): $*" >&2
    exit "$rc"
  fi
}

cd "$REPO_ROOT"
log "=== deploy start project=marley-sit site=marley-sit.local origin=http://127.0.0.1:18081 ==="
log "HOST $(uname -sm) NODE $(node --version) PNPM $(pnpm --version) DOCKER $(docker version --format '{{.Server.Version}}') COMPOSE $(docker compose version --short)"

source_rev="$(git -C "$REPO_ROOT" rev-parse --short HEAD)"
if ! git -C "$REPO_ROOT" diff --quiet; then
  source_rev="${source_rev}-dirty"
fi
export CLINIC_SOURCE_REV="$source_rev"
log "SOURCE_REV $CLINIC_SOURCE_REV"

run python3 deploy/local-sit/bin/gen-secrets.py

# Refuse to touch resources this project does not own (read-only check).
run python3 deploy/local-sit/bin/preflight.py

run pnpm --filter @clinic/app build:native

run "${COMPOSE[@]}" config -q

run "${COMPOSE[@]}" build backend

run "${COMPOSE[@]}" up -d

echo "waiting for site creation and provisioning one-shot services..."
run "${COMPOSE[@]}" wait create-site provision

run python3 deploy/local-sit/bin/verify.py

log "=== deploy complete ==="
echo "Deployment verified. Private log: $LOG"
