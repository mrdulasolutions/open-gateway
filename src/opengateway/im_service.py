"""Install OpenGateway IM as a background OS service (launchd / systemd).

Keeps ``opengateway im`` running across logins, crashes, and reboots so Live
Ops feels like always-on IM without a terminal window.
"""

from __future__ import annotations

import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


def _slug(s: str) -> str:
    t = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return (t or "agent")[:48]


def service_label(name: str, room: str, custom: str = "") -> str:
    if custom.strip():
        return _slug(custom)
    return f"{_slug(name)}-{_slug(room)}"


def plist_id(label: str) -> str:
    return f"xyz.opengateways.im.{label}"


def unit_name(label: str) -> str:
    return f"opengateway-im-{label}.service"


@dataclass
class ImServiceSpec:
    room: str
    name: str
    harness: str
    wake: str
    role: str = "contributor"
    url: str = ""
    auth_token: str = ""
    participant_id: str = ""
    wake_webhook: str = ""
    wake_hook: str = ""
    hermes_bin: str = "hermes"
    hermes_skills: str = "opengateway-collab"
    hermes_max_turns: int = 20
    agent_max_turns: int = 20
    debounce: float = 1.5
    label: str = ""
    log_dir: Optional[Path] = None

    def resolved_label(self) -> str:
        return service_label(self.name, self.room, self.label)

    def hub_url(self) -> str:
        return (
            self.url
            or os.environ.get("OPENGATEWAY_URL")
            or "http://127.0.0.1:8765"
        ).rstrip("/")

    def token(self) -> str:
        return (
            self.auth_token
            or os.environ.get("OPENGATEWAY_AUTH_TOKEN")
            or os.environ.get("OPENGATEWAY_TOKEN")
            or ""
        ).strip()


def resolve_opengateway_bin() -> str:
    """Prefer real executable on PATH; fall back to current interpreter -m."""
    for name in ("opengateway", "opengateways"):
        p = shutil.which(name)
        if p:
            return p
    # Running from source / venv without console script
    return sys.executable


def im_argv(spec: ImServiceSpec, bin_path: str) -> list[str]:
    if bin_path == sys.executable:
        cmd = [bin_path, "-m", "opengateway", "im"]
    else:
        cmd = [bin_path, "im"]
    cmd += [
        spec.room,
        "--name",
        spec.name,
        "--harness",
        spec.harness,
        "--role",
        spec.role,
        "--wake",
        spec.wake,
        "--debounce",
        str(spec.debounce),
        "--hermes-bin",
        spec.hermes_bin,
        "--hermes-skills",
        spec.hermes_skills,
        "--hermes-max-turns",
        str(spec.hermes_max_turns),
        "--agent-max-turns",
        str(spec.agent_max_turns),
        "--quiet",
    ]
    if spec.url or spec.hub_url():
        cmd += ["--url", spec.hub_url()]
    if spec.participant_id:
        cmd += ["--participant-id", spec.participant_id]
    if spec.wake_webhook:
        cmd += ["--wake-webhook", spec.wake_webhook]
    if spec.wake_hook:
        cmd += ["--wake-hook", spec.wake_hook]
    return cmd


def default_log_dir(label: str) -> Path:
    home = Path.home()
    d = home / ".opengateway" / "im-services" / label
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── launchd (macOS) ─────────────────────────────────────────────────────────


def launchd_plist_path(label: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{plist_id(label)}.plist"


def render_launchd_plist(spec: ImServiceSpec, bin_path: str) -> str:
    label = spec.resolved_label()
    log_dir = spec.log_dir or default_log_dir(label)
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout = log_dir / "stdout.log"
    stderr = log_dir / "stderr.log"
    args = im_argv(spec, bin_path)
    # PATH so hermes/claude/uvx resolve when wake spawns
    path_env = os.environ.get("PATH") or "/usr/local/bin:/usr/bin:/bin"
    # Ensure common tool locations
    extras = [
        str(Path.home() / ".local" / "bin"),
        str(Path.home() / ".cargo" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
    ]
    for e in extras:
        if e not in path_env.split(":"):
            path_env = f"{e}:{path_env}"

    env = {
        "PATH": path_env,
        "HOME": str(Path.home()),
        "OPENGATEWAY_URL": spec.hub_url(),
        "OPENGATEWAY_AUTH_TOKEN": spec.token(),
        "OPENGATEWAY_AGENT_NAME": spec.name,
        "OPENGATEWAY_HARNESS": spec.harness,
        "OPENGATEWAY_IM_SERVICE": label,
    }
    # Keep user locale for hermes etc.
    for k in ("LANG", "LC_ALL", "TMPDIR"):
        if os.environ.get(k):
            env[k] = os.environ[k]

    def esc(s: str) -> str:
        return (
            s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    prog_args = "\n".join(f"    <string>{esc(a)}</string>" for a in args)
    env_xml = "\n".join(
        f"    <key>{esc(k)}</key>\n    <string>{esc(v)}</string>" for k, v in env.items()
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{esc(plist_id(label))}</string>
  <key>ProgramArguments</key>
  <array>
{prog_args}
  </array>
  <key>EnvironmentVariables</key>
  <dict>
{env_xml}
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>StandardOutPath</key>
  <string>{esc(str(stdout))}</string>
  <key>StandardErrorPath</key>
  <string>{esc(str(stderr))}</string>
  <key>WorkingDirectory</key>
  <string>{esc(str(Path.home()))}</string>
  <key>ProcessType</key>
  <string>Background</string>
</dict>
</plist>
"""


def launchd_install(spec: ImServiceSpec) -> dict[str, Any]:
    if not spec.token():
        raise ValueError(
            "OPENGATEWAY_AUTH_TOKEN required (env or --token) to install IM service"
        )
    label = spec.resolved_label()
    bin_path = resolve_opengateway_bin()
    plist = launchd_plist_path(label)
    plist.parent.mkdir(parents=True, exist_ok=True)
    body = render_launchd_plist(spec, bin_path)
    plist.write_text(body, encoding="utf-8")
    # unload if present, then load
    uid = os.getuid()
    domain = f"gui/{uid}"
    subprocess.run(
        ["launchctl", "bootout", domain, str(plist)],
        capture_output=True,
        text=True,
    )
    r = subprocess.run(
        ["launchctl", "bootstrap", domain, str(plist)],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        # older macOS: load -w
        r2 = subprocess.run(
            ["launchctl", "load", "-w", str(plist)],
            capture_output=True,
            text=True,
        )
        if r2.returncode != 0:
            raise RuntimeError(
                f"launchctl bootstrap failed: {r.stderr or r.stdout or r2.stderr}"
            )
    # kickstart
    subprocess.run(
        ["launchctl", "kickstart", "-k", f"{domain}/{plist_id(label)}"],
        capture_output=True,
        text=True,
    )
    log_dir = spec.log_dir or default_log_dir(label)
    return {
        "ok": True,
        "platform": "launchd",
        "label": label,
        "plist": str(plist),
        "plist_id": plist_id(label),
        "logs": str(log_dir),
        "bin": bin_path,
        "hub": spec.hub_url(),
        "room": spec.room,
        "name": spec.name,
        "wake": spec.wake,
    }


def launchd_uninstall(label: str) -> dict[str, Any]:
    label = _slug(label)
    plist = launchd_plist_path(label)
    uid = os.getuid()
    domain = f"gui/{uid}"
    subprocess.run(
        ["launchctl", "bootout", domain, str(plist)],
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["launchctl", "unload", "-w", str(plist)],
        capture_output=True,
        text=True,
    )
    existed = plist.is_file()
    if existed:
        plist.unlink()
    return {
        "ok": True,
        "platform": "launchd",
        "label": label,
        "removed": existed,
        "plist": str(plist),
    }


def launchd_status(label: str) -> dict[str, Any]:
    label = _slug(label)
    plist = launchd_plist_path(label)
    uid = os.getuid()
    r = subprocess.run(
        ["launchctl", "print", f"gui/{uid}/{plist_id(label)}"],
        capture_output=True,
        text=True,
    )
    running = r.returncode == 0
    log_dir = default_log_dir(label)
    return {
        "ok": True,
        "platform": "launchd",
        "label": label,
        "plist": str(plist),
        "plist_exists": plist.is_file(),
        "running": running,
        "launchctl": (r.stdout or r.stderr or "")[:1500],
        "logs": str(log_dir),
    }


def launchd_list() -> list[dict[str, Any]]:
    agents = Path.home() / "Library" / "LaunchAgents"
    out: list[dict[str, Any]] = []
    if not agents.is_dir():
        return out
    for p in sorted(agents.glob("xyz.opengateways.im.*.plist")):
        lab = p.name.replace("xyz.opengateways.im.", "").replace(".plist", "")
        st = launchd_status(lab)
        out.append(
            {
                "label": lab,
                "plist": str(p),
                "running": st.get("running"),
            }
        )
    return out


# ── systemd (Linux user) ────────────────────────────────────────────────────


def systemd_unit_path(label: str) -> Path:
    return (
        Path.home()
        / ".config"
        / "systemd"
        / "user"
        / unit_name(label)
    )


def render_systemd_unit(spec: ImServiceSpec, bin_path: str) -> str:
    label = spec.resolved_label()
    log_dir = spec.log_dir or default_log_dir(label)
    log_dir.mkdir(parents=True, exist_ok=True)
    args = im_argv(spec, bin_path)
    exec_start = " ".join(shlex.quote(a) for a in args)
    path_env = os.environ.get("PATH") or "/usr/local/bin:/usr/bin:/bin"
    extras = [str(Path.home() / ".local" / "bin")]
    for e in extras:
        if e not in path_env.split(":"):
            path_env = f"{e}:{path_env}"
    return f"""[Unit]
Description=OpenGateway IM seat ({spec.name} / {spec.room})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={exec_start}
Restart=always
RestartSec=10
Environment=PATH={path_env}
Environment=HOME={Path.home()}
Environment=OPENGATEWAY_URL={shlex.quote(spec.hub_url())}
Environment=OPENGATEWAY_AUTH_TOKEN={shlex.quote(spec.token())}
Environment=OPENGATEWAY_AGENT_NAME={shlex.quote(spec.name)}
Environment=OPENGATEWAY_HARNESS={shlex.quote(spec.harness)}
Environment=OPENGATEWAY_IM_SERVICE={shlex.quote(label)}
WorkingDirectory={Path.home()}
StandardOutput=append:{log_dir / "stdout.log"}
StandardError=append:{log_dir / "stderr.log"}

[Install]
WantedBy=default.target
"""


def systemd_install(spec: ImServiceSpec) -> dict[str, Any]:
    if not spec.token():
        raise ValueError(
            "OPENGATEWAY_AUTH_TOKEN required (env or --token) to install IM service"
        )
    label = spec.resolved_label()
    bin_path = resolve_opengateway_bin()
    unit = systemd_unit_path(label)
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(render_systemd_unit(spec, bin_path), encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    r = subprocess.run(
        ["systemctl", "--user", "enable", "--now", unit_name(label)],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr or r.stdout or "systemctl enable failed")
    return {
        "ok": True,
        "platform": "systemd",
        "label": label,
        "unit": str(unit),
        "logs": str(spec.log_dir or default_log_dir(label)),
        "bin": bin_path,
        "hub": spec.hub_url(),
        "room": spec.room,
        "name": spec.name,
        "wake": spec.wake,
    }


def systemd_uninstall(label: str) -> dict[str, Any]:
    label = _slug(label)
    unit = systemd_unit_path(label)
    subprocess.run(
        ["systemctl", "--user", "disable", "--now", unit_name(label)],
        capture_output=True,
        text=True,
    )
    existed = unit.is_file()
    if existed:
        unit.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    return {
        "ok": True,
        "platform": "systemd",
        "label": label,
        "removed": existed,
        "unit": str(unit),
    }


def systemd_status(label: str) -> dict[str, Any]:
    label = _slug(label)
    unit = systemd_unit_path(label)
    r = subprocess.run(
        ["systemctl", "--user", "status", unit_name(label), "--no-pager"],
        capture_output=True,
        text=True,
    )
    return {
        "ok": True,
        "platform": "systemd",
        "label": label,
        "unit": str(unit),
        "unit_exists": unit.is_file(),
        "running": r.returncode == 0,
        "systemctl": (r.stdout or r.stderr or "")[:1500],
        "logs": str(default_log_dir(label)),
    }


def systemd_list() -> list[dict[str, Any]]:
    d = Path.home() / ".config" / "systemd" / "user"
    out: list[dict[str, Any]] = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("opengateway-im-*.service")):
        lab = p.name.replace("opengateway-im-", "").replace(".service", "")
        st = systemd_status(lab)
        out.append({"label": lab, "unit": str(p), "running": st.get("running")})
    return out


# ── facade ──────────────────────────────────────────────────────────────────


def detect_platform() -> str:
    sysname = platform.system().lower()
    if sysname == "darwin":
        return "launchd"
    if sysname == "linux":
        return "systemd"
    return "unsupported"


def install_service(spec: ImServiceSpec, platform_name: str = "") -> dict[str, Any]:
    plat = (platform_name or detect_platform()).lower()
    if plat == "launchd":
        return launchd_install(spec)
    if plat == "systemd":
        return systemd_install(spec)
    raise RuntimeError(
        f"IM service install not supported on this OS ({platform.system()}). "
        "Use launchd (macOS) or systemd --user (Linux)."
    )


def uninstall_service(label: str, platform_name: str = "") -> dict[str, Any]:
    plat = (platform_name or detect_platform()).lower()
    if plat == "launchd":
        return launchd_uninstall(label)
    if plat == "systemd":
        return systemd_uninstall(label)
    raise RuntimeError(f"Unsupported platform: {plat}")


def service_status(label: str, platform_name: str = "") -> dict[str, Any]:
    plat = (platform_name or detect_platform()).lower()
    if plat == "launchd":
        return launchd_status(label)
    if plat == "systemd":
        return systemd_status(label)
    raise RuntimeError(f"Unsupported platform: {plat}")


def list_services(platform_name: str = "") -> list[dict[str, Any]]:
    plat = (platform_name or detect_platform()).lower()
    if plat == "launchd":
        return launchd_list()
    if plat == "systemd":
        return systemd_list()
    return []
