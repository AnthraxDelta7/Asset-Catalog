from __future__ import annotations

import sqlite3
from pathlib import Path

from asset_catalogue import ingest, model_preview, settings

from conftest import make_pack


def _write_dummy_model(staging_folder: Path, pack_name: str, filename: str) -> Path:
    path = staging_folder / pack_name / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"dummy model content for {filename} -- never actually rendered in this test")
    return path


def test_missing_preview_filter_skips_cached_assets(
    conn: sqlite3.Connection, staging_folder: Path
) -> None:
    from asset_catalogue.catalogue import Catalogue

    pack_id = make_pack(conn, staging_folder, "Pack")
    _write_dummy_model(staging_folder, "Pack", "a.obj")
    _write_dummy_model(staging_folder, "Pack", "b.obj")
    ingest.ingest_pack(conn, staging_folder / "Pack", pack_id)
    rows = {row["filename"]: row["id"] for row in conn.execute("SELECT id, filename FROM assets")}
    assert set(rows) == {"a.obj", "b.obj"}

    catalogue = Catalogue(conn, staging_folder, staging_folder / "thumbs", staging_folder / "assets")
    content_hash_a = conn.execute(
        "SELECT content_hash FROM assets WHERE id = ?", (rows["a.obj"],)
    ).fetchone()["content_hash"]

    # Pre-seed a cached preview for "a.obj" only.
    preview_path = model_preview.preview_path(catalogue._preview_dir, content_hash_a)
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.write_bytes(b"glTF")

    # A static method, deliberately: the only real caller (render_model_previews_bg)
    # runs on a background thread with its own connection, not self._conn.
    missing = Catalogue._model_asset_ids_missing_preview(
        conn, catalogue._preview_dir, [rows["a.obj"], rows["b.obj"]]
    )
    assert missing == [rows["b.obj"]]


def test_render_model_previews_bg_skips_when_all_cached(
    conn: sqlite3.Connection, staging_folder: Path, monkeypatch, tmp_path: Path
) -> None:
    from asset_catalogue.catalogue import Catalogue

    pack_id = make_pack(conn, staging_folder, "Pack")
    _write_dummy_model(staging_folder, "Pack", "a.obj")
    ingest.ingest_pack(conn, staging_folder / "Pack", pack_id)
    asset_id = conn.execute("SELECT id FROM assets").fetchone()["id"]
    content_hash = conn.execute(
        "SELECT content_hash FROM assets WHERE id = ?", (asset_id,)
    ).fetchone()["content_hash"]

    library_folder = staging_folder.parent / "library"
    library_folder.mkdir()
    # render_model_previews_bg opens its own connection via
    # settings.load().db_path() (background-thread-safe -- see its own
    # docstring), so it needs settings pointed at the exact same on-disk
    # database this test's `conn` fixture already populated. Copy the
    # in-memory-committed tmp DB file there directly rather than trying to
    # reopen the pytest `conn` fixture's own path (conn doesn't expose it).
    import shutil
    conn.commit()
    db_source = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    shutil.copy(db_source, library_folder / "catalogue.db")

    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    settings.save(settings.Settings(
        staging_folder=str(staging_folder), library_folder=str(library_folder),
    ))

    catalogue = Catalogue(conn, staging_folder, library_folder / "thumbs", library_folder / "assets")
    preview_path = model_preview.preview_path(catalogue._preview_dir, content_hash)
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.write_bytes(b"glTF")

    # Already cached -- must return immediately without touching Blender
    # at all (no blender_path configured, so any real attempt would raise).
    stats = catalogue.render_model_previews_bg([asset_id])
    assert stats.already_done == 1
    assert stats.generated == 0
    assert stats.failed == 0
