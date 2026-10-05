from __future__ import annotations

from pathlib import Path

from opengateway.runner_service import (
    render_launchd_plist,
    render_systemd_unit,
    runner_argv,
)


def test_runner_service_argv_is_fixed() -> None:
    assert runner_argv("/usr/local/bin/opengateways") == [
        "/usr/local/bin/opengateways",
        "runner",
        "start",
    ]


def test_launchd_runner_service_contains_no_credentials(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENGATEWAY_AUTH_TOKEN", "must-not-leak")
    body = render_launchd_plist("/usr/local/bin/opengateways")
    assert "xyz.opengateways.runner" in body
    assert "<string>runner</string>" in body
    assert "<string>start</string>" in body
    assert "KeepAlive" in body
    assert "SuccessfulExit" in body
    assert "must-not-leak" not in body
    assert "OPENGATEWAY_AUTH_TOKEN" not in body


def test_systemd_runner_service_contains_no_credentials(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENGATEWAY_RUNNER_TOKEN", "must-not-leak")
    body = render_systemd_unit("/usr/bin/opengateways")
    assert "ExecStart=/usr/bin/opengateways runner start" in body
    assert "Restart=on-failure" in body
    assert "must-not-leak" not in body
    assert "OPENGATEWAY_RUNNER_TOKEN" not in body
