"""Bearer-token auth middleware for public gateways + optional Tailscale identity."""

from __future__ import annotations

from typing import Callable, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from opengateway.config import GatewayConfig

# Paths that stay open for health / static UI bootstrap
PUBLIC_PREFIXES = (
    "/ping",
    "/ui",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/favicon",
)


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, config: GatewayConfig):
        super().__init__(app)
        self.config = config

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not self.config.require_auth or not self.config.auth_token:
            return await call_next(request)

        path = request.url.path or "/"
        # Allow unauthenticated static UI + health (health reveals little)
        if path == "/" or any(path == p or path.startswith(p + "/") for p in PUBLIC_PREFIXES):
            return await call_next(request)

        # Bearer / query token
        token = _extract_token(request)
        if token and _const_eq(token, self.config.auth_token):
            return await call_next(request)

        # Optional: Tailscale Serve identity headers (ONLY when bound to localhost)
        if self.config.trust_tailscale_identity and self.config.is_localhost_bind:
            identity = _extract_tailscale_identity(request)
            if identity:
                # Stash for handlers that want to log who called
                request.state.tailscale_user = identity
                return await call_next(request)

        return JSONResponse(
            status_code=401,
            content={
                "detail": "Unauthorized — set Authorization: Bearer <OPENGATEWAY_AUTH_TOKEN>",
                "mode": self.config.mode.value,
                "network": self.config.network,
                "hint": (
                    "Behind Tailscale Serve on localhost you may also rely on "
                    "Tailscale-User-* identity headers when trust is enabled."
                    if self.config.trust_tailscale_identity
                    else None
                ),
            },
            headers={"WWW-Authenticate": "Bearer"},
        )


def _extract_token(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth:
        parts = auth.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        return auth.strip()
    # Query param for EventSource (cannot set headers easily)
    q = request.query_params.get("token") or request.query_params.get("access_token")
    return q


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
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a.encode(), b.encode()):
        result |= x ^ y
    return result == 0
