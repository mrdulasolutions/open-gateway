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
DATABASE_URL_ENV = "OPENGATEWAY_DATABASE_URL"


def default_db_path() -> Optional[Path | str]:
    """Resolve DB path or Postgres URL from env.

    - ``OPENGATEWAY_DATABASE_URL=postgresql://…`` → multi-writer Postgres
    - ``OPENGATEWAY_DB=postgresql://…`` → same
    - ``OPENGATEWAY_DB=/path/state.db`` → SQLite file
    - empty → ``~/.opengateway/state.db``
    - ``none`` / ``:memory:`` → no disk
    """
    url = os.environ.get(DATABASE_URL_ENV, "").strip()
    if url:
        return url
    raw = os.environ.get(DEFAULT_DB_ENV, "").strip()
    if raw.lower() in {"none", "off", "false", "0", ":memory:"}:
        return None
    if raw:
        if raw.startswith("postgres://") or raw.startswith("postgresql://"):
            # normalize postgres:// → postgresql:// for psycopg
            if raw.startswith("postgres://"):
                raw = "postgresql://" + raw[len("postgres://") :]
            return raw
        return Path(raw).expanduser()
    return Path.home() / ".opengateway" / "state.db"


def is_postgres_url(spec: Any) -> bool:
    s = str(spec or "")
    return s.startswith("postgresql://") or s.startswith("postgres://")


def open_persistence(spec: Path | str) -> Any:
    """Factory: SQLite file path or Postgres URL."""
    if is_postgres_url(spec):
        from opengateway.postgres_persistence import PostgresPersistence

        url = str(spec)
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://") :]
        return PostgresPersistence(url)
    return SqlitePersistence(Path(spec))


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
                CREATE TABLE IF NOT EXISTS tenants (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    tenant_id TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS invites (
                    code TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
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

    def save_tenant(self, rec: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO tenants (id, data) VALUES (?, ?)",
                (rec["id"], json.dumps(rec)),
            )
            self._conn.commit()

    def list_tenants(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT data FROM tenants").fetchall()
        return [json.loads(r["data"]) for r in rows]

    def get_tenant(self, tenant_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM tenants WHERE id = ?", (tenant_id,)
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def save_user(self, rec: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO users (id, email, tenant_id, data) VALUES (?, ?, ?, ?)",
                (rec["id"], rec["email"].lower(), rec["tenant_id"], json.dumps(rec)),
            )
            self._conn.commit()

    def get_user_by_email(self, email: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM users WHERE email = ?", (email.lower(),)
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def get_user(self, user_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def count_users(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
        return int(row["c"] if row else 0)

    def list_users(self, tenant_id: Optional[str] = None) -> list[dict[str, Any]]:
        with self._lock:
            if tenant_id:
                rows = self._conn.execute(
                    "SELECT data FROM users WHERE tenant_id = ?", (tenant_id,)
                ).fetchall()
            else:
                rows = self._conn.execute("SELECT data FROM users").fetchall()
        return [json.loads(r["data"]) for r in rows]

    def save_session(self, rec: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO sessions (token_hash, user_id, data) VALUES (?, ?, ?)",
                (rec["token_hash"], rec["user_id"], json.dumps(rec)),
            )
            self._conn.commit()

    def get_session(self, token_hash: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM sessions WHERE token_hash = ?", (token_hash,)
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def delete_session(self, token_hash: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM sessions WHERE token_hash = ?", (token_hash,)
            )
            self._conn.commit()

    def save_invite(self, rec: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO invites (code, tenant_id, data) VALUES (?, ?, ?)",
                (rec["code"], rec["tenant_id"], json.dumps(rec)),
            )
            self._conn.commit()

    def get_invite(self, code: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM invites WHERE code = ?", (code.upper(),)
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def get_meta(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (key, value),
            )
            self._conn.commit()

    def save_api_key(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO api_keys (id, key_hash, data) VALUES (?, ?, ?)",
                (record["id"], record["key_hash"], json.dumps(record)),
            )
            self._conn.commit()

    def delete_api_key(self, key_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def get_api_key_by_hash(self, key_hash: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM api_keys WHERE key_hash = ?", (key_hash,)
            ).fetchone()
        if not row:
            return None
        return json.loads(row["data"])

    def list_api_keys(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT data FROM api_keys").fetchall()
        return [json.loads(r["data"]) for r in rows]

    def save_push_sub(self, sub: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO push_subs (id, endpoint, data) VALUES (?, ?, ?)",
                (sub["id"], sub["endpoint"], json.dumps(sub)),
            )
            self._conn.commit()

    def delete_push_sub(self, sub_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM push_subs WHERE id = ?", (sub_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def delete_push_sub_by_endpoint(self, endpoint: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM push_subs WHERE endpoint = ?", (endpoint,)
            )
            self._conn.commit()
            return cur.rowcount > 0

    def list_push_subs(
        self, participant_id: Optional[str] = None
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT data FROM push_subs").fetchall()
        items = [json.loads(r["data"]) for r in rows]
        if participant_id:
            items = [s for s in items if s.get("participant_id") == participant_id]
        return items

    def append_audit(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Persist one audit row; returns entry with id + created_at."""
        from opengateway.models import utcnow

        created = entry.get("created_at") or utcnow().isoformat()
        detail = entry.get("detail") or {}
        if not isinstance(detail, str):
            detail = json.dumps(detail)
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO audit_log
                  (created_at, action, actor, room_id, resource_type, resource_id, outcome, ip, detail)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            self._conn.commit()
            row_id = cur.lastrowid
        out = dict(entry)
        out["id"] = row_id
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
            clauses.append("action = ?")
            params.append(action)
        if room_id:
            clauses.append("room_id = ?")
            params.append(room_id)
        if since_id is not None:
            clauses.append("id > ?")
            params.append(int(since_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT id, created_at, action, actor, room_id, resource_type, "
            f"resource_id, outcome, ip, detail FROM audit_log {where} "
            f"ORDER BY id DESC LIMIT ?"
        )
        params.append(limit)
        rows: list[dict[str, Any]] = []
        with self._lock:
            for row in self._conn.execute(sql, params):
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
