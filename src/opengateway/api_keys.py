"""Per-device API keys with scoped roles (hashed at rest)."""

from __future__ import annotations

import hashlib
import re
import secrets
from typing import Any, Iterable, Optional

# Scopes
SCOPE_ADMIN = "admin"  # full control incl. key management + vault write
SCOPE_WRITE = "write"  # mutate rooms/messages/tasks/workspace
SCOPE_READ = "read"  # GET only
SCOPE_PAIR = "pair"  # create pair links
SCOPE_PUSH = "push"  # manage own push subscription
SCOPE_TOOLS = "tools"  # list vault names + hub tool proxy (never secret values)
SCOPE_RUNNER = "runner"  # managed-runner heartbeat/job/log transport only
ALL_SCOPES = (
    SCOPE_ADMIN,
    SCOPE_WRITE,
    SCOPE_READ,
    SCOPE_PAIR,
    SCOPE_PUSH,
    SCOPE_TOOLS,
    SCOPE_RUNNER,
)

_RUNNER_PATH_RE = re.compile(
    r"^/v1/runners/[^/]+/(?:"
    r"heartbeat|disconnect|logs|jobs/wait|jobs/[^/]+/(?:claim|complete)"
    r")/?$"
)
_SECRET_FIELD_RE = re.compile(
    r"(?:^|[_-])(?:token|password|secret|authorization|auth|cookie|api[_-]?key)"
    r"(?:$|[_-])",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_OG_TOKEN_RE = re.compile(r"\b(?:ogk|ogs)_[A-Za-z0-9_-]{8,}\b")
_ASSIGNED_SECRET_RE = re.compile(
    r"(?i)\b(token|password|secret|api[_-]?key)\s*([=:])\s*([^\s,;]+)"
)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def generate_secret() -> str:
    """Create a high-entropy bearer secret (shown once)."""
    return f"ogk_{secrets.token_urlsafe(32)}"


def prefix_of(secret: str) -> str:
    return secret[:12] if len(secret) >= 12 else secret


def scrub_secret_text(value: str, *, max_length: Optional[int] = None) -> str:
    """Remove credential-shaped values before logs or job results are stored."""
    text = str(value or "")
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _OG_TOKEN_RE.sub("[REDACTED]", text)
    text = _ASSIGNED_SECRET_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text
    )
    if max_length is not None:
        text = text[: max(0, int(max_length))]
    return text


def scrub_secrets(value: Any) -> Any:
    """Recursively redact common credential fields and token-shaped strings."""
    if isinstance(value, dict):
        return {
            str(k): (
                "[REDACTED]"
                if _SECRET_FIELD_RE.search(str(k))
                else scrub_secrets(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [scrub_secrets(v) for v in value]
    if isinstance(value, tuple):
        return [scrub_secrets(v) for v in value]
    if isinstance(value, str):
        return scrub_secret_text(value)
    return value


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
    # Runner credentials are transport identities, not general hub keys. Binding
    # to the concrete runner id is enforced by the endpoint using key metadata.
    if SCOPE_RUNNER in scopes:
        if not _RUNNER_PATH_RE.fullmatch(p):
            return False
        if p.rstrip("/").endswith(
            ("/heartbeat", "/disconnect", "/logs", "/claim", "/complete")
        ):
            return m in {"POST", "OPTIONS"}
        return m in {"GET", "HEAD", "OPTIONS"}
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
