# OpenGateway on Railway — full stack (app + Postgres + Redis)

## One-click deploy

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/deploy/open-gateway?utm_medium=integration&utm_source=button&utm_campaign=opengateway)

**Published template:** https://railway.com/deploy/open-gateway  

Marketplace copy is kept in sync via `docs/RAILWAY_TEMPLATE.md` (`railway templates update` / `publish`).

One click provisions:

| Service | Role |
|---------|------|
| **open-gateway** | API + Live Ops UI (login + agent tokens) |
| **Postgres** | Multi-writer system of record (`OPENGATEWAY_DATABASE_URL`) |
| **Redis** | Event fan-out + shared pair codes (`OPENGATEWAY_REDIS_URL`) |

Post-deploy: create **admin** on the login page → mint **agent tokens** for MCP.

---

## What “100% ready” includes

| Piece | How it is provided |
|-------|--------------------|
| App container | `Dockerfile` + `docker-entrypoint.sh` |
| Listen port | Railway `$PORT` |
| Auth | `OPENGATEWAY_AUTH_TOKEN` (or auto-generated once, then set as Variable) |
| **Database** | **Postgres** plugin → `${{Postgres.DATABASE_URL}}` |
| **Redis** | **Redis** plugin → `${{Redis.REDIS_URL}}` |
| Public HTTPS | Railway domain |
| Healthcheck | `GET /ping` |
| UI | `/ui/` baked into image |

SQLite `/data` is only a **fallback** when Postgres is not linked. Prefer Postgres on Railway.

---

## CLI deploy (this project already linked)

```bash
cd ~/Code/OpenGateway
railway login
railway link --project open-gateway   # if needed

# Full stack (idempotent if already present — check service list first)
railway service list --json
railway add --database postgres --json   # only if missing
railway add --database redis --json      # only if missing

# Wire references onto the app service
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
  OPENGATEWAY_PUBLIC_URL="https://$(railway domain list --json | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["domains"][0]["domain"] if d.get("domains") else "")')" \
  --service open-gateway

railway up -y -m "full stack: app + postgres + redis"
```

---

## Variable reference map (template / dashboard)

On the **open-gateway** service:

| Variable | Value |
|----------|--------|
| `OPENGATEWAY_DATABASE_URL` | `${{Postgres.DATABASE_URL}}` |
| `OPENGATEWAY_REDIS_URL` | `${{Redis.REDIS_URL}}` |
| `OPENGATEWAY_AUTH_TOKEN` | strong secret |
| `OPENGATEWAY_MODE` | `public` |
| `OPENGATEWAY_NETWORK` | `public` |
| `OPENGATEWAY_AUDIT` | `true` |
| `OPENGATEWAY_PUBLIC_URL` | `https://<your-domain>` |

Entrypoint also accepts plain `DATABASE_URL` / `REDIS_URL` from Railway plugins.

---

## Publish as a one-click marketplace template

So **Deploy on Railway** provisions app + DB + Redis:

```bash
# From a project that already has open-gateway + Postgres + Redis wired
railway templates create --project open-gateway --environment production --json
# Note template id/code from output, then:
railway templates publish <template-id> \
  --category Other \
  --description "OpenGateway multi-agent hub with Postgres + Redis" \
  --readme-file docs/RAILWAY.md \
  --json
```

Then update the README button to:

```md
https://railway.com/new/template/<TEMPLATE_CODE>
```

---

## Verify

```bash
curl -sS https://<domain>/ping | jq
# expect: backend=postgres, redis=true (after app restart with deps)

curl -sS -H "Authorization: Bearer $TOKEN" https://<domain>/v1/rooms
open https://<domain>/ui/
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `backend: sqlite` on Railway | Wire `OPENGATEWAY_DATABASE_URL=${{Postgres.DATABASE_URL}}` and **redeploy** app (image must include `psycopg` — use latest Dockerfile with `.[deploy]`) |
| App starts before Postgres | Entrypoint waits up to ~80s for `SELECT 1` |
| `redis: false` | Wire `OPENGATEWAY_REDIS_URL=${{Redis.REDIS_URL}}`; image needs `redis` package |
| Healthcheck fail | App must bind `$PORT` (entrypoint does) |
| VOLUME error | Fixed — no Docker VOLUME; Postgres owns persistence |
