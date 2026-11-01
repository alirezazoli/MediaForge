# MediaForge Studio

A Windows desktop studio for video and photo work: turn photos into a moving film, repair
and upscale footage, edit with 38 ffmpeg tools, generate with the AI video and image
APIs, and let a director agent take a written brief all the way to an assembled cut.

Everything that can run on your own machine does — editing, repair, the photo film, and
the local AI engines need no account and no network. The cloud models are optional and
only used with your own keys.

## Install

Download `MediaForge-Studio-<version>-Setup.exe` from the
[Releases](https://github.com/alirezazoli/MediaForge/releases) page and run it.

- Installs for the current user by default — no administrator prompt. "Install for all
  users" is offered on the first page.
- ffmpeg is included (in `ffmpeg\` beside the program), so editing and rendering work
  straight away.
- Settings, keys and the learning history live in `%APPDATA%\MediaForge`; projects in
  `%USERPROFILE%\MediaForge Projects`. Uninstalling removes the program and leaves both
  alone.
- Windows 10 or 11, 64-bit.

Check an installed copy end to end at any time:

```bash
"%LOCALAPPDATA%\Programs\MediaForge Studio\MediaForge.exe" --selftest --report selftest.json
```

It finds ffmpeg and its filters, loads the catalogues, renders a short film from
generated photos, measures a clip with known faults, builds every screen and checks the
preview player. The exit code is the number of failed checks.

## Screens

Every task is a short sequence of steps with one primary button that is always the next
move, and every job reports its progress on the screen that started it.

| Screen | Steps | What it does |
|---|---|---|
| Photos to video | Photos → Look → Film → Create | Photos become shots with camera moves, crossfades, atmosphere and music |
| Repair & upscale | Video → Analyse → Fix → Result | Measures noise, softness, dead air, bars, shake, interlacing and loudness, then fixes what it found; before/after wipe |
| Batch repair | Folder → Analyse → Fix → Repair | The same, for a whole folder, with a per-file account of what happened |
| Edit | File → Tool → Settings → Result | 38 ffmpeg tools as tiles; the result can be fed straight back in for the next tool |
| Enhance | File → What to do → Result | Local AI engines: Real-ESRGAN, Real-CUGAN, waifu2x, RIFE, rembg, Whisper, Demucs, Kokoro |
| Generate | Task → Model → Prompt → Result | Every provider capability; only tasks something can actually run are shown |
| Director | Brief → Crew → Cut → Result | Writes a shot list, makes stills and clips, narrates, assembles |

Also: Library, Timeline, Templates, Find music (licence-checked search, or compose an
original track on this PC with ACE-Step), Model Hub, Assistant
(a local Ollama model that drives the app), Learning, Jobs, Settings. **Ctrl+K** opens the
command palette; **F1** lists every shortcut.

## What runs locally, and what needs a key

Each model in a dropdown says up front whether it will run: nothing appended means a
configured key covers it, **no key** means it runs without one, **needs a key** means it
is disabled and its tooltip names the key that would unlock it. "Only what runs without
an API key" on the Generate screen hides the rest.

### Built in: models MediaForge runs on your PC by itself

If these are already on the machine, MediaForge finds them and uses them with no key, no
workflow to export and nothing to configure. ComfyUI is started for you when it is
stopped; each model is found by asking ComfyUI which files it can load.

| Task | Model | Needs | Time on an 8 GB laptop GPU |
|---|---|---|---|
| Text to image, image edit | SDXL (any SDXL checkpoint, e.g. Juggernaut XL) | ComfyUI | 30–60 s per image |
| Text to video, image to video | Wan 2.2 TI2V 5B | ComfyUI | ~80 s Draft, ~4–5 min Standard |
| Lip sync (a photo speaks an audio track) | Wan 2.2 S2V 14B, GGUF or safetensors | ComfyUI (+ ComfyUI-GGUF) | slow: minutes per second |
| Text to music, with or without lyrics | ACE-Step 1.5 | ComfyUI | ~1 min for 30 s |
| Image upscale 4× | Real-ESRGAN x4plus | ComfyUI | 1–2 min |
| Scripts, shot lists, captions, the Assistant | every model pulled in Ollama, each selectable | Ollama | seconds |
| Video understanding | Ollama models that read images (Qwen 3.5, Gemma 4…) | Ollama | ~10 s for 6 frames |
| Text to speech | the voices built into Windows | nothing | instant |

Before a ComfyUI job, MediaForge asks Ollama to release the GPU, so a video render never
competes with a language model that answered a minute ago.

### Open models (run locally through ComfyUI)

Verified August 2026. "Available" and "open" are different things.

| Model | Weights | Where |
|---|---|---|
| **MiniMax H3 (Hailuo 3)** | **open** — released 2026-08-03, ~42.5 GB | HuggingFace, native ComfyUI |
| **Wan 2.2** | **open**, Apache 2.0 | [Wan-Video/Wan2.2](https://github.com/Wan-Video/Wan2.2) |
| **LTX-2 / LTX-Video** | **open** — synced audio and video | [Lightricks/LTX-Video](https://github.com/Lightricks/LTX-Video) |
| **HunyuanVideo** | **open**, 13B | [Tencent-Hunyuan/HunyuanVideo](https://github.com/Tencent-Hunyuan/HunyuanVideo) |
| **Mochi 1** | **open**, Apache 2.0 | [genmoai/mochi](https://github.com/genmoai/mochi) |
| **CogVideoX** | **open** | [THUDM/CogVideo](https://github.com/THUDM/CogVideo) |
| **SkyReels V2** | **open** | [SkyworkAI/SkyReels-V2](https://github.com/SkyworkAI/SkyReels-V2) |
| **FramePack** | **open**, long video on 6 GB VRAM | [lllyasviel/FramePack](https://github.com/lllyasviel/FramePack) |
| **FLUX.1 dev / schnell / Kontext** | **open** (schnell is Apache 2.0) | [black-forest-labs/flux](https://github.com/black-forest-labs/flux) |
| **Qwen-Image** | **open**, Apache 2.0 | [QwenLM/Qwen-Image](https://github.com/QwenLM/Qwen-Image) |
| **SDXL / SD 3.5** | **open** | [Stability-AI](https://github.com/Stability-AI/generative-models) |
| Lip sync: LatentSync, MuseTalk, Wav2Lip | **open** | [bytedance/LatentSync](https://github.com/bytedance/LatentSync) |
| Motion: Wan-VACE, MimicMotion, Champ | **open** | [ali-vilab/VACE](https://github.com/ali-vilab/VACE) |
| Seedance, Kling, Hailuo 2.x, Veo 3.1, Grok Imagine, Pixverse, Wan 2.5/2.6, Nano Banana | **closed** — API only | — |

Anything on GitHub claiming to be "Seedance weights" or "Kling open source" is an API
wrapper or worse.

### Local engines (Model Hub, installed on request)

| Engine | Does | Licence |
|---|---|---|
| Real-ESRGAN | 2×/3×/4× upscale, photo and anime models | BSD-3 |
| Real-CUGAN | upscale + denoise, best on illustration | MIT |
| waifu2x | gentle denoise + upscale | MIT |
| RIFE | frame interpolation, slow motion | MIT |
| rembg | background removal | MIT |
| faster-whisper | offline transcription, 99 languages | MIT |
| Demucs | vocal / drum / bass / other stems | MIT |
| Kokoro | local text to speech | Apache-2.0 |
| ComfyUI | the engine for every open model above | GPL-3 |

The `ncnn-vulkan` engines are self-contained and run on any Vulkan GPU, AMD and Intel
included. The Python engines (Whisper, Demucs, rembg, Kokoro) install into their own
environment under `%APPDATA%\MediaForge\engines-python`, created from any Python 3.10+ on
the machine, and always run as a separate process.

### Cloud providers (all optional)

| Provider | Models | Capabilities |
|---|---|---|
| Google | Nano Banana, Imagen 4, Veo 3.1, Gemini 2.5 | image, image edit, video, scripting, video understanding |
| ByteDance Seedance (Volcengine Ark / BytePlus) | Seedance, Seedream | video, image |
| Alibaba DashScope | Qwen3-Omni, Wan 2.5 | omni-modal, speech, video |
| fal.ai, Replicate | hundreds, plus any model id you paste | everything |
| Anthropic | Claude | scripts, shot lists, captions |
| ElevenLabs | Multilingual v2, Turbo v2.5, Scribe | voice, transcription |
| OpenAI-compatible | OpenAI, OpenRouter, Groq, Together, vLLM, LM Studio, Ollama | image, speech, text |

Keys are entered in Settings and stored obfuscated in `%APPDATA%\MediaForge`. An
environment variable always wins: `GEMINI_API_KEY`, `ARK_API_KEY`, `DASHSCOPE_API_KEY`,
`FAL_KEY`, `REPLICATE_API_TOKEN`, `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`,
`ANTHROPIC_API_KEY`.

**Adding a model needs no code.** `catalog/models.json` is copied to your config folder;
add an entry with a `routes` list and it appears in Generate with a working options form.
An untouched copy follows the bundled catalogue through upgrades; once you edit it, it is
yours and is never overwritten.

## Run from source

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python -m pip install -r requirements.txt
```

```bash
.venv\Scripts\python -m mediaforge
```

`--doctor` prints an environment report; `--version` the version. ffmpeg must be on PATH
(or set in Settings) when running from source: `winget install Gyan.FFmpeg`.

## Test

```bash
.venv\Scripts\python -m pip install pytest
```

```bash
.venv\Scripts\python -m pytest
```

The suite drives the real screens and engines on media it generates with ffmpeg. Every
run uses a throwaway profile and workspace (`MEDIAFORGE_HOME`), and `conftest.py` refuses
to start if the workspace would be your real one.

### The feature tour

```bash
.venv\Scripts\python tools\feature_tour.py C:\path\to\tour-output
```

Runs every screen for real with the models on this PC — every Edit tool, Generate with
each local model, Photos to video, the Director, Templates, Timeline, Repair, Batch,
Enhance, Find music, the Assistant — and keeps one output from each. `index.html` in the
output folder shows them all with their verdicts. It takes about an hour, most of it
video generation; `--only edit repair` runs just those sections.

## Build the installer

Needs the source requirements plus `pyinstaller`, ffmpeg installed through winget, and
[Inno Setup 6](https://jrsoftware.org/isinfo.php).

```bash
powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Python .venv\Scripts\python.exe
```

It freezes the app (PyInstaller, one folder, windowed), copies ffmpeg and the licence texts
in, runs the frozen build's own `--selftest`, and compiles
`MediaForge-Studio-<version>-Setup.exe`. Output goes to `%USERPROFILE%\MediaForge-build`,
outside the source tree.

## Layout

```
mediaforge/
  app.py            entry point and command-line options
  runtime.py        log file, crash reporting, single instance
  selftest.py       end-to-end checks for an installed copy
  paths.py          where things are, from source and when installed
  config.py         settings, credentials, profile location
  enginepython.py   the separate Python for pip-installed engines
  project.py        SQLite catalogue, assets, timeline
  jobs.py           thread-pool queue with progress and cancellation
  ffmpeg_tools.py   probing and every editing operation as an argv builder
  repair.py         measure-then-fix pipeline; diagnose.py has the detectors
  photofilm.py      photos to film; sceneanalysis.py, autopilot.py, atmosphere.py
  localai.py        local engines; gpu.py picks the device and model
  agents.py         the Director pipeline; agent.py the Ollama assistant
  catalog/          models, local engines and templates as data
  providers/        one class per backend
  ui/               the screens; stepper.py, widgets.py, theme.py, icons.py are shared
packaging/          PyInstaller spec, Inno Setup script, build.ps1, icon generator
tests/              pytest suite
```

Logs are in `%APPDATA%\MediaForge\logs\mediaforge.log`; an unexpected error is written
there and shown with the path.

## Known limitations

- Model ids for preview models (Veo, Seedance, Wan) change when vendors rename them. They
  are plain data in the catalogue, and the fal/Replicate custom-model fields route around a
  rename without a code change.
- The timeline is a cut list, not a multi-track editor: one video track and a music bed.
- The Python-based engines need a Python 3.10+ installed on the machine to set up their
  environment; the GPU engines do not.
- The installer is not code-signed, so Windows SmartScreen asks for confirmation on first
  run.

## Licence

MediaForge Studio is released under the [MIT licence](LICENSE). Third-party components
keep their own licences, listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md); the
bundled ffmpeg build is GPL v3.
