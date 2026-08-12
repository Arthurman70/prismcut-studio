"""core.updater - version compare, release-check parsing, install detection
and the self-update subprocess steps. Every network call is mocked
(monkeypatched prismcut.core.http.request_json / download) and every
subprocess/registry call is mocked too - this module must never hit GitHub,
run a real installer, or touch the real Windows registry during tests."""
import subprocess
import sys

import pytest

from prismcut.core import http as http_mod
from prismcut.core import updater


# ------------------------------------------------------------- version compare

@pytest.mark.parametrize("remote,local,expected", [
    ("v0.2.0", "0.1.0", True),
    ("0.1.0", "0.1.0", False),
    ("v0.0.9", "0.1.0", False),
    ("1.0.0", "0.1.0", True),
    ("0.1.0.1", "0.1.0", True),   # extra trailing component still counts as newer
    ("0.10.0", "0.9.0", True),    # numeric, not lexicographic, comparison
])
def test_is_newer(remote, local, expected):
    assert updater.is_newer(remote, local) is expected


def test_is_newer_defaults_to_the_running_app_version(monkeypatch):
    monkeypatch.setattr(updater, "__version__", "1.0.0")
    assert updater.is_newer("1.1.0") is True


# ----------------------------------------------------------------- check_latest

def test_check_latest_parses_tag_name_and_notes(monkeypatch):
    payload = {"tag_name": "v9.9.9", "name": "PrismCut 9.9.9", "body": "## Changelog\n- stuff",
              "html_url": "https://github.com/Arthurman70/prismcut-studio/releases/tag/v9.9.9",
              "assets": [{"name": "PrismCut-Studio-Setup-9.9.9.exe",
                         "browser_download_url": "https://example.test/setup.exe"},
                        {"name": "PrismCut-windows-x64.zip",
                         "browser_download_url": "https://example.test/portable.zip"}]}
    monkeypatch.setattr(http_mod, "request_json", lambda *a, **k: payload)

    release = updater.check_latest()

    assert release.tag == "v9.9.9"
    assert release.name == "PrismCut 9.9.9"
    assert "Changelog" in release.notes
    assert release.html_url.endswith("v9.9.9")
    if sys.platform.startswith("win"):
        # picks the installer asset, not the portable zip
        assert release.asset_name == "PrismCut-Studio-Setup-9.9.9.exe"
        assert release.asset_url == "https://example.test/setup.exe"


def test_check_latest_returns_none_without_a_tag(monkeypatch):
    monkeypatch.setattr(http_mod, "request_json", lambda *a, **k: {"tag_name": ""})
    assert updater.check_latest() is None


def test_check_latest_handles_a_release_with_no_installer_asset(monkeypatch):
    monkeypatch.setattr(http_mod, "request_json",
                        lambda *a, **k: {"tag_name": "v1.2.3", "assets": []})
    release = updater.check_latest()
    assert release.tag == "v1.2.3"
    assert release.asset_url == ""


# -------------------------------------------------------------- install detect

def test_installed_location_none_when_registry_key_missing(monkeypatch):
    if not sys.platform.startswith("win"):
        pytest.skip("Windows-only code path")
    import winreg

    def fake_open_key(*a, **k):
        raise OSError("not found")
    monkeypatch.setattr(winreg, "OpenKey", fake_open_key)
    assert updater.installed_location() is None


def test_installed_location_none_when_running_exe_is_outside_install_dir(monkeypatch, tmp_path):
    if not sys.platform.startswith("win"):
        pytest.skip("Windows-only code path")
    import winreg

    install_dir = tmp_path / "installed_copy"
    install_dir.mkdir()
    unrelated_dir = tmp_path / "somewhere_else"
    unrelated_dir.mkdir()

    class FakeKey:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: FakeKey())
    monkeypatch.setattr(winreg, "QueryValueEx", lambda *a, **k: (str(install_dir), 1))
    monkeypatch.setattr(sys, "executable", str(unrelated_dir / "python.exe"))

    # A stale registry entry (app since moved/reinstalled elsewhere) must not
    # be mistaken for "this running copy is the installed one."
    assert updater.installed_location() is None


def test_installed_location_matches_when_running_exe_is_inside_install_dir(monkeypatch, tmp_path):
    if not sys.platform.startswith("win"):
        pytest.skip("Windows-only code path")
    import winreg

    install_dir = tmp_path / "installed_copy"
    install_dir.mkdir()

    class FakeKey:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: FakeKey())
    monkeypatch.setattr(winreg, "QueryValueEx", lambda *a, **k: (str(install_dir), 1))
    monkeypatch.setattr(sys, "executable", str(install_dir / "PrismCut.exe"))

    assert updater.installed_location() == install_dir


# ---------------------------------------------------------------- self-update

def test_download_installer_requires_an_asset_url():
    release = updater.ReleaseInfo(tag="v1", name="v1", notes="", html_url="", asset_url="")
    with pytest.raises(ValueError):
        updater.download_installer(release)


def test_download_installer_downloads_to_cache_dir(monkeypatch, tmp_path):
    from prismcut.core import paths as paths_mod

    monkeypatch.setattr(paths_mod, "app_data_dir", lambda: tmp_path)
    calls = []

    def fake_download(url, dest, **kwargs):
        calls.append((url, dest))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake installer bytes")
        return dest

    monkeypatch.setattr(http_mod, "download", fake_download)
    release = updater.ReleaseInfo(tag="v1", name="v1", notes="", html_url="",
                                  asset_url="https://example.test/setup.exe",
                                  asset_name="Setup.exe")

    out = updater.download_installer(release)

    assert out.name == "Setup.exe"
    assert out.exists()
    assert calls[0][0] == "https://example.test/setup.exe"


def test_run_silent_install_raises_on_nonzero_exit(monkeypatch, tmp_path):
    fake_installer = tmp_path / "Setup.exe"
    fake_installer.write_bytes(b"")

    class FakeResult:
        returncode = 1
        stdout = b""
        stderr = b"install failed"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeResult())

    with pytest.raises(RuntimeError, match="install failed"):
        updater.run_silent_install(fake_installer)


def test_run_silent_install_succeeds_on_zero_exit(monkeypatch, tmp_path):
    fake_installer = tmp_path / "Setup.exe"
    fake_installer.write_bytes(b"")

    class FakeResult:
        returncode = 0
        stdout = b""
        stderr = b""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeResult())

    updater.run_silent_install(fake_installer)   # must not raise


def test_relaunch_from_returns_false_when_exe_missing(tmp_path):
    assert updater.relaunch_from(tmp_path, "NoSuchApp.exe") is False


def test_relaunch_from_launches_when_exe_present(monkeypatch, tmp_path):
    exe = tmp_path / "PrismCut.exe"
    exe.write_bytes(b"")
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: calls.append((a, k)))

    assert updater.relaunch_from(tmp_path, "PrismCut.exe") is True
    assert len(calls) == 1


def test_perform_self_update_raises_a_clear_error_if_relaunch_target_is_missing(
        monkeypatch, tmp_path):
    """download + install succeed, but the exe isn't where expected afterward
    (e.g. an installer that changed its layout) - must fail loudly, not
    silently leave the user thinking the update finished."""
    release = updater.ReleaseInfo(tag="v1", name="v1", notes="", html_url="",
                                  asset_url="https://example.test/setup.exe",
                                  asset_name="Setup.exe")
    monkeypatch.setattr(updater, "download_installer", lambda *a, **k: tmp_path / "Setup.exe")
    monkeypatch.setattr(updater, "run_silent_install", lambda *a, **k: None)

    empty_install_dir = tmp_path / "empty"
    empty_install_dir.mkdir()
    with pytest.raises(RuntimeError, match="wasn't found"):
        updater.perform_self_update(release, empty_install_dir)
