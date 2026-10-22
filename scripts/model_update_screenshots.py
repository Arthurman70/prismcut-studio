"""Render the updated dialogs using isolated settings and no real API keys."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['PRISMCUT_DATA_DIR'] = str(ROOT / '.model-ui-review')

from PySide6.QtWidgets import QApplication, QScrollArea
from PySide6.QtGui import QFontDatabase, QFont
from prismcut.core.settings import Settings
from prismcut.core.registry import Registry
from prismcut.ui import theme
from prismcut.ui.dialogs.model_manager import ModelManagerDialog
from prismcut.ui.dialogs.keys_dialog import KeysDialog


class BlankKeys:
    def get(self, key, default=None):
        return default

    def get_key(self, provider, env=''):
        return None


app = QApplication([])
# Qt's offscreen backend does not enumerate Windows system fonts reliably.
# Load them explicitly for the documentation render; the real app uses Qt Windows.
for font in ('segoeui.ttf', 'segoeuib.ttf', 'seguiemj.ttf'):
    QFontDatabase.addApplicationFont(str(Path(os.environ['WINDIR']) / 'Fonts' / font))
app.setFont(QFont('Segoe UI', 10))
settings = Settings()
settings.set('ui/theme', 'dark')
theme.apply_theme(app, settings)
registry = Registry()
shots = ROOT / 'docs/screenshots'
shots.mkdir(parents=True, exist_ok=True)
dialog = ModelManagerDialog(registry, settings)
dialog.resize(1150, 570)
dialog.search.setText('Muse')
dialog.show()
app.processEvents()
assert dialog.grab().save(str(shots / 'muse-models.png'))
dialog.close()
keys = KeysDialog(BlankKeys(), registry, None)
keys.resize(1000, 700)
keys.show()
app.processEvents()
keys.findChild(QScrollArea).ensureWidgetVisible(keys.edits['meta'])
app.processEvents()
assert keys.grab().save(str(shots / 'muse-api-key.png'))
keys.close()
print('Rendered model search and Meta API-key dialogs without real credentials.')
