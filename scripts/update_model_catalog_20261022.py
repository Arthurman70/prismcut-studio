"""Reproducible, additive catalog refresh from the provider docs listed below."""
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / 'prismcut/assets/models.json'
data = json.loads(path.read_text(encoding='utf-8'))
audit = []


def choice(name, values, default=None, label=None):
    return dict(name=name, label=label or name.replace('_', ' ').title(), type='choice',
                choices=values, default=values[0] if default is None else default)


def integer(name, lo, hi, default):
    return dict(name=name, label=name.replace('_', ' ').title(), type='int', min=lo, max=hi, default=default)


def boolean(name, default=True):
    return dict(name=name, label=name.replace('_', ' ').title(), type='bool', default=default)


def text(name):
    return dict(name=name, label=name.replace('_', ' ').title(), type='text', default='')


def add(provider, mid, label, caps, source, params=None, notes='', **extra):
    model = dict(id=mid, provider=provider, label=label, caps=caps, params=params or [], notes=notes, **extra)
    existing = next((i for i, m in enumerate(data['models']) if (m['provider'], m['id']) == (provider, mid)), None)
    if existing is None:
        data['models'].append(model)
    else:
        data['models'][existing] = model
    audit.append(dict(provider=provider, id=mid, source=source, verified='2026-10-22'))


data['providers']['meta'] = dict(label='Meta (Muse)', key_env='MODEL_API_KEY',
    key_url='https://dev.meta.ai/', base_url='https://api.meta.ai',
    doc_url='https://dev.meta.ai/docs', notes='Muse Standard mode: chat, images and transcription. '
    'Use your Meta Model API key. Muse Code subscriptions do not replace API billing.')
for version in ('1.3', '1.2', '1.1'):
    add('meta', f'muse-spark-{version}', f'Muse Spark {version}', ['chat'],
        'https://dev.meta.ai/docs/models', notes='Standard mode. Text/image/PDF, MP4 and WAV/MP3 chat inputs. '
        + ('Audio understanding is limited in 1.3; use 1.2 for audio.' if version == '1.3' else ''))
add('meta', 'muse-wonder-1.0', 'Muse Wonder 1.0 (preview)', ['chat'],
    'https://dev.meta.ai/docs/models', notes='Standard API preview. Not the Muse Code-only Wonder Full model.')
add('meta', 'muse-image-1.0', 'Muse Image 1.0', ['image_generate', 'image_edit'],
    'https://dev.meta.ai/docs/api-reference/images/schemas',
    params=[choice('size', ['1024x1024', '1536x1024', '1024x1536']),
            choice('reasoning_strength', ['high', 'low']), integer('n', 1, 10, 1),
            choice('output_format', ['png', 'webp', 'jpeg'])],
    notes='Generate, edit and compose references. Size controls shape, not exact pixels. No pixel masks.')
add('meta', 'muse-voice-transcribe-1.0', 'Muse Voice Transcribe 1.0', ['transcribe', 'transcribe_segments'],
    'https://dev.meta.ai/docs/speech-to-text', notes='File transcription and turn-level captions; up to 10 min. '
    'ffmpeg converts supported media to mono PCM WAV. No speech synthesis or word timestamps.')

for mid, label in [('gpt-6-astra', 'GPT-6 Astra'), ('gpt-6.1-sol', 'GPT-6.1 Sol'), ('gpt-6-luna', 'GPT-6 Luna')]:
    add('openai', mid, label, ['chat'], 'https://developers.openai.com/api/docs/guides/latest-model',
        notes='Responses API; medium reasoning; text, image and PDF input. Sampling temperature is not used.')
for variant in ('sunburst', 'flare'):
    add('openai', 'gpt-image-2.5-' + variant, 'GPT Image 2.5 ' + variant.title(),
        ['image_generate', 'image_edit'], 'https://developers.openai.com/api/docs/guides/image-generation',
        params=[choice('size', ['auto', '1024x1024', '1536x1024', '1024x1536']),
                choice('quality', ['auto', 'low', 'medium', 'high', 'xhigh', 'max']), integer('n', 1, 4, 1)])

for mid, label in [('claude-opus-5-5', 'Claude Opus 5.5'), ('claude-sonnet-5-5', 'Claude Sonnet 5.5'),
                   ('claude-haiku-5-5', 'Claude Haiku 5.5'), ('claude-fable-5-1', 'Claude Fable 5.1')]:
    add('anthropic', mid, label, ['chat'], 'https://platform.claude.com/docs/en/models/overview',
        notes='Adaptive thinking; temperature omitted. Existing model choices remain available.')
for mid, label in [('gemini-4-argon', 'Gemini 4 Argon'), ('gemini-3.8-flash', 'Gemini 3.8 Flash')]:
    add('google', mid, label, ['chat', 'transcribe'], 'https://ai.google.dev/gemini-api/docs/models',
        notes='Multimodal chat and prompted transcription. Not the dedicated live speech API.')
template = next(m for m in data['models'] if m['id'] == 'gemini-3.1-flash-image')
add('google', 'gemini-nano-banana-2.1', 'Nano Banana 2.1', ['image_generate', 'image_edit'],
    'https://ai.google.dev/gemini-api/docs/models/gemini-nano-banana-2.1',
    params=copy.deepcopy(template['params']), strict_ip_policy=True)
for mid, label in [('gemini-3.8-flash-tts', 'Gemini 3.8 Flash TTS'),
                   ('gemini-3.8-flash-lite-tts', 'Gemini 3.8 Flash-Lite TTS')]:
    add('google', mid, label, ['tts'], 'https://ai.google.dev/gemini-api/docs/models',
        notes='Prebuilt voice synthesis. WAV output handled automatically.')
for mid in ('MiniMax-M3.1-Flash', 'MiniMax-M3'):
    add('minimax', mid, mid, ['chat'], 'https://platform.minimax.io/docs/api-reference/text-openai-api',
        notes='OpenAI-compatible chat endpoint; reasoning preserved during tool calls.')
for mid in ('speech-2.8-hd', 'speech-2.8-turbo'):
    add('minimax', mid, 'MiniMax ' + mid, ['tts'], 'https://platform.minimax.io/docs/guides/models-intro')
add('xai', 'grok-4.7', 'Grok 4.7', ['chat'], 'https://docs.x.ai/developers/grok-4-7')
add('xai', 'grok-imagine-image-2.0', 'Grok Imagine Image 2.0', ['image_generate', 'image_edit'],
    'https://docs.x.ai/developers/migration/imagine-image-quality-nov-2',
    params=[choice('quality', ['low', 'medium', 'auto']), integer('n', 1, 4, 1),
            choice('aspect_ratio', ['1:1', '16:9', '9:16', '4:3', '3:4', '21:9', '5:2'])],
    notes='Prompt edits take one reference in PrismCut. No pixel mask. Low quality selected explicitly.')

for direction in ('text-to-video', 'image-to-video'):
    is_image = direction == 'image-to-video'
    caps = ['video_generate'] + (['image_to_video'] if is_image else [])
    params = [choice('duration', [str(n) for n in range(3, 16)], '5'), boolean('generate_audio')]
    params += [text('start_image_url'), text('end_image_url')] if is_image else [choice('aspect_ratio', ['16:9', '9:16', '1:1'])]
    mid = 'fal-ai/kling-video/v3/pro/' + direction
    add('fal', mid, 'Kling 3.0 Pro (' + direction + ')', caps, 'https://fal.ai/models/' + mid + '/api', params=params)
    mid = 'bytedance/seedance-2.5/' + direction
    params = [choice('duration', [str(n) for n in range(4, 31)], '5'),
              choice('resolution', ['480p', '720p', '1080p'], '720p'), boolean('generate_audio')]
    params += [text('image_url'), text('end_image_url')] if is_image else [choice('aspect_ratio', ['16:9', '9:16', '1:1', '21:9', '4:3', '3:4'])]
    add('fal', mid, 'Seedance 2.5 (' + direction + ')', caps, 'https://fal.ai/models/' + mid + '/api', params=params)
    mid = 'alibaba/wan-3.0/' + direction
    params = [integer('duration', 5, 30, 5), choice('resolution', ['480p', '720p', '1080p'], '720p'),
              choice('aspect_ratio', ['adaptive', '16:9', '9:16', '4:3', '1:1', '3:4']), boolean('audio')]
    if is_image:
        params += [text('start_image_url'), text('end_image_url')]
    add('fal', mid, 'Wan 3.0 (' + direction + ')', caps, 'https://fal.ai/models/' + mid + '/api', params=params)
    for speed in ('pro', 'fast'):
        mid = 'lightricks/ltx-2.5/' + direction + '/' + speed
        params = [choice('duration', ['6', '8', '10'], '6'),
                  choice('resolution', ['720p', '1080p'] if speed == 'pro' else ['720p', '1080p', '1440p', '2160p'], '1080p'),
                  choice('aspect_ratio', ['auto', '16:9', '9:16'] if is_image else ['16:9', '9:16']),
                  boolean('generate_audio')]
        if is_image:
            params += [text('image_url'), text('end_image_url')]
        add('fal', mid, 'LTX 2.5 ' + speed.title() + ' (' + direction + ')', caps,
            'https://fal.ai/models/' + mid + '/api', params=params,
            notes='Conservative 6/8/10 second presets valid across resolutions. Native audio.')

data['version'] = 4
data['updated'] = '2026-10-22'
path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
(ROOT / 'docs/model-update-20261022.json').write_text(json.dumps(dict(verified='2026-10-22', models=audit), indent=2) + '\n', encoding='utf-8')
print(f'{len(audit)} verified additions; {len(data["models"])} models total')
