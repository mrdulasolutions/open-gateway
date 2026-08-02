# Changelog

Versioning starts at **0.0.1**. Git tags use the form `v0.0.1`, `v0.0.2`, …

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
