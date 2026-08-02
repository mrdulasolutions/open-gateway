"""Tailscale diagnostics and serve-path helpers for multi-machine hardening."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
from typing import Any, Optional


def tailscale_available() -> bool:
    return shutil.which("tailscale") is not None


def tailscale_status() -> dict[str, Any]:
    """Rich status for doctor / health. Never raises."""
    out: dict[str, Any] = {
        "installed": tailscale_available(),
        "running": False,
        "hostname": None,
        "dns_name": None,
        "tailscale_ips": [],
        "backend_state": None,
        "serve_configured": None,
        "hints": [],
    }
    if not out["installed"]:
        out["hints"].append(
            "Install Tailscale (https://tailscale.com/download) for mesh multi-machine access."
        )
        return out

    try:
        raw = subprocess.check_output(
            ["tailscale", "status", "--json"],
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        data = json.loads(raw)
        self_node = data.get("Self") or {}
        out["running"] = True
        out["backend_state"] = data.get("BackendState") or self_node.get("Online")
        dns = (self_node.get("DNSName") or "").rstrip(".")
        out["dns_name"] = dns or None
        out["hostname"] = (self_node.get("HostName") or "").strip() or None
        ips = self_node.get("TailscaleIPs") or []
        out["tailscale_ips"] = list(ips)
        if not self_node.get("Online", True):
            out["hints"].append("This node appears offline on the tailnet.")
    except subprocess.TimeoutExpired:
        out["hints"].append("tailscale status timed out — is the daemon running?")
    except Exception as e:
        out["hints"].append(f"tailscale status failed: {e}")
        return out

    # Serve status (optional; some installs lack serve)
    try:
        serve_raw = subprocess.check_output(
            ["tailscale", "serve", "status", "--json"],
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        serve_data = json.loads(serve_raw) if serve_raw.strip() else {}
        out["serve_configured"] = bool(serve_data)
        out["serve_status"] = serve_data
        if not serve_data:
            out["hints"].append(
                "Tailscale Serve is not configured. Prefer: "
                "`tailscale serve --bg <port>` with `opengateway serve --mode serve` "
                "(binds 127.0.0.1; TLS on the tailnet)."
            )
    except Exception:
        out["serve_configured"] = False
        out["hints"].append(
            "Could not read `tailscale serve status`. "
            "Use Serve for multi-machine instead of opening raw 100.x ports."
        )

    if out["dns_name"]:
        out["hints"].append(
            f"MagicDNS: https://{out['dns_name']} (after Serve) "
            f"or http://{out['dns_name']}:PORT (raw — often blocked by host firewall)."
        )
    if out["tailscale_ips"]:
        out["hints"].append(
            "Direct Tailscale IP:PORT often times out when the host firewall "
            "allows LAN but not the utun interface. Prefer Tailscale Serve."
        )

    return out


def recommended_serve_command(port: int = 8765, https: bool = True) -> str:
    if https:
        return f"tailscale serve --bg {port}"
    return f"tailscale serve --bg --http=80 {port}"


def probe_tcp(host: str, port: int, timeout: float = 1.5) -> dict[str, Any]:
    """Best-effort TCP connect probe (local diagnostics only)."""
    result: dict[str, Any] = {"host": host, "port": port, "ok": False, "error": None}
    try:
        with socket.create_connection((host, port), timeout=timeout):
            result["ok"] = True
    except OSError as e:
        result["error"] = str(e)
    return result


def network_diagnostics(port: int = 8765) -> dict[str, Any]:
    """Aggregate Tailscale + local bind advice for /meta and doctor."""
    ts = tailscale_status()
    probes: list[dict[str, Any]] = [
        probe_tcp("127.0.0.1", port),
    ]
    for ip in ts.get("tailscale_ips") or []:
        if ":" in str(ip):
            continue  # skip IPv6 for simple probe
        probes.append(probe_tcp(str(ip), port))

    return {
        "tailscale": ts,
        "probes": probes,
        "recommended": {
            "mode": "serve",
            "commands": [
                f"opengateway serve --mode serve --token $OPENGATEWAY_AUTH_TOKEN --port {port}",
                recommended_serve_command(port),
            ],
            "why": (
                "Binding 0.0.0.0 and advertising a 100.x Tailscale IP often fails from "
                "other nodes due to host firewall rules. Tailscale Serve proxies "
                "https://<magicdns> → http://127.0.0.1:<port> on the tailnet only."
            ),
        },
    }
