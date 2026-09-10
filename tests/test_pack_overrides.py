"""A pack shipping its own corrections file, and the rule that stops a
prop being catalogued twice once its scene has been extracted.

Both exist because of what real Unity-converted Synty packs do: ship
`.gltf` stubs whose baked-in texture URI names an atlas from a different
product, alongside extracted `.glb` copies of the same props.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from asset_catalogue import ingest, pack_overrides


def _pack(tmp_path: Path) -> Path:
    root = tmp_path / "POLY_Pack"
    (root / "Textures").mkdir(parents=True)
    (root / "Textures" / "Polygon_Texture_vol3.png").write_bytes(b"atlas")
    return root


# -- the override file ------------------------------------------------


def test_absent_override_file_is_not_a_problem(tmp_path: Path) -> None:
    """The overwhelmingly common case. Reporting it would train people to
    ignore the report."""
    corrections, problems = pack_overrides.read(_pack(tmp_path))
    assert corrections == {}
    assert problems == []


def test_override_file_supplies_texture_overrides(tmp_path: Path) -> None:
    root = _pack(tmp_path)
    pack_overrides.path_for(root).write_text(
        json.dumps({"texture_overrides": {"main": "Textures/Polygon_Texture_vol3.png"}}),
        encoding="utf-8",
    )
    corrections, problems = pack_overrides.read(root)
    assert problems == []
    assert corrections["texture_overrides"] == {"main": "Textures/Polygon_Texture_vol3.png"}


def test_override_file_rejects_paths_that_escape_the_pack(tmp_path: Path) -> None:
    """A pack folder is wherever a downloaded archive was unpacked, so
    this file is attacker-influenced. Texture paths are resolved as
    pack_root / value at render time, which an absolute path or a `..`
    would turn into a read of anything on disk.
    """
    root = _pack(tmp_path)
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"no")
    pack_overrides.path_for(root).write_text(
        json.dumps(
            {
                "texture_overrides": {
                    "a": "../secret.png",
                    "b": str(outside),
                    "ok": "Textures/Polygon_Texture_vol3.png",
                }
            }
        ),
        encoding="utf-8",
    )
    corrections, problems = pack_overrides.read(root)
    assert corrections["texture_overrides"] == {"ok": "Textures/Polygon_Texture_vol3.png"}
    assert len(problems) == 2
    assert all("relative path inside the pack" in p for p in problems)


def test_override_file_reports_a_missing_texture_and_an_unknown_key(tmp_path: Path) -> None:
    """Hand-edited, so a typo is likely and silence would leave someone
    staring at a correction that never applied."""
    root = _pack(tmp_path)
    pack_overrides.path_for(root).write_text(
        json.dumps(
            {"texture_overrides": {"main": "Textures/nope.png"}, "textrue_overrides": {}}
        ),
        encoding="utf-8",
    )
    corrections, problems = pack_overrides.read(root)
    assert "texture_overrides" not in corrections
    assert any("does not exist" in p for p in problems)
    assert any("unknown key" in p for p in problems)


def test_malformed_override_file_reports_rather_than_raises(tmp_path: Path) -> None:
    root = _pack(tmp_path)
    pack_overrides.path_for(root).write_text("{not json", encoding="utf-8")
    corrections, problems = pack_overrides.read(root)
    assert corrections == {}
    assert len(problems) == 1


def test_merge_keeps_ui_fixes_the_file_does_not_mention(tmp_path: Path) -> None:
    """The file is the deliberate statement and re-asserts itself on every
    re-ingest, but it is usually partial -- clobbering a pack's whole
    accumulated set of UI fixes because it names one material would make
    shipping one a trap.
    """
    existing = {
        "texture_overrides": {"main": "old.png", "Glass": "glass.png"},
        "up_axis": "Y_UP",
    }
    from_file = {"texture_overrides": {"main": "Textures/Polygon_Texture_vol3.png"}}
    merged = pack_overrides.merge(existing, from_file)
    assert merged["texture_overrides"] == {
        "main": "Textures/Polygon_Texture_vol3.png",
        "Glass": "glass.png",
    }
    assert merged["up_axis"] == "Y_UP"


# -- superseded source models -----------------------------------------


def _unity_style_pack(tmp_path: Path) -> tuple[Path, list[Path]]:
    """The real shape: raw source models under Models/, and extracted
    scenes plus their exported .glb under Prefabs/.
    """
    root = tmp_path / "POLY_Pack"
    models, prefabs = root / "Models", root / "Prefabs"
    models.mkdir(parents=True)
    prefabs.mkdir(parents=True)
    # Distinct bytes per file: content_hash is UNIQUE, so identical
    # placeholder content would dedupe and the test would be measuring
    # that instead of the rule under test.
    for name in ("SM_Wheat_01_A", "SM_Anvil_01"):
        (models / f"{name}.gltf").write_bytes(f"stub {name}".encode())
        # godot_export writes scene_path.with_suffix(".glb"), so the
        # export inherits the scene's whole multi-dot name.
        (prefabs / f"{name}.prefab.scn").write_bytes(f"scene {name}".encode())
        (prefabs / f"{name}.prefab.glb").write_bytes(f"glb {name}".encode())
    (models / "OceanPlane.gltf").write_bytes(b"lonely ocean")
    files = sorted(p for p in root.rglob("*") if p.is_file())
    return root, files


def test_source_model_is_superseded_by_an_extracted_scene_of_the_same_name(
    tmp_path: Path,
) -> None:
    root, files = _unity_style_pack(tmp_path)
    superseded = ingest.find_superseded_source_models(root, files)
    assert {p.name for p in superseded} == {"SM_Wheat_01_A.gltf", "SM_Anvil_01.gltf"}


def test_a_source_model_with_no_extracted_twin_survives(tmp_path: Path) -> None:
    """The rule drops redundancy, never coverage -- an asset that only
    exists as a source model is the only copy there is."""
    root, files = _unity_style_pack(tmp_path)
    superseded = ingest.find_superseded_source_models(root, files)
    assert not any(p.name == "OceanPlane.gltf" for p in superseded)


def test_a_glb_without_a_scene_twin_supersedes_nothing(tmp_path: Path) -> None:
    """Otherwise a pack genuinely shipping Prop.glb next to Prop.fbx as
    alternate formats would lose the .fbx here, rather than through the
    format selection prompt where the user actually gets a say.
    """
    root = tmp_path / "Vendor"
    root.mkdir()
    (root / "Prop.glb").write_bytes(b"glb")
    (root / "Prop.fbx").write_bytes(b"fbx")
    files = sorted(p for p in root.rglob("*") if p.is_file())
    assert ingest.find_superseded_source_models(root, files) == set()


def test_ingest_skips_superseded_models_and_counts_them(tmp_path: Path) -> None:
    from asset_catalogue import db

    root, _ = _unity_style_pack(tmp_path)
    conn = db.connect(tmp_path / "catalogue.db")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "POLY_Pack", None, None, None)
    stats = ingest.ingest_pack(conn, root, pack_id)
    catalogued = {
        row["filename"]
        for row in conn.execute("SELECT filename FROM assets WHERE deleted_at IS NULL")
    }
    assert "SM_Wheat_01_A.prefab.glb" in catalogued
    assert "SM_Wheat_01_A.gltf" not in catalogued
    assert "OceanPlane.gltf" in catalogued
    assert stats.skipped_superseded_models == 2
    conn.close()


def test_prefer_source_models_keeps_both_copies(tmp_path: Path) -> None:
    """Opt-in, for when the source models are the better copy -- their
    textures are the vendor's originals rather than Godot's recompressed
    ones. It costs an extra entry per prop, hence not the default.
    """
    from asset_catalogue import db

    root, _ = _unity_style_pack(tmp_path)
    conn = db.connect(tmp_path / "catalogue.db")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "POLY_Pack", None, None, None)
    stats = ingest.ingest_pack(conn, root, pack_id, prefer_source_models=True)
    catalogued = {
        row["filename"]
        for row in conn.execute("SELECT filename FROM assets WHERE deleted_at IS NULL")
    }
    assert "SM_Wheat_01_A.gltf" in catalogued
    assert "SM_Wheat_01_A.prefab.glb" in catalogued
    assert stats.skipped_superseded_models == 0
    conn.close()
