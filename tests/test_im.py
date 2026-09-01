"""Agent IM wake helpers (no live hub required)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from opengateway.im import (
    ImConfig,
    ImDaemon,
    build_wake_prompt,
    event_message_body,
    format_messages_for_prompt,
    looks_like_ping,
)


def test_resolve_wake_mode_from_harness():
    cfg = ImConfig(base_url="https://h", room="r", harness="claude-code", wake="harness")
    d = ImDaemon(cfg)
    assert d._resolve_wake_mode() == "claude"
    cfg2 = ImConfig(base_url="https://h", room="r", harness="grok", wake="harness")
    assert ImDaemon(cfg2)._resolve_wake_mode() == "grok"
    cfg3 = ImConfig(base_url="https://h", room="r", harness="hermes", wake="hermes")
    assert ImDaemon(cfg3)._resolve_wake_mode() == "hermes"


def test_looks_like_ping():
    assert looks_like_ping("ping")
    assert looks_like_ping("Ping?")
    assert looks_like_ping("hey ping")
    assert not looks_like_ping("please review the ping-pong design doc thoroughly")
    assert not looks_like_ping("")


def test_event_message_body():
    ev = {
        "message": {
            "from_name": "matt",
            "parts": [{"content": "ping"}],
        }
    }
    assert event_message_body(ev) == "ping"
    assert "matt" in format_messages_for_prompt([ev])
    assert "ping" in format_messages_for_prompt([ev])


def test_build_wake_prompt_contains_ids():
    p = build_wake_prompt(
        agent_name="Hermes COO",
        room_id="room-abc",
        participant_id="part-xyz",
        messages=[
            {
                "message": {
                    "from_name": "ops",
                    "parts": [{"content": "ping"}],
                }
            }
        ],
        base_url="https://hub.example",
    )
    assert "Hermes COO" in p
    assert "room-abc" in p
    assert "part-xyz" in p
    assert "ping" in p
    assert "wait_for_messages" in p  # banned in instructions


def test_enqueue_wake_debounce_flushes(monkeypatch):
    cfg = ImConfig(
        base_url="https://hub.example",
        room="main",
        name="Hermes COO",
        wake="auto",
        debounce_seconds=0.05,
    )
    d = ImDaemon(cfg)
    d.room_id = "room-1"
    d.participant_id = "part-1"

    calls: list[list] = []

    def fake_dispatch(events):
        calls.append(events)

    monkeypatch.setattr(d, "_dispatch_wake", fake_dispatch)

    ev = {
        "type": "message",
        "message": {"from_name": "a", "parts": [{"content": "ping"}]},
    }
    d._enqueue_wake(ev)
    d._enqueue_wake(ev)
    # Force flush
    d._flush_wake()
    assert len(calls) == 1
    assert len(calls[0]) == 2


def test_wake_auto_posts_pong():
    cfg = ImConfig(
        base_url="https://hub.example",
        room="main",
        name="Hermes COO",
        wake="auto",
        auth_token="tok",
        auto_pong_text="pong",
    )
    d = ImDaemon(cfg)
    d.room_id = "room-1"
    d.participant_id = "part-me"

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post = MagicMock(return_value=mock_resp)

    with patch("opengateway.im.httpx.Client", return_value=mock_client):
        d._wake_auto(
            [
                {
                    "message": {
                        "from_name": "human",
                        "parts": [{"content": "ping"}],
                    }
                }
            ]
        )

    mock_client.post.assert_called_once()
    args, kwargs = mock_client.post.call_args
    assert args[0] == "/v1/rooms/room-1/messages"
    body = kwargs["json"]
    assert body["from_participant_id"] == "part-me"
    assert body["parts"][0]["content"] == "pong"
