"""Durable data directory covers SQLite and file blobs on every deploy path."""

from __future__ import annotations

from opengateway.persistence import data_dir, default_db_path, files_dir


def test_local_default_is_home_opengateway(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENGATEWAY_DATA_DIR", raising=False)
    monkeypatch.delenv("OPENGATEWAY_DB", raising=False)
    monkeypatch.delenv("OPENGATEWAY_DATABASE_URL", raising=False)
    monkeypatch.delenv("OPENGATEWAY_FILES_DIR", raising=False)
    monkeypatch.setattr("opengateway.persistence.Path.home", lambda: tmp_path)

    assert data_dir() == tmp_path / ".opengateway"
    assert default_db_path() == tmp_path / ".opengateway" / "state.db"
    assert files_dir() == tmp_path / ".opengateway" / "files"
    assert (tmp_path / ".opengateway" / "files").is_dir()


def test_data_dir_env_holds_sqlite_and_files(monkeypatch, tmp_path):
    root = tmp_path / "data"
    monkeypatch.setenv("OPENGATEWAY_DATA_DIR", str(root))
    monkeypatch.delenv("OPENGATEWAY_DB", raising=False)
    monkeypatch.delenv("OPENGATEWAY_DATABASE_URL", raising=False)
    monkeypatch.delenv("OPENGATEWAY_FILES_DIR", raising=False)

    assert data_dir() == root
    assert default_db_path() == root / "state.db"
    assert files_dir() == root / "files"


def test_sqlite_path_parent_is_data_dir_when_unset(monkeypatch, tmp_path):
    db = tmp_path / "vol" / "state.db"
    monkeypatch.delenv("OPENGATEWAY_DATA_DIR", raising=False)
    monkeypatch.delenv("OPENGATEWAY_DATABASE_URL", raising=False)
    monkeypatch.delenv("OPENGATEWAY_FILES_DIR", raising=False)
    monkeypatch.setenv("OPENGATEWAY_DB", str(db))

    assert default_db_path() == db
    assert data_dir() == tmp_path / "vol"
    assert files_dir(create=False) == tmp_path / "vol" / "files"


def test_postgres_keeps_files_on_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENGATEWAY_DATABASE_URL", "postgresql://hub/db")
    monkeypatch.setenv("OPENGATEWAY_DB", "none")
    monkeypatch.setenv("OPENGATEWAY_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OPENGATEWAY_FILES_DIR", raising=False)

    assert default_db_path() == "postgresql://hub/db"
    assert files_dir() == tmp_path / "files"
