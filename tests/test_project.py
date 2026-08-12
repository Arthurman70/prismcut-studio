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


def _image_item(p: Project, tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    return p.add_media(img)


def test_gap_at_returns_none_inside_a_clip(tmp_path):
    p = Project()
    item = _image_item(p, tmp_path)
    v1 = p.video_tracks()[-1]
    p.add_clip(item.id, v1.id, 0.0, 3.0)

    assert p.gap_at(v1.id, 1.5) is None


def test_gap_at_finds_gap_before_first_clip(tmp_path):
    p = Project()
    item = _image_item(p, tmp_path)
    v1 = p.video_tracks()[-1]
    clip = p.add_clip(item.id, v1.id, 2.0, 3.0)

    found = p.gap_at(v1.id, 1.0)

    assert found is not None
    gap, affected = found
    assert gap == 2.0
    assert affected == [clip]


def test_gap_at_finds_gap_between_clips(tmp_path):
    p = Project()
    item = _image_item(p, tmp_path)
    v1 = p.video_tracks()[-1]
    p.add_clip(item.id, v1.id, 0.0, 3.0)
    b = p.add_clip(item.id, v1.id, 5.0, 2.0)

    found = p.gap_at(v1.id, 4.0)

    assert found is not None
    gap, affected = found
    assert gap == 2.0
    assert affected == [b]


def test_gap_at_returns_none_for_already_adjacent_clips(tmp_path):
    p = Project()
    item = _image_item(p, tmp_path)
    v1 = p.video_tracks()[-1]
    p.add_clip(item.id, v1.id, 0.0, 3.0)
    p.add_clip(item.id, v1.id, 3.0, 2.0)

    assert p.gap_at(v1.id, 3.0) is None


def test_gap_at_returns_none_past_the_last_clip(tmp_path):
    p = Project()
    item = _image_item(p, tmp_path)
    v1 = p.video_tracks()[-1]
    p.add_clip(item.id, v1.id, 0.0, 3.0)

    assert p.gap_at(v1.id, 10.0) is None


def test_close_gap_after_ripples_only_the_affected_track(tmp_path):
    p = Project()
    item = _image_item(p, tmp_path)
    v1 = p.video_tracks()[-1]
    a1 = p.audio_tracks()[0]
    a = p.add_clip(item.id, v1.id, 0.0, 3.0)
    b = p.add_clip(item.id, v1.id, 5.0, 2.0)
    other_track_clip = p.add_clip(item.id, a1.id, 5.0, 2.0)

    changed = p.close_gap_after(v1.id, 4.0)

    assert changed is True
    assert a.start == 0.0                    # before the gap - unaffected
    assert b.start == 3.0                    # rippled left by the 2.0s gap
    assert other_track_clip.start == 5.0     # different track - untouched


def test_close_gap_after_returns_false_when_no_gap(tmp_path):
    p = Project()
    item = _image_item(p, tmp_path)
    v1 = p.video_tracks()[-1]
    clip = p.add_clip(item.id, v1.id, 0.0, 3.0)

    assert p.close_gap_after(v1.id, 1.0) is False
    assert clip.start == 0.0


def test_add_title_creates_synthetic_media_item():
    p = Project()
    item = p.add_title("Chapter One", font_size=48, color="#e8a33d",
                        position="bottom", bold=False, shadow=False, duration=3.0)

    assert item.kind == "title"
    assert item.id in p.media
    assert item.label == "Chapter One"
    assert item.duration == 3.0
    assert item.width == p.width and item.height == p.height
    assert item.has_audio is False
    assert item.group == "titles"
    assert item.meta == {
        "title_text": "Chapter One", "font_size": 48, "color": "#e8a33d",
        "position": "bottom", "bold": False, "shadow": False,
    }
    assert p.dirty is True


def test_add_title_defaults_and_label_fallback():
    p = Project()
    item = p.add_title("   ")   # blank/whitespace-only text

    assert item.label == "Title"
    assert item.meta["font_size"] == 64
    assert item.meta["color"] == "#ffffff"
    assert item.meta["position"] == "center"
    assert item.meta["bold"] is True
    assert item.meta["shadow"] is True
    assert item.duration == 5.0


def test_add_title_label_is_truncated_and_synthetic_path_is_unique():
    p = Project()
    long_text = "x" * 100
    a = p.add_title(long_text)
    b = p.add_title(long_text)

    assert len(a.label) <= 40
    assert a.path != b.path   # each title needs a distinct synthetic path


def test_add_title_round_trips_through_save_load(tmp_path):
    p = Project()
    p.add_title("Intro", color="#42a5f5", position="top")
    path = tmp_path / "proj.pcut"

    p.save(path)
    loaded = Project.load(path)

    items = list(loaded.media.values())
    assert len(items) == 1
    assert items[0].kind == "title"
    assert items[0].meta["title_text"] == "Intro"
    assert items[0].meta["color"] == "#42a5f5"


def test_add_title_clip_gets_default_duration_from_the_media_item(tmp_path):
    p = Project()
    item = p.add_title("Card", duration=7.0)
    v1 = p.video_tracks()[-1]

    clip = p.add_clip(item.id, v1.id, 0.0)   # no explicit duration

    assert clip.duration == 7.0
