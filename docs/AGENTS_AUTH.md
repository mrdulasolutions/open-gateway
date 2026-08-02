# Production auth: humans, agents, MCP

How authentication works when OpenGateway is on **Railway** (or any public gateway).

## Mental model

```text
┌─────────────────────────────────────────────────────────────┐
│  Gateway (require_auth = true)                              │
│                                                             │
│  Humans: email + password → session token (ogs_…)           │
│    · first user = org admin + creates tenant                │
│    · more users via open registration or invite code        │
│                                                             │
│  Master token (OPENGATEWAY_AUTH_TOKEN) — ops / Railway      │
│  Agent tokens (ogs UI mint / POST /v1/keys) — MCP harnesses │
└─────────────────────────────────────────────────────────────┘
         │                    │                    │
         ▼                    ▼                    ▼
   Live Ops login page   Grok MCP              Claude / Cursor
   (session ogs_…)       (device ogk_…)         (device ogk_…)
```

| Actor | How they authenticate |
|-------|------------------------|
| **You (browser)** | **Login page** (email/password) — no more paste-token for day-to-day |
| Additional humans | Register (open) or invite code from admin |
| Phone | Pair QR or login |
| Grok / Claude / Cursor | MCP env: URL + **agent token** from Live Ops |
| REST / scripts | Bearer master, session, or device key |

**Does login fix the Unauthorized banner?** Yes for humans: after Sign in / Create admin, the browser stores a session token and API calls succeed. Agents still need minted device keys.

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
| MCP handshake failed / process exits | Pin `mcp>=1.0,<2` and `uv sync` — MCP Python SDK 2.x removed `FastMCP` |
| MCP tools 401 | Set `OPENGATEWAY_AUTH_TOKEN` in **MCP client** env (not only hub); restart harness |
| `/ping` OK but `/v1/*` 401 | Token missing on client — hub allowlists `/ping` without auth |
| `Unknown from_participant_id` | Use `id` (or `participant_id`) returned by `join_room` in `post_message` |
| Tools missing mid-session | Config write does not reload MCP — **restart** Grok / Claude / Cursor |
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
