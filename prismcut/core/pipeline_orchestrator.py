"""Deterministic movie-pipeline orchestrator: script -> per-scene image ->
audio -> interim timeline insert -> video (+ optional lip-sync) -> final
swap. The AI is called as a writing service (script breakdown, implicitly
per-scene prompts via Scene.script) - this module decides what happens next
in a fixed order, never an autonomous loop.

Stage gating (user's decision, not re-litigated): script + audio run
ungated (audio starts automatically the moment narration text exists, right
after the script stage - it's cheap relative to image/video). Image
generation and video/lip-sync generation each need an explicit confirm from
the caller (the UI's two stage-action buttons) before this module's
run_image_batch()/run_video_batch() are even called - this module doesn't
own that confirmation itself, the same way GeneratePanel doesn't gate its
own jobs.submit() calls either.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal

from dataclasses import dataclass

from ..providers.base import ChatMessage
from . import http
from .pipeline import MoviePipeline, Scene, StageAsset, new_scene


@dataclass
class VideoPlan:
    video_model: object            # ModelSpec
    lipsync_model: object = None   # ModelSpec | None
    native_audio: bool = False     # True once some video model both generates AND hard-syncs natively


def resolve_video_plan(registry, video_model_key: str, lipsync_model_key: str = "") -> VideoPlan:
    """Decides how a scene's video gets its lip-sync, driven entirely by
    registry capability flags - never a hardcoded model-id check, so a
    future model that natively supports 'lip_sync' lights up the
    native_audio branch automatically the day it's added to models.json,
    with zero orchestrator changes."""
    video_model = registry.by_key(video_model_key)
    if not video_model:
        raise ValueError(f"Unknown video model {video_model_key!r}")
    if lipsync_model_key:
        lipsync_model = registry.by_key(lipsync_model_key)
        if not lipsync_model:
            raise ValueError(f"Unknown lip-sync model {lipsync_model_key!r}")
        return VideoPlan(video_model, lipsync_model, native_audio=False)
    if "lip_sync" in video_model.caps:
        return VideoPlan(video_model, None, native_audio=True)
    return VideoPlan(video_model, None, native_audio=False)


class _BatchTracker:
    """Fan-in for 'N jobs queued, do X once every one of them has finished
    (success or fail)' - used for both the image batch and the video/
    lip-sync batch."""

    def __init__(self, total: int, on_all_done: Callable[[], None]):
        self.remaining = total
        self.on_all_done = on_all_done
        self.failures: list[str] = []

    def one_done(self, ok: bool, msg: str = "") -> None:
        self.remaining -= 1
        if not ok:
            self.failures.append(msg)
        if self.remaining <= 0:
            self.on_all_done()


class PipelineRun(QObject):
    logMessage = Signal(str)
    statusChanged = Signal(str)
    sceneChanged = Signal(str)   # scene_id - emitted whenever a scene's stage assets change

    def __init__(self, main_window, pipeline: MoviePipeline):
        super().__init__(main_window)
        self.win = main_window
        self.pipeline = pipeline
        self._images_done = False
        self._audio_done = False

    # ---------------------------------------------------------------- helpers
    def _set_status(self, status: str) -> None:
        self.pipeline.status = status
        self.pipeline.save()
        self.statusChanged.emit(status)

    def _adapter_for(self, model_key: str):
        model = self.win.registry.by_key(model_key)
        if not model:
            raise ValueError(f"Unknown model {model_key!r}")
        return model, self.win.get_adapter(model.provider)

    def _ensure_tracks(self) -> None:
        if not self.pipeline.video_track_id or not self.win.project.track(self.pipeline.video_track_id):
            self.pipeline.video_track_id = self.win.add_track("video").id
        if not self.pipeline.audio_track_id or not self.win.project.track(self.pipeline.audio_track_id):
            self.pipeline.audio_track_id = self.win.add_track("audio").id

    # ---------------------------------------------------------- stage 1: script
    def generate_breakdown(self, on_done: Optional[Callable] = None,
                           on_fail: Optional[Callable] = None) -> None:
        model, adapter = self._adapter_for(self.pipeline.script_model)
        brief = self.pipeline.brief
        sys_prompt = (
            "You break a movie/show brief into a numbered list of scenes for an AI "
            "generation pipeline. Output STRICT JSON ONLY: a list of objects, each with "
            '"script" (a 1-2 sentence on-screen visual/action description, written as an '
            'image/video generation prompt) and "narration" (the exact dialogue or '
            "voiceover line spoken during that scene, or an empty string for a silent, "
            "visual-only scene). No commentary, no markdown code fences, JSON only.")

        def work(job):
            job.progress(-1, "Writing scene breakdown")
            return adapter.chat(model.id, [ChatMessage("user", brief)], system=sys_prompt,
                                temperature=0.8)

        def done(result):
            scenes = _parse_breakdown(str(result))
            if not scenes:
                self.logMessage.emit("The AI's scene breakdown couldn't be parsed - try again "
                                     "or edit the brief to be more specific.")
                if on_fail:
                    on_fail("Could not parse scene breakdown.")
                return
            self.pipeline.scenes = scenes
            self._set_status("scenes_ready")
            self.logMessage.emit(f"Scripted {len(scenes)} scene(s).")
            if on_done:
                on_done(scenes)
            self.run_audio_batch()   # ungated - starts automatically once narration text exists

        def fail(msg):
            self.logMessage.emit(f"Script breakdown failed: {msg}")
            if on_fail:
                on_fail(msg)

        self.win.jobs.submit(f"Movie: script breakdown for “{self.pipeline.name}”",
                             work, kind="chat", on_done=done, on_fail=fail)

    # ------------------------------------------------------- stage 3: images (gate 1)
    def run_image_batch(self, on_all_done: Optional[Callable] = None) -> None:
        model, adapter = self._adapter_for(self.pipeline.image_model)
        targets = [s for s in self.pipeline.scenes if s.image.active is None]
        if not targets:
            self._images_done = True
            self._maybe_insert_all_interim()
            if on_all_done:
                on_all_done()
            return
        self._set_status("images_running")
        tracker = _BatchTracker(len(targets), lambda: self._images_batch_done(on_all_done))
        for scene in targets:
            self._generate_scene_image(scene, model, adapter, tracker)

    def regenerate_scene_image(self, scene_id: str, prompt_override: str = "") -> None:
        """Single-scene regenerate, bypassing the batch/gate - used by the
        per-row Regenerate button, not the bulk action."""
        scene = self.pipeline.scene(scene_id)
        if not scene:
            return
        model, adapter = self._adapter_for(self.pipeline.image_model)
        tracker = _BatchTracker(1, lambda: None)
        self._generate_scene_image(scene, model, adapter, tracker, prompt_override)

    def _generate_scene_image(self, scene: Scene, model, adapter, tracker: _BatchTracker,
                              prompt_override: str = "") -> None:
        prompt = prompt_override or scene.script or self.pipeline.brief

        def work(job):
            job.progress(-1, f"Scene {scene.index + 1}: image")
            return adapter.generate_image(model.id, prompt, model.default_params())

        def done(result):
            outs = result or []
            if outs:
                item = self.win.bin.add_generated(str(outs[0]), {
                    "provider": model.provider, "model": model.id, "prompt": prompt, "mode": "image",
                    "source": "pipeline", "pipeline_id": self.pipeline.id, "scene_id": scene.id})
                scene.image.push(StageAsset(media_id=item.id, prompt=prompt, provider=model.provider,
                                            model=model.id, source="generated", created=time.time()))
                self.sceneChanged.emit(scene.id)
                self.pipeline.save()
            tracker.one_done(bool(outs), "" if outs else "No image returned.")

        def fail(msg):
            self.logMessage.emit(f"Scene {scene.index + 1} image failed: {msg}")
            tracker.one_done(False, msg)

        self.win.jobs.submit(f"Movie scene {scene.index + 1}: image", work, kind="image",
                             on_done=done, on_fail=fail)

    def set_scene_image_override(self, scene_id: str, path: str) -> None:
        """Draftboard drop: register a user-supplied image as this scene's
        active image asset - same StageAsset shape a generated image would
        produce (source='user' instead of 'generated'), so run_image_batch's
        `s.image.active is None` target filter already skips it for free."""
        scene = self.pipeline.scene(scene_id)
        if not scene:
            return
        item = self.win.bin.add_generated(path, {
            "mode": "image", "source": "pipeline", "pipeline_id": self.pipeline.id,
            "scene_id": scene.id})
        scene.image.push(StageAsset(media_id=item.id, source="user", created=time.time()))
        self.sceneChanged.emit(scene.id)
        self.pipeline.save()
        self._maybe_insert_all_interim()

    def _images_batch_done(self, on_all_done: Optional[Callable]) -> None:
        self._images_done = True
        if self.pipeline.status == "images_running":
            self._set_status("images_ready")
        self.logMessage.emit("All scene images ready.")
        self._maybe_insert_all_interim()
        if on_all_done:
            on_all_done()

    # --------------------------------------------------- stage 4: audio (ungated)
    def run_audio_batch(self, on_all_done: Optional[Callable] = None) -> None:
        model, adapter = self._adapter_for(self.pipeline.audio_model)
        targets = [s for s in self.pipeline.scenes if s.narration.strip() and s.audio.active is None]
        if not targets:
            self._audio_done = True
            self._maybe_insert_all_interim()
            if on_all_done:
                on_all_done()
            return
        tracker = _BatchTracker(len(targets), lambda: self._audio_batch_done(on_all_done))
        for scene in targets:
            self._generate_scene_audio(scene, model, adapter, tracker)

    def _generate_scene_audio(self, scene: Scene, model, adapter, tracker: _BatchTracker) -> None:
        text = scene.narration

        def work(job):
            job.progress(-1, f"Scene {scene.index + 1}: audio")
            return adapter.tts(model.id, text, "", model.default_params())

        def done(result):
            item = self.win.bin.add_generated(str(result), {
                "provider": model.provider, "model": model.id, "prompt": text, "mode": "tts",
                "source": "pipeline", "pipeline_id": self.pipeline.id, "scene_id": scene.id})
            scene.audio.push(StageAsset(media_id=item.id, prompt=text, provider=model.provider,
                                        model=model.id, source="generated", created=time.time()))
            self.sceneChanged.emit(scene.id)
            self.pipeline.save()
            tracker.one_done(True)

        def fail(msg):
            self.logMessage.emit(f"Scene {scene.index + 1} audio failed: {msg}")
            tracker.one_done(False, msg)

        self.win.jobs.submit(f"Movie scene {scene.index + 1}: audio", work, kind="tts",
                             on_done=done, on_fail=fail)

    def _audio_batch_done(self, on_all_done: Optional[Callable]) -> None:
        self._audio_done = True
        self.logMessage.emit("All scene audio ready.")
        self._maybe_insert_all_interim()
        if on_all_done:
            on_all_done()

    # ---------------------------------------------------- interim timeline insert
    def _maybe_insert_all_interim(self) -> None:
        """Waits for BOTH the image and audio batches to finish (whichever
        finishes second triggers this) rather than inserting per-scene as
        each stage completes out of order - jobs finish in whatever order
        the providers respond, but scenes need sequential placement, so
        waiting for both batches means every scene's real audio duration is
        known before any clip position is computed."""
        if not (self._images_done and self._audio_done):
            return
        self._ensure_tracks()
        cursor = 0.0
        self.win.undo_stack.beginMacro(f"Movie “{self.pipeline.name}”: lay out scenes")
        for scene in sorted(self.pipeline.scenes, key=lambda s: s.index):
            img = scene.image.active
            aud = scene.audio.active
            img_item = self.win.project.media.get(img.media_id) if img else None
            aud_item = self.win.project.media.get(aud.media_id) if aud else None
            duration = aud_item.duration if aud_item and aud_item.duration else 3.0
            if img_item and "image" not in scene.clip_ids and "video" not in scene.clip_ids:
                clip = self.win.timeline.add_media_at_playhead(
                    img_item.id, self.pipeline.video_track_id, cursor, duration)
                if clip:
                    scene.clip_ids["image"] = clip.id
            if aud_item and "audio" not in scene.clip_ids:
                clip = self.win.timeline.add_media_at_playhead(
                    aud_item.id, self.pipeline.audio_track_id, cursor, duration)
                if clip:
                    scene.clip_ids["audio"] = clip.id
            cursor += duration
        self.win.undo_stack.endMacro()
        self.pipeline.save()
        self.logMessage.emit("Scenes laid out on the timeline.")

    # ---------------------------------------------- stage 6: video + lip-sync (gate 2)
    def run_video_batch(self, on_all_done: Optional[Callable] = None) -> None:
        plan = resolve_video_plan(self.win.registry, self.pipeline.video_model,
                                  self.pipeline.lipsync_model)
        video_adapter = self.win.get_adapter(plan.video_model.provider)
        targets = [s for s in self.pipeline.scenes if s.video.active is None]
        if not targets:
            if on_all_done:
                on_all_done()
            return
        self._set_status("video_running")
        tracker = _BatchTracker(len(targets), lambda: self._video_batch_done(on_all_done))
        for scene in targets:
            self._generate_scene_video(scene, plan, video_adapter, tracker)

    def _generate_scene_video(self, scene: Scene, plan: VideoPlan, video_adapter,
                              tracker: _BatchTracker) -> None:
        video_model = plan.video_model
        img = scene.image.active
        img_item = self.win.project.media.get(img.media_id) if img else None
        aud = scene.audio.active
        aud_item = self.win.project.media.get(aud.media_id) if aud else None
        prompt = scene.script or self.pipeline.brief
        # Only hand the driving audio to the video call itself when the video
        # model natively hard-syncs to it (resolve_video_plan's native_audio
        # branch) AND this scene actually wants lip-sync - otherwise a silent
        # or lip-sync-declined scene shouldn't have sync forced on it.
        native_audio_path = (aud_item.path if plan.native_audio and scene.use_lipsync
                             and aud_item else None)

        def work(job):
            job.progress(-1, f"Scene {scene.index + 1}: video")
            kwargs = {"image": img_item.path if img_item else None,
                     "progress": job.progress, "should_cancel": lambda: job.cancelled}
            if native_audio_path:
                # Most provider adapters don't accept audio= at all yet (only
                # the base class declares it) - only include the kwarg when
                # there's a real native-sync model to receive it, so every
                # other adapter's generate_video() keeps its current signature
                # and isn't handed an argument it would reject with a
                # TypeError.
                kwargs["audio"] = native_audio_path
            return video_adapter.generate_video(
                video_model.id, prompt, video_model.default_params(), **kwargs)

        def done(video_path):
            item = self.win.bin.add_generated(str(video_path), {
                "provider": video_model.provider, "model": video_model.id, "prompt": prompt,
                "mode": "video", "source": "pipeline", "pipeline_id": self.pipeline.id,
                "scene_id": scene.id})
            scene.video.push(StageAsset(media_id=item.id, prompt=prompt, provider=video_model.provider,
                                        model=video_model.id, source="generated", created=time.time()))
            if plan.lipsync_model and scene.use_lipsync:
                self._chain_lipsync(scene, item, plan.lipsync_model, tracker)
            else:
                self._swap_interim_for_final(scene, item)
                self.sceneChanged.emit(scene.id)
                tracker.one_done(True)

        def fail(msg):
            self.logMessage.emit(f"Scene {scene.index + 1} video failed: {msg}")
            tracker.one_done(False, msg)

        self.win.jobs.submit(f"Movie scene {scene.index + 1}: video", work, kind="video",
                             on_done=done, on_fail=fail)

    def _chain_lipsync(self, scene: Scene, video_item, lipsync_model, tracker: _BatchTracker) -> None:
        """The concrete first-of-its-kind 'stage N's on_done submits stage
        N+1' chain: the video job's on_done, above, calls this, which
        submits a SECOND job rather than finishing the scene immediately."""
        lipsync_adapter = self.win.get_adapter(lipsync_model.provider)
        aud = scene.audio.active
        aud_item = self.win.project.media.get(aud.media_id) if aud else None
        if not aud_item:
            self.logMessage.emit(f"Scene {scene.index + 1}: no audio to lip-sync to - keeping plain video.")
            self._swap_interim_for_final(scene, video_item)
            self.sceneChanged.emit(scene.id)
            tracker.one_done(True)
            return
        params = dict(lipsync_model.default_params())
        params["video_url"] = http.data_uri(video_item.path)
        params["audio_url"] = http.data_uri(aud_item.path)

        def work(job):
            job.progress(-1, f"Scene {scene.index + 1}: lip-sync")
            return lipsync_adapter.generate_video(lipsync_model.id, "", params)

        def done(synced_path):
            item = self.win.bin.add_generated(str(synced_path), {
                "provider": lipsync_model.provider, "model": lipsync_model.id, "mode": "lip_sync",
                "source": "pipeline", "pipeline_id": self.pipeline.id, "scene_id": scene.id})
            scene.lipsync.push(StageAsset(media_id=item.id, provider=lipsync_model.provider,
                                          model=lipsync_model.id, source="generated", created=time.time()))
            self._swap_interim_for_final(scene, item)
            self.sceneChanged.emit(scene.id)
            tracker.one_done(True)

        def fail(msg):
            # A failed lip-sync pass still leaves a perfectly good plain
            # video - fall back to it rather than losing the scene entirely.
            self.logMessage.emit(f"Scene {scene.index + 1} lip-sync failed: {msg} (keeping plain video)")
            self._swap_interim_for_final(scene, video_item)
            self.sceneChanged.emit(scene.id)
            tracker.one_done(True)

        self.win.jobs.submit(f"Movie scene {scene.index + 1}: lip-sync", work, kind="lip_sync",
                             on_done=done, on_fail=fail)

    def _swap_interim_for_final(self, scene: Scene, video_item) -> None:
        """Replaces the scene's interim image clip with the finished video
        clip at the same start/track, atomically (one undo step)."""
        old_clip_id = scene.clip_ids.get("video") or scene.clip_ids.get("image")
        old_clip = self.win.project.clips.get(old_clip_id) if old_clip_id else None
        self.win.undo_stack.beginMacro(f"Movie scene {scene.index + 1}: finalize video")
        if old_clip:
            start, track_id, duration = old_clip.start, old_clip.track_id, old_clip.duration
            self.win.timeline.delete_clip(old_clip.id)
        else:
            start, track_id, duration = self._scene_fallback_start(scene), self.pipeline.video_track_id, 3.0
        new_clip = self.win.timeline.add_media_at_playhead(video_item.id, track_id, start, duration)
        self.win.undo_stack.endMacro()
        if new_clip:
            scene.clip_ids["video"] = new_clip.id
            scene.clip_ids.pop("image", None)
        self.pipeline.save()

    def _scene_fallback_start(self, scene: Scene) -> float:
        aud_item = (self.win.project.media.get(scene.audio.active.media_id)
                   if scene.audio.active else None)
        total = 0.0
        for s in sorted(self.pipeline.scenes, key=lambda x: x.index):
            if s.index >= scene.index:
                break
            a = self.win.project.media.get(s.audio.active.media_id) if s.audio.active else None
            total += a.duration if a and a.duration else 3.0
        return total

    def _video_batch_done(self, on_all_done: Optional[Callable]) -> None:
        self._set_status("done")
        self.logMessage.emit("Movie complete.")
        if on_all_done:
            on_all_done()


def _parse_breakdown(text: str) -> list[Scene]:
    import json
    import re

    cleaned = re.sub(r"^```(?:json)?\s*|\s*```\s*$", "", text.strip())
    try:
        raw = json.loads(cleaned)
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    scenes = []
    for i, item in enumerate(raw):
        s = new_scene(i)
        if isinstance(item, dict):
            s.script = str(item.get("script", "")).strip()
            s.narration = str(item.get("narration", "")).strip()
        else:
            s.script = str(item).strip()
        scenes.append(s)
    return scenes
