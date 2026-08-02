"""Append-only audit log for public / multi-machine gateways."""

from __future__ import annotations

import os
import re
from typing import Any, Optional

# Keys / substrings redacted from audit detail payloads
_SECRET_KEY_RE = re.compile(
    r"(token|password|secret|authorization|auth|cookie|api[_-]?key)",
    re.IGNORECASE,
)


def audit_enabled(require_auth: bool = False) -> bool:
    """Audit is on for public-auth gateways, or when OPENGATEWAY_AUDIT is set."""
    raw = os.environ.get("OPENGATEWAY_AUDIT", "").strip().lower()
    if raw in {"0", "false", "off", "no"}:
        return False
    if raw in {"1", "true", "on", "yes", "always"}:
        return True
    # Default: enable when the gateway requires auth (public / LAN / serve / funnel)
    return require_auth


def redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if _SECRET_KEY_RE.search(str(k)):
                out[k] = "***"
            else:
                out[k] = redact_value(v)
        return out
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    if isinstance(value, str) and len(value) > 8 and value.startswith(("ogk_", "a48")):
        return value[:4] + "…"
    return value


def build_audit_entry(
    *,
    action: str,
    actor: Optional[str] = None,
    room_id: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
    ip: Optional[str] = None,
    outcome: str = "ok",
) -> dict[str, Any]:
    return {
        "action": action,
        "actor": actor,
        "room_id": room_id,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "detail": redact_value(detail or {}),
        "ip": ip,
        "outcome": outcome,
    }
