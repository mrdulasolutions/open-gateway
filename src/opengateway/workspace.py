"""Path-addressed room workspace — shared collab FS on R2/disk.

Agents use path tools instead of dumping large blobs into chat. Storage reuses
file_store (R2 via hub Worker / disk fallback). Index lives in store meta so
list is reliable without R2 LIST permissions.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
from pathlib import Path
from typing import Any, Optional

from opengateway.file_store import (
    FILE_MAX_BYTES,
    delete_blob,
    files_base,
    get_blob_bytes,
    master_headers,
    put_blob,
    safe_filename,
    tenant_slug,
)
from opengateway.models import utcnow

WORKSPACE_INDEX_PREFIX = "workspace_index:"
MAX_PATH_LEN = 400
MAX_ENTRIES_PER_ROOM = 5_000


def normalize_path(path: str) -> str:
    """Normalize relative workspace path; reject traversal."""
    raw = (path or "").strip().replace("\\", "/")
    raw = raw.lstrip("/")
    if not raw:
        raise ValueError("path is required")
    if len(raw) > MAX_PATH_LEN:
        raise ValueError(f"path too long (max {MAX_PATH_LEN})")
    parts: list[str] = []
    for seg in raw.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            raise ValueError("path traversal not allowed")
        safe = safe_filename(seg)
        if not safe or safe in {".", ".."}:
            raise ValueError(f"invalid path segment: {seg!r}")
        parts.append(safe)
    if not parts:
        raise ValueError("path is required")
    return "/".join(parts)


def workspace_r2_key(room_id: str, path: str) -> str:
    tenant = tenant_slug()
    norm = normalize_path(path)
    return f"tenants/{tenant}/rooms/{room_id}/workspace/{norm}"


def path_file_id(path: str) -> str:
    return "ws_" + hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]


def _index_key(room_id: str) -> str:
    return f"{WORKSPACE_INDEX_PREFIX}{room_id}"


def _load_index(store: Any, room_id: str) -> dict[str, dict[str, Any]]:
    raw = store.get_meta(_index_key(room_id))
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def _save_index(store: Any, room_id: str, index: dict[str, dict[str, Any]]) -> None:
    store.set_meta(_index_key(room_id), json.dumps(index, separators=(",", ":")))


def list_workspace(
    store: Any,
    room_id: str,
    *,
    prefix: str = "",
) -> list[dict[str, Any]]:
    index = _load_index(store, room_id)
    pref = (prefix or "").strip().replace("\\", "/").lstrip("/")
    rows: list[dict[str, Any]] = []
    for path, meta in index.items():
        if pref and not (path == pref or path.startswith(pref.rstrip("/") + "/")):
            continue
        rows.append(
            {
                "path": path,
                "bytes": meta.get("bytes"),
                "content_type": meta.get("content_type"),
                "sha256": meta.get("sha256"),
                "updated_at": meta.get("updated_at"),
                "updated_by": meta.get("updated_by"),
                "storage": meta.get("storage"),
            }
        )
    rows.sort(key=lambda r: r["path"])
    return rows


def put_workspace_file(
    store: Any,
    *,
    room_id: str,
    path: str,
    raw: bytes,
    content_type: Optional[str] = None,
    updated_by: str = "",
    files_root: Path,
) -> dict[str, Any]:
    if len(raw) > FILE_MAX_BYTES:
        raise ValueError(f"File too large (max {FILE_MAX_BYTES // (1024 * 1024)}MB)")
    norm = normalize_path(path)
    index = _load_index(store, room_id)
    if norm not in index and len(index) >= MAX_ENTRIES_PER_ROOM:
        raise ValueError(f"Workspace full (max {MAX_ENTRIES_PER_ROOM} files per room)")

    filename = Path(norm).name
    file_id = path_file_id(norm)
    ct = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    ws_key = workspace_r2_key(room_id, norm)

    blob = put_blob(
        room_id=room_id,
        file_id=file_id,
        filename=filename,
        raw=raw,
        content_type=ct,
        files_root=files_root / "workspace",
    )

    # Prefer path-addressed R2 key for workspace when R2 is available
    if blob.storage == "r2" and files_base():
        try:
            import httpx

            base = files_base()
            with httpx.Client(timeout=12.0) as client:
                r = client.put(
                    f"{base}/__files/{ws_key}",
                    content=raw,
                    headers={
                        **master_headers(),
                        "Content-Type": ct,
                    },
                )
                if r.status_code < 300:
                    if blob.r2_key and blob.r2_key != ws_key:
                        try:
                            client.delete(
                                f"{base}/__files/{blob.r2_key}",
                                headers=master_headers(),
                            )
                        except Exception:
                            pass
                    blob.r2_key = ws_key
        except Exception:
            pass

    now = utcnow().isoformat()
    inline = blob.inline_content
    if inline and len(inline) > 80_000:
        inline = None
    entry = {
        "path": norm,
        "bytes": blob.bytes,
        "content_type": ct,
        "sha256": blob.sha256,
        "storage": blob.storage,
        "r2_key": blob.r2_key,
        "disk_path": blob.disk_path,
        "inline_content": inline,
        "content_encoding": blob.content_encoding if inline else "plain",
        "updated_at": now,
        "updated_by": updated_by or "",
        "file_id": file_id,
    }
    index[norm] = entry
    _save_index(store, room_id, index)
    return {
        "path": norm,
        "bytes": entry["bytes"],
        "content_type": ct,
        "sha256": entry["sha256"],
        "storage": entry["storage"],
        "updated_at": now,
        "updated_by": entry["updated_by"],
        "content_url": f"/v1/rooms/{room_id}/workspace/{norm}",
    }


def get_workspace_entry(store: Any, room_id: str, path: str) -> Optional[dict[str, Any]]:
    norm = normalize_path(path)
    return _load_index(store, room_id).get(norm)


def read_workspace_bytes(
    store: Any, room_id: str, path: str
) -> Optional[tuple[bytes, dict[str, Any]]]:
    entry = get_workspace_entry(store, room_id, path)
    if not entry:
        return None
    raw = get_blob_bytes(
        r2_key=entry.get("r2_key"),
        disk_path=entry.get("disk_path"),
        inline_content=entry.get("inline_content"),
        content_encoding=entry.get("content_encoding") or "plain",
    )
    if raw is None:
        raw = get_blob_bytes(
            r2_key=workspace_r2_key(room_id, entry["path"]),
            disk_path=entry.get("disk_path"),
            inline_content=None,
            content_encoding="plain",
        )
    if raw is None:
        return None
    return raw, entry


def delete_workspace_file(store: Any, room_id: str, path: str) -> bool:
    norm = normalize_path(path)
    index = _load_index(store, room_id)
    entry = index.pop(norm, None)
    if not entry:
        return False
    delete_blob(r2_key=entry.get("r2_key"), disk_path=entry.get("disk_path"))
    try:
        delete_blob(r2_key=workspace_r2_key(room_id, norm), disk_path=None)
    except Exception:
        pass
    _save_index(store, room_id, index)
    return True
