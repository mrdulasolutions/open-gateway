# Changelog

Versioning starts at **0.0.1**. Git tags use the form `v0.0.1`, `v0.0.2`, …

## 0.0.4 — 2026-08-02

### Audit log (public mode)
- SQLite `audit_log` table + in-memory fallback
- Enabled by default when auth is required; force with `OPENGATEWAY_AUDIT=true`
- `GET /v1/audit` (auth required) with action / room filters
- Redacts token/password-like fields

### Redis multi-process scale-out
- Optional `OPENGATEWAY_REDIS_URL` / `REDIS_URL` + extra `opengateway[redis]`
- Cross-worker event fan-out for SSE / wait
- Shared pair codes across workers
- Docs: [docs/SCALE.md](docs/SCALE.md)

### Publish & deploy
- GitHub Actions: CI on `main`, Publish (PyPI + GHCR) on `v*` tags
- `fly.toml` + `railway.toml` + [docs/DEPLOY.md](docs/DEPLOY.md)
- Compose / Docker image tag `0.0.4`

## 0.0.3 — 2026-08-02

### Packaging
- Live Ops UI bundled in the Python package (`opengateway/static/`) for `uv tool install` / wheels
- `docs/INSTALL.md` — tool install, Docker Compose, local wheel
- `Dockerfile` + `docker-compose.yml` for one-command hub
- `make package` / `make docker` / `make sync-static`

### Identity
- Multi-word display names with spaces (e.g. `Mark Dula`)
- Settings draft-on-type, commit on blur/Enter (no more space-eating PATCH-per-keystroke)
- Mentions support multi-word `@Name Last`

### Dual path: LAN + Tailscale Serve
- Keep open LAN bind **and** add Tailscale Serve (not either/or)
- Auto-register **tailnet-serve** gateway card when MagicDNS is known
- Pair API returns `access` / `urls` for `lan` + `tailscale`
- QR origin follows the gateway card you tap

### Cellular / phone pair
- **Tailnet card** QR → `https://…ts.net` works on **cellular** when Tailscale app is ON
- **LAN card** QR → same Wi‑Fi only (clear disclaimers)
- Pair redeem rate-limited per IP (shared auth-failure budget)
- UI copy: “Cellular OK · Tailscale must be ON”

### Hardening
- Production checklist updated for dual path + identity
- SECURITY notes for pair links (short-lived; rotate if shared broadly)

## 0.0.2 — 2026-08-02

### Tailscale path hardening
- `opengateway doctor` + `GET /v1/network` diagnostics (Serve vs raw 100.x)
- Serve-first messaging; document LAN vs tailnet firewall asymmetry
- Why 100.x:PORT times out when LAN works — prefer `tailscale serve`

### Mobile Live Ops
- Responsive drawers, safe-area, PWA manifest (`manifest.webmanifest`)
- `opengateway pair` + `POST /v1/pair` / redeem deep links (`#pair=`)
- Phone bootstrap: token + room from pair URL, then strip secrets from hash

## 0.0.1 — 2026-08-02

First tagged baseline of OpenGateway.

### Core
- Rooms, participants, messages, tasks, artifacts
- ACP-compatible `/agents` + `/runs` + built-ins (echo, room-facilitator, room-broadcast)
- MCP stdio bridge (Grok, Claude Code, Cursor, Codex, Hermes)
- Realtime: SSE, long-poll (`wait` + `next_since` cursor), WebSocket chat
- SQLite persistence (`~/.opengateway/state.db`)

### Gateways & security
- Modes: internal | public | serve | funnel
- Network badges: Internal (loopback) · **LAN** · Tailnet (Serve) · Internet (Funnel)
- Bearer auth required for non-loopback; MCP/CLI forward `OPENGATEWAY_AUTH_TOKEN`
- Auth failure rate limit (per-IP) on public modes
- Optional Tailscale identity headers only on localhost bind
- Soft-expire ONLINE agents after 120s idle; cancel their open nudge tasks
- Nudges only target **online** non-human agents

### Live Ops UI
- Vite + React day/night console
- Global search with type chips (DM vs room msg, agent, task, …)
- Notification bell (DMs, mentions, room pings, tasks)
- Sidebars collapsed by default
- Auth banner when public mode needs a token
- Prebuilt `webapp/dist` committed for zero-Node serve

### CLI
- `serve`, `mcp`, `status`, `demo`, `monitor`, `chat`, `agent-loop`, `ui`

### Docs
- README (with UI screenshot), PRODUCTION, GATEWAYS, ROADMAP, SECURITY, NOTICE, Apache-2.0 (© MR Dula Enterprise, LLC)
