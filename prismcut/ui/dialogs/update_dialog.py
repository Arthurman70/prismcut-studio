"""Update-available dialog: release notes + one action button, whose label
and behavior depend on how this copy of the app was installed (see
core.updater.installed_location()) - self-updating installed copies get
"Update & Restart", everything else (portable zip, dev run from source)
gets "Download Installer" + a link to the release page."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QLabel,
                               QMessageBox, QProgressBar, QTextBrowser, QVBoxLayout)

from ... import __version__
from ...core import updater


class UpdateDialog(QDialog):
    def __init__(self, release: updater.ReleaseInfo, jobs, settings, parent=None):
        super().__init__(parent)
        self.release = release
        self.jobs = jobs
        self.settings = settings
        self.install_dir = updater.installed_location()
        self.setWindowTitle("Update available")
        self.resize(560, 460)
        lay = QVBoxLayout(self)

        lay.addWidget(QLabel(f"<b>{release.name}</b> is available (you have {__version__})."))

        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(release.notes or "_No release notes provided._")
        lay.addWidget(notes, 1)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        lay.addWidget(self.progress)

        self.status_label = QLabel("")
        self.status_label.setProperty("dim", "true")
        lay.addWidget(self.status_label)

        self.skip_check = QCheckBox("Don't ask again for this version")
        lay.addWidget(self.skip_check)

        buttons = QDialogButtonBox()
        self.go = None
        if self.install_dir and release.asset_url:
            self.go = buttons.addButton("⬇ Update && Restart", QDialogButtonBox.ButtonRole.AcceptRole)
            self.go.clicked.connect(self._self_update)
        elif release.asset_url:
            self.go = buttons.addButton("⬇ Download Installer", QDialogButtonBox.ButtonRole.AcceptRole)
            self.go.clicked.connect(self._download_only)
        page_btn = buttons.addButton("🌐 Release Page", QDialogButtonBox.ButtonRole.HelpRole)
        page_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(release.html_url)))
        later_btn = buttons.addButton("Remind me later", QDialogButtonBox.ButtonRole.RejectRole)
        later_btn.clicked.connect(self._remind_later)
        lay.addWidget(buttons)

    # ---------------------------------------------------------------- shared
    def _maybe_skip(self):
        if self.skip_check.isChecked():
            self.settings.set("updater/skip_version", self.release.tag)

    def _remind_later(self):
        self._maybe_skip()
        self.reject()

    def _busy(self, msg: str):
        self.status_label.setText(msg)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        if self.go:
            self.go.setEnabled(False)

    def _on_progress(self, pct: int, msg: str):
        if pct >= 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(pct)
        else:
            self.progress.setRange(0, 0)
        if msg:
            self.status_label.setText(msg)

    def _fail(self, msg: str):
        self.status_label.setText(f"Failed: {msg}")
        self.progress.setVisible(False)
        if self.go:
            self.go.setEnabled(True)

    # ------------------------------------------------------------ self-update
    def _self_update(self):
        # This dialog is modal (exec()'d), so the main window can't be
        # touched while this runs - checking dirty state once, right here,
        # is enough; nothing can make it dirty again before done() fires.
        # Quitting via a hard QApplication.quit() would skip MainWindow.
        # closeEvent's unsaved-changes prompt entirely and could silently
        # discard the user's project - block up front instead.
        win = self.parent()
        project = getattr(win, "project", None)
        if project is not None and project.dirty and project.clips:
            QMessageBox.warning(self, "Unsaved changes",
                                "Save your project first — updating restarts the app, and "
                                "unsaved changes would be lost.")
            return
        self._maybe_skip()
        self._busy("Downloading update…")
        release = self.release

        def work(job):
            def on_chunk(written, total):
                job.progress(int(written * 100 / total) if total else -1, "Downloading update…")
            updater.perform_self_update(release, on_chunk=on_chunk)

        def done(_result):
            # The installer is already running at this point, but it can't
            # actually replace this process's own locked .exe/DLLs until we
            # exit - closing right away is what lets it finish (see
            # core.updater.perform_self_update's docstring). Its own [Run]
            # postinstall entry relaunches the app once install completes.
            self.status_label.setText("Closing so the update can finish installing…")
            self.accept()   # end this modal dialog's own event loop first
            if win is not None:
                # Deferred one tick so it runs after this dialog has actually
                # finished closing, rather than closing the parent window out
                # from under a still-executing child modal.
                QTimer.singleShot(0, win.close)
            else:
                QApplication.instance().quit()

        job = self.jobs.submit(f"Update to {release.tag}", work, kind="update",
                               on_done=done, on_fail=self._fail)
        job.progressed.connect(self._on_progress)

    # ----------------------------------------------------------- manual path
    def _download_only(self):
        self._maybe_skip()
        self._busy("Downloading installer…")
        release = self.release

        def work(job):
            def on_chunk(written, total):
                job.progress(int(written * 100 / total) if total else -1, "Downloading installer…")
            return updater.download_installer(release, on_chunk=on_chunk)

        def done(path):
            self.status_label.setText(f"Saved to {path}")
            self.progress.setVisible(False)
            if self.go:
                self.go.setEnabled(True)
            if sys.platform.startswith("win"):
                try:
                    os.startfile(Path(path).parent)
                except OSError:
                    pass

        job = self.jobs.submit(f"Download {release.asset_name or release.tag}", work,
                               kind="update", on_done=done, on_fail=self._fail)
        job.progressed.connect(self._on_progress)
