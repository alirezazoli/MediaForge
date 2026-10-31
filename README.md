# MediaForge Studio

A Windows desktop workbench for AI video and photo production. One app that talks to the
generative video/image APIs, does the local editing with ffmpeg, and runs an agent that
takes a written brief all the way to an assembled cut.

## Run it

```bash
C:\Users\Alireza\PyCharmMiscProject\.venv\Scripts\python.exe -m mediaforge
```

or double-click `MediaForge.bat`. Check the environment first with:

```bash
C:\Users\Alireza\PyCharmMiscProject\.venv\Scripts\python.exe -m mediaforge --doctor
```

## Photos to video

The main thing. Open the app, it lands on **Photos to video**:

1. Drag photos onto the strip (or Add photos / Add folder).
2. Drag thumbnails to reorder — they play left to right.
3. Pick a shape, a pace, and a movement. Optionally add music.
4. Press **Create video**. Progress and preview appear on the same screen.

Each photo becomes a moving shot — slow zoom or drift — and the shots crossfade together,
open from black and close to black. Music is trimmed to length and faded at both ends.

Movement styles: **Auto (varied)** cycles zoom in → pan right → zoom out → pan left →
zoom in + pan up → pan down, so no two shots feel the same. Or pick one and hold it.

Photos of mixed aspect ratios are handled: fill the frame (crop), fit with blurred edges,
or fit with black bars.

**No API key, no account, no network.** It is ffmpeg on your own machine.

## Availability: what you can actually use

Every model shows its state before you click it:

- **FREE** (green) — runs on your machine with no key. Open-weight models through a local
  ComfyUI, plus the local engines.
- **usable** — a key is configured for a backend that can run it.
- **needs a key** (greyed, unselectable) — no configured backend. The row is disabled, and
  its tooltip names exactly which key would unlock it.

The Generate screen has a **Free only** switch that hides everything needing a key. With no
keys at all you still get 15 free models and every local tool.

## Which models are actually free?

Verified August 2026. This matters more than the marketing suggests — "available" and
"open" are different things.

| Model | Weights | Where |
|---|---|---|
| **MiniMax H3 (Hailuo 3)** | **OPEN** — released 2026-08-03, ~42.5 GB | HuggingFace, native ComfyUI |
| **Wan 2.2** | **OPEN** Apache 2.0 — the open flagship | [Wan-Video/Wan2.2](https://github.com/Wan-Video/Wan2.2) |
| **LTX-2 / LTX-Video** | **OPEN** — first open model with synced audio+video | [Lightricks/LTX-Video](https://github.com/Lightricks/LTX-Video) |
| **HunyuanVideo** | **OPEN** 13B, best-in-class motion | [Tencent-Hunyuan/HunyuanVideo](https://github.com/Tencent-Hunyuan/HunyuanVideo) |
| **Mochi 1** | **OPEN** Apache 2.0 | [genmoai/mochi](https://github.com/genmoai/mochi) |
| **CogVideoX** | **OPEN** | [THUDM/CogVideo](https://github.com/THUDM/CogVideo) |
| **SkyReels V2** | **OPEN**, human-centric, infinite length | [SkyworkAI/SkyReels-V2](https://github.com/SkyworkAI/SkyReels-V2) |
| **FramePack** | **OPEN**, long video on 6 GB VRAM | [lllyasviel/FramePack](https://github.com/lllyasviel/FramePack) |
| **FLUX.1 dev / schnell / Kontext** | **OPEN** (schnell is Apache 2.0) | [black-forest-labs/flux](https://github.com/black-forest-labs/flux) |
| **Qwen-Image** | **OPEN** Apache 2.0, best text-in-image | [QwenLM/Qwen-Image](https://github.com/QwenLM/Qwen-Image) |
| **SDXL / SD 3.5** | **OPEN**, biggest LoRA/ControlNet ecosystem | [Stability-AI](https://github.com/Stability-AI/generative-models) |
| **Lip Sync** | **OPEN** — LatentSync (ByteDance), MuseTalk, Wav2Lip | [bytedance/LatentSync](https://github.com/bytedance/LatentSync) |
| **Motion Control** | **OPEN** — Wan-VACE, MimicMotion, Champ, StableAnimator | [ali-vilab/VACE](https://github.com/ali-vilab/VACE) |
| Wan 2.7 | **partly** — some weights on ModelScope, unclear licence | ModelScope |
| Seedance 2.5 / 2.0 / Fast / Mini / 1.5 Pro / 1.0 | **closed** — API only | — |
| Kling 3.0 / 2.6 / 2.5 Turbo | **closed** | — |
| Hailuo 2.3 / 02 | **closed** (only H3 is open) | — |
| Veo 3.1 | **closed** | — |
| Grok Imagine / 1.5 | **closed** | — |
| Pixverse V5 | **closed** | — |
| Wan 2.5 / 2.6 | **closed** — API-first releases | — |
| Nano Banana | **closed** (FLUX Kontext is the open substitute) | — |

Anything on GitHub claiming to be "Seedance weights" or "Kling open source" is an API
wrapper or worse. ByteDance open-sources plenty of research code — Seedance is not part
of it.

**All open models above run locally and free through ComfyUI.** Model Hub → Local engines
→ install ComfyUI, then point Settings → ComfyUI URL at it.

## Free local (non-generative) AI engines

Installed on demand from Model Hub. No key, no cost, offline. The `ncnn-vulkan` ones are
self-contained `.exe` files that run on **any** Vulkan GPU — AMD and Intel included, no CUDA.

| Engine | Does | Licence |
|---|---|---|
| Real-ESRGAN | 2×/3×/4× upscale, photo + anime models | BSD-3 |
| Real-CUGAN | upscale + denoise, best on illustration | MIT |
| waifu2x | gentle denoise + upscale | MIT |
| RIFE | frame interpolation → 60/120 fps, slow motion | MIT |
| rembg (U2Net/BiRefNet) | background removal | MIT |
| faster-whisper | offline transcription with word timings, 99 languages | MIT |
| Demucs | vocals/drums/bass/other stem separation | MIT |
| Kokoro TTS | free local voiceover, near-paid quality | Apache-2.0 |
| Gyroflow | gyro-data stabilisation, beats any optical method | GPL-3 |
| CodeFormer / GFPGAN | face restoration | S-Lab / Apache |
| ProPainter | video object removal + inpainting | S-Lab |
| SAM 2 | click an object, track it through the clip | Apache-2.0 |
| RobustVideoMatting | real-time video matting, no green screen | GPL-3 |
| Auto-Editor | alternative silence-cut engine | Unlicense |
| ComfyUI | the local engine for every open model above | GPL-3 |

## What is in it

### Providers (all optional — the app works with whichever keys you have)

| Provider | Models | Capabilities |
|---|---|---|
| **Google** | Nano Banana (Gemini 2.5 Flash Image), Imagen 4, Veo 3.1 / Veo 3.1 Fast, Gemini 2.5 Pro/Flash | image gen, image edit, text→video, image→video, scripting, video understanding |
| **ByteDance Seedance** (Volcengine Ark / BytePlus) | Seedance 1.0 Pro, Lite t2v, Lite i2v, Seedream 4.0 | text→video, image→video (first/last frame), image gen |
| **Qwen-Omni / Wan** (Alibaba DashScope) | Qwen3-Omni Flash, Qwen3-Omni 30B, Wan 2.5 t2v/i2v | omni-modal in/out, speech synth, text→video |
| **fal.ai** | FLUX, Kontext, Seedance, Veo 3, Kling 2, Hailuo 02, Wan, LTX-Video, HunyuanVideo, Topaz, Real-ESRGAN, Whisper + anything you paste | everything |
| **Replicate** | FLUX, Nano Banana, Seedance, Wan 2.2, LTX-Video, Real-ESRGAN, Whisper + anything you paste | everything |
| **ComfyUI** | any API-format workflow, local and free | image/video gen, upscale |
| **Anthropic** | Claude Opus 5 / Sonnet 5 | scripts, shotlists, captions |
| **ElevenLabs** | Multilingual v2, Turbo v2.5, Scribe v1 | voiceover, transcription with word timings |
| **OpenAI-compatible** | gpt-image-1, whisper-1, gpt-4o-mini-tts, gpt-4.1 — also OpenRouter, Groq, Together, vLLM, LM Studio, Ollama | image, transcribe, TTS, text |

**The open-source long tail:** fal.ai and Replicate are generic. Paste any model id into
Settings → Custom models and it becomes a first-class entry in the Generate panel with an
Extra JSON field for model-specific inputs. That is the path to the GitHub model catalogue
without waiting for a code change.

### Local editing — 38 operations, no AI, no network

**Cut & time** — trim · auto jump-cut (silence detection) · split at scene changes ·
speed ramp · reverse · loop / boomerang · fade in-out
**Frame** — social reframe (9:16 / 1:1 / 4:5, blurred fill) · crop · rotate / flip · transcode
**Look** — colour grade · LUT (.cube) · sharpen · denoise (hqdn3d / nlmeans / atadenoise /
removegrain / bm3d) · deband · deinterlace · HDR→SDR tonemap
**Stabilise** — two-pass vidstab (proper) · single-pass deshake (fast)
**Composite** — watermark · text / titles · picture-in-picture · split screen · chroma key ·
region blur (faces, plates) · burn subtitles
**Audio** — add / replace / mix · EBU R128 loudness normalise · speech clean-up chain
(high-pass, de-noise, gate, compress, limit) · extract
**Stills & export** — Ken Burns · extract frames · contact sheet · waveform image ·
image convert & resize · GIF · **12 platform export presets**

Export presets: YouTube 1080p/4K · Shorts/TikTok/Reels · Instagram 1:1 and 4:5 · X ·
LinkedIn · WhatsApp · Web VP9 · HEVC 4K · ProRes 422 master · lossless archive.
Hardware encoding via NVENC / AMF / QSV, probed against your actual ffmpeg build.

### Templates

Seven starters in the CapCut / Google Vids spirit — Reel 3-beat hook, product hero,
photo slideshow, talking head + b-roll, before/after, cinematic trailer, 6-step explainer.
A template sets the canvas, fills slots from your Library selection, and several carry a
Director brief you can send straight to the agent. Editable JSON.

### Navigation

A left sidebar, not ten tabs. It opens showing **six** entries — Home, Photos to video,
Edit, Enhance, Generate, Director. The other six (Library, Timeline, Templates, Model Hub,
Jobs, Settings) sit behind **Show advanced** (Ctrl+Shift+A), and reveal themselves
automatically if you jump to one by shortcut.

Home is a start screen of task cards plus an honest setup readout: whether ffmpeg is
present, which local engines are installed, which API keys exist, and how many models are
free right now.

### Keyboard

44 commands, every one bound and discoverable three ways: **Ctrl+K** command palette,
generated menus, and **F1** for the full sheet. Ctrl+0..9 jump between sections,
Ctrl+Shift+Return makes the photo clip, Ctrl+Return generates, Ctrl+R runs the current
edit, Space plays the active preview, Ctrl+Shift+X renders the timeline.

### Timeline

Order clips, set in/out points, crossfade, render at any preset (1080p landscape, vertical,
square, 4K), optionally with a music bed mixed under the original audio.

### Director agent

Brief → LLM writes a JSON shotlist → a still per shot (optionally locked to identity
reference photos) → image→video per shot → narration → ffmpeg assembles the cut with
crossfades. Every step is skippable, every intermediate lands in the library, and a failed
step never destroys the work before it.

## Setup

1. **API keys** — Settings tab. Stored obfuscated in `%APPDATA%\MediaForge\credentials.json`.
   An environment variable of the matching name always wins:
   `GEMINI_API_KEY`, `ARK_API_KEY`, `DASHSCOPE_API_KEY`, `FAL_KEY`, `REPLICATE_API_TOKEN`,
   `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`, `ANTHROPIC_API_KEY`.
2. **ffmpeg** — required for all local editing, thumbnails and rendering. API generation
   works without it.
   ```bash
   winget install Gyan.FFmpeg
   ```
   If `winget` is "not recognized", it exists but `%LOCALAPPDATA%\Microsoft\WindowsApps`
   is missing from your PATH — call it by full path instead:
   ```bash
   & "$env:LOCALAPPDATA\Microsoft\WindowsApps\winget.exe" install Gyan.FFmpeg
   ```
   Chocolatey works too, from an **elevated** console: `choco install ffmpeg -y`.

   Settings has an install button (it probes for winget then Chocolatey), a manual path
   field, and a Re-detect button. The locator also searches the winget and Chocolatey
   install folders directly, so ffmpeg is found without waiting for a PATH refresh.

## Project layout on disk

```
<project>/
  project.mfproj      SQLite catalogue: assets, generation history, timeline
  media/              imported and generated files
  renders/            exported cuts
  cache/thumbs/       thumbnails
  cache/staged/       trimmed segments during a timeline render
```

Self-contained — copy the folder anywhere and it still opens.

## Architecture

```
mediaforge/
  config.py       settings + credential store
  project.py      SQLite catalogue, assets, timeline
  jobs.py         thread-pool queue with progress and cooperative cancellation
  ffmpeg_tools.py probe, thumbnails, and every editing operation as an argv builder
  agents.py       the Director pipeline
  context.py      shared app state
  providers/
    base.py       Provider ABC + Capability enum + declarative option schema
    http.py       shared HTTP + async job polling
    google.py     Gemini / Imagen / Veo
    gateways.py   Seedance, Qwen-Omni/Wan, fal.ai, Replicate, ComfyUI
    assist.py     Anthropic, ElevenLabs, OpenAI-compatible
  ui/             Qt panels — Library, Generate, Director, Edit, Timeline, Jobs, Settings
```

**Adding a model needs no code at all.** `catalog/models.json` is copied to your config
folder on first run and yours wins. Add an entry, press Ctrl+Shift+L, and it appears in
Generate with a working options form:

```json
{
  "id": "some-model-4",
  "label": "Some Model 4.0",
  "openness": "closed",
  "capabilities": ["text_to_video"],
  "options_preset": "video_audio",
  "routes": [{"provider": "fal", "endpoint": "fal-ai/x/some-model-4",
              "capability": "text_to_video", "verified": false}]
}
```

A model declares *routes*; the catalogue picks the first route whose backend you have a
key for and delegates. So one entry can run on fal.ai today and the vendor's native API
tomorrow. Same for `localtools.json` (engines) and `templates.json` (starters).

**Adding a provider backend** is one class plus one line in
`providers/__init__.py:PROVIDER_CLASSES`. The UI builds its forms from the declarative
`options` dict, so no UI code changes either way.

## Known limitations

- Model ids for preview models (Veo, Seedance, Wan) change when the vendors rename them.
  They are plain strings in the provider files, and fal/Replicate custom-model fields let
  you route around a rename without touching code.
- `stabilise` uses single-pass `deshake`; two-pass `vidstab` needs an ffmpeg build with
  `--enable-libvidstab`.
- The timeline is a cut list, not a multi-track NLE — one video track, one optional music bed.
- Video-file upload to Gemini uses the raw (non-resumable) upload path; very large files
  are better analysed after trimming.
