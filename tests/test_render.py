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


def _arg_after(cmd: list, flag: str, occurrence: int = 0) -> str:
    idxs = [i for i, a in enumerate(cmd) if a == flag]
    return cmd[idxs[occurrence] + 1]


def test_atempo_chain_covers_full_speed_range():
    """atempo only accepts 0.5-2.0 per instance - the Effects panel's Speed
    slider offers 0.25x-4.0x, so anything outside that single-filter range
    must chain two stages whose product recovers the requested speed, each
    stage itself still within atempo's valid range."""
    from prismcut.core.render import _atempo_chain

    assert _atempo_chain(1.5) == ["atempo=1.5000"]
    assert _atempo_chain(2.0) == ["atempo=2.0000"]
    assert _atempo_chain(0.5) == ["atempo=0.5000"]

    for target in (4.0, 3.0, 0.25, 0.3):
        chain = _atempo_chain(target)
        assert len(chain) == 2
        product = 1.0
        for f in chain:
            val = float(f.split("=")[1])
            assert 0.5 <= val <= 2.0
            product *= val
        assert abs(product - target) < 1e-6


def test_source_trim_duration_scales_with_speed():
    """The clean fix for freeze/truncation: how much SOURCE material gets
    read must scale with speed so the retimed OUTPUT exactly fills the
    clip's fixed timeline slot."""
    from prismcut.core.project import Clip
    from prismcut.core.render import _source_trim_duration

    c = Clip(id="x", media_id="m", track_id="t", start=0.0, duration=4.0, effects={"speed": 2.0})
    assert _source_trim_duration(c) == 8.0
    c.effects = {"speed": 0.5}
    assert _source_trim_duration(c) == 2.0
    c.effects = {}
    assert _source_trim_duration(c) == 4.0


def test_speed_effect_scales_source_trim_and_atempo_in_export(tmp_path):
    """Bug fix: the source trim length used to always be c.duration
    regardless of speed, so a 2x clip only read half as much source as it
    needed (freezing on its last frame for the rest of its timeline slot)
    and a 0.5x clip got cut off before its retimed content finished."""
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"fake-mp4")
    p = Project()
    item = p.add_media(vid)
    item.has_audio = True
    v1 = p.video_tracks()[-1]
    clip = p.add_clip(item.id, v1.id, 0.0, 4.0)
    clip.effects = {"speed": 2.0}
    opts = RenderOptions(width=1280, height=720, fps=30, fmt="mp4",
                         out_path=str(tmp_path / "out.mp4"))

    cmd = build_command(p, opts)
    joined = " ".join(cmd)
    # 4.0s clip duration * 2.0x speed = 8.0s of real source must be read -
    # the timeline's own total duration (the trailing -t) stays 4.0s,
    # unaffected by speed.
    assert _arg_after(cmd, "-t", 0) == "8"
    assert _arg_after(cmd, "-t", 1) == "4"
    assert "setpts=PTS/2" in joined
    assert "atempo=2" in joined


def test_track_pan_is_applied_to_audio_filter_chain(tmp_path):
    """Bug fix: Track.pan was stored by the Audio Lab's pan dial but no
    ffmpeg filter ever read it - panning had zero actual effect."""
    wav = tmp_path / "b.wav"
    wav.write_bytes(b"RIFF")
    p = Project()
    ma = p.add_media(wav)
    ma.kind = "audio"
    a1 = p.audio_tracks()[0]
    a1.pan = -0.5
    p.add_clip(ma.id, a1.id, 0.0, 3.0)
    opts = RenderOptions(fmt="wav", out_path=str(tmp_path / "out.wav"))

    joined = " ".join(build_command(p, opts))
    assert "stereotools=balance_in=-0.500" in joined


def test_run_render_merges_stderr_into_stdout_to_avoid_pipe_deadlock(monkeypatch, tmp_path):
    """Bug fix: separate stdout/stderr pipes is the classic subprocess
    deadlock - if ffmpeg fills the stderr OS pipe buffer before finishing,
    it blocks on that write, which stalls the stdout-only readline loop
    too, making the cancel check unreachable. Merging the streams avoids
    it entirely. Mocks subprocess.Popen itself rather than running a real
    ffmpeg, matching how every other render test never actually invokes it."""
    import prismcut.core.render as render_mod
    from prismcut.core.render import run_render

    monkeypatch.setattr(render_mod.media_utils, "have_ffmpeg", lambda: True)

    class FakeStdout:
        def __init__(self, lines):
            self._lines = iter(lines)

        def readline(self):
            return next(self._lines, b"")

    class FakeProc:
        def __init__(self, lines):
            self.stdout = FakeStdout(lines)
            self.returncode = 0

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    captured_kwargs = {}

    def fake_popen(cmd, **kwargs):
        captured_kwargs.update(kwargs)
        return FakeProc([b"out_time_ms=1000000\n", b""])

    monkeypatch.setattr(render_mod.subprocess, "Popen", fake_popen)

    p = make_project(tmp_path)
    opts = RenderOptions(out_path=str(tmp_path / "out.mp4"))
    run_render(p, opts)

    assert captured_kwargs.get("stderr") == render_mod.subprocess.STDOUT


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
