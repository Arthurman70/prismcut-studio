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


def test_get_bool_survives_qsettings_string_roundtrip(tmp_path):
    """Bug fix: QSettings can round-trip a saved False as the literal
    string "false" (reproduced here with the same IniFormat backend the
    app uses whenever PRISMCUT_DATA_DIR is set) - bool("false") is True in
    Python, so a naive bool(settings.get(key)) silently read a saved "off"
    toggle back as "on". A fresh Settings() re-reading the same file
    matches what actually happens on the next app launch."""
    s = _isolated_settings(tmp_path)
    s.set("some/flag", False)
    s2 = _isolated_settings(tmp_path)
    assert s2.get_bool("some/flag", True) is False


def test_get_bool_true_roundtrips_and_default_applies_when_unset(tmp_path):
    s = _isolated_settings(tmp_path)
    s.set("some/flag", True)
    s2 = _isolated_settings(tmp_path)
    assert s2.get_bool("some/flag", False) is True
    assert s2.get_bool("never/set", True) is True
    assert s2.get_bool("never/set", False) is False
