"""core/media.py's generate_proxy() - mocks ffmpeg_path()/subprocess.run
rather than invoking real ffmpeg, same spirit as every other test file
in this suite never hitting a real provider or real ffmpeg."""
from pathlib import Path

from prismcut.core import media as media_mod


def test_generate_proxy_returns_none_without_ffmpeg(monkeypatch, tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"fake-mp4")
    monkeypatch.setattr(media_mod, "ffmpeg_path", lambda: None)

    assert media_mod.generate_proxy(src) is None


def test_generate_proxy_returns_none_when_source_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(media_mod, "ffmpeg_path", lambda: "ffmpeg")

    assert media_mod.generate_proxy(tmp_path / "does-not-exist.mp4") is None


def test_generate_proxy_reuses_an_existing_cached_output(monkeypatch, tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"fake-mp4")
    monkeypatch.setattr(media_mod, "ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(media_mod.paths, "cache_dir", lambda: tmp_path)

    expected = tmp_path / f"clip_proxy_{abs(hash((str(src), src.stat().st_mtime)))}.mp4"
    expected.write_bytes(b"already-here")

    called = []
    monkeypatch.setattr(media_mod.subprocess, "run", lambda *a, **k: called.append(1))

    result = media_mod.generate_proxy(src)

    assert result == expected
    assert called == []   # cache hit - ffmpeg never invoked


def test_generate_proxy_builds_a_scale_filter_and_returns_the_output_path(monkeypatch, tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"fake-mp4")
    monkeypatch.setattr(media_mod, "ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(media_mod.paths, "cache_dir", lambda: tmp_path)

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        # generate_proxy checks out.exists() afterward - simulate ffmpeg
        # having produced the file rather than actually running it.
        out = Path(cmd[-1])
        out.write_bytes(b"fake-proxy-mp4")

    monkeypatch.setattr(media_mod.subprocess, "run", fake_run)

    result = media_mod.generate_proxy(src, height=360)

    assert result is not None
    assert result.exists()
    cmd = captured["cmd"]
    assert "-vf" in cmd
    assert cmd[cmd.index("-vf") + 1] == "scale=-2:360"
    assert str(src) in cmd


def test_generate_proxy_returns_none_when_ffmpeg_fails_to_produce_output(monkeypatch, tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"fake-mp4")
    monkeypatch.setattr(media_mod, "ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(media_mod.paths, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(media_mod.subprocess, "run", lambda *a, **k: None)   # no output file created

    assert media_mod.generate_proxy(src) is None


# ----------------------------------------------------------- resolved_duration

def test_resolved_duration_short_circuits_on_an_already_positive_current(monkeypatch):
    calls = []
    monkeypatch.setattr(media_mod, "probe", lambda path: calls.append(path) or {"duration": 99.0})

    assert media_mod.resolved_duration("clip.mp3", 5.0) == 5.0
    assert calls == []   # never re-probes when current is already real


def test_resolved_duration_reprobes_and_returns_the_fresh_value(monkeypatch):
    monkeypatch.setattr(media_mod, "probe", lambda path: {"duration": 7.5})

    assert media_mod.resolved_duration("clip.mp3", 0.0) == 7.5


def test_resolved_duration_returns_none_when_the_reprobe_also_comes_back_empty(monkeypatch):
    monkeypatch.setattr(media_mod, "probe", lambda path: {"duration": 0.0})

    assert media_mod.resolved_duration("clip.mp3", 0.0) is None
