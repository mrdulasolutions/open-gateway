"""Managed-agent runner for one-click Add Agent.

The hub queues typed jobs; this process performs only fixed, local harness
operations.  No executable path, argv, shell command, or environment override
is accepted from a job payload.
"""

from __future__ import annotations

import json
import os
import platform
import re
import socket
import tempfile
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlparse

import httpx

from opengateway.harness_adapters import (
    AdapterError,
    AdapterUnavailable,
    HarnessAdapter,
    InvocationContext,
    ProbeResult,
    get_adapter,
    probe_all,
    valid_harness_session_id,
)
from opengateway.seat_registry import (
    SeatRecord,
    clear_runner_credentials,
    credential_store_path,
    get_managed_seat,
    get_managed_seats,
    load_agent_credential,
    load_registry,
    load_runner_token,
    lock_path,
    release_seat_lock,
    remove_agent_credential,
    remove_seat,
    runner_directory,
    save_agent_credential,
    save_runner_token,
    seat_lock_active,
    try_acquire_seat_lock,
    upsert_seat,
)
from opengateway.seat_runtime import SeatRuntimeManager, clear_cursor, seat_runtime


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


try:
    PACKAGE_VERSION = version("opengateways")
except PackageNotFoundError:
    PACKAGE_VERSION = "0.0.dev"


RUNNER_ID_LOCAL = "local"
RUNNER_CONFIG_VERSION = 1
_RUNNER_THREAD: threading.Thread | None = None
_RUNNER_SERVICE: "RunnerService | None" = None
_RUNNER_THREAD_LOCK = threading.Lock()


class RunnerError(RuntimeError):
    pass


class RunnerNotConnected(RunnerError):
    pass


class SetupRequired(RunnerError):
    def __init__(self, probe: ProbeResult) -> None:
        self.probe = probe
        super().__init__(f"{probe.detail}. {probe.setup_instructions}")


@dataclass(frozen=True)
class RunnerConfig:
    runner_id: str
    hub_url: str
    name: str
    connected_at: str = field(default_factory=_utcnow_iso)
    embedded: bool = False
    version: int = RUNNER_CONFIG_VERSION

    def public_dict(self) -> dict[str, Any]:
        return {
            "connected": True,
            "runner_id": self.runner_id,
            "hub_url": self.hub_url,
            "name": self.name,
            "connected_at": self.connected_at,
            "embedded": self.embedded,
        }


@dataclass(frozen=True)
class RedeemResult:
    runner_id: str
    runner_token: str = field(repr=False, compare=False)
    hub_url: str = ""
    name: str = ""


@dataclass(frozen=True)
class ManagedAgentSpec:
    managed_agent_id: str
    name: str
    harness: str
    room_id: str
    runner_id: str = ""
    target_room_id: str = ""
    room_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunnerJob:
    id: str
    action: str
    agent: ManagedAgentSpec


@dataclass(frozen=True)
class ClaimResult:
    agent_token: str = field(default="", repr=False, compare=False)
    hub_url: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)


@dataclass
class ManagedRuntime:
    spec: ManagedAgentSpec
    participant_id: str
    hub_url: str
    agent_token: str = field(repr=False)
    adapter: HarnessAdapter = field(repr=False)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    status: str = "running"
    started_at: str = field(default_factory=_utcnow_iso)
    last_error: str = ""

    def public_dict(self) -> dict[str, Any]:
        return {
            "managed_agent_id": self.spec.managed_agent_id,
            "name": self.spec.name,
            "harness": self.spec.harness,
            "room_id": self.spec.room_id,
            "participant_id": self.participant_id,
            "status": self.status,
            "started_at": self.started_at,
            "last_error": self.last_error or None,
        }


@dataclass(frozen=True)
class LogEntry:
    level: str
    message: str
    created_at: str = field(default_factory=_utcnow_iso)


class BoundedLog:
    def __init__(self, max_entries: int = 200, max_message_chars: int = 4000) -> None:
        self._entries: deque[LogEntry] = deque(maxlen=max(10, max_entries))
        self._max_message_chars = max(128, max_message_chars)
        self._lock = threading.Lock()

    def append(self, level: str, message: str) -> None:
        clean_level = level if level in {"debug", "info", "warning", "error"} else "info"
        clean_message = str(message or "").strip()
        if not clean_message:
            return
        with self._lock:
            self._entries.append(
                LogEntry(
                    level=clean_level,
                    message=clean_message[-self._max_message_chars :],
                )
            )

    def drain(self, limit: int = 50) -> list[LogEntry]:
        out: list[LogEntry] = []
        with self._lock:
            while self._entries and len(out) < max(1, limit):
                out.append(self._entries.popleft())
        return out

    def restore_front(self, entries: list[LogEntry]) -> None:
        with self._lock:
            for entry in reversed(entries):
                self._entries.appendleft(entry)

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [asdict(entry) for entry in self._entries]

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


class RunnerApiProtocol(Protocol):
    def heartbeat(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def wait_for_job(self, timeout: float = 30.0) -> RunnerJob | None: ...

    def claim_job(self, job_id: str) -> ClaimResult: ...

    def complete_job(
        self,
        job_id: str,
        *,
        success: bool,
        result: Mapping[str, Any],
        error: str = "",
    ) -> Mapping[str, Any]: ...

    def post_log(self, managed_agent_id: str, level: str, message: str) -> None: ...

    def close(self) -> None: ...


def runner_config_path() -> Path:
    return runner_directory() / "config.json"


def runner_status_path() -> Path:
    return runner_directory() / "status.json"


def embedded_runner_config_path() -> Path:
    return runner_directory() / "embedded-config.json"


def embedded_runner_credential_path() -> Path:
    return runner_directory() / "embedded-credentials.json"


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _atomic_private_json(path: Path, value: Any) -> None:
    _ensure_private_dir(path.parent)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def save_runner_config(config: RunnerConfig) -> None:
    # The dataclass has no token field by construction.
    _atomic_private_json(runner_config_path(), asdict(config))


def load_runner_config() -> RunnerConfig | None:
    return _load_config_file(runner_config_path())


def _load_config_file(path: Path) -> RunnerConfig | None:
    if not path.is_file():
        return None
    try:
        os.chmod(path, 0o600)
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None
    # Migrate any pre-release config that accidentally kept the token inline.
    legacy_token = str(
        raw.pop("runner_token", "")
        or raw.pop("auth_token", "")
        or raw.pop("token", "")
        or ""
    )
    if legacy_token:
        save_runner_token(legacy_token)
        _atomic_private_json(path, raw)
    runner_id = _first_text(raw, "runner_id", "id")
    hub_url = _first_text(raw, "hub_url", "url", "base_url")
    if not runner_id or not hub_url:
        return None
    try:
        hub_url = _validated_hub_url(hub_url)
    except RunnerError:
        return None
    return RunnerConfig(
        runner_id=runner_id,
        hub_url=hub_url,
        name=_first_text(raw, "name", "runner_name") or "runner",
        connected_at=_first_text(raw, "connected_at", "created_at") or _utcnow_iso(),
        embedded=bool(raw.get("embedded", False)),
        version=int(raw.get("version") or RUNNER_CONFIG_VERSION),
    )


def _save_embedded_connection(config: RunnerConfig, token: str) -> None:
    _atomic_private_json(embedded_runner_config_path(), asdict(config))
    _atomic_private_json(
        embedded_runner_credential_path(),
        {"runner_token": token},
    )


def _load_embedded_connection() -> tuple[RunnerConfig | None, str]:
    config = _load_config_file(embedded_runner_config_path())
    path = embedded_runner_credential_path()
    if not config or not path.is_file():
        return config, ""
    try:
        os.chmod(path, 0o600)
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return config, ""
    token = str(raw.get("runner_token") or "") if isinstance(raw, dict) else ""
    return config, token


def _first_text(data: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = data.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _nested_mapping(data: Mapping[str, Any], *names: str) -> Mapping[str, Any]:
    for name in names:
        value = data.get(name)
        if isinstance(value, Mapping):
            return value
    return {}


def decode_redeem_response(
    payload: Mapping[str, Any],
    *,
    requested_url: str = "",
    requested_name: str = "",
) -> RedeemResult:
    root = payload
    result = _nested_mapping(root, "result", "data")
    runner = _nested_mapping(root, "runner") or _nested_mapping(result, "runner")
    sources = (root, result, runner)
    runner_id = next(
        (_first_text(source, "runner_id", "runnerId", "id") for source in sources if source),
        "",
    )
    # The generator above can stop on an empty first source; select explicitly.
    runner_id = next(
        (
            value
            for source in sources
            if source
            for value in [_first_text(source, "runner_id", "runnerId", "id")]
            if value
        ),
        "",
    )
    token = next(
        (
            value
            for source in sources
            if source
            for value in [
                _first_text(
                    source,
                    "runner_token",
                    "runnerToken",
                    "auth_token",
                    "authToken",
                    "token",
                )
            ]
            if value
        ),
        "",
    )
    hub_url = next(
        (
            value
            for source in sources
            if source
            for value in [
                _first_text(source, "hub_url", "hubUrl", "base_url", "baseUrl", "url")
            ]
            if value
        ),
        requested_url,
    )
    name = next(
        (
            value
            for source in (runner, result, root)
            if source
            for value in [_first_text(source, "name", "runner_name", "runnerName")]
            if value
        ),
        requested_name,
    )
    if not runner_id or not token:
        raise RunnerError("Runner redeem response did not include runner id and token")
    return RedeemResult(
        runner_id=runner_id,
        runner_token=token,
        hub_url=(hub_url or requested_url).rstrip("/"),
        name=name or requested_name or "runner",
    )


def decode_job_payload(payload: Mapping[str, Any] | None) -> RunnerJob | None:
    if not payload:
        return None
    if payload.get("timed_out") or payload.get("timeout"):
        return None
    raw: Mapping[str, Any] = payload
    for _ in range(4):
        if _first_text(raw, "id", "job_id", "jobId") and _first_text(
            raw, "action", "operation", "type"
        ):
            break
        jobs = raw.get("jobs")
        if isinstance(jobs, list):
            if not jobs or not isinstance(jobs[0], Mapping):
                return None
            raw = jobs[0]
            continue
        nested = next(
            (
                candidate
                for key in ("job", "runner_job", "runnerJob", "data", "result")
                for candidate in [raw.get(key)]
                if isinstance(candidate, Mapping)
            ),
            None,
        )
        if nested is None:
            break
        raw = nested

    job_id = _first_text(raw, "id", "job_id", "jobId")
    action = _first_text(raw, "action", "operation", "type").lower()
    managed_agent_id = _first_text(
        raw, "managed_agent_id", "managedAgentId", "agent_id", "agentId"
    )
    nested_agent = _nested_mapping(raw, "agent", "managed_agent", "managedAgent")
    if nested_agent:
        managed_agent_id = managed_agent_id or _first_text(
            nested_agent, "id", "managed_agent_id", "managedAgentId"
        )
    name = _first_text(raw, "agent_name", "agentName", "name") or _first_text(
        nested_agent, "name", "agent_name", "agentName"
    )
    harness = _first_text(raw, "harness") or _first_text(nested_agent, "harness")
    room_id = _first_text(raw, "room_id", "roomId") or _first_text(
        nested_agent, "room_id", "roomId", "room"
    )
    runner_id = _first_text(raw, "runner_id", "runnerId") or _first_text(
        nested_agent, "runner_id", "runnerId"
    )
    target_room_id = _first_text(raw, "target_room_id", "targetRoomId") or ""
    raw_rooms = raw.get("room_ids")
    if not isinstance(raw_rooms, list):
        raw_rooms = raw.get("roomIds") if isinstance(raw.get("roomIds"), list) else []
    room_ids = tuple(str(item).strip() for item in raw_rooms if str(item).strip())
    if not job_id or not action or not managed_agent_id:
        raise RunnerError("Runner job is missing id, action, or managed agent id")
    return RunnerJob(
        id=job_id,
        action=action,
        agent=ManagedAgentSpec(
            managed_agent_id=managed_agent_id,
            name=name,
            harness=harness,
            room_id=room_id,
            runner_id=runner_id,
            target_room_id=target_room_id,
            room_ids=room_ids,
        ),
    )


def decode_claim_response(payload: Mapping[str, Any] | None) -> ClaimResult:
    root: Mapping[str, Any] = payload or {}
    result = _nested_mapping(root, "claim", "result", "data")
    credentials = (
        _nested_mapping(root, "credentials")
        or _nested_mapping(result, "credentials")
    )
    sources = (root, result, credentials)
    token = next(
        (
            value
            for source in sources
            if source
            for value in [
                _first_text(
                    source,
                    "agent_token",
                    "agentToken",
                    "auth_token",
                    "authToken",
                    "token",
                )
            ]
            if value
        ),
        "",
    )
    hub_url = next(
        (
            value
            for source in sources
            if source
            for value in [
                _first_text(source, "hub_url", "hubUrl", "base_url", "baseUrl", "url")
            ]
            if value
        ),
        "",
    )
    return ClaimResult(agent_token=token, hub_url=hub_url.rstrip("/"), raw=root)


class RunnerAPI:
    """Small REST client; all response-shape tolerance lives in decoders above."""

    def __init__(
        self,
        config: RunnerConfig,
        runner_token: str,
        *,
        client: Any | None = None,
    ) -> None:
        self.config = config
        self._runner_token = runner_token
        self._owned_client = client is None
        self._client = client or httpx.Client(
            base_url=config.hub_url,
            headers={"Authorization": f"Bearer {runner_token}"} if runner_token else {},
            timeout=45.0,
        )

    def __repr__(self) -> str:
        return (
            f"RunnerAPI(runner_id={self.config.runner_id!r}, "
            f"hub_url={self.config.hub_url!r}, authenticated={bool(self._runner_token)})"
        )

    @staticmethod
    def redeem(
        hub_url: str,
        code: str,
        name: str,
        *,
        client: Any | None = None,
        capabilities: Mapping[str, Any] | None = None,
    ) -> RedeemResult:
        body = {
            "code": code.strip(),
            "name": name.strip(),
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "version": PACKAGE_VERSION,
            "capabilities": dict(capabilities or runner_capabilities()),
        }
        owned = client is None
        c = client or httpx.Client(base_url=hub_url.rstrip("/"), timeout=15.0)
        try:
            response = c.post("/v1/runners/redeem", json=body)
            response.raise_for_status()
            data = _response_json(response)
            return decode_redeem_response(
                data,
                requested_url=hub_url,
                requested_name=name,
            )
        finally:
            if owned:
                c.close()

    def heartbeat(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._request(
            "POST",
            f"/v1/runners/{self.config.runner_id}/heartbeat",
            json=dict(payload),
        )

    def disconnect(self) -> Mapping[str, Any]:
        return self._request(
            "POST",
            f"/v1/runners/{self.config.runner_id}/disconnect",
            json={},
        )

    def wait_for_job(self, timeout: float = 30.0) -> RunnerJob | None:
        wait_seconds = max(1.0, min(float(timeout), 30.0))
        data = self._request(
            "GET",
            f"/v1/runners/{self.config.runner_id}/jobs/wait",
            params={"timeout": wait_seconds},
            timeout=max(5.0, wait_seconds + 10.0),
        )
        return decode_job_payload(data)

    def claim_job(self, job_id: str) -> ClaimResult:
        data = self._request(
            "POST",
            f"/v1/runners/{self.config.runner_id}/jobs/{job_id}/claim",
            json={},
        )
        return decode_claim_response(data)

    def complete_job(
        self,
        job_id: str,
        *,
        success: bool,
        result: Mapping[str, Any],
        error: str = "",
    ) -> Mapping[str, Any]:
        return self._request(
            "POST",
            f"/v1/runners/{self.config.runner_id}/jobs/{job_id}/complete",
            json={
                "success": bool(success),
                "result": dict(result),
                "error": error or None,
            },
        )

    def post_log(self, managed_agent_id: str, level: str, message: str) -> None:
        self._request(
            "POST",
            f"/v1/runners/{self.config.runner_id}/logs",
            json={
                "managed_agent_id": managed_agent_id,
                "level": level,
                "message": message,
            },
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Mapping[str, Any]:
        kwargs: dict[str, Any] = {
            "json": dict(json) if json is not None else None,
            "params": dict(params) if params is not None else None,
            "headers": (
                {"Authorization": f"Bearer {self._runner_token}"}
                if self._runner_token
                else {}
            ),
        }
        if timeout is not None and self._owned_client:
            kwargs["timeout"] = timeout
        response = self._client.request(method, path, **kwargs)
        response.raise_for_status()
        return _response_json(response)

    def close(self) -> None:
        if self._owned_client:
            self._client.close()


def _response_json(response: Any) -> Mapping[str, Any]:
    if getattr(response, "status_code", 200) == 204:
        return {}
    try:
        value = response.json()
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, Mapping) else {}


def runner_capabilities() -> dict[str, Any]:
    harnesses: dict[str, Any] = {}
    names = ("claude-code", "grok", "hermes")
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        probes = list(pool.map(lambda name: get_adapter(name).probe(), names))
    for probe in probes:
        harnesses[probe.harness] = {
            "available": probe.available,
            "installed": probe.available,
            "authenticated": probe.authenticated,
            "status": probe.status,
            "detail": probe.detail,
            "setup_instructions": probe.setup_instructions,
        }
    return {
        "managed_agents": True,
        "addressed_only": True,
        "harnesses": harnesses,
    }


def connect_runner(
    *,
    url: str,
    code: str,
    name: str,
    client: Any | None = None,
) -> dict[str, Any]:
    hub_url = _validated_hub_url(url)
    if not code.strip():
        raise RunnerError("Pairing code is required")
    if not name.strip():
        raise RunnerError("Runner name is required")
    redeemed = RunnerAPI.redeem(
        hub_url,
        code,
        name,
        client=client,
    )
    config = RunnerConfig(
        runner_id=redeemed.runner_id,
        hub_url=_prefer_reachable_hub_url(hub_url, redeemed.hub_url),
        name=redeemed.name or name.strip(),
    )
    save_runner_config(config)
    save_runner_token(redeemed.runner_token)
    # Redeem stores the runner as stale until the first heartbeat. Check in
    # from this process so Live Ops sees the connect before launchd starts.
    api = RunnerAPI(config, redeemed.runner_token, client=client)
    try:
        api.heartbeat(
            {
                "version": PACKAGE_VERSION,
                "capabilities": runner_capabilities(),
                "agent_statuses": {},
            }
        )
    finally:
        api.close()
    return config.public_dict()


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _loopback_host(host: str) -> bool:
    return (host or "").lower() in _LOOPBACK_HOSTS


def _hub_url_port(url: str) -> int:
    parsed = urlparse(_validated_hub_url(url))
    if parsed.port is not None:
        return parsed.port
    return 443 if parsed.scheme == "https" else 80


def _same_loopback_hub(left: str, right: str) -> bool:
    left_url = _validated_hub_url(left)
    right_url = _validated_hub_url(right)
    left_host = (urlparse(left_url).hostname or "").lower()
    right_host = (urlparse(right_url).hostname or "").lower()
    if not (_loopback_host(left_host) and _loopback_host(right_host)):
        return left_url.rstrip("/") == right_url.rstrip("/")
    return _hub_url_port(left_url) == _hub_url_port(right_url)


def ensure_local_runner(
    *,
    url: str,
    auth_token: str,
    name: str = "Local Runner",
    client: Any | None = None,
) -> dict[str, Any]:
    """Pair and start a background runner for a loopback hub (Docker / LAN on this Mac).

    Idempotent: reuses saved credentials when they already target the same hub
    and only restarts the OS service when needed.
    """
    hub_url = _validated_hub_url(url)
    host = (urlparse(hub_url).hostname or "").lower()
    if not _loopback_host(host):
        raise RunnerError("runner ensure only supports loopback hub URLs")
    token_value = (auth_token or "").strip()
    if not token_value:
        raise RunnerError("OPENGATEWAY_AUTH_TOKEN is required to pair a local runner")

    from opengateway.runner_service import (
        install_runner_service,
        runner_service_status,
    )

    existing = load_runner_config()
    runner_token = load_runner_token()
    if existing and runner_token and _same_loopback_hub(existing.hub_url, hub_url):
        service = runner_service_status()
        if service.get("running"):
            return {
                "status": "already_running",
                **existing.public_dict(),
                "service": service,
            }
        installed = install_runner_service()
        return {
            "status": "service_started",
            **existing.public_dict(),
            "service": installed,
        }

    headers = {"Authorization": f"Bearer {token_value}"}
    with httpx.Client(base_url=hub_url, headers=headers, timeout=30.0) as http:
        pair_response = http.post("/v1/runners/pair", json={"name": name})
        pair_response.raise_for_status()
        pair_data = _response_json(pair_response)
        code = _first_text(pair_data, "code", "pairing_code", "pairingCode")
        if not code:
            raise RunnerError("Hub pair response omitted its code")

    result = connect_runner(
        url=hub_url,
        code=code,
        name=name,
        client=client,
    )
    service = install_runner_service()
    return {
        "status": "paired",
        **result,
        "service": service,
    }


def _validated_hub_url(url: str) -> str:
    value = (url or "").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RunnerError("Runner URL must be an http(s) hub URL")
    if parsed.username or parsed.password:
        raise RunnerError("Runner URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise RunnerError("Runner URL must not contain a query or fragment")
    return value


def _prefer_reachable_hub_url(requested: str, advertised: str) -> str:
    """Keep a loopback URL when the hub also advertises Tailscale or LAN.

    The local runner talks to the process on this machine. The advertised
    MagicDNS URL is for other devices and is often not proxied yet.
    """
    requested_url = _validated_hub_url(requested)
    if not advertised:
        return requested_url
    advertised_url = _validated_hub_url(advertised)
    requested_host = (urlparse(requested_url).hostname or "").lower()
    if _loopback_host(requested_host):
        return requested_url
    advertised_host = (urlparse(advertised_url).hostname or "").lower()
    if _loopback_host(advertised_host):
        return requested_url
    return advertised_url


HubRequest = Callable[
    [str, str, str, str, Mapping[str, Any] | None, Mapping[str, Any] | None],
    Mapping[str, Any],
]


def _default_hub_request(
    method: str,
    hub_url: str,
    path: str,
    token: str,
    json_body: Mapping[str, Any] | None,
    params: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(base_url=hub_url, headers=headers, timeout=30.0) as client:
        response = client.request(
            method,
            path,
            json=dict(json_body) if json_body is not None else None,
            params=dict(params) if params is not None else None,
        )
        response.raise_for_status()
        return _response_json(response)


class RunnerService:
    def __init__(
        self,
        config: RunnerConfig,
        runner_token: str,
        *,
        api: RunnerApiProtocol | None = None,
        adapter_factory: Callable[[str], HarnessAdapter] = get_adapter,
        hub_request: HubRequest = _default_hub_request,
        runtime_manager: SeatRuntimeManager = seat_runtime,
        heartbeat_interval: float = 20.0,
        wait_timeout: float = 25.0,
        log_limit: int = 200,
        max_concurrency: int | None = None,
    ) -> None:
        self.config = config
        self._runner_token = runner_token
        self.api: RunnerApiProtocol = api or RunnerAPI(config, runner_token)
        self._adapter_factory = adapter_factory
        self._hub_request = hub_request
        self._runtime_manager = runtime_manager
        self._heartbeat_interval = max(2.0, heartbeat_interval)
        self._wait_timeout = max(1.0, wait_timeout)
        self._log_limit = max(10, log_limit)
        if max_concurrency is None:
            try:
                max_concurrency = int(
                    os.environ.get("OPENGATEWAY_RUNNER_MAX_CONCURRENCY") or "4"
                )
            except ValueError:
                max_concurrency = 4
        self._max_concurrency = max(1, min(int(max_concurrency), 32))
        self._turn_slots = threading.BoundedSemaphore(self._max_concurrency)
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._runtimes: dict[str, dict[str, ManagedRuntime]] = {}
        self._logs: dict[str, BoundedLog] = {}
        self._secrets: set[str] = {runner_token} if runner_token else set()
        self._last_error = ""
        self._started_at = ""
        self._capabilities: dict[str, Any] | None = None
        self._capabilities_at = 0.0
        self._terminal_state = ""
        self._agent_activity_by_room: dict[str, dict[str, str]] = {}

    def __repr__(self) -> str:
        return (
            f"RunnerService(runner_id={self.config.runner_id!r}, "
            f"hub_url={self.config.hub_url!r}, runtimes={len(self._runtimes)})"
        )

    def run(self, *, max_iterations: int | None = None) -> int:
        loop_owner = f"runner-loop:{self.config.runner_id}:{id(self)}"
        if not try_acquire_seat_lock(
            "__runner__",
            self.config.runner_id,
            owner=loop_owner,
        ):
            time.sleep(0.25)
            if not try_acquire_seat_lock(
                "__runner__",
                self.config.runner_id,
                owner=loop_owner,
            ):
                raise RunnerError(
                    f"Runner {self.config.runner_id} is already active in another process"
                )
        try:
            self._started_at = _utcnow_iso()
            self._write_status("running")
            self.restore_agents()
            next_heartbeat = 0.0
            iterations = 0
            while not self._stop_event.is_set():
                now = time.monotonic()
                if now >= next_heartbeat:
                    try:
                        self._heartbeat()
                        self._last_error = ""
                    except Exception as exc:
                        self._last_error = self._sanitize(str(exc))
                        if self._is_terminal_api_error(exc):
                            self._terminal_state = "revoked"
                            self._write_status(self._terminal_state)
                            break
                        self._write_status("degraded")
                    next_heartbeat = now + self._heartbeat_interval

                try:
                    job = self.api.wait_for_job(timeout=self._wait_timeout)
                    if job is not None:
                        self.process_job(job)
                except KeyboardInterrupt:
                    self._stop_event.set()
                except Exception as exc:
                    self._last_error = self._sanitize(str(exc))
                    if self._is_terminal_api_error(exc):
                        self._terminal_state = "revoked"
                        self._write_status(self._terminal_state)
                        break
                    self._write_status("degraded")
                    self._stop_event.wait(1.0)

                iterations += 1
                if max_iterations is not None and iterations >= max_iterations:
                    break
        finally:
            try:
                self.shutdown(preserve_desired=True)
                try:
                    self._write_status(self._terminal_state or "stopped")
                except OSError:
                    pass
            finally:
                try:
                    self.api.close()
                finally:
                    release_seat_lock(
                        "__runner__",
                        self.config.runner_id,
                        owner=loop_owner,
                    )
        return 0

    @staticmethod
    def _is_terminal_api_error(exc: BaseException) -> bool:
        seen: set[int] = set()
        current: BaseException | None = exc
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, httpx.HTTPStatusError):
                return current.response.status_code in {401, 403, 404}
            current = current.__cause__ or current.__context__
        return False

    def shutdown(self, *, preserve_desired: bool = True) -> None:
        self._stop_event.set()
        with self._lock:
            agent_ids = list(self._runtimes)
        for managed_agent_id in agent_ids:
            try:
                self._stop_agent(
                    managed_agent_id,
                    desired_state=None if preserve_desired else "stopped",
                    leave=True,
                )
            except Exception:
                continue

    def process_job(self, job: RunnerJob | Mapping[str, Any]) -> dict[str, Any]:
        parsed = decode_job_payload(job) if isinstance(job, Mapping) else job
        if parsed is None:
            raise RunnerError("Empty runner job")
        claim = self.api.claim_job(parsed.id)
        if claim.agent_token:
            self._remember_secret(claim.agent_token)
        self._log(
            parsed.agent.managed_agent_id,
            "info",
            f"claimed {parsed.action} job {parsed.id}",
        )
        try:
            result = self._execute(parsed, claim)
        except SetupRequired as exc:
            if claim.agent_token:
                remove_agent_credential(
                    f"managed:{parsed.agent.managed_agent_id}"
                )
            message = self._sanitize(exc.probe.detail)
            result = {
                "managed_agent_id": parsed.agent.managed_agent_id,
                "runtime_status": "error",
                "probe_status": exc.probe.status,
                "setup_instructions": exc.probe.setup_instructions,
            }
            self._mark_record_error(parsed.agent, message)
            self._log(parsed.agent.managed_agent_id, "error", message)
            self._flush_logs(parsed.agent.managed_agent_id)
            self._report_completion(
                parsed.id,
                success=False,
                result=result,
                error=message,
            )
            return result
        except Exception as exc:
            if claim.agent_token and parsed.action in {"start", "restart"}:
                remove_agent_credential(
                    f"managed:{parsed.agent.managed_agent_id}"
                )
            message = self._sanitize(str(exc)) or "managed runner job failed"
            result = {
                "managed_agent_id": parsed.agent.managed_agent_id,
                "runtime_status": "error",
            }
            self._mark_record_error(parsed.agent, message)
            self._log(parsed.agent.managed_agent_id, "error", message)
            self._flush_logs(parsed.agent.managed_agent_id)
            self._report_completion(
                parsed.id,
                success=False,
                result=result,
                error=message,
            )
            return {**result, "error": message}

        safe_result = self._sanitize_value(result)
        self._log(
            parsed.agent.managed_agent_id,
            "info",
            f"{parsed.action} job completed",
        )
        self._flush_logs(parsed.agent.managed_agent_id)
        try:
            self._report_completion(
                parsed.id,
                success=True,
                result=safe_result,
                error="",
            )
        except Exception as exc:
            # Execution already succeeded. Keep the local runtime/credential
            # intact and surface a transport failure for the outer loop rather
            # than falsely rolling the agent back to error.
            message = self._sanitize(str(exc))
            self._log(
                parsed.agent.managed_agent_id,
                "warning",
                f"job completion report failed: {message}",
            )
            self._flush_logs(parsed.agent.managed_agent_id)
            raise RunnerError(
                f"Could not report completed job {parsed.id}: {message}"
            ) from exc
        return safe_result

    def _report_completion(
        self,
        job_id: str,
        *,
        success: bool,
        result: Mapping[str, Any],
        error: str,
    ) -> Mapping[str, Any]:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return self.api.complete_job(
                    job_id,
                    success=success,
                    result=result,
                    error=error,
                )
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    self._stop_event.wait(0.2 * (attempt + 1))
        assert last_error is not None
        raise last_error

    def _execute(self, job: RunnerJob, claim: ClaimResult) -> dict[str, Any]:
        action = job.action.lower().strip()
        if action == "start":
            return self._start_memberships(job.agent, claim)
        if action == "stop":
            return self._stop_agent(
                job.agent.managed_agent_id,
                fallback_spec=job.agent,
                desired_state="stopped",
                leave=True,
            )
        if action == "restart":
            self._stop_agent(
                job.agent.managed_agent_id,
                fallback_spec=job.agent,
                desired_state="running",
                leave=True,
            )
            return self._start_memberships(job.agent, claim)
        if action == "join_room":
            target = (job.agent.target_room_id or "").strip()
            if not target:
                raise RunnerError("Join job is missing target_room_id")
            spec = ManagedAgentSpec(
                managed_agent_id=job.agent.managed_agent_id,
                name=job.agent.name,
                harness=job.agent.harness,
                room_id=target,
                runner_id=job.agent.runner_id,
            )
            result = self._start_agent(spec, claim)
            result["room_id"] = target
            return result
        if action == "leave_room":
            target = (job.agent.target_room_id or job.agent.room_id or "").strip()
            if not target:
                raise RunnerError("Leave job is missing a room")
            result = self._stop_agent(
                job.agent.managed_agent_id,
                fallback_spec=job.agent,
                desired_state="running",
                leave=True,
                room_id=target,
            )
            remove_seat(
                managed_agent_id=job.agent.managed_agent_id,
                room_id=target,
            )
            result["room_id"] = target
            return result
        if action == "move":
            target = (job.agent.target_room_id or "").strip()
            source = (job.agent.room_id or "").strip()
            if not target:
                raise RunnerError("Move job is missing target_room_id")
            if source and source != target:
                self._stop_agent(
                    job.agent.managed_agent_id,
                    fallback_spec=job.agent,
                    desired_state="running",
                    leave=True,
                    room_id=source,
                )
                remove_seat(
                    managed_agent_id=job.agent.managed_agent_id,
                    room_id=source,
                )
            moved = ManagedAgentSpec(
                managed_agent_id=job.agent.managed_agent_id,
                name=job.agent.name,
                harness=job.agent.harness,
                room_id=target,
                runner_id=job.agent.runner_id,
            )
            result = self._start_agent(moved, claim)
            result["room_id"] = target
            return result
        if action == "delete":
            return self._delete_agent(job.agent, claim)
        raise RunnerError(f"Unsupported managed-agent action: {action}")

    def _start_memberships(
        self, spec: ManagedAgentSpec, claim: ClaimResult
    ) -> dict[str, Any]:
        rooms = [room for room in spec.room_ids if room]
        if spec.room_id and spec.room_id not in rooms:
            rooms.insert(0, spec.room_id)
        if not rooms:
            rooms = [spec.room_id]
        token = claim.agent_token
        last: dict[str, Any] = {}
        for room in rooms:
            room_claim = ClaimResult(
                agent_token=token,
                hub_url=claim.hub_url,
                raw=claim.raw,
            )
            last = self._start_agent(
                ManagedAgentSpec(
                    managed_agent_id=spec.managed_agent_id,
                    name=spec.name,
                    harness=spec.harness,
                    room_id=room,
                    runner_id=spec.runner_id,
                ),
                room_claim,
            )
            token = token or load_agent_credential(
                f"managed:{spec.managed_agent_id}"
            )
        last["room_ids"] = rooms
        return last

    def _start_agent(
        self,
        spec: ManagedAgentSpec,
        claim: ClaimResult | None = None,
        *,
        restoring: bool = False,
    ) -> dict[str, Any]:
        self._validate_spec(spec)
        with self._lock:
            existing = (self._runtimes.get(spec.managed_agent_id) or {}).get(
                spec.room_id
            )
            if existing and existing.status == "running":
                return {
                    **existing.public_dict(),
                    "idempotent": True,
                    "note": "already_running",
                }

        record = get_managed_seat(spec.managed_agent_id, spec.room_id)
        if (
            record
            and record.participant_id
            and record.desired_state == "running"
            and record.room_id == spec.room_id
            and seat_lock_active(record.room_id, record.participant_id)
        ):
            return {
                "managed_agent_id": spec.managed_agent_id,
                "participant_id": record.participant_id,
                "room_id": record.room_id,
                "runtime_status": "running",
                "idempotent": True,
                "note": "already_running_other_process",
            }

        credential_key = f"managed:{spec.managed_agent_id}"
        token = (claim.agent_token if claim else "") or load_agent_credential(
            credential_key
        )
        if not token:
            raise RunnerError(
                "Start job did not provide an agent token and no saved credential exists"
            )
        self._remember_secret(token)
        hub_url = _validated_hub_url(
            (claim.hub_url if claim else "")
            or (record.base_url if record else "")
            or self.config.hub_url
        )
        # Persist the one-time claim before any probe/network work, but only
        # after duplicate detection so a redundant response cannot replace a
        # live seat's credential.
        save_agent_credential(credential_key, token)
        adapter = self._adapter_factory(spec.harness)
        probe = adapter.probe()
        if not probe.ready:
            raise SetupRequired(probe)

        upsert_seat(
            SeatRecord(
                room_id=spec.room_id,
                participant_id=record.participant_id if record else "",
                name=spec.name,
                harness=self._canonical_harness(spec.harness),
                wake="harness",
                base_url=hub_url,
                auth_token=token,
                updated_at=_utcnow_iso(),
                managed_agent_id=spec.managed_agent_id,
                runner_id=self.config.runner_id,
                desired_state="running",
                runtime_status="starting",
                metadata=dict(record.metadata) if record else {},
            )
        )
        self._log(
            spec.managed_agent_id,
            "info",
            "restoring managed agent" if restoring else "starting managed agent",
        )
        join_body: dict[str, Any] = {
            "name": spec.name,
            "harness": self._canonical_harness(spec.harness),
            "role": "contributor",
            "capabilities": ["listen", "radio", "managed-runner"],
            "metadata": {
                "managed_agent_id": spec.managed_agent_id,
                "runner_id": self.config.runner_id,
                "managed": True,
            },
        }
        participant = self._hub_request(
            "POST",
            hub_url,
            f"/v1/rooms/{spec.room_id}/join",
            token,
            join_body,
            None,
        )
        participant_id = _first_text(
            participant, "id", "participant_id", "participantId"
        )
        if not participant_id:
            nested = _nested_mapping(participant, "participant", "result", "data")
            participant_id = _first_text(
                nested, "id", "participant_id", "participantId"
            )
        if not participant_id:
            raise RunnerError("Room join did not return a participant id")

        runtime = ManagedRuntime(
            spec=spec,
            participant_id=participant_id,
            hub_url=hub_url,
            agent_token=token,
            adapter=adapter,
        )
        wake_handler = self._make_wake_handler(runtime)
        poller_status = self._runtime_manager.start(
            room_id=spec.room_id,
            participant_id=participant_id,
            name=spec.name,
            base_url=hub_url,
            auth_token=token,
            timeout=45.0,
            restart=False,
            on_wake=wake_handler,
            on_stop=runtime.cancel_event.set,
            register=False,
            harness=self._canonical_harness(spec.harness),
            wake="harness",
            lock_owner=f"runner:{self.config.runner_id}:{spec.managed_agent_id}",
            managed_agent_id=spec.managed_agent_id,
            runner_id=self.config.runner_id,
        )
        note = str(poller_status.get("note") or "")
        if poller_status.get("radio") == "external":
            runtime.status = "running"
        with self._lock:
            self._runtimes.setdefault(spec.managed_agent_id, {})[
                spec.room_id
            ] = runtime
        existing_meta = dict(record.metadata) if record else {}
        session_id = valid_harness_session_id(
            str(existing_meta.get("harness_session_id") or "")
        ) or str(uuid.uuid4())
        upsert_seat(
            SeatRecord(
                room_id=spec.room_id,
                participant_id=participant_id,
                name=spec.name,
                harness=self._canonical_harness(spec.harness),
                wake="harness",
                base_url=hub_url,
                auth_token=token,
                updated_at=_utcnow_iso(),
                managed_agent_id=spec.managed_agent_id,
                runner_id=self.config.runner_id,
                desired_state="running",
                runtime_status="running",
                metadata={
                    **existing_meta,
                    "participant_id": participant_id,
                    "restored": restoring,
                    "harness_session_id": session_id,
                    "harness_session_used": bool(
                        existing_meta.get("harness_session_used")
                    ),
                },
            )
        )
        self._write_status("running")
        if not note.startswith("already_"):
            self._post_checkin(runtime)
        return {
            **runtime.public_dict(),
            "runtime_status": "running",
            "presence": "listening",
            "poller": poller_status.get("radio") or "on",
            "note": note or ("restored" if restoring else "started"),
            "idempotent": note.startswith("already_"),
        }

    def _post_checkin(self, runtime: ManagedRuntime) -> None:
        harness = self._canonical_harness(runtime.spec.harness)
        body = {
            "from_participant_id": runtime.participant_id,
            "content": (
                f"{runtime.spec.name} ({harness}) joined and is listening."
            ),
            "metadata": {
                "checkin": True,
                "managed_agent_id": runtime.spec.managed_agent_id,
            },
        }
        try:
            self._hub_request(
                "POST",
                runtime.hub_url,
                f"/v1/rooms/{runtime.spec.room_id}/messages",
                runtime.agent_token,
                body,
                None,
            )
            self._log(
                runtime.spec.managed_agent_id,
                "info",
                "posted room check-in line",
            )
        except Exception as exc:
            self._log(
                runtime.spec.managed_agent_id,
                "warning",
                f"check-in line failed: {self._sanitize(str(exc))}",
            )

    def _release_seat_for_room_change(self, managed_agent_id: str) -> None:
        """Drop stale locks/cursors so a move can join a new room."""
        record = get_managed_seat(managed_agent_id)
        if not record or not record.participant_id:
            return
        clear_cursor(record.room_id, record.participant_id)
        release_seat_lock(record.room_id, record.participant_id, force=True)
        try:
            lock_path(record.room_id, record.participant_id).unlink(missing_ok=True)
        except OSError:
            pass
        metadata = dict(record.metadata)
        metadata.pop("harness_session_id", None)
        metadata.pop("harness_session_used", None)
        record.metadata = metadata
        record.participant_id = ""
        record.updated_at = _utcnow_iso()
        upsert_seat(record)

    def _stop_agent(
        self,
        managed_agent_id: str,
        *,
        fallback_spec: ManagedAgentSpec | None = None,
        desired_state: str | None = "stopped",
        leave: bool,
        room_id: str = "",
    ) -> dict[str, Any]:
        requested_room = room_id
        with self._lock:
            bucket = self._runtimes.get(managed_agent_id) or {}
            if room_id:
                runtime = bucket.pop(room_id, None)
                if not bucket:
                    self._runtimes.pop(managed_agent_id, None)
            else:
                runtime = None
                if bucket:
                    runtime = next(iter(bucket.values()))
                    for extra in list(bucket.values()):
                        if extra is not runtime:
                            extra.cancel_event.set()
                            self._runtime_manager.stop(
                                room_id=extra.spec.room_id,
                                participant_id=extra.participant_id,
                            )
                    self._runtimes.pop(managed_agent_id, None)
        record = get_managed_seat(managed_agent_id, room_id)
        spec = runtime.spec if runtime else fallback_spec
        room_id = (
            runtime.spec.room_id
            if runtime
            else record.room_id
            if record
            else spec.room_id
            if spec
            else ""
        )
        participant_id = (
            runtime.participant_id
            if runtime
            else record.participant_id
            if record
            else ""
        )
        if runtime:
            runtime.status = "stopping"
            runtime.cancel_event.set()
        stopped = self._runtime_manager.stop(
            room_id=room_id,
            participant_id=participant_id,
        )
        if stopped.get("still_running"):
            raise RunnerError("Managed agent poller did not terminate")

        token = (
            runtime.agent_token
            if runtime
            else load_agent_credential(f"managed:{managed_agent_id}")
        )
        hub_url = (
            runtime.hub_url
            if runtime
            else record.base_url
            if record
            else self.config.hub_url
        )
        presence_left = False
        if leave and room_id and participant_id:
            try:
                self._hub_request(
                    "POST",
                    hub_url,
                    f"/v1/rooms/{room_id}/leave",
                    token,
                    None,
                    {"participant_id": participant_id},
                )
                presence_left = True
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {401, 403, 404}:
                    raise
                self._log(
                    managed_agent_id,
                    "warning",
                    "presence leave was already unavailable; poller stopped locally",
                )
            except RunnerError:
                raise
            except Exception as exc:
                # Leave is best-effort for idempotency, but make the condition
                # visible in bounded logs.
                self._log(
                    managed_agent_id,
                    "warning",
                    f"presence leave failed: {self._sanitize(str(exc))}",
                )

        if record:
            record.runtime_status = "stopped"
            if desired_state is not None:
                record.desired_state = desired_state
            record.updated_at = _utcnow_iso()
            record.auth_token = token
            upsert_seat(record)
        if leave and not requested_room:
            for seat in get_managed_seats(managed_agent_id):
                if not seat.room_id or seat.room_id == room_id:
                    continue
                if seat.participant_id:
                    try:
                        self._hub_request(
                            "POST",
                            hub_url,
                            f"/v1/rooms/{seat.room_id}/leave",
                            token,
                            None,
                            {"participant_id": seat.participant_id},
                        )
                    except Exception:
                        pass
                seat.runtime_status = "stopped"
                if desired_state is not None:
                    seat.desired_state = desired_state
                seat.updated_at = _utcnow_iso()
                upsert_seat(seat)
        self._log(managed_agent_id, "info", "managed agent stopped")
        self._write_status("running")
        return {
            "managed_agent_id": managed_agent_id,
            "room_id": room_id or None,
            "participant_id": participant_id or None,
            "runtime_status": "stopped",
            "presence": (
                "offline"
                if participant_id and presence_left
                else "stale_pending"
                if participant_id
                else None
            ),
            "leave_attempted": bool(leave and room_id and participant_id),
            "leave_succeeded": presence_left,
            "idempotent": runtime is None and not stopped.get("stopped"),
            "note": "stopped" if runtime or stopped.get("stopped") else "already_stopped",
        }

    def _delete_agent(
        self,
        spec: ManagedAgentSpec,
        claim: ClaimResult,
    ) -> dict[str, Any]:
        record = get_managed_seat(spec.managed_agent_id)
        stopped = self._stop_agent(
            spec.managed_agent_id,
            fallback_spec=spec,
            desired_state="stopped",
            leave=True,
        )
        if record and record.participant_id:
            clear_cursor(record.room_id, record.participant_id)
            release_seat_lock(
                record.room_id,
                record.participant_id,
                force=True,
            )
            try:
                lock_path(record.room_id, record.participant_id).unlink(missing_ok=True)
            except OSError:
                pass
        remove_agent_credential(f"managed:{spec.managed_agent_id}")
        remove_seat(managed_agent_id=spec.managed_agent_id)
        with self._lock:
            self._runtimes.pop(spec.managed_agent_id, None)
        self._log(spec.managed_agent_id, "info", "managed agent deleted")
        return {
            **stopped,
            "runtime_status": "deleted",
            "deleted": True,
            "credential_cleared": True,
            "cursor_cleared": True,
        }

    def restore_agents(self) -> list[dict[str, Any]]:
        restored: list[dict[str, Any]] = []
        for record in load_registry():
            if not record.managed_agent_id or record.desired_state != "running":
                continue
            if record.runner_id and record.runner_id != self.config.runner_id:
                continue
            spec = ManagedAgentSpec(
                managed_agent_id=record.managed_agent_id,
                name=record.name,
                harness=record.harness,
                room_id=record.room_id,
                runner_id=record.runner_id or self.config.runner_id,
            )
            try:
                restored.append(self._start_agent(spec, restoring=True))
            except Exception as exc:
                message = self._sanitize(str(exc))
                record.runtime_status = "error"
                record.updated_at = _utcnow_iso()
                record.metadata = {
                    **record.metadata,
                    "last_error": message,
                }
                upsert_seat(record)
                self._log(record.managed_agent_id, "error", f"restore failed: {message}")
                restored.append(
                    {
                        "managed_agent_id": record.managed_agent_id,
                        "runtime_status": "error",
                        "error": message,
                    }
                )
        return restored

    def _make_wake_handler(
        self,
        runtime: ManagedRuntime,
    ) -> Callable[[list[dict[str, Any]]], None]:
        def on_wake(events: list[dict[str, Any]]) -> None:
            if runtime.cancel_event.is_set():
                return
            acquired = False
            while not runtime.cancel_event.is_set():
                if self._turn_slots.acquire(timeout=0.2):
                    acquired = True
                    break
            if not acquired:
                return
            try:
                self._log(
                    runtime.spec.managed_agent_id,
                    "info",
                    f"invoking {self._canonical_harness(runtime.spec.harness)} "
                    f"for {len(events)} addressed message(s)",
                )
                self._set_agent_activity(
                    runtime.spec.managed_agent_id,
                    "thinking",
                    runtime.spec.room_id,
                )
                result = self._invoke_harness_turn(runtime, events)
                if result.cancelled:
                    self._log(
                        runtime.spec.managed_agent_id,
                        "info",
                        "harness turn cancelled during stop",
                    )
                else:
                    reply = result.output.strip()
                    if not reply:
                        runtime.status = "running"
                        runtime.last_error = ""
                        self._persist_runtime_status(runtime, "running")
                        self._log(
                            runtime.spec.managed_agent_id,
                            "info",
                            "harness returned no reply; nothing was posted",
                        )
                        return
                    reply = reply[:65536]
                    post_body: dict[str, Any] = {
                        "from_participant_id": runtime.participant_id,
                        "content": reply,
                        "metadata": self._reply_metadata(runtime, events),
                    }
                    dm_peer = self._dm_reply_target(runtime, events)
                    if dm_peer:
                        post_body["to_participant_id"] = dm_peer
                    posted = self._hub_request(
                        "POST",
                        runtime.hub_url,
                        f"/v1/rooms/{runtime.spec.room_id}/messages",
                        runtime.agent_token,
                        post_body,
                        None,
                    )
                    if posted.get("blocked"):
                        self._log(
                            runtime.spec.managed_agent_id,
                            "warning",
                            f"reply suppressed by hub: {posted.get('reason') or 'guard'}",
                        )
                    else:
                        self._log(
                            runtime.spec.managed_agent_id,
                            "info",
                            "runner posted one harness reply",
                        )
                    runtime.status = "running"
                    runtime.last_error = ""
                    self._persist_runtime_status(runtime, "running")
                    self._log(
                        runtime.spec.managed_agent_id,
                        "info",
                        f"harness turn completed in {result.duration_seconds:.1f}s",
                    )
            except AdapterError as exc:
                runtime.last_error = self._sanitize(str(exc))
                runtime.status = "error"
                self._persist_runtime_status(
                    runtime,
                    "error",
                    error=runtime.last_error,
                )
                self._log(
                    runtime.spec.managed_agent_id,
                    "error",
                    runtime.last_error,
                )
            except Exception as exc:
                runtime.last_error = (
                    self._sanitize(str(exc)) or "managed harness turn failed"
                )
                runtime.status = "error"
                self._persist_runtime_status(
                    runtime,
                    "error",
                    error=runtime.last_error,
                )
                self._log(
                    runtime.spec.managed_agent_id,
                    "error",
                    runtime.last_error,
                )
            finally:
                self._set_agent_activity(
                    runtime.spec.managed_agent_id,
                    None,
                    runtime.spec.room_id,
                )
                self._turn_slots.release()
                self._flush_logs(runtime.spec.managed_agent_id)

        return on_wake

    def _harness_session_state(self, runtime: ManagedRuntime) -> tuple[str, bool]:
        record = get_managed_seat(runtime.spec.managed_agent_id)
        meta = dict(record.metadata) if record else {}
        session_id = valid_harness_session_id(
            str(meta.get("harness_session_id") or "")
        ) or str(uuid.uuid4())
        resume = bool(meta.get("harness_session_used")) and bool(
            valid_harness_session_id(str(meta.get("harness_session_id") or ""))
        )
        if not valid_harness_session_id(str(meta.get("harness_session_id") or "")):
            self._write_harness_session(runtime, session_id, used=False)
        return session_id, resume

    def _write_harness_session(
        self,
        runtime: ManagedRuntime,
        session_id: str,
        *,
        used: bool,
    ) -> None:
        record = get_managed_seat(runtime.spec.managed_agent_id)
        if not record:
            return
        record.metadata = {
            **dict(record.metadata),
            "harness_session_id": session_id,
            "harness_session_used": used,
        }
        record.updated_at = _utcnow_iso()
        upsert_seat(record)

    def _invoke_harness_turn(
        self,
        runtime: ManagedRuntime,
        events: list[dict[str, Any]],
    ) -> Any:
        session_id, resume = self._harness_session_state(runtime)
        history = self._recent_room_messages(runtime)
        context = InvocationContext(
            harness=runtime.spec.harness,
            agent_name=runtime.spec.name,
            room_id=runtime.spec.room_id,
            participant_id=runtime.participant_id,
            hub_url=runtime.hub_url,
            prompt=self._managed_reply_prompt(
                runtime,
                events,
                history=history,
                resume=resume,
            ),
            session_id=session_id,
            resume_session=resume,
        )
        self._log(
            runtime.spec.managed_agent_id,
            "info",
            (
                f"resuming harness session {session_id[:8]}…"
                if resume
                else f"starting harness session {session_id[:8]}…"
            ),
        )
        try:
            result = runtime.adapter.invoke(
                context,
                cancel_event=runtime.cancel_event,
                emit_log=lambda line: self._log(
                    runtime.spec.managed_agent_id,
                    "info",
                    line,
                ),
            )
        except AdapterError:
            if not resume or runtime.cancel_event.is_set():
                raise
            session_id = str(uuid.uuid4())
            self._write_harness_session(runtime, session_id, used=False)
            self._log(
                runtime.spec.managed_agent_id,
                "warning",
                "harness session was not resumable; starting a new one",
            )
            result = runtime.adapter.invoke(
                InvocationContext(
                    harness=runtime.spec.harness,
                    agent_name=runtime.spec.name,
                    room_id=runtime.spec.room_id,
                    participant_id=runtime.participant_id,
                    hub_url=runtime.hub_url,
                    prompt=self._managed_reply_prompt(
                        runtime,
                        events,
                        history=self._recent_room_messages(runtime),
                        resume=False,
                    ),
                    session_id=session_id,
                    resume_session=False,
                ),
                cancel_event=runtime.cancel_event,
                emit_log=lambda line: self._log(
                    runtime.spec.managed_agent_id,
                    "info",
                    line,
                ),
            )
        if not result.cancelled:
            self._write_harness_session(runtime, session_id, used=True)
        return result

    def _recent_room_messages(self, runtime: ManagedRuntime) -> list[dict[str, Any]]:
        try:
            payload = self._hub_request(
                "GET",
                runtime.hub_url,
                f"/v1/rooms/{runtime.spec.room_id}/messages",
                runtime.agent_token,
                None,
                {"limit": 24},
            )
        except Exception as exc:
            self._log(
                runtime.spec.managed_agent_id,
                "warning",
                f"could not load room history: {self._sanitize(str(exc))}",
            )
            return []
        rows = payload.get("messages") if isinstance(payload, Mapping) else None
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    @staticmethod
    def _format_room_transcript(rows: list[dict[str, Any]]) -> str:
        from opengateway.delivery import format_recent_room_transcript

        return format_recent_room_transcript(rows)

    @staticmethod
    def _managed_reply_prompt(
        runtime: ManagedRuntime,
        events: list[dict[str, Any]],
        history: list[dict[str, Any]] | None = None,
        resume: bool = False,
    ) -> str:
        from opengateway.im import format_messages_for_prompt

        inbound = format_messages_for_prompt(events)
        transcript = RunnerService._format_room_transcript(history or [])
        session_note = (
            "Your Claude/Grok harness session continues from prior turns."
            if resume
            else "This is a new harness session for this seat."
        )
        return f"""You are {runtime.spec.name}, a managed {runtime.spec.harness} agent.
{session_note}
This is one shared OpenGateway room. Other agents' public posts appear in the transcript below.

Recent conversation (oldest first):
{transcript}

The latest message explicitly addressed to you:
{inbound}

Reply to the latest message using the transcript for room context.
If a peer already answered the same ask, coordinate with @TheirName — do not fork a second plan to the human.
@Name is the A2A handoff (there is no separate a2a tool on this path).
Post one concise reply, then stop until you are addressed again.
Return only the reply text for the room.
Do not call OpenGateway, MCP, HTTP, drain_inbox, or wait_for_messages.
The managed runner will deliver your output exactly once.
If the inbound message is empty, system traffic, or only an acknowledgement, return no text."""

    @staticmethod
    def _dm_reply_target(
        runtime: ManagedRuntime,
        events: list[dict[str, Any]],
    ) -> str:
        """When the wake was a private DM to this seat, reply in-thread."""
        seat = runtime.participant_id
        for event in reversed(events):
            message = (
                event.get("message")
                if isinstance(event.get("message"), dict)
                else event
            )
            if not isinstance(message, dict):
                continue
            to_id = message.get("to_participant_id")
            from_id = message.get("from_participant_id")
            if to_id and str(to_id) == seat and from_id:
                return str(from_id)
        return ""

    @staticmethod
    def _reply_metadata(
        runtime: ManagedRuntime,
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        event = events[-1] if events else {}
        message = (
            event.get("message")
            if isinstance(event.get("message"), dict)
            else {}
        )
        source_meta = (
            message.get("metadata")
            if isinstance(message.get("metadata"), dict)
            else {}
        )
        delivery_id = str(
            event.get("delivery_id")
            or source_meta.get("delivery_id")
            or message.get("id")
            or ""
        )
        chain_id = str(
            source_meta.get("chain_id")
            or source_meta.get("delivery_id")
            or source_meta.get("source_message_id")
            or message.get("id")
            or delivery_id
        )
        try:
            agent_hop = int(source_meta.get("agent_hop") or 0) + 1
        except (TypeError, ValueError):
            agent_hop = 1
        return {
            "managed_agent_id": runtime.spec.managed_agent_id,
            "in_reply_to_delivery": delivery_id,
            "chain_id": chain_id,
            "agent_hop": agent_hop,
        }

    def _set_agent_activity(
        self,
        managed_agent_id: str,
        activity: str | None,
        room_id: str | None = None,
    ) -> None:
        with self._lock:
            if not room_id:
                self._agent_activity_by_room.pop(managed_agent_id, None)
                return
            rooms = self._agent_activity_by_room.setdefault(
                managed_agent_id, {}
            )
            if activity:
                rooms[room_id] = activity
            else:
                rooms.pop(room_id, None)
                if not rooms:
                    self._agent_activity_by_room.pop(managed_agent_id, None)

    def _persist_runtime_status(
        self,
        runtime: ManagedRuntime,
        status: str,
        *,
        error: str = "",
    ) -> None:
        record = get_managed_seat(runtime.spec.managed_agent_id)
        if not record:
            return
        record.runtime_status = status
        record.updated_at = _utcnow_iso()
        metadata = dict(record.metadata)
        if error:
            metadata["last_error"] = self._sanitize(error)
        else:
            metadata.pop("last_error", None)
        record.metadata = metadata
        upsert_seat(record)

    def _heartbeat(self) -> None:
        statuses: dict[str, str] = {}
        for record in load_registry():
            if record.managed_agent_id and (
                not record.runner_id or record.runner_id == self.config.runner_id
            ):
                status = record.runtime_status or "stopped"
                if status not in {
                    "starting",
                    "running",
                    "stopping",
                    "stopped",
                    "restarting",
                    "deleting",
                    "deleted",
                    "error",
                }:
                    status = "error"
                statuses[record.managed_agent_id] = status
        with self._lock:
            for managed_agent_id, rooms in self._runtimes.items():
                if any(runtime.status == "running" for runtime in rooms.values()):
                    statuses[managed_agent_id] = "running"
                elif rooms:
                    statuses[managed_agent_id] = next(iter(rooms.values())).status
        now = time.monotonic()
        if self._capabilities is None or now - self._capabilities_at >= 60.0:
            self._capabilities = {
                **runner_capabilities(),
                "embedded": self.config.embedded,
                "max_concurrency": self._max_concurrency,
            }
            self._capabilities_at = now
        with self._lock:
            by_room = {
                agent_id: dict(rooms)
                for agent_id, rooms in self._agent_activity_by_room.items()
            }
        legacy_activity: dict[str, str] = {}
        for agent_id, rooms in by_room.items():
            if any((v or "").strip() for v in rooms.values()):
                legacy_activity[agent_id] = "thinking"
        self.api.heartbeat(
            {
                "version": PACKAGE_VERSION,
                "capabilities": self._capabilities,
                "agent_statuses": statuses,
                "agent_activity": legacy_activity,
                "agent_activity_by_room": by_room,
            }
        )
        for managed_agent_id in list(self._logs):
            self._flush_logs(managed_agent_id)
        self._write_status("running")

    def _flush_logs(self, managed_agent_id: str) -> None:
        log = self._logs.get(managed_agent_id)
        if log is None:
            return
        entries = log.drain(50)
        if not entries:
            return
        sent = 0
        try:
            for entry in entries:
                self.api.post_log(
                    managed_agent_id,
                    entry.level,
                    self._sanitize(entry.message),
                )
                sent += 1
        except Exception:
            log.restore_front(entries[sent:])

    def _log(self, managed_agent_id: str, level: str, message: str) -> None:
        if not managed_agent_id:
            return
        safe = self._sanitize(message)
        with self._lock:
            log = self._logs.setdefault(
                managed_agent_id,
                BoundedLog(max_entries=self._log_limit),
            )
        log.append(level, safe)

    def _sanitize(self, value: str) -> str:
        text = str(value or "")
        for secret in sorted(self._secrets, key=len, reverse=True):
            if secret and len(secret) >= 4:
                text = text.replace(secret, "[REDACTED]")
        text = re.sub(
            r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+",
            r"\1[REDACTED]",
            text,
        )
        text = re.sub(
            r'(?i)(["\']?(?:agent_)?(?:auth_)?token["\']?\s*[:=]\s*["\']?)[^"\'\s,}]+',
            r"\1[REDACTED]",
            text,
        )
        text = re.sub(
            r"(?i)(\b[A-Z0-9_]*(?:API_KEY|TOKEN|PASSWORD|SECRET)"
            r"\s*[:=]\s*)[^\s,;]+",
            r"\1[REDACTED]",
            text,
        )
        return text

    def _sanitize_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._sanitize(value)
        if isinstance(value, Mapping):
            return {
                str(key): self._sanitize_value(item)
                for key, item in value.items()
                if str(key).lower()
                not in {"token", "auth_token", "agent_token", "runner_token"}
            }
        if isinstance(value, list):
            return [self._sanitize_value(item) for item in value]
        return value

    def _remember_secret(self, token: str) -> None:
        if token:
            self._secrets.add(token)

    def _validate_spec(self, spec: ManagedAgentSpec) -> None:
        if not spec.managed_agent_id:
            raise RunnerError("managed_agent_id is required")
        if not spec.name:
            raise RunnerError("agent name is required")
        if not spec.room_id:
            raise RunnerError("room_id is required")
        if spec.runner_id and spec.runner_id != self.config.runner_id:
            raise RunnerError("Managed agent job targets a different runner")
        # get_adapter is the allowlist.  Do not inspect or honor any other job
        # fields, even if a newer/malicious hub sends them.
        self._canonical_harness(spec.harness)

    @staticmethod
    def _canonical_harness(harness: str) -> str:
        key = (harness or "").strip().lower()
        aliases = {
            "claude": "claude-code",
            "claude_code": "claude-code",
            "anthropic": "claude-code",
            "xai": "grok",
            "x-ai": "grok",
        }
        value = aliases.get(key, key)
        if value not in {"claude-code", "grok", "hermes"}:
            raise AdapterUnavailable(
                "Unsupported managed harness. Choose claude-code, grok, or hermes."
            )
        return value

    def _mark_record_error(self, spec: ManagedAgentSpec, error: str) -> None:
        record = get_managed_seat(spec.managed_agent_id, spec.room_id)
        if not record:
            return
        record.runtime_status = "error"
        record.desired_state = "stopped"
        record.updated_at = _utcnow_iso()
        record.metadata = {**record.metadata, "last_error": error}
        upsert_seat(record)

    def status(self) -> dict[str, Any]:
        with self._lock:
            runtimes = [
                runtime.public_dict()
                for rooms in self._runtimes.values()
                for runtime in rooms.values()
            ]
        return {
            **self.config.public_dict(),
            "pid": os.getpid(),
            "state": "stopping" if self._stop_event.is_set() else "running",
            "started_at": self._started_at or None,
            "last_error": self._sanitize(self._last_error) or None,
            "agents": runtimes,
        }

    def _write_status(self, state: str) -> None:
        payload = self.status()
        payload["state"] = state
        _atomic_private_json(runner_status_path(), self._sanitize_value(payload))


def runner_status(*, include_probes: bool = True) -> dict[str, Any]:
    config = load_runner_config()
    if config is None:
        config, _embedded_token = _load_embedded_connection()
    if not config:
        return {
            "connected": False,
            "state": "disconnected",
            "config_path": str(runner_config_path()),
        }
    status: dict[str, Any] = config.public_dict()
    path = runner_status_path()
    if path.is_file():
        try:
            os.chmod(path, 0o600)
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and (
                not raw.get("runner_id")
                or raw.get("runner_id") == config.runner_id
            ):
                status.update(raw)
        except (OSError, ValueError, TypeError):
            pass
    pid = int(status.get("pid") or 0)
    status["process_alive"] = _pid_alive(pid)
    if status.get("state") == "running" and not status["process_alive"]:
        status["state"] = "stale"
    if include_probes:
        status["harnesses"] = [
            {
                "harness": probe.harness,
                "status": probe.status,
                "available": probe.available,
                "authenticated": probe.authenticated,
                "detail": probe.detail,
                "setup_instructions": probe.setup_instructions,
            }
            for probe in probe_all()
        ]
    return status


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def start_connected_runner() -> int:
    config = load_runner_config()
    token = load_runner_token()
    if config is None or not token:
        raise RunnerNotConnected(
            "Runner is not connected. Run `opengateways runner connect "
            "--url HUB --code CODE --name NAME` first."
        )
    service = RunnerService(config, token)
    return service.run()


def disconnect_runner() -> dict[str, Any]:
    config = load_runner_config()
    token = load_runner_token()
    if config is None:
        return {"connected": False, "state": "disconnected"}
    if config is not None and token:
        api = RunnerAPI(config, token)
        try:
            api.disconnect()
        except httpx.HTTPStatusError as exc:
            # A revoked or removed identity is already disconnected remotely.
            if exc.response.status_code not in {401, 403, 404}:
                raise
        finally:
            api.close()

    for record in load_registry():
        if (
            not record.managed_agent_id
            or record.runner_id != config.runner_id
        ):
            continue
        if record.participant_id:
            clear_cursor(record.room_id, record.participant_id)
            release_seat_lock(record.room_id, record.participant_id, force=True)
        remove_seat(managed_agent_id=record.managed_agent_id)
    clear_runner_credentials(include_agents=False)
    runner_config_path().unlink(missing_ok=True)
    status_path = runner_status_path()
    remove_status = True
    if status_path.is_file():
        try:
            raw_status = json.loads(status_path.read_text(encoding="utf-8"))
            remove_status = (
                not isinstance(raw_status, dict)
                or raw_status.get("runner_id") == config.runner_id
            )
        except (OSError, ValueError, TypeError):
            remove_status = True
    if remove_status:
        status_path.unlink(missing_ok=True)
    return {"connected": False, "state": "disconnected"}


_RAILWAY_ENV_KEYS = (
    "RAILWAY_ENVIRONMENT",
    "RAILWAY_ENVIRONMENT_ID",
    "RAILWAY_PROJECT_ID",
    "RAILWAY_SERVICE_ID",
)
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _hosted_elsewhere(env: Mapping[str, str]) -> bool:
    """True when this process must not launch a local harness worker."""
    if any(env.get(name) for name in _RAILWAY_ENV_KEYS):
        return True
    if env.get("OPENGATEWAY_RUNNER_SERVICE"):
        return True
    if Path("/.dockerenv").is_file():
        return True
    return False


def embedded_runner_allowed(
    gateway_config: Any,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    env = environ if environ is not None else os.environ
    if _hosted_elsewhere(env):
        return False
    mode = getattr(gateway_config, "mode", "")
    mode_value = getattr(mode, "value", mode)
    host = str(getattr(gateway_config, "host", "") or "")
    network = str(getattr(gateway_config, "network", "") or "")
    advertised = str(getattr(gateway_config, "base_url", "") or "")
    advertised_host = (
        (urlparse(advertised).hostname or "").lower()
        if advertised
        else host.lower()
    )
    return (
        str(mode_value).lower() == "internal"
        and host in _LOOPBACK_HOSTS
        and network == "loopback"
        and advertised_host in _LOOPBACK_HOSTS
    )


def host_runner_allowed(
    gateway_config: Any,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """True when `serve` should install the OS runner on this machine.

    Tailscale Serve and Funnel stay bound to localhost, so the embedded worker
    stays off, but the harnesses still run here. The same is true for a LAN
    bind on the host. Railway, Docker, and the runner service itself do not
    install another worker.
    """
    env = environ if environ is not None else os.environ
    if _hosted_elsewhere(env):
        return False
    if embedded_runner_allowed(gateway_config, environ=env):
        return False
    host = str(getattr(gateway_config, "host", "") or "").lower()
    network = str(getattr(gateway_config, "network", "") or "").lower()
    if host in _LOOPBACK_HOSTS:
        return True
    return host in {"0.0.0.0", "::"} and network == "lan"


def schedule_local_runner_service(
    *,
    hub_url: str,
    auth_token: str,
    name: str = "Local Runner",
    log: Callable[[str], None] | None = None,
    wait_seconds: float = 45.0,
) -> threading.Thread:
    """Pair and install the background runner after this hub accepts /ping."""

    def _log(message: str) -> None:
        if log is not None:
            log(message)

    def _wait_and_ensure() -> None:
        deadline = time.monotonic() + max(1.0, wait_seconds)
        ping_url = f"{hub_url.rstrip('/')}/ping"
        while time.monotonic() < deadline:
            try:
                response = httpx.get(ping_url, timeout=2.0)
                if response.status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.4)
        else:
            _log(
                "Runner: hub did not answer /ping; "
                "run `opengateways runner ensure` after it is up"
            )
            return
        try:
            result = ensure_local_runner(
                url=hub_url,
                auth_token=auth_token,
                name=name,
            )
        except Exception as exc:
            _log(f"Runner: background service was not installed: {exc}")
            return
        _log(
            "Runner: "
            f"{result.get('status', 'ok')} "
            f"({result.get('runner_id', '')}) · {hub_url}"
        )

    thread = threading.Thread(
        target=_wait_and_ensure,
        name="opengateway-host-runner",
        daemon=True,
    )
    thread.start()
    return thread


def start_embedded_runner(
    *,
    hub_url: str,
    auth_token: str = "",
    name: str = "Local Runner",
    runner_id: str = RUNNER_ID_LOCAL,
) -> threading.Thread:
    """Start one daemon runner for a loopback hub.

    The worker self-pairs over loopback because runner transport routes require
    a runner-scoped token even when normal hub auth is disabled. Public/Railway
    callers must use the explicit connect/start flow and never call this helper.
    """
    global _RUNNER_SERVICE, _RUNNER_THREAD
    with _RUNNER_THREAD_LOCK:
        if _RUNNER_THREAD and _RUNNER_THREAD.is_alive():
            return _RUNNER_THREAD

        def run_embedded() -> None:
            global _RUNNER_SERVICE
            base = hub_url.rstrip("/")
            while True:
                config, token = _load_embedded_connection()
                if config and config.hub_url != base:
                    config, token = None, ""
                if config and token:
                    api = RunnerAPI(config, token)
                    try:
                        api.heartbeat(
                            {
                                "version": PACKAGE_VERSION,
                                "capabilities": {
                                    **runner_capabilities(),
                                    "embedded": True,
                                },
                                "agent_statuses": {},
                            }
                        )
                    except Exception as exc:
                        config, token = None, ""
                        if RunnerService._is_terminal_api_error(exc):
                            for path in (
                                embedded_runner_config_path(),
                                embedded_runner_credential_path(),
                            ):
                                try:
                                    path.unlink(missing_ok=True)
                                except OSError:
                                    pass
                    finally:
                        api.close()
                if not config or not token:
                    headers = (
                        {"Authorization": f"Bearer {auth_token}"}
                        if auth_token
                        else {}
                    )
                    try:
                        with httpx.Client(
                            base_url=base,
                            headers=headers,
                            timeout=10.0,
                        ) as client:
                            pair_response = client.post(
                                "/v1/runners/pair",
                                json={"name": name},
                            )
                            pair_response.raise_for_status()
                            pair_data = _response_json(pair_response)
                            code = _first_text(
                                pair_data,
                                "code",
                                "pairing_code",
                                "pairingCode",
                            )
                            if not code:
                                raise RunnerError(
                                    "Local runner pair response omitted its code"
                                )
                            redeemed = RunnerAPI.redeem(
                                base,
                                code,
                                name,
                                client=client,
                                capabilities={
                                    **runner_capabilities(),
                                    "embedded": True,
                                },
                            )
                        config = RunnerConfig(
                            runner_id=redeemed.runner_id or runner_id,
                            hub_url=redeemed.hub_url or base,
                            name=redeemed.name or name,
                            embedded=True,
                        )
                        token = redeemed.runner_token
                        _save_embedded_connection(config, token)
                    except Exception:
                        time.sleep(1.0)
                        continue
                service = RunnerService(config, token)
                _RUNNER_SERVICE = service
                try:
                    service.run()
                except RunnerError:
                    time.sleep(0.5)
                    continue
                if service._terminal_state == "revoked":
                    for path in (
                        embedded_runner_config_path(),
                        embedded_runner_credential_path(),
                    ):
                        try:
                            path.unlink(missing_ok=True)
                        except OSError:
                            pass
                    _RUNNER_SERVICE = None
                    time.sleep(0.2)
                    continue
                return

        thread = threading.Thread(
            target=run_embedded,
            name="opengateway-embedded-runner",
            daemon=True,
        )
        _RUNNER_THREAD = thread
        thread.start()
        return thread
