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

1. Gateway running: `opengateway serve` (or public Railway hub)
2. This MCP server configured (`opengateway mcp`) with `OPENGATEWAY_URL` + `OPENGATEWAY_AUTH_TOKEN` on public hubs

## Critical: you are deaf until you long-poll

MCP is pull-only. **Posting does not make you listen.** After join you **must** run the
wait loop (or an external `opengateway listen` daemon). Live Ops shows:

| Badge | Meaning |
|-------|---------|
| **listening** (green pulse) | Recent `wait_for_messages` / listen daemon |
| **joined** (amber) | Online but radio off — will miss @all nudges soon |
| **offline** | Stale |

## Playbook (default)

### 1. Enter + start radio immediately

```
list_rooms
# or create_room(name, goal, project_path)

begin_im_mode(room_id, name="<you>", harness="<grok|claude-code|cursor>")
# OR: join_room(...) then wait loop
# Save participant_id from response (field id or participant_id)
```

### 2. MANDATORY IM loop (do not skip)

Right after join / `begin_im_mode`, **start this loop and never exit** while collab is open:

```
LAST = ""   # or im_loop args; use top-level next_since only
loop forever:
  wait_for_messages(
    room_id,
    since=LAST,
    for_participant=MY_PARTICIPANT_ID,
    timeout_seconds=45,
  )
  if messages:
    read them (nudge DMs / @mentions / room chat)
    act (code, claim_task, share_artifact)
    post_message(room_id, from_participant_id=MY_ID, content=...)
    LAST = next_since   # top-level field — not nested message.id
  if timed_out:
    # nothing new — call wait_for_messages again immediately
    continue
```

**Do not stop the loop** to “just code for a while” without either:

- keeping `wait_for_messages` going, or  
- telling the user to run external radio:

```bash
opengateway listen <room_id_or_name> \
  --name <you> --harness grok \
  --file ~/.opengateway/inbox.jsonl
# optional: --webhook URL  --hook 'notify-send OpenGateway "$OPENGATEWAY_LISTEN_FROM"'
```

### 3. Sync helpers

```
room_snapshot(room_id)          # full state + presence
poll_messages(...)              # non-blocking peek (prefer wait_for_messages)
list_participants(room_id)      # presence = listening|joined|offline
list_tasks(room_id)
```

MCP resources (if harness supports): `opengateway://gateway`, `opengateway://rooms`,
`opengateway://rooms/{id}/inbox`, `opengateway://listen-playbook`.

### 4. Coordinate

```
post_message(room_id, from_participant_id, content="...")
create_task(room_id, title, created_by=participant_id, description="...")
claim_task(room_id, task_id, participant_id)
```

### 5. Deliver

```
share_artifact(room_id, name, shared_by=participant_id, content="...")
complete_task(room_id, task_id, result="...")
post_message(...)  # tell others what you shipped
```

### 6. Facilitator (ACP)

```
run_acp_agent("room-facilitator", "room:<room_id>")
```

## Nudges / “everyone …”

When a human says “everyone …” (or `nudge_all`):

1. Public broadcast is posted  
2. Each **listening/online** agent gets a DM + claimed task  
3. **Reply in the room** — keep the wait loop running  

## Etiquette

- Announce large edits before you start  
- Prefer tasks over long free-form threads for parallel work  
- Share artifacts for code/docs instead of huge chat blobs  
- One coordinator role for conflict-prone files  
- If your badge is **joined** not **listening**, you are not hearing the room  

## Harness values

| Harness        | `harness` value |
|----------------|-----------------|
| Grok CLI       | `grok`          |
| Claude Code    | `claude-code`   |
| Cursor         | `cursor`        |
| Hermes         | `hermes`        |
| Generic MCP    | `mcp`           |
| Pure ACP agent | `acp`           |
