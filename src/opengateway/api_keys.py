"""Per-device API keys with scoped roles (hashed at rest)."""

from __future__ import annotations

import hashlib
import secrets
from typing import Any, Iterable, Optional

# Scopes
SCOPE_ADMIN = "admin"  # full control incl. key management + vault write
SCOPE_WRITE = "write"  # mutate rooms/messages/tasks/workspace
SCOPE_READ = "read"  # GET only
SCOPE_PAIR = "pair"  # create pair links
SCOPE_PUSH = "push"  # manage own push subscription
SCOPE_TOOLS = "tools"  # list vault names + hub tool proxy (never secret values)
ALL_SCOPES = (
    SCOPE_ADMIN,
    SCOPE_WRITE,
    SCOPE_READ,
    SCOPE_PAIR,
    SCOPE_PUSH,
    SCOPE_TOOLS,
)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def generate_secret() -> str:
    """Create a high-entropy bearer secret (shown once)."""
    return f"ogk_{secrets.token_urlsafe(32)}"


def prefix_of(secret: str) -> str:
    return secret[:12] if len(secret) >= 12 else secret


def normalize_scopes(scopes: Optional[Iterable[str]]) -> list[str]:
    if not scopes:
        return [SCOPE_WRITE, SCOPE_READ]
    out: list[str] = []
    for s in scopes:
        s = (s or "").strip().lower()
        if s in ALL_SCOPES and s not in out:
            out.append(s)
    if SCOPE_ADMIN in out:
        return [SCOPE_ADMIN]
    if not out:
        out = [SCOPE_READ]
    return out


def scopes_allow(scopes: list[str], method: str, path: str) -> bool:
    """Return True if scopes permit this HTTP method + path."""
    m = (method or "GET").upper()
    p = path or "/"
    if SCOPE_ADMIN in scopes:
        return True
    # Key management is admin-only
    if p.startswith("/v1/keys"):
        return False
    if p.startswith("/v1/audit") and m != "GET":
        return False
    if p.startswith("/v1/tools/credentials"):
        if m in {"GET", "HEAD", "OPTIONS"}:
            return (
                SCOPE_TOOLS in scopes
                or SCOPE_READ in scopes
                or SCOPE_WRITE in scopes
            )
        return False
    if p.startswith("/v1/tools/proxy"):
        return SCOPE_TOOLS in scopes or SCOPE_WRITE in scopes
    if m in {"GET", "HEAD", "OPTIONS"}:
        return (
            SCOPE_READ in scopes
            or SCOPE_WRITE in scopes
            or SCOPE_PAIR in scopes
            or SCOPE_TOOLS in scopes
        )
    if p.startswith("/v1/pair"):
        return SCOPE_PAIR in scopes or SCOPE_WRITE in scopes
    if p.startswith("/v1/push"):
        return SCOPE_PUSH in scopes or SCOPE_WRITE in scopes or SCOPE_READ in scopes
    if "/workspace" in p:
        return SCOPE_WRITE in scopes
    # Mutations
    return SCOPE_WRITE in scopes
