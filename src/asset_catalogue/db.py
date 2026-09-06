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
    corrections TEXT,
    notes TEXT,
    rating INTEGER
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
    thumbnail_status TEXT NOT NULL DEFAULT 'pending',
    favorite INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT,
    needs_glb_conversion INTEGER NOT NULL DEFAULT 0
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

CREATE TABLE IF NOT EXISTS exports (
    id INTEGER PRIMARY KEY,
    asset_id INTEGER NOT NULL REFERENCES assets(id),
    project_identifier TEXT NOT NULL,
    destination_path TEXT NOT NULL,
    timestamp TEXT NOT NULL
);

-- Tombstones a (asset, tag) pair the user explicitly untagged, so a later
-- `tag pack` cascade never silently re-applies it -- the schema otherwise
-- only tracks *how* a tag was applied (inherited/explicit), not "was
-- deliberately removed", so a re-run of the same pack-wide tag would
-- clobber the removal. A new CREATE TABLE (not a change to asset_tags'
-- schema/CHECK constraint) specifically so this needs no migration of
-- existing databases -- IF NOT EXISTS just adds it cleanly either way.
-- Explicitly re-tagging the asset (tag_asset) clears the tombstone, same
-- "explicit always wins" precedence already used elsewhere.
CREATE TABLE IF NOT EXISTS excluded_tags (
    asset_id INTEGER NOT NULL REFERENCES assets(id),
    tag_id INTEGER NOT NULL REFERENCES tags(id),
    PRIMARY KEY (asset_id, tag_id)
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

-- One row per (asset, material) still referencing a broken texture
-- reference as of that asset's last render -- what the "Missing Textures"
-- review dialog and the detail panel's Fix Texture button both read from.
-- Replaced wholesale for an asset on every render (see broken_textures.py's
-- replace_for_asset), not just added to -- self-correcting the same way
-- assets.needs_glb_conversion is, so a re-render that fixes something
-- clears it here without a separate cleanup step.
CREATE TABLE IF NOT EXISTS broken_texture_materials (
    asset_id INTEGER NOT NULL REFERENCES assets(id),
    material_name TEXT NOT NULL,
    PRIMARY KEY (asset_id, material_name)
);
"""


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """Adds a column to an existing table if it's not already there --
    ALTER TABLE ... ADD COLUMN is a cheap, non-destructive operation in
    SQLite (existing rows just get the column's default/NULL), so this is
    safe to call unconditionally on every connect() rather than needing a
    versioned migration system. A fresh database never hits this at all --
    it gets the column straight from SCHEMA above.
    """
    existing_columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing_columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    # One-time migration: "imports" was renamed to "exports" to match the
    # export-to-project feature's new name. ALTER TABLE ... RENAME TO is a
    # cheap metadata-only operation in SQLite -- no rows are touched, no
    # separate migration tooling needed. Guarded so it only ever runs once
    # per database, and never on a database created fresh (which already
    # gets "exports" straight from SCHEMA below).
    existing_tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if "imports" in existing_tables and "exports" not in existing_tables:
        conn.execute("ALTER TABLE imports RENAME TO exports")
        conn.commit()

    conn.executescript(SCHEMA)

    _ensure_column(conn, "packs", "notes", "notes TEXT")
    _ensure_column(conn, "packs", "rating", "rating INTEGER")
    _ensure_column(conn, "assets", "favorite", "favorite INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "assets", "deleted_at", "deleted_at TEXT")
    # Set/cleared by blender_render.generate_model_thumbnails whenever a
    # non-.glb model's last render needed smart texture matching to look
    # right (a broken reference relinked, or a bare material matched to a
    # texture by name) -- that fix lives only in the ephemeral render, not
    # in the asset's own file, until Convert to glTF bakes it in for real.
    # See conversion.py's _apply_successful_conversion, which clears this
    # back to 0 the moment a conversion actually happens.
    _ensure_column(conn, "assets", "needs_glb_conversion", "needs_glb_conversion INTEGER NOT NULL DEFAULT 0")
    conn.commit()

    return conn
