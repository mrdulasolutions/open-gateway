"""Agent IM daemon — always-on radio + wake the agent runtime on every inbound.

Architecture gap this closes:

  Radio / listen  = mailbox + presence (listening badge)
  Agent turn      = open mailbox, reason, reply

IM requires both. Desktop MCP turns only open the mailbox when the human
messages that chat. This process owns the socket and fires a wake on each
room event so Live Ops feels like iMessage, not email with a green light.

Wake backends:
  hermes   — spawn ``hermes chat -q …`` (default when --wake hermes)
  webhook  — POST event JSON to a URL (e.g. Hermes gateway /webhooks/…)
  hook     — shell command with event JSON on stdin
  none     — presence + buffer only (same as listen without auto-reply)
  auto     — no LLM; simple rules (ping→pong) via hub REST as this participant

See docs/AGENTS_IM.md.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from opengateway.listen import (
    ListenConfig,
    ListenSinks,
    join_room,
    message_text,
    resolve_room_id,
)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _headers(token: str) -> dict[str, str]:
    t = (
        token
        or os.environ.get("OPENGATEWAY_AUTH_TOKEN")
        or os.environ.get("OPENGATEWAY_TOKEN")
        or ""
    ).strip()
    if not t:
        return {}
    return {"Authorization": f"Bearer {t}"}


def event_message_body(ev: dict[str, Any]) -> str:
    """Extract plain text from a listen/im event or raw hub message."""
    msg = ev.get("message") if isinstance(ev.get("message"), dict) else None
    if msg is None and isinstance(ev, dict):
        msg = ev
    if not isinstance(msg, dict):
        return ""
    # listen.message_text expects {"message": {parts…}}
    body = message_text({"message": msg})
    if body:
        return body
    parts = msg.get("parts") or []
    return " ".join(
        str(p.get("content") or "") for p in parts if isinstance(p, dict)
    ).strip()


def format_messages_for_prompt(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for i, ev in enumerate(messages, 1):
        msg = ev.get("message") if isinstance(ev.get("message"), dict) else ev
        who = (
            (msg or {}).get("from_name")
            or (msg or {}).get("from_participant_id")
            or "?"
        )
        body = event_message_body(ev)
        lines.append(f"{i}. [{who}]: {body or '(empty)'}")
    return "\n".join(lines) if lines else "(no text)"


def build_wake_prompt(
    *,
    agent_name: str,
    room_id: str,
    participant_id: str,
    messages: list[dict[str, Any]],
    base_url: str,
) -> str:
    body = format_messages_for_prompt(messages)
    return f"""You are {agent_name} on OpenGateway (multi-agent hub).
This is an IM wake — a human or peer posted in the room while you were listening.
Reply in the room. Do not start a wait_for_messages loop.

Hub: {base_url}
Room id: {room_id}
Your participant_id: {participant_id}

Inbound message(s):
{body}

Required actions:
1. Prefer OpenGateway MCP: drain_inbox then post_message(
     room_id="{room_id}", from_participant_id="{participant_id}", content="…"
   ). If MCP is unavailable, call the hub HTTP API with OPENGATEWAY_AUTH_TOKEN.
2. If anyone said ping (or "are you there"), reply with a short pong.
3. Keep the reply concise and in-character.
4. Do NOT call wait_for_messages in a loop. Finish after posting.
5. Do not rejoin as a different name/role.
"""


def looks_like_ping(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t or len(t) > 64:
        return False
    if t in {"ping", "ping?", "pong?", "hello?", "hi?", "anyone?", "you there?"}:
        return True
    # Short messages that are clearly a liveness check, not "ping-pong design"
    if re.fullmatch(r"ping[!?.]*", t):
        return True
    if re.fullmatch(r"(hey|hi|hello)[,!]?\s+ping[!?.]*", t):
        return True
    return False


@dataclass
class ImConfig:
    base_url: str
    room: str
    name: str = "Hermes COO"
    harness: str = "hermes"
    role: str = "contributor"
    auth_token: str = ""
    timeout: float = 45.0
    participant_id: str = ""
    # wake: hermes | claude | grok | webhook | hook | auto | none | harness
    wake: str = "hermes"
    wake_webhook: str = ""
    wake_hook: str = ""
    hermes_bin: str = "hermes"
    hermes_skills: str = "opengateway-collab"
    hermes_max_turns: int = 20
    hermes_extra_args: str = ""
    claude_bin: str = "claude"
    grok_bin: str = "grok"
    agent_max_turns: int = 20
    debounce_seconds: float = 1.5
    coalesce_max: int = 12
    auto_pong_text: str = "pong"
    # Optional sinks (same as listen)
    sinks: ListenSinks = field(default_factory=ListenSinks)
    # Skip wakes from these participant ids (always skip self)
    ignore_names: tuple[str, ...] = ()


@dataclass
class _WakeState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    pending: list[dict[str, Any]] = field(default_factory=list)
    timer: Optional[threading.Timer] = None
    busy: bool = False
    wakes: int = 0
    last_error: str = ""


class ImDaemon:
    """Long-poll + debounced agent wake."""

    def __init__(self, cfg: ImConfig) -> None:
        self.cfg = cfg
        self._stop = threading.Event()
        self._wake = _WakeState()
        self.room_id = ""
        self.participant_id = ""

    def stop(self) -> None:
        self._stop.set()
        with self._wake.lock:
            if self._wake.timer:
                self._wake.timer.cancel()
                self._wake.timer = None

    def run(self) -> int:
        cfg = self.cfg
        base = cfg.base_url.rstrip("/")
        headers = _headers(cfg.auth_token)
        backoff = 2.0

        while not self._stop.is_set():
            try:
                code = self._session_loop(base, headers)
                if code == 0 and self._stop.is_set():
                    return 0
                # unexpected clean exit — restart
                backoff = 2.0
            except KeyboardInterrupt:
                self.stop()
                cfg.sinks.emit(
                    {
                        "type": "status",
                        "detail": "stopped",
                        "room_id": self.room_id,
                        "participant_id": self.participant_id,
                        "ts": _utcnow_iso(),
                    }
                )
                return 0
            except Exception as e:
                cfg.sinks.emit(
                    {
                        "type": "error",
                        "detail": f"im session error: {e}; reconnect in {backoff:.0f}s",
                        "ts": _utcnow_iso(),
                    }
                )
                if self._stop.wait(backoff):
                    return 1
                backoff = min(backoff * 2, 30.0)
        return 0

    def _session_loop(self, base: str, headers: dict[str, str]) -> int:
        cfg = self.cfg
        with httpx.Client(base_url=base, timeout=15.0, headers=headers) as c:
            c.get("/ping").raise_for_status()
            room_id = resolve_room_id(c, cfg.room)
            participant = join_room(
                c,
                room_id,
                name=cfg.name,
                harness=cfg.harness,
                role=cfg.role,
                participant_id=cfg.participant_id or self.participant_id,
            )
        pid = str(participant.get("id") or "")
        self.room_id = room_id
        self.participant_id = pid
        # Persist for rejoin across reconnects
        if pid:
            self.cfg.participant_id = pid

        cfg.sinks.emit(
            {
                "type": "status",
                "detail": (
                    f"IM on as {cfg.name} ({pid[:8]}…) room {room_id[:8]}… "
                    f"wake={cfg.wake} debounce={cfg.debounce_seconds}s · {base}"
                ),
                "room_id": room_id,
                "participant_id": pid,
                "wake": cfg.wake,
                "ts": _utcnow_iso(),
            }
        )

        wait_timeout = max(1.0, min(float(cfg.timeout), 120.0))
        client_timeout = wait_timeout + 15.0
        since: Optional[str] = None

        while not self._stop.is_set():
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
                if r.status_code >= 500:
                    raise RuntimeError(f"hub {r.status_code}: {r.text[:200]}")
                r.raise_for_status()
                data = r.json()

            msgs = data.get("messages") or []
            since = data.get("next_since") or data.get("last_id") or since
            if msgs:
                for m in msgs:
                    if not isinstance(m, dict):
                        continue
                    since = m.get("id") or since
                    if m.get("from_participant_id") == pid:
                        continue
                    from_name = (m.get("from_name") or "").strip()
                    if from_name and from_name in cfg.ignore_names:
                        continue
                    event = {
                        "type": "message",
                        "room_id": room_id,
                        "participant_id": pid,
                        "message": m,
                        "ts": _utcnow_iso(),
                    }
                    cfg.sinks.emit(event)
                    self._enqueue_wake(event)
            elif data.get("timed_out"):
                cfg.sinks.emit(
                    {
                        "type": "heartbeat",
                        "room_id": room_id,
                        "participant_id": pid,
                        "ts": _utcnow_iso(),
                        "detail": "listening",
                    }
                )
            time.sleep(0.05)
        return 0

    def _enqueue_wake(self, event: dict[str, Any]) -> None:
        if self.cfg.wake in ("", "none", "off"):
            return
        with self._wake.lock:
            self._wake.pending.append(event)
            if len(self._wake.pending) >= max(1, self.cfg.coalesce_max):
                if self._wake.timer:
                    self._wake.timer.cancel()
                    self._wake.timer = None
                batch = list(self._wake.pending)
                self._wake.pending.clear()
                threading.Thread(
                    target=self._run_wake,
                    args=(batch,),
                    name="og-im-wake",
                    daemon=True,
                ).start()
                return
            if self._wake.timer:
                self._wake.timer.cancel()
            delay = max(0.05, float(self.cfg.debounce_seconds))
            t = threading.Timer(delay, self._flush_wake)
            t.daemon = True
            self._wake.timer = t
            t.start()

    def _flush_wake(self) -> None:
        with self._wake.lock:
            self._wake.timer = None
            batch = list(self._wake.pending)
            self._wake.pending.clear()
        if batch:
            self._run_wake(batch)

    def _run_wake(self, events: list[dict[str, Any]]) -> None:
        with self._wake.lock:
            if self._wake.busy:
                # Re-queue if a wake is already running
                self._wake.pending.extend(events)
                if not self._wake.timer:
                    t = threading.Timer(0.5, self._flush_wake)
                    t.daemon = True
                    self._wake.timer = t
                    t.start()
                return
            self._wake.busy = True
        try:
            self._dispatch_wake(events)
            with self._wake.lock:
                self._wake.wakes += 1
                self._wake.last_error = ""
        except Exception as e:
            with self._wake.lock:
                self._wake.last_error = str(e)
            self.cfg.sinks.emit(
                {
                    "type": "error",
                    "detail": f"wake failed: {e}",
                    "ts": _utcnow_iso(),
                }
            )
        finally:
            with self._wake.lock:
                self._wake.busy = False
                # Drain anything queued during wake
                if self._wake.pending and not self._wake.timer:
                    t = threading.Timer(0.2, self._flush_wake)
                    t.daemon = True
                    self._wake.timer = t
                    t.start()

    def _resolve_wake_mode(self) -> str:
        """Map --wake / harness tags onto a concrete backend name."""
        mode = (self.cfg.wake or "none").lower().strip()
        if mode in ("harness", "auto-harness", "default"):
            mode = (self.cfg.harness or "hermes").lower().strip()
        # Normalize harness tags → wake backend
        aliases = {
            "claude-code": "claude",
            "claude_code": "claude",
            "anthropic": "claude",
            "cursor-agent": "cursor",
            "cursor_cli": "cursor",
            "xai": "grok",
            "x-ai": "grok",
        }
        return aliases.get(mode, mode)

    def _dispatch_wake(self, events: list[dict[str, Any]]) -> None:
        cfg = self.cfg
        mode = self._resolve_wake_mode()
        payload = {
            "type": "im_wake",
            "room_id": self.room_id,
            "participant_id": self.participant_id,
            "agent_name": cfg.name,
            "harness": cfg.harness,
            "base_url": cfg.base_url.rstrip("/"),
            "messages": events,
            "count": len(events),
            "ts": _utcnow_iso(),
        }
        prompt = build_wake_prompt(
            agent_name=cfg.name,
            room_id=self.room_id,
            participant_id=self.participant_id,
            messages=events,
            base_url=cfg.base_url.rstrip("/"),
        )
        payload["prompt"] = prompt

        cfg.sinks.emit(
            {
                "type": "status",
                "detail": f"wake/{mode} ×{len(events)} message(s)",
                "room_id": self.room_id,
                "ts": _utcnow_iso(),
            }
        )

        if mode == "auto":
            self._wake_auto(events)
            return
        if mode == "webhook":
            url = (cfg.wake_webhook or "").strip()
            if not url:
                raise RuntimeError("--wake webhook requires --wake-webhook URL")
            r = httpx.post(url, json=payload, timeout=60.0)
            r.raise_for_status()
            return
        if mode == "hook":
            cmd = (cfg.wake_hook or "").strip()
            if not cmd:
                raise RuntimeError("--wake hook requires --wake-hook 'shell…'")
            subprocess.run(
                cmd,
                shell=True,
                input=json.dumps(payload, default=str).encode("utf-8"),
                check=False,
                timeout=600,
                env={
                    **os.environ,
                    "OPENGATEWAY_IM_WAKE": "1",
                    "OPENGATEWAY_IM_ROOM": self.room_id,
                    "OPENGATEWAY_IM_PARTICIPANT": self.participant_id,
                    "OPENGATEWAY_IM_PROMPT": prompt,
                },
            )
            return
        if mode == "hermes":
            self._wake_hermes(prompt)
            return
        if mode == "claude":
            self._wake_claude(prompt)
            return
        if mode == "grok":
            self._wake_grok(prompt)
            return
        if mode == "cursor":
            raise RuntimeError(
                "wake=cursor has no stable one-shot CLI on all machines. "
                "Use: opengateway im … --wake hook --wake-hook "
                "'./scripts/im-wake-cursor.sh' (or webhook into your runner). "
                "See skills/opengateway-collab/harnesses/cursor.md"
            )
        raise RuntimeError(
            f"unknown wake mode: {mode}. "
            "Use hermes|claude|grok|auto|webhook|hook|none (or harness)."
        )

    def _agent_env(self) -> dict[str, str]:
        cfg = self.cfg
        env = {
            **os.environ,
            "OPENGATEWAY_URL": cfg.base_url.rstrip("/"),
            "OPENGATEWAY_IM_WAKE": "1",
            "OPENGATEWAY_AGENT_NAME": cfg.name,
            "OPENGATEWAY_HARNESS": cfg.harness,
            "OPENGATEWAY_IM_ROOM": self.room_id,
            "OPENGATEWAY_IM_PARTICIPANT": self.participant_id,
        }
        if cfg.auth_token:
            env["OPENGATEWAY_AUTH_TOKEN"] = cfg.auth_token
        return env

    def _resolve_bin(self, preferred: str, fallbacks: list[str]) -> str:
        candidates = [preferred, *fallbacks]
        for name in candidates:
            n = (name or "").strip()
            if not n:
                continue
            if Path(n).is_file() and os.access(n, os.X_OK):
                return n
            found = shutil.which(n)
            if found:
                return found
            home = Path.home() / ".local" / "bin" / Path(n).name
            if home.is_file() and os.access(home, os.X_OK):
                return str(home)
        raise RuntimeError(
            f"agent binary not found (tried {candidates}). "
            "Install the harness CLI or use --wake auto|webhook|hook"
        )

    def _wake_auto(self, events: list[dict[str, Any]]) -> None:
        """No-LLM path: ping→pong via hub REST as this participant."""
        cfg = self.cfg
        base = cfg.base_url.rstrip("/")
        headers = {
            **_headers(cfg.auth_token),
            "Content-Type": "application/json",
        }
        for ev in events:
            msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
            text = event_message_body(ev)
            if not looks_like_ping(text):
                continue
            body = {
                "from_participant_id": self.participant_id,
                "parts": [
                    {
                        "content_type": "text/plain",
                        "content": cfg.auto_pong_text or "pong",
                    }
                ],
            }
            with httpx.Client(base_url=base, timeout=20.0, headers=headers) as c:
                r = c.post(f"/v1/rooms/{self.room_id}/messages", json=body)
                r.raise_for_status()
            cfg.sinks.emit(
                {
                    "type": "status",
                    "detail": f"auto-pong to {(msg.get('from_name') or '?')}",
                    "ts": _utcnow_iso(),
                }
            )

    def _wake_hermes(self, prompt: str) -> None:
        cfg = self.cfg
        bin_name = self._resolve_bin(cfg.hermes_bin or "hermes", ["hermes"])
        max_turns = max(3, int(cfg.hermes_max_turns or cfg.agent_max_turns or 20))
        cmd = [
            bin_name,
            "chat",
            "-Q",
            "-q",
            prompt,
            "--max-turns",
            str(max_turns),
            "--accept-hooks",
        ]
        skills = (cfg.hermes_skills or "").strip()
        if skills:
            cmd.extend(["-s", skills])
        extra = (cfg.hermes_extra_args or "").strip()
        if extra:
            cmd.extend(extra.split())
        self._run_agent_cmd(cmd, label="hermes", max_turns=max_turns)

    def _wake_claude(self, prompt: str) -> None:
        """Claude Code one-shot print mode (MCP must be configured for OG tools)."""
        cfg = self.cfg
        bin_name = self._resolve_bin(cfg.claude_bin or "claude", ["claude"])
        max_turns = max(3, int(cfg.agent_max_turns or 20))
        # -p/--print: non-interactive turn; permission-mode for unattended IM seats
        cmd = [
            bin_name,
            "-p",
            prompt,
            "--permission-mode",
            "bypassPermissions",
        ]
        self._run_agent_cmd(cmd, label="claude", max_turns=max_turns)

    def _wake_grok(self, prompt: str) -> None:
        """Grok Build single-turn (-p) with auto-approve for tool calls."""
        cfg = self.cfg
        bin_name = self._resolve_bin(cfg.grok_bin or "grok", ["grok"])
        max_turns = max(3, int(cfg.agent_max_turns or 20))
        cmd = [
            bin_name,
            "-p",
            prompt,
            "--always-approve",
        ]
        self._run_agent_cmd(cmd, label="grok", max_turns=max_turns)

    def _run_agent_cmd(
        self, cmd: list[str], *, label: str, max_turns: int
    ) -> None:
        env = self._agent_env()
        proc = subprocess.run(
            cmd,
            env=env,
            timeout=max(60, int(max_turns) * 45),
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "")[-800:]
            raise RuntimeError(f"{label} exit {proc.returncode}: {err}")
        if proc.stdout:
            print(proc.stdout[-500:], file=sys.stderr, flush=True)


def run_im(cfg: ImConfig, *, should_stop: Optional[Callable[[], bool]] = None) -> int:
    """Entry used by CLI. should_stop polled lightly via stop event on KeyboardInterrupt only."""
    daemon = ImDaemon(cfg)
    if should_stop:
        def _watch() -> None:
            while not daemon._stop.is_set():
                if should_stop():
                    daemon.stop()
                    return
                time.sleep(0.5)

        threading.Thread(target=_watch, name="og-im-stopwatch", daemon=True).start()
    return daemon.run()
