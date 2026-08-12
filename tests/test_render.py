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


def test_no_markers_leaves_command_unaffected(tmp_path):
    """The overwhelmingly common case (no markers used) must produce a
    byte-identical command to before chapters existed - no stray input, no
    filesystem write, no -map_chapters."""
    p = make_project(tmp_path)
    opts = RenderOptions(fmt="mp4", out_path=str(tmp_path / "out.mp4"))
    cmd = build_command(p, opts)
    assert "-map_chapters" not in cmd
    assert "ffmetadata" not in " ".join(cmd)


def test_markers_produce_chapters_ffmetadata_input(monkeypatch, tmp_path):
    from prismcut.core import render as render_mod
    monkeypatch.setattr(render_mod.paths, "cache_dir", lambda: tmp_path)
    p = make_project(tmp_path)
    p.add_marker(0.0, "Intro")
    p.add_marker(2.0, "Main")
    opts = RenderOptions(fmt="mp4", out_path=str(tmp_path / "out.mp4"))

    cmd = build_command(p, opts)

    assert "-map_chapters" in cmd
    assert "ffmetadata" in cmd
    chapters_path = Path(_arg_after(cmd, "-i", occurrence=cmd.count("-i") - 1))
    text = chapters_path.read_text(encoding="utf-8")
    assert text.startswith(";FFMETADATA1")
    assert "title=Intro" in text
    assert "title=Main" in text
    assert "START=0" in text
    assert "START=2000" in text


def test_marker_at_or_past_project_end_is_skipped(monkeypatch, tmp_path):
    from prismcut.core import render as render_mod
    monkeypatch.setattr(render_mod.paths, "cache_dir", lambda: tmp_path)
    p = make_project(tmp_path)
    p.add_marker(999.0, "past the end")
    opts = RenderOptions(fmt="mp4", out_path=str(tmp_path / "out.mp4"))

    cmd = build_command(p, opts)

    assert "-map_chapters" not in cmd


def test_chapters_skipped_for_webm_and_audio_formats(monkeypatch, tmp_path):
    from prismcut.core import render as render_mod
    monkeypatch.setattr(render_mod.paths, "cache_dir", lambda: tmp_path)
    p = make_project(tmp_path)
    p.add_marker(0.0, "Intro")

    for fmt, out in (("webm", "out.webm"), ("mp3", "out.mp3")):
        opts = RenderOptions(fmt=fmt, out_path=str(tmp_path / out))
        cmd = build_command(p, opts)
        assert "-map_chapters" not in cmd


def test_unlabeled_marker_gets_a_default_chapter_title(monkeypatch, tmp_path):
    from prismcut.core import render as render_mod
    monkeypatch.setattr(render_mod.paths, "cache_dir", lambda: tmp_path)
    p = make_project(tmp_path)
    p.add_marker(0.0)
    opts = RenderOptions(fmt="mp4", out_path=str(tmp_path / "out.mp4"))

    cmd = build_command(p, opts)

    chapters_path = Path(_arg_after(cmd, "-i", occurrence=cmd.count("-i") - 1))
    assert "title=Chapter 1" in chapters_path.read_text(encoding="utf-8")


def test_ffmpeg_color_converts_hex_to_0x_syntax():
    from prismcut.core.render import _ffmpeg_color

    assert _ffmpeg_color("#e8a33d") == "0xe8a33d"
    assert _ffmpeg_color("") == "0xffffff"   # falsy input falls back to white


def test_drawtext_escape_handles_colons_quotes_backslashes_and_newlines():
    from prismcut.core.render import _drawtext_escape

    assert _drawtext_escape("12:30") == "12\\:30"
    assert _drawtext_escape("it's") == "it\\'s"
    assert _drawtext_escape("a\\b") == "a\\\\b"
    assert _drawtext_escape("line one\nline two") == "line one line two"


def test_title_clip_uses_lavfi_color_source_not_a_file(tmp_path):
    p = Project()
    item = p.add_title("Hello", duration=4.0)
    v1 = p.video_tracks()[-1]
    p.add_clip(item.id, v1.id, 0.0, 4.0)
    opts = RenderOptions(width=1280, height=720, fps=30, fmt="mp4",
                         out_path=str(tmp_path / "out.mp4"))

    cmd = build_command(p, opts)

    assert "-f" in cmd
    lavfi_idx = cmd.index("lavfi")
    assert cmd[lavfi_idx - 1] == "-f"
    color_arg = _arg_after(cmd, "-i", occurrence=0)
    assert color_arg == "color=c=black@0.0:s=1280x720:r=30"
    joined = " ".join(cmd)
    assert "drawtext=" in joined
    assert "text='Hello'" in joined
    assert "fontsize=64" in joined
    assert "fontcolor=0xffffff" in joined
    assert "format=yuva420p" in joined


def test_title_clip_bold_and_position_selection(tmp_path):
    p = Project()
    item = p.add_title("Lower Third", font_size=32, color="#42a5f5",
                       position="bottom", bold=False, shadow=False, duration=2.0)
    v1 = p.video_tracks()[-1]
    p.add_clip(item.id, v1.id, 0.0, 2.0)
    opts = RenderOptions(fmt="mp4", out_path=str(tmp_path / "out.mp4"))

    joined = " ".join(build_command(p, opts))

    assert "arial.ttf" in joined and "arialbd.ttf" not in joined
    assert "fontcolor=0x42a5f5" in joined
    assert "y=h-text_h-h*0.08" in joined
    assert "shadowcolor" not in joined   # shadow disabled


def test_title_clip_shadow_and_bold_defaults(tmp_path):
    p = Project()
    item = p.add_title("Card")   # bold=True, shadow=True by default
    v1 = p.video_tracks()[-1]
    p.add_clip(item.id, v1.id, 0.0, 5.0)
    opts = RenderOptions(fmt="mp4", out_path=str(tmp_path / "out.mp4"))

    joined = " ".join(build_command(p, opts))

    assert "arialbd.ttf" in joined
    assert "shadowcolor=black@0.7" in joined


def test_title_clip_text_is_escaped_in_the_filtergraph(tmp_path):
    p = Project()
    item = p.add_title("It's 12:30")
    v1 = p.video_tracks()[-1]
    p.add_clip(item.id, v1.id, 0.0, 5.0)
    opts = RenderOptions(fmt="mp4", out_path=str(tmp_path / "out.mp4"))

    joined = " ".join(build_command(p, opts))

    assert "text='It\\'s 12\\:30'" in joined


def test_title_clip_has_no_embedded_audio_mixed_in(tmp_path):
    p = Project()
    item = p.add_title("Silent")
    v1 = p.video_tracks()[-1]
    p.add_clip(item.id, v1.id, 0.0, 5.0)
    opts = RenderOptions(fmt="mp4", out_path=str(tmp_path / "out.mp4"))

    cmd = build_command(p, opts)

    assert "amix" not in " ".join(cmd)
    assert "[aout]" not in cmd


def _video_item(p: Project, tmp_path, name: str, has_audio: bool):
    vid = tmp_path / name
    vid.write_bytes(b"fake-mp4")
    item = p.add_media(vid)
    item.kind = "video"
    item.has_audio = has_audio
    return item


def test_transition_out_produces_xfade_between_adjacent_clips(tmp_path):
    p = Project()
    img_a = tmp_path / "a.png"
    img_a.write_bytes(b"\x89PNG")
    img_b = tmp_path / "b.png"
    img_b.write_bytes(b"\x89PNG")
    ia, ib = p.add_media(img_a), p.add_media(img_b)
    v1 = p.video_tracks()[-1]
    a = p.add_clip(ia.id, v1.id, 0.0, 4.0)
    b = p.add_clip(ib.id, v1.id, 4.0, 3.0)   # exactly adjacent
    a.transition_out = 1.0
    opts = RenderOptions(fmt="mp4", out_path=str(tmp_path / "out.mp4"))

    cmd = build_command(p, opts)
    joined = " ".join(cmd)

    assert "-i" in cmd and cmd.count("-i") == 2   # both clips still become real inputs
    assert "xfade=transition=fade:duration=1:offset=3" in joined
    # exactly one overlay stage for the pair, not two - [c.start, b.end)
    assert "enable='between(t,0,7)'" in joined


def test_transition_out_falls_back_to_two_clips_when_not_adjacent(tmp_path):
    p = Project()
    img_a = tmp_path / "a.png"
    img_a.write_bytes(b"\x89PNG")
    img_b = tmp_path / "b.png"
    img_b.write_bytes(b"\x89PNG")
    ia, ib = p.add_media(img_a), p.add_media(img_b)
    v1 = p.video_tracks()[-1]
    a = p.add_clip(ia.id, v1.id, 0.0, 4.0)
    p.add_clip(ib.id, v1.id, 5.0, 3.0)   # 1s gap - not adjacent
    a.transition_out = 1.0
    opts = RenderOptions(fmt="mp4", out_path=str(tmp_path / "out.mp4"))

    joined = " ".join(build_command(p, opts))

    assert "xfade" not in joined
    assert joined.count("overlay=") == 2


def test_transition_out_ignored_without_a_next_clip(tmp_path):
    p = Project()
    img_a = tmp_path / "a.png"
    img_a.write_bytes(b"\x89PNG")
    ia = p.add_media(img_a)
    v1 = p.video_tracks()[-1]
    a = p.add_clip(ia.id, v1.id, 0.0, 4.0)
    a.transition_out = 1.0   # nothing after it on this track

    opts = RenderOptions(fmt="mp4", out_path=str(tmp_path / "out.mp4"))
    cmd = build_command(p, opts)   # must not raise

    assert "xfade" not in " ".join(cmd)


def test_transition_out_clamped_to_the_shorter_clips_duration(tmp_path):
    p = Project()
    img_a = tmp_path / "a.png"
    img_a.write_bytes(b"\x89PNG")
    img_b = tmp_path / "b.png"
    img_b.write_bytes(b"\x89PNG")
    ia, ib = p.add_media(img_a), p.add_media(img_b)
    v1 = p.video_tracks()[-1]
    a = p.add_clip(ia.id, v1.id, 0.0, 4.0)
    p.add_clip(ib.id, v1.id, 4.0, 1.5)   # shorter than the requested overlap
    a.transition_out = 10.0   # way more than either clip's duration

    joined = " ".join(build_command(p, RenderOptions(fmt="mp4", out_path=str(tmp_path / "out.mp4"))))

    assert "xfade=transition=fade:duration=1.5:offset=2.5" in joined


def test_transition_out_skipped_when_next_clip_is_muted(tmp_path):
    p = Project()
    img_a = tmp_path / "a.png"
    img_a.write_bytes(b"\x89PNG")
    img_b = tmp_path / "b.png"
    img_b.write_bytes(b"\x89PNG")
    ia, ib = p.add_media(img_a), p.add_media(img_b)
    v1 = p.video_tracks()[-1]
    a = p.add_clip(ia.id, v1.id, 0.0, 4.0)
    b = p.add_clip(ib.id, v1.id, 4.0, 3.0)
    a.transition_out = 1.0
    b.muted = True

    joined = " ".join(build_command(p, RenderOptions(fmt="mp4", out_path=str(tmp_path / "out.mp4"))))

    assert "xfade" not in joined


def test_transition_crossfades_audio_when_both_clips_have_it(tmp_path):
    p = Project()
    va = _video_item(p, tmp_path, "a.mp4", has_audio=True)
    vb = _video_item(p, tmp_path, "b.mp4", has_audio=True)
    v1 = p.video_tracks()[-1]
    a = p.add_clip(va.id, v1.id, 0.0, 4.0)
    p.add_clip(vb.id, v1.id, 4.0, 3.0)
    a.transition_out = 1.0

    joined = " ".join(build_command(p, RenderOptions(fmt="mp4", out_path=str(tmp_path / "out.mp4"))))

    assert "acrossfade=d=1" in joined
    assert "amix" not in joined   # only one combined audio stream - nothing else to mix with


def test_transition_audio_falls_back_to_single_clips_when_only_one_has_audio(tmp_path):
    p = Project()
    va = _video_item(p, tmp_path, "a.mp4", has_audio=True)
    vb = _video_item(p, tmp_path, "b.mp4", has_audio=False)
    v1 = p.video_tracks()[-1]
    a = p.add_clip(va.id, v1.id, 0.0, 4.0)
    p.add_clip(vb.id, v1.id, 4.0, 3.0)
    a.transition_out = 1.0

    joined = " ".join(build_command(p, RenderOptions(fmt="mp4", out_path=str(tmp_path / "out.mp4"))))

    assert "acrossfade" not in joined
    assert "adelay=0|0" in joined   # clip a's own audio, positioned at its own start (0)


def test_transition_works_between_a_title_and_an_image(tmp_path):
    p = Project()
    img_b = tmp_path / "b.png"
    img_b.write_bytes(b"\x89PNG")
    title = p.add_title("Intro", duration=4.0)
    ib = p.add_media(img_b)
    v1 = p.video_tracks()[-1]
    a = p.add_clip(title.id, v1.id, 0.0, 4.0)
    p.add_clip(ib.id, v1.id, 4.0, 3.0)
    a.transition_out = 1.0

    cmd = build_command(p, RenderOptions(fmt="mp4", out_path=str(tmp_path / "out.mp4")))
    joined = " ".join(cmd)

    assert "drawtext=" in joined   # title branch still ran for the first half of the pair
    assert "xfade=transition=fade:duration=1:offset=3" in joined


def test_chroma_key_off_by_default(tmp_path):
    p = make_project(tmp_path)
    joined = " ".join(build_command(p, RenderOptions(fmt="mp4", out_path=str(tmp_path / "out.mp4"))))

    assert "chromakey" not in joined
    assert "despill" not in joined


def test_chroma_key_adds_chromakey_and_despill_with_defaults(tmp_path):
    p = make_project(tmp_path)
    clip = next(c for c in p.clips.values() if p.track(c.track_id).kind == "video")
    clip.effects["chroma_key"] = True

    joined = " ".join(build_command(p, RenderOptions(fmt="mp4", out_path=str(tmp_path / "out.mp4"))))

    assert "chromakey=color=0x00ff00:similarity=0.200:blend=0.100" in joined
    assert "despill=type=green" in joined


def test_chroma_key_custom_color_and_tolerance(tmp_path):
    p = make_project(tmp_path)
    clip = next(c for c in p.clips.values() if p.track(c.track_id).kind == "video")
    clip.effects.update(chroma_key=True, chroma_key_color="#0000ff",
                        chroma_key_similarity=40, chroma_key_blend=25)

    joined = " ".join(build_command(p, RenderOptions(fmt="mp4", out_path=str(tmp_path / "out.mp4"))))

    assert "chromakey=color=0x0000ff:similarity=0.400:blend=0.250" in joined
    assert "despill=type=blue" in joined


def test_chroma_key_similarity_and_blend_are_clamped(tmp_path):
    p = make_project(tmp_path)
    clip = next(c for c in p.clips.values() if p.track(c.track_id).kind == "video")
    clip.effects.update(chroma_key=True, chroma_key_similarity=500, chroma_key_blend=-30)

    joined = " ".join(build_command(p, RenderOptions(fmt="mp4", out_path=str(tmp_path / "out.mp4"))))

    assert "similarity=1.000" in joined
    assert "blend=0.000" in joined


def test_lut_off_by_default(tmp_path):
    p = make_project(tmp_path)
    joined = " ".join(build_command(p, RenderOptions(fmt="mp4", out_path=str(tmp_path / "out.mp4"))))

    assert "lut3d" not in joined


def test_lut_path_adds_lut3d_filter(tmp_path):
    from prismcut.core.render import _escape_filter_path

    p = make_project(tmp_path)
    clip = next(c for c in p.clips.values() if p.track(c.track_id).kind == "video")
    lut_path = str(tmp_path / "look.cube")
    clip.effects["lut_path"] = lut_path

    joined = " ".join(build_command(p, RenderOptions(fmt="mp4", out_path=str(tmp_path / "out.mp4"))))

    assert f"lut3d=file='{_escape_filter_path(lut_path)}'" in joined


def test_lut_path_windows_drive_colon_is_escaped(tmp_path):
    p = make_project(tmp_path)
    clip = next(c for c in p.clips.values() if p.track(c.track_id).kind == "video")
    clip.effects["lut_path"] = r"C:\LUTs\Teal and Orange.cube"

    joined = " ".join(build_command(p, RenderOptions(fmt="mp4", out_path=str(tmp_path / "out.mp4"))))

    assert "lut3d=file='C\\:/LUTs/Teal and Orange.cube'" in joined


def test_nvenc_off_by_default_uses_libx264(tmp_path):
    # Precise -c:v-value checks rather than a bare substring search: the
    # ffmpeg command embeds full input file paths, and pytest's tmp_path
    # is derived from this test's own name (which contains "nvenc"),
    # so a loose "nvenc" not in joined check would false-positive on the
    # file paths themselves.
    p = make_project(tmp_path)
    cmd = build_command(p, RenderOptions(fmt="mp4", out_path=str(tmp_path / "out.mp4")))

    assert _arg_after(cmd, "-c:v") == "libx264"


def test_nvenc_on_swaps_libx264_for_h264_nvenc(tmp_path):
    p = make_project(tmp_path)
    opts = RenderOptions(fmt="mp4", use_nvenc=True, crf=22, out_path=str(tmp_path / "out.mp4"))

    cmd = build_command(p, opts)

    assert _arg_after(cmd, "-c:v") == "h264_nvenc"
    assert _arg_after(cmd, "-cq") == "22"
    assert "-crf" not in cmd


def test_nvenc_on_swaps_libx265_for_hevc_nvenc(tmp_path):
    p = make_project(tmp_path)
    opts = RenderOptions(fmt="mp4-hevc", use_nvenc=True, crf=20, out_path=str(tmp_path / "out.mp4"))

    cmd = build_command(p, opts)

    assert _arg_after(cmd, "-c:v") == "hevc_nvenc"
    assert _arg_after(cmd, "-cq") == "24"   # crf + 4, same offset the software path uses


def test_nvenc_does_not_apply_to_prores(tmp_path):
    p = make_project(tmp_path)
    opts = RenderOptions(fmt="mov-prores", use_nvenc=True, out_path=str(tmp_path / "out.mov"))

    cmd = build_command(p, opts)

    assert _arg_after(cmd, "-c:v") == "prores_ks"
    assert "-cq" not in cmd


def _fake_popen_factory(lines, returncode):
    class FakeStdout:
        def __init__(self, ls):
            self._lines = iter(ls)

        def readline(self):
            return next(self._lines, b"")

    class FakeProc:
        def __init__(self, ls):
            self.stdout = FakeStdout(ls)
            self.returncode = returncode

        def poll(self):
            return returncode

        def wait(self, timeout=None):
            return returncode

        def kill(self):
            pass

    return lambda cmd, **kwargs: FakeProc(lines)


def test_run_render_nvenc_failure_gives_a_clear_message(monkeypatch, tmp_path):
    import prismcut.core.render as render_mod
    from prismcut.core.render import run_render

    monkeypatch.setattr(render_mod.media_utils, "have_ffmpeg", lambda: True)
    monkeypatch.setattr(render_mod.subprocess, "Popen", _fake_popen_factory(
        [b"[h264_nvenc @ 0x0] Cannot load nvcuda.dll\n", b""], returncode=1))

    p = make_project(tmp_path)
    opts = RenderOptions(use_nvenc=True, out_path=str(tmp_path / "out.mp4"))

    try:
        run_render(p, opts)
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "Hardware encoding (NVENC) failed" in str(e)
        assert "your GPU or driver may not support it" in str(e)


def test_run_render_non_nvenc_failure_shows_raw_ffmpeg_output(monkeypatch, tmp_path):
    import prismcut.core.render as render_mod
    from prismcut.core.render import run_render

    monkeypatch.setattr(render_mod.media_utils, "have_ffmpeg", lambda: True)
    monkeypatch.setattr(render_mod.subprocess, "Popen", _fake_popen_factory(
        [b"Unknown encoder 'bogus'\n", b""], returncode=1))

    p = make_project(tmp_path)
    opts = RenderOptions(use_nvenc=False, out_path=str(tmp_path / "out.mp4"))

    try:
        run_render(p, opts)
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "Hardware encoding (NVENC) failed" not in str(e)
        assert "ffmpeg failed" in str(e)
        assert "Unknown encoder" in str(e)
