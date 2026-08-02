# OpenGateway on Railway — full stack (app + Postgres + Redis)

## One-click deploy

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/deploy/open-gateway?utm_medium=integration&utm_source=button&utm_campaign=opengateway)

**Canonical template:** https://railway.com/deploy/open-gateway  

Marketplace copy: `docs/RAILWAY_TEMPLATE.md` (`railway templates publish`).

One click provisions:

| Service | Role |
|---------|------|
| **open-gateway** | API + Live Ops UI (login + agent tokens) |
| **Postgres** | Multi-writer system of record (`OPENGATEWAY_DATABASE_URL`) |
| **Redis** | Event fan-out + shared pair codes (`OPENGATEWAY_REDIS_URL`) |

Post-deploy: create **admin** on the login page → mint **agent tokens** for MCP.

---

## What “production ready” includes

| Piece | How it is provided |
|-------|--------------------|
| App container | `Dockerfile` + `docker-entrypoint.sh` |
| Listen port | Railway `$PORT` |
| Auth | `OPENGATEWAY_AUTH_TOKEN` as a **Variable** (entrypoint warns if only auto-generated) |
| Public URL | Auto from `RAILWAY_PUBLIC_DOMAIN` when unset |
| Registration | Invite-only after first admin (`OPENGATEWAY_OPEN_REGISTRATION=false`) |
| **Database** | **Postgres** plugin → `${{Postgres.DATABASE_URL}}` |
| **Redis** | **Redis** plugin → `${{Redis.REDIS_URL}}` |
| Public HTTPS | Railway domain |
| Healthcheck | `GET /ping` |
| UI | `/ui/` baked into image |

No Railway Volume required when Postgres is linked. SQLite `/data` is only a fallback.

---

## CLI deploy (this project already linked)

```bash
cd ~/Code/OpenGateway
railway login
railway link --project open-gateway   # if needed

railway service list --json
railway add --database postgres --json   # only if missing
railway add --database redis --json      # only if missing

railway variable set \
  'OPENGATEWAY_DATABASE_URL=${{Postgres.DATABASE_URL}}' \
  'OPENGATEWAY_REDIS_URL=${{Redis.REDIS_URL}}' \
  'DATABASE_URL=${{Postgres.DATABASE_URL}}' \
  'REDIS_URL=${{Redis.REDIS_URL}}' \
  --service open-gateway

railway variable set \
  OPENGATEWAY_AUTH_TOKEN="$(openssl rand -hex 24)" \
  OPENGATEWAY_MODE=public \
  OPENGATEWAY_NETWORK=public \
  OPENGATEWAY_AUDIT=true \
  OPENGATEWAY_OPEN_REGISTRATION=false \
  OPENGATEWAY_DB=none \
  --service open-gateway

# Domain → PUBLIC_URL is auto-derived by entrypoint; optional explicit:
# railway variable set OPENGATEWAY_PUBLIC_URL="https://….up.railway.app" --service open-gateway

railway up -y -m "full stack: app + postgres + redis"
```

---

## Variable reference map (template / dashboard)

On the **open-gateway** service:

| Variable | Value |
|----------|--------|
| `OPENGATEWAY_DATABASE_URL` | `${{Postgres.DATABASE_URL}}` |
| `OPENGATEWAY_REDIS_URL` | `${{Redis.REDIS_URL}}` |
| `OPENGATEWAY_AUTH_TOKEN` | strong secret (**Variable**, not logs-only) |
| `OPENGATEWAY_MODE` | `public` |
| `OPENGATEWAY_NETWORK` | `public` |
| `OPENGATEWAY_AUDIT` | `true` |
| `OPENGATEWAY_OPEN_REGISTRATION` | `false` (invite-only after first admin) |
| `OPENGATEWAY_PUBLIC_URL` | auto from Railway domain, or set explicitly |
| `OPENGATEWAY_DB` | `none` when using Postgres |

Entrypoint also accepts plain `DATABASE_URL` / `REDIS_URL` from Railway plugins.

---

## Publish / update the one-click template

From a project that already has **open-gateway + Postgres + Redis** wired:

```bash
# Update existing published template (preferred)
railway templates publish open-gateway \
  --category Other \
  --description "OpenGateway multi-agent hub: login, Postgres, Redis, agent tokens" \
  --readme-file docs/RAILWAY_TEMPLATE.md \
  --json

# Or create then publish
railway templates create --project open-gateway --environment production --json
```

Canonical button URL:

```md
https://railway.com/deploy/open-gateway
```

Unpublish duplicate codes (e.g. `open-gateway-1`) if present so only one marketplace listing is used.

---

## Verify

```bash
curl -sS https://<domain>/ping | jq
# expect: backend=postgres, redis=true, require_auth=true

curl -sS -H "Authorization: Bearer $TOKEN" https://<domain>/v1/rooms
open https://<domain>/ui/

export OPENGATEWAY_URL=https://<domain>
export OPENGATEWAY_AUTH_TOKEN=$TOKEN
opengateway doctor --skip-network
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Auth changes every deploy | Set `OPENGATEWAY_AUTH_TOKEN` as a Railway Variable |
| Pair QR wrong host | Ensure domain exists; entrypoint sets PUBLIC_URL from `RAILWAY_PUBLIC_DOMAIN` |
| “Registration closed” after admin | Expected — set `OPENGATEWAY_OPEN_REGISTRATION=true` or use invite codes |
| `backend` not postgres | Wire `OPENGATEWAY_DATABASE_URL=${{Postgres.DATABASE_URL}}` and redeploy |
| Agents 401 | Mint device key in UI; put in MCP env (not only hub Variables) |

See also [AGENTS_AUTH.md](AGENTS_AUTH.md), [PRODUCTION.md](PRODUCTION.md).
