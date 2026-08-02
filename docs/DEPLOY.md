# Deploy OpenGateway (Fly · Railway · Docker · multi-worker)

## One-click style deploys

| Platform | Config | Steps |
|----------|--------|--------|
| **Fly.io** | [`fly.toml`](../fly.toml) | `fly launch` → secrets → `fly deploy` |
| **Railway** | [`railway.toml`](../railway.toml) | Connect GitHub → set token → deploy |
| **Docker** | [`docker-compose.yml`](../docker-compose.yml) | `docker compose up -d` |
| **GHCR image** | CI on `v*` tags | `docker pull ghcr.io/mrdulasolutions/open-gateway:0.0.4` |

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

1. **New Project** → Deploy from GitHub → `open-gateway`
2. **Variables**

   | Name | Value |
   |------|--------|
   | `OPENGATEWAY_AUTH_TOKEN` | `openssl rand -hex 24` |
   | `OPENGATEWAY_MODE` | `public` |
   | `OPENGATEWAY_VIA` | `open` |
   | `OPENGATEWAY_NETWORK` | `public` |
   | `OPENGATEWAY_AUDIT` | `true` |
   | `OPENGATEWAY_DB` | `/data/state.db` |
   | `OPENGATEWAY_PUBLIC_URL` | `https://<your-app>.up.railway.app` (after domain) |

3. **Volume (required for persistence)** — Railway does **not** support Docker `VOLUME` in the Dockerfile.  
   In the service → **Settings → Volumes** → Add volume → mount path **`/data`**.  
   Without a volume, `/data/state.db` is ephemeral (lost on redeploy).
4. Optional: add **Redis** plugin → `OPENGATEWAY_REDIS_URL=${{Redis.REDIS_URL}}`
5. Generate domain → `https://…up.railway.app/ui/` → paste auth token in Settings

`railway.toml` points the builder at the repo `Dockerfile`.

---

## GHCR / PyPI (release tags)

On `git push origin v0.0.4`:

1. **PyPI** — wheel + sdist via Trusted Publishing (configure the `release` environment on GitHub → PyPI)
2. **GHCR** — `ghcr.io/<owner>/open-gateway:<version>` and `:latest`

```bash
docker pull ghcr.io/mrdulasolutions/open-gateway:0.0.4
docker run --rm -p 8765:8765 \
  -e OPENGATEWAY_AUTH_TOKEN="$OPENGATEWAY_AUTH_TOKEN" \
  ghcr.io/mrdulasolutions/open-gateway:0.0.4
```

```bash
pip install opengateway==0.0.4   # after first PyPI publish
# or until then:
uv tool install "git+https://github.com/mrdulasolutions/open-gateway.git@v0.0.4"
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
