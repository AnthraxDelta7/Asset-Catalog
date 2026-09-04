from __future__ import annotations

import sqlite3
from pathlib import Path

from asset_catalogue import ingest, library_assets, removal, thumbnails

from conftest import write_texture


def _ingest_and_prepare(conn, staging_folder, thumbnail_dir, assets_dir, pack_name="Pack"):
    write_texture(staging_folder, pack_name, "a.png")
    pack_id, _ = ingest.get_or_create_pack(conn, pack_name, pack_name, None, None, None)
    ingest.ingest_pack(conn, staging_folder / pack_name, pack_id)
    thumbnails.generate_texture_thumbnails(conn, staging_folder, thumbnail_dir)
    library_assets.archive_pack(conn, staging_folder, assets_dir, pack_id)
    asset_id = conn.execute("SELECT id FROM assets").fetchone()["id"]
    return pack_id, asset_id


def test_remove_assets_deletes_thumbnail_and_library_copy_not_staging(
    conn: sqlite3.Connection, staging_folder: Path, thumbnail_dir: Path, assets_dir: Path
) -> None:
    pack_id, asset_id = _ingest_and_prepare(conn, staging_folder, thumbnail_dir, assets_dir)
    content_hash = conn.execute("SELECT content_hash FROM assets WHERE id = ?", (asset_id,)).fetchone()["content_hash"]
    thumb_path = thumbnails.thumbnail_path(thumbnail_dir, content_hash)
    library_path = assets_dir / "Pack" / "a.png"
    staging_path = staging_folder / "Pack" / "a.png"
    assert thumb_path.is_file() and library_path.is_file() and staging_path.is_file()

    stats = removal.remove_assets(conn, thumbnail_dir, assets_dir, [asset_id])
    assert stats.removed == 1
    assert not thumb_path.exists()
    assert not library_path.exists()
    assert staging_path.exists(), "staging copy must never be touched by removal"
    assert conn.execute("SELECT 1 FROM assets WHERE id = ?", (asset_id,)).fetchone() is None


def test_remove_assets_clears_tags_and_exports(conn: sqlite3.Connection, staging_folder: Path, thumbnail_dir: Path, assets_dir: Path) -> None:
    from asset_catalogue import tagging

    pack_id, asset_id = _ingest_and_prepare(conn, staging_folder, thumbnail_dir, assets_dir)
    tag_id = tagging.get_or_create_tag(conn, "weapons", None)
    tagging.tag_asset(conn, asset_id, tag_id)
    conn.execute(
        "INSERT INTO exports (asset_id, project_identifier, destination_path, timestamp) "
        "VALUES (?, '/proj', '/proj/a.png', '2020-01-01T00:00:00')",
        (asset_id,),
    )
    conn.commit()

    removal.remove_assets(conn, thumbnail_dir, assets_dir, [asset_id])
    assert conn.execute("SELECT 1 FROM asset_tags WHERE asset_id = ?", (asset_id,)).fetchone() is None
    assert conn.execute("SELECT 1 FROM exports WHERE asset_id = ?", (asset_id,)).fetchone() is None


def test_remove_pack_deletes_pack_row_and_whole_library_folder(
    conn: sqlite3.Connection, staging_folder: Path, thumbnail_dir: Path, assets_dir: Path
) -> None:
    pack_id, asset_id = _ingest_and_prepare(conn, staging_folder, thumbnail_dir, assets_dir)
    stats = removal.remove_pack(conn, thumbnail_dir, assets_dir, pack_id)
    assert stats.pack_removed is True
    assert stats.removed_assets == 1
    assert conn.execute("SELECT 1 FROM packs WHERE id = ?", (pack_id,)).fetchone() is None
    assert not (assets_dir / "Pack").exists()
