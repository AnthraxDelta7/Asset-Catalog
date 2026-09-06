from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from asset_catalogue import conversion, ingest, library_assets

from conftest import write_texture


def test_build_conversion_job_includes_pack_root(conn: sqlite3.Connection, staging_folder: Path) -> None:
    """pack_root needs to reach blender_common.apply_corrections for smart
    texture matching (relinking a broken texture, or matching a bare
    material name to a texture file elsewhere in the pack) to have
    anything to search -- without it in the job dict, conversion silently
    got none of the same texture recovery thumbnail generation already
    gets, a real gap this pins down.
    """
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    pack_root = staging_folder / "Pack"
    pack_root.mkdir()
    original = pack_root / "model.fbx"
    original.write_bytes(b"original fbx bytes")
    original_hash = ingest.hash_file(original)
    cursor = conn.execute(
        "INSERT INTO assets (pack_id, relative_path, filename, extension, file_size, "
        "content_hash, asset_type) VALUES (?, 'model.fbx', 'model.fbx', '.fbx', ?, ?, 'model')",
        (pack_id, original.stat().st_size, original_hash),
    )
    asset_id = cursor.lastrowid
    conn.commit()

    row = conversion._resolve_conversion_row(conn, asset_id)
    job, new_relative_path, output_path = conversion._build_conversion_job(staging_folder, asset_id, row)

    assert job["pack_root"] == str(pack_root)
    assert new_relative_path == "model.glb"
    assert output_path == pack_root / "model.glb"


def test_apply_successful_conversion_clears_needs_glb_conversion(
    conn: sqlite3.Connection, staging_folder: Path, assets_dir: Path
) -> None:
    """needs_glb_conversion is set by blender_render.py whenever a non-.glb
    asset's last render needed a texture fix that lives only in the
    render, not the file -- once conversion.py actually bakes that fix
    into a real .glb, the flag needs to come back down, or the asset would
    still show up in "Convert All Flagged" (and the ⚠ grid badge) after
    there's nothing left to fix.
    """
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    pack_root = staging_folder / "Pack"
    pack_root.mkdir()
    original = pack_root / "model.fbx"
    original.write_bytes(b"original fbx bytes")
    original_hash = ingest.hash_file(original)
    cursor = conn.execute(
        "INSERT INTO assets (pack_id, relative_path, filename, extension, file_size, "
        "content_hash, asset_type, needs_glb_conversion) "
        "VALUES (?, 'model.fbx', 'model.fbx', '.fbx', ?, ?, 'model', 1)",
        (pack_id, original.stat().st_size, original_hash),
    )
    asset_id = cursor.lastrowid
    conn.commit()

    output_path = pack_root / "model.glb"
    output_path.write_bytes(b"fake converted glb bytes")
    row = conversion._resolve_conversion_row(conn, asset_id)

    conversion._apply_successful_conversion(
        conn, staging_folder, assets_dir, asset_id, row, "model.glb", output_path
    )

    updated = conn.execute("SELECT needs_glb_conversion, extension FROM assets WHERE id = ?", (asset_id,)).fetchone()
    assert updated["needs_glb_conversion"] == 0
    assert updated["extension"] == ".glb"


def _make_convertible_model_asset(conn: sqlite3.Connection, staging_folder: Path) -> tuple[int, Path]:
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    pack_root = staging_folder / "Pack"
    pack_root.mkdir()
    original = pack_root / "model.fbx"
    original.write_bytes(b"original fbx bytes")
    original_hash = ingest.hash_file(original)
    cursor = conn.execute(
        "INSERT INTO assets (pack_id, relative_path, filename, extension, file_size, "
        "content_hash, asset_type) VALUES (?, 'model.fbx', 'model.fbx', '.fbx', ?, ?, 'model')",
        (pack_id, original.stat().st_size, original_hash),
    )
    conn.commit()
    return cursor.lastrowid, pack_root


def test_convert_asset_to_gltf_reports_broken_materials(
    conn: sqlite3.Connection, staging_folder: Path, assets_dir: Path
) -> None:
    """broken_materials mirrors smart_texture_notes' own parsing
    (ASSET_CATALOGUE_CONVERT_BROKEN_MATERIAL lines from Blender's
    stdout), added alongside the "Missing Textures" review workflow so a
    conversion -- not just a thumbnail render -- can also report a
    material still needing a human's attention. subprocess.run is mocked
    entirely (no real Blender involved); the mock also writes the output
    .glb the real Blender run would have produced, since
    _apply_successful_conversion (called once the mocked "ok" result
    comes back) expects it to already exist on disk.
    """
    asset_id, pack_root = _make_convertible_model_asset(conn, staging_folder)

    def fake_run(*_args, **_kwargs):
        (pack_root / "model.glb").write_bytes(b"fake converted glb bytes")
        return SimpleNamespace(
            stdout=(
                f"ASSET_CATALOGUE_CONVERT_BROKEN_MATERIAL|{asset_id}|BrokenMat\n"
                f"ASSET_CATALOGUE_CONVERT_RESULT|{asset_id}|ok\n"
            ),
            stderr="",
        )

    with patch("asset_catalogue.conversion.subprocess.run", side_effect=fake_run):
        result = conversion.convert_asset_to_gltf(
            conn, staging_folder, assets_dir, Path("blender.exe"), asset_id
        )

    assert result.ok is True
    assert result.broken_materials == [(asset_id, "model.fbx", "BrokenMat")]


def test_convert_assets_to_gltf_batch_reports_broken_materials(
    conn: sqlite3.Connection, staging_folder: Path, assets_dir: Path
) -> None:
    """Same as the single-asset test above, but through the batch path
    (subprocess.Popen with streamed stdout, not subprocess.run) -- the
    two conversion entry points parse ASSET_CATALOGUE_CONVERT_BROKEN_
    MATERIAL lines independently, so a fix to one wouldn't necessarily
    catch a regression in the other.
    """
    asset_id, pack_root = _make_convertible_model_asset(conn, staging_folder)

    def fake_popen(*_args, **_kwargs):
        (pack_root / "model.glb").write_bytes(b"fake converted glb bytes")
        process = MagicMock()
        process.stdout = iter(
            [
                f"ASSET_CATALOGUE_CONVERT_BROKEN_MATERIAL|{asset_id}|BrokenMat\n",
                f"ASSET_CATALOGUE_CONVERT_RESULT|{asset_id}|ok\n",
            ]
        )
        process.wait.return_value = None
        process.returncode = 0
        return process

    with patch("asset_catalogue.conversion.subprocess.Popen", side_effect=fake_popen):
        result = conversion.convert_assets_to_gltf(
            conn, staging_folder, assets_dir, Path("blender.exe"), [asset_id]
        )

    assert result.converted == 1
    assert result.broken_materials == [(asset_id, "model.fbx", "BrokenMat")]


def _make_asset_with_pending_conversion(conn: sqlite3.Connection, staging_folder: Path, assets_dir: Path) -> int:
    """Seeds an asset that looks like it's mid-conversion: its DB row
    already points at the (fake) converted .glb, and a pending_conversions
    row remembers the pre-conversion original -- exactly the state
    conversion.convert_asset_to_gltf leaves behind, without needing Blender
    to actually produce it.
    """
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    pack_root = staging_folder / "Pack"
    pack_root.mkdir()
    original = pack_root / "model.fbx"
    original.write_bytes(b"original fbx bytes")
    original_hash = ingest.hash_file(original)
    cursor = conn.execute(
        "INSERT INTO assets (pack_id, relative_path, filename, extension, file_size, "
        "content_hash, asset_type) VALUES (?, 'model.fbx', 'model.fbx', '.fbx', ?, ?, 'model')",
        (pack_id, original.stat().st_size, original_hash),
    )
    asset_id = cursor.lastrowid

    converted = pack_root / "model.glb"
    converted.write_bytes(b"fake converted glb bytes")
    library_assets.archive_asset(conn, staging_folder, assets_dir, asset_id)  # archives the .fbx first

    conn.execute(
        "INSERT INTO pending_conversions (asset_id, original_relative_path, original_filename, "
        "original_extension, original_content_hash, original_file_size, converted_at) "
        "VALUES (?, 'model.fbx', 'model.fbx', '.fbx', ?, ?, '2020-01-01T00:00:00')",
        (asset_id, original_hash, original.stat().st_size),
    )
    conn.execute(
        "UPDATE assets SET relative_path = 'model.glb', filename = 'model.glb', extension = '.glb', "
        "content_hash = ?, file_size = ? WHERE id = ?",
        (ingest.hash_file(converted), converted.stat().st_size, asset_id),
    )
    conn.commit()
    library_assets.archive_asset(conn, staging_folder, assets_dir, asset_id)  # archives the .glb too
    return asset_id


def test_has_pending_conversion_and_listing(conn: sqlite3.Connection, staging_folder: Path, assets_dir: Path) -> None:
    asset_id = _make_asset_with_pending_conversion(conn, staging_folder, assets_dir)
    assert conversion.has_pending_conversion(conn, asset_id) is True
    assert conversion.list_pending_conversion_asset_ids(conn) == [asset_id]


def test_list_pending_conversions_includes_pack_and_filenames(
    conn: sqlite3.Connection, staging_folder: Path, assets_dir: Path
) -> None:
    asset_id = _make_asset_with_pending_conversion(conn, staging_folder, assets_dir)
    rows = conversion.list_pending_conversions(conn)
    assert len(rows) == 1
    row = rows[0]
    assert row["asset_id"] == asset_id
    assert row["pack_name"] == "Pack"
    assert row["original_filename"] == "model.fbx"
    assert row["converted_filename"] == "model.glb"
    assert row["converted_at"] == "2020-01-01T00:00:00"


def test_revert_conversion_restores_original_and_removes_glb(
    conn: sqlite3.Connection, staging_folder: Path, assets_dir: Path
) -> None:
    asset_id = _make_asset_with_pending_conversion(conn, staging_folder, assets_dir)

    reverted = conversion.revert_conversion(conn, staging_folder, assets_dir, asset_id)
    assert reverted is True
    assert conversion.has_pending_conversion(conn, asset_id) is False

    row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    assert row["relative_path"] == "model.fbx"
    assert row["extension"] == ".fbx"
    assert not (assets_dir / "Pack" / "model.glb").exists()
    assert (assets_dir / "Pack" / "model.fbx").exists()  # self-healing re-archive of the original


def test_revert_conversion_returns_false_when_nothing_pending(conn: sqlite3.Connection, staging_folder: Path, assets_dir: Path) -> None:
    assert conversion.revert_conversion(conn, staging_folder, assets_dir, 999) is False


def test_cleanup_pending_conversion_deletes_original_keeps_glb(
    conn: sqlite3.Connection, staging_folder: Path, assets_dir: Path
) -> None:
    asset_id = _make_asset_with_pending_conversion(conn, staging_folder, assets_dir)

    cleaned = conversion.cleanup_pending_conversion(conn, staging_folder, assets_dir, asset_id)
    assert cleaned is True
    assert conversion.has_pending_conversion(conn, asset_id) is False
    assert not (assets_dir / "Pack" / "model.fbx").exists()
    assert (assets_dir / "Pack" / "model.glb").exists()

    row = conn.execute("SELECT relative_path FROM assets WHERE id = ?", (asset_id,)).fetchone()
    assert row["relative_path"] == "model.glb"  # DB row keeps pointing at the converted file


def test_cleanup_all_pending_conversions_handles_multiple(
    conn: sqlite3.Connection, staging_folder: Path, assets_dir: Path
) -> None:
    a1 = _make_asset_with_pending_conversion(conn, staging_folder, assets_dir)
    # A second pack/asset going through the same pending-conversion setup.
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack2", "Pack2", None, None, None)
    (staging_folder / "Pack2").mkdir()

    count = conversion.cleanup_all_pending_conversions(conn, staging_folder, assets_dir)
    assert count == 1
    assert conversion.list_pending_conversion_asset_ids(conn) == []
