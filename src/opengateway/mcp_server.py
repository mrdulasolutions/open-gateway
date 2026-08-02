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
        "(Claude Code, Grok, Cursor, ACP) in shared rooms.\n\n"
        "AUTH: public/Railway needs OPENGATEWAY_URL + OPENGATEWAY_AUTH_TOKEN in MCP env.\n\n"
        "MANDATORY realtime (you are deaf until you do this):\n"
        "After join_room, immediately start the IM loop and never exit it while collab is open:\n"
        "  LAST=\"\"\n"
        "  loop forever:\n"
        "    wait_for_messages(room_id, since=LAST, for_participant=MY_ID, timeout_seconds=45)\n"
        "    if messages: handle + post_message replies; LAST=next_since\n"
        "    if timed_out: call wait_for_messages again immediately\n"
        "If you must code for a long stretch, tell the user to run "
        "`opengateway listen <room>` so presence stays radio-on.\n\n"
        "Resources: opengateway://gateway, opengateway://rooms, "
        "opengateway://rooms/{room_id}/inbox — read for passive inbox snapshot.\n\n"
        "Flow: list/create room → join_room (save id/participant_id) → "
        "wait loop → post_message / tasks / artifacts → room_snapshot"
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
    """How to stay radio-on (MCP loop + external daemon)."""
    return """# OpenGateway listen playbook

## In-session (MCP agent)
1. join_room → save participant_id (field `id` or `participant_id`)
2. Loop forever:
   wait_for_messages(room_id, since=LAST, for_participant=MY_ID, timeout_seconds=45)
   handle messages; LAST = next_since; on timed_out, wait again immediately
3. Do not exit the loop while collaboration is active

## External radio (when coding / cold harness)
  opengateway listen <room> --name <you> --harness grok \\
    --file ~/.opengateway/inbox.jsonl

Optional sinks: --webhook URL, --hook 'shell…'

## Presence badges (Live Ops)
- listening — long-poll in last ~90s
- joined — online but not polling
- offline — stale
"""


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
def list_rooms() -> str:
    """List all collaboration rooms on the gateway."""
    with _client() as c:
        return _dumps(_json(c.get("/v1/rooms")))


@mcp.tool()
def join_room(
    room_id: str,
    name: str,
    harness: str = DEFAULT_HARNESS,
    role: str = "contributor",
    capabilities: str = "",
    participant_id: str = "",
) -> str:
    """Join a room. Save field `id` (aliased as `participant_id`) for post_message and other tools.

    Args:
        room_id: Room UUID from create_room or list_rooms.
        name: Your agent identity (e.g. 'claude-code', 'grok-builder').
        harness: One of: grok, claude-code, cursor, hermes, acp, mcp, human, other.
        role: coordinator | contributor | reviewer | observer.
        capabilities: Comma-separated skills (e.g. 'python,testing,frontend').
        participant_id: Optional — rejoin with an existing id.
    """
    caps = [c.strip() for c in capabilities.split(",") if c.strip()] if capabilities else []
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
        return _dumps(data)


@mcp.tool()
def leave_room(room_id: str, participant_id: str) -> str:
    """Leave a collaboration room."""
    with _client() as c:
        return _dumps(_json(c.post(f"/v1/rooms/{room_id}/leave", params={"participant_id": participant_id})))


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
) -> str:
    """Join (or rejoin) a room and return a ready-to-run wait loop contract.

    Call this first when starting multi-agent collab. Then immediately call
    wait_for_messages with the returned participant_id and loop forever.

    Args:
        room_id: Room UUID.
        name: Agent display name (defaults to OPENGATEWAY_AGENT_NAME / harness).
        harness: grok | claude-code | cursor | hermes | mcp | …
        participant_id: Optional rejoin id.
    """
    body: dict[str, Any] = {
        "name": name or DEFAULT_AGENT_NAME or harness or "agent",
        "harness": harness,
        "role": "contributor",
        "capabilities": ["chat", "listen"],
    }
    if participant_id:
        body["participant_id"] = participant_id
    with _client() as c:
        data = _json(c.post(f"/v1/rooms/{room_id}/join", json=body))
    if isinstance(data, dict) and data.get("id") and "participant_id" not in data:
        data = {**data, "participant_id": data["id"]}
    if isinstance(data, dict) and not data.get("error"):
        pid = data.get("participant_id") or data.get("id")
        data["im_loop"] = {
            "room_id": room_id,
            "participant_id": pid,
            "next_tool": "wait_for_messages",
            "args": {
                "room_id": room_id,
                "since": "",
                "for_participant": pid,
                "timeout_seconds": 45,
            },
            "rule": (
                "Call wait_for_messages next. On every response: handle messages, "
                "set since=next_since, call wait_for_messages again. Never stop while collab is open."
            ),
            "external_radio": (
                f"opengateway listen {room_id} --name {body['name']} "
                f"--harness {harness} --file ~/.opengateway/inbox.jsonl"
            ),
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


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
