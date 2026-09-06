"""Error/crash logging for the UI -- every uncaught exception (main thread
via sys.excepthook, a background _BackgroundWorker job, or Qt's own C++-side
messages via qInstallMessageHandler) gets a full traceback written to a log
file, not just printed to a console window most users running the packaged
.exe never see at all. Set up once, at the very top of main() before
QApplication is even constructed, so it's in place before anything that
could go wrong has a chance to.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
import traceback
from types import TracebackType

from asset_catalogue import settings

LOG_DIR = settings.SETTINGS_PATH.parent / "logs"
LOG_PATH = LOG_DIR / "asset_catalogue.log"

_logger = logging.getLogger("asset_catalogue")


def setup_logging() -> None:
    """Idempotent -- safe to call more than once (a second call is a
    no-op), so nothing needs to track whether this already ran.
    """
    if _logger.handlers:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # Rotates at 2MB, keeps 3 old copies -- a crash log left to grow
    # forever is its own kind of problem, and nobody needs more than a
    # few MB of history to diagnose a recent issue.
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)
    _logger.info("=== Asset Catalogue starting (version %s) ===", _version())

    sys.excepthook = _log_uncaught_exception


def _version() -> str:
    try:
        from asset_catalogue.version import __version__

        return __version__
    except Exception:  # noqa: BLE001 -- the log header itself must never be what crashes startup
        return "unknown"


def _log_uncaught_exception(
    exc_type: type[BaseException], exc_value: BaseException, exc_tb: TracebackType | None
) -> None:
    _logger.error(
        "Uncaught exception on the main thread:\n%s",
        "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
    )
    # Still hands off to Python's own default hook afterward -- when a
    # console is actually attached (running from source), the traceback
    # still shows up there too, same as before this existed.
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def log_exception(context: str, exc: BaseException) -> None:
    """For an exception the app already handles gracefully elsewhere (a
    background job reporting failure via a QMessageBox, say) -- captures
    the full traceback anyway, since str(exc) alone (all a QMessageBox
    ever showed) is often far less informative than the actual traceback
    for tracking down what actually went wrong.
    """
    _logger.error(
        "%s:\n%s", context, "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    )


def install_qt_message_handler() -> None:
    """Qt's own C++-side warnings/errors (a failed OpenGL context, a
    layout warning, a plugin load failure) never go through Python's
    exception machinery at all -- qInstallMessageHandler is the only way
    to actually see them, and they're exactly the kind of thing behind a
    "crash with no visible error" report, so they belong in the same log.
    """
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler

    _LEVELS = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }

    def handler(msg_type, context, message) -> None:  # noqa: ANN001 -- Qt's own callback signature
        _logger.log(_LEVELS.get(msg_type, logging.WARNING), "[Qt] %s", message)

    qInstallMessageHandler(handler)
