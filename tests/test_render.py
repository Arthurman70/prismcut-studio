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


def test_mov_command_uses_h264_and_faststart(tmp_path):
    p = make_project(tmp_path)
    opts = RenderOptions(fmt="mov", out_path=str(tmp_path / "out.mov"))
    joined = " ".join(build_command(p, opts))
    assert "libx264" in joined
    assert "+faststart" in joined
    assert "out.mov" in joined


def test_video_clip_with_strip_audio_excluded_from_audio_mix(tmp_path):
    """A video clip that's had its audio auto-split onto a companion clip
    (Clip.strip_audio) must not ALSO contribute its own embedded audio to
    the export mix - otherwise the split clip's sound plays twice."""
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"fake-mp4")  # existence is enough for command building
    p = Project()
    item = p.add_media(vid)
    item.has_audio = True
    v1 = p.video_tracks()[-1]
    clip = p.add_clip(item.id, v1.id, 0.0, 4.0)
    opts = RenderOptions(width=1280, height=720, fps=30, fmt="mp4",
                         out_path=str(tmp_path / "out.mp4"))

    normal_cmd = " ".join(build_command(p, opts))
    assert ":a]" in normal_cmd   # the video's own embedded audio is included

    clip.strip_audio = True
    stripped_cmd = " ".join(build_command(p, opts))
    assert ":a]" not in stripped_cmd   # excluded once split onto a companion clip


def test_mov_prores_command_uses_prores_and_pcm_audio_no_faststart(tmp_path):
    p = make_project(tmp_path)
    opts = RenderOptions(fmt="mov-prores", out_path=str(tmp_path / "out.mov"))
    cmd = build_command(p, opts)
    joined = " ".join(cmd)
    assert "prores_ks" in joined
    assert "pcm_s16le" in joined
    assert "+faststart" not in joined   # not meaningful for a mastering/archival ProRes file


def test_mkv_command_uses_h264_no_faststart(tmp_path):
    p = make_project(tmp_path)
    opts = RenderOptions(fmt="mkv", out_path=str(tmp_path / "out.mkv"))
    joined = " ".join(build_command(p, opts))
    assert "libx264" in joined
    assert "+faststart" not in joined   # faststart is an isobmff (mp4/mov) muxer flag, not mkv's


def test_flac_command_is_audio_only_lossless(tmp_path):
    p = make_project(tmp_path)
    opts = RenderOptions(fmt="flac", out_path=str(tmp_path / "out.flac"))
    joined = " ".join(build_command(p, opts))
    assert "-c:a flac" in joined
    assert "overlay" not in joined


def test_m4a_command_is_audio_only_aac(tmp_path):
    p = make_project(tmp_path)
    opts = RenderOptions(fmt="m4a", out_path=str(tmp_path / "out.m4a"))
    joined = " ".join(build_command(p, opts))
    assert "-c:a aac" in joined
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
