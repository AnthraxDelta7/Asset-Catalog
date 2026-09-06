from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

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


def test_build_export_jobs_maps_absolute_paths_to_res_uris(tmp_path: Path) -> None:
    project_root = tmp_path
    scene_a = project_root / "Prefabs" / "SM_Box.prefab.scn"
    scene_b = project_root / "main.tscn"

    jobs, output_by_scene = godot_export._build_export_jobs(project_root, [scene_a, scene_b])

    assert jobs == [
        {"scene_path": "res://Prefabs/SM_Box.prefab.scn", "output_path": str(scene_a.with_suffix(".glb"))},
        {"scene_path": "res://main.tscn", "output_path": str(scene_b.with_suffix(".glb"))},
    ]
    assert output_by_scene == {
        "res://Prefabs/SM_Box.prefab.scn": scene_a.with_suffix(".glb"),
        "res://main.tscn": scene_b.with_suffix(".glb"),
    }


def test_parse_export_result_line_ok() -> None:
    parsed = godot_export._parse_export_result_line(
        "GODOT_EXPORT_RESULT|res://main.tscn|ok|C:/out/main.glb"
    )
    assert parsed == ("res://main.tscn", "ok", "C:/out/main.glb")


def test_parse_export_result_line_error_detail_can_contain_pipes() -> None:
    # split(..., 3) must keep everything after the 3rd "|" as one field --
    # an error message from Godot could plausibly contain its own "|".
    parsed = godot_export._parse_export_result_line(
        "GODOT_EXPORT_RESULT|res://x.tscn|error|append_from_scene failed (code 5 | see log)"
    )
    assert parsed == ("res://x.tscn", "error", "append_from_scene failed (code 5 | see log)")


def test_parse_export_result_line_ignores_unrelated_stdout_noise() -> None:
    # Godot's own startup/shutdown logging shares the same stdout stream.
    assert godot_export._parse_export_result_line("Godot Engine v4.4.stable.official") is None
    assert godot_export._parse_export_result_line("") is None


def _fake_version_output(stdout: str):
    result = MagicMock()
    result.stdout = stdout
    return result


def test_get_godot_version_parses_real_output_format() -> None:
    with patch("subprocess.run", return_value=_fake_version_output("4.4.stable.official.4c311cbee\n")):
        assert godot_export.get_godot_version(Path("godot.exe")) == (4, 4, 0)


def test_get_godot_version_parses_a_full_semver_triple() -> None:
    with patch("subprocess.run", return_value=_fake_version_output("4.2.1.stable.official\n")):
        assert godot_export.get_godot_version(Path("godot.exe")) == (4, 2, 1)


def test_get_godot_version_none_when_unparseable() -> None:
    with patch("subprocess.run", return_value=_fake_version_output("not a version string")):
        assert godot_export.get_godot_version(Path("godot.exe")) is None


def test_find_godot_prefers_an_explicit_setting_that_exists(tmp_path: Path) -> None:
    real_exe = tmp_path / "MyGodot.exe"
    real_exe.write_bytes(b"")
    with patch("shutil.which", return_value=None):
        assert godot_export.find_godot(str(real_exe)) == real_exe


def test_find_godot_falls_back_to_path_when_setting_is_missing(tmp_path: Path) -> None:
    nonexistent_setting = str(tmp_path / "does_not_exist.exe")
    with patch("shutil.which", side_effect=lambda name: "/usr/bin/godot" if name == "godot" else None):
        assert godot_export.find_godot(nonexistent_setting) == Path("/usr/bin/godot")


def test_find_godot_none_when_nothing_found() -> None:
    with patch("shutil.which", return_value=None), patch.object(Path, "is_dir", return_value=False):
        assert godot_export.find_godot(None) is None


def test_resolve_godot_reports_missing_when_not_found() -> None:
    with patch.object(godot_export, "find_godot", return_value=None):
        godot_exe, error = godot_export.resolve_godot(None)
    assert godot_exe is None
    assert "not found" in error.lower()


def test_resolve_godot_reports_unparseable_version(tmp_path: Path) -> None:
    fake_exe = tmp_path / "godot.exe"
    fake_exe.write_bytes(b"")
    with (
        patch.object(godot_export, "find_godot", return_value=fake_exe),
        patch.object(godot_export, "get_godot_version", return_value=None),
    ):
        godot_exe, error = godot_export.resolve_godot(None)
    assert godot_exe is None
    assert "could not determine" in error.lower()


def test_resolve_godot_rejects_a_too_old_version(tmp_path: Path) -> None:
    fake_exe = tmp_path / "godot.exe"
    fake_exe.write_bytes(b"")
    with (
        patch.object(godot_export, "find_godot", return_value=fake_exe),
        patch.object(godot_export, "get_godot_version", return_value=(3, 5, 0)),
    ):
        godot_exe, error = godot_export.resolve_godot(None)
    assert godot_exe is None
    assert "older than the minimum" in error.lower()


def test_resolve_godot_succeeds_for_a_supported_version(tmp_path: Path) -> None:
    fake_exe = tmp_path / "godot.exe"
    fake_exe.write_bytes(b"")
    with (
        patch.object(godot_export, "find_godot", return_value=fake_exe),
        patch.object(godot_export, "get_godot_version", return_value=(4, 4, 0)),
    ):
        godot_exe, error = godot_export.resolve_godot(None)
    assert godot_exe == fake_exe
    assert error is None
