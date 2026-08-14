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
    # A user-created Bin's id, or "" for the default kind-based grouping
    # (see GROUPS in project_bin.py) - sorting into a bin doesn't change
    # `group`/`kind`, it's an independent, optional organizational layer
    # the bin panel checks first and falls back from.
    bin_id: str = ""
    label_color: str = ""           # "" = none, else a hex color from a fixed palette
    # Whether `path` currently points at a file that exists - a live,
    # recomputed-on-load status flag, not something meaningful to persist
    # (see Project.check_offline()); round-trips through save/load
    # harmlessly since load() immediately overwrites it with a fresh check
    # regardless of whatever value was on disk.
    offline: bool = False
    # A lower-res transcode for smoother preview scrubbing of a heavy video
    # source (see core.media.generate_proxy()) - "" until generated.
    # Preview-only: export always renders from `path`, never this.
    proxy_path: str = ""


@dataclass
class Bin:
    id: str
    name: str


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
class Marker:
    id: str
    time: float
    label: str = ""
    color: str = "#e8a33d"


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
    # Set on a video clip whose audio was auto-split onto a companion clip
    # (see Project.split_video_audio) - the source file still has its own
    # embedded audio, but it's excluded from playback/export so the split
    # audio clip is the only thing that plays it, avoiding doubled sound.
    # Distinct from `muted`, which hides the whole clip (picture and all).
    strip_audio: bool = False
    # Cross-dissolve into the NEXT clip on this track, in seconds (0 = no
    # transition). Only meaningful (and only ever applied at render time)
    # when a clip with start == self.end actually exists right after this
    # one - a dangling value left over from a since-moved/deleted neighbor
    # is simply ignored rather than erroring, same "ignore if now
    # meaningless" spirit as strip_audio on a clip whose audio track was
    # since deleted.
    transition_out: float = 0.0
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
        self.markers: list[Marker] = []
        self.bins: list[Bin] = []
        self.dirty = False

    # -------------------------------------------------------------------- bins
    def add_bin(self, name: str) -> Bin:
        b = Bin(id=_uid(), name=name.strip() or "New Bin")
        self.bins.append(b)
        self.dirty = True
        return b

    def rename_bin(self, bin_id: str, name: str) -> None:
        b = next((x for x in self.bins if x.id == bin_id), None)
        if b:
            b.name = name.strip() or b.name
            self.dirty = True

    def remove_bin(self, bin_id: str) -> None:
        """Deletes the bin itself; media that was filed under it falls back
        to the default kind-based grouping rather than vanishing or being
        deleted - a bin is just an organizational label, not a container
        with ownership semantics."""
        self.bins = [b for b in self.bins if b.id != bin_id]
        for m in self.media.values():
            if m.bin_id == bin_id:
                m.bin_id = ""
        self.dirty = True

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

    def add_title(self, text: str, *, font_size: int = 64, color: str = "#ffffff",
                  position: str = "center", bold: bool = True, shadow: bool = True,
                  duration: float = 5.0) -> MediaItem:
        """Synthetic media item for a text/title clip - unlike add_media(),
        it has no real backing file: render.py generates the frame on the
        fly (an ffmpeg lavfi color source + drawtext, see
        _title_drawtext()), and the Project/Clip Monitor draw a QPainter
        preview instead of decoding a file (see MonitorBase.show_title()).
        Style lives in meta rather than new MediaItem fields, the same way
        generated media already carries provider/prompt info there."""
        item = MediaItem(
            id=_uid(), path=f"title-{_uid()}", kind="title", label=text.strip()[:40] or "Title",
            duration=max(0.2, duration), width=self.width, height=self.height, has_audio=False,
            group="titles", meta={
                "title_text": text, "font_size": font_size, "color": color,
                "position": position, "bold": bold, "shadow": shadow,
            },
        )
        self.media[item.id] = item
        self.dirty = True
        return item

    def remove_media(self, media_id: str) -> None:
        self.media.pop(media_id, None)
        for cid in [c.id for c in self.clips.values() if c.media_id == media_id]:
            self.clips.pop(cid, None)
        self.dirty = True

    def check_offline(self) -> int:
        """Refreshes MediaItem.offline for every item against the real
        filesystem - called once at the end of load() and safe to call
        again any time (e.g. after the user fixes things outside the app).
        Titles have no real backing file (see add_title()) so they're
        never considered offline. Returns how many are currently missing."""
        count = 0
        for item in self.media.values():
            item.offline = item.kind != "title" and not Path(item.path).exists()
            if item.offline:
                count += 1
        return count

    def find_relink_matches(self, folder) -> dict[str, Path]:
        """For every currently-offline item, the first file found anywhere
        under `folder` (recursively) whose filename matches - a pure
        finder, not a mutator, so the UI layer can turn each match into
        its own undoable relink rather than this silently changing state
        out from under the undo stack."""
        folder = Path(folder)
        offline_items = [m for m in self.media.values() if m.offline]
        if not offline_items or not folder.is_dir():
            return {}
        index: dict[str, Path] = {}
        for f in folder.rglob("*"):
            if f.is_file() and f.name not in index:
                index[f.name] = f
        return {m.id: index[Path(m.path).name] for m in offline_items if Path(m.path).name in index}

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
            resolved = media_utils.resolved_duration(item.path, item.duration)
            if resolved is not None:
                item.duration = resolved   # self-heals the MediaItem, not just this one clip
            duration = resolved if resolved is not None else 5.0
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

    def next_adjacent_clip(self, clip_id: str) -> Optional[Clip]:
        """The clip on the same track whose start exactly meets this one's
        end (no gap), or None - the only configuration a transition_out
        cross-dissolve can actually apply to."""
        c = self.clips.get(clip_id)
        if not c:
            return None
        for other in self.clips_on(c.track_id):
            if other.id != c.id and abs(other.start - c.end) < 0.01:
                return other
        return None

    def duration(self) -> float:
        return max((c.end for c in self.clips.values()), default=0.0)

    def gap_at(self, track_id: str, time: float) -> Optional[tuple[float, list[Clip]]]:
        """(gap_width, clips_to_ripple) if `time` falls inside a real,
        closeable gap on `track_id` - the empty space before the first
        clip, or between two non-adjacent clips - else None (`time` is
        inside a clip, the clips on either side are already adjacent, or
        `time` is past the last clip, where there's nothing after it to
        pull left). Single-track only, by design - not a cross-track
        ripple-trim."""
        clips = self.clips_on(track_id)
        prev_end = 0.0
        for i, c in enumerate(clips):
            if prev_end <= time <= c.start and c.start > prev_end:
                return c.start - prev_end, clips[i:]
            if c.start <= time < c.end:
                return None
            prev_end = max(prev_end, c.end)
        return None

    def close_gap_after(self, track_id: str, time: float) -> bool:
        """Ripples every clip on `track_id` at or after the gap containing
        `time` left by that gap's width, closing it. Returns False (no-op)
        if `time` isn't actually inside a closeable gap - see gap_at()."""
        found = self.gap_at(track_id, time)
        if not found:
            return False
        gap, affected = found
        for c in affected:
            c.start = max(0.0, c.start - gap)
        self.dirty = True
        return True

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

    def resolve_audio_at(self, t: float):
        """Topmost audible audio-track clip covering time t -> (clip, media,
        source_offset) - the audio-track equivalent of resolve_at(), used by
        preview to mix in sound resolve_at() can't see because it only ever
        looks at video tracks (a video's auto-split companion clip,
        freestanding background music, narration, ...)."""
        for track in self.audio_tracks():
            if track.mute:
                continue
            for c in self.clips_on(track.id):
                if not c.muted and c.start <= t < c.end:
                    m = self.media.get(c.media_id)
                    if m:
                        return c, m, c.in_point + (t - c.start)
        return None, None, 0.0

    def split_video_audio(self, clip: "Clip") -> Optional["Clip"]:
        """Extracts a video clip's embedded audio onto a new companion Clip
        on an audio track (same start/duration/in_point), and marks the
        video clip strip_audio so its own embedded track no longer also
        plays - each can then be trimmed, muted, or deleted independently.
        Best-effort: returns None (video clip left untouched, still plays
        its own embedded audio as before) if there's no audio track to
        receive the split or ffmpeg extraction fails - never raises, since
        this runs inline in the normal "add this clip to the timeline"
        path and a missing ffmpeg shouldn't block that."""
        item = self.media.get(clip.media_id)
        if not item or item.kind != "video" or not item.has_audio:
            return None
        audio_tracks = self.audio_tracks()
        if not audio_tracks:
            return None
        audio_path = media_utils.extract_audio(item.path)
        if not audio_path:
            return None
        audio_item = self.add_media(audio_path, group="audio",
                                    meta={"source": "split_from_video", "video_media_id": item.id})
        audio_clip = self.add_clip(audio_item.id, audio_tracks[0].id, clip.start, clip.duration,
                                   label=f"{clip.label or item.label} (audio)")
        if not audio_clip:
            return None
        audio_clip.in_point = clip.in_point
        clip.strip_audio = True
        return audio_clip

    # ---------------------------------------------------------------- markers
    def add_marker(self, time: float, label: str = "", color: str = "#e8a33d") -> Marker:
        m = Marker(id=_uid(), time=max(0.0, time), label=label, color=color)
        self.markers.append(m)
        self.markers.sort(key=lambda mk: mk.time)
        self.dirty = True
        return m

    def remove_marker(self, marker_id: str) -> None:
        self.markers = [m for m in self.markers if m.id != marker_id]
        self.dirty = True

    def marker_near(self, time: float, tolerance: float = 0.4) -> Optional[Marker]:
        """Closest marker within `tolerance` seconds of `time`, or None -
        shared by snap-to-marker and ruler right-click/click hit-testing."""
        candidates = [m for m in self.markers if abs(m.time - time) <= tolerance]
        return min(candidates, key=lambda m: abs(m.time - time)) if candidates else None

    # ------------------------------------------------------------------ io
    def to_dict(self) -> dict:
        return {
            "app": "prismcut", "version": 1, "name": self.name,
            "width": self.width, "height": self.height, "fps": self.fps,
            "media": [asdict(m) for m in self.media.values()],
            "tracks": [asdict(t) for t in self.tracks],
            "clips": [asdict(c) for c in self.clips.values()],
            "markers": [asdict(mk) for mk in self.markers],
            "bins": [asdict(b) for b in self.bins],
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
        proj.markers = [Marker(**mk) for mk in data.get("markers", [])]
        proj.bins = [Bin(**b) for b in data.get("bins", [])]
        proj.check_offline()
        proj.dirty = False
        return proj
