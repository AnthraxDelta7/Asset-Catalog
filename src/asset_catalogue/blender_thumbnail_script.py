"""Runs inside Blender's own Python interpreter via `blender --background --python`.

Not part of the asset_catalogue package's normal import graph -- bpy only
exists inside Blender, so this file is invoked as a subprocess script, never
imported directly. See blender_render.py for the host-side half of this.
"""

import json
import math
import sys

import bpy
import mathutils

RESOLUTION = 256
WORLD_COLOR = (0.05, 0.05, 0.06)  # neutral dark gray/charcoal
CAMERA_DIRECTION = mathutils.Vector((1, -1, 0.7)).normalized()
CAMERA_DISTANCE_FACTOR = 3.2  # empirical fit for the default ~40deg camera FOV
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


def setup_scene_once() -> None:
    scene = bpy.context.scene
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    camera_data = bpy.data.cameras.new("Camera")
    camera = bpy.data.objects.new("Camera", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera

    light_data = bpy.data.lights.new("Light", type="SUN")
    light_data.energy = 3.0
    light = bpy.data.objects.new("Light", light_data)
    scene.collection.objects.link(light)

    world = bpy.data.worlds.get("World") or bpy.data.worlds.new("World")
    background = world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs[0].default_value = (*WORLD_COLOR, 1.0)
    scene.world = world

    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = RESOLUTION
    scene.render.resolution_y = RESOLUTION
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False


def clear_imported_objects() -> None:
    for obj in list(bpy.data.objects):
        if obj.name not in ("Camera", "Light"):
            bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)


def apply_corrections(corrections: dict) -> list:
    imported = [
        obj for obj in bpy.data.objects if obj.name not in ("Camera", "Light")
    ]

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


def frame_and_render(mesh_objects: list, output_path: str) -> bool:
    if not mesh_objects:
        return False

    min_corner = mathutils.Vector((math.inf, math.inf, math.inf))
    max_corner = mathutils.Vector((-math.inf, -math.inf, -math.inf))
    for obj in mesh_objects:
        for corner in obj.bound_box:
            world_corner = obj.matrix_world @ mathutils.Vector(corner)
            min_corner.x = min(min_corner.x, world_corner.x)
            min_corner.y = min(min_corner.y, world_corner.y)
            min_corner.z = min(min_corner.z, world_corner.z)
            max_corner.x = max(max_corner.x, world_corner.x)
            max_corner.y = max(max_corner.y, world_corner.y)
            max_corner.z = max(max_corner.z, world_corner.z)

    center = (min_corner + max_corner) / 2
    size = max_corner - min_corner
    # Bounding-sphere radius (half the box diagonal), not half of any single
    # axis -- from a diagonal camera angle the apparent extent is closer to
    # the diagonal than to any one dimension.
    radius = max(size.length / 2, 0.001)
    distance = radius * CAMERA_DISTANCE_FACTOR

    camera = bpy.data.objects["Camera"]
    camera.location = center + CAMERA_DIRECTION * distance
    look_direction = (center - camera.location).normalized()
    camera.rotation_euler = look_direction.to_track_quat("-Z", "Y").to_euler()
    camera.data.clip_end = max(distance * 4, camera.data.clip_end)

    light = bpy.data.objects["Light"]
    light.location = center + CAMERA_DIRECTION * distance * 1.5
    light.rotation_euler = look_direction.to_track_quat("-Z", "Y").to_euler()

    bpy.context.scene.render.filepath = output_path
    bpy.ops.render.render(write_still=True)
    return True


def main() -> None:
    job_list_path = get_job_list_path()
    with open(job_list_path, "r", encoding="utf-8") as f:
        jobs = json.load(f)

    setup_scene_once()

    for job in jobs:
        clear_imported_objects()
        asset_id = job["asset_id"]
        extension = job["extension"].lower()
        importer = IMPORTERS.get(extension)

        ok = False
        if importer is not None:
            try:
                importer(job["source_path"])
                mesh_objects = apply_corrections(job.get("corrections") or {})
                ok = frame_and_render(mesh_objects, job["output_path"])
            except Exception as exc:  # noqa: BLE001 - report and continue the batch
                print(f"ASSET_CATALOGUE_ERROR|{asset_id}|{exc}", flush=True)
                ok = False

        status = "ok" if ok else "fail"
        print(f"ASSET_CATALOGUE_RESULT|{asset_id}|{status}", flush=True)


main()
