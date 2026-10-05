"""Private local seat metadata, credentials, and process ownership.

Seat metadata is deliberately separate from credentials.  The metadata file is
safe to use for status/restore operations; bearer tokens live only in the
runner credential store and are never included in ``SeatRecord`` repr output.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def registry_path() -> Path:
    return Path.home() / ".opengateway" / "seats.json"


def runner_directory() -> Path:
    return Path.home() / ".opengateway" / "runner"


def credential_store_path() -> Path:
    return runner_directory() / "credentials.json"


@dataclass
class SeatRecord:
    room_id: str
    participant_id: str = ""
    name: str = "agent"
    harness: str = "mcp"
    wake: str = "harness"
    base_url: str = ""
    # Backwards-compatible in-memory field. save_registry() never serializes it.
    auth_token: str = field(default="", repr=False, compare=False)
    label: str = ""
    updated_at: str = ""
    managed_agent_id: str = ""
    runner_id: str = ""
    desired_state: str = "running"
    runtime_status: str = "stopped"
    metadata: dict[str, Any] = field(default_factory=dict)


_file_lock = threading.RLock()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _chmod_private(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _atomic_private_json(path: Path, data: Any) -> None:
    """Write JSON without a window where the file has permissive mode bits."""
    _ensure_private_dir(path.parent)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        _chmod_private(path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        _chmod_private(path)
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _seat_credential_key(record: SeatRecord) -> str:
    if record.managed_agent_id:
        return f"managed:{record.managed_agent_id}"
    identity = record.participant_id or record.name
    return f"seat:{record.room_id}:{identity}"


def load_registry() -> list[SeatRecord]:
    path = registry_path()
    raw = _read_json(path, [])
    out: list[SeatRecord] = []
    legacy_tokens: list[tuple[SeatRecord, str]] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict):
            record = SeatRecord(
                **{
                    k: item[k]
                    for k in SeatRecord.__dataclass_fields__
                    if k in item
                }
            )
            # Migrate old registries that stored the token inline.  Keep the
            # token usable for this read, then scrub it from the metadata file.
            if record.auth_token:
                legacy_tokens.append((record, record.auth_token))
            else:
                record.auth_token = load_agent_credential(_seat_credential_key(record))
            out.append(record)
    if legacy_tokens:
        for record, token in legacy_tokens:
            save_agent_credential(_seat_credential_key(record), token)
        save_registry(out)
    return out


def save_registry(seats: list[SeatRecord]) -> None:
    data: list[dict[str, Any]] = []
    for seat in seats:
        item = asdict(seat)
        item.pop("auth_token", None)
        data.append(item)
    with _file_lock:
        _atomic_private_json(registry_path(), data)


def upsert_seat(record: SeatRecord) -> None:
    with _file_lock:
        if record.auth_token:
            save_agent_credential(_seat_credential_key(record), record.auth_token)
        seats = load_registry()
        key = (
            f"managed:{record.managed_agent_id}"
            if record.managed_agent_id
            else f"seat:{record.room_id}:{record.participant_id or record.name}"
        )
        kept: list[SeatRecord] = []
        replaced = False
        for seat in seats:
            seat_key = (
                f"managed:{seat.managed_agent_id}"
                if seat.managed_agent_id
                else f"seat:{seat.room_id}:{seat.participant_id or seat.name}"
            )
            if seat_key == key:
                kept.append(record)
                replaced = True
            else:
                kept.append(seat)
        if not replaced:
            kept.append(record)
        save_registry(kept)


def get_managed_seat(managed_agent_id: str) -> SeatRecord | None:
    return next(
        (
            seat
            for seat in load_registry()
            if seat.managed_agent_id == managed_agent_id
        ),
        None,
    )


def remove_seat(
    room_id: str = "",
    participant_id: str = "",
    *,
    managed_agent_id: str = "",
) -> None:
    with _file_lock:
        seats = load_registry()
        kept: list[SeatRecord] = []
        removed: list[SeatRecord] = []
        for seat in seats:
            match = False
            if managed_agent_id:
                match = seat.managed_agent_id == managed_agent_id
            elif room_id:
                match = seat.room_id == room_id and (
                    not participant_id or seat.participant_id == participant_id
                )
            if match:
                removed.append(seat)
            else:
                kept.append(seat)
        save_registry(kept)
        for seat in removed:
            remove_agent_credential(_seat_credential_key(seat))


def lock_path(room_id: str, participant_id: str) -> Path:
    import hashlib

    raw = f"{room_id}\0{participant_id}".encode("utf-8", errors="replace")
    slug = hashlib.sha256(raw).hexdigest()[:24]
    d = Path.home() / ".opengateway" / "seat-locks"
    _ensure_private_dir(d)
    return d / f"{slug}.lock"


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


def read_seat_lock(room_id: str, participant_id: str) -> dict[str, Any] | None:
    p = lock_path(room_id, participant_id)
    data = _read_json(p, None)
    return data if isinstance(data, dict) else None


def seat_lock_active(room_id: str, participant_id: str) -> bool:
    data = read_seat_lock(room_id, participant_id)
    if not data:
        return False
    try:
        return _pid_alive(int(data.get("pid") or 0))
    except (TypeError, ValueError):
        return False


def try_acquire_seat_lock(
    room_id: str,
    participant_id: str,
    *,
    owner: str = "mcp",
) -> bool:
    """Atomically acquire a PID-aware lock, reclaiming only stale owners."""
    p = lock_path(room_id, participant_id)
    payload = {
        "pid": os.getpid(),
        "owner": owner,
        "room_id": room_id,
        "participant_id": participant_id,
        "created_at": _utcnow_iso(),
    }
    for _ in range(2):
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, sort_keys=True)
                fh.write("\n")
            _chmod_private(p)
            return True
        except FileExistsError:
            current = read_seat_lock(room_id, participant_id) or {}
            try:
                current_pid = int(current.get("pid") or 0)
            except (TypeError, ValueError):
                current_pid = 0
            if current_pid == os.getpid() and current.get("owner") == owner:
                return True
            if _pid_alive(current_pid):
                return False
            try:
                p.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                return False
        except OSError:
            return False
    return False


def try_acquire_mcp_lock(room_id: str, participant_id: str) -> bool:
    """Compatibility wrapper for local MCP-owned seats."""
    return try_acquire_seat_lock(room_id, participant_id, owner="mcp")


def release_seat_lock(
    room_id: str,
    participant_id: str,
    *,
    owner: str | None = None,
    force: bool = False,
) -> None:
    p = lock_path(room_id, participant_id)
    try:
        current = read_seat_lock(room_id, participant_id) or {}
        same_process = int(current.get("pid") or 0) == os.getpid()
        same_owner = owner is None or current.get("owner") == owner
        stale = not _pid_alive(int(current.get("pid") or 0))
        if force or stale or (same_process and same_owner):
            p.unlink(missing_ok=True)
    except (OSError, TypeError, ValueError):
        pass


def release_mcp_lock(room_id: str, participant_id: str) -> None:
    release_seat_lock(room_id, participant_id, owner="mcp")


def _load_credentials() -> dict[str, Any]:
    raw = _read_json(credential_store_path(), {})
    if not isinstance(raw, dict):
        return {"version": 1, "runner_token": "", "agents": {}}
    agents = raw.get("agents")
    if not isinstance(agents, dict):
        agents = {}
    return {
        "version": 1,
        "runner_token": str(raw.get("runner_token") or ""),
        "agents": agents,
    }


def _save_credentials(data: dict[str, Any]) -> None:
    _atomic_private_json(credential_store_path(), data)


def save_runner_token(token: str) -> None:
    with _file_lock:
        data = _load_credentials()
        data["runner_token"] = str(token or "")
        _save_credentials(data)


def load_runner_token() -> str:
    with _file_lock:
        return str(_load_credentials().get("runner_token") or "")


def save_agent_credential(key: str, token: str) -> None:
    if not key or not token:
        return
    with _file_lock:
        data = _load_credentials()
        agents = data.setdefault("agents", {})
        agents[str(key)] = {"token": str(token)}
        _save_credentials(data)


def load_agent_credential(key: str) -> str:
    if not key:
        return ""
    with _file_lock:
        item = (_load_credentials().get("agents") or {}).get(str(key)) or {}
        return str(item.get("token") or "") if isinstance(item, dict) else ""


def remove_agent_credential(key: str) -> None:
    if not key:
        return
    with _file_lock:
        data = _load_credentials()
        agents = data.setdefault("agents", {})
        agents.pop(str(key), None)
        _save_credentials(data)


def clear_runner_credentials(*, include_agents: bool = False) -> None:
    with _file_lock:
        data = _load_credentials()
        data["runner_token"] = ""
        if include_agents:
            data["agents"] = {}
        _save_credentials(data)
