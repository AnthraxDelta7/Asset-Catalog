"""Shared helpers for the Blender-side scripts (blender_thumbnail_script.py,
blender_convert_script.py). Runs inside Blender's own Python interpreter --
bpy only exists there. Blender adds a --python script's own directory to
sys.path automatically, which is what lets both scripts `import
blender_common` despite this not being part of the normal package.
"""

import math
import os
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import texture_matching

FALLBACK_MATERIAL_NAME = "AssetCatalogueFallback"


def _long_path(path: str) -> str:
    r"""Windows' classic file APIs cap a path at 260 characters (MAX_PATH).
    Python's own file access is long-path-aware and doesn't hit this, but
    Blender's importers (STL confirmed; likely the others too, just not yet
    hit by a deep enough pack) call straight into the OS APIs that do --
    "Cannot open file" for a perfectly real, readable file once its full
    staging path creeps past 260 chars (easy with a nested zip extraction
    plus a long pack/creator name). Prefixing with \\?\ (\\?\UNC\ for a
    network path) opts into Windows' extended-length path handling, which
    lifts the limit to ~32,767 chars -- the standard fix, not Blender- or
    asset-type-specific, so it's applied once here for every importer
    rather than per file type.
    """
    normalized = os.path.abspath(path)
    if normalized.startswith("\\\\"):
        return "\\\\?\\UNC\\" + normalized[2:]
    return "\\\\?\\" + normalized


IMPORTERS = {
    ".obj": lambda path: bpy.ops.wm.obj_import(filepath=_long_path(path)),
    ".fbx": lambda path: bpy.ops.import_scene.fbx(filepath=_long_path(path)),
    ".gltf": lambda path: bpy.ops.import_scene.gltf(filepath=_long_path(path)),
    ".glb": lambda path: bpy.ops.import_scene.gltf(filepath=_long_path(path)),
    ".stl": lambda path: bpy.ops.wm.stl_import(filepath=_long_path(path)),
    ".blend": lambda path: _import_blend(_long_path(path)),
}


def _import_blend(path: str) -> None:
    with bpy.data.libraries.load(path, link=False) as (data_from, data_to):
        data_to.objects = data_from.objects
    for obj in data_to.objects:
        if obj is not None:
            bpy.context.collection.objects.link(obj)


def get_job_list_path() -> str:
    argv = sys.argv
    return argv[argv.index("--") + 1]


def clear_imported_objects(keep: tuple[str, ...] = ()) -> None:
    for obj in list(bpy.data.objects):
        if obj.name not in keep:
            bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)


def find_broken_texture_images() -> list:
    """Every loaded image whose pixel data never actually resolved (size
    (0, 0)) -- Blender's own reliable signal that an image failed to load,
    regardless of why. In practice the common real-world cause isn't a
    typo'd relative path but an absolute one baked into the file at export
    time from the original author's own machine (e.g. "L:\\UNITY\\...",
    confirmed against a real downloaded pack) -- meaningless on any other
    machine, and there's nothing this app can do to recover the actual
    image data if the pack simply didn't include it. Checked directly
    against the loaded image's actual pixel size rather than guessing from
    the path string, since a relative path can be just as broken as an
    absolute one, and an absolute path pointing at a real, correct
    location isn't a problem at all.
    """
    return [image for image in bpy.data.images if tuple(image.size) == (0, 0)]


def _mesh_uses_any_image(obj, images: set) -> bool:
    for material in obj.data.materials:
        if material is None or not material.use_nodes:
            continue
        for node in material.node_tree.nodes:
            if node.type == "TEX_IMAGE" and node.image in images:
                return True
    return False


def _get_or_create_fallback_material():
    material = bpy.data.materials.get(FALLBACK_MATERIAL_NAME)
    if material is None:
        material = bpy.data.materials.new(FALLBACK_MATERIAL_NAME)
        material.use_nodes = True
        bsdf = material.node_tree.nodes.get("Principled BSDF")
        if bsdf is not None:
            bsdf.inputs["Base Color"].default_value = (0.6, 0.6, 0.6, 1.0)
    return material


def _mesh_materials(mesh_objects) -> dict:
    """name -> material, one entry per distinct material actually used
    across mesh_objects (a material can be shared by several objects;
    each is only ever processed once).
    """
    materials = {}
    for obj in mesh_objects:
        for material in obj.data.materials:
            if material is not None and material.name not in materials:
                materials[material.name] = material
    return materials


def _material_has_any_image_texture(material) -> bool:
    if not material.use_nodes:
        return False
    return any(node.type == "TEX_IMAGE" for node in material.node_tree.nodes)


def _assign_base_color_texture(material, texture_path: Path) -> bool:
    if not material.use_nodes:
        material.use_nodes = True
    bsdf = material.node_tree.nodes.get("Principled BSDF")
    if bsdf is None:
        return False
    image = bpy.data.images.load(str(texture_path))
    tex_node = material.node_tree.nodes.new("ShaderNodeTexImage")
    tex_node.image = image
    material.node_tree.links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
    return True


def _apply_texture_overrides(mesh_objects, pack_root: Path, overrides: dict) -> tuple[list[str], set[str]]:
    """Explicit material_name -> texture-path (relative to pack_root)
    overrides, applied before any automatic matching -- lets a user
    manually point at the right texture when the naming-convention
    matcher can't find one on its own. Returns (notes, handled_material_
    names); the caller skips both automatic passes for any name in the
    second set, whether or not applying it actually succeeded, so a
    materials with an override entry is never also second-guessed by the
    automatic matcher.
    """
    if not overrides:
        return [], set()
    materials = _mesh_materials(mesh_objects)
    notes = []
    handled = set()
    for material_name, relative_path in overrides.items():
        material = materials.get(material_name)
        if material is None:
            continue
        handled.add(material_name)
        if not relative_path:
            continue
        texture_path = pack_root / relative_path
        if not texture_path.is_file():
            continue
        if _assign_base_color_texture(material, texture_path):
            notes.append(f"'{material_name}' -> {texture_path.name} (manual override)")
    return notes, handled


def _relink_broken_images(broken_images, pack_root: Path) -> list[str]:
    """Attempts to fix each broken image by finding a file with the exact
    same basename anywhere else in the pack -- the common real-world
    cause of a broken reference is an absolute path baked in from the
    original author's own machine at export time, and the actual file is
    often still present in the pack, just not at that exact path
    (confirmed against a real downloaded pack). Returns a human-readable
    note per image successfully relinked.
    """
    notes = []
    for image in broken_images:
        basename = Path(image.filepath).name
        found = texture_matching.find_file_by_basename(basename, pack_root)
        if found is None:
            continue
        image.filepath = str(found)
        image.reload()
        if tuple(image.size) != (0, 0):
            notes.append(f"relinked {basename}")
    return notes


def _inject_missing_textures(mesh_objects, pack_root: Path, skip_materials: set[str]) -> list[str]:
    """For any material with no image texture node at all (never had one,
    not just broken), searches the pack for a texture file whose name
    matches the material's own name by convention (see
    texture_matching.find_texture_match) and wires it into Base Color if
    found. Never touches a material that already has any image texture
    node, working or broken -- this only ever fills in something that was
    completely absent, confirmed against a real pack where most modular-
    kit material names corresponded exactly to a texture file the source
    FBX just never referenced, sitting right alongside dozens of other,
    unrelated material names (character/plant/prop parts) that legitimately
    have no texture counterpart at all and are correctly left untouched.
    """
    texture_files = texture_matching.find_image_files(pack_root)
    if not texture_files:
        return []

    notes = []
    for name, material in _mesh_materials(mesh_objects).items():
        if name in skip_materials or _material_has_any_image_texture(material):
            continue
        match = texture_matching.find_texture_match(name, texture_files)
        if match is None:
            continue
        if _assign_base_color_texture(material, match):
            notes.append(f"'{name}' -> {match.name}")
    return notes


def apply_corrections(corrections: dict, pack_root: Path | None = None) -> tuple[list, bool, list[str]]:
    """Applies pack-level corrections to whatever was just imported (every
    object that isn't Camera/Light -- those only exist in the thumbnail
    script's scene, harmless to exclude by name here regardless). Returns
    (mesh_objects, has_broken_texture, smart_texture_notes):
    - mesh_objects, for the caller's next step (framing for a render, or
      nothing special for an export).
    - has_broken_texture: whether ANY mesh still references an image that
      failed to load, checked unconditionally (not gated behind
      broken_texture_fallback) since a caller needs to know this
      regardless of whether that fallback correction is turned on, to
      report it back to the user.
    - smart_texture_notes: one human-readable line per texture manually
      overridden, auto-relinked, or auto-matched by name -- see
      _apply_texture_overrides / _relink_broken_images /
      _inject_missing_textures. Always attempted when pack_root is given,
      unless disable_smart_texture_matching is set (an explicit, easy
      rollback switch for a pack where an automatic match turned out
      wrong) -- manual overrides still apply even then, since those are
      the user's own explicit instruction, not a guess.
    """
    imported = [obj for obj in bpy.data.objects if obj.name not in ("Camera", "Light")]

    if corrections.get("up_axis") == "Y_UP":
        for obj in imported:
            if obj.parent is None:
                obj.rotation_euler.x += math.radians(90)

    scale = corrections.get("scale")
    if scale and scale != 1.0:
        for obj in imported:
            if obj.parent is None:
                obj.scale = tuple(s * scale for s in obj.scale)

    if corrections.get("material_fallback"):
        material = _get_or_create_fallback_material()
        for obj in imported:
            if obj.type == "MESH":
                obj.data.materials.clear()
                obj.data.materials.append(material)

    mesh_objects = [obj for obj in imported if obj.type == "MESH"]
    smart_texture_notes: list[str] = []

    if pack_root is not None:
        override_notes, handled_materials = _apply_texture_overrides(
            mesh_objects, pack_root, corrections.get("texture_overrides") or {}
        )
        smart_texture_notes.extend(override_notes)

        if not corrections.get("disable_smart_texture_matching"):
            smart_texture_notes.extend(_relink_broken_images(set(find_broken_texture_images()), pack_root))
            smart_texture_notes.extend(_inject_missing_textures(mesh_objects, pack_root, handled_materials))

    broken_images = set(find_broken_texture_images())  # recomputed -- a relink above may have fixed some
    has_broken_texture = any(_mesh_uses_any_image(obj, broken_images) for obj in mesh_objects)

    if has_broken_texture and corrections.get("broken_texture_fallback"):
        # Surgical, not blanket like material_fallback above: only the
        # mesh(es) actually using a broken image get replaced -- a pack
        # can easily have some assets with working, deliberately-authored
        # materials right alongside others with a genuinely broken texture
        # link (confirmed against a real pack: guns with a dead absolute
        # path next to walls with real flat colors), and blanket-replacing
        # everything would wrongly gray out the ones that already work.
        material = _get_or_create_fallback_material()
        for obj in mesh_objects:
            if _mesh_uses_any_image(obj, broken_images):
                obj.data.materials.clear()
                obj.data.materials.append(material)

    bpy.context.view_layer.update()
    return mesh_objects, has_broken_texture, smart_texture_notes
