"""SRT caption/subtitle helpers - Qt-free, no ffmpeg/network involved.
Burn-in (rendering captions into the video) is out of scope for this v1;
this only covers producing/reading a sidecar .srt file, which is what most
platforms (YouTube, editors, players) accept directly."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Segment:
    start: float   # seconds
    end: float     # seconds
    text: str


def _ts(seconds: float) -> str:
    """seconds -> SRT timestamp "HH:MM:SS,mmm"."""
    seconds = max(0.0, seconds)
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _parse_ts(text: str) -> float:
    h, m, rest = text.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def format_srt(segments: list[Segment]) -> str:
    blocks = []
    for i, seg in enumerate(segments, 1):
        text = seg.text.strip()
        if not text:
            continue
        blocks.append(f"{i}\n{_ts(seg.start)} --> {_ts(seg.end)}\n{text}\n")
    return "\n".join(blocks)


def write_srt(segments: list[Segment], path) -> None:
    Path(path).write_text(format_srt(segments), encoding="utf-8")


_TIME_LINE = re.compile(r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})")


def read_srt(path) -> list[Segment]:
    """Parses an .srt file back into Segments - tolerant of blank-line
    variations between blocks, since not every writer follows the spec's
    exact blank-line convention."""
    raw = Path(path).read_text(encoding="utf-8-sig")
    segments: list[Segment] = []
    block: list[str] = []

    def flush(block: list[str]):
        for i, line in enumerate(block):
            m = _TIME_LINE.search(line)
            if m:
                start, end = _parse_ts(m.group(1)), _parse_ts(m.group(2))
                text = "\n".join(block[i + 1:]).strip()
                if text:
                    segments.append(Segment(start, end, text))
                return

    for line in raw.splitlines():
        if line.strip() == "":
            if block:
                flush(block)
                block = []
        else:
            block.append(line)
    if block:
        flush(block)
    return segments
