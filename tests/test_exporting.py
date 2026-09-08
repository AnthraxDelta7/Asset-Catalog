from __future__ import annotations

import sqlite3
from pathlib import Path

from asset_catalogue import exporting, ingest

from conftest import write_texture


def test_select_assets_filters_by_asset_ids(conn: sqlite3.Connection, staging_folder: Path) -> None:
    write_texture(staging_folder, "Pack", "a.png", color=(1, 2, 3))
    write_texture(staging_folder, "Pack", "b.png", color=(4, 5, 6))
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, staging_folder / "Pack", pack_id)
    all_ids = [row["id"] for row in conn.execute("SELECT id FROM assets")]

    assert exporting.select_assets(conn, asset_ids=[]) == []
    one = exporting.select_assets(conn, asset_ids=[all_ids[0]])
    assert len(one) == 1
    assert one[0]["id"] == all_ids[0]


def test_select_assets_filters_by_pack_asset_type_and_singular_asset_id(
    conn: sqlite3.Connection, staging_folder: Path
) -> None:
    write_texture(staging_folder, "PackA", "a.png", color=(1, 2, 3))
    write_texture(staging_folder, "PackB", "b.png", color=(4, 5, 6))
    pack_a_id, _ = ingest.get_or_create_pack(conn, "PackA", "PackA", None, None, None)
    ingest.ingest_pack(conn, staging_folder / "PackA", pack_a_id)
    pack_b_id, _ = ingest.get_or_create_pack(conn, "PackB", "PackB", None, None, None)
    ingest.ingest_pack(conn, staging_folder / "PackB", pack_b_id)
    a_id = conn.execute("SELECT id FROM assets WHERE filename = 'a.png'").fetchone()["id"]

    by_pack = exporting.select_assets(conn, pack="PackA")
    assert {row["id"] for row in by_pack} == {a_id}

    by_type = exporting.select_assets(conn, asset_type="texture")
    assert len(by_type) == 2

    by_type_none = exporting.select_assets(conn, asset_type="model")
    assert by_type_none == []

    by_single_id = exporting.select_assets(conn, asset_id=a_id)
    assert {row["id"] for row in by_single_id} == {a_id}


def test_select_assets_filters_by_tag(conn: sqlite3.Connection, staging_folder: Path) -> None:
    from asset_catalogue import tagging

    write_texture(staging_folder, "Pack", "a.png", color=(1, 2, 3))
    write_texture(staging_folder, "Pack", "b.png", color=(4, 5, 6))
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, staging_folder / "Pack", pack_id)
    a_id = conn.execute("SELECT id FROM assets WHERE filename = 'a.png'").fetchone()["id"]
    tag_id = tagging.get_or_create_tag(conn, "weapons", None)
    tagging.tag_asset(conn, a_id, tag_id)

    tagged = exporting.select_assets(conn, tag="weapons")

    assert {row["id"] for row in tagged} == {a_id}


def test_export_assets_copies_files_preserving_pack_subfolder(
    conn: sqlite3.Connection, staging_folder: Path, tmp_path: Path
) -> None:
    write_texture(staging_folder, "My Pack", "a.png")
    pack_id, _ = ingest.get_or_create_pack(conn, "My Pack", "My Pack", None, None, None)
    ingest.ingest_pack(conn, staging_folder / "My Pack", pack_id)

    project_root = tmp_path / "project"
    project_root.mkdir()
    assets = exporting.select_assets(conn, asset_ids=[conn.execute("SELECT id FROM assets").fetchone()["id"]])

    messages: list[str] = []
    stats = exporting.export_assets(
        conn, staging_folder, project_root, str(project_root), "exported_assets", assets,
        on_progress=messages.append,
    )
    assert stats.copied == 1
    dest = project_root / "exported_assets" / "My Pack" / "a.png"
    assert dest.is_file()
    assert any("Exporting a.png" in m for m in messages)

    row = conn.execute("SELECT * FROM exports").fetchone()
    assert row["project_identifier"] == str(project_root)
    assert row["destination_path"] == str(dest)


def test_export_assets_sanitizes_pack_name_with_slashes(
    conn: sqlite3.Connection, staging_folder: Path, tmp_path: Path
) -> None:
    # pack_folder (the real on-disk staging subfolder) can't contain a
    # literal slash, but the pack's display `name` is a free-form DB field
    # that can -- _sanitize_folder_name is what keeps that from producing a
    # nested/broken export destination path.
    write_texture(staging_folder, "WeirdPackFolder", "a.png")
    pack_id, _ = ingest.get_or_create_pack(conn, "Weird/Pack", "WeirdPackFolder", None, None, None)
    ingest.ingest_pack(conn, staging_folder / "WeirdPackFolder", pack_id)
    project_root = tmp_path / "project"
    project_root.mkdir()
    assets = exporting.select_assets(conn)

    exporting.export_assets(conn, staging_folder, project_root, str(project_root), "exported_assets", assets)
    assert (project_root / "exported_assets" / "Weird_Pack" / "a.png").is_file()


def test_export_assets_flattens_the_packs_own_subfolder_structure(
    conn: sqlite3.Connection, staging_folder: Path, tmp_path: Path
) -> None:
    """A real pack's own internal layout (Models/, Textures/, a creator's
    own nested folders) isn't something worth reproducing underneath the
    export -- one flat folder per pack, the file directly inside it, not
    buried under whatever subfolders it happened to live in the source
    pack.
    """
    pack_root = staging_folder / "Pack"
    (pack_root / "Models" / "Nested").mkdir(parents=True)
    (pack_root / "Models" / "Nested" / "hero.glb").write_bytes(b"fake")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, pack_root, pack_id)
    project_root = tmp_path / "project"
    project_root.mkdir()
    assets = exporting.select_assets(conn)
    assert assets[0]["relative_path"] == "Models/Nested/hero.glb"  # confirms the source really is nested

    exporting.export_assets(conn, staging_folder, project_root, str(project_root), "exported_assets", assets)

    assert (project_root / "exported_assets" / "Pack" / "hero.glb").is_file()
    assert not (project_root / "exported_assets" / "Pack" / "Models").exists()


def test_export_assets_disambiguates_a_same_filename_collision(
    conn: sqlite3.Connection, staging_folder: Path, tmp_path: Path
) -> None:
    """Flattening (see the test above) can turn two assets that never
    collided in the source pack -- same filename, different subfolders --
    into a real collision at the destination. Must not silently overwrite
    one with the other.
    """
    pack_root = staging_folder / "Pack"
    (pack_root / "Props").mkdir(parents=True)
    (pack_root / "Weapons").mkdir(parents=True)
    (pack_root / "Props" / "diffuse.png").write_bytes(b"props version")
    (pack_root / "Weapons" / "diffuse.png").write_bytes(b"weapons version")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, pack_root, pack_id)
    project_root = tmp_path / "project"
    project_root.mkdir()
    assets = exporting.select_assets(conn)
    assert len(assets) == 2

    stats = exporting.export_assets(
        conn, staging_folder, project_root, str(project_root), "exported_assets", assets
    )

    assert stats.copied == 2
    pack_dir = project_root / "exported_assets" / "Pack"
    assert (pack_dir / "diffuse.png").is_file()
    assert (pack_dir / "diffuse (2).png").is_file()
    # Both files genuinely survived with their own real content -- neither
    # one silently overwritten by the other.
    contents = {(pack_dir / "diffuse.png").read_bytes(), (pack_dir / "diffuse (2).png").read_bytes()}
    assert contents == {b"props version", b"weapons version"}


def test_is_godot_export_eligible_true_for_all_godot_importable_models(
    conn: sqlite3.Connection, staging_folder: Path
) -> None:
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    pack_root = staging_folder / "Pack"
    pack_root.mkdir()
    (pack_root / "a.glb").write_bytes(b"fake")
    (pack_root / "b.fbx").write_bytes(b"fake")
    ingest.ingest_pack(conn, pack_root, pack_id)

    assets = exporting.select_assets(conn)
    assert exporting.is_godot_export_eligible(assets) is True


def test_is_godot_export_eligible_false_for_a_mixed_selection(
    conn: sqlite3.Connection, staging_folder: Path
) -> None:
    """A model plus a non-Godot-importable asset (a texture here, but the
    same reasoning applies to .stl/.blend) must never be treated as
    eligible -- see is_godot_export_eligible's own docstring for why a
    mixed selection isn't partially handled.
    """
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    pack_root = staging_folder / "Pack"
    pack_root.mkdir()
    (pack_root / "a.glb").write_bytes(b"fake")
    ingest.ingest_pack(conn, pack_root, pack_id)
    write_texture(staging_folder, "Pack", "b.png")
    ingest.ingest_pack(conn, pack_root, pack_id)

    assets = exporting.select_assets(conn)
    assert len(assets) == 2
    assert exporting.is_godot_export_eligible(assets) is False


def test_is_godot_export_eligible_true_for_a_blender_only_model_format(
    conn: sqlite3.Connection, staging_folder: Path
) -> None:
    # Godot has no built-in .stl importer, but it never sees the .stl:
    # Blender converts it to a textured .glb on the way in, so a wrapper
    # scene can be built for it after all.
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    pack_root = staging_folder / "Pack"
    pack_root.mkdir()
    (pack_root / "a.stl").write_bytes(b"fake")
    ingest.ingest_pack(conn, pack_root, pack_id)

    assets = exporting.select_assets(conn)
    assert exporting.is_godot_export_eligible(assets) is True


def test_is_godot_export_eligible_false_for_an_empty_selection() -> None:
    assert exporting.is_godot_export_eligible([]) is False


def test_plan_godot_export_copies_a_glb_straight_in(
    conn: sqlite3.Connection, staging_folder: Path, tmp_path: Path
) -> None:
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    pack_root = staging_folder / "Pack"
    pack_root.mkdir()
    (pack_root / "a.glb").write_bytes(b"fake glb bytes")
    ingest.ingest_pack(conn, pack_root, pack_id)

    project_root = tmp_path / "GodotProject"
    project_root.mkdir()
    assets = exporting.select_assets(conn)

    items = exporting.plan_godot_export(
        conn, staging_folder, project_root, str(project_root), "exported_assets", assets
    )

    expected = project_root / "exported_assets" / "Pack" / "a.glb"
    assert [item.destination for item in items] == [expected]
    assert items[0].needs_conversion is False
    # A .glb already embeds its textures, so it's copied here and now
    # rather than waiting on Blender.
    assert expected.is_file()
    # Same recording as a plain export -- the exports table doesn't
    # distinguish which export mode produced a given row.
    row = conn.execute("SELECT * FROM exports").fetchone()
    assert row["destination_path"] == str(expected)


def test_plan_godot_export_defers_a_convertible_model_and_renames_it_to_glb(
    conn: sqlite3.Connection, staging_folder: Path, tmp_path: Path
) -> None:
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    pack_root = staging_folder / "Pack"
    (pack_root / "Models").mkdir(parents=True)
    (pack_root / "Models" / "prop.fbx").write_bytes(b"fake fbx bytes")
    ingest.ingest_pack(conn, pack_root, pack_id)

    project_root = tmp_path / "GodotProject"
    project_root.mkdir()
    assets = exporting.select_assets(conn)

    items = exporting.plan_godot_export(
        conn, staging_folder, project_root, str(project_root), "exported_assets", assets
    )

    item = items[0]
    assert item.needs_conversion is True
    # Named for what it will be, not what it was.
    assert item.destination == project_root / "exported_assets" / "Pack" / "prop.glb"
    # Source stays the staging library's own copy -- converting anywhere
    # else would separate the .fbx from the textures it references.
    assert item.source == pack_root / "Models" / "prop.fbx"
    # Nothing copied and nothing recorded yet: Blender hasn't run.
    assert not item.destination.exists()
    assert not (project_root / "exported_assets" / "Pack" / "prop.fbx").exists()
    assert conn.execute("SELECT COUNT(*) AS n FROM exports").fetchone()["n"] == 0


def test_plan_godot_export_resolves_a_collision_between_fbx_and_glb_of_one_name(
    conn: sqlite3.Connection, staging_folder: Path, tmp_path: Path
) -> None:
    # prop.fbx becomes prop.glb, which would otherwise silently overwrite
    # a real prop.glb exported in the same batch.
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    pack_root = staging_folder / "Pack"
    pack_root.mkdir()
    (pack_root / "prop.fbx").write_bytes(b"fake fbx bytes")
    (pack_root / "prop.glb").write_bytes(b"fake glb bytes")
    ingest.ingest_pack(conn, pack_root, pack_id)

    project_root = tmp_path / "GodotProject"
    project_root.mkdir()
    assets = exporting.select_assets(conn)

    items = exporting.plan_godot_export(
        conn, staging_folder, project_root, str(project_root), "exported_assets", assets
    )

    destinations = {item.source.name: item.destination.name for item in items}
    assert destinations == {"prop.fbx": "prop.glb", "prop.glb": "prop (2).glb"} or destinations == {
        "prop.fbx": "prop (2).glb",
        "prop.glb": "prop.glb",
    }
    assert len({item.destination for item in items}) == 2


def test_godot_destination_name_only_renames_what_blender_converts() -> None:
    assert exporting.godot_destination_name("Models/prop.fbx") == "prop.glb"
    assert exporting.godot_destination_name("Models/prop.obj") == "prop.glb"
    assert exporting.godot_destination_name("Models/prop.stl") == "prop.glb"
    assert exporting.godot_destination_name("Models/prop.glb") == "prop.glb"
