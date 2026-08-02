"""Runtime gateway configuration — internal vs public, Tailscale Serve / Funnel."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from uuid import uuid4


class GatewayMode(str, Enum):
    INTERNAL = "internal"  # single machine / loopback
    PUBLIC = "public"  # multi-agent over network (LAN / tailnet / internet)


# Soft network labels for UI + serve strategy
# loopback | lan | tailscale | funnel | public
NETWORK_LABELS = frozenset({"loopback", "lan", "tailscale", "funnel", "public"})


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


@dataclass
class GatewayConfig:
    """How this OpenGateway process is exposed."""

    mode: GatewayMode = GatewayMode.INTERNAL
    host: str = "127.0.0.1"
    port: int = 8765
    auth_token: Optional[str] = None
    public_url: Optional[str] = None
    allow_origins: list[str] = field(default_factory=lambda: ["*"])
    gateway_id: str = field(default_factory=lambda: str(uuid4()))
    name: str = "local"
    require_auth: bool = False
    # Soft network labels for UI
    network: str = "loopback"  # loopback | lan | tailscale | funnel | public
    tailscale_hostname: Optional[str] = None
    # Trust Tailscale-User-* headers only when bound to localhost behind Serve
    trust_tailscale_identity: bool = False
    # Printed to operator when using Serve
    serve_hint: Optional[str] = None

    @property
    def base_url(self) -> str:
        if self.public_url:
            return self.public_url.rstrip("/")
        host = self.host if self.host not in {"0.0.0.0", "::"} else "127.0.0.1"
        return f"http://{host}:{self.port}"

    @property
    def is_localhost_bind(self) -> bool:
        return self.host in {"127.0.0.1", "localhost", "::1"}

    def to_public_dict(self) -> dict:
        return {
            "id": self.gateway_id,
            "name": self.name,
            "mode": self.mode.value,
            "network": self.network,
            "host": self.host,
            "port": self.port,
            "base_url": self.base_url,
            "public_url": self.public_url,
            "require_auth": self.require_auth,
            "auth_configured": bool(self.auth_token),
            "tailscale_hostname": self.tailscale_hostname,
            "trust_tailscale_identity": self.trust_tailscale_identity,
            "serve_hint": self.serve_hint,
            "bind": f"{self.host}:{self.port}",
            "label": network_ui_label(self.network),
        }


def network_ui_label(network: str) -> str:
    return {
        "loopback": "Internal (loopback)",
        "lan": "LAN",
        "tailscale": "Tailnet (Serve)",
        "funnel": "Internet (Funnel)",
        "public": "Internet (open bind)",
    }.get(network, network)


def detect_tailscale_hostname() -> Optional[str]:
    """Best-effort MagicDNS name if `tailscale` CLI is available."""
    try:
        import subprocess

        out = subprocess.check_output(
            ["tailscale", "status", "--json"],
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        import json

        data = json.loads(out)
        self_node = data.get("Self") or {}
        dns = (self_node.get("DNSName") or "").rstrip(".")
        if dns:
            return dns
        host = (self_node.get("HostName") or "").strip()
        return host or None
    except Exception:
        return None


def load_gateway_config() -> GatewayConfig:
    mode_raw = _env("OPENGATEWAY_MODE", "internal").lower()
    # Aliases: serve / tailscale / tailnet → public mode with tailscale network
    network_raw = _env("OPENGATEWAY_NETWORK", "").lower()
    via_raw = _env("OPENGATEWAY_VIA", "").lower()  # serve | funnel | open

    if mode_raw in {"serve", "tailscale", "tailnet"}:
        mode = GatewayMode.PUBLIC
        network_raw = network_raw or "tailscale"
        via_raw = via_raw or "serve"
    elif mode_raw in {"funnel"}:
        mode = GatewayMode.PUBLIC
        network_raw = network_raw or "funnel"
        via_raw = via_raw or "funnel"
    elif mode_raw in {"public", "net", "network"}:
        mode = GatewayMode.PUBLIC
    else:
        mode = GatewayMode.INTERNAL

    # via=serve is the recommended multi-machine path: bind localhost + Tailscale Serve
    if via_raw in {"serve", "tailscale", "ts"}:
        network_raw = network_raw or "tailscale"
    elif via_raw in {"funnel"}:
        network_raw = network_raw or "funnel"

    port = int(_env("OPENGATEWAY_PORT", "8765") or "8765")
    token = _env("OPENGATEWAY_AUTH_TOKEN") or None
    public_url = _env("OPENGATEWAY_PUBLIC_URL") or None
    name = _env(
        "OPENGATEWAY_NAME",
        "local" if mode == GatewayMode.INTERNAL else "public",
    )

    host_env = _env("OPENGATEWAY_HOST")
    if host_env:
        host = host_env
    elif mode == GatewayMode.INTERNAL:
        host = "127.0.0.1"
    elif network_raw in {"tailscale", "funnel"} or via_raw in {"serve", "funnel", "tailscale", "ts"}:
        # Serve/Funnel reverse-proxy to localhost — never open 0.0.0.0 for that path
        host = "127.0.0.1"
    else:
        host = "0.0.0.0"

    # Auth: required for public unless explicitly disabled
    require_auth_env = _env("OPENGATEWAY_REQUIRE_AUTH")
    if require_auth_env:
        require_auth = require_auth_env.lower() in {"1", "true", "yes", "on"}
    else:
        require_auth = mode == GatewayMode.PUBLIC

    if mode == GatewayMode.PUBLIC and require_auth and not token:
        token = f"ogk_{uuid4().hex}"
        os.environ["OPENGATEWAY_AUTH_TOKEN"] = token

    origins_raw = _env("OPENGATEWAY_CORS_ORIGINS", "*")
    allow_origins = [o.strip() for o in origins_raw.split(",") if o.strip()] or ["*"]

    ts = detect_tailscale_hostname()

    if mode == GatewayMode.INTERNAL:
        network = "loopback"
    elif network_raw in NETWORK_LABELS:
        network = network_raw
    elif ts:
        network = "tailscale"
    elif host in {"0.0.0.0", "::"}:
        network = "public"
    else:
        network = "lan"

    serve_hint: Optional[str] = None
    if network == "tailscale":
        if not public_url and ts:
            # HTTPS via Tailscale Serve (preferred advertised URL)
            public_url = f"https://{ts}"
        serve_hint = (
            f"tailscale serve --bg {port}"
            if not public_url or public_url.startswith("https://")
            else f"tailscale serve --bg --http=80 {port}"
        )
        if not name or name == "public":
            name = "tailnet"
    elif network == "funnel":
        if not public_url and ts:
            public_url = f"https://{ts}"
        serve_hint = f"tailscale funnel --bg {port}"
        if not name or name == "public":
            name = "funnel"
    elif mode == GatewayMode.PUBLIC and not public_url and ts:
        public_url = f"http://{ts}:{port}"

    # Identity headers only safe when app is only reachable via local reverse-proxy
    trust_ts = _env("OPENGATEWAY_TRUST_TAILSCALE_IDENTITY", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if trust_ts is False and network in {"tailscale", "funnel"} and host in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        # Default ON for serve/funnel localhost binds (headers injected by Tailscale)
        trust_ts = _env("OPENGATEWAY_TRUST_TAILSCALE_IDENTITY", "true").lower() not in {
            "0",
            "false",
            "no",
            "off",
        }

    return GatewayConfig(
        mode=mode,
        host=host,
        port=port,
        auth_token=token,
        public_url=public_url,
        allow_origins=allow_origins,
        gateway_id=_env("OPENGATEWAY_ID") or str(uuid4()),
        name=name,
        require_auth=require_auth,
        network=network,
        tailscale_hostname=ts,
        trust_tailscale_identity=trust_ts and host in {"127.0.0.1", "localhost", "::1"},
        serve_hint=serve_hint,
    )


def local_ips() -> list[str]:
    ips: list[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            ip = info[4][0]
            if ":" not in ip and ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass
    return ips
