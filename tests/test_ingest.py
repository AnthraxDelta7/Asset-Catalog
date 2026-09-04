from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path

from asset_catalogue import ingest


def test_classify() -> None:
    assert ingest.classify(".fbx") == "model"
    assert ingest.classify(".PNG") == "texture"
    assert ingest.classify(".wav") == "audio"
    assert ingest.classify(".xyz") == "other"


def test_hash_file_is_stable_and_content_sensitive(tmp_path: Path) -> None:
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"hello world")
    b.write_bytes(b"hello world")
    c = tmp_path / "c.bin"
    c.write_bytes(b"different content")

    assert ingest.hash_file(a) == ingest.hash_file(b)
    assert ingest.hash_file(a) != ingest.hash_file(c)


def test_get_or_create_pack_inserts_then_only_updates_changed_fields(conn: sqlite3.Connection) -> None:
    pack_id, updated = ingest.get_or_create_pack(conn, "Pack", "Pack", "Creator A", "MIT", None)
    assert updated == []

    # Re-ingest with a new creator but no licence supplied (None) -- the
    # existing licence must survive untouched, only creator should update.
    pack_id2, updated2 = ingest.get_or_create_pack(conn, "Pack", "Pack", "Creator B", None, None)
    assert pack_id2 == pack_id
    assert updated2 == ["creator"]
    row = conn.execute("SELECT creator, licence FROM packs WHERE id = ?", (pack_id,)).fetchone()
    assert row["creator"] == "Creator B"
    assert row["licence"] == "MIT"


def test_ingest_pack_counts_new_and_duplicate(conn: sqlite3.Connection, staging_folder: Path) -> None:
    pack_root = staging_folder / "Pack"
    pack_root.mkdir()
    (pack_root / "a.png").write_bytes(b"data-a")
    (pack_root / "b.png").write_bytes(b"data-b")

    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    stats = ingest.ingest_pack(conn, pack_root, pack_id)
    assert stats.new == 2
    assert stats.duplicate == 0
    assert stats.total == 2

    # Re-ingesting the same files must count them as duplicates, not new.
    stats2 = ingest.ingest_pack(conn, pack_root, pack_id)
    assert stats2.new == 0
    assert stats2.duplicate == 2


def test_ingest_pack_skips_engine_project_files_and_folders(conn: sqlite3.Connection, staging_folder: Path) -> None:
    pack_root = staging_folder / "Pack"
    pack_root.mkdir()
    (pack_root / "real_asset.png").write_bytes(b"real")
    (pack_root / "meta_sidecar.meta").write_bytes(b"unity meta")
    (pack_root / "Library").mkdir()
    (pack_root / "Library" / "junk.bin").write_bytes(b"engine cache junk")

    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    stats = ingest.ingest_pack(conn, pack_root, pack_id)

    assert stats.new == 1
    assert stats.skipped_engine_files == 1
    assert stats.skipped_engine_folders == 1
    filenames = {row["filename"] for row in conn.execute("SELECT filename FROM assets")}
    assert filenames == {"real_asset.png"}


def test_ingest_pack_extracts_nested_zip(conn: sqlite3.Connection, staging_folder: Path) -> None:
    pack_root = staging_folder / "Pack"
    pack_root.mkdir()
    (pack_root / "top_level.png").write_bytes(b"top")

    nested_zip = pack_root / "Bonus.zip"
    with zipfile.ZipFile(nested_zip, "w") as zf:
        zf.writestr("bonus_texture.png", b"bonus data")

    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    stats = ingest.ingest_pack(conn, pack_root, pack_id)

    assert stats.new == 2
    assert stats.nested_zips_extracted == 1
    filenames = {row["filename"] for row in conn.execute("SELECT filename FROM assets")}
    assert filenames == {"top_level.png", "bonus_texture.png"}

    # Re-ingesting must not double-count the already-extracted nested folder.
    stats2 = ingest.ingest_pack(conn, pack_root, pack_id)
    assert stats2.total == 2
    assert stats2.duplicate == 2


def test_ingest_pack_reports_progress(conn: sqlite3.Connection, staging_folder: Path) -> None:
    pack_root = staging_folder / "Pack"
    pack_root.mkdir()
    (pack_root / "a.png").write_bytes(b"data")

    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    messages: list[str] = []
    ingest.ingest_pack(conn, pack_root, pack_id, on_progress=messages.append)
    assert any("Hashing a.png" in m for m in messages)
