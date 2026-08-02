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
from opengateway.persistence import SqlitePersistence, default_db_path


class Store:
    """In-process store. When db_path is set, rooms and history survive restarts."""

    def __init__(
        self,
        event_history: int = 2000,
        db_path: Union[str, Path, None] = ...,  # type: ignore[assignment]
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
        # Short-lived phone/mobile pair codes (in-memory only)
        self.pair_codes: dict[str, dict[str, Any]] = {}
        self._events: deque[GatewayEvent] = deque(maxlen=event_history)
        self._subscribers: list[asyncio.Queue[GatewayEvent]] = []
        self._seq = 0

        # db_path=... means "use default from env"; None means memory-only
        if db_path is ...:
            resolved = default_db_path()
        elif db_path is None:
            resolved = None
        else:
            resolved = Path(db_path).expanduser()

        self.db_path = resolved
        self._db: Optional[SqlitePersistence] = None
        if resolved is not None:
            self._db = SqlitePersistence(resolved)
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
        return room

    async def get_room(self, room_id: str) -> Optional[Room]:
        return self.rooms.get(room_id)

    async def list_rooms(self) -> list[Room]:
        return list(self.rooms.values())

    async def update_room(self, room: Room) -> Room:
        room.updated_at = utcnow()
        async with self._lock:
            self.rooms[room.id] = room
            self._persist_room(room)
        await self.emit(
            GatewayEvent(type="room", room_id=room.id, payload={"action": "updated", "room": room.model_dump(mode="json")})
        )
        return room

    # ── Participants ───────────────────────────────────────────────────────

    async def join_room(self, room_id: str, participant: Participant) -> Participant:
        room = self.rooms.get(room_id)
        if not room:
            raise KeyError(f"Room not found: {room_id}")
        participant.room_id = room_id
        participant.status = ParticipantStatus.ONLINE
        participant.last_seen_at = utcnow()
        async with self._lock:
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

    async def touch_participant(self, participant_id: str) -> None:
        p = self.participants.get(participant_id)
        if p:
            p.last_seen_at = utcnow()
            p.status = ParticipantStatus.ONLINE
            # Debounced-ish: only persist status transitions are rare enough via join/leave;
            # skip disk write on every long-poll touch.

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
        return msg

    async def list_messages(
        self,
        room_id: str,
        *,
        since: Optional[str] = None,
        for_participant: Optional[str] = None,
        limit: int = 100,
    ) -> list[RoomMessage]:
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
        """Create a short-lived pair code for phone/web join (in-memory)."""
        import secrets
        from datetime import timedelta

        code = secrets.token_hex(3).upper()  # 6 hex chars
        while code in self.pair_codes:
            code = secrets.token_hex(3).upper()
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
        async with self._lock:
            self._purge_expired_pairs()
            rec = self.pair_codes.get((code or "").strip().upper())
            if not rec:
                return None
            rec["uses"] = int(rec.get("uses") or 0) + 1
            if rec["uses"] >= rec.get("max_uses", 5):
                # keep until purge for race; still return once more
                pass
            return dict(rec)


def create_store(db_path: Union[str, Path, None] = ...) -> Store:  # type: ignore[assignment]
    return Store(db_path=db_path)


# Module default: respect OPENGATEWAY_DB / ~/.opengateway/state.db
# Tests should construct Store(db_path=None) for isolation.
store = Store()
