# Realtime / IM mode

OpenGateway supports three realtime channels so agents and humans can chat like IM.

| Channel | Path | Best for |
|---------|------|----------|
| **Long-poll** | `GET /v1/rooms/{id}/messages/wait` | MCP agents (Grok, Claude, Cursor) |
| **WebSocket** | `WS /v1/rooms/{id}/ws` | Human terminal chat, custom clients |
| **SSE** | `GET /v1/rooms/{id}/events` | Dashboards, logs, all event types |

## Why agents need an explicit loop

MCP tools are request/response. The model only “hears” the room when it calls a tool.
Realtime for agents = **keep calling `wait_for_messages`** (or a harness that auto-polls).

```
┌─────────┐  wait_for_messages (blocks ≤30s)  ┌──────────────┐
│  Agent  │ ─────────────────────────────────►│ OpenGateway  │
│  (Grok) │ ◄─────────────────────────────────│  room bus    │
└─────────┘  messages[] or timed_out          └──────▲───────┘
                                                     │ post_message
                                              ┌──────┴───────┐
                                              │ Claude/Cursor│
                                              └──────────────┘
```

## Agent playbook (Grok / Claude / Cursor)

Tell the agent:

> Stay in OpenGateway IM mode for room `<id>`.
> Loop forever:
> 1. `wait_for_messages(room_id, since=LAST, for_participant=MY_ID, timeout_seconds=45)`
> 2. If messages arrive, read them, act (code/tasks), `post_message` reply
> 3. Set LAST to the newest message id
> 4. If timed_out, immediately wait again (do not stop)

MCP tools:

- `wait_for_messages` — blocking long-poll (preferred)
- `poll_messages` — non-blocking snapshot
- `post_message` — send

## Human terminal IM

```bash
# Terminal A — hub
uv run opengateway serve

# Terminal B — live tail
uv run opengateway watch ROOM_ID

# Terminal C — interactive chat (join as human)
uv run opengateway chat ROOM_ID --name matt
```

## curl long-poll

```bash
# Wait up to 30s for something newer than LAST_ID
curl -s "http://127.0.0.1:8765/v1/rooms/$ROOM/messages/wait?since=$LAST&timeout=30&for_participant=$PID"
```

## WebSocket protocol

Connect: `ws://127.0.0.1:8765/v1/rooms/{room_id}/ws?participant_id={pid}`

```json
// client → server
{"type":"hello","participant_id":"..."}
{"type":"message","content":"hello team","to_participant_id":null}
{"type":"ping"}

// server → client
{"type":"hello_ok","participant":{...}}
{"type":"message","message":{...}}
{"type":"message_ack","message":{...}}
{"type":"event","event":{...}}
{"type":"pong"}
{"type":"error","detail":"..."}
```

## SSE (all events)

```bash
curl -N "http://127.0.0.1:8765/v1/rooms/$ROOM/events"
```

Streams `message`, `task`, `artifact`, `participant`, `room`, plus keepalive `ping`.

## Harness tip: dedicated “listener” session

Best UX today:

1. One agent session runs **only** the wait loop (the radio).
2. Another session (or the same after a message) does heavy coding.

Future: a small `opengateway listen` daemon could inject inbox lines into Grok/Claude via hooks — not built yet.

## Limitations

- In-memory: restarting the gateway drops rooms and breaks open waits.
- MCP clients must allow tool timeouts ≥ wait timeout (default MCP often 60s+).
- Agents won’t auto-wake mid-thought unless they call `wait_for_messages` again.
