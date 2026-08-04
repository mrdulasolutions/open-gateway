"""Production security regression tests (OSS OpenGateway).

Covers: WebSocket auth, LFI download, pair scoped keys, tenant IDOR,
DM privacy, audit admin-only, memory invites, artifact path strip.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from opengateway.config import GatewayConfig, GatewayMode
from opengateway.models import new_id, utcnow
from opengateway.server import create_app
from opengateway.store import Store
from opengateway.users import hash_password


def _public_cfg(token: str = "master-secret-token-xyz") -> GatewayConfig:
    return GatewayConfig(
        mode=GatewayMode.PUBLIC,
        host="127.0.0.1",
        port=8765,
        auth_token=token,
        require_auth=True,
        network="lan",
        name="sec-gw",
    )


@pytest.fixture
def store() -> Store:
    return Store(db_path=None, audit=True)


@pytest.fixture
async def authed_client(store: Store):
    app = create_app(store=store, config=_public_cfg())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.headers["Authorization"] = "Bearer master-secret-token-xyz"
        yield ac, app, store


@pytest.mark.asyncio
async def test_websocket_rejects_without_auth(authed_client):
    client, app, store = authed_client
    room = (await client.post("/v1/rooms", json={"name": "ws-room"})).json()
    p = (
        await client.post(
            f"/v1/rooms/{room['id']}/join",
            json={"name": "alice", "harness": "human"},
        )
    ).json()
    with TestClient(app) as tc:
        with pytest.raises(Exception):
            with tc.websocket_connect(
                f"/v1/rooms/{room['id']}/ws?participant_id={p['id']}"
            ) as ws:
                ws.receive_json()


@pytest.mark.asyncio
async def test_websocket_accepts_with_token_query(authed_client):
    client, app, store = authed_client
    room = (await client.post("/v1/rooms", json={"name": "ws-ok"})).json()
    p = (
        await client.post(
            f"/v1/rooms/{room['id']}/join",
            json={"name": "alice", "harness": "human"},
        )
    ).json()
    tok = "master-secret-token-xyz"
    with TestClient(app) as tc:
        with tc.websocket_connect(
            f"/v1/rooms/{room['id']}/ws?participant_id={p['id']}&token={tok}"
        ) as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello_ok"
            ws.send_json({"type": "message", "content": "secured hello"})
            ack = ws.receive_json()
            assert ack["type"] == "message_ack"


@pytest.mark.asyncio
async def test_artifact_path_cannot_lfi(authed_client, tmp_path):
    client, app, store = authed_client
    secret = tmp_path / "secret.txt"
    secret.write_text("LEAKED_SECRET")
    room = (await client.post("/v1/rooms", json={"name": "lfi"})).json()
    p = (
        await client.post(
            f"/v1/rooms/{room['id']}/join",
            json={"name": "alice", "harness": "human"},
        )
    ).json()
    art = (
        await client.post(
            f"/v1/rooms/{room['id']}/artifacts",
            json={
                "name": "evil",
                "shared_by": p["id"],
                "content": "inline",
                "metadata": {"path": str(secret)},
            },
        )
    ).json()
    # path must be stripped from stored metadata (no client LFI vector)
    assert "path" not in (art.get("metadata") or {})
    dl = await client.get(f"/v1/rooms/{room['id']}/files/{art['id']}/download")
    # May serve inline content "x" from the artifact body — never /tmp secret
    assert b"LEAKED_SECRET" not in dl.content
    if dl.status_code == 200:
        assert b"x" in dl.content or len(dl.content) < 100


@pytest.mark.asyncio
async def test_pair_redeem_mints_scoped_key_not_master(authed_client):
    client, app, store = authed_client
    room = (await client.post("/v1/rooms", json={"name": "pair"})).json()
    pair = (
        await client.post(
            "/v1/pair",
            json={"room_id": room["id"], "label": "phone"},
        )
    ).json()
    assert "token=" not in pair.get("url", "")
    assert "master-secret" not in pair.get("url", "")
    assert len(pair["code"]) >= 16

    # redeem without auth
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as bare:
        redeemed = (
            await bare.post(
                "/v1/pair/redeem",
                json={"code": pair["code"], "name": "mobile"},
            )
        ).json()
    assert redeemed["ok"] is True
    token = redeemed["auth_token"]
    assert token and token.startswith("ogk_")
    assert token != "master-secret-token-xyz"

    # scoped key can list rooms but not mint keys
    async with AsyncClient(transport=transport, base_url="http://test") as device:
        device.headers["Authorization"] = f"Bearer {token}"
        assert (await device.get("/v1/rooms")).status_code == 200
        assert (
            await device.post("/v1/keys", json={"name": "x", "scopes": ["admin"]})
        ).status_code == 403


@pytest.mark.asyncio
async def test_tenant_idor_blocked():
    store = Store(db_path=None, audit=True)
    app = create_app(store=store, config=_public_cfg())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        a = (
            await c.post(
                "/v1/auth/register",
                json={
                    "email": "a@ex.com",
                    "password": "password12",
                    "org_name": "Alpha",
                },
            )
        ).json()
        room = (
            await c.post(
                "/v1/rooms",
                json={"name": "alpha-secret"},
                headers={"Authorization": f"Bearer {a['token']}"},
            )
        ).json()
        await c.post(
            f"/v1/rooms/{room['id']}/messages",
            json={
                "from_participant_id": (
                    await c.post(
                        f"/v1/rooms/{room['id']}/join",
                        json={"name": "Alice", "harness": "human"},
                        headers={"Authorization": f"Bearer {a['token']}"},
                    )
                ).json()["id"],
                "content": "TENANT_A_SECRET",
            },
            headers={"Authorization": f"Bearer {a['token']}"},
        )

        tid_b = new_id()
        store._memory_tenants = getattr(store, "_memory_tenants", {})
        store._memory_tenants[tid_b] = {"id": tid_b, "name": "Beta", "slug": "beta"}
        uid_b = new_id()
        store._memory_users = getattr(store, "_memory_users", {})
        store._memory_users[uid_b] = {
            "id": uid_b,
            "tenant_id": tid_b,
            "email": "b@ex.com",
            "password_hash": hash_password("password12"),
            "role": "admin",
            "display_name": "Bob",
            "created_at": utcnow().isoformat(),
        }
        sess = await store.create_session(store._memory_users[uid_b])
        tb = sess["token"]
        headers = {"Authorization": f"Bearer {tb}"}

        assert (await c.get(f"/v1/rooms/{room['id']}", headers=headers)).status_code == 404
        assert (
            await c.get(f"/v1/rooms/{room['id']}/messages", headers=headers)
        ).status_code == 404
        assert (
            await c.get(f"/v1/rooms/{room['id']}/snapshot", headers=headers)
        ).status_code == 404
        assert (
            await c.post(
                f"/v1/rooms/{room['id']}/join",
                json={"name": "Eve", "harness": "human"},
                headers=headers,
            )
        ).status_code == 404
        search = await c.get(
            "/v1/search", params={"q": "TENANT_A_SECRET"}, headers=headers
        )
        assert search.status_code == 200
        assert search.json()["hits"] == []


@pytest.mark.asyncio
async def test_member_cannot_read_audit():
    store = Store(db_path=None, audit=True)
    app = create_app(store=store, config=_public_cfg())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        admin = (
            await c.post(
                "/v1/auth/register",
                json={
                    "email": "admin@ex.com",
                    "password": "password12",
                    "org_name": "Org",
                },
            )
        ).json()
        # Inject member
        uid = new_id()
        store._memory_users[uid] = {
            "id": uid,
            "tenant_id": admin["tenant_id"],
            "email": "mem@ex.com",
            "password_hash": hash_password("password12"),
            "role": "member",
            "display_name": "Mem",
            "created_at": utcnow().isoformat(),
        }
        sess = await store.create_session(store._memory_users[uid])
        r = await c.get(
            "/v1/audit", headers={"Authorization": f"Bearer {sess['token']}"}
        )
        assert r.status_code == 403
        # Master still ok
        r2 = await c.get(
            "/v1/audit",
            headers={"Authorization": "Bearer master-secret-token-xyz"},
        )
        assert r2.status_code == 200


@pytest.mark.asyncio
async def test_memory_invite_works():
    store = Store(db_path=None)
    first = await store.register_user(
        email="first@ex.com", password="password12", org_name="Org"
    )
    inv = await store.create_invite(
        tenant_id=first["tenant_id"], created_by="first@ex.com"
    )
    second = await store.register_user(
        email="second@ex.com",
        password="password12",
        invite_code=inv["code"],
    )
    assert second["tenant_id"] == first["tenant_id"]
    assert second["user"]["role"] == "member"


@pytest.mark.asyncio
async def test_phantom_tenant_invite_rejected():
    store = Store(db_path=None)
    await store.register_user(
        email="first@ex.com", password="password12", org_name="Org"
    )
    with pytest.raises(ValueError, match="Unknown tenant"):
        await store.create_invite(tenant_id="does-not-exist", created_by="x")


@pytest.mark.asyncio
async def test_cross_room_participant_moves_cleanly(authed_client):
    client, app, store = authed_client
    r1 = (await client.post("/v1/rooms", json={"name": "R1"})).json()
    r2 = (await client.post("/v1/rooms", json={"name": "R2"})).json()
    p = (
        await client.post(
            f"/v1/rooms/{r1['id']}/join",
            json={"name": "Alice", "harness": "human"},
        )
    ).json()
    moved = (
        await client.post(
            f"/v1/rooms/{r2['id']}/join",
            json={
                "name": "Alice",
                "harness": "human",
                "participant_id": p["id"],
            },
        )
    ).json()
    assert moved["room_id"] == r2["id"]
    room1 = await store.get_room(r1["id"])
    assert p["id"] not in room1.participant_ids
    room2 = await store.get_room(r2["id"])
    assert p["id"] in room2.participant_ids


@pytest.mark.asyncio
async def test_docs_not_public_when_auth_required(authed_client):
    client, app, store = authed_client
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as bare:
        assert (await bare.get("/docs")).status_code == 401
        assert (await bare.get("/openapi.json")).status_code == 401
