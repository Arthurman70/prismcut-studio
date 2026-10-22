"""Meta Model API: Muse chat, image generation/editing and file transcription.

API contracts: https://dev.meta.ai/docs (verified 2026-10-22).
Standard-mode IDs only: Contributor mode has different data-use terms.
"""
from __future__ import annotations

import base64
import json
import math
import subprocess
import tempfile
import wave
from pathlib import Path

from ..core import http, media
from ..core.captions import Segment
from ..core.http import NotSupported, ProviderError
from .openai_compat import OpenAICompatChat


class Meta(OpenAICompatChat):
    name = "meta"
    label = "Meta (Muse)"

    def base_url(self):
        # Accept either the API origin or the /v1 base used in Meta's SDK examples.
        base = super().base_url()
        return base[:-3] if base.endswith("/v1") else base

    def _auth(self):
        return {"Authorization": f"Bearer {self.key()}"}

    def _content_for(self, msg):
        if not msg.attachments:
            return msg.text
        parts = [{"type": "text", "text": msg.text or ""}]
        for path in msg.attachments:
            file = Path(path)
            if file.stat().st_size > 50_000_000:
                raise ProviderError("Muse chat attachments must be 50 MB or smaller.")
            ext = file.suffix.lower()
            if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                parts.append({"type": "image_url", "image_url": {"url": http.data_uri(file)}})
            elif ext in (".mp3", ".wav"):
                parts.append({"type": "input_audio", "input_audio": {
                    "data": http.file_b64(file), "format": ext[1:]}})
            elif ext == ".mp4":
                parts.append({"type": "video_url", "video_url": {"url": http.data_uri(file)}})
            elif ext == ".pdf":
                parts.append({"type": "file", "file": {"filename": file.name,
                                                          "file_data": http.data_uri(file)}})
            else:
                raise NotSupported("Muse accepts PNG/JPEG/GIF/WebP, MP4, WAV/MP3 and PDF attachments. "
                                   "Convert this file before attaching it.")
        return parts

    @staticmethod
    def _image_body(model, prompt, params):
        params = params or {}
        n = int(params.get("n", 1))
        if not 1 <= n <= 10:
            raise ProviderError("Muse Image supports between 1 and 10 images per request.")
        fmt = params.get("output_format", "png")
        if fmt not in ("png", "jpeg", "webp"):
            raise ProviderError("Muse Image output must be PNG, JPEG or WebP.")
        body = {"model": model, "prompt": prompt, "n": n,
                "response_format": "b64_json", "output_format": fmt}
        if params.get("size") and params["size"] != "auto":
            body["size"] = params["size"]
        if params.get("reasoning_strength"):
            body["reasoning_strength"] = params["reasoning_strength"]
        return body

    def _images(self, path, body):
        data = http.request_json("POST", self.base_url() + path,
                                 headers={**self._auth(), "Content-Type": "application/json"},
                                 json_body=body, timeout=600)
        outputs = []
        ext = "." + body["output_format"]
        for item in data.get("data", []):
            if item.get("b64_json"):
                try:
                    raw = base64.b64decode(item["b64_json"], validate=True)
                except (ValueError, TypeError) as exc:
                    raise ProviderError("Muse returned invalid image data.") from exc
                outputs.append(self._save_bytes(raw, "image", ext))
            elif item.get("url"):
                out = self._out("image", ext)
                http.download(item["url"], out)  # Never forward the API key to a CDN.
                outputs.append(str(out))
        if not outputs:
            raise ProviderError("Muse returned no images. Check the prompt and account access.")
        return outputs

    def generate_image(self, model, prompt, params=None, refs=None):
        body = self._image_body(model, prompt, params)
        if refs:
            body["images"] = [{"image_url": http.data_uri(p)} for p in refs]
        return self._images("/v1/images/edits" if refs else "/v1/images/generations", body)

    def edit_image(self, model, prompt, image, mask=None, params=None):
        if mask is not None:
            raise NotSupported("Muse Image supports whole-image prompt edits, not a pixel mask. "
                               "Clear the mask or choose a model with masked inpainting.")
        return self.generate_image(model, prompt, params, refs=[image])

    @staticmethod
    def _wav_info(path):
        try:
            with wave.open(str(path), "rb") as audio:
                rate = audio.getframerate()
                duration = audio.getnframes() / rate if rate else math.inf
                valid = (audio.getnchannels() == 1 and audio.getsampwidth() == 2
                         and rate in (16000, 24000) and audio.getcomptype() == "NONE")
                return valid, duration
        except (wave.Error, EOFError):
            return False, None

    def _transcription(self, model, audio, mode):
        source = Path(audio)
        valid, duration = self._wav_info(source)
        if duration is not None and duration > 600:
            raise ProviderError("Muse Voice accepts clips up to 10 minutes. Split this clip first.")
        with tempfile.TemporaryDirectory(prefix="prismcut-muse-") as work:
            upload = source
            if not valid:
                ffmpeg = media.ffmpeg_path()
                if not ffmpeg:
                    raise ProviderError("Install ffmpeg to convert audio for Muse Voice, or supply "
                                        "a mono 16-bit PCM WAV at 16 or 24 kHz.")
                upload = Path(work) / "audio.wav"
                # Read at most 601 s: detect over-limit inputs without decoding
                # arbitrarily long media or silently truncating the transcript.
                command = [ffmpeg, "-y", "-i", str(source), "-vn", "-t", "601",
                           "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le",
                           "-map_metadata", "-1", str(upload)]
                try:
                    result = subprocess.run(command, capture_output=True, timeout=180,
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                except (OSError, subprocess.TimeoutExpired) as exc:
                    raise ProviderError("Could not convert this audio for Muse Voice.") from exc
                if result.returncode:
                    raise ProviderError("ffmpeg could not convert this audio for Muse Voice.")
                valid, duration = self._wav_info(upload)
            if not valid or duration is None or duration <= 0:
                raise ProviderError("Muse Voice requires a nonempty mono PCM WAV recording.")
            if duration > 600:
                raise ProviderError("Muse Voice accepts clips up to 10 minutes. Split this clip first.")
            if upload.stat().st_size > 31_000_000:
                raise ProviderError("The WAV file is too large for Muse Voice's 32 MB request limit.")
            request = {"model": model, "mode": mode, "audioEncoding": "WAV"}
            data = http.request_json("POST", self.base_url() + "/v1/asr/transcribe",
                                     headers={**self._auth(), "Accept": "application/json"},
                                     files={"request": (None, json.dumps(request), "application/json"),
                                            "audio": ("audio.wav", upload.read_bytes(), "audio/wav")},
                                     timeout=600)
        if "transcript" not in data:
            raise ProviderError("Muse Voice returned no transcript.")
        return data

    def transcribe(self, model, audio):
        return self._transcription(model, audio, "PUSH_TO_TALK")["transcript"]

    def transcribe_segments(self, model, audio):
        data = self._transcription(model, audio, "ENDPOINTING")
        turns = data.get("turns", [])
        if not turns and data.get("transcript", "").strip():
            raise ProviderError("Muse returned text without turn timestamps; cannot create captions.")
        segments = []
        for turn in turns:
            try:
                start, end = float(turn["startMs"]) / 1000, float(turn["endMs"]) / 1000
                if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
                    raise ValueError("invalid timing")
                segments.append(Segment(start, end, str(turn["transcript"])))
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderError("Muse returned invalid caption timing.") from exc
        return segments

    def test_key(self):
        try:
            http.request_json("GET", self.base_url() + "/v1/models",
                              headers=self._auth(), timeout=20)
            return True, "Meta key OK"
        except (ProviderError, OSError) as exc:
            return False, str(exc)
