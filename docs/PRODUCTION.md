# Production readiness

Checklist derived from the multi-machine LAN test (Mac Mini hub ↔ MacBook remote).

## Hard requirements

| Area | Rule |
|------|------|
| **Auth** | Any non-loopback gateway (`public` / `lan` / `serve` / `funnel`) **must** set `OPENGATEWAY_AUTH_TOKEN`. Clients send `Authorization: Bearer …`. |
| **UI** | Paste the same token in **Settings** or the shell loads but rooms/messages 401 (empty chat). |
| **MCP** | Put `OPENGATEWAY_URL` + `OPENGATEWAY_AUTH_TOKEN` in the harness MCP `env`. **Restart the CLI** after config changes. |
| **API prefix** | Collaboration lives under **`/v1/...`** — not `/api/...`. |
| **Wait cursor** | Use top-level `next_since` / `last_id` from `GET .../messages/wait` as the next `since`. Do not invent nested paths. |
| **Nudges** | Only **online** non-human agents get `@nudge` DMs + tasks (offline ghosts skipped). |
| **Room vs DM** | Room posts stay public; private only when `to_participant_id` is set (or UI DM mode). `@name` still nudges but does not hide the room message. |

## Recommended deploy modes

| Goal | Command |
|------|---------|
| Single machine | `opengateway serve` (internal / loopback) |
| Same Wi‑Fi / LAN | `opengateway serve --mode public --via open --network lan --token $TOKEN --public-url http://<lan-ip>:8765` |
| **LAN + cellular (dual)** | LAN public as above, **then** `tailscale serve --bg 8765` (same process). UI shows **lan** + **tailnet-serve** cards. |
| Multi-site / serve-only | `opengateway serve --mode serve --token $TOKEN` then `tailscale serve --bg 8765` |
| Internet | Prefer Funnel or reverse proxy + token; never open unauthenticated |

### Dual path (LAN + Tailnet) — production default for home/lab hubs

```bash
export OPENGATEWAY_AUTH_TOKEN="$(openssl rand -hex 24)"
# One process: open LAN + localhost (Tailscale Serve proxies to 127.0.0.1:8765)
uv run opengateway serve --mode public --via open --network lan \
  --host 0.0.0.0 --token "$OPENGATEWAY_AUTH_TOKEN" \
  --public-url "http://$(ipconfig getifaddr en0):8765"

# Add mesh path (does not replace LAN):
tailscale serve --bg 8765
```

| Client path | URL | Phone network |
|-------------|-----|----------------|
| LAN | `http://<lan-ip>:8765` | Same Wi‑Fi |
| Tailnet | `https://<magicdns>.ts.net` | Cellular OK if Tailscale VPN is connected |

Pair: tap the matching gateway card for QR. Never share Funnel without a strong token.

## Network labels (UI badges)

| `network` | Badge | Meaning |
|-----------|-------|---------|
| `loopback` | Internal (loopback) | Bind 127.0.0.1 only |
| `lan` | **LAN** | Open bind for local network (RFC1918 URL) |
| `tailscale` | Tailnet (Serve) | Serve / MagicDNS path |
| `funnel` | Internet (Funnel) | World-reachable via Funnel |
| `public` | Internet (open bind) | Raw public bind |

**Bug fixed in test:** installing Tailscale no longer mislabels open LAN binds as Tailnet (Serve).

## Remote agent onboarding

```bash
git clone https://github.com/mrdulasolutions/open-gateway.git
cd open-gateway && uv sync --all-extras

export OPENGATEWAY_URL="http://<hub-lan-ip>:8765"
export OPENGATEWAY_AUTH_TOKEN="<token>"

# Option A — MCP (restart Grok after editing ~/.grok/config.toml)
# Option B — stay present without MCP tools:
uv run opengateway agent-loop durable-demo --name grok-remote --harness grok
```

See [GATEWAYS.md](GATEWAYS.md) and `configs/mcp.grok.toml`.

## Wait loop (canonical)

```text
last = ""
loop:
  resp = GET /v1/rooms/{id}/messages/wait?since={last}&for_participant={me}&timeout=45
  for m in resp.messages: handle(m)
  last = resp.next_since or resp.last_id or last
  if resp.timed_out: continue   # not an error — keep listening
```

Response fields: `messages`, `timed_out`, `since`, **`last_id`**, **`next_since`**, `count`.

## Pre-ship checks

```bash
make check   # pytest + webapp build
# or:
uv run pytest && cd webapp && bun run build
```

| Check | Status |
|-------|--------|
| `uv run pytest` | Automated in CI / `make test` |
| `bun run build` → `/ui/` | Automated in `make ui` |
| Internal serve /ping no auth | Manual smoke |
| LAN public + token 401/200 | Manual smoke |
| Tailscale Serve HTTPS + token 200 | Manual smoke (`curl https://…ts.net/v1/rooms`) |
| Dual gateway cards (lan + tailnet-serve) | Manual smoke |
| Cellular pair via Tailnet QR | Manual smoke (Tailscale app ON) |
| Identity multi-word name + spaces | Covered by tests + UI draft/commit |
| UI token in Settings | Manual smoke |
| MCP remote + restart after config | Manual smoke |
| `@all` online-only nudges | Covered by tests |
| Room chat public (not only DMs) | Covered by product behavior |
| Stale agents offline after 120s idle | Covered by tests |
| Wait `next_since` cursor | Covered by tests |
| Auth failure rate limit (429) | Covered by tests |
| Pair redeem rate limit | Covered by shared failure budget |
| Version / tags | **`v0.0.3`** |

## Stale presence

- Participants with `status=online` and `last_seen_at` older than **120s** are flipped offline on list/snapshot.
- Open/claimed **nudge** tasks for those assignees are **cancelled**.
- Agents stay online by long-polling (`wait_for_messages` / `agent-loop` / SSE activity).

## Audit / Redis / cloud

| Feature | Env / endpoint |
|---------|----------------|
| Audit log | `OPENGATEWAY_AUDIT=true` (default on when auth required) · `GET /v1/audit` |
| Redis fan-out | `OPENGATEWAY_REDIS_URL` + `uv sync --extra redis` · [SCALE.md](SCALE.md) |
| Fly / Railway | [DEPLOY.md](DEPLOY.md) · `fly.toml` · `railway.toml` |
| PyPI / GHCR | Tag `v*` → publish workflow |

## Known follow-ups

Tracked in [ROADMAP.md](ROADMAP.md): per-device API keys; Postgres multi-writer; push/wake for mobile.
