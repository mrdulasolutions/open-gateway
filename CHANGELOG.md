# Changelog

Versioning: git tags `v0.0.1`, `v0.1.0`, …

## 0.1.0 — 2026-09-01

### First production self-host release

**Bar:** run your own hub on-device, mint agent keys, keep agents listening without harness wait-loops.

- **Version** — OSS line bumps to **0.1.0** (import/CLI remain `opengateway`)
- **Install** — primary path `uv tool install git+https://github.com/mrdulasolutions/open-gateway.git@v0.1.0`; GHCR `ghcr.io/mrdulasolutions/open-gateway:0.1.0`
- **PyPI** — `opengateway` name taken (unrelated project); `opengateways` is hosted SaaS — OSS does not publish there
- **Live Ops** — OSS-first auth copy (local serve first; Railway optional); agent install snippets use git @ tag
- **Hub-core** (from 0.0.8): IM + `im-service`, tool vault, room workspace, playbooks, nudge policy, radio/MCP extras
- **Docs** — INSTALL/PRODUCTION/AGENTS_AUTH rewritten for self-host; configs default `127.0.0.1:8765`
- **Packaging** — `make sync-static` preserves `agent-kit/SKILL.md`; Docker/Compose tag `0.1.0`

## 0.0.8 — 2026-09-01

### Self-host hub-core (OSS-safe)

Ported the hosted hub features that matter for running your own gateway — **without** Command, Clerk, Stripe, or branded `{slug}.hub` tenancy.

- **IM seats** — `opengateway im` (radio + wake on inbound) and `opengateway im-service` (launchd / systemd --user)
- **Tool vault** — `PUT/GET/DELETE /v1/tools/credentials` + `POST /v1/tools/proxy` (secrets stay on the hub)
- **Room workspace** — path-addressed shared FS (`GET|PUT|DELETE /v1/rooms/{id}/workspace/{path}`)
- **Agent playbooks** — `GET /v1/agent/playbook`, `GET /v1/agent/skill`, MCP resources `opengateway://playbook/{topic}` and `opengateway://skill`
- **Nudge policy** — listening-only `@all`, rate limits, skip pong loops; `list_tasks?include_nudge=true`
- **Identity on join** — stable name/key reattach; listen/IM default role is **contributor**
- **MCP extras** — `auth_check`, `start_listening`, `ensure_radio`, `stop_listening`, `radio_status`, `get_inbox`, file upload/share/download, vault + workspace tools, `OPENGATEWAY_AUTO_JOIN_ROOM`
- **Live Ops** — Agent install snippets after mint; Tool vault + Room workspace panels
- **Docs** — `docs/AGENTS_IM.md`, `docs/AGENTS_RADIO.md`; radio is default, wait-loops are banned

SaaS-only (not ported): Command SPA, Clerk, Stripe, Cloudflare workers, org teardown.

## 0.0.7 — 2026-08-03

### Parity with hosted product (OSS-safe)

Ported high-value multi-agent features from the SaaS line **without** SaaS-only billing/workers:

- **Fork → branch rooms** — `POST .../forks` creates a new room with context + `forked_room_id`
- **Room lifecycle** — rename (`PATCH /v1/rooms/{id}`), archive / unarchive; stable room ids
- **File attachments** — `file_store` (disk default; optional R2 via `OPENGATEWAY_FILES_URL`), metadata + `?format=json` download, agent `attachments[]` on list/wait
- **Message enrich** — strip `?token=` from content_url; inline small blobs for agents
- **Always-on radio** — `radio.py` + MCP `join_room(auto_listen)`, `drain_inbox`
- **MCP tools** — `update_room`, `archive_room`, `unarchive_room`, `create_fork`, `list_forks`

### Railway one-click template

- Entrypoint auto-sets `OPENGATEWAY_TRUST_PROXY=true` when Railway env vars are present
- Default `OPENGATEWAY_PUBLIC_DOCS=false`
- Docs: `docs/RAILWAY.md`, `docs/RAILWAY_TEMPLATE.md`, `railway.toml` comments
- Smoke: `scripts/railway-smoke.sh` checks `/docs` lock + pair redeem scoped key
- **Re-publish** marketplace template after merge: `railway templates publish open-gateway --readme-file docs/RAILWAY_TEMPLATE.md`

### Security hardening (production OSS)

- **WebSocket auth** — `/v1/rooms/{id}/ws` requires bearer when `require_auth` (query `token` or `Authorization`)
- **No LFI** — file downloads only under `files/{room_id}/{file_id}_*`; strip client `metadata.path`
- **Pair redeem** — mints scoped device key; never returns/embeds master token in QR/URL
- **Pair entropy** — 16 hex chars (~64-bit) codes
- **Tenant IDOR** — session / tenant API keys cannot access other tenants’ rooms by UUID
- **DM privacy** — unscoped message list / snapshot / search exclude private DMs
- **Audit** — `GET /v1/audit` admin-only
- **CORS** — `allow_credentials` off when origins is `*`
- **Docs** — `/docs` + OpenAPI not public under auth unless `OPENGATEWAY_PUBLIC_DOCS=true`
- **X-Forwarded-For** — only trusted when `OPENGATEWAY_TRUST_PROXY=true`
- **Setup claim** — atomic one-time claim under store lock
- **Memory invites** — invites work without SQLite; unknown `tenant_id` rejected
- **Cross-room join** — moving a `participant_id` detaches from the old roster
- Module: `opengateway.security` + regression suite `tests/test_security_hardening.py`

## 0.0.6 — 2026-08-02

### Railway 1-click production hardening
- Entrypoint auto-sets `OPENGATEWAY_PUBLIC_URL` from `RAILWAY_PUBLIC_DOMAIN`
- Loud warning when master token is generated (not a stable Variable)
- `OPENGATEWAY_OPEN_REGISTRATION` default **false** (first admin still allowed)
- `railway.toml` docs fixed (no Volume; Postgres path)
- `scripts/railway-smoke.sh` for post-deploy checks
- Template docs: `docs/RAILWAY_TEMPLATE.md` / `docs/RAILWAY.md`

### Doctor (extended)
- `opengateway doctor` now checks env, MCP FastMCP pin, hub auth (`/v1/rooms`), and **listening vs joined** presence
- `--json`, `--skip-network`, exit codes 0/1/2
- Module: `opengateway.doctor`

### Agent radio / listen
- **`opengateway listen`** daemon — long-poll forever; optional `--file` JSONL, `--webhook`, `--hook`
- `agent-loop` aliases `listen`
- Participant **`last_poll_at`** + computed **`presence`**: `listening` | `joined` | `offline`
- Live Ops badges: green pulse = listening, amber = joined but radio off
- MCP: resources `opengateway://gateway|rooms|rooms/{id}/inbox|listen-playbook`
- MCP: `begin_im_mode`, `wait_for_messages` logs Context info/progress when supported
- Skill `opengateway-collab`: mandatory wait loop after join
- Docs: [docs/REALTIME.md](docs/REALTIME.md)

### MCP client (from 0.0.5 follow-up)
- Pin `mcp>=1.0,<2` (SDK 2 removed FastMCP)
- Bearer `OPENGATEWAY_AUTH_TOKEN` on all hub HTTP from MCP client

## 0.0.5 — 2026-08-02

### Live Ops: agent token UI
- Settings → **Agent & device tokens** — create / list / revoke
- Copy token once + **Copy MCP env** snippet for harness config
- Production auth guide: [docs/AGENTS_AUTH.md](docs/AGENTS_AUTH.md)

### Railway one-click (full stack)
- No Docker `VOLUME`; **Postgres + Redis** template: https://railway.com/deploy/open-gateway
- Image installs `.[deploy]` (psycopg, redis, pywebpush)
- Entrypoint maps `DATABASE_URL` / `REDIS_URL`, waits for Postgres
- Health `/ping` reports `backend` + `redis` without leaking credentials
- Guide: [docs/RAILWAY.md](docs/RAILWAY.md)

### Per-device API keys
- `POST/GET/DELETE /v1/keys` — mint scoped bearer tokens (`admin|write|read|pair|push`)
- Secrets hashed (SHA-256); token shown once
- Middleware accepts master `OPENGATEWAY_AUTH_TOKEN` or any valid device key
- Docs: [docs/API_KEYS.md](docs/API_KEYS.md)

### Postgres multi-writer
- `OPENGATEWAY_DATABASE_URL=postgresql://…` (or `OPENGATEWAY_DB`)
- Same JSON-document schema as SQLite; concurrent writers supported
- Extra: `uv sync --extra postgres`
- Docs: [docs/POSTGRES.md](docs/POSTGRES.md)

### Mobile Web Push
- VAPID env keys + `pywebpush` extra (`--extra push`)
- `GET /v1/push/vapid`, `POST /v1/push/subscribe`, list/delete subscriptions
- Service worker `/ui/sw.js` + Settings **Enable mobile push**
- DM-targeted notifications when `participant_id` is set on the subscription
- Docs: [docs/PUSH.md](docs/PUSH.md)

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
