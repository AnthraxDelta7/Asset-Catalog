"""Leftover unpacked folders are removed -- but only ones we made.

A folder sitting beside a zip of the same name looks identical whether
this app extracted it or the user did months ago. Nothing is inferred:
each extraction is written down as it happens, and only recorded paths
are ever deleted.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from asset_catalogue import db, extractions, ingest, settings
from asset_catalogue.catalogue import Catalogue

from conftest import write_minimal_glb


def _library(tmp_path: Path, monkeypatch) -> tuple[Catalogue, Path, Path]:
    library, staging = tmp_path / "library", tmp_path / "staging"
    library.mkdir()
    staging.mkdir()
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    settings.save(settings.Settings(staging_folder=str(staging), library_folder=str(library)))
    conn = db.connect(library / "catalogue.db")
    catalogue = Catalogue(
        conn, staging, library / "thumbnails", library / "assets", library / "previews"
    )
    return catalogue, staging, library


def _zip_of_a_pack(tmp_path: Path, staging: Path, name: str) -> Path:
    build = tmp_path / "build" / name
    build.mkdir(parents=True)
    write_minimal_glb(build / "prop.glb", {"meshes": [{"name": name}]})
    zip_path = staging / f"{name}.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.write(build / "prop.glb", "prop.glb")
    return zip_path


def test_a_folder_we_unpacked_is_removed_after_ingest(tmp_path: Path, monkeypatch) -> None:
    catalogue, staging, library = _library(tmp_path, monkeypatch)
    _zip_of_a_pack(tmp_path, staging, "CoolPack")

    stats, _updated = catalogue.ingest_pack_bg("CoolPack.zip", "CoolPack", None, None, None)

    assert stats.archived > 0
    assert stats.extractions_removed == 1
    assert not (staging / "CoolPack").exists(), "our own unpacked leftover should be gone"
    assert (staging / "CoolPack.zip").is_file(), "the original zip is never touched"
    # And the library kept a usable copy.
    assert (library / "assets" / "CoolPack" / "prop.glb").is_file()
    catalogue.close()


def test_a_folder_the_user_unpacked_is_never_touched(tmp_path: Path, monkeypatch) -> None:
    """The whole reason extraction is recorded rather than inferred. This
    folder is indistinguishable from one we made -- same name, same place,
    same contents -- and must survive.
    """
    catalogue, staging, library = _library(tmp_path, monkeypatch)
    theirs = staging / "TheirPack"
    theirs.mkdir()
    write_minimal_glb(theirs / "prop.glb", {"meshes": [{"name": "theirs"}]})
    (theirs / "notes.txt").write_text("hand-written", encoding="utf-8")

    stats, _updated = catalogue.ingest_pack_bg("TheirPack", "TheirPack", None, None, None)

    assert stats.extractions_removed == 0
    assert theirs.is_dir()
    assert (theirs / "prop.glb").is_file()
    assert (theirs / "notes.txt").read_text(encoding="utf-8") == "hand-written"
    catalogue.close()


def test_nothing_is_removed_when_nothing_reached_the_library(tmp_path: Path, monkeypatch) -> None:
    """If archiving produced nothing, the unpacked copy is still the only
    copy, and deleting it would destroy the pack.
    """
    catalogue, staging, library = _library(tmp_path, monkeypatch)
    _zip_of_a_pack(tmp_path, staging, "Fragile")
    conn = db.connect(library / "catalogue.db")

    pack_root = staging / "Fragile"
    extractions.record(conn, pack_root)
    pack_id, _ = ingest.get_or_create_pack(conn, "Fragile", "Fragile", None, None, None)
    extractions.claim(conn, pack_id)

    class NothingArchived:
        archived = 0
        extracted_dirs: list[Path] = []

    catalogue._clean_up_extractions(conn, pack_id, NothingArchived(), None)
    assert extractions.for_pack(conn, pack_id) == [pack_root]
    conn.close()
    catalogue.close()


def test_nested_extractions_go_before_the_folder_containing_them(tmp_path: Path) -> None:
    """Deepest first, or removing the outer folder leaves a recorded path
    pointing into nothing.
    """
    library = tmp_path / "library"
    library.mkdir()
    conn = db.connect(library / "catalogue.db")
    pack_id, _ = ingest.get_or_create_pack(conn, "P", "P", None, None, None)

    outer = tmp_path / "outer"
    inner = outer / "nested" / "deep"
    inner.mkdir(parents=True)
    (inner / "f.txt").write_text("x", encoding="utf-8")
    extractions.record(conn, outer, pack_id)
    extractions.record(conn, inner, pack_id)

    removed, problems = extractions.cleanup_pack(conn, pack_id)
    assert problems == []
    assert removed == 2
    assert not outer.exists()
    assert extractions.for_pack(conn, pack_id) == []
    conn.close()


def test_a_recorded_path_that_has_become_a_file_is_left_alone(tmp_path: Path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    conn = db.connect(library / "catalogue.db")
    pack_id, _ = ingest.get_or_create_pack(conn, "P", "P", None, None, None)

    odd = tmp_path / "was_a_folder"
    odd.write_text("something else has been at it", encoding="utf-8")
    extractions.record(conn, odd, pack_id)

    removed, problems = extractions.cleanup_pack(conn, pack_id)
    assert removed == 0
    assert problems and "no longer a folder" in problems[0]
    assert odd.is_file()
    conn.close()


def test_a_path_that_is_already_gone_is_just_forgotten(tmp_path: Path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    conn = db.connect(library / "catalogue.db")
    pack_id, _ = ingest.get_or_create_pack(conn, "P", "P", None, None, None)
    extractions.record(conn, tmp_path / "never_existed", pack_id)

    removed, problems = extractions.cleanup_pack(conn, pack_id)
    assert (removed, problems) == (0, [])
    assert extractions.for_pack(conn, pack_id) == []
    conn.close()
