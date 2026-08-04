"""Room rename + archive fan-out (agents track stable room_id)."""

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
    # Explicit no-auth config — other tests may leave OPENGATEWAY_* in the env
    cfg = GatewayConfig(mode=GatewayMode.INTERNAL, require_auth=False, auth_token=None)
    app = create_app(store, config=cfg)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_rename_room_keeps_id_and_announces(client: AsyncClient, store: Store):
    created = (
        await client.post("/v1/rooms", json={"name": "main", "goal": "ship", "created_by": "t"})
    ).json()
    assert "id" in created, created
    rid = created["id"]
    assert created["name"] == "main"

    patched = (
        await client.patch(
            f"/v1/rooms/{rid}",
            json={"name": "ship-it", "actor": "Matt", "announce": True},
        )
    ).json()
    assert patched["id"] == rid
    assert patched["name"] == "ship-it"

    listed = (await client.get("/v1/rooms")).json()["rooms"]
    assert any(r["id"] == rid and r["name"] == "ship-it" for r in listed)

    # System announce for long-poll agents
    msgs = (await client.get(f"/v1/rooms/{rid}/messages")).json()["messages"]
    assert any(
        m.get("from_name") == "system"
        and "renamed" in (m.get("message", {}).get("parts", [{}])[0].get("content") or "").lower()
        for m in msgs
    )
    # Event stream payload
    events = list(store._events)
    room_events = [e for e in events if e.type == "room" and e.room_id == rid]
    assert any(e.payload.get("action") == "renamed" for e in room_events)


@pytest.mark.asyncio
async def test_archive_hides_from_default_list(client: AsyncClient):
    created = (
        await client.post("/v1/rooms", json={"name": "old-work", "created_by": "t"})
    ).json()
    assert "id" in created, created
    rid = created["id"]

    arch = (await client.post(f"/v1/rooms/{rid}/archive", params={"actor": "Matt"})).json()
    assert arch["status"] == "archived"
    assert arch["id"] == rid

    active = (await client.get("/v1/rooms")).json()["rooms"]
    assert not any(r["id"] == rid for r in active)

    all_rooms = (await client.get("/v1/rooms", params={"include_archived": True})).json()[
        "rooms"
    ]
    assert any(r["id"] == rid and r["status"] == "archived" for r in all_rooms)

    restored = (await client.post(f"/v1/rooms/{rid}/unarchive")).json()
    assert restored["status"] == "open"
    active2 = (await client.get("/v1/rooms")).json()["rooms"]
    assert any(r["id"] == rid for r in active2)


@pytest.mark.asyncio
async def test_patch_archive_via_status(client: AsyncClient):
    created = (await client.post("/v1/rooms", json={"name": "tmp", "created_by": "t"})).json()
    assert "id" in created, created
    rid = created["id"]
    patched = (
        await client.patch(f"/v1/rooms/{rid}", json={"status": "archived", "actor": "bot"})
    ).json()
    assert patched["status"] == "archived"
