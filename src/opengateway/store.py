"""In-memory collaboration store with async-safe locks and event fan-out."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from typing import Any, Optional

from opengateway.models import (
    Artifact,
    GatewayEvent,
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


class Store:
    """Process-local store. Swap for Redis/Postgres later without changing handlers."""

    def __init__(self, event_history: int = 2000) -> None:
        self._lock = asyncio.Lock()
        self.rooms: dict[str, Room] = {}
        self.participants: dict[str, Participant] = {}
        self.messages: dict[str, list[RoomMessage]] = defaultdict(list)
        self.tasks: dict[str, list[Task]] = defaultdict(list)
        self.artifacts: dict[str, list[Artifact]] = defaultdict(list)
        self.agents: dict[str, RegisteredAgent] = {}
        self.runs: dict[str, Run] = {}
        self._events: deque[GatewayEvent] = deque(maxlen=event_history)
        self._subscribers: list[asyncio.Queue[GatewayEvent]] = []
        self._seq = 0

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
        await self.emit(
            GatewayEvent(
                type="participant",
                room_id=room_id,
                payload={"action": "joined", "participant": participant.model_dump(mode="json")},
            )
        )
        return participant

    async def leave_room(self, room_id: str, participant_id: str) -> Optional[Participant]:
        participant = self.participants.get(participant_id)
        room = self.rooms.get(room_id)
        if not participant or not room:
            return None
        async with self._lock:
            participant.status = ParticipantStatus.OFFLINE
            participant.last_seen_at = utcnow()
            if participant_id in room.participant_ids:
                room.participant_ids.remove(participant_id)
            room.updated_at = utcnow()
        await self.emit(
            GatewayEvent(
                type="participant",
                room_id=room_id,
                payload={"action": "left", "participant_id": participant_id},
            )
        )
        return participant

    async def list_participants(self, room_id: str) -> list[Participant]:
        room = self.rooms.get(room_id)
        if not room:
            return []
        return [self.participants[pid] for pid in room.participant_ids if pid in self.participants]

    async def get_participant(self, participant_id: str) -> Optional[Participant]:
        return self.participants.get(participant_id)

    async def touch_participant(self, participant_id: str) -> None:
        p = self.participants.get(participant_id)
        if p:
            p.last_seen_at = utcnow()
            p.status = ParticipantStatus.ONLINE

    # ── Messages ───────────────────────────────────────────────────────────

    async def post_message(self, msg: RoomMessage) -> RoomMessage:
        async with self._lock:
            self.messages[msg.room_id].append(msg)
            await self.touch_participant(msg.from_participant_id)
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

    # ── Tasks ──────────────────────────────────────────────────────────────

    async def create_task(self, task: Task) -> Task:
        async with self._lock:
            self.tasks[task.room_id].append(task)
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

    # ── Agents / runs ──────────────────────────────────────────────────────

    async def register_agent(self, agent: RegisteredAgent) -> RegisteredAgent:
        async with self._lock:
            self.agents[agent.name] = agent
        await self.emit(
            GatewayEvent(type="system", payload={"action": "agent_registered", "agent": agent.model_dump(mode="json")})
        )
        return agent

    async def list_agents(self) -> list[RegisteredAgent]:
        return list(self.agents.values())

    async def save_run(self, run: Run) -> Run:
        async with self._lock:
            self.runs[run.run_id] = run
        return run

    async def get_run(self, run_id: str) -> Optional[Run]:
        return self.runs.get(run_id)


# Singleton used by the HTTP and MCP servers
store = Store()
