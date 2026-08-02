"""Postgres multi-writer persistence (JSON document tables, same shape as SQLite)."""

from __future__ import annotations

import json
import threading
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


class PostgresPersistence:
    """Thread-safe JSON-document store on PostgreSQL (concurrent writers OK)."""

    def __init__(self, url: str) -> None:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as e:
            raise RuntimeError(
                "Postgres URL set but psycopg missing — uv sync --extra postgres"
            ) from e
        self.url = url
        self._lock = threading.Lock()
        self._psycopg = psycopg
        self._dict_row = dict_row
        self._conn = psycopg.connect(url, row_factory=dict_row, autocommit=False)
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_schema(self) -> None:
        ddl = """
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
        CREATE TABLE IF NOT EXISTS api_keys (
            id TEXT PRIMARY KEY,
            key_hash TEXT NOT NULL UNIQUE,
            data TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);
        CREATE TABLE IF NOT EXISTS push_subs (
            id TEXT PRIMARY KEY,
            endpoint TEXT NOT NULL UNIQUE,
            data TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id BIGSERIAL PRIMARY KEY,
            created_at TEXT NOT NULL,
            action TEXT NOT NULL,
            actor TEXT,
            room_id TEXT,
            resource_type TEXT,
            resource_id TEXT,
            outcome TEXT NOT NULL DEFAULT 'ok',
            ip TEXT,
            detail TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
        CREATE INDEX IF NOT EXISTS idx_audit_room ON audit_log(room_id);
        """
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(ddl)
            self._conn.commit()

    def _dump(self, model: Any) -> str:
        if hasattr(model, "model_dump"):
            return json.dumps(model.model_dump(mode="json"))
        return json.dumps(model)

    def _upsert(self, table: str, cols: list[str], vals: tuple[Any, ...]) -> None:
        placeholders = ", ".join(["%s"] * len(vals))
        col_list = ", ".join(cols)
        # first col is PK
        pk = cols[0]
        updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols[1:])
        sql = (
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT ({pk}) DO UPDATE SET {updates}"
        )
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(sql, vals)
            self._conn.commit()

    def save_room(self, room: Room) -> None:
        self._upsert("rooms", ["id", "data"], (room.id, self._dump(room)))

    def save_participant(self, participant: Participant) -> None:
        self._upsert(
            "participants",
            ["id", "room_id", "data"],
            (participant.id, participant.room_id, self._dump(participant)),
        )

    def delete_participant(self, participant_id: str) -> None:
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute("DELETE FROM participants WHERE id = %s", (participant_id,))
            self._conn.commit()

    def save_message(self, msg: RoomMessage) -> None:
        self._upsert(
            "messages",
            ["id", "room_id", "created_at", "data"],
            (msg.id, msg.room_id, msg.created_at.isoformat(), self._dump(msg)),
        )

    def save_task(self, task: Task) -> None:
        self._upsert(
            "tasks",
            ["id", "room_id", "data"],
            (task.id, task.room_id, self._dump(task)),
        )

    def save_artifact(self, artifact: Artifact) -> None:
        self._upsert(
            "artifacts",
            ["id", "room_id", "data"],
            (artifact.id, artifact.room_id, self._dump(artifact)),
        )

    def save_agent(self, agent: RegisteredAgent) -> None:
        self._upsert("agents", ["name", "data"], (agent.name, self._dump(agent)))

    def save_bookmark(self, bookmark: Bookmark) -> None:
        self._upsert(
            "bookmarks",
            ["id", "room_id", "data"],
            (bookmark.id, bookmark.room_id, self._dump(bookmark)),
        )

    def delete_bookmark(self, bookmark_id: str) -> None:
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute("DELETE FROM bookmarks WHERE id = %s", (bookmark_id,))
            self._conn.commit()

    def save_fork(self, fork: Fork) -> None:
        self._upsert(
            "forks",
            ["id", "room_id", "data"],
            (fork.id, fork.room_id, self._dump(fork)),
        )

    def save_gateway(self, gw: GatewayRecord) -> None:
        self._upsert("gateways", ["id", "data"], (gw.id, self._dump(gw)))

    def delete_gateway(self, gateway_id: str) -> None:
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute("DELETE FROM gateways WHERE id = %s", (gateway_id,))
            self._conn.commit()

    def get_meta(self, key: str) -> Optional[str]:
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute("SELECT value FROM meta WHERE key = %s", (key,))
                row = cur.fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._upsert("meta", ["key", "value"], (key, value))

    def save_api_key(self, record: dict[str, Any]) -> None:
        self._upsert(
            "api_keys",
            ["id", "key_hash", "data"],
            (record["id"], record["key_hash"], json.dumps(record)),
        )

    def delete_api_key(self, key_id: str) -> bool:
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute("DELETE FROM api_keys WHERE id = %s", (key_id,))
                n = cur.rowcount
            self._conn.commit()
            return n > 0

    def get_api_key_by_hash(self, key_hash: str) -> Optional[dict[str, Any]]:
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT data FROM api_keys WHERE key_hash = %s", (key_hash,)
                )
                row = cur.fetchone()
        if not row:
            return None
        return json.loads(row["data"])

    def list_api_keys(self) -> list[dict[str, Any]]:
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute("SELECT data FROM api_keys")
                rows = cur.fetchall()
        return [json.loads(r["data"]) for r in rows]

    def save_push_sub(self, sub: dict[str, Any]) -> None:
        self._upsert(
            "push_subs",
            ["id", "endpoint", "data"],
            (sub["id"], sub["endpoint"], json.dumps(sub)),
        )

    def delete_push_sub(self, sub_id: str) -> bool:
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute("DELETE FROM push_subs WHERE id = %s", (sub_id,))
                n = cur.rowcount
            self._conn.commit()
            return n > 0

    def delete_push_sub_by_endpoint(self, endpoint: str) -> bool:
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute("DELETE FROM push_subs WHERE endpoint = %s", (endpoint,))
                n = cur.rowcount
            self._conn.commit()
            return n > 0

    def list_push_subs(
        self, participant_id: Optional[str] = None
    ) -> list[dict[str, Any]]:
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute("SELECT data FROM push_subs")
                rows = cur.fetchall()
        items = [json.loads(r["data"]) for r in rows]
        if participant_id:
            items = [s for s in items if s.get("participant_id") == participant_id]
        return items

    def append_audit(self, entry: dict[str, Any]) -> dict[str, Any]:
        from opengateway.models import utcnow

        created = entry.get("created_at") or utcnow().isoformat()
        detail = entry.get("detail") or {}
        if not isinstance(detail, str):
            detail = json.dumps(detail)
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit_log
                      (created_at, action, actor, room_id, resource_type, resource_id, outcome, ip, detail)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        created,
                        entry.get("action") or "unknown",
                        entry.get("actor"),
                        entry.get("room_id"),
                        entry.get("resource_type"),
                        entry.get("resource_id"),
                        entry.get("outcome") or "ok",
                        entry.get("ip"),
                        detail,
                    ),
                )
                row = cur.fetchone()
            self._conn.commit()
        out = dict(entry)
        out["id"] = row["id"] if row else None
        out["created_at"] = created
        if isinstance(out.get("detail"), str):
            try:
                out["detail"] = json.loads(out["detail"])
            except Exception:
                pass
        return out

    def list_audit(
        self,
        *,
        limit: int = 100,
        action: Optional[str] = None,
        room_id: Optional[str] = None,
        since_id: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        clauses: list[str] = []
        params: list[Any] = []
        if action:
            clauses.append("action = %s")
            params.append(action)
        if room_id:
            clauses.append("room_id = %s")
            params.append(room_id)
        if since_id is not None:
            clauses.append("id > %s")
            params.append(int(since_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT id, created_at, action, actor, room_id, resource_type, "
            f"resource_id, outcome, ip, detail FROM audit_log {where} "
            f"ORDER BY id DESC LIMIT %s"
        )
        params.append(limit)
        rows: list[dict[str, Any]] = []
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
                for row in cur.fetchall():
                    detail: Any = row["detail"]
                    try:
                        detail = json.loads(detail) if detail else {}
                    except Exception:
                        pass
                    rows.append(
                        {
                            "id": row["id"],
                            "created_at": row["created_at"],
                            "action": row["action"],
                            "actor": row["actor"],
                            "room_id": row["room_id"],
                            "resource_type": row["resource_type"],
                            "resource_id": row["resource_id"],
                            "outcome": row["outcome"],
                            "ip": row["ip"],
                            "detail": detail,
                        }
                    )
        return rows

    def load_all(self) -> dict[str, Any]:
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute("SELECT id, data FROM rooms")
                rooms = {
                    row["id"]: Room.model_validate_json(row["data"])
                    for row in cur.fetchall()
                }
                cur.execute("SELECT id, data FROM participants")
                participants = {
                    row["id"]: Participant.model_validate_json(row["data"])
                    for row in cur.fetchall()
                }
                for p in participants.values():
                    p.status = ParticipantStatus.OFFLINE

                cur.execute(
                    "SELECT room_id, data FROM messages ORDER BY created_at ASC"
                )
                messages: dict[str, list[RoomMessage]] = {}
                for row in cur.fetchall():
                    msg = RoomMessage.model_validate_json(row["data"])
                    messages.setdefault(row["room_id"], []).append(msg)

                cur.execute("SELECT room_id, data FROM tasks")
                tasks: dict[str, list[Task]] = {}
                for row in cur.fetchall():
                    task = Task.model_validate_json(row["data"])
                    tasks.setdefault(row["room_id"], []).append(task)

                cur.execute("SELECT room_id, data FROM artifacts")
                artifacts: dict[str, list[Artifact]] = {}
                for row in cur.fetchall():
                    art = Artifact.model_validate_json(row["data"])
                    artifacts.setdefault(row["room_id"], []).append(art)

                cur.execute("SELECT name, data FROM agents")
                agents = {
                    row["name"]: RegisteredAgent.model_validate_json(row["data"])
                    for row in cur.fetchall()
                }

                cur.execute("SELECT room_id, data FROM bookmarks")
                bookmarks: dict[str, list[Bookmark]] = {}
                for row in cur.fetchall():
                    b = Bookmark.model_validate_json(row["data"])
                    bookmarks.setdefault(row["room_id"], []).append(b)

                cur.execute("SELECT room_id, data FROM forks")
                forks: dict[str, list[Fork]] = {}
                for row in cur.fetchall():
                    f = Fork.model_validate_json(row["data"])
                    forks.setdefault(row["room_id"], []).append(f)

                cur.execute("SELECT id, data FROM gateways")
                gateways = {
                    row["id"]: GatewayRecord.model_validate_json(row["data"])
                    for row in cur.fetchall()
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
