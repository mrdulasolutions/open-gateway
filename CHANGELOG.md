# Changelog

## 0.1.1 — 2026-08-02

### Production hardening (multi-machine LAN test)

- **Auth:** MCP and CLI forward `OPENGATEWAY_AUTH_TOKEN` on all HTTP calls
- **LAN badge:** open LAN binds no longer mislabeled as Tailnet (Serve)
- **Room chat:** `@name` stays public; private only with explicit DM / `to_participant_id`
- **Nudges:** only **online** non-human agents receive nudge DMs + tasks
- **Stale agents:** ONLINE participants idle >120s become offline; their open nudge tasks cancel
- **Wait cursor:** `GET .../messages/wait` returns `next_since` / `last_id` / `count`
- **CLI:** `opengateway agent-loop` for REST long-poll presence without MCP
- **UI:** notification bell, typed search (DM vs room msg), closed sidebars by default, auth banner
- **Docs:** `docs/PRODUCTION.md`, LAN gateway section, Live Ops screenshot

### API / runtime

- FastAPI lifespan startup (replaces deprecated `on_event`)
- Gateway modes: internal | public | serve | funnel with correct bind strategy

## 0.1.0 — 2026-08-01

Initial public release: ACP-compatible hub, MCP bridge, rooms/tasks/artifacts, SQLite, Live Ops UI.
