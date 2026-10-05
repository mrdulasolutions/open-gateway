"""Regression: human @all should not cause empty-message ping-pong storms."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from opengateway.delivery import should_wake_seat
from opengateway.config import GatewayConfig, GatewayMode
from opengateway.server import create_app
from opengateway.store import Store


@pytest.fixture
async def im_client():
    st = Store(db_path=None)
    cfg = GatewayConfig(
        mode=GatewayMode.INTERNAL,
        require_auth=False,
        host="127.0.0.1",
        port=8765,
    )
    app = create_app(st, config=cfg)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, st


@pytest.mark.asyncio
async def test_human_all_structured_delivery_per_agent(im_client):
    ac, _st = im_client
    room = (await ac.post("/v1/rooms", json={"name": "incident", "goal": "im"})).json()
    room_id = room["id"]
    human = (
        await ac.post(
            f"/v1/rooms/{room_id}/join",
            json={"name": "mark", "harness": "human"},
        )
    ).json()
    a = (
        await ac.post(
            f"/v1/rooms/{room_id}/join",
            json={"name": "claude-code", "harness": "claude-code"},
        )
    ).json()
    b = (
        await ac.post(
            f"/v1/rooms/{room_id}/join",
            json={"name": "grok", "harness": "grok"},
        )
    ).json()

    text = "@all please collaborate without empty inbox loops — ship the fix."
    res = (
        await ac.post(
            f"/v1/rooms/{room_id}/messages",
            json={"from_participant_id": human["id"], "content": text},
        )
    ).json()
    assert res["nudge_count"] == 2

    for agent in (a, b):
        inbox = (
            await ac.get(
                f"/v1/rooms/{room_id}/messages",
                params={"for_participant": agent["id"]},
            )
        ).json()["messages"]
        dms = [m for m in inbox if m.get("to_participant_id") == agent["id"]]
        assert len(dms) >= 1
        meta = dms[-1].get("metadata") or {}
        assert meta.get("structured_delivery")
        assert meta.get("delivery_id")
        body = dms[-1]["message"]["parts"][0]["content"]
        assert text in body


@pytest.mark.asyncio
async def test_unaddressed_agent_reply_does_not_wake_peer(im_client):
    ac, _ = im_client
    room = (await ac.post("/v1/rooms", json={"name": "quiet", "goal": "a2a"})).json()
    room_id = room["id"]
    a = (
        await ac.post(
            f"/v1/rooms/{room_id}/join",
            json={"name": "alice", "harness": "claude-code"},
        )
    ).json()
    b = (
        await ac.post(
            f"/v1/rooms/{room_id}/join",
            json={"name": "grok", "harness": "grok"},
        )
    ).json()
    post = (
        await ac.post(
            f"/v1/rooms/{room_id}/messages",
            json={
                "from_participant_id": a["id"],
                "content": "Done with my part — no mentions here.",
            },
        )
    ).json()
    msg = post if post.get("message") else post
    row = msg.get("message") or msg
    # serialized shape from API
    if isinstance(row, dict) and "parts" not in row:
        row = {
            "id": msg.get("id") or "x",
            "from_participant_id": a["id"],
            "from_name": "alice",
            "message": row,
        }
    wake, reason, _ = should_wake_seat(
        {
            "id": msg.get("id", "x"),
            "from_participant_id": a["id"],
            "from_name": "alice",
            "message": msg.get("message") if isinstance(msg.get("message"), dict) else msg,
        },
        seat_participant_id=b["id"],
        seat_name="grok",
    )
    assert not wake
    assert reason == "transcript_only"
