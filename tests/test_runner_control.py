"""Managed-runner control-plane security and lifecycle regressions."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from opengateway.config import GatewayConfig, GatewayMode
from opengateway.models import ManagedAgentStatus, RunnerStatus, new_id, utcnow
from opengateway.server import create_app
from opengateway.store import Store


MASTER = "runner-control-master-secret"


def _config(*, auth: bool = True) -> GatewayConfig:
    return GatewayConfig(
        mode=GatewayMode.PUBLIC if auth else GatewayMode.INTERNAL,
        host="127.0.0.1",
        port=8765,
        auth_token=MASTER if auth else None,
        require_auth=auth,
        network="lan" if auth else "loopback",
        name="runner-control",
    )


def _master_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {MASTER}"}


async def _pair_and_redeem(
    client: AsyncClient,
    *,
    admin_headers: dict[str, str] | None = None,
    name: str = "local-runner",
) -> tuple[dict[str, Any], str, str]:
    pair_response = await client.post(
        "/v1/runners/pair",
        json={"name": name},
        headers=admin_headers,
    )
    assert pair_response.status_code == 200, pair_response.text
    pair = pair_response.json()
    redeemed_response = await client.post(
        "/v1/runners/redeem",
        json={
            "code": pair["code"],
            "name": name,
            "hostname": "test-host",
            "platform": "pytest",
            "version": "1.0",
            "capabilities": {
                "managed_agents": True,
                "harnesses": {"claude-code": {"available": True}},
            },
        },
    )
    assert redeemed_response.status_code == 200, redeemed_response.text
    redeemed = redeemed_response.json()
    heartbeat = await client.post(
        f"/v1/runners/{redeemed['runner']['id']}/heartbeat",
        json={},
        headers={"Authorization": f"Bearer {redeemed['token']}"},
    )
    assert heartbeat.status_code == 200, heartbeat.text
    return pair, redeemed["runner"]["id"], redeemed["token"]


@pytest.mark.asyncio
async def test_pairing_is_single_use_hashed_and_runner_isolated(tmp_path: Path):
    db_path = tmp_path / "runner-pair.db"
    store = Store(db_path=db_path, audit=True)
    app = create_app(store=store, config=_config())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        unauthenticated = await client.post(
            "/v1/runners/pair", json={"name": "blocked"}
        )
        assert unauthenticated.status_code == 401

        pair, runner_id, runner_token = await _pair_and_redeem(
            client, admin_headers=_master_headers()
        )
        assert len(pair["code"].replace("-", "")) == 12
        assert pair["code"].encode() not in db_path.read_bytes()

        reused = await client.post(
            "/v1/runners/redeem",
            json={
                "code": pair["code"],
                "name": "replay",
                "hostname": "other",
                "platform": "pytest",
                "version": "1",
                "capabilities": {},
            },
        )
        assert reused.status_code == 404

        runner_headers = {"Authorization": f"Bearer {runner_token}"}
        own = await client.post(
            f"/v1/runners/{runner_id}/heartbeat",
            json={},
            headers=runner_headers,
        )
        assert own.status_code == 200
        wrong_id = await client.post(
            f"/v1/runners/{new_id()}/heartbeat",
            json={},
            headers=runner_headers,
        )
        assert wrong_id.status_code == 403

        admin_only = await client.get("/v1/runners", headers=runner_headers)
        assert admin_only.status_code == 403
        listed = (
            await client.get("/v1/runners", headers=_master_headers())
        ).json()["runners"]
        assert listed[0]["id"] == runner_id
        assert "api_key_id" not in listed[0]
        assert runner_token not in str(listed)
        assert pair["code"] not in str(listed)
    store._db.close()


@pytest.mark.asyncio
async def test_typed_start_claim_returns_agent_token_once(tmp_path: Path):
    db_path = tmp_path / "runner-claim.db"
    store = Store(db_path=db_path, audit=True)
    app = create_app(store=store, config=_config())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin = _master_headers()
        room = (
            await client.post(
                "/v1/rooms", json={"name": "managed"}, headers=admin
            )
        ).json()
        other_room = (
            await client.post(
                "/v1/rooms", json={"name": "private"}, headers=admin
            )
        ).json()
        _, runner_id, runner_token = await _pair_and_redeem(
            client, admin_headers=admin
        )
        runner = {"Authorization": f"Bearer {runner_token}"}

        rejected = await client.post(
            "/v1/managed-agents",
            json={
                "name": "bot",
                "harness": "claude-code",
                "room_id": room["id"],
                "runner_id": runner_id,
                "command": "rm -rf /",
            },
            headers=admin,
        )
        assert rejected.status_code == 422
        unsupported = await client.post(
            "/v1/managed-agents",
            json={
                "name": "cursor-seat",
                "harness": "cursor",
                "room_id": room["id"],
                "runner_id": runner_id,
            },
            headers=admin,
        )
        assert unsupported.status_code == 422

        created_response = await client.post(
            "/v1/managed-agents",
            json={
                "name": "bot",
                "harness": "claude-code",
                "room_id": room["id"],
                "runner_id": runner_id,
            },
            headers=admin,
        )
        assert created_response.status_code == 200, created_response.text
        created = created_response.json()
        job = created["job"]
        assert job["action"] == "start"
        assert job["status"] == "queued"
        assert not ({"command", "env", "path", "args"} & set(job))
        duplicate = await client.post(
            "/v1/managed-agents",
            json={
                "name": "BOT",
                "harness": "grok",
                "room_id": room["id"],
                "runner_id": runner_id,
            },
            headers=admin,
        )
        assert duplicate.status_code == 400

        first, second = await asyncio.gather(
            client.post(
                f"/v1/runners/{runner_id}/jobs/{job['id']}/claim",
                json={},
                headers=runner,
            ),
            client.post(
                f"/v1/runners/{runner_id}/jobs/{job['id']}/claim",
                json={},
                headers=runner,
            ),
        )
        claims = [first.json(), second.json()]
        with_token = [item for item in claims if item.get("agent_token")]
        assert len(with_token) == 1
        agent_token = with_token[0]["agent_token"]
        assert agent_token.startswith("ogk_")
        assert sum("agent_token" in item for item in claims) == 1

        claimed_key = await store.verify_api_key(agent_token)
        assert claimed_key is not None
        assert set(claimed_key["scopes"]) == {"read", "write", "tools"}
        metadata = claimed_key["metadata"]
        assert metadata["managed_agent_id"] == created["agent"]["id"]
        assert metadata["room_id"] == room["id"]
        assert metadata["identity"]["name"] == "bot"

        agent_headers = {"Authorization": f"Bearer {agent_token}"}
        rooms = (await client.get("/v1/rooms", headers=agent_headers)).json()[
            "rooms"
        ]
        assert [item["id"] for item in rooms] == [room["id"]]
        outside = await client.get(
            f"/v1/rooms/{other_room['id']}", headers=agent_headers
        )
        assert outside.status_code == 404
        joined = (
            await client.post(
                f"/v1/rooms/{room['id']}/join",
                headers=agent_headers,
                json={
                    "name": "bot",
                    "harness": "claude-code",
                    "capabilities": ["listen"],
                },
            )
        ).json()
        assert joined["metadata"]["managed_agent_id"] == created["agent"]["id"]
        assert joined["metadata"]["room_id"] == room["id"]

        completed = await client.post(
            f"/v1/runners/{runner_id}/jobs/{job['id']}/complete",
            headers=runner,
            json={
                "success": True,
                "result": {
                    "echo": agent_token,
                    "authorization": f"Bearer {agent_token}",
                },
            },
        )
        assert completed.status_code == 200
        assert completed.json()["job"]["status"] == "completed"
        assert agent_token not in str(completed.json())
        assert agent_token.encode() not in db_path.read_bytes()
        assert agent_token not in str(store.runner_jobs)
    store._db.close()
    reloaded = Store(db_path=db_path)
    assert runner_id in reloaded.runners
    assert created["agent"]["id"] in reloaded.managed_agents
    assert job["id"] in reloaded.runner_jobs
    assert agent_token not in str(reloaded.runner_jobs)
    reloaded._db.close()


@pytest.mark.asyncio
async def test_managed_agent_start_stop_restart_delete_and_scrubbed_logs(
    tmp_path: Path,
):
    store = Store(db_path=tmp_path / "runner-lifecycle.db", audit=True)
    store.RUNNER_LOG_LIMIT = 3
    app = create_app(store=store, config=_config())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin = _master_headers()
        room = (
            await client.post(
                "/v1/rooms", json={"name": "lifecycle"}, headers=admin
            )
        ).json()
        _, runner_id, runner_token = await _pair_and_redeem(
            client, admin_headers=admin
        )
        runner = {"Authorization": f"Bearer {runner_token}"}
        created = (
            await client.post(
                "/v1/managed-agents",
                json={
                    "name": "worker",
                    "harness": "hermes",
                    "room_id": room["id"],
                    "runner_id": runner_id,
                },
                headers=admin,
            )
        ).json()
        agent_id = created["agent"]["id"]
        start_id = created["job"]["id"]
        start_claim = (
            await client.post(
                f"/v1/runners/{runner_id}/jobs/{start_id}/claim",
                json={},
                headers=runner,
            )
        ).json()
        first_agent_token = start_claim["agent_token"]
        await client.post(
            f"/v1/runners/{runner_id}/jobs/{start_id}/complete",
            json={"success": True, "result": {"participant_id": "seat-1"}},
            headers=runner,
        )
        assert (await store.get_managed_agent(agent_id)).participant_id == "seat-1"

        for index in range(5):
            logged = await client.post(
                f"/v1/runners/{runner_id}/logs",
                json={
                    "managed_agent_id": agent_id,
                    "level": "info",
                    "message": (
                        f"line {index} Bearer {first_agent_token} "
                        f"token={first_agent_token}"
                    ),
                },
                headers=runner,
            )
            assert logged.status_code == 200
            assert first_agent_token not in logged.text
        logs = (
            await client.get(
                f"/v1/managed-agents/{agent_id}/logs", headers=admin
            )
        ).json()["logs"]
        assert len(logs) == 3
        assert first_agent_token not in str(logs)
        store.RUNNER_LOG_RATE_LIMIT = 5
        limited = await client.post(
            f"/v1/runners/{runner_id}/logs",
            json={
                "managed_agent_id": agent_id,
                "level": "info",
                "message": "one line too many",
            },
            headers=runner,
        )
        assert limited.status_code == 429
        assert limited.headers["retry-after"] == "60"

        stop = (
            await client.post(
                f"/v1/managed-agents/{agent_id}/actions/stop",
                headers=admin,
            )
        ).json()
        assert stop["agent"]["status"] == "stopping"
        # The old key stays valid just long enough for the runner to leave the
        # room cleanly; completion revokes it.
        assert await store.verify_api_key(first_agent_token) is not None
        stop_claim = (
            await client.post(
                f"/v1/runners/{runner_id}/jobs/{stop['job']['id']}/claim",
                json={},
                headers=runner,
            )
        ).json()
        assert "agent_token" not in stop_claim
        await client.post(
            f"/v1/runners/{runner_id}/jobs/{stop['job']['id']}/complete",
            json={"success": True, "result": {}},
            headers=runner,
        )
        assert await store.verify_api_key(first_agent_token) is None
        assert (await store.get_managed_agent(agent_id)).status == ManagedAgentStatus.STOPPED

        start = (
            await client.post(
                f"/v1/managed-agents/{agent_id}/actions/start",
                headers=admin,
            )
        ).json()
        second_agent_token = (
            await client.post(
                f"/v1/runners/{runner_id}/jobs/{start['job']['id']}/claim",
                json={},
                headers=runner,
            )
        ).json()["agent_token"]
        await client.post(
            f"/v1/runners/{runner_id}/jobs/{start['job']['id']}/complete",
            json={"success": True, "result": {}},
            headers=runner,
        )

        restart = (
            await client.post(
                f"/v1/managed-agents/{agent_id}/actions/restart",
                headers=admin,
            )
        ).json()
        assert await store.verify_api_key(second_agent_token) is not None
        restarted_token = (
            await client.post(
                f"/v1/runners/{runner_id}/jobs/{restart['job']['id']}/claim",
                json={},
                headers=runner,
            )
        ).json()["agent_token"]
        await client.post(
            f"/v1/runners/{runner_id}/jobs/{restart['job']['id']}/complete",
            json={"success": True, "result": {}},
            headers=runner,
        )
        assert await store.verify_api_key(second_agent_token) is None

        deleted = (
            await client.delete(
                f"/v1/managed-agents/{agent_id}", headers=admin
            )
        ).json()
        assert deleted["status"] == "deleting"
        assert await store.verify_api_key(restarted_token) is not None
        visible = (
            await client.get("/v1/managed-agents", headers=admin)
        ).json()["agents"]
        deleting_agent = next(item for item in visible if item["id"] == agent_id)
        assert deleting_agent["status"] == "deleting"
        delete_claim = (
            await client.post(
                f"/v1/runners/{runner_id}/jobs/{deleted['job']['id']}/claim",
                json={},
                headers=runner,
            )
        ).json()
        assert "agent_token" not in delete_claim
        await client.post(
            f"/v1/runners/{runner_id}/jobs/{deleted['job']['id']}/complete",
            json={"success": True, "result": {}},
            headers=runner,
        )
        assert await store.verify_api_key(restarted_token) is None
        assert (await store.get_managed_agent(agent_id)).status == ManagedAgentStatus.DELETED
    store._db.close()


@pytest.mark.asyncio
async def test_managed_agent_move_updates_room_and_mints_target_key(
    tmp_path: Path,
):
    store = Store(db_path=tmp_path / "runner-move.db", audit=True)
    app = create_app(store=store, config=_config())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin = _master_headers()
        room_a = (
            await client.post(
                "/v1/rooms", json={"name": "room-a"}, headers=admin
            )
        ).json()
        room_b = (
            await client.post(
                "/v1/rooms", json={"name": "room-b"}, headers=admin
            )
        ).json()
        _, runner_id, runner_token = await _pair_and_redeem(
            client, admin_headers=admin
        )
        runner = {"Authorization": f"Bearer {runner_token}"}
        created = (
            await client.post(
                "/v1/managed-agents",
                json={
                    "name": "mover",
                    "harness": "claude-code",
                    "room_id": room_a["id"],
                    "runner_id": runner_id,
                },
                headers=admin,
            )
        ).json()
        agent_id = created["agent"]["id"]
        start_id = created["job"]["id"]
        start_claim = (
            await client.post(
                f"/v1/runners/{runner_id}/jobs/{start_id}/claim",
                json={},
                headers=runner,
            )
        ).json()
        first_token = start_claim["agent_token"]
        await client.post(
            f"/v1/runners/{runner_id}/jobs/{start_id}/complete",
            json={"success": True, "result": {"participant_id": "seat-a"}},
            headers=runner,
        )
        key_a = await store.verify_api_key(first_token)
        assert key_a is not None
        assert key_a["metadata"]["room_id"] == room_a["id"]

        moved = await client.post(
            f"/v1/managed-agents/{agent_id}/actions/move",
            json={"room_id": room_b["id"]},
            headers=admin,
        )
        assert moved.status_code == 200, moved.text
        body = moved.json()
        assert body["job"]["action"] == "move"
        assert body["job"]["target_room_id"] == room_b["id"]
        assert body["agent"]["status"] == "restarting"

        move_id = body["job"]["id"]
        move_claim = (
            await client.post(
                f"/v1/runners/{runner_id}/jobs/{move_id}/claim",
                json={},
                headers=runner,
            )
        ).json()
        assert "agent_token" not in move_claim

        await client.post(
            f"/v1/runners/{runner_id}/jobs/{move_id}/complete",
            json={
                "success": True,
                "result": {"participant_id": "seat-b", "room_id": room_b["id"]},
            },
            headers=runner,
        )
        assert await store.verify_api_key(first_token) is not None
        agent = await store.get_managed_agent(agent_id)
        assert agent is not None
        assert agent.room_id == room_b["id"]
        assert agent.room_ids == [room_b["id"]]
        assert agent.seats.get(room_b["id"]) == "seat-b"
        assert agent.participant_id == "seat-b"
        assert agent.status == ManagedAgentStatus.RUNNING

        same_room = await client.post(
            f"/v1/managed-agents/{agent_id}/actions/move",
            json={"room_id": room_b["id"]},
            headers=admin,
        )
        assert same_room.status_code == 409
    store._db.close()


@pytest.mark.asyncio
async def test_managed_agent_joins_second_room_and_failed_join_keeps_the_first(
    tmp_path: Path,
):
    store = Store(db_path=tmp_path / "runner-membership.db", audit=True)
    app = create_app(store=store, config=_config())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin = _master_headers()
        room_a = (
            await client.post("/v1/rooms", json={"name": "room-a"}, headers=admin)
        ).json()
        room_b = (
            await client.post("/v1/rooms", json={"name": "room-b"}, headers=admin)
        ).json()
        room_c = (
            await client.post("/v1/rooms", json={"name": "room-c"}, headers=admin)
        ).json()
        _, runner_id, runner_token = await _pair_and_redeem(
            client, admin_headers=admin
        )
        runner = {"Authorization": f"Bearer {runner_token}"}
        created = (
            await client.post(
                "/v1/managed-agents",
                json={
                    "name": "member",
                    "harness": "grok",
                    "room_id": room_a["id"],
                    "runner_id": runner_id,
                },
                headers=admin,
            )
        ).json()
        agent_id = created["agent"]["id"]
        start_id = created["job"]["id"]
        start_claim = (
            await client.post(
                f"/v1/runners/{runner_id}/jobs/{start_id}/claim",
                json={},
                headers=runner,
            )
        ).json()
        token = start_claim["agent_token"]
        await client.post(
            f"/v1/runners/{runner_id}/jobs/{start_id}/complete",
            json={"success": True, "result": {"participant_id": "seat-a"}},
            headers=runner,
        )

        joined = await client.post(
            f"/v1/managed-agents/{agent_id}/rooms",
            json={"room_id": room_b["id"]},
            headers=admin,
        )
        assert joined.status_code == 200, joined.text
        body = joined.json()
        assert body["job"]["action"] == "join_room"
        assert room_b["id"] in body["agent"]["room_ids"]
        assert room_a["id"] in body["agent"]["room_ids"]
        join_claim = (
            await client.post(
                f"/v1/runners/{runner_id}/jobs/{body['job']['id']}/claim",
                json={},
                headers=runner,
            )
        ).json()
        assert "agent_token" not in join_claim
        await client.post(
            f"/v1/runners/{runner_id}/jobs/{body['job']['id']}/complete",
            json={"success": True, "result": {"participant_id": "seat-b"}},
            headers=runner,
        )
        agent = await store.get_managed_agent(agent_id)
        assert agent is not None
        assert agent.room_ids == [room_a["id"], room_b["id"]]
        assert agent.seats[room_b["id"]] == "seat-b"
        assert agent.seats[room_a["id"]] == "seat-a"
        assert await store.verify_api_key(token) is not None

        failed = await client.post(
            f"/v1/managed-agents/{agent_id}/rooms",
            json={"room_id": room_c["id"]},
            headers=admin,
        )
        assert failed.status_code == 200, failed.text
        fail_body = failed.json()
        claimed_fail = await client.post(
            f"/v1/runners/{runner_id}/jobs/{fail_body['job']['id']}/claim",
            json={},
            headers=runner,
        )
        assert claimed_fail.status_code == 200, claimed_fail.text
        failed_complete = await client.post(
            f"/v1/runners/{runner_id}/jobs/{fail_body['job']['id']}/complete",
            json={"success": False, "error": "join failed"},
            headers=runner,
        )
        assert failed_complete.status_code == 200, failed_complete.text
        agent = await store.get_managed_agent(agent_id)
        assert agent is not None
        assert room_c["id"] not in agent.room_ids
        assert agent.room_ids == [room_a["id"], room_b["id"]]
        assert agent.last_error == "join failed"
        assert await store.verify_api_key(token) is not None

        left = await client.delete(
            f"/v1/managed-agents/{agent_id}/rooms/{room_a['id']}",
            headers=admin,
        )
        assert left.status_code == 200, left.text
        leave_body = left.json()
        assert leave_body["job"]["action"] == "leave_room"
        await client.post(
            f"/v1/runners/{runner_id}/jobs/{leave_body['job']['id']}/claim",
            json={},
            headers=runner,
        )
        await client.post(
            f"/v1/runners/{runner_id}/jobs/{leave_body['job']['id']}/complete",
            json={"success": True, "result": {}},
            headers=runner,
        )
        agent = await store.get_managed_agent(agent_id)
        assert agent is not None
        assert agent.room_ids == [room_b["id"]]
        assert agent.room_id == room_b["id"]
    store._db.close()


@pytest.mark.asyncio
async def test_runner_binding_and_stale_status_when_hub_auth_is_disabled():
    store = Store(db_path=None)
    app = create_app(store=store, config=_config(auth=False))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        _, runner_id, runner_token = await _pair_and_redeem(client)
        headers = {"Authorization": f"Bearer {runner_token}"}

        missing = await client.post(
            f"/v1/runners/{runner_id}/heartbeat", json={}
        )
        assert missing.status_code == 401
        wrong = await client.post(
            f"/v1/runners/{new_id()}/heartbeat", json={}, headers=headers
        )
        assert wrong.status_code == 403
        assert (
            await client.post(
                f"/v1/runners/{runner_id}/heartbeat",
                json={},
                headers=headers,
            )
        ).status_code == 200

        # Open hubs still require runner/admin role separation when a runner
        # presents its scoped credential.
        assert (await client.get("/v1/runners", headers=headers)).status_code == 403

        stored = store.runners[runner_id]
        stored.last_seen_at = utcnow() - timedelta(
            seconds=store.RUNNER_STALE_SECONDS + 1
        )
        listed = (await client.get("/v1/runners")).json()["runners"]
        assert listed[0]["status"] == RunnerStatus.STALE.value


@pytest.mark.asyncio
async def test_session_admin_can_manage_runners_but_member_cannot(tmp_path: Path):
    store = Store(db_path=tmp_path / "runner-session.db", audit=True)
    app = create_app(store=store, config=_config())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin_registration = (
            await client.post(
                "/v1/auth/register",
                json={
                    "email": "admin@runner.test",
                    "password": "password123",
                    "org_name": "Runner Org",
                },
            )
        ).json()
        admin_headers = {
            "Authorization": f"Bearer {admin_registration['token']}"
        }
        assert (
            await client.post(
                "/v1/runners/pair",
                json={"name": "session-runner"},
                headers=admin_headers,
            )
        ).status_code == 200

        invite = (
            await client.post(
                "/v1/auth/invite", json={}, headers=admin_headers
            )
        ).json()
        member_registration = (
            await client.post(
                "/v1/auth/register",
                json={
                    "email": "member@runner.test",
                    "password": "password123",
                    "invite_code": invite["code"],
                },
            )
        ).json()
        member_headers = {
            "Authorization": f"Bearer {member_registration['token']}"
        }
        denied = await client.post(
            "/v1/runners/pair",
            json={"name": "not-admin"},
            headers=member_headers,
        )
        assert denied.status_code == 403
    store._db.close()


@pytest.mark.asyncio
async def test_invite_list_and_member_cannot_manage(tmp_path: Path):
    store = Store(db_path=tmp_path / "invite-list.db", audit=True)
    app = create_app(store=store, config=_config())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin_registration = (
            await client.post(
                "/v1/auth/register",
                json={
                    "email": "admin@invite.test",
                    "password": "password123",
                    "org_name": "Invite Org",
                },
            )
        ).json()
        admin_headers = {
            "Authorization": f"Bearer {admin_registration['token']}"
        }
        created = (
            await client.post(
                "/v1/auth/invite", json={"role": "member"}, headers=admin_headers
            )
        ).json()
        assert created["code"]
        assert created["uses"] == 0
        assert created["max_uses"] == 20
        assert created["role"] == "member"

        listed = (
            await client.get("/v1/auth/invites", headers=admin_headers)
        ).json()
        assert listed["open_registration"] is False
        assert any(item["code"] == created["code"] for item in listed["invites"])

        invite = (
            await client.post(
                "/v1/auth/invite", json={}, headers=admin_headers
            )
        ).json()
        member_registration = (
            await client.post(
                "/v1/auth/register",
                json={
                    "email": "member@invite.test",
                    "password": "password123",
                    "invite_code": invite["code"],
                },
            )
        ).json()
        member_headers = {
            "Authorization": f"Bearer {member_registration['token']}"
        }
        denied_list = await client.get(
            "/v1/auth/invites", headers=member_headers
        )
        assert denied_list.status_code == 403
        denied_create = await client.post(
            "/v1/auth/invite", json={}, headers=member_headers
        )
        assert denied_create.status_code == 403

        after_use = (
            await client.get("/v1/auth/invites", headers=admin_headers)
        ).json()
        used = next(
            item for item in after_use["invites"] if item["code"] == invite["code"]
        )
        assert used["uses"] == 1
    store._db.close()
