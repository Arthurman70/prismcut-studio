"""OpenAI: GPT chat, gpt-image generation/edits, Sora video, TTS & Whisper.

Docs: https://developers.openai.com/api - key: https://platform.openai.com/api-keys
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional

from ..core import http
from ..core.http import ProviderError
from .base import CancelFn, ProgressFn
from .openai_compat import OpenAICompatChat


class OpenAI(OpenAICompatChat):
    name = "openai"
    label = "OpenAI"

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.key()}"}

    # --------------------------------------------------------------- images
    def generate_image(self, model: str, prompt: str, params: Optional[dict] = None,
                       refs: Optional[list] = None) -> list[str]:
        params = params or {}
        if refs:
            return self.edit_image(model, prompt, refs[0], None, params)
        body = {"model": model, "prompt": prompt, "n": int(params.get("n", 1))}
        if params.get("size") and params["size"] != "auto":
            body["size"] = params["size"]
        if params.get("quality") and params["quality"] != "auto":
            body["quality"] = params["quality"]
        data = http.request_json("POST", self.base_url() + "/v1/images/generations",
                                 headers={**self._auth(), "Content-Type": "application/json"},
                                 json_body=body, timeout=300)
        return self._collect_images(data, "image")

    def edit_image(self, model: str, prompt: str, image, mask=None,
                   params: Optional[dict] = None) -> list[str]:
        params = params or {}
        files = {"image": (Path(image).name, Path(image).read_bytes(), http.guess_mime(image))}
        if mask is not None:
            files["mask"] = (Path(mask).name, Path(mask).read_bytes(), "image/png")
        data_fields = {"model": model, "prompt": prompt, "n": str(int(params.get("n", 1)))}
        if params.get("size") and params["size"] != "auto":
            data_fields["size"] = params["size"]
        data = http.request_json("POST", self.base_url() + "/v1/images/edits",
                                 headers=self._auth(), data=data_fields, files=files, timeout=300)
        return self._collect_images(data, "edit")

    def _collect_images(self, data: dict, stem: str) -> list[str]:
        outs = []
        for item in data.get("data", []):
            if item.get("b64_json"):
                outs.append(self._save_bytes(base64.b64decode(item["b64_json"]), stem, ".png"))
            elif item.get("url"):
                out = self._out(stem, ".png")
                http.download(item["url"], out)
                outs.append(str(out))
        if not outs:
            raise ProviderError(f"OpenAI returned no image: {str(data)[:300]}")
        return outs

    # ---------------------------------------------------------------- video
    def generate_video(self, model: str, prompt: str, params: Optional[dict] = None,
                       image=None, progress: Optional[ProgressFn] = None,
                       should_cancel: Optional[CancelFn] = None) -> str:
        params = params or {}
        headers = self._auth()
        fields = {"model": model, "prompt": prompt,
                  "seconds": str(params.get("seconds", params.get("duration", 4))),
                  "size": str(params.get("size", "1280x720"))}
        if image is not None:
            files = {"input_reference": (Path(image).name, Path(image).read_bytes(),
                                         http.guess_mime(image))}
            video = http.request_json("POST", self.base_url() + "/v1/videos",
                                      headers=headers, data=fields, files=files, timeout=120)
        else:
            video = http.request_json("POST", self.base_url() + "/v1/videos",
                                      headers={**headers, "Content-Type": "application/json"},
                                      json_body={**fields, "seconds": fields["seconds"]},
                                      timeout=120)
        vid = video.get("id")
        if not vid:
            raise ProviderError(f"Sora did not return a video id: {str(video)[:300]}")

        def fetch():
            st = http.request_json("GET", f"{self.base_url()}/v1/videos/{vid}", headers=headers)
            status = st.get("status")
            if status in ("completed", "succeeded"):
                return st
            if status in ("failed", "cancelled", "expired"):
                err = (st.get("error") or {})
                raise ProviderError(f"Sora {status}: {err.get('message', '')}")
            if progress and st.get("progress") is not None:
                try:
                    progress(int(st["progress"]), f"Sora {status}")
                except Exception:
                    pass
            return None

        self._poll(fetch, interval=6, timeout=1800, progress=progress,
                   should_cancel=should_cancel, label="Sora rendering")
        out = self._out("sora", ".mp4")
        data = http.request_bytes("GET", f"{self.base_url()}/v1/videos/{vid}/content",
                                  headers=headers, timeout=600)
        out.write_bytes(data)
        return str(out)

    # ---------------------------------------------------------------- audio
    def tts(self, model: str, text: str, voice: str = "", params: Optional[dict] = None) -> str:
        body = {"model": model, "input": text, "voice": voice or "alloy"}
        data = http.request_bytes("POST", self.base_url() + "/v1/audio/speech",
                                  headers={**self._auth(), "Content-Type": "application/json"},
                                  json_body=body, timeout=300)
        return self._save_bytes(data, "tts", ".mp3")

    def transcribe(self, model: str, audio) -> str:
        files = {"file": (Path(audio).name, Path(audio).read_bytes(), http.guess_mime(audio))}
        data = http.request_json("POST", self.base_url() + "/v1/audio/transcriptions",
                                 headers=self._auth(), data={"model": model}, files=files,
                                 timeout=600)
        return data.get("text", "")
