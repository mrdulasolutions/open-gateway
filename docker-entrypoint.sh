#!/bin/sh
# Cloud entrypoint (Railway / Fly / Docker) — PORT, Postgres, Redis, auth, PUBLIC_URL.
set -e

export PORT="${PORT:-${OPENGATEWAY_PORT:-8765}}"
export OPENGATEWAY_PORT="$PORT"
export OPENGATEWAY_HOST="${OPENGATEWAY_HOST:-0.0.0.0}"
export OPENGATEWAY_MODE="${OPENGATEWAY_MODE:-public}"
export OPENGATEWAY_VIA="${OPENGATEWAY_VIA:-open}"
export OPENGATEWAY_NETWORK="${OPENGATEWAY_NETWORK:-public}"
export OPENGATEWAY_AUDIT="${OPENGATEWAY_AUDIT:-true}"

# Production default: invite-only after first admin (first user always allowed).
# Set OPENGATEWAY_OPEN_REGISTRATION=true for open team signup demos.
export OPENGATEWAY_OPEN_REGISTRATION="${OPENGATEWAY_OPEN_REGISTRATION:-false}"

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

# Public URL for pair QR, gateway cards, agent copy (Railway injects domain vars)
if [ -z "$OPENGATEWAY_PUBLIC_URL" ]; then
  if [ -n "$RAILWAY_PUBLIC_DOMAIN" ]; then
    export OPENGATEWAY_PUBLIC_URL="https://${RAILWAY_PUBLIC_DOMAIN}"
    echo "OpenGateway PUBLIC_URL from RAILWAY_PUBLIC_DOMAIN: $OPENGATEWAY_PUBLIC_URL"
  elif [ -n "$RAILWAY_STATIC_URL" ]; then
    case "$RAILWAY_STATIC_URL" in
      http://*|https://*) export OPENGATEWAY_PUBLIC_URL="$RAILWAY_STATIC_URL" ;;
      *) export OPENGATEWAY_PUBLIC_URL="https://${RAILWAY_STATIC_URL}" ;;
    esac
    echo "OpenGateway PUBLIC_URL from RAILWAY_STATIC_URL: $OPENGATEWAY_PUBLIC_URL"
  fi
fi

# SQLite fallback only when no Postgres URL (ephemeral on Railway without Postgres)
if [ -z "$OPENGATEWAY_DATABASE_URL" ]; then
  export OPENGATEWAY_DB="${OPENGATEWAY_DB:-/data/state.db}"
  mkdir -p "$(dirname "$OPENGATEWAY_DB")" 2>/dev/null || true
  echo "OpenGateway persistence: SQLite at $OPENGATEWAY_DB (link Postgres for production)"
else
  echo "OpenGateway persistence: Postgres (${OPENGATEWAY_DATABASE_URL%%\?*})"
  # Prefer explicit none when using Postgres so dual-path is clear
  if [ -z "$OPENGATEWAY_DB" ] || [ "$OPENGATEWAY_DB" = "/data/state.db" ]; then
    export OPENGATEWAY_DB=none
  fi
fi

if [ -n "$OPENGATEWAY_REDIS_URL" ]; then
  echo "OpenGateway redis: configured"
else
  echo "OpenGateway redis: not set (pair codes / multi-replica fan-out limited to one instance)"
fi

# Master token: must live in Railway Variables to survive redeploys
_AUTH_FROM_ENV=0
if [ -n "$OPENGATEWAY_AUTH_TOKEN" ]; then
  _AUTH_FROM_ENV=1
else
  OPENGATEWAY_AUTH_TOKEN="$(openssl rand -hex 24 2>/dev/null || python3 -c 'import secrets; print(secrets.token_hex(24))')"
  export OPENGATEWAY_AUTH_TOKEN
  echo "============================================================"
  echo "WARNING: OPENGATEWAY_AUTH_TOKEN was NOT set as a Railway Variable."
  echo "Generated a one-time master token for this boot only:"
  echo "  $OPENGATEWAY_AUTH_TOKEN"
  echo ""
  echo "Save it NOW, then set Railway Variable OPENGATEWAY_AUTH_TOKEN"
  echo "so redeploys keep the same secret (otherwise agents/MCP break)."
  echo "============================================================"
fi

if [ "$_AUTH_FROM_ENV" = "1" ]; then
  echo "OpenGateway auth: OPENGATEWAY_AUTH_TOKEN loaded from environment (stable across redeploys)"
fi

echo "OpenGateway registration: OPENGATEWAY_OPEN_REGISTRATION=$OPENGATEWAY_OPEN_REGISTRATION"
if [ -n "$OPENGATEWAY_PUBLIC_URL" ]; then
  echo "OpenGateway public_url: $OPENGATEWAY_PUBLIC_URL"
else
  echo "OpenGateway public_url: (unset — pair QR / cards may use request host)"
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
