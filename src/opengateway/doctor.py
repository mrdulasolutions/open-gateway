"""OpenGateway doctor — network, auth, MCP, and radio/presence diagnostics.

Pure functions return structured check results for CLI and tests.
"""

from __future__ import annotations

import importlib.metadata
import os
import socket
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

import httpx


# ── result model ────────────────────────────────────────────────────────────


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    severity: str = "info"  # info | warn | error
    fix: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "severity": self.severity,
            "fix": self.fix,
        }


@dataclass
class DoctorReport:
    base_url: str
    checks: list[Check] = field(default_factory=list)
    ping: Optional[dict[str, Any]] = None
    rooms_summary: Optional[dict[str, Any]] = None
    network: Optional[dict[str, Any]] = None
    tailscale: Optional[dict[str, Any]] = None
    recommendations: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[Check]:
        return [c for c in self.checks if not c.ok and c.severity == "error"]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if not c.ok and c.severity == "warn"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def exit_code(self) -> int:
        if self.errors:
            return 1
        if self.warnings:
            return 2
        return 0


def _token() -> str:
    return (
        os.environ.get("OPENGATEWAY_AUTH_TOKEN")
        or os.environ.get("OPENGATEWAY_TOKEN")
        or ""
    ).strip()


def _auth_headers(token: Optional[str] = None) -> dict[str, str]:
    t = (token if token is not None else _token()).strip()
    if not t:
        return {}
    return {"Authorization": f"Bearer {t}"}


# ── individual checks ───────────────────────────────────────────────────────


def check_env(base_url: str) -> list[Check]:
    out: list[Check] = []
    env_url = (os.environ.get("OPENGATEWAY_URL") or "").strip()
    if env_url:
        out.append(
            Check(
                "env.OPENGATEWAY_URL",
                True,
                env_url,
                "info",
            )
        )
        if env_url.rstrip("/") != base_url.rstrip("/"):
            out.append(
                Check(
                    "env.URL_vs_probe",
                    False,
                    f"OPENGATEWAY_URL={env_url} but doctor probing {base_url}",
                    "warn",
                    "Pass --url $OPENGATEWAY_URL or export OPENGATEWAY_URL to the hub you mean.",
                )
            )
    else:
        out.append(
            Check(
                "env.OPENGATEWAY_URL",
                True,
                f"(unset — using {base_url})",
                "info",
                "Set OPENGATEWAY_URL for MCP and listen daemons.",
            )
        )

    tok = _token()
    if tok:
        prefix = tok[:8] + "…" if len(tok) > 8 else tok
        kind = (
            "device key (ogk_)"
            if tok.startswith("ogk_")
            else "session (ogs_)"
            if tok.startswith("ogs_")
            else "master/other"
        )
        out.append(
            Check(
                "env.OPENGATEWAY_AUTH_TOKEN",
                True,
                f"set ({kind}, {prefix})",
                "info",
            )
        )
    else:
        out.append(
            Check(
                "env.OPENGATEWAY_AUTH_TOKEN",
                False,
                "unset",
                "warn",
                "Public/Railway hubs need a Bearer token in MCP env and for doctor auth checks. "
                "Mint in Live Ops → Agent tokens, or use master OPENGATEWAY_AUTH_TOKEN.",
            )
        )

    harness = (os.environ.get("OPENGATEWAY_HARNESS") or "").strip()
    agent = (os.environ.get("OPENGATEWAY_AGENT_NAME") or "").strip()
    out.append(
        Check(
            "env.OPENGATEWAY_HARNESS",
            True,
            harness or "(unset — default mcp)",
            "info",
        )
    )
    out.append(
        Check(
            "env.OPENGATEWAY_AGENT_NAME",
            True,
            agent or "(unset)",
            "info",
        )
    )
    return out


def check_mcp_sdk() -> list[Check]:
    out: list[Check] = []
    try:
        ver = importlib.metadata.version("mcp")
    except importlib.metadata.PackageNotFoundError:
        return [
            Check(
                "mcp.package",
                False,
                "mcp not installed",
                "error",
                'uv sync  # requires "mcp>=1.0,<2" in this package',
            )
        ]
    out.append(Check("mcp.version", True, ver, "info"))
    # Major version 2 removed FastMCP path we use
    try:
        major = int(str(ver).split(".")[0])
    except ValueError:
        major = 0
    if major >= 2:
        out.append(
            Check(
                "mcp.pin",
                False,
                f"mcp {ver} is 2.x — FastMCP import will fail",
                "error",
                'Pin "mcp>=1.0,<2" in pyproject and uv sync',
            )
        )
    else:
        out.append(
            Check(
                "mcp.pin",
                True,
                f"mcp {ver} is 1.x (FastMCP supported)",
                "info",
            )
        )

    try:
        from mcp.server.fastmcp import FastMCP  # noqa: F401

        out.append(Check("mcp.fastmcp", True, "from mcp.server.fastmcp import FastMCP", "info"))
    except Exception as e:
        out.append(
            Check(
                "mcp.fastmcp",
                False,
                str(e),
                "error",
                'Pin mcp>=1.0,<2 and reinstall: uv sync',
            )
        )

    try:
        import opengateway.mcp_server  # noqa: F401

        out.append(Check("mcp.opengateway_module", True, "import opengateway.mcp_server", "info"))
    except Exception as e:
        out.append(
            Check(
                "mcp.opengateway_module",
                False,
                str(e),
                "error",
                "Fix import errors in mcp_server.py / dependencies",
            )
        )
    return out


def check_hub(
    base_url: str,
    *,
    timeout: float = 8.0,
    token: Optional[str] = None,
) -> tuple[list[Check], Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """Ping hub, test auth on /v1/rooms, return (checks, ping_json, rooms_payload)."""
    checks: list[Check] = []
    ping: Optional[dict[str, Any]] = None
    rooms_payload: Optional[dict[str, Any]] = None
    headers = _auth_headers(token)

    try:
        with httpx.Client(base_url=base_url, timeout=timeout, headers=headers) as c:
            r = c.get("/ping")
            if r.status_code != 200:
                checks.append(
                    Check(
                        "hub.ping",
                        False,
                        f"HTTP {r.status_code}",
                        "error",
                        f"Start hub or fix URL: opengateway serve / check {base_url}",
                    )
                )
                return checks, None, None
            ping = r.json()
            checks.append(
                Check(
                    "hub.ping",
                    True,
                    (
                        f"ok version={ping.get('version')} mode={ping.get('mode')} "
                        f"network={ping.get('network')} require_auth={ping.get('require_auth')} "
                        f"backend={ping.get('backend') or ping.get('db_path')}"
                    ),
                    "info",
                )
            )
            require_auth = bool(ping.get("require_auth"))

            # Auth: unauthenticated rooms probe (only if we can strip auth)
            with httpx.Client(base_url=base_url, timeout=timeout) as bare:
                bare_r = bare.get("/v1/rooms")
            if require_auth:
                if bare_r.status_code == 401:
                    checks.append(
                        Check(
                            "hub.auth_required",
                            True,
                            "GET /v1/rooms without token → 401 (expected)",
                            "info",
                        )
                    )
                else:
                    checks.append(
                        Check(
                            "hub.auth_required",
                            False,
                            f"require_auth=true but bare /v1/rooms → HTTP {bare_r.status_code}",
                            "warn",
                            "Hub may not be enforcing auth correctly.",
                        )
                    )
            else:
                checks.append(
                    Check(
                        "hub.auth_required",
                        True,
                        f"require_auth=false; bare /v1/rooms → HTTP {bare_r.status_code}",
                        "info",
                    )
                )

            # Authenticated rooms
            auth_r = c.get("/v1/rooms")
            if auth_r.status_code == 200:
                rooms_payload = auth_r.json()
                n = len((rooms_payload or {}).get("rooms") or [])
                checks.append(
                    Check(
                        "hub.rooms_auth",
                        True,
                        f"GET /v1/rooms with client auth → 200 ({n} rooms)",
                        "info",
                    )
                )
            elif auth_r.status_code == 401:
                checks.append(
                    Check(
                        "hub.rooms_auth",
                        False,
                        "GET /v1/rooms → 401 (token missing or invalid)",
                        "error",
                        "Set OPENGATEWAY_AUTH_TOKEN (device ogk_… or master). "
                        "MCP clients must send Authorization: Bearer …",
                    )
                )
            else:
                checks.append(
                    Check(
                        "hub.rooms_auth",
                        False,
                        f"GET /v1/rooms → HTTP {auth_r.status_code}: {auth_r.text[:200]}",
                        "error",
                        "Inspect hub logs / token scopes",
                    )
                )

            # /ping allowlist reminder
            if require_auth:
                checks.append(
                    Check(
                        "hub.ping_allowlist",
                        True,
                        "/ping is public — gateway_status can look healthy while /v1/* 401s",
                        "info",
                    )
                )
    except httpx.ConnectError as e:
        checks.append(
            Check(
                "hub.ping",
                False,
                f"connect failed: {e}",
                "error",
                f"Is the hub up? opengateway serve — or wrong OPENGATEWAY_URL ({base_url})",
            )
        )
    except Exception as e:
        checks.append(
            Check(
                "hub.ping",
                False,
                str(e),
                "error",
                f"Fix connectivity to {base_url}",
            )
        )
    return checks, ping, rooms_payload


def check_presence(
    base_url: str,
    rooms_payload: Optional[dict[str, Any]],
    *,
    timeout: float = 8.0,
    token: Optional[str] = None,
    max_rooms: int = 8,
) -> tuple[list[Check], Optional[dict[str, Any]]]:
    """Fetch participants for rooms; summarize listening vs joined."""
    checks: list[Check] = []
    summary: dict[str, Any] = {
        "rooms": [],
        "listening": [],
        "joined_not_listening": [],
        "offline": [],
    }
    rooms = (rooms_payload or {}).get("rooms") or []
    if not rooms:
        checks.append(
            Check(
                "radio.rooms",
                True,
                "no rooms (or rooms list unavailable)",
                "info",
                "Create a room in Live Ops or: opengateway create-room …",
            )
        )
        return checks, summary

    headers = _auth_headers(token)
    try:
        with httpx.Client(base_url=base_url, timeout=timeout, headers=headers) as c:
            for room in rooms[:max_rooms]:
                rid = room.get("id")
                rname = room.get("name") or rid
                if not rid:
                    continue
                pr = c.get(f"/v1/rooms/{rid}/participants")
                if pr.status_code != 200:
                    continue
                parts = (pr.json() or {}).get("participants") or []
                room_row = {
                    "id": rid,
                    "name": rname,
                    "listening": [],
                    "joined": [],
                    "offline": [],
                }
                for p in parts:
                    name = p.get("name") or "?"
                    harness = p.get("harness") or ""
                    presence = p.get("presence") or (
                        "joined" if p.get("status") == "online" else "offline"
                    )
                    label = f"{name}/{harness}"
                    entry = {
                        "name": name,
                        "harness": harness,
                        "presence": presence,
                        "room": rname,
                        "room_id": rid,
                    }
                    if presence == "listening":
                        summary["listening"].append(entry)
                        room_row["listening"].append(label)
                    elif presence == "joined":
                        summary["joined_not_listening"].append(entry)
                        room_row["joined"].append(label)
                    else:
                        summary["offline"].append(entry)
                        room_row["offline"].append(label)
                summary["rooms"].append(room_row)
    except Exception as e:
        checks.append(
            Check(
                "radio.presence",
                False,
                str(e),
                "warn",
                "Could not load participants — auth or network issue",
            )
        )
        return checks, summary

    n_listen = len(summary["listening"])
    n_joined = len(summary["joined_not_listening"])
    n_off = len(summary["offline"])
    detail = f"listening={n_listen} joined(not radio)={n_joined} offline={n_off}"
    if n_listen == 0 and (n_joined > 0 or n_off > 0 or rooms):
        checks.append(
            Check(
                "radio.listening",
                False,
                detail + " — no agent has radio on",
                "warn",
                "Agents only hear the room while long-polling. Run:\n"
                "  opengateway listen <room> --name <agent> --harness grok\n"
                "or in MCP: begin_im_mode + wait_for_messages loop forever.",
            )
        )
    elif n_listen == 0:
        checks.append(
            Check(
                "radio.listening",
                True,
                detail + " (empty room is fine)",
                "info",
            )
        )
    else:
        names = ", ".join(
            f"{e['name']}@{e['room']}" for e in summary["listening"][:6]
        )
        more = "…" if n_listen > 6 else ""
        checks.append(
            Check(
                "radio.listening",
                True,
                f"{detail} · radio-on: {names}{more}",
                "info",
            )
        )

    if n_joined > 0:
        names = ", ".join(
            f"{e['name']}@{e['room']}" for e in summary["joined_not_listening"][:6]
        )
        checks.append(
            Check(
                "radio.joined_stale",
                False,
                f"{n_joined} joined but not listening: {names}",
                "warn",
                "They look online but will miss messages/nudges. "
                "Start wait_for_messages or `opengateway listen`.",
            )
        )
    return checks, summary


def check_local_port(port: int = 8765) -> list[Check]:
    out: list[Check] = []
    for host in ("127.0.0.1",):
        try:
            with socket.create_connection((host, port), timeout=1.0):
                out.append(
                    Check(
                        f"local.{host}:{port}",
                        True,
                        "TCP accept",
                        "info",
                    )
                )
        except OSError as e:
            out.append(
                Check(
                    f"local.{host}:{port}",
                    False,
                    str(e),
                    "info",
                    "No local hub on this port (ok if using Railway only).",
                )
            )
    return out


def check_url_sanity(base_url: str) -> list[Check]:
    out: list[Check] = []
    try:
        u = urlparse(base_url)
        if u.scheme not in {"http", "https"}:
            out.append(
                Check(
                    "url.scheme",
                    False,
                    f"unexpected scheme {u.scheme!r}",
                    "error",
                    "Use http:// or https://",
                )
            )
        else:
            out.append(Check("url.scheme", True, u.scheme, "info"))
        host = u.hostname or ""
        if host in {"127.0.0.1", "localhost"}:
            out.append(
                Check(
                    "url.host",
                    True,
                    f"{host} (local hub — not Railway)",
                    "info",
                    "If you expected production UI, set "
                    "OPENGATEWAY_URL=https://….up.railway.app",
                )
            )
        elif "railway.app" in host:
            out.append(Check("url.host", True, host, "info"))
        else:
            out.append(Check("url.host", True, host or base_url, "info"))
    except Exception as e:
        out.append(Check("url.parse", False, str(e), "error"))
    return out


def run_doctor(
    base_url: str,
    *,
    port: int = 8765,
    include_network: bool = True,
    token: Optional[str] = None,
) -> DoctorReport:
    """Run full diagnostic suite against base_url."""
    base = base_url.rstrip("/")
    report = DoctorReport(base_url=base)

    report.checks.extend(check_url_sanity(base))
    report.checks.extend(check_env(base))
    report.checks.extend(check_mcp_sdk())
    report.checks.extend(check_local_port(port))

    hub_checks, ping, rooms = check_hub(base, token=token)
    report.checks.extend(hub_checks)
    report.ping = ping

    if rooms is not None or (ping and not any(c.name == "hub.rooms_auth" and not c.ok for c in hub_checks)):
        # Only presence if we could auth
        if not any(c.name == "hub.rooms_auth" and not c.ok for c in hub_checks):
            pres_checks, summary = check_presence(base, rooms, token=token)
            report.checks.extend(pres_checks)
            report.rooms_summary = summary

    if include_network:
        try:
            from opengateway.tailscale import network_diagnostics, tailscale_status

            report.tailscale = tailscale_status()
            report.network = network_diagnostics(port)
        except Exception as e:
            report.checks.append(
                Check(
                    "network.tailscale",
                    False,
                    str(e),
                    "warn",
                    "Tailscale diagnostics optional",
                )
            )

    # Aggregate recommendations
    for c in report.checks:
        if not c.ok and c.fix:
            if c.fix not in report.recommendations:
                report.recommendations.append(c.fix)

    if report.rooms_summary and not report.rooms_summary.get("listening"):
        msg = (
            "No listeners — start radio: "
            f"opengateway listen <room> --name grok --harness grok "
            f"(OPENGATEWAY_URL={base})"
        )
        if msg not in report.recommendations:
            # only if we had rooms or joined agents
            if report.rooms_summary.get("joined_not_listening") or report.rooms_summary.get(
                "rooms"
            ):
                report.recommendations.append(msg)

    return report
