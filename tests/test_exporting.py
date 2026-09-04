from __future__ import annotations

import sqlite3
from pathlib import Path

from asset_catalogue import exporting, ingest

from conftest import write_texture


def test_select_assets_filters_by_asset_ids(conn: sqlite3.Connection, staging_folder: Path) -> None:
    write_texture(staging_folder, "Pack", "a.png", color=(1, 2, 3))
    write_texture(staging_folder, "Pack", "b.png", color=(4, 5, 6))
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, staging_folder / "Pack", pack_id)
    all_ids = [row["id"] for row in conn.execute("SELECT id FROM assets")]

    assert exporting.select_assets(conn, asset_ids=[]) == []
    one = exporting.select_assets(conn, asset_ids=[all_ids[0]])
    assert len(one) == 1
    assert one[0]["id"] == all_ids[0]


def test_export_assets_copies_files_preserving_pack_subfolder(
    conn: sqlite3.Connection, staging_folder: Path, tmp_path: Path
) -> None:
    write_texture(staging_folder, "My Pack", "a.png")
    pack_id, _ = ingest.get_or_create_pack(conn, "My Pack", "My Pack", None, None, None)
    ingest.ingest_pack(conn, staging_folder / "My Pack", pack_id)

    project_root = tmp_path / "project"
    project_root.mkdir()
    assets = exporting.select_assets(conn, asset_ids=[conn.execute("SELECT id FROM assets").fetchone()["id"]])

    messages: list[str] = []
    stats = exporting.export_assets(
        conn, staging_folder, project_root, str(project_root), "exported_assets", assets,
        on_progress=messages.append,
    )
    assert stats.copied == 1
    dest = project_root / "exported_assets" / "My Pack" / "a.png"
    assert dest.is_file()
    assert any("Exporting a.png" in m for m in messages)

    row = conn.execute("SELECT * FROM exports").fetchone()
    assert row["project_identifier"] == str(project_root)
    assert row["destination_path"] == str(dest)


def test_export_assets_sanitizes_pack_name_with_slashes(
    conn: sqlite3.Connection, staging_folder: Path, tmp_path: Path
) -> None:
    # pack_folder (the real on-disk staging subfolder) can't contain a
    # literal slash, but the pack's display `name` is a free-form DB field
    # that can -- _sanitize_folder_name is what keeps that from producing a
    # nested/broken export destination path.
    write_texture(staging_folder, "WeirdPackFolder", "a.png")
    pack_id, _ = ingest.get_or_create_pack(conn, "Weird/Pack", "WeirdPackFolder", None, None, None)
    ingest.ingest_pack(conn, staging_folder / "WeirdPackFolder", pack_id)
    project_root = tmp_path / "project"
    project_root.mkdir()
    assets = exporting.select_assets(conn)

    exporting.export_assets(conn, staging_folder, project_root, str(project_root), "exported_assets", assets)
    assert (project_root / "exported_assets" / "Weird_Pack" / "a.png").is_file()
