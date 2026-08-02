#!/bin/sh
# Railway / Fly / Docker entrypoint — honors $PORT, generates token if missing.
set -e

# Cloud platforms inject PORT; fall back to OPENGATEWAY_PORT or 8765
export PORT="${PORT:-${OPENGATEWAY_PORT:-8765}}"
export OPENGATEWAY_PORT="$PORT"
export OPENGATEWAY_HOST="${OPENGATEWAY_HOST:-0.0.0.0}"
export OPENGATEWAY_MODE="${OPENGATEWAY_MODE:-public}"
export OPENGATEWAY_VIA="${OPENGATEWAY_VIA:-open}"
export OPENGATEWAY_NETWORK="${OPENGATEWAY_NETWORK:-public}"
export OPENGATEWAY_DB="${OPENGATEWAY_DB:-/data/state.db}"
export OPENGATEWAY_AUDIT="${OPENGATEWAY_AUDIT:-true}"

# Ensure data directory exists (Railway Volume should mount at /data)
mkdir -p "$(dirname "$OPENGATEWAY_DB")" 2>/dev/null || true

if [ -z "$OPENGATEWAY_AUTH_TOKEN" ]; then
  # Ephemeral token for first boot — set a stable secret in Railway Variables for production
  OPENGATEWAY_AUTH_TOKEN="$(openssl rand -hex 24 2>/dev/null || python3 -c 'import secrets; print(secrets.token_hex(24))')"
  export OPENGATEWAY_AUTH_TOKEN
  echo "============================================================"
  echo "OpenGateway generated OPENGATEWAY_AUTH_TOKEN (save this):"
  echo "  $OPENGATEWAY_AUTH_TOKEN"
  echo "Set it as a Railway Variable so it survives redeploys."
  echo "============================================================"
fi

# If caller passed args (e.g. docker CMD override), run them
if [ "$#" -gt 0 ] && [ "$1" != "opengateway" ] && [ "$1" != "serve" ]; then
  exec "$@"
fi

exec opengateway serve \
  --mode "$OPENGATEWAY_MODE" \
  --via "$OPENGATEWAY_VIA" \
  --network "$OPENGATEWAY_NETWORK" \
  --host "$OPENGATEWAY_HOST" \
  --port "$PORT" \
  --token "$OPENGATEWAY_AUTH_TOKEN"
