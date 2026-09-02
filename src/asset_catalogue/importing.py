from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

GODOT_PROJECT_FILE = "project.godot"


def is_godot_project(project_root: Path) -> bool:
    return (project_root / GODOT_PROJECT_FILE).is_file()


def _sanitize_folder_name(name: str) -> str:
    return name.replace("/", "_").replace("\\", "_")


@dataclass
class ImportStats:
    copied: int = 0


def select_assets(
    conn: sqlite3.Connection,
    pack: str | None = None,
    asset_type: str | None = None,
    tag: str | None = None,
    asset_id: int | None = None,
) -> list[sqlite3.Row]:
    query = (
        "SELECT assets.id, assets.relative_path, packs.pack_folder, packs.name AS pack_name "
        "FROM assets JOIN packs ON packs.id = assets.pack_id"
    )
    clauses: list[str] = []
    params: list = []
    if asset_id is not None:
        clauses.append("assets.id = ?")
        params.append(asset_id)
    if tag:
        query += (
            " JOIN asset_tags ON asset_tags.asset_id = assets.id"
            " JOIN tags ON tags.id = asset_tags.tag_id"
        )
        clauses.append("tags.name = ?")
        params.append(tag)
    if pack:
        clauses.append("packs.name = ?")
        params.append(pack)
    if asset_type:
        clauses.append("assets.asset_type = ?")
        params.append(asset_type)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY packs.name, assets.relative_path"
    return conn.execute(query, params).fetchall()


def import_assets(
    conn: sqlite3.Connection,
    staging_folder: Path,
    project_root: Path,
    project_identifier: str,
    dest_subfolder: str,
    assets: list[sqlite3.Row],
) -> ImportStats:
    stats = ImportStats()
    for asset in assets:
        source = staging_folder / asset["pack_folder"] / asset["relative_path"]
        destination = (
            project_root
            / dest_subfolder
            / _sanitize_folder_name(asset["pack_name"])
            / asset["relative_path"]
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

        conn.execute(
            "INSERT INTO imports (asset_id, project_identifier, destination_path, timestamp) "
            "VALUES (?, ?, ?, ?)",
            (
                asset["id"],
                project_identifier,
                str(destination),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        stats.copied += 1
    conn.commit()
    return stats
