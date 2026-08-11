"""Offscreen screenshot generator for docs (CI-safe).

QT_QPA_PLATFORM=offscreen python scripts/screenshots.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from prismcut.ui import theme  # noqa: E402
from prismcut.ui.main_window import MainWindow  # noqa: E402

DEMO = ROOT / "demo_media"
SHOTS = ROOT / "docs" / "screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)


def main() -> None:
    app = QApplication(sys.argv)
    theme.apply_theme(app)
    win = MainWindow()
    win.resize(1680, 980)
    win.show()

    # populate demo project
    files = [DEMO / "sunset_city.jpg", DEMO / "portrait.jpg", DEMO / "ambient_chord.wav"]
    vid = DEMO / "test_clip.mp4"
    if vid.exists():
        files.append(vid)
    win.bin.add_files([str(f) for f in files if f.exists()])

    media = list(win.project.media.values())
    img = next((m for m in media if m.kind == "image"), None)
    aud = next((m for m in media if m.kind == "audio"), None)
    vidm = next((m for m in media if m.kind == "video"), None)
    v1 = win.project.video_tracks()[-1]
    v2 = win.project.video_tracks()[0]
    a1 = win.project.audio_tracks()[0]
    if img:
        win.project.add_clip(img.id, v1.id, 0.0, 6.0)
        c2 = win.project.add_clip(img.id, v2.id, 3.0, 4.0)
        if c2 is not None:
            c2.effects = {"opacity": 60, "scale_pct": 60}
    if vidm:
        win.project.add_clip(vidm.id, v1.id, 6.0)
    if aud:
        c = win.project.add_clip(aud.id, a1.id, 0.0)
        if c is not None:
            c.fade_in = 0.5
            c.fade_out = 1.0
    win.timeline.refresh(True)
    win.timeline.set_playhead(2.0)
    if img:
        win.clip_monitor.show_media(img.path)

    # chat demo transcript (local render only - no API calls)
    win.chat.append_message("user", "Give me a Veo 3.1 prompt for a neon "
                                    "cyberpunk alley fly-through")
    win.chat.append_message("assistant",
                            "**Prompt:** Slow dolly through a rain-slicked cyberpunk "
                            "alley at night, neon signs reflecting in puddles, steam "
                            "rising from vents, cinematic volumetric light, 35mm "
                            "anamorphic, moody teal-magenta grade.\n\nSet duration 8s, "
                            "resolution 1080p, aspect 16:9 in the Generate panel.")
    win.generate.set_prompt("Slow dolly through a rain-slicked cyberpunk alley at "
                            "night, neon signs reflecting in puddles…")

    app.processEvents()
    win.grab().save(str(SHOTS / "edit.png"))

    if img:
        win.photo.open_path(img.path)
    win.tabs.setCurrentWidget(win.photo)
    app.processEvents()
    win.grab().save(str(SHOTS / "photo_studio.png"))

    win.tabs.setCurrentIndex(0)
    win.gen_dock.raise_()
    app.processEvents()
    win.grab().save(str(SHOTS / "ai_panels.png"))

    win.tabs.setCurrentWidget(win.audio)
    if aud:
        clip = next((c for c in win.project.clips.values()
                     if c.media_id == aud.id), None)
        if clip:
            win.audio.show_clip(clip.id)
    app.processEvents()
    win.grab().save(str(SHOTS / "audio_lab.png"))
    print("screenshots ->", SHOTS)


if __name__ == "__main__":
    main()
