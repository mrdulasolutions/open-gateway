"""Listen daemon, presence badges, MCP resources."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from opengateway.models import (
    JOINED_STALE_SECONDS,
    LISTENING_SECONDS,
    Harness,
    Participant,
    ParticipantStatus,
    presence_for,
    utcnow,
)


def test_presence_listening_from_last_poll():
    p = Participant(
        name="grok",
        harness=Harness.GROK,
        status=ParticipantStatus.ONLINE,
        last_seen_at=utcnow(),
        last_poll_at=utcnow(),
    )
    assert presence_for(p) == "listening"
    assert p.presence == "listening"


def test_presence_joined_without_poll():
    p = Participant(
        name="grok",
        harness=Harness.GROK,
        status=ParticipantStatus.ONLINE,
        last_seen_at=utcnow(),
        last_poll_at=None,
    )
    assert presence_for(p) == "joined"


def test_presence_offline_when_stale():
    old = utcnow() - timedelta(seconds=JOINED_STALE_SECONDS + 30)
    p = Participant(
        name="grok",
        harness=Harness.GROK,
        status=ParticipantStatus.ONLINE,
        last_seen_at=old,
        last_poll_at=old - timedelta(seconds=10),
    )
    # last_poll too old → not listening; last_seen too old → not joined
    assert presence_for(p) == "offline"


def test_presence_listening_window_expires():
    p = Participant(
        name="g",
        harness=Harness.GROK,
        status=ParticipantStatus.ONLINE,
        last_seen_at=utcnow(),
        last_poll_at=utcnow() - timedelta(seconds=LISTENING_SECONDS + 5),
    )
    assert presence_for(p) == "joined"


def test_listen_message_text():
    from opengateway.listen import message_text

    msg = {
        "from_name": "a",
        "message": {"parts": [{"content": "hello"}, {"content": "world"}]},
    }
    assert message_text(msg) == "hello\nworld"


def test_listen_sinks_file(tmp_path: Path):
    from opengateway.listen import ListenSinks

    path = tmp_path / "inbox.jsonl"
    sinks = ListenSinks(print_console=False, file_path=path)
    sinks.emit({"type": "message", "message": {"from_name": "x", "message": {"parts": []}}})
    assert path.exists()
    line = path.read_text().strip()
    assert json.loads(line)["type"] == "message"


@pytest.mark.asyncio
async def test_wait_marks_listening(monkeypatch):
    from opengateway.config import GatewayConfig, GatewayMode
    from opengateway.server import create_app
    from opengateway.store import Store

    monkeypatch.delenv("OPENGATEWAY_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("OPENGATEWAY_REQUIRE_AUTH", raising=False)
    store = Store(db_path=None)
    cfg = GatewayConfig(mode=GatewayMode.INTERNAL, require_auth=False, auth_token=None)
    app = create_app(store, config=cfg)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        room_resp = await c.post(
            "/v1/rooms", json={"name": "listen-test", "created_by": "t"}
        )
        assert room_resp.status_code == 200, room_resp.text
        room = room_resp.json()
        rid = room["id"]
        p = (
            await c.post(
                f"/v1/rooms/{rid}/join",
                json={"name": "radio", "harness": "grok", "role": "contributor"},
            )
        ).json()
        pid = p["id"]
        assert p.get("presence") == "joined" or p.get("last_poll_at") is None

        # short wait with for_participant → listening
        w = await c.get(
            f"/v1/rooms/{rid}/messages/wait",
            params={"for_participant": pid, "timeout": 0.1},
        )
        assert w.status_code == 200
        parts = (await c.get(f"/v1/rooms/{rid}/participants")).json()["participants"]
        me = next(x for x in parts if x["id"] == pid)
        assert me.get("presence") == "listening"
        assert me.get("last_poll_at") is not None


def test_mcp_resources_importable():
    import opengateway.mcp_server as mod

    assert hasattr(mod, "resource_gateway")
    assert hasattr(mod, "resource_listen_playbook")
    assert hasattr(mod, "begin_im_mode")
