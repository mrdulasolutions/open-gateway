"""Simple multi-user / multi-tenant auth (password + session tokens).

First registered user creates a tenant and is admin.
Later users join via invite code (or open registration into same tenant).
Session tokens work as Bearer tokens for the Live Ops UI.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

from opengateway.models import new_id, utcnow


class Tenant(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    slug: str
    created_at: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class User(BaseModel):
    id: str = Field(default_factory=new_id)
    tenant_id: str
    email: str
    password_hash: str = ""
    role: str = "member"  # admin | member
    display_name: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    last_login_at: Optional[datetime] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "email": self.email,
            "role": self.role,
            "display_name": self.display_name or self.email.split("@")[0],
            "created_at": self.created_at.isoformat()
            if hasattr(self.created_at, "isoformat")
            else self.created_at,
        }


class Invite(BaseModel):
    id: str = Field(default_factory=new_id)
    tenant_id: str
    code: str
    role: str = "member"
    created_by: str = ""
    expires_at: Optional[datetime] = None
    max_uses: int = 20
    uses: int = 0
    created_at: datetime = Field(default_factory=utcnow)


class Session(BaseModel):
    id: str = Field(default_factory=new_id)
    token_hash: str
    user_id: str
    tenant_id: str
    expires_at: datetime
    created_at: datetime = Field(default_factory=utcnow)


def hash_password(password: str, salt: Optional[str] = None) -> str:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000
    )
    return f"pbkdf2_sha256${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt, digest = stored.split("$", 2)
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000
        )
        return hmac.compare_digest(dk.hex(), digest)
    except Exception:
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint_session_token() -> str:
    return f"ogs_{secrets.token_urlsafe(32)}"


def mint_invite_code() -> str:
    return secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:12].upper()


def slugify(name: str) -> str:
    s = "".join(c.lower() if c.isalnum() else "-" for c in (name or "org"))
    while "--" in s:
        s = s.replace("--", "-")
    return (s.strip("-") or "org")[:48]


def open_registration() -> bool:
    """If true, new users can register into the first tenant without invite."""
    return os.environ.get("OPENGATEWAY_OPEN_REGISTRATION", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def session_ttl_days() -> int:
    try:
        return max(1, int(os.environ.get("OPENGATEWAY_SESSION_DAYS", "30")))
    except Exception:
        return 30
