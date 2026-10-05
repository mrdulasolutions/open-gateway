"""Canonical message text extraction and addressed-only delivery policy.

Shared by server fan-out, radio/IM seat runtime, MCP inboxes, and listen daemons.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from opengateway.nudge_policy import is_auto_ack_content, metadata_blocks_nudge

_EMPTY_LOOP_RE = re.compile(
    r"(came through empty|inbox (was|is) empty|inbox is clear|"
    r"wake landed, inbox empty|messages came through empty|"
    r"last posts came through empty|your message was empty)",
    re.IGNORECASE,
)

_ALL_NEEDLES = (
    "everyone",
    "everybody",
    "@all",
    "@everyone",
    "all agents",
    "hey all",
    "hey everyone",
    "all of you",
    "you all",
)


def _message_nodes(ev: dict[str, Any]) -> list[dict[str, Any]]:
    """Hub rows nest parts under message.message.parts; listen events wrap again."""
    nodes: list[dict[str, Any]] = []

    def add(node: Any) -> None:
        if isinstance(node, dict) and node not in nodes:
            nodes.append(node)

    add(ev)
    outer = ev.get("message")
    add(outer)
    if isinstance(outer, dict):
        add(outer.get("message"))
    return nodes


def message_text_shallow(msg: dict[str, Any]) -> str:
    """Extract text from a single hub row or {message: {parts}} shape."""
    parts = (msg.get("message") or {}).get("parts") or msg.get("parts") or []
    chunks: list[str] = []
    for p in parts:
        if isinstance(p, dict) and p.get("content"):
            chunks.append(str(p["content"]))
    return "\n".join(chunks) if chunks else ""


def event_message_body(ev: dict[str, Any]) -> str:
    """Plain text from a listen/im event or raw hub message dict."""
    if not isinstance(ev, dict):
        return ""
    for node in _message_nodes(ev):
        body = message_text_shallow({"message": node}) or message_text_shallow(node)
        if body:
            return body
    return ""


def room_message_text(msg: dict[str, Any]) -> str:
    """Canonical text from a serialized RoomMessage or hub wait row."""
    if not isinstance(msg, dict):
        return ""
    inner = msg.get("message")
    if isinstance(inner, dict):
        # ACP Message with parts
        parts = inner.get("parts") or []
        chunks: list[str] = []
        for p in parts:
            if isinstance(p, dict) and p.get("content"):
                chunks.append(str(p["content"]))
        if chunks:
            return "\n".join(chunks)
    return event_message_body({"message": msg})


def event_is_system(ev: dict[str, Any]) -> bool:
    for node in _message_nodes(ev):
        if (node.get("from_name") or "").strip().lower() == "system":
            return True
        meta = node.get("metadata")
        if isinstance(meta, dict) and (
            meta.get("system") or meta.get("nudge_summary") or meta.get("kind") == "system"
        ):
            return True
    if isinstance(ev.get("metadata"), dict) and (
        ev["metadata"].get("system") or ev["metadata"].get("nudge_summary")
    ):
        return True
    return False


def event_is_empty_loop(ev: dict[str, Any]) -> bool:
    body = event_message_body(ev).strip()
    if not body or len(body) > 320:
        return False
    return _EMPTY_LOOP_RE.search(body) is not None


def is_all_call(text: str) -> bool:
    t = (text or "").lower()
    return any(n in t for n in _ALL_NEEDLES)


def mentioned_names(text: str, peer_names: list[str]) -> set[str]:
    """Return lowercased peer names mentioned with @ in text."""
    if not text or not peer_names:
        return set()
    found: set[str] = set()
    lower = text
    for name in sorted(peer_names, key=len, reverse=True):
        n = (name or "").strip()
        if not n:
            continue
        if f"@{n}".lower() in lower.lower():
            found.add(n.lower())
    return found


def delivery_id_for(source_message_id: str, target_participant_id: str) -> str:
    raw = f"{source_message_id}:{target_participant_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


@dataclass
class StructuredDelivery:
    delivery_id: str
    source_message_id: str
    room_id: str
    from_participant_id: str
    from_name: str
    target_participant_id: str
    text: str
    kind: str = "delivery"
    chain_id: str = ""
    agent_hop: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_metadata(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "structured_delivery": True,
            "delivery_id": self.delivery_id,
            "source_message_id": self.source_message_id,
            "target_participant_id": self.target_participant_id,
            "chain_id": self.chain_id or self.delivery_id,
            "agent_hop": self.agent_hop,
            **self.metadata,
        }


def structured_delivery_from_message(
    msg: dict[str, Any],
    *,
    target_participant_id: str,
    canonical_text: str,
) -> StructuredDelivery:
    mid = str(msg.get("id") or "")
    meta = msg.get("metadata") if isinstance(msg.get("metadata"), dict) else {}
    chain = str(meta.get("chain_id") or mid)
    hop = int(meta.get("agent_hop") or 0)
    did = str(meta.get("delivery_id") or delivery_id_for(mid, target_participant_id))
    return StructuredDelivery(
        delivery_id=did,
        source_message_id=mid,
        room_id=str(msg.get("room_id") or ""),
        from_participant_id=str(msg.get("from_participant_id") or ""),
        from_name=str(msg.get("from_name") or ""),
        target_participant_id=target_participant_id,
        text=canonical_text,
        chain_id=chain,
        agent_hop=hop,
    )


def should_suppress_wake(ev: dict[str, Any]) -> tuple[bool, str]:
    """True when a message must never wake an agent seat."""
    if event_is_system(ev):
        return True, "system"
    if event_is_empty_loop(ev):
        return True, "empty_loop"
    body = event_message_body(ev).strip()
    if not body:
        return True, "blank_body"
    if is_auto_ack_content(body):
        return True, "auto_ack"
    meta = ev.get("metadata") if isinstance(ev.get("metadata"), dict) else {}
    hub = ev.get("message") if isinstance(ev.get("message"), dict) else {}
    hub_meta = hub.get("metadata") if isinstance(hub.get("metadata"), dict) else {}
    combined = {**hub_meta, **meta}
    if metadata_blocks_nudge(combined) and combined.get("nudge_summary"):
        return True, "nudge_summary"
    return False, ""


def should_wake_seat(
    msg: dict[str, Any],
    *,
    seat_participant_id: str,
    seat_name: str,
    peer_names: Optional[list[str]] = None,
) -> tuple[bool, str, Optional[str]]:
    """Addressed-only wake: @all, @name, DMs wake; public chat does not.

    Returns (wake, reason, delivery_id_for_dedupe).
    """
    if not isinstance(msg, dict):
        return False, "invalid", None
    from_id = str(msg.get("from_participant_id") or "")
    if from_id and from_id == seat_participant_id:
        return False, "self", None

    ev = {"message": msg, "metadata": msg.get("metadata")}
    suppressed, why = should_suppress_wake(ev)
    if suppressed:
        return False, why, None

    meta = msg.get("metadata") if isinstance(msg.get("metadata"), dict) else {}
    to_id = msg.get("to_participant_id")
    text = room_message_text(msg)
    delivery_id = meta.get("delivery_id")

    # Structured delivery DM to this seat
    if to_id and str(to_id) == seat_participant_id:
        if meta.get("structured_delivery") or meta.get("nudge"):
            did = str(delivery_id or delivery_id_for(str(msg.get("id") or ""), seat_participant_id))
            return True, "structured_dm", did
        return True, "dm", str(delivery_id or msg.get("id"))

    if to_id:
        return False, "dm_other", None

    # Public broadcast — only if this seat is addressed
    if is_all_call(text):
        did = str(delivery_id or delivery_id_for(str(msg.get("id") or ""), seat_participant_id))
        return True, "broadcast_all", did

    names = list(peer_names or [])
    if seat_name and seat_name not in names:
        names.append(seat_name)
    mentioned = mentioned_names(text, names)
    if seat_name.lower() in mentioned:
        did = str(delivery_id or delivery_id_for(str(msg.get("id") or ""), seat_participant_id))
        return True, "mention", did

    return False, "transcript_only", None


DEFAULT_MAX_AGENT_HOP = 12


def next_agent_hop(parent_meta: Optional[dict[str, Any]]) -> tuple[str, int]:
    meta = parent_meta or {}
    chain = str(meta.get("chain_id") or meta.get("delivery_id") or meta.get("source_message_id") or "")
    hop = int(meta.get("agent_hop") or 0) + 1
    if not chain:
        chain = str(meta.get("source_message_id") or "")
    return chain, hop


def hop_exceeded(hop: int, *, max_hop: int = DEFAULT_MAX_AGENT_HOP) -> bool:
    return hop > max_hop


def format_recent_room_transcript(
    rows: list[dict[str, Any]],
    *,
    max_lines: int = 16,
    max_body_len: int = 800,
) -> str:
    """Public room lines for harness prompts (skips system, check-ins, nudge rows)."""
    lines: list[str] = []
    for row in rows:
        event = {
            "from_name": row.get("from_name"),
            "from_participant_id": row.get("from_participant_id"),
            "message": {
                "from_name": row.get("from_name"),
                "from_participant_id": row.get("from_participant_id"),
                "message": row.get("message"),
                "metadata": row.get("metadata") or {},
            },
            "metadata": row.get("metadata") or {},
        }
        if event_is_system(event):
            continue
        meta = event["metadata"]
        if isinstance(meta, dict) and meta.get("checkin"):
            continue
        body = event_message_body(event).strip()
        if not body or body.lower().startswith("nudge:"):
            continue
        if len(body) > max_body_len:
            body = body[:max_body_len].rstrip() + "…"
        who = str(row.get("from_name") or row.get("from_participant_id") or "?")
        lines.append(f"{len(lines) + 1}. [{who}]: {body}")
    if not lines:
        return "(no earlier messages)"
    return "\n".join(lines[-max_lines:])
