"""Windows' own voices: text to speech with nothing to install and no key.

Every Windows installation ships SAPI voices (David and Zira in English on most machines,
more with language packs). They are not as natural as a neural voice, but they read a
script aloud offline and at once, which is what a narration draft or a lip-sync test
needs. The voices are listed from the registry, where SAPI itself finds them, so no
process has to start just to fill the model list.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .base import Capability, GenerationResult, ModelSpec, Provider, ProviderError

VOICE_KEYS = (r"SOFTWARE\Microsoft\Speech\Voices\Tokens",
              r"SOFTWARE\WOW6432Node\Microsoft\Speech\Voices\Tokens")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

SPEAK_SCRIPT = r"""
param([string]$Voice, [int]$Rate, [string]$TextFile, [string]$Output)
Add-Type -AssemblyName System.Speech
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
$speaker.SelectVoice($Voice)
$speaker.Rate = $Rate
$speaker.SetOutputToWaveFile($Output)
$speaker.Speak([IO.File]::ReadAllText($TextFile, [Text.Encoding]::UTF8))
$speaker.Dispose()
"""


def installed_voices() -> list[tuple[str, str]]:
    """(name SAPI selects by, language) for every desktop voice on this machine."""
    if sys.platform != "win32":
        return []
    import winreg

    voices: dict[str, str] = {}
    for path in VOICE_KEYS:
        try:
            root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
        except OSError:
            continue
        with root:
            index = 0
            while True:
                try:
                    token = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(root, token + r"\Attributes") as attributes:
                        name = winreg.QueryValueEx(attributes, "Name")[0]
                        try:
                            language = winreg.QueryValueEx(attributes, "Language")[0]
                        except OSError:
                            language = ""
                except OSError:
                    continue
                voices.setdefault(str(name), str(language))
    return sorted(voices.items())


class WindowsSpeechProvider(Provider):
    key = "windows-tts"
    name = "Windows voices (built in)"
    homepage = "https://support.microsoft.com/windows/download-languages-and-voices"
    credential_slot = ""
    credential_help = "No key. More voices come with Windows language packs."

    _voices: list[tuple[str, str]] | None = None

    @property
    def models(self) -> list[ModelSpec]:
        if type(self)._voices is None:
            type(self)._voices = installed_voices()
        return [ModelSpec(
            id=voice, label=f"{voice.replace('Microsoft ', '').replace(' Desktop', '')} "
                            "(Windows voice)",
            capabilities=[Capability.TEXT_TO_SPEECH],
            notes="Reads the script with a voice built into Windows: offline, instant, no "
                  "key. Clear rather than natural -- good for drafts and timing.",
            options={"rate": {"type": "int", "default": 0, "min": -5, "max": 5,
                              "label": "Speed (-5 slow, 5 fast)"}},
        ) for voice, _language in type(self)._voices]

    @property
    def configured(self) -> bool:
        return bool(self.models)

    def generate(self, request, progress=lambda f, m: None, should_cancel=None) -> GenerationResult:
        started = time.time()
        text = request.prompt.strip()
        if not text:
            raise ProviderError("Write the words the voice should read.")
        if self.model(request.model_id) is None:
            raise ProviderError(f"The Windows voice '{request.model_id}' is not installed.")
        folder = Path(tempfile.mkdtemp(prefix="mediaforge_tts_"))
        script = folder / "speak.ps1"
        script.write_text(SPEAK_SCRIPT, encoding="utf-8")
        text_file = folder / "script.txt"
        text_file.write_text(text, encoding="utf-8")
        destination = self._stamped_path(request.output_dir, "voice", ".wav")
        progress(0.2, f"{request.model_id} is reading the script")
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
             "-Voice", request.model_id, "-Rate", str(int(request.options.get("rate", 0))),
             "-TextFile", str(text_file), "-Output", str(destination)],
            capture_output=True, text=True, creationflags=_NO_WINDOW, timeout=600)
        if result.returncode != 0 or not destination.exists() or destination.stat().st_size < 100:
            raise ProviderError(f"Windows could not read the script: "
                                f"{(result.stderr or result.stdout).strip()[-300:]}")
        progress(1.0, "Done")
        return GenerationResult(files=[destination], provider=self.key,
                                model_id=request.model_id,
                                elapsed_seconds=time.time() - started,
                                cost_note="built into Windows")
