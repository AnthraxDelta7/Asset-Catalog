from __future__ import annotations

import sqlite3
from pathlib import Path


def generate_report(conn: sqlite3.Connection, project_root: Path | str | None = None) -> str:
    """A plain-text attribution report, grouped by pack: creator, licence,
    and source URL -- the fields most asset licences actually require you
    to credit. With project_root, only packs that have at least one asset
    actually exported into that project are included (via the `exports`
    table); without it, every pack currently in the catalogue is included,
    regardless of whether it's been used anywhere yet.
    """
    if project_root is not None:
        project_identifier = str(Path(project_root).resolve())
        rows = conn.execute(
            "SELECT DISTINCT packs.name, packs.creator, packs.licence, packs.source_url "
            "FROM packs "
            "JOIN assets ON assets.pack_id = packs.id "
            "JOIN exports ON exports.asset_id = assets.id "
            "WHERE exports.project_identifier = ? "
            "ORDER BY packs.name",
            (project_identifier,),
        ).fetchall()
        header = f"Credits for project: {project_identifier}"
    else:
        rows = conn.execute(
            "SELECT name, creator, licence, source_url FROM packs ORDER BY name"
        ).fetchall()
        header = "Credits for the entire catalogue"

    if not rows:
        return f"{header}\n\nNo packs found.\n"

    lines = [header, f"{len(rows)} pack(s)", ""]
    for row in rows:
        lines.append(f"Pack: {row['name']}")
        lines.append(f"  Creator: {row['creator'] or '(not specified)'}")
        lines.append(f"  Licence: {row['licence'] or '(not specified)'}")
        lines.append(f"  Source: {row['source_url'] or '(not specified)'}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"
