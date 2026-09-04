from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from asset_catalogue import audio_thumbnails, paths, thumbnails

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
        [str(blender_exe), "--version"], capture_output=True, text=True, timeout=30
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


def build_job_list(
    conn: sqlite3.Connection,
    staging_folder: Path,
    thumbnail_dir: Path,
    pack_name: str | None,
    force: bool,
    asset_id: int | None = None,
    asset_ids: list[int] | None = None,
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
        jobs.append(
            {
                "asset_id": row["id"],
                "filename": row["filename"],
                "source_path": str(staging_folder / row["pack_folder"] / row["relative_path"]),
                "output_path": str(dest),
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
    on_progress: ProgressCallback | None = None,
) -> ModelThumbnailStats:
    report = on_progress or (lambda _text: None)
    jobs, already_done = build_job_list(
        conn, staging_folder, thumbnail_dir, pack_name, force, asset_id, asset_ids
    )
    stats = ModelThumbnailStats(already_done=already_done)
    if not jobs:
        return stats

    filenames_by_id = {job["asset_id"]: job["filename"] for job in jobs}
    report(
        f"Starting Blender to render {len(jobs)} model thumbnail"
        f"{'s' if len(jobs) != 1 else ''}..."
    )
    thumbnail_dir.mkdir(parents=True, exist_ok=True)
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
        )

        seen_ids: set[int] = set()
        assert process.stdout is not None
        for line in process.stdout:
            line = line.strip()
            if not line.startswith("ASSET_CATALOGUE_RESULT|"):
                continue
            _, asset_id_str, status = line.split("|")
            asset_id = int(asset_id_str)
            seen_ids.add(asset_id)
            new_status = "done" if status == "ok" else "failed"
            conn.execute(
                "UPDATE assets SET thumbnail_status = ? WHERE id = ?", (new_status, asset_id)
            )
            conn.commit()
            filename = filenames_by_id.get(asset_id, f"asset {asset_id}")
            if status == "ok":
                stats.generated += 1
                report(f"Rendered thumbnail for {filename} ({len(seen_ids)}/{len(jobs)})")
            else:
                stats.failed += 1
                report(f"Failed to render thumbnail for {filename} ({len(seen_ids)}/{len(jobs)})")
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
        return stats

    preview_stats = generate_model_thumbnails(
        conn, staging_folder, thumbnail_dir, blender_exe, asset_id=model_ids[0],
        on_progress=on_progress,
    )
    stats.generated += preview_stats.generated
    stats.failed += preview_stats.failed
    stats.calibration_preview = True
    stats.models_pending = len(model_ids) - 1
    stats.preview_asset_id = model_ids[0]
    return stats
