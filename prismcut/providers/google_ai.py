"""Google AI (Gemini API): chat, Nano Banana image gen/edit, Veo video, TTS,
audio/video understanding & transcription.

Docs: https://ai.google.dev/gemini-api/docs
Auth: x-goog-api-key header (easy key drop-in from https://aistudio.google.com/apikey).
"""
from __future__ import annotations

import base64
from typing import Callable, Optional

from ..core import http, media
from ..core.http import ProviderError
from .base import Adapter, CancelFn, ChatMessage, DeltaFn, ProgressFn
from .tools import MAX_TOOL_ITERATIONS, Tool, ToolCall, ToolResult


class GoogleAI(Adapter):
    name = "google"
    label = "Google AI (Gemini)"

    def _headers(self) -> dict:
        return {"x-goog-api-key": self.key(), "Content-Type": "application/json"}

    def _url(self, tail: str) -> str:
        return f"{self.base_url()}/v1beta/{tail.lstrip('/')}"

    @staticmethod
    def _inline(path) -> dict:
        return {"inline_data": {"mime_type": http.guess_mime(path), "data": http.file_b64(path)}}

    def _contents(self, messages: list[ChatMessage]) -> list[dict]:
        contents = []
        for m in messages:
            parts: list[dict] = []
            if m.text:
                parts.append({"text": m.text})
            for a in m.attachments or []:
                parts.append(self._inline(a))
            if not parts:
                parts = [{"text": ""}]
            contents.append({"role": "user" if m.role == "user" else "model", "parts": parts})
        return contents

    # ---------------------------------------------------------------- chat
    def chat(self, model: str, messages: list[ChatMessage], system: str = "",
             temperature: float = 0.7, on_delta: Optional[DeltaFn] = None,
             should_cancel: Optional[CancelFn] = None,
             tools: Optional[list[Tool]] = None,
             on_tool_call: Optional[Callable[[ToolCall], ToolResult]] = None) -> str:
        body: dict = {"contents": self._contents(messages),
                      "generationConfig": {"temperature": temperature}}
        if system:
            body["system_instruction"] = {"parts": [{"text": system}]}

        if tools and on_tool_call:
            body["tools"] = [{"functionDeclarations": [
                {"name": t.name, "description": t.description, "parameters": t.parameters}
                for t in tools]}]
            for _ in range(MAX_TOOL_ITERATIONS):
                if should_cancel and should_cancel():
                    return ""
                data = http.request_json("POST", self._url(f"models/{model}:generateContent"),
                                         headers=self._headers(), json_body=body)
                cands = data.get("candidates", [])
                parts = (cands[0].get("content") or {}).get("parts", []) if cands else []
                calls = [p["functionCall"] for p in parts if p.get("functionCall")]
                if not calls:
                    return self._first_text(data)
                body["contents"].append({"role": "model", "parts": parts})
                response_parts = []
                for fc in calls:
                    call_id = fc.get("id", "")
                    result = on_tool_call(ToolCall(call_id, fc["name"], fc.get("args") or {}))
                    response_parts.append({"functionResponse": {
                        "name": fc["name"], "id": call_id, "response": {"result": result.content}}})
                body["contents"].append({"role": "user", "parts": response_parts})
            return ""  # exhausted MAX_TOOL_ITERATIONS without a final answer

        if on_delta is not None:
            url = self._url(f"models/{model}:streamGenerateContent")
            full = []
            for ev in http.stream_sse("POST", url, headers=self._headers(),
                                      json_body=body, params={"alt": "sse"}):
                if should_cancel and should_cancel():
                    break
                for cand in ev.get("candidates", []):
                    for part in (cand.get("content") or {}).get("parts", []):
                        t = part.get("text")
                        if t:
                            full.append(t)
                            on_delta(t)
            return "".join(full)
        data = http.request_json("POST", self._url(f"models/{model}:generateContent"),
                                 headers=self._headers(), json_body=body)
        return self._first_text(data)

    @staticmethod
    def _first_text(data: dict) -> str:
        for cand in data.get("candidates", []):
            for part in (cand.get("content") or {}).get("parts", []):
                if part.get("text"):
                    return part["text"]
        raise ProviderError(f"No text in Gemini response: {str(data)[:300]}")

    # --------------------------------------------------------------- images
    def _image_generate_content(self, model: str, parts: list[dict], params: dict) -> list[str]:
        gen_cfg: dict = {"responseModalities": ["IMAGE"]}
        img_cfg = {}
        if params.get("aspect_ratio"):
            img_cfg["aspectRatio"] = params["aspect_ratio"]
        if params.get("image_size"):
            img_cfg["imageSize"] = params["image_size"]
        if img_cfg:
            gen_cfg["imageConfig"] = img_cfg
        body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": gen_cfg}
        data = http.request_json("POST", self._url(f"models/{model}:generateContent"),
                                 headers=self._headers(), json_body=body, timeout=300)
        outs = []
        for cand in data.get("candidates", []):
            for part in (cand.get("content") or {}).get("parts", []):
                blob = part.get("inline_data") or part.get("inlineData")
                if blob and blob.get("data"):
                    outs.append(self._save_bytes(base64.b64decode(blob["data"]), "nano", ".png"))
        if not outs:
            raise ProviderError("Gemini returned no image (possibly safety-filtered). "
                                f"Response: {str(data)[:300]}")
        return outs

    def generate_image(self, model: str, prompt: str, params: Optional[dict] = None,
                       refs: Optional[list] = None) -> list[str]:
        params = params or {}
        if model.startswith("imagen"):
            body = {"instances": [{"prompt": prompt}],
                    "parameters": {"sampleCount": int(params.get("n", 1))}}
            if params.get("aspect_ratio"):
                body["parameters"]["aspectRatio"] = params["aspect_ratio"]
            data = http.request_json("POST", self._url(f"models/{model}:predict"),
                                     headers=self._headers(), json_body=body, timeout=300)
            outs = [self._save_bytes(base64.b64decode(p["bytesBase64Encoded"]), "imagen", ".png")
                    for p in data.get("predictions", []) if p.get("bytesBase64Encoded")]
            if not outs:
                raise ProviderError(f"Imagen returned no image: {str(data)[:300]}")
            return outs
        parts: list[dict] = [{"text": prompt}]
        for r in (refs or [])[:8]:
            parts.append(self._inline(r))
        return self._image_generate_content(model, parts, params)

    def edit_image(self, model: str, prompt: str, image, mask=None,
                   params: Optional[dict] = None) -> list[str]:
        parts: list[dict] = []
        text = prompt
        if mask is not None:
            text += ("\n\nThe second image is a mask: apply the edit ONLY inside the white "
                     "region of the mask; keep everything else pixel-identical.")
        parts.append({"text": text})
        parts.append(self._inline(image))
        if mask is not None:
            parts.append(self._inline(mask))
        return self._image_generate_content(model, parts, params or {})

    # ---------------------------------------------------------------- video
    def generate_video(self, model: str, prompt: str, params: Optional[dict] = None,
                       image=None, progress: Optional[ProgressFn] = None,
                       should_cancel: Optional[CancelFn] = None) -> str:
        params = params or {}
        instance: dict = {"prompt": prompt}
        if image is not None:
            instance["image"] = {"bytesBase64Encoded": http.file_b64(image),
                                 "mimeType": http.guess_mime(image)}
        parameters = {}
        for src, dst in (("aspect_ratio", "aspectRatio"), ("resolution", "resolution"),
                         ("negative_prompt", "negativePrompt"), ("duration", "durationSeconds")):
            if params.get(src) not in (None, "", 0, -1):
                parameters[dst] = params[src]
        if params.get("seed") not in (None, -1, ""):
            parameters["seed"] = int(params["seed"])
        body = {"instances": [instance], "parameters": parameters}
        op = http.request_json("POST", self._url(f"models/{model}:predictLongRunning"),
                               headers=self._headers(), json_body=body, timeout=120)
        op_name = op.get("name")
        if not op_name:
            raise ProviderError(f"Veo did not return an operation: {str(op)[:300]}")

        def fetch():
            st = http.request_json("GET", self._url(op_name), headers=self._headers())
            if st.get("error"):
                raise ProviderError(f"Veo error: {st['error'].get('message', st['error'])}")
            if st.get("done"):
                return st
            return None

        st = self._poll(fetch, interval=8, timeout=1200, progress=progress,
                        should_cancel=should_cancel, label="Veo rendering")
        resp = st.get("response", {})
        gvr = resp.get("generateVideoResponse", resp)
        samples = gvr.get("generatedSamples") or gvr.get("videos") or []
        uri = None
        for s in samples:
            uri = (s.get("video") or {}).get("uri") or s.get("uri") or s.get("bytesBase64Encoded")
            if uri:
                break
        if not uri:
            urls = http.find_media_urls(resp)
            uri = urls[0] if urls else None
        if not uri:
            raise ProviderError(f"Veo finished but no video found: {str(resp)[:400]}")
        out = self._out("veo", ".mp4")
        if uri.startswith("http"):
            http.download(uri, out, headers={"x-goog-api-key": self.key()})
        else:
            out.write_bytes(base64.b64decode(uri))
        return str(out)

    # ----------------------------------------------------------------- audio
    def tts(self, model: str, text: str, voice: str = "", params: Optional[dict] = None) -> str:
        body = {
            "contents": [{"role": "user", "parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": voice or "Kore"}}},
            },
        }
        data = http.request_json("POST", self._url(f"models/{model}:generateContent"),
                                 headers=self._headers(), json_body=body, timeout=300)
        for cand in data.get("candidates", []):
            for part in (cand.get("content") or {}).get("parts", []):
                blob = part.get("inline_data") or part.get("inlineData")
                if blob and blob.get("data"):
                    pcm = base64.b64decode(blob["data"])
                    out = self._out("tts", ".wav")
                    media.pcm_to_wav(pcm, out, rate=24000)
                    return str(out)
        raise ProviderError(f"Gemini TTS returned no audio: {str(data)[:300]}")

    def transcribe(self, model: str, audio) -> str:
        msg = ChatMessage("user", "Transcribe this audio verbatim. Output only the transcript.",
                          [audio])
        return self.chat(model, [msg])

    def test_key(self) -> tuple[bool, str]:
        try:
            http.request_json("GET", self._url("models"), headers=self._headers(),
                              params={"pageSize": 1}, timeout=20)
            return True, "Key OK"
        except ProviderError as e:
            return False, str(e)
