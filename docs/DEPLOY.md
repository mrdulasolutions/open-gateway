# Deploy OpenGateway (Fly · Railway · Docker · multi-worker)

## One-click style deploys

| Platform | Config | Steps |
|----------|--------|--------|
| **Fly.io** | [`fly.toml`](../fly.toml) | `fly launch` → secrets → `fly deploy` |
| **Railway (1-click)** | [![Deploy](https://railway.com/button.svg)](https://railway.com/deploy/open-gateway) | App + Postgres + Redis · [RAILWAY.md](RAILWAY.md) |
| **Docker** | [`docker-compose.yml`](../docker-compose.yml) | `docker compose up -d` |
| **GHCR image** | CI on `v*` tags | `docker pull ghcr.io/mrdulasolutions/open-gateway:0.1.2` |

Always set a strong token:

```bash
export OPENGATEWAY_AUTH_TOKEN="$(openssl rand -hex 24)"
```

---

## Fly.io

```bash
# Install flyctl, then:
fly apps create opengateway-YOURNAME   # unique
# edit fly.toml app = "..."
fly volumes create og_data --size 1 --region iad
fly secrets set OPENGATEWAY_AUTH_TOKEN="$(openssl rand -hex 24)"
fly deploy
fly open /ui/
```

Paste the token in Live Ops → Settings.  
Public URL is the Fly HTTPS hostname; set `OPENGATEWAY_PUBLIC_URL` if cards look wrong:

```bash
fly secrets set OPENGATEWAY_PUBLIC_URL="https://opengateway-YOURNAME.fly.dev"
```

Optional Redis (multi-machine / multi-process fan-out):

```bash
fly redis create
fly secrets set OPENGATEWAY_REDIS_URL="redis://default:…@….upstash.io:6379"
```

---

## Railway

**One-click (app + Postgres + Redis):** [Deploy on Railway](https://railway.com/deploy/open-gateway) · **[RAILWAY.md](RAILWAY.md)**

Quick post-deploy:

1. Set `OPENGATEWAY_AUTH_TOKEN` (or copy generated token from first-boot logs)
2. Confirm `OPENGATEWAY_DATABASE_URL=${{Postgres.DATABASE_URL}}` and Redis refs
3. Generate domain + set `OPENGATEWAY_PUBLIC_URL`
4. Open `/ui/` → paste token · `GET /ping` should show `"backend":"postgres"`, `"redis":true`

Config: `Dockerfile` installs `.[deploy]` (psycopg + redis + pywebpush); entrypoint waits for Postgres.

---

## GHCR / PyPI (OSS release tags)

Push `v*` → workflow **Publish OSS** ([OSS_RELEASE.md](OSS_RELEASE.md)). PyPI: **`opengateways`** · trusted publisher env **`oss-release`**.

On `git push origin v0.1.2`:

1. **GHCR** — `ghcr.io/mrdulasolutions/open-gateway:0.1.2` and `:latest`
2. **PyPI** — `opengateways` (`uv tool install opengateways==0.1.2`; CLI: `opengateways`, alias `opengateway`)

```bash
docker pull ghcr.io/mrdulasolutions/open-gateway:0.1.2
docker run --rm -p 8765:8765 \
  -e OPENGATEWAY_AUTH_TOKEN="$OPENGATEWAY_AUTH_TOKEN" \
  ghcr.io/mrdulasolutions/open-gateway:0.1.2
```

```bash
uv tool install opengateways==0.1.2
```

---

## Multi-process scale-out (Redis)

| Piece | Role |
|-------|------|
| **SQLite** (`OPENGATEWAY_DB`) | System of record (rooms, messages) — prefer **one writer** or a shared volume with care |
| **Redis** (`OPENGATEWAY_REDIS_URL`) | Event fan-out across workers + shared pair codes |

```bash
uv sync --extra redis
export OPENGATEWAY_REDIS_URL="redis://127.0.0.1:6379/0"
export OPENGATEWAY_AUTH_TOKEN="…"
# example: 2 workers behind a reverse proxy — share DB path + Redis
uvicorn opengateway.server:build_app --factory --host 0.0.0.0 --port 8765 --workers 2
```

Without Redis, each worker only sees its own SSE/wait traffic for in-process events.

---

## Audit log (public mode)

Enabled by default when `require_auth` is on, or force with `OPENGATEWAY_AUDIT=true`.

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://your-hub/v1/audit?limit=50" | jq
```

Sensitive fields (`token`, `password`, …) are redacted. See [SECURITY.md](../SECURITY.md).

---

## Checklist before going public

- [ ] Strong `OPENGATEWAY_AUTH_TOKEN`
- [ ] `OPENGATEWAY_AUDIT=true` (or public mode)
- [ ] HTTPS (Fly/Railway terminate TLS)
- [ ] Volume for `/data` if you care about history
- [ ] Prefer Tailscale / private network over open Funnel when possible
