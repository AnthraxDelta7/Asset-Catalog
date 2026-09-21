"""The exports table must name files that exist.

Export to Godot lands an intermediate .glb, wraps it into a .res or
.tscn, then deletes the .glb -- while the export had been recorded
against the .glb. Nothing reads the table yet, so this was latent rather
than broken, which is the kind of wrong that only surfaces once someone
builds the feature that depends on it.
"""

from __future__ import annotations

from pathlib import Path

from asset_catalogue import db, exporting, ingest

from conftest import write_minimal_glb


def _catalogued_asset(tmp_path: Path):
    library, staging = tmp_path / "library", tmp_path / "staging"
    pack = staging / "Pack"
    pack.mkdir(parents=True)
    library.mkdir()
    write_minimal_glb(pack / "prop.glb", {"meshes": [{"name": "prop"}]})
    conn = db.connect(library / "catalogue.db")
    pack_id, _ = ingest.get_or_create_pack(conn, "Pack", "Pack", None, None, None)
    ingest.ingest_pack(conn, pack, pack_id)
    asset_id = conn.execute("SELECT id FROM assets").fetchone()["id"]
    return conn, asset_id


def test_a_single_mesh_export_is_recorded_against_its_res_file(tmp_path: Path) -> None:
    conn, asset_id = _catalogued_asset(tmp_path)
    project = tmp_path / "project" / "exported_assets"
    project.mkdir(parents=True)
    landed_glb = project / "prop.glb"
    landed_glb.write_bytes(b"glb")

    exporting.record_export(conn, asset_id, "MyGame", landed_glb)
    conn.commit()

    # The wrapper produced a .res and the .glb is about to be removed.
    artifact = project / "prop.res"
    artifact.write_bytes(b"res")
    assert exporting.wrapped_artifact_for(landed_glb) == artifact
    assert exporting.repoint_export(conn, landed_glb, artifact) == 1
    conn.commit()

    recorded = conn.execute("SELECT destination_path FROM exports").fetchone()[0]
    assert Path(recorded) == artifact
    conn.close()


def test_a_multi_mesh_export_is_recorded_against_its_tscn(tmp_path: Path) -> None:
    conn, asset_id = _catalogued_asset(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    landed_glb = project / "prop.glb"
    landed_glb.write_bytes(b"glb")
    scene = project / "prop.tscn"
    scene.write_bytes(b"tscn")

    exporting.record_export(conn, asset_id, "MyGame", landed_glb)
    conn.commit()
    assert exporting.wrapped_artifact_for(landed_glb) == scene
    exporting.repoint_export(conn, landed_glb, scene)
    conn.commit()

    assert Path(conn.execute("SELECT destination_path FROM exports").fetchone()[0]) == scene
    conn.close()


def test_a_preserved_glb_keeps_its_own_record(tmp_path: Path) -> None:
    """A rigged model is exported by being left exactly as it is -- there
    is no wrapper artifact, and the .glb is the real result. Repointing
    must not invent one.
    """
    conn, asset_id = _catalogued_asset(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    landed_glb = project / "character.glb"
    landed_glb.write_bytes(b"glb")

    exporting.record_export(conn, asset_id, "MyGame", landed_glb)
    conn.commit()

    assert exporting.wrapped_artifact_for(landed_glb) is None
    assert Path(conn.execute("SELECT destination_path FROM exports").fetchone()[0]) == landed_glb
    conn.close()


def test_repointing_reports_when_it_matched_nothing(tmp_path: Path) -> None:
    """Returns the row count so a caller can tell a real repoint from a
    silent no-op, rather than assuming it worked.
    """
    conn, _asset_id = _catalogued_asset(tmp_path)
    assert exporting.repoint_export(conn, tmp_path / "a.glb", tmp_path / "a.res") == 0
    conn.close()
