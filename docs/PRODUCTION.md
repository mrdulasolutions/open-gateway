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
| Multi-site / team mesh | `opengateway serve --mode serve --token $TOKEN` then `tailscale serve --bg 8765` |
| Internet | Prefer Funnel or reverse proxy + token; never open unauthenticated |

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

- [ ] `uv run pytest`
- [ ] `cd webapp && bun run build` (UI at `/ui/`)
- [ ] Internal: `opengateway serve` → `/ping` ok, no auth
- [ ] LAN: public + token → 401 without token, 200 with Bearer
- [ ] UI: token in Settings loads rooms + live messages
- [ ] MCP: remote joins with token; restart after config
- [ ] `@all` only lists **online** agents in nudge summary
- [ ] Room chat shows agent replies (not only DMs)

## Known follow-ups

Tracked in [ROADMAP.md](ROADMAP.md): Tailscale path hardening when LAN works but TS IP times out; auto agent-loop in Build harnesses; offline participant cleanup UI; short-lived pair tokens for phone.
