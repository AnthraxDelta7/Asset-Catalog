from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from asset_catalogue import packs

from conftest import make_pack


def test_set_metadata_overwrites_including_blank(conn: sqlite3.Connection, staging_folder: Path) -> None:
    pack_id = make_pack(conn, staging_folder)
    packs.set_metadata(conn, pack_id, "Creator", "MIT", "https://example.com")
    packs.set_metadata(conn, pack_id, "Creator", "", None)  # blank licence clears it
    row = conn.execute("SELECT creator, licence, source_url FROM packs WHERE id = ?", (pack_id,)).fetchone()
    assert row["creator"] == "Creator"
    assert row["licence"] is None
    assert row["source_url"] is None


def test_rename_pack_moves_archived_library_folder(conn: sqlite3.Connection, staging_folder: Path, assets_dir: Path) -> None:
    pack_id = make_pack(conn, staging_folder, "OldName")
    old_folder = assets_dir / "OldName"
    old_folder.mkdir(parents=True)
    (old_folder / "a.png").write_bytes(b"data")

    packs.rename_pack(conn, assets_dir, pack_id, "NewName")

    assert not old_folder.exists()
    assert (assets_dir / "NewName" / "a.png").is_file()
    row = conn.execute("SELECT name FROM packs WHERE id = ?", (pack_id,)).fetchone()
    assert row["name"] == "NewName"


def test_rename_pack_rejects_name_collision(conn: sqlite3.Connection, staging_folder: Path, assets_dir: Path) -> None:
    make_pack(conn, staging_folder, "Taken")
    pack_id = make_pack(conn, staging_folder, "Mine")
    with pytest.raises(ValueError):
        packs.rename_pack(conn, assets_dir, pack_id, "Taken")


def test_corrections_round_trip(conn: sqlite3.Connection, staging_folder: Path) -> None:
    pack_id = make_pack(conn, staging_folder)
    assert packs.get_corrections(conn, pack_id) == {}

    packs.set_corrections(conn, pack_id, {"up_axis": "Y_UP", "scale": 1.5})
    assert packs.get_corrections(conn, pack_id) == {"up_axis": "Y_UP", "scale": 1.5}

    packs.set_corrections(conn, pack_id, {})
    assert packs.get_corrections(conn, pack_id) == {}
