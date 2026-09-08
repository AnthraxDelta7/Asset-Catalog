from __future__ import annotations

import json
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
        "SELECT assets.id, assets.relative_path, packs.pack_folder, packs.name AS pack_name, "
        "packs.corrections "
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


# Model formats Blender can import, and so that this app can turn into a
# textured .glb on the way into a Godot project (see
# conversion.convert_for_export). Every one of these references its
# textures as sibling files by relative path rather than embedding them,
# so copying the file alone into a project -- especially into the one
# flat folder per pack that _copy_assets now produces -- reliably lands
# it with its materials pointing at nothing. Converting via Blender
# sidesteps that entirely: a .glb embeds its textures, and the pack's own
# corrections (material_fallback, broken_texture_fallback) get applied on
# the way through, which no engine-side importer could do.
BLENDER_CONVERTIBLE_EXTENSIONS = frozenset({".gltf", ".fbx", ".obj", ".stl", ".blend"})

# Everything a Godot MeshInstance3D wrapper scene can be produced for:
# .glb goes straight to Godot (already self-contained), anything else
# goes through Blender first. Deliberately NOT "what Godot itself can
# import" -- .stl and .blend are in here despite Godot being unable to
# read either, precisely because Godot never sees them in that form.
GODOT_EXPORTABLE_EXTENSIONS = frozenset({".glb"}) | BLENDER_CONVERTIBLE_EXTENSIONS


def needs_blender_conversion(relative_path: str) -> bool:
    return Path(relative_path).suffix.lower() in BLENDER_CONVERTIBLE_EXTENSIONS


def godot_destination_name(relative_path: str) -> str:
    """The filename this asset lands under in the Godot project. Anything
    Blender converts on the way in arrives as a .glb, so its destination
    is named for what it will actually be, not what it started as --
    decided here, before collision resolution, so a pack holding both
    prop.fbx and prop.glb resolves that collision against the real final
    names rather than discovering it after the fact.
    """
    name = Path(relative_path).name
    if needs_blender_conversion(name):
        return str(Path(name).with_suffix(".glb"))
    return name


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
        Path(asset["relative_path"]).suffix.lower() in GODOT_EXPORTABLE_EXTENSIONS
        for asset in assets
    )


def record_export(
    conn: sqlite3.Connection, asset_id: int, project_identifier: str, destination: Path
) -> None:
    """Logs one asset landing in one project. Public because the Godot
    path records its converted models from Catalogue.export_assets_to_
    godot_bg -- only once Blender has actually produced the .glb, so a
    failed conversion leaves no export history claiming a file that isn't
    there. Caller commits.
    """
    conn.execute(
        "INSERT INTO exports (asset_id, project_identifier, destination_path, timestamp) "
        "VALUES (?, ?, ?, ?)",
        (asset_id, project_identifier, str(destination), datetime.now(timezone.utc).isoformat()),
    )


def _plan_destinations(
    project_root: Path,
    dest_subfolder: str,
    assets: list[sqlite3.Row],
    namer: Callable[[str], str],
) -> list[Path]:
    """Where each asset lands, with same-name collisions already resolved.

    One flat folder per pack -- not the pack's own internal subfolder
    structure (Models/, Textures/, a creator's own nested layout, ...)
    reproduced underneath it. A real downloaded pack's own organization
    is rarely something worth preserving once you've already deliberately
    picked out the handful of assets you're exporting; see
    _unique_destination for how a same-name collision this can now cause
    gets resolved instead of silently overwriting.

    Planned for the whole batch up front rather than as each file is
    written, because the Godot path has to know a converted model's final
    .glb name before Blender runs -- the conversion writes straight to
    its destination, so there's no "copy it and rename later" step to
    resolve a collision in.
    """
    destinations: list[Path] = []
    taken: set[Path] = set()
    for asset in assets:
        pack_dir = project_root / dest_subfolder / _sanitize_folder_name(asset["pack_name"])
        destination = _unique_destination(pack_dir, namer(asset["relative_path"]), taken)
        taken.add(destination)
        destinations.append(destination)
    return destinations


def _copy_assets(
    conn: sqlite3.Connection,
    staging_folder: Path,
    project_root: Path,
    project_identifier: str,
    dest_subfolder: str,
    assets: list[sqlite3.Row],
    on_progress: ProgressCallback | None = None,
) -> tuple[ExportStats, list[Path]]:
    """The actual file-copy loop behind export_assets."""
    report = on_progress or (lambda _text: None)
    stats = ExportStats()
    destinations = _plan_destinations(
        project_root, dest_subfolder, assets, lambda rel: Path(rel).name
    )
    for asset, destination in zip(assets, destinations):
        report(f"Exporting {asset['relative_path']}...")
        source = staging_folder / asset["pack_folder"] / asset["relative_path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        record_export(conn, asset["id"], project_identifier, destination)
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


@dataclass
class GodotExportItem:
    """One model on its way into a Godot project as a .glb.

    source is the file in the *staging library*, deliberately not a copy
    of it made inside the project: Blender resolves a .fbx/.obj/.gltf's
    texture references relative to the model file's own location, so a
    conversion run against a copy that flattening has already separated
    from its sibling texture folder would produce exactly the untextured
    result this whole path exists to avoid.
    """

    asset_id: int
    display_name: str
    source: Path
    destination: Path
    pack_root: Path
    extension: str
    corrections: dict
    needs_conversion: bool


def plan_godot_export(
    conn: sqlite3.Connection,
    staging_folder: Path,
    project_root: Path,
    project_identifier: str,
    dest_subfolder: str,
    assets: list[sqlite3.Row],
    on_progress: ProgressCallback | None = None,
) -> list[GodotExportItem]:
    """Works out where every selected model lands as a .glb, and copies
    the ones that already are one straight there. Anything Blender has to
    convert is only *planned* here and returned with needs_conversion set
    -- see Catalogue.export_assets_to_godot_bg, which runs the whole batch
    through a single Blender process afterward rather than paying its
    startup cost per file.

    Caller is expected to have already checked is_godot_export_eligible;
    this doesn't check it again, since it has no sensible fallback of its
    own for an ineligible asset -- deciding what to do about that belongs
    at the point the export mode itself gets chosen, not buried here.
    """
    report = on_progress or (lambda _text: None)
    destinations = _plan_destinations(
        project_root, dest_subfolder, assets, godot_destination_name
    )
    items: list[GodotExportItem] = []
    for asset, destination in zip(assets, destinations):
        pack_root = staging_folder / asset["pack_folder"]
        relative_path = asset["relative_path"]
        item = GodotExportItem(
            asset_id=asset["id"],
            display_name=Path(relative_path).name,
            source=pack_root / relative_path,
            destination=destination,
            pack_root=pack_root,
            extension=Path(relative_path).suffix.lower(),
            corrections=json.loads(asset["corrections"]) if asset["corrections"] else {},
            needs_conversion=needs_blender_conversion(relative_path),
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not item.needs_conversion:
            report(f"Exporting {relative_path}...")
            shutil.copy2(item.source, destination)
            record_export(conn, item.asset_id, project_identifier, destination)
        items.append(item)
    conn.commit()
    return items
