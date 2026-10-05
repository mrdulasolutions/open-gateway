"""Hub-authoritative speaking floor for multi-agent @all coordination."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

FLOOR_HOLD_TIMEOUT_SEC = 90.0


@dataclass
class _Chain:
    chain_id: str
    source_message_id: str
    queue: list[str]
    index: int = 0
    holder: Optional[str] = None
    holder_since: float = 0.0

    def _expire_stale_holder(self) -> None:
        if self.holder and self.holder_since:
            if time.time() - self.holder_since > FLOOR_HOLD_TIMEOUT_SEC:
                self.holder = None
                self.holder_since = 0.0
                self.index += 1

    def try_grant(self, participant_id: str) -> tuple[bool, str]:
        self._expire_stale_holder()
        if not self.queue:
            return True, "empty_queue"
        if len(self.queue) == 1 and self.queue[0] == participant_id:
            self.holder = participant_id
            self.holder_since = time.time()
            return True, "sole_target"
        if self.holder == participant_id:
            return True, "already_holding"
        head = self.queue[self.index] if self.index < len(self.queue) else None
        if head == participant_id and self.holder is None:
            self.holder = participant_id
            self.holder_since = time.time()
            return True, "granted"
        if self.index >= len(self.queue):
            return True, "past_queue"
        return False, "waiting"

    def release(self, participant_id: str) -> Optional[str]:
        if self.holder != participant_id:
            return None
        self.holder = None
        self.holder_since = 0.0
        self.index += 1
        if self.index < len(self.queue):
            return self.queue[self.index]
        return None

    def position(self, participant_id: str) -> int:
        try:
            return self.queue.index(participant_id)
        except ValueError:
            return -1


class RoomSpeakingFloor:
    """Serializes harness turns when multiple seats share one delivery chain."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._chains: dict[str, _Chain] = {}

    def open_chain(
        self,
        room_id: str,
        *,
        chain_id: str,
        source_message_id: str,
        participant_ids: list[str],
    ) -> None:
        ordered = list(dict.fromkeys(participant_ids))
        if not ordered:
            return
        with self._lock:
            self._chains[room_id] = _Chain(
                chain_id=chain_id,
                source_message_id=source_message_id,
                queue=ordered,
            )

    def cancel_room(self, room_id: str) -> None:
        with self._lock:
            self._chains.pop(room_id, None)

    def acquire(
        self,
        room_id: str,
        *,
        participant_id: str,
        chain_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            chain = self._chains.get(room_id)
            if not chain or chain.chain_id != chain_id:
                return {"granted": True, "reason": "no_active_floor"}
            granted, reason = chain.try_grant(participant_id)
            pos = chain.position(participant_id)
            return {
                "granted": granted,
                "reason": reason,
                "position": pos,
                "queue_len": len(chain.queue),
                "chain_id": chain.chain_id,
            }

    def release(self, room_id: str, *, participant_id: str) -> dict[str, Any]:
        with self._lock:
            chain = self._chains.get(room_id)
            if not chain:
                return {"released": False, "reason": "no_floor"}
            nxt = chain.release(participant_id)
            if chain.index >= len(chain.queue) and not chain.holder:
                self._chains.pop(room_id, None)
            return {
                "released": True,
                "next_participant_id": nxt,
                "chain_id": chain.chain_id,
            }

    def status(self, room_id: str = "") -> dict[str, Any]:
        with self._lock:
            if room_id:
                ch = self._chains.get(room_id)
                if not ch:
                    return {"room_id": room_id, "active": False}
                return {
                    "room_id": room_id,
                    "active": True,
                    "chain_id": ch.chain_id,
                    "queue": ch.queue,
                    "index": ch.index,
                    "holder": ch.holder,
                }
            return {
                "rooms": {
                    rid: {
                        "chain_id": c.chain_id,
                        "queue": c.queue,
                        "index": c.index,
                        "holder": c.holder,
                    }
                    for rid, c in self._chains.items()
                }
            }


def chain_id_from_wake_batch(events: list[dict[str, Any]]) -> str:
    for ev in reversed(events):
        msg = ev.get("message") if isinstance(ev.get("message"), dict) else ev
        meta = msg.get("metadata") if isinstance(msg, dict) else {}
        if isinstance(meta, dict):
            cid = str(meta.get("chain_id") or "").strip()
            if cid:
                return cid
        mid = str(msg.get("id") or "") if isinstance(msg, dict) else ""
        if mid:
            return mid
    return ""


def wait_for_floor_grant(
    client: Any,
    *,
    room_id: str,
    participant_id: str,
    chain_id: str,
    timeout_sec: float = FLOOR_HOLD_TIMEOUT_SEC,
    poll_sec: float = 0.25,
) -> bool:
    """Poll hub until this seat may run a harness turn (or timeout)."""
    if not chain_id:
        return True
    deadline = time.time() + max(1.0, timeout_sec)
    path = f"/v1/rooms/{room_id}/speaking-floor/acquire"
    body = {"participant_id": participant_id, "chain_id": chain_id}
    while time.time() < deadline:
        try:
            r = client.post(path, json=body, timeout=10.0)
            if r.status_code >= 400:
                return True
            data = r.json() or {}
            if data.get("granted"):
                return True
        except Exception:
            return True
        time.sleep(poll_sec)
    return False
