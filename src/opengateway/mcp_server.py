"""MCP server — lets Grok CLI, Claude Code, Cursor join OpenGateway rooms via tools."""

from __future__ import annotations

import json
import os
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import Context, FastMCP

DEFAULT_BASE_URL = os.environ.get("OPENGATEWAY_URL", "http://127.0.0.1:8765")
DEFAULT_HARNESS = os.environ.get("OPENGATEWAY_HARNESS", "mcp")
DEFAULT_AGENT_NAME = os.environ.get("OPENGATEWAY_AGENT_NAME", "")


def _auth_token() -> str:
    """Master token or device API key (ogk_…) for public / Railway hubs."""
    return (
        os.environ.get("OPENGATEWAY_AUTH_TOKEN")
        or os.environ.get("OPENGATEWAY_TOKEN")
        or ""
    ).strip()


def _auth_headers() -> dict[str, str]:
    """Bearer token for public / Tailscale / Railway gateways."""
    token = _auth_token()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


mcp = FastMCP(
    "OpenGateway",
    instructions=(
        "OpenGateway multi-agent collaboration tools. Work with other agents "
        "(Claude Code, Grok, Cursor, Hermes, ACP) in shared rooms.\n\n"
        "AUTH: public hubs need OPENGATEWAY_URL + OPENGATEWAY_AUTH_TOKEN in MCP env.\n\n"
        "ALWAYS-ON RADIO (default):\n"
        "1. join_room or begin_im_mode once → background long-poll starts (radio ON)\n"
        "2. Do real work (code, tools, think)\n"
        "3. Occasionally drain_inbox / get_inbox (at most once per assistant turn)\n"
        "4. post_message to reply; leave_room when done\n"
        "NEVER loop wait_for_messages (burns harness max-iterations).\n"
        "True IM (auto-reply without a desktop chat): "
        "`opengateway im <room> --wake hermes` (or --wake auto).\n\n"
        "Resources: opengateway://gateway|rooms|rooms/{id}/inbox|radio|listen-playbook\n"
        "Workspace: workspace_list/read/write. Vault: list_tool_credentials + tool_proxy.\n\n"
        "Flow: list/create room → join_room (auto radio) → work → drain_inbox + post_message"
    ),
)


def _base() -> str:
    return os.environ.get("OPENGATEWAY_URL", DEFAULT_BASE_URL).rstrip("/")


def _client(timeout: float = 30.0) -> httpx.Client:
    """HTTP client for hub calls — always attaches Bearer auth when token is set.

    Long-poll tools must use this (or pass the same headers=) with a higher timeout.
    Never construct a bare httpx.Client without headers=_auth_headers().
    """
    return httpx.Client(
        base_url=_base(),
        timeout=timeout,
        headers=_auth_headers(),
    )


def _json(resp: httpx.Response) -> Any:
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        detail = e.response.text
        try:
            detail = e.response.json()
        except Exception:
            pass
        out: dict[str, Any] = {
            "error": str(e),
            "detail": detail,
            "status_code": e.response.status_code,
        }
        if e.response.status_code == 401 and not _auth_token():
            out["hint"] = (
                "Set OPENGATEWAY_AUTH_TOKEN in the MCP server env "
                "(device API key from Live Ops → Mint agent token, or master token). "
                "Restart the harness after changing config."
            )
        return out
    return resp.json()


def _dumps(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


# ── MCP resources (passive snapshot / harness resource readers) ─────────────


@mcp.resource("opengateway://gateway")
def resource_gateway() -> str:
    """Hub health, mode, auth requirements."""
    with _client() as c:
        return _dumps(_json(c.get("/ping")))


@mcp.resource("opengateway://rooms")
def resource_rooms() -> str:
    """List collaboration rooms on the hub."""
    with _client() as c:
        return _dumps(_json(c.get("/v1/rooms")))


@mcp.resource("opengateway://rooms/{room_id}/inbox")
def resource_room_inbox(room_id: str) -> str:
    """Recent messages + participants (presence) for a room — passive read, not a wait loop."""
    with _client() as c:
        snap = _json(c.get(f"/v1/rooms/{room_id}/snapshot"))
        if isinstance(snap, dict) and "error" not in snap:
            parts = snap.get("participants") or []
            listening = [p for p in parts if p.get("presence") == "listening"]
            joined = [p for p in parts if p.get("presence") == "joined"]
            snap["presence_summary"] = {
                "listening": [p.get("name") for p in listening],
                "joined_not_listening": [p.get("name") for p in joined],
                "hint": (
                    "presence=listening means recent wait_for_messages / opengateway listen. "
                    "joined means online but radio off."
                ),
            }
        return _dumps(snap)


@mcp.resource("opengateway://listen-playbook")
def resource_listen_playbook() -> str:
    """How to stay radio-on without burning harness tool-iteration budgets."""
    return """# OpenGateway radio playbook (anti max-iterations)

## Problem
Hermes / Claude Code / Cursor / Grok cap tool calls per turn (~90 on Hermes).
Looping wait_for_messages burns that budget → "max iterations" stop.

## Default (always-on) — USE THIS
1. join_room or begin_im_mode → radio starts automatically (background long-poll)
2. You stay presence=listening while the MCP process is alive
3. Do real work; occasionally drain_inbox (≤1 per assistant turn)
4. post_message to reply; leave_room / stop_listening when done

## Do NOT
- Call wait_for_messages in a tight loop to "stay online"
- Poll more than once per turn just for presence

## One-shot block (rare)
  wait_for_messages(...) — single block until a message or timeout; then stop

## External CLI radio (presence only — no LLM wake)
  opengateway listen <room> --name <you> --harness hermes

## True IM (presence + wake agent on every message)
  opengateway im <room> --name "Hermes COO" --harness hermes --wake hermes
  opengateway im <room> --wake auto   # ping→pong smoke test without LLM
  See docs/AGENTS_IM.md

## Presence badges (Live Ops)
- listening — long-poll in last ~90s (radio on)
- joined — online but radio off
- offline — stale
"""


@mcp.resource("opengateway://radio")
def resource_radio() -> str:
    """Always-on radio sessions in this MCP process."""
    from opengateway.radio import radio

    return _dumps({"sessions": radio.list_sessions()})


@mcp.resource("opengateway://playbook/{topic}")
def resource_playbook(topic: str = "connect") -> str:
    """Public playbooks: connect | radio | im | mcp | workspace | vault."""
    from opengateway.agent_playbook import playbook

    return playbook(topic)


@mcp.resource("opengateway://skill")
def resource_skill() -> str:
    """opengateway-collab skill markdown."""
    from opengateway.agent_playbook import skill_markdown

    return skill_markdown()


@mcp.tool()
def auth_check() -> str:
    """Verify OPENGATEWAY_AUTH_TOKEN can call room APIs.

    On 401: stop all room tools, tell the human to mint a new device key and restart
    the harness MCP process. Do not tight-loop.
    """
    with _client() as c:
        ping = _json(c.get("/ping"))
        rooms = c.get("/v1/rooms")
        auth = c.get("/v1/auth/status")
        try:
            auth_body = auth.json()
        except Exception:
            auth_body = {"status_code": auth.status_code}
        ok = rooms.status_code < 400
        out: dict[str, Any] = {
            "auth_ok": ok,
            "rooms_status": rooms.status_code,
            "auth": auth_body,
            "ping_ok": isinstance(ping, dict) and ping.get("status") == "ok",
            "gateway_url": _base(),
        }
        if not ok:
            out["action"] = (
                "STOP. Do not retry room tools in a loop. "
                "Human must mint a new agent token in Live Ops, set OPENGATEWAY_AUTH_TOKEN, "
                "and restart this harness MCP process."
            )
            out["code"] = "auth_required"
        return _dumps(out)


@mcp.tool()
def gateway_status() -> str:
    """Check OpenGateway health and version."""
    with _client() as c:
        return _dumps(_json(c.get("/ping")))


@mcp.tool()
def create_room(
    name: str,
    goal: str = "",
    project_path: str = "",
    created_by: str = "mcp-agent",
) -> str:
    """Create a collaboration room for multiple agents to work together.

    Args:
        name: Short room name (e.g. 'auth-refactor').
        goal: Shared objective all agents should pursue.
        project_path: Absolute path to the project workspace, if any.
        created_by: Your agent display name.
    """
    body: dict[str, Any] = {"name": name, "goal": goal, "created_by": created_by}
    if project_path:
        body["project_path"] = project_path
    with _client() as c:
        return _dumps(_json(c.post("/v1/rooms", json=body)))


@mcp.tool()
def list_rooms(include_archived: bool = False) -> str:
    """List collaboration rooms on the gateway.

    Args:
        include_archived: When true, include archived rooms (default: active only).
    """
    params = {"include_archived": "true"} if include_archived else None
    with _client() as c:
        return _dumps(_json(c.get("/v1/rooms", params=params)))


@mcp.tool()
def update_room(
    room_id: str,
    name: str = "",
    goal: str = "",
    status: str = "",
    actor: str = "",
    announce: bool = True,
) -> str:
    """Rename, edit goal, or archive a room (id is stable across renames).

    Args:
        room_id: Room UUID (stable across renames).
        name: New display name (leave empty to keep).
        goal: New goal text (leave empty to keep).
        status: open | active | paused | closed | archived (leave empty to keep).
        actor: Who made the change (shown in system announce).
        announce: Post a system message so long-poll agents see the change.
    """
    body: dict[str, Any] = {"announce": announce}
    if name.strip():
        body["name"] = name.strip()
    if goal.strip():
        body["goal"] = goal
    if status.strip():
        body["status"] = status.strip()
    if actor.strip():
        body["actor"] = actor.strip()
    with _client() as c:
        return _dumps(_json(c.patch(f"/v1/rooms/{room_id}", json=body)))


@mcp.tool()
def archive_room(room_id: str, actor: str = "") -> str:
    """Archive a room (hidden from default list_rooms; id preserved)."""
    params = {"actor": actor} if actor.strip() else None
    with _client() as c:
        return _dumps(
            _json(c.post(f"/v1/rooms/{room_id}/archive", params=params or None))
        )


@mcp.tool()
def unarchive_room(room_id: str, actor: str = "") -> str:
    """Restore an archived room so it appears in list_rooms again."""
    params = {"actor": actor} if actor.strip() else None
    with _client() as c:
        return _dumps(
            _json(c.post(f"/v1/rooms/{room_id}/unarchive", params=params or None))
        )


def _start_radio_for(data: dict[str, Any], room_id: str) -> dict[str, Any]:
    """Always-on radio after a successful join."""
    from opengateway.radio import radio

    pid = (data or {}).get("id") or (data or {}).get("participant_id")
    name = (data or {}).get("name") or ""
    if not pid:
        return {"radio": "off", "reason": "join failed or missing participant id"}
    return radio.start(
        room_id=room_id,
        participant_id=str(pid),
        name=str(name),
        base_url=os.environ.get("OPENGATEWAY_URL")
        or os.environ.get("OPENGATEWAY_BASE_URL")
        or "http://127.0.0.1:8765",
        auth_token=os.environ.get("OPENGATEWAY_AUTH_TOKEN")
        or os.environ.get("OPENGATEWAY_TOKEN")
        or "",
    )


@mcp.tool()
def join_room(
    room_id: str,
    name: str,
    harness: str = DEFAULT_HARNESS,
    role: str = "contributor",
    capabilities: str = "",
    participant_id: str = "",
    auto_listen: bool = True,
) -> str:
    """Join a room and (by default) start always-on radio so you stay listening.

    Save field `id` (aliased as `participant_id`) for post_message and other tools.

    Args:
        room_id: Room UUID from create_room or list_rooms.
        name: Your agent identity (e.g. 'claude-code', 'grok-builder').
        harness: One of: grok, claude-code, cursor, hermes, acp, mcp, human, other.
        role: coordinator | contributor | reviewer | observer.
        capabilities: Comma-separated skills (e.g. 'python,testing,frontend').
        participant_id: Optional — rejoin with an existing id.
        auto_listen: Default True — start background radio immediately.
    """
    caps = [c.strip() for c in capabilities.split(",") if c.strip()] if capabilities else []
    if auto_listen and "listen" not in {c.lower() for c in caps}:
        caps = [*caps, "listen", "radio"]
    body: dict[str, Any] = {
        "name": name or DEFAULT_AGENT_NAME or harness,
        "harness": harness,
        "role": role,
        "capabilities": caps,
    }
    if participant_id:
        body["participant_id"] = participant_id
    with _client() as c:
        data = _json(c.post(f"/v1/rooms/{room_id}/join", json=body))
        # Hub returns participant under `id`; alias for agents that expect participant_id.
        if isinstance(data, dict) and data.get("id") and "participant_id" not in data:
            data = {**data, "participant_id": data["id"]}
        if auto_listen and isinstance(data, dict):
            try:
                data["radio"] = _start_radio_for(data, room_id)
                data["hint"] = (
                    "Radio is ON. Use drain_inbox (≤1/turn) then post_message. "
                    "Do not loop wait_for_messages. True IM: opengateway im --wake …"
                )
            except Exception as e:
                data["radio"] = {"radio": "error", "detail": str(e)}
        return _dumps(data)


@mcp.tool()
def leave_room(room_id: str, participant_id: str) -> str:
    """Leave a collaboration room and stop always-on radio for this participant."""
    try:
        from opengateway.radio import radio

        radio.stop(room_id=room_id, participant_id=participant_id)
    except Exception:
        pass
    with _client() as c:
        data = _json(
            c.post(
                f"/v1/rooms/{room_id}/leave",
                params={"participant_id": participant_id},
            )
        )
        if isinstance(data, dict):
            data["radio"] = "stopped"
        return _dumps(data)


@mcp.tool()
def drain_inbox(
    room_id: str = "",
    participant_id: str = "",
    limit: int = 50,
) -> str:
    """Read messages buffered by always-on radio (non-blocking).

    Prefer this after join_room(auto_listen=true) while you code.
    Check message.attachments[] for file ids.

    Args:
        room_id: Optional filter to one room.
        participant_id: Optional filter to one seat.
        limit: Max messages to return.
    """
    from opengateway.radio import radio

    return _dumps(
        radio.drain_inbox(
            room_id=room_id or "",
            participant_id=participant_id or "",
            max_items=limit,
        )
    )


@mcp.tool()
def start_listening(
    room_id: str,
    participant_id: str,
    name: str = "",
    timeout_seconds: float = 45.0,
) -> str:
    """Start (or restart) always-on radio for an already-joined participant."""
    from opengateway.radio import radio

    return _dumps(
        radio.start(
            room_id=room_id,
            participant_id=participant_id,
            name=name or DEFAULT_AGENT_NAME or "agent",
            base_url=_base(),
            auth_token=_auth_token(),
            timeout=timeout_seconds,
            restart=True,
        )
    )


@mcp.tool()
def ensure_radio(
    room_id: str,
    participant_id: str,
    name: str = "",
    timeout_seconds: float = 45.0,
) -> str:
    """Idempotent: ensure background radio is ON (does not block / burn wait loops)."""
    from opengateway.radio import radio

    return _dumps(
        radio.start(
            room_id=room_id,
            participant_id=participant_id,
            name=name or DEFAULT_AGENT_NAME or "agent",
            base_url=_base(),
            auth_token=_auth_token(),
            timeout=timeout_seconds,
            restart=False,
        )
    )


@mcp.tool()
def stop_listening(room_id: str = "", participant_id: str = "") -> str:
    """Stop always-on radio (go deaf). Pass room_id and/or participant_id to filter."""
    from opengateway.radio import radio

    return _dumps(radio.stop(room_id=room_id, participant_id=participant_id))


@mcp.tool()
def radio_status() -> str:
    """List always-on radio sessions in this MCP process (listening agents)."""
    from opengateway.radio import radio

    return _dumps({"sessions": radio.list_sessions()})


@mcp.tool()
def get_inbox(
    participant_id: str = "",
    room_id: str = "",
    limit: int = 20,
) -> str:
    """Peek at buffered radio messages without removing them (use drain_inbox to clear)."""
    from opengateway.radio import radio

    return _dumps(
        radio.peek_inbox(
            participant_id=participant_id,
            room_id=room_id,
            limit=limit,
        )
    )


@mcp.tool()
def create_fork(
    room_id: str,
    root_message_id: str,
    created_by: str,
    title: str = "",
    note: str = "",
    created_by_name: str = "",
    context_messages: int = 15,
    join_creator: bool = True,
) -> str:
    """Fork a message into a new branch room. Returns fork with forked_room_id.

    Args:
        room_id: Parent room UUID.
        root_message_id: Message to fork from.
        created_by: Your participant id.
        title: Branch room title.
        note: Optional note shown in the branch banner.
        created_by_name: Display name for the creator seat.
        context_messages: Prior messages to copy into the branch (0–100).
        join_creator: Auto-join creator into the forked room.
    """
    body: dict[str, Any] = {
        "root_message_id": root_message_id,
        "created_by": created_by,
        "context_messages": max(0, min(int(context_messages), 100)),
        "join_creator": join_creator,
    }
    if title.strip():
        body["title"] = title.strip()
    if note.strip():
        body["note"] = note.strip()
    if created_by_name.strip():
        body["created_by_name"] = created_by_name.strip()
    with _client() as c:
        data = _json(c.post(f"/v1/rooms/{room_id}/forks", json=body))
        if isinstance(data, dict) and data.get("forked_room_id"):
            data["hint"] = (
                f"Open forked chat: join_room(room_id={data['forked_room_id']!r}) "
                "then continue the discussion there."
            )
        return _dumps(data)


@mcp.tool()
def list_forks(room_id: str) -> str:
    """List conversation forks from a room. Open each via forked_room_id."""
    with _client() as c:
        return _dumps(_json(c.get(f"/v1/rooms/{room_id}/forks")))


@mcp.tool()
def list_participants(room_id: str) -> str:
    """List agents currently in a room."""
    with _client() as c:
        return _dumps(_json(c.get(f"/v1/rooms/{room_id}/participants")))


@mcp.tool()
def post_message(
    room_id: str,
    from_participant_id: str,
    content: str,
    to_participant_id: str = "",
    nudge_all: bool = False,
) -> str:
    """Post a message to the room (broadcast) or DM another participant.

    If the content addresses everyone (or nudge_all=true), the gateway DMs each
    online agent and creates a claimed task for them so multi-agent replies work.

    When you receive a nudge DM or a task assigned to you, reply with post_message.

    Args:
        room_id: Room UUID.
        from_participant_id: Your participant id from join_room (`id` or `participant_id`).
            Must match a prior join — empty/wrong id returns 400 Unknown from_participant_id.
        content: Message body (markdown/plain).
        to_participant_id: Optional — set to DM a specific participant.
        nudge_all: Force nudge all online agents even without “everyone” in the text.
    """
    body: dict[str, Any] = {
        "from_participant_id": from_participant_id,
        "content": content,
        "nudge_all": nudge_all,
    }
    if to_participant_id:
        body["to_participant_id"] = to_participant_id
    with _client() as c:
        return _dumps(_json(c.post(f"/v1/rooms/{room_id}/messages", json=body)))


@mcp.tool()
def poll_messages(
    room_id: str,
    since: str = "",
    for_participant: str = "",
    limit: int = 50,
) -> str:
    """Fetch recent room messages (non-blocking). Pass since=<message_id> for only newer ones.

    Prefer wait_for_messages for IM-style realtime loops.

    Args:
        room_id: Room UUID.
        since: Last message id you already processed (exclusive).
        for_participant: Filter to broadcasts + messages to/from this participant.
        limit: Max messages to return.
    """
    params: dict[str, Any] = {"limit": limit}
    if since:
        params["since"] = since
    if for_participant:
        params["for_participant"] = for_participant
    with _client() as c:
        return _dumps(_json(c.get(f"/v1/rooms/{room_id}/messages", params=params)))


@mcp.tool()
async def wait_for_messages(
    room_id: str,
    since: str = "",
    for_participant: str = "",
    timeout_seconds: float = 45.0,
    limit: int = 50,
    ctx: Optional[Context] = None,
) -> str:
    """IM-style long-poll: block until a new room message arrives (or timeout).

    REQUIRED LOOP for multi-agent chat — call again immediately on timed_out:
      1. wait_for_messages(room_id, since=last_id, for_participant=my_id, timeout_seconds=45)
      2. process messages; reply with post_message
      3. LAST = next_since; goto 1

    Marks you presence=listening in Live Ops while polling.
    If the harness supports MCP logging, progress is notified during the wait.

    Args:
        room_id: Room UUID.
        since: Last message id you already processed (exclusive).
        for_participant: Your participant_id — broadcasts + DMs to you.
        timeout_seconds: Block 1–120s (default 45).
        limit: Max messages when unblocking.
    """
    params: dict[str, Any] = {
        "timeout": max(1.0, min(float(timeout_seconds), 120.0)),
        "limit": limit,
    }
    if since:
        params["since"] = since
    if for_participant:
        params["for_participant"] = for_participant
    # Must use _client() so Authorization is always set (same as poll_messages).
    # A separate bare httpx.Client was a prior bug → 401 on public hubs only for wait.
    client_timeout = max(35.0, float(timeout_seconds) + 10.0)
    if ctx is not None:
        try:
            await ctx.info(
                f"OpenGateway listening room={room_id[:8]}… "
                f"since={since or '∅'} timeout={params['timeout']}s"
            )
            await ctx.report_progress(0, 1, "long-poll wait")
        except Exception:
            pass
    with _client(timeout=client_timeout) as c:
        data = _json(c.get(f"/v1/rooms/{room_id}/messages/wait", params=params))
        if isinstance(data, dict) and "messages" in data:
            msgs = data.get("messages") or []
            if msgs and not data.get("next_since"):
                last = msgs[-1]
                mid = last.get("id") if isinstance(last, dict) else None
                data["last_id"] = mid
                data["next_since"] = mid
            data["presence"] = "listening"
            data["hint"] = (
                "Pass next_since (or last_id) as the next wait_for_messages(since=…). "
                "timed_out=true → call wait_for_messages again immediately (keep radio on). "
                "External daemon: opengateway listen <room> --file ~/.opengateway/inbox.jsonl"
            )
            if ctx is not None:
                try:
                    n = len(msgs)
                    if n:
                        await ctx.info(f"OpenGateway inbox: {n} new message(s)")
                        await ctx.report_progress(1, 1, f"{n} messages")
                    else:
                        await ctx.info("OpenGateway timed_out — loop wait_for_messages again")
                        await ctx.report_progress(1, 1, "timed_out")
                except Exception:
                    pass
        return _dumps(data)


@mcp.tool()
def begin_im_mode(
    room_id: str,
    name: str = "",
    harness: str = DEFAULT_HARNESS,
    participant_id: str = "",
    auto_listen: bool = True,
) -> str:
    """Join (or rejoin) a room with always-on radio (default).

    Starts background listening so Live Ops shows you online and peer agents
    can reach you. Drain new messages with drain_inbox; reply with post_message.
    For ping→pong without a desktop turn, run `opengateway im --wake …`.

    Args:
        room_id: Room UUID.
        name: Agent display name (defaults to OPENGATEWAY_AGENT_NAME / harness).
        harness: grok | claude-code | cursor | hermes | mcp | …
        participant_id: Optional rejoin id.
        auto_listen: Default True — keep radio on until leave_room/stop_listening.
    """
    env_name = (DEFAULT_AGENT_NAME or "").strip()
    join_name = env_name or (name or "").strip() or harness or "agent"
    body: dict[str, Any] = {
        "name": join_name,
        "harness": harness,
        "role": "contributor",
        "capabilities": ["chat", "listen", "radio"],
    }
    if participant_id:
        body["participant_id"] = participant_id
    with _client() as c:
        data = _json(c.post(f"/v1/rooms/{room_id}/join", json=body))
    if isinstance(data, dict) and data.get("id") and "participant_id" not in data:
        data = {**data, "participant_id": data["id"]}
    if isinstance(data, dict) and not data.get("error"):
        pid = data.get("participant_id") or data.get("id")
        if auto_listen:
            data["radio"] = _start_radio_for(data, room_id)
        data["im"] = {
            "room_id": room_id,
            "participant_id": pid,
            "radio": "on" if auto_listen else "off",
            "next": [
                "Radio keeps presence=listening; it does NOT wake your LLM",
                "For true IM (ping→pong without a human desktop turn): run "
                "`opengateway im <room> --wake hermes` (or --wake auto)",
                "In this desktop turn: drain_inbox ≤1, post_message, no wait loops",
                "leave_room / stop_listening when done",
            ],
            "anti_pattern": {
                "bad": "wait_for_messages forever (burns Hermes/Claude max tool turns)",
                "good": "radio for presence + opengateway im for auto-reply wakes",
            },
            "docs": "docs/AGENTS_IM.md + docs/AGENTS_RADIO.md",
        }
    return _dumps(data)


@mcp.tool()
def create_task(
    room_id: str,
    title: str,
    created_by: str,
    description: str = "",
) -> str:
    """Create a work item in the room for agents to claim.

    Args:
        room_id: Room UUID.
        title: Short task title.
        created_by: Your participant_id or name.
        description: Full description / acceptance criteria.
    """
    body = {
        "title": title,
        "description": description,
        "created_by": created_by,
    }
    with _client() as c:
        return _dumps(_json(c.post(f"/v1/rooms/{room_id}/tasks", json=body)))


@mcp.tool()
def list_tasks(room_id: str, status: str = "") -> str:
    """List tasks in a room. Optional status filter: open, claimed, in_progress, done, blocked, cancelled."""
    params: dict[str, Any] = {}
    if status:
        params["status"] = status
    with _client() as c:
        return _dumps(_json(c.get(f"/v1/rooms/{room_id}/tasks", params=params)))


@mcp.tool()
def claim_task(room_id: str, task_id: str, participant_id: str) -> str:
    """Claim a task so other agents know you own it."""
    body = {"claimed_by": participant_id, "status": "claimed"}
    with _client() as c:
        return _dumps(_json(c.patch(f"/v1/rooms/{room_id}/tasks/{task_id}", json=body)))


@mcp.tool()
def update_task(
    room_id: str,
    task_id: str,
    status: str = "",
    result: str = "",
    claimed_by: str = "",
) -> str:
    """Update task status/result. Status: open, claimed, in_progress, done, blocked, cancelled."""
    body: dict[str, Any] = {}
    if status:
        body["status"] = status
    if result:
        body["result"] = result
    if claimed_by:
        body["claimed_by"] = claimed_by
    with _client() as c:
        return _dumps(_json(c.patch(f"/v1/rooms/{room_id}/tasks/{task_id}", json=body)))


@mcp.tool()
def complete_task(room_id: str, task_id: str, result: str = "") -> str:
    """Mark a task done, optionally attaching a result summary."""
    body: dict[str, Any] = {"status": "done"}
    if result:
        body["result"] = result
    with _client() as c:
        return _dumps(_json(c.patch(f"/v1/rooms/{room_id}/tasks/{task_id}", json=body)))


@mcp.tool()
def share_artifact(
    room_id: str,
    name: str,
    shared_by: str,
    content: str = "",
    content_url: str = "",
    content_type: str = "text/plain",
    description: str = "",
) -> str:
    """Share a named artifact (file contents, report, patch, etc.) with the room.

    Args:
        room_id: Room UUID.
        name: Artifact path/name (e.g. '/src/auth.py' or 'plan.md').
        shared_by: Your participant_id.
        content: Inline content (prefer for text).
        content_url: External URL if not inline.
        content_type: MIME type.
        description: What this artifact is.
    """
    body: dict[str, Any] = {
        "name": name,
        "shared_by": shared_by,
        "content_type": content_type,
        "description": description,
    }
    if content:
        body["content"] = content
    if content_url:
        body["content_url"] = content_url
    with _client() as c:
        return _dumps(_json(c.post(f"/v1/rooms/{room_id}/artifacts", json=body)))


@mcp.tool()
def list_artifacts(room_id: str) -> str:
    """List artifacts shared in a room."""
    with _client() as c:
        return _dumps(_json(c.get(f"/v1/rooms/{room_id}/artifacts")))


@mcp.tool()
def room_snapshot(room_id: str) -> str:
    """Full room state: room meta, participants, tasks, recent messages, artifacts."""
    with _client() as c:
        return _dumps(_json(c.get(f"/v1/rooms/{room_id}/snapshot")))


@mcp.tool()
def upload_file(
    room_id: str,
    shared_by: str,
    filename: str,
    content_base64: str,
    content_type: str = "application/octet-stream",
) -> str:
    """Upload a file into a room. Returns artifact id + content_url."""
    import base64 as b64

    try:
        raw = b64.b64decode(content_base64)
    except Exception as e:
        return _dumps({"error": "invalid_base64", "detail": str(e)})
    files = {"file": (filename or "upload.bin", raw, content_type)}
    data = {"shared_by": shared_by}
    with _client() as c:
        r = c.post(f"/v1/rooms/{room_id}/files", data=data, files=files)
        return _dumps(_json(r))


@mcp.tool()
def share_file_message(
    room_id: str,
    from_participant_id: str,
    file_id: str,
    caption: str = "",
) -> str:
    """Announce an uploaded file in the room chat."""
    with _client() as c:
        meta = _json(c.get(f"/v1/rooms/{room_id}/files/{file_id}"))
        if isinstance(meta, dict) and meta.get("error"):
            return _dumps(meta)
        name = (meta or {}).get("name") or "file"
        ctype = (meta or {}).get("content_type") or "application/octet-stream"
        url = (meta or {}).get("content_url") or (
            f"/v1/rooms/{room_id}/files/{file_id}/download"
        )
        text = (caption or "").strip() or f"Shared file: {name}"
        body = {
            "from_participant_id": from_participant_id,
            "content": text,
            "parts": [
                {"content_type": "text/plain", "content": text},
                {"name": name, "content_type": ctype, "content_url": url},
            ],
            "metadata": {"files": [file_id]},
        }
        return _dumps(_json(c.post(f"/v1/rooms/{room_id}/messages", json=body)))


@mcp.tool()
def download_attachment(
    room_id: str,
    file_id: str = "",
    content_url: str = "",
) -> str:
    """Download a chat/file attachment as JSON (inline base64 when small)."""
    import re

    fid = (file_id or "").strip()
    if not fid and content_url:
        m = re.search(r"/files/([^/?#]+)/download", content_url)
        if m:
            fid = m.group(1)
    if not fid:
        return _dumps(
            {
                "error": "file_id_or_content_url_required",
                "hint": "Pass attachments[].file_id from the message, or content_url.",
            }
        )
    with _client() as c:
        meta = _json(c.get(f"/v1/rooms/{room_id}/files/{fid}"))
        data = _json(
            c.get(
                f"/v1/rooms/{room_id}/files/{fid}/download",
                params={"format": "json"},
            )
        )
        if isinstance(data, dict) and data.get("error") == "file_too_large_for_json":
            data["meta"] = meta
            data["agent_action"] = (
                "HTTP GET the content_url with your OPENGATEWAY_AUTH_TOKEN Bearer "
                "and write bytes to a workspace file; do not base64 into the prompt."
            )
        return _dumps(data)


@mcp.tool()
def list_tool_credentials() -> str:
    """List named tool credentials on the hub (names + metadata only — never secret values)."""
    with _client() as c:
        return _dumps(_json(c.get("/v1/tools/credentials")))


@mcp.tool()
def tool_proxy(
    credential: str,
    url: str,
    method: str = "GET",
    body: str = "",
    body_base64: str = "",
    headers_json: str = "",
    timeout: float = 30.0,
) -> str:
    """Call an external HTTP API via the hub, injecting a vault credential server-side."""
    payload: dict[str, Any] = {
        "credential": credential,
        "url": url,
        "method": method or "GET",
        "timeout": timeout,
    }
    if body_base64.strip():
        payload["body_base64"] = body_base64.strip()
    elif body:
        try:
            payload["body"] = json.loads(body)
        except Exception:
            payload["body"] = body
    if headers_json.strip():
        try:
            payload["headers"] = json.loads(headers_json)
        except Exception:
            return _dumps({"error": "invalid_headers_json"})
    with _client(timeout=max(float(timeout) + 5.0, 35.0)) as c:
        return _dumps(_json(c.post("/v1/tools/proxy", json=payload)))


@mcp.tool()
def workspace_list(room_id: str, prefix: str = "") -> str:
    """List files in the room's shared path-addressed workspace."""
    params = {"prefix": prefix} if prefix else None
    with _client() as c:
        return _dumps(_json(c.get(f"/v1/rooms/{room_id}/workspace", params=params)))


@mcp.tool()
def workspace_read(room_id: str, path: str) -> str:
    """Read a file from the room workspace by path (text or base64)."""
    with _client() as c:
        return _dumps(
            _json(
                c.get(
                    f"/v1/rooms/{room_id}/workspace/{path.lstrip('/')}",
                    params={"format": "json"},
                )
            )
        )


@mcp.tool()
def workspace_write(
    room_id: str,
    path: str,
    content: str = "",
    content_base64: str = "",
    content_type: str = "text/plain",
    updated_by: str = "",
) -> str:
    """Write/overwrite a file in the room workspace."""
    body: dict[str, Any] = {
        "content_type": content_type or "text/plain",
        "updated_by": updated_by or DEFAULT_AGENT_NAME or "",
    }
    if content_base64.strip():
        body["content_base64"] = content_base64.strip()
    else:
        body["content"] = content
    with _client() as c:
        return _dumps(
            _json(
                c.put(
                    f"/v1/rooms/{room_id}/workspace/{path.lstrip('/')}",
                    json=body,
                    headers={"Content-Type": "application/json"},
                )
            )
        )


@mcp.tool()
def workspace_delete(room_id: str, path: str) -> str:
    """Delete a file from the room workspace."""
    with _client() as c:
        return _dumps(
            _json(c.delete(f"/v1/rooms/{room_id}/workspace/{path.lstrip('/')}"))
        )


@mcp.tool()
def list_acp_agents() -> str:
    """List ACP agents exposed by the gateway (facilitator, echo, registered, live participants)."""
    with _client() as c:
        return _dumps(_json(c.get("/agents")))


@mcp.tool()
def run_acp_agent(agent_name: str, message: str, mode: str = "sync") -> str:
    """Invoke a built-in or registered ACP agent (e.g. room-facilitator).

    Args:
        agent_name: Agent name from list_acp_agents.
        message: User message / instructions.
        mode: sync or async.
    """
    body = {
        "agent_name": agent_name,
        "mode": mode,
        "input": [
            {
                "role": "user",
                "parts": [{"content_type": "text/plain", "content": message}],
            }
        ],
    }
    with _client() as c:
        return _dumps(_json(c.post("/runs", json=body)))


@mcp.tool()
def register_agent(
    name: str,
    harness: str = DEFAULT_HARNESS,
    description: str = "",
    capabilities: str = "",
) -> str:
    """Register your agent on the gateway for discovery (optional; join_room is enough for rooms)."""
    caps = [c.strip() for c in capabilities.split(",") if c.strip()] if capabilities else []
    body = {
        "name": name,
        "harness": harness,
        "description": description,
        "capabilities": caps,
    }
    with _client() as c:
        return _dumps(_json(c.post("/v1/agents/register", json=body)))


def _maybe_auto_join() -> None:
    """Optional OPENGATEWAY_AUTO_JOIN_ROOM=id|name — join + radio at MCP startup."""
    room = (os.environ.get("OPENGATEWAY_AUTO_JOIN_ROOM") or "").strip()
    if not room:
        return
    try:
        with _client() as c:
            rooms = _json(c.get("/v1/rooms"))
            rid = room
            if isinstance(rooms, dict):
                for r in rooms.get("rooms") or []:
                    if r.get("id") == room or r.get("name") == room:
                        rid = r["id"]
                        break
            name = DEFAULT_AGENT_NAME or DEFAULT_HARNESS or "agent"
            data = _json(
                c.post(
                    f"/v1/rooms/{rid}/join",
                    json={
                        "name": name,
                        "harness": DEFAULT_HARNESS,
                        "role": "contributor",
                        "capabilities": ["listen", "radio", "chat"],
                    },
                )
            )
            if isinstance(data, dict) and data.get("id"):
                radio_info = _start_radio_for(data, rid)
                import sys

                print(
                    f"[opengateway] auto-join room={rid[:8]}… "
                    f"as {name} radio={radio_info.get('radio')}",
                    file=sys.stderr,
                    flush=True,
                )
    except Exception as e:
        import sys

        print(f"[opengateway] auto-join failed: {e}", file=sys.stderr, flush=True)


def main() -> None:
    _maybe_auto_join()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
