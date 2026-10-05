"""Unified seat runtime: one long-poll, cursor persistence, delivery dedupe, wake filter."""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Deque, Optional

import httpx

from opengateway.delivery import should_wake_seat

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

from opengateway.seat_registry import (
    SeatRecord,
    release_seat_lock,
    seat_lock_active,
    try_acquire_seat_lock,
    upsert_seat,
)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cursor_path(room_id: str, participant_id: str) -> Path:
    d = Path.home() / ".opengateway" / "cursors"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{room_id}_{participant_id}.json"


def load_cursor(room_id: str, participant_id: str) -> Optional[str]:
    p = _cursor_path(room_id, participant_id)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return str(data.get("since") or "") or None
    except Exception:
        return None


def save_cursor(room_id: str, participant_id: str, since: Optional[str]) -> None:
    if not since:
        return
    p = _cursor_path(room_id, participant_id)
    p.write_text(json.dumps({"since": since, "updated_at": _utcnow_iso()}), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def clear_cursor(room_id: str, participant_id: str) -> None:
    try:
        _cursor_path(room_id, participant_id).unlink(missing_ok=True)
    except OSError:
        pass


def seed_cursor_at_tail(client: httpx.Client, room_id: str, participant_id: str) -> Optional[str]:
    """Start at room tail so reconnects do not replay history."""
    r = client.get(
        f"/v1/rooms/{room_id}/messages",
        params={"limit": 1, "for_participant": participant_id},
    )
    r.raise_for_status()
    msgs = (r.json() or {}).get("messages") or []
    if msgs:
        return str(msgs[-1].get("id") or "")
    return None


@dataclass
class SeatSession:
    room_id: str
    participant_id: str
    name: str
    base_url: str
    auth_token: str = field(default="", repr=False, compare=False)
    timeout: float = 45.0
    since: Optional[str] = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    thread: Optional[threading.Thread] = None
    inbox: Deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=500))
    seen_delivery_ids: set[str] = field(default_factory=set)
    last_error: str = ""
    polls: int = 0
    messages_buffered: int = 0
    wakes_suppressed: int = 0
    last_wake_reason: str = ""
    last_delivery_id: str = ""
    started_at: str = field(default_factory=_utcnow_iso)
    on_wake: Optional[Callable[[list[dict[str, Any]]], None]] = None
    on_stop: Optional[Callable[[], None]] = None
    peer_names: list[str] = field(default_factory=list)
    lock_owner: str = "mcp"
    managed_agent_id: str = ""
    client: Optional[httpx.Client] = field(default=None, repr=False, compare=False)
    client_lock: threading.Lock = field(
        default_factory=threading.Lock,
        repr=False,
        compare=False,
    )

    @property
    def key(self) -> str:
        return f"{self.room_id}:{self.participant_id}"

    def running(self) -> bool:
        return (
            self.thread is not None
            and self.thread.is_alive()
            and not self.stop_event.is_set()
        )


class SeatRuntimeManager:
    """Process-wide seat sessions (radio + IM share this)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, SeatSession] = {}

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
        on_wake: Optional[Callable[[list[dict[str, Any]]], None]] = None,
        on_stop: Optional[Callable[[], None]] = None,
        peer_names: Optional[list[str]] = None,
        register: bool = True,
        harness: str = "mcp",
        wake: str = "harness",
        lock_owner: str = "mcp",
        managed_agent_id: str = "",
        runner_id: str = "",
    ) -> dict[str, Any]:
        if not room_id or not participant_id:
            return {"ok": False, "error": "room_id and participant_id required"}
        base = (base_url or os.environ.get("OPENGATEWAY_URL") or "http://127.0.0.1:8765").rstrip("/")
        key = f"{room_id}:{participant_id}"

        existing: Optional[SeatSession]
        with self._lock:
            existing = self._sessions.get(key)
        if existing and existing.running():
            if not restart:
                return self._status(existing, note="already_listening")
            stopped = self.stop(room_id, participant_id)
            if stopped.get("still_running"):
                return {
                    "ok": False,
                    "radio": "stopping",
                    "room_id": room_id,
                    "participant_id": participant_id,
                    "name": name,
                    "note": "previous_poller_did_not_stop",
                    "presence": "joined",
                }

        owner = (lock_owner or "mcp").strip()
        if not try_acquire_seat_lock(
            room_id,
            participant_id,
            owner=owner,
        ):
            return {
                "ok": True,
                "radio": "external",
                "room_id": room_id,
                "participant_id": participant_id,
                "name": name,
                "note": "already_listening_other_process",
                "presence": "listening",
            }

        try:
            since = load_cursor(room_id, participant_id)
            sess = SeatSession(
                room_id=room_id,
                participant_id=participant_id,
                name=name or "agent",
                base_url=base,
                auth_token=auth_token,
                timeout=max(5.0, min(float(timeout), 120.0)),
                since=since,
                on_wake=on_wake,
                on_stop=on_stop,
                peer_names=list(peer_names or []),
                lock_owner=owner,
                managed_agent_id=managed_agent_id,
            )
            t = threading.Thread(
                target=self._loop,
                args=(sess,),
                name=f"og-seat-{participant_id[:8]}",
                daemon=True,
            )
            sess.thread = t
            with self._lock:
                self._sessions[key] = sess
            try:
                t.start()
            except Exception:
                with self._lock:
                    self._sessions.pop(key, None)
                raise
            if register:
                upsert_seat(
                    SeatRecord(
                        room_id=room_id,
                        participant_id=participant_id,
                        name=name,
                        harness=harness,
                        wake=wake,
                        base_url=base,
                        auth_token=auth_token,
                        updated_at=_utcnow_iso(),
                        managed_agent_id=managed_agent_id,
                        runner_id=runner_id,
                        desired_state="running",
                        runtime_status="listening",
                    )
                )
            return self._status(sess, note="seat_started")
        except Exception:
            release_seat_lock(
                room_id,
                participant_id,
                owner=owner,
            )
            raise

    def stop(self, room_id: str = "", participant_id: str = "") -> dict[str, Any]:
        stopped: list[str] = []
        sessions: list[SeatSession] = []
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
                    sessions.append(sess)
                    stopped.append(key)
        for sess in sessions:
            with sess.client_lock:
                active_client = sess.client
            if active_client is not None:
                try:
                    active_client.close()
                except Exception:
                    pass
            if sess.on_stop:
                try:
                    sess.on_stop()
                except Exception:
                    pass
            if (
                sess.thread
                and sess.thread is not threading.current_thread()
                and sess.thread.is_alive()
            ):
                sess.thread.join(timeout=10.0)
            if not sess.thread or not sess.thread.is_alive():
                release_seat_lock(
                    sess.room_id,
                    sess.participant_id,
                    owner=sess.lock_owner,
                )
        still_running = [
            sess.key
            for sess in sessions
            if sess.thread is not None and sess.thread.is_alive()
        ]
        return {
            "ok": not still_running,
            "stopped": stopped,
            "still_running": still_running,
        }

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._status(s) for s in self._sessions.values()]

    def drain_inbox(
        self, participant_id: str = "", room_id: str = "", max_items: int = 100
    ) -> dict[str, Any]:
        out: list[dict[str, Any]] = []
        with self._lock:
            sessions = list(self._sessions.values())
        for s in sessions:
            if participant_id and s.participant_id != participant_id:
                continue
            if room_id and s.room_id != room_id:
                continue
            while s.inbox and len(out) < max_items:
                item = s.inbox.popleft()
                out.append(self._normalize_inbox_item(item))
        return {
            "ok": True,
            "messages": out,
            "count": len(out),
            "hint": "Normalized deliveries; radio keeps listening in the background.",
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
            msgs.extend(self._normalize_inbox_item(x) for x in list(s.inbox)[-limit:])
        return {"ok": True, "messages": msgs[-limit:], "count": len(msgs[-limit:])}

    def _normalize_inbox_item(self, item: dict[str, Any]) -> dict[str, Any]:
        from opengateway.delivery import event_message_body, room_message_text

        m = item.get("message") if isinstance(item.get("message"), dict) else item
        meta = m.get("metadata") if isinstance(m, dict) else {}
        text = room_message_text(m) if isinstance(m, dict) else event_message_body(item)
        return {
            **item,
            "text": text,
            "delivery_id": (meta or {}).get("delivery_id"),
            "source_message_id": (meta or {}).get("source_message_id"),
            "wake_reason": item.get("wake_reason"),
        }

    def _status(self, sess: SeatSession, note: str = "") -> dict[str, Any]:
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
            "since": sess.since,
            "last_error": sess.last_error or None,
            "last_wake_reason": sess.last_wake_reason or None,
            "last_delivery_id": sess.last_delivery_id or None,
            "wakes_suppressed": sess.wakes_suppressed,
            "started_at": sess.started_at,
            "note": note or None,
            "presence": "listening" if sess.running() else "joined",
            "owner": sess.lock_owner,
            "managed_agent_id": sess.managed_agent_id or None,
        }

    def _loop(self, sess: SeatSession) -> None:
        wait_timeout = sess.timeout
        client_timeout = wait_timeout + 20.0
        headers = _auth_headers(sess.auth_token)
        since = sess.since
        time.sleep(0.15)
        client = httpx.Client(
            base_url=sess.base_url,
            timeout=client_timeout,
            headers=headers,
        )
        with sess.client_lock:
            sess.client = client
        try:
            try:
                if sess.stop_event.is_set():
                    return
                c = client
                if since is None:
                    since = seed_cursor_at_tail(c, sess.room_id, sess.participant_id)
                    sess.since = since
                    save_cursor(sess.room_id, sess.participant_id, since)
            except Exception as e:
                if not sess.stop_event.is_set():
                    sess.last_error = f"cursor seed: {e}"

            while not sess.stop_event.is_set():
                params: dict[str, Any] = {
                    "timeout": wait_timeout,
                    "limit": 50,
                    "for_participant": sess.participant_id,
                }
                if since:
                    params["since"] = since
                try:
                    r = client.get(
                        f"/v1/rooms/{sess.room_id}/messages/wait",
                        params=params,
                    )
                    r.raise_for_status()
                    data = r.json()
                    if sess.stop_event.is_set():
                        break
                    sess.polls += 1
                    sess.last_error = ""
                    msgs = data.get("messages") or []
                    since = data.get("next_since") or data.get("last_id") or since
                    wake_batch: list[dict[str, Any]] = []
                    for m in msgs:
                        if not isinstance(m, dict):
                            continue
                        since = m.get("id") or since
                        if m.get("from_participant_id") == sess.participant_id:
                            continue
                        wake, reason, did = should_wake_seat(
                            m,
                            seat_participant_id=sess.participant_id,
                            seat_name=sess.name,
                            peer_names=sess.peer_names,
                        )
                        item = {
                            "room_id": sess.room_id,
                            "participant_id": sess.participant_id,
                            "message": m,
                            "received_at": _utcnow_iso(),
                            "wake": wake,
                            "wake_reason": reason,
                            "delivery_id": did,
                        }
                        if wake and did and did in sess.seen_delivery_ids:
                            sess.wakes_suppressed += 1
                            item["wake"] = False
                            item["wake_reason"] = "dedupe_delivery"
                            wake = False
                        if wake:
                            if did:
                                sess.seen_delivery_ids.add(did)
                                sess.last_delivery_id = did
                            sess.last_wake_reason = reason
                            wake_batch.append(item)
                        sess.inbox.append(item)
                        sess.messages_buffered += 1
                    sess.since = since
                    save_cursor(sess.room_id, sess.participant_id, since)
                    if wake_batch and sess.on_wake:
                        try:
                            from opengateway.room_floor import (
                                chain_id_from_wake_batch,
                                wait_for_floor_grant,
                            )

                            cid = chain_id_from_wake_batch(wake_batch)
                            if cid and sess.client:
                                granted = wait_for_floor_grant(
                                    sess.client,
                                    room_id=sess.room_id,
                                    participant_id=sess.participant_id,
                                    chain_id=cid,
                                )
                                if not granted:
                                    sess.wakes_suppressed += 1
                                    sess.last_error = "speaking floor timeout"
                                    continue
                            sess.on_wake(wake_batch)
                        except Exception as e:
                            sess.last_error = f"on_wake: {e}"
                except Exception as e:
                    sess.last_error = str(e)
                    if sess.stop_event.wait(2.0):
                        break
                    continue
                if sess.stop_event.wait(0.05):
                    break
        finally:
            with sess.client_lock:
                sess.client = None
            try:
                client.close()
            except Exception:
                pass
            release_seat_lock(
                sess.room_id,
                sess.participant_id,
                owner=sess.lock_owner,
            )


seat_runtime = SeatRuntimeManager()

_im_threads: dict[str, threading.Thread] = {}
_im_lock = threading.Lock()


def auto_im_enabled() -> bool:
    if os.environ.get("OPENGATEWAY_IM_WAKE") == "1":
        return False
    v = (os.environ.get("OPENGATEWAY_AUTO_IM") or "on").lower().strip()
    return v not in {"off", "0", "false", "no"}


def resolve_wake_from_harness(harness: str) -> str:
    h = (harness or "hermes").lower().strip()
    aliases = {
        "claude-code": "claude",
        "claude_code": "claude",
        "cursor-agent": "cursor",
        "xai": "grok",
    }
    return aliases.get(h, h)


def make_im_wake_handler(
    *,
    room_id: str,
    participant_id: str,
    name: str,
    harness: str,
    base_url: str,
    auth_token: str = "",
) -> Callable[[list[dict[str, Any]]], None]:
    from opengateway.im import ImConfig, ImDaemon

    def on_wake(events: list[dict[str, Any]]) -> None:
        cfg = ImConfig(
            base_url=base_url,
            room=room_id,
            name=name,
            harness=harness,
            wake=resolve_wake_from_harness(harness),
            auth_token=auth_token,
            participant_id=participant_id,
            debounce_seconds=0.5,
        )
        daemon = ImDaemon(cfg)
        daemon.room_id = room_id
        daemon.participant_id = participant_id
        daemon._run_wake(events)

    return on_wake


def start_seat_supervisor(base_url: str = "", auth_token: str = "") -> None:
    """Restore registered seats when local serve starts (no duplicate MCP locks)."""
    from opengateway.seat_registry import load_registry

    if not auto_im_enabled():
        return
    base = (base_url or os.environ.get("OPENGATEWAY_URL") or "http://127.0.0.1:8765").rstrip("/")
    for rec in load_registry():
        # Managed seats are restored by the runner that owns their agent token.
        if rec.managed_agent_id:
            continue
        if not rec.room_id or not rec.participant_id:
            continue
        if seat_lock_active(rec.room_id, rec.participant_id):
            continue
        wake_handler = make_im_wake_handler(
            room_id=rec.room_id,
            participant_id=rec.participant_id,
            name=rec.name,
            harness=rec.harness,
            base_url=rec.base_url or base,
            auth_token=rec.auth_token or auth_token,
        )
        seat_runtime.start(
            room_id=rec.room_id,
            participant_id=rec.participant_id,
            name=rec.name,
            base_url=rec.base_url or base,
            auth_token=rec.auth_token or auth_token,
            restart=False,
            on_wake=wake_handler,
            register=False,
            harness=rec.harness,
            wake=rec.wake,
            lock_owner="supervisor",
        )
