"""The rest of the models already in the user's ComfyUI, run with no workflow to export.

comfy_builtin.py does this for Wan 2.2 5B. This module does it for every other model this
kind of install tends to have on disk, each found by asking the running server which files
it can load -- so nothing is assumed about names or folders:

  SDXL (any SDXL checkpoint; Juggernaut XL here)   text to image, and image edit as img2img
  ACE-Step 1.5                                      text to music, with or without lyrics
  Real-ESRGAN                                       4x image upscale
  Wan 2.2 S2V 14B (GGUF or safetensors)             a still photo speaks or sings an audio track

The graphs follow ComfyUI's own templates for these models (comfyui_workflow_templates:
image_sdxl_simple, audio_ace_step_1_5_checkpoint, utility-gan_upscaler, video_wan2_2_14B_s2v),
with the settings those templates ship.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from .base import Capability, ModelSpec, ProviderError
from .comfy_builtin import _choices, has_node

SDXL_ID = "sdxl-local"
ACE_STEP_ID = "ace-step-1.5"
ESRGAN_ID = "realesrgan-local"
S2V_ID = "wan2.2-s2v-14b"

S2V_FPS = 16
SDXL_NEGATIVE = ("lowres, blurry, out of focus, deformed, bad anatomy, extra limbs, "
                 "mutated hands, watermark, text, logo, signature, jpeg artifacts")
S2V_NEGATIVE = ("static frame, frozen, blurry, distorted face, deformed mouth, extra fingers, "
                "bad hands, subtitles, text, watermark, low quality, jpeg artifacts")

# SDXL is trained near one megapixel; these are its native buckets.
SDXL_SIZES = {"1:1": (1024, 1024), "16:9": (1344, 768), "9:16": (768, 1344),
              "4:3": (1152, 896), "3:4": (896, 1152), "3:2": (1216, 832),
              "2:3": (832, 1216), "21:9": (1536, 640)}
SDXL_STEPS = {"Draft": 20, "Standard": 30, "High": 40}
# S2V output area per quality (the side of a square of the same area), multiples of 16.
S2V_AREA = {"Draft": 448, "Standard": 576, "High (slow)": 704}
S2V_STEPS = {"Draft": 12, "Standard": 20, "High (slow)": 30}
MUSIC_KEYS = ["C major", "G major", "D major", "A major", "E major", "F major",
              "A minor", "E minor", "D minor", "B minor", "C minor", "G minor"]


def _sdxl_checkpoint(name: str) -> bool:
    lowered = name.lower()
    return ("xl" in lowered and "refiner" not in lowered and "ace" not in lowered
            and "turbo" not in lowered)


@dataclass(frozen=True)
class Need:
    """One file the graph loads: which loader lists it, and how to recognise it."""
    role: str
    node: str
    field: str
    matches: Callable[[str], bool]
    prefer: Callable[[str], Any] = lambda name: name


NEEDS: dict[str, list[list[Need]]] = {
    # Each inner list is alternatives for one role; the first that the server has wins.
    SDXL_ID: [[Need("checkpoint", "CheckpointLoaderSimple", "ckpt_name", _sdxl_checkpoint,
                    prefer=lambda name: ("juggernaut" not in name.lower(), name))]],
    ACE_STEP_ID: [[Need("checkpoint", "CheckpointLoaderSimple", "ckpt_name",
                        lambda name: "ace_step" in name.lower().replace("-", "_")
                        and ("1.5" in name or "1_5" in name))]],
    ESRGAN_ID: [[Need("upscaler", "UpscaleModelLoader", "model_name",
                      lambda name: "esrgan" in name.lower(),
                      prefer=lambda name: ("x4plus" not in name.lower(), name))]],
    S2V_ID: [
        [Need("unet_gguf", "UnetLoaderGGUF", "unet_name", lambda name: "s2v" in name.lower()),
         Need("unet", "UNETLoader", "unet_name", lambda name: "s2v" in name.lower())],
        [Need("text_encoder", "CLIPLoader", "clip_name", lambda name: "umt5" in name.lower())],
        [Need("vae", "VAELoader", "vae_name",
              lambda name: "wan_2.1_vae" in name.lower() or "wan2.1_vae" in name.lower())],
        [Need("audio_encoder", "AudioEncoderLoader", "audio_encoder_name",
              lambda name: "wav2vec2" in name.lower())],
    ],
}

MISSING = {
    SDXL_ID: "an SDXL checkpoint in models/checkpoints",
    ACE_STEP_ID: "ace_step_1.5 (an all-in-one checkpoint) in models/checkpoints",
    ESRGAN_ID: "a Real-ESRGAN model in models/upscale_models",
    S2V_ID: ("Wan2.2-S2V-14B (models/diffusion_models, GGUF needs the ComfyUI-GGUF nodes), "
             "umt5_xxl (text_encoders), wan_2.1_vae (vae) and wav2vec2 (audio_encoders)"),
}

SPECS = [
    ModelSpec(
        id=SDXL_ID, label="SDXL (built in)",
        capabilities=[Capability.TEXT_TO_IMAGE, Capability.IMAGE_EDIT],
        notes="Runs on this PC through ComfyUI with the SDXL checkpoint already installed "
              "(Juggernaut XL here). About 30-60 s per image on an 8 GB GPU. With a "
              "reference image it repaints that image to follow the prompt.",
        options={
            "aspect_ratio": {"type": "choice", "default": "16:9",
                             "choices": list(SDXL_SIZES), "label": "Shape"},
            "quality": {"type": "choice", "default": "Standard",
                        "choices": list(SDXL_STEPS), "label": "Quality"},
            "cfg": {"type": "float", "default": 5.5, "min": 1.0, "max": 12.0,
                    "label": "Prompt strength"},
            # 0.55 kept a daylight room in daylight when asked for dusk; at 0.7 the
            # composition holds and the change asked for shows.
            "strength": {"type": "float", "default": 0.7, "min": 0.1, "max": 1.0,
                         "label": "How much an edit may change"},
        },
    ),
    ModelSpec(
        id=ACE_STEP_ID, label="ACE-Step 1.5 (built in)",
        capabilities=[Capability.TEXT_TO_MUSIC],
        notes="Original music made on this PC: describe the style, instruments and mood; "
              "add lyrics for a song, or leave them empty for an instrumental. About "
              "half a minute for 30 s of music on an 8 GB GPU. The track is yours to use.",
        options={
            "seconds": {"type": "float", "default": 30.0, "min": 5.0, "max": 240.0,
                        "label": "Length (seconds)"},
            "bpm": {"type": "int", "default": 100, "min": 40, "max": 220, "label": "Tempo (BPM)"},
            "key": {"type": "choice", "default": "C major", "choices": MUSIC_KEYS,
                    "label": "Key"},
            "lyrics": {"type": "text", "default": "", "label": "Lyrics (empty: instrumental)",
                       "placeholder": "[Verse]\nFirst line of the song\n\n[Chorus]\n…"},
            "language": {"type": "choice", "default": "en",
                         "choices": ["en", "fa", "ar", "tr", "fr", "de", "es", "zh", "ja"],
                         "label": "Lyrics language"},
        },
    ),
    ModelSpec(
        id=ESRGAN_ID, label="Real-ESRGAN x4 (built in)",
        capabilities=[Capability.UPSCALE],
        notes="Upscales a photo 4x on this PC through ComfyUI, recovering edges and texture. "
              "For video, use Enhance, which runs the same model frame by frame.",
        options={"scale": {"type": "choice", "default": "4x", "choices": ["4x", "2x"],
                           "label": "Scale"}},
    ),
    ModelSpec(
        id=S2V_ID, label="Wan 2.2 S2V 14B (built in, slow)",
        capabilities=[Capability.LIP_SYNC],
        notes="A photo of a person speaks or sings the audio you give it: mouth, face and "
              "head move with the voice. Runs on this PC, but the 14B model is larger than "
              "an 8 GB GPU, so expect several minutes per second of video at Draft.",
        options={
            "quality": {"type": "choice", "default": "Draft", "choices": list(S2V_AREA),
                        "label": "Quality"},
            "seconds": {"type": "float", "default": 3.0, "min": 1.0, "max": 5.0,
                        "label": "Length (seconds)"},
            "cfg": {"type": "float", "default": 4.5, "min": 1.0, "max": 8.0,
                    "label": "Prompt strength"},
        },
    ),
]
SPEC_IDS = {spec.id for spec in SPECS}

_files_cache: dict[tuple[str, str], tuple[float, dict[str, str] | None]] = {}
FILES_TTL = 30.0


def find_files(base_url: str, model_id: str, force: bool = False) -> dict[str, str] | None:
    """Role -> file name as the server lists it, or None if any role has no file."""
    key = (base_url, model_id)
    now = time.time()
    cached = _files_cache.get(key)
    if cached and not force and now - cached[0] < FILES_TTL:
        return cached[1]
    found: dict[str, str] | None = {}
    try:
        for alternatives in NEEDS.get(model_id, []):
            chosen = None
            for need in alternatives:
                if not has_node(base_url, need.node):
                    continue
                names = [name for name in _choices(base_url, need.node, need.field)
                         if need.matches(name)]
                if names:
                    chosen = (need.role, sorted(names, key=need.prefer)[0])
                    break
            if chosen is None:
                found = None
                break
            found[chosen[0]] = chosen[1]
    except (ProviderError, OSError, ValueError):
        found = None
    _files_cache[key] = (now, found)
    return found


def clear_cache() -> None:
    _files_cache.clear()


# ======================================================================================
# Graphs
# ======================================================================================
def sdxl_graph(files: dict[str, str], prompt: str, negative: str, width: int, height: int,
               steps: int, cfg: float, seed: int, prefix: str,
               image_name: str | None = None, strength: float = 0.6) -> dict[str, Any]:
    graph: dict[str, Any] = {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": files["checkpoint"]}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": prompt}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": negative}},
        "4": {"class_type": "EmptyLatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}},
        "5": {"class_type": "KSampler",
              "inputs": {"model": ["1", 0], "seed": seed, "steps": steps, "cfg": cfg,
                         "sampler_name": "dpmpp_2m", "scheduler": "karras",
                         "positive": ["2", 0], "negative": ["3", 0],
                         "latent_image": ["4", 0], "denoise": 1.0}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": prefix}},
    }
    if image_name:
        # img2img: the reference, fitted to the SDXL bucket, is the starting point and
        # `strength` is how far the sampler may move away from it.
        graph["8"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
        graph["9"] = {"class_type": "ImageScale",
                      "inputs": {"image": ["8", 0], "upscale_method": "lanczos",
                                 "width": width, "height": height, "crop": "center"}}
        graph["4"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["9", 0], "vae": ["1", 2]}}
        graph["5"]["inputs"]["denoise"] = max(0.05, min(1.0, strength))
    return graph


def ace_step_graph(files: dict[str, str], tags: str, lyrics: str, seconds: float, bpm: int,
                   key: str, language: str, seed: int, prefix: str) -> dict[str, Any]:
    return {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": files["checkpoint"]}},
        "2": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.0}},
        "3": {"class_type": "EmptyAceStep1.5LatentAudio",
              "inputs": {"seconds": seconds, "batch_size": 1}},
        "4": {"class_type": "TextEncodeAceStepAudio1.5",
              "inputs": {"clip": ["1", 1], "tags": tags, "lyrics": lyrics or "[Instrumental]",
                         "seed": seed, "bpm": bpm, "duration": seconds, "timesignature": "4",
                         "language": language, "keyscale": key,
                         "generate_audio_codes": True, "cfg_scale": 2.0,
                         "temperature": 0.85, "top_p": 0.9, "top_k": 0, "min_p": 0.0}},
        "5": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}},
        "6": {"class_type": "KSampler",
              "inputs": {"model": ["2", 0], "seed": seed, "steps": 8, "cfg": 1.0,
                         "sampler_name": "euler", "scheduler": "simple",
                         "positive": ["4", 0], "negative": ["5", 0],
                         "latent_image": ["3", 0], "denoise": 1.0}},
        "7": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
        "8": {"class_type": "SaveAudioMP3",
              "inputs": {"audio": ["7", 0], "filename_prefix": prefix, "quality": "V0"}},
    }


def esrgan_graph(files: dict[str, str], image_name: str, scale: int,
                 prefix: str) -> dict[str, Any]:
    graph: dict[str, Any] = {
        "1": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": files["upscaler"]}},
        "3": {"class_type": "ImageUpscaleWithModel",
              "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}},
        "4": {"class_type": "SaveImage", "inputs": {"images": ["3", 0], "filename_prefix": prefix}},
    }
    if scale == 2:
        graph["5"] = {"class_type": "ImageScaleBy",
                      "inputs": {"image": ["3", 0], "upscale_method": "lanczos", "scale_by": 0.5}}
        graph["4"]["inputs"]["images"] = ["5", 0]
    return graph


def s2v_size(image_width: int, image_height: int, quality: str) -> tuple[int, int]:
    """Keeps the photo's shape at the quality's pixel area, in multiples of 16."""
    side = S2V_AREA.get(quality, S2V_AREA["Draft"])
    ratio = (image_width / image_height) if image_width and image_height else 1.0
    width = side * ratio ** 0.5
    height = side / ratio ** 0.5
    return max(256, int(width // 16) * 16), max(256, int(height // 16) * 16)


def s2v_frames(seconds: float) -> int:
    wanted = max(13, int(round(seconds * S2V_FPS)))
    return ((wanted - 1 + 3) // 4) * 4 + 1


def s2v_graph(files: dict[str, str], prompt: str, negative: str, image_name: str,
              audio_name: str, width: int, height: int, length: int, steps: int, cfg: float,
              seed: int, prefix: str) -> dict[str, Any]:
    unet_loader = ({"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": files["unet_gguf"]}}
                   if "unet_gguf" in files else
                   {"class_type": "UNETLoader",
                    "inputs": {"unet_name": files["unet"], "weight_dtype": "default"}})
    return {
        "1": unet_loader,
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": files["text_encoder"], "type": "wan"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": files["vae"]}},
        "4": {"class_type": "AudioEncoderLoader",
              "inputs": {"audio_encoder_name": files["audio_encoder"]}},
        "5": {"class_type": "LoadAudio", "inputs": {"audio": audio_name}},
        "6": {"class_type": "AudioEncoderEncode",
              "inputs": {"audio_encoder": ["4", 0], "audio": ["5", 0]}},
        "7": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "8": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": 8.0}},
        "9": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        "10": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": negative}},
        "11": {"class_type": "WanSoundImageToVideo",
               "inputs": {"positive": ["9", 0], "negative": ["10", 0], "vae": ["3", 0],
                          "width": width, "height": height, "length": length,
                          "batch_size": 1, "audio_encoder_output": ["6", 0],
                          "ref_image": ["7", 0]}},
        "12": {"class_type": "KSampler",
               "inputs": {"model": ["8", 0], "seed": seed, "steps": steps, "cfg": cfg,
                          "sampler_name": "uni_pc", "scheduler": "simple",
                          "positive": ["11", 0], "negative": ["11", 1],
                          "latent_image": ["11", 2], "denoise": 1.0}},
        # As the template does: repeat the first latent frame, decode, then drop the three
        # images that repeat produces, which removes the washed-out opening frame.
        "13": {"class_type": "LatentCut",
               "inputs": {"samples": ["12", 0], "dim": "t", "index": 0, "amount": 1}},
        "14": {"class_type": "LatentConcat",
               "inputs": {"samples1": ["13", 0], "samples2": ["12", 0], "dim": "t"}},
        "15": {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["3", 0]}},
        "16": {"class_type": "ImageFromBatch",
               "inputs": {"image": ["15", 0], "batch_index": 3, "length": 4096}},
        "17": {"class_type": "SaveWEBM",
               "inputs": {"images": ["16", 0], "filename_prefix": prefix, "codec": "vp9",
                          "fps": float(S2V_FPS), "crf": 14.0}},
    }
