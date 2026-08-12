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
        # Idempotent past zero: the Jobs panel's "Retry" resubmits a failed
        # job with its ORIGINAL on_done/on_fail closures, which still close
        # over this same tracker - if the batch already finished (this was
        # the last one remaining, on_all_done() already fired), a later
        # manual retry of that one job must not drive remaining negative
        # and re-trigger batch-completion a second time.
        if self.remaining <= 0:
            return
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

    def scene_current_clip_id(self, scene_id: str) -> Optional[str]:
        """Whichever clip_ids entry currently represents this scene's
        picture on the timeline (the finished video if one exists, else the
        interim image) - None if the scene hasn't been placed yet."""
        scene = self.pipeline.scene(scene_id)
        if not scene:
            return None
        return scene.clip_ids.get("video") or scene.clip_ids.get("image")

    # ---------------------------------------------------------- stage 1: script
    def generate_breakdown(self, on_done: Optional[Callable] = None,
                           on_fail: Optional[Callable] = None) -> None:
        model, adapter = self._adapter_for(self.pipeline.script_model)
        brief = self.pipeline.brief
        sys_prompt = (
            "You break a movie/show brief into a numbered list of scenes for an AI "
            "generation pipeline. Respond with NOTHING but a JSON array - the very first "
            "character of your reply must be '[' and the very last must be ']'. No greeting, "
            "no explanation, no markdown code fence, no text before or after the array. Each "
            'array element is an object with "script" (a 1-2 sentence on-screen visual/action '
            'description, written as an image/video generation prompt) and "narration" (the '
            "exact dialogue or voiceover line spoken during that scene, or an empty string "
            "for a silent, visual-only scene).")

        def work(job):
            job.progress(-1, "Writing scene breakdown")
            # Low temperature: this call needs a strictly-shaped, parseable
            # response, not creative variety - the per-scene prompts
            # themselves (Scene.script) are what actually needs creativity,
            # and those come from this same call's content, not its format.
            return adapter.chat(model.id, [ChatMessage("user", brief)], system=sys_prompt,
                                temperature=0.3)

        def done(result):
            text = str(result)
            scenes = _parse_breakdown(text)
            if not scenes:
                snippet = text.strip()[:200] or "(empty response)"
                self.logMessage.emit(
                    "The AI's scene breakdown couldn't be parsed as JSON - it may have "
                    f"declined the brief or used an unexpected format. Its response started: "
                    f"“{snippet}”")
                if on_fail:
                    on_fail(f"Could not parse scene breakdown. Response started: “{snippet}”")
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
    def run_image_batch(self, on_all_done: Optional[Callable] = None,
                        limit: Optional[int] = None) -> None:
        """limit caps how many of the still-missing scenes get queued this
        call (lowest scene index first) instead of always doing every
        remaining one - lets the UI offer "just the next scene" or "next 5"
        as well as "all remaining". Calling this again later only ever
        targets scenes still missing an image, so it doubles as "continue
        where I left off" with no separate resume path needed."""
        model, adapter = self._adapter_for(self.pipeline.image_model)
        targets = sorted((s for s in self.pipeline.scenes if s.image.active is None),
                         key=lambda s: s.index)
        if limit is not None:
            targets = targets[:limit]
        if not targets:
            self._images_done = True
            self._maybe_insert_all_interim()
            if on_all_done:
                on_all_done()
            return
        self._set_status("images_running")
        tracker = _BatchTracker(len(targets), lambda: self._images_batch_done(on_all_done))
        self._run_image_chain(targets, model, adapter, tracker)

    def _run_image_chain(self, remaining: list, model, adapter, tracker: _BatchTracker) -> None:
        """Generates scene images ONE AT A TIME, in scene order, instead of
        firing every scene's job in parallel - each scene after the first
        passes reference images from earlier scenes (see
        _image_context_refs) so the model has visual context to keep
        character/outfit/art-style appearance consistent across the whole
        movie, rather than every scene being generated in isolation with no
        knowledge of what came before. This necessarily makes the batch take
        longer wall-clock time than running everything in parallel would -
        an inherent tradeoff of the earlier-scenes-as-context requirement,
        not an oversight."""
        if not remaining:
            return
        scene, rest = remaining[0], remaining[1:]
        refs = self._image_context_refs(scene)

        def advance():
            self._run_image_chain(rest, model, adapter, tracker)

        self._generate_scene_image(scene, model, adapter, tracker, extra_refs=refs,
                                   on_settled=advance)

    def _image_context_refs(self, scene: Scene) -> list:
        """Reference image paths for visual continuity: the first EARLIER
        scene that has an image (an anchor, resisting gradual drift across a
        long chain of "feed the last output back in") plus the immediately
        preceding scene with an image (for local continuity) - deduplicated
        when they're the same scene. Works whether an earlier scene's image
        came from generation or a user's draftboard override, since both
        just set scene.image.active the same way."""
        earlier = sorted((s for s in self.pipeline.scenes
                         if s.index < scene.index and s.image.active), key=lambda s: s.index)
        if not earlier:
            return []
        picks = {earlier[0].id: earlier[0], earlier[-1].id: earlier[-1]}   # anchor + previous
        refs = []
        for s in sorted(picks.values(), key=lambda s: s.index):
            item = self.win.project.media.get(s.image.active.media_id)
            if item:
                refs.append(item.path)
        return refs

    def regenerate_scene_image(self, scene_id: str, prompt_override: str = "") -> None:
        """Single-scene regenerate, bypassing the batch/gate - used by the
        per-row Regenerate button, not the bulk action. Still passes the
        same earlier-scenes reference context as the batch chain, so a
        regenerated scene stays visually consistent with the rest of the
        movie too."""
        scene = self.pipeline.scene(scene_id)
        if not scene:
            return
        model, adapter = self._adapter_for(self.pipeline.image_model)
        tracker = _BatchTracker(1, lambda: None)
        refs = self._image_context_refs(scene)
        self._generate_scene_image(scene, model, adapter, tracker, prompt_override, extra_refs=refs)

    def regenerate_scene_current_stage(self, scene_id: str) -> None:
        """Regenerates whichever stage this scene is CURRENTLY showing on
        the timeline - the finished video if one exists, else the image.
        The one action a user reaching for "fix this bad scene" wants,
        without needing to know which stage actually produced what they're
        looking at. Shared by the scene row's regenerate button and the
        timeline clip's right-click "Regenerate this scene" action."""
        scene = self.pipeline.scene(scene_id)
        if not scene:
            return
        if scene.video.active:
            self.regenerate_scene_video(scene_id)
        else:
            self.regenerate_scene_image(scene_id)

    def _generate_scene_image(self, scene: Scene, model, adapter, tracker: _BatchTracker,
                              prompt_override: str = "", extra_refs: Optional[list] = None,
                              on_settled: Optional[Callable] = None) -> None:
        prompt = prompt_override or scene.script or self.pipeline.brief
        params = {**model.default_params(), **scene.image_params}
        refs = list(extra_refs) if extra_refs else None

        def work(job):
            job.progress(-1, f"Scene {scene.index + 1}: image")
            return adapter.generate_image(model.id, prompt, params, refs)

        def done(result):
            outs = result or []
            if outs:
                item = self.win.bin.add_generated(str(outs[0]), {
                    "provider": model.provider, "model": model.id, "prompt": prompt, "mode": "image",
                    "source": "pipeline", "pipeline_id": self.pipeline.id, "scene_id": scene.id})
                scene.image.push(StageAsset(media_id=item.id, prompt=prompt, provider=model.provider,
                                            model=model.id, source="generated", created=time.time()))
                scene.last_error = ""
                if "image" in scene.clip_ids and "video" not in scene.clip_ids:
                    # A regenerate, not this scene's first-ever image - it's
                    # already sitting on the timeline, so swap the stale one
                    # for the fresh one at the same spot rather than leaving
                    # the old image visible while the new one sits unused in
                    # the bin. If the scene's video is already finalized,
                    # leave the timeline alone - only scene.image.active (a
                    # future video regenerate's input) changes.
                    self._swap_scene_visual_clip(scene, item, "image", "update image")
            else:
                # The job itself didn't raise (so fail() below never fires),
                # but came back with nothing to show for it - the shape a
                # silent moderation/content-policy rejection often takes.
                # Surface it the same as a hard failure rather than leaving
                # the scene looking merely "still pending".
                scene.last_error = "No image returned - possibly rejected by moderation or API constraints."
            self.sceneChanged.emit(scene.id)
            self.pipeline.save()
            tracker.one_done(bool(outs), "" if outs else "No image returned.")
            if on_settled:
                on_settled()

        def fail(msg):
            self.logMessage.emit(f"Scene {scene.index + 1} image failed: {msg}")
            scene.last_error = msg
            self.sceneChanged.emit(scene.id)
            self.pipeline.save()
            tracker.one_done(False, msg)
            if on_settled:
                on_settled()

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
        if "image" in scene.clip_ids and "video" not in scene.clip_ids:
            self._swap_scene_visual_clip(scene, item, "image", "update image")
        self.sceneChanged.emit(scene.id)
        self.pipeline.save()
        self._maybe_insert_all_interim()

    def _images_batch_done(self, on_all_done: Optional[Callable]) -> None:
        self._images_done = True
        remaining = sum(1 for s in self.pipeline.scenes if s.image.active is None)
        if self.pipeline.status == "images_running":
            # Only claim the images stage is fully "ready" once every scene
            # actually has one - a limited/partial batch settling just
            # returns to "scenes_ready" (not busy, more images still queued
            # on the next Continue) rather than falsely reporting done.
            self._set_status("images_ready" if remaining == 0 else "scenes_ready")
        self.logMessage.emit("All scene images ready." if remaining == 0 else
                             f"{len(self.pipeline.scenes) - remaining} scene image(s) ready, "
                             f"{remaining} still to go.")
        self._maybe_insert_all_interim()
        if on_all_done:
            on_all_done()

    # --------------------------------------------------- stage 4: audio (ungated)
    def run_audio_batch(self, on_all_done: Optional[Callable] = None) -> None:
        # Voice/TTS is optional - a pipeline built without one (silent movie,
        # or narration added manually later) has audio_model == "", which
        # registry.by_key("") can't resolve - skip the stage entirely rather
        # than let _adapter_for() raise.
        if not self.pipeline.audio_model:
            self._audio_done = True
            self._maybe_insert_all_interim()
            if on_all_done:
                on_all_done()
            return
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
            params = model.default_params()
            # Every tts() adapter resolves voice from this positional arg,
            # not from params - hardcoding "" here silently ignored
            # whatever voice was actually configured for the model (Model
            # Manager default or per-model override) and fell back to each
            # provider's own hardcoded default (Kore/alloy/Wise_Woman/...)
            # for every single scene, matching generate_panel.py's already-
            # correct pattern instead.
            voice = str(params.get("voice", ""))
            return adapter.tts(model.id, text, voice, params)

        def done(result):
            item = self.win.bin.add_generated(str(result), {
                "provider": model.provider, "model": model.id, "prompt": text, "mode": "tts",
                "source": "pipeline", "pipeline_id": self.pipeline.id, "scene_id": scene.id})
            scene.audio.push(StageAsset(media_id=item.id, prompt=text, provider=model.provider,
                                        model=model.id, source="generated", created=time.time()))
            scene.last_error = ""
            self.sceneChanged.emit(scene.id)
            self.pipeline.save()
            tracker.one_done(True)

        def fail(msg):
            self.logMessage.emit(f"Scene {scene.index + 1} audio failed: {msg}")
            scene.last_error = msg
            self.sceneChanged.emit(scene.id)
            self.pipeline.save()
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
                    img_item.id, self.pipeline.video_track_id, cursor, duration,
                    label=f"Scene {scene.index + 1}")
                if clip:
                    scene.clip_ids["image"] = clip.id
            if aud_item and "audio" not in scene.clip_ids:
                clip = self.win.timeline.add_media_at_playhead(
                    aud_item.id, self.pipeline.audio_track_id, cursor, duration,
                    label=f"Scene {scene.index + 1} (audio)")
                if clip:
                    scene.clip_ids["audio"] = clip.id
            cursor += duration
        self.win.undo_stack.endMacro()
        self.pipeline.save()
        self.logMessage.emit("Scenes laid out on the timeline.")

    # ---------------------------------------------- stage 6: video + lip-sync (gate 2)
    def run_video_batch(self, on_all_done: Optional[Callable] = None,
                        limit: Optional[int] = None) -> None:
        """See run_image_batch's docstring - same limit/continue semantics,
        applied to the video (+ optional lip-sync) stage."""
        plan = resolve_video_plan(self.win.registry, self.pipeline.video_model,
                                  self.pipeline.lipsync_model)
        video_adapter = self.win.get_adapter(plan.video_model.provider)
        targets = sorted((s for s in self.pipeline.scenes if s.video.active is None),
                         key=lambda s: s.index)
        if limit is not None:
            targets = targets[:limit]
        if not targets:
            if on_all_done:
                on_all_done()
            return
        self._set_status("video_running")
        tracker = _BatchTracker(len(targets), lambda: self._video_batch_done(on_all_done))
        for scene in targets:
            self._generate_scene_video(scene, plan, video_adapter, tracker)

    def regenerate_scene_video(self, scene_id: str) -> None:
        """Single-scene video regenerate, bypassing the batch/gate - mirrors
        regenerate_scene_image. Reuses _generate_scene_video verbatim, so a
        regenerate goes through the exact same optional lip-sync chain and
        timeline swap (_swap_scene_visual_clip, via _swap_interim_for_final)
        as a batch-run scene - whether this scene's current timeline clip is
        still an interim image or an earlier finished video, either way it
        gets replaced in place at the same position."""
        scene = self.pipeline.scene(scene_id)
        if not scene:
            return
        plan = resolve_video_plan(self.win.registry, self.pipeline.video_model,
                                  self.pipeline.lipsync_model)
        video_adapter = self.win.get_adapter(plan.video_model.provider)
        tracker = _BatchTracker(1, lambda: None)
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
        params = {**video_model.default_params(), **scene.video_params}

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
            return video_adapter.generate_video(video_model.id, prompt, params, **kwargs)

        def done(video_path):
            item = self.win.bin.add_generated(str(video_path), {
                "provider": video_model.provider, "model": video_model.id, "prompt": prompt,
                "mode": "video", "source": "pipeline", "pipeline_id": self.pipeline.id,
                "scene_id": scene.id})
            scene.video.push(StageAsset(media_id=item.id, prompt=prompt, provider=video_model.provider,
                                        model=video_model.id, source="generated", created=time.time()))
            scene.last_error = ""
            if plan.lipsync_model and scene.use_lipsync:
                self._chain_lipsync(scene, item, plan.lipsync_model, tracker)
            else:
                self._swap_interim_for_final(scene, item)
                self.sceneChanged.emit(scene.id)
                tracker.one_done(True)

        def fail(msg):
            self.logMessage.emit(f"Scene {scene.index + 1} video failed: {msg}")
            scene.last_error = msg
            self.pipeline.save()
            self.sceneChanged.emit(scene.id)
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

    def _swap_scene_visual_clip(self, scene: Scene, item, clip_key: str, macro_label: str) -> None:
        """Replaces whatever currently occupies this scene's video-track
        slot (an interim image OR a previously-finalized video) with `item`,
        at the same start/track/duration, atomically (one undo step).
        clip_key is which scene.clip_ids entry the NEW clip is recorded
        under ('image' or 'video') - the old entry is always cleared first
        so a scene never simultaneously claims both an image and a video
        slot on the timeline."""
        old_clip_id = scene.clip_ids.get("video") or scene.clip_ids.get("image")
        old_clip = self.win.project.clips.get(old_clip_id) if old_clip_id else None
        self.win.undo_stack.beginMacro(f"Movie scene {scene.index + 1}: {macro_label}")
        if old_clip:
            start, track_id, duration = old_clip.start, old_clip.track_id, old_clip.duration
            self.win.timeline.delete_clip(old_clip.id)
        else:
            start, track_id, duration = self._scene_fallback_start(scene), self.pipeline.video_track_id, 3.0
        # A finished video's own real (already-probed) length is the actual
        # authoritative answer to "how long is this scene" once one exists -
        # the duration inherited above is just the interim image+audio
        # placeholder's length (driven by narration audio). Without this, a
        # scene's requested video_params["duration"] had no visible effect:
        # the AI could honor a 10s request and the timeline clip would still
        # only ever be however long the placeholder happened to be.
        if clip_key == "video" and item.duration and item.duration > 0:
            duration = item.duration
        new_clip = self.win.timeline.add_media_at_playhead(
            item.id, track_id, start, duration, label=f"Scene {scene.index + 1}")
        self.win.undo_stack.endMacro()
        if new_clip:
            scene.clip_ids.pop("image", None)
            scene.clip_ids.pop("video", None)
            scene.clip_ids[clip_key] = new_clip.id
        self.pipeline.save()

    def _swap_interim_for_final(self, scene: Scene, video_item) -> None:
        """Replaces the scene's interim image (or an earlier video, on a
        regenerate) with the finished video clip - see
        _swap_scene_visual_clip."""
        self._swap_scene_visual_clip(scene, video_item, "video", "finalize video")

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
        remaining = sum(1 for s in self.pipeline.scenes if s.video.active is None)
        if remaining == 0:
            self._set_status("done")
            self.logMessage.emit("Movie complete.")
        else:
            self._set_status("images_ready")   # not busy; video stage still has scenes left
            self.logMessage.emit(f"{len(self.pipeline.scenes) - remaining} scene video(s) ready, "
                                 f"{remaining} still to go.")
        if on_all_done:
            on_all_done()


def _extract_json_array(text: str) -> Optional[list]:
    """Real chat models routinely ignore "output ONLY JSON" and wrap the
    array in commentary ("Sure! Here's the breakdown:\n\n```json\n[...]\n```
    \nLet me know if..."), even at low temperature - requiring the entire
    trimmed response to be exactly clean JSON (the old behavior) made that
    the single most common cause of scenes never populating. Tries
    increasingly permissive strategies rather than giving up after the
    first one fails."""
    import json
    import re

    text = text.strip()
    try:
        val = json.loads(text)
        if isinstance(val, list):
            return val
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", text)
    if fence:
        try:
            val = json.loads(fence.group(1))
            if isinstance(val, list):
                return val
        except json.JSONDecodeError:
            pass
    # Bare array with no fence, possibly with prose before/after - scan every
    # '[' as a candidate start and track bracket depth to find its true
    # matching ']' (not just the first/last bracket in the text, which would
    # break if the model's commentary itself contains a stray bracket).
    for start in (m.start() for m in re.finditer(r"\[", text)):
        depth = 0
        for end in range(start, len(text)):
            if text[end] == "[":
                depth += 1
            elif text[end] == "]":
                depth -= 1
                if depth == 0:
                    try:
                        val = json.loads(text[start:end + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(val, list):
                        return val
                    break
    return None


def _parse_breakdown(text: str) -> list[Scene]:
    raw = _extract_json_array(text)
    if raw is None:
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
