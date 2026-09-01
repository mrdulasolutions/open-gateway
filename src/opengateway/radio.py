"""Always-on radio for agents — background long-poll so presence stays listening.

When an MCP (or CLI) agent joins a room, start_radio() runs a daemon thread that
long-polls /v1/rooms/{id}/messages/wait. That:

  1. Keeps last_poll_at fresh → presence=listening in Live Ops
  2. Buffers inbound messages for get_inbox / drain_inbox tools
  3. Does not require the LLM to stay inside wait_for_messages forever

stop_radio() / leave ends the loop. Without this, agents join once and go deaf —
the product fails its core multi-agent chat promise.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Optional

import httpx


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _auth_headers(token: str = "") -> dict[str, str]:
    t = (
        token
        or os.environ.get("OPENGATEWAY_AUTH_TOKEN")
        or os.environ.get("OPENGATEWAY_TOKEN")
        or ""
    ).strip()
    if not t:
        return {}
    return {"Authorization": f"Bearer {t}"}


@dataclass
class RadioSession:
    room_id: str
    participant_id: str
    name: str
    base_url: str
    auth_token: str = ""
    timeout: float = 45.0
    since: Optional[str] = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    thread: Optional[threading.Thread] = None
    inbox: Deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=500))
    last_error: str = ""
    polls: int = 0
    messages_buffered: int = 0
    started_at: str = field(default_factory=_utcnow_iso)

    @property
    def key(self) -> str:
        return f"{self.room_id}:{self.participant_id}"

    def running(self) -> bool:
        return (
            self.thread is not None
            and self.thread.is_alive()
            and not self.stop_event.is_set()
        )


class RadioManager:
    """Process-wide always-on radio sessions (one thread per room participant)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, RadioSession] = {}

    def start(
        self,
        *,
        room_id: str,
        participant_id: str,
        name: str = "agent",
        base_url: str = "",
        auth_token: str = "",
        timeout: float = 45.0,
        restart: bool = True,
    ) -> dict[str, Any]:
        if not room_id or not participant_id:
            return {"ok": False, "error": "room_id and participant_id required"}
        base = (base_url or os.environ.get("OPENGATEWAY_URL") or "http://127.0.0.1:8765").rstrip(
            "/"
        )
        key = f"{room_id}:{participant_id}"
        with self._lock:
            existing = self._sessions.get(key)
            if existing and existing.running():
                if not restart:
                    return self._status(existing, note="already_listening")
                existing.stop_event.set()
            sess = RadioSession(
                room_id=room_id,
                participant_id=participant_id,
                name=name or "agent",
                base_url=base,
                auth_token=auth_token,
                timeout=max(5.0, min(float(timeout), 120.0)),
            )
            t = threading.Thread(
                target=self._loop,
                args=(sess,),
                name=f"og-radio-{participant_id[:8]}",
                daemon=True,
            )
            sess.thread = t
            self._sessions[key] = sess
            t.start()
            return self._status(sess, note="radio_started")

    def stop(self, room_id: str = "", participant_id: str = "") -> dict[str, Any]:
        stopped: list[str] = []
        with self._lock:
            keys = list(self._sessions.keys())
            for key in keys:
                rid, pid = key.split(":", 1)
                if room_id and rid != room_id:
                    continue
                if participant_id and pid != participant_id:
                    continue
                sess = self._sessions.pop(key, None)
                if sess:
                    sess.stop_event.set()
                    stopped.append(key)
        return {"ok": True, "stopped": stopped}

    def stop_all(self) -> dict[str, Any]:
        return self.stop()

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._status(s) for s in self._sessions.values()]

    def drain_inbox(
        self, participant_id: str = "", room_id: str = "", max_items: int = 100
    ) -> dict[str, Any]:
        """Pop buffered messages for the agent (or all sessions matching filters)."""
        out: list[dict[str, Any]] = []
        with self._lock:
            sessions = list(self._sessions.values())
        for s in sessions:
            if participant_id and s.participant_id != participant_id:
                continue
            if room_id and s.room_id != room_id:
                continue
            while s.inbox and len(out) < max_items:
                out.append(s.inbox.popleft())
        return {
            "ok": True,
            "messages": out,
            "count": len(out),
            "hint": "Reply with post_message; radio keeps listening in the background.",
        }

    def peek_inbox(
        self, participant_id: str = "", room_id: str = "", limit: int = 20
    ) -> dict[str, Any]:
        with self._lock:
            sessions = list(self._sessions.values())
        msgs: list[dict[str, Any]] = []
        for s in sessions:
            if participant_id and s.participant_id != participant_id:
                continue
            if room_id and s.room_id != room_id:
                continue
            msgs.extend(list(s.inbox)[-limit:])
        return {"ok": True, "messages": msgs[-limit:], "count": len(msgs[-limit:])}

    def _status(self, sess: RadioSession, note: str = "") -> dict[str, Any]:
        return {
            "ok": True,
            "radio": "on" if sess.running() else "off",
            "room_id": sess.room_id,
            "participant_id": sess.participant_id,
            "name": sess.name,
            "base_url": sess.base_url,
            "polls": sess.polls,
            "messages_buffered": sess.messages_buffered,
            "inbox_size": len(sess.inbox),
            "last_error": sess.last_error or None,
            "started_at": sess.started_at,
            "note": note or None,
            "presence": "listening" if sess.running() else "joined",
        }

    def _loop(self, sess: RadioSession) -> None:
        wait_timeout = sess.timeout
        client_timeout = wait_timeout + 20.0
        headers = _auth_headers(sess.auth_token)
        since = sess.since
        # Small delay so join response returns before first long-poll
        time.sleep(0.15)
        while not sess.stop_event.is_set():
            params: dict[str, Any] = {
                "timeout": wait_timeout,
                "limit": 50,
                "for_participant": sess.participant_id,
            }
            if since:
                params["since"] = since
            try:
                with httpx.Client(
                    base_url=sess.base_url,
                    timeout=client_timeout,
                    headers=headers,
                ) as c:
                    r = c.get(
                        f"/v1/rooms/{sess.room_id}/messages/wait",
                        params=params,
                    )
                    r.raise_for_status()
                    data = r.json()
                sess.polls += 1
                sess.last_error = ""
                msgs = data.get("messages") or []
                since = data.get("next_since") or data.get("last_id") or since
                for m in msgs:
                    if isinstance(m, dict):
                        since = m.get("id") or since
                        # Skip our own posts
                        if m.get("from_participant_id") == sess.participant_id:
                            continue
                        item = {
                            "room_id": sess.room_id,
                            "participant_id": sess.participant_id,
                            "message": m,
                            "received_at": _utcnow_iso(),
                        }
                        sess.inbox.append(item)
                        sess.messages_buffered += 1
                        _maybe_radio_wake(sess, item)
                sess.since = since
            except Exception as e:
                sess.last_error = str(e)
                # Back off on errors so we don't spin
                if sess.stop_event.wait(2.0):
                    break
                continue
            # Yield so other threads run; timed_out already kept radio warm
            if sess.stop_event.wait(0.05):
                break


def _maybe_radio_wake(sess: RadioSession, item: dict[str, Any]) -> None:
    """Optional env-configured wake when radio buffers a message.

    Prefer ``opengateway im`` for full IM. This is a thin bridge for MCP
    processes that already run radio and want push into Hermes/webhooks.
    """
    url = (os.environ.get("OPENGATEWAY_RADIO_WAKE_URL") or "").strip()
    hook = (os.environ.get("OPENGATEWAY_RADIO_WAKE_HOOK") or "").strip()
    if not url and not hook:
        return
    payload = {
        "type": "im_wake",
        "source": "mcp_radio",
        "room_id": sess.room_id,
        "participant_id": sess.participant_id,
        "agent_name": sess.name,
        "base_url": sess.base_url,
        "messages": [item],
        "count": 1,
        "ts": _utcnow_iso(),
    }
    line = __import__("json").dumps(payload, default=str)
    if url:
        try:
            httpx.post(
                url,
                content=line,
                headers={"Content-Type": "application/json"},
                timeout=15.0,
            )
        except Exception as e:
            sess.last_error = f"wake_url: {e}"
    if hook:
        try:
            import shlex
            import subprocess

            args = shlex.split(hook) if isinstance(hook, str) else hook
            subprocess.Popen(
                args,
                shell=False,
                stdin=subprocess.PIPE,
                env={
                    **os.environ,
                    "OPENGATEWAY_IM_WAKE": "1",
                    "OPENGATEWAY_IM_ROOM": sess.room_id,
                    "OPENGATEWAY_IM_PARTICIPANT": sess.participant_id,
                },
            ).communicate(input=line.encode("utf-8"), timeout=30)
        except Exception as e:
            sess.last_error = f"wake_hook: {e}"


# Process singleton used by MCP + CLI
radio = RadioManager()
