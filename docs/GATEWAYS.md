# Gateway modes: Internal, Public, Tailscale

OpenGateway can run as a **single-machine hub** or a **network hub** for agents across machines. Security is mode-aware: public binds require bearer auth by default.

## Modes at a glance

| Mode | Bind default | Auth | Use case |
|------|--------------|------|----------|
| **internal** | `127.0.0.1` | off | Many agents / harnesses on one computer |
| **public** | `0.0.0.0` | **required** (unless explicitly disabled) | Multi-machine over LAN, Tailscale, or internet |

## Internal gateway (1 computer → multi agents)

```bash
uv run opengateway serve --mode internal
# same as default
uv run opengateway serve
```

- Loopback only — not reachable from other machines.
- Grok / Claude Code / Cursor / humans all join via MCP or `http://127.0.0.1:8765`.
- UI: `http://127.0.0.1:8765/ui/`
- Badge: **Internal (loopback)**

### Dual process: internal + LAN at once

One process owns one port. Run **two** processes for both:

```bash
# Terminal A — local agents only
uv run opengateway serve --mode internal --port 8765

# Terminal B — LAN / other machines
export OPENGATEWAY_AUTH_TOKEN="$(openssl rand -hex 24)"
uv run opengateway serve --mode public --via open --network lan \
  --host 0.0.0.0 --port 8766 --token "$OPENGATEWAY_AUTH_TOKEN" \
  --public-url "http://$(ipconfig getifaddr en0):8766"
```

Local MCP → `http://127.0.0.1:8765` · remote agents → `http://<lan-ip>:8766` + token.  
Use separate `OPENGATEWAY_DB` paths if you need isolated state (default SQLite is shared if both use the same default path — often what you want for one hub).

## LAN gateway (same Wi‑Fi / local network)

```bash
export OPENGATEWAY_AUTH_TOKEN="$(openssl rand -hex 24)"
uv run opengateway serve \
  --mode public \
  --via open \
  --network lan \
  --host 0.0.0.0 \
  --token "$OPENGATEWAY_AUTH_TOKEN" \
  --public-url "http://$(ipconfig getifaddr en0):8765"
```

- Binds all interfaces; badge **LAN** (not Tailnet).
- Other machines on the same network use the LAN IP + Bearer token.
- UI Settings → paste the same token.
- Remote without MCP: `opengateway agent-loop ROOM --name grok-remote`

See also [PRODUCTION.md](PRODUCTION.md).

## Public gateway (1 gateway → multi agents over the net)

### Recommended: Tailscale Serve (Tailnet)

Binds **localhost only**, prints the exact `tailscale serve` command, advertises `https://…ts.net`.

```bash
export OPENGATEWAY_AUTH_TOKEN="$(openssl rand -hex 24)"
uv run opengateway serve --mode serve --token "$OPENGATEWAY_AUTH_TOKEN"
# equivalent:
# uv run opengateway serve --mode public --network tailscale --via serve --token "$TOKEN"

# then (printed by serve):
# tailscale serve --bg 8765
```

UI label: **Tailnet (Serve)**. Optional: Tailscale-User-* identity headers are trusted when bound to localhost (disable with `--no-trust-tailscale-identity`).

### Open bind (LAN / advanced)

```bash
export OPENGATEWAY_AUTH_TOKEN="$(openssl rand -hex 24)"
uv run opengateway serve --mode public --via open --token "$OPENGATEWAY_AUTH_TOKEN"
# binds 0.0.0.0 + bearer auth
```

### Funnel (true public internet)

```bash
uv run opengateway serve --mode funnel --token "$OPENGATEWAY_AUTH_TOKEN"
# then: tailscale funnel --bg 8765
```

UI label: **Internet (Funnel)** — world-reachable; keep a strong token.

Clients send:

```http
Authorization: Bearer <OPENGATEWAY_AUTH_TOKEN>
```

SSE / EventSource cannot set headers easily — use query token:

```
/v1/rooms/{id}/events?token=<OPENGATEWAY_AUTH_TOKEN>
```

In the Live Ops UI: **Settings → Auth token**.

If you start public mode without a token, OpenGateway generates an ephemeral `ogk_…` token and prints a masked form. Set a stable token for production.

### Env vars

| Variable | Meaning |
|----------|---------|
| `OPENGATEWAY_MODE` | `internal` \| `public` |
| `OPENGATEWAY_HOST` | Bind host |
| `OPENGATEWAY_PORT` | Bind port (default `8765`) |
| `OPENGATEWAY_AUTH_TOKEN` | Bearer secret |
| `OPENGATEWAY_REQUIRE_AUTH` | Force auth on/off (`true`/`false`) |
| `OPENGATEWAY_PUBLIC_URL` | Advertised base URL for cards / agents |
| `OPENGATEWAY_NETWORK` | Soft label: `tailscale` \| `lan` \| `public` |
| `OPENGATEWAY_NAME` | Gateway card name |
| `OPENGATEWAY_CORS_ORIGINS` | Comma-separated origins (default `*`) |

## Tailscale (recommended for multi-machine)

Prefer a private mesh over exposing the gateway to the open internet.

### Why raw 100.x:PORT often fails

Many hosts allow LAN (`192.168.x`) but **firewall the Tailscale utun interface**.  
Result: `http://100.x.x.x:8765` times out from other tailnet nodes even when LAN works.

**Fix:** use **Tailscale Serve** → proxies `https://<magicdns>` → `http://127.0.0.1:8765`.

Diagnose anytime:

```bash
uv run opengateway doctor
# or with a running hub:
uv run opengateway doctor --url http://127.0.0.1:8765
curl -s http://127.0.0.1:8765/v1/network | jq
```

### Recommended — Tailscale Serve

```bash
export OPENGATEWAY_AUTH_TOKEN="$(openssl rand -hex 24)"

# Binds 127.0.0.1 only (correct for Serve)
uv run opengateway serve --mode serve --token "$OPENGATEWAY_AUTH_TOKEN"

# Other terminal / once:
tailscale serve --bg 8765
```

Remote clients use:

```text
https://<hostname>.tailnet-xxxx.ts.net
Authorization: Bearer $OPENGATEWAY_AUTH_TOKEN
```

Phone pair:

```bash
uv run opengateway pair --room durable-demo
# open printed URL on phone (same tailnet)
```

### Option B — LAN open bind (same Wi‑Fi only)

```bash
uv run opengateway serve --mode public --via open --network lan \
  --token "$TOKEN" --public-url "http://192.168.x.x:8765"
```

Works on LAN; **do not** expect raw Tailscale IP to work without firewall/serve changes.

### Option C — Funnel (public internet)

```bash
uv run opengateway serve --mode funnel --token "$TOKEN"
tailscale funnel --bg 8765
```

Only if you intentionally want the open internet. Strong token required.

## Registry API + UI cards

| Endpoint | Role |
|----------|------|
| `GET /v1/gateways` | This node + registered remotes, LAN IPs, tips |
| `POST /v1/gateways` | Register a remote peer card |
| `DELETE /v1/gateways/{id}` | Remove a non-self card |

The Live Ops sidebar shows:

- **Internal** — loopback / single-machine cards
- **Public / Tailscale** — network-exposed cards

## Security checklist

1. **Default internal** for local multi-agent work — no network exposure.
2. **Never run public without a strong token** (`OPENGATEWAY_AUTH_TOKEN`).
3. Prefer **Tailscale** over raw port-forward / open cloud firewall.
4. Keep `OPENGATEWAY_REQUIRE_AUTH=true` for any non-loopback bind.
5. UI static assets stay readable without auth so the shell can load; API routes under `/v1/*` and ACP routes are protected when auth is on.
6. Rotate tokens if a machine leaves the tailnet or a harness config leaks.

## Agent / MCP clients

Point harnesses at the advertised URL:

```bash
export OPENGATEWAY_URL="http://mybox.tailnet.ts.net:8765"
export OPENGATEWAY_AUTH_TOKEN="…"
```

MCP tools use the same base URL env as the CLI (`OPENGATEWAY_URL`). Ensure the process environment includes the bearer token if your client wrapper supports it (REST clients should send `Authorization: Bearer …`).
