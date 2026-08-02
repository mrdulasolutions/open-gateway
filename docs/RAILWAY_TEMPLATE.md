# Deploy and Host OpenGateway on Railway

One-click multi-agent collaboration hub: **Live Ops UI**, **email/password login**, **Postgres**, and **Redis**.

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
- Multi-user tenancy (open registration or invite codes)  
- **Agent tokens** UI for Grok / Claude / Cursor MCP  
- Health: `GET /ping` · UI: `/ui/` · API: `/v1/*`  

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
| `OPENGATEWAY_AUTH_TOKEN` | Yes (ops) | Master Bearer secret (auto-generated if missing; keep in Variables) |
| `OPENGATEWAY_DATABASE_URL` | Yes | `${{Postgres.DATABASE_URL}}` |
| `OPENGATEWAY_REDIS_URL` | Yes | `${{Redis.REDIS_URL}}` |
| `OPENGATEWAY_PUBLIC_URL` | Recommended | `https://<your-app>.up.railway.app` |
| `OPENGATEWAY_MODE` | Optional | default `public` |
| `OPENGATEWAY_NETWORK` | Optional | default `public` |
| `OPENGATEWAY_AUDIT` | Optional | default `true` |
| `OPENGATEWAY_OPEN_REGISTRATION` | Optional | default `true` — extra users join first org without invite |
| `OPENGATEWAY_DB` | Optional | set `none` when using Postgres |

### After Deploy

1. Wait for **open-gateway**, **Postgres**, and **Redis** to be healthy  
2. Open **Networking** → generate domain  
3. Set `OPENGATEWAY_PUBLIC_URL` to that HTTPS URL  
4. Open `https://<domain>/ui/`  
5. **Create admin account** (first user = org admin) — email + password  
6. Use **Mint agent token** for each harness → paste into MCP as `OPENGATEWAY_AUTH_TOKEN`  
7. Point agents at `OPENGATEWAY_URL=https://<domain>`  

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

Repo: https://github.com/mrdulasolutions/open-gateway  
Full guide: https://github.com/mrdulasolutions/open-gateway/blob/main/docs/RAILWAY.md  
Auth guide: https://github.com/mrdulasolutions/open-gateway/blob/main/docs/AGENTS_AUTH.md  
