"""Project data model: media bin, tracks, clips. Qt-free and JSON-serializable."""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from . import media as media_utils

PROJECT_EXT = ".pcut"


def _uid() -> str:
    return uuid.uuid4().hex[:10]


@dataclass
class MediaItem:
    id: str
    path: str
    kind: str                       # image | video | audio
    label: str = ""
    duration: float = 0.0
    width: int = 0
    height: int = 0
    has_audio: bool = False
    group: str = "import"           # import | generated | audio
    meta: dict = field(default_factory=dict)   # provider/model/prompt for generated media


@dataclass
class Track:
    id: str
    kind: str                       # video | audio
    name: str
    mute: bool = False
    solo: bool = False
    locked: bool = False
    gain_db: float = 0.0
    pan: float = 0.0                # -1..1 (applied at export)


@dataclass
class Clip:
    id: str
    media_id: str
    track_id: str
    start: float                    # timeline position (s)
    duration: float
    in_point: float = 0.0           # source offset (s)
    label: str = ""
    gain_db: float = 0.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    muted: bool = False
    effects: dict = field(default_factory=dict)
    # effects keys: scale_pct, pos_x, pos_y, rotate_deg, opacity, brightness,
    #               contrast, saturation, blur, speed

    @property
    def end(self) -> float:
        return self.start + self.duration


class Project:
    def __init__(self):
        self.path: Optional[Path] = None
        self.name = "Untitled"
        self.width, self.height, self.fps = 1920, 1080, 30
        self.media: dict[str, MediaItem] = {}
        self.tracks: list[Track] = [
            Track(_uid(), "video", "V2"),
            Track(_uid(), "video", "V1"),
            Track(_uid(), "audio", "A1"),
            Track(_uid(), "audio", "A2"),
        ]
        self.clips: dict[str, Clip] = {}
        self.dirty = False

    # ------------------------------------------------------------------ media
    def add_media(self, path, group: str = "import", meta: Optional[dict] = None) -> MediaItem:
        p = Path(path)
        for item in self.media.values():
            if item.path == str(p) and group == item.group:
                return item
        kind = media_utils.kind_of(p)
        info = media_utils.probe(p)
        item = MediaItem(
            id=_uid(), path=str(p), kind=kind, label=p.name,
            duration=info["duration"], width=info["width"], height=info["height"],
            has_audio=info["has_audio"], group=group, meta=meta or {},
        )
        self.media[item.id] = item
        self.dirty = True
        return item

    def remove_media(self, media_id: str) -> None:
        self.media.pop(media_id, None)
        for cid in [c.id for c in self.clips.values() if c.media_id == media_id]:
            self.clips.pop(cid, None)
        self.dirty = True

    # ------------------------------------------------------------------ tracks
    def video_tracks(self) -> list[Track]:
        return [t for t in self.tracks if t.kind == "video"]

    def audio_tracks(self) -> list[Track]:
        return [t for t in self.tracks if t.kind == "audio"]

    def add_track(self, kind: str) -> Track:
        n = len([t for t in self.tracks if t.kind == kind]) + 1
        name = ("V" if kind == "video" else "A") + str(n)
        t = Track(_uid(), kind, name)
        if kind == "video":
            self.tracks.insert(0, t)
        else:
            self.tracks.append(t)
        self.dirty = True
        return t

    def track(self, track_id: str) -> Optional[Track]:
        return next((t for t in self.tracks if t.id == track_id), None)

    # ------------------------------------------------------------------ clips
    def add_clip(self, media_id: str, track_id: str, start: float,
                 duration: Optional[float] = None, label: str = "") -> Optional[Clip]:
        item = self.media.get(media_id)
        track = self.track(track_id)
        if not item or not track:
            return None
        if duration is None:
            duration = item.duration if item.duration > 0 else 5.0
        clip = Clip(id=_uid(), media_id=media_id, track_id=track_id,
                    start=max(0.0, start), duration=max(0.2, duration), label=label or item.label)
        self.clips[clip.id] = clip
        self.dirty = True
        return clip

    def remove_clip(self, clip_id: str) -> None:
        self.clips.pop(clip_id, None)
        self.dirty = True

    def split_clip(self, clip_id: str, t: float) -> Optional[Clip]:
        c = self.clips.get(clip_id)
        if not c or not (c.start + 0.05 < t < c.end - 0.05):
            return None
        right = Clip(id=_uid(), media_id=c.media_id, track_id=c.track_id,
                     start=t, duration=c.end - t, in_point=c.in_point + (t - c.start),
                     label=c.label, gain_db=c.gain_db, fade_out=c.fade_out,
                     effects=dict(c.effects))
        c.duration = t - c.start
        c.fade_out = 0.0
        self.clips[right.id] = right
        self.dirty = True
        return right

    def clips_on(self, track_id: str) -> list[Clip]:
        return sorted((c for c in self.clips.values() if c.track_id == track_id),
                      key=lambda c: c.start)

    def duration(self) -> float:
        return max((c.end for c in self.clips.values()), default=0.0)

    def resolve_at(self, t: float):
        """Topmost visible video clip covering time t -> (clip, media, source_offset)."""
        for track in self.video_tracks():          # first video track = topmost
            if track.mute:
                continue
            for c in self.clips_on(track.id):
                if not c.muted and c.start <= t < c.end:
                    m = self.media.get(c.media_id)
                    if m:
                        return c, m, c.in_point + (t - c.start)
        return None, None, 0.0

    # ------------------------------------------------------------------ io
    def to_dict(self) -> dict:
        return {
            "app": "prismcut", "version": 1, "name": self.name,
            "width": self.width, "height": self.height, "fps": self.fps,
            "media": [asdict(m) for m in self.media.values()],
            "tracks": [asdict(t) for t in self.tracks],
            "clips": [asdict(c) for c in self.clips.values()],
        }

    def save(self, path=None) -> Path:
        if path:
            self.path = Path(path)
        if not self.path:
            raise ValueError("No project path set")
        self.path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        self.dirty = False
        return self.path

    @classmethod
    def load(cls, path) -> "Project":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        proj = cls()
        proj.path = Path(path)
        proj.name = data.get("name", "Untitled")
        proj.width = data.get("width", 1920)
        proj.height = data.get("height", 1080)
        proj.fps = data.get("fps", 30)
        proj.media = {m["id"]: MediaItem(**m) for m in data.get("media", [])}
        proj.tracks = [Track(**t) for t in data.get("tracks", [])] or proj.tracks
        proj.clips = {c["id"]: Clip(**c) for c in data.get("clips", [])}
        proj.dirty = False
        return proj
