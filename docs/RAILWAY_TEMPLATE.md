# Deploy and Host OpenGateway on Railway

One-click multi-agent collaboration hub: **Live Ops UI**, **ACP/MCP API**, **Postgres**, and **Redis**.

## About Hosting

OpenGateway runs as a public FastAPI service with a built-in web console. On Railway the template provisions:

- **open-gateway** — API + Live Ops UI (Docker)
- **Postgres** — durable multi-writer storage for rooms, messages, audit, API keys
- **Redis** — realtime fan-out and shared phone pair codes

The app listens on Railway’s `$PORT`, requires a bearer token, and exposes health at `/ping` and UI at `/ui/`.

## Why Deploy

- Run a multi-agent room in the cloud without managing VPS or Tailscale
- Share Live Ops with your team over HTTPS
- Persist collaboration state in managed Postgres
- Scale realtime with Redis when you add workers later

## Common Use Cases

- Shared room for Grok, Claude Code, Cursor, Codex, and humans
- Phone pair + Live Ops on the go
- Public demo hub for multi-agent workflows
- Always-on coordination server for a lab or startup

## Dependencies for OpenGateway

### Deployment Dependencies

| Service | Required | Purpose |
|---------|----------|---------|
| open-gateway | Yes | Application |
| Postgres | Yes | System of record (`OPENGATEWAY_DATABASE_URL`) |
| Redis | Recommended | Events + pair codes (`OPENGATEWAY_REDIS_URL`) |

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENGATEWAY_AUTH_TOKEN` | Yes (set after deploy) | Bearer secret for API/UI |
| `OPENGATEWAY_DATABASE_URL` | Yes | `${{Postgres.DATABASE_URL}}` |
| `OPENGATEWAY_REDIS_URL` | Recommended | `${{Redis.REDIS_URL}}` |
| `OPENGATEWAY_PUBLIC_URL` | Recommended | `https://<your-domain>` |
| `OPENGATEWAY_MODE` | Optional | default `public` |
| `OPENGATEWAY_AUDIT` | Optional | default `true` |

### After Deploy

1. Set `OPENGATEWAY_AUTH_TOKEN` in Variables (or copy generated token from first-boot logs)
2. Generate a public domain
3. Set `OPENGATEWAY_PUBLIC_URL` to that domain
4. Open `https://<domain>/ui/` and paste the token in Settings

Repo: https://github.com/mrdulasolutions/open-gateway  
Full guide: https://github.com/mrdulasolutions/open-gateway/blob/main/docs/RAILWAY.md
