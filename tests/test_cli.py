from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from asset_catalogue import cli, conversion, ingest, settings, tagging
from asset_catalogue.cli import (
    _get_asset_id,
    _get_pack_id,
    _get_tag_id,
    _parse_only_formats,
    build_parser,
)

from conftest import make_pack, write_texture

# ---------------------------------------------------------------------------
# _parse_only_formats
# ---------------------------------------------------------------------------


def test_parse_only_formats_none_when_omitted() -> None:
    assert _parse_only_formats(None) is None
    assert _parse_only_formats("") is None


def test_parse_only_formats_single_extension_normalizes_dot_and_case() -> None:
    assert _parse_only_formats("glb") == {".glb"}
    assert _parse_only_formats(".GLB") == {".glb"}


def test_parse_only_formats_comma_separated_tolerates_whitespace() -> None:
    assert _parse_only_formats(" fbx , .GLB ") == {".fbx", ".glb"}


# ---------------------------------------------------------------------------
# _get_pack_id / _get_asset_id / _get_tag_id -- the shared "not found" guards
# every subcommand taking a pack/asset/tag argument goes through
# ---------------------------------------------------------------------------


def test_get_pack_id_raises_for_unknown_name(cli_env) -> None:
    conn, _staging = cli_env
    with pytest.raises(SystemExit, match="No such pack"):
        _get_pack_id(conn, "Nonexistent")


def test_get_pack_id_returns_id_for_known_pack(cli_env) -> None:
    conn, staging = cli_env
    pack_id = make_pack(conn, staging, "Pack")
    assert _get_pack_id(conn, "Pack") == pack_id


def test_get_asset_id_raises_for_unknown_id(cli_env) -> None:
    conn, _staging = cli_env
    with pytest.raises(SystemExit, match="No such asset id"):
        _get_asset_id(conn, 999)


def test_get_tag_id_raises_for_unknown_name(cli_env) -> None:
    conn, _staging = cli_env
    with pytest.raises(SystemExit, match="No such tag"):
        _get_tag_id(conn, "nonexistent")


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------


def test_cmd_settings_set_updates_only_given_fields(cli_env, monkeypatch) -> None:
    _conn, staging = cli_env
    settings.save(settings.Settings(staging_folder=str(staging), library_folder="B", blender_path="old-blender"))

    cli.cmd_settings_set(argparse.Namespace(staging_folder=None, library_folder="C", blender_path=None, godot_path=None))

    loaded = settings.load()
    assert loaded.staging_folder == str(staging)  # untouched
    assert loaded.library_folder == "C"  # updated
    assert loaded.blender_path == "old-blender"  # untouched


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------


def test_cmd_ingest_raises_without_staging_folder(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    settings.save(settings.Settings(staging_folder=None, library_folder=str(tmp_path / "library")))

    with pytest.raises(SystemExit, match="No staging folder configured"):
        cli.cmd_ingest(argparse.Namespace(pack_folder="Pack", pack_name="Pack", creator=None, licence=None, source_url=None, only_formats=None))


def test_cmd_ingest_raises_for_missing_pack_folder(cli_env) -> None:
    _conn, staging = cli_env
    with pytest.raises(SystemExit, match="Pack folder not found"):
        cli.cmd_ingest(argparse.Namespace(pack_folder="DoesNotExist", pack_name="Pack", creator=None, licence=None, source_url=None, only_formats=None))


def test_cmd_ingest_from_a_plain_folder(cli_env, capsys) -> None:
    conn, staging = cli_env
    write_texture(staging, "Pack", "a.png")

    cli.cmd_ingest(argparse.Namespace(pack_folder="Pack", pack_name="Pack", creator="Creator", licence=None, source_url=None, only_formats=None))

    assert conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 1
    assert "Ingested 'Pack': 1 new" in capsys.readouterr().out


def test_cmd_ingest_extracts_a_zip_source_directly_in_staging(cli_env, capsys) -> None:
    """A folder argument that turns out to be a .zip sitting directly in
    staging is extracted transparently, same as the UI's own zip-source
    handling (Catalogue._resolve_pack_root) -- this is cli.py's own,
    separate implementation of that same idea, so it's tested separately.
    """
    import zipfile

    conn, staging = cli_env
    zip_path = staging / "Pack.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("a.png", b"fake png bytes")

    cli.cmd_ingest(argparse.Namespace(pack_folder="Pack.zip", pack_name="Pack", creator=None, licence=None, source_url=None, only_formats=None))

    assert (staging / "Pack" / "a.png").is_file()
    assert "Extracted 'Pack.zip'" in capsys.readouterr().out
    assert conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 1


def test_cmd_ingest_does_not_re_extract_an_already_extracted_zip_destination(cli_env, capsys) -> None:
    import zipfile

    conn, staging = cli_env
    zip_path = staging / "Pack.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("a.png", b"fake png bytes")
    cli.cmd_ingest(argparse.Namespace(pack_folder="Pack.zip", pack_name="Pack", creator=None, licence=None, source_url=None, only_formats=None))
    capsys.readouterr()

    cli.cmd_ingest(argparse.Namespace(pack_folder="Pack.zip", pack_name="Pack", creator=None, licence=None, source_url=None, only_formats=None))

    assert "already exists -- ingesting from it as-is" in capsys.readouterr().out


def test_cmd_ingest_zip_raises_for_missing_zip_file(cli_env, tmp_path: Path) -> None:
    _conn, _staging = cli_env
    with pytest.raises(SystemExit, match="Zip file not found"):
        cli.cmd_ingest_zip(argparse.Namespace(
            zip_path=str(tmp_path / "nope.zip"), pack_folder=None, pack_name="Pack",
            creator=None, licence=None, source_url=None, only_formats=None,
        ))


# ---------------------------------------------------------------------------
# tag / untag
# ---------------------------------------------------------------------------


def test_cmd_tag_asset_and_untag_asset_round_trip(cli_env, capsys) -> None:
    conn, staging = cli_env
    pack_id = make_pack(conn, staging, "Pack")
    cursor = conn.execute(
        "INSERT INTO assets (pack_id, relative_path, filename, extension, file_size, "
        "content_hash, asset_type) VALUES (?, 'a.png', 'a.png', '.png', 1, 'h1', 'texture')",
        (pack_id,),
    )
    conn.commit()
    asset_id = cursor.lastrowid

    cli.cmd_tag_asset(argparse.Namespace(asset_id=asset_id, tag_name="weapons", category=None))
    assert "Tagged asset" in capsys.readouterr().out
    assert conn.execute("SELECT 1 FROM asset_tags").fetchone() is not None

    cli.cmd_untag_asset(argparse.Namespace(asset_id=asset_id, tag_name="weapons"))
    assert "Removed 'weapons'" in capsys.readouterr().out
    assert conn.execute("SELECT 1 FROM asset_tags").fetchone() is None


def test_cmd_untag_asset_reports_when_tag_was_not_present(cli_env, capsys) -> None:
    conn, staging = cli_env
    pack_id = make_pack(conn, staging, "Pack")
    cursor = conn.execute(
        "INSERT INTO assets (pack_id, relative_path, filename, extension, file_size, "
        "content_hash, asset_type) VALUES (?, 'a.png', 'a.png', '.png', 1, 'h1', 'texture')",
        (pack_id,),
    )
    conn.commit()
    tagging.get_or_create_tag(conn, "weapons", None)

    cli.cmd_untag_asset(argparse.Namespace(asset_id=cursor.lastrowid, tag_name="weapons"))

    assert "did not have tag" in capsys.readouterr().out


def test_cmd_tag_delete_aborts_without_yes_when_declined(cli_env, monkeypatch, capsys) -> None:
    conn, _staging = cli_env
    tag_id = tagging.get_or_create_tag(conn, "weapons", None)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")

    cli.cmd_tag_delete(argparse.Namespace(tag_name="weapons", yes=False))

    assert "Aborted" in capsys.readouterr().out
    assert conn.execute("SELECT 1 FROM tags WHERE id = ?", (tag_id,)).fetchone() is not None


def test_cmd_tag_delete_with_yes_skips_the_prompt(cli_env, capsys) -> None:
    conn, _staging = cli_env
    tag_id = tagging.get_or_create_tag(conn, "weapons", None)

    cli.cmd_tag_delete(argparse.Namespace(tag_name="weapons", yes=True))

    assert "Deleted 'weapons'" in capsys.readouterr().out
    assert conn.execute("SELECT 1 FROM tags WHERE id = ?", (tag_id,)).fetchone() is None


# ---------------------------------------------------------------------------
# convert
# ---------------------------------------------------------------------------


def _make_model_asset(conn: sqlite3.Connection, staging: Path, pack_name: str = "Pack") -> int:
    pack_id = make_pack(conn, staging, pack_name)
    cursor = conn.execute(
        "INSERT INTO assets (pack_id, relative_path, filename, extension, file_size, "
        "content_hash, asset_type) VALUES (?, 'model.fbx', 'model.fbx', '.fbx', 1, ?, 'model')",
        (pack_id, f"hash-{pack_name}-{id(pack_name)}"),
    )
    conn.commit()
    return cursor.lastrowid


def test_cmd_convert_to_gltf_raises_for_unknown_asset_id_before_touching_blender(cli_env) -> None:
    _conn, _staging = cli_env
    with (
        patch.object(cli.blender_render, "resolve_blender", return_value=(Path("blender.exe"), None)),
        patch.object(cli.conversion, "convert_asset_to_gltf") as mock_convert,
    ):
        with pytest.raises(SystemExit, match="No such asset id"):
            cli.cmd_convert_to_gltf(argparse.Namespace(asset_id=[999]))
    mock_convert.assert_not_called()


def test_cmd_convert_to_gltf_single_asset_dispatches_to_single_conversion(cli_env, capsys) -> None:
    conn, staging = cli_env
    asset_id = _make_model_asset(conn, staging)

    with (
        patch.object(cli.blender_render, "resolve_blender", return_value=(Path("blender.exe"), None)),
        patch.object(cli.conversion, "convert_asset_to_gltf", return_value=conversion.ConversionResult(True)) as mock_convert,
        patch.object(cli.blender_render, "generate_model_thumbnails") as mock_thumbs,
    ):
        cli.cmd_convert_to_gltf(argparse.Namespace(asset_id=[asset_id]))

    mock_convert.assert_called_once()
    assert mock_convert.call_args.args[4] == asset_id
    mock_thumbs.assert_called_once()
    assert f"Converted asset {asset_id} to .glb" in capsys.readouterr().out


def test_cmd_convert_to_gltf_single_asset_failure_raises_system_exit(cli_env) -> None:
    conn, staging = cli_env
    asset_id = _make_model_asset(conn, staging)

    with (
        patch.object(cli.blender_render, "resolve_blender", return_value=(Path("blender.exe"), None)),
        patch.object(cli.conversion, "convert_asset_to_gltf", return_value=conversion.ConversionResult(False, "boom")),
    ):
        with pytest.raises(SystemExit, match="Conversion failed: boom"):
            cli.cmd_convert_to_gltf(argparse.Namespace(asset_id=[asset_id]))


def test_cmd_convert_to_gltf_multiple_assets_dispatches_to_batch_conversion(cli_env) -> None:
    conn, staging = cli_env
    a1 = _make_model_asset(conn, staging, "PackA")
    a2 = _make_model_asset(conn, staging, "PackB")

    with (
        patch.object(cli.blender_render, "resolve_blender", return_value=(Path("blender.exe"), None)),
        patch.object(
            cli.conversion, "convert_assets_to_gltf",
            return_value=conversion.ConversionBatchResult(converted=2, converted_asset_ids=[a1, a2]),
        ) as mock_batch,
        patch.object(cli.blender_render, "generate_model_thumbnails") as mock_thumbs,
    ):
        cli.cmd_convert_to_gltf(argparse.Namespace(asset_id=[a1, a2]))

    mock_batch.assert_called_once()
    assert mock_batch.call_args.args[4] == [a1, a2]
    mock_thumbs.assert_called_once()


def test_cmd_convert_flagged_reports_when_nothing_needs_conversion(cli_env, capsys) -> None:
    _conn, _staging = cli_env
    with patch.object(cli.blender_render, "resolve_blender", return_value=(Path("blender.exe"), None)):
        cli.cmd_convert_flagged(argparse.Namespace(yes=True))
    assert "No assets currently need conversion" in capsys.readouterr().out


def test_cmd_convert_flagged_aborts_without_yes_when_declined(cli_env, monkeypatch, capsys) -> None:
    conn, staging = cli_env
    asset_id = _make_model_asset(conn, staging)
    conn.execute("UPDATE assets SET needs_glb_conversion = 1 WHERE id = ?", (asset_id,))
    conn.commit()
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")

    with (
        patch.object(cli.blender_render, "resolve_blender", return_value=(Path("blender.exe"), None)),
        patch.object(cli.conversion, "convert_assets_to_gltf") as mock_batch,
    ):
        cli.cmd_convert_flagged(argparse.Namespace(yes=False))

    mock_batch.assert_not_called()
    assert "Aborted" in capsys.readouterr().out


def test_cmd_convert_revert_raises_when_nothing_pending(cli_env) -> None:
    conn, staging = cli_env
    asset_id = _make_model_asset(conn, staging)
    with pytest.raises(SystemExit, match="no pending conversion to revert"):
        cli.cmd_convert_revert(argparse.Namespace(asset_id=asset_id))


def test_cmd_convert_cleanup_all_reports_when_nothing_pending(cli_env, capsys) -> None:
    _conn, _staging = cli_env
    cli.cmd_convert_cleanup_all(argparse.Namespace(yes=True))
    assert "No pending conversions to clean up" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# pack
# ---------------------------------------------------------------------------


def test_cmd_pack_show_corrections_with_none_set(cli_env, capsys) -> None:
    conn, staging = cli_env
    make_pack(conn, staging, "Pack")
    cli.cmd_pack_show_corrections(argparse.Namespace(pack_name="Pack"))
    assert "has no corrections set" in capsys.readouterr().out


def test_cmd_pack_set_corrections_raises_with_no_flags_given(cli_env) -> None:
    conn, staging = cli_env
    make_pack(conn, staging, "Pack")
    args = argparse.Namespace(
        pack_name="Pack", clear=False, up_axis=None, scale=None, material_fallback=None,
        broken_texture_fallback=None, disable_smart_texture_matching=None,
        texture_override=None, clear_texture_overrides=False,
    )
    with pytest.raises(SystemExit, match="No corrections given"):
        cli.cmd_pack_set_corrections(args)


def test_cmd_pack_set_corrections_rejects_malformed_texture_override(cli_env) -> None:
    conn, staging = cli_env
    make_pack(conn, staging, "Pack")
    args = argparse.Namespace(
        pack_name="Pack", clear=False, up_axis=None, scale=None, material_fallback=None,
        broken_texture_fallback=None, disable_smart_texture_matching=None,
        texture_override=["not-a-key-value-pair"], clear_texture_overrides=False,
    )
    with pytest.raises(SystemExit, match=r"MATERIAL=PATH"):
        cli.cmd_pack_set_corrections(args)


def test_cmd_pack_set_corrections_sets_and_clears(cli_env) -> None:
    from asset_catalogue import packs

    conn, staging = cli_env
    pack_id = make_pack(conn, staging, "Pack")

    cli.cmd_pack_set_corrections(argparse.Namespace(
        pack_name="Pack", clear=False, up_axis="Y_UP", scale=2.0, material_fallback=None,
        broken_texture_fallback=None, disable_smart_texture_matching=True,
        texture_override=["RecessA=Textures/a.png"], clear_texture_overrides=False,
    ))
    corrections = packs.get_corrections(conn, pack_id)
    assert corrections["up_axis"] == "Y_UP"
    assert corrections["scale"] == 2.0
    assert corrections["disable_smart_texture_matching"] is True
    assert corrections["texture_overrides"] == {"RecessA": "Textures/a.png"}

    cli.cmd_pack_set_corrections(argparse.Namespace(
        pack_name="Pack", clear=True, up_axis=None, scale=None, material_fallback=None,
        broken_texture_fallback=None, disable_smart_texture_matching=None,
        texture_override=None, clear_texture_overrides=False,
    ))
    assert packs.get_corrections(conn, pack_id) == {}


def test_cmd_pack_set_metadata_clear_flags(cli_env) -> None:
    conn, staging = cli_env
    pack_id = make_pack(conn, staging, "Pack")
    cli.cmd_pack_set_metadata(argparse.Namespace(
        pack_name="Pack", creator="Creator", licence="MIT", source_url="https://example.com",
        clear_creator=False, clear_licence=False, clear_source_url=False,
    ))

    cli.cmd_pack_set_metadata(argparse.Namespace(
        pack_name="Pack", creator=None, licence=None, source_url=None,
        clear_creator=False, clear_licence=True, clear_source_url=False,
    ))

    row = conn.execute("SELECT creator, licence, source_url FROM packs WHERE id = ?", (pack_id,)).fetchone()
    assert row["creator"] == "Creator"  # untouched
    assert row["licence"] is None  # cleared
    assert row["source_url"] == "https://example.com"  # untouched


def test_cmd_pack_notes_clear_flags(cli_env) -> None:
    conn, staging = cli_env
    pack_id = make_pack(conn, staging, "Pack")
    cli.cmd_pack_notes(argparse.Namespace(pack_name="Pack", notes="Great pack", rating=4, clear_notes=False, clear_rating=False))

    cli.cmd_pack_notes(argparse.Namespace(pack_name="Pack", notes=None, rating=None, clear_notes=True, clear_rating=False))

    row = conn.execute("SELECT notes, rating FROM packs WHERE id = ?", (pack_id,)).fetchone()
    assert row["notes"] is None
    assert row["rating"] == 4  # untouched


def test_cmd_pack_rename_raises_on_collision(cli_env) -> None:
    conn, staging = cli_env
    make_pack(conn, staging, "Taken")
    make_pack(conn, staging, "Mine")
    with pytest.raises(SystemExit, match="already exists"):
        cli.cmd_pack_rename(argparse.Namespace(pack_name="Mine", new_name="Taken"))


def test_cmd_pack_remove_aborts_without_yes_when_declined(cli_env, monkeypatch, capsys) -> None:
    conn, staging = cli_env
    make_pack(conn, staging, "Pack")
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")

    cli.cmd_pack_remove(argparse.Namespace(pack_name="Pack", yes=False))

    assert "Aborted" in capsys.readouterr().out
    assert conn.execute("SELECT 1 FROM packs WHERE name = 'Pack'").fetchone() is not None


def test_cmd_pack_remove_with_yes_skips_the_prompt(cli_env, capsys) -> None:
    conn, staging = cli_env
    make_pack(conn, staging, "Pack")

    cli.cmd_pack_remove(argparse.Namespace(pack_name="Pack", yes=True))

    assert "Removed 'Pack'" in capsys.readouterr().out
    assert conn.execute("SELECT 1 FROM packs WHERE name = 'Pack'").fetchone() is None


# ---------------------------------------------------------------------------
# export / remove -- the "refuse unfiltered" guards
# ---------------------------------------------------------------------------


def test_cmd_export_refuses_when_unfiltered(cli_env) -> None:
    _conn, _staging = cli_env
    args = argparse.Namespace(pack=None, type=None, tag=None, asset_id=None, all=False, project_root=".")
    with pytest.raises(SystemExit, match="Refusing to export"):
        cli.cmd_export(args)


def test_cmd_export_raises_for_missing_project_root(cli_env, tmp_path: Path) -> None:
    _conn, _staging = cli_env
    args = argparse.Namespace(
        pack="Pack", type=None, tag=None, asset_id=None, all=False,
        project_root=str(tmp_path / "nonexistent"), dest_subfolder="exported_assets",
    )
    with pytest.raises(SystemExit, match="Project folder not found"):
        cli.cmd_export(args)


def test_cmd_remove_refuses_when_unfiltered(cli_env) -> None:
    _conn, _staging = cli_env
    args = argparse.Namespace(asset_id=None, pack=None, type=None, tag=None, all=False, yes=False)
    with pytest.raises(SystemExit, match="Refusing to remove"):
        cli.cmd_remove(args)


def test_cmd_remove_aborts_without_yes_when_declined(cli_env, monkeypatch, capsys) -> None:
    conn, staging = cli_env
    write_texture(staging, "Pack", "a.png")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, staging / "Pack", pack_id)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")

    cli.cmd_remove(argparse.Namespace(asset_id=None, pack="Pack", type=None, tag=None, all=False, yes=False))

    assert "Aborted" in capsys.readouterr().out
    assert conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 1


def test_cmd_remove_with_yes_skips_the_prompt(cli_env, capsys) -> None:
    conn, staging = cli_env
    write_texture(staging, "Pack", "a.png")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, staging / "Pack", pack_id)

    cli.cmd_remove(argparse.Namespace(asset_id=None, pack="Pack", type=None, tag=None, all=False, yes=True))

    assert "Removed 1 asset(s)" in capsys.readouterr().out
    assert conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# trash
# ---------------------------------------------------------------------------


def test_cmd_trash_restore_with_no_asset_id_restores_everything(cli_env, capsys) -> None:
    conn, staging = cli_env
    write_texture(staging, "Pack", "a.png", color=(1, 2, 3))
    write_texture(staging, "Pack", "b.png", color=(4, 5, 6))
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, staging / "Pack", pack_id)
    asset_ids = [row["id"] for row in conn.execute("SELECT id FROM assets")]
    cli.cmd_trash_move(argparse.Namespace(asset_id=asset_ids))
    capsys.readouterr()

    cli.cmd_trash_restore(argparse.Namespace(asset_id=None))

    assert "Restored 2 asset(s)" in capsys.readouterr().out
    assert conn.execute("SELECT COUNT(*) FROM assets WHERE deleted_at IS NOT NULL").fetchone()[0] == 0


def test_cmd_trash_empty_reports_when_trash_is_empty(cli_env, capsys) -> None:
    _conn, _staging = cli_env
    cli.cmd_trash_empty(argparse.Namespace(asset_id=None, yes=True))
    assert "Trash is empty" in capsys.readouterr().out


def test_cmd_trash_empty_aborts_without_yes_when_declined(cli_env, monkeypatch, capsys) -> None:
    conn, staging = cli_env
    write_texture(staging, "Pack", "a.png")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, staging / "Pack", pack_id)
    asset_id = conn.execute("SELECT id FROM assets").fetchone()[0]
    cli.cmd_trash_move(argparse.Namespace(asset_id=[asset_id]))
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")

    cli.cmd_trash_empty(argparse.Namespace(asset_id=None, yes=False))

    assert "Aborted" in capsys.readouterr().out
    assert conn.execute("SELECT 1 FROM assets WHERE id = ?", (asset_id,)).fetchone() is not None


# ---------------------------------------------------------------------------
# check --fix
# ---------------------------------------------------------------------------


def test_cmd_check_fix_calls_the_repair_functions_for_flagged_issues(cli_env, capsys) -> None:
    from asset_catalogue import library_health

    conn, staging = cli_env
    write_texture(staging, "Pack", "a.png")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, staging / "Pack", pack_id)
    asset_id = conn.execute("SELECT id FROM assets").fetchone()[0]
    conn.execute("UPDATE assets SET thumbnail_status = 'done' WHERE id = ?", (asset_id,))
    conn.commit()
    # A "done" thumbnail with no actual file on disk -- MISSING_THUMBNAIL_FILE.

    with patch.object(library_health, "reset_broken_thumbnails", return_value=1) as mock_reset:
        cli.cmd_check(argparse.Namespace(fix=True))

    mock_reset.assert_called_once()
    assert "Reset 1 broken thumbnail" in capsys.readouterr().out


def test_cmd_check_reports_no_issues_for_a_clean_library(cli_env, capsys) -> None:
    conn, staging = cli_env
    write_texture(staging, "Pack", "a.png")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, staging / "Pack", pack_id)
    from asset_catalogue import library_assets, thumbnails

    thumbnails.generate_texture_thumbnails(conn, staging, settings.load().thumbnail_dir())
    library_assets.archive_pack(conn, staging, settings.load().assets_dir(), pack_id)

    cli.cmd_check(argparse.Namespace(fix=False))

    assert "No issues found" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Parser-level structural tests -- argparse configuration mistakes (wrong
# type, missing required, wrong action) wouldn't be caught by any of the
# command-level tests above, which hand-build a Namespace and bypass
# argparse entirely.
# ---------------------------------------------------------------------------


def test_parser_requires_pack_name_for_ingest() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["ingest", "SomeFolder"])  # missing required --pack-name


def test_parser_convert_to_gltf_asset_id_is_repeatable_and_int() -> None:
    parser = build_parser()
    args = parser.parse_args(["convert", "to-gltf", "--asset-id", "1", "--asset-id", "2"])
    assert args.asset_id == [1, 2]
    assert args.func is cli.cmd_convert_to_gltf


def test_parser_material_fallback_boolean_optional_action_tri_state() -> None:
    parser = build_parser()
    neither = parser.parse_args(["pack", "set-corrections", "Pack"])
    assert neither.material_fallback is None

    on = parser.parse_args(["pack", "set-corrections", "Pack", "--material-fallback"])
    assert on.material_fallback is True

    off = parser.parse_args(["pack", "set-corrections", "Pack", "--no-material-fallback"])
    assert off.material_fallback is False


def test_parser_rating_choices_rejects_out_of_range() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["pack", "notes", "Pack", "--rating", "9"])


def test_parser_up_axis_choices_rejects_invalid_value() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["pack", "set-corrections", "Pack", "--up-axis", "X_UP"])


def test_parser_texture_override_is_repeatable() -> None:
    parser = build_parser()
    args = parser.parse_args([
        "pack", "set-corrections", "Pack",
        "--texture-override", "A=a.png", "--texture-override", "B=b.png",
    ])
    assert args.texture_override == ["A=a.png", "B=b.png"]


def test_parser_dispatches_every_subcommand_to_a_callable() -> None:
    """A cheap net for a copy-paste mistake in set_defaults(func=...) --
    every leaf subcommand must resolve to some callable, not silently
    fall through to argparse's own default (None), which would only ever
    surface as an AttributeError at actual runtime.
    """
    parser = build_parser()
    leaf_commands = [
        ["settings", "show"],
        ["settings", "set"],
        ["list"],
        ["tags"],
        ["stats"],
        ["check"],
        ["exports"],
        ["credits"],
    ]
    for command in leaf_commands:
        args = parser.parse_args(command)
        assert callable(args.func), command
