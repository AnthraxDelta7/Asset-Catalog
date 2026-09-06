from __future__ import annotations

from pathlib import Path

from asset_catalogue import godot_export


def test_find_godot_project_roots_finds_root_and_nested_projects(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("")
    nested = tmp_path / "DemoSubproject"
    nested.mkdir()
    (nested / "project.godot").write_text("")
    (tmp_path / "NotAProject").mkdir()

    roots = godot_export.find_godot_project_roots(tmp_path)
    assert set(roots) == {tmp_path, nested}


def test_find_godot_project_roots_empty_when_none_found(tmp_path: Path) -> None:
    (tmp_path / "SomeFolder").mkdir()
    assert godot_export.find_godot_project_roots(tmp_path) == []


def test_find_scenes_finds_tscn_files_recursively(tmp_path: Path) -> None:
    (tmp_path / "main.tscn").write_text("")
    sub = tmp_path / "scenes"
    sub.mkdir()
    (sub / "level1.tscn").write_text("")
    (tmp_path / "not_a_scene.gd").write_text("")

    scenes = godot_export.find_scenes(tmp_path)
    assert set(scenes) == {tmp_path / "main.tscn", sub / "level1.tscn"}


def test_find_scenes_also_finds_binary_scn_files(tmp_path: Path) -> None:
    """A marketplace pack converted from another engine commonly ships
    Godot's compressed-binary .scn format exclusively, with no .tscn at
    all -- confirmed against a real Synty POLYGON pack where a .tscn-only
    search silently found nothing to export.
    """
    (tmp_path / "prefab.scn").write_bytes(b"RSCC")
    scenes = godot_export.find_scenes(tmp_path)
    assert scenes == [tmp_path / "prefab.scn"]


def test_find_scenes_excludes_the_godot_reimport_cache(tmp_path: Path) -> None:
    """.godot/imported/ mirrors every real scene as its own auto-generated,
    hash-named .scn -- not real content. Confirmed against the same real
    pack: 200 of 305 files a naive rglob found were exactly this cache.
    """
    (tmp_path / "real.tscn").write_text("")
    cache_dir = tmp_path / ".godot" / "imported"
    cache_dir.mkdir(parents=True)
    (cache_dir / "SomeAsset.gltf-abc123.scn").write_bytes(b"RSCC")

    scenes = godot_export.find_scenes(tmp_path)
    assert scenes == [tmp_path / "real.tscn"]


def test_has_real_geometry_true_for_a_real_mesh(tmp_path: Path) -> None:
    import trimesh

    box = trimesh.creation.box(extents=(1, 1, 1))
    glb_path = tmp_path / "box.glb"
    box.export(glb_path)

    assert godot_export._has_real_geometry(glb_path) is True


def test_has_real_geometry_false_for_an_empty_scene(tmp_path: Path) -> None:
    # Mirrors what a real Godot export of a mesh-less scene (UI, autoloads,
    # marker-only nodes) actually produces: a valid but geometry-less glb,
    # confirmed against a real Godot 4.4 headless run before writing this
    # module -- trimesh drops a zero-vertex mesh entirely on export, so the
    # reloaded scene has no geometry at all, same as the real file.
    import numpy as np
    import trimesh

    zero_vertex_mesh = trimesh.Trimesh(vertices=np.zeros((0, 3)), faces=np.zeros((0, 3), dtype=int))
    glb_path = tmp_path / "empty.glb"
    trimesh.Scene([zero_vertex_mesh]).export(glb_path)

    assert godot_export._has_real_geometry(glb_path) is False


def test_has_real_geometry_false_for_a_missing_or_corrupt_file(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.glb"
    assert godot_export._has_real_geometry(missing) is False

    corrupt = tmp_path / "corrupt.glb"
    corrupt.write_bytes(b"not actually a glb")
    assert godot_export._has_real_geometry(corrupt) is False
