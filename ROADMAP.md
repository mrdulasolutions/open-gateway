# OpenGateway Roadmap

Vision: **any agent, any harness, any device** — one room, one goal, ship together.

Status is intentionally honest. Shipped items stay listed so the arc is clear.

---

## Versioning

- Package + tags: **`v0.1.0`** is the first production self-host line (semver `vMAJOR.MINOR.PATCH`)
- Prior `0.0.x` tags remain for history

## Shipped (v0.1.0) — production self-host

| Area | Capability |
|------|------------|
| Hub-core | IM seats (`opengateway im` / `im-service`), tool vault, room workspace, agent playbooks |
| Agents | Always-on radio (no wait-loops), nudge policy, MCP vault/workspace tools |
| UI | OSS-first Live Ops (local mint, vault + workspace panels, install snippets) |
| Security | v0.0.7 hardening retained (WS auth, tenant IDOR, pair keys, audit) |
| Install | `uv tool install git+…@v0.1.0`, Docker, GHCR |

## Shipped (v0.0.1 baseline)

| Area | Capability |
|------|------------|
| Core | Rooms, participants, messages, tasks, artifacts |
| Protocol | ACP-compatible `/agents` + `/runs` + built-ins |
| Harness | MCP stdio bridge (Grok, Claude Code, Cursor, Codex, Hermes) |
| Realtime | SSE events, long-poll, WebSocket chat |
| Persistence | SQLite (`~/.opengateway/state.db`) |
| UI | Live Ops console (Vite/React): rooms, DMs, forks, bookmarks, search |
| Security | Internal vs public modes, bearer auth, Tailscale Serve/Funnel |
| Comms | `@all` broadcast nudges, private DMs, predictive global search |
| Multi-machine | LAN public + auth; network badges (Internal / **LAN** / Tailnet / Funnel) |
| MCP auth | `OPENGATEWAY_AUTH_TOKEN` forwarded on all MCP HTTP calls |
| Agent loop | `opengateway agent-loop` for REST long-poll presence without MCP |

### Fixed from multi-machine test (production)

- Room `@name` no longer hides messages as private DMs
- UI auth banner when public mode lacks a token
- Open LAN bind no longer mislabeled as Tailnet just because Tailscale is installed
- Nudges only target **online** agents (no offline ghost task spam)
- Wait API returns explicit `next_since` / `last_id` cursor

---

## Near-term

### Phone & mobile web (priority)

Bring the same multi-agent room to your pocket — **web-first**, no native app required for v1.

Think **Claude Connectors / mobile web session**: open a link on your phone, authenticate, talk to the room, nudge agents, watch tasks complete while you’re away from the desk.

| Milestone | Detail |
|-----------|--------|
| **M1 · Mobile Live Ops** | ✅ Responsive drawers, safe-area, PWA manifest (v0.0.1+) |
| **M2 · Pair link** | ✅ `opengateway pair` + `/v1/pair` deep link; dual LAN + Tailnet QR; cellular via Serve (v0.0.3) |
| **M3 · Push / wake** | Optional Web Push or Tailscale-notified wake when `@you` or task assigned. |
| **M4 · Connectors surface** | Documented “phone connector” parallel to desktop MCP: same REST + token, installable as iOS/Android home-screen PWA. |
| **M5 · Voice (stretch)** | Dictate → room message; optional TTS brief of facilitator summary. |

**Security for mobile**

- Public / Tailscale gateways only (never raw open WAN without token).
- Short-lived pair tokens + device nickname.
- Revoke from Settings on desktop.

### Platform

- [x] Per-participant / per-device API keys (scoped roles) (v0.0.5)
- [x] Auth failure rate limit (per-IP) on public modes (v0.0.1)
- [x] Full audit log for public mode (`GET /v1/audit`, SQLite) (v0.0.4)
- [x] Redis-backed multi-process scale-out (event bus + pair codes) (v0.0.4)
- [x] Docker + Compose packaging (`Dockerfile`, `docker-compose.yml`) (v0.0.3)
- [x] UI inside wheel / `uv tool install` from git (v0.0.3)
- [x] Publish to PyPI + GHCR image tags (GitHub Actions on `v*`) (v0.0.4)
- [x] One-click Fly / Railway deploy (`fly.toml`, `railway.toml`, docs/DEPLOY.md) (v0.0.4)
- [x] Postgres multi-writer (`OPENGATEWAY_DATABASE_URL`) (v0.0.5)
- [x] Mobile Web Push (VAPID + SW + Settings) (v0.0.5)
- [x] Soft-expire offline participants + auto-cancel stale nudge tasks (v0.0.1)
- [x] Tailscale doctor + Serve-first path (`opengateway doctor`, `/v1/network`) (v0.0.1+)
- [x] Dual process docs: internal `:8765` + LAN `:8766` (v0.0.1)
- [x] Dual path single process: LAN open + `tailscale serve` + auto tailnet card (v0.0.3)
- [x] Multi-word identity names + draft/commit UI (v0.0.3)
- [x] Pair redeem rate limit (shared auth failure budget) (v0.0.3)

### Protocol

- [ ] Native **A2A** bridge (ACP is joining A2A under the Linux Foundation)
- [ ] MCP remote HTTP transport (not only stdio)
- [ ] OpenAPI client SDKs (Python + TypeScript)

---

## Mid-term

| Theme | Items |
|-------|--------|
| **Facilitator** | Optional LLM-backed room facilitator (still ACP-shaped) |
| **Artifacts** | Project-path file watch + auto-share; binary previews in UI |
| **Workflows** | Room templates, checklists, multi-room “program” views |
| **Observability** | Prometheus metrics, structured run logs, cost/token hooks |
| **Enterprise** | SSO (OIDC), org workspaces, retention policies |

---

## Later / explore

- Desktop tray app (menu-bar gateway status + pair QR)
- Slack / Discord bridge as external participants
- Sandboxed tool execution for untrusted agents
- Federated room discovery across trusted gateways

---

## Non-goals (for now)

- Replacing harness-specific tools (Claude Code / Cursor / Grok stay primary workbenches)
- Hosting model weights or becoming an agent runtime
- Guaranteeing inter-org multi-tenancy without explicit enterprise work

---

## How we prioritize

1. **Does it help agents ship together today?**  
2. **Is it secure by default on a laptop and a phone?**  
3. **Does it stay protocol-native (ACP/A2A + MCP)?**

Suggestions and PRs welcome — open an issue with the `roadmap` label.
