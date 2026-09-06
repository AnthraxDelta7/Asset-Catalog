from __future__ import annotations

import sqlite3
from pathlib import Path

from asset_catalogue import ingest, library_assets

from conftest import write_texture


def test_archive_asset_copies_file_once(conn: sqlite3.Connection, staging_folder: Path, assets_dir: Path) -> None:
    write_texture(staging_folder, "Pack", "a.png")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, staging_folder / "Pack", pack_id)
    asset_id = conn.execute("SELECT id FROM assets").fetchone()["id"]

    dest = library_assets.archive_asset(conn, staging_folder, assets_dir, asset_id)
    assert dest is not None
    assert dest.is_file()
    assert dest == assets_dir / "Pack" / "a.png"

    # Calling it again should be a cheap no-op returning the same path, not
    # re-copy or error.
    dest2 = library_assets.archive_asset(conn, staging_folder, assets_dir, asset_id)
    assert dest2 == dest


def test_archive_asset_none_for_nonexistent_asset_id(
    conn: sqlite3.Connection, staging_folder: Path, assets_dir: Path
) -> None:
    assert library_assets.archive_asset(conn, staging_folder, assets_dir, 999) is None


def test_archive_asset_none_when_staging_source_is_missing(
    conn: sqlite3.Connection, staging_folder: Path, assets_dir: Path
) -> None:
    write_texture(staging_folder, "Pack", "a.png")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, staging_folder / "Pack", pack_id)
    asset_id = conn.execute("SELECT id FROM assets").fetchone()["id"]

    (staging_folder / "Pack" / "a.png").unlink()

    assert library_assets.archive_asset(conn, staging_folder, assets_dir, asset_id) is None


def test_archive_asset_returns_none_if_source_missing(conn: sqlite3.Connection, staging_folder: Path, assets_dir: Path) -> None:
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    conn.execute(
        "INSERT INTO assets (pack_id, relative_path, filename, extension, file_size, "
        "content_hash, asset_type) VALUES (?, 'ghost.png', 'ghost.png', '.png', 1, 'h1', 'texture')",
        (pack_id,),
    )
    conn.commit()
    asset_id = conn.execute("SELECT id FROM assets").fetchone()["id"]

    assert library_assets.archive_asset(conn, staging_folder, assets_dir, asset_id) is None


def test_archive_pack_archives_every_asset_and_reports_progress(
    conn: sqlite3.Connection, staging_folder: Path, assets_dir: Path
) -> None:
    write_texture(staging_folder, "Pack", "a.png", color=(200, 50, 50))
    write_texture(staging_folder, "Pack", "b.png", color=(50, 200, 50))
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, staging_folder / "Pack", pack_id)

    messages: list[str] = []
    archived = library_assets.archive_pack(conn, staging_folder, assets_dir, pack_id, on_progress=messages.append)
    assert archived == 2
    assert (assets_dir / "Pack" / "a.png").is_file()
    assert (assets_dir / "Pack" / "b.png").is_file()
    assert any("Archiving" in m for m in messages)
