"""Shared helpers for the Blender-side scripts (blender_thumbnail_script.py,
blender_convert_script.py). Runs inside Blender's own Python interpreter --
bpy only exists there. Blender adds a --python script's own directory to
sys.path automatically, which is what lets both scripts `import
blender_common` despite this not being part of the normal package.
"""

import math
import os
import sys

import bpy

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


def apply_corrections(corrections: dict) -> tuple[list, bool]:
    """Applies pack-level corrections to whatever was just imported (every
    object that isn't Camera/Light -- those only exist in the thumbnail
    script's scene, harmless to exclude by name here regardless). Returns
    (mesh_objects, has_broken_texture) -- the mesh objects for the caller's
    next step (framing for a render, or nothing special for an export),
    and whether ANY of them references an image that failed to load,
    checked unconditionally (not gated behind broken_texture_fallback)
    since a caller needs to know this regardless of whether the fallback
    correction is actually turned on, to report it back to the user.
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

    broken_images = set(find_broken_texture_images())
    mesh_objects = [obj for obj in imported if obj.type == "MESH"]
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
    return mesh_objects, has_broken_texture
