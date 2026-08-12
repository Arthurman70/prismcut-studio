from prismcut.core import media as media_utils


def test_is_accepted_by_kind():
    assert media_utils.is_accepted("photo.png", "image")
    assert not media_utils.is_accepted("photo.png", "audio")
    assert media_utils.is_accepted("clip.mp4", "video", "audio")
    assert not media_utils.is_accepted("notes.txt", "image", "video", "audio")


def test_is_accepted_no_kinds_means_anything():
    assert media_utils.is_accepted("whatever.xyz")


def test_is_accepted_case_insensitive():
    assert media_utils.is_accepted("PHOTO.PNG", "image")


def test_accepted_exts_union():
    exts = media_utils.accepted_exts("image", "audio")
    assert ".png" in exts and ".mp3" in exts and ".mp4" not in exts


def test_filters_cover_kind_of_extensions():
    # every extension kind_of() recognizes for a kind must appear in that
    # kind's filter string, so dialogs/drops/kind_of can never drift apart
    for ext in media_utils.IMAGE_EXT:
        assert f"*{ext}" in media_utils.IMAGE_FILTER
    for ext in media_utils.VIDEO_EXT:
        assert f"*{ext}" in media_utils.VIDEO_FILTER
    for ext in media_utils.AUDIO_EXT:
        assert f"*{ext}" in media_utils.AUDIO_FILTER


def test_media_filter_includes_all_kinds():
    assert "*.png" in media_utils.MEDIA_FILTER
    assert "*.mp4" in media_utils.MEDIA_FILTER
    assert "*.mp3" in media_utils.MEDIA_FILTER
    assert "All files (*)" in media_utils.MEDIA_FILTER
