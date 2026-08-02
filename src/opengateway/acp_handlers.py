"""Built-in ACP agents hosted by OpenGateway."""

from __future__ import annotations

from typing import Any

from opengateway.models import Message, Run, RunStatus, text_message, utcnow
from opengateway.store import Store


def _text(role: str, content: str) -> Message:
    return text_message(role, content)


BUILTIN_AGENTS: list[dict[str, Any]] = [
    {
        "name": "room-facilitator",
        "description": (
            "OpenGateway facilitator. Summarizes a room's state, participants, open tasks, "
            "and recent messages. Pass room_id in input text as 'room:<id>' or plain room id."
        ),
        "metadata": {"org.open-gateway/kind": "facilitator"},
    },
    {
        "name": "room-broadcast",
        "description": (
            "Post a message into a room on behalf of a system/agent. "
            "Input: first line 'room:<id>', second line 'from:<participant_id>', rest is body."
        ),
        "metadata": {"org.open-gateway/kind": "broadcast"},
    },
    {
        "name": "echo",
        "description": "Echoes input back (health / ACP smoke test).",
        "metadata": {"org.open-gateway/kind": "utility"},
    },
]


def _extract_text(messages: list[Message]) -> str:
    return "\n".join(m.text() for m in messages).strip()


def _parse_kv_header(text: str) -> tuple[dict[str, str], str]:
    """Parse leading key:value lines until a blank line or non-kv line."""
    headers: dict[str, str] = {}
    lines = text.splitlines()
    body_start = 0
    for i, line in enumerate(lines):
        if ":" in line and not line.startswith(" "):
            key, _, val = line.partition(":")
            key = key.strip().lower()
            if key in {"room", "room_id", "from", "from_participant_id", "to"}:
                headers[key] = val.strip()
                body_start = i + 1
                continue
        body_start = i
        break
    else:
        body_start = len(lines)
    body = "\n".join(lines[body_start:]).strip()
    # Normalize aliases
    if "room_id" in headers and "room" not in headers:
        headers["room"] = headers["room_id"]
    if "from_participant_id" in headers and "from" not in headers:
        headers["from"] = headers["from_participant_id"]
    return headers, body


async def run_echo(run: Run, messages: list[Message], store: Store) -> Run:
    run.status = RunStatus.IN_PROGRESS
    text = _extract_text(messages) or "(empty)"
    run.output = [_text("agent/echo", text)]
    run.status = RunStatus.COMPLETED
    run.finished_at = utcnow()
    await store.save_run(run)
    return run


async def run_room_facilitator(run: Run, messages: list[Message], store: Store) -> Run:
    run.status = RunStatus.IN_PROGRESS
    text = _extract_text(messages)
    headers, body = _parse_kv_header(text)
    room_id = headers.get("room") or body.strip() or None

    if not room_id:
        # Pick the most recently updated active room if only one exists
        rooms = await store.list_rooms()
        if len(rooms) == 1:
            room_id = rooms[0].id
        else:
            run.output = [
                _text(
                    "agent/room-facilitator",
                    "Provide a room id (e.g. `room:<uuid>`) or create/join a room first.\n"
                    f"Known rooms: {', '.join(r.name + '=' + r.id for r in rooms) or '(none)'}",
                )
            ]
            run.status = RunStatus.COMPLETED
            run.finished_at = utcnow()
            await store.save_run(run)
            return run

    room = await store.get_room(room_id)
    if not room:
        run.output = [_text("agent/room-facilitator", f"Room not found: {room_id}")]
        run.status = RunStatus.FAILED
        run.error = "room_not_found"
        run.finished_at = utcnow()
        await store.save_run(run)
        return run

    participants = await store.list_participants(room_id)
    tasks = await store.list_tasks(room_id)
    messages_list = await store.list_messages(room_id, limit=12)
    artifacts = await store.list_artifacts(room_id)

    open_tasks = [t for t in tasks if t.status.value in {"open", "claimed", "in_progress", "blocked"}]
    lines = [
        f"# Room: {room.name}",
        f"id: {room.id}",
        f"status: {room.status.value}",
        f"goal: {room.goal or '(none)'}",
        f"project_path: {room.project_path or '(none)'}",
        "",
        f"## Participants ({len(participants)})",
    ]
    for p in participants:
        lines.append(f"- {p.name} [{p.harness.value}] role={p.role} status={p.status.value} id={p.id}")
    if not participants:
        lines.append("- (none)")

    lines.append("")
    lines.append(f"## Open / active tasks ({len(open_tasks)})")
    for t in open_tasks:
        owner = t.claimed_by or "unclaimed"
        lines.append(f"- [{t.status.value}] {t.title} (id={t.id}, by={owner})")
    if not open_tasks:
        lines.append("- (none)")

    lines.append("")
    lines.append(f"## Recent messages ({len(messages_list)})")
    for m in messages_list[-8:]:
        target = f" → {m.to_participant_id}" if m.to_participant_id else ""
        lines.append(f"- {m.from_name}{target}: {m.message.text()[:200]}")
    if not messages_list:
        lines.append("- (none yet — agents should post_message after joining)")

    lines.append("")
    lines.append(f"## Artifacts ({len(artifacts)})")
    for a in artifacts:
        lines.append(f"- {a.name} ({a.content_type}) shared_by={a.shared_by}")
    if not artifacts:
        lines.append("- (none)")

    lines.append("")
    lines.append("## Suggested next steps")
    if len(participants) < 2:
        lines.append("1. Invite another agent to join this room via MCP `join_room`.")
    if open_tasks:
        lines.append("1. Unclaimed work: agents should `claim_task` then `complete_task`.")
    else:
        lines.append("1. Coordinator should `create_task` to decompose the goal.")
    lines.append("2. Use `post_message` for coordination; share outputs via `share_artifact`.")
    lines.append("3. Poll with `poll_messages` so every harness stays in sync.")

    run.output = [_text("agent/room-facilitator", "\n".join(lines))]
    run.status = RunStatus.COMPLETED
    run.finished_at = utcnow()
    await store.save_run(run)
    return run


async def run_room_broadcast(run: Run, messages: list[Message], store: Store) -> Run:
    from opengateway.models import RoomMessage

    run.status = RunStatus.IN_PROGRESS
    text = _extract_text(messages)
    headers, body = _parse_kv_header(text)
    room_id = headers.get("room")
    from_id = headers.get("from")

    if not room_id or not from_id or not body:
        run.output = [
            _text(
                "agent/room-broadcast",
                "Usage:\nroom:<room_id>\nfrom:<participant_id>\n\n<message body>",
            )
        ]
        run.status = RunStatus.FAILED
        run.error = "invalid_input"
        run.finished_at = utcnow()
        await store.save_run(run)
        return run

    participant = await store.get_participant(from_id)
    if not participant:
        run.output = [_text("agent/room-broadcast", f"Unknown participant: {from_id}")]
        run.status = RunStatus.FAILED
        run.error = "unknown_participant"
        run.finished_at = utcnow()
        await store.save_run(run)
        return run

    msg = RoomMessage(
        room_id=room_id,
        from_participant_id=from_id,
        from_name=participant.name,
        to_participant_id=headers.get("to"),
        message=_text(f"agent/{participant.name}", body),
    )
    await store.post_message(msg)
    run.output = [_text("agent/room-broadcast", f"Posted message {msg.id} to room {room_id}")]
    run.status = RunStatus.COMPLETED
    run.finished_at = utcnow()
    await store.save_run(run)
    return run


async def dispatch_acp_run(run: Run, messages: list[Message], store: Store) -> Run:
    handlers = {
        "echo": run_echo,
        "room-facilitator": run_room_facilitator,
        "room-broadcast": run_room_broadcast,
    }
    handler = handlers.get(run.agent_name)
    if not handler:
        run.status = RunStatus.FAILED
        run.error = f"unknown_agent:{run.agent_name}"
        run.output = [_text("agent/opengateway", f"Unknown agent: {run.agent_name}")]
        run.finished_at = utcnow()
        await store.save_run(run)
        return run
    return await handler(run, messages, store)
