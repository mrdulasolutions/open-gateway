"""Bearer-token auth middleware for public gateways + optional Tailscale identity."""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Any, Callable, Deque, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from opengateway.api_keys import SCOPE_ADMIN
from opengateway.config import GatewayConfig
from opengateway.security import (
    client_ip as security_client_ip,
    const_eq,
    resolve_auth,
    token_from_request,
)

# Paths that stay open for health / static UI bootstrap
PUBLIC_PREFIXES = (
    "/ping",
    "/ui",
    "/favicon",
)

# OpenAPI/docs are public only when explicitly enabled (production-safe default: off when auth on)
def _docs_public() -> bool:
    return os.environ.get("OPENGATEWAY_PUBLIC_DOCS", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


# Auth failure rate limit: max failures per IP in window (production default)
_AUTH_FAIL_WINDOW_SEC = 60.0
_AUTH_FAIL_MAX = 30  # ~0.5/s sustained; bursts allowed
_fail_lock = Lock()
_fail_buckets: dict[str, Deque[float]] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    return security_client_ip(request)


def _auth_failures_blocked(ip: str) -> bool:
    now = time.monotonic()
    with _fail_lock:
        q = _fail_buckets[ip]
        while q and now - q[0] > _AUTH_FAIL_WINDOW_SEC:
            q.popleft()
        return len(q) >= _AUTH_FAIL_MAX


def _record_auth_failure(ip: str) -> None:
    now = time.monotonic()
    with _fail_lock:
        q = _fail_buckets[ip]
        while q and now - q[0] > _AUTH_FAIL_WINDOW_SEC:
            q.popleft()
        q.append(now)


def _clear_auth_failures(ip: str) -> None:
    with _fail_lock:
        _fail_buckets.pop(ip, None)


def client_ip(request: Request) -> str:
    """Public helper for endpoints that rate-limit outside middleware."""
    return _client_ip(request)


def auth_failures_blocked(ip: str) -> bool:
    return _auth_failures_blocked(ip)


def record_auth_failure(ip: str) -> None:
    _record_auth_failure(ip)


def clear_auth_failures(ip: str) -> None:
    _clear_auth_failures(ip)


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, config: GatewayConfig, store: Any = None):
        super().__init__(app)
        self.config = config
        self.store = store

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Auth off only when not required
        if not self.config.require_auth:
            return await call_next(request)
        # Require either master token configured OR device keys in store
        has_master = bool(self.config.auth_token)
        if not has_master and self.store is None:
            return await call_next(request)

        path = request.url.path or "/"
        # Allow unauthenticated static UI + health (health reveals little)
        if path == "/" or any(path == p or path.startswith(p + "/") for p in PUBLIC_PREFIXES):
            return await call_next(request)
        # OpenAPI only when explicitly enabled (or when auth not required — handled above)
        if _docs_public() and path in {"/docs", "/openapi.json", "/redoc"}:
            return await call_next(request)
        if _docs_public() and (
            path.startswith("/docs/") or path.startswith("/redoc/")
        ):
            return await call_next(request)
        # Phone pair redeem is public (code is the secret); create still requires auth
        if path.rstrip("/") == "/v1/pair/redeem" and request.method in {"POST", "OPTIONS"}:
            return await call_next(request)
        # Push VAPID public key is safe to expose
        if path.rstrip("/") == "/v1/push/vapid" and request.method in {"GET", "OPTIONS"}:
            return await call_next(request)
        # First-run UI setup claim (one-time bootstrap for Railway / public deploys)
        if path.rstrip("/") in {"/v1/setup", "/v1/setup/claim"} and request.method in {
            "GET",
            "POST",
            "OPTIONS",
        }:
            return await call_next(request)
        # User login / register / status (session auth)
        if path.rstrip("/") in {
            "/v1/auth/status",
            "/v1/auth/login",
            "/v1/auth/register",
        } and request.method in {"GET", "POST", "OPTIONS"}:
            return await call_next(request)

        ip = _client_ip(request)
        if _auth_failures_blocked(ip):
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Too many auth failures — wait and retry",
                    "mode": self.config.mode.value,
                    "retry_after_seconds": int(_AUTH_FAIL_WINDOW_SEC),
                },
                headers={"Retry-After": str(int(_AUTH_FAIL_WINDOW_SEC))},
            )

        token = token_from_request(request)
        request.state.auth_kind = None
        request.state.api_key = None
        request.state.auth_scopes = [SCOPE_ADMIN]
        request.state.user = None
        request.state.tenant_id = None

        auth = await resolve_auth(
            token,
            config=self.config,
            store=self.store,
            method=request.method,
            path=path,
        )
        if auth and auth.get("forbidden"):
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "Token scope does not allow this action",
                    "scopes": auth.get("auth_scopes") or [],
                    "path": path,
                },
            )
        if auth:
            _clear_auth_failures(ip)
            request.state.auth_kind = auth.get("auth_kind")
            request.state.auth_scopes = auth.get("auth_scopes") or [SCOPE_ADMIN]
            request.state.user = auth.get("user")
            request.state.api_key = auth.get("api_key")
            request.state.tenant_id = auth.get("tenant_id")
            return await call_next(request)

        # Optional: Tailscale Serve identity headers (ONLY when bound to localhost)
        if self.config.trust_tailscale_identity and self.config.is_localhost_bind:
            identity = _extract_tailscale_identity(request)
            if identity:
                request.state.tailscale_user = identity
                request.state.auth_kind = "tailscale"
                request.state.auth_scopes = [SCOPE_ADMIN]
                _clear_auth_failures(ip)
                return await call_next(request)

        # Only count *wrong* tokens toward rate limit — missing token is common
        # for static UI probes and should not lock out the whole LAN IP.
        if token:
            _record_auth_failure(ip)
        return JSONResponse(
            status_code=401,
            content={
                "detail": "Unauthorized — set Authorization: Bearer <token or device API key>",
                "mode": self.config.mode.value,
                "network": self.config.network,
                "hint": (
                    "Behind Tailscale Serve on localhost you may also rely on "
                    "Tailscale-User-* identity headers when trust is enabled."
                    if self.config.trust_tailscale_identity
                    else "Use the master OPENGATEWAY_AUTH_TOKEN or a device API key from POST /v1/keys."
                ),
            },
            headers={"WWW-Authenticate": "Bearer"},
        )


def _extract_token(request: Request) -> Optional[str]:
    """Back-compat for server.auth_logout and tests."""
    return token_from_request(request)


def _extract_tailscale_identity(request: Request) -> Optional[dict[str, str]]:
    """Parse Tailscale Serve/Funnel identity headers if present.

    Only call this when the process is bound to loopback so clients cannot
    spoof Tailscale-User-* from the network.
    """
    login = (
        request.headers.get("tailscale-user-login")
        or request.headers.get("Tailscale-User-Login")
    )
    if not login:
        return None
    return {
        "login": login,
        "name": request.headers.get("tailscale-user-name")
        or request.headers.get("Tailscale-User-Name")
        or "",
        "profile_pic": request.headers.get("tailscale-user-profile-pic")
        or request.headers.get("Tailscale-User-Profile-Pic")
        or "",
    }


def _const_eq(a: str, b: str) -> bool:
    """Back-compat alias — prefer security.const_eq."""
    return const_eq(a, b)
