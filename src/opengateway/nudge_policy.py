"""Nudge fan-out policy — anti-storm rules (OG-010 and related).

Pure functions + in-process rate limiter used by the HTTP post_message path.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from typing import Any, Optional

# Presence / auto-ack patterns that must not re-trigger nudge fan-out.
# Incident: "@Grok pong — Heard: …" caused pong↔nudge loops.
_PONG_HEAD = re.compile(
    r"^\s*@[\w][\w .'-]{0,80}?\s+"
    r"(?:pong|ack|got\s*it|roger|copy|👍|✅)\b",
    re.IGNORECASE,
)
_PONG_INLINE = re.compile(
    r"\bpong\b.{0,40}\b(?:heard|got|ack)\b|\b(?:heard|got|ack)\b.{0,40}\bpong\b",
    re.IGNORECASE,
)
_PURE_ACK = re.compile(
    r"^\s*(?:pong|ack|👍|✅|got\s*it|roger|copy that)\s*[.!…]*\s*$",
    re.IGNORECASE,
)

# Default caps (overridable via env in server wiring)
DEFAULT_MAX_NUDGES_PER_PAIR_PER_MIN = 2
DEFAULT_MAX_NUDGES_PER_ROOM_PER_MIN = 40
DEFAULT_STORM_THRESHOLD_PER_MIN = 12


def is_auto_ack_content(content: str) -> bool:
    """True for pure presence pongs / auto-acks that must not re-nudge."""
    t = (content or "").strip()
    if not t:
        return False
    if _PURE_ACK.match(t):
        return True
    if _PONG_HEAD.match(t):
        return True
    if _PONG_INLINE.search(t) and len(t) < 400:
        return True
    # Common agent template: "@Name pong — Heard: <snippet>"
    low = t.lower()
    if "pong" in low and ("heard:" in low or "heard —" in low or "heard -" in low):
        return True
    return False


def metadata_blocks_nudge(metadata: Optional[dict[str, Any]]) -> bool:
    """System / nudge DMs / summaries never fan out further."""
    meta = metadata or {}
    if meta.get("system"):
        return True
    if meta.get("nudge"):
        return True
    if meta.get("nudge_summary"):
        return True
    if meta.get("skip_nudge") or meta.get("no_nudge"):
        return True
    if meta.get("nudge_reply"):
        return True
    return False


def should_skip_nudge(
    *,
    content: str,
    metadata: Optional[dict[str, Any]] = None,
    to_participant_id: Optional[str] = None,
) -> tuple[bool, str]:
    """Return (skip, reason). Private DMs already handled by caller via to_id."""
    if metadata_blocks_nudge(metadata):
        return True, "metadata_exempt"
    if is_auto_ack_content(content):
        return True, "auto_ack"
    return False, ""


def should_create_nudge_task(
    *,
    want_nudge_all: bool,
    content: str,
    metadata: Optional[dict[str, Any]] = None,
) -> bool:
    """Tasks for @all / explicit nudge_all only — casual @mention = DM only (OG-011)."""
    meta = metadata or {}
    if meta.get("nudge_task") is True:
        return True
    if meta.get("nudge_task") is False:
        return False
    if is_auto_ack_content(content):
        return False
    return bool(want_nudge_all)


def presence_of(participant: Any) -> str:
    """Resolve presence string from a Participant or dict."""
    if participant is None:
        return "offline"
    pr = getattr(participant, "presence", None)
    if pr:
        return str(pr)
    if isinstance(participant, dict):
        return str(participant.get("presence") or "offline")
    try:
        from opengateway.models import presence_for

        return presence_for(participant)
    except Exception:
        return "offline"


def is_listen_only(participant: Any) -> bool:
    """True when agent is presence-only (no full chat/inference seat)."""
    caps = getattr(participant, "capabilities", None)
    if caps is None and isinstance(participant, dict):
        caps = participant.get("capabilities")
    caps_l = {str(c).lower() for c in (caps or [])}
    if "listen-only" in caps_l or "presence-only" in caps_l:
        return True
    if "chat" in caps_l:
        return False
    return False


def nudge_target_eligible(
    participant: Any,
    *,
    include_joined: bool = False,
) -> tuple[bool, str]:
    """OG-015: nudge only listening (optionally joined). Returns (ok, skip_reason)."""
    harness = getattr(participant, "harness", None)
    if harness is not None and hasattr(harness, "value"):
        harness = harness.value
    elif isinstance(participant, dict):
        harness = participant.get("harness")
    if str(harness or "").lower() == "human":
        return False, "human"

    presence = presence_of(participant)
    if presence == "listening":
        return True, ""
    if presence == "joined":
        if include_joined:
            return True, ""
        return False, "joined_not_listening"
    return False, "offline"


def message_kind_from_meta(
    metadata: Optional[dict[str, Any]],
    *,
    to_participant_id: Optional[str] = None,
) -> str:
    """OG-016: kind in {nudge, system, broadcast, chat, dm}."""
    meta = metadata or {}
    if meta.get("kind"):
        return str(meta["kind"])
    if meta.get("system") or meta.get("nudge_summary"):
        return "system"
    if meta.get("nudge"):
        return "nudge"
    if to_participant_id:
        return "dm"
    return "chat"


class NudgeRateLimiter:
    """Sliding-window caps to stop feedback storms + edge debug graph."""

    def __init__(
        self,
        *,
        max_per_pair_per_min: int = DEFAULT_MAX_NUDGES_PER_PAIR_PER_MIN,
        max_room_per_min: int = DEFAULT_MAX_NUDGES_PER_ROOM_PER_MIN,
        storm_threshold_per_min: int = DEFAULT_STORM_THRESHOLD_PER_MIN,
        window_sec: float = 60.0,
        edge_window_sec: float = 300.0,
    ) -> None:
        self.max_per_pair = max_per_pair_per_min
        self.max_room = max_room_per_min
        self.storm_threshold = storm_threshold_per_min
        self.window = window_sec
        self.edge_window = edge_window_sec
        self._pair: dict[tuple[str, str, str], deque[float]] = defaultdict(deque)
        self._room: dict[str, deque[float]] = defaultdict(deque)
        # (ts, room_id, from_id, to_id, from_name, to_name)
        self._edges: deque[tuple[float, str, str, str, str, str]] = deque(maxlen=2000)

    def _prune(self, q: deque[float], now: float) -> None:
        cutoff = now - self.window
        while q and q[0] < cutoff:
            q.popleft()

    def allow(
        self, room_id: str, from_id: str, to_id: str, *, now: Optional[float] = None
    ) -> tuple[bool, str]:
        t = time.time() if now is None else now
        pk = (room_id, from_id, to_id)
        pq = self._pair[pk]
        rq = self._room[room_id]
        self._prune(pq, t)
        self._prune(rq, t)
        if len(pq) >= self.max_per_pair:
            return False, "pair_rate_limit"
        if len(rq) >= self.max_room:
            return False, "room_rate_limit"
        pq.append(t)
        rq.append(t)
        return True, ""

    def record_edge(
        self,
        room_id: str,
        from_id: str,
        to_id: str,
        *,
        from_name: str = "",
        to_name: str = "",
        now: Optional[float] = None,
    ) -> None:
        t = time.time() if now is None else now
        self._edges.append((t, room_id, from_id, to_id, from_name, to_name))

    def edges_graph(
        self, room_id: str, *, minutes: float = 5.0, now: Optional[float] = None
    ) -> list[dict[str, Any]]:
        """Who nudged whom (aggregated) for Live Ops debug (OG-031)."""
        t = time.time() if now is None else now
        cutoff = t - max(60.0, minutes * 60.0)
        counts: dict[tuple[str, str, str, str], int] = defaultdict(int)
        for ts, rid, fid, tid, fn, tn in self._edges:
            if rid != room_id or ts < cutoff:
                continue
            counts[(fid, tid, fn, tn)] += 1
        out = [
            {
                "from_id": fid,
                "to_id": tid,
                "from_name": fn,
                "to_name": tn,
                "count": n,
            }
            for (fid, tid, fn, tn), n in sorted(
                counts.items(), key=lambda x: -x[1]
            )
        ]
        return out

    def room_stats(self, room_id: str, *, now: Optional[float] = None) -> dict[str, Any]:
        t = time.time() if now is None else now
        rq = self._room[room_id]
        self._prune(rq, t)
        n = len(rq)
        return {
            "nudges_last_minute": n,
            "storm": n >= self.storm_threshold,
            "storm_threshold": self.storm_threshold,
            "max_room_per_min": self.max_room,
            "max_per_pair_per_min": self.max_per_pair,
            "edges": self.edges_graph(room_id, minutes=5.0, now=t),
        }

    def document_grammar() -> str:
        """Human-readable nudge trigger grammar (OG-010 docs AC)."""
        return (
            "Nudge triggers (public room posts only; never private DMs):\n"
            "- @all / @everyone / 'everyone' / 'all agents' / nudge_all=true → fan-out to all "
            "online non-human agents (DM + claimed task).\n"
            "- @ExactAgentName → DM that agent only (no claimed task unless metadata.nudge_task).\n"
            "Never nudge on: metadata.system, metadata.nudge, metadata.nudge_summary, "
            "metadata.skip_nudge, or auto-ack content (pong/heard templates).\n"
            "Rate limits: per (from→to) pair and per room per minute; storm banner when "
            "room exceeds storm threshold."
        )
