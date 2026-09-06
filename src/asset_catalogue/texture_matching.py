"""Pure helper for relinking a broken texture reference by exact filename --
no bpy dependency, unlike blender_common.py, so this can be (and is)
unit-tested directly. blender_common.py imports this module the same way
it's imported here: as a plain top-level module sitting directly in
src/asset_catalogue/ (see paths.py's package_dir() docstring for why it
can't live in a subpackage), added to Blender's own sys.path by the
calling script.

Used to also guess a material's texture by *name* when no reference
existed at all (matching "RecessA" to "SciFiTextures_RecessA_albedo.png"
by naming convention) -- removed after repeated real-pack cases where the
guess was confidently wrong (a material name shared by several unrelated
meshes, or a same-suffixed file that turned out to be a recolor mask, not
a usable color texture -- no naming convention reliably tells those apart).
Exact-basename relink doesn't have that problem: it only ever restores the
literal file a mesh's own material already named, never guesses across
meshes or file purposes.
"""

from __future__ import annotations

from pathlib import Path


def find_file_by_basename(basename: str, search_root: Path) -> Path | None:
    """The first file under search_root (recursive) whose name matches
    basename case-insensitively -- used to relink a broken absolute-path
    image reference to wherever the same file actually lives in the
    downloaded pack, when it's present but just not at the path baked
    into the source file.
    """
    lowered = basename.lower()
    for candidate in search_root.rglob("*"):
        if candidate.is_file() and candidate.name.lower() == lowered:
            return candidate
    return None
