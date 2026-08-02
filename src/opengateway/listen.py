"""OpenGateway listen daemon — keep radio on via long-poll + optional sinks.

Sinks:
  - stdout (default, human-readable)
  - JSONL file drop (--file)
  - webhook POST (--webhook)
  - shell hook (--hook) with message JSON on stdin

Use when MCP agents cannot stay in a wait loop (coding turns, cold harness).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import httpx


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def message_text(msg: dict[str, Any]) -> str:
    parts = (msg.get("message") or {}).get("parts") or []
    chunks: list[str] = []
    for p in parts:
        if isinstance(p, dict) and p.get("content"):
            chunks.append(str(p["content"]))
    return "\n".join(chunks) if chunks else ""


@dataclass
class ListenSinks:
    print_console: bool = True
    file_path: Optional[Path] = None
    webhook_url: Optional[str] = None
    hook_cmd: Optional[str] = None
    json_stdout: bool = False

    def emit(self, event: dict[str, Any]) -> None:
        line = json.dumps(event, default=str)
        if self.json_stdout:
            print(line, flush=True)
        elif self.print_console and event.get("type") == "message":
            msg = event.get("message") or {}
            who = msg.get("from_name") or "?"
            body = message_text(msg)
            room = (event.get("room_id") or "")[:8]
            print(f"[{room}…] {who}: {body}", flush=True)
        elif self.print_console and event.get("type") == "status":
            print(f"[listen] {event.get('detail')}", flush=True)

        if self.file_path:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            with self.file_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

        if self.webhook_url:
            try:
                httpx.post(
                    self.webhook_url,
                    json=event,
                    timeout=15.0,
                    headers={"Content-Type": "application/json"},
                )
            except Exception as e:
                print(f"[listen] webhook error: {e}", file=sys.stderr, flush=True)

        if self.hook_cmd:
            try:
                subprocess.run(
                    self.hook_cmd,
                    shell=True,
                    input=line.encode("utf-8"),
                    check=False,
                    timeout=60,
                    env={
                        **os.environ,
                        "OPENGATEWAY_LISTEN_EVENT": event.get("type") or "",
                        "OPENGATEWAY_LISTEN_ROOM": str(event.get("room_id") or ""),
                        "OPENGATEWAY_LISTEN_FROM": str(
                            (event.get("message") or {}).get("from_name") or ""
                        ),
                    },
                )
            except Exception as e:
                print(f"[listen] hook error: {e}", file=sys.stderr, flush=True)


@dataclass
class ListenConfig:
    base_url: str
    room: str  # id or name
    name: str = "listen-daemon"
    harness: str = "mcp"
    role: str = "observer"
    auth_token: str = ""
    timeout: float = 45.0
    participant_id: str = ""
    sinks: ListenSinks = field(default_factory=ListenSinks)
    max_events: Optional[int] = None  # None = forever


def _headers(token: str) -> dict[str, str]:
    t = (token or os.environ.get("OPENGATEWAY_AUTH_TOKEN") or "").strip()
    if not t:
        return {}
    return {"Authorization": f"Bearer {t}"}


def resolve_room_id(client: httpx.Client, room: str) -> str:
    rooms = (client.get("/v1/rooms").json() or {}).get("rooms") or []
    if any(r.get("id") == room for r in rooms):
        return room
    match = next(
        (
            r
            for r in rooms
            if r.get("name") == room or str(r.get("id", "")).startswith(room)
        ),
        None,
    )
    if not match:
        raise RuntimeError(f"Room not found: {room}")
    return str(match["id"])


def join_room(
    client: httpx.Client,
    room_id: str,
    *,
    name: str,
    harness: str,
    role: str,
    participant_id: str = "",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name,
        "harness": harness,
        "role": role,
        "capabilities": ["listen", "radio"],
    }
    if participant_id:
        body["participant_id"] = participant_id
    r = client.post(f"/v1/rooms/{room_id}/join", json=body)
    r.raise_for_status()
    return r.json()


def run_listen_loop(
    cfg: ListenConfig,
    *,
    should_stop: Optional[Callable[[], bool]] = None,
) -> int:
    """Block forever (or until max_events / should_stop). Returns exit code."""
    base = cfg.base_url.rstrip("/")
    headers = _headers(cfg.auth_token)
    since: Optional[str] = None
    events = 0

    with httpx.Client(base_url=base, timeout=15.0, headers=headers) as c:
        try:
            c.get("/ping").raise_for_status()
        except Exception as e:
            cfg.sinks.emit(
                {
                    "type": "error",
                    "detail": f"Cannot reach {base}: {e}",
                    "ts": _utcnow_iso(),
                }
            )
            return 1
        try:
            room_id = resolve_room_id(c, cfg.room)
        except Exception as e:
            cfg.sinks.emit({"type": "error", "detail": str(e), "ts": _utcnow_iso()})
            return 1
        try:
            participant = join_room(
                c,
                room_id,
                name=cfg.name,
                harness=cfg.harness,
                role=cfg.role,
                participant_id=cfg.participant_id,
            )
        except Exception as e:
            cfg.sinks.emit(
                {
                    "type": "error",
                    "detail": f"join failed: {e}",
                    "ts": _utcnow_iso(),
                }
            )
            return 1

    pid = participant.get("id") or ""
    cfg.sinks.emit(
        {
            "type": "status",
            "detail": f"joined as {cfg.name} ({pid[:8]}…) room {room_id[:8]}… "
            f"presence=listening while long-polling {base}",
            "room_id": room_id,
            "participant_id": pid,
            "ts": _utcnow_iso(),
        }
    )

    wait_timeout = max(1.0, min(float(cfg.timeout), 120.0))
    client_timeout = wait_timeout + 15.0

    try:
        while True:
            if should_stop and should_stop():
                break
            params: dict[str, Any] = {
                "timeout": wait_timeout,
                "limit": 50,
                "for_participant": pid,
            }
            if since:
                params["since"] = since
            with httpx.Client(
                base_url=base, timeout=client_timeout, headers=headers
            ) as c:
                r = c.get(f"/v1/rooms/{room_id}/messages/wait", params=params)
                r.raise_for_status()
                data = r.json()
            msgs = data.get("messages") or []
            since = data.get("next_since") or data.get("last_id") or since
            if msgs:
                for m in msgs:
                    since = m.get("id") or since
                    cfg.sinks.emit(
                        {
                            "type": "message",
                            "room_id": room_id,
                            "participant_id": pid,
                            "message": m,
                            "ts": _utcnow_iso(),
                        }
                    )
                    events += 1
                    if cfg.max_events is not None and events >= cfg.max_events:
                        return 0
            elif data.get("timed_out"):
                cfg.sinks.emit(
                    {
                        "type": "heartbeat",
                        "room_id": room_id,
                        "participant_id": pid,
                        "ts": _utcnow_iso(),
                        "detail": "timed_out — still listening",
                    }
                )
            time.sleep(0.05)
    except KeyboardInterrupt:
        cfg.sinks.emit(
            {
                "type": "status",
                "detail": "stopped",
                "room_id": room_id,
                "participant_id": pid,
                "ts": _utcnow_iso(),
            }
        )
        return 0
    except Exception as e:
        cfg.sinks.emit(
            {
                "type": "error",
                "detail": str(e),
                "room_id": room_id,
                "participant_id": pid,
                "ts": _utcnow_iso(),
            }
        )
        return 1
    return 0


def grok_hook_snippet(inbox_file: Path) -> str:
    """Suggested shell hook that appends for Grok / agents to poll a file."""
    return f"""# Example --hook: notify + drop for harness
# opengateway listen ROOM --file {inbox_file} --hook 'echo \"OpenGateway inbox updated\" >&2'
# Then in agent session: read {inbox_file} tail after each coding turn.
"""
