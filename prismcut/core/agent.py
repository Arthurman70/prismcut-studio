"""Agent Mode: lets the Chat panel's AI call a curated set of tools to
actually drive the app, instead of only talking. See providers/tools.py for
the wire-format-agnostic Tool/ToolCall/ToolResult shapes each adapter
translates to/from.

Every tool that mutates the project routes through the SAME undo
infrastructure (core/undo_commands.py) every human-driven edit already uses,
and every mutating tool is gated by the same confirm_destructive() dialog
used everywhere else in the app - Agent Mode adds no new confirmation UI,
just a new caller of the existing one.

Deliberately excluded (see the plan / PR description for why): raw
filesystem actions, remove_media/remove_clip/remove_track, export/render,
API key read/write, anything already behind confirm_destructive elsewhere,
and app configuration (Model Manager/Settings/theme/shortcuts).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, Qt, Signal

from ..providers.tools import Tool, ToolCall, ToolResult
from .undo_commands import AddOrRemoveItemCommand, ChangePropertiesCommand

_STR = {"type": "string"}
_NUM = {"type": "number"}
_BOOL = {"type": "boolean"}


def _obj(properties: dict, required: list | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or []}


@dataclass
class _ToolSpec:
    tool: Tool
    mutates: bool
    handler: Callable[[dict], str]
    describe: Optional[Callable[[dict], str]] = None   # human-readable summary for the confirm dialog


class AgentToolRunner(QObject):
    """Owns the tool dispatch table and the cross-thread hop that makes it
    safe to call. Tool dispatch happens on the ChatWorker (background)
    thread, but any tool that touches the project must run on the GUI
    thread - QUndoStack.push() fires UI-updating signals, and confirm
    dialogs can only be shown from the GUI thread. A Qt BlockingQueuedConnection
    is the exact primitive for "call this on another thread and block until
    it's done": dispatch() emits a signal and blocks; the connected slot
    genuinely executes on the GUI thread and can safely push undo commands
    or show a modal dialog before returning its result."""

    # No-argument signal, deliberately: PySide6 copies mutable Python
    # objects (lists, custom objects passed as `object`) when they cross a
    # queued connection, including BlockingQueuedConnection - a slot
    # appending to a `list` argument mutates a COPY, not the caller's
    # original, so the result would silently never make it back. Passing
    # the call in and the result out via plain instance attributes instead
    # works because `self` is the same real QObject on both sides of the
    # connection, never copied - only the signal *arguments* are. This is
    # safe because dispatch() is only ever called one-at-a-time in practice
    # (ChatPanel allows only one live ChatWorker, and each adapter's tool
    # loop calls on_tool_call synchronously, never concurrently).
    _invoke = Signal()

    def __init__(self, main_window):
        super().__init__(main_window)
        self.win = main_window
        self._invoke.connect(self._run_on_gui_thread, Qt.ConnectionType.BlockingQueuedConnection)
        self._pending_call: Optional[ToolCall] = None
        self._last_result: Optional[ToolResult] = None
        self._specs: dict[str, _ToolSpec] = {}
        self._register_all()

    # ------------------------------------------------------------ dispatch
    def dispatch(self, call: ToolCall) -> ToolResult:
        """Called from the ChatWorker thread (via adapter on_tool_call)."""
        self._pending_call = call
        self._invoke.emit()   # blocks until _run_on_gui_thread has returned
        return self._last_result or ToolResult(call.id, "No result.", is_error=True)

    def _run_on_gui_thread(self):
        call = self._pending_call
        spec = self._specs.get(call.name)
        if spec is None:
            self._last_result = ToolResult(call.id, f"Unknown tool: {call.name}", is_error=True)
            return
        if spec.mutates:
            from ..ui.widgets.common import confirm_destructive
            summary = spec.describe(call.arguments) if spec.describe else str(call.arguments)
            if not confirm_destructive(
                    self.win, self.win.settings, f"agent_tool_{call.name}",
                    "AI wants to make a change",
                    f"The AI wants to:\n\n{summary}\n\nAllow it?", "Allow"):
                self._last_result = ToolResult(call.id, "The user declined this action.", is_error=True)
                return
        try:
            self._last_result = ToolResult(call.id, spec.handler(call.arguments))
        except Exception as exc:  # noqa: BLE001 - any tool failure must reach the model as text, not crash the app
            self._last_result = ToolResult(call.id, f"Error: {exc}", is_error=True)

    @property
    def tools(self) -> list[Tool]:
        return [s.tool for s in self._specs.values()]

    def _add(self, name, description, parameters, handler, mutates=False, describe=None):
        self._specs[name] = _ToolSpec(Tool(name, description, parameters), mutates, handler, describe)

    # ================================================================ read
    def _register_all(self):
        self._add("list_media", "List every media item in the project bin.", _obj({}),
                  self._t_list_media)
        self._add("get_timeline_summary", "Summarize the timeline: tracks and clip counts/duration.",
                  _obj({}), self._t_timeline_summary)
        self._add("get_clip", "Get full details of one timeline clip.",
                  _obj({"clip_id": _STR}, ["clip_id"]), self._t_get_clip)
        self._add("search_bin", "Search project bin media by label substring.",
                  _obj({"query": _STR}, ["query"]), self._t_search_bin)
        self._add("list_available_models",
                  "List configured models for a capability "
                  "(chat/image_generate/image_edit/video_generate/tts/music/sfx/transcribe/lip_sync).",
                  _obj({"capability": _STR}, ["capability"]), self._t_list_models)
        self._add("get_job_status", "List active and recently finished generation jobs.",
                  _obj({}), self._t_job_status)
        self._add("move_playhead", "Move the timeline playhead to a given time in seconds.",
                  _obj({"seconds": _NUM}, ["seconds"]), self._t_move_playhead)
        self._add("set_selected_clip", "Select a clip on the timeline by id.",
                  _obj({"clip_id": _STR}, ["clip_id"]), self._t_select_clip)

        # ---------------------------------------------------------- additive
        self._add("add_media_to_timeline",
                  "Add an existing bin media item to the timeline.",
                  _obj({"media_id": _STR, "track_id": _STR,
                       "start_seconds": _NUM, "duration_seconds": _NUM}, ["media_id"]),
                  self._t_add_to_timeline, mutates=True,
                  describe=lambda a: f"Add media {a.get('media_id')} to the timeline")
        self._add("create_track", "Create a new video or audio track.",
                  _obj({"kind": {"type": "string", "enum": ["video", "audio"]}}, ["kind"]),
                  self._t_create_track, mutates=True,
                  describe=lambda a: f"Create a new {a.get('kind', '?')} track")

        # -------------------------------------------------------- generation
        for cap, method, kind, out_group in (
                ("generate_image", "generate_image", "image", "generated"),
                ("generate_video", "generate_video", "video", "generated"),
                ("generate_speech", "tts", "tts", "audio"),
                ("generate_music", "music", "music", "audio"),
                ("generate_sfx", "sound_effect", "sfx", "audio")):
            self._add(
                cap, f"Queue a {kind} generation job (returns immediately - check get_job_status "
                    "or wait for the result to appear in the bin).",
                _obj({"model_key": _STR, "prompt": _STR, "reference_media_id": _STR},
                    ["model_key", "prompt"]),
                self._make_generation_handler(method, kind), mutates=True,
                describe=lambda a, k=kind: f"Generate {k} with {a.get('model_key', '?')}: "
                                           f"“{str(a.get('prompt', ''))[:80]}”")

        # -------------------------------------------------------- adjustment
        self._add("set_clip_properties",
                  "Adjust a timeline clip's timing/audio/label. Omit fields you don't want to change.",
                  _obj({"clip_id": _STR, "start_seconds": _NUM, "duration_seconds": _NUM,
                       "gain_db": _NUM, "fade_in_seconds": _NUM, "fade_out_seconds": _NUM,
                       "muted": _BOOL, "label": _STR}, ["clip_id"]),
                  self._t_set_clip_properties, mutates=True,
                  describe=lambda a: f"Change properties of clip {a.get('clip_id')}")
        self._add("set_clip_effects",
                  "Set a clip's effects (scale_pct/rotate_deg/opacity/brightness/contrast/"
                  "saturation/blur/speed) - replaces the whole effects set.",
                  _obj({"clip_id": _STR, "effects": {"type": "object"}}, ["clip_id", "effects"]),
                  self._t_set_clip_effects, mutates=True,
                  describe=lambda a: f"Change effects on clip {a.get('clip_id')}")
        self._add("set_track_properties", "Adjust a track's mute/solo/lock/gain/pan.",
                  _obj({"track_id": _STR, "mute": _BOOL, "solo": _BOOL, "locked": _BOOL,
                       "gain_db": _NUM, "pan": _NUM}, ["track_id"]),
                  self._t_set_track_properties, mutates=True,
                  describe=lambda a: f"Change properties of track {a.get('track_id')}")
        self._add("rename_media", "Rename a bin media item's label.",
                  _obj({"media_id": _STR, "label": _STR}, ["media_id", "label"]),
                  self._t_rename_media, mutates=True,
                  describe=lambda a: f"Rename media {a.get('media_id')} to “{a.get('label')}”")

    # ---------------------------------------------------------------- read impls
    def _t_list_media(self, args: dict) -> str:
        items = list(self.win.project.media.values())
        if not items:
            return "The project bin is empty."
        return "\n".join(f"- {m.id}: {m.label!r} ({m.kind}, {m.duration:.1f}s, group={m.group})"
                         for m in items)

    def _t_timeline_summary(self, args: dict) -> str:
        p = self.win.project
        lines = [f"Timeline duration: {p.duration():.1f}s"]
        for t in p.tracks:
            clips = p.clips_on(t.id)
            lines.append(f"- {t.name} ({t.kind}{', muted' if t.mute else ''}): {len(clips)} clip(s)")
        return "\n".join(lines)

    def _t_get_clip(self, args: dict) -> str:
        c = self.win.project.clips.get(args.get("clip_id", ""))
        if not c:
            return "No such clip."
        return (f"id={c.id} media_id={c.media_id} track_id={c.track_id} start={c.start:.2f} "
               f"duration={c.duration:.2f} label={c.label!r} gain_db={c.gain_db} "
               f"muted={c.muted} effects={c.effects}")

    def _t_search_bin(self, args: dict) -> str:
        q = str(args.get("query", "")).lower()
        hits = [m for m in self.win.project.media.values() if q in m.label.lower()]
        if not hits:
            return f"No media matching {q!r}."
        return "\n".join(f"- {m.id}: {m.label!r} ({m.kind})" for m in hits)

    def _t_list_models(self, args: dict) -> str:
        cap = args.get("capability", "")
        models = self.win.registry.models_with(cap)
        if not models:
            return f"No models configured for capability {cap!r}."
        lines = []
        for m in models:
            prov = self.win.registry.provider(m.provider)
            has_key = self.win.settings.has_key(m.provider, prov.key_env if prov else "")
            lines.append(f"- {m.key}: {m.display} ({'key set' if has_key else 'NO KEY'})")
        return "\n".join(lines)

    def _t_job_status(self, args: dict) -> str:
        jobs = self.win.jobs.jobs[-20:]
        if not jobs:
            return "No jobs yet."
        return "\n".join(f"- {j.title}: {j.status}" + (f" ({j.message})" if j.message else "")
                         for j in jobs)

    def _t_move_playhead(self, args: dict) -> str:
        self.win.timeline.set_playhead(float(args.get("seconds", 0)))
        return f"Playhead moved to {args.get('seconds', 0)}s."

    def _t_select_clip(self, args: dict) -> str:
        clip_id = args.get("clip_id", "")
        item = self.win.timeline._items.get(clip_id)
        if not item:
            return "No such clip."
        self.win.timeline.scene.clearSelection()
        item.setSelected(True)
        return f"Selected clip {clip_id}."

    # ------------------------------------------------------------ additive impls
    def _t_add_to_timeline(self, args: dict) -> str:
        clip = self.win.timeline.add_media_at_playhead(
            args["media_id"], args.get("track_id", ""),
            args.get("start_seconds"), args.get("duration_seconds"))
        if not clip:
            return "Couldn't add that media (bad media_id, track_id, or no matching track exists)."
        return f"Added clip {clip.id} at {clip.start:.2f}s on track {clip.track_id}."

    def _t_create_track(self, args: dict) -> str:
        track = self.win.add_track(args.get("kind", "video"))
        return f"Created track {track.id} ({track.name})."

    # --------------------------------------------------------- generation impls
    def _make_generation_handler(self, adapter_method: str, kind: str):
        def handler(args: dict) -> str:
            model = self.win.registry.by_key(args.get("model_key", ""))
            if not model:
                return f"Unknown model_key {args.get('model_key')!r}."
            adapter = self.win.get_adapter(model.provider)
            prompt = str(args.get("prompt", ""))
            ref_id = args.get("reference_media_id")
            ref_path = None
            if ref_id:
                item = self.win.project.media.get(ref_id)
                ref_path = item.path if item else None

            def work(job):
                job.progress(-1, f"{kind} via {model.display}")
                if adapter_method == "generate_image":
                    return adapter.generate_image(model.id, prompt, model.default_params(),
                                                  [ref_path] if ref_path else None)
                if adapter_method == "generate_video":
                    return [adapter.generate_video(model.id, prompt, model.default_params(),
                                                   image=ref_path, progress=job.progress,
                                                   should_cancel=lambda: job.cancelled)]
                if adapter_method == "tts":
                    return [adapter.tts(model.id, prompt, "", model.default_params())]
                if adapter_method == "music":
                    return [adapter.music(model.id, prompt, model.default_params(),
                                          progress=job.progress, should_cancel=lambda: job.cancelled)]
                return [adapter.sound_effect(model.id, prompt, model.default_params())]

            meta = {"provider": model.provider, "model": model.id, "prompt": prompt,
                    "mode": kind, "source": "agent"}

            def done(result):
                for out in result or []:
                    self.win.bin.add_generated(str(out), meta)
                self.win.toast(f"AI: {kind} ready ({model.display})", "success")

            def fail(msg):
                self.win.toast(f"AI: {kind} failed - {msg}", "error")

            self.win.jobs.submit(f"AI {kind}: {prompt[:32]}… · {model.display}",
                                 work, kind=kind, on_done=done, on_fail=fail)
            return f"Queued a {kind} job with {model.display}. It'll land in the bin when done."
        return handler

    # -------------------------------------------------------- adjustment impls
    def _t_set_clip_properties(self, args: dict) -> str:
        c = self.win.project.clips.get(args.get("clip_id", ""))
        if not c:
            return "No such clip."
        field_map = {"start_seconds": "start", "duration_seconds": "duration", "gain_db": "gain_db",
                    "fade_in_seconds": "fade_in", "fade_out_seconds": "fade_out",
                    "muted": "muted", "label": "label"}
        before, after = {}, {}
        for arg_key, field_name in field_map.items():
            if arg_key in args:
                before[field_name] = getattr(c, field_name)
                after[field_name] = args[arg_key]
        if not after:
            return "Nothing to change."
        self.win.undo_stack.push(ChangePropertiesCommand(
            f"AI: Adjust {c.label or 'clip'}", c, before, after,
            lambda: (self.win.timeline.refresh(), self.win.timeline.timelineChanged.emit())))
        return f"Updated clip {c.id}: {after}."

    def _t_set_clip_effects(self, args: dict) -> str:
        c = self.win.project.clips.get(args.get("clip_id", ""))
        if not c:
            return "No such clip."
        new_effects = args.get("effects") or {}
        self.win.undo_stack.push(ChangePropertiesCommand(
            f"AI: Effects on {c.label or 'clip'}", c, {"effects": dict(c.effects)},
            {"effects": new_effects},
            lambda: (self.win.effects.show_clip(c.id) if self.win.effects.clip_id == c.id else None,
                    self.win.timeline.refresh())))
        return f"Updated effects on clip {c.id}: {new_effects}."

    def _t_set_track_properties(self, args: dict) -> str:
        t = self.win.project.track(args.get("track_id", ""))
        if not t:
            return "No such track."
        field_map = {"mute": "mute", "solo": "solo", "locked": "locked",
                    "gain_db": "gain_db", "pan": "pan"}
        before, after = {}, {}
        for arg_key, field_name in field_map.items():
            if arg_key in args:
                before[field_name] = getattr(t, field_name)
                after[field_name] = args[arg_key]
        if not after:
            return "Nothing to change."
        self.win.undo_stack.push(ChangePropertiesCommand(
            f"AI: Adjust {t.name}", t, before, after,
            lambda: (self.win.timeline.refresh(), self.win.audio.refresh_tracks())))
        return f"Updated track {t.id}: {after}."

    def _t_rename_media(self, args: dict) -> str:
        m = self.win.project.media.get(args.get("media_id", ""))
        if not m:
            return "No such media item."
        new_label = str(args.get("label", m.label))
        self.win.undo_stack.push(ChangePropertiesCommand(
            "AI: Rename media", m, {"label": m.label}, {"label": new_label},
            lambda: (setattr(self.win.project, "dirty", True), self.win.bin.refresh())))
        return f"Renamed {m.id} to {new_label!r}."
