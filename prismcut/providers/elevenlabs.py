"""ElevenLabs: TTS voices, sound effects and Eleven Music.

Docs: https://elevenlabs.io/docs - key: https://elevenlabs.io/app/settings/api-keys
"""
from __future__ import annotations

from typing import Optional

from ..core import http
from ..core.http import ProviderError
from .base import Adapter, CancelFn, ProgressFn

DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"  # "Rachel"


class ElevenLabs(Adapter):
    name = "elevenlabs"
    label = "ElevenLabs"

    def _headers(self, json_ct: bool = True) -> dict:
        h = {"xi-api-key": self.key()}
        if json_ct:
            h["Content-Type"] = "application/json"
        return h

    def voices(self) -> list[tuple[str, str]]:
        try:
            data = http.request_json("GET", self.base_url() + "/v1/voices",
                                     headers=self._headers(False), timeout=30)
            return [(v.get("name", "?"), v.get("voice_id", "")) for v in data.get("voices", [])]
        except ProviderError:
            return []

    def tts(self, model: str, text: str, voice: str = "", params: Optional[dict] = None) -> str:
        voice_id = voice or DEFAULT_VOICE
        body = {"text": text, "model_id": model}
        data = http.request_bytes(
            "POST", f"{self.base_url()}/v1/text-to-speech/{voice_id}",
            headers=self._headers(), json_body=body,
            params={"output_format": "mp3_44100_128"}, timeout=300)
        return self._save_bytes(data, "tts", ".mp3")

    def sound_effect(self, model: str, text: str, params: Optional[dict] = None) -> str:
        params = params or {}
        body: dict = {"text": text}
        if params.get("duration"):
            body["duration_seconds"] = float(params["duration"])
        if params.get("prompt_influence") is not None:
            body["prompt_influence"] = float(params["prompt_influence"])
        data = http.request_bytes("POST", self.base_url() + "/v1/sound-generation",
                                  headers=self._headers(), json_body=body, timeout=300)
        return self._save_bytes(data, "sfx", ".mp3")

    def music(self, model: str, prompt: str, params: Optional[dict] = None,
              progress: Optional[ProgressFn] = None,
              should_cancel: Optional[CancelFn] = None) -> str:
        params = params or {}
        body: dict = {"prompt": prompt}
        if params.get("duration"):
            body["music_length_ms"] = int(float(params["duration"]) * 1000)
        data = http.request_bytes("POST", self.base_url() + "/v1/music",
                                  headers=self._headers(), json_body=body, timeout=600)
        return self._save_bytes(data, "music", ".mp3")

    def test_key(self) -> tuple[bool, str]:
        try:
            http.request_json("GET", self.base_url() + "/v1/user",
                              headers=self._headers(False), timeout=20)
            return True, "Key OK"
        except ProviderError as e:
            return False, str(e)
