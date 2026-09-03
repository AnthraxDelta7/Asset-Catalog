from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS packs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    pack_folder TEXT NOT NULL,
    creator TEXT,
    licence TEXT,
    source_url TEXT,
    date_added TEXT NOT NULL,
    corrections TEXT
);

CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY,
    pack_id INTEGER NOT NULL REFERENCES packs(id),
    relative_path TEXT NOT NULL,
    filename TEXT NOT NULL,
    extension TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    content_hash TEXT NOT NULL UNIQUE,
    asset_type TEXT NOT NULL,
    thumbnail_status TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    category TEXT
);

CREATE TABLE IF NOT EXISTS asset_tags (
    asset_id INTEGER NOT NULL REFERENCES assets(id),
    tag_id INTEGER NOT NULL REFERENCES tags(id),
    source TEXT NOT NULL CHECK (source IN ('inherited', 'explicit')),
    PRIMARY KEY (asset_id, tag_id)
);

CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY,
    asset_id INTEGER NOT NULL REFERENCES assets(id),
    project_identifier TEXT NOT NULL,
    destination_path TEXT NOT NULL,
    timestamp TEXT NOT NULL
);

-- Tracks a model asset mid-conversion (e.g. to glTF): the pre-conversion
-- file's original identity, kept around so the conversion can be reverted
-- or, once confirmed good, cleaned up. See conversion.py.
CREATE TABLE IF NOT EXISTS pending_conversions (
    id INTEGER PRIMARY KEY,
    asset_id INTEGER NOT NULL UNIQUE REFERENCES assets(id),
    original_relative_path TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    original_extension TEXT NOT NULL,
    original_content_hash TEXT NOT NULL,
    original_file_size INTEGER NOT NULL,
    converted_at TEXT NOT NULL
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn
