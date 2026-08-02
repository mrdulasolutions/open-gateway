"""MCP server — lets Grok CLI, Claude Code, Cursor join OpenGateway rooms via tools."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

DEFAULT_BASE_URL = os.environ.get("OPENGATEWAY_URL", "http://127.0.0.1:8765")
DEFAULT_HARNESS = os.environ.get("OPENGATEWAY_HARNESS", "mcp")
DEFAULT_AGENT_NAME = os.environ.get("OPENGATEWAY_AGENT_NAME", "")

mcp = FastMCP(
    "OpenGateway",
    instructions=(
        "OpenGateway multi-agent collaboration tools. Use these to work with other agents "
        "(Claude Code, Grok, Cursor, ACP agents) on the same project.\n\n"
        "Typical flow:\n"
        "1. create_room (or list_rooms + join existing)\n"
        "2. join_room with your agent name and harness\n"
        "3. create_task / claim_task for work items\n"
        "4. post_message to coordinate; poll_messages to hear others\n"
        "5. share_artifact for files/results; complete_task when done\n"
        "6. room_snapshot anytime for full state"
    ),
)


def _base() -> str:
    return os.environ.get("OPENGATEWAY_URL", DEFAULT_BASE_URL).rstrip("/")


def _client() -> httpx.Client:
    return httpx.Client(base_url=_base(), timeout=30.0)


def _json(resp: httpx.Response) -> Any:
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        detail = e.response.text
        try:
            detail = e.response.json()
        except Exception:
            pass
        return {"error": str(e), "detail": detail, "status_code": e.response.status_code}
    return resp.json()


def _dumps(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


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
    """Join a room. Returns your participant_id — save it for later tool calls.

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
        return _dumps(_json(c.post(f"/v1/rooms/{room_id}/join", json=body)))


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
        from_participant_id: Your participant id from join_room.
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
def wait_for_messages(
    room_id: str,
    since: str = "",
    for_participant: str = "",
    timeout_seconds: float = 30.0,
    limit: int = 50,
) -> str:
    """IM-style long-poll: block until a new room message arrives (or timeout).

    Use this in a loop for realtime chat with other agents:
      1. wait_for_messages(room_id, since=last_id, for_participant=my_id, timeout_seconds=45)
      2. process any messages; remember last message id
      3. reply with post_message
      4. goto 1

    timed_out=true with empty messages means nothing new — call again to keep listening.

    Args:
        room_id: Room UUID.
        since: Last message id you already processed (exclusive). Empty = wait for brand-new only
               if history is empty; usually pass your last seen id.
        for_participant: Your participant_id — only broadcasts + DMs to you + your own msgs.
        timeout_seconds: How long to block (1–120). Default 30.
        limit: Max messages to return when unblocking.
    """
    params: dict[str, Any] = {
        "timeout": max(1.0, min(float(timeout_seconds), 120.0)),
        "limit": limit,
    }
    if since:
        params["since"] = since
    if for_participant:
        params["for_participant"] = for_participant
    # Long-poll needs a higher client timeout than the server wait
    client_timeout = max(35.0, float(timeout_seconds) + 10.0)
    with httpx.Client(base_url=_base(), timeout=client_timeout) as c:
        return _dumps(_json(c.get(f"/v1/rooms/{room_id}/messages/wait", params=params)))


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
