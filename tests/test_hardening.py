"""Guards for the ways this app was found to die or fail silently.

Each of these corresponds to a real observed failure or a contract a
caller already relied on, not a hypothetical.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from asset_catalogue import blender_render, godot_export, settings


# -- settings: a bad file must not stop the app starting ---------------


@pytest.fixture
def settings_path(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)
    return path


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("{not json", id="truncated"),
        pytest.param("", id="empty"),
        pytest.param("[1, 2, 3]", id="not-an-object"),
        pytest.param('{"library_folder": {"nope": 1}, "recent_export_projects": 5}', id="wrong-types"),
    ],
)
def test_an_unreadable_settings_file_falls_back_to_defaults(settings_path: Path, content: str) -> None:
    """settings.load() is called on startup and from ~25 other places. An
    exception here isn't a bad preference, it's an app that won't launch
    with a raw traceback and no way back except deleting the file by hand.
    """
    settings_path.write_text(content, encoding="utf-8")
    loaded = settings.load()
    assert loaded.library_folder == settings.Settings().library_folder
    assert settings.last_load_error, "a silent reset is indistinguishable from amnesia"


def test_a_good_settings_file_still_loads_and_reports_no_error(settings_path: Path) -> None:
    settings.save(settings.Settings(library_folder="L", staging_folder="S"))
    loaded = settings.load()
    assert (loaded.library_folder, loaded.staging_folder) == ("L", "S")
    assert settings.last_load_error is None


def test_saving_never_leaves_a_truncated_file_in_place(settings_path: Path) -> None:
    """A plain write truncates the real file first, so a crash mid-write
    leaves a half-written settings.json -- which, paired with a load()
    that used to raise, bricked the app. The write goes to a temporary
    file and is swapped in with one atomic replace.
    """
    settings.save(settings.Settings(library_folder="first"))
    original = settings_path.read_text(encoding="utf-8")

    settings.save(settings.Settings(library_folder="second"))
    assert json.loads(settings_path.read_text(encoding="utf-8"))["library_folder"] == "second"
    assert original != settings_path.read_text(encoding="utf-8")
    # No debris left beside it.
    assert list(settings_path.parent.glob("*.tmp")) == []


# -- external tools: resolve_* promises an error string, not an exception


def test_a_blender_path_that_cannot_be_launched_reports_rather_than_raises(tmp_path: Path) -> None:
    """resolve_blender's whole contract is "(None, error) on failure" so
    callers can treat a missing Blender as a soft skip. An OSError from
    the version probe escaped that, several call sites deep.
    """
    not_an_exe = tmp_path / "blender.exe"
    not_an_exe.write_text("definitely not a program", encoding="utf-8")
    assert blender_render.get_blender_version(not_an_exe) is None

    resolved, error = blender_render.resolve_blender(str(not_an_exe))
    assert resolved is None
    assert error


def test_a_godot_path_that_cannot_be_launched_reports_rather_than_raises(tmp_path: Path) -> None:
    not_an_exe = tmp_path / "godot.exe"
    not_an_exe.write_text("definitely not a program", encoding="utf-8")
    assert godot_export.get_godot_version(not_an_exe) is None


def test_a_godot_import_pass_that_cannot_run_is_a_failure_not_a_crash(tmp_path: Path) -> None:
    not_an_exe = tmp_path / "godot.exe"
    not_an_exe.write_text("nope", encoding="utf-8")
    assert godot_export._run_godot_import_pass(not_an_exe, tmp_path) is False


# -- background jobs ---------------------------------------------------


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _spin(ms: int) -> None:
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def test_an_error_message_is_never_blank(qapp) -> None:
    """Plenty of exceptions stringify to nothing, and feeding that to a
    message box produces a dialog with a title, an OK button, and no
    indication of what went wrong.
    """
    from asset_catalogue.ui.jobs import describe_exception

    class Silent(Exception):
        pass

    assert describe_exception(Silent()) == "Silent"
    assert describe_exception(ValueError("  ")) == "ValueError"
    assert describe_exception(ValueError("real detail")) == "real detail"


def test_a_second_job_is_refused_rather_than_overwriting_the_first(qapp, monkeypatch) -> None:
    """The worker's only Python reference is the attribute the runner
    stores it in, so starting a second job over the top of a live one
    destroys a running QThread -- which Qt handles by aborting the
    process. Four copied runners had dropped this check.
    """
    from PySide6.QtWidgets import QDialog, QMessageBox

    from asset_catalogue.ui import jobs

    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    )
    owner = QDialog()
    started = jobs.run_background_job(
        owner, lambda report: time.sleep(1.0), "First...", lambda result: None
    )
    assert started
    first = owner._job_worker

    refused = jobs.run_background_job(
        owner, lambda report: None, "Second...", lambda result: None
    )
    assert refused is False
    assert owner._job_worker is first, "the live worker must not be replaced"
    assert jobs.wait_for_all_jobs(5000)


def test_running_jobs_sees_workers_that_keep_their_own_reference(qapp) -> None:
    """Two workers deliberately live outside the shared runner (the silent
    update check and the update downloader, which has its own progress
    bar). Shutdown has to wait on those too, so tracking is central rather
    than per-owner.
    """
    from asset_catalogue.ui import jobs

    assert jobs.wait_for_all_jobs(5000)
    assert jobs.running_jobs() == []

    worker = jobs.BackgroundWorker(lambda report: time.sleep(0.4))
    worker.start()
    _spin(80)
    assert worker in jobs.running_jobs()

    assert jobs.wait_for_all_jobs(5000)
    assert jobs.running_jobs() == []


def test_uncaught_main_thread_errors_are_announced(qapp, monkeypatch) -> None:
    """PySide6 routes a slot exception through sys.excepthook and carries
    on, which is right -- but it left the failure completely invisible,
    and a half-finished handler reads as "the button is broken".
    """
    from asset_catalogue import crash_log

    seen: list[str] = []
    monkeypatch.setattr(crash_log, "on_uncaught", seen.append)
    try:
        raise ValueError("something specific")
    except ValueError as exc:
        crash_log._log_uncaught_exception(type(exc), exc, exc.__traceback__)

    assert seen == ["ValueError: something specific"]


def test_reporting_a_failure_never_raises_a_second_one(qapp, monkeypatch) -> None:
    from asset_catalogue import crash_log

    def broken(_summary: str) -> None:
        raise RuntimeError("the reporter itself is broken")

    monkeypatch.setattr(crash_log, "on_uncaught", broken)
    try:
        raise ValueError("original")
    except ValueError as exc:
        crash_log._log_uncaught_exception(type(exc), exc, exc.__traceback__)
