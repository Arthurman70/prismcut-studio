"""API key vault dialog - the "easy key drop-in". One row per provider with a
link to get the key, show/hide, Test button and advanced base-URL overrides."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QGridLayout, QLabel,
                               QLineEdit, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

from ...core.registry import Registry
from ...providers import make_adapter
from ..widgets.common import CollapsibleSection, label


class KeysDialog(QDialog):
    def __init__(self, settings, registry: Registry, jobs, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.registry = registry
        self.jobs = jobs
        self.setWindowTitle("API Keys")
        self.resize(760, 640)
        self.edits: dict[str, QLineEdit] = {}
        self.status_labels: dict[str, QLabel] = {}
        self.base_edits: dict[str, QLineEdit] = {}

        outer = QVBoxLayout(self)
        outer.addWidget(label(
            "Drop in a key for any provider you want to use - everything else keeps working "
            "without it. Keys are stored locally (OS keyring when available, otherwise app "
            "settings) and are only ever sent to that provider's own API. Environment "
            "variables (e.g. GEMINI_API_KEY) override what is stored here.", dim=True))

        host = QWidget()
        grid = QGridLayout(host)
        grid.setColumnStretch(2, 1)
        row = 0
        for name, spec in self.registry.providers.items():
            name_lbl = QLabel(f"<b>{spec.label}</b>")
            link = QLabel(f"<a href='{spec.key_url}'>get key ↗</a>" if spec.key_url else "")
            link.setOpenExternalLinks(True)
            edit = QLineEdit(self.settings.get_key(name, spec.key_env) or "")
            edit.setEchoMode(QLineEdit.EchoMode.Password)
            edit.setPlaceholderText(spec.key_env or "API key")
            show = QPushButton("👁")
            show.setFixedWidth(30)
            show.setCheckable(True)
            show.toggled.connect(lambda on, e=edit: e.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
            test = QPushButton("Test")
            test.setFixedWidth(52)
            test.clicked.connect(lambda _=False, n=name: self._test(n))
            status = QLabel("")
            status.setFixedWidth(20)
            self.edits[name] = edit
            self.status_labels[name] = status
            grid.addWidget(name_lbl, row, 0)
            grid.addWidget(link, row, 1)
            grid.addWidget(edit, row, 2)
            grid.addWidget(show, row, 3)
            grid.addWidget(test, row, 4)
            grid.addWidget(status, row, 5)
            if spec.notes:
                note = label(spec.notes, dim=True)
                grid.addWidget(note, row + 1, 2, 1, 3)
                row += 1
            row += 1

        scroll = QScrollArea()
        scroll.setWidget(host)
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll, 1)

        adv_host = QWidget()
        adv = QGridLayout(adv_host)
        r = 0
        for name, spec in self.registry.providers.items():
            adv.addWidget(QLabel(spec.label), r, 0)
            be = QLineEdit(str(self.settings.get(f"providers/{name}/base_url", "")))
            be.setPlaceholderText(spec.base_url)
            self.base_edits[name] = be
            adv.addWidget(be, r, 1)
            r += 1
        outer.addWidget(CollapsibleSection(
            "Advanced: endpoint / base-URL overrides (regions, gateways, self-hosted)",
            adv_host, expanded=False))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _save(self):
        for name, edit in self.edits.items():
            self.settings.set_key(name, edit.text())
        for name, be in self.base_edits.items():
            v = be.text().strip()
            if v:
                self.settings.set(f"providers/{name}/base_url", v)
            else:
                self.settings.q.remove(f"providers/{name}/base_url")
        self.accept()

    def _test(self, provider: str):
        # store the current text first so the adapter sees it
        self.settings.set_key(provider, self.edits[provider].text())
        v = self.base_edits[provider].text().strip()
        if v:
            self.settings.set(f"providers/{provider}/base_url", v)
        status = self.status_labels[provider]
        status.setText("⏳")

        def work(job):
            adapter = make_adapter(provider, self.settings, self.registry)
            return adapter.test_key()

        def done(result):
            ok, msg = result
            status.setText("✅" if ok else "❌")
            status.setToolTip(msg)

        self.jobs.submit(f"Test {provider} key", work, on_done=done,
                         on_fail=lambda m: (status.setText("❌"), status.setToolTip(m)))
