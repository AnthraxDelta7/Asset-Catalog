from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timezone
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


def set_notes_and_rating(
    conn: sqlite3.Connection, pack_id: int, notes: str | None, rating: int | None
) -> None:
    """A personal, freeform record of what a pack is actually good for --
    separate from creator/licence/source_url (facts about the pack) since
    notes/rating are the user's own opinion, most useful when digging back
    through a large purchased-pack backlog later. rating is 1-5 stars, or
    None for unrated -- 0 is treated the same as None (never stored as an
    explicit "zero stars").
    """
    conn.execute(
        "UPDATE packs SET notes = ?, rating = ? WHERE id = ?",
        (notes or None, rating or None, pack_id),
    )
    conn.commit()


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


def set_hidden(conn: sqlite3.Connection, pack_ids: list[int], hidden: bool) -> None:
    """Hides packs from the filter panel's list. Purely a listing
    concern: the assets stay catalogued, searchable and exportable, and
    an asset from a hidden pack still shows its pack name. Meant for the
    long tail of packs someone has finished with, not as a delete.
    """
    if not pack_ids:
        return
    placeholders = ",".join("?" for _ in pack_ids)
    conn.execute(
        f"UPDATE packs SET hidden = ? WHERE id IN ({placeholders})",
        [1 if hidden else 0, *pack_ids],
    )
    if not hidden:
        # Unhiding is someone saying "I want this one back", and the
        # filter panel only shows a handful of recents -- so without this
        # a pack could be unhidden and still not appear, which reads as
        # the button having done nothing. Touching it puts it at the top.
        conn.execute(
            f"UPDATE packs SET last_used_at = ? WHERE id IN ({placeholders})",
            [_now(), *pack_ids],
        )
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def touch(conn: sqlite3.Connection, pack_id: int) -> None:
    """Marks a pack as just-used, which is what the filter panel's recents
    list is ordered by. Cheap enough to call on every pack selection.
    """
    conn.execute("UPDATE packs SET last_used_at = ? WHERE id = ?", (_now(), pack_id))
    conn.commit()


def touch_by_name(conn: sqlite3.Connection, name: str) -> None:
    conn.execute("UPDATE packs SET last_used_at = ? WHERE name = ?", (_now(), name))
    conn.commit()


def list_recent(conn: sqlite3.Connection, limit: int) -> list[str]:
    """The most recently used pack names, newest first, hidden ones left out.

    NULL last_used_at (every pack from before the column existed) sorts
    last, so a library nobody has clicked through yet still lists
    sensibly -- newest ingest first -- rather than in arbitrary order.
    """
    rows = conn.execute(
        "SELECT name FROM packs WHERE hidden = 0 "
        "ORDER BY last_used_at IS NULL, last_used_at DESC, date_added DESC, name "
        "LIMIT ?",
        (limit,),
    ).fetchall()
    return [row["name"] for row in rows]
