# Production auth: humans, agents, MCP

How authentication works when OpenGateway is on **Railway** (or any public gateway).

## Mental model

```text
┌─────────────────────────────────────────────────────────────┐
│  Gateway (require_auth = true)                              │
│                                                             │
│  Master token (OPENGATEWAY_AUTH_TOKEN)                      │
│    · full admin · mint keys · audit · settings              │
│                                                             │
│  Device / agent tokens (POST /v1/keys or Live Ops UI)       │
│    · scoped: write, read, pair, push                        │
│    · one per harness / phone / CI                           │
│    · shown once · hashed at rest                            │
└─────────────────────────────────────────────────────────────┘
         │                    │                    │
         ▼                    ▼                    ▼
   Live Ops browser      Grok MCP             Claude / Cursor
   (paste token)         (env token)          (env token)
```

There is **no separate OAuth login UI** for agents. Production auth is **Bearer tokens**.

| Actor | How they authenticate |
|-------|------------------------|
| You (browser) | Paste master or device token in **Settings → This browser's auth token** |
| Phone (pair QR) | Pair link embeds token in hash once; then stored locally |
| Grok / Claude / Cursor | MCP env: `OPENGATEWAY_URL` + `OPENGATEWAY_AUTH_TOKEN` |
| REST / scripts | `Authorization: Bearer <token>` |

There is **no automatic “local agent discovers Railway and handshakes”** without a token. The agent process must be given a token (or use the master). That is intentional for public internet gateways.

---

## Recommended production flow

### 1. Deploy hub (Railway)

- Template: https://railway.com/deploy/open-gateway  
- Master secret is **generated on the server** (`OPENGATEWAY_AUTH_TOKEN` in Variables)
- Open `https://…up.railway.app/ui/`  
  - Prefer **Connect this browser (one-time setup)** if shown (first visitor claims token into localStorage)  
  - Or paste from Railway → Variables → `OPENGATEWAY_AUTH_TOKEN`

### 2. Mint agent tokens (UI)

**Settings → Agent & device tokens → Create agent token**

- Name: `grok`, `claude`, `cursor`, `ci`  
- Copy token **once** (or **Copy MCP env**)  
- Revoke any key anytime from the same panel  

Scopes created by the UI: `write`, `read`, `pair`, `push` (not admin).

### 3. Wire each harness (MCP)

**Grok** (`~/.grok/config.toml`):

```toml
[mcp_servers.opengateway]
command = "uv"
args = ["run", "--directory", "/path/to/open-gateway", "opengateway", "mcp"]
# Or after tool install: args = ["tool", "run", "opengateway", "mcp"]
env = {
  OPENGATEWAY_URL = "https://open-gateway-production.up.railway.app",
  OPENGATEWAY_AUTH_TOKEN = "ogk_…",   # agent token from UI
  OPENGATEWAY_HARNESS = "grok",
  OPENGATEWAY_AGENT_NAME = "grok",
}
enabled = true
```

**Restart the harness** after env changes (MCP config is load-once).

Same pattern for Claude Code / Cursor / Codex templates under `configs/`.

### 4. Agent join (no extra handshake)

Once MCP starts with a valid token:

1. Tools call `GET/POST /v1/...` with `Authorization: Bearer …`  
2. Agent runs `join_room` / `list_rooms` as usual  
3. Gateway treats the token as an authenticated client (not a separate user login)

That **is** the handshake: first successful authenticated API call.

---

## Why not OAuth / magic link for agents?

| Approach | Production fit |
|----------|----------------|
| **Bearer tokens (current)** | Simple, works offline MCP stdio, CI-friendly, revocable |
| OAuth browser login | Good for humans; awkward for headless CLI agents |
| mTLS / Tailscale identity | Great on private mesh; optional on Serve (`trust_tailscale_identity`) |
| Unauthenticated public | Never for internet-facing hubs |

On **Tailscale Serve** with identity trust, localhost Serve headers can identify users without a token — but Railway is public HTTPS, so **tokens are required**.

---

## Security practices

1. **Master token** only in Railway Variables + your password manager — not in MCP configs on shared machines  
2. **One agent token per harness** — revoke when a laptop leaves  
3. Prefer UI-minted device keys over sharing master with agents  
4. Rotate master if it ever appears in a screenshot or pair QR shared broadly  
5. Pair URLs may embed the master briefly — treat as secrets  

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| MCP tools 401 | Set `OPENGATEWAY_AUTH_TOKEN` to a valid key; restart harness |
| “Cannot list keys” in UI | Browser token is not admin — paste **master** token |
| Agent joins but UI empty | UI token missing/wrong — paste same or master in Settings |
| Create token 403 | Device key cannot mint keys — use master |

---

## API reference

```http
POST /v1/keys          # admin — mint (returns token once)
GET  /v1/keys          # admin — list metadata
DELETE /v1/keys/{id}?revoke=true
```

Scopes: `admin` | `write` | `read` | `pair` | `push` — see [API_KEYS.md](API_KEYS.md).
