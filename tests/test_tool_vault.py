"""Tool credential vault + hub proxy (secrets never listed)."""

from __future__ import annotations

import json

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from opengateway.api_keys import (
    SCOPE_ADMIN,
    SCOPE_READ,
    SCOPE_TOOLS,
    SCOPE_WRITE,
    scopes_allow,
)
from opengateway.config import GatewayConfig, GatewayMode
from opengateway.server import create_app
from opengateway.store import Store
from opengateway.tool_vault import (
    get_secret_value,
    list_credentials,
    put_credential,
    seal,
    unseal,
)


def test_seal_unseal_roundtrip(monkeypatch):
    monkeypatch.setenv("OPENGATEWAY_VAULT_KEY", "test-vault-key-xyz")
    token = seal("super-secret-token")
    assert token.startswith("v1:")
    assert "super-secret" not in token
    assert unseal(token) == "super-secret-token"


def test_list_never_includes_value(monkeypatch):
    monkeypatch.setenv("OPENGATEWAY_VAULT_KEY", "k2")
    st = Store(db_path=None)
    put_credential(
        st,
        name="github",
        value="ghp_secret_should_not_leak",
        description="GitHub PAT",
        allowed_hosts=["api.github.com"],
    )
    rows = list_credentials(st)
    assert len(rows) == 1
    assert rows[0]["name"] == "github"
    assert rows[0]["has_value"] is True
    blob = json.dumps(rows)
    assert "ghp_secret" not in blob
    assert "sealed" not in blob
    assert get_secret_value(st, "github") == "ghp_secret_should_not_leak"


def test_scopes_vault_and_proxy():
    assert scopes_allow([SCOPE_READ], "GET", "/v1/tools/credentials")
    assert scopes_allow([SCOPE_TOOLS], "GET", "/v1/tools/credentials")
    assert not scopes_allow([SCOPE_WRITE], "PUT", "/v1/tools/credentials/x")
    assert not scopes_allow([SCOPE_TOOLS], "PUT", "/v1/tools/credentials/x")
    assert scopes_allow([SCOPE_ADMIN], "PUT", "/v1/tools/credentials/x")
    assert scopes_allow([SCOPE_TOOLS], "POST", "/v1/tools/proxy")
    assert scopes_allow([SCOPE_WRITE], "POST", "/v1/tools/proxy")
    assert not scopes_allow([SCOPE_READ], "POST", "/v1/tools/proxy")


@pytest.fixture
def store() -> Store:
    return Store(db_path=None)


@pytest.fixture
async def client(store: Store, monkeypatch):
    monkeypatch.setenv("OPENGATEWAY_VAULT_KEY", "http-test-key")
    # Explicit config — earlier tests may pollute OPENGATEWAY_MODE/AUTH via os.environ
    cfg = GatewayConfig(mode=GatewayMode.INTERNAL, require_auth=False, auth_token=None)
    app = create_app(store, config=cfg)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_credentials_http_list_put_delete(client: AsyncClient, store: Store):
    r = await client.put(
        "/v1/tools/credentials/openai",
        json={
            "value": "sk-test-abc",
            "description": "OpenAI",
            "inject": "bearer",
            "allowed_hosts": ["api.openai.com"],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "openai"
    assert "value" not in body
    assert "sealed" not in body
    assert body["has_value"] is True

    listed = (await client.get("/v1/tools/credentials")).json()
    assert any(c["name"] == "openai" for c in listed["credentials"])
    assert "sk-test" not in json.dumps(listed)

    d = await client.delete("/v1/tools/credentials/openai")
    assert d.status_code == 200
    listed2 = (await client.get("/v1/tools/credentials")).json()
    assert listed2["credentials"] == []


@pytest.mark.asyncio
async def test_proxy_injects_and_ssrf_blocks(client: AsyncClient, monkeypatch):
    await client.put(
        "/v1/tools/credentials/demo",
        json={
            "value": "tok_xyz",
            "inject": "bearer",
            "allowed_hosts": ["example.com"],
        },
    )

    # SSRF: localhost blocked
    bad = await client.post(
        "/v1/tools/proxy",
        json={
            "credential": "demo",
            "method": "GET",
            "url": "http://127.0.0.1:9/",
        },
    )
    assert bad.status_code == 400
    assert "blocked" in bad.json()["detail"].lower() or "ssrf" in bad.json()["detail"].lower()

    # Host not allowed
    denied = await client.post(
        "/v1/tools/proxy",
        json={
            "credential": "demo",
            "method": "GET",
            "url": "https://evil.example.org/x",
        },
    )
    assert denied.status_code in {400, 403}

    # Mock successful upstream
    class _Resp:
        status_code = 200
        headers = {"content-type": "application/json"}
        content = b'{"ok":true}'

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def request(self, method, url, headers=None, content=None):
            assert method == "GET"
            assert "example.com" in url
            assert headers.get("Authorization") == "Bearer tok_xyz"
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    ok = await client.post(
        "/v1/tools/proxy",
        json={
            "credential": "demo",
            "method": "GET",
            "url": "https://example.com/v1/me",
        },
    )
    assert ok.status_code == 200
    data = ok.json()
    assert data["status_code"] == 200
    assert data["body"] == '{"ok":true}'
    assert "tok_xyz" not in json.dumps(data)
