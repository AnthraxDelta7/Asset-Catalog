from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from asset_catalogue import library_assets, thumbnails

MISSING_LIBRARY_COPY = "missing_library_copy"
MISSING_THUMBNAIL_FILE = "missing_thumbnail_file"
MISSING_STAGING_SOURCE = "missing_staging_source"


@dataclass
class HealthIssue:
    asset_id: int
    pack_name: str
    filename: str
    issue_type: str
    detail: str


@dataclass
class HealthReport:
    checked_count: int
    issues: list[HealthIssue] = field(default_factory=list)


def check_integrity(
    conn: sqlite3.Connection,
    staging_folder: Path | None,
    assets_dir: Path,
    thumbnail_dir: Path,
) -> HealthReport:
    """Read-only scan for the ways the catalogue's records can drift from
    reality on disk: an archived library copy that went missing (deleted
    outside the app, a failed copy, a moved drive), or a thumbnail file
    gone despite thumbnail_status saying 'done' -- same kind of drift, just
    a different file. Trashed assets are skipped: their files not being
    touched is the whole point of Trash, not something to flag. Staging
    source is checked too, but only as an informational note (see
    MISSING_STAGING_SOURCE) since many people clean up staging after
    ingest on purpose -- once archived, an asset doesn't need its staging
    copy for anything except a future re-render/re-convert.
    """
    rows = conn.execute(
        "SELECT assets.id, assets.filename, assets.relative_path, assets.content_hash, "
        "assets.thumbnail_status, packs.name AS pack_name, packs.pack_folder "
        "FROM assets JOIN packs ON packs.id = assets.pack_id "
        "WHERE assets.deleted_at IS NULL"
    ).fetchall()

    issues: list[HealthIssue] = []
    for row in rows:
        library_path = library_assets.asset_library_path(
            assets_dir, row["pack_name"], row["relative_path"]
        )
        if not library_path.exists():
            issues.append(
                HealthIssue(
                    row["id"], row["pack_name"], row["filename"], MISSING_LIBRARY_COPY,
                    "Archived library copy is missing.",
                )
            )

        if row["thumbnail_status"] == "done":
            thumb_path = thumbnails.thumbnail_path(thumbnail_dir, row["content_hash"])
            if not thumb_path.exists():
                issues.append(
                    HealthIssue(
                        row["id"], row["pack_name"], row["filename"], MISSING_THUMBNAIL_FILE,
                        "Marked as thumbnailed, but the thumbnail file is missing.",
                    )
                )

        if staging_folder is not None:
            source = staging_folder / row["pack_folder"] / row["relative_path"]
            if not source.exists():
                issues.append(
                    HealthIssue(
                        row["id"], row["pack_name"], row["filename"], MISSING_STAGING_SOURCE,
                        "Staging source is gone (fine if already archived; blocks a future "
                        "re-render or re-convert).",
                    )
                )

    return HealthReport(checked_count=len(rows), issues=issues)


def reset_broken_thumbnails(conn: sqlite3.Connection, asset_ids: list[int]) -> int:
    """The one-click fix for MISSING_THUMBNAIL_FILE -- resets
    thumbnail_status back to 'pending' so the next thumbnail generation
    pass (automatic or manual) naturally re-renders it, rather than
    leaving it stuck claiming 'done' for a file that doesn't exist.
    """
    count = 0
    for asset_id in asset_ids:
        cursor = conn.execute(
            "UPDATE assets SET thumbnail_status = 'pending' WHERE id = ? AND thumbnail_status = 'done'",
            (asset_id,),
        )
        count += cursor.rowcount
    conn.commit()
    return count


def rearchive_assets(
    conn: sqlite3.Connection, staging_folder: Path, assets_dir: Path, asset_ids: list[int]
) -> int:
    """The one-click fix for MISSING_LIBRARY_COPY when the staging source
    still exists -- just re-runs the normal archive step. An asset whose
    staging source is also gone can't be fixed this way (it'll keep
    showing up until removed or trashed).
    """
    count = 0
    for asset_id in asset_ids:
        if library_assets.archive_asset(conn, staging_folder, assets_dir, asset_id) is not None:
            count += 1
    return count
