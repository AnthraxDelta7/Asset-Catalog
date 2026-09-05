from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass
class PackSize:
    pack_name: str
    asset_count: int
    total_size_bytes: int


@dataclass
class LibraryStats:
    total_assets: int
    total_size_bytes: int
    pack_count: int
    favorite_count: int
    by_asset_type: dict[str, int] = field(default_factory=dict)
    by_thumbnail_status: dict[str, int] = field(default_factory=dict)
    largest_packs: list[PackSize] = field(default_factory=list)


def compute_stats(conn: sqlite3.Connection, top_n_packs: int = 10) -> LibraryStats:
    """Read-only aggregation over the catalogue's own tracked metadata (each
    asset's file_size is recorded at ingest time -- no filesystem walk
    needed here). Trashed assets (see removal.py's soft-delete) are counted
    the same as active ones -- they're still "in the library" until
    actually purged, just hidden from the normal grid.
    """
    totals = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(file_size), 0) AS size, "
        "SUM(favorite) AS favorites FROM assets"
    ).fetchone()
    pack_count = conn.execute("SELECT COUNT(*) AS n FROM packs").fetchone()["n"]

    by_type = {
        row["asset_type"]: row["n"]
        for row in conn.execute(
            "SELECT asset_type, COUNT(*) AS n FROM assets GROUP BY asset_type"
        )
    }
    by_status = {
        row["thumbnail_status"]: row["n"]
        for row in conn.execute(
            "SELECT thumbnail_status, COUNT(*) AS n FROM assets GROUP BY thumbnail_status"
        )
    }
    largest_packs = [
        PackSize(row["name"], row["asset_count"], row["total_size"])
        for row in conn.execute(
            "SELECT packs.name, COUNT(assets.id) AS asset_count, "
            "COALESCE(SUM(assets.file_size), 0) AS total_size "
            "FROM packs LEFT JOIN assets ON assets.pack_id = packs.id "
            "GROUP BY packs.id ORDER BY total_size DESC LIMIT ?",
            (top_n_packs,),
        )
    ]

    return LibraryStats(
        total_assets=totals["n"],
        total_size_bytes=totals["size"],
        pack_count=pack_count,
        favorite_count=totals["favorites"] or 0,
        by_asset_type=by_type,
        by_thumbnail_status=by_status,
        largest_packs=largest_packs,
    )


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def format_report(stats: LibraryStats) -> str:
    """Shared plain-text rendering used by both the CLI's `stats` command
    and the UI's Library Statistics dialog, so the two never drift apart.
    """
    lines = [
        f"{stats.total_assets} asset(s) in {stats.pack_count} pack(s), "
        f"{format_bytes(stats.total_size_bytes)} total",
        f"{stats.favorite_count} favorited",
        "",
        "By type:",
    ]
    for asset_type, count in sorted(stats.by_asset_type.items()):
        lines.append(f"  {asset_type}: {count}")
    lines.append("")
    lines.append("By thumbnail status:")
    for status, count in sorted(stats.by_thumbnail_status.items()):
        lines.append(f"  {status}: {count}")
    if stats.largest_packs:
        lines.append("")
        lines.append("Largest packs:")
        for pack in stats.largest_packs:
            lines.append(
                f"  {pack.pack_name}: {format_bytes(pack.total_size_bytes)} "
                f"({pack.asset_count} asset(s))"
            )
    return "\n".join(lines)
