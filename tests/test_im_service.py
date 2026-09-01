"""IM service packaging helpers (no launchctl required)."""

from pathlib import Path

from opengateway.im_service import (
    ImServiceSpec,
    im_argv,
    plist_id,
    render_launchd_plist,
    render_systemd_unit,
    resolve_opengateway_bin,
    service_label,
)


def test_service_label_slug():
    assert service_label("Hermes COO", "main") == "hermes-coo-main"
    assert service_label("x", "y", custom="My Seat") == "my-seat"


def test_plist_id():
    assert plist_id("hermes-coo-main") == "xyz.opengateways.im.hermes-coo-main"


def test_im_argv_includes_room_and_wake():
    spec = ImServiceSpec(
        room="main",
        name="bot",
        harness="hermes",
        wake="auto",
        url="https://acme.hub.opengateways.xyz",
    )
    argv = im_argv(spec, "/usr/bin/opengateway")
    assert argv[0] == "/usr/bin/opengateway"
    assert "im" in argv
    assert "main" in argv
    assert "--wake" in argv
    assert "auto" in argv
    assert "--quiet" in argv


def test_render_launchd_plist_contains_token_and_keepalive(tmp_path: Path):
    spec = ImServiceSpec(
        room="main",
        name="Hermes COO",
        harness="hermes",
        wake="hermes",
        url="https://acme.hub.opengateways.xyz",
        auth_token="ogk_test_token",
        label="test-seat",
        log_dir=tmp_path / "logs",
    )
    xml = render_launchd_plist(spec, "/usr/local/bin/opengateway")
    assert "xyz.opengateways.im.test-seat" in xml
    assert "ogk_test_token" in xml
    assert "KeepAlive" in xml
    assert "RunAtLoad" in xml
    assert "https://acme.hub.opengateways.xyz" in xml
    assert "opengateway" in xml
    assert str(tmp_path / "logs") in xml or "stdout.log" in xml


def test_render_systemd_unit():
    spec = ImServiceSpec(
        room="ops",
        name="bot",
        harness="hermes",
        wake="auto",
        auth_token="ogk_x",
        url="https://hub.example",
        label="bot-ops",
    )
    unit = render_systemd_unit(spec, "/bin/opengateway")
    assert "ExecStart=" in unit
    assert "Restart=always" in unit
    assert "ogk_x" in unit
    assert "WantedBy=default.target" in unit


def test_resolve_bin_returns_string():
    b = resolve_opengateway_bin()
    assert isinstance(b, str)
    assert b
