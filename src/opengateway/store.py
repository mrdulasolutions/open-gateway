"""Collaboration store with optional SQLite persistence and event fan-out."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Optional, Union

from opengateway.models import (
    Artifact,
    Bookmark,
    Fork,
    GatewayEvent,
    GatewayRecord,
    Participant,
    ParticipantStatus,
    RegisteredAgent,
    Room,
    RoomMessage,
    RoomStatus,
    Run,
    Task,
    TaskStatus,
    utcnow,
)
from opengateway.api_keys import (
    generate_secret,
    hash_secret,
    normalize_scopes,
    prefix_of,
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
        # API keys / push live only in DB (or memory maps); loaded on demand

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
        return {"code": code, "role": rec["role"], "max_uses": 20}

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
        participant.last_seen_at = utcnow()
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

    async def create_api_key(
        self,
        *,
        name: str,
        scopes: Optional[list[str]] = None,
        role: str = "contributor",
        device_label: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        secret = generate_secret()
        rec = {
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
        if self._db:
            self._db.save_api_key(rec)
        else:
            self._memory_api_keys[rec["id"]] = rec
            self._memory_api_by_hash[rec["key_hash"]] = rec["id"]
        if metadata and metadata.get("tenant_id"):
            rec["metadata"] = {**rec.get("metadata", {}), "tenant_id": metadata["tenant_id"]}
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
                pass
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
