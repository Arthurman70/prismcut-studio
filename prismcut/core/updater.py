"""Self-updater: checks GitHub Releases for a newer tag, and for copies
installed via the Inno Setup installer (packaging/installer.iss), can
silently download and hand off to the installer. Portable-zip and
from-source runs are detected by the same single check (no matching
registry entry) and just get a "here's the download, go grab it"
experience instead - see installed_location().

The installer can't overwrite this process's own running .exe/DLLs while
we're still alive to hold Windows' lock on them, so perform_self_update()
only downloads and launches the installer detached - it's the CALLER's job
to close this app immediately afterward (see launch_installer_detached's
docstring), and packaging/installer.iss's own [Run] postinstall entry that
relaunches the app once install actually completes.

Qt-free plain functions by design (same style as core/pipeline.py) - a
version check or a download is "just another job", submitted through
jobs.submit() by the UI layer exactly like every other network call in this
app, so this module never touches Qt and stays trivially testable/mockable.

No packaging/semver dependency: release tags here are plain dotted-integer
strings (v0.1.0, 1.2.3), so a hand-rolled tuple compare is all that's needed
and matches this project's minimal-deps style.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .. import __version__
from . import http, paths

REPO = "Arthurman70/prismcut-studio"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
CHECK_INTERVAL_SECS = 24 * 3600
# packaging/installer.iss AppId - stable across releases by design (the
# comment there explains why), so it's safe to hardcode as the fingerprint
# for "is this machine's copy the one that installer manages."
INSTALLER_APP_ID = "BC2968F4-DE79-4830-898D-0813B11BA474"


def _version_tuple(v: str) -> tuple:
    v = v.strip().lstrip("vV")
    out = []
    for part in v.split("."):
        digits = "".join(c for c in part if c.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def is_newer(remote: str, local: Optional[str] = None) -> bool:
    # local resolves __version__ at CALL time, not import time - a plain
    # `local: str = __version__` default would freeze in the value from
    # when this module first loaded, making it unpatchable in tests (and,
    # in principle, stale if __version__ could ever change at runtime).
    if local is None:
        local = __version__
    return _version_tuple(remote) > _version_tuple(local)


@dataclass
class ReleaseInfo:
    tag: str
    name: str
    notes: str
    html_url: str
    asset_url: str = ""     # Windows installer asset, if this platform/release has one
    asset_name: str = ""


def check_latest() -> Optional[ReleaseInfo]:
    """One blocking network call - always run this via jobs.submit(), never
    directly on the GUI thread."""
    data = http.request_json("GET", RELEASES_API, timeout=15)
    tag = str(data.get("tag_name") or "").strip()
    if not tag:
        return None
    asset_url, asset_name = "", ""
    if sys.platform.startswith("win"):
        for a in data.get("assets") or []:
            name = str(a.get("name") or "")
            if "setup" in name.lower() and name.lower().endswith(".exe"):
                asset_url, asset_name = str(a.get("browser_download_url") or ""), name
                break
    return ReleaseInfo(tag=tag, name=str(data.get("name") or tag),
                       notes=str(data.get("body") or ""),
                       html_url=str(data.get("html_url") or RELEASES_PAGE),
                       asset_url=asset_url, asset_name=asset_name)


# ------------------------------------------------------------- install detect

def installed_location() -> Optional[Path]:
    """The Inno Setup install directory for THIS app, but only if the
    currently-running executable actually lives under it - guards against a
    stale registry entry left by a since-removed install, or an unrelated
    match. Returns None for a portable zip or a dev run from source (both
    get the same "notify + link" treatment, no silent self-update)."""
    if not sys.platform.startswith("win"):
        return None
    try:
        import winreg
    except ImportError:  # pragma: no cover - stdlib on Windows only
        return None
    key_path = ("Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\"
               f"{{{INSTALLER_APP_ID}}}_is1")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            install_location, _ = winreg.QueryValueEx(key, "InstallLocation")
    except OSError:
        return None
    loc = Path(str(install_location))
    try:
        Path(sys.executable).resolve().relative_to(loc.resolve())
    except (ValueError, OSError):
        return None
    return loc


# --------------------------------------------------------------- self-update

def download_installer(release: ReleaseInfo,
                       on_chunk: Optional[Callable[[int, int], None]] = None) -> Path:
    if not release.asset_url:
        raise ValueError("This release has no Windows installer asset.")
    dest = paths.cache_dir() / (release.asset_name or f"PrismCut-Setup-{release.tag}.exe")
    return http.download(release.asset_url, dest, on_chunk=on_chunk)


def launch_installer_detached(installer_path: Path) -> None:
    """Launches the downloaded installer and returns immediately WITHOUT
    waiting for it to finish.

    This used to be a blocking subprocess.run() that waited for the
    installer's exit code before relaunching the app itself - which can
    never work: the installer needs to overwrite THIS process's own running
    .exe and DLLs, and Windows holds those files locked for as long as this
    process is alive. Blocking here just means the installer sits there
    failing (or erroring on the locked files) while we wait for an exit
    code that can't arrive until after we've already exited - exactly the
    "files are being used by app currently" failure this fixes.

    The correct order is: launch the installer detached (it survives our
    exit), return control to the caller so it can close this app right
    away, and let packaging/installer.iss's own [Run] postinstall entry
    (CloseApplications/RestartApplications, plus no skipifsilent) handle
    relaunching once install actually completes - this process is gone by
    then and can't do it itself."""
    kwargs = {}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS
    subprocess.Popen([str(installer_path), "/VERYSILENT", "/NORESTART"],
                     close_fds=True, **kwargs)


def perform_self_update(release: ReleaseInfo,
                        on_chunk: Optional[Callable[[int, int], None]] = None) -> Path:
    """Downloads the installer and launches it (detached), then returns -
    meant to run inside a background job's fn(job). The caller MUST close
    this app immediately once this returns without raising (see
    launch_installer_detached's docstring for why: the installer is already
    running but blocked on our own file lock, and can't proceed until we
    exit). Returns the installer path, mainly for tests/logging."""
    installer_path = download_installer(release, on_chunk=on_chunk)
    launch_installer_detached(installer_path)
    return installer_path
