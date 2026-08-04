"""Durable room file storage — R2 via hub Worker /__files, with disk/inline fallback.

Agents and UI share the same backend: Artifact metadata in SQLite; multi-MB bytes
in R2 (long-term). Container disk is a cache only.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx

FILE_MAX_BYTES = 25 * 1024 * 1024
FILE_INLINE_TEXT = 500_000
FILE_INLINE_BINARY = 2_000_000  # non-image binary dual-write ceiling
# Phone photos must NOT become multi-MB base64 in SQLite / chat JSON
FILE_INLINE_IMAGE = 200_000
R2_PUT_TIMEOUT = 12.0  # never hang upload path for minutes


def _safe_filename(name: str) -> str:
    base = Path(name or "upload.bin").name
    base = re.sub(r"[^\w.\- ()[\]]+", "_", base).strip("._") or "upload.bin"
    return base[:180]


def _tenant_slug() -> str:
    return (
        os.environ.get("OPENGATEWAY_TENANT_SLUG")
        or os.environ.get("OPENGATEWAY_TENANT")
        or "local"
    ).strip().lower()


def _files_base() -> str:
    return (
        os.environ.get("OPENGATEWAY_FILES_URL")
        or os.environ.get("OPENGATEWAY_PUBLIC_URL")
        or ""
    ).rstrip("/")


def _files_backend() -> str:
    """r2 | disk — r2 when Worker binding + URL available."""
    mode = (os.environ.get("OPENGATEWAY_FILES_BACKEND") or "auto").strip().lower()
    if mode in {"r2", "disk"}:
        return mode
    if _files_base() and os.environ.get("OPENGATEWAY_AUTH_TOKEN"):
        return "r2"
    return "disk"


def r2_key(room_id: str, file_id: str, filename: str) -> str:
    tenant = _tenant_slug()
    safe = _safe_filename(filename)
    return f"tenants/{tenant}/rooms/{room_id}/files/{file_id}/{safe}"


def _master_headers() -> dict[str, str]:
    tok = (os.environ.get("OPENGATEWAY_AUTH_TOKEN") or "").strip()
    h = {"Authorization": f"Bearer {tok}"} if tok else {}
    return h


@dataclass
class StoredBlob:
    storage: str  # r2 | inline | disk
    r2_key: Optional[str]
    disk_path: Optional[str]
    inline_content: Optional[str]
    content_encoding: str  # plain | base64
    sha256: str
    bytes: int
    durable: bool


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def put_blob(
    *,
    room_id: str,
    file_id: str,
    filename: str,
    raw: bytes,
    content_type: str,
    files_root: Path,
) -> StoredBlob:
    """Persist bytes: R2 (preferred) + optional inline for small files."""
    if len(raw) > FILE_MAX_BYTES:
        raise ValueError(f"File too large (max {FILE_MAX_BYTES // (1024 * 1024)}MB)")

    digest = _sha256(raw)
    safe = _safe_filename(filename)
    key = r2_key(room_id, file_id, safe)
    backend = _files_backend()

    # Disk cache (always try — helps local/dev and hot path)
    dest_dir = files_root / room_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{file_id}_{safe}"
    dest.write_bytes(raw)

    # Inline only when small enough for chat/SQLite (never multi-MB photos)
    inline: Optional[str] = None
    encoding = "plain"
    is_image = (content_type or "").startswith("image/")
    if content_type.startswith("text/") and len(raw) <= FILE_INLINE_TEXT:
        try:
            inline = raw.decode("utf-8")
        except UnicodeDecodeError:
            inline = base64.b64encode(raw).decode("ascii")
            encoding = "base64"
    elif is_image and len(raw) <= FILE_INLINE_IMAGE:
        inline = base64.b64encode(raw).decode("ascii")
        encoding = "base64"
    elif not is_image and len(raw) <= FILE_INLINE_BINARY:
        inline = base64.b64encode(raw).decode("ascii")
        encoding = "base64"

    # R2: short timeout — disk already written; don't block API on R2 outage
    r2_ok = False
    if backend == "r2" and _files_base():
        try:
            url = f"{_files_base()}/__files/{key}"
            with httpx.Client(timeout=R2_PUT_TIMEOUT) as client:
                r = client.put(
                    url,
                    content=raw,
                    headers={
                        **_master_headers(),
                        "Content-Type": content_type or "application/octet-stream",
                    },
                )
                r2_ok = r.status_code < 300
        except Exception:
            r2_ok = False

    stored_key: Optional[str] = None
    if r2_ok:
        storage = "r2"
        durable = True
        stored_key = key
    elif inline is not None:
        storage = "inline"
        durable = True
    else:
        storage = "disk"
        durable = False  # ephemeral until R2 available

    return StoredBlob(
        storage=storage,
        r2_key=stored_key,
        disk_path=str(dest),
        inline_content=inline,
        content_encoding=encoding if inline else "plain",
        sha256=digest,
        bytes=len(raw),
        durable=durable,
    )


def get_blob_bytes(
    *,
    r2_key: Optional[str],
    disk_path: Optional[str],
    inline_content: Optional[str],
    content_encoding: str = "plain",
) -> Optional[bytes]:
    """Load file bytes: disk cache → R2 → inline."""
    if disk_path and Path(disk_path).is_file():
        return Path(disk_path).read_bytes()
    if r2_key and _files_base() and _files_backend() == "r2":
        try:
            url = f"{_files_base()}/__files/{r2_key}"
            with httpx.Client(timeout=120.0) as client:
                r = client.get(url, headers=_master_headers())
                if r.status_code == 200:
                    raw = r.content
                    # Warm disk cache best-effort
                    return raw
        except Exception:
            pass
    if inline_content:
        if content_encoding == "base64":
            return base64.b64decode(inline_content)
        return inline_content.encode("utf-8")
    return None


def delete_blob(*, r2_key: Optional[str], disk_path: Optional[str]) -> None:
    if disk_path:
        try:
            Path(disk_path).unlink(missing_ok=True)  # type: ignore[arg-type]
        except TypeError:
            p = Path(disk_path)
            if p.is_file():
                p.unlink()
        except Exception:
            pass
    if r2_key and _files_base():
        try:
            url = f"{_files_base()}/__files/{r2_key}"
            with httpx.Client(timeout=30.0) as client:
                client.delete(url, headers=_master_headers())
        except Exception:
            pass


def category_for(content_type: str) -> str:
    ct = content_type or ""
    if ct.startswith("image/"):
        return "image"
    if ct.startswith("audio/"):
        return "audio"
    if ct.startswith("video/"):
        return "video"
    if ct.startswith("text/") or ct in {
        "application/json",
        "application/xml",
        "application/javascript",
    }:
        return "text"
    if ct == "application/pdf":
        return "pdf"
    return "file"


def artifact_meta(blob: StoredBlob, content_type: str) -> dict[str, Any]:
    return {
        "bytes": blob.bytes,
        "path": blob.disk_path,
        "storage": blob.storage,
        "r2_key": blob.r2_key,
        "sha256": blob.sha256,
        "tenant_slug": _tenant_slug(),
        "durable": blob.durable,
        "durable_inline": bool(blob.inline_content),
        "category": category_for(content_type),
    }
