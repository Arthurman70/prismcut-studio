"""Project data model tests - Qt-free, no real ffmpeg/ffprobe required (media
extraction is monkeypatched, same spirit as test_render.py never actually
invoking ffmpeg)."""
from prismcut.core.project import Project


def test_split_video_audio_creates_companion_clip_and_strips_source(monkeypatch, tmp_path):
    from prismcut.core import project as project_mod

    def fake_extract_audio(src, dst_ext=".m4a"):
        out = tmp_path / "extracted.m4a"
        out.write_bytes(b"fake-audio")
        return out

    monkeypatch.setattr(project_mod.media_utils, "extract_audio", fake_extract_audio)
    p = Project()
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"fake-mp4")
    item = p.add_media(vid)
    item.has_audio = True
    v1 = p.video_tracks()[-1]
    clip = p.add_clip(item.id, v1.id, 2.0, 5.0)

    audio_clip = p.split_video_audio(clip)

    assert audio_clip is not None
    assert clip.strip_audio is True
    assert audio_clip.start == 2.0
    assert audio_clip.duration == 5.0
    assert audio_clip.track_id == p.audio_tracks()[0].id
    assert p.media[audio_clip.media_id].kind == "audio"


def test_split_video_audio_is_noop_when_media_has_no_audio(tmp_path):
    p = Project()
    vid = tmp_path / "silent.mp4"
    vid.write_bytes(b"fake-mp4")
    item = p.add_media(vid)
    item.has_audio = False
    v1 = p.video_tracks()[-1]
    clip = p.add_clip(item.id, v1.id, 0.0, 4.0)

    assert p.split_video_audio(clip) is None
    assert clip.strip_audio is False


def test_split_video_audio_returns_none_when_extraction_fails(monkeypatch, tmp_path):
    from prismcut.core import project as project_mod
    monkeypatch.setattr(project_mod.media_utils, "extract_audio", lambda *a, **k: None)
    p = Project()
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"fake-mp4")
    item = p.add_media(vid)
    item.has_audio = True
    v1 = p.video_tracks()[-1]
    clip = p.add_clip(item.id, v1.id, 0.0, 4.0)

    assert p.split_video_audio(clip) is None
    # Left untouched - still plays its own embedded audio, same as before
    # this feature existed, rather than ending up silent with nothing to
    # replace it.
    assert clip.strip_audio is False


def test_split_video_audio_is_noop_without_an_audio_track(monkeypatch, tmp_path):
    from prismcut.core import project as project_mod
    monkeypatch.setattr(project_mod.media_utils, "extract_audio",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not run")))
    p = Project()
    p.tracks = [t for t in p.tracks if t.kind != "audio"]
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"fake-mp4")
    item = p.add_media(vid)
    item.has_audio = True
    v1 = p.video_tracks()[-1]
    clip = p.add_clip(item.id, v1.id, 0.0, 4.0)

    assert p.split_video_audio(clip) is None
    assert clip.strip_audio is False


def test_resolve_audio_at_finds_clip_that_resolve_at_ignores(tmp_path):
    p = Project()
    aud = tmp_path / "narration.wav"
    aud.write_bytes(b"RIFF")
    item = p.add_media(aud)
    a1 = p.audio_tracks()[0]
    clip = p.add_clip(item.id, a1.id, 1.0, 3.0)

    # resolve_at() only ever looks at video tracks - confirms the gap
    # resolve_audio_at() exists to close.
    _vclip, vitem, _off = p.resolve_at(2.0)
    assert vitem is None

    aclip, aitem, offset = p.resolve_audio_at(2.0)
    assert aclip is clip
    assert aitem is item
    assert offset == 1.0


def test_resolve_audio_at_skips_muted_clips_and_tracks(tmp_path):
    p = Project()
    aud = tmp_path / "narration.wav"
    aud.write_bytes(b"RIFF")
    item = p.add_media(aud)
    a1 = p.audio_tracks()[0]
    clip = p.add_clip(item.id, a1.id, 0.0, 3.0)
    clip.muted = True

    assert p.resolve_audio_at(1.0) == (None, None, 0.0)

    clip.muted = False
    a1.mute = True
    assert p.resolve_audio_at(1.0) == (None, None, 0.0)


def test_add_marker_keeps_markers_sorted_by_time():
    p = Project()
    p.add_marker(5.0, "third")
    p.add_marker(1.0, "first")
    p.add_marker(3.0, "second")

    assert [m.label for m in p.markers] == ["first", "second", "third"]
    assert p.dirty is True


def test_add_marker_clamps_negative_time_to_zero():
    p = Project()
    m = p.add_marker(-2.0)

    assert m.time == 0.0


def test_remove_marker():
    p = Project()
    m = p.add_marker(2.0, "cut here")

    p.remove_marker(m.id)

    assert p.markers == []


def test_remove_marker_missing_id_is_a_noop():
    p = Project()
    p.add_marker(2.0)

    p.remove_marker("does-not-exist")

    assert len(p.markers) == 1


def test_marker_near_finds_closest_within_tolerance():
    p = Project()
    far = p.add_marker(1.0)
    near = p.add_marker(9.9)
    p.add_marker(20.0)

    found = p.marker_near(10.0, tolerance=0.4)

    assert found is near
    assert found is not far


def test_marker_near_returns_none_outside_tolerance():
    p = Project()
    p.add_marker(1.0)

    assert p.marker_near(10.0, tolerance=0.4) is None


def test_project_save_load_round_trips_markers(tmp_path):
    p = Project()
    p.add_marker(2.5, "intro ends", color="#42a5f5")
    path = tmp_path / "proj.pcut"

    p.save(path)
    loaded = Project.load(path)

    assert len(loaded.markers) == 1
    assert loaded.markers[0].time == 2.5
    assert loaded.markers[0].label == "intro ends"
    assert loaded.markers[0].color == "#42a5f5"
