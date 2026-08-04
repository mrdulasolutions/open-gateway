"""Fork creates a real branch room with context."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from opengateway.config import GatewayConfig, GatewayMode
from opengateway.server import create_app
from opengateway.store import Store


@pytest.fixture
def store() -> Store:
    return Store(db_path=None)


@pytest.fixture
async def client(store: Store):
    cfg = GatewayConfig(mode=GatewayMode.INTERNAL, require_auth=False, auth_token=None)
    app = create_app(store, config=cfg)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_fork_opens_new_room_with_context(client: AsyncClient):
    room = (await client.post("/v1/rooms", json={"name": "main", "created_by": "t"})).json()
    rid = room["id"]
    p = (
        await client.post(
            f"/v1/rooms/{rid}/join",
            json={"name": "Matt", "harness": "human", "role": "admin"},
        )
    ).json()
    pid = p["id"]

    for text in ["alpha", "beta", "gamma-root"]:
        await client.post(
            f"/v1/rooms/{rid}/messages",
            json={"from_participant_id": pid, "content": text},
        )
    msgs = (await client.get(f"/v1/rooms/{rid}/messages")).json()["messages"]
    root = msgs[-1]
    assert "gamma" in (root["message"]["parts"][0].get("content") or "")

    fork = (
        await client.post(
            f"/v1/rooms/{rid}/forks",
            json={
                "root_message_id": root["id"],
                "created_by": pid,
                "created_by_name": "Matt",
                "title": "Explore gamma",
                "context_messages": 5,
                "join_creator": True,
            },
        )
    ).json()
    assert fork.get("forked_room_id"), fork
    branch_id = fork["forked_room_id"]
    assert branch_id != rid

    branch = (await client.get(f"/v1/rooms/{branch_id}")).json()
    assert branch["metadata"]["is_fork"] is True
    assert branch["metadata"]["parent_room_id"] == rid

    branch_msgs = (await client.get(f"/v1/rooms/{branch_id}/messages")).json()["messages"]
    assert len(branch_msgs) >= 2  # system + at least root
    bodies = []
    for m in branch_msgs:
        for part in (m.get("message") or {}).get("parts") or []:
            if part.get("content"):
                bodies.append(part["content"])
    assert any("Forked from" in b or "fork" in b.lower() for b in bodies)
    assert any("gamma" in b for b in bodies)

    # Parent lists the fork
    listed = (await client.get(f"/v1/rooms/{rid}/forks")).json()["forks"]
    assert any(f["id"] == fork["id"] and f.get("forked_room_id") == branch_id for f in listed)

    # Rooms list includes branch
    rooms = (await client.get("/v1/rooms")).json()["rooms"]
    assert any(r["id"] == branch_id for r in rooms)
