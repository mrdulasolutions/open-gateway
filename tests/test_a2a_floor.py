"""Speaking floor and A2A coordination (no parallel @all harness turns)."""

from __future__ import annotations

from opengateway.delivery import DEFAULT_MAX_AGENT_HOP, hop_exceeded
from opengateway.delivery_guard import DeliveryGuard
from opengateway.room_floor import RoomSpeakingFloor
from opengateway.runner import RunnerService


def test_speaking_floor_serializes_two_targets():
    floor = RoomSpeakingFloor()
    floor.open_chain(
        "room-1",
        chain_id="chain-a",
        source_message_id="msg-1",
        participant_ids=["p-a", "p-b"],
    )
    first = floor.acquire("room-1", participant_id="p-a", chain_id="chain-a")
    second = floor.acquire("room-1", participant_id="p-b", chain_id="chain-a")
    assert first["granted"] is True
    assert second["granted"] is False
    floor.release("room-1", participant_id="p-a")
    third = floor.acquire("room-1", participant_id="p-b", chain_id="chain-a")
    assert third["granted"] is True


def test_hop_cap_allows_six_step_relay():
    guard = DeliveryGuard(max_hop=DEFAULT_MAX_AGENT_HOP)
    assert DEFAULT_MAX_AGENT_HOP == 12
    for hop in range(1, 13):
        ok, _ = guard.check_post_allowed(
            room_id="r",
            from_participant_id="a",
            metadata={"agent_hop": hop},
        )
        assert ok
    allowed, reason = guard.check_post_allowed(
        room_id="r",
        from_participant_id="a",
        metadata={"agent_hop": 13},
    )
    assert not allowed
    assert reason == "agent_hop_exceeded"
    assert hop_exceeded(13)


def test_managed_prompt_includes_transcript_on_resume():
    events = [
        {
            "message": {
                "from_name": "human",
                "message": {"parts": [{"content": "@bot go"}]},
            }
        }
    ]
    history = [
        {
            "from_name": "Grok",
            "message": {"parts": [{"content": "I already proposed plan B."}]},
        }
    ]
    from opengateway.runner import ManagedRuntime

    runtime = ManagedRuntime(
        spec=type("S", (), {"name": "Claudester", "harness": "claude-code"})(),
        participant_id="p1",
        hub_url="http://127.0.0.1:8765",
        agent_token="t",
        adapter=None,
    )
    prompt = RunnerService._managed_reply_prompt(
        runtime, events, history=history, resume=True
    )
    assert "plan B" in prompt
    assert "Grok" in prompt
    assert "harness session continues" in prompt

