"""Job queue panel: every API call / render with live status and cancel."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QProgressBar, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from ...core.jobs import Job, JobManager
from .. import theme

ICONS = {"queued": "⏳", "running": "⚙️", "done": "✅", "error": "❌", "cancelled": "🚫"}


class JobRow(QWidget):
    def __init__(self, job: Job, parent=None):
        super().__init__(parent)
        self.job = job
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        self.icon = QLabel(ICONS.get(job.status, "⏳"))
        self.icon.setFixedWidth(22)
        self.title = QLabel(job.title)
        self.title.setToolTip(job.title)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setFixedWidth(130)
        self.msg = QLabel("")
        self.msg.setProperty("dim", "true")
        self.cancel = QPushButton("✕")
        self.cancel.setFixedSize(24, 22)
        self.cancel.setToolTip("Cancel job")
        self.cancel.clicked.connect(job.cancel)
        lay.addWidget(self.icon)
        lay.addWidget(self.title, 2)
        lay.addWidget(self.bar)
        lay.addWidget(self.msg, 3)
        lay.addWidget(self.cancel)
        job.progressed.connect(self._progress)
        job.finished.connect(lambda *_: self.sync())
        job.failed.connect(lambda *_: self.sync())
        job.started.connect(self.sync)
        self.sync()

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
            if st == "error" and self.job.error:
                self.msg.setText(str(self.job.error)[:120])
                self.msg.setStyleSheet(f"color:{theme.DANGER};")
                self.msg.setToolTip(str(self.job.error))


class JobsPanel(QWidget):
    def __init__(self, manager: JobManager, parent=None):
        super().__init__(parent)
        self.manager = manager
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
        row = JobRow(job)
        self._rows[job] = row
        self.list_lay.insertWidget(0, row)
        self._sync_summary()

    def _clear(self):
        for job, row in list(self._rows.items()):
            if job.status in ("done", "error", "cancelled"):
                row.deleteLater()
                del self._rows[job]
        self.manager.clear_finished()

    def _sync_summary(self):
        active = self.manager.active_count()
        total = len(self._rows)
        self.summary.setText(f"{active} active · {total} total"
                             if total else "No jobs yet — generation, edits and renders appear here.")
