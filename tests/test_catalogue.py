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
