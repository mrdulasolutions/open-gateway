"""Agent identity binding — one token ↔ one persona (OG-001…004, OG-002).

Precedence for join display name:
  1. API key canonical name (device key `name` field) when present
  2. OPENGATEWAY_AGENT_NAME env (MCP client side)
  3. explicit join `name` argument
  4. harness default
"""

from __future__ import annotations

from typing import Any, Optional


def resolve_join_name(
    *,
    request_name: str,
    env_agent_name: str = "",
    key_agent_name: str = "",
    harness: str = "",
    enforce_key_name: bool = True,
) -> tuple[str, Optional[str]]:
    """Return (resolved_name, error_detail_or_None).

    When enforce_key_name and key has a name, mismatches become 400-style errors
    unless request_name is empty (then rewritten to key name).
    """
    req = (request_name or "").strip()
    env = (env_agent_name or "").strip()
    key = (key_agent_name or "").strip()
    harness = (harness or "").strip() or "agent"

    if key and enforce_key_name:
        if req and req.lower() != key.lower():
            return key, (
                f"Join name {req!r} does not match token agent name {key!r}. "
                "One device token = one persona. Mint a separate token for another name."
            )
        return key, None

    if env:
        # Env wins over tool arg when key not binding (master token path)
        if req and req.lower() != env.lower():
            # Soft rewrite: env is source of truth for MCP (OG-002)
            return env, None
        return env, None

    if req:
        return req, None
    return harness, None


def participant_key_metadata(api_key: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Metadata stamped on join for key↔participant binding (OG-004)."""
    if not api_key:
        return {}
    return {
        "api_key_id": api_key.get("id"),
        "key_prefix": api_key.get("key_prefix"),
        "key_name": api_key.get("name"),
        "device_label": api_key.get("device_label") or "",
    }


def names_match(a: str, b: str) -> bool:
    return (a or "").strip().lower() == (b or "").strip().lower()
