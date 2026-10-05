"""Per-room idempotency: one agent response per delivery, hop limits."""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any, Optional

from opengateway.delivery import DEFAULT_MAX_AGENT_HOP, hop_exceeded


class DeliveryGuard:
    """In-process guard for duplicate wakes and runaway A2A chains."""

    def __init__(self, *, max_hop: int = DEFAULT_MAX_AGENT_HOP, ttl_sec: float = 3600.0) -> None:
        self.max_hop = max_hop
        self.ttl = ttl_sec
        self._lock = threading.Lock()
        # (room_id, delivery_id, participant_id) -> ts
        self._responded: dict[tuple[str, str, str], float] = defaultdict(float)
        self._loop_breaks: dict[str, int] = defaultdict(int)

    def _prune(self, now: float) -> None:
        cutoff = now - self.ttl
        stale = [k for k, ts in self._responded.items() if ts < cutoff]
        for k in stale:
            self._responded.pop(k, None)

    def already_responded(
        self, room_id: str, delivery_id: str, participant_id: str
    ) -> bool:
        if not delivery_id:
            return False
        now = time.time()
        with self._lock:
            self._prune(now)
            key = (room_id, delivery_id, participant_id)
            return key in self._responded

    def mark_responded(
        self, room_id: str, delivery_id: str, participant_id: str
    ) -> None:
        if not delivery_id:
            return
        with self._lock:
            self._responded[(room_id, delivery_id, participant_id)] = time.time()

    def check_post_allowed(
        self,
        *,
        room_id: str,
        from_participant_id: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        meta = metadata or {}
        delivery_id = str(meta.get("in_reply_to_delivery") or meta.get("delivery_id") or "")
        if delivery_id and self.already_responded(room_id, delivery_id, from_participant_id):
            return False, "duplicate_delivery_response"
        hop = int(meta.get("agent_hop") or 0)
        if hop_exceeded(hop, max_hop=self.max_hop):
            self._record_loop_break(room_id)
            return False, "agent_hop_exceeded"
        return True, ""

    def record_outbound(
        self,
        *,
        room_id: str,
        from_participant_id: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        meta = metadata or {}
        delivery_id = str(meta.get("in_reply_to_delivery") or meta.get("delivery_id") or "")
        if delivery_id:
            self.mark_responded(room_id, delivery_id, from_participant_id)

    def _record_loop_break(self, room_id: str) -> None:
        with self._lock:
            self._loop_breaks[room_id] += 1

    def loop_break_count(self, room_id: str) -> int:
        with self._lock:
            return int(self._loop_breaks.get(room_id, 0))

    def status(self, room_id: str = "") -> dict[str, Any]:
        with self._lock:
            if room_id:
                return {
                    "room_id": room_id,
                    "loop_breaks": self._loop_breaks.get(room_id, 0),
                }
            return {"loop_breaks_by_room": dict(self._loop_breaks)}
