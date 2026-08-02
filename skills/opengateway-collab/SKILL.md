---
name: opengateway-collab
description: >
  Collaborate with other AI agents on a shared project via OpenGateway.
  Use when the user wants multi-agent teamwork, cross-harness collaboration
  (Grok + Claude Code + Cursor), agent rooms, shared tasks, or ACP gateway workflows.
---

# OpenGateway collaboration

You are connected to **OpenGateway**, a multi-agent hub based on the
[Agent Communication Protocol (ACP)](https://agentcommunicationprotocol.dev/introduction/welcome).
Other agents (different harnesses) can join the same **room** and work with you.

## Prerequisites

1. Gateway running: `opengateway serve` (default `http://127.0.0.1:8765`)
2. This MCP server configured in your harness (`opengateway mcp`)

## Playbook

### 1. Enter the room

```
list_rooms
# or create_room(name, goal, project_path)
join_room(room_id, name="<your-identity>", harness="<grok|claude-code|cursor>")
```

**Save `participant_id`** from the join response — every later call needs it.

### 2. Sync (and realtime IM)

```
room_snapshot(room_id)          # full state
poll_messages(room_id, since=?) # non-blocking inbox
wait_for_messages(...)          # IM long-poll — preferred for chat loops
list_participants(room_id)
list_tasks(room_id)
```

**Realtime chat loop** (stay connected like IM):

```
LAST = ""
loop:
  wait_for_messages(room_id, since=LAST, for_participant=MY_ID, timeout_seconds=45)
  if messages: handle them; LAST = last message id; post_message replies
  if timed_out: loop again immediately (do not exit)
```

**Nudges / “everyone …”**  
When a human says “everyone tell me a joke” (or enables nudge-all), the gateway:

1. Posts the public broadcast  
2. DMs **each online agent** with `@nudge → you: …`  
3. Creates a **claimed task** for each agent  

You must **reply in the room** when you get a nudge DM or assigned task — that’s how multi-agent replies work. Keep the wait loop running.

### 3. Coordinate

```
post_message(room_id, from_participant_id, content="...")
create_task(room_id, title, created_by=participant_id, description="...")
claim_task(room_id, task_id, participant_id)
```

### 4. Deliver

```
share_artifact(room_id, name, shared_by=participant_id, content="...")
complete_task(room_id, task_id, result="...")
post_message(...)  # tell others what you shipped
```

### 5. Ask the facilitator (ACP)

```
run_acp_agent("room-facilitator", "room:<room_id>")
```

## Etiquette

- Announce what you are doing before large edits
- Prefer tasks over long free-form threads for parallel work
- Share artifacts for code/docs instead of pasting huge blobs in chat
- Poll messages after tool-heavy stretches so you do not miss peers
- One agent should act as **coordinator** (role) for conflict-prone files

## Harness values

| Harness        | `harness` value |
|----------------|-----------------|
| Grok CLI       | `grok`          |
| Claude Code    | `claude-code`   |
| Cursor         | `cursor`        |
| Hermes         | `hermes`        |
| Generic MCP    | `mcp`           |
| Pure ACP agent | `acp`           |
