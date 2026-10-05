"""Install the paired managed runner as an always-on user service."""

from __future__ import annotations

import os
import platform
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from opengateway.im_service import resolve_opengateway_bin


SERVICE_ID = "xyz.opengateways.runner"
UNIT_NAME = "opengateway-runner.service"


def service_log_dir() -> Path:
    path = Path.home() / ".opengateway" / "runner-service"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def runner_argv(bin_path: str) -> list[str]:
    if bin_path == sys.executable:
        return [bin_path, "-m", "opengateway", "runner", "start"]
    return [bin_path, "runner", "start"]


def _service_path() -> str:
    value = os.environ.get("PATH") or "/usr/local/bin:/usr/bin:/bin"
    for extra in (
        str(Path.home() / ".local" / "bin"),
        str(Path.home() / ".cargo" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
    ):
        if extra not in value.split(":"):
            value = f"{extra}:{value}"
    return value


def launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{SERVICE_ID}.plist"


def render_launchd_plist(bin_path: str) -> str:
    log_dir = service_log_dir()

    def esc(value: str) -> str:
        return (
            value.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    args = "\n".join(
        f"    <string>{esc(arg)}</string>" for arg in runner_argv(bin_path)
    )
    env = {
        "PATH": _service_path(),
        "HOME": str(Path.home()),
        "OPENGATEWAY_RUNNER_SERVICE": "1",
    }
    for key in ("LANG", "LC_ALL", "TMPDIR"):
        if os.environ.get(key):
            env[key] = str(os.environ[key])
    env_xml = "\n".join(
        f"    <key>{esc(key)}</key>\n    <string>{esc(value)}</string>"
        for key, value in env.items()
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{SERVICE_ID}</string>
  <key>ProgramArguments</key>
  <array>
{args}
  </array>
  <key>EnvironmentVariables</key>
  <dict>
{env_xml}
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>StandardOutPath</key>
  <string>{esc(str(log_dir / "stdout.log"))}</string>
  <key>StandardErrorPath</key>
  <string>{esc(str(log_dir / "stderr.log"))}</string>
  <key>WorkingDirectory</key>
  <string>{esc(str(Path.home()))}</string>
  <key>ProcessType</key>
  <string>Background</string>
</dict>
</plist>
"""


def _launchd_install() -> dict[str, Any]:
    bin_path = resolve_opengateway_bin()
    plist = launchd_plist_path()
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_text(render_launchd_plist(bin_path), encoding="utf-8")
    uid = os.getuid()
    domain = f"gui/{uid}"
    subprocess.run(
        ["launchctl", "bootout", domain, str(plist)],
        capture_output=True,
        text=True,
    )
    result = subprocess.run(
        ["launchctl", "bootstrap", domain, str(plist)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        fallback = subprocess.run(
            ["launchctl", "load", "-w", str(plist)],
            capture_output=True,
            text=True,
        )
        if fallback.returncode != 0:
            raise RuntimeError(
                result.stderr
                or result.stdout
                or fallback.stderr
                or "launchctl bootstrap failed"
            )
    subprocess.run(
        ["launchctl", "kickstart", "-k", f"{domain}/{SERVICE_ID}"],
        capture_output=True,
        text=True,
    )
    return {
        "ok": True,
        "platform": "launchd",
        "running": True,
        "service": SERVICE_ID,
        "plist": str(plist),
        "logs": str(service_log_dir()),
    }


def _launchd_uninstall() -> dict[str, Any]:
    plist = launchd_plist_path()
    domain = f"gui/{os.getuid()}"
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
        "running": False,
        "removed": existed,
        "plist": str(plist),
    }


def _launchd_status() -> dict[str, Any]:
    plist = launchd_plist_path()
    result = subprocess.run(
        ["launchctl", "print", f"gui/{os.getuid()}/{SERVICE_ID}"],
        capture_output=True,
        text=True,
    )
    return {
        "ok": True,
        "platform": "launchd",
        "service": SERVICE_ID,
        "installed": plist.is_file(),
        "running": result.returncode == 0,
        "plist": str(plist),
        "logs": str(service_log_dir()),
    }


def systemd_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / UNIT_NAME


def render_systemd_unit(bin_path: str) -> str:
    argv = " ".join(shlex.quote(arg) for arg in runner_argv(bin_path))
    log_dir = service_log_dir()
    return f"""[Unit]
Description=OpenGateway managed agent runner
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={argv}
Restart=on-failure
RestartSec=10
Environment="PATH={_service_path()}"
Environment="HOME={Path.home()}"
Environment="OPENGATEWAY_RUNNER_SERVICE=1"
WorkingDirectory={shlex.quote(str(Path.home()))}
StandardOutput=append:{log_dir / "stdout.log"}
StandardError=append:{log_dir / "stderr.log"}

[Install]
WantedBy=default.target
"""


def _systemd_install() -> dict[str, Any]:
    bin_path = resolve_opengateway_bin()
    unit = systemd_unit_path()
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(render_systemd_unit(bin_path), encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    result = subprocess.run(
        ["systemctl", "--user", "enable", "--now", UNIT_NAME],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr or result.stdout or "systemctl enable failed"
        )
    return {
        "ok": True,
        "platform": "systemd",
        "running": True,
        "service": UNIT_NAME,
        "unit": str(unit),
        "logs": str(service_log_dir()),
    }


def _systemd_uninstall() -> dict[str, Any]:
    unit = systemd_unit_path()
    subprocess.run(
        ["systemctl", "--user", "disable", "--now", UNIT_NAME],
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
        "running": False,
        "removed": existed,
        "unit": str(unit),
    }


def _systemd_status() -> dict[str, Any]:
    unit = systemd_unit_path()
    result = subprocess.run(
        ["systemctl", "--user", "is-active", UNIT_NAME],
        capture_output=True,
        text=True,
    )
    return {
        "ok": True,
        "platform": "systemd",
        "service": UNIT_NAME,
        "installed": unit.is_file(),
        "running": result.returncode == 0,
        "unit": str(unit),
        "logs": str(service_log_dir()),
    }


def detect_platform() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "launchd"
    if system == "linux":
        return "systemd"
    return "unsupported"


def install_runner_service(platform_name: str = "") -> dict[str, Any]:
    selected = (platform_name or detect_platform()).lower()
    if selected == "launchd":
        return _launchd_install()
    if selected == "systemd":
        return _systemd_install()
    raise RuntimeError(
        f"Runner service install is not supported on {platform.system()}"
    )


def uninstall_runner_service(platform_name: str = "") -> dict[str, Any]:
    selected = (platform_name or detect_platform()).lower()
    if selected == "launchd":
        return _launchd_uninstall()
    if selected == "systemd":
        return _systemd_uninstall()
    return {
        "ok": True,
        "platform": selected,
        "running": False,
        "removed": False,
    }


def runner_service_status(platform_name: str = "") -> dict[str, Any]:
    selected = (platform_name or detect_platform()).lower()
    if selected == "launchd":
        return _launchd_status()
    if selected == "systemd":
        return _systemd_status()
    return {
        "ok": True,
        "platform": selected,
        "installed": False,
        "running": False,
    }
