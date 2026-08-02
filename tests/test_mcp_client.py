"""MCP client unit tests — auth headers, join_room participant_id alias, import smoke."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest


def test_mcp_fastmcp_importable():
    """Regression: mcp 2.x removed mcp.server.fastmcp (pin mcp>=1.0,<2)."""
    from mcp.server.fastmcp import FastMCP

    assert FastMCP is not None
    import opengateway.mcp_server as mod

    assert mod.mcp is not None


def test_auth_headers_from_opengateway_auth_token(monkeypatch):
    monkeypatch.setenv("OPENGATEWAY_AUTH_TOKEN", "ogk_test_secret")
    monkeypatch.delenv("OPENGATEWAY_TOKEN", raising=False)
    from opengateway import mcp_server as mod

    assert mod._auth_headers() == {"Authorization": "Bearer ogk_test_secret"}


def test_auth_headers_fallback_opengateway_token(monkeypatch):
    monkeypatch.delenv("OPENGATEWAY_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("OPENGATEWAY_TOKEN", "master-from-alias")
    from opengateway import mcp_server as mod

    assert mod._auth_headers() == {"Authorization": "Bearer master-from-alias"}


def test_auth_headers_empty_when_unset(monkeypatch):
    monkeypatch.delenv("OPENGATEWAY_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("OPENGATEWAY_TOKEN", raising=False)
    from opengateway import mcp_server as mod

    assert mod._auth_headers() == {}


def test_client_sends_authorization(monkeypatch):
    monkeypatch.setenv("OPENGATEWAY_AUTH_TOKEN", "ogk_hdr")
    monkeypatch.setenv("OPENGATEWAY_URL", "http://hub.test")
    from opengateway import mcp_server as mod

    with mod._client() as c:
        assert c.headers.get("Authorization") == "Bearer ogk_hdr"
        assert str(c.base_url).rstrip("/") == "http://hub.test"


def test_json_401_without_token_includes_hint(monkeypatch):
    monkeypatch.delenv("OPENGATEWAY_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("OPENGATEWAY_TOKEN", raising=False)
    from opengateway import mcp_server as mod

    req = httpx.Request("GET", "http://hub.test/v1/rooms")
    resp = httpx.Response(401, json={"detail": "Unauthorized"}, request=req)
    out = mod._json(resp)
    assert out["status_code"] == 401
    assert "OPENGATEWAY_AUTH_TOKEN" in out.get("hint", "")


def test_join_room_aliases_participant_id(monkeypatch):
    monkeypatch.setenv("OPENGATEWAY_AUTH_TOKEN", "ogk_x")
    from opengateway import mcp_server as mod

    payload = {
        "id": "1c65cb22-b00d-49a2-9921-8f32a3383793",
        "name": "grok",
        "harness": "grok",
        "status": "online",
        "room_id": "d85aa8c4-fb21-4d17-a9ab-7908ed99286a",
    }

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=payload)
    mock_resp.status_code = 200

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post = MagicMock(return_value=mock_resp)

    with patch.object(mod, "_client", return_value=mock_client):
        raw = mod.join_room(
            room_id="d85aa8c4-fb21-4d17-a9ab-7908ed99286a",
            name="grok",
            harness="grok",
        )

    data = json.loads(raw)
    assert data["id"] == payload["id"]
    assert data["participant_id"] == payload["id"]
