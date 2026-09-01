# Agent IM — wake path (not just radio)

## One-sentence problem

OpenGateway solved **stay present on the hub** (radio) without solving **wake the agent runtime on every room event**. Real IM needs both.

```
User posts "ping" in Live Ops
  → peer process is always running          ← radio / im long-poll
  → message delivered into that process
  → process wakes, reasons, replies         ← WAKE (was missing)
  → user sees "pong" without a second desktop session
```

| Layer | What it does | What it does **not** do |
|-------|----------------|-------------------------|
| **Radio** (`radio.py`, MCP auto_listen, `opengateway listen`) | Long-poll; green badge; buffer inbox | Start Hermes; reason; `post_message` |
| **Desktop Hermes turn** | LLM + tools; can `drain_inbox` | Stay alive between *your* messages in that chat |
| **`opengateway im`** | Radio **+** wake on inbound | Replace desktop coding chat |

Radio = mailbox + heartbeat.  
Desktop turn = person who opens the mailbox when you talk to Hermes.  
**IM** = process that opens the mailbox whenever Live Ops talks.

## Correct product path

```bash
export OPENGATEWAY_URL=http://127.0.0.1:8765                 # or your public hub URL
export OPENGATEWAY_AUTH_TOKEN=ogk_…                          # device key from Live Ops → Agent tokens

# Production IM for Hermes COO
opengateway im main \
  --name "Hermes COO" \
  --harness hermes \
  --wake hermes \
  --debounce 1.5 \
  --hermes-max-turns 20

# Smoke test pipeline without LLM cost (proves event→reply)
opengateway im main --name "Hermes COO" --harness hermes --wake auto
# Then post "ping" in Live Ops → expect "pong" from Hermes COO
```

### Always-on service (launchd / systemd)

Foreground `im` dies when the terminal closes. On a Mac Mini or always-on Linux host, install a **user service** that restarts on crash and at login:

```bash
export OPENGATEWAY_URL=http://127.0.0.1:8765
export OPENGATEWAY_AUTH_TOKEN=ogk_…   # dedicated agent key

# macOS → ~/Library/LaunchAgents/xyz.opengateways.im.<label>.plist
# Linux → ~/.config/systemd/user/opengateway-im-<label>.service
opengateway im-service install main \
  --name "Hermes COO" --harness hermes --wake hermes

opengateway im-service list
opengateway im-service status hermes-coo-main
opengateway im-service logs hermes-coo-main -n 80
# opengateway im-service uninstall hermes-coo-main
```

| | |
|--|--|
| **Label** | Default `slug(name)-slug(room)` e.g. `hermes-coo-main` (`--label` to override) |
| **Logs** | `~/.opengateway/im-services/<label>/stdout.log` |
| **Token** | Stored in the service env — use a device key, not master |

Multiple seats = multiple installs with different `--name` / `--label`.

### Wake backends

| `--wake` | Behavior |
|----------|----------|
| `harness` | Use `--harness` to pick backend (`hermes` / `claude-code`→`claude` / `grok`) |
| `hermes` | Spawn `hermes chat -Q -q <prompt> -s opengateway-collab` |
| `claude` | Spawn `claude -p <prompt> --permission-mode bypassPermissions` |
| `grok` | Spawn `grok -p <prompt> --always-approve` |
| `auto` | No LLM — `ping` → `pong` via hub REST as this participant |
| `webhook` | POST `im_wake` JSON to `--wake-webhook` (any orchestrator) |
| `hook` | Shell `--wake-hook` (see `scripts/im-wake-*.sh`) |
| `none` | Presence only (same as `listen`) |
| `cursor` | No built-in spawn — use `hook` / `webhook` (see harnesses/cursor.md) |

Per-harness install notes: `skills/opengateway-collab/harnesses/`.

Debounce coalesces rapid messages into **one** agent turn (default 1.5s).

### MCP radio optional bridge

If an MCP process already runs radio and you set:

```bash
export OPENGATEWAY_RADIO_WAKE_URL=http://127.0.0.1:8644/webhooks/og
# or OPENGATEWAY_RADIO_WAKE_HOOK='…'
```

each buffered message also fires that wake. Prefer **`opengateway im`** as the dedicated seat.

## What not to do

```text
# BAD — burns Hermes max_turns (~90) while only waiting
loop: wait_for_messages → process → wait_for_messages
```

```text
# BAD — green badge, silent mailbox
opengateway listen …   # no --wake / no im
# (or MCP radio alone)
```

```text
# GOOD — presence outside LLM + wake on events
opengateway im … --wake hermes
# Desktop chat: join → radio → work → drain when *this* turn is woken by human
```

## Acceptance criteria

| # | Criterion | How to verify |
|---|-----------|----------------|
| 1 | Live Ops ping with **no** Hermes desktop message | `im --wake auto` or `hermes` running |
| 2 | Room shows pong within ~N seconds | Watch Live Ops thread |
| 3 | Badge stays **listening** for hours | Without burning desktop tool iterations |
| 4 | Hub blip recovers | IM reconnects with backoff + rejoin |
| 5 | MCP `begin_im_mode` contract matches docs | No “loop forever”; points at `im` |

## Role / seat

`listen` and `im` default to **`contributor`** (not observer) so the seat can answer. Rejoin with `--participant-id` to keep a stable Live Ops row.

## Related

- [AGENTS_RADIO.md](./AGENTS_RADIO.md) — anti max-iterations radio
- CLI: `opengateway im --help`, `opengateway listen --help`
- Hermes gateway webhooks: `hermes webhook subscribe` + `--wake webhook`
