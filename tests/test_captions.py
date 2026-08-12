"""core/captions.py - Qt-free, no network or ffmpeg involved."""
from prismcut.core.captions import Segment, format_srt, read_srt, write_srt


def test_format_srt_produces_standard_blocks():
    segments = [
        Segment(0.0, 1.5, "Hello there"),
        Segment(1.5, 63.25, "General Kenobi"),
    ]

    text = format_srt(segments)

    assert text.startswith("1\n00:00:00,000 --> 00:00:01,500\nHello there\n")
    assert "2\n00:00:01,500 --> 00:01:03,250\nGeneral Kenobi\n" in text


def test_format_srt_skips_blank_segments():
    segments = [Segment(0.0, 1.0, "  "), Segment(1.0, 2.0, "Real line")]

    text = format_srt(segments)

    assert "Real line" in text
    assert text.count("-->") == 1


def test_format_srt_clamps_negative_start():
    segments = [Segment(-5.0, 1.0, "Clamped")]

    text = format_srt(segments)

    assert "00:00:00,000 --> 00:00:01,000" in text


def test_write_srt_writes_utf8_file(tmp_path):
    path = tmp_path / "out.srt"
    write_srt([Segment(0.0, 2.0, "Café")], path)

    assert path.read_text(encoding="utf-8") == format_srt([Segment(0.0, 2.0, "Café")])


def test_read_srt_round_trips_write_srt(tmp_path):
    segments = [
        Segment(0.0, 1.5, "Hello there"),
        Segment(1.5, 63.25, "General Kenobi"),
        Segment(63.25, 65.0, "You are a bold one"),
    ]
    path = tmp_path / "out.srt"
    write_srt(segments, path)

    parsed = read_srt(path)

    assert len(parsed) == 3
    for original, back in zip(segments, parsed):
        assert abs(original.start - back.start) < 1e-3
        assert abs(original.end - back.end) < 1e-3
        assert original.text == back.text


def test_read_srt_handles_multiline_captions(tmp_path):
    path = tmp_path / "multi.srt"
    path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nLine one\nLine two\n\n"
        "2\n00:00:02,000 --> 00:00:04,000\nSecond caption\n",
        encoding="utf-8",
    )

    segments = read_srt(path)

    assert len(segments) == 2
    assert segments[0].text == "Line one\nLine two"
    assert segments[1].text == "Second caption"


def test_read_srt_empty_file_returns_no_segments(tmp_path):
    path = tmp_path / "empty.srt"
    path.write_text("", encoding="utf-8")

    assert read_srt(path) == []
