"""Background execution so the interface never freezes.

``run_task`` runs a function in a worker thread and delivers its result (or a
friendly error message) back on the GUI thread via Qt signals. The function
receives a progress callback and a :class:`CancelToken`.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot

from ..data_access import CancelToken, Cancelled, DataAccessError
from ..export import ExportError
from ..processing import ProcessingError
from ..timeutil import TimeParseError

log = logging.getLogger("vitaldb_explorer")

_FRIENDLY = (DataAccessError, ExportError, ProcessingError, TimeParseError)


class _Worker(QObject):
    progress = Signal(str)
    done = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    finished = Signal()

    def __init__(self, fn: Callable, token: CancelToken):
        super().__init__()
        self._fn = fn
        self._token = token

    def run(self) -> None:
        try:
            result = self._fn(self.progress.emit, self._token)
        except Cancelled:
            self.cancelled.emit()
        except _FRIENDLY as exc:
            self.failed.emit(str(exc))
        except MemoryError:
            self.failed.emit("The computer ran out of memory for this request. Try a shorter time "
                             "interval or fewer high-frequency tracks.")
        except Exception as exc:  # unexpected: log full details, show a short message
            log.exception("Unexpected error in background task")
            self.failed.emit(f"Something unexpected went wrong ({type(exc).__name__}: {exc}). "
                             "The details were written to the log file in the application data folder.")
        else:
            self.done.emit(result)
        finally:
            self.finished.emit()


class _Relay(QObject):
    """Lives on the GUI thread; receives worker signals (queued) and runs the callbacks there.

    Connecting a worker signal straight to a plain Python callable would run the callable in the
    worker thread, so every GUI callback must go through a QObject owned by the GUI thread.
    """

    def __init__(self, on_done, on_error, on_progress, on_cancelled, on_finished):
        super().__init__()
        self._cb = (on_done, on_error, on_progress, on_cancelled, on_finished)

    @Slot(object)
    def done(self, result) -> None:
        if self._cb[0]:
            self._cb[0](result)

    @Slot(str)
    def failed(self, msg: str) -> None:
        if self._cb[1]:
            self._cb[1](msg)

    @Slot(str)
    def progress(self, msg: str) -> None:
        if self._cb[2]:
            self._cb[2](msg)

    @Slot()
    def cancelled(self) -> None:
        if self._cb[3]:
            self._cb[3]()

    @Slot()
    def finished(self) -> None:
        if self._cb[4]:
            self._cb[4]()


class TaskHandle:
    """Returned by run_task; lets the caller cancel the operation."""

    def __init__(self, thread: QThread, worker: _Worker, token: CancelToken, relay: _Relay):
        self.thread, self.worker, self.token, self.relay = thread, worker, token, relay

    def cancel(self) -> None:
        self.token.cancel()

    @property
    def running(self) -> bool:
        return self.thread.isRunning()


_ALIVE: set = set()


def run_task(parent: QObject, fn: Callable[[Callable[[str], None], CancelToken], Any],
             on_done: Callable[[Any], None], on_error: Optional[Callable[[str], None]] = None,
             on_progress: Optional[Callable[[str], None]] = None,
             on_cancelled: Optional[Callable[[], None]] = None,
             on_finished: Optional[Callable[[], None]] = None) -> TaskHandle:
    """Run ``fn(progress, token)`` in a thread; callbacks are always invoked on the GUI thread."""
    token = CancelToken()
    thread = QThread()
    worker = _Worker(fn, token)
    relay = _Relay(on_done, on_error, on_progress, on_cancelled, on_finished)   # created on the GUI thread
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    q = Qt.QueuedConnection
    worker.progress.connect(relay.progress, q)
    worker.done.connect(relay.done, q)
    worker.failed.connect(relay.failed, q)
    worker.cancelled.connect(relay.cancelled, q)
    worker.finished.connect(relay.finished, q)
    handle = TaskHandle(thread, worker, token, relay)
    worker.finished.connect(thread.quit)
    thread.finished.connect(lambda: _ALIVE.discard(handle), q)
    thread.finished.connect(thread.deleteLater)
    _ALIVE.add(handle)
    thread.start()
    return handle


def wait_all(timeout_ms: int = 3000) -> None:
    """Ask running tasks to stop (used on application exit)."""
    for h in list(_ALIVE):
        h.cancel()
    for h in list(_ALIVE):
        h.thread.quit()
        h.thread.wait(timeout_ms)
