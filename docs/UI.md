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

## Layout

```
OpsSidebar │  topbar + GlobalSearch (⌘K) + Live badge
 (rooms,   │  AgentChat (messages + composer)
  DMs,     │  SideRail (participants / tasks / artifacts / events)
  forks,
  Gateways
   Internal / Public,
  Settings + auth token)
```

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
| `src/components/agent-chat/AgentChat.tsx` | 21st Agent Chat adaptation |
| `src/components/agent-chat-21st-source.tsx` | Raw dump from `21st get` (reference) |
| `src/components/layout/OpsSidebar.tsx` | Rooms, DMs, forks, gateway cards, settings |
| `src/components/GlobalSearch.tsx` | Predictive global search |
| `src/components/layout/SideRail.tsx` | Right ops rail |
| `src/App.tsx` | Room state, SSE, join/rename/send |
| `src/lib/api.ts` | REST client (+ bearer token) |
