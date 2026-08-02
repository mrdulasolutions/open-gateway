# Example: Claude Code + Grok on one feature

## Setup

```bash
# Terminal A
cd /Users/mrdulasolutions/Code/OpenGateway
uv run opengateway serve

# Terminal B
uv run opengateway create-room "login-button" \
  --goal "Add a polished login button to the marketing page" \
  --project-path /path/to/web-app
```

Note the printed **room id**.

## Claude Code (coordinator)

With MCP configured (`configs/mcp.claude.json`):

1. `join_room(room_id, name="claude-code", harness="claude-code", role="coordinator")`
2. Save `participant_id`
3. `create_task(..., title="Implement LoginButton component")`
4. `create_task(..., title="Wire button to auth route")`
5. `post_message(..., content="Grok — take the component task; I'll review and wire routes.")`

## Grok (implementer)

With MCP configured (`configs/mcp.grok.json`):

1. `join_room(room_id, name="grok", harness="grok", role="contributor")`
2. `poll_messages` / `list_tasks`
3. `claim_task` on the component task
4. Implement in the real repo
5. `share_artifact(name="src/components/LoginButton.tsx", content=...)`
6. `complete_task` + `post_message` that the component is ready

## Claude Code (finish)

1. `poll_messages`
2. Claim route-wiring task
3. Integrate Grok's artifact
4. `complete_task` remaining work
5. `run_acp_agent("room-facilitator", "room:<id>")` for a closing brief

## Pure HTTP alternative

Agents without MCP can use curl against the same APIs — see README and `/docs`.
