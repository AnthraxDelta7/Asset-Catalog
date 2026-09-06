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
    """Every .tscn file in the project, in a stable order -- includes
    non-mesh scenes (UI, autoloads, etc); export_scenes_to_glb skips those
    after the fact once it can see the exported result has no geometry,
    rather than trying to guess from the .tscn text upfront.
    """
    return sorted(project_root.rglob("*.tscn"))


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

    jobs = []
    output_by_scene: dict[str, Path] = {}
    for scene_path in scene_paths:
        relative = scene_path.relative_to(project_root).as_posix()
        output_path = scene_path.with_suffix(".glb")
        jobs.append({"scene_path": f"res://{relative}", "output_path": str(output_path)})
        output_by_scene[f"res://{relative}"] = output_path

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
        for line in process.stdout:
            line = line.strip()
            if not line.startswith("GODOT_EXPORT_RESULT|"):
                continue
            _, scene_res_path, status, detail = line.split("|", 3)
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
