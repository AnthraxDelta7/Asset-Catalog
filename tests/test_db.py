from __future__ import annotations

import sqlite3
from pathlib import Path

from asset_catalogue import db


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_connect_creates_expected_schema(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "new.db")
    tables = _table_names(conn)
    for expected in ("packs", "assets", "tags", "asset_tags", "exports", "excluded_tags", "pending_conversions"):
        assert expected in tables
    assert "imports" not in tables
    conn.close()


def test_connect_enables_foreign_keys(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "new.db")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()


def test_connect_migrates_legacy_imports_table_preserving_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    raw = sqlite3.connect(db_path)
    raw.executescript(
        """
        CREATE TABLE packs (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            pack_folder TEXT NOT NULL, creator TEXT, licence TEXT, source_url TEXT,
            date_added TEXT NOT NULL, corrections TEXT);
        CREATE TABLE assets (id INTEGER PRIMARY KEY, pack_id INTEGER NOT NULL,
            relative_path TEXT NOT NULL, filename TEXT NOT NULL, extension TEXT NOT NULL,
            file_size INTEGER NOT NULL, content_hash TEXT NOT NULL UNIQUE,
            asset_type TEXT NOT NULL, thumbnail_status TEXT NOT NULL DEFAULT 'pending');
        CREATE TABLE imports (id INTEGER PRIMARY KEY, asset_id INTEGER NOT NULL,
            project_identifier TEXT NOT NULL, destination_path TEXT NOT NULL, timestamp TEXT NOT NULL);
        INSERT INTO packs (id, name, pack_folder, date_added) VALUES (1, 'P', 'P', '2020-01-01');
        INSERT INTO assets (id, pack_id, relative_path, filename, extension, file_size, content_hash, asset_type)
            VALUES (1, 1, 'a.png', 'a.png', '.png', 10, 'hash1', 'texture');
        INSERT INTO imports (asset_id, project_identifier, destination_path, timestamp)
            VALUES (1, '/proj', '/proj/exported_assets/P/a.png', '2020-01-02T00:00:00');
        """
    )
    raw.commit()
    raw.close()

    conn = db.connect(db_path)
    tables = _table_names(conn)
    assert "exports" in tables
    assert "imports" not in tables
    row = conn.execute("SELECT * FROM exports").fetchone()
    assert row["project_identifier"] == "/proj"
    conn.close()

    # Re-opening (as a real app restart would) must be a safe no-op.
    conn2 = db.connect(db_path)
    assert conn2.execute("SELECT COUNT(*) FROM exports").fetchone()[0] == 1
    conn2.close()
