"""Collaboration store with optional SQLite persistence and event fan-out."""

from __future__ import annotations

import asyncio
import secrets
from collections import defaultdict, deque
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Union

from opengateway.models import (
    Artifact,
    Bookmark,
    Fork,
    GatewayEvent,
    GatewayRecord,
    Harness,
    ManagedAgent,
    ManagedAgentStatus,
    Participant,
    ParticipantStatus,
    RegisteredAgent,
    Room,
    RoomMessage,
    RoomStatus,
    Run,
    Runner,
    RunnerJob,
    RunnerJobAction,
    RunnerJobStatus,
    RunnerLogEntry,
    RunnerPairCode,
    RunnerStatus,
    Task,
    TaskStatus,
    utcnow,
)
from opengateway.api_keys import (
    SCOPE_READ,
    SCOPE_RUNNER,
    SCOPE_TOOLS,
    SCOPE_WRITE,
    generate_secret,
    hash_secret,
    normalize_scopes,
    prefix_of,
    scrub_secret_text,
    scrub_secrets,
)
from opengateway.audit import audit_enabled, build_audit_entry
from opengateway.models import new_id
from opengateway.persistence import default_db_path, is_postgres_url, open_persistence
from opengateway.users import (
    hash_password,
    hash_token,
    mint_invite_code,
    mint_session_token,
    open_registration,
    session_ttl_days,
    slugify,
    verify_password,
)


class Store:
    """In-process store. When db_path is set, rooms and history survive restarts."""

    RUNNER_STALE_SECONDS = 90.0
    RUNNER_PAIR_TTL_SECONDS = 600
    RUNNER_LOG_LIMIT = 500
    RUNNER_LOG_RATE_LIMIT = 600
    RUNNER_LOG_RATE_WINDOW_SECONDS = 60.0

    def __init__(
        self,
        event_history: int = 2000,
        db_path: Union[str, Path, None] = ...,  # type: ignore[assignment]
        *,
        audit: Optional[bool] = None,
    ) -> None:
        self._lock = asyncio.Lock()
        self.rooms: dict[str, Room] = {}
        self.participants: dict[str, Participant] = {}
        self.messages: dict[str, list[RoomMessage]] = defaultdict(list)
        self.tasks: dict[str, list[Task]] = defaultdict(list)
        self.artifacts: dict[str, list[Artifact]] = defaultdict(list)
        self.bookmarks: dict[str, list[Bookmark]] = defaultdict(list)
        self.forks: dict[str, list[Fork]] = defaultdict(list)
        self.gateways: dict[str, GatewayRecord] = {}
        self.agents: dict[str, RegisteredAgent] = {}
        self.runs: dict[str, Run] = {}
        self.runners: dict[str, Runner] = {}
        self.runner_pair_codes: dict[str, RunnerPairCode] = {}
        self.managed_agents: dict[str, ManagedAgent] = {}
        self.runner_jobs: dict[str, RunnerJob] = {}
        self.runner_logs: dict[str, list[RunnerLogEntry]] = defaultdict(list)
        self._runner_log_windows: dict[str, deque[float]] = defaultdict(deque)
        self._runner_job_events: dict[str, asyncio.Event] = {}
        # Short-lived phone/mobile pair codes (in-memory only; Redis optional)
        self.pair_codes: dict[str, dict[str, Any]] = {}
        self._events: deque[GatewayEvent] = deque(maxlen=event_history)
        self._subscribers: list[asyncio.Queue[GatewayEvent]] = []
        self._seq = 0
        self._redis: Any = None  # optional RedisBus
        self._audit_enabled = bool(audit) if audit is not None else False
        self._memory_audit: deque[dict[str, Any]] = deque(maxlen=2000)
        self._memory_api_keys: dict[str, dict[str, Any]] = {}  # id -> record
        self._memory_api_by_hash: dict[str, str] = {}  # hash -> id
        self._memory_push: dict[str, dict[str, Any]] = {}  # id -> sub
        self._memory_meta: dict[str, str] = {}
        from opengateway.nudge_policy import NudgeRateLimiter

        self.nudge_limiter = NudgeRateLimiter()
        from opengateway.delivery_guard import DeliveryGuard
        from opengateway.room_floor import RoomSpeakingFloor

        self.delivery_guard = DeliveryGuard()
        self.speaking_floor = RoomSpeakingFloor()

        # db_path=... means "use default from env"; None means memory-only
        if db_path is ...:
            resolved = default_db_path()
        elif db_path is None:
            resolved = None
        elif isinstance(db_path, str) and is_postgres_url(db_path):
            resolved = db_path
        else:
            resolved = Path(db_path).expanduser()

        self.db_path = resolved
        self._db: Any = None
        self.backend_kind = "memory"
        if resolved is not None:
            self._db = open_persistence(resolved)
            self.backend_kind = "postgres" if is_postgres_url(resolved) else "sqlite"
            self._load_from_db()
            self._ensure_general_rooms()

    @property
    def persistent(self) -> bool:
        return self._db is not None

    def _load_from_db(self) -> None:
        if not self._db:
            return
        data = self._db.load_all()
        self.rooms = data["rooms"]
        self.participants = data["participants"]
        self.messages = defaultdict(list, data["messages"])
        self.tasks = defaultdict(list, data["tasks"])
        self.artifacts = defaultdict(list, data["artifacts"])
        self.bookmarks = defaultdict(list, data.get("bookmarks") or {})
        self.forks = defaultdict(list, data.get("forks") or {})
        self.gateways = data.get("gateways") or {}
        self.agents = data["agents"]
        self.runners = data.get("runners") or {}
        self.runner_pair_codes = data.get("runner_pair_codes") or {}
        self.managed_agents = data.get("managed_agents") or {}
        self.runner_jobs = data.get("runner_jobs") or {}
        # API keys / push live only in DB (or memory maps); loaded on demand

    def _ensure_general_rooms(self) -> None:
        """Give every empty hub a room so Add Agent does not wait on room setup."""
        if self._db is None:
            return
        tenant_ids: list[Optional[str]] = [None]
        if hasattr(self._db, "list_tenants"):
            for tenant in self._db.list_tenants() or []:
                tid = tenant.get("id")
                if tid:
                    tenant_ids.append(tid)
        for tenant_id in tenant_ids:
            self._ensure_general_room(tenant_id=tenant_id)

    def _ensure_general_room(self, *, tenant_id: Optional[str] = None) -> Optional[Room]:
        if any(
            room.status != RoomStatus.ARCHIVED and room.tenant_id == tenant_id
            for room in self.rooms.values()
        ):
            return None
        room = Room(
            name="General",
            goal="Start here. Add agents and talk in this room.",
            created_by="system",
            tenant_id=tenant_id,
            metadata={"builtin": "general"},
        )
        self.rooms[room.id] = room
        self._persist_room(room)
        return room

    def _persist_room(self, room: Room) -> None:
        if self._db:
            self._db.save_room(room)

    def _persist_participant(self, participant: Participant) -> None:
        if self._db:
            self._db.save_participant(participant)

    def _persist_message(self, msg: RoomMessage) -> None:
        if self._db:
            self._db.save_message(msg)

    def _persist_task(self, task: Task) -> None:
        if self._db:
            self._db.save_task(task)

    def _persist_artifact(self, artifact: Artifact) -> None:
        if self._db:
            self._db.save_artifact(artifact)

    def _persist_agent(self, agent: RegisteredAgent) -> None:
        if self._db:
            self._db.save_agent(agent)

    def _persist_runner(self, runner: Runner) -> None:
        if self._db:
            self._db.save_runner(runner)

    def _persist_managed_agent(self, agent: ManagedAgent) -> None:
        if self._db:
            self._db.save_managed_agent(agent)

    def _persist_runner_job(self, job: RunnerJob) -> None:
        if self._db:
            self._db.save_runner_job(job)

    def enable_audit(self, enabled: bool = True) -> None:
        self._audit_enabled = enabled

    def get_meta(self, key: str) -> Optional[str]:
        if self._db and hasattr(self._db, "get_meta"):
            return self._db.get_meta(key)
        return self._memory_meta.get(key)

    def set_meta(self, key: str, value: str) -> None:
        if self._db and hasattr(self._db, "set_meta"):
            self._db.set_meta(key, value)
        else:
            self._memory_meta[key] = value

    # ── Multi-user / multi-tenant ──────────────────────────────────────────

    def count_users(self) -> int:
        if self._db and hasattr(self._db, "count_users"):
            return self._db.count_users()
        return len(getattr(self, "_memory_users", {}) or {})

    async def auth_status(self) -> dict[str, Any]:
        n = self.count_users()
        return {
            "has_users": n > 0,
            "user_count": n,
            "registration_open": n == 0 or open_registration(),
            "first_user_is_admin": True,
            "open_registration": open_registration(),
        }

    async def register_user(
        self,
        *,
        email: str,
        password: str,
        display_name: str = "",
        org_name: str = "",
        invite_code: str = "",
    ) -> dict[str, Any]:
        email = (email or "").strip().lower()
        if not email or "@" not in email:
            raise ValueError("Valid email required")
        if len(password or "") < 8:
            raise ValueError("Password must be at least 8 characters")
        if self._db and self._db.get_user_by_email(email):
            raise ValueError("Email already registered")
        if not self._db:
            self._memory_users = getattr(self, "_memory_users", {})
            if any(u.get("email") == email for u in self._memory_users.values()):
                raise ValueError("Email already registered")

        n = self.count_users()
        tenant_id: str
        role = "member"

        if n == 0:
            # First user → create tenant + admin
            tid = new_id()
            tname = (org_name or "My organization").strip() or "My organization"
            tenant = {
                "id": tid,
                "name": tname,
                "slug": slugify(tname),
                "created_at": utcnow().isoformat(),
                "metadata": {},
            }
            if self._db:
                self._db.save_tenant(tenant)
            else:
                self._memory_tenants = getattr(self, "_memory_tenants", {})
                self._memory_tenants[tid] = tenant
            tenant_id = tid
            role = "admin"
            self._ensure_general_room(tenant_id=tenant_id)
        elif invite_code:
            code_key = (invite_code or "").strip()
            inv = None
            if self._db:
                inv = self._db.get_invite(code_key)
            else:
                mem = getattr(self, "_memory_invites", {})
                inv = mem.get(code_key) or mem.get(code_key.upper())
            if not inv:
                raise ValueError("Invalid invite code")
            if inv.get("uses", 0) >= inv.get("max_uses", 20):
                raise ValueError("Invite code exhausted")
            tenant_id = inv["tenant_id"]
            role = inv.get("role") or "member"
            inv["uses"] = int(inv.get("uses") or 0) + 1
            if self._db:
                self._db.save_invite(inv)
            else:
                self._memory_invites = getattr(self, "_memory_invites", {})
                self._memory_invites[inv["code"]] = inv
        elif open_registration():
            # Join first tenant
            tenants = self._db.list_tenants() if self._db else list(
                getattr(self, "_memory_tenants", {}).values()
            )
            if not tenants:
                raise ValueError("No organization yet — contact admin")
            tenant_id = tenants[0]["id"]
            role = "member"
        else:
            raise ValueError("Registration closed — ask an admin for an invite code")

        uid = new_id()
        user = {
            "id": uid,
            "tenant_id": tenant_id,
            "email": email,
            "password_hash": hash_password(password),
            "role": role,
            "display_name": (display_name or email.split("@")[0]).strip(),
            "created_at": utcnow().isoformat(),
            "last_login_at": None,
            "metadata": {},
        }
        if self._db:
            self._db.save_user(user)
        else:
            self._memory_users[uid] = user

        session = await self.create_session(user)
        await self.audit(
            "user.register",
            actor=email,
            resource_type="user",
            resource_id=uid,
            detail={"tenant_id": tenant_id, "role": role},
        )
        return {
            "user": {
                "id": uid,
                "email": email,
                "role": role,
                "display_name": user["display_name"],
                "tenant_id": tenant_id,
            },
            "token": session["token"],
            "tenant_id": tenant_id,
        }

    async def login_user(self, email: str, password: str) -> dict[str, Any]:
        email = (email or "").strip().lower()
        user = None
        if self._db:
            user = self._db.get_user_by_email(email)
        else:
            for u in getattr(self, "_memory_users", {}).values():
                if u.get("email") == email:
                    user = u
                    break
        if not user or not verify_password(password, user.get("password_hash") or ""):
            raise ValueError("Invalid email or password")
        user["last_login_at"] = utcnow().isoformat()
        if self._db:
            self._db.save_user(user)
        session = await self.create_session(user)
        await self.audit(
            "user.login",
            actor=email,
            resource_type="user",
            resource_id=user["id"],
        )
        return {
            "user": {
                "id": user["id"],
                "email": user["email"],
                "role": user.get("role") or "member",
                "display_name": user.get("display_name") or email.split("@")[0],
                "tenant_id": user["tenant_id"],
            },
            "token": session["token"],
            "tenant_id": user["tenant_id"],
        }

    async def create_session(self, user: dict[str, Any]) -> dict[str, Any]:
        raw = mint_session_token()
        exp = utcnow() + __import__("datetime").timedelta(days=session_ttl_days())
        rec = {
            "id": new_id(),
            "token_hash": hash_token(raw),
            "user_id": user["id"],
            "tenant_id": user["tenant_id"],
            "expires_at": exp.isoformat(),
            "created_at": utcnow().isoformat(),
        }
        if self._db:
            self._db.save_session(rec)
        else:
            self._memory_sessions = getattr(self, "_memory_sessions", {})
            self._memory_sessions[rec["token_hash"]] = rec
        return {"token": raw, "expires_at": rec["expires_at"]}

    async def verify_session_token(self, token: str) -> Optional[dict[str, Any]]:
        if not token or not token.startswith("ogs_"):
            return None
        th = hash_token(token)
        rec = None
        if self._db:
            rec = self._db.get_session(th)
        else:
            rec = getattr(self, "_memory_sessions", {}).get(th)
        if not rec:
            return None
        try:
            from datetime import datetime

            exp = datetime.fromisoformat(rec["expires_at"])
            if exp.tzinfo is None:
                from datetime import timezone

                exp = exp.replace(tzinfo=timezone.utc)
            if exp < utcnow():
                return None
        except Exception:
            return None
        user = None
        if self._db:
            user = self._db.get_user(rec["user_id"])
        else:
            user = getattr(self, "_memory_users", {}).get(rec["user_id"])
        if not user:
            return None
        return {
            "user_id": user["id"],
            "email": user["email"],
            "role": user.get("role") or "member",
            "display_name": user.get("display_name") or user["email"].split("@")[0],
            "tenant_id": user["tenant_id"],
            "scopes": ["admin"]
            if user.get("role") == "admin"
            else ["write", "read", "pair", "push"],
        }

    async def logout_session(self, token: str) -> None:
        if not token:
            return
        th = hash_token(token)
        if self._db:
            self._db.delete_session(th)
        else:
            getattr(self, "_memory_sessions", {}).pop(th, None)

    async def create_invite(
        self, *, tenant_id: str, created_by: str, role: str = "member"
    ) -> dict[str, Any]:
        # Validate tenant exists when we have tenant storage
        if self._db and hasattr(self._db, "get_tenant"):
            if not self._db.get_tenant(tenant_id):
                raise ValueError("Unknown tenant_id")
        else:
            tenants = getattr(self, "_memory_tenants", {}) or {}
            if tenants and tenant_id not in tenants:
                raise ValueError("Unknown tenant_id")
        code = mint_invite_code()
        rec = {
            "id": new_id(),
            "tenant_id": tenant_id,
            "code": code,
            "role": role if role in {"admin", "member"} else "member",
            "created_by": created_by,
            "max_uses": 20,
            "uses": 0,
            "created_at": utcnow().isoformat(),
        }
        if self._db:
            self._db.save_invite(rec)
        else:
            self._memory_invites = getattr(self, "_memory_invites", {})
            self._memory_invites[code] = rec
        return self._public_invite(rec)

    def _public_invite(self, rec: dict[str, Any]) -> dict[str, Any]:
        return {
            "code": rec["code"],
            "role": rec.get("role") or "member",
            "max_uses": int(rec.get("max_uses") or 20),
            "uses": int(rec.get("uses") or 0),
            "created_at": rec.get("created_at"),
            "created_by": rec.get("created_by"),
        }

    async def list_invites(self, tenant_id: str) -> list[dict[str, Any]]:
        tid = (tenant_id or "").strip()
        if not tid:
            raise ValueError("tenant_id is required")
        if self._db and hasattr(self._db, "list_invites"):
            items = self._db.list_invites(tid)
        else:
            mem = getattr(self, "_memory_invites", {}) or {}
            items = [
                value
                for value in mem.values()
                if str(value.get("tenant_id") or "") == tid
            ]
            items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return [self._public_invite(item) for item in items]

    async def resolve_invite_tenant_id(
        self,
        *,
        explicit: Optional[str] = None,
        session_tenant_id: Optional[str] = None,
    ) -> str:
        if session_tenant_id:
            return session_tenant_id
        tid = (explicit or "").strip()
        if tid:
            if self._db and hasattr(self._db, "get_tenant"):
                if not self._db.get_tenant(tid):
                    raise ValueError("Unknown tenant_id")
            else:
                tenants = getattr(self, "_memory_tenants", {}) or {}
                if tenants and tid not in tenants:
                    raise ValueError("Unknown tenant_id")
            return tid
        tenants = (
            self._db.list_tenants()
            if self._db and hasattr(self._db, "list_tenants")
            else list(getattr(self, "_memory_tenants", {}).values())
        )
        if len(tenants) == 1:
            return str(tenants[0]["id"])
        raise ValueError("tenant_id required")

    async def claim_setup(self) -> bool:
        """Atomic one-time setup claim. Returns True if this caller won the claim."""
        async with self._lock:
            if self.get_meta("setup_claimed") == "1":
                return False
            self.set_meta("setup_claimed", "1")
            self.set_meta("setup_claimed_at", utcnow().isoformat())
            return True

    def attach_redis(self, bus: Any) -> None:
        """Attach optional RedisBus for multi-worker event fan-out + pair codes."""
        self._redis = bus
        if bus is not None:
            bus.start_listener(self._on_remote_event)

    def _on_remote_event(self, raw: dict[str, Any]) -> None:
        """Fan remote Redis events into local SSE subscribers (no re-publish)."""
        try:
            event = GatewayEvent.model_validate(raw)
        except Exception:
            return
        self._events.append(event)
        dead: list[asyncio.Queue[GatewayEvent]] = []
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            if q in self._subscribers:
                self._subscribers.remove(q)

    async def audit(
        self,
        action: str,
        *,
        actor: Optional[str] = None,
        room_id: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        detail: Optional[dict[str, Any]] = None,
        ip: Optional[str] = None,
        outcome: str = "ok",
    ) -> Optional[dict[str, Any]]:
        if not self._audit_enabled:
            return None
        entry = build_audit_entry(
            action=action,
            actor=actor,
            room_id=room_id,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=detail,
            ip=ip,
            outcome=outcome,
        )
        if self._db:
            return self._db.append_audit(entry)
        entry["id"] = len(self._memory_audit) + 1
        entry["created_at"] = utcnow().isoformat()
        self._memory_audit.appendleft(entry)
        return entry

    async def list_audit(
        self,
        *,
        limit: int = 100,
        action: Optional[str] = None,
        room_id: Optional[str] = None,
        since_id: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        if self._db:
            return self._db.list_audit(
                limit=limit, action=action, room_id=room_id, since_id=since_id
            )
        items = list(self._memory_audit)
        if action:
            items = [e for e in items if e.get("action") == action]
        if room_id:
            items = [e for e in items if e.get("room_id") == room_id]
        if since_id is not None:
            items = [e for e in items if int(e.get("id") or 0) > since_id]
        return items[: max(1, min(limit, 500))]

    async def emit(self, event: GatewayEvent) -> GatewayEvent:
        async with self._lock:
            self._seq += 1
            if not event.id:
                event.id = str(self._seq)
            self._events.append(event)
            dead: list[asyncio.Queue[GatewayEvent]] = []
            for q in self._subscribers:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    dead.append(q)
            for q in dead:
                self._subscribers.remove(q)
        # Cross-process fan-out (best-effort)
        if self._redis is not None:
            try:
                await self._redis.publish_event(event.model_dump(mode="json"))
            except Exception:
                pass
        return event

    def subscribe(self, maxsize: int = 256) -> asyncio.Queue[GatewayEvent]:
        q: asyncio.Queue[GatewayEvent] = asyncio.Queue(maxsize=maxsize)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[GatewayEvent]) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    def recent_events(
        self,
        room_id: Optional[str] = None,
        after_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[GatewayEvent]:
        items = list(self._events)
        if after_id:
            try:
                idx = next(i for i, e in enumerate(items) if e.id == after_id)
                items = items[idx + 1 :]
            except StopIteration:
                pass
        if room_id:
            items = [e for e in items if e.room_id == room_id or e.room_id is None]
        return items[-limit:]

    # ── Rooms ──────────────────────────────────────────────────────────────

    async def create_room(self, room: Room) -> Room:
        async with self._lock:
            self.rooms[room.id] = room
            self._persist_room(room)
        await self.emit(
            GatewayEvent(type="room", room_id=room.id, payload={"action": "created", "room": room.model_dump(mode="json")})
        )
        await self.audit(
            "room.create",
            actor=room.created_by,
            room_id=room.id,
            resource_type="room",
            resource_id=room.id,
            detail={"name": room.name, "tenant_id": room.tenant_id},
        )
        return room

    async def get_room(self, room_id: str) -> Optional[Room]:
        return self.rooms.get(room_id)

    async def list_rooms(
        self,
        tenant_id: Optional[str] = None,
        *,
        include_archived: bool = False,
    ) -> list[Room]:
        rooms = list(self.rooms.values())
        if tenant_id is not None:
            # Tenant users only see their tenant's rooms (legacy null rooms hidden)
            rooms = [r for r in rooms if r.tenant_id == tenant_id]
        if not include_archived:
            rooms = [r for r in rooms if r.status != RoomStatus.ARCHIVED]
        return rooms

    async def update_room(
        self,
        room: Room,
        *,
        action: str = "updated",
        previous: Optional[dict[str, Any]] = None,
    ) -> Room:
        room.updated_at = utcnow()
        async with self._lock:
            self.rooms[room.id] = room
            self._persist_room(room)
        await self.emit(
            GatewayEvent(
                type="room",
                room_id=room.id,
                payload={
                    "action": action,
                    "room": room.model_dump(mode="json"),
                    **({"previous": previous} if previous else {}),
                },
            )
        )
        return room

    async def patch_room(
        self,
        room_id: str,
        *,
        name: Optional[str] = None,
        goal: Optional[str] = None,
        project_path: Optional[str] = None,
        status: Optional[RoomStatus] = None,
        metadata: Optional[dict[str, Any]] = None,
        actor: str = "system",
        announce: bool = True,
    ) -> Room:
        """Rename / archive / edit room; fan-out GatewayEvent + optional system message.

        Room id is stable so agents keep the same room_id while tracking name/status.
        """
        from opengateway.models import text_message

        room = self.rooms.get(room_id)
        if not room:
            raise KeyError(f"Room not found: {room_id}")

        previous = {
            "name": room.name,
            "goal": room.goal,
            "status": room.status.value if hasattr(room.status, "value") else str(room.status),
            "project_path": room.project_path,
        }
        changed: list[str] = []

        if name is not None:
            cleaned = " ".join(str(name).split()).strip()
            if cleaned and cleaned != room.name:
                room.name = cleaned
                changed.append("name")
        if goal is not None and goal != room.goal:
            room.goal = goal
            changed.append("goal")
        if project_path is not None and project_path != room.project_path:
            room.project_path = project_path or None
            changed.append("project_path")
        if status is not None and status != room.status:
            room.status = status
            changed.append("status")
        if metadata is not None:
            room.metadata = {**room.metadata, **metadata}
            changed.append("metadata")

        if not changed:
            return room

        if changed == ["status"] and room.status == RoomStatus.ARCHIVED:
            action = "archived"
        elif (
            "status" in changed
            and previous.get("status") == RoomStatus.ARCHIVED.value
            and room.status != RoomStatus.ARCHIVED
        ):
            action = "unarchived"
        elif "name" in changed and set(changed) <= {"name", "goal"}:
            action = "renamed" if "name" in changed else "updated"
        elif "name" in changed:
            action = "renamed"
        else:
            action = "updated"

        await self.update_room(room, action=action, previous=previous)
        await self.audit(
            f"room.{action}",
            actor=actor,
            room_id=room.id,
            resource_type="room",
            resource_id=room.id,
            detail={"changed": changed, "previous": previous, "name": room.name},
        )

        if announce:
            if action == "renamed":
                body = (
                    f'Room renamed: "{previous["name"]}" → "{room.name}"'
                    + (f" (by {actor})" if actor and actor != "system" else "")
                )
            elif action == "archived":
                body = f'Room archived: "{room.name}"' + (
                    f" (by {actor})" if actor and actor != "system" else ""
                )
            elif action == "unarchived":
                body = f'Room restored: "{room.name}"' + (
                    f" (by {actor})" if actor and actor != "system" else ""
                )
            else:
                parts = []
                if "name" in changed:
                    parts.append(f'name "{previous["name"]}" → "{room.name}"')
                if "goal" in changed:
                    parts.append("goal updated")
                if "status" in changed:
                    parts.append(f"status → {room.status.value}")
                body = "Room updated: " + (", ".join(parts) if parts else "metadata")
            note = RoomMessage(
                room_id=room.id,
                from_participant_id="system",
                from_name="system",
                message=text_message("agent/system", body),
                metadata={
                    "system": True,
                    "room_event": action,
                    "room_id": room.id,
                    "room_name": room.name,
                    "previous": previous,
                    "actor": actor,
                },
            )
            await self.post_message(note)

        return room

    # ── Participants ───────────────────────────────────────────────────────

    async def join_room(self, room_id: str, participant: Participant) -> Participant:
        room = self.rooms.get(room_id)
        if not room:
            raise KeyError(f"Room not found: {room_id}")
        # If seat moved from another room, detach from old roster (no dual-room ghosts)
        old_room_id = participant.room_id
        participant.room_id = room_id
        participant.status = ParticipantStatus.ONLINE
        now = utcnow()
        participant.last_seen_at = now
        # Non-human / radio-capable agents start as listening while MCP radio warms up
        caps = {str(c).lower() for c in (participant.capabilities or [])}
        harness = str(getattr(participant.harness, "value", participant.harness) or "")
        if caps & {"listen", "radio"} or harness not in {"human", ""}:
            participant.last_poll_at = now
        async with self._lock:
            if old_room_id and old_room_id != room_id:
                old_room = self.rooms.get(old_room_id)
                if old_room and participant.id in old_room.participant_ids:
                    old_room.participant_ids = [
                        x for x in old_room.participant_ids if x != participant.id
                    ]
                    old_room.updated_at = utcnow()
                    self._persist_room(old_room)
            self.participants[participant.id] = participant
            if participant.id not in room.participant_ids:
                room.participant_ids.append(participant.id)
            if room.status == RoomStatus.OPEN:
                room.status = RoomStatus.ACTIVE
            room.updated_at = utcnow()
            self._persist_participant(participant)
            self._persist_room(room)
        await self.emit(
            GatewayEvent(
                type="participant",
                room_id=room_id,
                payload={"action": "joined", "participant": participant.model_dump(mode="json")},
            )
        )
        await self.audit(
            "participant.join",
            actor=participant.name,
            room_id=room_id,
            resource_type="participant",
            resource_id=participant.id,
            detail={"harness": str(participant.harness), "role": participant.role},
        )
        return participant

    async def leave_room(self, room_id: str, participant_id: str) -> Optional[Participant]:
        """Soft-leave: mark offline but keep seat (prevents ghost re-joins)."""
        participant = self.participants.get(participant_id)
        room = self.rooms.get(room_id)
        if not participant or not room:
            return None
        async with self._lock:
            participant.status = ParticipantStatus.OFFLINE
            participant.last_seen_at = utcnow()
            # Clear radio so presence is offline (not stuck listening)
            participant.last_poll_at = None
            # Keep participant_ids — rejoin reuses same identity
            room.updated_at = utcnow()
            self._persist_participant(participant)
            self._persist_room(room)
        await self.emit(
            GatewayEvent(
                type="participant",
                room_id=room_id,
                payload={"action": "left", "participant_id": participant_id},
            )
        )
        return participant

    def find_participant_by_identity(
        self, room_id: str, name: str, harness: str
    ) -> Optional[Participant]:
        """Match existing seat by name+harness (case-insensitive name)."""
        room = self.rooms.get(room_id)
        if not room:
            return None
        n = name.strip().lower()
        h = str(harness).lower()
        for pid in room.participant_ids:
            p = self.participants.get(pid)
            if not p:
                continue
            if p.name.strip().lower() == n and str(p.harness.value if hasattr(p.harness, "value") else p.harness).lower() == h:
                return p
        return None

    def find_participant_by_api_key(
        self, room_id: str, api_key_id: str
    ) -> Optional[Participant]:
        """Rejoin merge: one device key → one seat per room."""
        if not api_key_id:
            return None
        room = self.rooms.get(room_id)
        if not room:
            return None
        candidates: list[Participant] = []
        for pid in room.participant_ids:
            p = self.participants.get(pid)
            if not p:
                continue
            if (p.metadata or {}).get("api_key_id") == api_key_id:
                candidates.append(p)
        if not candidates:
            return None
        candidates.sort(
            key=lambda p: (
                0 if p.status == ParticipantStatus.ONLINE else 1,
                -(p.last_seen_at.timestamp() if p.last_seen_at else 0.0),
            )
        )
        return candidates[0]

    def participants_for_api_key(self, key_id: str) -> list[dict[str, Any]]:
        """Live seats using this key — for revoke confirm."""
        out: list[dict[str, Any]] = []
        for p in self.participants.values():
            if (p.metadata or {}).get("api_key_id") != key_id:
                continue
            out.append(
                {
                    "participant_id": p.id,
                    "name": p.name,
                    "room_id": p.room_id,
                    "status": p.status.value if hasattr(p.status, "value") else str(p.status),
                    "harness": p.harness.value if hasattr(p.harness, "value") else str(p.harness),
                    "presence": getattr(p, "presence", None),
                    "key_prefix": (p.metadata or {}).get("key_prefix"),
                }
            )
        return out

    # Agents that stop long-polling go stale; mark offline so @all does not spam them.
    STALE_ONLINE_SECONDS = 120.0

    async def refresh_stale_online(
        self, room_id: str, *, max_age_seconds: float | None = None
    ) -> list[str]:
        """Mark ONLINE participants offline if last_seen is older than max_age.

        Also cancels open/claimed nudge tasks for those participants so the board
        does not fill with ghost "Respond to …" work.
        Returns participant ids that transitioned offline.
        """
        max_age = (
            self.STALE_ONLINE_SECONDS if max_age_seconds is None else max_age_seconds
        )
        room = self.rooms.get(room_id)
        if not room:
            return []
        now = utcnow()
        marked: list[str] = []
        for pid in list(room.participant_ids):
            p = self.participants.get(pid)
            if not p or p.status != ParticipantStatus.ONLINE:
                continue
            seen = p.last_seen_at
            if seen.tzinfo is None:
                from datetime import timezone

                seen = seen.replace(tzinfo=timezone.utc)
            age = (now - seen).total_seconds()
            if age <= max_age:
                continue
            p.status = ParticipantStatus.OFFLINE
            p.last_seen_at = now
            async with self._lock:
                self._persist_participant(p)
            marked.append(pid)
            await self._cancel_nudge_tasks_for(room_id, pid)
            await self.emit(
                GatewayEvent(
                    type="participant",
                    room_id=room_id,
                    payload={
                        "action": "stale_offline",
                        "participant": p.model_dump(mode="json"),
                        "stale_seconds": age,
                    },
                )
            )
        return marked

    async def _cancel_nudge_tasks_for(self, room_id: str, participant_id: str) -> int:
        cancelled = 0
        for t in list(self.tasks.get(room_id, [])):
            if t.claimed_by != participant_id:
                continue
            if t.status not in {TaskStatus.OPEN, TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS}:
                continue
            if not (t.metadata or {}).get("nudge"):
                continue
            t.status = TaskStatus.CANCELLED
            t.result = "Cancelled — assignee went offline / stale"
            t.updated_at = utcnow()
            self._persist_task(t)
            cancelled += 1
            await self.emit(
                GatewayEvent(
                    type="task",
                    room_id=room_id,
                    payload={"action": "updated", "task": t.model_dump(mode="json")},
                )
            )
        return cancelled

    async def list_participants(self, room_id: str) -> list[Participant]:
        room = self.rooms.get(room_id)
        if not room:
            return []
        await self.refresh_stale_online(room_id)
        # Deduplicate display list: one row per name+harness (prefer online, then newest)
        seen: dict[str, Participant] = {}
        for pid in room.participant_ids:
            p = self.participants.get(pid)
            if not p:
                continue
            key = f"{p.name.strip().lower()}::{p.harness.value if hasattr(p.harness, 'value') else p.harness}"
            prev = seen.get(key)
            if not prev:
                seen[key] = p
                continue
            # Prefer online over offline
            if prev.status != ParticipantStatus.ONLINE and p.status == ParticipantStatus.ONLINE:
                seen[key] = p
            elif prev.status == p.status and p.last_seen_at >= prev.last_seen_at:
                seen[key] = p
        return list(seen.values())

    async def get_participant(self, participant_id: str) -> Optional[Participant]:
        return self.participants.get(participant_id)

    async def update_participant(
        self,
        participant_id: str,
        *,
        name: Optional[str] = None,
        role: Optional[str] = None,
        status: Optional[ParticipantStatus] = None,
        capabilities: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Optional[Participant]:
        """Rename / patch a participant in place (no new id)."""
        p = self.participants.get(participant_id)
        if not p:
            return None
        if name is not None:
            # Allow multi-word names; collapse internal whitespace runs only
            cleaned = " ".join(str(name).split())
            p.name = cleaned or p.name
        if role is not None:
            cleaned_role = " ".join(str(role).split())
            p.role = cleaned_role or p.role
        if status is not None:
            p.status = status
        if capabilities is not None:
            p.capabilities = capabilities
        if metadata is not None:
            p.metadata = {**p.metadata, **metadata}
        p.last_seen_at = utcnow()
        async with self._lock:
            self._persist_participant(p)
        await self.emit(
            GatewayEvent(
                type="participant",
                room_id=p.room_id,
                payload={"action": "updated", "participant": p.model_dump(mode="json")},
            )
        )
        return p

    async def touch_participant(
        self, participant_id: str, *, listening: bool = False
    ) -> None:
        """Mark participant active. listening=True when they are long-polling (radio on)."""
        p = self.participants.get(participant_id)
        if p:
            now = utcnow()
            p.last_seen_at = now
            p.status = ParticipantStatus.ONLINE
            if listening:
                p.last_poll_at = now
            # Debounced-ish: skip disk write on every long-poll touch.

    # ── Messages ───────────────────────────────────────────────────────────

    async def post_message(self, msg: RoomMessage) -> RoomMessage:
        async with self._lock:
            self.messages[msg.room_id].append(msg)
            p = self.participants.get(msg.from_participant_id)
            if p:
                p.last_seen_at = utcnow()
                p.status = ParticipantStatus.ONLINE
            self._persist_message(msg)
        await self.emit(
            GatewayEvent(
                type="message",
                room_id=msg.room_id,
                payload={"message": msg.model_dump(mode="json")},
            )
        )
        await self.audit(
            "message.post",
            actor=msg.from_name,
            room_id=msg.room_id,
            resource_type="message",
            resource_id=msg.id,
            detail={
                "dm": bool(msg.to_participant_id),
                "to": msg.to_participant_id,
            },
        )
        # Best-effort mobile push (non-blocking failures)
        try:
            await self._notify_push_for_message(msg)
        except Exception:
            pass
        return msg

    # ── API keys ───────────────────────────────────────────────────────────

    def _public_key(self, rec: dict[str, Any]) -> dict[str, Any]:
        return {
            k: rec.get(k)
            for k in (
                "id",
                "name",
                "key_prefix",
                "scopes",
                "role",
                "device_label",
                "created_at",
                "last_used_at",
                "revoked_at",
                "metadata",
            )
        }

    def _new_api_key_record(
        self,
        *,
        name: str,
        scopes: Optional[list[str]] = None,
        role: str = "contributor",
        device_label: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> tuple[str, dict[str, Any]]:
        secret = generate_secret()
        return secret, {
            "id": new_id(),
            "name": (name or "device").strip() or "device",
            "key_prefix": prefix_of(secret),
            "key_hash": hash_secret(secret),
            "scopes": normalize_scopes(scopes),
            "role": role or "contributor",
            "device_label": device_label or "",
            "created_at": utcnow().isoformat(),
            "last_used_at": None,
            "revoked_at": None,
            "metadata": metadata or {},
        }

    def _save_api_key_record(self, rec: dict[str, Any]) -> None:
        if self._db:
            self._db.save_api_key(rec)
        else:
            self._memory_api_keys[rec["id"]] = rec
            self._memory_api_by_hash[rec["key_hash"]] = rec["id"]

    async def create_api_key(
        self,
        *,
        name: str,
        scopes: Optional[list[str]] = None,
        role: str = "contributor",
        device_label: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        secret, rec = self._new_api_key_record(
            name=name,
            scopes=scopes,
            role=role,
            device_label=device_label,
            metadata=metadata,
        )
        self._save_api_key_record(rec)
        await self.audit(
            "api_key.create",
            actor=name,
            resource_type="api_key",
            resource_id=rec["id"],
            detail={"scopes": rec["scopes"], "device_label": device_label},
        )
        out = self._public_key(rec)
        out["token"] = secret  # shown once
        return out

    async def list_api_keys(self) -> list[dict[str, Any]]:
        if self._db:
            rows = self._db.list_api_keys()
        else:
            rows = list(self._memory_api_keys.values())
        return [self._public_key(r) for r in rows if not r.get("revoked_at")]

    async def revoke_api_key(self, key_id: str) -> bool:
        if self._db:
            rows = self._db.list_api_keys()
            rec = next((r for r in rows if r.get("id") == key_id), None)
            if not rec:
                return False
            rec["revoked_at"] = utcnow().isoformat()
            self._db.save_api_key(rec)
        else:
            rec = self._memory_api_keys.get(key_id)
            if not rec:
                return False
            rec["revoked_at"] = utcnow().isoformat()
        await self.audit(
            "api_key.revoke",
            resource_type="api_key",
            resource_id=key_id,
        )
        return True

    async def delete_api_key(self, key_id: str) -> bool:
        if self._db:
            ok = self._db.delete_api_key(key_id)
        else:
            rec = self._memory_api_keys.pop(key_id, None)
            if rec:
                self._memory_api_by_hash.pop(rec.get("key_hash", ""), None)
            ok = rec is not None
        if ok:
            await self.audit(
                "api_key.delete",
                resource_type="api_key",
                resource_id=key_id,
            )
        return ok

    async def verify_api_key(self, secret: str) -> Optional[dict[str, Any]]:
        """Return public key metadata if secret is valid and not revoked."""
        if not secret:
            return None
        h = hash_secret(secret)
        rec: Optional[dict[str, Any]] = None
        if self._db:
            rec = self._db.get_api_key_by_hash(h)
        else:
            kid = self._memory_api_by_hash.get(h)
            rec = self._memory_api_keys.get(kid) if kid else None
        if not rec or rec.get("revoked_at"):
            return None
        rec["last_used_at"] = utcnow().isoformat()
        if self._db:
            self._db.save_api_key(rec)
        return self._public_key(rec)

    # ── Managed runners / agents ──────────────────────────────────────────

    @staticmethod
    def _normalize_runner_pair_code(code: str) -> str:
        return "".join(ch for ch in (code or "").upper() if ch.isalnum())

    async def create_runner_pair_code(
        self,
        *,
        name: str,
        tenant_id: Optional[str] = None,
        created_by: str = "admin",
        ttl_seconds: int | None = None,
    ) -> tuple[str, RunnerPairCode]:
        """Create a hashed, single-use runner bootstrap code."""
        ttl = max(
            60,
            min(
                int(ttl_seconds or self.RUNNER_PAIR_TTL_SECONDS),
                3600,
            ),
        )
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        async with self._lock:
            while True:
                compact = "".join(secrets.choice(alphabet) for _ in range(12))
                digest = hash_secret(compact)
                if digest not in self.runner_pair_codes:
                    break
            raw_code = "-".join(compact[i : i + 4] for i in range(0, 12, 4))
            pair = RunnerPairCode(
                code_hash=digest,
                name=(
                    scrub_secret_text(name or "runner", max_length=120).strip()
                    or "runner"
                ),
                tenant_id=tenant_id,
                created_by=created_by or "admin",
                expires_at=utcnow() + timedelta(seconds=ttl),
            )
            self.runner_pair_codes[digest] = pair
            if self._db:
                self._db.save_runner_pair_code(pair)
        await self.audit(
            "runner.pair.create",
            actor=created_by,
            resource_type="runner_pair",
            resource_id=pair.id,
            detail={"name": pair.name, "expires_at": pair.expires_at.isoformat()},
        )
        return raw_code, pair

    async def redeem_runner_pair_code(
        self,
        *,
        code: str,
        name: str = "",
        hostname: str = "",
        platform: str = "",
        version: str = "",
        capabilities: Optional[dict[str, Any]] = None,
    ) -> Optional[tuple[Runner, str]]:
        """Consume a pairing code and mint a runner-only bearer identity."""
        compact = self._normalize_runner_pair_code(code)
        if len(compact) != 12:
            return None
        digest = hash_secret(compact)
        now = utcnow()
        async with self._lock:
            if self._db and hasattr(self._db, "consume_runner_pair_code"):
                pair = self._db.consume_runner_pair_code(digest, now)
                if pair is not None:
                    self.runner_pair_codes[digest] = pair
            else:
                pair = self.runner_pair_codes.get(digest)
                if (
                    pair is None
                    or pair.used_at is not None
                    or pair.expires_at <= now
                ):
                    pair = None
                else:
                    pair.used_at = now
                    self.runner_pair_codes[digest] = pair
            if pair is None:
                return None

            requested_name = (name or pair.name or "runner").strip()
            if (
                requested_name.casefold() == "runner"
                and (hostname or "").strip()
            ):
                requested_name = hostname.strip()
            runner = Runner(
                name=(
                    scrub_secret_text(
                        requested_name, max_length=120
                    ).strip()
                    or "runner"
                ),
                hostname=scrub_secret_text(
                    hostname or "", max_length=255
                ).strip(),
                platform=scrub_secret_text(
                    platform or "", max_length=120
                ).strip(),
                version=scrub_secret_text(
                    version or "", max_length=80
                ).strip(),
                capabilities=scrub_secrets(capabilities or {}),
                tenant_id=pair.tenant_id,
                status=RunnerStatus.STALE,
                metadata={"paired_by": pair.created_by},
            )
            token, key_record = self._new_api_key_record(
                name=f"runner:{runner.name}",
                scopes=[SCOPE_RUNNER],
                role="runner",
                device_label=runner.hostname or runner.name,
                metadata={
                    "runner_id": runner.id,
                    "tenant_id": runner.tenant_id,
                    "identity_type": "runner",
                },
            )
            runner.api_key_id = key_record["id"]
            self.runners[runner.id] = runner
            self._persist_runner(runner)
            self._save_api_key_record(key_record)

        await self.audit(
            "runner.pair.redeem",
            actor=runner.name,
            resource_type="runner",
            resource_id=runner.id,
            detail={"hostname": runner.hostname, "platform": runner.platform},
        )
        return runner, token

    def _runner_stale(self, runner: Runner) -> bool:
        seen = runner.last_seen_at
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        return (utcnow() - seen).total_seconds() > self.RUNNER_STALE_SECONDS

    async def get_runner(self, runner_id: str) -> Optional[Runner]:
        runner = self.runners.get(runner_id)
        if (
            runner
            and runner.status != RunnerStatus.REVOKED
            and self._runner_stale(runner)
            and runner.status != RunnerStatus.STALE
        ):
            runner.status = RunnerStatus.STALE
            self._persist_runner(runner)
        return runner

    async def list_runners(
        self, *, tenant_id: Optional[str] = None
    ) -> list[Runner]:
        items: list[Runner] = []
        for runner_id in list(self.runners):
            runner = await self.get_runner(runner_id)
            if not runner:
                continue
            if tenant_id is not None and runner.tenant_id != tenant_id:
                continue
            items.append(runner)
        return sorted(items, key=lambda item: item.created_at)

    async def heartbeat_runner(
        self,
        runner_id: str,
        *,
        version: Optional[str] = None,
        capabilities: Optional[dict[str, Any]] = None,
        agent_statuses: Optional[dict[str, ManagedAgentStatus]] = None,
        agent_activity: Optional[dict[str, str]] = None,
        agent_activity_by_room: Optional[dict[str, dict[str, str]]] = None,
    ) -> Runner:
        async with self._lock:
            runner = self.runners.get(runner_id)
            if not runner or runner.status == RunnerStatus.REVOKED:
                raise KeyError("Runner not found")
            runner.status = RunnerStatus.ONLINE
            runner.last_seen_at = utcnow()
            if version is not None:
                runner.version = scrub_secret_text(version, max_length=80)
            if capabilities is not None:
                runner.capabilities = scrub_secrets(capabilities)
            self._persist_runner(runner)
            for agent_id, reported in (agent_statuses or {}).items():
                agent = self.managed_agents.get(agent_id)
                if not agent or agent.runner_id != runner_id or agent.deleted_at:
                    continue
                if reported in {
                    ManagedAgentStatus.STARTING,
                    ManagedAgentStatus.RUNNING,
                    ManagedAgentStatus.STOPPED,
                    ManagedAgentStatus.ERROR,
                }:
                    agent.status = reported
                    agent.updated_at = utcnow()
                    self._persist_managed_agent(agent)
            for agent_id, activity in (agent_activity or {}).items():
                agent = self.managed_agents.get(agent_id)
                if not agent or agent.runner_id != runner_id or agent.deleted_at:
                    continue
                meta = dict(agent.metadata)
                cleaned = (activity or "").strip().lower()
                if cleaned:
                    meta["activity"] = cleaned[:32]
                else:
                    meta.pop("activity", None)
                agent.metadata = meta
                agent.updated_at = utcnow()
                self._persist_managed_agent(agent)
            for agent_id, room_map in (agent_activity_by_room or {}).items():
                agent = self.managed_agents.get(agent_id)
                if not agent or agent.runner_id != runner_id or agent.deleted_at:
                    continue
                meta = dict(agent.metadata)
                cleaned_map: dict[str, str] = {}
                for rid, act in (room_map or {}).items():
                    room_key = str(rid or "").strip()
                    if not room_key:
                        continue
                    cleaned_act = (act or "").strip().lower()
                    if cleaned_act:
                        cleaned_map[room_key] = cleaned_act[:32]
                if cleaned_map:
                    meta["activity_by_room"] = cleaned_map
                    meta["activity"] = "thinking"
                else:
                    meta.pop("activity_by_room", None)
                    if not (agent_activity or {}).get(agent_id):
                        meta.pop("activity", None)
                agent.metadata = meta
                agent.updated_at = utcnow()
                self._persist_managed_agent(agent)
        return runner

    async def revoke_runner(self, runner_id: str) -> bool:
        key_ids: list[str] = []
        async with self._lock:
            runner = self.runners.get(runner_id)
            if not runner:
                return False
            if runner.status == RunnerStatus.REVOKED:
                return True
            runner.status = RunnerStatus.REVOKED
            runner.revoked_at = utcnow()
            runner.last_seen_at = runner.revoked_at
            self._persist_runner(runner)
            if runner.api_key_id:
                key_ids.append(runner.api_key_id)
            for agent in self.managed_agents.values():
                if agent.runner_id != runner_id or agent.deleted_at:
                    continue
                if agent.active_key_id:
                    key_ids.append(agent.active_key_id)
                    agent.active_key_id = None
                key_ids.extend(agent.retiring_key_ids)
                agent.retiring_key_ids = []
                agent.status = ManagedAgentStatus.ERROR
                agent.last_error = "Runner revoked"
                agent.updated_at = utcnow()
                self._persist_managed_agent(agent)
            for job in self.runner_jobs.values():
                if (
                    job.runner_id == runner_id
                    and job.status
                    in {RunnerJobStatus.QUEUED, RunnerJobStatus.CLAIMED}
                ):
                    job.status = RunnerJobStatus.CANCELLED
                    job.completed_at = utcnow()
                    job.error = "Runner revoked"
                    self._persist_runner_job(job)
        for key_id in set(key_ids):
            await self.revoke_api_key(key_id)
        await self.audit(
            "runner.revoke",
            resource_type="runner",
            resource_id=runner_id,
        )
        return True

    def _pending_runner_job(
        self,
        agent_id: str,
        action: RunnerJobAction,
        *,
        target_room_id: Optional[str] = None,
    ) -> Optional[RunnerJob]:
        for job in self.runner_jobs.values():
            if (
                job.managed_agent_id == agent_id
                and job.action == action
                and job.status
                in {RunnerJobStatus.QUEUED, RunnerJobStatus.CLAIMED}
                and (
                    target_room_id is None
                    or (job.target_room_id or "") == target_room_id
                )
            ):
                return job
        return None

    def _membership_job_pending(self, agent_id: str) -> Optional[RunnerJob]:
        membership = {
            RunnerJobAction.JOIN_ROOM,
            RunnerJobAction.LEAVE_ROOM,
            RunnerJobAction.MOVE,
        }
        for job in self.runner_jobs.values():
            if (
                job.managed_agent_id == agent_id
                and job.action in membership
                and job.status
                in {RunnerJobStatus.QUEUED, RunnerJobStatus.CLAIMED}
            ):
                return job
        return None

    def _name_taken_in_room(
        self, room_id: str, name: str, *, except_id: str = ""
    ) -> bool:
        normalized = (name or "").strip().casefold()
        if not normalized:
            return False
        for existing in self.managed_agents.values():
            if existing.deleted_at is not None or existing.id == except_id:
                continue
            rooms = list(existing.room_ids or [])
            if existing.room_id and existing.room_id not in rooms:
                rooms.append(existing.room_id)
            if room_id in rooms and existing.name.strip().casefold() == normalized:
                return True
        return False

    @staticmethod
    def _membership_snapshot(agent: ManagedAgent) -> dict[str, Any]:
        return {
            "room_id": agent.room_id,
            "room_ids": list(agent.room_ids),
            "seats": dict(agent.seats),
            "participant_id": agent.participant_id,
            "status": agent.status.value,
        }

    def _enqueue_runner_job_locked(
        self,
        agent: ManagedAgent,
        action: RunnerJobAction,
        *,
        target_room_id: Optional[str] = None,
        room_id: Optional[str] = None,
    ) -> RunnerJob:
        pending = self._pending_runner_job(
            agent.id, action, target_room_id=target_room_id
        )
        if pending is not None:
            return pending
        job = RunnerJob(
            runner_id=agent.runner_id,
            managed_agent_id=agent.id,
            action=action,
            agent_name=agent.name,
            harness=agent.harness,
            room_id=room_id or agent.room_id,
            room_ids=list(agent.room_ids or [agent.room_id]),
            target_room_id=target_room_id,
        )
        self.runner_jobs[job.id] = job
        self._persist_runner_job(job)
        self._runner_job_events.setdefault(agent.runner_id, asyncio.Event()).set()
        return job

    async def create_managed_agent(
        self,
        *,
        name: str,
        harness: Harness,
        room_id: str,
        runner_id: str,
        tenant_id: Optional[str] = None,
    ) -> tuple[ManagedAgent, RunnerJob]:
        async with self._lock:
            runner = self.runners.get(runner_id)
            if not runner or runner.status == RunnerStatus.REVOKED:
                raise ValueError("Runner not found or revoked")
            if (
                runner.status != RunnerStatus.ONLINE
                or self._runner_stale(runner)
            ):
                runner.status = RunnerStatus.STALE
                self._persist_runner(runner)
                raise ValueError("Runner is not online")
            room = self.rooms.get(room_id)
            if not room:
                raise ValueError("Room not found")
            if tenant_id is not None and runner.tenant_id != tenant_id:
                raise ValueError("Runner is not in this organization")
            if (
                runner.tenant_id is not None
                and room.tenant_id is not None
                and runner.tenant_id != room.tenant_id
            ):
                raise ValueError("Runner and room belong to different organizations")
            if self._name_taken_in_room(room_id, name):
                raise ValueError(
                    "A managed agent with this name already exists in the room"
                )
            agent = ManagedAgent(
                name=scrub_secret_text(name or "", max_length=120).strip(),
                harness=harness,
                room_id=room_id,
                room_ids=[room_id],
                runner_id=runner_id,
                tenant_id=tenant_id or runner.tenant_id or room.tenant_id,
            )
            self.managed_agents[agent.id] = agent
            self._persist_managed_agent(agent)
            job = self._enqueue_runner_job_locked(agent, RunnerJobAction.START)
        await self.audit(
            "managed_agent.create",
            actor=agent.name,
            room_id=room_id,
            resource_type="managed_agent",
            resource_id=agent.id,
            detail={
                "runner_id": runner_id,
                "harness": agent.harness.value,
                "job_id": job.id,
            },
        )
        return agent, job

    async def get_managed_agent(
        self, agent_id: str, *, include_deleted: bool = True
    ) -> Optional[ManagedAgent]:
        agent = self.managed_agents.get(agent_id)
        if agent and not include_deleted and agent.deleted_at is not None:
            return None
        return agent

    async def list_managed_agents(
        self,
        *,
        tenant_id: Optional[str] = None,
        include_deleted: bool = False,
    ) -> list[ManagedAgent]:
        items = list(self.managed_agents.values())
        if tenant_id is not None:
            items = [item for item in items if item.tenant_id == tenant_id]
        if not include_deleted:
            items = [item for item in items if item.deleted_at is None]
        return sorted(items, key=lambda item: item.created_at)

    async def action_managed_agent(
        self,
        agent_id: str,
        action: RunnerJobAction,
        *,
        target_room_id: Optional[str] = None,
    ) -> tuple[ManagedAgent, Optional[RunnerJob]]:
        async with self._lock:
            agent = self.managed_agents.get(agent_id)
            if not agent:
                raise KeyError("Managed agent not found")
            if agent.deleted_at is not None and action != RunnerJobAction.DELETE:
                raise ValueError("Managed agent is deleted")
            pending = self._pending_runner_job(
                agent.id, action, target_room_id=(target_room_id or "").strip() or None
            )
            if pending is not None:
                return agent, pending

            if action == RunnerJobAction.START:
                if agent.status == ManagedAgentStatus.RUNNING:
                    return agent, None
                agent.status = ManagedAgentStatus.STARTING
            elif action == RunnerJobAction.STOP:
                if agent.status == ManagedAgentStatus.STOPPED:
                    return agent, None
                agent.status = ManagedAgentStatus.STOPPING
            elif action == RunnerJobAction.RESTART:
                agent.status = ManagedAgentStatus.RESTARTING
            elif action == RunnerJobAction.MOVE:
                if not (target_room_id or "").strip():
                    raise ValueError("target_room_id is required for move")
                agent.status = ManagedAgentStatus.RESTARTING
            elif action in {RunnerJobAction.JOIN_ROOM, RunnerJobAction.LEAVE_ROOM}:
                if not (target_room_id or "").strip():
                    raise ValueError("target_room_id is required")
                agent.status = ManagedAgentStatus.RESTARTING
            elif action == RunnerJobAction.DELETE:
                agent.status = ManagedAgentStatus.DELETING
            else:  # pragma: no cover - enum protects this boundary
                raise ValueError("Unsupported managed-agent action")

            # Stop, restart, start, and delete replace in-flight work.
            # Joining another room must not cancel a start that is still queued.
            membership = {
                RunnerJobAction.JOIN_ROOM,
                RunnerJobAction.LEAVE_ROOM,
                RunnerJobAction.MOVE,
            }
            target = (target_room_id or "").strip()
            for existing in self.runner_jobs.values():
                if (
                    existing.managed_agent_id != agent.id
                    or existing.status
                    not in {RunnerJobStatus.QUEUED, RunnerJobStatus.CLAIMED}
                ):
                    continue
                if action in membership:
                    if existing.action not in membership:
                        continue
                    same_target = (existing.target_room_id or "") == target
                    if action != RunnerJobAction.MOVE and not same_target:
                        continue
                existing.status = RunnerJobStatus.CANCELLED
                existing.completed_at = utcnow()
                existing.error = f"Superseded by {action.value}"
                self._persist_runner_job(existing)

            agent.last_error = None
            agent.updated_at = utcnow()
            self._persist_managed_agent(agent)
            job = self._enqueue_runner_job_locked(
                agent,
                action,
                target_room_id=(target_room_id or "").strip() or None,
            )

        await self.audit(
            f"managed_agent.{action.value}",
            actor=agent.name,
            room_id=agent.room_id,
            resource_type="managed_agent",
            resource_id=agent.id,
            detail={
                "runner_id": agent.runner_id,
                "job_id": job.id,
                "target_room_id": target_room_id,
            },
        )
        return agent, job

    def _require_live_room(self, room_id: str):
        room = self.rooms.get(room_id)
        if not room:
            raise ValueError("Room not found")
        if room.status == RoomStatus.ARCHIVED:
            raise ValueError("Cannot use an archived room")
        return room

    async def join_managed_agent_room(
        self, agent_id: str, room_id: str
    ) -> tuple[ManagedAgent, Optional[RunnerJob]]:
        target = (room_id or "").strip()
        if not target:
            raise ValueError("room_id is required")
        async with self._lock:
            agent = self.managed_agents.get(agent_id)
            if not agent or agent.deleted_at is not None:
                raise KeyError("Managed agent not found")
            self._require_live_room(target)
            if target in (agent.room_ids or []) and agent.seats.get(target):
                return agent, None
            if self._name_taken_in_room(target, agent.name, except_id=agent.id):
                raise ValueError(
                    "A managed agent with this name already exists in the room"
                )
            if self._membership_job_pending(agent.id):
                raise ValueError("A room change is already in progress")
            agent.metadata = {
                **agent.metadata,
                "membership_rollback": self._membership_snapshot(agent),
            }
            if target not in agent.room_ids:
                agent.room_ids = [*agent.room_ids, target]
            agent.updated_at = utcnow()
            self._persist_managed_agent(agent)
        return await self.action_managed_agent(
            agent_id, RunnerJobAction.JOIN_ROOM, target_room_id=target
        )

    async def leave_managed_agent_room(
        self, agent_id: str, room_id: str
    ) -> tuple[ManagedAgent, Optional[RunnerJob]]:
        target = (room_id or "").strip()
        if not target:
            raise ValueError("room_id is required")
        async with self._lock:
            agent = self.managed_agents.get(agent_id)
            if not agent or agent.deleted_at is not None:
                raise KeyError("Managed agent not found")
            rooms = list(agent.room_ids or [agent.room_id])
            if target not in rooms:
                raise ValueError("Agent is not in that room")
            if len(rooms) <= 1:
                raise ValueError("Stop the agent to leave its last room")
            if self._membership_job_pending(agent.id):
                raise ValueError("A room change is already in progress")
            agent.metadata = {
                **agent.metadata,
                "membership_rollback": self._membership_snapshot(agent),
            }
            agent.room_ids = [rid for rid in rooms if rid != target]
            agent.seats.pop(target, None)
            if agent.room_id == target:
                agent.room_id = agent.room_ids[0]
                agent.participant_id = agent.seats.get(agent.room_id)
            agent.updated_at = utcnow()
            self._persist_managed_agent(agent)
        return await self.action_managed_agent(
            agent_id, RunnerJobAction.LEAVE_ROOM, target_room_id=target
        )

    async def move_managed_agent(
        self, agent_id: str, room_id: str
    ) -> tuple[ManagedAgent, Optional[RunnerJob]]:
        target = (room_id or "").strip()
        if not target:
            raise ValueError("room_id is required")
        async with self._lock:
            agent = self.managed_agents.get(agent_id)
            if not agent:
                raise KeyError("Managed agent not found")
            if agent.deleted_at is not None:
                raise ValueError("Managed agent is deleted")
            rooms = list(agent.room_ids or [agent.room_id])
            if rooms == [target] or (agent.room_id == target and target in rooms and len(rooms) == 1):
                raise ValueError("Agent is already in that room")
            self._require_live_room(target)
            if target not in rooms and self._name_taken_in_room(
                target, agent.name, except_id=agent.id
            ):
                raise ValueError(
                    "A managed agent with this name already exists in the room"
                )
            if self._membership_job_pending(agent.id):
                raise ValueError("A room change is already in progress")
            source = agent.room_id
            agent.metadata = {
                **agent.metadata,
                "membership_rollback": self._membership_snapshot(agent),
            }
            next_rooms = [rid for rid in rooms if rid != source]
            if target not in next_rooms:
                next_rooms.append(target)
            agent.room_ids = next_rooms or [target]
            agent.seats.pop(source, None)
            agent.room_id = target
            agent.participant_id = agent.seats.get(target)
            agent.updated_at = utcnow()
            self._persist_managed_agent(agent)
            agent.status = ManagedAgentStatus.RESTARTING
            job = self._enqueue_runner_job_locked(
                agent,
                RunnerJobAction.MOVE,
                target_room_id=target,
                room_id=source,
            )
            self._persist_managed_agent(agent)
        await self.audit(
            "managed_agent.move",
            actor=agent.name,
            room_id=source,
            resource_type="managed_agent",
            resource_id=agent.id,
            detail={"target_room_id": target, "job_id": job.id},
        )
        return agent, job

    async def list_runner_jobs(
        self,
        runner_id: str,
        *,
        status: Optional[RunnerJobStatus] = None,
    ) -> list[RunnerJob]:
        jobs = [
            job for job in self.runner_jobs.values() if job.runner_id == runner_id
        ]
        if status is not None:
            jobs = [job for job in jobs if job.status == status]
        return sorted(jobs, key=lambda item: item.created_at)

    async def wait_for_runner_job(
        self, runner_id: str, *, timeout: float = 25.0
    ) -> Optional[RunnerJob]:
        await self.heartbeat_runner(runner_id)
        timeout = max(0.0, min(float(timeout), 30.0))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        event = self._runner_job_events.setdefault(runner_id, asyncio.Event())
        while True:
            queued = await self.list_runner_jobs(
                runner_id, status=RunnerJobStatus.QUEUED
            )
            if queued:
                return queued[0]
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            event.clear()
            # Re-check after clear to avoid dropping a set between list and clear.
            queued = await self.list_runner_jobs(
                runner_id, status=RunnerJobStatus.QUEUED
            )
            if queued:
                return queued[0]
            try:
                await asyncio.wait_for(event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                return None

    async def claim_runner_job(
        self, runner_id: str, job_id: str
    ) -> tuple[RunnerJob, Optional[str]]:
        """Atomically claim one job and return a launch token at most once."""
        token: Optional[str] = None
        key_record: Optional[dict[str, Any]] = None
        async with self._lock:
            current = self.runner_jobs.get(job_id)
            if not current or current.runner_id != runner_id:
                raise KeyError("Runner job not found")
            if current.status != RunnerJobStatus.QUEUED:
                return current, None
            agent = self.managed_agents.get(current.managed_agent_id)
            if not agent or agent.runner_id != runner_id:
                raise KeyError("Managed agent not found")

            claimed = current.model_copy(deep=True)
            claimed.status = RunnerJobStatus.CLAIMED
            claimed.claimed_at = utcnow()
            updated_agent = agent.model_copy(deep=True)
            if claimed.action in {
                RunnerJobAction.START,
                RunnerJobAction.RESTART,
            } or (
                claimed.action == RunnerJobAction.JOIN_ROOM
                and not agent.active_key_id
            ):
                key_room_id = agent.room_id
                if (
                    claimed.action == RunnerJobAction.JOIN_ROOM
                    and (claimed.target_room_id or "").strip()
                    and not agent.room_ids
                ):
                    key_room_id = str(claimed.target_room_id).strip()
                token, key_record = self._new_api_key_record(
                    name=agent.name,
                    scopes=[SCOPE_READ, SCOPE_WRITE, SCOPE_TOOLS],
                    role="contributor",
                    device_label=f"managed:{agent.harness.value}",
                    metadata={
                        "managed_agent_id": agent.id,
                        "room_id": key_room_id,
                        "room_ids": list(agent.room_ids or [key_room_id]),
                        "runner_id": runner_id,
                        "tenant_id": agent.tenant_id,
                        "identity_type": "managed_agent",
                        "identity": {
                            "name": agent.name,
                            "harness": agent.harness.value,
                            "room_id": key_room_id,
                            "managed_agent_id": agent.id,
                        },
                    },
                )
                if (
                    updated_agent.active_key_id
                    and updated_agent.active_key_id
                    not in updated_agent.retiring_key_ids
                ):
                    updated_agent.retiring_key_ids.append(
                        updated_agent.active_key_id
                    )
                updated_agent.active_key_id = key_record["id"]
                updated_agent.updated_at = utcnow()

            if self._db and hasattr(self._db, "claim_runner_job"):
                won = self._db.claim_runner_job(
                    claimed,
                    key_record=key_record,
                    agent=updated_agent,
                )
                if not won:
                    loaded = self._db.load_all()
                    stored = (loaded.get("runner_jobs") or {}).get(job_id)
                    if stored is not None:
                        self.runner_jobs[job_id] = stored
                    refreshed_agent = (loaded.get("managed_agents") or {}).get(
                        agent.id
                    )
                    if refreshed_agent is not None:
                        self.managed_agents[agent.id] = refreshed_agent
                    return stored or current, None
            else:
                if key_record is not None:
                    self._save_api_key_record(key_record)
                self._persist_runner_job(claimed)
                self._persist_managed_agent(updated_agent)

            self.runner_jobs[job_id] = claimed
            self.managed_agents[agent.id] = updated_agent

        if key_record is not None:
            await self.audit(
                "api_key.create",
                actor=updated_agent.name,
                room_id=updated_agent.room_id,
                resource_type="api_key",
                resource_id=key_record["id"],
                detail={
                    "scopes": key_record["scopes"],
                    "managed_agent_id": updated_agent.id,
                },
            )
        await self.audit(
            "runner_job.claim",
            actor=runner_id,
            room_id=updated_agent.room_id,
            resource_type="runner_job",
            resource_id=claimed.id,
            detail={
                "action": claimed.action.value,
                "managed_agent_id": claimed.managed_agent_id,
            },
        )
        return claimed, token

    async def complete_runner_job(
        self,
        runner_id: str,
        job_id: str,
        *,
        success: bool,
        result: Optional[dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> RunnerJob:
        revoke_key_ids: list[str] = []
        async with self._lock:
            job = self.runner_jobs.get(job_id)
            if not job or job.runner_id != runner_id:
                raise KeyError("Runner job not found")
            if job.status in {
                RunnerJobStatus.COMPLETED,
                RunnerJobStatus.FAILED,
                RunnerJobStatus.CANCELLED,
            }:
                return job
            if job.status != RunnerJobStatus.CLAIMED:
                raise ValueError("Runner job must be claimed before completion")
            agent = self.managed_agents.get(job.managed_agent_id)
            if not agent:
                raise KeyError("Managed agent not found")

            job.status = (
                RunnerJobStatus.COMPLETED if success else RunnerJobStatus.FAILED
            )
            job.completed_at = utcnow()
            job.result = scrub_secrets(result or {})
            job.error = (
                scrub_secret_text(error or "", max_length=1000) or None
            )

            if success:
                participant_id = (result or {}).get("participant_id")
                pid = (
                    participant_id.strip()[:128]
                    if isinstance(participant_id, str) and participant_id.strip()
                    else ""
                )
                meta = dict(agent.metadata)
                meta.pop("membership_rollback", None)
                agent.metadata = meta
                if job.action in {
                    RunnerJobAction.START,
                    RunnerJobAction.RESTART,
                    RunnerJobAction.MOVE,
                    RunnerJobAction.JOIN_ROOM,
                }:
                    agent.status = ManagedAgentStatus.RUNNING
                    seat_room = agent.room_id
                    if job.action in {
                        RunnerJobAction.MOVE,
                        RunnerJobAction.JOIN_ROOM,
                    }:
                        seat_room = (job.target_room_id or "").strip() or seat_room
                    if job.action == RunnerJobAction.MOVE and seat_room:
                        agent.room_id = seat_room
                        if seat_room not in agent.room_ids:
                            agent.room_ids = [*agent.room_ids, seat_room]
                    if pid and seat_room:
                        agent.seats = {**agent.seats, seat_room: pid}
                        if agent.room_id == seat_room:
                            agent.participant_id = pid
                    elif pid and job.action in {
                        RunnerJobAction.START,
                        RunnerJobAction.RESTART,
                    }:
                        agent.participant_id = pid
                        if agent.room_id:
                            agent.seats = {**agent.seats, agent.room_id: pid}
                    revoke_key_ids.extend(agent.retiring_key_ids)
                    agent.retiring_key_ids = []
                elif job.action == RunnerJobAction.LEAVE_ROOM:
                    left = (job.target_room_id or "").strip()
                    if left:
                        agent.seats = {
                            rid: seat
                            for rid, seat in agent.seats.items()
                            if rid != left
                        }
                        agent.room_ids = [
                            rid for rid in agent.room_ids if rid != left
                        ]
                    agent.status = ManagedAgentStatus.RUNNING
                    agent.participant_id = agent.seats.get(agent.room_id)
                elif job.action == RunnerJobAction.STOP:
                    agent.status = ManagedAgentStatus.STOPPED
                    if agent.active_key_id:
                        revoke_key_ids.append(agent.active_key_id)
                    revoke_key_ids.extend(agent.retiring_key_ids)
                    agent.active_key_id = None
                    agent.retiring_key_ids = []
                elif job.action == RunnerJobAction.DELETE:
                    agent.status = ManagedAgentStatus.DELETED
                    agent.deleted_at = agent.deleted_at or utcnow()
                    if agent.active_key_id:
                        revoke_key_ids.append(agent.active_key_id)
                    revoke_key_ids.extend(agent.retiring_key_ids)
                    agent.active_key_id = None
                    agent.retiring_key_ids = []
                agent.last_error = None
            else:
                restored = False
                if job.action in {
                    RunnerJobAction.JOIN_ROOM,
                    RunnerJobAction.LEAVE_ROOM,
                    RunnerJobAction.MOVE,
                }:
                    snap = agent.metadata.get("membership_rollback")
                    if isinstance(snap, dict):
                        agent.room_id = str(snap.get("room_id") or agent.room_id)
                        agent.room_ids = [
                            str(rid)
                            for rid in (snap.get("room_ids") or [])
                            if rid
                        ] or [agent.room_id]
                        seats = snap.get("seats") if isinstance(snap.get("seats"), dict) else {}
                        agent.seats = {str(k): str(v) for k, v in seats.items()}
                        raw_pid = snap.get("participant_id")
                        agent.participant_id = (
                            str(raw_pid) if isinstance(raw_pid, str) and raw_pid else None
                        )
                        try:
                            agent.status = ManagedAgentStatus(
                                str(snap.get("status") or "running")
                            )
                        except ValueError:
                            agent.status = ManagedAgentStatus.RUNNING
                        meta = dict(agent.metadata)
                        meta.pop("membership_rollback", None)
                        agent.metadata = meta
                        restored = True
                    target = (job.target_room_id or "").strip()
                    if job.action == RunnerJobAction.JOIN_ROOM and target:
                        agent.room_ids = [
                            rid for rid in agent.room_ids if rid != target
                        ]
                        agent.seats = {
                            rid: seat
                            for rid, seat in agent.seats.items()
                            if rid != target
                        }
                        if agent.room_id == target and agent.room_ids:
                            agent.room_id = agent.room_ids[0]
                            agent.participant_id = agent.seats.get(agent.room_id)
                if not restored:
                    agent.status = ManagedAgentStatus.ERROR
                agent.last_error = job.error or "Runner job failed"
                if not restored and job.action in {
                    RunnerJobAction.START,
                    RunnerJobAction.RESTART,
                    RunnerJobAction.DELETE,
                }:
                    if agent.active_key_id:
                        revoke_key_ids.append(agent.active_key_id)
                    revoke_key_ids.extend(agent.retiring_key_ids)
                    agent.active_key_id = None
                    agent.retiring_key_ids = []
                if job.action == RunnerJobAction.DELETE:
                    agent.deleted_at = None
            agent.updated_at = utcnow()
            self._persist_runner_job(job)
            self._persist_managed_agent(agent)

        for key_id in set(revoke_key_ids):
            await self.revoke_api_key(key_id)
        await self.audit(
            "runner_job.complete",
            actor=runner_id,
            room_id=agent.room_id,
            resource_type="runner_job",
            resource_id=job.id,
            outcome="ok" if success else "error",
            detail={
                "action": job.action.value,
                "managed_agent_id": agent.id,
                "success": success,
                "error": job.error,
            },
        )
        return job

    async def append_runner_log(
        self,
        *,
        runner_id: str,
        managed_agent_id: str,
        level: str,
        message: str,
    ) -> RunnerLogEntry:
        agent = self.managed_agents.get(managed_agent_id)
        if not agent or agent.runner_id != runner_id:
            raise KeyError("Managed agent not found")
        normalized_level = (
            level if level in {"debug", "info", "warning", "error"} else "info"
        )
        entry = RunnerLogEntry(
            runner_id=runner_id,
            managed_agent_id=managed_agent_id,
            level=normalized_level,  # type: ignore[arg-type]
            message=scrub_secret_text(message, max_length=4000),
        )
        async with self._lock:
            now = utcnow().timestamp()
            window = self._runner_log_windows[runner_id]
            cutoff = now - self.RUNNER_LOG_RATE_WINDOW_SECONDS
            while window and window[0] <= cutoff:
                window.popleft()
            if len(window) >= self.RUNNER_LOG_RATE_LIMIT:
                raise ValueError("Runner log rate limit exceeded")
            window.append(now)
            if self._db and hasattr(self._db, "save_runner_log"):
                self._db.save_runner_log(entry, keep=self.RUNNER_LOG_LIMIT)
            else:
                rows = self.runner_logs[managed_agent_id]
                rows.append(entry)
                if len(rows) > self.RUNNER_LOG_LIMIT:
                    del rows[: len(rows) - self.RUNNER_LOG_LIMIT]
        return entry

    async def list_runner_logs(
        self, managed_agent_id: str, *, limit: int = 200
    ) -> list[RunnerLogEntry]:
        limit = max(1, min(int(limit), self.RUNNER_LOG_LIMIT))
        if self._db and hasattr(self._db, "list_runner_logs"):
            return self._db.list_runner_logs(managed_agent_id, limit=limit)
        return list(self.runner_logs.get(managed_agent_id, []))[-limit:]

    # ── Web Push subscriptions ─────────────────────────────────────────────

    async def save_push_subscription(
        self,
        *,
        subscription: dict[str, Any],
        participant_id: Optional[str] = None,
        device_label: str = "",
    ) -> dict[str, Any]:
        endpoint = (subscription or {}).get("endpoint") or ""
        if not endpoint:
            raise ValueError("subscription.endpoint required")
        sub = {
            "id": new_id(),
            "endpoint": endpoint,
            "subscription": subscription,
            "participant_id": participant_id,
            "device_label": device_label or "",
            "created_at": utcnow().isoformat(),
        }
        # Replace same endpoint
        if self._db:
            self._db.delete_push_sub_by_endpoint(endpoint)
            self._db.save_push_sub(sub)
        else:
            dead = [
                i
                for i, s in self._memory_push.items()
                if s.get("endpoint") == endpoint
            ]
            for i in dead:
                self._memory_push.pop(i, None)
            self._memory_push[sub["id"]] = sub
        await self.audit(
            "push.subscribe",
            resource_type="push",
            resource_id=sub["id"],
            detail={"participant_id": participant_id, "device_label": device_label},
        )
        return {
            "id": sub["id"],
            "endpoint": endpoint,
            "participant_id": participant_id,
            "device_label": device_label,
        }

    async def list_push_subscriptions(
        self, participant_id: Optional[str] = None
    ) -> list[dict[str, Any]]:
        if self._db:
            rows = self._db.list_push_subs(participant_id)
        else:
            rows = list(self._memory_push.values())
            if participant_id:
                rows = [s for s in rows if s.get("participant_id") == participant_id]
        return [
            {
                "id": r["id"],
                "endpoint": r.get("endpoint"),
                "participant_id": r.get("participant_id"),
                "device_label": r.get("device_label"),
                "created_at": r.get("created_at"),
            }
            for r in rows
        ]

    async def delete_push_subscription(self, sub_id: str) -> bool:
        if self._db:
            return self._db.delete_push_sub(sub_id)
        return self._memory_push.pop(sub_id, None) is not None

    async def _notify_push_for_message(self, msg: RoomMessage) -> None:
        from opengateway.push import (
            notification_for_message,
            send_web_push,
            vapid_configured,
        )

        if not vapid_configured():
            return
        # Target: explicit DM recipient, else all subs (room broadcast — careful)
        targets: list[dict[str, Any]]
        if self._db:
            all_subs = self._db.list_push_subs()
        else:
            all_subs = list(self._memory_push.values())
        if msg.to_participant_id:
            targets = [
                s
                for s in all_subs
                if s.get("participant_id") == msg.to_participant_id
            ]
        else:
            # Broadcast: only subs without a participant filter or room-wide devices
            targets = [
                s
                for s in all_subs
                if not s.get("participant_id")
                or s.get("participant_id") != msg.from_participant_id
            ]
            # Cap room broadcast spam
            targets = targets[:20]
        preview = ""
        try:
            preview = msg.message.text() if msg.message else ""
        except Exception:
            preview = ""
        payload = notification_for_message(
            room_id=msg.room_id,
            from_name=msg.from_name or "agent",
            preview=preview,
            is_dm=bool(msg.to_participant_id),
        )
        for s in targets:
            sub_info = s.get("subscription") or {}
            ok, err = send_web_push(sub_info, payload)
            if not ok and err and "410" in err:
                # Gone — drop
                if self._db:
                    self._db.delete_push_sub(s["id"])
                else:
                    self._memory_push.pop(s["id"], None)

    async def list_messages(
        self,
        room_id: str,
        *,
        since: Optional[str] = None,
        for_participant: Optional[str] = None,
        limit: int = 100,
        include_dms: bool = False,
    ) -> list[RoomMessage]:
        """List room messages.

        Privacy (production default):
        - Without ``for_participant``: only public room messages (no private DMs),
          unless ``include_dms=True`` (admin/master audit paths only).
        - With ``for_participant``: public + DMs involving that participant.
        """
        items = self.messages.get(room_id, [])
        if since:
            try:
                idx = next(i for i, m in enumerate(items) if m.id == since)
                items = items[idx + 1 :]
            except StopIteration:
                # Stale/unknown cursor — do not replay full transcript
                items = []
        if for_participant:
            items = [
                m
                for m in items
                if m.to_participant_id is None
                or m.to_participant_id == for_participant
                or m.from_participant_id == for_participant
            ]
        elif not include_dms:
            items = [m for m in items if m.to_participant_id is None]
        return items[-limit:]

    async def wait_for_messages(
        self,
        room_id: str,
        *,
        since: Optional[str] = None,
        for_participant: Optional[str] = None,
        timeout: float = 30.0,
        limit: int = 50,
    ) -> list[RoomMessage]:
        """Long-poll: return immediately if new messages exist, else wait up to timeout."""
        existing = await self.list_messages(
            room_id, since=since, for_participant=for_participant, limit=limit
        )
        if existing:
            return existing

        q = self.subscribe(maxsize=512)
        deadline = asyncio.get_event_loop().time() + max(0.0, timeout)
        try:
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                if event.type != "message" or event.room_id != room_id:
                    continue
                found = await self.list_messages(
                    room_id, since=since, for_participant=for_participant, limit=limit
                )
                if found:
                    return found
        finally:
            self.unsubscribe(q)

        return await self.list_messages(
            room_id, since=since, for_participant=for_participant, limit=limit
        )

    # ── Tasks ──────────────────────────────────────────────────────────────

    async def create_task(self, task: Task) -> Task:
        async with self._lock:
            self.tasks[task.room_id].append(task)
            self._persist_task(task)
        await self.emit(
            GatewayEvent(type="task", room_id=task.room_id, payload={"action": "created", "task": task.model_dump(mode="json")})
        )
        return task

    async def list_tasks(self, room_id: str, status: Optional[TaskStatus] = None) -> list[Task]:
        items = self.tasks.get(room_id, [])
        if status:
            items = [t for t in items if t.status == status]
        return items

    async def get_task(self, room_id: str, task_id: str) -> Optional[Task]:
        for t in self.tasks.get(room_id, []):
            if t.id == task_id:
                return t
        return None

    async def update_task(self, room_id: str, task_id: str, **fields: Any) -> Optional[Task]:
        task = await self.get_task(room_id, task_id)
        if not task:
            return None
        for k, v in fields.items():
            if v is not None and hasattr(task, k):
                setattr(task, k, v)
        task.updated_at = utcnow()
        self._persist_task(task)
        await self.emit(
            GatewayEvent(
                type="task",
                room_id=room_id,
                payload={"action": "updated", "task": task.model_dump(mode="json")},
            )
        )
        return task

    # ── Artifacts ──────────────────────────────────────────────────────────

    async def share_artifact(self, artifact: Artifact) -> Artifact:
        async with self._lock:
            self.artifacts[artifact.room_id].append(artifact)
            self._persist_artifact(artifact)
        await self.emit(
            GatewayEvent(
                type="artifact",
                room_id=artifact.room_id,
                payload={"action": "shared", "artifact": artifact.model_dump(mode="json")},
            )
        )
        return artifact

    async def list_artifacts(self, room_id: str) -> list[Artifact]:
        return list(self.artifacts.get(room_id, []))

    # ── Bookmarks / forks ──────────────────────────────────────────────────

    def _persist_bookmark(self, b: Bookmark) -> None:
        if self._db:
            self._db.save_bookmark(b)

    def _persist_fork(self, f: Fork) -> None:
        if self._db:
            self._db.save_fork(f)

    async def add_bookmark(self, bookmark: Bookmark) -> Bookmark:
        async with self._lock:
            # one bookmark per message per creator
            existing = [
                b
                for b in self.bookmarks[bookmark.room_id]
                if b.message_id == bookmark.message_id and b.created_by == bookmark.created_by
            ]
            if existing:
                return existing[0]
            self.bookmarks[bookmark.room_id].append(bookmark)
            self._persist_bookmark(bookmark)
        await self.emit(
            GatewayEvent(
                type="bookmark",
                room_id=bookmark.room_id,
                payload={"action": "created", "bookmark": bookmark.model_dump(mode="json")},
            )
        )
        return bookmark

    async def list_bookmarks(self, room_id: str) -> list[Bookmark]:
        return list(self.bookmarks.get(room_id, []))

    async def delete_bookmark(self, room_id: str, bookmark_id: str) -> bool:
        items = self.bookmarks.get(room_id, [])
        for i, b in enumerate(items):
            if b.id == bookmark_id:
                items.pop(i)
                if self._db:
                    self._db.delete_bookmark(bookmark_id)
                await self.emit(
                    GatewayEvent(
                        type="bookmark",
                        room_id=room_id,
                        payload={"action": "deleted", "bookmark_id": bookmark_id},
                    )
                )
                return True
        return False

    async def add_fork(self, fork: Fork) -> Fork:
        async with self._lock:
            self.forks[fork.room_id].append(fork)
            self._persist_fork(fork)
        await self.emit(
            GatewayEvent(
                type="fork",
                room_id=fork.room_id,
                payload={"action": "created", "fork": fork.model_dump(mode="json")},
            )
        )
        return fork

    async def list_forks(self, room_id: str) -> list[Fork]:
        return list(self.forks.get(room_id, []))

    async def get_message(self, room_id: str, message_id: str) -> Optional[RoomMessage]:
        for m in self.messages.get(room_id, []):
            if m.id == message_id:
                return m
        return None

    # ── Gateway registry ───────────────────────────────────────────────────

    async def upsert_gateway(self, gw: GatewayRecord) -> GatewayRecord:
        async with self._lock:
            self.gateways[gw.id] = gw
            if self._db:
                self._db.save_gateway(gw)
        return gw

    async def list_gateways(self) -> list[GatewayRecord]:
        return list(self.gateways.values())

    async def delete_gateway(self, gateway_id: str) -> bool:
        async with self._lock:
            if gateway_id in self.gateways:
                del self.gateways[gateway_id]
                if self._db:
                    self._db.delete_gateway(gateway_id)
                return True
        return False

    # ── Agents / runs ──────────────────────────────────────────────────────

    async def register_agent(self, agent: RegisteredAgent) -> RegisteredAgent:
        async with self._lock:
            self.agents[agent.name] = agent
            self._persist_agent(agent)
        await self.emit(
            GatewayEvent(type="system", payload={"action": "agent_registered", "agent": agent.model_dump(mode="json")})
        )
        return agent

    async def list_agents(self) -> list[RegisteredAgent]:
        return list(self.agents.values())

    async def save_run(self, run: Run) -> Run:
        async with self._lock:
            self.runs[run.run_id] = run
        # Runs are ephemeral (in-flight ACP); not required for room continuity
        return run

    async def get_run(self, run_id: str) -> Optional[Run]:
        return self.runs.get(run_id)

    # ── Mobile pair codes ──────────────────────────────────────────────────

    async def create_pair_code(
        self,
        *,
        room_id: Optional[str] = None,
        label: str = "",
        ttl_seconds: int = 900,
    ) -> dict[str, Any]:
        """Create a short-lived pair code (memory + optional Redis for multi-worker)."""
        import secrets
        from datetime import timedelta

        # 16 hex chars (~64 bits) — short enough for QR, hard to brute-force
        code = secrets.token_hex(8).upper()
        while code in self.pair_codes:
            code = secrets.token_hex(8).upper()
        rec = {
            "code": code,
            "room_id": room_id,
            "label": label or "mobile",
            "created_at": utcnow().isoformat(),
            "expires_at": (utcnow() + timedelta(seconds=ttl_seconds)).isoformat(),
            "ttl_seconds": ttl_seconds,
            "uses": 0,
            "max_uses": 5,
        }
        async with self._lock:
            self._purge_expired_pairs()
            self.pair_codes[code] = rec
        if self._redis is not None:
            try:
                await self._redis.pair_set(code, rec, ttl_seconds)
            except Exception:
                pass
        await self.audit(
            "pair.create",
            room_id=room_id,
            resource_type="pair",
            resource_id=code,
            detail={"label": label, "ttl_seconds": ttl_seconds},
        )
        return dict(rec)

    def _purge_expired_pairs(self) -> None:
        now = utcnow()
        dead = []
        for code, rec in self.pair_codes.items():
            try:
                from datetime import datetime

                exp = datetime.fromisoformat(rec["expires_at"])
                if exp.tzinfo is None:
                    from datetime import timezone

                    exp = exp.replace(tzinfo=timezone.utc)
                if exp < now or rec.get("uses", 0) >= rec.get("max_uses", 5):
                    dead.append(code)
            except Exception:
                dead.append(code)
        for c in dead:
            self.pair_codes.pop(c, None)

    async def redeem_pair_code(self, code: str) -> Optional[dict[str, Any]]:
        key = (code or "").strip().upper()
        # Prefer Redis when multi-worker (shared pair state)
        if self._redis is not None:
            try:
                remote = await self._redis.pair_incr_uses(key)
                if remote:
                    await self.audit(
                        "pair.redeem",
                        room_id=remote.get("room_id"),
                        resource_type="pair",
                        resource_id=key,
                        detail={"via": "redis"},
                    )
                    return remote
            except Exception:
                pass
        async with self._lock:
            self._purge_expired_pairs()
            rec = self.pair_codes.get(key)
            if not rec:
                return None
            rec["uses"] = int(rec.get("uses") or 0) + 1
            if rec["uses"] >= rec.get("max_uses", 5):
                # keep until purge for race; still return once more
                pass
            out = dict(rec)
        await self.audit(
            "pair.redeem",
            room_id=out.get("room_id"),
            resource_type="pair",
            resource_id=key,
            detail={"via": "memory"},
        )
        return out


def create_store(db_path: Union[str, Path, None] = ...) -> Store:  # type: ignore[assignment]
    return Store(db_path=db_path)


# Module default: respect OPENGATEWAY_DB / ~/.opengateway/state.db
# Tests should construct Store(db_path=None) for isolation.
store = Store()
