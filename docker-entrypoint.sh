#!/bin/sh
# Cloud entrypoint (Railway / Fly / Docker) — PORT, Postgres, Redis, auth.
set -e

export PORT="${PORT:-${OPENGATEWAY_PORT:-8765}}"
export OPENGATEWAY_PORT="$PORT"
export OPENGATEWAY_HOST="${OPENGATEWAY_HOST:-0.0.0.0}"
export OPENGATEWAY_MODE="${OPENGATEWAY_MODE:-public}"
export OPENGATEWAY_VIA="${OPENGATEWAY_VIA:-open}"
export OPENGATEWAY_NETWORK="${OPENGATEWAY_NETWORK:-public}"
export OPENGATEWAY_AUDIT="${OPENGATEWAY_AUDIT:-true}"

# Prefer managed Postgres (Railway template / plugin)
# Railway refs: OPENGATEWAY_DATABASE_URL=${{Postgres.DATABASE_URL}} or DATABASE_URL
if [ -n "$OPENGATEWAY_DATABASE_URL" ]; then
  :
elif [ -n "$DATABASE_URL" ]; then
  export OPENGATEWAY_DATABASE_URL="$DATABASE_URL"
elif [ -n "$POSTGRES_URL" ]; then
  export OPENGATEWAY_DATABASE_URL="$POSTGRES_URL"
fi

# Prefer managed Redis
if [ -z "$OPENGATEWAY_REDIS_URL" ] && [ -n "$REDIS_URL" ]; then
  export OPENGATEWAY_REDIS_URL="$REDIS_URL"
fi

# SQLite fallback only when no Postgres URL
if [ -z "$OPENGATEWAY_DATABASE_URL" ]; then
  export OPENGATEWAY_DB="${OPENGATEWAY_DB:-/data/state.db}"
  mkdir -p "$(dirname "$OPENGATEWAY_DB")" 2>/dev/null || true
else
  # Avoid confusing dual-path: leave OPENGATEWAY_DB alone but log backend
  echo "OpenGateway persistence: Postgres (${OPENGATEWAY_DATABASE_URL%%\?*})"
fi

if [ -n "$OPENGATEWAY_REDIS_URL" ]; then
  echo "OpenGateway redis: configured"
fi

if [ -z "$OPENGATEWAY_AUTH_TOKEN" ]; then
  OPENGATEWAY_AUTH_TOKEN="$(openssl rand -hex 24 2>/dev/null || python3 -c 'import secrets; print(secrets.token_hex(24))')"
  export OPENGATEWAY_AUTH_TOKEN
  echo "============================================================"
  echo "OpenGateway generated OPENGATEWAY_AUTH_TOKEN (save this):"
  echo "  $OPENGATEWAY_AUTH_TOKEN"
  echo "Set it as a Railway Variable so it survives redeploys."
  echo "============================================================"
fi

# Wait for Postgres if URL is set (template race on first boot)
if [ -n "$OPENGATEWAY_DATABASE_URL" ]; then
  echo "Waiting for Postgres…"
  i=0
  while [ "$i" -lt 40 ]; do
    if python3 - <<'PY'
import os, sys
url = os.environ.get("OPENGATEWAY_DATABASE_URL", "")
try:
    import psycopg
    with psycopg.connect(url, connect_timeout=3) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
    sys.exit(0)
except Exception as e:
    sys.stderr.write(f"  postgres not ready: {e}\n")
    sys.exit(1)
PY
    then
      echo "Postgres ready."
      break
    fi
    i=$((i + 1))
    sleep 2
  done
fi

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
