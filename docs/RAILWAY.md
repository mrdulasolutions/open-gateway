# OpenGateway on Railway — full journey

## One-click deploy

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/new/template?template=https://github.com/mrdulasolutions/open-gateway&referralCode=opengateway&utm_medium=integration&utm_source=button&utm_campaign=opengateway)

Or open:

```text
https://railway.com/new/template?template=https://github.com/mrdulasolutions/open-gateway
```

That creates a project from this public repo, builds the Dockerfile, and starts the hub.

---

## End-to-end checklist (first time)

| Step | Action | Why |
|------|--------|-----|
| 1 | Click **Deploy on Railway** | Provisions project + service from GitHub |
| 2 | Wait for build | Dockerfile installs Python package + UI |
| 3 | Open **Deploy logs** | First boot prints a generated `OPENGATEWAY_AUTH_TOKEN` if unset |
| 4 | **Variables** → set `OPENGATEWAY_AUTH_TOKEN` to a stable secret | Survives redeploys |
| 5 | **Settings → Volumes** → mount path **`/data`** | SQLite persistence (Docker `VOLUME` is forbidden on Railway) |
| 6 | **Settings → Networking** → Generate domain | Public HTTPS URL |
| 7 | Set `OPENGATEWAY_PUBLIC_URL=https://<your-app>.up.railway.app` | Correct gateway cards / pair QR |
| 8 | Open `https://…/ui/` → paste token in **Settings** | Live Ops loads rooms |

Optional:

| Variable | Purpose |
|----------|---------|
| `OPENGATEWAY_AUDIT=true` | Audit log (default on in image) |
| `OPENGATEWAY_DATABASE_URL` | Postgres instead of SQLite |
| `OPENGATEWAY_REDIS_URL` | Multi-worker event fan-out |
| `OPENGATEWAY_VAPID_PUBLIC` / `_PRIVATE` | Mobile Web Push |

---

## What the image does

```text
docker-entrypoint.sh
  ├─ PORT / OPENGATEWAY_PORT   ← Railway injects PORT
  ├─ host 0.0.0.0              ← accept public traffic
  ├─ mode public + auth token
  ├─ DB /data/state.db         ← mount volume here
  └─ opengateway serve …
```

Health check: `GET /ping` (must return 200).

---

## Agent / CLI deploy (from this laptop)

```bash
# Once
railway login                 # or: railway setup agent -y

cd ~/Code/OpenGateway
railway link                  # pick project, or:
railway up -y -m "deploy OpenGateway"

# Variables
railway variable set OPENGATEWAY_AUTH_TOKEN="$(openssl rand -hex 24)"
railway domain                # attach public domain
railway logs --lines 100
```

Troubleshoot live:

```bash
railway status --json
railway deployment list --json
railway logs --build --lines 200
railway logs --lines 200
```

---

## Common failures

| Error / symptom | Fix |
|-----------------|-----|
| `dockerfile invalid: docker VOLUME … not supported` | Fixed in main — no `VOLUME` in Dockerfile; use Railway Volume at `/data` |
| Build OK, healthcheck fails | App must listen on `$PORT` — entrypoint maps Railway `PORT` |
| 401 on UI | Paste `OPENGATEWAY_AUTH_TOKEN` in Live Ops Settings |
| Data lost on redeploy | Add Volume mounted at `/data` |
| Wrong pair / gateway URL | Set `OPENGATEWAY_PUBLIC_URL` to the Railway HTTPS domain |

---

## Architecture on Railway

```text
Internet
   │ HTTPS
   ▼
Railway edge ──► container :$PORT
                    │
                    ├─ /ui/  Live Ops
                    ├─ /v1/* API (Bearer)
                    └─ /data/state.db  (Volume)
```

Prefer **Postgres** (`OPENGATEWAY_DATABASE_URL`) for multi-replica later; SQLite + single replica is fine for personal hubs.
