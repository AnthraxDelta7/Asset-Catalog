"""Rig and animation counts, kept on the asset row.

model_metadata can answer this per asset, but each answer costs a file
read -- and the grid needs it for every visible asset at once. NULL means
"not inspected yet", deliberately distinct from 0, so a badge never
claims a model has no rig when nothing has looked at it.
"""

from __future__ import annotations

import json
from pathlib import Path

from asset_catalogue import db, ingest, library_assets, model_facts, model_metadata

from conftest import write_minimal_glb


def _pack(tmp_path: Path):
    library, staging = tmp_path / "library", tmp_path / "staging"
    pack = staging / "Pack"
    pack.mkdir(parents=True)
    library.mkdir()
    return library, staging, pack


def test_a_rigged_gltf_is_recorded_at_ingest(tmp_path: Path) -> None:
    """A glTF container answers this from its header, so there is no
    reason to make anyone wait for a render to find out.
    """
    library, staging, pack = _pack(tmp_path)
    write_minimal_glb(
        pack / "character.glb",
        {
            "meshes": [{"name": "body"}],
            "skins": [{"joints": [0, 1, 2, 3]}],
            "animations": [{"name": "Idle"}, {"name": "Walk"}],
        },
    )
    write_minimal_glb(pack / "rock.glb", {"meshes": [{"name": "rock"}]})

    conn = db.connect(library / "catalogue.db")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, pack, pack_id)

    rows = {
        r["filename"]: (r["joint_count"], r["animation_count"])
        for r in conn.execute("SELECT filename, joint_count, animation_count FROM assets")
    }
    assert rows["character.glb"] == (4, 2)
    # Inspected and found to have none -- 0, not NULL.
    assert rows["rock.glb"] == (0, 0)
    conn.close()


def test_a_format_blender_must_read_stays_unknown_until_rendered(tmp_path: Path) -> None:
    """NULL, not 0. An .fbx cannot be read without Blender, and claiming
    "no rig" about a file nobody has opened is a confident lie.
    """
    library, staging, pack = _pack(tmp_path)
    (pack / "chair.fbx").write_bytes(b"not really an fbx")

    conn = db.connect(library / "catalogue.db")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, pack, pack_id)

    row = conn.execute("SELECT joint_count, animation_count FROM assets").fetchone()
    assert row["joint_count"] is None
    assert row["animation_count"] is None
    conn.close()


def test_a_render_fills_in_what_it_learned(tmp_path: Path) -> None:
    """The render is the only thing that can read an .fbx, so what it
    reports goes onto the row -- keyed by hash, since identical bytes are
    the same model.
    """
    library, staging, pack = _pack(tmp_path)
    (pack / "chair.fbx").write_bytes(b"fbx bytes")

    conn = db.connect(library / "catalogue.db")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, pack, pack_id)
    content_hash = conn.execute("SELECT content_hash FROM assets").fetchone()[0]

    assert model_facts.record_by_hash(conn, content_hash, 31, 4) == 1
    conn.commit()
    row = conn.execute("SELECT joint_count, animation_count FROM assets").fetchone()
    assert (row["joint_count"], row["animation_count"]) == (31, 4)
    conn.close()


def test_backfill_fills_only_what_is_unknown_and_can_be_read(tmp_path: Path) -> None:
    library, staging, pack = _pack(tmp_path)
    write_minimal_glb(pack / "rig.glb", {"meshes": [{}], "skins": [{"joints": [0, 1]}]})
    (pack / "chair.fbx").write_bytes(b"fbx bytes")

    conn = db.connect(library / "catalogue.db")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, pack, pack_id)
    assets_dir = library / "assets"
    library_assets.archive_pack(conn, staging, assets_dir, pack_id)
    # Pretend nothing has been inspected, as a pre-column library would be.
    conn.execute("UPDATE assets SET joint_count = NULL, animation_count = NULL")
    conn.commit()

    filled = model_facts.backfill(conn, assets_dir, staging, library / "previews")
    assert filled == 1  # the .glb; the .fbx is unreadable without Blender

    rows = {
        r["filename"]: r["joint_count"]
        for r in conn.execute("SELECT filename, joint_count FROM assets")
    }
    assert rows["rig.glb"] == 2
    assert rows["chair.fbx"] is None, "unreadable must stay unknown, not become zero"

    # Re-running is a no-op once everything readable is done.
    assert model_facts.backfill(conn, assets_dir, staging, library / "previews") == 0
    conn.close()


def test_backfill_uses_the_render_cache_for_non_gltf(tmp_path: Path) -> None:
    """An .fbx rendered before these columns existed already has its rig
    recorded in the per-hash cache -- backfill should find it there
    rather than declaring it unknowable.
    """
    library, staging, pack = _pack(tmp_path)
    (pack / "chair.fbx").write_bytes(b"fbx bytes")

    conn = db.connect(library / "catalogue.db")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, pack, pack_id)
    assets_dir = library / "assets"
    library_assets.archive_pack(conn, staging, assets_dir, pack_id)
    content_hash = conn.execute("SELECT content_hash FROM assets").fetchone()[0]

    previews = library / "previews"
    model_metadata.write_cache(previews, content_hash, 17, ["Sit", "Stand"])

    assert model_facts.backfill(conn, assets_dir, staging, previews) == 1
    row = conn.execute("SELECT joint_count, animation_count FROM assets").fetchone()
    assert (row["joint_count"], row["animation_count"]) == (17, 2)
    conn.close()
