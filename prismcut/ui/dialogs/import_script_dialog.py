"""Import an existing script: the user pastes a script/outline they already
wrote, an AI call structures it into Scene objects (script + narration per
beat) - reuses pipeline_orchestrator's own JSON-array parse contract
(_parse_breakdown) so the result is indistinguishable from a normal
script-breakdown's output, just extracted rather than invented. Doesn't
touch PipelineRun/MoviePipeline at all - NewPipelineDialog hasn't created a
pipeline yet at the point this dialog runs, so it works standalone off
just a script model and hands its result (self.scenes) back for the
caller to attach."""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QMessageBox, QPlainTextEdit, QVBoxLayout

from ...core.pipeline_orchestrator import _parse_breakdown
from ...providers.base import ChatMessage
from ..widgets.common import SpellCheckHighlighter, label

_SYS_PROMPT = (
    "You extract scenes from a movie/show script the user already wrote, for an AI "
    "generation pipeline. Do NOT invent new scenes, dialogue, or events - use ONLY what's "
    "already in the provided script, just structure it. Respond with NOTHING but a JSON "
    "array - the very first character of your reply must be '[' and the very last must be "
    "']'. No greeting, no explanation, no markdown code fence, no text before or after the "
    'array. Each array element is an object with "script" (the on-screen visual/action '
    'description for that scene/beat, drawn from the source script) and "narration" (the '
    "dialogue or voiceover text spoken during that scene, drawn from the source script, or "
    "an empty string for a silent, visual-only beat).")


class ImportScriptDialog(QDialog):
    def __init__(self, script_model, get_adapter, jobs, parent=None):
        super().__init__(parent)
        self.script_model = script_model
        self.get_adapter = get_adapter
        self.jobs = jobs
        self.scenes: list = []
        self.setWindowTitle("Import your own script")
        self.resize(560, 480)
        v = QVBoxLayout(self)
        v.addWidget(label(
            "Paste your script or a scene-by-scene outline below - the AI structures it "
            "into scenes without inventing new content, so you keep full control of the "
            "story and dialogue.", dim=True))
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText(
            "Scene 1: A robot wakes up in an empty workshop, dust in the light.\n"
            "Dialogue: \"Where... am I?\"\n\n"
            "Scene 2: It finds an old paintbrush on the workbench.\n...")
        SpellCheckHighlighter(self.text_edit.document())
        v.addWidget(self.text_edit, 1)

        buttons = QDialogButtonBox()
        self.go = buttons.addButton("📥 Import", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._import)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

    def _import(self):
        text = self.text_edit.toPlainText().strip()
        if not text:
            QMessageBox.information(self, "Nothing to import", "Paste your script first.")
            return
        if self.script_model is None:
            QMessageBox.information(self, "No model", "Pick a script model first.")
            return
        adapter = self.get_adapter(self.script_model.provider)

        def work(job):
            job.progress(-1, "Structuring your script into scenes")
            return adapter.chat(self.script_model.id, [ChatMessage("user", text)],
                                system=_SYS_PROMPT, temperature=0.1)

        def done(result):
            scenes = _parse_breakdown(str(result))
            if not scenes:
                snippet = str(result).strip()[:200] or "(empty response)"
                QMessageBox.warning(
                    self, "Import failed",
                    f"Couldn't structure your script into scenes. The AI's response started: "
                    f"“{snippet}”")
                self.go.setEnabled(True)
                return
            self.scenes = scenes
            self.accept()

        def fail(msg):
            QMessageBox.warning(self, "Import failed", str(msg))
            self.go.setEnabled(True)

        self.go.setEnabled(False)
        self.jobs.submit("Import movie script", work, kind="chat", on_done=done, on_fail=fail)
