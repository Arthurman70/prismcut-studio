"""Media helpers around ffmpeg/ffprobe and Pillow (Qt-free)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from . import paths

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg", ".wmv"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus", ".wma", ".aiff"}

# Custom drag mime type carrying newline-joined MediaItem ids, used to drag
# items out of the Project Bin's tree onto the Timeline - lives here (Qt-free
# core) rather than in either UI file so neither has to import the other
# just for this one constant.
MEDIA_ID_MIME_TYPE = "application/x-prismcut-media-id"


def ffmpeg_path() -> Optional[str]:
    return shutil.which("ffmpeg")


def ffprobe_path() -> Optional[str]:
    return shutil.which("ffprobe")


def have_ffmpeg() -> bool:
    return ffmpeg_path() is not None


def kind_of(path) -> str:
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    return "other"


_KIND_EXTS = {"image": IMAGE_EXT, "video": VIDEO_EXT, "audio": AUDIO_EXT}


def _filter_group(label: str, exts: set) -> str:
    return f"{label} (" + " ".join(f"*{e}" for e in sorted(exts)) + ")"


def accepted_exts(*kinds: str) -> set:
    """Union of extensions for the given kinds, e.g. accepted_exts('image','video')."""
    return set().union(*(_KIND_EXTS[k] for k in kinds)) if kinds else set()


def is_accepted(path, *kinds: str) -> bool:
    """True if path's extension belongs to one of the given kinds
    ('image'/'video'/'audio'). No kinds given == accept anything."""
    if not kinds:
        return True
    return Path(path).suffix.lower() in accepted_exts(*kinds)


IMAGE_FILTER = _filter_group("Images", IMAGE_EXT)
VIDEO_FILTER = _filter_group("Video", VIDEO_EXT)
AUDIO_FILTER = _filter_group("Audio", AUDIO_EXT)
AV_FILTER = ";;".join([_filter_group("Audio/Video", VIDEO_EXT | AUDIO_EXT), "All files (*)"])
MEDIA_FILTER = ";;".join([_filter_group("Media files", IMAGE_EXT | VIDEO_EXT | AUDIO_EXT),
                          IMAGE_FILTER, VIDEO_FILTER, AUDIO_FILTER, "All files (*)"])


def probe(path) -> dict:
    """Return {duration, width, height, has_audio, has_video} best-effort."""
    p = Path(path)
    info = {"duration": 0.0, "width": 0, "height": 0, "has_audio": False, "has_video": False}
    kind = kind_of(p)
    if kind == "image":
        try:
            from PIL import Image
            with Image.open(p) as im:
                info.update(width=im.width, height=im.height)
        except Exception:
            pass
        info["has_video"] = True
        return info
    fp = ffprobe_path()
    if not fp:
        return info
    try:
        out = subprocess.run(
            [fp, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(p)],
            capture_output=True, text=True, timeout=30).stdout
        data = json.loads(out or "{}")
        fmt = data.get("format", {})
        info["duration"] = float(fmt.get("duration") or 0.0)
        for s in data.get("streams", []):
            if s.get("codec_type") == "video":
                info["has_video"] = True
                info["width"] = int(s.get("width") or 0)
                info["height"] = int(s.get("height") or 0)
            elif s.get("codec_type") == "audio":
                info["has_audio"] = True
    except Exception:
        pass
    return info


def thumbnail(path, size: int = 288) -> Optional[Path]:
    """Return a cached thumbnail PNG for any media file (image/video/audio)."""
    p = Path(path)
    if not p.exists():
        return None
    out = paths.thumbs_dir() / f"{abs(hash((str(p), p.stat().st_mtime, size)))}.png"
    if out.exists():
        return out
    kind = kind_of(p)
    try:
        if kind == "image":
            from PIL import Image
            with Image.open(p) as im:
                im = im.convert("RGB")
                im.thumbnail((size, size))
                im.save(out)
            return out
        ff = ffmpeg_path()
        if not ff:
            return None
        if kind == "video":
            subprocess.run([ff, "-y", "-v", "error", "-ss", "0.4", "-i", str(p),
                            "-frames:v", "1", "-vf", f"scale={size}:-1", str(out)],
                           capture_output=True, timeout=30)
        elif kind == "audio":
            subprocess.run([ff, "-y", "-v", "error", "-i", str(p),
                            "-filter_complex",
                            f"showwavespic=s={size}x{size // 2}:colors=#26c6da|#80deea",
                            "-frames:v", "1", str(out)],
                           capture_output=True, timeout=30)
        return out if out.exists() else None
    except Exception:
        return None


def waveform_png(path, out_png: Optional[Path] = None, width: int = 640, height: int = 120,
                 color: str = "#26c6da") -> Optional[Path]:
    ff = ffmpeg_path()
    p = Path(path)
    if not ff or not p.exists():
        return None
    out = Path(out_png) if out_png else paths.thumbs_dir() / f"wf_{abs(hash((str(p), width, height)))}.png"
    if out.exists():
        return out
    try:
        subprocess.run([ff, "-y", "-v", "error", "-i", str(p), "-filter_complex",
                        f"aformat=channel_layouts=mono,showwavespic=s={width}x{height}:colors={color}",
                        "-frames:v", "1", str(out)], capture_output=True, timeout=60)
        return out if out.exists() else None
    except Exception:
        return None


def measure_loudness(path) -> Optional[float]:
    """Mean volume in dB via ffmpeg volumedetect (for one-click normalize)."""
    ff = ffmpeg_path()
    if not ff:
        return None
    try:
        res = subprocess.run([ff, "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                             capture_output=True, text=True, timeout=120)
        for line in res.stderr.splitlines():
            if "mean_volume:" in line:
                return float(line.split("mean_volume:")[1].strip().split(" ")[0])
    except Exception:
        pass
    return None


def convert_audio(src, dst_ext: str = ".wav") -> Optional[Path]:
    """Convert audio to a widely accepted upload format (wav/mp3)."""
    ff = ffmpeg_path()
    src = Path(src)
    if not ff:
        return src
    out = paths.cache_dir() / (src.stem + "_conv" + dst_ext)
    try:
        subprocess.run([ff, "-y", "-v", "error", "-i", str(src), str(out)],
                       capture_output=True, timeout=300)
        return out if out.exists() else src
    except Exception:
        return src


def extract_audio(src, dst_ext: str = ".m4a") -> Optional[Path]:
    """Pulls just the audio stream out of a video file (ffmpeg -vn), so it
    can be edited as an independent clip - distinct from convert_audio()
    (which re-encodes a file that's already audio-only). Returns None on
    failure rather than the original path: callers need to tell "here's
    your extracted audio" apart from "extraction didn't happen", since on
    failure a video clip should keep playing its own embedded audio
    instead of being silently muted with nothing to replace it."""
    ff = ffmpeg_path()
    src = Path(src)
    if not ff or not src.exists():
        return None
    out = paths.cache_dir() / f"{src.stem}_audio_{abs(hash((str(src), src.stat().st_mtime)))}{dst_ext}"
    if out.exists():
        return out
    try:
        subprocess.run([ff, "-y", "-v", "error", "-i", str(src), "-vn", str(out)],
                       capture_output=True, timeout=300)
        return out if out.exists() else None
    except Exception:
        return None


def generate_proxy(src, height: int = 540) -> Optional[Path]:
    """Fast, low-res transcode of a video for smoother preview scrubbing of
    a heavy source - never used for export, which always renders from the
    original file untouched (see MediaItem.proxy_path). -2 rounds the
    auto-computed width to the nearest even number, required by most video
    codecs. Returns None on failure rather than the original path, same
    "tell success apart from no-op" contract as extract_audio()."""
    ff = ffmpeg_path()
    src = Path(src)
    if not ff or not src.exists():
        return None
    out = paths.cache_dir() / f"{src.stem}_proxy_{abs(hash((str(src), src.stat().st_mtime)))}.mp4"
    if out.exists():
        return out
    try:
        subprocess.run([ff, "-y", "-v", "error", "-i", str(src),
                        "-vf", f"scale=-2:{height}",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                        "-c:a", "aac", "-b:a", "96k", str(out)],
                       capture_output=True, timeout=600)
        return out if out.exists() else None
    except Exception:
        return None


def pcm_to_wav(pcm: bytes, out_path: Path, rate: int = 24000, channels: int = 1,
               sampwidth: int = 2) -> Path:
    """Wrap raw 16-bit PCM (e.g. Gemini TTS output) into a WAV container."""
    import wave
    out_path = Path(out_path)
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sampwidth)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out_path
