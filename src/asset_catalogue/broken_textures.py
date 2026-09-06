"""Tracks which (asset, material) pairs are currently known to reference a
broken texture -- see db.py's broken_texture_materials table. Populated by
blender_render.py/conversion.py after every render (see replace_for_asset),
read by the "Missing Textures" review dialog and the asset detail panel's
Fix Texture button (both in ui/main_window.py) via catalogue.py.
"""

from __future__ import annotations

import sqlite3


def replace_for_asset(conn: sqlite3.Connection, asset_id: int, material_names: list[str]) -> None:
    """Overwrites the full broken-material set for one asset with whatever
    its most recent render actually found -- called after every render
    (even with an empty list), the same "recompute, don't just add"
    approach as assets.needs_glb_conversion, so a re-render that fixes a
    material clears it here automatically rather than needing a separate
    cleanup step.
    """
    conn.execute("DELETE FROM broken_texture_materials WHERE asset_id = ?", (asset_id,))
    conn.executemany(
        "INSERT INTO broken_texture_materials (asset_id, material_name) VALUES (?, ?)",
        [(asset_id, name) for name in material_names],
    )
    conn.commit()


def list_all(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every currently-broken (asset, material) pair still relevant to
    review -- joined with asset/pack context for display, trashed assets
    excluded (their files aren't touched, so there's nothing to fix).
    """
    return conn.execute(
        "SELECT broken_texture_materials.asset_id, broken_texture_materials.material_name, "
        "assets.filename, assets.pack_id, packs.name AS pack_name, packs.pack_folder "
        "FROM broken_texture_materials "
        "JOIN assets ON assets.id = broken_texture_materials.asset_id "
        "JOIN packs ON packs.id = assets.pack_id "
        "WHERE assets.deleted_at IS NULL "
        "ORDER BY packs.name, assets.filename, broken_texture_materials.material_name"
    ).fetchall()


def list_for_asset(conn: sqlite3.Connection, asset_id: int) -> list[str]:
    return [
        row["material_name"]
        for row in conn.execute(
            "SELECT material_name FROM broken_texture_materials WHERE asset_id = ? ORDER BY material_name",
            (asset_id,),
        )
    ]


def delete_for_pack_material(conn: sqlite3.Connection, pack_id: int, material_name: str) -> None:
    """Clears one material across every asset in a pack at once -- used
    right after a texture override or a "no texture needed"
    acknowledgment, both of which are pack+material-name scoped (see
    catalogue.py's set_texture_override_bg / acknowledge_no_texture_bg),
    not per-asset, so the fix applies everywhere that material shows up
    without waiting for each individual asset to be re-rendered first.
    """
    conn.execute(
        "DELETE FROM broken_texture_materials WHERE material_name = ? "
        "AND asset_id IN (SELECT id FROM assets WHERE pack_id = ?)",
        (material_name, pack_id),
    )
    conn.commit()
