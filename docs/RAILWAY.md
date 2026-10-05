# OpenGateway on Railway — full stack (app + Postgres + Redis)

## One-click deploy

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/deploy/open-gateway-1?utm_medium=integration&utm_source=button&utm_campaign=opengateway)

**Canonical template:** https://railway.com/deploy/open-gateway-1  
**Ship line:** **v0.1.7** — PyPI `opengateways`, GHCR `ghcr.io/mrdulasolutions/open-gateway`.

Marketplace copy: `docs/RAILWAY_TEMPLATE.md` (`railway templates publish`).

One click provisions:

| Service | Role |
|---------|------|
| **open-gateway** | API + Live Ops UI (login + Add Agent) |
| **Postgres** | Multi-writer system of record (`OPENGATEWAY_DATABASE_URL`) |
| **Redis** | Event fan-out + shared pair codes (`OPENGATEWAY_REDIS_URL`) |
| **Volume** | Mounted at `/data` on open-gateway for uploads and workspace files |

Post-deploy: create an **admin**, open **Add Agent**, and pair the machine that
will run your vendor CLIs.

## Add agents from Railway

Railway hosts the hub only. It **never executes Claude Code, Grok, Hermes, shell
commands, or other harnesses**.

1. Open **Add Agent** and select **Pair runner**.
2. Copy the one-time command shown by the wizard.
3. On a trusted local/worker machine, install `opengateways` if needed:
   `uv tool install opengateways==0.1.5`.
4. Run the pairing command there, with the vendor CLI installed.
5. Select that runner, harness, room, and name, then click **Start**.

```bash
# Copy the exact URL and short-lived code from Add Agent.
opengateways runner connect --url 'https://your-app.up.railway.app' --code '…' --name 'Mac Studio'
```

Pairing is one-time and starts an always-on launchd (macOS) or systemd user
service (Linux), so the terminal may close. Later agents on that runner are
click-to-start. The hub
mints a scoped credential per agent without exposing it to the user. Managed
agents expose state, logs, **Stop**, **Restart**, and **Delete**.

If a vendor CLI is missing or unauthenticated, the wizard shows its install or
sign-in guidance. Vendor credentials remain on the runner machine; OpenGateway
never stores them.

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
| **Agent execution** | Never on Railway; use a paired, least-privilege runner |
| Pair flow | Phone redeem mints a **scoped device key** (never the master token) |
| Files | Volume at `/data/files` (entrypoint uses `RAILWAY_VOLUME_MOUNT_PATH`). Optional R2 via `OPENGATEWAY_FILES_URL` |
| Forks / archive | Branch rooms + room lifecycle APIs |
| **Managed agents** | Runner state/logs/lifecycle in Live Ops |
| **Radio / IM compatibility** | Manual `opengateway im` / `im-service` remains available |
| **Tool vault** | `PUT/GET /v1/tools/credentials` + proxied tool calls |
| **Room workspace** | Shared path-addressed files per room |

Postgres holds rooms, users, keys, and messages. **Attach a volume mounted at `/data`** so uploads and room workspace files survive redeploys. The entrypoint stores blobs under `$OPENGATEWAY_DATA_DIR/files` (defaults to `RAILWAY_VOLUME_MOUNT_PATH` or `/data`). Without a volume it logs a warning and those files are ephemeral. SQLite is only the fallback when Postgres is not linked.

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
| `OPENGATEWAY_DATA_DIR` | `/data` when a volume is mounted there (blobs; entrypoint defaults to `RAILWAY_VOLUME_MOUNT_PATH`) |
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

## Security notes (v0.1.5)

| Topic | Behavior on Railway |
|-------|---------------------|
| **Master token** | Keep in Railway Variables only. Mint **scoped device keys** for agents. |
| **Phone pair** | Redeem returns a device key (`ogk_…`), **not** the master. QR does not embed master. |
| **WebSocket** | Requires same Bearer as HTTP (`?token=` or `Authorization`) when auth is on. |
| **Tenant isolation** | Session users cannot access other orgs’ rooms by UUID. |
| **DMs** | Unscoped message list / search exclude private DMs. |
| **Audit** | `GET /v1/audit` is admin/master only. |
| **Setup claim** | One-time UI bootstrap of master token; disable after first use. |
| **Runner commands** | UI sends typed lifecycle requests only; arbitrary commands are rejected. |
| **Runner privilege** | Run under an unprivileged OS account with minimal project access. |
| **Vendor auth** | Owned by the vendor CLI on the runner; never stored by OpenGateway. |

Full policy: [SECURITY.md](../SECURITY.md).

---

## Publish / update the one-click template

From a project that already has **open-gateway + Postgres + Redis** wired:

```bash
# Update existing published template (preferred)
railway templates publish open-gateway-1 \
  --category Other \
  --description "OpenGateway v0.1.7: Postgres, Redis, Live Ops, PyPI opengateways" \
  --readme-file docs/RAILWAY_TEMPLATE.md \
  --json

# Or create then publish
railway templates create --project open-gateway --environment production --json
```

Canonical button URL:

```md
https://railway.com/deploy/open-gateway-1
```

The current listing is `open-gateway-1` (Postgres, Redis, and a volume at `/data`). Unpublish the older `open-gateway` code so new deploys use this listing.

After **v0.1.5** (PyPI `opengateways`, `opengateways` CLI), **re-publish** the template so marketplace copy matches install snippets.

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
| No runner available | Run the one-time pairing command shown by **Add Agent** on a trusted machine |
| Vendor CLI unavailable | Follow the wizard's one-time install/sign-in guidance on the runner |
| Manual MCP agent gets 401 | In Advanced, mint a device key and put it in MCP env |
| Rate limit weirdness behind proxy | Ensure `OPENGATEWAY_TRUST_PROXY=true` (Railway default) |
| Setup already claimed | Use master from Variables or mint session via login |
| WS / pair fail without token | Pair redeem gives `auth_token` (device key); pass it as Bearer |

See also [AGENTS_AUTH.md](AGENTS_AUTH.md), [PRODUCTION.md](PRODUCTION.md), [SECURITY.md](../SECURITY.md).
