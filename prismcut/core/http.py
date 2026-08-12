"""Thin HTTP layer shared by every provider adapter.

Everything goes through the functions in this module so tests can monkeypatch
one place and adapters stay Qt-free.
"""
from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

import requests

DEFAULT_TIMEOUT = 180


class ProviderError(RuntimeError):
    """Raised for any provider/API failure with a human-readable message."""

    def __init__(self, message: str, status: Optional[int] = None, detail: Any = None):
        super().__init__(message)
        self.status = status
        self.detail = detail


class NotSupported(ProviderError):
    """The selected provider/model does not support the requested operation."""


def _short(detail: Any, limit: int = 400) -> str:
    try:
        s = json.dumps(detail) if not isinstance(detail, str) else detail
    except Exception:
        s = str(detail)
    return s[:limit]


def _raise_for(resp: requests.Response) -> None:
    if resp.status_code < 400:
        return
    try:
        detail = resp.json()
    except Exception:
        detail = (resp.text or "")[:500]
    msg = None
    if isinstance(detail, dict):
        err = detail.get("error") or detail.get("base_resp") or detail
        if isinstance(err, dict):
            msg = err.get("message") or err.get("status_msg") or err.get("msg")
        elif isinstance(err, str):
            msg = err
    raise ProviderError(f"HTTP {resp.status_code}: {msg or _short(detail)}", resp.status_code, detail)


def request_json(method: str, url: str, *, headers=None, json_body=None, data=None,
                 files=None, params=None, timeout: int = DEFAULT_TIMEOUT) -> dict:
    resp = requests.request(method, url, headers=headers, json=json_body, data=data,
                            files=files, params=params, timeout=timeout)
    _raise_for(resp)
    if not resp.content:
        return {}
    try:
        return resp.json()
    except ValueError:
        raise ProviderError(f"Expected JSON from {url}, got: {resp.text[:200]}")


def request_bytes(method: str, url: str, *, headers=None, json_body=None, data=None,
                  files=None, params=None, timeout: int = DEFAULT_TIMEOUT) -> bytes:
    resp = requests.request(method, url, headers=headers, json=json_body, data=data,
                            files=files, params=params, timeout=timeout)
    _raise_for(resp)
    return resp.content


def stream_sse(method: str, url: str, *, headers=None, json_body=None, params=None,
               timeout: int = DEFAULT_TIMEOUT) -> Iterator[dict]:
    """Yield parsed JSON objects from a server-sent-events response.

    Works for OpenAI-compatible, Anthropic and Gemini (?alt=sse) streams.
    """
    resp = requests.request(method, url, headers=headers, json=json_body,
                            params=params, timeout=timeout, stream=True)
    _raise_for(resp)
    try:
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw:
                continue
            if raw.startswith("data:"):
                payload = raw[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    yield json.loads(payload)
                except ValueError:
                    continue
    finally:
        resp.close()


def download(url: str, dest: Path, *, headers=None, params=None, timeout: int = 600,
            on_chunk: Optional[Callable[[int, int], None]] = None) -> Path:
    """on_chunk(bytes_so_far, total_bytes) - total_bytes is 0 if the server
    didn't send Content-Length (callers should treat that as unknown/
    indeterminate, same convention Job.progress() already uses for -1)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, headers=headers, params=params, timeout=timeout, stream=True) as resp:
        _raise_for(resp)
        total = int(resp.headers.get("Content-Length") or 0)
        written = 0
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                if chunk:
                    fh.write(chunk)
                    written += len(chunk)
                    if on_chunk:
                        on_chunk(written, total)
    return dest


# ---------------------------------------------------------------- file helpers

def guess_mime(path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    if mime:
        return mime
    ext = str(path).lower().rsplit(".", 1)[-1]
    return {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp",
        "gif": "image/gif", "mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm",
        "mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4", "ogg": "audio/ogg",
        "flac": "audio/flac", "aac": "audio/aac",
    }.get(ext, "application/octet-stream")


def file_b64(path) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def data_uri(path) -> str:
    return f"data:{guess_mime(path)};base64,{file_b64(path)}"


def find_media_urls(obj: Any, exts=(".mp4", ".webm", ".mov", ".png", ".jpg", ".jpeg",
                                    ".webp", ".gif", ".mp3", ".wav", ".m4a", ".ogg", ".flac")) -> list[str]:
    """Recursively collect media URLs from an arbitrary API response (fal/replicate style)."""
    found: list[str] = []

    def walk(o: Any):
        if isinstance(o, str):
            if o.startswith("http") and (any(o.split("?")[0].lower().endswith(e) for e in exts)
                                         or "output" in o or "/files/" in o):
                found.append(o)
        elif isinstance(o, dict):
            # common shape: {"url": "...", "content_type": "video/mp4"}
            u = o.get("url")
            if isinstance(u, str) and u.startswith("http"):
                found.append(u)
            for v in o.values():
                if v is not u:
                    walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)

    walk(obj)
    # de-dup preserving order
    seen, out = set(), []
    for u in found:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out
