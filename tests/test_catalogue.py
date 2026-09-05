from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from asset_catalogue import db, ingest, settings
from asset_catalogue.catalogue import Catalogue

from conftest import write_texture


@pytest.fixture
def catalogue(tmp_path: Path) -> Catalogue:
    library_folder = tmp_path / "library"
    library_folder.mkdir()
    staging_folder = tmp_path / "staging"
    staging_folder.mkdir()
    conn = db.connect(library_folder / "catalogue.db")
    cat = Catalogue(conn, staging_folder, library_folder / "thumbnails", library_folder / "assets")
    yield cat
    cat.close()


@pytest.fixture
def catalogue_with_asset(catalogue: Catalogue) -> tuple[Catalogue, int]:
    write_texture(catalogue.staging_folder(), "Pack", "a.png")
    pack_id, _ = ingest.get_or_create_pack(catalogue._conn, "Pack", "Pack", "Creator", "MIT", None)
    ingest.ingest_pack(catalogue._conn, catalogue.staging_folder() / "Pack", pack_id)
    asset_id = catalogue._conn.execute("SELECT id FROM assets").fetchone()["id"]
    return catalogue, asset_id


def test_list_assets_applies_filters(catalogue_with_asset: tuple[Catalogue, int]) -> None:
    catalogue, asset_id = catalogue_with_asset
    assert len(catalogue.list_assets()) == 1
    assert len(catalogue.list_assets(pack="Pack")) == 1
    assert len(catalogue.list_assets(pack="Nonexistent")) == 0
    assert len(catalogue.list_assets(asset_type="texture")) == 1
    assert len(catalogue.list_assets(asset_type="model")) == 0
    assert len(catalogue.list_assets(search="a.png")) == 1
    assert len(catalogue.list_assets(search="nomatch")) == 0


def test_get_asset_and_get_pack_detail(catalogue_with_asset: tuple[Catalogue, int]) -> None:
    catalogue, asset_id = catalogue_with_asset
    asset = catalogue.get_asset(asset_id)
    assert asset is not None
    assert asset.filename == "a.png"
    assert asset.pack_name == "Pack"

    assert catalogue.get_asset(999999) is None

    detail = catalogue.get_pack_detail("Pack")
    assert detail is not None
    assert detail.creator == "Creator"
    assert detail.licence == "MIT"
    assert detail.asset_count == 1
    assert catalogue.get_pack_detail("Nonexistent") is None


def test_tag_asset_and_untag_asset(catalogue_with_asset: tuple[Catalogue, int]) -> None:
    catalogue, asset_id = catalogue_with_asset
    catalogue.tag_asset(asset_id, "weapons")
    assert catalogue.get_asset_tags(asset_id) == ["weapons"]
    catalogue.untag_asset(asset_id, "weapons")
    assert catalogue.get_asset_tags(asset_id) == []


def test_set_favorite_and_favorites_only_filter(catalogue_with_asset: tuple[Catalogue, int]) -> None:
    catalogue, asset_id = catalogue_with_asset
    assert catalogue.get_asset(asset_id).favorite is False
    assert catalogue.list_assets(favorites_only=True) == []

    catalogue.set_favorite([asset_id], True)
    assert catalogue.get_asset(asset_id).favorite is True
    assert len(catalogue.list_assets(favorites_only=True)) == 1

    catalogue.set_favorite([asset_id], False)
    assert catalogue.get_asset(asset_id).favorite is False
    assert catalogue.list_assets(favorites_only=True) == []


def test_trash_hides_from_list_assets_but_not_get_asset(
    catalogue_with_asset: tuple[Catalogue, int]
) -> None:
    catalogue, asset_id = catalogue_with_asset
    assert len(catalogue.list_assets()) == 1

    catalogue.trash_assets([asset_id])
    assert catalogue.list_assets() == []
    assert catalogue.count_trashed_assets() == 1
    trashed = catalogue.list_trashed_assets()
    assert len(trashed) == 1 and trashed[0].id == asset_id
    # get_asset is an identity lookup, not a listing -- still finds a
    # trashed asset (e.g. so the trash dialog can look it up by id).
    assert catalogue.get_asset(asset_id) is not None

    catalogue.restore_assets([asset_id])
    assert len(catalogue.list_assets()) == 1
    assert catalogue.count_trashed_assets() == 0


def test_update_pack_bg_sets_notes_and_rating(
    catalogue_with_asset: tuple[Catalogue, int], monkeypatch, tmp_path: Path
) -> None:
    # update_pack_bg opens its own connection via settings.load().db_path()
    # (background-thread-safe -- see catalogue.py's "Background-safe
    # operations" section), so it needs settings pointed at the exact same
    # on-disk database the catalogue_with_asset fixture already opened.
    catalogue, asset_id = catalogue_with_asset
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    settings.save(settings.Settings(
        staging_folder=str(catalogue.staging_folder()),
        library_folder=str(catalogue._thumbnail_dir.parent),
    ))

    detail = catalogue.get_pack_detail("Pack")
    catalogue.update_pack_bg(
        detail.id, detail.name, detail.creator, detail.licence, detail.source_url,
        detail.corrections, notes="great pack", rating=5,
    )
    updated = catalogue.get_pack_detail("Pack")
    assert updated.notes == "great pack"
    assert updated.rating == 5
    # The asset summary's denormalized pack_rating/pack_notes track it too
    # (used by the detail panel without a separate pack lookup).
    asset = catalogue.get_asset(asset_id)
    assert asset.pack_rating == 5
    assert asset.pack_notes == "great pack"


def test_get_library_stats(catalogue_with_asset: tuple[Catalogue, int]) -> None:
    catalogue, asset_id = catalogue_with_asset
    stats = catalogue.get_library_stats()
    assert stats.total_assets == 1
    assert stats.pack_count == 1


def test_thumbnail_path_for_returns_none_when_not_rendered(catalogue_with_asset: tuple[Catalogue, int]) -> None:
    catalogue, asset_id = catalogue_with_asset
    asset = catalogue.get_asset(asset_id)
    assert catalogue.thumbnail_path_for(asset.content_hash) is None


def test_library_asset_path_if_archived(catalogue_with_asset: tuple[Catalogue, int]) -> None:
    catalogue, asset_id = catalogue_with_asset
    asset = catalogue.get_asset(asset_id)
    assert catalogue.library_asset_path_if_archived(asset.pack_name, asset.relative_path) is None

    from asset_catalogue import library_assets
    library_assets.archive_asset(catalogue._conn, catalogue.staging_folder(), catalogue._assets_dir, asset_id)
    assert catalogue.library_asset_path_if_archived(asset.pack_name, asset.relative_path) is not None


def test_count_pending_conversions_starts_at_zero(catalogue_with_asset: tuple[Catalogue, int]) -> None:
    catalogue, _asset_id = catalogue_with_asset
    assert catalogue.count_pending_conversions() == 0


def test_ingest_pack_bg_end_to_end(catalogue: Catalogue, tmp_path: Path, monkeypatch) -> None:
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "SETTINGS_PATH", settings_path)
    s = settings.Settings(
        staging_folder=str(catalogue.staging_folder()),
        library_folder=str(tmp_path / "library"),
    )
    # Point settings at the same library the catalogue fixture already uses.
    s.library_folder = str(Path(catalogue._thumbnail_dir).parent)
    settings.save(s)

    write_texture(catalogue.staging_folder(), "Pack", "a.png")
    stats, updated_fields = catalogue.ingest_pack_bg("Pack", "Pack", "Creator", None, None)
    assert stats.new == 1
    assert updated_fields == []
    assert len(catalogue.list_assets()) == 1


def test_export_assets_bg_end_to_end(catalogue_with_asset: tuple[Catalogue, int], tmp_path: Path, monkeypatch) -> None:
    catalogue, asset_id = catalogue_with_asset
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "SETTINGS_PATH", settings_path)
    s = settings.Settings(
        staging_folder=str(catalogue.staging_folder()),
        library_folder=str(Path(catalogue._thumbnail_dir).parent),
    )
    settings.save(s)

    project_root = tmp_path / "project"
    project_root.mkdir()
    stats = catalogue.export_assets_bg([asset_id], project_root)
    assert stats.copied == 1
    assert (project_root / "exported_assets" / "Pack" / "a.png").is_file()


def test_model_preview_path_for(catalogue_with_asset: tuple[Catalogue, int]) -> None:
    from asset_catalogue import model_preview

    catalogue, asset_id = catalogue_with_asset
    asset = catalogue.get_asset(asset_id)
    # No preview .glb has been generated for this asset -- nothing cached yet.
    assert catalogue.model_preview_path_for(asset.content_hash) is None

    # Once one exists on disk (as blender_thumbnail_script.py would have
    # written it alongside the static thumbnail), it's found.
    preview_path = model_preview.preview_path(catalogue._preview_dir, asset.content_hash)
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.write_bytes(b"glTF")
    assert catalogue.model_preview_path_for(asset.content_hash) == preview_path
