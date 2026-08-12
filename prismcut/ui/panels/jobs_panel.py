"""Job queue panel: every API call / render with live status, cancel and retry."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMenu, QProgressBar, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from ...core.jobs import Job, JobManager
from .. import theme
from ..widgets.common import STATUS_ICONS as ICONS
from ..widgets.common import confirm_destructive


class JobRow(QWidget):
    def __init__(self, job: Job, manager: JobManager, parent=None):
        super().__init__(parent)
        self.job = job
        self.manager = manager
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        self.icon = QLabel(ICONS.get(job.status, "⏳"))
        self.icon.setFixedWidth(22)
        self.title = QLabel(job.title)
        self.title.setToolTip(job.title)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)          # 0/0 = Qt's built-in animated "busy" mode
        self.bar.setFixedWidth(130)
        self.elapsed = QLabel("")
        self.elapsed.setFixedWidth(38)
        self.elapsed.setProperty("dim", "true")
        self.msg = QLabel("")
        self.msg.setProperty("dim", "true")
        self.retry = QPushButton("↻")
        self.retry.setFixedSize(24, 22)
        self.retry.setToolTip("Retry with the same settings")
        self.retry.setVisible(False)
        self.retry.clicked.connect(self._retry)
        self.cancel = QPushButton("✕")
        self.cancel.setFixedSize(24, 22)
        self.cancel.setToolTip("Cancel job")
        self.cancel.setAccessibleName(f"Cancel {job.title}")
        self.cancel.clicked.connect(job.cancel)
        lay.addWidget(self.icon)
        lay.addWidget(self.title, 2)
        lay.addWidget(self.bar)
        lay.addWidget(self.elapsed)
        lay.addWidget(self.msg, 3)
        lay.addWidget(self.retry)
        lay.addWidget(self.cancel)
        job.progressed.connect(self._progress)
        job.finished.connect(lambda *_: self.sync())
        job.failed.connect(lambda *_: self.sync())
        job.started.connect(self.sync)
        self._ticker = QTimer(self)
        self._ticker.setInterval(1000)
        self._ticker.timeout.connect(self._tick_elapsed)
        self._ticker.start()
        self.sync()

    def _tick_elapsed(self):
        if self.job.status in ("queued", "running"):
            secs = int(time.time() - self.job.created)
            self.elapsed.setText(f"{secs}s")
        else:
            self._ticker.stop()

    def _progress(self, pct: int, msg: str):
        if pct >= 0:
            self.bar.setRange(0, 100)
            self.bar.setValue(pct)
        else:
            self.bar.setRange(0, 0)
        if msg:
            self.msg.setText(msg[:90])

    def sync(self):
        st = self.job.status
        self.icon.setText(ICONS.get(st, "⏳"))
        if st in ("done", "error", "cancelled"):
            self.bar.setRange(0, 100)
            self.bar.setValue(100 if st == "done" else 0)
            self.cancel.setEnabled(False)
            self.retry.setVisible(st in ("error", "cancelled"))
            if st == "error" and self.job.error:
                self.msg.setText(str(self.job.error)[:120])
                self.msg.setStyleSheet(f"color:{theme.DANGER};")
                self.msg.setToolTip(str(self.job.error))

    def _retry(self):
        self.manager.submit(self.job.title, self.job.fn, self.job.kind,
                            on_done=self.job.on_done, on_fail=self.job.on_fail)

    def _menu(self, pos):
        menu = QMenu(self)
        if self.job.status in ("error", "cancelled"):
            menu.addAction("↻ Retry", self._retry)
        if self.job.status in ("queued", "running"):
            menu.addAction("✕ Cancel", self.job.cancel)
        menu.exec(self.mapToGlobal(pos))


class JobsPanel(QWidget):
    def __init__(self, manager: JobManager, settings, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.settings = settings
        self._rows: dict[Job, JobRow] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        top = QHBoxLayout()
        self.summary = QLabel("No jobs yet — generation, edits and renders appear here.")
        self.summary.setProperty("dim", "true")
        clear = QPushButton("Clear finished")
        clear.clicked.connect(self._clear)
        top.addWidget(self.summary, 1)
        top.addWidget(clear)
        outer.addLayout(top)
        self.list_host = QWidget()
        self.list_lay = QVBoxLayout(self.list_host)
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self.list_lay.setSpacing(1)
        self.list_lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(self.list_host)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll, 1)
        manager.jobAdded.connect(self._add)
        manager.jobsChanged.connect(self._sync_summary)

    def _add(self, job: Job):
        row = JobRow(job, self.manager)
        self._rows[job] = row
        self.list_lay.insertWidget(0, row)
        self._sync_summary()

    def _clear(self):
        finished = [j for j in self._rows if j.status in ("done", "error", "cancelled")]
        if not finished:
            return
        if not confirm_destructive(self, self.settings, "clear_finished_jobs",
                                   "Clear finished jobs",
                                   f"Clear {len(finished)} finished job(s) from this list? "
                                   "Their outputs (already saved to the bin/disk) are unaffected.",
                                   "Clear"):
            return
        for job in finished:
            self._rows[job].deleteLater()
            del self._rows[job]
        self.manager.clear_finished()

    def _sync_summary(self):
        active = self.manager.active_count()
        total = len(self._rows)
        self.summary.setText(f"{active} active · {total} total"
                             if total else "No jobs yet — generation, edits and renders appear here.")
