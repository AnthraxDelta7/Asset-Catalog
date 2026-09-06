from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from asset_catalogue import audio_thumbnails, model_preview, paths, thumbnails

ProgressCallback = Callable[[str], None]

# bpy.ops.wm.obj_import / bpy.ops.wm.stl_import (used by the render script)
# were introduced in Blender 4.0, replacing the legacy import_scene.obj /
# import_mesh.stl operators. Verified against 5.2.1; not tested below 4.0.
MIN_BLENDER_VERSION = (4, 0, 0)

RENDER_SCRIPT_PATH = paths.package_dir() / "blender_thumbnail_script.py"

_WINDOWS_SEARCH_DIRS = (
    Path("C:/Program Files/Blender Foundation"),
    Path.home() / "AppData/Local/Programs/Blender Foundation",
)


def find_blender(blender_path_setting: str | None) -> Path | None:
    if blender_path_setting:
        candidate = Path(blender_path_setting)
        if candidate.is_file():
            return candidate

    found = shutil.which("blender")
    if found:
        return Path(found)

    for base in _WINDOWS_SEARCH_DIRS:
        if not base.is_dir():
            continue
        matches = sorted(base.glob("Blender */blender.exe"), reverse=True)
        if matches:
            return matches[0]

    return None


def get_blender_version(blender_exe: Path) -> tuple[int, int, int] | None:
    result = subprocess.run(
        [str(blender_exe), "--version"],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    match = re.search(r"Blender (\d+)\.(\d+)\.(\d+)", result.stdout)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def resolve_blender(blender_path_setting: str | None) -> tuple[Path | None, str | None]:
    """Finds and version-checks Blender. Returns (blender_exe, error) -- on
    failure blender_exe is None and error explains why, so a caller can
    treat "Blender isn't available" as a soft skip rather than a hard error
    (see generate_pack_thumbnails, and cli.py's cmd_thumbnail_generate_models
    for the CLI's own hard-error use of the same checks).
    """
    blender_exe = find_blender(blender_path_setting)
    if blender_exe is None:
        return None, "Blender not found. Set its path in Settings, or install it."
    version = get_blender_version(blender_exe)
    if version is None:
        return None, f"Could not determine Blender's version from {blender_exe}"
    if version < MIN_BLENDER_VERSION:
        min_version = ".".join(str(part) for part in MIN_BLENDER_VERSION)
        found_version = ".".join(str(part) for part in version)
        return None, f"Blender {found_version} is older than the minimum supported {min_version}"
    return blender_exe, None


@dataclass
class ModelThumbnailStats:
    generated: int = 0
    already_done: int = 0
    failed: int = 0
    # Filenames whose material references an image that failed to load
    # (Blender's own signal: the loaded image's pixel size is (0, 0)) --
    # in practice almost always an absolute path baked into the file from
    # the original author's own machine, meaningless on any other one.
    # Reported regardless of render success (frame_and_render still
    # renders *something*, just without the intended texture), so this is
    # a real, visible heads-up rather than a silent gray/wrong-looking
    # thumbnail that just looks like this app is broken.
    broken_texture_filenames: list[str] = field(default_factory=list)
    # One line per texture manually overridden, auto-relinked (a broken
    # absolute-path reference pointed at a same-named file found elsewhere
    # in the pack), or auto-matched by name (a material with no texture at
    # all wired to a same-named file) -- see blender_common.py's
    # apply_corrections. Reported so an automatic match is never silent,
    # even though it's applied by default.
    smart_texture_notes: list[str] = field(default_factory=list)


def build_job_list(
    conn: sqlite3.Connection,
    staging_folder: Path,
    thumbnail_dir: Path,
    pack_name: str | None,
    force: bool,
    asset_id: int | None = None,
    asset_ids: list[int] | None = None,
    preview_dir: Path | None = None,
) -> tuple[list[dict], int]:
    if asset_ids is not None and not asset_ids:
        return [], 0

    # Targeting specific asset(s) directly is a calibration preview / a
    # post-conversion refresh -- always re-render them regardless of prior
    # status, same as --force.
    effective_force = force or asset_id is not None or asset_ids is not None

    query = (
        "SELECT assets.id, assets.filename, assets.relative_path, assets.content_hash, "
        "assets.extension, packs.pack_folder, packs.corrections "
        "FROM assets JOIN packs ON packs.id = assets.pack_id "
        "WHERE assets.asset_type = 'model'"
    )
    params: list = []
    if asset_id is not None:
        query += " AND assets.id = ?"
        params.append(asset_id)
    if asset_ids is not None:
        placeholders = ",".join("?" for _ in asset_ids)
        query += f" AND assets.id IN ({placeholders})"
        params.extend(asset_ids)
    if not effective_force:
        query += " AND assets.thumbnail_status != 'done'"
    if pack_name:
        query += " AND packs.name = ?"
        params.append(pack_name)

    rows = conn.execute(query, params).fetchall()
    jobs: list[dict] = []
    already_done = 0
    for row in rows:
        dest = thumbnails.thumbnail_path(thumbnail_dir, row["content_hash"])
        if dest.exists() and not effective_force:
            conn.execute(
                "UPDATE assets SET thumbnail_status = 'done' WHERE id = ?", (row["id"],)
            )
            already_done += 1
            continue

        corrections = json.loads(row["corrections"]) if row["corrections"] else {}
        preview_output_path = None
        if preview_dir is not None:
            preview_output_path = str(model_preview.preview_path(preview_dir, row["content_hash"]))
        jobs.append(
            {
                "asset_id": row["id"],
                "filename": row["filename"],
                "source_path": str(staging_folder / row["pack_folder"] / row["relative_path"]),
                "pack_root": str(staging_folder / row["pack_folder"]),
                "output_path": str(dest),
                "preview_output_path": preview_output_path,
                "extension": row["extension"],
                "corrections": corrections,
            }
        )
    conn.commit()
    return jobs, already_done


def generate_model_thumbnails(
    conn: sqlite3.Connection,
    staging_folder: Path,
    thumbnail_dir: Path,
    blender_exe: Path,
    pack_name: str | None = None,
    force: bool = False,
    asset_id: int | None = None,
    asset_ids: list[int] | None = None,
    preview_dir: Path | None = None,
    on_progress: ProgressCallback | None = None,
) -> ModelThumbnailStats:
    report = on_progress or (lambda _text: None)
    jobs, already_done = build_job_list(
        conn, staging_folder, thumbnail_dir, pack_name, force, asset_id, asset_ids, preview_dir
    )
    stats = ModelThumbnailStats(already_done=already_done)
    if not jobs:
        return stats

    filenames_by_id = {job["asset_id"]: job["filename"] for job in jobs}
    extensions_by_id = {job["asset_id"]: job["extension"].lower() for job in jobs}
    report(
        f"Starting Blender to render {len(jobs)} model thumbnail"
        f"{'s' if len(jobs) != 1 else ''}..."
    )
    thumbnail_dir.mkdir(parents=True, exist_ok=True)
    if preview_dir is not None:
        preview_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(jobs, f)
        job_list_path = Path(f.name)

    try:
        process = subprocess.Popen(
            [
                str(blender_exe),
                "--background",
                "--python",
                str(RENDER_SCRIPT_PATH),
                "--",
                str(job_list_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        seen_ids: set[int] = set()
        smart_texture_asset_ids: set[int] = set()
        assert process.stdout is not None
        for line in process.stdout:
            line = line.strip()
            if line.startswith("ASSET_CATALOGUE_SMART_TEXTURE|"):
                _, smart_asset_id_str, note = line.split("|", 2)
                smart_texture_asset_ids.add(int(smart_asset_id_str))
                filename = filenames_by_id.get(int(smart_asset_id_str), f"asset {smart_asset_id_str}")
                stats.smart_texture_notes.append(f"{filename}: {note}")
                report(f"  Texture match: {filename}: {note}")
                continue
            if not line.startswith("ASSET_CATALOGUE_RESULT|"):
                continue
            parts = line.split("|")
            _, asset_id_str, status = parts[:3]
            # A 4th field (broken-texture flag) is new -- tolerate an older
            # blender_thumbnail_script.py (or a stray malformed line)
            # without it rather than crashing the whole batch on IndexError.
            broken_texture = len(parts) > 3 and parts[3] == "1"
            asset_id = int(asset_id_str)
            seen_ids.add(asset_id)
            new_status = "done" if status == "ok" else "failed"
            # An asset needs converting to .glb when this render only looks
            # right because of a fix that lives in the ephemeral Blender
            # scene, not the asset's own file -- a relinked/matched texture,
            # or (still) a broken one. Meaningless for an asset that's
            # already .glb (Convert refuses those anyway), and explicitly
            # recomputed either way rather than only ever set, so a re-
            # render that no longer needs a fix clears it back to 0.
            already_glb = extensions_by_id.get(asset_id) == ".glb"
            needs_conversion = (broken_texture or asset_id in smart_texture_asset_ids) and not already_glb
            conn.execute(
                "UPDATE assets SET thumbnail_status = ?, needs_glb_conversion = ? WHERE id = ?",
                (new_status, 1 if needs_conversion else 0, asset_id),
            )
            conn.commit()
            filename = filenames_by_id.get(asset_id, f"asset {asset_id}")
            if status == "ok":
                stats.generated += 1
                report(f"Rendered thumbnail for {filename} ({len(seen_ids)}/{len(jobs)})")
            else:
                stats.failed += 1
                report(f"Failed to render thumbnail for {filename} ({len(seen_ids)}/{len(jobs)})")
            if broken_texture:
                stats.broken_texture_filenames.append(filename)
                report(f"  Note: {filename} references a texture that failed to load")
            print(f"  [{len(seen_ids)}/{len(jobs)}] asset {asset_id}: {status}")

        process.wait()

        missing = [job for job in jobs if job["asset_id"] not in seen_ids]
        for job in missing:
            conn.execute(
                "UPDATE assets SET thumbnail_status = 'failed' WHERE id = ?",
                (job["asset_id"],),
            )
            stats.failed += 1
        conn.commit()

        if missing:
            print(
                f"Warning: Blender exited (code {process.returncode}) before completing "
                f"{len(missing)} job(s); marked failed."
            )
    finally:
        job_list_path.unlink(missing_ok=True)

    return stats


@dataclass
class AutoThumbnailStats:
    generated: int = 0
    failed: int = 0
    blender_unavailable_reason: str | None = None
    # True if this pack has never had a successfully-rendered model
    # thumbnail before -- only one model was rendered (as a calibration
    # preview), and models_pending were deliberately left un-rendered. See
    # generate_pack_thumbnails.
    calibration_preview: bool = False
    models_pending: int = 0
    preview_asset_id: int | None = None
    broken_texture_filenames: list[str] = field(default_factory=list)
    smart_texture_notes: list[str] = field(default_factory=list)


def generate_pack_thumbnails(
    conn: sqlite3.Connection,
    staging_folder: Path,
    thumbnail_dir: Path,
    blender_path_setting: str | None,
    pack_id: int,
    pack_name: str,
    on_progress: ProgressCallback | None = None,
) -> AutoThumbnailStats:
    """Generates thumbnails for every asset currently in a pack, dispatched
    by type -- Pillow for textures and audio, Blender for models. If the
    pack has no model assets, Blender is never even checked for (avoids
    paying its startup cost on packs that don't need it). If it does and
    Blender isn't available, that's a soft skip (recorded in
    blender_unavailable_reason), not an error -- 2D/audio thumbnails still
    complete either way.

    The first time a pack ever gets a model thumbnail, only ONE model asset
    is actually rendered -- a calibration preview, exactly the existing
    "render one asset, check it, adjust corrections, preview again" manual
    workflow (see "Per-pack calibration" in the README), just triggered
    automatically instead of requiring a first manual step. The rest of the
    pack's models are left thumbnail_status='pending' rather than rendered
    up front: if the preview turns out wrong (bad up_axis/scale/materials),
    only that one asset needs a corrected re-render, not the whole pack's
    worth. Once a pack has at least one successfully-rendered model (from
    this preview, or any prior run), later ingests into the same pack skip
    the preview step and render normally -- calibration is a once-per-pack
    concern, not once-per-ingest.
    """
    stats = AutoThumbnailStats()

    texture_stats = thumbnails.generate_texture_thumbnails(
        conn, staging_folder, thumbnail_dir, pack_name=pack_name, on_progress=on_progress
    )
    stats.generated += texture_stats.generated
    stats.failed += texture_stats.failed

    audio_stats = audio_thumbnails.generate_audio_thumbnails(
        conn, staging_folder, thumbnail_dir, pack_name=pack_name, on_progress=on_progress
    )
    stats.generated += audio_stats.generated
    stats.failed += audio_stats.failed

    model_ids = [
        row["id"]
        for row in conn.execute(
            "SELECT id FROM assets WHERE pack_id = ? AND asset_type = 'model'", (pack_id,)
        )
    ]
    if not model_ids:
        return stats

    blender_exe, error = resolve_blender(blender_path_setting)
    if blender_exe is None:
        stats.blender_unavailable_reason = error
        return stats

    already_calibrated = (
        conn.execute(
            "SELECT 1 FROM assets WHERE pack_id = ? AND asset_type = 'model' "
            "AND thumbnail_status = 'done' LIMIT 1",
            (pack_id,),
        ).fetchone()
        is not None
    )

    if already_calibrated:
        model_stats = generate_model_thumbnails(
            conn, staging_folder, thumbnail_dir, blender_exe, pack_name=pack_name,
            on_progress=on_progress,
        )
        stats.generated += model_stats.generated
        stats.failed += model_stats.failed
        stats.broken_texture_filenames.extend(model_stats.broken_texture_filenames)
        stats.smart_texture_notes.extend(model_stats.smart_texture_notes)
        return stats

    preview_stats = generate_model_thumbnails(
        conn, staging_folder, thumbnail_dir, blender_exe, asset_id=model_ids[0],
        on_progress=on_progress,
    )
    stats.generated += preview_stats.generated
    stats.failed += preview_stats.failed
    stats.broken_texture_filenames.extend(preview_stats.broken_texture_filenames)
    stats.smart_texture_notes.extend(preview_stats.smart_texture_notes)
    stats.calibration_preview = True
    stats.models_pending = len(model_ids) - 1
    stats.preview_asset_id = model_ids[0]
    return stats
