from pathlib import Path

from prismcut.core.project import Project
from prismcut.core.render import RenderOptions, build_command


def make_project(tmp_path: Path) -> Project:
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")  # existence is enough for command building
    wav = tmp_path / "b.wav"
    wav.write_bytes(b"RIFF")
    p = Project()
    mi = p.add_media(img)
    ma = p.add_media(wav)
    ma.kind = "audio"
    v1 = p.video_tracks()[-1]
    a1 = p.audio_tracks()[0]
    c1 = p.add_clip(mi.id, v1.id, 0.0, 4.0)
    c1.effects = {"brightness": 20, "blur": 4, "opacity": 80}
    c2 = p.add_clip(ma.id, a1.id, 1.0, 3.0)
    c2.gain_db = -6
    c2.fade_in = 0.5
    return p


def test_mp4_command_shape(tmp_path):
    p = make_project(tmp_path)
    opts = RenderOptions(width=1280, height=720, fps=30, fmt="mp4",
                         out_path=str(tmp_path / "out.mp4"))
    cmd = build_command(p, opts)
    joined = " ".join(cmd)
    assert "-filter_complex" in cmd
    assert "overlay" in joined
    assert "amix" in joined or "apad" in joined
    assert "adelay=1000|1000" in joined
    assert "volume=-6.00dB" in joined
    assert "afade=t=in" in joined
    assert "eq=" in joined and "gblur" in joined and "colorchannelmixer" in joined
    assert cmd[-1].endswith("out.mp4")
    assert "libx264" in joined


def test_audio_only_command(tmp_path):
    p = make_project(tmp_path)
    opts = RenderOptions(fmt="mp3", out_path=str(tmp_path / "out.mp3"))
    cmd = build_command(p, opts)
    joined = " ".join(cmd)
    assert "libmp3lame" in joined
    assert "overlay" not in joined


def test_gif_uses_palette(tmp_path):
    p = make_project(tmp_path)
    opts = RenderOptions(fmt="gif", out_path=str(tmp_path / "out.gif"))
    joined = " ".join(build_command(p, opts))
    assert "palettegen" in joined and "paletteuse" in joined


def test_split_and_resolve(tmp_path):
    p = make_project(tmp_path)
    clip = next(c for c in p.clips.values() if p.track(c.track_id).kind == "video")
    right = p.split_clip(clip.id, 2.0)
    assert right is not None
    assert abs(clip.duration - 2.0) < 1e-6
    assert abs(right.in_point - 2.0) < 1e-6
    found, media, off = p.resolve_at(3.0)
    assert found is right and abs(off - 3.0) < 1e-6
