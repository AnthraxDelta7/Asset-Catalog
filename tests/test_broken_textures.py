from __future__ import annotations

import sqlite3
from pathlib import Path

from asset_catalogue import broken_textures, ingest

from conftest import make_pack


def _make_model_asset(conn: sqlite3.Connection, staging_folder: Path, pack_name: str, filename: str) -> int:
    pack_id = make_pack(conn, staging_folder, pack_name)
    path = staging_folder / pack_name / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("dummy model content -- never actually rendered in this test")
    ingest.ingest_pack(conn, staging_folder / pack_name, pack_id)
    asset_id = conn.execute(
        "SELECT id FROM assets WHERE filename = ?", (filename,)
    ).fetchone()["id"]
    return asset_id


def test_replace_for_asset_then_list_all(conn: sqlite3.Connection, staging_folder: Path) -> None:
    asset_id = _make_model_asset(conn, staging_folder, "Pack", "a.fbx")

    broken_textures.replace_for_asset(conn, asset_id, ["MaterialA", "MaterialB"])
    rows = broken_textures.list_all(conn)

    assert {row["material_name"] for row in rows} == {"MaterialA", "MaterialB"}
    assert all(row["asset_id"] == asset_id and row["pack_name"] == "Pack" for row in rows)


def test_replace_for_asset_is_self_correcting(conn: sqlite3.Connection, staging_folder: Path) -> None:
    """A re-render that fixes one material and leaves another broken must
    update the stored set to match exactly, not just add to it -- same
    "recompute, don't accumulate" behavior as assets.needs_glb_conversion.
    """
    asset_id = _make_model_asset(conn, staging_folder, "Pack", "a.fbx")

    broken_textures.replace_for_asset(conn, asset_id, ["MaterialA", "MaterialB"])
    broken_textures.replace_for_asset(conn, asset_id, ["MaterialB"])

    assert broken_textures.list_for_asset(conn, asset_id) == ["MaterialB"]


def test_replace_for_asset_with_empty_list_clears_it(conn: sqlite3.Connection, staging_folder: Path) -> None:
    asset_id = _make_model_asset(conn, staging_folder, "Pack", "a.fbx")

    broken_textures.replace_for_asset(conn, asset_id, ["MaterialA"])
    broken_textures.replace_for_asset(conn, asset_id, [])

    assert broken_textures.list_for_asset(conn, asset_id) == []
    assert broken_textures.list_all(conn) == []


def test_list_all_excludes_trashed_assets(conn: sqlite3.Connection, staging_folder: Path) -> None:
    asset_id = _make_model_asset(conn, staging_folder, "Pack", "a.fbx")
    broken_textures.replace_for_asset(conn, asset_id, ["MaterialA"])

    conn.execute("UPDATE assets SET deleted_at = '2020-01-01T00:00:00' WHERE id = ?", (asset_id,))
    conn.commit()

    assert broken_textures.list_all(conn) == []
    # list_for_asset is used by the detail panel for one specific,
    # already-known asset -- it still reports the truth regardless of
    # trash status, only the review dialog's list_all hides trashed rows.
    assert broken_textures.list_for_asset(conn, asset_id) == ["MaterialA"]


def test_delete_for_pack_material_clears_across_every_asset_in_the_pack(
    conn: sqlite3.Connection, staging_folder: Path
) -> None:
    pack_id = make_pack(conn, staging_folder, "Pack")
    for filename in ("a.fbx", "b.fbx"):
        path = staging_folder / "Pack" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"dummy content for {filename}")
    ingest.ingest_pack(conn, staging_folder / "Pack", pack_id)
    asset_ids = [row["id"] for row in conn.execute("SELECT id FROM assets ORDER BY filename")]

    for asset_id in asset_ids:
        broken_textures.replace_for_asset(conn, asset_id, ["SharedMaterial", "OtherMaterial"])

    broken_textures.delete_for_pack_material(conn, pack_id, "SharedMaterial")

    for asset_id in asset_ids:
        assert broken_textures.list_for_asset(conn, asset_id) == ["OtherMaterial"]
