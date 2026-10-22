"""Check packaged resources and boot a disposable offscreen app, without API calls."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[1]
exe = ROOT / 'dist/PrismCut/PrismCut.exe'
archive = CArchiveReader(str(exe))
pyz_name = next(name for name in archive.toc if name.endswith('.pyz'))
modules = archive.open_embedded_archive(pyz_name)
for name in ('prismcut.providers.meta', 'prismcut.providers.openai_responses',
             'prismcut.ui.dialogs.model_manager'):
    assert name in modules.toc, name
version = {}
exec(modules.extract('prismcut'), version)
assert version['__version__'] == '1.9.0'
catalog = json.loads((exe.parent / '_internal/prismcut/assets/models.json').read_text(encoding='utf-8'))
assert len(catalog['models']) == 98
assert len([m for m in catalog['models'] if m['provider'] == 'meta']) == 6

with tempfile.TemporaryDirectory(prefix='packaged-smoke-', dir=ROOT) as folder:
    env = {**os.environ, 'PRISMCUT_DATA_DIR': folder, 'QT_QPA_PLATFORM': 'offscreen'}
    log = ROOT / 'packaged-smoke.log'
    with log.open('wb') as output:
        process = subprocess.Popen([str(exe)], env=env, stdout=output, stderr=output,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            time.sleep(8)
            assert process.poll() is None, f'Packaged app exited: {process.returncode}'
            assert (Path(folder) / 'autosave').exists(), 'Main window did not finish initialization; see packaged-smoke.log'
        finally:
            # Only stop this disposable process, never another PrismCut session.
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
    text = log.read_text(encoding='utf-8', errors='replace')
    assert 'Traceback' not in text and 'Failed to execute script' not in text, text[-3000:]
print('Packaged 1.9.0 boot passed: adapters present, 98 models, six Muse models, no startup traceback.')
