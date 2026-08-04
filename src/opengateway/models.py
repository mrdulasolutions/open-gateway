"""Shared Pydantic models for OpenGateway and ACP-compatible payloads."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


# Presence for Live Ops badges (not a stored status enum — derived from activity).
# listening: recent long-poll / wait touch (radio on)
# joined: online or recently seen, but not actively long-polling
# offline: stale or left
LISTENING_SECONDS = 90.0
JOINED_STALE_SECONDS = 120.0


# ── ACP-compatible message shapes ──────────────────────────────────────────


class MessagePart(BaseModel):
    """ACP MessagePart — multimodal content unit."""

    name: Optional[str] = None
    content_type: str = "text/plain"
    content: Optional[str] = None
    content_encoding: Literal["plain", "base64"] = "plain"
    content_url: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class Message(BaseModel):
    """ACP Message — role + ordered parts."""

    role: str = "user"  # user | agent | agent/{name}
    parts: list[MessagePart] = Field(default_factory=list)

    def text(self) -> str:
        chunks: list[str] = []
        for p in self.parts:
            if p.content and (
                p.content_type.startswith("text/")
                or p.content_type == "application/json"
            ):
                chunks.append(p.content)
            elif p.name or p.content_url or (
                p.content and not p.content_type.startswith("text/")
            ):
                # Surface attachments so agents don't only see blank text
                label = p.name or "file"
                kind = p.content_type or "application/octet-stream"
                url = p.content_url or ""
                inline = "inline" if p.content else "link-only"
                chunks.append(
                    f"[attachment name={label!r} type={kind} {inline}"
                    + (f" url={url}" if url else "")
                    + "]"
                )
        return "\n".join(chunks)


def text_message(role: str, content: str, content_type: str = "text/plain") -> Message:
    return Message(role=role, parts=[MessagePart(content=content, content_type=content_type)])


# ── OpenGateway domain ─────────────────────────────────────────────────────


class Harness(str, Enum):
    GROK = "grok"
    CLAUDE_CODE = "claude-code"
    CURSOR = "cursor"
    HERMES = "hermes"
    ACP = "acp"
    MCP = "mcp"
    HUMAN = "human"
    OTHER = "other"


class ParticipantStatus(str, Enum):
    ONLINE = "online"
    AWAY = "away"
    BUSY = "busy"
    OFFLINE = "offline"


class RoomStatus(str, Enum):
    OPEN = "open"
    ACTIVE = "active"
    PAUSED = "paused"
    CLOSED = "closed"
    ARCHIVED = "archived"


class TaskStatus(str, Enum):
    OPEN = "open"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class Participant(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    harness: Harness = Harness.OTHER
    role: str = "contributor"  # coordinator | contributor | reviewer | observer
    status: ParticipantStatus = ParticipantStatus.ONLINE
    capabilities: list[str] = Field(default_factory=list)
    room_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    joined_at: datetime = Field(default_factory=utcnow)
    last_seen_at: datetime = Field(default_factory=utcnow)
    # Updated only on long-poll wait / WS pump — drives "listening" badge
    last_poll_at: Optional[datetime] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def presence(self) -> str:
        """Derived radio state: listening | joined | offline."""
        return presence_for(self)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def presence_for(p: Participant) -> str:
    """listening = active long-poll; joined = online but radio off; offline = stale."""
    now = utcnow()
    poll = _aware(p.last_poll_at)
    if poll is not None:
        if (now - poll).total_seconds() <= LISTENING_SECONDS:
            return "listening"
    if p.status == ParticipantStatus.ONLINE:
        seen = _aware(p.last_seen_at) or now
        if (now - seen).total_seconds() <= JOINED_STALE_SECONDS:
            return "joined"
    if p.status in {ParticipantStatus.AWAY, ParticipantStatus.BUSY}:
        seen = _aware(p.last_seen_at) or now
        if (now - seen).total_seconds() <= JOINED_STALE_SECONDS:
            return "joined"
    return "offline"


class RoomMessage(BaseModel):
    """A message posted into a collaboration room (ACP-shaped body)."""

    id: str = Field(default_factory=new_id)
    room_id: str
    from_participant_id: str
    from_name: str
    to_participant_id: Optional[str] = None  # None = broadcast
    message: Message
    created_at: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Task(BaseModel):
    id: str = Field(default_factory=new_id)
    room_id: str
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.OPEN
    created_by: str
    claimed_by: Optional[str] = None
    depends_on: list[str] = Field(default_factory=list)
    result: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Artifact(BaseModel):
    id: str = Field(default_factory=new_id)
    room_id: str
    name: str
    content_type: str = "text/plain"
    content: Optional[str] = None
    content_url: Optional[str] = None
    content_encoding: Literal["plain", "base64"] = "plain"
    shared_by: str
    description: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Room(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    goal: str = ""
    project_path: Optional[str] = None
    status: RoomStatus = RoomStatus.OPEN
    created_by: str = "system"
    # Multi-tenant isolation (null = legacy / master-token-only visibility)
    tenant_id: Optional[str] = None
    participant_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Bookmark(BaseModel):
    """Saved highlight / bookmark on a room message (outline-style)."""

    id: str = Field(default_factory=new_id)
    room_id: str
    message_id: str
    title: str
    excerpt: str = ""
    created_by: str
    created_at: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Fork(BaseModel):
    """Fork of a conversation into a new room (branch chat).

    ``room_id`` is the parent room. ``forked_room_id`` is the new branch room
    where the forked chat continues. Agents and UI open ``forked_room_id``.
    """

    id: str = Field(default_factory=new_id)
    room_id: str
    root_message_id: str
    forked_room_id: Optional[str] = None
    title: str
    created_by: str
    created_by_name: str = ""
    note: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── Request / response DTOs ────────────────────────────────────────────────


class CreateRoomRequest(BaseModel):
    name: str
    goal: str = ""
    project_path: Optional[str] = None
    created_by: str = "system"
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateRoomRequest(BaseModel):
    """Rename, edit goal/path, or archive/unarchive a room (id stable)."""

    name: Optional[str] = None
    goal: Optional[str] = None
    project_path: Optional[str] = None
    status: Optional[RoomStatus] = None
    metadata: Optional[dict[str, Any]] = None
    actor: Optional[str] = None
    announce: bool = True


class JoinRoomRequest(BaseModel):
    name: str
    harness: Harness = Harness.OTHER
    role: str = "contributor"
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    participant_id: Optional[str] = None  # re-join with known id


class UpdateParticipantRequest(BaseModel):
    """In-place identity update — rename without creating a new participant."""

    name: Optional[str] = None
    role: Optional[str] = None
    status: Optional[ParticipantStatus] = None
    capabilities: Optional[list[str]] = None
    metadata: Optional[dict[str, Any]] = None


class PostMessageRequest(BaseModel):
    from_participant_id: str
    content: str
    content_type: str = "text/plain"
    to_participant_id: Optional[str] = None
    role: Optional[str] = None  # defaults to agent/{from_name}
    metadata: dict[str, Any] = Field(default_factory=dict)
    parts: Optional[list[MessagePart]] = None
    # When true (or content addresses everyone), nudge each other online agent
    # with a DM + open task so multi-agent replies are more likely.
    nudge_all: bool = False


class CreateTaskRequest(BaseModel):
    title: str
    description: str = ""
    created_by: str
    depends_on: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateTaskRequest(BaseModel):
    status: Optional[TaskStatus] = None
    claimed_by: Optional[str] = None
    result: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class ShareArtifactRequest(BaseModel):
    name: str
    shared_by: str
    content: Optional[str] = None
    content_url: Optional[str] = None
    content_type: str = "text/plain"
    content_encoding: Literal["plain", "base64"] = "plain"
    description: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateBookmarkRequest(BaseModel):
    message_id: str
    title: str
    excerpt: str = ""
    created_by: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateForkRequest(BaseModel):
    root_message_id: str
    title: str = ""
    note: str = ""
    created_by: str
    created_by_name: str = ""
    # Copy this many messages before the root into the new room as context
    context_messages: int = Field(default=15, ge=0, le=100)
    # Auto-join creator into the forked room
    join_creator: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class GatewayRecord(BaseModel):
    """Known gateway (this node or a remote peer)."""

    id: str = Field(default_factory=new_id)
    name: str
    mode: str = "internal"  # internal | public
    network: str = "loopback"  # loopback | lan | tailscale | public
    base_url: str
    require_auth: bool = False
    is_self: bool = False
    notes: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RegisterGatewayRequest(BaseModel):
    name: str
    mode: str = "public"
    network: str = "public"
    base_url: str
    require_auth: bool = True
    notes: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class RegisterAgentRequest(BaseModel):
    name: str
    harness: Harness = Harness.OTHER
    description: str = ""
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RegisteredAgent(BaseModel):
    """External or virtual agent known to the gateway."""

    name: str
    description: str = ""
    harness: Harness = Harness.OTHER
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    registered_at: datetime = Field(default_factory=utcnow)


# ── ACP run shapes (subset of OpenAPI 0.2.0) ───────────────────────────────


class AgentManifest(BaseModel):
    name: str
    description: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentsListResponse(BaseModel):
    agents: list[AgentManifest]


class RunCreateRequest(BaseModel):
    agent_name: str
    input: list[Message] = Field(default_factory=list)
    mode: Literal["sync", "async", "stream"] = "sync"
    session_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunStatus(str, Enum):
    CREATED = "created"
    IN_PROGRESS = "in-progress"
    AWAITING = "awaiting"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


class Run(BaseModel):
    run_id: str = Field(default_factory=new_id)
    agent_name: str
    session_id: str = Field(default_factory=new_id)
    status: RunStatus = RunStatus.CREATED
    await_request: Optional[dict[str, Any]] = None
    output: list[Message] = Field(default_factory=list)
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: Optional[datetime] = None


class GatewayEvent(BaseModel):
    """SSE / poll event for room activity."""

    id: str = Field(default_factory=new_id)
    type: str  # message | task | artifact | participant | room | system
    room_id: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
