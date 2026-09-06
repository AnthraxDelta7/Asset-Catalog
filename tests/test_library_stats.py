from __future__ import annotations

import sqlite3
from pathlib import Path

from asset_catalogue import ingest, library_stats

from conftest import write_texture


def test_compute_stats_totals_and_breakdowns(
    conn: sqlite3.Connection, staging_folder: Path
) -> None:
    write_texture(staging_folder, "PackA", "a.png", color=(1, 2, 3))
    write_texture(staging_folder, "PackA", "b.png", color=(4, 5, 6))
    write_texture(staging_folder, "PackB", "c.png", color=(7, 8, 9))
    pack_a_id, _ = ingest.get_or_create_pack(conn, "PackA", "PackA", None, None, None)
    ingest.ingest_pack(conn, staging_folder / "PackA", pack_a_id)
    pack_b_id, _ = ingest.get_or_create_pack(conn, "PackB", "PackB", None, None, None)
    ingest.ingest_pack(conn, staging_folder / "PackB", pack_b_id)

    stats = library_stats.compute_stats(conn)
    assert stats.total_assets == 3
    assert stats.pack_count == 2
    assert stats.by_asset_type == {"texture": 3}
    assert stats.by_thumbnail_status == {"pending": 3}
    assert stats.favorite_count == 0
    assert stats.total_size_bytes > 0

    pack_names = [p.pack_name for p in stats.largest_packs]
    assert set(pack_names) == {"PackA", "PackB"}
    pack_a = next(p for p in stats.largest_packs if p.pack_name == "PackA")
    assert pack_a.asset_count == 2


def test_compute_stats_counts_favorites(conn: sqlite3.Connection, staging_folder: Path) -> None:
    write_texture(staging_folder, "Pack", "a.png", color=(1, 2, 3))
    write_texture(staging_folder, "Pack", "b.png", color=(4, 5, 6))
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, staging_folder / "Pack", pack_id)

    asset_id = conn.execute("SELECT id FROM assets LIMIT 1").fetchone()["id"]
    conn.execute("UPDATE assets SET favorite = 1 WHERE id = ?", (asset_id,))
    conn.commit()

    stats = library_stats.compute_stats(conn)
    assert stats.favorite_count == 1


def test_compute_stats_empty_library(conn: sqlite3.Connection) -> None:
    stats = library_stats.compute_stats(conn)
    assert stats.total_assets == 0
    assert stats.total_size_bytes == 0
    assert stats.pack_count == 0
    assert stats.largest_packs == []


def test_format_bytes_boundary_values() -> None:
    assert library_stats.format_bytes(0) == "0 B"
    assert library_stats.format_bytes(1023) == "1023 B"
    assert library_stats.format_bytes(1024) == "1.0 KB"
    assert library_stats.format_bytes(1024 * 1024 - 1) == "1024.0 KB"
    assert library_stats.format_bytes(1024 * 1024) == "1.0 MB"
    assert library_stats.format_bytes(1024**3) == "1.0 GB"
    # GB is the last unit -- no TB step, so even a huge value stays in GB
    # rather than continuing to divide (format_bytes' own "or unit == GB"
    # short-circuit).
    assert library_stats.format_bytes(2048 * 1024**3) == "2048.0 GB"


def test_format_report_renders_all_sections() -> None:
    stats = library_stats.LibraryStats(
        total_assets=3,
        total_size_bytes=2048,
        pack_count=2,
        favorite_count=1,
        by_asset_type={"model": 2, "texture": 1},
        by_thumbnail_status={"done": 3},
        largest_packs=[library_stats.PackSize("PackA", 2, 2048)],
    )

    report = library_stats.format_report(stats)

    assert "3 asset(s) in 2 pack(s), 2.0 KB total" in report
    assert "1 favorited" in report
    assert "model: 2" in report
    assert "texture: 1" in report
    assert "done: 3" in report
    assert "PackA: 2.0 KB (2 asset(s))" in report


def test_format_report_omits_largest_packs_section_when_empty() -> None:
    stats = library_stats.LibraryStats(
        total_assets=0, total_size_bytes=0, pack_count=0, favorite_count=0
    )

    report = library_stats.format_report(stats)

    assert "Largest packs:" not in report
