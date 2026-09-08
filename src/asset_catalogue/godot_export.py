"""Extracts individual scenes from a staged Godot project into real,
textured .glb files -- a pre-ingest step, not part of ingest.py itself.

The problem this solves: a Godot pack's mesh files (.obj/.fbx/.gltf, or
even a bare .glb) commonly carry no material of their own -- the texture
is assigned in the .tscn scene (a MeshInstance3D's material override) or a
separate .tres material resource, which is exactly the linkup Godot's own
scene graph resolves and this app's Blender-based importer has no way to
see. Rather than parse that linkup ourselves (fragile across material/
shader variations), this shells out to the real Godot editor headlessly
and asks it to export the fully-resolved scene via its own GLTFDocument
API -- the same mechanism behind Godot's Scene > Export As > glTF2 Scene
menu item, just scripted. Verified for real against actual Godot 4.4
projects (including an inherited-scene/skeletal-mesh case) before writing
this, not assumed from documentation alone.

Output .glb files are written next to their source .tscn, inside the same
staged pack folder -- ingest_pack's own recursive walk (see ingest.py)
picks them up afterward as ordinary, already-recognized model assets, so
nothing about the ingest pipeline itself needs to know Godot was involved.

Mirrors blender_render.py's shape: detected-not-bundled external tool,
one process handling many jobs since startup dominates the cost, progress
reported via delimited stdout lines.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from asset_catalogue import paths

ProgressCallback = Callable[[str], None]

# GLTFDocument/GLTFState scripting from a headless -s run is a Godot 4 API;
# not present in 3.x. Verified against 4.4 specifically -- not tested below
# that, but the API has been stable across 4.x minor releases so far.
MIN_GODOT_VERSION = (4, 0, 0)

EXPORT_SCRIPT_PATH = paths.package_dir() / "godot_export_script.gd"
WRAPPER_SCRIPT_PATH = paths.package_dir() / "godot_meshinstance_wrapper_script.gd"

_WINDOWS_SEARCH_DIRS = (
    Path("C:/Program Files/Godot"),
    Path.home() / "AppData/Local/Programs/Godot",
)


def find_godot(godot_path_setting: str | None) -> Path | None:
    """Godot ships as a single portable, version-named .exe with no
    standard install location (unlike Blender's installer) -- an explicit
    setting is the primary path here, PATH/common-folder lookups are just
    a bonus for the minority who've placed it somewhere predictable.
    """
    if godot_path_setting:
        candidate = Path(godot_path_setting)
        if candidate.is_file():
            return candidate

    for name in ("godot", "godot4", "Godot"):
        found = shutil.which(name)
        if found:
            return Path(found)

    for base in _WINDOWS_SEARCH_DIRS:
        if not base.is_dir():
            continue
        matches = sorted(base.glob("**/Godot*.exe"), reverse=True)
        if matches:
            return matches[0]

    return None


def get_godot_version(godot_exe: Path) -> tuple[int, int, int] | None:
    result = subprocess.run(
        [str(godot_exe), "--version"],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", result.stdout)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


def resolve_godot(godot_path_setting: str | None) -> tuple[Path | None, str | None]:
    """Finds and version-checks Godot. Returns (godot_exe, error) -- on
    failure godot_exe is None and error explains why, mirroring
    blender_render.resolve_blender's soft-skip contract.
    """
    godot_exe = find_godot(godot_path_setting)
    if godot_exe is None:
        return None, "Godot not found. Set its path in Settings."
    version = get_godot_version(godot_exe)
    if version is None:
        return None, f"Could not determine Godot's version from {godot_exe}"
    if version < MIN_GODOT_VERSION:
        min_version = ".".join(str(part) for part in MIN_GODOT_VERSION)
        found_version = ".".join(str(part) for part in version)
        return None, f"Godot {found_version} is older than the minimum supported {min_version}"
    return godot_exe, None


def find_godot_project_roots(search_root: Path) -> list[Path]:
    """Every directory under search_root (search_root itself included)
    that has its own project.godot -- a staged pack occasionally bundles
    more than one Godot project (e.g. separate demo + asset projects).
    """
    roots = []
    if (search_root / "project.godot").is_file():
        roots.append(search_root)
    for candidate in search_root.rglob("project.godot"):
        if candidate.parent != search_root:
            roots.append(candidate.parent)
    return roots


def find_scenes(project_root: Path) -> list[Path]:
    """Every scene file in the project, in a stable order -- both the
    text-based .tscn format and Godot's equally-valid compressed-binary
    .scn format (real packs use both; a marketplace pack converted from
    another engine commonly ships .scn exclusively, confirmed against a
    real Synty POLYGON pack where a .tscn-only search silently found
    nothing to export at all). Includes non-mesh scenes (UI, autoloads,
    etc); export_scenes_to_glb skips those after the fact once it can see
    the exported result has no geometry, rather than trying to guess from
    the scene's own content upfront.

    Excludes anything under a .godot/ folder -- Godot's own editor re-
    import cache, which mirrors every real .scn/.tscn (and every raw
    imported mesh) as its own auto-generated, hash-named .scn under
    .godot/imported/. Confirmed against the same real pack above: 200 of
    305 "scenes" a naive rglob found were exactly this cache, not real
    content -- indistinguishable from genuine scenes by extension alone,
    only by living under .godot/.
    """
    tscn_files = project_root.rglob("*.tscn")
    scn_files = project_root.rglob("*.scn")
    return sorted(
        path for path in list(tscn_files) + list(scn_files) if ".godot" not in path.relative_to(project_root).parts
    )


@dataclass
class GodotExportStats:
    exported: int = 0
    skipped_empty: int = 0
    failed: int = 0
    failures: list[str] = field(default_factory=list)


def _has_real_geometry(glb_path: Path) -> bool:
    """A scene with no MeshInstance3D (UI, autoloads, marker-only scenes)
    still exports successfully -- Godot happily writes a near-empty glb
    rather than erroring. Loading it back through trimesh (already a
    project dependency, see model_preview_dialog.py) is a simple, reliable
    way to tell "real content" apart from that, without trying to guess
    from the .tscn's own text upfront.
    """
    import trimesh

    try:
        loaded = trimesh.load(glb_path, force="scene")
    except Exception:
        return False
    geometries = loaded.geometry.values() if hasattr(loaded, "geometry") else [loaded]
    return any(len(getattr(mesh, "vertices", [])) > 0 for mesh in geometries)


def _build_export_jobs(
    project_root: Path, scene_paths: list[Path]
) -> tuple[list[dict], dict[str, Path]]:
    """Turns absolute scene paths into the res://-relative job list the
    export script expects, plus a res:// -> output-path lookup for
    matching each GODOT_EXPORT_RESULT line back to a real filesystem path.
    Pulled out of export_scenes_to_glb as pure, no-subprocess logic so it
    can be tested directly rather than only indirectly through a real
    Godot run.
    """
    jobs = []
    output_by_scene: dict[str, Path] = {}
    for scene_path in scene_paths:
        relative = scene_path.relative_to(project_root).as_posix()
        output_path = scene_path.with_suffix(".glb")
        res_path = f"res://{relative}"
        jobs.append({"scene_path": res_path, "output_path": str(output_path)})
        output_by_scene[res_path] = output_path
    return jobs, output_by_scene


def _parse_export_result_line(line: str) -> tuple[str, str, str] | None:
    """Parses one GODOT_EXPORT_RESULT|<scene_path>|<status>|<detail> line
    from the export script's stdout into (scene_path, status, detail).
    Returns None for any other line -- Godot's own startup/shutdown
    logging shares the same stdout stream and is expected, harmless noise
    to skip past, not something to raise on encountering.
    """
    if not line.startswith("GODOT_EXPORT_RESULT|"):
        return None
    _, scene_res_path, status, detail = line.split("|", 3)
    return scene_res_path, status, detail


def export_scenes_to_glb(
    godot_exe: Path,
    project_root: Path,
    scene_paths: list[Path],
    include_colliders: bool = True,
    on_progress: ProgressCallback | None = None,
) -> GodotExportStats:
    """Exports each of scene_paths (absolute paths under project_root) to
    a .glb sitting right next to its source .tscn, via one headless Godot
    process for the whole batch (see godot_export_script.gd). A scene that
    turns out to have no real geometry once exported has its output
    deleted and is counted as skipped, not failed -- an ordinary outcome
    for a UI/autoload scene, not something the user needs to act on.

    include_colliders (on by default) has the script inject a low-poly
    debug mesh for every CollisionShape3D it finds before exporting --
    collision shapes have no visual representation of their own, so
    without this they're silently absent from the export entirely. Off
    doesn't fail on a pack with no colliders either way; it's just noise
    to skip when you know a pack has none.
    """
    report = on_progress or (lambda _text: None)
    stats = GodotExportStats()
    if not scene_paths:
        return stats

    jobs, output_by_scene = _build_export_jobs(project_root, scene_paths)

    report(
        f"Starting Godot to export {len(jobs)} scene{'s' if len(jobs) != 1 else ''} "
        f"from {project_root.name}..."
    )
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump({"include_colliders": include_colliders, "jobs": jobs}, f)
        job_list_path = Path(f.name)

    try:
        process = subprocess.Popen(
            [
                str(godot_exe),
                "--headless",
                "--path",
                str(project_root),
                "-s",
                str(EXPORT_SCRIPT_PATH),
                "--",
                str(job_list_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        seen: set[str] = set()
        assert process.stdout is not None
        for raw_line in process.stdout:
            parsed = _parse_export_result_line(raw_line.strip())
            if parsed is None:
                continue
            scene_res_path, status, detail = parsed
            seen.add(scene_res_path)
            display_name = Path(scene_res_path).name
            if status != "ok":
                stats.failed += 1
                stats.failures.append(f"{display_name}: {detail}")
                report(f"Failed to export {display_name}: {detail}")
                continue

            output_path = output_by_scene[scene_res_path]
            if _has_real_geometry(output_path):
                stats.exported += 1
                report(f"Exported {display_name} -> {output_path.name} ({stats.exported}/{len(jobs)})")
            else:
                output_path.unlink(missing_ok=True)
                stats.skipped_empty += 1
                report(f"Skipped {display_name} (no mesh content)")

        process.wait()

        missing = [job for job in jobs if job["scene_path"] not in seen]
        for job in missing:
            stats.failed += 1
            stats.failures.append(f"{Path(job['scene_path']).name}: Godot exited before finishing")
    finally:
        job_list_path.unlink(missing_ok=True)

    return stats


@dataclass
class GodotWrapperStats:
    generated: int = 0
    failed: int = 0
    # How many of `generated` were exported by being left exactly as they
    # are, because flattening them would have destroyed something (see
    # gltf_metadata.preservation_reasons). Counted separately purely so
    # the UI can say what actually happened -- reporting a preserved
    # rigged character as a "generated MeshInstance3D scene" states the
    # opposite of the truth.
    preserved: int = 0
    failures: list[str] = field(default_factory=list)
    # Absolute source .glb paths a wrapper was actually generated for --
    # Catalogue.export_assets_to_godot_bg uses this to know exactly which
    # intermediate .glb copies are now safe to delete (the wrapper scene
    # is fully self-contained, confirmed directly against a real Godot
    # install -- see the wrapper script's own docstring) versus which
    # ones failed and should be left in place as the only usable result
    # for that asset.
    succeeded_sources: list[Path] = field(default_factory=list)


def _run_godot_import_pass(godot_exe: Path, project_root: Path) -> bool:
    """Forces Godot to import any newly-added files under project_root --
    a .glb this app just copied in has no .import cache yet, and a bare
    SceneTree script run (-s, what generate_meshinstance_wrappers uses for
    the actual work) can't load a resource without one: confirmed
    directly, load() on an unimported .glb fails outright with "No loader
    found for resource", not a recoverable error. export_scenes_to_glb
    doesn't need this same step -- that direction's project is assumed
    already-imported from the user's own prior use of it in the real
    Godot editor.

    Godot's own headless-editor progress-dialog machinery prints noisy
    but harmless ERROR lines to stderr during this even on a fully
    successful run (confirmed directly against a real project) -- only
    the process's own exit code is checked, not its output.
    """
    result = subprocess.run(
        [str(godot_exe), "--headless", "--editor", "--path", str(project_root), "--import"],
        capture_output=True,
        text=True,
        timeout=180,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return result.returncode == 0


def _build_wrapper_jobs(project_root: Path, glb_paths: list[Path]) -> list[dict]:
    """Turns absolute .glb paths (already copied under project_root) into
    the res://-relative job list the wrapper script expects. Pulled out of
    generate_meshinstance_wrappers as pure, no-subprocess logic so it can
    be tested directly, mirroring _build_export_jobs's own reasoning.

    Each job carries an extension-less output_base rather than a full
    output path: whether a model becomes a bare <name>.res Mesh resource
    or a <name>_meshinstance.tscn scene depends on how many meshes it
    turns out to contain, which nothing here can know without loading it
    in Godot. The script decides and reports the path it actually wrote.
    """
    jobs = []
    for glb_path in glb_paths:
        relative = glb_path.relative_to(project_root).as_posix()
        base_relative = glb_path.with_suffix("").relative_to(project_root).as_posix()
        jobs.append({"glb_path": f"res://{relative}", "output_base": f"res://{base_relative}"})
    return jobs


def _parse_wrapper_result_line(line: str) -> tuple[str, str, str] | None:
    """Parses one GODOT_WRAPPER_RESULT|<glb_path>|<status>|<detail> line
    from the wrapper script's stdout into (glb_path, status, detail).
    Returns None for any other line -- same reasoning as
    _parse_export_result_line: Godot's own startup/shutdown logging
    shares this stdout stream and is expected, harmless noise to skip.
    """
    if not line.startswith("GODOT_WRAPPER_RESULT|"):
        return None
    _, glb_res_path, status, detail = line.split("|", 3)
    return glb_res_path, status, detail


def generate_meshinstance_wrappers(
    godot_exe: Path,
    project_root: Path,
    glb_paths: list[Path],
    on_progress: ProgressCallback | None = None,
) -> GodotWrapperStats:
    """For each of glb_paths (absolute paths under project_root, already
    copied there by the caller), generates a companion
    <name>_meshinstance.tscn: a clean scene whose root is a single
    MeshInstance3D (or, for a source with more than one mesh, a Node3D
    root with one MeshInstance3D child per mesh, each at its correct
    relative position) instead of whatever raw node hierarchy Godot's own
    glTF importer produced -- an arbitrary root node type, potentially
    carrying an AnimationPlayer/Skeleton3D import artifact even for a
    static prop. See godot_meshinstance_wrapper_script.gd for the actual
    scene-building logic and what's been verified about it directly.

    Runs a full headless project import pass first (see
    _run_godot_import_pass) so the just-copied .glb files can actually be
    loaded at all, then one Godot process for the whole batch, mirroring
    export_scenes_to_glb's own "one process, many jobs" shape.
    """
    report = on_progress or (lambda _text: None)
    stats = GodotWrapperStats()
    if not glb_paths:
        return stats

    report("Importing new files into the Godot project...")
    if not _run_godot_import_pass(godot_exe, project_root):
        stats.failed = len(glb_paths)
        stats.failures.append("Godot's headless import pass failed")
        return stats

    jobs = _build_wrapper_jobs(project_root, glb_paths)
    source_by_res_path = {job["glb_path"]: source for job, source in zip(jobs, glb_paths)}

    report(f"Generating {len(jobs)} Godot asset{'s' if len(jobs) != 1 else ''}...")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump({"jobs": jobs}, f)
        job_list_path = Path(f.name)

    try:
        process = subprocess.Popen(
            [
                str(godot_exe),
                "--headless",
                "--path",
                str(project_root),
                "-s",
                str(WRAPPER_SCRIPT_PATH),
                "--",
                str(job_list_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        seen: set[str] = set()
        assert process.stdout is not None
        for raw_line in process.stdout:
            parsed = _parse_wrapper_result_line(raw_line.strip())
            if parsed is None:
                continue
            glb_res_path, status, detail = parsed
            seen.add(glb_res_path)
            display_name = Path(glb_res_path).name
            if status != "ok":
                stats.failed += 1
                stats.failures.append(f"{display_name}: {detail}")
                report(f"Failed to generate wrapper for {display_name}: {detail}")
                continue

            stats.generated += 1
            stats.succeeded_sources.append(source_by_res_path[glb_res_path])
            # detail is the res:// path the script actually wrote -- only
            # it knows whether this became a .res or a .tscn.
            report(f"Generated {Path(detail).name} ({stats.generated}/{len(jobs)})")

        process.wait()

        missing = [job for job in jobs if job["glb_path"] not in seen]
        for job in missing:
            stats.failed += 1
            stats.failures.append(f"{Path(job['glb_path']).name}: Godot exited before finishing")
    finally:
        job_list_path.unlink(missing_ok=True)

    return stats
