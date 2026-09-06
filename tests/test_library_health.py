from __future__ import annotations

import sqlite3
from pathlib import Path

from asset_catalogue import ingest, library_assets, library_health, thumbnails

from conftest import write_texture


def _ingest_and_prepare(conn, staging_folder, thumbnail_dir, assets_dir, pack_name="Pack"):
    write_texture(staging_folder, pack_name, "a.png")
    pack_id, _ = ingest.get_or_create_pack(conn, pack_name, pack_name, None, None, None)
    ingest.ingest_pack(conn, staging_folder / pack_name, pack_id)
    thumbnails.generate_texture_thumbnails(conn, staging_folder, thumbnail_dir)
    library_assets.archive_pack(conn, staging_folder, assets_dir, pack_id)
    asset_id = conn.execute("SELECT id FROM assets").fetchone()["id"]
    return pack_id, asset_id


def test_check_integrity_clean_library_has_no_issues(
    conn: sqlite3.Connection, staging_folder: Path, thumbnail_dir: Path, assets_dir: Path
) -> None:
    _ingest_and_prepare(conn, staging_folder, thumbnail_dir, assets_dir)
    report = library_health.check_integrity(conn, staging_folder, assets_dir, thumbnail_dir)
    assert report.checked_count == 1
    assert report.issues == []


def test_check_integrity_flags_missing_library_copy(
    conn: sqlite3.Connection, staging_folder: Path, thumbnail_dir: Path, assets_dir: Path
) -> None:
    _, asset_id = _ingest_and_prepare(conn, staging_folder, thumbnail_dir, assets_dir)
    (assets_dir / "Pack" / "a.png").unlink()

    report = library_health.check_integrity(conn, staging_folder, assets_dir, thumbnail_dir)
    types = [i.issue_type for i in report.issues if i.asset_id == asset_id]
    assert library_health.MISSING_LIBRARY_COPY in types


def test_check_integrity_with_no_staging_folder_skips_the_staging_check(
    conn: sqlite3.Connection, staging_folder: Path, thumbnail_dir: Path, assets_dir: Path
) -> None:
    """staging_folder=None is a documented, real case (no staging folder
    currently configured, or one not needed for this check) -- the
    staging-source check must be skipped cleanly, not raise trying to
    join None with a relative path, and must not report a false
    MISSING_STAGING_SOURCE for an asset whose staging copy is actually
    still there (unreachable) or has already been legitimately cleaned
    up (the documented common case this check treats as informational).
    """
    _ingest_and_prepare(conn, staging_folder, thumbnail_dir, assets_dir)

    report = library_health.check_integrity(conn, None, assets_dir, thumbnail_dir)

    assert report.checked_count == 1
    types = [i.issue_type for i in report.issues]
    assert library_health.MISSING_STAGING_SOURCE not in types


def test_check_integrity_flags_missing_thumbnail_file_when_status_says_done(
    conn: sqlite3.Connection, staging_folder: Path, thumbnail_dir: Path, assets_dir: Path
) -> None:
    _, asset_id = _ingest_and_prepare(conn, staging_folder, thumbnail_dir, assets_dir)
    content_hash = conn.execute(
        "SELECT content_hash FROM assets WHERE id = ?", (asset_id,)
    ).fetchone()["content_hash"]
    thumbnails.thumbnail_path(thumbnail_dir, content_hash).unlink()

    report = library_health.check_integrity(conn, staging_folder, assets_dir, thumbnail_dir)
    types = [i.issue_type for i in report.issues if i.asset_id == asset_id]
    assert library_health.MISSING_THUMBNAIL_FILE in types


def test_check_integrity_flags_missing_staging_source(
    conn: sqlite3.Connection, staging_folder: Path, thumbnail_dir: Path, assets_dir: Path
) -> None:
    _, asset_id = _ingest_and_prepare(conn, staging_folder, thumbnail_dir, assets_dir)
    (staging_folder / "Pack" / "a.png").unlink()

    report = library_health.check_integrity(conn, staging_folder, assets_dir, thumbnail_dir)
    types = [i.issue_type for i in report.issues if i.asset_id == asset_id]
    assert library_health.MISSING_STAGING_SOURCE in types
    # The library copy itself is still fine -- only staging is flagged.
    assert library_health.MISSING_LIBRARY_COPY not in types


def test_check_integrity_skips_trashed_assets(
    conn: sqlite3.Connection, staging_folder: Path, thumbnail_dir: Path, assets_dir: Path
) -> None:
    from asset_catalogue import removal

    _, asset_id = _ingest_and_prepare(conn, staging_folder, thumbnail_dir, assets_dir)
    (assets_dir / "Pack" / "a.png").unlink()
    removal.trash_assets(conn, [asset_id])

    report = library_health.check_integrity(conn, staging_folder, assets_dir, thumbnail_dir)
    assert report.checked_count == 0
    assert report.issues == []


def test_reset_broken_thumbnails(
    conn: sqlite3.Connection, staging_folder: Path, thumbnail_dir: Path, assets_dir: Path
) -> None:
    _, asset_id = _ingest_and_prepare(conn, staging_folder, thumbnail_dir, assets_dir)
    content_hash = conn.execute(
        "SELECT content_hash FROM assets WHERE id = ?", (asset_id,)
    ).fetchone()["content_hash"]
    thumbnails.thumbnail_path(thumbnail_dir, content_hash).unlink()

    count = library_health.reset_broken_thumbnails(conn, [asset_id])
    assert count == 1
    status = conn.execute(
        "SELECT thumbnail_status FROM assets WHERE id = ?", (asset_id,)
    ).fetchone()["thumbnail_status"]
    assert status == "pending"

    # Re-scanning no longer flags it -- 'pending' isn't checked for a file.
    report = library_health.check_integrity(conn, staging_folder, assets_dir, thumbnail_dir)
    assert not any(i.asset_id == asset_id for i in report.issues)


def test_rearchive_assets_restores_missing_library_copy(
    conn: sqlite3.Connection, staging_folder: Path, thumbnail_dir: Path, assets_dir: Path
) -> None:
    _, asset_id = _ingest_and_prepare(conn, staging_folder, thumbnail_dir, assets_dir)
    library_path = assets_dir / "Pack" / "a.png"
    library_path.unlink()
    assert not library_path.exists()

    count = library_health.rearchive_assets(conn, staging_folder, assets_dir, [asset_id])
    assert count == 1
    assert library_path.is_file()
