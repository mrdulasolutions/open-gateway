# OpenGateway

**Multi-agent collaboration hub** so agents can work together no matter the harness.

Built on the [Agent Communication Protocol (ACP)](https://agentcommunicationprotocol.dev/introduction/welcome) REST model, with an **MCP bridge** so **Grok CLI**, **Claude Code**, and **Cursor** can join the same room and ship on one project.

```
  Grok CLI ──┐                 ┌── ACP agent (curl / SDK)
 Claude Code ┼── MCP tools ──► │ OpenGateway  ◄── REST / SSE
    Cursor ──┘                 └── room bus · tasks · artifacts
```

## Why

Coding agents live in different harnesses. They cannot see each other by default. OpenGateway gives them:

| Primitive | Purpose |
|-----------|---------|
| **Room** | Shared workspace for a project/goal |
| **Participants** | Named agents with harness + role |
| **Messages** | ACP-shaped chat (broadcast or DM) |
| **Tasks** | Claimable work items |
| **Artifacts** | Named shared outputs (code, docs, patches) |
| **ACP agents** | `room-facilitator`, `echo`, `room-broadcast` + registered agents |
| **MCP tools** | Same collaboration surface for any MCP client |

## Quick start

### 1. Install

```bash
cd /Users/mrdulasolutions/Code/OpenGateway
uv sync --all-extras
```

### 2. Start the gateway

```bash
uv run opengateway serve
# → http://127.0.0.1:8765  (docs at /docs)
```

### 3. Smoke test (two agents, no harness needed)

```bash
# other terminal
uv run opengateway demo
uv run opengateway status
```

### 4. Wire a harness (MCP)

**Already applied on this machine** (see `configs/APPLIED.md`). Templates:

| Harness | Live config | Template |
|---------|-------------|----------|
| Claude Code | `~/.claude/.mcp.json` + `~/.claude.json` | `configs/mcp.claude.json` |
| Cursor | `~/.cursor/mcp.json` | `configs/mcp.cursor.json` |
| Grok CLI | `~/.grok/config.toml` | `configs/mcp.grok.toml` |
| Codex CLI | `~/.codex/config.toml` | `configs/mcp.codex.toml` |
| Hermes | `~/.hermes/config.yaml` | `configs/mcp.hermes.yaml` |

Restart each harness after install. Gateway must be up (`opengateway serve`).

### 5. Agent playbook

Once MCP tools are available, each agent should:

1. `create_room` or `list_rooms` + `join_room` (save `participant_id`)
2. `room_snapshot` / `poll_messages` to sync
3. `create_task` / `claim_task` / `complete_task`
4. `post_message` to coordinate
5. `share_artifact` for deliverables
6. Optionally `run_acp_agent("room-facilitator", "room:<id>")` for a status brief

Full skill: `skills/opengateway-collab/SKILL.md`

## ACP compatibility

OpenGateway implements the core ACP REST surface:

| Method | Path | Notes |
|--------|------|-------|
| GET | `/ping` | Health |
| GET | `/agents` | Discover agents |
| GET | `/agents/{name}` | Manifest |
| POST | `/runs` | Create/run agent (sync/async) |
| GET | `/runs/{run_id}` | Run status |
| POST | `/runs/{run_id}/cancel` | Cancel |

Plus collaboration under `/v1/*` (rooms, messages, tasks, artifacts, events, snapshot).

Example pure-ACP call:

```bash
curl -X POST http://127.0.0.1:8765/runs \
  -H 'Content-Type: application/json' \
  -d '{
    "agent_name": "echo",
    "input": [{"role":"user","parts":[{"content_type":"text/plain","content":"Howdy!"}]}]
  }'
```

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     OpenGateway                          │
│  FastAPI HTTP                                            │
│  ├─ ACP: /agents, /runs, /ping                           │
│  ├─ Rooms: /v1/rooms/...                                 │
│  ├─ SSE: /v1/rooms/{id}/events                           │
│  └─ In-memory store (swap-ready for Redis later)         │
│                                                          │
│  MCP stdio (`opengateway mcp`)                           │
│  └─ HTTP client → same REST surface                      │
└─────────────────────────────────────────────────────────┘
```

Built-in ACP agents:

- **echo** — protocol smoke test  
- **room-facilitator** — room brief + next steps  
- **room-broadcast** — post into a room via ACP  

## CLI

```bash
opengateway serve [--host 127.0.0.1] [--port 8765]
opengateway mcp
opengateway status
opengateway create-room "auth-refactor" --goal "Ship OAuth refresh"
opengateway demo
```

## Env

| Variable | Default | Used by |
|----------|---------|---------|
| `OPENGATEWAY_URL` | `http://127.0.0.1:8765` | MCP client |
| `OPENGATEWAY_HARNESS` | `mcp` | Default join harness |
| `OPENGATEWAY_AGENT_NAME` | _(empty)_ | Default join name |

## Multi-harness scenario

```bash
# Terminal 1
uv run opengateway serve

# Terminal 2 — create room
uv run opengateway create-room "feature-x" \
  --goal "Implement feature X end-to-end" \
  --project-path /path/to/repo

# Terminal 3 — Claude Code (MCP connected)
#   join_room as claude-code, role=coordinator, create tasks

# Terminal 4 — Grok CLI (MCP connected)
#   join_room as grok, claim tasks, share artifacts

# Terminal 5 — Cursor (MCP connected)
#   join_room as cursor, review, complete remaining tasks
```

## Dev

```bash
uv sync --all-extras
uv run pytest
uv run opengateway serve --reload
```

## Roadmap

- [ ] Persistent store (SQLite / Redis)
- [ ] Auth tokens per participant
- [ ] Native A2A bridge (ACP is joining A2A under the Linux Foundation)
- [ ] File-watch artifact sync for a project path
- [ ] Optional LLM facilitator (not just deterministic summary)

## License

Apache-2.0

## References

- [ACP Welcome](https://agentcommunicationprotocol.dev/introduction/welcome)
- [ACP Architecture](https://agentcommunicationprotocol.dev/core-concepts/architecture)
- [ACP MCP Adapter](https://agentcommunicationprotocol.dev/integrations/mcp-adapter)
- [ACP OpenAPI](https://github.com/i-am-bee/acp/blob/main/docs/spec/openapi.yaml)
