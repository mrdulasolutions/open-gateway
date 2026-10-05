"""Strict, local-only adapters for supported agent harnesses.

Managed-agent jobs select only a harness name.  Executable paths, environment
overrides, shell fragments, and extra arguments are intentionally not part of
this interface.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
import uuid
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping


class AdapterError(RuntimeError):
    """Base class for safe, user-facing adapter failures."""


class AdapterUnavailable(AdapterError):
    """The selected harness is missing or is not authenticated."""


@dataclass(frozen=True)
class ProbeResult:
    harness: str
    status: str
    available: bool
    authenticated: bool | None
    detail: str
    setup_instructions: str
    executable: str = ""

    @property
    def ready(self) -> bool:
        return self.available and self.authenticated is not False


@dataclass(frozen=True)
class InvocationContext:
    harness: str
    agent_name: str
    room_id: str
    participant_id: str
    hub_url: str
    prompt: str = field(repr=False)
    session_id: str = ""
    resume_session: bool = False


@dataclass(frozen=True)
class InvocationResult:
    harness: str
    returncode: int
    duration_seconds: float
    cancelled: bool = False
    timed_out: bool = False
    output: str = field(default="", repr=False)

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.cancelled and not self.timed_out


LogEmitter = Callable[[str], None]

_HOOK_NOISE_MARKERS = (
    "sessionend hook",
    "sessionstart hook",
    "pretooluse hook",
    "posttooluse hook",
    "subagentstop hook",
    "hook from plugin",
    "hook error",
)


def is_harness_noise(line: str) -> bool:
    """True for vendor hook/plugin diagnostics that must not become a room reply."""
    lower = line.lower()
    return any(marker in lower for marker in _HOOK_NOISE_MARKERS)


def valid_harness_session_id(value: str) -> str:
    """Accept only a UUID so session flags cannot become extra argv."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return str(uuid.UUID(raw))
    except (ValueError, TypeError, AttributeError):
        return ""


def sanitize_harness_output(text: str) -> str:
    """Keep the model reply; drop Claude/Grok/Hermes hook failure banners."""
    kept = [
        line
        for line in str(text or "").splitlines()
        if line.strip() and not is_harness_noise(line)
    ]
    return "\n".join(kept).strip()


class HarnessAdapter(ABC):
    """Fixed-command adapter used by managed runner seats."""

    harness: str
    executable_name: str
    invocation_timeout: float = 900.0

    @abstractmethod
    def setup_instructions(self) -> str:
        """Return terminal-only setup guidance; never request credentials."""

    @abstractmethod
    def _invocation_argv(self, executable: str, context: InvocationContext) -> list[str]:
        """Return a fixed argv list for one non-interactive turn."""

    def _auth_probe_argv(self, executable: str) -> list[str] | None:
        return None

    def _resolve_executable(self) -> str:
        # Deliberately accepts only the built-in command name.
        return shutil.which(self.executable_name) or ""

    def probe(self) -> ProbeResult:
        executable = self._resolve_executable()
        setup = self.setup_instructions()
        if not executable:
            return ProbeResult(
                harness=self.harness,
                status="missing",
                available=False,
                authenticated=None,
                detail=f"{self.harness} CLI is not installed or not on PATH",
                setup_instructions=setup,
            )

        auth_argv = self._auth_probe_argv(executable)
        if not auth_argv:
            return ProbeResult(
                harness=self.harness,
                status="ready",
                available=True,
                authenticated=None,
                detail=f"{self.harness} CLI found; authentication is checked on invoke",
                setup_instructions=setup,
                executable=self.executable_name,
            )

        try:
            completed = subprocess.run(
                auth_argv,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=10.0,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return ProbeResult(
                harness=self.harness,
                status="probe_error",
                available=True,
                authenticated=None,
                detail=f"{self.harness} CLI was found but its auth status could not be checked",
                setup_instructions=setup,
                executable=self.executable_name,
            )

        if completed.returncode == 0:
            return ProbeResult(
                harness=self.harness,
                status="ready",
                available=True,
                authenticated=True,
                detail=f"{self.harness} CLI is installed and authenticated",
                setup_instructions=setup,
                executable=self.executable_name,
            )

        # Never return vendor output: it can contain account data or credentials.
        output = (completed.stdout or "").lower()
        auth_markers = (
            "not authenticated",
            "not logged",
            "log in",
            "login",
            "credential",
            "api key",
            "configure",
            "setup",
            "authentication",
        )
        if any(marker in output for marker in auth_markers):
            return ProbeResult(
                harness=self.harness,
                status="not_authenticated",
                available=True,
                authenticated=False,
                detail=f"{self.harness} CLI is installed but not authenticated",
                setup_instructions=setup,
                executable=self.executable_name,
            )
        return ProbeResult(
            harness=self.harness,
            status="probe_error",
            available=True,
            authenticated=None,
            detail=f"{self.harness} CLI is installed; auth status was inconclusive",
            setup_instructions=setup,
            executable=self.executable_name,
        )

    def invoke(
        self,
        context: InvocationContext,
        *,
        cancel_event: threading.Event | None = None,
        emit_log: LogEmitter | None = None,
    ) -> InvocationResult:
        executable = self._resolve_executable()
        if not executable:
            raise AdapterUnavailable(
                f"{self.harness} CLI is missing. {self.setup_instructions()}"
            )

        argv = self._invocation_argv(executable, context)
        env = self._fixed_environment(context)
        started = time.monotonic()
        kwargs: dict[str, object] = {
            "args": argv,
            "shell": False,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "env": env,
            "bufsize": 1,
        }
        if os.name != "nt":
            kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(**kwargs)  # type: ignore[arg-type]
        except OSError as exc:
            raise AdapterUnavailable(
                f"Could not start {self.harness}. {self.setup_instructions()}"
            ) from exc

        # Keep draining stdout so the child cannot block, but bound retained
        # reply text even if a vendor CLI becomes unexpectedly noisy.
        output_lines: deque[str] = deque(maxlen=512)

        def read_output() -> None:
            stream = proc.stdout
            if stream is None:
                return
            try:
                while True:
                    line = stream.readline(4096)
                    if not line:
                        break
                    clean = line.rstrip("\r\n")
                    if is_harness_noise(clean):
                        continue
                    output_lines.append(clean)
                    if emit_log:
                        emit_log(clean)
            except (OSError, ValueError):
                return

        reader = threading.Thread(
            target=read_output,
            name=f"og-{self.harness}-output",
            daemon=True,
        )
        reader.start()
        cancelled = False
        timed_out = False
        deadline = started + max(30.0, float(self.invocation_timeout))
        while proc.poll() is None:
            if cancel_event is not None and cancel_event.wait(0.1):
                cancelled = True
                self._terminate(proc)
                break
            if cancel_event is None:
                time.sleep(0.1)
            if time.monotonic() >= deadline:
                timed_out = True
                self._terminate(proc)
                break

        try:
            returncode = proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            self._kill(proc)
            returncode = proc.wait(timeout=3.0)
        reader.join(timeout=1.0)
        duration = max(0.0, time.monotonic() - started)
        result = InvocationResult(
            harness=self.harness,
            returncode=int(returncode),
            duration_seconds=duration,
            cancelled=cancelled,
            timed_out=timed_out,
            output=sanitize_harness_output("\n".join(output_lines)),
        )
        if not result.ok and not cancelled:
            reason = "timed out" if timed_out else f"exited with code {returncode}"
            raise AdapterError(
                f"{self.harness} {reason}. {self.setup_instructions()}"
            )
        return result

    def _fixed_environment(self, context: InvocationContext) -> Mapping[str, str]:
        env = dict(os.environ)
        # Never leak the hub master/runner identity inherited by a serve process.
        env.pop("OPENGATEWAY_TOKEN", None)
        env.pop("OPENGATEWAY_RUNNER_TOKEN", None)
        env.update(
            {
                "OPENGATEWAY_URL": context.hub_url.rstrip("/"),
                "OPENGATEWAY_IM_WAKE": "1",
                "OPENGATEWAY_AGENT_NAME": context.agent_name,
                "OPENGATEWAY_HARNESS": self.harness,
                "OPENGATEWAY_IM_ROOM": context.room_id,
                "OPENGATEWAY_IM_PARTICIPANT": context.participant_id,
            }
        )
        # The runner owns room delivery. Vendor subprocesses only produce text;
        # they never receive a hub, runner, or managed-agent bearer token.
        env.pop("OPENGATEWAY_AUTH_TOKEN", None)
        env.pop("OPENGATEWAY_TOKEN", None)
        env["OPENGATEWAY_MANAGED_RUNNER"] = "1"
        return env

    @staticmethod
    def _terminate(proc: subprocess.Popen[str]) -> None:
        try:
            if os.name != "nt":
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
        except (OSError, ProcessLookupError):
            pass

    @staticmethod
    def _kill(proc: subprocess.Popen[str]) -> None:
        try:
            if os.name != "nt":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except (OSError, ProcessLookupError):
            pass


class ClaudeCodeAdapter(HarnessAdapter):
    harness = "claude-code"
    executable_name = "claude"

    def setup_instructions(self) -> str:
        return (
            "Install Claude Code, then run `claude auth login` in your terminal "
            "and verify with `claude auth status`."
        )

    def _auth_probe_argv(self, executable: str) -> list[str]:
        return [executable, "auth", "status"]

    def _invocation_argv(self, executable: str, context: InvocationContext) -> list[str]:
        argv = [
            executable,
            "-p",
            context.prompt,
            "--permission-mode",
            "bypassPermissions",
        ]
        session_id = valid_harness_session_id(context.session_id)
        if session_id and context.resume_session:
            argv.extend(["--resume", session_id])
        elif session_id:
            argv.extend(["--session-id", session_id])
        return argv


class GrokAdapter(HarnessAdapter):
    harness = "grok"
    executable_name = "grok"

    def setup_instructions(self) -> str:
        return (
            "Install Grok CLI, then run `grok login` in your terminal. "
            "OpenGateway never collects vendor credentials."
        )

    def probe(self) -> ProbeResult:
        executable = self._resolve_executable()
        setup = self.setup_instructions()
        if not executable:
            return ProbeResult(
                harness=self.harness,
                status="missing",
                available=False,
                authenticated=None,
                detail="grok CLI is not installed or not on PATH",
                setup_instructions=setup,
            )
        grok_home = Path(
            os.environ.get("GROK_HOME") or (Path.home() / ".grok")
        ).expanduser()
        cached_login = (grok_home / "auth.json").is_file()
        env_login = bool((os.environ.get("XAI_API_KEY") or "").strip())
        delegated_login = any(
            (os.environ.get(name) or "").strip()
            for name in ("GROK_AUTH_PROVIDER_COMMAND", "GROK_OIDC_ISSUER")
        )
        if cached_login or env_login:
            return ProbeResult(
                harness=self.harness,
                status="ready",
                available=True,
                authenticated=True,
                detail="grok CLI is installed and has a configured login",
                setup_instructions=setup,
                executable=self.executable_name,
            )
        if delegated_login:
            return ProbeResult(
                harness=self.harness,
                status="ready",
                available=True,
                authenticated=None,
                detail="grok CLI is installed with delegated authentication",
                setup_instructions=setup,
                executable=self.executable_name,
            )
        return ProbeResult(
            harness=self.harness,
            status="not_authenticated",
            available=True,
            authenticated=False,
            detail="grok CLI is installed but no login is configured",
            setup_instructions=setup,
            executable=self.executable_name,
        )

    def _invocation_argv(self, executable: str, context: InvocationContext) -> list[str]:
        argv = [executable, "-p", context.prompt, "--always-approve"]
        session_id = valid_harness_session_id(context.session_id)
        if session_id and context.resume_session:
            argv.extend(["--resume", session_id])
        elif session_id:
            argv.extend(["--session-id", session_id])
        return argv


class HermesAdapter(HarnessAdapter):
    harness = "hermes"
    executable_name = "hermes"

    def setup_instructions(self) -> str:
        return (
            "Install Hermes Agent, then run `hermes setup` in your terminal. "
            "Keep provider credentials in Hermes; do not paste them into OpenGateway."
        )

    def _invocation_argv(self, executable: str, context: InvocationContext) -> list[str]:
        return [
            executable,
            "chat",
            "-Q",
            "-q",
            context.prompt,
            "--max-turns",
            "20",
            "--accept-hooks",
        ]


_ADAPTERS: dict[str, type[HarnessAdapter]] = {
    "claude": ClaudeCodeAdapter,
    "claude-code": ClaudeCodeAdapter,
    "claude_code": ClaudeCodeAdapter,
    "anthropic": ClaudeCodeAdapter,
    "grok": GrokAdapter,
    "xai": GrokAdapter,
    "x-ai": GrokAdapter,
    "hermes": HermesAdapter,
}


def supported_harnesses() -> tuple[str, ...]:
    return ("claude-code", "grok", "hermes")


def get_adapter(harness: str) -> HarnessAdapter:
    key = (harness or "").strip().lower()
    adapter_type = _ADAPTERS.get(key)
    if adapter_type is None:
        raise AdapterUnavailable(
            f"Unsupported managed harness {harness!r}. "
            f"Choose one of: {', '.join(supported_harnesses())}."
        )
    return adapter_type()


def probe_all() -> list[ProbeResult]:
    return [get_adapter(name).probe() for name in supported_harnesses()]
