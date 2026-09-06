"""Runs inside Blender's own Python interpreter via `blender --background --python`.

Imports a model asset in its original format, applies pack corrections, and
exports it as .glb. Not part of the asset_catalogue package's normal import
graph -- bpy only exists inside Blender. See conversion.py for the
host-side half of this.
"""

import json
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))

from blender_common import IMPORTERS, apply_corrections, clear_imported_objects, get_job_list_path


def main() -> None:
    job_list_path = get_job_list_path()
    with open(job_list_path, "r", encoding="utf-8") as f:
        jobs = json.load(f)

    for job in jobs:
        clear_imported_objects()
        asset_id = job["asset_id"]
        extension = job["extension"].lower()
        importer = IMPORTERS.get(extension)

        ok = False
        if importer is not None:
            try:
                importer(job["source_path"])
                pack_root = job.get("pack_root")
                _mesh_objects, _has_broken_texture, smart_texture_notes = apply_corrections(
                    job.get("corrections") or {},
                    Path(pack_root) if pack_root else None,
                )
                for note in smart_texture_notes:
                    print(f"ASSET_CATALOGUE_CONVERT_SMART_TEXTURE|{asset_id}|{note}", flush=True)
                bpy.ops.export_scene.gltf(filepath=job["output_path"], export_format="GLB")
                ok = True
            except Exception as exc:  # noqa: BLE001 - report and continue the batch
                print(f"ASSET_CATALOGUE_CONVERT_ERROR|{asset_id}|{exc}", flush=True)
                ok = False

        status = "ok" if ok else "fail"
        print(f"ASSET_CATALOGUE_CONVERT_RESULT|{asset_id}|{status}", flush=True)


main()
