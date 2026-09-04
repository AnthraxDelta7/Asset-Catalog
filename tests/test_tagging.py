from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from asset_catalogue import tagging

from conftest import make_pack


def _add_asset(conn: sqlite3.Connection, pack_id: int, filename: str, content_hash: str) -> int:
    cursor = conn.execute(
        "INSERT INTO assets (pack_id, relative_path, filename, extension, file_size, "
        "content_hash, asset_type) VALUES (?, ?, ?, '.png', 1, ?, 'texture')",
        (pack_id, filename, filename, content_hash),
    )
    conn.commit()
    return cursor.lastrowid


def test_get_or_create_tag_is_idempotent(conn: sqlite3.Connection) -> None:
    id1 = tagging.get_or_create_tag(conn, "weapons", "theme")
    id2 = tagging.get_or_create_tag(conn, "weapons", "theme")
    assert id1 == id2


def test_tag_pack_cascades_to_all_assets(conn: sqlite3.Connection, staging_folder: Path) -> None:
    pack_id = make_pack(conn, staging_folder)
    a1 = _add_asset(conn, pack_id, "a.png", "h1")
    a2 = _add_asset(conn, pack_id, "b.png", "h2")
    tag_id = tagging.get_or_create_tag(conn, "weapons", None)

    applied = tagging.tag_pack(conn, pack_id, tag_id)
    assert applied == 2
    tagged = {row["asset_id"] for row in conn.execute("SELECT asset_id FROM asset_tags")}
    assert tagged == {a1, a2}


def test_untag_asset_survives_a_later_pack_cascade(conn: sqlite3.Connection, staging_folder: Path) -> None:
    pack_id = make_pack(conn, staging_folder)
    a1 = _add_asset(conn, pack_id, "a.png", "h1")
    a2 = _add_asset(conn, pack_id, "b.png", "h2")
    tag_id = tagging.get_or_create_tag(conn, "weapons", None)

    tagging.tag_pack(conn, pack_id, tag_id)
    assert tagging.untag_asset(conn, a1, tag_id) is True

    # Re-running the exact same cascade must NOT silently re-apply the tag
    # to the asset that was deliberately untagged (this was a real bug).
    tagging.tag_pack(conn, pack_id, tag_id)
    tagged = {row["asset_id"] for row in conn.execute("SELECT asset_id FROM asset_tags")}
    assert tagged == {a2}

    # Explicitly re-tagging the asset is a deliberate override -- it must
    # clear the exclusion so a later cascade would apply again if untagged.
    tagging.tag_asset(conn, a1, tag_id)
    tagged_after = {row["asset_id"] for row in conn.execute("SELECT asset_id FROM asset_tags")}
    assert tagged_after == {a1, a2}
    assert conn.execute(
        "SELECT 1 FROM excluded_tags WHERE asset_id = ? AND tag_id = ?", (a1, tag_id)
    ).fetchone() is None


def test_rename_tag_rejects_name_collision(conn: sqlite3.Connection) -> None:
    tagging.get_or_create_tag(conn, "weapons", None)
    other_id = tagging.get_or_create_tag(conn, "armor", None)
    with pytest.raises(ValueError):
        tagging.rename_tag(conn, other_id, "weapons", None)


def test_delete_tag_removes_usages_and_exclusions(conn: sqlite3.Connection, staging_folder: Path) -> None:
    pack_id = make_pack(conn, staging_folder)
    a1 = _add_asset(conn, pack_id, "a.png", "h1")
    tag_id = tagging.get_or_create_tag(conn, "weapons", None)
    tagging.tag_asset(conn, a1, tag_id)
    tagging.untag_asset(conn, a1, tag_id)  # leaves a tombstone too

    removed = tagging.delete_tag(conn, tag_id)
    assert removed == 0  # already untagged by this point
    assert conn.execute("SELECT 1 FROM tags WHERE id = ?", (tag_id,)).fetchone() is None
    assert conn.execute("SELECT 1 FROM excluded_tags WHERE tag_id = ?", (tag_id,)).fetchone() is None
