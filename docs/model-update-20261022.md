# PrismCut Studio 1.9.0 — model update

The October 22, 2026 refresh adds 36 entries to the existing 62-model catalog.
The total is 98 bundled model entries across 14 providers plus Custom.
Existing models, saved defaults, keys and custom overrides are preserved.

## Added models

| Provider | Added models | Workflows |
|---|---|---|
| Meta | Muse Spark 1.3, 1.2, 1.1; Muse Wonder 1.0 preview | Chat, media attachments and tool calls |
| Meta | Muse Image 1.0 | Generation, prompt edits and multiple reference images |
| Meta | Muse Voice Transcribe 1.0 | File transcription and turn-level captions |
| OpenAI | GPT-6 Astra, GPT-6.1 Sol, GPT-6 Luna | Responses chat, streaming and tools |
| OpenAI | GPT Image 2.5 Sunburst, Flare | Generation and masked edits |
| Anthropic | Claude Opus 5.5, Sonnet 5.5, Haiku 5.5, Fable 5.1 | Chat and tools with adaptive thinking |
| Google | Gemini 4 Argon, Gemini 3.8 Flash | Multimodal chat and prompted transcription |
| Google | Nano Banana 2.1 | Image generation and reference edits |
| Google | Gemini 3.8 Flash TTS, Flash-Lite TTS | Speech synthesis |
| MiniMax | M3.1 Flash, M3 | Chat and tools |
| MiniMax | Speech 2.8 HD, Turbo | Speech synthesis |
| xAI | Grok 4.7; Grok Imagine Image 2.0 | Chat; image generation and prompt edits |
| fal.ai | Kling 3.0 Pro, Seedance 2.5, Wan 3.0 | Text-to-video and image-to-video presets for each |
| fal.ai | LTX 2.5 Pro and Fast | Text-to-video and image-to-video presets for each |

## Using Muse

1. Open **AI → API Keys** and enter a Meta Model API key in **Meta (Muse)**.
   Alternatively, set `MODEL_API_KEY` before starting the app.
2. Open **AI → Model Manager** and search `Muse` to see all six entries.
3. Choose the matching model in Chat, Generate, Photo Studio or Audio Lab.

Muse chat supports PNG/JPEG/GIF/WebP, MP4, WAV/MP3 and PDF inputs, subject to
provider limits. Spark 1.3 has limited audio understanding; use Spark 1.2 for
audio. Muse Image saves PNG, JPEG or WebP as selected. It accepts reference
images for whole-image edits but does not support PrismCut's pixel mask.
Muse Voice accepts up to ten minutes per request. When needed, ffmpeg converts
audio to mono 24 kHz 16-bit PCM WAV. Caption timing is per turn, not per word.
Long clips fail with instructions to split them, rather than being truncated.

## Compatibility work

- GPT-6 uses Responses and preserves reasoning items through tool loops.
  Failed or interrupted response streams are reported as errors.
- New Claude models use adaptive thinking without unsupported temperature.
- MiniMax M3 uses its OpenAI-compatible endpoint and retains reasoning during
  tool calls. Older MiniMax models keep the existing route.
- Gemini 3.8 TTS WAV output is saved directly. Older PCM output is still wrapped
  correctly; WAV files no longer receive a second WAV header.
- Grok image edits use JSON reference inputs. PrismCut currently accepts one
  Grok reference per edit and rejects pixel masks explicitly. JPEG output keeps
  the correct file extension when reused as a reference.
- Video presets use each endpoint's actual image field, duration type and
  audio option. Image-to-video requests require a starting image.
- The Model Manager can search IDs, names, providers, capabilities and notes.

## Availability and limits

Account eligibility and provider billing still apply. This update does not
change saved model defaults or automatically choose a more expensive model.
New entries without a verified price show no cost estimate; check the provider's
current rate before generating. This release does not invent pricing estimates.
Only Muse Standard-mode IDs are bundled. Contributor-mode models have different
data-use terms and are not selected automatically. Wonder Full is Muse Code-only,
and Kling 4 was not listed as an available API at review time. Self-hosted Muse
models may be connected through Custom using the deployment's actual API contract.
Real-time voice, segmentation and provider interfaces without an implemented
PrismCut workflow are outside this release.

Validation: 535 regression tests passed; after the final JPEG-format fix, all
45 focused adapter tests passed. Both updated dialogs were rendered and visually
checked. The packaged Windows executable booted with disposable settings, all
98 catalog entries and the new provider modules present. Build dependency
collection used a clean PATH to avoid unrelated PDF/image-tool DLLs.
Tests use mocked HTTP responses. They do not establish that every model is
enabled on a particular account; no paid generation requests were made.

## Primary sources

Each new ID has a source URL in [the machine-readable catalog audit](model-update-20261022.json).
The catalog builder is `scripts/update_model_catalog_20261022.py` and is additive.

- [Meta model catalog](https://dev.meta.ai/docs/models),
  [chat schemas](https://dev.meta.ai/docs/api-reference/chat-completions/schemas),
  [image schemas](https://dev.meta.ai/docs/api-reference/images/schemas)
- [OpenAI latest models](https://developers.openai.com/api/docs/guides/latest-model),
  [image generation](https://developers.openai.com/api/docs/guides/image-generation)
- [Claude models](https://platform.claude.com/docs/en/models/overview)
- [Google models](https://ai.google.dev/gemini-api/docs/models)
- [MiniMax compatible API](https://platform.minimax.io/docs/api-reference/text-openai-api)
- [Grok 4.7](https://docs.x.ai/developers/grok-4-7),
  [Imagine quality migration](https://docs.x.ai/developers/migration/imagine-image-quality-nov-2)
- [Kling 3 Pro](https://fal.ai/models/fal-ai/kling-video/v3/pro/text-to-video/api),
  [Seedance 2.5](https://fal.ai/models/bytedance/seedance-2.5/text-to-video/api),
  [Wan 3](https://fal.ai/models/alibaba/wan-3.0/text-to-video/api),
  [LTX 2.5](https://fal.ai/models/lightricks/ltx-2.5/text-to-video/pro/api)
