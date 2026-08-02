"""Smoke tests for OpenGateway collaboration + ACP surface."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from opengateway.server import create_app
from opengateway.store import Store


@pytest.fixture
def store() -> Store:
    return Store()


@pytest.fixture
async def client(store: Store):
    app = create_app(store)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_ping(client: AsyncClient):
    r = await client.get("/ping")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_acp_list_agents(client: AsyncClient):
    r = await client.get("/agents")
    assert r.status_code == 200
    names = {a["name"] for a in r.json()["agents"]}
    assert "echo" in names
    assert "room-facilitator" in names


@pytest.mark.asyncio
async def test_acp_echo_run(client: AsyncClient):
    r = await client.post(
        "/runs",
        json={
            "agent_name": "echo",
            "input": [
                {
                    "role": "user",
                    "parts": [{"content_type": "text/plain", "content": "Howdy!"}],
                }
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
    assert body["output"][0]["parts"][0]["content"] == "Howdy!"


@pytest.mark.asyncio
async def test_two_agents_collaborate(client: AsyncClient):
    room = (await client.post("/v1/rooms", json={"name": "pair", "goal": "ship feature"})).json()
    room_id = room["id"]

    alice = (
        await client.post(
            f"/v1/rooms/{room_id}/join",
            json={"name": "alice", "harness": "claude-code", "role": "coordinator"},
        )
    ).json()
    bob = (
        await client.post(
            f"/v1/rooms/{room_id}/join",
            json={"name": "bob", "harness": "grok", "role": "contributor"},
        )
    ).json()

    await client.post(
        f"/v1/rooms/{room_id}/messages",
        json={"from_participant_id": alice["id"], "content": "Bob, please take the API task."},
    )
    task = (
        await client.post(
            f"/v1/rooms/{room_id}/tasks",
            json={"title": "Write API", "created_by": alice["id"]},
        )
    ).json()
    await client.patch(
        f"/v1/rooms/{room_id}/tasks/{task['id']}",
        json={"claimed_by": bob["id"], "status": "in_progress"},
    )
    await client.post(
        f"/v1/rooms/{room_id}/artifacts",
        json={
            "name": "api.py",
            "shared_by": bob["id"],
            "content": "def handler(): ...",
            "content_type": "text/x-python",
        },
    )
    await client.patch(
        f"/v1/rooms/{room_id}/tasks/{task['id']}",
        json={"status": "done", "result": "api.py shared"},
    )

    snap = (await client.get(f"/v1/rooms/{room_id}/snapshot")).json()
    assert len(snap["participants"]) == 2
    assert len(snap["messages"]) >= 1
    assert snap["tasks"][0]["status"] == "done"
    assert snap["artifacts"][0]["name"] == "api.py"

    # Facilitator ACP agent summarizes the room
    run = (
        await client.post(
            "/runs",
            json={
                "agent_name": "room-facilitator",
                "input": [
                    {
                        "role": "user",
                        "parts": [{"content_type": "text/plain", "content": f"room:{room_id}"}],
                    }
                ],
            },
        )
    ).json()
    text = run["output"][0]["parts"][0]["content"]
    assert "alice" in text
    assert "bob" in text
    assert "pair" in text
