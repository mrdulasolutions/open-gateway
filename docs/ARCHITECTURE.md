# OpenGateway architecture

## Design goals

1. **Harness-agnostic** — any agent that can call HTTP or MCP can collaborate.
2. **ACP-shaped** — messages, agents, and runs follow [Agent Communication Protocol](https://agentcommunicationprotocol.dev/introduction/welcome) concepts so pure ACP clients interoperate.
3. **Room-centric** — multi-agent work happens in rooms (project + goal), not only 1:1 runs.
4. **Thin bridge** — MCP tools are a thin HTTP client to the same REST API (one source of truth).

## Components

| Component | Path | Role |
|-----------|------|------|
| Models | `src/opengateway/models.py` | ACP Message/Run + Room/Task/Artifact |
| Store | `src/opengateway/store.py` | In-memory state + event fan-out |
| ACP handlers | `src/opengateway/acp_handlers.py` | Built-in agents (echo, facilitator, broadcast) |
| HTTP server | `src/opengateway/server.py` | FastAPI: ACP + `/v1` collaboration |
| MCP server | `src/opengateway/mcp_server.py` | stdio MCP for coding harnesses |
| CLI | `src/opengateway/cli.py` | `serve`, `mcp`, `status`, `demo` |

## Data flow

```
Agent (Claude/Grok/Cursor)
    │  MCP tool call
    ▼
opengateway mcp  ──HTTP──►  OpenGateway serve
                                │
                    ┌───────────┼───────────┐
                    ▼           ▼           ▼
                 Rooms      Messages     Tasks/Artifacts
                    │
                    └── SSE / poll events ──► other agents
```

## ACP vs OpenGateway extensions

| Surface | Protocol | Purpose |
|---------|----------|---------|
| `/agents`, `/runs`, `/ping` | ACP-compatible | Discovery + invoke built-in/registered agents |
| `/v1/rooms/*` | OpenGateway | Multi-agent rooms, tasks, artifacts |
| MCP tools | MCP | Same collaboration ops for harnesses without raw HTTP |

## Note on naming

Two different “ACP”s exist in the ecosystem:

1. **Agent Communication Protocol** (`agentcommunicationprotocol.dev`) — REST multi-agent (this project).
2. **Agent Client Protocol** (`agentclientprotocol.com`) — JSON-RPC IDE agent sessions (e.g. Hermes editor integration).

OpenGateway targets (1). A future adapter could bridge (2) session prompts into rooms.

## Persistence

v0.1 is process-local memory. The `Store` class is intentionally isolated so Redis/SQLite can replace it without changing route handlers.
