"""Timeline export: builds one ffmpeg command from the project and runs it.

Strategy (v0.1, deliberately simple & predictable):
  * every clip becomes its own ffmpeg input (-ss/-t trims at the demuxer),
  * a black base canvas is generated at project size/fps,
  * video clips are overlaid bottom-track-first with time-shifted PTS,
  * audio (audio-track clips + embedded audio of video clips) is delayed,
    gain-staged, faded and mixed with amix.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import media as media_utils
from .project import Clip, Project


@dataclass
class RenderOptions:
    width: int = 1920
    height: int = 1080
    fps: int = 30
    fmt: str = "mp4"          # mp4 | mp4-hevc | webm | gif | png-seq | mp3 | wav
    crf: int = 20
    out_path: str = "output.mp4"
    extra: dict = field(default_factory=dict)


def _esc(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".")


def _clip_video_filters(c: Clip, w: int, h: int) -> str:
    fx = c.effects or {}
    chain = [f"scale={w}:{h}:force_original_aspect_ratio=decrease",
             f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black", "setsar=1"]
    speed = float(fx.get("speed", 1.0) or 1.0)
    eq = []
    if fx.get("brightness") not in (None, 0):
        eq.append(f"brightness={float(fx['brightness']) / 100.0:.3f}")
    if fx.get("contrast") not in (None, 0):
        eq.append(f"contrast={1.0 + float(fx['contrast']) / 100.0:.3f}")
    if fx.get("saturation") not in (None, 0):
        eq.append(f"saturation={1.0 + float(fx['saturation']) / 100.0:.3f}")
    if eq:
        chain.append("eq=" + ":".join(eq))
    if fx.get("blur"):
        chain.append(f"gblur=sigma={float(fx['blur']):.2f}")
    if fx.get("scale_pct") not in (None, 100):
        s = max(1.0, float(fx["scale_pct"])) / 100.0
        chain.append(f"scale=iw*{s:.3f}:ih*{s:.3f}")
    if fx.get("rotate_deg"):
        chain.append(f"rotate={float(fx['rotate_deg'])}*PI/180:c=black@0")
    if speed != 1.0:
        chain.append(f"setpts=PTS/{speed:.4f}")
    op = fx.get("opacity")
    if op is not None and float(op) < 100:
        chain.append(f"format=rgba,colorchannelmixer=aa={float(op) / 100.0:.3f}")
    chain.append("format=yuva420p")
    chain.append(f"setpts=PTS-STARTPTS+{_esc(c.start)}/TB")
    return ",".join(chain)


def _clip_audio_filters(c: Clip, track_gain: float = 0.0) -> str:
    fx = c.effects or {}
    speed = float(fx.get("speed", 1.0) or 1.0)
    parts = ["aresample=48000", "aformat=sample_fmts=fltp:channel_layouts=stereo",
             "asetpts=PTS-STARTPTS"]
    if speed != 1.0 and 0.5 <= speed <= 2.0:
        parts.append(f"atempo={speed:.4f}")
    gain = c.gain_db + track_gain
    if gain:
        parts.append(f"volume={gain:.2f}dB")
    if c.fade_in > 0:
        parts.append(f"afade=t=in:st=0:d={_esc(c.fade_in)}")
    if c.fade_out > 0:
        st = max(0.0, c.duration - c.fade_out)
        parts.append(f"afade=t=out:st={_esc(st)}:d={_esc(c.fade_out)}")
    ms = int(round(c.start * 1000))
    parts.append(f"adelay={ms}|{ms}")
    return ",".join(parts)


def build_command(project: Project, opts: RenderOptions) -> list[str]:
    ff = media_utils.ffmpeg_path() or "ffmpeg"
    w, h, fps = opts.width, opts.height, opts.fps
    total = max(project.duration(), 0.1)
    audio_only = opts.fmt in ("mp3", "wav")

    solo_tracks = [t.id for t in project.tracks if t.solo]

    def audible(track) -> bool:
        if track.mute:
            return False
        return not solo_tracks or track.id in solo_tracks

    inputs: list[list[str]] = []
    vparts: list[str] = []
    vlabels: list[str] = []
    aparts: list[str] = []
    alabels: list[str] = []
    idx = 0

    # bottom video track first so upper tracks overlay on top
    for track in reversed(project.video_tracks()):
        for c in project.clips_on(track.id):
            m = project.media.get(c.media_id)
            if not m or c.muted or track.mute:
                continue
            src = str(Path(m.path))
            if m.kind == "image":
                inputs.append(["-loop", "1", "-t", _esc(c.duration), "-framerate", str(fps), "-i", src])
            else:
                inputs.append(["-ss", _esc(c.in_point), "-t", _esc(c.duration), "-i", src])
            if not audio_only:
                lbl = f"v{idx}"
                vparts.append(f"[{idx}:v]{_clip_video_filters(c, w, h)}[{lbl}]")
                vlabels.append((lbl, c.start, c.end))
            if m.kind == "video" and m.has_audio and audible(track):
                al = f"a{idx}"
                aparts.append(f"[{idx}:a]{_clip_audio_filters(c, track.gain_db)}[{al}]")
                alabels.append(al)
            idx += 1

    for track in project.audio_tracks():
        for c in project.clips_on(track.id):
            m = project.media.get(c.media_id)
            if not m or c.muted or not audible(track):
                continue
            inputs.append(["-ss", _esc(c.in_point), "-t", _esc(c.duration), "-i", str(Path(m.path))])
            al = f"a{idx}"
            aparts.append(f"[{idx}:a]{_clip_audio_filters(c, track.gain_db)}[{al}]")
            alabels.append(al)
            idx += 1

    filters: list[str] = []
    maps: list[str] = []

    if not audio_only:
        filters.append(f"color=c=black:s={w}x{h}:r={fps}:d={_esc(total)}[base]")
        filters.extend(vparts)
        last = "base"
        for i, (lbl, st, en) in enumerate(vlabels):
            out = f"ov{i}"
            filters.append(f"[{last}][{lbl}]overlay=(W-w)/2:(H-h)/2:"
                           f"enable='between(t,{_esc(st)},{_esc(en)})'[{out}]")
            last = out
        if opts.fmt == "gif":
            filters.append(f"[{last}]fps=12,scale=640:-1:flags=lanczos,split[g1][g2];"
                           f"[g1]palettegen=stats_mode=diff[pal];"
                           f"[g2][pal]paletteuse=dither=bayer[vout]")
        else:
            filters.append(f"[{last}]format=yuv420p[vout]")
        maps += ["-map", "[vout]"]

    filters.extend(aparts)
    if alabels:
        if len(alabels) == 1:
            filters.append(f"[{alabels[0]}]apad=whole_dur={_esc(total)}[aout]")
        else:
            joined = "".join(f"[{a}]" for a in alabels)
            filters.append(f"{joined}amix=inputs={len(alabels)}:normalize=0:"
                           f"dropout_transition=0,apad=whole_dur={_esc(total)}[aout]")
        if opts.fmt not in ("gif", "png-seq"):
            maps += ["-map", "[aout]"]
    elif audio_only:
        filters.append(f"anullsrc=r=48000:cl=stereo:d={_esc(total)}[aout]")
        maps += ["-map", "[aout]"]

    cmd = [ff, "-y", "-hide_banner"]
    for inp in inputs:
        cmd += inp
    if not inputs and audio_only:
        pass
    cmd += ["-filter_complex", ";".join(filters)] if filters else []
    cmd += maps

    fmt = opts.fmt
    if fmt == "mp4":
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", str(opts.crf),
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
    elif fmt == "mp4-hevc":
        cmd += ["-c:v", "libx265", "-preset", "medium", "-crf", str(opts.crf + 4),
                "-tag:v", "hvc1", "-c:a", "aac", "-b:a", "192k"]
    elif fmt == "webm":
        cmd += ["-c:v", "libvpx-vp9", "-crf", str(opts.crf + 12), "-b:v", "0",
                "-c:a", "libopus", "-b:a", "160k"]
    elif fmt == "gif":
        pass
    elif fmt == "png-seq":
        out = Path(opts.out_path)
        cmd += ["-t", _esc(total), str(out.with_name(out.stem + "_%05d.png"))]
        return cmd
    elif fmt == "mp3":
        cmd += ["-c:a", "libmp3lame", "-b:a", "256k"]
    elif fmt == "wav":
        cmd += ["-c:a", "pcm_s16le"]
    cmd += ["-t", _esc(total), str(opts.out_path)]
    return cmd


_TIME_RE = re.compile(rb"out_time_ms=(\d+)")


def run_render(project: Project, opts: RenderOptions, job=None) -> str:
    """Execute the render; reports progress via the job. Returns output path."""
    if not media_utils.have_ffmpeg():
        raise RuntimeError("ffmpeg not found. Install it and make sure it is on PATH "
                           "(https://ffmpeg.org/download.html).")
    if not project.clips:
        raise RuntimeError("Timeline is empty - add some clips first.")
    cmd = build_command(project, opts)
    total = max(project.duration(), 0.1)
    proc = subprocess.Popen(cmd + ["-progress", "pipe:1", "-nostats"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    err_tail = b""
    try:
        while True:
            if job is not None and job.cancelled:
                proc.kill()
                raise RuntimeError("Render cancelled")
            line = proc.stdout.readline()
            if not line:
                break
            m = _TIME_RE.search(line)
            if m and job is not None:
                secs = int(m.group(1)) / 1_000_000.0
                job.progress(min(99, int(secs / total * 100)), f"Rendering {secs:.1f}s / {total:.1f}s")
        err_tail = proc.stderr.read() or b""
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed:\n" + err_tail.decode("utf-8", "replace")[-800:])
    if job is not None:
        job.progress(100, "Done")
    return str(opts.out_path)
