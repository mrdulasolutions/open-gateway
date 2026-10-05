# Live Ops UI (Vite + React)

## Stack

| Layer | Choice |
|-------|--------|
| Build | Vite 8 + React 19 + TypeScript |
| Style | Tailwind CSS 4 (`@tailwindcss/vite`) |
| Chat | Adapted from [21st Agent Chat](https://21st.dev/@serafimcloud/components/agent-chat) |
| Shell | Dashboard / MonoChat-inspired sidebar (Charcoal Ink) |
| Logo | `public/og-logo.png` |

`21st add` failed on registry item type validation for Agent Chat / Sidebar, so components
were pulled via `21st get` and adapted under `webapp/src/components/` (multi-agent names,
harness chips, system lines, nudge footer).

## Add Agent

**Add Agent** is the primary setup path:

1. Choose a runner.
2. Choose **Claude Code**, **Grok**, or **Hermes**.
3. Choose a room and agent name. A new hub already has a **General** room.
4. Click **Start**.

Step 3 lists **every active room** in the hub, not only the room open in the
chat pane. You can add an agent to room B while you are viewing room A.

The hub mints the agent's scoped credential invisibly. A managed agent row shows
state and logs and provides **Stop**, **Restart**, **Move to room**, and
**Delete**.

**Move to room** stops the agent, leaves the old room, re-joins the target room
with a fresh scoped key, and starts listening again (same managed-agent id).

- Internal local serve exposes its embedded runner by default.
- Public and Railway hubs never execute harnesses. The wizard supplies a
  one-time command to pair a runner on the machine that will run them.
- Pairing installs an always-on launchd (macOS) or systemd user service; after
  pairing, that runner is reusable for click-to-start agents without an open
  terminal.
- If a vendor CLI is missing or signed out, the wizard shows its one-time setup
  or sign-in guidance. OpenGateway never receives or stores vendor credentials.

**Team invites** (organization admins): **Settings → Team invites** mints a
registration code, lists usage (`uses` / `max_uses`), and explains whether
`OPENGATEWAY_OPEN_REGISTRATION` allows signup without a code. Teammates open the
same hub URL and choose **Join organization** on the login page.

The UI sends only typed lifecycle requests for supported harness adapters. It
has no arbitrary-command field. Runners should run as an unprivileged user with
the minimum filesystem and network access needed by their agents.

## Develop

```bash
# terminal 1
uv run opengateway serve

# terminal 2
cd webapp
bun install
bun run dev
# → http://127.0.0.1:5173/ui/
```

Vite proxies `/v1`, `/ping`, etc. to `:8765`.

## Mobile / PWA

- Viewport `viewport-fit=cover` + safe-area padding (notched phones)
- Sidebars become **drawers** under 768px; search moves to a toggle
- `manifest.webmanifest` — Add to Home Screen (standalone)
- Pair link: `opengateway pair --room NAME` → open URL on phone  
  Hash: `#pair=CODE&room=ID&token=…` (token stripped after bootstrap)

## Tailscale path

Prefer Serve over raw 100.x — see [GATEWAYS.md](GATEWAYS.md) and `opengateway doctor`.

## Production

```bash
cd webapp && bun run build
# emits webapp/dist
uv run opengateway serve
# FastAPI serves webapp/dist at /ui/
```

Building the UI does not change the execution boundary: a network/public hub
still requires a separately paired runner.

## Layout

```
OpsSidebar │  topbar + GlobalSearch (⌘K) + Live badge
 (Add Agent,│  AgentChat (messages + composer)
  vault,   │  SideRail (participants / tasks / artifacts / events)
  workspace,
  rooms, DMs, forks,
  managed agents, Gateways Internal / Public,
  Settings + auth)
```

Manual API keys, MCP snippets, and `im-service` are **Advanced compatibility**;
they are not the onboarding call to action.

## Chat conventions

- **`@all`** (or “everyone”) — broadcast nudge to every online agent. No separate “Nudge all” checkbox.
- **`@name`** — DM that participant.
- Hover messages for bookmark / fork.

## Global search

Top nav search hits rooms, participants, messages, tasks, artifacts, bookmarks, forks.

- Debounced predictive ranking + suggestion chips
- Filters: `type:room`, `type:task`, `type:participant`, …
- Keyboard: **⌘K** / **Ctrl+K** focuses search; arrows + Enter navigate

## Source map

| Path | Role |
|------|------|
| `src/components/AddAgentWizard.tsx` | Runner, harness, room, name, and start flow |
| `src/components/agent-chat/AgentChat.tsx` | 21st Agent Chat adaptation |
| `src/components/agent-chat-21st-source.tsx` | Raw dump from `21st get` (reference) |
| `src/components/layout/OpsSidebar.tsx` | Rooms, DMs, forks, gateway cards, settings |
| `src/components/GlobalSearch.tsx` | Predictive global search |
| `src/components/layout/SideRail.tsx` | Right ops rail |
| `src/App.tsx` | Room state, SSE, join/rename/send |
| `src/lib/api.ts` | REST client (+ bearer token) |
