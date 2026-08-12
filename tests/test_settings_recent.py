import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings

from prismcut.core.settings import Settings


def _isolated_settings(tmp_path) -> Settings:
    # Point QSettings at a throwaway ini file so tests never touch the
    # user's real PrismCut/PrismCutStudio registry-backed settings.
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    s = Settings()
    s.q = QSettings(str(tmp_path / "test_settings.ini"), QSettings.Format.IniFormat)
    return s


def test_add_recent_dedupes_and_orders_most_recent_first(tmp_path):
    s = _isolated_settings(tmp_path)
    s.add_recent("projects", "a.pcut")
    s.add_recent("projects", "b.pcut")
    s.add_recent("projects", "a.pcut")  # re-opening a moves it back to front
    assert s.recent("projects") == ["a.pcut", "b.pcut"]


def test_recent_respects_limit(tmp_path):
    s = _isolated_settings(tmp_path)
    for i in range(15):
        s.add_recent("projects", f"p{i}.pcut", limit=10)
    assert len(s.recent("projects")) == 10
    assert s.recent("projects")[0] == "p14.pcut"


def test_clear_recent(tmp_path):
    s = _isolated_settings(tmp_path)
    s.add_recent("projects", "a.pcut")
    s.clear_recent("projects")
    assert s.recent("projects") == []
