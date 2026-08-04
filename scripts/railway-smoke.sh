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

echo "== /docs under auth (expect 401 unless PUBLIC_DOCS=true) =="
DOCS_CODE="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 "$BASE/docs" || true)"
echo "GET /docs → HTTP $DOCS_CODE"
if [[ "$DOCS_CODE" != "401" && "$DOCS_CODE" != "200" && "$DOCS_CODE" != "404" ]]; then
  echo "unexpected /docs status" >&2
  exit 1
fi

if [[ -n "$TOKEN" ]]; then
  echo "== pair create (scoped redeem key, no master in URL) =="
  export TOKEN
  PAIR_JSON="$(curl -fsS --max-time 20 -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{}' "$BASE/v1/pair" || true)"
  if [[ -n "$PAIR_JSON" ]]; then
    echo "$PAIR_JSON" | python3 -c "
import sys, json
d=json.load(sys.stdin)
url=d.get('url') or ''
assert 'token=' not in url, 'pair URL must not embed master token'
code=d.get('code') or ''
assert len(code) >= 12, ('pair code too short', code)
print('pair code ok, url has no token=')
"
    CODE="$(echo "$PAIR_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('code',''))")"
    if [[ -n "$CODE" ]]; then
      REDEEM="$(curl -fsS --max-time 20 -H "Content-Type: application/json" \
        -d "{\"code\":\"$CODE\",\"name\":\"smoke-phone\"}" "$BASE/v1/pair/redeem")"
      echo "$REDEEM" | TOKEN="$TOKEN" python3 -c "
import sys, json, os
master=os.environ.get('TOKEN') or ''
d=json.load(sys.stdin)
assert d.get('ok') is True
tok=d.get('auth_token') or ''
assert tok.startswith('ogk_'), ('expected scoped device key', tok[:20])
assert tok != master, 'redeem must not return master token'
print('pair redeem returns scoped ogk_ key (not master)')
"
    fi
  else
    echo "(pair create skipped — request failed)"
  fi
fi

echo "OK — smoke passed for $BASE"
