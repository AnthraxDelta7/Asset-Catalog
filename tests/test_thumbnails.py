from __future__ import annotations

import sqlite3
from pathlib import Path

from asset_catalogue import ingest, thumbnails

from conftest import write_texture


def test_generate_texture_thumbnails_renders_and_updates_status(
    conn: sqlite3.Connection, staging_folder: Path, thumbnail_dir: Path
) -> None:
    write_texture(staging_folder, "Pack", "a.png")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, staging_folder / "Pack", pack_id)

    stats = thumbnails.generate_texture_thumbnails(conn, staging_folder, thumbnail_dir)
    assert stats.generated == 1
    assert stats.already_done == 0
    content_hash = conn.execute("SELECT content_hash FROM assets").fetchone()["content_hash"]
    assert thumbnails.thumbnail_path(thumbnail_dir, content_hash).is_file()
    assert conn.execute("SELECT thumbnail_status FROM assets").fetchone()["thumbnail_status"] == "done"

    # A 'done' asset is excluded from the query entirely on a later
    # non-force run -- nothing left to do, not even counted.
    stats2 = thumbnails.generate_texture_thumbnails(conn, staging_folder, thumbnail_dir)
    assert stats2.generated == 0
    assert stats2.already_done == 0


def test_generate_texture_thumbnails_already_done_when_file_exists_but_status_is_not(
    conn: sqlite3.Connection, staging_folder: Path, thumbnail_dir: Path
) -> None:
    """Covers the recovery case: a thumbnail file already exists on disk
    for an asset whose status isn't 'done' yet (e.g. a crash between the
    file write and the DB commit on an earlier run) -- it should be
    recognized and marked done without being re-rendered.
    """
    write_texture(staging_folder, "Pack", "a.png")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, staging_folder / "Pack", pack_id)
    content_hash = conn.execute("SELECT content_hash FROM assets").fetchone()["content_hash"]

    dest = thumbnails.thumbnail_path(thumbnail_dir, content_hash)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"pretend this is already a rendered thumbnail")

    stats = thumbnails.generate_texture_thumbnails(conn, staging_folder, thumbnail_dir)
    assert stats.already_done == 1
    assert stats.generated == 0
    assert conn.execute("SELECT thumbnail_status FROM assets").fetchone()["thumbnail_status"] == "done"


def test_generate_texture_thumbnails_marks_corrupt_file_failed(
    conn: sqlite3.Connection, staging_folder: Path, thumbnail_dir: Path
) -> None:
    pack_root = staging_folder / "Pack"
    pack_root.mkdir()
    bad_file = pack_root / "corrupt.png"
    bad_file.write_bytes(b"not a real png")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, pack_root, pack_id)

    stats = thumbnails.generate_texture_thumbnails(conn, staging_folder, thumbnail_dir)
    assert stats.failed == 1
    assert conn.execute("SELECT thumbnail_status FROM assets").fetchone()["thumbnail_status"] == "failed"


def test_generate_texture_thumbnails_force_rerenders_even_if_already_done(
    conn: sqlite3.Connection, staging_folder: Path, thumbnail_dir: Path
) -> None:
    write_texture(staging_folder, "Pack", "a.png")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, staging_folder / "Pack", pack_id)

    thumbnails.generate_texture_thumbnails(conn, staging_folder, thumbnail_dir)
    stats = thumbnails.generate_texture_thumbnails(conn, staging_folder, thumbnail_dir, force=True)
    assert stats.generated == 1
    assert stats.already_done == 0
