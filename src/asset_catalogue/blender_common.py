"""Shared helpers for the Blender-side scripts (blender_thumbnail_script.py,
blender_convert_script.py). Runs inside Blender's own Python interpreter --
bpy only exists there. Blender adds a --python script's own directory to
sys.path automatically, which is what lets both scripts `import
blender_common` despite this not being part of the normal package.
"""

import math
import sys

import bpy

FALLBACK_MATERIAL_NAME = "AssetCatalogueFallback"

IMPORTERS = {
    ".obj": lambda path: bpy.ops.wm.obj_import(filepath=path),
    ".fbx": lambda path: bpy.ops.import_scene.fbx(filepath=path),
    ".gltf": lambda path: bpy.ops.import_scene.gltf(filepath=path),
    ".glb": lambda path: bpy.ops.import_scene.gltf(filepath=path),
    ".stl": lambda path: bpy.ops.wm.stl_import(filepath=path),
    ".blend": lambda path: _import_blend(path),
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


def apply_corrections(corrections: dict) -> list:
    """Applies pack-level corrections to whatever was just imported (every
    object that isn't Camera/Light -- those only exist in the thumbnail
    script's scene, harmless to exclude by name here regardless). Returns
    the mesh objects, since that's what both callers need next (framing for
    a render, or nothing special for an export).
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
        material = bpy.data.materials.get(FALLBACK_MATERIAL_NAME)
        if material is None:
            material = bpy.data.materials.new(FALLBACK_MATERIAL_NAME)
            material.use_nodes = True
            bsdf = material.node_tree.nodes.get("Principled BSDF")
            if bsdf is not None:
                bsdf.inputs["Base Color"].default_value = (0.6, 0.6, 0.6, 1.0)
        for obj in imported:
            if obj.type == "MESH":
                obj.data.materials.clear()
                obj.data.materials.append(material)

    bpy.context.view_layer.update()
    return [obj for obj in imported if obj.type == "MESH"]
