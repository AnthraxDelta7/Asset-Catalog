from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ProgressCallback = Callable[[str], None]


def _sanitize_folder_name(name: str) -> str:
    return name.replace("/", "_").replace("\\", "_")


def _unique_destination(pack_dir: Path, filename: str, taken: set[Path]) -> Path:
    """One flat folder per pack (see _copy_assets) means two assets that
    happen to share a filename in different subfolders of the source pack
    -- a texture reused under both Models/ and Props/, say -- would
    otherwise silently collide and overwrite each other. taken tracks
    every destination already claimed *in this export batch* (an already-
    existing file from a previous, separate export is deliberately not
    checked here -- shutil.copy2 overwriting a stale previous export of
    the same asset is the expected, idempotent re-export behavior, not a
    collision); a real collision gets " (2)", " (3)", etc. appended before
    the extension, the same convention Windows Explorer itself uses.
    """
    candidate = pack_dir / filename
    if candidate not in taken:
        return candidate
    stem, suffix = Path(filename).stem, Path(filename).suffix
    n = 2
    while True:
        candidate = pack_dir / f"{stem} ({n}){suffix}"
        if candidate not in taken:
            return candidate
        n += 1


@dataclass
class ExportStats:
    copied: int = 0


def select_assets(
    conn: sqlite3.Connection,
    pack: str | None = None,
    asset_type: str | None = None,
    tag: str | None = None,
    asset_id: int | None = None,
    asset_ids: list[int] | None = None,
) -> list[sqlite3.Row]:
    if asset_ids is not None and not asset_ids:
        return []

    query = (
        "SELECT assets.id, assets.relative_path, packs.pack_folder, packs.name AS pack_name "
        "FROM assets JOIN packs ON packs.id = assets.pack_id"
    )
    clauses: list[str] = []
    params: list = []
    if asset_id is not None:
        clauses.append("assets.id = ?")
        params.append(asset_id)
    if asset_ids is not None:
        placeholders = ",".join("?" for _ in asset_ids)
        clauses.append(f"assets.id IN ({placeholders})")
        params.extend(asset_ids)
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


# Formats Godot's own importers can actually turn into a scene on their
# own (glTF/.glb natively, .fbx/.obj via its built-in importers) -- the
# MeshInstance3D wrapper step (see godot_export.generate_meshinstance_
# wrappers) only makes sense for these. .stl/.blend are real model assets
# this app catalogues, but Godot doesn't import either directly, so a
# wrapper scene can never be built for them.
GODOT_IMPORTABLE_EXTENSIONS = frozenset({".glb", ".gltf", ".fbx", ".obj"})


def is_godot_export_eligible(assets: list[sqlite3.Row]) -> bool:
    """Whether every one of `assets` is something generate_meshinstance_
    wrappers can actually produce a wrapper scene for. Checked on the
    whole selection at once, not per-asset -- a mixed selection (a model
    plus a texture, say) falls back to a plain export entirely rather
    than silently wrapper-generating just the model half and copying the
    rest, which would make "what did this button just do" unpredictable.
    """
    if not assets:
        return False
    return all(
        Path(asset["relative_path"]).suffix.lower() in GODOT_IMPORTABLE_EXTENSIONS
        for asset in assets
    )


def _copy_assets(
    conn: sqlite3.Connection,
    staging_folder: Path,
    project_root: Path,
    project_identifier: str,
    dest_subfolder: str,
    assets: list[sqlite3.Row],
    on_progress: ProgressCallback | None = None,
) -> tuple[ExportStats, list[Path]]:
    """The actual file-copy loop shared by export_assets and
    export_assets_to_godot -- the latter additionally needs the resulting
    destination paths (to know exactly which files to hand to Godot for
    wrapper generation), which export_assets' own public signature has
    never needed to expose.
    """
    report = on_progress or (lambda _text: None)
    stats = ExportStats()
    destinations: list[Path] = []
    taken: set[Path] = set()
    for asset in assets:
        report(f"Exporting {asset['relative_path']}...")
        source = staging_folder / asset["pack_folder"] / asset["relative_path"]
        # One flat folder per pack -- not the pack's own internal
        # subfolder structure (Models/, Textures/, a creator's own nested
        # layout, ...) reproduced underneath it. A real downloaded pack's
        # own organization is rarely something worth preserving once
        # you've already deliberately picked out the handful of assets
        # you're exporting; see _unique_destination for how a same-name
        # collision this can now cause gets resolved instead of silently
        # overwriting.
        pack_dir = project_root / dest_subfolder / _sanitize_folder_name(asset["pack_name"])
        destination = _unique_destination(pack_dir, Path(asset["relative_path"]).name, taken)
        taken.add(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        destinations.append(destination)

        conn.execute(
            "INSERT INTO exports (asset_id, project_identifier, destination_path, timestamp) "
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
    return stats, destinations


def export_assets(
    conn: sqlite3.Connection,
    staging_folder: Path,
    project_root: Path,
    project_identifier: str,
    dest_subfolder: str,
    assets: list[sqlite3.Row],
    on_progress: ProgressCallback | None = None,
) -> ExportStats:
    stats, _destinations = _copy_assets(
        conn, staging_folder, project_root, project_identifier, dest_subfolder, assets, on_progress
    )
    return stats


def export_assets_to_godot(
    conn: sqlite3.Connection,
    staging_folder: Path,
    project_root: Path,
    project_identifier: str,
    dest_subfolder: str,
    assets: list[sqlite3.Row],
    on_progress: ProgressCallback | None = None,
) -> tuple[ExportStats, list[Path]]:
    """Same copy as export_assets, plus the destination paths -- callers
    (see Catalogue.export_assets_to_godot_bg) pass every model's path
    straight into godot_export.generate_meshinstance_wrappers afterward.
    Caller is expected to have already checked is_godot_export_eligible;
    this doesn't check it again, since it has no sensible fallback of its
    own for an ineligible asset -- deciding what to do about that belongs
    at the point the export mode itself gets chosen, not buried here.
    """
    return _copy_assets(
        conn, staging_folder, project_root, project_identifier, dest_subfolder, assets, on_progress
    )
