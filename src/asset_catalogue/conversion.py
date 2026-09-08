from __future__ import annotations

import json
import subprocess
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from asset_catalogue import ingest, library_assets, paths

ProgressCallback = Callable[[str], None]

CONVERT_SCRIPT_PATH = paths.package_dir() / "blender_convert_script.py"
CONVERT_TIMEOUT_SECONDS = 300


@dataclass
class ConversionResult:
    ok: bool
    error: str | None = None
    smart_texture_notes: list[str] = field(default_factory=list)
    broken_materials: list[tuple[int, str, str]] = field(default_factory=list)


@dataclass
class ConversionBatchResult:
    converted: int = 0
    converted_asset_ids: list[int] = field(default_factory=list)
    # Not a model asset, or already .glb -- convert_assets_to_gltf's
    # documented behavior of silently ignoring these rather than failing.
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    smart_texture_notes: list[str] = field(default_factory=list)
    broken_materials: list[tuple[int, str, str]] = field(default_factory=list)


# Every line blender_convert_script.py emits is PREFIX|<asset_id>|<rest>,
# so one split(maxsplit=2) covers all four kinds uniformly.
_RESULT_PREFIXES = {
    "ASSET_CATALOGUE_CONVERT_RESULT|": "result",
    "ASSET_CATALOGUE_CONVERT_ERROR|": "error",
    "ASSET_CATALOGUE_CONVERT_SMART_TEXTURE|": "smart_texture",
    "ASSET_CATALOGUE_CONVERT_BROKEN_MATERIAL|": "broken_material",
}


def _stream_conversion_results(
    blender_exe: Path, jobs: list[dict]
) -> Iterator[tuple[str, int, str]]:
    """Runs one Blender process for the whole of `jobs` and yields
    (kind, asset_id, payload) for each recognized line it prints, in
    order. One process per batch, not per job, because Blender's startup
    dominates the per-asset cost (same reasoning as
    blender_render.generate_model_thumbnails).

    A generator rather than a return-everything call so a caller can act
    on each result as it arrives -- convert_assets_to_gltf commits its
    database work per asset and reports progress as it goes, rather than
    going quiet for the length of a long batch.
    """
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
                str(CONVERT_SCRIPT_PATH),
                "--",
                str(job_list_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.strip()
            kind = next(
                (k for prefix, k in _RESULT_PREFIXES.items() if line.startswith(prefix)), None
            )
            if kind is None:
                continue
            _, asset_id_str, payload = line.split("|", 2)
            yield kind, int(asset_id_str), payload
        process.wait()
    finally:
        job_list_path.unlink(missing_ok=True)


def _resolve_conversion_row(conn: sqlite3.Connection, asset_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT assets.relative_path, assets.filename, assets.extension, "
        "assets.content_hash, assets.file_size, assets.asset_type, "
        "packs.pack_folder, packs.corrections, packs.name AS pack_name "
        "FROM assets JOIN packs ON packs.id = assets.pack_id WHERE assets.id = ?",
        (asset_id,),
    ).fetchone()


def _build_conversion_job(
    staging_folder: Path, asset_id: int, row: sqlite3.Row
) -> tuple[dict, str, Path]:
    """Caller must have already validated row is a non-.glb model asset.
    Returns (job_dict, new_relative_path, output_path).
    """
    pack_root = staging_folder / row["pack_folder"]
    new_relative_path = str(Path(row["relative_path"]).with_suffix(".glb"))
    output_path = pack_root / new_relative_path
    corrections = json.loads(row["corrections"]) if row["corrections"] else {}
    job = {
        "asset_id": asset_id,
        "source_path": str(pack_root / row["relative_path"]),
        "pack_root": str(pack_root),
        "output_path": str(output_path),
        "extension": row["extension"],
        "corrections": corrections,
    }
    return job, new_relative_path, output_path


def _apply_successful_conversion(
    conn: sqlite3.Connection,
    staging_folder: Path,
    assets_dir: Path,
    asset_id: int,
    row: sqlite3.Row,
    new_relative_path: str,
    output_path: Path,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO pending_conversions "
        "(asset_id, original_relative_path, original_filename, original_extension, "
        " original_content_hash, original_file_size, converted_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            asset_id,
            row["relative_path"],
            row["filename"],
            row["extension"],
            row["content_hash"],
            row["file_size"],
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    new_hash = ingest.hash_file(output_path)
    conn.execute(
        "UPDATE assets SET relative_path = ?, filename = ?, extension = '.glb', "
        "content_hash = ?, file_size = ?, thumbnail_status = 'pending', "
        "needs_glb_conversion = 0 WHERE id = ?",
        (
            new_relative_path,
            Path(new_relative_path).name,
            new_hash,
            output_path.stat().st_size,
            asset_id,
        ),
    )
    conn.commit()
    library_assets.archive_asset(conn, staging_folder, assets_dir, asset_id)


def convert_asset_to_gltf(
    conn: sqlite3.Connection,
    staging_folder: Path,
    assets_dir: Path,
    blender_exe: Path,
    asset_id: int,
    on_progress: ProgressCallback | None = None,
) -> ConversionResult:
    """Converts one model asset to .glb via Blender, in place: the same
    assets.id keeps its tags/import history, but relative_path/extension/
    content_hash/file_size are updated to point at the new .glb. The
    pre-conversion original is left untouched in staging (not deleted) and
    recorded in pending_conversions -- see revert_conversion (undo) and
    cleanup_pending_conversion (confirm and discard the original) below.
    Caller is responsible for regenerating the thumbnail afterward (reuse
    blender_render.generate_model_thumbnails with asset_id=), since that's
    already a well-tested single-asset preview path.
    """
    report = on_progress or (lambda _text: None)
    row = _resolve_conversion_row(conn, asset_id)
    if row is None:
        return ConversionResult(False, "Asset not found")
    if row["asset_type"] != "model":
        return ConversionResult(False, "Only model assets can be converted")
    if row["extension"].lower() == ".glb":
        return ConversionResult(False, "Already a .glb")

    job, new_relative_path, output_path = _build_conversion_job(staging_folder, asset_id, row)
    if output_path.exists():
        return ConversionResult(
            False, f"A file already exists at the conversion target: {new_relative_path}"
        )

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump([job], f)
        job_list_path = Path(f.name)

    report(f"Converting {row['filename']} to .glb...")
    try:
        process = subprocess.run(
            [
                str(blender_exe),
                "--background",
                "--python",
                str(CONVERT_SCRIPT_PATH),
                "--",
                str(job_list_path),
            ],
            capture_output=True,
            text=True,
            timeout=CONVERT_TIMEOUT_SECONDS,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    finally:
        job_list_path.unlink(missing_ok=True)

    output = process.stdout + process.stderr
    if f"ASSET_CATALOGUE_CONVERT_RESULT|{asset_id}|ok" not in output:
        error_line = next(
            (
                line
                for line in output.splitlines()
                if line.startswith("ASSET_CATALOGUE_CONVERT_ERROR|")
            ),
            None,
        )
        error_message = error_line.split("|", 2)[-1] if error_line else "Conversion failed"
        if output_path.exists():
            output_path.unlink()
        return ConversionResult(False, error_message)

    smart_texture_notes = [
        line.split("|", 2)[-1]
        for line in output.splitlines()
        if line.startswith(f"ASSET_CATALOGUE_CONVERT_SMART_TEXTURE|{asset_id}|")
    ]
    broken_materials = [
        (asset_id, row["filename"], line.split("|", 2)[-1])
        for line in output.splitlines()
        if line.startswith(f"ASSET_CATALOGUE_CONVERT_BROKEN_MATERIAL|{asset_id}|")
    ]

    _apply_successful_conversion(
        conn, staging_folder, assets_dir, asset_id, row, new_relative_path, output_path
    )
    return ConversionResult(True, smart_texture_notes=smart_texture_notes, broken_materials=broken_materials)


def convert_assets_to_gltf(
    conn: sqlite3.Connection,
    staging_folder: Path,
    assets_dir: Path,
    blender_exe: Path,
    asset_ids: list[int],
    on_progress: ProgressCallback | None = None,
) -> ConversionBatchResult:
    """Batch counterpart to convert_asset_to_gltf: converts every model
    asset in asset_ids that isn't already .glb, via a single Blender
    process for the whole batch (same batching rationale as
    blender_render.generate_model_thumbnails -- startup dominates the
    per-asset cost). Anything not a model, or already .glb, is silently
    skipped rather than counted as a failure -- this is what lets a caller
    pass a mixed multi-selection straight through without pre-filtering.
    Caller is responsible for regenerating thumbnails for the converted ids
    afterward (blender_render.generate_model_thumbnails with asset_ids=).
    """
    report = on_progress or (lambda _text: None)
    result = ConversionBatchResult()
    if not asset_ids:
        return result

    jobs: list[dict] = []
    job_context: dict[int, tuple[sqlite3.Row, str, Path]] = {}
    for asset_id in asset_ids:
        row = _resolve_conversion_row(conn, asset_id)
        if row is None or row["asset_type"] != "model" or row["extension"].lower() == ".glb":
            result.skipped += 1
            continue

        job, new_relative_path, output_path = _build_conversion_job(staging_folder, asset_id, row)
        if output_path.exists():
            result.failed += 1
            result.errors.append(
                f"asset {asset_id}: a file already exists at the conversion target "
                f"({new_relative_path})"
            )
            continue

        jobs.append(job)
        job_context[asset_id] = (row, new_relative_path, output_path)

    if not jobs:
        return result

    report(
        f"Starting Blender to convert {len(jobs)} model{'s' if len(jobs) != 1 else ''} to .glb..."
    )

    def display_name_for(asset_id: int) -> str:
        row = job_context.get(asset_id, (None,))[0]
        return row["filename"] if row is not None else f"asset {asset_id}"

    seen_ids: set[int] = set()
    for kind, asset_id, payload in _stream_conversion_results(blender_exe, jobs):
        if kind == "error":
            result.errors.append(f"asset {asset_id}: {payload}")
        elif kind == "smart_texture":
            result.smart_texture_notes.append(f"{display_name_for(asset_id)}: {payload}")
        elif kind == "broken_material":
            result.broken_materials.append((asset_id, display_name_for(asset_id), payload))
        else:
            seen_ids.add(asset_id)
            row, new_relative_path, output_path = job_context[asset_id]
            if payload != "ok":
                result.failed += 1
                report(f"Failed to convert {row['filename']} ({len(seen_ids)}/{len(jobs)})")
                if output_path.exists():
                    output_path.unlink()
                continue
            _apply_successful_conversion(
                conn, staging_folder, assets_dir, asset_id, row, new_relative_path, output_path
            )
            result.converted += 1
            result.converted_asset_ids.append(asset_id)
            report(f"Converted {row['filename']} to .glb ({len(seen_ids)}/{len(jobs)})")

    missing = [job for job in jobs if job["asset_id"] not in seen_ids]
    for job in missing:
        result.failed += 1
        result.errors.append(f"asset {job['asset_id']}: Blender exited before completing this job")

    return result


@dataclass
class ExportConversionResult:
    converted_asset_ids: list[int] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


def build_export_conversion_job(
    asset_id: int,
    source: Path,
    pack_root: Path,
    output_path: Path,
    extension: str,
    corrections: dict,
) -> dict:
    """One job for convert_for_export, in the shape blender_convert_
    script.py already reads. Deliberately the same shape
    _build_conversion_job produces for the library-side conversion, so
    both directions run the identical Blender script rather than
    maintaining a second one that would drift.
    """
    return {
        "asset_id": asset_id,
        "source_path": str(source),
        "pack_root": str(pack_root),
        "output_path": str(output_path),
        "extension": extension,
        "corrections": corrections,
    }


def convert_for_export(
    blender_exe: Path,
    jobs: list[dict],
    display_names: dict[int, str] | None = None,
    on_progress: ProgressCallback | None = None,
) -> ExportConversionResult:
    """Converts models to .glb straight into an export destination,
    touching no database and no catalogue state at all -- the asset in
    the library stays exactly the .fbx/.obj/.stl it was. This is the
    export-time counterpart to convert_assets_to_gltf, which converts the
    library's own copy in place and records a pending_conversions row so
    it can be reverted; nothing here is revertible because nothing here
    is a change to anything the user owns.

    Its whole reason for existing is textures: every format this handles
    references them as sibling files by relative path, and a .glb embeds
    them instead, so converting is what makes an exported model arrive in
    Godot actually textured. The pack's own corrections get applied on the
    way through as a bonus.
    """
    report = on_progress or (lambda _text: None)
    names = display_names or {}
    result = ExportConversionResult()
    if not jobs:
        return result

    def display_name_for(asset_id: int) -> str:
        return names.get(asset_id, f"asset {asset_id}")

    report(
        f"Converting {len(jobs)} model{'s' if len(jobs) != 1 else ''} to .glb with Blender "
        "(preserving textures)..."
    )
    seen_ids: set[int] = set()
    # blender_convert_script.py prints its ERROR line (carrying the real
    # reason) before the RESULT|fail line for the same asset, so a failure
    # already explained isn't restated with a vaguer message.
    explained: set[int] = set()
    for kind, asset_id, payload in _stream_conversion_results(blender_exe, jobs):
        if kind == "error":
            explained.add(asset_id)
            result.failures.append(f"{display_name_for(asset_id)}: {payload}")
        elif kind == "result":
            seen_ids.add(asset_id)
            if payload == "ok":
                result.converted_asset_ids.append(asset_id)
                report(
                    f"Converted {display_name_for(asset_id)} to .glb "
                    f"({len(result.converted_asset_ids)}/{len(jobs)})"
                )
                continue
            if asset_id not in explained:
                result.failures.append(f"{display_name_for(asset_id)}: conversion failed")
            report(f"Failed to convert {display_name_for(asset_id)}")

    for job in jobs:
        if job["asset_id"] not in seen_ids:
            result.failures.append(
                f"{display_name_for(job['asset_id'])}: Blender exited before completing this job"
            )
    return result


def has_pending_conversion(conn: sqlite3.Connection, asset_id: int) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM pending_conversions WHERE asset_id = ?", (asset_id,)
        ).fetchone()
        is not None
    )


def list_pending_conversion_asset_ids(conn: sqlite3.Connection) -> list[int]:
    return [
        row["asset_id"] for row in conn.execute("SELECT asset_id FROM pending_conversions")
    ]


def list_pending_conversions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Everything needed to show a real, reviewable list of pending
    conversions -- which assets, which packs, converted when -- rather than
    just the count `count_pending_conversions` gives. Backs the pending-
    conversions review dialog, so a bulk revert/cleanup decision can be
    made with actual context instead of a bare confirmation prompt.
    """
    return conn.execute(
        "SELECT pending_conversions.asset_id, assets.filename AS converted_filename, "
        "pending_conversions.original_filename, packs.name AS pack_name, "
        "pending_conversions.converted_at "
        "FROM pending_conversions "
        "JOIN assets ON assets.id = pending_conversions.asset_id "
        "JOIN packs ON packs.id = assets.pack_id "
        "ORDER BY packs.name, pending_conversions.converted_at"
    ).fetchall()


def revert_conversion(
    conn: sqlite3.Connection, staging_folder: Path, assets_dir: Path, asset_id: int
) -> bool:
    """Undoes a conversion: deletes the converted .glb (staging + library),
    restores the asset row to its pre-conversion file, and removes the
    pending_conversions record. Returns False if there was nothing pending.
    """
    pending = conn.execute(
        "SELECT * FROM pending_conversions WHERE asset_id = ?", (asset_id,)
    ).fetchone()
    if pending is None:
        return False

    asset_row = conn.execute(
        "SELECT assets.relative_path, packs.pack_folder, packs.name AS pack_name "
        "FROM assets JOIN packs ON packs.id = assets.pack_id WHERE assets.id = ?",
        (asset_id,),
    ).fetchone()

    converted_path = staging_folder / asset_row["pack_folder"] / asset_row["relative_path"]
    if converted_path.exists():
        converted_path.unlink()
    converted_library_path = library_assets.asset_library_path(
        assets_dir, asset_row["pack_name"], asset_row["relative_path"]
    )
    if converted_library_path.exists():
        converted_library_path.unlink()

    conn.execute(
        "UPDATE assets SET relative_path = ?, filename = ?, extension = ?, "
        "content_hash = ?, file_size = ?, thumbnail_status = 'pending' WHERE id = ?",
        (
            pending["original_relative_path"],
            pending["original_filename"],
            pending["original_extension"],
            pending["original_content_hash"],
            pending["original_file_size"],
            asset_id,
        ),
    )
    conn.execute("DELETE FROM pending_conversions WHERE asset_id = ?", (asset_id,))
    conn.commit()

    # Self-healing: re-confirm/restore the original's library copy in case
    # it was somehow missing (it shouldn't be, since cleanup is the only
    # thing that removes it, and this only runs when a conversion is still
    # pending, i.e. cleanup hasn't happened yet).
    library_assets.archive_asset(conn, staging_folder, assets_dir, asset_id)
    return True


def cleanup_pending_conversion(
    conn: sqlite3.Connection, staging_folder: Path, assets_dir: Path, asset_id: int
) -> bool:
    """Confirms a conversion is good: deletes the pre-conversion original
    (staging + library) and removes the pending_conversions record, keeping
    the converted asset as the permanent version. Returns False if there
    was nothing pending.
    """
    pending = conn.execute(
        "SELECT * FROM pending_conversions WHERE asset_id = ?", (asset_id,)
    ).fetchone()
    if pending is None:
        return False

    pack_row = conn.execute(
        "SELECT packs.pack_folder, packs.name AS pack_name "
        "FROM assets JOIN packs ON packs.id = assets.pack_id WHERE assets.id = ?",
        (asset_id,),
    ).fetchone()

    original_path = staging_folder / pack_row["pack_folder"] / pending["original_relative_path"]
    if original_path.exists():
        original_path.unlink()
    original_library_path = library_assets.asset_library_path(
        assets_dir, pack_row["pack_name"], pending["original_relative_path"]
    )
    if original_library_path.exists():
        original_library_path.unlink()

    conn.execute("DELETE FROM pending_conversions WHERE asset_id = ?", (asset_id,))
    conn.commit()
    return True


def cleanup_all_pending_conversions(
    conn: sqlite3.Connection, staging_folder: Path, assets_dir: Path
) -> int:
    count = 0
    for asset_id in list_pending_conversion_asset_ids(conn):
        if cleanup_pending_conversion(conn, staging_folder, assets_dir, asset_id):
            count += 1
    return count
