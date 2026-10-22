"""Offline API-contract regressions for the October model refresh. No paid calls."""
import base64
import copy
import json
import wave
from pathlib import Path

import pytest

from prismcut.core import http, media
from prismcut.core.http import ProviderError, NotSupported
from prismcut.core.registry import Registry
from prismcut.providers import make_adapter
from prismcut.providers.base import ChatMessage
from prismcut.providers.tools import Tool, ToolResult


class Keys:
    def get_key(self, provider, env=''):
        return 'test-key'

    def get(self, key, default=None):
        return default


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('PRISMCUT_DATA_DIR', str(tmp_path / 'app'))
    reg = Registry(user_path=tmp_path / 'models.json')
    def forbidden(*a, **kw):
        raise AssertionError('A test attempted a real HTTP request')
    monkeypatch.setattr(http.requests, 'request', forbidden)
    monkeypatch.setattr(http.requests, 'get', forbidden)
    return reg, lambda provider: make_adapter(provider, Keys(), reg)


def capture(monkeypatch, response):
    calls = []
    def send(method, url, **kw):
        calls.append(dict(method=method, url=url, **copy.deepcopy(kw)))
        return copy.deepcopy(response)
    monkeypatch.setattr(http, 'request_json', send)
    return calls


def wav(path, rate=24000, seconds=1, channels=1):
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b'\0\0' * rate * seconds * channels)
    return path


def test_meta_chat_media_and_v1_base(monkeypatch, setup, tmp_path):
    _, adapter = setup
    meta = adapter('meta')
    meta.keychain.get = lambda key, default=None: 'https://api.meta.ai/v1' if key.endswith('base_url') else default
    attachments = []
    for suffix in ('png', 'mp4', 'wav', 'pdf'):
        file = tmp_path / ('media.' + suffix)
        file.write_bytes(b'data')
        attachments.append(str(file))
    calls = capture(monkeypatch, {'choices': [{'message': {'content': 'Summary'}}]})
    assert meta.chat('muse-spark-1.2', [ChatMessage('user', 'Describe', attachments)]) == 'Summary'
    assert calls[0]['url'] == 'https://api.meta.ai/v1/chat/completions'
    parts = calls[0]['json_body']['messages'][0]['content']
    assert [p['type'] for p in parts] == ['text', 'image_url', 'video_url', 'input_audio', 'file']
    assert parts[3]['input_audio']['format'] == 'wav'
    bad = tmp_path / 'unsupported.flac'
    bad.write_bytes(b'data')
    with pytest.raises(NotSupported):
        meta.chat('muse-spark-1.3', [ChatMessage('user', 'Describe', [bad])])
    assert len(calls) == 1


def test_meta_images_keep_all_refs_and_real_format(monkeypatch, setup, tmp_path):
    _, adapter = setup
    images = [tmp_path / 'a.png', tmp_path / 'b.png']
    for img in images:
        img.write_bytes(b'reference')
    calls = capture(monkeypatch, {'data': [{'b64_json': base64.b64encode(b'webp-result').decode()}]})
    out = adapter('meta').generate_image('muse-image-1.0', 'Compose',
        {'output_format': 'webp', 'reasoning_strength': 'low', 'size': '1536x1024'}, refs=images)
    assert out[0].endswith('.webp') and Path(out[0]).read_bytes() == b'webp-result'
    body = calls[0]['json_body']
    assert calls[0]['url'].endswith('/v1/images/edits')
    assert len(body['images']) == 2 and body['output_format'] == 'webp'
    assert body['reasoning_strength'] == 'low' and 'quality' not in body
    with pytest.raises(NotSupported, match='mask'):
        adapter('meta').edit_image('muse-image-1.0', 'Paint', images[0], mask=images[1])
    assert len(calls) == 1


def test_meta_download_never_forwards_key(monkeypatch, setup):
    _, adapter = setup
    capture(monkeypatch, {'data': [{'url': 'https://cdn.example/output.png'}]})
    downloads = []
    def download(url, dest, **kw):
        downloads.append(kw)
        Path(dest).write_bytes(b'png')
    monkeypatch.setattr(http, 'download', download)
    adapter('meta').generate_image('muse-image-1.0', 'Fox')
    assert downloads == [{}]


def test_meta_empty_image_response_and_bad_key(monkeypatch, setup):
    _, adapter = setup
    capture(monkeypatch, {'data': []})
    with pytest.raises(ProviderError, match='no images'):
        adapter('meta').generate_image('muse-image-1.0', 'Fox')
    def fail(*a, **kw):
        raise ProviderError('Unavailable', 503)
    monkeypatch.setattr(http, 'request_json', fail)
    assert adapter('meta').test_key() == (False, 'Unavailable')


def test_muse_voice_timestamps_and_upload(monkeypatch, setup, tmp_path):
    _, adapter = setup
    audio = wav(tmp_path / 'speech.wav')
    calls = capture(monkeypatch, {'transcript': 'Hello', 'turns': [
        {'startMs': 120, 'endMs': 900, 'transcript': 'Hello'}]})
    segments = adapter('meta').transcribe_segments('muse-voice-transcribe-1.0', audio)
    assert (segments[0].start, segments[0].end, segments[0].text) == (.12, .9, 'Hello')
    request = json.loads(calls[0]['files']['request'][1])
    assert request == {'model': 'muse-voice-transcribe-1.0', 'mode': 'ENDPOINTING', 'audioEncoding': 'WAV'}
    assert calls[0]['files']['audio'][1] == audio.read_bytes()
    assert 'Content-Type' not in calls[0]['headers']
    assert adapter('meta').transcribe('muse-voice-transcribe-1.0', audio) == 'Hello'


def test_muse_voice_conversion_cleanup_and_limit(monkeypatch, setup, tmp_path):
    _, adapter = setup
    audio = tmp_path / 'speech.mp3'
    audio.write_bytes(b'mp3')
    monkeypatch.setattr(media, 'ffmpeg_path', lambda: 'ffmpeg')
    converted = []
    def convert(command, **kw):
        assert '-t' in command and command[command.index('-t') + 1] == '601'
        target = Path(command[-1])
        wav(target)
        converted.append(target)
        return type('Result', (), {'returncode': 0})()
    monkeypatch.setattr('prismcut.providers.meta.subprocess.run', convert)
    calls = capture(monkeypatch, {'transcript': 'Hi'})
    assert adapter('meta').transcribe('muse-voice-transcribe-1.0', audio) == 'Hi'
    assert not converted[0].exists()
    long_audio = wav(tmp_path / 'long.wav', rate=16000, seconds=601)
    with pytest.raises(ProviderError, match='10 minutes'):
        adapter('meta').transcribe('muse-voice-transcribe-1.0', long_audio)
    assert len(calls) == 1


def test_muse_voice_rejects_missing_timing(monkeypatch, setup, tmp_path):
    _, adapter = setup
    audio = wav(tmp_path / 'speech.wav')
    capture(monkeypatch, {'transcript': 'Hello', 'turns': []})
    with pytest.raises(ProviderError, match='timestamps'):
        adapter('meta').transcribe_segments('muse-voice-transcribe-1.0', audio)


@pytest.mark.parametrize('provider,model', [('meta','muse-spark-1.3'),('minimax','MiniMax-M3.1-Flash')])
def test_reasoning_preserved_in_chat_tool_loop(monkeypatch, setup, provider, model):
    _, adapter = setup
    calls = []
    def request(method, url, **kw):
        calls.append(copy.deepcopy(kw['json_body']))
        if len(calls) == 1:
            return {'choices': [{'message': {'role': 'assistant', 'content': None,
                'reasoning_content': 'opaque reasoning', 'tool_calls': [
                    {'id': 'c1', 'type': 'function', 'function': {'name': 'lookup', 'arguments': '{}'}}]}}]}
        return {'choices': [{'message': {'content': 'Done'}}]}
    monkeypatch.setattr(http, 'request_json', request)
    result = adapter(provider).chat(model, [ChatMessage('user', 'Look it up')],
        tools=[Tool('lookup','Lookup', {'type':'object','properties':{}})],
        on_tool_call=lambda c: ToolResult(c.id,'Found'))
    assert result == 'Done'
    assert calls[1]['messages'][1]['reasoning_content'] == 'opaque reasoning'


@pytest.mark.parametrize('model', ['gpt-6-astra', 'gpt-6.1-sol', 'gpt-6-luna'])
def test_gpt6_tool_loop_uses_responses(monkeypatch, setup, model):
    _, adapter = setup
    calls = []
    reasoning = {'type': 'reasoning', 'id': 'rs1', 'summary': [], 'encrypted_content': 'opaque'}
    def request(method, url, **kw):
        calls.append((url, copy.deepcopy(kw['json_body'])))
        if len(calls) == 1:
            return {'status': 'completed', 'output': [reasoning, {
                'type': 'function_call', 'id': 'fc1', 'call_id': 'call1', 'name': 'lookup', 'arguments': '{}'}]}
        return {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text','text': 'Done'}]}]}
    monkeypatch.setattr(http, 'request_json', request)
    assert adapter('openai').chat(model, [ChatMessage('user', 'Lookup')],
        tools=[Tool('lookup','Lookup', {'type':'object','properties':{}})],
        on_tool_call=lambda c: ToolResult(c.id, 'Found')) == 'Done'
    assert calls[0][0].endswith('/v1/responses')
    assert 'temperature' not in calls[0][1] and calls[0][1]['store'] is False
    assert reasoning in calls[1][1]['input']
    assert calls[1][1]['input'][-1] == {'type': 'function_call_output', 'call_id': 'call1', 'output': 'Found'}


def test_gpt6_stream_and_incomplete_error(monkeypatch, setup):
    _, adapter = setup
    events = [{'type':'response.output_text.delta','delta':'Hi'},
              {'type':'response.completed','response':{'status':'completed','output':[]}}]
    monkeypatch.setattr(http, 'stream_sse', lambda *a, **kw: iter(events))
    chunks = []
    assert adapter('openai').chat('gpt-6-astra',[ChatMessage('user','Hello')],on_delta=chunks.append) == 'Hi'
    assert chunks == ['Hi']
    events.pop()
    with pytest.raises(ProviderError, match='before completion'):
        adapter('openai').chat('gpt-6-astra',[ChatMessage('user','Hello')],on_delta=lambda _: None)


def test_legacy_chat_and_new_minimax_routes(monkeypatch, setup):
    _, adapter = setup
    calls = capture(monkeypatch, {'choices':[{'message':{'content':'Hi'}}]})
    adapter('openai').chat('gpt-5.6-sol', [ChatMessage('user','Hi')])
    assert calls[-1]['url'].endswith('/v1/chat/completions')
    assert calls[-1]['json_body']['temperature'] == .7
    adapter('minimax').chat('MiniMax-M3.1-Flash', [ChatMessage('user','Hi')])
    assert calls[-1]['url'] == 'https://api.minimax.io/v1/chat/completions'
    assert calls[-1]['json_body']['reasoning_effort'] == 'medium'
    assert 'temperature' not in calls[-1]['json_body']


@pytest.mark.parametrize('model', ['claude-opus-5-5','claude-fable-5-1','claude-sonnet-5-5','claude-haiku-5-5'])
def test_claude_adaptive_thinking(monkeypatch, setup, model):
    _, adapter = setup
    calls = capture(monkeypatch, {'content':[{'type':'text','text':'Hi'}]})
    assert adapter('anthropic').chat(model,[ChatMessage('user','Hi')]) == 'Hi'
    assert calls[0]['json_body']['thinking'] == {'type':'adaptive'}
    assert 'temperature' not in calls[0]['json_body']


@pytest.mark.parametrize('container', [True, False])
def test_gemini_tts_wav_and_legacy_pcm(monkeypatch, setup, tmp_path, container):
    _, adapter = setup
    raw = wav(tmp_path / 'wav.wav').read_bytes() if container else b'\x00\x00' * 48
    capture(monkeypatch, {'candidates':[{'content':{'parts':[{'inlineData':{'data':base64.b64encode(raw).decode()}}]}}]})
    out = adapter('google').tts('gemini-3.8-flash-tts' if container else 'gemini-3.1-flash-tts-preview','Hi')
    if container:
        assert Path(out).read_bytes() == raw
    with wave.open(out, 'rb') as audio:
        assert audio.getframerate() == 24000


def test_grok_prompt_edit_json(monkeypatch, setup, tmp_path):
    _, adapter = setup
    image = tmp_path / 'ref.png'
    image.write_bytes(b'ref')
    calls = capture(monkeypatch, {'data':[{'b64_json':base64.b64encode(b'png').decode()}]})
    adapter('xai').edit_image('grok-imagine-image-2.0','Paint', image, params={'quality':'low'})
    assert calls[0]['url'].endswith('/v1/images/edits')
    assert calls[0]['json_body']['image']['url'].startswith('data:image/png;')
    assert calls[0]['json_body']['quality'] == 'low' and 'files' not in calls[0]


def test_grok_jpeg_saved_with_correct_mime(monkeypatch, setup):
    _, adapter = setup
    raw = b'\xff\xd8\xff' + b'image'
    capture(monkeypatch, {'data':[{'b64_json':base64.b64encode(raw).decode()}]})
    output = adapter('xai').generate_image('grok-imagine-image-2.0', 'Mountain')[0]
    assert output.endswith('.jpg') and Path(output).read_bytes() == raw
    assert http.guess_mime(output) == 'image/jpeg'


@pytest.mark.parametrize('prefix,field', [('fal-ai/kling-video/v3/pro','start_image_url'),
    ('alibaba/wan-3.0','start_image_url'), ('bytedance/seedance-2.5','image_url'),
    ('lightricks/ltx-2.5','image_url')])
def test_fal_video_presets_and_required_images(monkeypatch, setup, tmp_path, prefix, field):
    registry, adapter = setup
    model = prefix + '/image-to-video' + ('/pro' if prefix.startswith('lightricks') else '')
    spec = registry.find('fal', model)
    image = tmp_path / 'ref.png'
    image.write_bytes(b'ref')
    fal = adapter('fal')
    captured = []
    def run(mid, payload, *args):
        captured.append(payload)
        return {'video':{'url':'https://cdn.example/video.mp4'}}
    monkeypatch.setattr(fal, '_run', run)
    monkeypatch.setattr(fal, '_download_all', lambda *args: ['output.mp4'])
    assert fal.generate_video(model,'Move',spec.default_params(),image=image) == 'output.mp4'
    assert captured[0][field].startswith('data:image/png;')
    if 'wan-3' in model:
        assert captured[0]['audio'] is True and isinstance(captured[0]['duration'], int)
    else:
        assert captured[0]['generate_audio'] is True and isinstance(captured[0]['duration'], str)
    with pytest.raises(ProviderError, match='starting image'):
        fal.generate_video(model, 'Move', spec.default_params())


def test_model_manager_search_and_meta_key_row(setup, tmp_path):
    from PySide6.QtWidgets import QApplication
    from prismcut.core.settings import Settings
    from prismcut.ui.dialogs.model_manager import ModelManagerDialog
    from prismcut.ui.dialogs.keys_dialog import KeysDialog
    app = QApplication.instance() or QApplication([])
    registry, _ = setup
    dialog = ModelManagerDialog(registry, Settings())
    dialog.search.setText('Muse')
    visible = [dialog.tree.topLevelItem(i) for i in range(dialog.tree.topLevelItemCount())
               if not dialog.tree.topLevelItem(i).isHidden()]
    assert len(visible) == 1 and visible[0].text(0) == 'Meta (Muse)'
    assert visible[0].childCount() == 6
    dialog.search.setText('no-such-model-123')
    assert all(dialog.tree.topLevelItem(i).isHidden() for i in range(dialog.tree.topLevelItemCount()))
    dialog.close()
    keys = KeysDialog(Keys(), registry, None)
    assert keys.edits['meta'].placeholderText() == 'MODEL_API_KEY'
    assert keys.base_edits['meta'].placeholderText() == 'https://api.meta.ai'
    keys.close()
