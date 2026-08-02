"""SQLite persistence so rooms survive gateway restarts."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

from opengateway.models import (
    Artifact,
    Bookmark,
    Fork,
    GatewayRecord,
    Participant,
    ParticipantStatus,
    RegisteredAgent,
    Room,
    RoomMessage,
    Task,
)


DEFAULT_DB_ENV = "OPENGATEWAY_DB"


def default_db_path() -> Optional[Path]:
    """Resolve DB path from env. Empty / 'none' / ':memory:' disables disk."""
    raw = os.environ.get(DEFAULT_DB_ENV, "").strip()
    if raw.lower() in {"none", "off", "false", "0", ":memory:"}:
        return None
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".opengateway" / "state.db"


class SqlitePersistence:
    """Thread-safe JSON-document store for collaboration state."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS rooms (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS participants (
                    id TEXT PRIMARY KEY,
                    room_id TEXT,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    room_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_room ON messages(room_id, created_at);
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    room_id TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    room_id TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agents (
                    name TEXT PRIMARY KEY,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bookmarks (
                    id TEXT PRIMARY KEY,
                    room_id TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS forks (
                    id TEXT PRIMARY KEY,
                    room_id TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS gateways (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            self._conn.commit()

    def _dump(self, model: Any) -> str:
        if hasattr(model, "model_dump"):
            return json.dumps(model.model_dump(mode="json"))
        return json.dumps(model)

    def save_room(self, room: Room) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO rooms (id, data) VALUES (?, ?)",
                (room.id, self._dump(room)),
            )
            self._conn.commit()

    def save_participant(self, participant: Participant) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO participants (id, room_id, data) VALUES (?, ?, ?)",
                (participant.id, participant.room_id, self._dump(participant)),
            )
            self._conn.commit()

    def delete_participant(self, participant_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM participants WHERE id = ?", (participant_id,))
            self._conn.commit()

    def save_message(self, msg: RoomMessage) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO messages (id, room_id, created_at, data) VALUES (?, ?, ?, ?)",
                (msg.id, msg.room_id, msg.created_at.isoformat(), self._dump(msg)),
            )
            self._conn.commit()

    def save_task(self, task: Task) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO tasks (id, room_id, data) VALUES (?, ?, ?)",
                (task.id, task.room_id, self._dump(task)),
            )
            self._conn.commit()

    def save_artifact(self, artifact: Artifact) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO artifacts (id, room_id, data) VALUES (?, ?, ?)",
                (artifact.id, artifact.room_id, self._dump(artifact)),
            )
            self._conn.commit()

    def save_agent(self, agent: RegisteredAgent) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO agents (name, data) VALUES (?, ?)",
                (agent.name, self._dump(agent)),
            )
            self._conn.commit()

    def save_bookmark(self, bookmark: Bookmark) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO bookmarks (id, room_id, data) VALUES (?, ?, ?)",
                (bookmark.id, bookmark.room_id, self._dump(bookmark)),
            )
            self._conn.commit()

    def delete_bookmark(self, bookmark_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM bookmarks WHERE id = ?", (bookmark_id,))
            self._conn.commit()

    def save_fork(self, fork: Fork) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO forks (id, room_id, data) VALUES (?, ?, ?)",
                (fork.id, fork.room_id, self._dump(fork)),
            )
            self._conn.commit()

    def save_gateway(self, gw: GatewayRecord) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO gateways (id, data) VALUES (?, ?)",
                (gw.id, self._dump(gw)),
            )
            self._conn.commit()

    def delete_gateway(self, gateway_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM gateways WHERE id = ?", (gateway_id,))
            self._conn.commit()

    def load_all(self) -> dict[str, Any]:
        """Return hydrated domain objects for Store."""
        with self._lock:
            rooms = {
                row["id"]: Room.model_validate_json(row["data"])
                for row in self._conn.execute("SELECT id, data FROM rooms")
            }
            participants = {
                row["id"]: Participant.model_validate_json(row["data"])
                for row in self._conn.execute("SELECT id, data FROM participants")
            }
            # After restart, nobody is actually connected
            for p in participants.values():
                p.status = ParticipantStatus.OFFLINE

            messages: dict[str, list[RoomMessage]] = {}
            for row in self._conn.execute(
                "SELECT room_id, data FROM messages ORDER BY created_at ASC"
            ):
                msg = RoomMessage.model_validate_json(row["data"])
                messages.setdefault(row["room_id"], []).append(msg)

            tasks: dict[str, list[Task]] = {}
            for row in self._conn.execute("SELECT room_id, data FROM tasks"):
                task = Task.model_validate_json(row["data"])
                tasks.setdefault(row["room_id"], []).append(task)

            artifacts: dict[str, list[Artifact]] = {}
            for row in self._conn.execute("SELECT room_id, data FROM artifacts"):
                art = Artifact.model_validate_json(row["data"])
                artifacts.setdefault(row["room_id"], []).append(art)

            agents = {
                row["name"]: RegisteredAgent.model_validate_json(row["data"])
                for row in self._conn.execute("SELECT name, data FROM agents")
            }

            bookmarks: dict[str, list[Bookmark]] = {}
            for row in self._conn.execute("SELECT room_id, data FROM bookmarks"):
                b = Bookmark.model_validate_json(row["data"])
                bookmarks.setdefault(row["room_id"], []).append(b)

            forks: dict[str, list[Fork]] = {}
            for row in self._conn.execute("SELECT room_id, data FROM forks"):
                f = Fork.model_validate_json(row["data"])
                forks.setdefault(row["room_id"], []).append(f)

            gateways = {
                row["id"]: GatewayRecord.model_validate_json(row["data"])
                for row in self._conn.execute("SELECT id, data FROM gateways")
            }

        return {
            "rooms": rooms,
            "participants": participants,
            "messages": messages,
            "tasks": tasks,
            "artifacts": artifacts,
            "agents": agents,
            "bookmarks": bookmarks,
            "forks": forks,
            "gateways": gateways,
        }
