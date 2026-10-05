"""Fresh SQLite hubs stay readable across restart and a damaged file."""

from __future__ import annotations

import json

from opengateway.models import Room, RoomStatus
from opengateway.persistence import SqlitePersistence
from opengateway.store import Store


def test_new_database_uses_wal(tmp_path):
    db = SqlitePersistence(tmp_path / "state.db")
    mode = db._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal"
    db.close()


def test_unknown_room_status_does_not_block_startup(tmp_path):
    path = tmp_path / "state.db"
    db = SqlitePersistence(path)
    room = Room(name="General", status=RoomStatus.OPEN, created_by="system")
    raw = json.loads(room.model_dump_json())
    raw["status"] = "online"
    db._conn.execute(
        "INSERT INTO rooms (id, data) VALUES (?, ?)",
        (room.id, json.dumps(raw)),
    )
    db._conn.commit()
    db.close()

    reloaded = SqlitePersistence(path)
    loaded = reloaded.load_all()["rooms"][room.id]
    assert loaded.status == RoomStatus.OPEN
    reloaded.close()


def test_damaged_database_is_replaced(tmp_path):
    path = tmp_path / "state.db"
    path.write_bytes(b"this is not a sqlite database")

    store = Store(db_path=path)
    assert any(room.name == "General" for room in store.rooms.values())
    broken = list(tmp_path.glob("state.db.broken-*"))
    assert len(broken) == 1
    assert broken[0].read_bytes().startswith(b"this is not")
