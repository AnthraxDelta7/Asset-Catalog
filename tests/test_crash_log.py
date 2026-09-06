from __future__ import annotations

import logging
from pathlib import Path

from asset_catalogue import crash_log


def _reset_logger() -> None:
    """crash_log._logger is a module-level singleton (same reasoning as
    settings.SETTINGS_PATH being a module-level constant) -- tests must
    tear down whatever handlers a previous test attached, or setup_logging
    's own idempotency guard would make every test after the first a
    silent no-op against a stale (possibly already-closed) file handle.
    """
    for handler in list(crash_log._logger.handlers):
        crash_log._logger.removeHandler(handler)
        handler.close()


def test_setup_logging_creates_log_file(tmp_path: Path, monkeypatch) -> None:
    _reset_logger()
    log_path = tmp_path / "logs" / "asset_catalogue.log"
    monkeypatch.setattr(crash_log, "LOG_DIR", log_path.parent)
    monkeypatch.setattr(crash_log, "LOG_PATH", log_path)

    crash_log.setup_logging()

    assert log_path.exists()
    assert "starting" in log_path.read_text()
    _reset_logger()


def test_setup_logging_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    _reset_logger()
    log_path = tmp_path / "asset_catalogue.log"
    monkeypatch.setattr(crash_log, "LOG_DIR", log_path.parent)
    monkeypatch.setattr(crash_log, "LOG_PATH", log_path)

    crash_log.setup_logging()
    handler_count_after_first = len(crash_log._logger.handlers)
    crash_log.setup_logging()

    assert len(crash_log._logger.handlers) == handler_count_after_first
    _reset_logger()


def test_log_exception_writes_full_traceback(tmp_path: Path, monkeypatch) -> None:
    _reset_logger()
    log_path = tmp_path / "asset_catalogue.log"
    monkeypatch.setattr(crash_log, "LOG_DIR", log_path.parent)
    monkeypatch.setattr(crash_log, "LOG_PATH", log_path)
    crash_log.setup_logging()

    try:
        raise ValueError("a specific, identifiable failure")
    except ValueError as exc:
        crash_log.log_exception("Background job failed", exc)

    contents = log_path.read_text()
    assert "Background job failed" in contents
    assert "a specific, identifiable failure" in contents
    assert "Traceback" in contents
    _reset_logger()


def test_log_uncaught_exception_logs_without_raising(tmp_path: Path, monkeypatch) -> None:
    _reset_logger()
    log_path = tmp_path / "asset_catalogue.log"
    monkeypatch.setattr(crash_log, "LOG_DIR", log_path.parent)
    monkeypatch.setattr(crash_log, "LOG_PATH", log_path)
    crash_log.setup_logging()
    # The real hook also calls sys.__excepthook__, which would print to
    # stderr during a test run -- harmless, but silenced here so it
    # doesn't clutter pytest's own output.
    monkeypatch.setattr("sys.__excepthook__", lambda *args: None)

    try:
        raise RuntimeError("uncaught on the main thread")
    except RuntimeError:
        import sys

        crash_log._log_uncaught_exception(*sys.exc_info())

    assert "Uncaught exception on the main thread" in log_path.read_text()
    assert "RuntimeError" in log_path.read_text()
    _reset_logger()


def test_install_qt_message_handler_logs_a_real_qt_warning(tmp_path: Path, monkeypatch) -> None:
    """Qt's own C++-side warnings (a failed OpenGL context, a layout
    warning) never go through Python's exception machinery -- this
    triggers a real one via qWarning() (not a hand-called handler
    function) to confirm qInstallMessageHandler is actually wired up to
    this project's own log, not just that the handler closure would work
    in isolation if something called it.
    """
    from PySide6.QtCore import qWarning
    from PySide6.QtWidgets import QApplication

    _reset_logger()
    log_path = tmp_path / "asset_catalogue.log"
    monkeypatch.setattr(crash_log, "LOG_DIR", log_path.parent)
    monkeypatch.setattr(crash_log, "LOG_PATH", log_path)
    crash_log.setup_logging()

    QApplication.instance() or QApplication([])
    crash_log.install_qt_message_handler()
    try:
        qWarning("a specific, identifiable Qt warning")
        contents = log_path.read_text()
        assert "[Qt]" in contents
        assert "a specific, identifiable Qt warning" in contents
    finally:
        from PySide6.QtCore import qInstallMessageHandler

        qInstallMessageHandler(None)  # restore Qt's own default handler
        _reset_logger()
