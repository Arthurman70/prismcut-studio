# Contributing to PrismCut Studio

Thanks for helping build the open AI editing suite!

## Dev setup

```bash
git clone https://github.com/Arthurman70/prismcut-studio
cd prismcut-studio
python -m venv .venv && . .venv/bin/activate   # .venv\Scripts\activate on Windows
pip install -e .[dev]
python -m prismcut          # run the app
pytest                      # offline tests (no API keys needed)
```

Headless test of the full UI (CI does this):

```bash
QT_QPA_PLATFORM=offscreen python -c "
from PySide6.QtWidgets import QApplication
from prismcut.ui.main_window import MainWindow
app = QApplication([]); MainWindow().show()"
```

## Adding a provider

1. Create `prismcut/providers/yourprovider.py` subclassing `Adapter`
   (or `OpenAICompatChat` if the chat API is OpenAI-shaped).
2. Implement only the capabilities the provider has; the base class raises
   friendly `NotSupported` errors for the rest.
3. Route every HTTP call through `prismcut.core.http` (so tests can
   monkeypatch) and save outputs with `self._save_bytes()` / `self._out()`.
4. Register the class in `providers/__init__.py`, add the provider + models
   to `prismcut/assets/models.json`.
5. Add a payload test in `tests/test_payloads.py` (mock `http.request_json`).

## Adding a Nano Tool

Edit `prismcut/assets/nano_tools.json` — `{strength}` in the prompt is
replaced by the strength slider wording; `"needs_mask": true` requires a
painted mask. Users can also add tools in-app (stored in the app data dir).

## Guidelines

- Keep adapters Qt-free; UI code never blocks (use the JobManager).
- No GPL code (this repo is MIT). Kdenlive may inspire UX, never source.
- Don't hardcode model ids in Python - registry only.
- `pytest` must pass offline.

## Releasing

Tag `vX.Y.Z` → GitHub Actions builds and attaches the Windows zip
automatically. Bump `prismcut/__init__.py` + `pyproject.toml` first.
