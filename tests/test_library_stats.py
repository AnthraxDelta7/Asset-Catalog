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
