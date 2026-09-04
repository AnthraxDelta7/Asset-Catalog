from __future__ import annotations

import sqlite3


def get_or_create_tag(conn: sqlite3.Connection, name: str, category: str | None) -> int:
    row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
    if row is not None:
        return row["id"]
    cursor = conn.execute(
        "INSERT INTO tags (name, category) VALUES (?, ?)", (name, category)
    )
    conn.commit()
    return cursor.lastrowid


def tag_pack(conn: sqlite3.Connection, pack_id: int, tag_id: int) -> int:
    """Cascade a tag onto every asset currently in the pack.

    INSERT OR IGNORE skips any asset that already has a row for this tag,
    whether inherited from an earlier cascade or explicit from manual
    tagging -- so re-running this after new assets are ingested only
    backfills the new ones, and never overwrites manual work. Also skips
    any asset explicitly untagged from this tag before (see excluded_tags
    in db.py) -- otherwise re-running the cascade would silently clobber
    that removal, previously a known limitation.
    """
    asset_ids = conn.execute(
        "SELECT id FROM assets WHERE pack_id = ?", (pack_id,)
    ).fetchall()
    applied = 0
    for row in asset_ids:
        excluded = conn.execute(
            "SELECT 1 FROM excluded_tags WHERE asset_id = ? AND tag_id = ?",
            (row["id"], tag_id),
        ).fetchone()
        if excluded is not None:
            continue
        cursor = conn.execute(
            "INSERT OR IGNORE INTO asset_tags (asset_id, tag_id, source) "
            "VALUES (?, ?, 'inherited')",
            (row["id"], tag_id),
        )
        applied += cursor.rowcount
    conn.commit()
    return applied


def tag_asset(conn: sqlite3.Connection, asset_id: int, tag_id: int) -> None:
    """Explicitly tag one asset, upgrading any inherited row to explicit.

    Also clears any exclusion tombstone for this (asset, tag) pair --
    explicitly tagging is a deliberate override of a past removal, same
    "explicit always wins" precedence already used for inherited tags.
    """
    conn.execute(
        "DELETE FROM excluded_tags WHERE asset_id = ? AND tag_id = ?",
        (asset_id, tag_id),
    )
    conn.execute(
        "INSERT INTO asset_tags (asset_id, tag_id, source) VALUES (?, ?, 'explicit') "
        "ON CONFLICT (asset_id, tag_id) DO UPDATE SET source = 'explicit'",
        (asset_id, tag_id),
    )
    conn.commit()


def untag_asset(conn: sqlite3.Connection, asset_id: int, tag_id: int) -> bool:
    """Removes the tag from the asset, and tombstones the pair so a later
    `tag pack` cascade never silently re-applies it (see excluded_tags).
    """
    cursor = conn.execute(
        "DELETE FROM asset_tags WHERE asset_id = ? AND tag_id = ?",
        (asset_id, tag_id),
    )
    conn.execute(
        "INSERT OR IGNORE INTO excluded_tags (asset_id, tag_id) VALUES (?, ?)",
        (asset_id, tag_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def rename_tag(conn: sqlite3.Connection, tag_id: int, new_name: str, new_category: str | None) -> None:
    """Renames a tag and/or changes its category in place -- every asset
    currently carrying it (inherited or explicit) is unaffected other than
    seeing the new name, since asset_tags references tags by id, not name.
    """
    collision = conn.execute(
        "SELECT id FROM tags WHERE name = ? AND id != ?", (new_name, tag_id)
    ).fetchone()
    if collision is not None:
        raise ValueError(f"A tag named '{new_name}' already exists")
    conn.execute(
        "UPDATE tags SET name = ?, category = ? WHERE id = ?",
        (new_name, new_category or None, tag_id),
    )
    conn.commit()


def delete_tag(conn: sqlite3.Connection, tag_id: int) -> int:
    """Removes a tag from the vocabulary entirely -- every asset_tags row
    referencing it too (both inherited and explicit), not just one asset,
    plus any exclusion tombstones (meaningless once the tag itself is
    gone). Returns how many assets lost the tag.
    """
    cursor = conn.execute("DELETE FROM asset_tags WHERE tag_id = ?", (tag_id,))
    removed_from = cursor.rowcount
    conn.execute("DELETE FROM excluded_tags WHERE tag_id = ?", (tag_id,))
    conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
    conn.commit()
    return removed_from
