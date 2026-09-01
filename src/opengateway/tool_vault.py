"""Tool credential vault — secrets stay on the hub; agents only see names.

Inspired by the AgentTeams isolation idea (secrets out of agent env), implemented
as our own vault + hub-side HTTP proxy. No third-party orchestration code.

Agents:
  - list credential *names* and metadata (never values)
  - call POST /v1/tools/proxy so the hub injects the secret into an outbound request

Admins:
  - put / delete credentials (admin scope)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import socket
from typing import Any, Optional
from urllib.parse import urlparse

from opengateway.models import new_id, utcnow

VAULT_META_KEY = "tool_vault_v1"
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
MAX_SECRET_BYTES = 16_384
MAX_PROXY_BODY = 1_000_000
MAX_PROXY_RESPONSE = 2_000_000
PROXY_TIMEOUT = 30.0

# Default private/reserved nets — block SSRF to loopback / RFC1918 unless overridden
_BLOCKED_NETS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)


def validate_name(name: str) -> str:
    n = (name or "").strip()
    if not NAME_RE.match(n):
        raise ValueError(
            "Credential name must be 1–64 chars, start with a letter, "
            "and use only A–Z a–z 0–9 _ . -"
        )
    return n


def _master_material() -> bytes:
    """Derive vault key material from hub secrets (never stored with vault)."""
    parts = [
        os.environ.get("OPENGATEWAY_VAULT_KEY") or "",
        os.environ.get("OPENGATEWAY_AUTH_TOKEN") or "",
        os.environ.get("OPENGATEWAY_TENANT") or "",
        "opengateway-tool-vault-v1",
    ]
    raw = "|".join(parts).encode("utf-8")
    return hashlib.sha256(raw).digest()


def seal(plaintext: str) -> str:
    """Seal a secret for at-rest storage (stdlib stream + HMAC; not Fernet)."""
    if not isinstance(plaintext, str):
        plaintext = str(plaintext)
    data = plaintext.encode("utf-8")
    if len(data) > MAX_SECRET_BYTES:
        raise ValueError(f"Secret too large (max {MAX_SECRET_BYTES} bytes)")
    nonce = os.urandom(16)
    key = _master_material()
    # Keystream via successive HMAC blocks
    out = bytearray()
    counter = 0
    while len(out) < len(data):
        block = hmac.new(
            key, nonce + counter.to_bytes(4, "big"), hashlib.sha256
        ).digest()
        out.extend(block)
        counter += 1
    cipher = bytes(a ^ b for a, b in zip(data, out[: len(data)]))
    tag = hmac.new(key, nonce + cipher, hashlib.sha256).digest()[:16]
    blob = nonce + tag + cipher
    return "v1:" + base64.urlsafe_b64encode(blob).decode("ascii")


def unseal(token: str) -> str:
    if not token or not token.startswith("v1:"):
        raise ValueError("Unknown vault encoding")
    blob = base64.urlsafe_b64decode(token[3:].encode("ascii"))
    if len(blob) < 32:
        raise ValueError("Corrupt vault entry")
    nonce, tag, cipher = blob[:16], blob[16:32], blob[32:]
    key = _master_material()
    expect = hmac.new(key, nonce + cipher, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(tag, expect):
        raise ValueError("Vault integrity check failed")
    out = bytearray()
    counter = 0
    while len(out) < len(cipher):
        block = hmac.new(
            key, nonce + counter.to_bytes(4, "big"), hashlib.sha256
        ).digest()
        out.extend(block)
        counter += 1
    plain = bytes(a ^ b for a, b in zip(cipher, out[: len(cipher)]))
    return plain.decode("utf-8")


def public_view(rec: dict[str, Any]) -> dict[str, Any]:
    """Agent-safe view — never includes value / ciphertext."""
    return {
        "name": rec.get("name"),
        "description": rec.get("description") or "",
        "header_name": rec.get("header_name") or "Authorization",
        "inject": rec.get("inject") or "bearer",
        "allowed_hosts": list(rec.get("allowed_hosts") or []),
        "created_at": rec.get("created_at"),
        "updated_at": rec.get("updated_at"),
        "has_value": bool(rec.get("sealed")),
        # Never expose sealed / value
    }


def load_vault(store: Any) -> dict[str, dict[str, Any]]:
    raw = store.get_meta(VAULT_META_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for k, v in data.items():
        if isinstance(v, dict) and v.get("name"):
            out[str(k)] = v
    return out


def save_vault(store: Any, vault: dict[str, dict[str, Any]]) -> None:
    store.set_meta(VAULT_META_KEY, json.dumps(vault, separators=(",", ":")))


def put_credential(
    store: Any,
    *,
    name: str,
    value: str,
    description: str = "",
    header_name: str = "Authorization",
    inject: str = "bearer",
    allowed_hosts: Optional[list[str]] = None,
) -> dict[str, Any]:
    name = validate_name(name)
    if not value or not str(value).strip():
        raise ValueError("value is required")
    inject_n = (inject or "bearer").strip().lower()
    if inject_n not in {"bearer", "header", "query", "basic"}:
        raise ValueError("inject must be bearer | header | query | basic")
    hosts = []
    for h in allowed_hosts or []:
        h = (h or "").strip().lower().lstrip(".")
        if h and h not in hosts:
            hosts.append(h)
    vault = load_vault(store)
    now = utcnow().isoformat()
    prev = vault.get(name) or {}
    rec = {
        "id": prev.get("id") or new_id(),
        "name": name,
        "sealed": seal(str(value)),
        "description": (description or "").strip()[:500],
        "header_name": (header_name or "Authorization").strip() or "Authorization",
        "inject": inject_n,
        "allowed_hosts": hosts,
        "created_at": prev.get("created_at") or now,
        "updated_at": now,
    }
    vault[name] = rec
    save_vault(store, vault)
    return public_view(rec)


def delete_credential(store: Any, name: str) -> bool:
    name = validate_name(name)
    vault = load_vault(store)
    if name not in vault:
        return False
    del vault[name]
    save_vault(store, vault)
    return True


def list_credentials(store: Any) -> list[dict[str, Any]]:
    vault = load_vault(store)
    return sorted(
        (public_view(r) for r in vault.values()),
        key=lambda r: (r.get("name") or ""),
    )


def get_secret_value(store: Any, name: str) -> Optional[str]:
    """Internal only — never expose via HTTP/MCP response."""
    name = validate_name(name)
    rec = load_vault(store).get(name)
    if not rec or not rec.get("sealed"):
        return None
    return unseal(rec["sealed"])


def host_allowed(rec: dict[str, Any], hostname: str) -> bool:
    allowed = rec.get("allowed_hosts") or []
    if not allowed:
        return True  # unrestricted (still SSRF-private blocked)
    host = (hostname or "").lower().rstrip(".")
    for a in allowed:
        a = (a or "").lower().lstrip(".")
        if host == a or host.endswith("." + a):
            return True
    return False


def _is_blocked_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
        return True
    for net in _BLOCKED_NETS:
        if addr in net:
            return True
    return False


def assert_safe_url(url: str, *, allow_private: bool = False) -> tuple[str, str]:
    """Validate URL scheme/host; return (scheme, hostname). Raises ValueError."""
    p = urlparse(url)
    if p.scheme not in {"http", "https"}:
        raise ValueError("Only http/https URLs allowed")
    host = (p.hostname or "").lower()
    if not host:
        raise ValueError("URL missing host")
    if host in {"localhost", "metadata.google.internal"}:
        if not allow_private:
            raise ValueError("Destination host blocked (SSRF protection)")
    if not allow_private:
        # Resolve and check — fail closed on resolution errors for literal IPs
        try:
            infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80))
        except socket.gaierror as e:
            raise ValueError(f"Could not resolve host: {e}") from e
        for info in infos:
            ip = info[4][0]
            if _is_blocked_ip(ip):
                raise ValueError("Destination IP blocked (SSRF protection)")
    return p.scheme, host


def build_auth_header(rec: dict[str, Any], secret: str) -> dict[str, str]:
    inject = (rec.get("inject") or "bearer").lower()
    header = rec.get("header_name") or "Authorization"
    if inject == "bearer":
        return {header: f"Bearer {secret}"}
    if inject == "basic":
        # secret may already be user:pass or raw token
        if ":" in secret and not secret.startswith("Basic "):
            enc = base64.b64encode(secret.encode("utf-8")).decode("ascii")
            return {header: f"Basic {enc}"}
        return {header: secret if secret.lower().startswith("basic ") else f"Basic {secret}"}
    if inject == "header":
        return {header: secret}
    return {}


def apply_query_secret(url: str, rec: dict[str, Any], secret: str) -> str:
    from urllib.parse import parse_qsl, urlencode, urlunparse, urlparse as up

    p = up(url)
    q = dict(parse_qsl(p.query, keep_blank_values=True))
    key = (rec.get("header_name") or "api_key").strip() or "api_key"
    q[key] = secret
    return urlunparse(p._replace(query=urlencode(q)))
