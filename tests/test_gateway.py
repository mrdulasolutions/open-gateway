"""Smoke tests for OpenGateway collaboration + ACP surface."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from opengateway.server import create_app
from opengateway.store import Store


@pytest.fixture
def store() -> Store:
    # Isolated memory store for unit tests
    return Store(db_path=None)


@pytest.fixture
async def client(store: Store):
    app = create_app(store)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_sqlite_survives_restart(tmp_path):
    from opengateway.models import Participant, Room, RoomMessage, text_message

    db = tmp_path / "state.db"
    s1 = Store(db_path=db)
    room = await s1.create_room(Room(name="persist-me", goal="survive reboot", created_by="test"))
    room_id = room.id
    p = await s1.join_room(room_id, Participant(name="alice", harness="grok"))
    await s1.post_message(
        RoomMessage(
            room_id=room_id,
            from_participant_id=p.id,
            from_name="alice",
            message=text_message("agent/alice", "hello durable world"),
        )
    )

    # New process / new Store, same file
    s2 = Store(db_path=db)
    assert room_id in s2.rooms
    assert s2.rooms[room_id].name == "persist-me"
    assert len(s2.messages[room_id]) == 1
    assert s2.messages[room_id][0].message.text() == "hello durable world"
    # Participants reloaded offline
    assert s2.participants[p.id].status.value == "offline"


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
async def test_rename_participant_in_place(client: AsyncClient):
    room = (await client.post("/v1/rooms", json={"name": "rename", "goal": "id stable"})).json()
    room_id = room["id"]
    p = (
        await client.post(
            f"/v1/rooms/{room_id}/join",
            json={"name": "human", "harness": "human", "role": "observer"},
        )
    ).json()
    pid = p["id"]
    updated = (
        await client.patch(
            f"/v1/rooms/{room_id}/participants/{pid}",
            json={"name": "mark"},
        )
    ).json()
    assert updated["id"] == pid
    assert updated["name"] == "mark"
    listed = (await client.get(f"/v1/rooms/{room_id}/participants")).json()["participants"]
    assert len(listed) == 1
    assert listed[0]["name"] == "mark"
    assert listed[0]["id"] == pid


@pytest.mark.asyncio
async def test_everyone_nudges_all_agents(client: AsyncClient):
    room = (await client.post("/v1/rooms", json={"name": "jokes", "goal": "multi"})).json()
    room_id = room["id"]
    human = (
        await client.post(
            f"/v1/rooms/{room_id}/join",
            json={"name": "mark", "harness": "human"},
        )
    ).json()
    a = (
        await client.post(
            f"/v1/rooms/{room_id}/join",
            json={"name": "alice", "harness": "claude-code"},
        )
    ).json()
    b = (
        await client.post(
            f"/v1/rooms/{room_id}/join",
            json={"name": "grok", "harness": "grok"},
        )
    ).json()

    res = (
        await client.post(
            f"/v1/rooms/{room_id}/messages",
            json={
                "from_participant_id": human["id"],
                "content": "everyone tell me a joke",
            },
        )
    ).json()
    assert res["nudge_count"] == 2
    names = {n["name"] for n in res["nudged"]}
    assert names == {"alice", "grok"}

    tasks = (await client.get(f"/v1/rooms/{room_id}/tasks")).json()["tasks"]
    assert len(tasks) == 2
    # Each agent should see a DM nudge when filtering for themselves
    for agent in (a, b):
        inbox = (
            await client.get(
                f"/v1/rooms/{room_id}/messages",
                params={"for_participant": agent["id"]},
            )
        ).json()["messages"]
        assert any("@nudge" in (m["message"]["parts"][0]["content"]) for m in inbox)


@pytest.mark.asyncio
async def test_long_poll_wait_for_message(client: AsyncClient, store: Store):
    import asyncio

    room = (await client.post("/v1/rooms", json={"name": "im", "goal": "chat"})).json()
    room_id = room["id"]
    a = (
        await client.post(
            f"/v1/rooms/{room_id}/join",
            json={"name": "a", "harness": "grok"},
        )
    ).json()
    b = (
        await client.post(
            f"/v1/rooms/{room_id}/join",
            json={"name": "b", "harness": "claude-code"},
        )
    ).json()

    async def late_post():
        await asyncio.sleep(0.3)
        await client.post(
            f"/v1/rooms/{room_id}/messages",
            json={"from_participant_id": b["id"], "content": "ping from b"},
        )

    task = asyncio.create_task(late_post())
    r = await client.get(
        f"/v1/rooms/{room_id}/messages/wait",
        params={"timeout": 5, "for_participant": a["id"]},
    )
    await task
    assert r.status_code == 200
    body = r.json()
    assert body["timed_out"] is False
    assert any("ping from b" in (m["message"]["parts"][0]["content"]) for m in body["messages"])


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


@pytest.mark.asyncio
async def test_at_all_nudges_like_everyone(client: AsyncClient):
    room = (await client.post("/v1/rooms", json={"name": "atall", "goal": "broadcast"})).json()
    room_id = room["id"]
    human = (
        await client.post(
            f"/v1/rooms/{room_id}/join",
            json={"name": "ops", "harness": "human"},
        )
    ).json()
    await client.post(
        f"/v1/rooms/{room_id}/join",
        json={"name": "worker", "harness": "grok"},
    )
    res = (
        await client.post(
            f"/v1/rooms/{room_id}/messages",
            json={
                "from_participant_id": human["id"],
                "content": "@all stand by for deploy",
            },
        )
    ).json()
    assert res["nudge_count"] == 1
    assert res["nudged"][0]["name"] == "worker"


@pytest.mark.asyncio
async def test_rename_allows_spaces(client: AsyncClient):
    room = (await client.post("/v1/rooms", json={"name": "id-room", "goal": "names"})).json()
    room_id = room["id"]
    p = (
        await client.post(
            f"/v1/rooms/{room_id}/join",
            json={"name": "mark", "harness": "human"},
        )
    ).json()
    updated = (
        await client.patch(
            f"/v1/rooms/{room_id}/participants/{p['id']}",
            json={"name": "  Mark   Dula  "},
        )
    ).json()
    assert updated["id"] == p["id"]
    assert updated["name"] == "Mark Dula"
    listed = (await client.get(f"/v1/rooms/{room_id}/participants")).json()["participants"]
    assert any(x["name"] == "Mark Dula" for x in listed)


@pytest.mark.asyncio
async def test_pair_code_create_and_redeem(client: AsyncClient):
    room = (await client.post("/v1/rooms", json={"name": "pair-room", "goal": "phone"})).json()
    created = (
        await client.post(
            "/v1/pair",
            json={"room_id": room["id"], "label": "iphone", "ttl_seconds": 600},
        )
    ).json()
    assert created.get("code")
    assert "pair=" in created.get("url", "")
    assert room["id"] in created.get("url", "")
    assert "urls" in created
    assert "access" in created

    redeemed = (
        await client.post(
            "/v1/pair/redeem",
            json={"code": created["code"], "name": "phone-mark"},
        )
    ).json()
    assert redeemed["ok"] is True
    assert redeemed["room_id"] == room["id"]
    assert redeemed["harness"] == "mobile"
    assert redeemed["suggested_name"] == "phone-mark"

    bad = await client.post("/v1/pair/redeem", json={"code": "ZZZZZZ"})
    assert bad.status_code == 404


@pytest.mark.asyncio
async def test_pair_prefers_tailscale_base_when_requested(client: AsyncClient, monkeypatch):
    """Cellular path: pair QR must advertise MagicDNS when network=tailscale."""
    from opengateway.config import GatewayConfig, GatewayMode
    from opengateway.server import create_app
    from httpx import ASGITransport, AsyncClient as AC

    cfg = GatewayConfig(
        mode=GatewayMode.PUBLIC,
        host="0.0.0.0",
        port=8765,
        auth_token=None,
        require_auth=False,
        public_url="http://192.168.1.50:8765",
        network="lan",
        name="lan",
        tailscale_hostname="hub.tailnet-xxxx.ts.net",
    )
    app = create_app(config=cfg)
    transport = ASGITransport(app=app)
    async with AC(transport=transport, base_url="http://test") as c:
        room = (await c.post("/v1/rooms", json={"name": "cell", "goal": "away"})).json()
        created = (
            await c.post(
                "/v1/pair",
                json={
                    "room_id": room["id"],
                    "network": "tailscale",
                    "base_url": "https://hub.tailnet-xxxx.ts.net",
                },
            )
        ).json()
        assert created["url"].startswith("https://hub.tailnet-xxxx.ts.net/")
        assert "pair=" in created["url"]
        assert created.get("urls", {}).get("tailscale", "").startswith("https://")
        assert created.get("access", {}).get("tailscale") == "https://hub.tailnet-xxxx.ts.net"

        gws = (await c.get("/v1/gateways")).json()["gateways"]
        assert any(
            g.get("network") == "tailscale"
            and "ts.net" in (g.get("base_url") or "")
            for g in gws
        )


@pytest.mark.asyncio
async def test_network_diagnostics(client: AsyncClient):
    r = await client.get("/v1/network")
    assert r.status_code == 200
    body = r.json()
    assert "tailscale" in body
    assert "probes" in body
    assert "recommended" in body
    assert body["recommended"]["mode"] == "serve"


@pytest.mark.asyncio
async def test_device_api_keys_scoped(tmp_path):
    """Master token can mint device keys; scoped key can read but not mint keys."""
    from opengateway.config import GatewayConfig, GatewayMode
    from opengateway.server import create_app
    from opengateway.store import Store

    st = Store(db_path=tmp_path / "keys.db", audit=True)
    cfg = GatewayConfig(
        mode=GatewayMode.PUBLIC,
        host="127.0.0.1",
        port=8765,
        auth_token="master-secret-key",
        require_auth=True,
        network="lan",
        name="keys-gw",
    )
    app = create_app(store=st, config=cfg)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        master = {"Authorization": "Bearer master-secret-key"}
        created = (
            await c.post(
                "/v1/keys",
                json={
                    "name": "phone",
                    "device_label": "iPhone",
                    "scopes": ["read", "write"],
                    "role": "observer",
                },
                headers=master,
            )
        ).json()
        assert created.get("token", "").startswith("ogk_")
        device = {"Authorization": f"Bearer {created['token']}"}
        # Device can list rooms
        r = await c.get("/v1/rooms", headers=device)
        assert r.status_code == 200
        # Device cannot mint keys (admin-only)
        denied = await c.post(
            "/v1/keys",
            json={"name": "evil", "scopes": ["admin"]},
            headers=device,
        )
        assert denied.status_code == 403
        listed = (await c.get("/v1/keys", headers=master)).json()["keys"]
        assert any(k["id"] == created["id"] for k in listed)


@pytest.mark.asyncio
async def test_audit_log_public_mode(tmp_path, monkeypatch):
    """Public/auth gateways record redacted audit entries."""
    monkeypatch.setenv("OPENGATEWAY_AUDIT", "true")
    monkeypatch.setenv("OPENGATEWAY_DB", str(tmp_path / "audit.db"))
    from opengateway.config import GatewayConfig, GatewayMode
    from opengateway.server import create_app
    from opengateway.store import Store

    st = Store(db_path=tmp_path / "audit.db", audit=True)
    cfg = GatewayConfig(
        mode=GatewayMode.PUBLIC,
        host="127.0.0.1",
        port=8765,
        auth_token="test-token-audit",
        require_auth=True,
        network="lan",
        name="audit-gw",
    )
    app = create_app(store=st, config=cfg)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        headers = {"Authorization": "Bearer test-token-audit"}
        room = (
            await c.post(
                "/v1/rooms",
                json={"name": "audited", "goal": "log me"},
                headers=headers,
            )
        ).json()
        await c.post(
            f"/v1/rooms/{room['id']}/join",
            json={"name": "alice", "harness": "human"},
            headers=headers,
        )
        audit = (await c.get("/v1/audit", headers=headers)).json()
        assert audit["enabled"] is True
        assert audit["count"] >= 1
        actions = {e["action"] for e in audit["audit"]}
        assert "room.create" in actions or "participant.join" in actions
        # Secrets must not appear raw in detail blobs
        blob = str(audit)
        assert "test-token-audit" not in blob


@pytest.mark.asyncio
async def test_push_vapid_endpoint(client: AsyncClient):
    r = await client.get("/v1/push/vapid")
    assert r.status_code == 200
    body = r.json()
    assert "configured" in body
    assert body["configured"] is False


@pytest.mark.asyncio
async def test_user_register_login_session(tmp_path):
    """First user is admin; session token authenticates API."""
    from opengateway.config import GatewayConfig, GatewayMode
    from opengateway.server import create_app
    from opengateway.store import Store

    st = Store(db_path=tmp_path / "users.db", audit=True)
    cfg = GatewayConfig(
        mode=GatewayMode.PUBLIC,
        host="127.0.0.1",
        port=8765,
        auth_token="master-for-ops",
        require_auth=True,
        network="public",
        name="users-gw",
    )
    app = create_app(store=st, config=cfg)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        stt = (await c.get("/v1/auth/status")).json()
        assert stt["has_users"] is False
        reg = (
            await c.post(
                "/v1/auth/register",
                json={
                    "email": "admin@example.com",
                    "password": "password123",
                    "org_name": "Acme",
                    "display_name": "Admin",
                },
            )
        ).json()
        assert reg["user"]["role"] == "admin"
        assert reg["token"].startswith("ogs_")
        headers = {"Authorization": f"Bearer {reg['token']}"}
        rooms = await c.get("/v1/rooms", headers=headers)
        assert rooms.status_code == 200
        # create room under tenant
        r = (
            await c.post(
                "/v1/rooms",
                json={"name": "t1", "goal": "x", "created_by": "Admin"},
                headers=headers,
            )
        ).json()
        assert r.get("tenant_id") == reg["tenant_id"]
        login = (
            await c.post(
                "/v1/auth/login",
                json={"email": "admin@example.com", "password": "password123"},
            )
        ).json()
        assert login["token"].startswith("ogs_")


@pytest.mark.asyncio
async def test_setup_claim_one_time(tmp_path):
    """First UI visitor can claim master token once (Railway bootstrap)."""
    from opengateway.config import GatewayConfig, GatewayMode
    from opengateway.server import create_app
    from opengateway.store import Store

    st = Store(db_path=tmp_path / "setup.db", audit=True)
    cfg = GatewayConfig(
        mode=GatewayMode.PUBLIC,
        host="127.0.0.1",
        port=8765,
        auth_token="master-setup-token-xyz",
        require_auth=True,
        network="public",
        name="setup-gw",
    )
    app = create_app(store=st, config=cfg)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        status = (await c.get("/v1/setup")).json()
        assert status["claimable"] is True
        claimed = (await c.post("/v1/setup/claim")).json()
        assert claimed["token"] == "master-setup-token-xyz"
        status2 = (await c.get("/v1/setup")).json()
        assert status2["claimable"] is False
        again = await c.post("/v1/setup/claim")
        assert again.status_code == 410


@pytest.mark.asyncio
async def test_stale_online_marked_offline_and_nudge_cancelled(store: Store):
    from datetime import timedelta

    from opengateway.models import Participant, Room, Task, TaskStatus, utcnow

    room = await store.create_room(Room(name="stale", goal="x", created_by="t"))
    p = await store.join_room(
        room.id, Participant(name="sleepy", harness="grok", role="contributor")
    )
    p.last_seen_at = utcnow() - timedelta(seconds=300)
    task = await store.create_task(
        Task(
            room_id=room.id,
            title="Respond to someone",
            created_by="t",
            claimed_by=p.id,
            status=TaskStatus.CLAIMED,
            metadata={"nudge": True, "assignee_id": p.id},
        )
    )
    marked = await store.refresh_stale_online(room.id, max_age_seconds=120)
    assert p.id in marked
    assert p.status.value == "offline"
    updated = await store.get_task(room.id, task.id)
    assert updated is not None
    assert updated.status == TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_nudge_skips_offline_agents(client: AsyncClient):
    room = (await client.post("/v1/rooms", json={"name": "nudge-off", "goal": "x"})).json()
    room_id = room["id"]
    human = (
        await client.post(
            f"/v1/rooms/{room_id}/join",
            json={"name": "ops", "harness": "human"},
        )
    ).json()
    online = (
        await client.post(
            f"/v1/rooms/{room_id}/join",
            json={"name": "online-bot", "harness": "grok"},
        )
    ).json()
    ghost = (
        await client.post(
            f"/v1/rooms/{room_id}/join",
            json={"name": "ghost-bot", "harness": "claude-code"},
        )
    ).json()
    # Mark ghost offline
    await client.patch(
        f"/v1/rooms/{room_id}/participants/{ghost['id']}",
        json={"status": "offline"},
    )
    res = (
        await client.post(
            f"/v1/rooms/{room_id}/messages",
            json={
                "from_participant_id": human["id"],
                "content": "@all hello only online",
            },
        )
    ).json()
    names = {n["name"] for n in res["nudged"]}
    assert names == {"online-bot"}
    assert res["nudge_count"] == 1
    assert online["name"] == "online-bot"


@pytest.mark.asyncio
async def test_wait_returns_next_since_cursor(client: AsyncClient):
    room = (await client.post("/v1/rooms", json={"name": "wait-cur", "goal": "c"})).json()
    room_id = room["id"]
    a = (
        await client.post(
            f"/v1/rooms/{room_id}/join",
            json={"name": "a", "harness": "grok"},
        )
    ).json()
    b = (
        await client.post(
            f"/v1/rooms/{room_id}/join",
            json={"name": "b", "harness": "grok"},
        )
    ).json()
    posted = (
        await client.post(
            f"/v1/rooms/{room_id}/messages",
            json={"from_participant_id": b["id"], "content": "cursor check"},
        )
    ).json()
    mid = posted.get("id") or posted.get("message", {}).get("id")
    r = await client.get(
        f"/v1/rooms/{room_id}/messages/wait",
        params={"timeout": 1, "for_participant": a["id"]},
    )
    body = r.json()
    assert "next_since" in body
    assert "last_id" in body
    assert body["next_since"] == mid or any(
        m["id"] == body["next_since"] for m in body.get("messages") or []
    )


@pytest.mark.asyncio
async def test_global_search_rooms_and_type_filter(client: AsyncClient):
    room = (
        await client.post(
            "/v1/rooms",
            json={"name": "search-lab", "goal": "find me later"},
        )
    ).json()
    room_id = room["id"]
    p = (
        await client.post(
            f"/v1/rooms/{room_id}/join",
            json={"name": "finder", "harness": "grok"},
        )
    ).json()
    q = (
        await client.post(
            f"/v1/rooms/{room_id}/join",
            json={"name": "peer", "harness": "claude-code"},
        )
    ).json()
    await client.post(
        f"/v1/rooms/{room_id}/messages",
        json={"from_participant_id": p["id"], "content": "needle in haystack phrase"},
    )
    await client.post(
        f"/v1/rooms/{room_id}/messages",
        json={
            "from_participant_id": p["id"],
            "to_participant_id": q["id"],
            "content": "secret dm needle phrase",
        },
    )
    await client.post(
        f"/v1/rooms/{room_id}/tasks",
        json={"title": "ship search API", "created_by": p["id"]},
    )

    r = await client.get("/v1/search", params={"q": "search-lab"})
    assert r.status_code == 200
    hits = r.json()["hits"]
    assert any(h["type"] == "room" and h["title"] == "search-lab" for h in hits)

    r2 = await client.get("/v1/search", params={"q": "needle"})
    types = {h["type"] for h in r2.json()["hits"]}
    assert "message" in types
    assert "dm" in types
    dm_hit = next(h for h in r2.json()["hits"] if h["type"] == "dm")
    assert dm_hit.get("type_label") == "DM"
    assert "DM ·" in dm_hit["subtitle"]

    r3 = await client.get("/v1/search", params={"q": "type:task ship"})
    body = r3.json()
    assert body.get("parsed", {}).get("type") == "task"
    assert any(h["type"] == "task" for h in body["hits"])

    r4 = await client.get("/v1/search", params={"q": "type:dm"})
    assert all(h["type"] == "dm" for h in r4.json()["hits"])


@pytest.mark.asyncio
async def test_gateways_list_includes_self(client: AsyncClient):
    r = await client.get("/v1/gateways")
    assert r.status_code == 200
    body = r.json()
    assert "gateways" in body
    assert any(g.get("is_self") for g in body["gateways"])
    assert "tips" in body
    assert "tailscale" in body["tips"]


@pytest.mark.asyncio
async def test_public_mode_requires_auth():
    from opengateway.config import GatewayConfig, GatewayMode
    import opengateway.auth as auth_mod

    # Isolate rate-limit buckets for this test
    with auth_mod._fail_lock:
        auth_mod._fail_buckets.clear()

    store = Store(db_path=None)
    cfg = GatewayConfig(
        mode=GatewayMode.PUBLIC,
        host="0.0.0.0",
        auth_token="secret-token-xyz",
        require_auth=True,
        network="public",
        name="test-public",
    )
    app = create_app(store, config=cfg)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Health stays open
        assert (await ac.get("/ping")).status_code == 200
        # API locked
        r = await ac.get("/v1/rooms")
        assert r.status_code == 401
        # Bearer works
        r2 = await ac.get(
            "/v1/rooms",
            headers={"Authorization": "Bearer secret-token-xyz"},
        )
        assert r2.status_code == 200
        # Query token works (SSE-style)
        r3 = await ac.get("/v1/rooms", params={"token": "secret-token-xyz"})
        assert r3.status_code == 200


@pytest.mark.asyncio
async def test_auth_rate_limit_after_failures():
    from opengateway.config import GatewayConfig, GatewayMode
    import opengateway.auth as auth_mod

    with auth_mod._fail_lock:
        auth_mod._fail_buckets.clear()
    # Tighten limits for a fast test
    old_max, old_win = auth_mod._AUTH_FAIL_MAX, auth_mod._AUTH_FAIL_WINDOW_SEC
    auth_mod._AUTH_FAIL_MAX = 5
    auth_mod._AUTH_FAIL_WINDOW_SEC = 60.0
    try:
        store = Store(db_path=None)
        cfg = GatewayConfig(
            mode=GatewayMode.PUBLIC,
            host="0.0.0.0",
            auth_token="good-token",
            require_auth=True,
            network="lan",
            name="rate",
        )
        app = create_app(store, config=cfg)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Missing token: 401 but does not burn rate limit
            for _ in range(10):
                r = await ac.get("/v1/rooms")
                assert r.status_code == 401
            # Wrong tokens: count toward limit
            for _ in range(5):
                r = await ac.get(
                    "/v1/rooms",
                    headers={"Authorization": "Bearer wrong-token"},
                )
                assert r.status_code == 401
            r = await ac.get(
                "/v1/rooms",
                headers={"Authorization": "Bearer wrong-token"},
            )
            assert r.status_code == 429
            # Good token after clear
            with auth_mod._fail_lock:
                auth_mod._fail_buckets.clear()
            ok = await ac.get(
                "/v1/rooms",
                headers={"Authorization": "Bearer good-token"},
            )
            assert ok.status_code == 200
    finally:
        auth_mod._AUTH_FAIL_MAX = old_max
        auth_mod._AUTH_FAIL_WINDOW_SEC = old_win
        with auth_mod._fail_lock:
            auth_mod._fail_buckets.clear()


@pytest.mark.asyncio
async def test_dm_is_private_and_lists_peer_status(client: AsyncClient):
    room = (await client.post("/v1/rooms", json={"name": "dm-room", "goal": "private"})).json()
    room_id = room["id"]
    a = (
        await client.post(
            f"/v1/rooms/{room_id}/join",
            json={"name": "alice", "harness": "human"},
        )
    ).json()
    b = (
        await client.post(
            f"/v1/rooms/{room_id}/join",
            json={"name": "bob", "harness": "grok", "role": "contributor"},
        )
    ).json()
    # Private DM
    dm = (
        await client.post(
            f"/v1/rooms/{room_id}/messages",
            json={
                "from_participant_id": a["id"],
                "to_participant_id": b["id"],
                "content": "secret hello",
            },
        )
    ).json()
    assert dm["to_participant_id"] == b["id"]

    # for_participant=a sees DM; unscoped list includes it but UI filters public
    for_a = (
        await client.get(
            f"/v1/rooms/{room_id}/messages",
            params={"for_participant": a["id"]},
        )
    ).json()["messages"]
    assert any("secret hello" in (m["message"]["parts"][0]["content"]) for m in for_a)

    threads = (
        await client.get(
            f"/v1/rooms/{room_id}/dms",
            params={"participant_id": a["id"]},
        )
    ).json()["threads"]
    assert len(threads) == 1
    assert threads[0]["peer_id"] == b["id"]
    assert threads[0]["peer_status"] in {"online", "offline"}
    assert threads[0]["peer_name"] == "bob"

    # Participants sorted online-first
    people = (await client.get(f"/v1/rooms/{room_id}/participants")).json()["participants"]
    assert people[0]["status"] == "online"


@pytest.mark.asyncio
async def test_tailscale_identity_headers_on_localhost():
    from opengateway.config import GatewayConfig, GatewayMode

    store = Store(db_path=None)
    cfg = GatewayConfig(
        mode=GatewayMode.PUBLIC,
        host="127.0.0.1",
        auth_token="secret-token-xyz",
        require_auth=True,
        network="tailscale",
        trust_tailscale_identity=True,
        name="serve",
    )
    app = create_app(store, config=cfg)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        assert (await ac.get("/v1/rooms")).status_code == 401
        ok = await ac.get(
            "/v1/rooms",
            headers={"Tailscale-User-Login": "matt@example.com"},
        )
        assert ok.status_code == 200


def test_serve_mode_binds_localhost():
    import os
    from opengateway.config import load_gateway_config

    for k in list(os.environ):
        if k.startswith("OPENGATEWAY_"):
            del os.environ[k]
    os.environ["OPENGATEWAY_MODE"] = "serve"
    os.environ["OPENGATEWAY_AUTH_TOKEN"] = "tok"
    os.environ["OPENGATEWAY_PORT"] = "8765"
    cfg = load_gateway_config()
    assert cfg.host == "127.0.0.1"
    assert cfg.network == "tailscale"
    assert cfg.serve_hint and "tailscale serve" in cfg.serve_hint
    assert cfg.require_auth is True


def test_open_lan_bind_not_mislabeled_tailscale():
    """Having Tailscale installed must not brand an open LAN bind as Serve."""
    import os
    from opengateway.config import load_gateway_config

    for k in list(os.environ):
        if k.startswith("OPENGATEWAY_"):
            del os.environ[k]
    os.environ["OPENGATEWAY_MODE"] = "public"
    os.environ["OPENGATEWAY_VIA"] = "open"
    os.environ["OPENGATEWAY_HOST"] = "0.0.0.0"
    os.environ["OPENGATEWAY_AUTH_TOKEN"] = "tok"
    os.environ["OPENGATEWAY_PUBLIC_URL"] = "http://192.168.1.233:8765"
    os.environ["OPENGATEWAY_PORT"] = "8765"
    cfg = load_gateway_config()
    assert cfg.host == "0.0.0.0"
    assert cfg.network == "lan"
    assert cfg.require_auth is True
    assert cfg.serve_hint is None
