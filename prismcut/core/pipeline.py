"""Movie pipeline data model: describe-a-movie -> scripted, storyboarded,
voiced, generated scenes. Qt-free and JSON-serializable, same style as
core/project.py. Scene.*.active.media_id are soft references into
Project.media (and clip_ids into Project.clips) - deleting the underlying
media via normal bin operations orphans the reference the same way
MediaItem.meta already tolerates being untyped; callers must .get()
defensively.

Stored separately from Project (see paths.pipelines_dir()), not embedded in
the .pcut file - keeps the project schema/tests untouched and keeps
per-take regen-history bloat out of the file that matters for opening/
exporting a project.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from . import paths

# status values, in order: draft -> scenes_ready -> images_running ->
# images_ready -> video_running -> done
STATUSES = ("draft", "scenes_ready", "images_running", "images_ready",
           "video_running", "done")


def _uid() -> str:
    return uuid.uuid4().hex[:10]


@dataclass
class StageAsset:
    media_id: str = ""                 # -> Project.media[id]; "" until generated
    prompt: str = ""
    source: str = "generated"          # "generated" | "user" (draftboard/manual override)
    provider: str = ""
    model: str = ""
    params: dict = field(default_factory=dict)
    created: float = 0.0


@dataclass
class SceneHistory:
    """Append-only regen log - generalizes Photo Studio's proven
    versions/vindex pattern (photo_studio.py) to any pipeline stage."""
    entries: list = field(default_factory=list)   # list[StageAsset]
    current: int = -1

    @property
    def active(self) -> Optional[StageAsset]:
        return self.entries[self.current] if 0 <= self.current < len(self.entries) else None

    def push(self, asset: StageAsset) -> None:
        self.entries = self.entries[: self.current + 1]   # drop redo-forward history, like _push_version
        self.entries.append(asset)
        self.current = len(self.entries) - 1

    @classmethod
    def from_dict(cls, d: dict) -> "SceneHistory":
        return cls(entries=[StageAsset(**e) for e in d.get("entries", [])],
                   current=d.get("current", -1))


@dataclass
class Scene:
    id: str
    index: int
    script: str = ""                   # on-screen beat description
    narration: str = ""                # dialogue/voiceover source text (feeds TTS)
    image: SceneHistory = field(default_factory=SceneHistory)
    audio: SceneHistory = field(default_factory=SceneHistory)
    video: SceneHistory = field(default_factory=SceneHistory)
    lipsync: SceneHistory = field(default_factory=SceneHistory)
    use_lipsync: bool = False
    clip_ids: dict = field(default_factory=dict)   # {"video": clip_id, "audio": clip_id}
    # Per-scene overrides layered on top of the pipeline's image/video model
    # default_params() at generation time (e.g. a shorter duration or lower
    # resolution for one specific scene) - empty means "use the model's
    # defaults, same as every other scene."
    image_params: dict = field(default_factory=dict)
    video_params: dict = field(default_factory=dict)
    # Set by the orchestrator when this scene's most recent image/video/audio
    # generation attempt failed (API error, moderation rejection, etc.) -
    # cleared on the next successful attempt for that stage. "" means no
    # error is currently outstanding, regardless of how many succeeded before.
    last_error: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Scene":
        d = dict(d)
        for stage in ("image", "audio", "video", "lipsync"):
            d[stage] = SceneHistory.from_dict(d.get(stage) or {})
        return cls(**d)


@dataclass
class MoviePipeline:
    id: str = field(default_factory=_uid)
    name: str = "Untitled movie"
    created: float = field(default_factory=time.time)
    brief: str = ""
    script_model: str = ""             # registry keys, "provider::id"
    image_model: str = ""
    audio_model: str = ""
    video_model: str = ""
    lipsync_model: str = ""            # "" = no separate lip-sync pass
    scenes: list = field(default_factory=list)     # list[Scene]
    video_track_id: str = ""
    audio_track_id: str = ""
    status: str = "draft"

    def scene(self, scene_id: str) -> Optional[Scene]:
        return next((s for s in self.scenes if s.id == scene_id), None)

    # ---------------------------------------------------------------- io
    def to_dict(self) -> dict:
        d = asdict(self)
        d["app"] = "prismcut-pipeline"
        d["version"] = 1
        return d

    def save(self, path: Optional[Path] = None) -> Path:
        p = Path(path) if path else paths.pipelines_dir() / f"{self.id}.json"
        p.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path) -> "MoviePipeline":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        data.pop("app", None)
        data.pop("version", None)
        data["scenes"] = [Scene.from_dict(s) for s in data.get("scenes", [])]
        return cls(**data)

    @staticmethod
    def list_saved() -> list[Path]:
        return sorted(paths.pipelines_dir().glob("*.json"), key=lambda p: p.stat().st_mtime,
                     reverse=True)


def new_scene(index: int) -> Scene:
    return Scene(id=_uid(), index=index)
