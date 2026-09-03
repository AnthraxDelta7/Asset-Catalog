from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

from asset_catalogue import library_assets


def set_metadata(
    conn: sqlite3.Connection,
    pack_id: int,
    creator: str | None,
    licence: str | None,
    source_url: str | None,
) -> None:
    """Overwrites creator/licence/source_url outright (unlike ingest's
    only-if-changed delta merge) -- this is a deliberate edit form the user
    filled out with full current values, not an idempotent re-ingest, so a
    blank field here means "clear it", not "leave alone".
    """
    conn.execute(
        "UPDATE packs SET creator = ?, licence = ?, source_url = ? WHERE id = ?",
        (creator or None, licence or None, source_url or None, pack_id),
    )
    conn.commit()


def rename_pack(conn: sqlite3.Connection, assets_dir: Path, pack_id: int, new_name: str) -> None:
    """Renames a pack and, if it has an archived library folder, moves it to
    match -- otherwise "Show in Library Folder" and future archiving would
    silently start writing to a folder named after the *old* name while the
    already-archived copies stay orphaned under it.
    """
    row = conn.execute("SELECT name FROM packs WHERE id = ?", (pack_id,)).fetchone()
    if row is None:
        raise ValueError("Pack not found")
    old_name = row["name"]
    if new_name == old_name:
        return

    collision = conn.execute(
        "SELECT id FROM packs WHERE name = ? AND id != ?", (new_name, pack_id)
    ).fetchone()
    if collision is not None:
        raise ValueError(f"A pack named '{new_name}' already exists")

    conn.execute("UPDATE packs SET name = ? WHERE id = ?", (new_name, pack_id))
    conn.commit()

    old_folder = library_assets.pack_library_folder(assets_dir, old_name)
    new_folder = library_assets.pack_library_folder(assets_dir, new_name)
    if old_folder.exists() and not new_folder.exists():
        new_folder.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old_folder), str(new_folder))


def get_corrections(conn: sqlite3.Connection, pack_id: int) -> dict:
    row = conn.execute("SELECT corrections FROM packs WHERE id = ?", (pack_id,)).fetchone()
    if row is None or not row["corrections"]:
        return {}
    return json.loads(row["corrections"])


def set_corrections(conn: sqlite3.Connection, pack_id: int, corrections: dict) -> None:
    conn.execute(
        "UPDATE packs SET corrections = ? WHERE id = ?",
        (json.dumps(corrections) if corrections else None, pack_id),
    )
    conn.commit()
