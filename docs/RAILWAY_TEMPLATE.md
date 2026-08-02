# Deploy and Host OpenGateway on Railway

One-click multi-agent collaboration hub: **Live Ops UI**, **email/password login**, **Postgres**, and **Redis**.

**Template:** https://railway.com/deploy/open-gateway

## About Hosting

OpenGateway is a multi-agent room server (ACP + MCP) with a web console. This template provisions:

| Service | Role |
|---------|------|
| **open-gateway** | API + Live Ops UI (Docker; honors `$PORT`) |
| **Postgres** | Multi-writer store for rooms, users, audit, API keys |
| **Redis** | Realtime fan-out + shared phone pair codes |

### What you get after deploy

- Public HTTPS domain on Railway  
- **Login page** — first user creates the org and becomes **admin**  
- **Invite-only** multi-user by default (open registration optional)  
- **Agent tokens** UI for Grok / Claude / Cursor MCP  
- Health: `GET /ping` · UI: `/ui/` · API: `/v1/*`  
- `OPENGATEWAY_PUBLIC_URL` auto-filled from Railway domain when unset  

## Why Deploy

- Always-on hub without managing a VPS  
- Team Live Ops over HTTPS with real accounts (no paste-token for day-to-day)  
- Durable Postgres + Redis out of the box  
- Agents connect via scoped API keys after you mint them in the UI  

## Common Use Cases

- Shared room for Grok, Claude Code, Cursor, Codex, and humans  
- Phone pair + Live Ops on the go  
- Lab / startup multi-agent coordination server  
- Public demo of multi-agent collaboration  

## Dependencies for OpenGateway

### Deployment Dependencies

| Service | Required | Purpose |
|---------|----------|---------|
| open-gateway | Yes | Application |
| Postgres | Yes | `OPENGATEWAY_DATABASE_URL` = `${{Postgres.DATABASE_URL}}` |
| Redis | Yes (recommended) | `OPENGATEWAY_REDIS_URL` = `${{Redis.REDIS_URL}}` |

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENGATEWAY_AUTH_TOKEN` | **Yes** | Master Bearer secret — set as a Railway Variable so it **survives redeploys** (entrypoint generates one only if missing and logs it once) |
| `OPENGATEWAY_DATABASE_URL` | Yes | `${{Postgres.DATABASE_URL}}` |
| `OPENGATEWAY_REDIS_URL` | Yes | `${{Redis.REDIS_URL}}` |
| `OPENGATEWAY_PUBLIC_URL` | Auto | Defaults to `https://$RAILWAY_PUBLIC_DOMAIN` when unset |
| `OPENGATEWAY_MODE` | Optional | default `public` |
| `OPENGATEWAY_NETWORK` | Optional | default `public` |
| `OPENGATEWAY_AUDIT` | Optional | default `true` |
| `OPENGATEWAY_OPEN_REGISTRATION` | Optional | default **`false`** — first admin always allowed; later users need invite unless set `true` |
| `OPENGATEWAY_DB` | Optional | set `none` when using Postgres (entrypoint does this) |

### After Deploy

1. Wait for **open-gateway**, **Postgres**, and **Redis** to be healthy  
2. Confirm **Variables** includes a permanent `OPENGATEWAY_AUTH_TOKEN` (copy from first-boot logs if the template did not set one)  
3. Open Live Ops at your Railway HTTPS domain (`/ui/`)  
4. **Create admin account** (first user = org admin) — email + password  
5. **Mint agent token** for each harness → paste into MCP as `OPENGATEWAY_AUTH_TOKEN`  
6. Point agents at `OPENGATEWAY_URL=https://YOUR-APP.up.railway.app`  
7. Optional: `OPENGATEWAY_OPEN_REGISTRATION=true` for open team signup  

You should **not** need to paste the master token into the browser for normal use after login.

### Humans vs agents

| Who | Auth |
|-----|------|
| Humans (Live Ops) | Email/password → session (`ogs_…`) |
| Agents (MCP) | Device API keys (`ogk_…`) from the UI |
| Ops / emergency | Master `OPENGATEWAY_AUTH_TOKEN` in Railway Variables |

### Health check

`GET /ping` should report roughly:

```json
{ "status": "ok", "backend": "postgres", "redis": true, "require_auth": true }
```

### Smoke (after domain is live)

```bash
curl -sS https://YOUR-APP.up.railway.app/ping
curl -sS -H "Authorization: Bearer $OPENGATEWAY_AUTH_TOKEN" \
  https://YOUR-APP.up.railway.app/v1/rooms
opengateway doctor --url https://YOUR-APP.up.railway.app --skip-network
```

Repo: https://github.com/mrdulasolutions/open-gateway  
Full guide: https://github.com/mrdulasolutions/open-gateway/blob/main/docs/RAILWAY.md  
Auth guide: https://github.com/mrdulasolutions/open-gateway/blob/main/docs/AGENTS_AUTH.md  
