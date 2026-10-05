"""Delivery policy and loop guards."""

from __future__ import annotations

from opengateway.delivery import (
    delivery_id_for,
    event_message_body,
    is_all_call,
    should_suppress_wake,
    should_wake_seat,
)
from opengateway.delivery_guard import DeliveryGuard


def test_hub_envelope_text_extraction():
    text = "@all please stop the empty ping pong and ship IM."
    ev = {
        "message": {
            "from_name": "human",
            "message": {
                "parts": [{"content": text}],
            },
        }
    }
    assert event_message_body(ev) == text


def test_addressed_only_wake():
    human_all = {
        "id": "m1",
        "from_participant_id": "h1",
        "from_name": "human",
        "to_participant_id": None,
        "message": {"parts": [{"content": "@all fix IM"}]},
    }
    assert should_wake_seat(human_all, seat_participant_id="a1", seat_name="alice")[0]

    agent_chat = {
        "id": "m2",
        "from_participant_id": "a1",
        "from_name": "alice",
        "to_participant_id": None,
        "message": {"parts": [{"content": "I finished the API layer."}]},
    }
    wake, reason, _ = should_wake_seat(
        agent_chat, seat_participant_id="b1", seat_name="grok"
    )
    assert not wake
    assert reason == "transcript_only"


def test_mention_wake_only_target():
    msg = {
        "id": "m3",
        "from_participant_id": "h1",
        "from_name": "human",
        "message": {"parts": [{"content": "@grok please review"}]},
    }
    assert should_wake_seat(msg, seat_participant_id="g1", seat_name="grok")[0]
    assert not should_wake_seat(msg, seat_participant_id="c1", seat_name="claude")[0]


def test_empty_loop_suppressed():
    loop = {
        "message": {
            "from_name": "claude",
            "message": {"parts": [{"content": "inbox was empty"}]},
        }
    }
    assert should_suppress_wake(loop)[0]


def test_delivery_dedupe_id_stable():
    a = delivery_id_for("src", "peer")
    b = delivery_id_for("src", "peer")
    assert a == b


def test_delivery_guard_one_response():
    g = DeliveryGuard()
    did = "d1"
    assert g.check_post_allowed(room_id="r", from_participant_id="p", metadata={"delivery_id": did})[0]
    g.record_outbound(room_id="r", from_participant_id="p", metadata={"delivery_id": did})
    assert not g.check_post_allowed(
        room_id="r", from_participant_id="p", metadata={"delivery_id": did}
    )[0]


def test_is_all_call():
    assert is_all_call("hey @all — ping")
    assert not is_all_call("just chatting")
