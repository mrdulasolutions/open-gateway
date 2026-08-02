"""OpenGateway HTTP server — ACP-compatible + collaboration REST API."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from opengateway import __version__
from opengateway.acp_handlers import BUILTIN_AGENTS, dispatch_acp_run
from opengateway.models import (
    AgentManifest,
    AgentsListResponse,
    Artifact,
    CreateRoomRequest,
    CreateTaskRequest,
    GatewayEvent,
    JoinRoomRequest,
    Message,
    Participant,
    PostMessageRequest,
    RegisterAgentRequest,
    RegisteredAgent,
    Room,
    RoomMessage,
    Run,
    RunCreateRequest,
    RunStatus,
    ShareArtifactRequest,
    Task,
    TaskStatus,
    UpdateTaskRequest,
    new_id,
    text_message,
    utcnow,
)
from opengateway.store import Store, store as default_store


def create_app(store: Store | None = None) -> FastAPI:
    st = store or default_store

    app = FastAPI(
        title="OpenGateway",
        description=(
            "Multi-agent collaboration hub. ACP-compatible REST for agent runs, "
            "plus room-based messaging so Grok, Claude Code, Cursor, and any "
            "ACP/MCP agent can work on the same project."
        ),
        version=__version__,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.store = st

    # ── Health / meta ──────────────────────────────────────────────────────

    @app.get("/ping")
    async def ping() -> dict[str, Any]:
        return {"status": "ok", "service": "opengateway", "version": __version__}

    @app.get("/")
    async def root() -> dict[str, Any]:
        return {
            "name": "OpenGateway",
            "version": __version__,
            "protocol": "ACP-compatible + OpenGateway collaboration API",
            "docs": "/docs",
            "acp": {
                "agents": "/agents",
                "runs": "/runs",
                "ping": "/ping",
            },
            "collaboration": {
                "rooms": "/v1/rooms",
                "events": "/v1/events",
            },
            "mcp": "Run `opengateway mcp` to expose collaboration tools over stdio MCP.",
        }

    # ── ACP: agents ────────────────────────────────────────────────────────

    @app.get("/agents", response_model=AgentsListResponse)
    async def list_agents(
        limit: int = Query(10, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ) -> AgentsListResponse:
        builtin = [
            AgentManifest(name=a["name"], description=a["description"], metadata=a.get("metadata", {}))
            for a in BUILTIN_AGENTS
        ]
        registered = [
            AgentManifest(
                name=a.name,
                description=a.description,
                metadata={**a.metadata, "harness": a.harness.value, "capabilities": a.capabilities},
            )
            for a in await st.list_agents()
        ]
        # Room participants also appear as live agents
        live: list[AgentManifest] = []
        seen = {m.name for m in builtin + registered}
        for room in await st.list_rooms():
            for p in await st.list_participants(room.id):
                key = f"{p.name}@{room.name}"
                if key not in seen:
                    live.append(
                        AgentManifest(
                            name=key,
                            description=f"Live participant in room '{room.name}' via {p.harness.value}",
                            metadata={
                                "org.open-gateway/participant_id": p.id,
                                "org.open-gateway/room_id": room.id,
                                "harness": p.harness.value,
                            },
                        )
                    )
                    seen.add(key)
        all_agents = builtin + registered + live
        return AgentsListResponse(agents=all_agents[offset : offset + limit])

    @app.get("/agents/{name}", response_model=AgentManifest)
    async def get_agent(name: str) -> AgentManifest:
        for a in BUILTIN_AGENTS:
            if a["name"] == name:
                return AgentManifest(name=a["name"], description=a["description"], metadata=a.get("metadata", {}))
        reg = next((a for a in await st.list_agents() if a.name == name), None)
        if reg:
            return AgentManifest(
                name=reg.name,
                description=reg.description,
                metadata={**reg.metadata, "harness": reg.harness.value},
            )
        raise HTTPException(status_code=404, detail=f"Agent not found: {name}")

    # ── ACP: runs ──────────────────────────────────────────────────────────

    @app.post("/runs")
    async def create_run(body: RunCreateRequest) -> Any:
        run = Run(
            agent_name=body.agent_name,
            session_id=body.session_id or new_id(),
            status=RunStatus.CREATED,
        )
        await st.save_run(run)
        if body.mode == "async":
            asyncio.create_task(dispatch_acp_run(run, body.input, st))
            run.status = RunStatus.IN_PROGRESS
            await st.save_run(run)
            return JSONResponse(status_code=202, content=run.model_dump(mode="json"))
        return await dispatch_acp_run(run, body.input, st)

    @app.get("/runs/{run_id}", response_model=Run)
    async def get_run(run_id: str) -> Run:
        run = await st.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        return run

    @app.post("/runs/{run_id}/cancel", response_model=Run)
    async def cancel_run(run_id: str) -> JSONResponse:
        run = await st.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        if run.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
            return JSONResponse(status_code=202, content=run.model_dump(mode="json"))
        run.status = RunStatus.CANCELLED
        run.finished_at = utcnow()
        await st.save_run(run)
        return JSONResponse(status_code=202, content=run.model_dump(mode="json"))

    # ── OpenGateway: agent registration ────────────────────────────────────

    @app.post("/v1/agents/register", response_model=RegisteredAgent)
    async def register_agent(body: RegisterAgentRequest) -> RegisteredAgent:
        agent = RegisteredAgent(
            name=body.name,
            description=body.description,
            harness=body.harness,
            capabilities=body.capabilities,
            metadata=body.metadata,
        )
        return await st.register_agent(agent)

    @app.get("/v1/agents")
    async def list_registered_agents() -> dict[str, Any]:
        return {"agents": [a.model_dump(mode="json") for a in await st.list_agents()]}

    # ── OpenGateway: rooms ─────────────────────────────────────────────────

    @app.post("/v1/rooms", response_model=Room)
    async def create_room(body: CreateRoomRequest) -> Room:
        room = Room(
            name=body.name,
            goal=body.goal,
            project_path=body.project_path,
            created_by=body.created_by,
            metadata=body.metadata,
        )
        return await st.create_room(room)

    @app.get("/v1/rooms")
    async def list_rooms() -> dict[str, Any]:
        rooms = await st.list_rooms()
        return {"rooms": [r.model_dump(mode="json") for r in rooms]}

    @app.get("/v1/rooms/{room_id}", response_model=Room)
    async def get_room(room_id: str) -> Room:
        room = await st.get_room(room_id)
        if not room:
            raise HTTPException(status_code=404, detail="Room not found")
        return room

    @app.post("/v1/rooms/{room_id}/join", response_model=Participant)
    async def join_room(room_id: str, body: JoinRoomRequest) -> Participant:
        if not await st.get_room(room_id):
            raise HTTPException(status_code=404, detail="Room not found")
        if body.participant_id and (existing := await st.get_participant(body.participant_id)):
            existing.name = body.name
            existing.harness = body.harness
            existing.role = body.role
            existing.capabilities = body.capabilities
            existing.metadata = {**existing.metadata, **body.metadata}
            return await st.join_room(room_id, existing)
        participant = Participant(
            name=body.name,
            harness=body.harness,
            role=body.role,
            capabilities=body.capabilities,
            metadata=body.metadata,
        )
        return await st.join_room(room_id, participant)

    @app.post("/v1/rooms/{room_id}/leave")
    async def leave_room(room_id: str, participant_id: str = Query(...)) -> dict[str, str]:
        result = await st.leave_room(room_id, participant_id)
        if not result:
            raise HTTPException(status_code=404, detail="Room or participant not found")
        return {"status": "left", "participant_id": participant_id}

    @app.get("/v1/rooms/{room_id}/participants")
    async def list_participants(room_id: str) -> dict[str, Any]:
        if not await st.get_room(room_id):
            raise HTTPException(status_code=404, detail="Room not found")
        return {
            "participants": [p.model_dump(mode="json") for p in await st.list_participants(room_id)]
        }

    # ── Messages ───────────────────────────────────────────────────────────

    @app.post("/v1/rooms/{room_id}/messages", response_model=RoomMessage)
    async def post_message(room_id: str, body: PostMessageRequest) -> RoomMessage:
        if not await st.get_room(room_id):
            raise HTTPException(status_code=404, detail="Room not found")
        participant = await st.get_participant(body.from_participant_id)
        if not participant:
            raise HTTPException(status_code=400, detail="Unknown from_participant_id — join the room first")
        if body.parts:
            message = Message(
                role=body.role or f"agent/{participant.name}",
                parts=body.parts,
            )
        else:
            message = text_message(body.role or f"agent/{participant.name}", body.content, body.content_type)
        msg = RoomMessage(
            room_id=room_id,
            from_participant_id=body.from_participant_id,
            from_name=participant.name,
            to_participant_id=body.to_participant_id,
            message=message,
            metadata=body.metadata,
        )
        return await st.post_message(msg)

    @app.get("/v1/rooms/{room_id}/messages")
    async def list_messages(
        room_id: str,
        since: Optional[str] = None,
        for_participant: Optional[str] = None,
        limit: int = Query(100, ge=1, le=500),
    ) -> dict[str, Any]:
        if not await st.get_room(room_id):
            raise HTTPException(status_code=404, detail="Room not found")
        items = await st.list_messages(room_id, since=since, for_participant=for_participant, limit=limit)
        return {"messages": [m.model_dump(mode="json") for m in items]}

    # ── Tasks ──────────────────────────────────────────────────────────────

    @app.post("/v1/rooms/{room_id}/tasks", response_model=Task)
    async def create_task(room_id: str, body: CreateTaskRequest) -> Task:
        if not await st.get_room(room_id):
            raise HTTPException(status_code=404, detail="Room not found")
        task = Task(
            room_id=room_id,
            title=body.title,
            description=body.description,
            created_by=body.created_by,
            depends_on=body.depends_on,
            metadata=body.metadata,
        )
        return await st.create_task(task)

    @app.get("/v1/rooms/{room_id}/tasks")
    async def list_tasks(
        room_id: str,
        status: Optional[TaskStatus] = None,
    ) -> dict[str, Any]:
        if not await st.get_room(room_id):
            raise HTTPException(status_code=404, detail="Room not found")
        return {"tasks": [t.model_dump(mode="json") for t in await st.list_tasks(room_id, status=status)]}

    @app.patch("/v1/rooms/{room_id}/tasks/{task_id}", response_model=Task)
    async def update_task(room_id: str, task_id: str, body: UpdateTaskRequest) -> Task:
        fields: dict[str, Any] = {}
        if body.status is not None:
            fields["status"] = body.status
        if body.claimed_by is not None:
            fields["claimed_by"] = body.claimed_by
            if body.status is None:
                fields["status"] = TaskStatus.CLAIMED
        if body.result is not None:
            fields["result"] = body.result
        if body.metadata is not None:
            fields["metadata"] = body.metadata
        task = await st.update_task(room_id, task_id, **fields)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        return task

    # ── Artifacts ──────────────────────────────────────────────────────────

    @app.post("/v1/rooms/{room_id}/artifacts", response_model=Artifact)
    async def share_artifact(room_id: str, body: ShareArtifactRequest) -> Artifact:
        if not await st.get_room(room_id):
            raise HTTPException(status_code=404, detail="Room not found")
        if not body.content and not body.content_url:
            raise HTTPException(status_code=400, detail="Provide content or content_url")
        artifact = Artifact(
            room_id=room_id,
            name=body.name,
            content_type=body.content_type,
            content=body.content,
            content_url=body.content_url,
            content_encoding=body.content_encoding,
            shared_by=body.shared_by,
            description=body.description,
            metadata=body.metadata,
        )
        return await st.share_artifact(artifact)

    @app.get("/v1/rooms/{room_id}/artifacts")
    async def list_artifacts(room_id: str) -> dict[str, Any]:
        if not await st.get_room(room_id):
            raise HTTPException(status_code=404, detail="Room not found")
        return {"artifacts": [a.model_dump(mode="json") for a in await st.list_artifacts(room_id)]}

    # ── Events (SSE + poll) ────────────────────────────────────────────────

    @app.get("/v1/events")
    async def poll_events(
        room_id: Optional[str] = None,
        after_id: Optional[str] = None,
        limit: int = Query(100, ge=1, le=500),
    ) -> dict[str, Any]:
        events = st.recent_events(room_id=room_id, after_id=after_id, limit=limit)
        return {"events": [e.model_dump(mode="json") for e in events]}

    @app.get("/v1/rooms/{room_id}/events")
    async def room_events_sse(room_id: str, request: Request) -> EventSourceResponse:
        if not await st.get_room(room_id):
            raise HTTPException(status_code=404, detail="Room not found")

        async def gen():
            q = st.subscribe()
            try:
                # Replay recent
                for e in st.recent_events(room_id=room_id, limit=20):
                    yield {"event": e.type, "id": e.id, "data": json.dumps(e.model_dump(mode="json"))}
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event: GatewayEvent = await asyncio.wait_for(q.get(), timeout=25.0)
                    except asyncio.TimeoutError:
                        yield {"event": "ping", "data": "{}"}
                        continue
                    if event.room_id is None or event.room_id == room_id:
                        yield {
                            "event": event.type,
                            "id": event.id,
                            "data": json.dumps(event.model_dump(mode="json")),
                        }
            finally:
                st.unsubscribe(q)

        return EventSourceResponse(gen())

    # ── Snapshot helper for agents ─────────────────────────────────────────

    @app.get("/v1/rooms/{room_id}/snapshot")
    async def room_snapshot(room_id: str) -> dict[str, Any]:
        room = await st.get_room(room_id)
        if not room:
            raise HTTPException(status_code=404, detail="Room not found")
        return {
            "room": room.model_dump(mode="json"),
            "participants": [p.model_dump(mode="json") for p in await st.list_participants(room_id)],
            "tasks": [t.model_dump(mode="json") for t in await st.list_tasks(room_id)],
            "messages": [m.model_dump(mode="json") for m in await st.list_messages(room_id, limit=50)],
            "artifacts": [a.model_dump(mode="json") for a in await st.list_artifacts(room_id)],
        }

    return app


# Module-level app for `uvicorn opengateway.server:app`
app = create_app()
