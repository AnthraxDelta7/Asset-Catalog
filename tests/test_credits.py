from __future__ import annotations

import sqlite3
from pathlib import Path

from asset_catalogue import credits, exporting, ingest

from conftest import write_texture


def test_whole_catalogue_report_lists_every_pack_regardless_of_use(conn: sqlite3.Connection, staging_folder: Path) -> None:
    ingest.get_or_create_pack(conn, "Used Pack", "Used Pack", "Alice", "CC0", "https://example.com/used")
    ingest.get_or_create_pack(conn, "Unused Pack", "Unused Pack", None, None, None)

    report = credits.generate_report(conn)
    assert "Credits for the entire catalogue" in report
    assert "2 pack(s)" in report
    assert "Pack: Used Pack" in report
    assert "Creator: Alice" in report
    assert "Licence: CC0" in report
    assert "Source: https://example.com/used" in report
    assert "Pack: Unused Pack" in report
    assert "Creator: (not specified)" in report


def test_empty_catalogue_report(conn: sqlite3.Connection) -> None:
    report = credits.generate_report(conn)
    assert "No packs found." in report


def test_project_scoped_report_only_includes_exported_packs(
    conn: sqlite3.Connection, staging_folder: Path, tmp_path: Path
) -> None:
    write_texture(staging_folder, "Exported Pack", "a.png")
    exported_pack_id, _ = ingest.get_or_create_pack(
        conn, "Exported Pack", "Exported Pack", "Bob", "MIT", None
    )
    ingest.ingest_pack(conn, staging_folder / "Exported Pack", exported_pack_id)

    ingest.get_or_create_pack(conn, "Never Exported Pack", "Never Exported Pack", "Carol", "MIT", None)

    project_root = tmp_path / "project"
    project_root.mkdir()
    assets = exporting.select_assets(conn, pack="Exported Pack")
    exporting.export_assets(conn, staging_folder, project_root, str(project_root.resolve()), "exported_assets", assets)

    report = credits.generate_report(conn, project_root)
    assert "Credits for project:" in report
    assert "1 pack(s)" in report
    assert "Pack: Exported Pack" in report
    assert "Never Exported Pack" not in report


def test_project_scoped_report_resolves_relative_path_same_as_export(
    conn: sqlite3.Connection, staging_folder: Path, tmp_path: Path, monkeypatch
) -> None:
    write_texture(staging_folder, "Pack", "a.png")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, staging_folder / "Pack", pack_id)

    project_root = tmp_path / "project"
    project_root.mkdir()
    assets = exporting.select_assets(conn)
    # Mirrors exactly how catalogue.export_assets_bg resolves project_root
    # before recording it, so a caller passing the same relative-ish path
    # to generate_report finds the same records.
    resolved = str(Path(project_root).resolve())
    exporting.export_assets(conn, staging_folder, project_root, resolved, "exported_assets", assets)

    report = credits.generate_report(conn, project_root)
    assert "1 pack(s)" in report
