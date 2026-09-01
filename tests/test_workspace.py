"""Path-addressed room workspace."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from opengateway.config import GatewayConfig, GatewayMode
from opengateway.server import create_app
from opengateway.store import Store
from opengateway.workspace import (
    delete_workspace_file,
    list_workspace,
    normalize_path,
    put_workspace_file,
    read_workspace_bytes,
    workspace_r2_key,
)


def test_normalize_path_rejects_traversal():
    assert normalize_path("docs/plan.md") == "docs/plan.md"
    assert normalize_path("/src/app.py") == "src/app.py"
    with pytest.raises(ValueError):
        normalize_path("../etc/passwd")
    with pytest.raises(ValueError):
        normalize_path("a/../../b")
    with pytest.raises(ValueError):
        normalize_path("")


def test_workspace_r2_key(monkeypatch):
    monkeypatch.setenv("OPENGATEWAY_TENANT_SLUG", "acme")
    k = workspace_r2_key("room1", "docs/a.md")
    assert k == "tenants/acme/rooms/room1/workspace/docs/a.md"


def test_put_list_read_delete(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENGATEWAY_FILES_BACKEND", "disk")
    monkeypatch.delenv("OPENGATEWAY_PUBLIC_URL", raising=False)
    st = Store(db_path=None)
    meta = put_workspace_file(
        st,
        room_id="r1",
        path="docs/plan.md",
        raw=b"# Plan\nhello",
        content_type="text/markdown",
        updated_by="alice",
        files_root=tmp_path,
    )
    assert meta["path"] == "docs/plan.md"
    assert meta["bytes"] == len(b"# Plan\nhello")

    files = list_workspace(st, "r1")
    assert len(files) == 1
    assert files[0]["path"] == "docs/plan.md"

    got = read_workspace_bytes(st, "r1", "docs/plan.md")
    assert got is not None
    raw, entry = got
    assert raw == b"# Plan\nhello"
    assert entry["updated_by"] == "alice"

    # prefix filter
    put_workspace_file(
        st,
        room_id="r1",
        path="src/main.py",
        raw=b"print(1)",
        content_type="text/x-python",
        files_root=tmp_path,
    )
    assert len(list_workspace(st, "r1", prefix="docs")) == 1
    assert len(list_workspace(st, "r1", prefix="src")) == 1
    assert len(list_workspace(st, "r1")) == 2

    assert delete_workspace_file(st, "r1", "docs/plan.md") is True
    assert list_workspace(st, "r1", prefix="docs") == []
    assert read_workspace_bytes(st, "r1", "docs/plan.md") is None


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENGATEWAY_FILES_BACKEND", "disk")
    monkeypatch.delenv("OPENGATEWAY_PUBLIC_URL", raising=False)
    store = Store(db_path=None)
    cfg = GatewayConfig(mode=GatewayMode.INTERNAL, require_auth=False, auth_token=None)
    app = create_app(store, config=cfg)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_workspace_http_roundtrip(client: AsyncClient):
    room = (await client.post("/v1/rooms", json={"name": "ws", "goal": "files"})).json()
    rid = room["id"]

    put = await client.put(
        f"/v1/rooms/{rid}/workspace/notes/todo.md",
        json={
            "content": "- ship vault\n- ship workspace\n",
            "content_type": "text/markdown",
            "updated_by": "tester",
        },
    )
    assert put.status_code == 200, put.text
    assert put.json()["path"] == "notes/todo.md"

    listed = (await client.get(f"/v1/rooms/{rid}/workspace")).json()
    assert any(f["path"] == "notes/todo.md" for f in listed["files"])

    got = await client.get(
        f"/v1/rooms/{rid}/workspace/notes/todo.md",
        params={"format": "json"},
    )
    assert got.status_code == 200
    body = got.json()
    assert "ship vault" in (body.get("content") or "")

    snap = (await client.get(f"/v1/rooms/{rid}/snapshot")).json()
    assert any(f["path"] == "notes/todo.md" for f in snap.get("workspace") or [])

    deleted = await client.delete(f"/v1/rooms/{rid}/workspace/notes/todo.md")
    assert deleted.status_code == 200
    empty = (await client.get(f"/v1/rooms/{rid}/workspace")).json()
    assert empty["files"] == []


@pytest.mark.asyncio
async def test_workspace_rejects_traversal(client: AsyncClient):
    room = (await client.post("/v1/rooms", json={"name": "ws2", "goal": "x"})).json()
    rid = room["id"]
    bad = await client.put(
        f"/v1/rooms/{rid}/workspace/../../etc/passwd",
        json={"content": "nope"},
    )
    # FastAPI may normalize path before handler; either 400 or not-found-ish
    assert bad.status_code in {400, 404, 422}
