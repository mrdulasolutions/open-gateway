"""Chat file attachments are visible/downloadable to agents."""

from __future__ import annotations

import base64

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
async def test_upload_inline_and_message_parts_for_agents(client: AsyncClient):
    room = (await client.post("/v1/rooms", json={"name": "files", "created_by": "t"})).json()
    rid = room["id"]
    p = (
        await client.post(
            f"/v1/rooms/{rid}/join",
            json={"name": "human", "harness": "human", "role": "admin"},
        )
    ).json()
    pid = p["id"]

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    files = {"file": ("dot.png", png, "image/png")}
    data = {"shared_by": pid}
    up = await client.post(f"/v1/rooms/{rid}/files", data=data, files=files)
    assert up.status_code == 200, up.text
    art = up.json()
    assert art["id"]
    assert art.get("content"), "small files must inline for agents"
    assert art["content_url"].startswith("/v1/rooms/")

    # Simulate UI that wrongly embeds ?token= — server must strip + enrich
    posted = await client.post(
        f"/v1/rooms/{rid}/messages",
        json={
            "from_participant_id": pid,
            "content": "see image",
            "parts": [
                {"content_type": "text/plain", "content": "see image"},
                {
                    "name": "dot.png",
                    "content_type": "image/png",
                    "content_url": art["content_url"] + "?token=browser-secret",
                },
            ],
        },
    )
    assert posted.status_code == 200, posted.text
    body = posted.json()
    # With nudge wrapper: {message: RoomMessage}; plain: RoomMessage
    msg = body["message"] if "message" in body and "parts" not in body.get("message", {}) else body
    if "nudge_count" in body:
        msg = body["message"]
    parts = (msg.get("message") or {}).get("parts") or msg.get("parts") or []
    file_parts = [x for x in parts if x.get("name") == "dot.png"]
    assert file_parts, body
    fp = file_parts[0]
    assert "token=" not in (fp.get("content_url") or ""), fp
    assert fp.get("content"), f"enriched from artifact for agents: {fp}"

    listed = (await client.get(f"/v1/rooms/{rid}/messages")).json()["messages"]
    assert listed
    last = listed[-1]
    assert last.get("attachments"), last
    assert last["attachments"][0]["file_id"] == art["id"]
    assert last["attachments"][0]["has_inline_content"] is True

    dl = await client.get(
        f"/v1/rooms/{rid}/files/{art['id']}/download",
        params={"format": "json"},
    )
    assert dl.status_code == 200, dl.text
    body = dl.json()
    assert body["content_encoding"] == "base64"
    assert base64.b64decode(body["content"]) == png


@pytest.mark.asyncio
async def test_message_text_surfaces_attachment():
    from opengateway.models import Message, MessagePart

    m = Message(
        role="user",
        parts=[
            MessagePart(content="hi", content_type="text/plain"),
            MessagePart(
                name="a.pdf",
                content_type="application/pdf",
                content_url="/v1/rooms/r/files/f/download",
            ),
        ],
    )
    t = m.text()
    assert "hi" in t
    assert "attachment" in t
    assert "a.pdf" in t
