# Provider guide

Every adapter lives in `prismcut/providers/` and speaks the provider's native
API. Keys go in **AI ▸ API Keys…**; model IDs live in the registry
(**AI ▸ Model Manager…**) so they can be updated without code.

| Provider | Key page | Env var | Notes |
|---|---|---|---|
| Google AI (Gemini) | https://aistudio.google.com/apikey | `GEMINI_API_KEY` | One key = Gemini chat, **Nano Banana 2 / Pro** image gen+edit, **Veo 3.1** video, TTS, transcription. The project's namesake and the default for Nano Tools. |
| OpenAI | https://platform.openai.com/api-keys | `OPENAI_API_KEY` | GPT-5.6 chat, gpt-image-2 gen/edit (mask inpainting), **Sora 2 / Pro** video, TTS, Whisper. Sora requires an org with video access. |
| Anthropic | https://console.anthropic.com/settings/keys | `ANTHROPIC_API_KEY` | Claude chat (vision attachments supported). |
| Meta (Muse) | https://dev.meta.ai/ | `MODEL_API_KEY` | Muse Spark / Wonder chat, Muse Image generation and prompt edits, Muse Voice file transcription and turn-level captions. Standard API models; separate API access required. |
| xAI | https://console.x.ai | `XAI_API_KEY` | Grok chat, Grok Imagine image + **Grok Imagine Video 1.5** (`/v1/videos/generations`). |
| DeepSeek | https://platform.deepseek.com/api_keys | `DEEPSEEK_API_KEY` | deepseek-chat / deepseek-reasoner (text-only chat, great for prompt enhancement). |
| MiniMax | https://platform.minimax.io | `MINIMAX_API_KEY` | **MiniMax-H3 = "Hailuo 03" (the 'HD 3')**: 4–15 s, native 2K + stereo audio. Also Hailuo 02, Music 1.5, Speech-02-HD. Mainland base URL override: `https://api.minimaxi.com`. |
| Seedance (BytePlus Ark) | https://console.byteplus.com/ark | `ARK_API_KEY` | **Seedance 2.0** (`dreamina-seedance-2-0-260128`) + 1.0 Pro. Region base URL configurable in API Keys ▸ advanced. Also reachable via fal.ai / Replicate. |
| Stability AI | https://platform.stability.ai/account/keys | `STABILITY_API_KEY` | Max-control image gen (seed/CFG/style presets), true masked inpainting, background removal, 4× upscale. |
| Black Forest Labs | https://dashboard.bfl.ai | `BFL_API_KEY` | FLUX 2 Pro gen, FLUX Kontext Pro/Max in-context editing. |
| fal.ai | https://fal.ai/dashboard/keys | `FAL_KEY` | Aggregator: hundreds of models (Seedance, Kling, Hailuo, WAN, LTX, FLUX…). Add **any** fal model id via Model Manager — the adapter is generic. |
| Replicate | https://replicate.com/account/api-tokens | `REPLICATE_API_TOKEN` | Same idea: run any `owner/model`. |
| ElevenLabs | https://elevenlabs.io/app/settings/api-keys | `ELEVENLABS_API_KEY` | TTS (voice ids from your voice library), Eleven Music, sound effects. |
| Suno | via gateway, e.g. https://sunoapi.org | `SUNO_API_KEY` | ⚠️ Suno has no self-serve official API (mid-2026). The adapter speaks the common community-gateway protocol; set your gateway's base URL in API Keys ▸ advanced. Prefer ElevenLabs/MiniMax music for a fully supported path. |
| Custom | — | `CUSTOM_OPENAI_API_KEY` | Any OpenAI-compatible chat endpoint: OpenRouter, Groq, Together, Ollama (`http://localhost:11434`), LM Studio, vLLM, corporate gateways. |

## Name-mapping cheat sheet

The [October 2026 model update](docs/model-update-20261022.md) supplements the
existing models below. Search **AI ▸ Model Manager** to find the new entries.

- **Muse** → Meta Model API, not a Muse Code subscription. Add your Meta key in
  **AI ▸ API Keys ▸ Meta (Muse)**. The default origin is `https://api.meta.ai`;
  overrides with `/v1` also work. Spark handles chat and supported media;
  Muse Image handles generation and reference edits; Muse Voice handles
  transcription. Voice is not a speech-synthesis model.
- **Muse Glimmer / Muse Spark Open** → self-hosted or third-party deployments.
  Configure the actual server URL, served model ID and key under Custom if
  that deployment exposes OpenAI-compatible chat. They are not automatically
  routed to Meta's hosted API.
- **GPT-6 Astra / GPT-6.1 Sol / GPT-6 Luna** → Responses API, including native
  tool calls with reasoning preserved. Older OpenAI models keep their current
  transport. Medium reasoning is selected; temperature is omitted.
- **Seedance 2.5 / Wan 3.0 / LTX 2.5 / Kling 3.0** → the new presets use fal.ai
  and need a `FAL_KEY`. A BytePlus or MiniMax key does not authenticate fal.ai.

- **"Nano Banana"** → `gemini-3.1-flash-image` (Nano Banana 2); **Pro** → `gemini-3-pro-image`.
- **"MiniMax HD 3" / "Hailuo 3"** → model id `MiniMax-H3` on the MiniMax video API.
- **"Omni Flash"** → the fast multimodal tier: Gemini Flash family (`gemini-3.6-flash`) and OpenAI's omni-class models; both are registered.
- **Seedance** → BytePlus Ark id `dreamina-seedance-2-0-260128` (or via fal.ai id `fal-ai/bytedance/seedance/...`).

## When a provider ships a new model

Open **AI ▸ Model Manager ▸ Add model**, paste the model id from the
provider's docs, tick its capabilities, optionally add a params schema
(the JSON snippet format is shown in the dialog), save. It immediately shows
up in the chat/generate/nano selectors. Models must use an API contract the
provider adapter supports; a new transport cannot be enabled by an ID alone.

## How files reach the APIs

| Direction | Mechanism |
|---|---|
| Images/audio/video → Gemini | `inline_data` base64 parts (chat, edit, transcribe) |
| Images → OpenAI edits, audio → Whisper | multipart uploads |
| First-frame → MiniMax / fal / xAI / Seedance | data-URI or `image_url` |
| Results ← Veo / Sora / FLUX / fal / Replicate / MiniMax | polled operations, then signed-URL / bytes download into the app's `generated` folder and the Project Bin |
