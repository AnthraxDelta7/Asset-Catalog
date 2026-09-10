"""Running a long operation off the UI thread, and telling the user about it.

One runner, used by the main window and by every dialog that kicks off
work of its own. It used to be five near-identical copies, and the copies
had quietly diverged in the way copies do: the original refused to start
a second job while one was running, and not one of the four
reimplementations kept that check. The reason it exists is that the only
Python reference to a running QThread is the attribute the caller stores
it in, so starting a second job over the top of the first drops the first
mid-flight -- "QThread: Destroyed while thread is still running", which on
Windows aborts the process rather than warning about it.
"""

from __future__ import annotations

import time
import weakref

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

from asset_catalogue import crash_log
from asset_catalogue.ui.flowing_progress_bar import FlowingProgressBar

# How long a background job has to run before its progress dialog is
# worth putting on screen. Below this a window appears and disappears
# before it can even be read, and on Windows it never gets past the blank
# white client area painted before Qt's first frame -- so the only thing
# a short job's dialog communicates is a flash. Long enough to cover the
# many sub-second jobs here, short enough that anything genuinely slow
# still feels acknowledged rather than frozen.
PROGRESS_DIALOG_DELAY_MS = 400


# Every worker ever started, weakly held so a finished one can still be
# collected. Shutdown needs to wait on *all* of them, and the per-owner
# attributes miss the two that deliberately keep their own: the update
# check (silent, no progress dialog) and the update downloader (its own
# determinate bar). A thread nobody is tracking is exactly the one that
# gets destroyed while running.
_live_workers: weakref.WeakSet = weakref.WeakSet()


def running_jobs() -> list:
    return [worker for worker in _live_workers if worker.isRunning()]


def wait_for_all_jobs(timeout_ms: int = 5000) -> bool:
    """Blocks until every running job ends, up to roughly timeout_ms total.

    For application shutdown. Qt destroys a still-running QThread by
    aborting the process, so quitting mid-job either waits or dies -- and
    nothing here can cancel a Blender or Godot subprocess, so waiting is
    the only honest option. Returns False if something is still running
    when the budget runs out, which the caller reports rather than
    pretending it shut down cleanly.
    """
    deadline = time.monotonic() + timeout_ms / 1000
    for worker in running_jobs():
        remaining = int(max(0, deadline - time.monotonic()) * 1000)
        if remaining <= 0 or not worker.wait(remaining):
            return False
    return True


class BackgroundWorker(QThread):
    finished_ok = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, fn) -> None:
        super().__init__()
        self._fn = fn
        _live_workers.add(self)

    def run(self) -> None:
        try:
            result = self._fn(self.progress.emit)
        except Exception as exc:  # noqa: BLE001 -- reported to the UI, not swallowed
            # The QMessageBox this feeds only ever shows str(exc) -- often
            # far less informative than the real traceback, which is why
            # this is logged in full even though the failure itself is
            # already being handled gracefully, not just re-raised.
            crash_log.log_exception("Background job failed", exc)
            self.failed.emit(describe_exception(exc))
            return
        self.finished_ok.emit(result)


class ProgressLogDialog(QDialog):
    """Modal dialog shown during a background job.

    The bar is deliberately not a percentage. A job runs through phases
    with unrelated counts of their own -- "Pack 1/2", then "frame 5/24"
    -- and nothing in that text says how much of the whole job a phase
    represents, so any position derived from it jumps around and misleads.
    What a long run actually needs to convey is "still working", which the
    travelling sheen does honestly, alongside the current step and elapsed
    time.

    There is deliberately no Cancel button. These jobs are mostly Blender
    and Godot subprocesses with no cancellation path, so a Cancel that
    only greyed itself out and let the work continue would be a worse lie
    than not offering one.

    Shown on a delay rather than immediately -- see show_after. Most jobs
    here finish in well under a second, and a window that exists that
    briefly never gets past the blank white client area Windows paints
    before Qt's first frame. The dialog is dark once it renders; the flash
    was the window itself, not its styling.
    """

    def __init__(self, title: str, initial_text: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(560, 300)
        self._finished = False
        self._reveal = QTimer(self)
        self._reveal.setSingleShot(True)
        self._reveal.timeout.connect(self._reveal_now)

        layout = QVBoxLayout(self)

        self._step_label = QLabel(initial_text or "Working...")
        self._step_label.setWordWrap(True)
        step_font = self._step_label.font()
        step_font.setPointSize(step_font.pointSize() + 1)
        self._step_label.setFont(step_font)
        layout.addWidget(self._step_label)

        self._bar = FlowingProgressBar(self)
        layout.addWidget(self._bar)

        status_row = QHBoxLayout()
        status_row.addStretch(1)
        self._elapsed_label = QLabel("0:00")
        self._elapsed_label.setStyleSheet("color: #9a9a9a;")
        status_row.addWidget(self._elapsed_label)
        layout.addLayout(status_row)

        self._log = QPlainTextEdit(self)
        self._log.setReadOnly(True)
        log_font = self._log.font()
        log_font.setFamily("Consolas")
        self._log.setFont(log_font)
        layout.addWidget(self._log, stretch=1)

        self._started = time.monotonic()
        self._clock = QTimer(self)
        self._clock.timeout.connect(self._tick)
        self._clock.start(1000)

        if initial_text:
            self.append(initial_text)

    def show_after(self, delay_ms: int = PROGRESS_DIALOG_DELAY_MS) -> None:
        """Appear only if the job is still running `delay_ms` from now.

        A job that beats the delay never puts a window on screen at all,
        which is the right answer for the many that finish in a few
        hundred milliseconds: there is nothing to read, and the window is
        pure interruption. Progress text still accumulates in the
        meantime, so a job that does cross the threshold opens with its
        history already in place rather than an empty box.
        """
        self._reveal.start(delay_ms)

    def _reveal_now(self) -> None:
        if not self._finished and not self.isVisible():
            self.show()

    def _tick(self) -> None:
        seconds = int(time.monotonic() - self._started)
        self._elapsed_label.setText(f"{seconds // 60}:{seconds % 60:02d}")

    def append(self, text: str) -> None:
        self._step_label.setText(text)
        self._log.appendPlainText(text)
        scrollbar = self._log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _stop_timers(self) -> None:
        """Every timer this dialog owns, and the flag that keeps a
        late-firing one from reopening it.

        Called from both done() and close(), because a dialog that was
        never shown gets neither. QWidget.close() delivers a close event
        only to a visible widget, and QDialog reaches done() through that
        event -- so for the short jobs this class now stays hidden for,
        close() would return having stopped nothing, and the reveal timer
        would open the window several hundred milliseconds after the job
        it was reporting on had already finished.
        """
        self._finished = True
        self._bar.stop()
        self._clock.stop()
        self._reveal.stop()

    def close(self) -> bool:
        self._stop_timers()
        return super().close()

    def done(self, result: int) -> None:
        # The two repaint timers keep a closed dialog alive and burn a
        # frame every 33ms if left running.
        self._stop_timers()
        super().done(result)


def describe_exception(exc: BaseException) -> str:
    """A message that is never empty.

    Plenty of exceptions stringify to nothing at all -- a bare raise of a
    custom class, an OSError with no strerror -- and feeding that into a
    message box produces a dialog with a title, an OK button, and no
    indication whatsoever of what went wrong.
    """
    return str(exc).strip() or type(exc).__name__


def run_background_job(
    owner,
    fn,
    progress_text: str,
    on_ok,
    on_fail=None,
    set_busy=None,
    dialog_parent=None,
) -> bool:
    """Runs fn(report) on a worker thread, with a delayed progress dialog.

    Returns False without starting anything if `owner` already has a job
    in flight. That guard is the whole reason this is shared code: the
    worker's only Python reference is owner._job_worker, so overwriting it
    while the first thread is alive destroys a running QThread.

    on_fail defaults to a critical message box. set_busy, when given, is
    called with False while the job runs and True when it ends -- for a
    dialog that greys its own buttons rather than relying on the modal
    progress window to block input.
    """
    existing = getattr(owner, "_job_worker", None)
    if existing is not None and existing.isRunning():
        QMessageBox.information(
            owner,
            "Asset Catalogue",
            "Another background job is already running -- please wait for it to finish.",
        )
        return False

    if set_busy is not None:
        set_busy(False)
    # Parented to the modal dialog on top, when there is one: a progress
    # dialog parented to the main window would be blocked by an open modal
    # (the 3D preview) and never become visible.
    progress = ProgressLogDialog(
        "Asset Catalogue", progress_text, dialog_parent or QApplication.activeModalWidget() or owner
    )
    # Not shown yet -- most jobs finish first. See PROGRESS_DIALOG_DELAY_MS.
    progress.show_after()

    worker = BackgroundWorker(fn)

    def handle_ok(result) -> None:
        progress.close()
        if set_busy is not None:
            set_busy(True)
        on_ok(result)

    def handle_fail(message: str) -> None:
        progress.close()
        if set_busy is not None:
            set_busy(True)
        if on_fail is not None:
            on_fail(message)
        else:
            QMessageBox.critical(owner, "Asset Catalogue", message)

    def handle_finished() -> None:
        # Cleared here rather than left pointing at a worker that is about
        # to be destroyed, so the guard above can be checked at any time
        # without risking a shiboken "already deleted" error.
        if getattr(owner, "_job_worker", None) is worker:
            owner._job_worker = None
        worker.deleteLater()

    worker.progress.connect(progress.append, Qt.QueuedConnection)
    worker.finished_ok.connect(handle_ok, Qt.QueuedConnection)
    worker.failed.connect(handle_fail, Qt.QueuedConnection)
    worker.finished.connect(handle_finished)
    owner._job_worker = worker
    worker.start()
    return True


def wait_for_job(owner, timeout_ms: int = 5000) -> bool:
    """Blocks until owner's job ends, up to timeout_ms. True if it is done.

    For application shutdown: Qt destroys a still-running QThread by
    aborting the process, so quitting mid-job has to either wait for the
    thread or refuse to quit. Nothing here can cancel a Blender or Godot
    subprocess, so waiting is the only honest option.
    """
    worker = getattr(owner, "_job_worker", None)
    if worker is None or not worker.isRunning():
        return True
    return worker.wait(timeout_ms)
