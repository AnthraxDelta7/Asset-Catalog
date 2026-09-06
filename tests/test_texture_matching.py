from __future__ import annotations

from pathlib import Path

from asset_catalogue import texture_matching


def test_tokenize_splits_on_non_alphanumeric_and_lowercases() -> None:
    assert texture_matching.tokenize("SciFiTextures_RecessA_albedo") == ["scifitextures", "recessa", "albedo"]
    assert texture_matching.tokenize("Recess-A Trim") == ["recess", "a", "trim"]


def test_find_texture_match_finds_a_prefixed_suffixed_file() -> None:
    # Exactly the real pattern found on a downloaded low-poly sci-fi pack:
    # SciFiTextures_<MaterialName>_<maptype>.png, several map types per key.
    candidates = [
        Path("Textures/SciFiTextures_RecessA_albedo.png"),
        Path("Textures/SciFiTextures_RecessA_normal.png"),
        Path("Textures/SciFiTextures_RecessB_albedo.png"),
        Path("Textures/SciFiTextures_PavedFloorA_albedo.png"),
    ]
    match = texture_matching.find_texture_match("RecessA", candidates)
    assert match == Path("Textures/SciFiTextures_RecessA_albedo.png")


def test_find_texture_match_does_not_confuse_similarly_prefixed_keys() -> None:
    # "RecessA" must not match a file keyed "RecessB", and a material
    # named "Recess" alone must not match "RecessA" just by containing it.
    candidates = [
        Path("SciFiTextures_RecessB_albedo.png"),
        Path("SciFiTextures_RecessA_albedo.png"),
    ]
    assert texture_matching.find_texture_match("Recess", candidates) is None
    assert texture_matching.find_texture_match("RecessC", candidates) is None


def test_find_texture_match_prefers_baked_albedo_over_plain_albedo() -> None:
    candidates = [
        Path("SciFiTextures_RecessA_albedo.png"),
        Path("PrebakedAlbedo/SciFiTextures_RecessA_BakedAlbedo.png"),
    ]
    match = texture_matching.find_texture_match("RecessA", candidates)
    assert match == Path("PrebakedAlbedo/SciFiTextures_RecessA_BakedAlbedo.png")


def test_find_texture_match_ignores_normal_and_ao_only_matches() -> None:
    # A material name matching only a normal/ao/roughness/etc. map (no
    # color-type map available at all for that key) must not be used as a
    # Base Color texture -- that would look actively wrong, not just
    # imperfect, so no match should be returned here at all.
    candidates = [
        Path("SciFiTextures_RecessA_normal.png"),
        Path("SciFiTextures_RecessA_ao.png"),
        Path("SciFiTextures_RecessA_matid.png"),
    ]
    assert texture_matching.find_texture_match("RecessA", candidates) is None


def test_find_texture_match_accepts_a_bare_filename_with_no_suffix() -> None:
    # A pack that ships one plain texture per material, no PBR-map naming
    # scheme at all, is the simplest and probably most common case.
    candidates = [Path("Textures/RecessA.png"), Path("Textures/RecessB.png")]
    assert texture_matching.find_texture_match("RecessA", candidates) == Path("Textures/RecessA.png")


def test_find_texture_match_none_when_nothing_matches() -> None:
    candidates = [Path("SciFiTextures_RecessA_albedo.png")]
    assert texture_matching.find_texture_match("AlienPlantB", candidates) is None


def test_find_texture_match_none_for_empty_material_name() -> None:
    assert texture_matching.find_texture_match("", [Path("anything.png")]) is None


def test_find_texture_match_no_false_positive_on_unrelated_character_materials() -> None:
    # The real pack this was built against has ~9 material names that
    # exactly match a texture key, and ~54 others (character/plant/armor
    # parts) that legitimately have no texture counterpart at all -- none
    # of those 54 should ever accidentally match one of the 9 real keys.
    texture_files = [
        Path(f"Textures/SciFiTextures_{key}_albedo.png")
        for key in ("PavedFloorA", "PavedFloorB", "RecessA", "RecessB", "RecessC", "SciFiFloorTrim", "TiledPanelsA", "TiledPanelsB", "TiledPanelsC")
    ]
    unrelated_materials = [
        "Accent", "AlienGrass", "ArmourDark", "ArmourPrimary", "CharacterVisor",
        "DenimA", "GasMask", "HairA", "SkinColourA", "Window", "Metal1", "Earth",
        "GlowingLight", "WarningYellow", "SciFiBuildingPrimary", "SciFiBuildingSecondary",
    ]
    for name in unrelated_materials:
        assert texture_matching.find_texture_match(name, texture_files) is None, name


def test_find_file_by_basename_finds_a_relocated_file(tmp_path: Path) -> None:
    real_dir = tmp_path / "Textures" / "Nested"
    real_dir.mkdir(parents=True)
    real_file = real_dir / "AtlasColor.png"
    real_file.write_bytes(b"fake png bytes")

    found = texture_matching.find_file_by_basename("AtlasColor.png", tmp_path)
    assert found == real_file


def test_find_file_by_basename_case_insensitive(tmp_path: Path) -> None:
    real_file = tmp_path / "atlascolor.PNG"
    real_file.write_bytes(b"fake png bytes")

    assert texture_matching.find_file_by_basename("AtlasColor.png", tmp_path) == real_file


def test_find_file_by_basename_none_when_not_present(tmp_path: Path) -> None:
    (tmp_path / "unrelated.png").write_bytes(b"x")
    assert texture_matching.find_file_by_basename("AtlasColor.png", tmp_path) is None


def test_find_image_files_only_returns_recognized_image_extensions(tmp_path: Path) -> None:
    (tmp_path / "a.png").write_bytes(b"")
    (tmp_path / "b.jpg").write_bytes(b"")
    (tmp_path / "readme.txt").write_bytes(b"")
    (tmp_path / "model.fbx").write_bytes(b"")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.tga").write_bytes(b"")

    found = {p.name for p in texture_matching.find_image_files(tmp_path)}
    assert found == {"a.png", "b.jpg", "c.tga"}
