#!/usr/bin/env bash
# Post-deploy smoke for OpenGateway on Railway (or any public hub).
# Usage:
#   BASE=https://….up.railway.app TOKEN=… ./scripts/railway-smoke.sh
# Exit 0 = healthy, 1 = fail.
set -euo pipefail

BASE="${BASE:-${OPENGATEWAY_URL:-}}"
TOKEN="${TOKEN:-${OPENGATEWAY_AUTH_TOKEN:-}}"
BASE="${BASE%/}"

if [[ -z "$BASE" ]]; then
  echo "BASE or OPENGATEWAY_URL required" >&2
  exit 1
fi

echo "== ping $BASE/ping =="
PING="$(curl -fsS --max-time 20 "$BASE/ping")"
echo "$PING" | python3 -m json.tool 2>/dev/null || echo "$PING"
echo "$PING" | python3 -c "
import sys, json
d=json.load(sys.stdin)
assert d.get('status')=='ok', d
print('version', d.get('version'), 'backend', d.get('backend') or d.get('db_path'), 'redis', d.get('redis'), 'require_auth', d.get('require_auth'))
"

echo "== unauth /v1/rooms (expect 401 if require_auth) =="
CODE="$(curl -sS -o /tmp/og-rooms-bare.json -w '%{http_code}' --max-time 20 "$BASE/v1/rooms" || true)"
echo "HTTP $CODE"
if [[ "$CODE" != "401" && "$CODE" != "200" ]]; then
  echo "unexpected status for bare rooms" >&2
  cat /tmp/og-rooms-bare.json >&2 || true
  exit 1
fi

if [[ -n "$TOKEN" ]]; then
  echo "== authed /v1/rooms =="
  curl -fsS --max-time 20 -H "Authorization: Bearer $TOKEN" "$BASE/v1/rooms" | python3 -m json.tool | head -40
  echo "== authed /v1/auth/status =="
  curl -fsS --max-time 20 -H "Authorization: Bearer $TOKEN" "$BASE/v1/auth/status" | python3 -m json.tool || \
    curl -fsS --max-time 20 "$BASE/v1/auth/status" | python3 -m json.tool
else
  echo "(skip authed checks — no TOKEN / OPENGATEWAY_AUTH_TOKEN)"
fi

echo "== UI index =="
UI_CODE="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 "$BASE/ui/" || true)"
echo "GET /ui/ → HTTP $UI_CODE"
if [[ "$UI_CODE" != "200" && "$UI_CODE" != "301" && "$UI_CODE" != "302" ]]; then
  echo "UI not serving" >&2
  exit 1
fi

echo "OK — smoke passed for $BASE"
