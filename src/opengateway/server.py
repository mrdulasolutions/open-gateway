"""OpenGateway HTTP server — ACP-compatible + collaboration REST API."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

import base64
import re
import uuid
from pathlib import Path

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from opengateway import __version__
from opengateway.acp_handlers import BUILTIN_AGENTS, dispatch_acp_run
from opengateway.auth import BearerAuthMiddleware
from opengateway.config import GatewayConfig, load_gateway_config, local_ips, network_ui_label
from opengateway.models import (
    AgentManifest,
    AgentsListResponse,
    Artifact,
    Bookmark,
    CreateBookmarkRequest,
    CreateForkRequest,
    CreateRoomRequest,
    CreateTaskRequest,
    Fork,
    GatewayEvent,
    GatewayRecord,
    JoinRoomRequest,
    Message,
    Participant,
    PostMessageRequest,
    RegisterAgentRequest,
    RegisterGatewayRequest,
    RegisteredAgent,
    Room,
    RoomMessage,
    Run,
    RunCreateRequest,
    RunStatus,
    ShareArtifactRequest,
    Task,
    TaskStatus,
    UpdateParticipantRequest,
    UpdateTaskRequest,
    new_id,
    text_message,
    utcnow,
)
from opengateway.search import global_search
from opengateway.store import Store


def _find_web_dir() -> Path | None:
    """Locate UI assets — prefer Vite React build (webapp/dist), else static web/."""
    here = Path(__file__).resolve()
    root = here.parents[2]  # OpenGateway/
    candidates = [
        root / "webapp" / "dist",  # Vite + React production build
        Path.cwd() / "webapp" / "dist",
        root / "web",  # legacy static SPA
        here.parents[1] / "web",
        Path.cwd() / "web",
        Path.home() / "Code" / "OpenGateway" / "webapp" / "dist",
        Path.home() / "Code" / "OpenGateway" / "web",
    ]
    for c in candidates:
        if (c / "index.html").is_file():
            return c
    return None


def create_app(
    store: Store | None = None,
    config: GatewayConfig | None = None,
) -> FastAPI:
    """Build the FastAPI app. Pass a Store, or a fresh default (SQLite unless OPENGATEWAY_DB=none)."""
    st = store if store is not None else Store()
    cfg = config or load_gateway_config()

    app = FastAPI(
        title="OpenGateway",
        description=(
            "Multi-agent collaboration hub. ACP-compatible REST for agent runs, "
            "plus room-based messaging so Grok, Claude Code, Cursor, and any "
            "ACP/MCP agent can work on the same project. "
            "Rooms persist to SQLite by default (~/.opengateway/state.db)."
        ),
        version=__version__,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if cfg.require_auth and cfg.auth_token:
        app.add_middleware(BearerAuthMiddleware, config=cfg)

    app.state.store = st
    app.state.gateway_config = cfg

    # Register self in gateway registry
    async def _ensure_self_gateway() -> GatewayRecord:
        self_rec = GatewayRecord(
            id=cfg.gateway_id,
            name=cfg.name,
            mode=cfg.mode.value,
            network=cfg.network,
            base_url=cfg.base_url,
            require_auth=cfg.require_auth,
            is_self=True,
            notes=network_ui_label(cfg.network) if cfg.network else "This process",
            metadata={
                "bind": f"{cfg.host}:{cfg.port}",
                "label": network_ui_label(cfg.network),
                "serve_hint": cfg.serve_hint,
                "trust_tailscale_identity": cfg.trust_tailscale_identity,
            },
        )
        # Replace any prior self with same mode
        for g in list(await st.list_gateways()):
            if g.is_self:
                await st.delete_gateway(g.id)
        return await st.upsert_gateway(self_rec)

    @app.on_event("startup")
    async def _startup() -> None:
        await _ensure_self_gateway()

    # ── Health / meta ──────────────────────────────────────────────────────

    @app.get("/ping")
    async def ping() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "opengateway",
            "version": __version__,
            "persistent": st.persistent,
            "db_path": str(st.db_path) if st.db_path else None,
            "rooms": len(st.rooms),
            "mode": cfg.mode.value,
            "network": cfg.network,
            "require_auth": cfg.require_auth,
            "base_url": cfg.base_url,
        }

    @app.get("/")
    async def root() -> RedirectResponse:
        """Browser home → Live Ops web UI."""
        return RedirectResponse(url="/ui/", status_code=302)

    @app.get("/meta")
    async def meta() -> dict[str, Any]:
        return {
            "name": "OpenGateway",
            "version": __version__,
            "protocol": "ACP-compatible + OpenGateway collaboration API",
            "ui": "/ui/",
            "docs": "/docs",
            "gateway": cfg.to_public_dict(),
            "persistence": {
                "enabled": st.persistent,
                "db_path": str(st.db_path) if st.db_path else None,
                "hint": "Set OPENGATEWAY_DB path, or OPENGATEWAY_DB=none for memory-only",
            },
            "acp": {
                "agents": "/agents",
                "runs": "/runs",
                "ping": "/ping",
            },
            "collaboration": {
                "rooms": "/v1/rooms",
                "events": "/v1/events",
                "events_stream": "/v1/events/stream",
                "search": "/v1/search",
                "gateways": "/v1/gateways",
            },
            "mcp": "Run `opengateway mcp` to expose collaboration tools over stdio MCP.",
            "security": {
                "require_auth": cfg.require_auth,
                "auth_configured": bool(cfg.auth_token),
                "mode": cfg.mode.value,
                "network": cfg.network,
                "tailscale": {
                    "hostname": cfg.tailscale_hostname,
                    "hint": "Prefer Tailscale Serve/MagicDNS for multi-machine without public internet",
                },
            },
        }

    @app.get("/v1/gateways")
    async def list_gateways() -> dict[str, Any]:
        gws = await st.list_gateways()
        # Ensure self present
        if not any(g.is_self for g in gws):
            await _ensure_self_gateway()
            gws = await st.list_gateways()
        return {
            "gateways": [g.model_dump(mode="json") for g in gws],
            "self": cfg.to_public_dict(),
            "lan_ips": local_ips(),
            "tips": {
                "internal": "opengateway serve --mode internal  (127.0.0.1, multi-agent on one machine)",
                "public": "opengateway serve --mode public --token $TOKEN  (bind 0.0.0.0 + auth)",
                "tailscale": "opengateway serve --mode serve --token $TOKEN  then: tailscale serve --bg 8765",
                "funnel": "opengateway serve --mode funnel --token $TOKEN  then: tailscale funnel --bg 8765",
            },
            "labels": {
                "loopback": "Internal (loopback)",
                "lan": "LAN",
                "tailscale": "Tailnet (Serve)",
                "funnel": "Internet (Funnel)",
                "public": "Internet (open bind)",
            },
        }

    @app.post("/v1/gateways")
    async def register_gateway(body: RegisterGatewayRequest) -> GatewayRecord:
        gw = GatewayRecord(
            name=body.name,
            mode=body.mode,
            network=body.network,
            base_url=body.base_url.rstrip("/"),
            require_auth=body.require_auth,
            is_self=False,
            notes=body.notes,
            metadata=body.metadata,
        )
        return await st.upsert_gateway(gw)

    @app.delete("/v1/gateways/{gateway_id}")
    async def remove_gateway(gateway_id: str) -> dict[str, str]:
        g = next((x for x in await st.list_gateways() if x.id == gateway_id), None)
        if g and g.is_self:
            raise HTTPException(status_code=400, detail="Cannot delete this process's gateway card")
        ok = await st.delete_gateway(gateway_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Gateway not found")
        return {"status": "deleted"}

    @app.get("/v1/search")
    async def search(
        q: str = Query("", min_length=0),
        limit: int = Query(40, ge=1, le=100),
        room_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Predictive global search across rooms, people, messages, tasks, bookmarks, forks."""
        return await global_search(st, q, limit=limit, room_id=room_id)

    web_dir = _find_web_dir()
    if web_dir is not None:
        # html=True serves index.html for /ui/ (Hermes-style single process UI)
        app.mount(
            "/ui",
            StaticFiles(directory=str(web_dir), html=True),
            name="ui",
        )

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
        name = (body.name or "").strip() or "agent"
        # 1) Explicit id always wins (stable client session)
        if body.participant_id and (existing := await st.get_participant(body.participant_id)):
            existing.name = name
            existing.harness = body.harness
            existing.role = body.role
            existing.capabilities = body.capabilities
            existing.metadata = {**existing.metadata, **body.metadata}
            return await st.join_room(room_id, existing)
        # 2) Reuse same name+harness seat (kills ghost duplicates on leave/rejoin)
        twin = st.find_participant_by_identity(room_id, name, body.harness.value)
        if twin:
            twin.name = name
            twin.harness = body.harness
            twin.role = body.role
            twin.capabilities = body.capabilities or twin.capabilities
            twin.metadata = {**twin.metadata, **body.metadata}
            return await st.join_room(room_id, twin)
        participant = Participant(
            name=name,
            harness=body.harness,
            role=body.role,
            capabilities=body.capabilities,
            metadata=body.metadata,
        )
        return await st.join_room(room_id, participant)

    @app.patch("/v1/rooms/{room_id}/participants/{participant_id}", response_model=Participant)
    async def update_participant(
        room_id: str,
        participant_id: str,
        body: UpdateParticipantRequest,
    ) -> Participant:
        """Rename or patch a participant without creating a new one."""
        if not await st.get_room(room_id):
            raise HTTPException(status_code=404, detail="Room not found")
        p = await st.get_participant(participant_id)
        if not p or p.room_id != room_id:
            raise HTTPException(status_code=404, detail="Participant not found in room")
        updated = await st.update_participant(
            participant_id,
            name=body.name,
            role=body.role,
            status=body.status,
            capabilities=body.capabilities,
            metadata=body.metadata,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Participant not found")
        return updated

    @app.post("/v1/rooms/{room_id}/leave")
    async def leave_room(room_id: str, participant_id: str = Query(...)) -> dict[str, str]:
        result = await st.leave_room(room_id, participant_id)
        if not result:
            raise HTTPException(status_code=404, detail="Room or participant not found")
        return {"status": "left", "participant_id": participant_id}

    def _sort_participants(people: list[Participant]) -> list[Participant]:
        """Online first, then most recently active."""

        def key(p: Participant) -> tuple:
            online = 0 if p.status.value == "online" else 1
            seen = p.last_seen_at.timestamp() if p.last_seen_at else 0.0
            return (online, -seen, p.name.lower())

        return sorted(people, key=key)

    @app.get("/v1/rooms/{room_id}/participants")
    async def list_participants(room_id: str) -> dict[str, Any]:
        if not await st.get_room(room_id):
            raise HTTPException(status_code=404, detail="Room not found")
        people = _sort_participants(await st.list_participants(room_id))
        return {"participants": [p.model_dump(mode="json") for p in people]}

    # ── Messages ───────────────────────────────────────────────────────────

    def _is_all_call(text: str) -> bool:
        t = (text or "").lower()
        needles = (
            "everyone",
            "everybody",
            "@all",
            "@everyone",
            "all agents",
            "hey all",
            "hey everyone",
            "all of you",
            "you all",
        )
        return any(n in t for n in needles)

    def _mentioned_peers(text: str, peers: list[Participant]) -> list[Participant]:
        """Resolve @Name mentions (case-insensitive, longest name first)."""
        if not text or not peers:
            return []
        found: list[Participant] = []
        lower = text
        # Sort longer names first so @alice-demo beats @alice
        for p in sorted(peers, key=lambda x: len(x.name), reverse=True):
            # @name as word boundary-ish
            pat = re.compile(rf"@{re.escape(p.name)}\b", re.IGNORECASE)
            if pat.search(lower):
                found.append(p)
                # avoid double-match shorter prefixes by blanking
                lower = pat.sub(" ", lower)
        return found

    files_root = Path.home() / ".opengateway" / "files"
    files_root.mkdir(parents=True, exist_ok=True)

    @app.post("/v1/rooms/{room_id}/files")
    async def upload_room_file(
        room_id: str,
        shared_by: str = Form(...),
        file: UploadFile = File(...),
    ) -> Artifact:
        """Upload a file into the room as an artifact (for chat attachments)."""
        if not await st.get_room(room_id):
            raise HTTPException(status_code=404, detail="Room not found")
        if not await st.get_participant(shared_by):
            raise HTTPException(status_code=400, detail="Unknown shared_by participant")
        raw = await file.read()
        if len(raw) > 25 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File too large (max 25MB)")
        safe_name = Path(file.filename or "upload.bin").name
        art_id = str(uuid.uuid4())
        dest_dir = files_root / room_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{art_id}_{safe_name}"
        dest.write_bytes(raw)
        content_type = file.content_type or "application/octet-stream"
        # Small text files also store inline for agents without download
        inline: Optional[str] = None
        encoding = "plain"
        if content_type.startswith("text/") and len(raw) < 200_000:
            try:
                inline = raw.decode("utf-8")
            except UnicodeDecodeError:
                inline = base64.b64encode(raw).decode("ascii")
                encoding = "base64"
        elif len(raw) < 400_000:
            inline = base64.b64encode(raw).decode("ascii")
            encoding = "base64"

        artifact = Artifact(
            id=art_id,
            room_id=room_id,
            name=safe_name,
            content_type=content_type,
            content=inline,
            content_url=f"/v1/rooms/{room_id}/files/{art_id}/download",
            content_encoding=encoding if inline else "plain",
            shared_by=shared_by,
            description=f"Chat attachment ({len(raw)} bytes)",
            metadata={"bytes": len(raw), "path": str(dest)},
        )
        return await st.share_artifact(artifact)

    @app.get("/v1/rooms/{room_id}/files/{file_id}/download")
    async def download_room_file(room_id: str, file_id: str) -> FileResponse:
        arts = await st.list_artifacts(room_id)
        art = next((a for a in arts if a.id == file_id), None)
        if not art:
            raise HTTPException(status_code=404, detail="File not found")
        path = (art.metadata or {}).get("path")
        if not path or not Path(path).is_file():
            # fallback: reconstruct from known layout
            matches = list((files_root / room_id).glob(f"{file_id}_*"))
            if not matches:
                raise HTTPException(status_code=404, detail="File missing on disk")
            path = str(matches[0])
        return FileResponse(
            path,
            media_type=art.content_type or "application/octet-stream",
            filename=art.name,
        )

    @app.post("/v1/rooms/{room_id}/messages")
    async def post_message(room_id: str, body: PostMessageRequest) -> Any:
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
        # Auto-DM when exactly one @mention and no explicit target
        to_id = body.to_participant_id
        all_peers = [
            p
            for p in await st.list_participants(room_id)
            if p.id != body.from_participant_id
        ]
        mentioned = _mentioned_peers(body.content or "", all_peers)
        if not to_id and len(mentioned) == 1 and not _is_all_call(body.content or ""):
            to_id = mentioned[0].id

        msg = RoomMessage(
            room_id=room_id,
            from_participant_id=body.from_participant_id,
            from_name=participant.name,
            to_participant_id=to_id,
            message=message,
            metadata=body.metadata,
        )
        saved = await st.post_message(msg)

        nudged: list[dict[str, Any]] = []
        # @all / everyone → nudge all agents. Single @name → nudge that agent.
        # Explicit nudge_all still supported for API clients.
        want_nudge_all = bool(body.nudge_all) or (
            not to_id and _is_all_call(body.content or "")
        )
        want_nudge_mentioned = bool(mentioned) and not want_nudge_all
        if want_nudge_all or want_nudge_mentioned:
            if want_nudge_all:
                peers = [
                    p
                    for p in all_peers
                    if p.harness.value not in {"human"}
                ]
            else:
                peers = [p for p in mentioned if p.harness.value not in {"human"}]
            # Still nudge other humans if they aren't the sender? skip human harness only
            # Still nudge other humans if they aren't the sender? skip human harness only
            for peer in peers:
                # Personal DM so wait_for_messages(for_participant=peer) always fires
                dm = RoomMessage(
                    room_id=room_id,
                    from_participant_id=body.from_participant_id,
                    from_name=participant.name,
                    to_participant_id=peer.id,
                    message=text_message(
                        f"agent/{participant.name}",
                        f"@nudge → {peer.name}: {body.content}",
                    ),
                    metadata={"nudge": True, "broadcast_id": saved.id},
                )
                await st.post_message(dm)
                task = Task(
                    room_id=room_id,
                    title=f"Respond to {participant.name}",
                    description=(
                        f"{participant.name} addressed everyone:\n\n{body.content}\n\n"
                        f"Please reply in the room (post_message). This task is for {peer.name}."
                    ),
                    created_by=body.from_participant_id,
                    claimed_by=peer.id,
                    status=TaskStatus.CLAIMED,
                    metadata={
                        "nudge": True,
                        "assignee_id": peer.id,
                        "assignee_name": peer.name,
                        "source_message_id": saved.id,
                    },
                )
                await st.create_task(task)
                nudged.append(
                    {
                        "participant_id": peer.id,
                        "name": peer.name,
                        "task_id": task.id,
                    }
                )
            # System note in the public channel
            note = RoomMessage(
                room_id=room_id,
                from_participant_id=body.from_participant_id,
                from_name="system",
                message=text_message(
                    "agent/system",
                    "Nudge sent to: "
                    + (", ".join(n["name"] for n in nudged) if nudged else "(no online agents)"),
                ),
                metadata={"system": True, "nudge_summary": True},
            )
            # Post as synthetic system — use a fixed from that still validates
            # Keep from as sender so auth stays simple; name shown as system via role
            note.from_name = "system"
            await st.post_message(note)

        if want_nudge_all or want_nudge_mentioned:
            return {
                "message": saved.model_dump(mode="json"),
                "nudged": nudged,
                "nudge_count": len(nudged),
            }
        return saved

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

    @app.get("/v1/rooms/{room_id}/messages/wait")
    async def wait_messages(
        room_id: str,
        since: Optional[str] = None,
        for_participant: Optional[str] = None,
        timeout: float = Query(30.0, ge=0.0, le=120.0),
        limit: int = Query(50, ge=1, le=500),
    ) -> dict[str, Any]:
        """Long-poll for new messages (IM-style). Blocks until a message arrives or timeout.

        Agents should loop: wait → process → wait again, using the last message id as `since`.
        """
        if not await st.get_room(room_id):
            raise HTTPException(status_code=404, detail="Room not found")
        if for_participant:
            await st.touch_participant(for_participant)
        items = await st.wait_for_messages(
            room_id,
            since=since,
            for_participant=for_participant,
            timeout=timeout,
            limit=limit,
        )
        return {
            "messages": [m.model_dump(mode="json") for m in items],
            "timed_out": len(items) == 0,
            "since": since,
        }

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

    @app.get("/v1/events/stream")
    async def global_events_sse(request: Request) -> EventSourceResponse:
        """SSE stream of all gateway events (all rooms). For CLI monitor."""

        async def gen():
            q = st.subscribe()
            try:
                for e in st.recent_events(limit=30):
                    yield {"event": e.type, "id": e.id, "data": json.dumps(e.model_dump(mode="json"))}
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event: GatewayEvent = await asyncio.wait_for(q.get(), timeout=25.0)
                    except asyncio.TimeoutError:
                        yield {"event": "ping", "data": "{}"}
                        continue
                    yield {
                        "event": event.type,
                        "id": event.id,
                        "data": json.dumps(event.model_dump(mode="json")),
                    }
            finally:
                st.unsubscribe(q)

        return EventSourceResponse(gen())

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

    # ── WebSocket IM channel ───────────────────────────────────────────────

    @app.websocket("/v1/rooms/{room_id}/ws")
    async def room_ws(websocket: WebSocket, room_id: str) -> None:
        """Realtime room socket.

        Query: participant_id (required after connect handshake msg if omitted).
        Client → server JSON:
          {"type":"hello","participant_id":"..."}
          {"type":"message","content":"...","to_participant_id":null}
          {"type":"ping"}
        Server → client JSON:
          {"type":"hello_ok","participant":{...}}
          {"type":"message","message":{...}}
          {"type":"event","event":{...}}
          {"type":"error","detail":"..."}
          {"type":"pong"}
        """
        await websocket.accept()
        if not await st.get_room(room_id):
            await websocket.send_json({"type": "error", "detail": "room_not_found"})
            await websocket.close()
            return

        participant_id = websocket.query_params.get("participant_id")
        q = st.subscribe(maxsize=512)

        async def pump_events() -> None:
            try:
                while True:
                    event = await q.get()
                    if event.room_id is not None and event.room_id != room_id:
                        continue
                    payload: dict[str, Any] = {"type": "event", "event": event.model_dump(mode="json")}
                    if event.type == "message" and "message" in event.payload:
                        payload = {"type": "message", "message": event.payload["message"]}
                    await websocket.send_json(payload)
            except Exception:
                return

        pump_task = asyncio.create_task(pump_events())
        try:
            # Optional immediate hello via query param
            if participant_id:
                p = await st.get_participant(participant_id)
                if p:
                    await st.touch_participant(participant_id)
                    await websocket.send_json(
                        {"type": "hello_ok", "participant": p.model_dump(mode="json")}
                    )
                else:
                    await websocket.send_json({"type": "error", "detail": "unknown_participant"})

            while True:
                raw = await websocket.receive_text()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_json({"type": "error", "detail": "invalid_json"})
                    continue

                msg_type = data.get("type")
                if msg_type == "ping":
                    await websocket.send_json({"type": "pong"})
                    continue

                if msg_type == "hello":
                    participant_id = data.get("participant_id") or participant_id
                    p = await st.get_participant(participant_id) if participant_id else None
                    if not p:
                        await websocket.send_json({"type": "error", "detail": "unknown_participant"})
                        continue
                    await st.touch_participant(participant_id)
                    await websocket.send_json(
                        {"type": "hello_ok", "participant": p.model_dump(mode="json")}
                    )
                    continue

                if msg_type == "message":
                    if not participant_id:
                        await websocket.send_json(
                            {"type": "error", "detail": "send hello with participant_id first"}
                        )
                        continue
                    p = await st.get_participant(participant_id)
                    if not p:
                        await websocket.send_json({"type": "error", "detail": "unknown_participant"})
                        continue
                    content = (data.get("content") or "").strip()
                    if not content:
                        await websocket.send_json({"type": "error", "detail": "empty_content"})
                        continue
                    room_msg = RoomMessage(
                        room_id=room_id,
                        from_participant_id=participant_id,
                        from_name=p.name,
                        to_participant_id=data.get("to_participant_id"),
                        message=text_message(f"agent/{p.name}", content),
                    )
                    saved = await st.post_message(room_msg)
                    # Echo confirmation (subscribers also get the fan-out)
                    await websocket.send_json(
                        {"type": "message_ack", "message": saved.model_dump(mode="json")}
                    )
                    continue

                await websocket.send_json({"type": "error", "detail": f"unknown_type:{msg_type}"})
        except WebSocketDisconnect:
            pass
        finally:
            pump_task.cancel()
            st.unsubscribe(q)

    # ── Bookmarks / forks / DMs ────────────────────────────────────────────

    @app.get("/v1/rooms/{room_id}/bookmarks")
    async def list_bookmarks(room_id: str) -> dict[str, Any]:
        if not await st.get_room(room_id):
            raise HTTPException(status_code=404, detail="Room not found")
        return {
            "bookmarks": [b.model_dump(mode="json") for b in await st.list_bookmarks(room_id)]
        }

    @app.post("/v1/rooms/{room_id}/bookmarks")
    async def create_bookmark(room_id: str, body: CreateBookmarkRequest) -> Bookmark:
        if not await st.get_room(room_id):
            raise HTTPException(status_code=404, detail="Room not found")
        msg = await st.get_message(room_id, body.message_id)
        if not msg:
            raise HTTPException(status_code=404, detail="Message not found")
        excerpt = body.excerpt
        if not excerpt:
            excerpt = (msg.message.parts[0].content or "")[:180] if msg.message.parts else ""
        title = body.title.strip() or f"Bookmark · {msg.from_name}"
        bookmark = Bookmark(
            room_id=room_id,
            message_id=body.message_id,
            title=title,
            excerpt=excerpt,
            created_by=body.created_by,
            metadata=body.metadata,
        )
        return await st.add_bookmark(bookmark)

    @app.delete("/v1/rooms/{room_id}/bookmarks/{bookmark_id}")
    async def remove_bookmark(room_id: str, bookmark_id: str) -> dict[str, str]:
        ok = await st.delete_bookmark(room_id, bookmark_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Bookmark not found")
        return {"status": "deleted"}

    @app.get("/v1/rooms/{room_id}/forks")
    async def list_forks(room_id: str) -> dict[str, Any]:
        if not await st.get_room(room_id):
            raise HTTPException(status_code=404, detail="Room not found")
        return {"forks": [f.model_dump(mode="json") for f in await st.list_forks(room_id)]}

    @app.post("/v1/rooms/{room_id}/forks")
    async def create_fork(room_id: str, body: CreateForkRequest) -> Fork:
        if not await st.get_room(room_id):
            raise HTTPException(status_code=404, detail="Room not found")
        msg = await st.get_message(room_id, body.root_message_id)
        if not msg:
            raise HTTPException(status_code=404, detail="Message not found")
        text = (msg.message.parts[0].content or "")[:80] if msg.message.parts else ""
        title = body.title.strip() or f"Fork of {msg.from_name}: {text}"
        if len(title) > 120:
            title = title[:117] + "…"
        fork = Fork(
            room_id=room_id,
            root_message_id=body.root_message_id,
            title=title,
            created_by=body.created_by,
            created_by_name=body.created_by_name,
            note=body.note,
            metadata={
                **body.metadata,
                "root_from": msg.from_name,
                "root_excerpt": text,
            },
        )
        return await st.add_fork(fork)

    @app.get("/v1/rooms/{room_id}/dms")
    async def list_dms(
        room_id: str,
        participant_id: str = Query(...),
        limit: int = Query(100, ge=1, le=500),
    ) -> dict[str, Any]:
        """List DM threads involving this participant (directed messages only)."""
        if not await st.get_room(room_id):
            raise HTTPException(status_code=404, detail="Room not found")
        all_msgs = await st.list_messages(room_id, limit=500)
        dms = [
            m
            for m in all_msgs
            if m.to_participant_id
            and (
                m.to_participant_id == participant_id
                or m.from_participant_id == participant_id
            )
        ]
        # Group by peer
        peers: dict[str, list[dict[str, Any]]] = {}
        for m in dms[-limit:]:
            peer = (
                m.to_participant_id
                if m.from_participant_id == participant_id
                else m.from_participant_id
            )
            peers.setdefault(peer or "unknown", []).append(m.model_dump(mode="json"))
        threads = []
        for peer_id, msgs in peers.items():
            p = await st.get_participant(peer_id)
            threads.append(
                {
                    "peer_id": peer_id,
                    "peer_name": p.name if p else peer_id[:8],
                    "peer_harness": p.harness.value if p else "other",
                    "peer_status": p.status.value if p else "offline",
                    "peer_role": p.role if p else "",
                    "peer_last_seen": p.last_seen_at.isoformat() if p and p.last_seen_at else None,
                    "messages": msgs,
                    "last_at": msgs[-1]["created_at"] if msgs else None,
                }
            )
        # Online first, then most recent activity
        def _dm_sort(t: dict[str, Any]) -> tuple:
            online = 0 if (t.get("peer_status") or "") == "online" else 1
            return (online, -(1 if t.get("last_at") else 0), t.get("last_at") or "", t.get("peer_name") or "")

        threads.sort(key=_dm_sort)
        return {"threads": threads}

    # ── Snapshot helper for agents ─────────────────────────────────────────

    @app.get("/v1/rooms/{room_id}/snapshot")
    async def room_snapshot(room_id: str) -> dict[str, Any]:
        room = await st.get_room(room_id)
        if not room:
            raise HTTPException(status_code=404, detail="Room not found")
        people = _sort_participants(await st.list_participants(room_id))
        return {
            "room": room.model_dump(mode="json"),
            "participants": [p.model_dump(mode="json") for p in people],
            "tasks": [t.model_dump(mode="json") for t in await st.list_tasks(room_id)],
            "messages": [m.model_dump(mode="json") for m in await st.list_messages(room_id, limit=200)],
            "artifacts": [a.model_dump(mode="json") for a in await st.list_artifacts(room_id)],
            "bookmarks": [b.model_dump(mode="json") for b in await st.list_bookmarks(room_id)],
            "forks": [f.model_dump(mode="json") for f in await st.list_forks(room_id)],
        }

    return app


def build_app() -> FastAPI:
    """Uvicorn factory entrypoint — creates Store after env (OPENGATEWAY_DB) is set."""
    return create_app()


# Eager app for ASGI importers that expect `app` (uses current env at import time)
app = create_app()
