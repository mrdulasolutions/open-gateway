"""Production security helpers — token verification, tenant checks, file paths."""

from __future__ import annotations

import hmac
import os
from pathlib import Path
from typing import Any, Optional

from starlette.requests import Request
from starlette.websockets import WebSocket

from opengateway.api_keys import SCOPE_ADMIN, scopes_allow
from opengateway.config import GatewayConfig


def const_eq(a: str, b: str) -> bool:
    """Constant-time string compare (handles length mismatch safely)."""
    return hmac.compare_digest((a or "").encode("utf-8"), (b or "").encode("utf-8"))


def trust_proxy_headers() -> bool:
    """When true, honor X-Forwarded-For for rate-limit IP (only behind a trusted reverse proxy)."""
    return os.environ.get("OPENGATEWAY_TRUST_PROXY", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def client_ip(request: Request | WebSocket, *, trust_proxy: bool | None = None) -> str:
    use_xff = trust_proxy if trust_proxy is not None else trust_proxy_headers()
    if use_xff:
        xff = None
        if isinstance(request, Request):
            xff = request.headers.get("x-forwarded-for") or request.headers.get(
                "X-Forwarded-For"
            )
        else:
            xff = request.headers.get("x-forwarded-for") or request.headers.get(
                "X-Forwarded-For"
            )
        if xff:
            return xff.split(",")[0].strip() or "unknown"
    if isinstance(request, Request):
        if request.client:
            return request.client.host or "unknown"
    else:
        client = request.client
        if client:
            return client.host or "unknown"
    return "unknown"


def extract_bearer_token(
    *,
    authorization: Optional[str] = None,
    query_token: Optional[str] = None,
    query_access_token: Optional[str] = None,
) -> Optional[str]:
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        return authorization.strip()
    return (query_token or query_access_token or None) or None


def token_from_request(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    return extract_bearer_token(
        authorization=auth,
        query_token=request.query_params.get("token"),
        query_access_token=request.query_params.get("access_token"),
    )


def token_from_websocket(websocket: WebSocket) -> Optional[str]:
    auth = websocket.headers.get("authorization") or websocket.headers.get("Authorization")
    return extract_bearer_token(
        authorization=auth,
        query_token=websocket.query_params.get("token"),
        query_access_token=websocket.query_params.get("access_token"),
    )


async def resolve_auth(
    token: Optional[str],
    *,
    config: GatewayConfig,
    store: Any,
    method: str = "GET",
    path: str = "/",
) -> Optional[dict[str, Any]]:
    """Return auth context dict or None if token invalid.

    Keys: auth_kind, auth_scopes, user?, api_key?, tenant_id?
    """
    if not config.require_auth:
        return {
            "auth_kind": "open",
            "auth_scopes": [SCOPE_ADMIN],
            "tenant_id": None,
            "user": None,
            "api_key": None,
        }

    has_master = bool(config.auth_token)
    if not token:
        return None

    if has_master and const_eq(token, config.auth_token or ""):
        return {
            "auth_kind": "master",
            "auth_scopes": [SCOPE_ADMIN],
            "tenant_id": None,
            "user": None,
            "api_key": None,
        }

    if store is not None and token.startswith("ogs_"):
        try:
            sess = await store.verify_session_token(token)
        except Exception:
            sess = None
        if sess:
            scopes = sess.get("scopes") or ["read", "write"]
            if not scopes_allow(scopes, method, path):
                return {
                    "auth_kind": "session",
                    "auth_scopes": scopes,
                    "tenant_id": sess.get("tenant_id"),
                    "user": sess,
                    "api_key": None,
                    "forbidden": True,
                }
            return {
                "auth_kind": "session",
                "auth_scopes": scopes,
                "tenant_id": sess.get("tenant_id"),
                "user": sess,
                "api_key": None,
            }

    if store is not None:
        try:
            key = await store.verify_api_key(token)
        except Exception:
            key = None
        if key:
            scopes = key.get("scopes") or []
            if not scopes_allow(scopes, method, path):
                return {
                    "auth_kind": "api_key",
                    "auth_scopes": scopes,
                    "tenant_id": (key.get("metadata") or {}).get("tenant_id"),
                    "user": None,
                    "api_key": key,
                    "forbidden": True,
                }
            return {
                "auth_kind": "api_key",
                "auth_scopes": scopes,
                "tenant_id": (key.get("metadata") or {}).get("tenant_id"),
                "user": None,
                "api_key": key,
            }

    return None


def is_admin_principal(auth: Optional[dict[str, Any]]) -> bool:
    if not auth:
        return False
    if auth.get("auth_kind") in {"master", "open", "tailscale"}:
        return True
    scopes = auth.get("auth_scopes") or []
    if SCOPE_ADMIN in scopes:
        return True
    user = auth.get("user") or {}
    if user.get("role") == "admin":
        return True
    return False


def tenant_must_isolate(auth: Optional[dict[str, Any]]) -> bool:
    """Session users and tenant-bound API keys must not see other tenants' rooms."""
    if not auth:
        return False
    if auth.get("auth_kind") == "session":
        return True
    if auth.get("auth_kind") == "api_key" and auth.get("tenant_id"):
        return True
    return False


def room_visible_to(room: Any, auth: Optional[dict[str, Any]]) -> bool:
    """Whether room may be read/written by this principal."""
    if not tenant_must_isolate(auth):
        return True
    tenant_id = (auth or {}).get("tenant_id")
    room_tid = getattr(room, "tenant_id", None)
    if room_tid is None:
        # Legacy unscoped rooms: not visible to tenant-bound sessions
        return False
    return room_tid == tenant_id


def safe_upload_filename(name: Optional[str]) -> str:
    base = Path(name or "upload.bin").name
    # Strip path separators and nulls
    base = base.replace("\x00", "").strip() or "upload.bin"
    if base in {".", ".."}:
        base = "upload.bin"
    return base[:200]


def resolve_room_file_path(
    files_root: Path,
    room_id: str,
    file_id: str,
    *,
    metadata_path: Optional[str] = None,
) -> Optional[Path]:
    """Resolve a download path only under files_root/room_id/{file_id}_*.

    Never follows arbitrary metadata.path outside the room directory.
    """
    if not room_id or not file_id:
        return None
    # Reject path traversal in ids
    if ".." in room_id or ".." in file_id or "/" in room_id or "/" in file_id:
        return None
    if "\\" in room_id or "\\" in file_id:
        return None

    root = (files_root / room_id).resolve()
    try:
        if not root.is_dir():
            # still allow exact match under root if file appears later
            root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    matches = sorted(root.glob(f"{file_id}_*"))
    for candidate in matches:
        try:
            resolved = candidate.resolve()
            if resolved.is_file() and str(resolved).startswith(str(root) + os.sep):
                return resolved
            if resolved.is_file() and resolved.parent == root:
                return resolved
        except OSError:
            continue

    # Optional: accept metadata path only if it is already under room dir and named correctly
    if metadata_path:
        try:
            p = Path(metadata_path).resolve()
            if (
                p.is_file()
                and (str(p).startswith(str(root) + os.sep) or p.parent == root)
                and p.name.startswith(f"{file_id}_")
            ):
                return p
        except OSError:
            return None
    return None


def cors_credentials_for_origins(allow_origins: list[str]) -> bool:
    """Browsers reject Access-Control-Allow-Origin: * with credentials."""
    if not allow_origins or "*" in allow_origins:
        return False
    return True
