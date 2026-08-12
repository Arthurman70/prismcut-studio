"""Background job system: every API call and render runs off the GUI thread."""
from __future__ import annotations

import threading
import time
import traceback
from typing import Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class Job(QObject):
    started = Signal()
    progressed = Signal(int, str)      # percent (-1 = indeterminate), message
    finished = Signal(object)          # result
    failed = Signal(str)               # error message

    def __init__(self, title: str, fn: Callable[["Job"], object], kind: str = "api"):
        super().__init__()
        self.title = title
        self.fn = fn
        self.kind = kind
        self.status = "queued"         # queued | running | done | error | cancelled
        self.message = ""
        self.percent = -1
        self.result = None
        self.error: Optional[str] = None
        self.created = time.time()
        self._cancel = threading.Event()
        # Stashed by JobManager.submit() purely so a UI can offer "Retry"
        # (re-submit with the exact same callbacks) without re-deriving them.
        self.on_done: Optional[Callable] = None
        self.on_fail: Optional[Callable] = None

    # Called from worker threads --------------------------------------------
    def progress(self, percent: int = -1, message: str = "") -> None:
        self.percent = percent
        if message:
            self.message = message
        self.progressed.emit(percent, message)

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def check_cancelled(self) -> None:
        if self.cancelled:
            raise RuntimeError("Cancelled by user")

    # Called from the GUI thread --------------------------------------------
    def cancel(self) -> None:
        self._cancel.set()
        if self.status in ("queued", "running"):
            self.status = "cancelled"


def _safe_emit(signal, *args) -> None:
    """A job's worker thread can outlive the Job QObject (or whatever owns
    it) if the app/window closes while the job is still in flight - Qt then
    raises 'Signal source has been deleted' on .emit(). At that point there's
    nothing left to notify anyway, so swallow just that specific failure
    mode rather than letting it propagate as an unhandled exception inside
    QThreadPool (which the ORIGINAL bug did doubly: the finished.emit()
    RuntimeError would itself be caught by the generic except Exception
    below, which then tried failed.emit() on the same deleted object,
    raising again - uncaught the second time)."""
    try:
        signal.emit(*args)
    except RuntimeError:
        pass


class _Runner(QRunnable):
    def __init__(self, job: Job):
        super().__init__()
        self.job = job
        self.setAutoDelete(True)

    def run(self) -> None:
        job = self.job
        if job.cancelled:
            return
        job.status = "running"
        _safe_emit(job.started)
        try:
            result = job.fn(job)
            if job.cancelled:
                job.status = "cancelled"
                _safe_emit(job.failed, "Cancelled")
                return
            job.result = result
            job.status = "done"
            _safe_emit(job.finished, result)
        except Exception as exc:  # noqa: BLE001 - surface everything to the UI
            job.status = "cancelled" if job.cancelled else "error"
            job.error = str(exc) or exc.__class__.__name__
            traceback.print_exc()
            _safe_emit(job.failed, job.error)


class JobManager(QObject):
    jobAdded = Signal(object)
    jobsChanged = Signal()

    def __init__(self, max_threads: int = 6):
        super().__init__()
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(max_threads)
        self.jobs: list[Job] = []

    def submit(self, title: str, fn: Callable[[Job], object], kind: str = "api",
               on_done: Optional[Callable] = None, on_fail: Optional[Callable] = None) -> Job:
        job = Job(title, fn, kind)
        job.on_done, job.on_fail = on_done, on_fail
        self.jobs.append(job)
        for sig in (job.started, job.finished, job.failed, job.progressed):
            sig.connect(lambda *_a, **_k: self.jobsChanged.emit())
        if on_done:
            job.finished.connect(on_done)
        if on_fail:
            job.failed.connect(on_fail)
        self.jobAdded.emit(job)
        self.pool.start(_Runner(job))
        return job

    def active_count(self) -> int:
        return sum(1 for j in self.jobs if j.status in ("queued", "running"))

    def clear_finished(self) -> None:
        self.jobs = [j for j in self.jobs if j.status in ("queued", "running")]
        self.jobsChanged.emit()
