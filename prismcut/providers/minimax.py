"""MiniMax: Hailuo video (incl. MiniMax-H3 / "Hailuo 03"), Image-01 image
generation, music, TTS and chat.

Docs: https://platform.minimax.io/docs - key: https://platform.minimax.io
International base URL: https://api.minimax.io  (mainland: https://api.minimaxi.com)
"""
from __future__ import annotations

import base64
import binascii
from typing import Optional

from ..core import http
from ..core.http import ProviderError
from .base import CancelFn, ProgressFn
from .openai_compat import OpenAICompatChat


class MiniMax(OpenAICompatChat):
    name = "minimax"
    label = "MiniMax (Hailuo)"
    chat_path = "/v1/text/chatcompletion_v2"

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.key()}", "Content-Type": "application/json"}

    @staticmethod
    def _check(data: dict) -> None:
        base = data.get("base_resp") or {}
        if base.get("status_code") not in (None, 0):
            raise ProviderError(f"MiniMax error {base.get('status_code')}: "
                                f"{base.get('status_msg', 'unknown')}")

    # ---------------------------------------------------------------- image
    def generate_image(self, model: str, prompt: str, params: Optional[dict] = None,
                       refs: Optional[list] = None) -> list[str]:
        params = params or {}
        body: dict = {"model": model, "prompt": prompt, "response_format": "base64"}
        if params.get("aspect_ratio"):
            body["aspect_ratio"] = params["aspect_ratio"]
        if refs:
            # Only ONE reference image is accepted per request (unlike
            # Google's up to 8) - take the first, silently drop the rest.
            body["subject_reference"] = [{"type": "character",
                                          "image_file": http.data_uri(refs[0])}]
        data = http.request_json("POST", self.base_url() + "/v1/image_generation",
                                 headers=self._auth(), json_body=body, timeout=120)
        self._check(data)
        b64_list = (data.get("data") or {}).get("image_base64") or []
        if not b64_list:
            raise ProviderError(f"MiniMax image generation returned no image: {str(data)[:300]}")
        out = []
        for b64 in b64_list:
            try:
                out.append(self._save_bytes(base64.b64decode(b64), "image01", ".png"))
            except (binascii.Error, ValueError):
                continue
        if not out:
            raise ProviderError(
                f"MiniMax image generation returned undecodable image data: {str(data)[:300]}")
        return out

    # ---------------------------------------------------------------- video
    def generate_video(self, model: str, prompt: str, params: Optional[dict] = None,
                       image=None, progress: Optional[ProgressFn] = None,
                       should_cancel: Optional[CancelFn] = None) -> str:
        params = params or {}
        body: dict = {"model": model, "prompt": prompt}
        if params.get("duration"):
            body["duration"] = int(params["duration"])
        if params.get("resolution"):
            body["resolution"] = params["resolution"]
        if params.get("aspect_ratio"):
            body["aspect_ratio"] = params["aspect_ratio"]
        if image is not None:
            body["first_frame_image"] = http.data_uri(image)
        data = http.request_json("POST", self.base_url() + "/v1/video_generation",
                                 headers=self._auth(), json_body=body, timeout=120)
        self._check(data)
        task_id = data.get("task_id")
        if not task_id:
            raise ProviderError(f"MiniMax did not return a task id: {str(data)[:300]}")

        def fetch():
            st = http.request_json("GET", self.base_url() + "/v1/query/video_generation",
                                   headers=self._auth(), params={"task_id": task_id})
            self._check(st)
            status = (st.get("status") or "").lower()
            if status in ("success", "succeeded", "finished"):
                return st
            if status in ("fail", "failed", "error"):
                raise ProviderError(f"MiniMax generation failed: {str(st)[:200]}")
            return None

        st = self._poll(fetch, interval=8, timeout=1800, progress=progress,
                        should_cancel=should_cancel, label="Hailuo rendering")
        file_id = st.get("file_id")
        if not file_id:
            urls = http.find_media_urls(st)
            if urls:
                out = self._out("hailuo", ".mp4")
                http.download(urls[0], out)
                return str(out)
            raise ProviderError(f"MiniMax finished without file id: {str(st)[:300]}")
        f = http.request_json("GET", self.base_url() + "/v1/files/retrieve",
                              headers=self._auth(), params={"file_id": file_id})
        self._check(f)
        url = (f.get("file") or {}).get("download_url")
        if not url:
            raise ProviderError(f"MiniMax file retrieve failed: {str(f)[:300]}")
        out = self._out("hailuo", ".mp4")
        http.download(url, out)
        return str(out)

    # ---------------------------------------------------------------- music
    def music(self, model: str, prompt: str, params: Optional[dict] = None,
              progress: Optional[ProgressFn] = None,
              should_cancel: Optional[CancelFn] = None) -> str:
        params = params or {}
        body = {"model": model, "prompt": prompt,
                "lyrics": params.get("lyrics", "") or "[instrumental]",
                "audio_setting": {"sample_rate": 44100, "bitrate": 256000, "format": "mp3"}}
        data = http.request_json("POST", self.base_url() + "/v1/music_generation",
                                 headers=self._auth(), json_body=body, timeout=600)
        self._check(data)
        payload = (data.get("data") or {})
        audio_hex = payload.get("audio")
        if audio_hex:
            try:
                return self._save_bytes(binascii.unhexlify(audio_hex), "music", ".mp3")
            except (binascii.Error, ValueError):
                pass
        urls = http.find_media_urls(data)
        if urls:
            out = self._out("music", ".mp3")
            http.download(urls[0], out)
            return str(out)
        raise ProviderError(f"MiniMax music returned no audio: {str(data)[:300]}")

    # ------------------------------------------------------------------ tts
    def tts(self, model: str, text: str, voice: str = "", params: Optional[dict] = None) -> str:
        body = {"model": model, "text": text,
                "voice_setting": {"voice_id": voice or "Wise_Woman", "speed": 1.0,
                                  "vol": 1.0, "pitch": 0},
                "audio_setting": {"sample_rate": 32000, "bitrate": 128000,
                                  "format": "mp3", "channel": 1}}
        data = http.request_json("POST", self.base_url() + "/v1/t2a_v2",
                                 headers=self._auth(), json_body=body, timeout=300)
        self._check(data)
        audio_hex = (data.get("data") or {}).get("audio")
        if not audio_hex:
            raise ProviderError(f"MiniMax TTS returned no audio: {str(data)[:300]}")
        return self._save_bytes(binascii.unhexlify(audio_hex), "tts", ".mp3")
