# OpenGateway on Railway — full stack (app + Postgres + Redis)

## One-click deploy

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/deploy/open-gateway?utm_medium=integration&utm_source=button&utm_campaign=opengateway)

**Canonical template:** https://railway.com/deploy/open-gateway  
**Ship line:** **v0.1.0** — re-publish after hub-core + OSS-first UI land on `main`.

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
| Trust proxy | Auto `OPENGATEWAY_TRUST_PROXY=true` on Railway (X-Forwarded-For rate limits) |
| OpenAPI | Closed under auth (`OPENGATEWAY_PUBLIC_DOCS=false`) |
| Registration | Invite-only after first admin (`OPENGATEWAY_OPEN_REGISTRATION=false`) |
| **Database** | **Postgres** plugin → `${{Postgres.DATABASE_URL}}` |
| **Redis** | **Redis** plugin → `${{Redis.REDIS_URL}}` |
| Public HTTPS | Railway domain |
| Healthcheck | `GET /ping` |
| UI | `/ui/` baked into image |
| Pair flow | Phone redeem mints a **scoped device key** (never the master token) |
| Files | Disk under the container (+ optional R2 via `OPENGATEWAY_FILES_URL`) |
| Forks / archive | Branch rooms + room lifecycle APIs |
| **Radio / IM** | Always-on presence; `opengateway im` + optional `im-service` |
| **Tool vault** | `PUT/GET /v1/tools/credentials` + proxied tool calls |
| **Room workspace** | Shared path-addressed files per room |

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
  OPENGATEWAY_TRUST_PROXY=true \
  OPENGATEWAY_PUBLIC_DOCS=false \
  --service open-gateway

# Domain → PUBLIC_URL is auto-derived by entrypoint; optional explicit:
# railway variable set OPENGATEWAY_PUBLIC_URL="https://….up.railway.app" --service open-gateway

# After first UI setup claim (if used):
# railway variable set OPENGATEWAY_DISABLE_SETUP_CLAIM=true --service open-gateway

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
| `OPENGATEWAY_TRUST_PROXY` | `true` on Railway (entrypoint default when `RAILWAY_*` present) |
| `OPENGATEWAY_PUBLIC_DOCS` | `false` — keep `/docs` private under auth |
| `OPENGATEWAY_DISABLE_SETUP_CLAIM` | `true` after first-run UI bootstrap (optional) |

Entrypoint also accepts plain `DATABASE_URL` / `REDIS_URL` from Railway plugins.

### Optional advanced

| Variable | Purpose |
|----------|---------|
| `OPENGATEWAY_FILES_URL` | If set with master token, file_store can PUT/GET R2-style durable blobs |
| `OPENGATEWAY_SESSION_DAYS` | Session TTL (default 30) |
| `OPENGATEWAY_CORS_ORIGINS` | Comma-separated origins (avoid `*` with credentialed browsers) |

---

## Security notes (v0.1.0)

| Topic | Behavior on Railway |
|-------|---------------------|
| **Master token** | Keep in Railway Variables only. Mint **scoped device keys** for agents. |
| **Phone pair** | Redeem returns a device key (`ogk_…`), **not** the master. QR does not embed master. |
| **WebSocket** | Requires same Bearer as HTTP (`?token=` or `Authorization`) when auth is on. |
| **Tenant isolation** | Session users cannot access other orgs’ rooms by UUID. |
| **DMs** | Unscoped message list / search exclude private DMs. |
| **Audit** | `GET /v1/audit` is admin/master only. |
| **Setup claim** | One-time UI bootstrap of master token; disable after first use. |

Full policy: [SECURITY.md](../SECURITY.md).

---

## Publish / update the one-click template

From a project that already has **open-gateway + Postgres + Redis** wired:

```bash
# Update existing published template (preferred)
railway templates publish open-gateway \
  --category Other \
  --description "OpenGateway v0.1.0: Postgres, Redis, radio/IM, agent tokens" \
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

After merging **v0.1.0** (hub-core + radio/IM + OSS-first Live Ops), **re-publish** the template so marketplace deploys pick up the new Dockerfile defaults and marketplace copy.

---

## Verify

```bash
curl -sS https://<domain>/ping | jq
# expect: backend=postgres, redis=true, require_auth=true

curl -sS -H "Authorization: Bearer $TOKEN" https://<domain>/v1/rooms
# /docs should be 401 when auth is on (unless PUBLIC_DOCS=true)
curl -sS -o /dev/null -w '%{http_code}\n' https://<domain>/docs

open https://<domain>/ui/

export OPENGATEWAY_URL=https://<domain>
export OPENGATEWAY_AUTH_TOKEN=$TOKEN
opengateway doctor --skip-network

# Or full smoke:
BASE=https://<domain> TOKEN=$TOKEN ./scripts/railway-smoke.sh
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
| Rate limit weirdness behind proxy | Ensure `OPENGATEWAY_TRUST_PROXY=true` (Railway default) |
| Setup already claimed | Use master from Variables or mint session via login |
| WS / pair fail without token | Pair redeem gives `auth_token` (device key); pass it as Bearer |

See also [AGENTS_AUTH.md](AGENTS_AUTH.md), [PRODUCTION.md](PRODUCTION.md), [SECURITY.md](../SECURITY.md).
