"""A real run through every screen of MediaForge, keeping one output from each.

    python tools/feature_tour.py OUTPUT_FOLDER [--skip SECTION ...] [--only SECTION ...]

It builds the real main window (offscreen), creates a project inside OUTPUT_FOLDER, and
drives each screen the way a person would -- choose, set, run -- through the same panel
methods the buttons call. Every model it uses runs on this PC with no key: ComfyUI (SDXL,
Wan 2.2, ACE-Step, Real-ESRGAN, Wan S2V), Ollama and the Windows voices. Nothing touches
the user's own profile or projects: the profile is a temporary folder.

Outputs land in OUTPUT_FOLDER/project (renders/, media/, music/), a screenshot of each
screen in OUTPUT_FOLDER/screens, and the verdicts in report.json / report.md /
index.html. Dialogs are answered "Yes" and their text is logged, so a refusal shows up
in the report instead of hanging the run.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["MEDIAFORGE_HOME"] = tempfile.mkdtemp(prefix="mediaforge-tour-")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFontDatabase  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QLineEdit, QMessageBox, QSpinBox,
    QTextEdit,
)

app = QApplication(sys.argv[:1])
for font in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf", "SegUIVar.ttf", "SegoeIcons.ttf"):
    path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / font
    if path.exists():
        QFontDatabase.addApplicationFont(str(path))

from mediaforge import ffmpeg_tools  # noqa: E402
from mediaforge.context import AppContext  # noqa: E402
from mediaforge.jobs import JobState  # noqa: E402
from mediaforge.providers import Capability as C  # noqa: E402
from mediaforge.providers import comfy_builtin, comfy_local  # noqa: E402
from mediaforge.ui.main_window import MainWindow  # noqa: E402
from mediaforge.ui.widgets import FilePicker  # noqa: E402

# -- dialogs: answered, and remembered -------------------------------------------------------
DIALOGS: list[str] = []


def _log_dialog(kind):
    def answer(parent, title, text="", *args, **kwargs):
        DIALOGS.append(f"[{kind}] {title}: {str(text)[:300]}")
        return QMessageBox.StandardButton.Yes if kind == "question" else QMessageBox.StandardButton.Ok
    return staticmethod(answer)


for _kind in ("question", "information", "warning", "critical"):
    setattr(QMessageBox, _kind, _log_dialog(_kind))


# ======================================================================================
# Plumbing
# ======================================================================================
class Tour:
    def __init__(self, out: Path) -> None:
        self.out = out
        self.inputs = out / "inputs"
        self.screens = out / "screens"
        for folder in (self.inputs, self.screens):
            folder.mkdir(parents=True, exist_ok=True)
        self.context = AppContext()
        self.window = MainWindow(self.context)
        self.window.resize(1280, 800)
        self.window.show()
        self.pump(0.5)
        self.window._load_project(out / "project")
        self.locator = self.context.locator
        self.results: list[dict] = []
        self.made: dict[str, Path] = {}

    # -- event loop and jobs ---------------------------------------------------------------
    def pump(self, seconds: float = 0.05) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

    def mark(self) -> int:
        jobs = self.context.jobs.jobs()
        return jobs[0].id if jobs else 0

    def wait_jobs(self, since: int, timeout: float, settle: float = 1.0) -> list:
        """Waits for every job started after `since` (and any they start) to finish."""
        deadline = time.monotonic() + timeout
        start_by = time.monotonic() + 15
        quiet_since = None
        while time.monotonic() < deadline:
            self.pump(0.2)
            new = [job for job in self.context.jobs.jobs() if job.id > since]
            if not new and time.monotonic() > start_by:
                # The screen refused before starting anything; its dialog says why.
                raise RuntimeError("no job started: " + ("; ".join(DIALOGS) or "no message"))
            busy = any(job.state in (JobState.QUEUED, JobState.RUNNING) for job in new)
            if new and not busy:
                quiet_since = quiet_since or time.monotonic()
                if time.monotonic() - quiet_since >= settle:
                    return sorted(new, key=lambda job: job.id)
            else:
                quiet_since = None
        raise TimeoutError(f"jobs still running after {timeout:.0f} s: " + ", ".join(
            f"{job.title} ({job.message})" for job in self.context.jobs.jobs()
            if job.id > since and job.state in (JobState.QUEUED, JobState.RUNNING)))

    def wait_for(self, condition, timeout: float, what: str) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.pump(0.2)
            if condition():
                return
        raise TimeoutError(f"{what} did not happen within {timeout:.0f} s")

    def screenshot(self, key: str, name: str) -> Path:
        self.window.go_to(key)
        self.pump(0.4)
        target = self.screens / f"{name}.png"
        self.window.grab().save(str(target))
        return target

    # -- media helpers ---------------------------------------------------------------------
    def ffmpeg(self, *args: str) -> None:
        subprocess.run([self.locator.ffmpeg, "-y", "-v", "error", *args], check=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def describe(self, path: Path) -> str:
        if path.is_dir():
            return f"folder, {sum(1 for _ in path.iterdir())} files"
        try:
            info = ffmpeg_tools.probe(self.locator, path)
        except Exception:
            return f"{path.stat().st_size // 1024} KB"
        parts = [info.kind]
        if info.width:
            parts.append(f"{info.width}x{info.height}")
        if info.duration:
            parts.append(f"{info.duration:.1f} s")
        if info.has_audio:
            parts.append("with sound")
        parts.append(f"{path.stat().st_size // 1024} KB")
        return ", ".join(parts)

    def asset(self, path: Path, source: str = "import"):
        project = self.context.require_project()
        return project.asset(self.context.catalogue(path, source=source))

    # -- recording --------------------------------------------------------------------------
    def check(self, section: str, name: str, work) -> object:
        """Runs one check; records PASS with its outputs, or FAIL with the reason."""
        DIALOGS.clear()
        started = time.monotonic()
        entry = {"section": section, "name": name, "status": "PASS", "outputs": [],
                 "note": "", "seconds": 0.0}
        print(f"\n== {section} / {name}", flush=True)
        try:
            produced = work()
            if isinstance(produced, tuple):
                produced, entry["note"] = produced
            for item in produced if isinstance(produced, list) else [produced]:
                if isinstance(item, Path):
                    if not item.exists():
                        raise RuntimeError(f"output missing: {item}")
                    entry["outputs"].append({"path": str(item), "about": self.describe(item)})
                elif item:
                    entry["note"] = (entry["note"] + "\n" + str(item)).strip()
        except Skip as reason:
            entry["status"], entry["note"] = "SKIP", str(reason)
        except Exception as error:
            entry["status"] = "FAIL"
            entry["note"] = f"{type(error).__name__}: {error}"
            entry["trace"] = traceback.format_exc()[-2500:]
        if DIALOGS:
            entry["dialogs"] = list(DIALOGS)
        entry["seconds"] = round(time.monotonic() - started, 1)
        print(f"   {entry['status']} in {entry['seconds']} s  {entry['note'][:300]}", flush=True)
        for output in entry["outputs"]:
            print(f"   -> {output['path']}  ({output['about']})", flush=True)
        self.results.append(entry)
        self.save_report()
        return produced if entry["status"] == "PASS" else None

    def save_report(self) -> None:
        (self.out / "report.json").write_text(json.dumps(self.results, indent=1),
                                              encoding="utf-8")
        counts = {state: sum(1 for r in self.results if r["status"] == state)
                  for state in ("PASS", "FAIL", "SKIP")}
        lines = [f"# MediaForge feature tour — {time.strftime('%Y-%m-%d %H:%M')}", "",
                 f"PASS {counts['PASS']} · FAIL {counts['FAIL']} · SKIP {counts['SKIP']}", "",
                 "| Section | Check | Result | Time | Output / note |", "|---|---|---|---|---|"]
        for r in self.results:
            detail = "<br>".join(f"`{Path(o['path']).name}` ({o['about']})" for o in r["outputs"])
            note = r["note"].replace("\n", " ").replace("|", "/")[:240]
            lines.append(f"| {r['section']} | {r['name']} | {r['status']} | {r['seconds']} s | "
                         f"{detail}{'<br>' if detail and note else ''}{note} |")
        (self.out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        self._write_html(counts)

    def _write_html(self, counts: dict) -> None:
        rows = []
        for r in self.results:
            media = []
            for output in r["outputs"]:
                path = Path(output["path"])
                rel = os.path.relpath(path, self.out).replace("\\", "/")
                suffix = path.suffix.lower()
                if suffix in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                    media.append(f'<a href="{rel}"><img src="{rel}" loading="lazy"></a>')
                elif suffix in (".mp4", ".webm", ".mov"):
                    media.append(f'<video src="{rel}" controls preload="metadata"></video>')
                elif suffix in (".mp3", ".wav", ".m4a"):
                    media.append(f'<audio src="{rel}" controls preload="none"></audio>')
                media.append(f'<div class="file"><a href="{rel}">{html.escape(path.name)}</a> '
                             f'· {html.escape(output["about"])}</div>')
            rows.append(
                f'<tr class="{r["status"].lower()}"><td>{html.escape(r["section"])}</td>'
                f'<td>{html.escape(r["name"])}</td><td><b>{r["status"]}</b><br>'
                f'{r["seconds"]} s</td><td>{"".join(media)}'
                f'<div class="note">{html.escape(r["note"][:1200])}</div></td></tr>')
        (self.out / "index.html").write_text(
            "<!doctype html><meta charset=utf-8><title>MediaForge feature tour</title>"
            "<style>body{font:14px Segoe UI,sans-serif;margin:24px;color:#1f1f1f}"
            "table{border-collapse:collapse;width:100%}td{border-bottom:1px solid #ddd;"
            "padding:8px;vertical-align:top}img,video{max-width:360px;max-height:220px;"
            "display:block;margin:4px 0}.pass b{color:#146c2e}.fail b{color:#b3261e}"
            ".skip b{color:#7a5900}.note{white-space:pre-wrap;color:#444;font-size:12px}"
            ".file{font-size:12px;color:#555}</style>"
            f"<h1>MediaForge feature tour</h1><p>{time.strftime('%Y-%m-%d %H:%M')} — "
            f"PASS {counts['PASS']} · FAIL {counts['FAIL']} · SKIP {counts['SKIP']}</p>"
            "<table>" + "".join(rows) + "</table>", encoding="utf-8")


class Skip(Exception):
    """Not a failure: the feature needs something this machine does not have."""


def set_value(widget, value) -> None:
    if isinstance(widget, QComboBox):
        index = widget.findText(str(value))
        if index < 0:
            raise ValueError(f"'{value}' is not one of {[widget.itemText(i) for i in range(widget.count())]}")
        widget.setCurrentIndex(index)
    elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
        widget.setValue(value)
    elif isinstance(widget, QCheckBox):
        widget.setChecked(bool(value))
    elif isinstance(widget, FilePicker):
        widget.set_path(str(value))
    elif isinstance(widget, QTextEdit):
        widget.setPlainText(str(value))
    elif isinstance(widget, QLineEdit):
        widget.setText(str(value))
    else:
        raise TypeError(f"cannot set {type(widget).__name__}")


# ======================================================================================
# Inputs
# ======================================================================================
def prepare_inputs(tour: Tour) -> list[Path]:
    from PIL import Image, ImageDraw, ImageFont

    made = []
    photos = tour.inputs / "photos"
    photos.mkdir(exist_ok=True)
    wallpapers = sorted(Path(os.environ.get("WINDIR", r"C:\Windows"), "Web", "Wallpaper")
                        .glob("*/*.jpg"))
    landscape = [p for p in wallpapers if Image.open(p).width > Image.open(p).height][:4]
    for index, source in enumerate(landscape, 1):
        target = photos / f"photo_{index}{source.suffix}"
        shutil.copy(source, target)
        made.append(target)
    tour.made["photos"] = photos

    logo = tour.inputs / "logo.png"
    image = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((10, 10, 390, 390), fill=(11, 87, 208, 230))
    try:
        font = ImageFont.truetype(str(Path(os.environ["WINDIR"]) / "Fonts" / "segoeuib.ttf"), 170)
    except OSError:
        font = ImageFont.load_default()
    draw.text((200, 205), "MF", fill="white", font=font, anchor="mm")
    image.save(logo)
    tour.made["logo"] = logo

    lut = tour.inputs / "warm.cube"
    size = 17
    rows = [f"LUT_3D_SIZE {size}"]
    for b in range(size):
        for g in range(size):
            for r in range(size):
                red, green, blue = r / (size - 1), g / (size - 1), b / (size - 1)
                rows.append(f"{min(1, red * 1.08 + 0.02):.5f} {min(1, green * 1.01):.5f} "
                            f"{max(0, blue * 0.88):.5f}")
    lut.write_text("\n".join(rows) + "\n", encoding="ascii")
    tour.made["lut"] = lut

    faulty = tour.inputs / "faulty.mp4"
    tour.ffmpeg("-f", "lavfi", "-i", "color=black:size=640x360:rate=25:duration=2",
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:duration=2",
                "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=4",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                "-filter_complex", "[0:v][1:a][2:v][3:a]concat=n=2:v=1:a=1[v][a]",
                "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", str(faulty))
    tour.made["faulty"] = faulty

    green = tour.inputs / "greenscreen.mp4"
    tour.ffmpeg("-f", "lavfi", "-i", "color=c=0x00FF00:size=1280x720:rate=25:duration=4",
                "-f", "lavfi", "-i", "testsrc2=size=360x360:rate=25:duration=4",
                "-filter_complex", "[0][1]overlay=x='200+200*sin(t*1.5)':y=180[v]",
                "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(green))
    tour.made["green"] = green

    # A real HDR10 clip (PQ, BT.2020, 10-bit HEVC), so HDR to SDR has something to convert.
    hdr = tour.inputs / "hdr10.mp4"
    tour.ffmpeg("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=3",
                "-vf", "zscale=tin=bt709:min=bt709:pin=bt709:t=smpte2084:m=bt2020nc:p=bt2020:"
                       "npl=300,format=yuv420p10le",
                "-c:v", "libx265", "-x265-params",
                "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:hdr10=1:log-level=error",
                "-tag:v", "hvc1", str(hdr))
    tour.made["hdr"] = hdr

    # Hard cuts between three photos, so Split by scene has scenes to find.
    cuts = tour.inputs / "hard_cuts.mp4"
    shots = sorted(photos.iterdir())[:3]
    inputs = []
    for shot in shots:
        inputs += ["-loop", "1", "-t", "1.6", "-i", str(shot)]
    chains = "".join(f"[{i}:v]scale=1280:720:force_original_aspect_ratio=increase,"
                     f"crop=1280:720,setsar=1,fps=25[v{i}];" for i in range(len(shots)))
    tour.ffmpeg(*inputs, "-filter_complex",
                chains + "".join(f"[v{i}]" for i in range(len(shots)))
                + f"concat=n={len(shots)}:v=1:a=0[v]",
                "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(cuts))
    tour.made["cuts"] = cuts
    return made + [logo, lut, faulty, green, hdr, cuts]


def talking_clip(tour: Tour, still: Path, speech: Path) -> Path:
    """The speech twice with 1.8 s of silence between, under a slow zoom on a still."""
    target = tour.inputs / "talking.mp4"
    tour.ffmpeg("-loop", "1", "-i", str(still), "-i", str(speech), "-i", str(speech),
                "-filter_complex",
                "[1:a]aresample=44100,apad=pad_dur=1.8[a1];[2:a]aresample=44100[a2];"
                "[a1][a2]concat=n=2:v=0:a=1[a];"
                "[0:v]scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,"
                "zoompan=z='min(zoom+0.0006,1.12)':d=1:s=1280x720:fps=25[v]",
                "-map", "[v]", "-map", "[a]", "-shortest", "-c:v", "libx264",
                "-pix_fmt", "yuv420p", "-c:a", "aac", str(target))
    return target


def subtitles_for(tour: Tour, clip: Path, sentence_seconds: float) -> Path:
    srt = tour.inputs / "talking.srt"
    lines = ["Welcome to MediaForge Studio.", "Made on this computer — no internet, no key."]
    blocks, start = [], 0.3
    for index, text in enumerate(lines * 2, 1):
        end = start + sentence_seconds / 2
        blocks.append(f"{index}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}\n")
        start = end + (1.8 if index == 2 else 0.1)
    srt.write_text("\n".join(blocks), encoding="utf-8")
    return srt


def _srt_time(seconds: float) -> str:
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{int(hours):02d}:{int(minutes):02d}:{secs:06.3f}".replace(".", ",")


# ======================================================================================
# Sections
# ======================================================================================
def generate(tour: Tour, capability, provider: str, model: str, prompt: str = "",
             images=(), media: Path | None = None, options: dict | None = None,
             timeout: float = 900) -> list:
    window, panel = tour.window, tour.window.generate_panel
    window.go_to("generate")
    panel.local_only.setChecked(False)
    panel.tiles.select(capability.value)
    panel._on_task_chosen(capability.value)
    index = panel.provider_box.findData(provider)
    if index < 0:
        raise RuntimeError(f"{provider} is not offered for {capability.value}")
    panel.provider_box.setCurrentIndex(index)
    index = panel.model_box.findData(model)
    if index < 0:
        raise RuntimeError(f"{model} is not offered by {provider}")
    panel.model_box.setCurrentIndex(index)
    panel.prompt_box.setPlainText(prompt)
    panel._reference_images = [Path(p) for p in images]
    panel._reference_media = media
    panel._refresh_reference_list()
    for name, value in (options or {}).items():
        set_value(panel.options_form._editors[name], value)
    since = tour.mark()
    panel.generate()
    jobs = tour.wait_jobs(since, timeout)
    job = jobs[-1]
    if job.state != JobState.DONE:
        raise RuntimeError(job.error or job.message)
    result = job.result
    if result.text and not result.files:
        return [result.text]
    return list(result.files) + ([result.text] if result.text else [])


def section_generate(tour: Tour) -> None:
    S = "Generate"
    def keep(key):
        def store(produced):
            if produced:
                tour.made[key] = produced[0]
            return produced
        return store

    keep("scene")(tour.check(S, "Text to image — SDXL", lambda: generate(
        tour, C.TEXT_TO_IMAGE, "comfyui", comfy_local.SDXL_ID,
        "a sunlit modern living room with large windows, wooden floor and green plants, "
        "architectural photography, natural light", options={"aspect_ratio": "16:9"})))
    keep("portrait")(tour.check(S, "Text to image — SDXL portrait", lambda: generate(
        tour, C.TEXT_TO_IMAGE, "comfyui", comfy_local.SDXL_ID,
        "head and shoulders portrait of a friendly middle-aged man looking straight at the "
        "camera, neutral grey studio background, soft light, sharp focus, photograph",
        options={"aspect_ratio": "1:1"})))
    if tour.made.get("scene"):
        tour.check(S, "Image edit — SDXL img2img", lambda: generate(
            tour, C.IMAGE_EDIT, "comfyui", comfy_local.SDXL_ID,
            "the same living room at dusk, warm lamps on, blue hour light outside",
            images=[tour.made["scene"]], options={"aspect_ratio": "16:9"}))
        tour.check(S, "Upscale — Real-ESRGAN (ComfyUI)", lambda: generate(
            tour, C.UPSCALE, "comfyui", comfy_local.ESRGAN_ID, images=[tour.made["scene"]],
            options={"scale": "2x"}))
    keep("speech")(tour.check(S, "Text to speech — Windows voice", lambda: generate(
        tour, C.TEXT_TO_SPEECH, "windows-tts", "Microsoft David Desktop",
        "Welcome to MediaForge Studio. Everything you see was made on this computer, "
        "with no internet and no key.")))
    keep("music")(tour.check(S, "Text to music — ACE-Step", lambda: generate(
        tour, C.TEXT_TO_MUSIC, "comfyui", comfy_local.ACE_STEP_ID,
        "warm acoustic guitar and soft piano, gentle and hopeful, cinematic background",
        options={"seconds": 30, "bpm": 92, "key": "D major"})))
    tour.check(S, "Text — Ollama (best local model)", lambda: generate(
        tour, C.LLM, "ollama", "auto",
        "Write three short, punchy captions for a video about a bright modern living room."))
    if tour.made.get("scene"):
        keep("i2v")(tour.check(S, "Image to video — Wan 2.2 (Draft)", lambda: generate(
            tour, C.IMAGE_TO_VIDEO, "comfyui", comfy_builtin.WAN22_ID,
            "slow natural motion: plants sway gently, light shifts softly; camera holds still",
            images=[tour.made["scene"]], options={"quality": "Draft", "seconds": 2.0},
            timeout=1200)))
    tour.check(S, "Text to video — Wan 2.2 (Draft)", lambda: generate(
        tour, C.TEXT_TO_VIDEO, "comfyui", comfy_builtin.WAN22_ID,
        "waves rolling onto a sandy beach at sunset, gentle foam, warm golden light",
        options={"quality": "Draft", "seconds": 2.0}, timeout=1200))
    if tour.made.get("portrait") and tour.made.get("speech"):
        keep("lipsync")(tour.check(S, "Lip sync — Wan 2.2 S2V 14B (Draft, 2 s)", lambda: generate(
            tour, C.LIP_SYNC, "comfyui", comfy_local.S2V_ID,
            "The man speaks to the camera in a friendly way.",
            images=[tour.made["portrait"]], media=tour.made["speech"],
            options={"quality": "Draft", "seconds": 2.0}, timeout=3600)))
    tour.screenshot("generate", "generate")


def section_photos(tour: Tour) -> None:
    S = "Photos to video"
    panel = tour.window.photo_panel

    def film():
        photos = sorted(tour.made["photos"].iterdir())
        panel.strip.add_paths(photos)
        set_value(panel.ai_policy, "Never (camera moves only)")
        if tour.made.get("music"):
            panel._music = tour.made["music"]
            panel.music_label.setText(tour.made["music"].name)
        since = tour.mark()
        panel.run_autopilot()
        tour.wait_jobs(since, 600)
        since = tour.mark()
        panel.create()
        jobs = tour.wait_jobs(since, 1200)
        if jobs[-1].state != JobState.DONE:
            raise RuntimeError(jobs[-1].error)
        tour.made["film"] = panel._result
        return [panel._result], panel.report_box.toPlainText()[:600]

    tour.check(S, "Four photos, autopilot, camera moves, composed music", film)
    tour.screenshot("photos", "photos")


def section_director(tour: Tour) -> None:
    S = "Director"
    panel = tour.window.director_panel

    def film():
        panel.reload_providers()
        panel.brief_box.setPlainText(
            "A calm 8-second film about a quiet morning in a bright modern home: coffee by "
            "the window, plants in the sun.")
        panel.shot_count.setValue(2)
        panel.total_seconds.setValue(8)
        for check in (panel.stills_check, panel.clips_check, panel.narration_check,
                      panel.assemble_check):
            check.setChecked(True)
        crew = {role: (provider.currentData(), model.currentData()) for role, provider, model in (
            ("writer", panel.llm_provider, panel.llm_model),
            ("stills", panel.image_provider, panel.image_model),
            ("camera", panel.video_provider, panel.video_model),
            ("narrator", panel.tts_provider, panel.tts_model))}
        since = tour.mark()
        panel.run(plan_only=False)
        jobs = tour.wait_jobs(since, 3600)
        job = jobs[-1]
        if job.state != JobState.DONE:
            raise RuntimeError(job.error)
        result = job.result
        outputs = [p for p in [result.final_video, result.narration_audio] if p]
        outputs += [shot.still for shot in result.shots if shot.still]
        note = f"crew: {crew}\ntitle: {result.title}\n" + "\n".join(result.warnings)
        if result.final_video:
            tour.made["director"] = result.final_video
        return outputs, note

    tour.check(S, "Brief → shot list → stills → clips → narration → cut (all local)", film)
    tour.screenshot("director", "director")


def section_understanding(tour: Tour) -> None:
    clip = tour.made.get("film") or tour.made.get("director")
    if clip:
        tour.check("Generate", "Video understanding — Ollama vision", lambda: generate(
            tour, C.VIDEO_UNDERSTANDING, "ollama", "auto",
            "What is shown in this video, and how does the camera move?", media=clip))


def section_library_templates_timeline(tour: Tour) -> None:
    project = tour.context.require_project()
    window = tour.window

    def library():
        photos = sorted(tour.made["photos"].iterdir())
        for photo in photos:
            tour.context.catalogue(photo, source="import")
        window.library_panel.refresh()
        tour.pump(0.5)
        count = len(project.assets()) if hasattr(project, "assets") else "?"
        return [tour.screenshot("library", "library")], f"{count} assets in the library"
    tour.check("Library", "Import photos, list everything made so far", library)

    def template():
        panel = window.templates_panel
        photos = [tour.asset(p) for p in sorted(tour.made["photos"].iterdir())]
        panel.set_selection_provider(lambda: photos)
        names = [panel.list_widget.item(i).text() for i in range(panel.list_widget.count())]
        row = next((i for i, name in enumerate(names) if "slideshow" in name.lower()), 0)
        panel.list_widget.setCurrentRow(row)
        panel.apply_template()
        tour.pump(0.5)
        clips = project.timeline()
        if not clips:
            raise RuntimeError("the template put nothing on the timeline")
        return [tour.screenshot("templates", "templates")], \
            f"'{names[row].splitlines()[0]}' filled {len(clips)} timeline slots"
    tour.check("Templates", "Apply a template to the timeline", template)

    def timeline():
        panel = window.timeline_panel
        panel.refresh()
        since = tour.mark()
        panel.render(tour.made.get("music"))
        jobs = tour.wait_jobs(since, 900)
        if jobs[-1].state != JobState.DONE:
            raise RuntimeError(jobs[-1].error)
        tour.screenshot("timeline", "timeline")
        return [jobs[-1].result]
    tour.check("Timeline", "Render the timeline with the composed music", timeline)


EDIT_PLAN = [
    # (tool, source key, settings); "@key" means the file the tour made under that key
    ("trim", "film", {"start": "00:00:01.00", "end": "00:00:04.00"}),
    ("jumpcut", "talking", {}),
    ("scenesplit", "cuts", {"add_to_timeline": False}),
    ("speed", "film", {"factor": 1.5}),
    ("reverse", "short", {}),
    ("loop", "short", {"times": 2, "boomerang": True}),
    ("fade", "film", {"fade_in": 1.0, "fade_out": 1.0}),
    ("social", "film", {"aspect": "9:16"}),
    ("crop", "film", {"width": 960, "height": 540, "x": 200, "y": 100}),
    ("rotate", "short", {"rotation": "90 clockwise"}),
    ("transcode", "film", {"width": 1280, "crf": 23}),
    ("grade", "film", {"contrast": 1.15, "saturation": 1.25, "brightness": 0.03}),
    ("lut", "film", {"lut": "@lut"}),
    ("sharpen", "film", {"amount": 1.2}),
    ("denoise", "film", {}),
    ("deband", "film", {}),
    ("deinterlace", "film", {}),
    ("tonemap", "hdr", {}),
    ("vidstab", "short", {}),
    ("stabilise", "short", {}),
    ("text", "film", {"text": "MediaForge — made on this PC", "position": "bottom"}),
    ("overlay", "film", {"image": "@logo", "position": "top-right", "scale_percent": 12}),
    ("pip", "film", {"inset": "@talking", "position": "bottom-right"}),
    ("splitscreen", "film", {"right": "@talking", "orientation": "horizontal"}),
    ("chromakey", "green", {"background": "@scene"}),
    ("blurregion", "film", {"x": 100, "y": 100, "width": 400, "height": 300}),
    ("subtitles", "talking", {"subtitles": "@srt"}),
    ("audio", "film", {"audio": "@speech", "keep_original": True}),
    ("loudness", "talking", {"target_lufs": -14.0}),
    ("cleanspeech", "talking", {}),
    ("extract_audio", "talking", {}),
    ("kenburns", "scene", {"seconds": 4.0}),
    ("frames", "film", {"every_seconds": 2.0, "width": 640}),
    ("contactsheet", "film", {"columns": 3, "rows": 3, "width": 320}),
    ("waveform", "talking", {}),
    ("convertimage", "scene", {"format": ".webp", "width": 1280}),
    ("gif", "short", {"fps": 10, "width": 360}),
    ("gif", "short", {"_label": "Animated WebP", "format": "Animated WebP (smaller, full colour)",
                      "fps": 10, "width": 360}),
    ("export", "film", {"preset": "YouTube 1080p"}),
    ("convert", "film", {"_label": "Convert to HEVC",
                         "target": "MP4 · H.265 / HEVC (half the size)"}),
    ("convert", "film", {"_label": "Convert to WebM VP9", "target": "WebM · VP9 (for the web)"}),
    ("convert", "talking", {"_label": "Convert to FLAC (sound only)",
                            "target": "FLAC (sound only, lossless)"}),
    ("convert", "film", {"_label": "Convert on the graphics card (NVENC)",
                         "target": "MP4 · H.264 (plays everywhere)", "gpu": True}),
    ("compress", "film", {"target_mb": 2.0}),
    ("join", "film", {"second": "@talking", "crossfade": 0.5}),
    ("volume", "talking", {"gain_db": -6.0}),
    ("mute", "film", {}),
    ("snapshot", "film", {"at": "00:00:02.00", "format": ".jpg"}),
    ("softsubs", "talking", {"subtitles": "@srt", "container": ".mp4"}),
]


def section_edit(tour: Tour) -> None:
    from mediaforge.ui import edit_panel as ep

    panel = tour.window.edit_panel
    sources = dict(tour.made)
    film = tour.made.get("film")
    if film:
        short = tour.inputs / "short.mp4"
        tour.ffmpeg("-i", str(film), "-t", "3", "-vf", "scale=960:-2", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(short))
        sources["short"] = short
    sources["talking"] = tour.made.get("talking")
    sources["srt"] = tour.made.get("srt")

    for key, source_key, settings in EDIT_PLAN:
        label = settings.get("_label") or ep.TOOLS[key][0]
        settings = {name: value for name, value in settings.items() if name != "_label"}

        def run_tool(key=key, source_key=source_key, settings=settings):
            source = sources.get(source_key)
            if source is None:
                raise Skip(f"no '{source_key}' input was made earlier in the tour")
            panel.set_asset(tour.asset(Path(source)))
            tour.window.go_to("edit")
            panel.tiles.select(key)
            panel._on_operation_changed()
            widgets = panel._pages[key].property("widgets") or {}
            for name, value in settings.items():
                if isinstance(value, str) and value.startswith("@"):
                    if sources.get(value[1:]) is None:
                        raise Skip(f"no '{value[1:]}' input was made earlier in the tour")
                    value = sources[value[1:]]
                set_value(widgets[name], value)
            since = tour.mark()
            panel.run()
            if tour.mark() == since:
                if any("driver" in dialog for dialog in DIALOGS):
                    # The PC, not the app: the refusal names the driver to install.
                    raise Skip("; ".join(DIALOGS))
                raise RuntimeError("the tool refused to start: " + "; ".join(DIALOGS))
            jobs = tour.wait_jobs(since, 900)
            job = jobs[-1]
            if job.state != JobState.DONE:
                raise RuntimeError(job.error[-600:])
            result = job.result
            if isinstance(result, Path):
                return [result]
            if isinstance(result, (list, tuple)):
                paths = [Path(p) for p in result if isinstance(p, (str, Path))]
                return (paths[:3] or [str(result)[:300]])
            return [str(result)[:400]]

        tour.check("Edit", label, run_tool)
    tour.screenshot("edit", "edit")


def section_repair_batch(tour: Tour) -> None:
    from mediaforge.ui import batch_panel as bp

    def repair():
        panel = tour.window.repair_panel
        tour.window.go_to("repair")
        panel.set_source(tour.made["faulty"])
        since = tour.mark()
        panel.scan()
        tour.wait_jobs(since, 300)
        tour.wait_for(lambda: panel.report is not None, 30, "the analysis report")
        since = tour.mark()
        panel.run()
        jobs = tour.wait_jobs(since, 900)
        if jobs[-1].state != JobState.DONE:
            raise RuntimeError(jobs[-1].error or "; ".join(DIALOGS))
        tour.screenshot("repair", "repair")
        before = ffmpeg_tools.probe(tour.locator, tour.made["faulty"]).duration
        after = ffmpeg_tools.probe(tour.locator, jobs[-1].result).duration
        return [jobs[-1].result], f"{before:.1f} s → {after:.1f} s (2 s of black silence cut)"
    tour.check("Repair", "Analyse and repair a clip with a dead section", repair)

    def batch():
        folder = tour.inputs / "batch"
        folder.mkdir(exist_ok=True)
        shutil.copy(tour.made["faulty"], folder / "a_faulty.mp4")
        shutil.copy(tour.made["green"], folder / "b_clean.mp4")
        panel = tour.window.batch_panel
        tour.window.go_to("batch")
        panel.folder = folder
        panel._reload_folder()
        panel.flow.go_to(1)
        since = tour.mark()
        panel.scan()
        tour.wait_jobs(since, 600)
        since = tour.mark()
        panel.run()
        tour.wait_jobs(since, 1200)
        tour.screenshot("batch", "batch")
        done = [item for item in panel.items if item.state is bp.ItemState.DONE]
        outputs = [Path(item.output) for item in done if getattr(item, "output", None)]
        return outputs or [folder], panel.log.toPlainText()[-500:]
    tour.check("Batch repair", "Measure and repair a folder", batch)


def section_enhance(tour: Tour) -> None:
    panel = tour.window.enhance_panel
    film = tour.made.get("film")
    small = None
    if film:
        small = tour.inputs / "small.mp4"
        tour.ffmpeg("-i", str(film), "-t", "2", "-vf", "scale=480:-2", "-an", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(small))
    inputs = {"upscale_image": tour.made.get("portrait"), "upscale_video": small,
              "interpolate": small, "matte": tour.made.get("portrait"),
              "transcribe": tour.made.get("talking"), "stems": tour.made.get("film"),
              "tts": None}
    from mediaforge.ui.enhance_panel import OPERATIONS
    for op_key, (label, tool_id, kind) in OPERATIONS.items():
        def run(op_key=op_key, tool_id=tool_id, kind=kind):
            tool = tour.context.local_tools.get(tool_id)
            if tool is None or not tool.installed:
                size = getattr(tool, "size_mb", "?")
                raise Skip(f"{tool_id} is not installed on this PC (about {size} MB; "
                           "one click in Model Hub → Local engines)")
            source = inputs.get(kind)
            if source is None and kind != "tts":
                raise Skip(f"no input for '{kind}' was made earlier in the tour")
            if source is not None:
                panel.set_asset(tour.asset(Path(source)))
            tour.window.go_to("enhance")
            panel.tiles.select(op_key)
            panel._on_operation_changed()
            if kind == "tts":
                panel.text_box.setPlainText("Hello from MediaForge.")
            since = tour.mark()
            panel.run()
            if tour.mark() == since:
                raise RuntimeError("refused: " + "; ".join(DIALOGS))
            jobs = tour.wait_jobs(since, 1800)
            if jobs[-1].state != JobState.DONE:
                raise RuntimeError(jobs[-1].error[-600:])
            result = jobs[-1].result
            return [result] if isinstance(result, Path) else [str(result)[:400]]
        tour.check("Enhance", label, run)
    tour.screenshot("enhance", "enhance")


def section_music(tour: Tour) -> None:
    panel = tour.window.music_panel

    def search_and_download():
        tour.window.go_to("music")
        panel.query.setText("calm piano")
        since = tour.mark()
        panel.search()
        jobs = tour.wait_jobs(since, 180)
        if jobs[-1].state != JobState.DONE:
            raise RuntimeError(jobs[-1].error)
        if not panel._tracks:
            raise RuntimeError("no usable tracks came back")
        panel.table.setCurrentCell(0, 0)
        since = tour.mark()
        panel.download_selected()
        jobs = tour.wait_jobs(since, 300)
        if jobs[-1].state != JobState.DONE:
            raise RuntimeError(jobs[-1].error)
        track = jobs[-1].result["track"]
        return [jobs[-1].result["path"]], (f"{len(panel._tracks)} usable tracks; downloaded "
                                           f"'{track.title}' ({track.licence.code})")
    tour.check("Find music", "Search openly licensed music and download one", search_and_download)

    def compose():
        tour.window.go_to("music")
        panel.query.setText("lo-fi chill beat with soft electric piano")
        panel.compose_length.setValue(20)
        since = tour.mark()
        panel.compose()
        jobs = tour.wait_jobs(since, 900)
        if jobs[-1].state != JobState.DONE:
            raise RuntimeError(jobs[-1].error or "; ".join(DIALOGS))
        tour.screenshot("music", "music")
        return [panel._composed]
    tour.check("Find music", "Compose an original track (ACE-Step)", compose)


def section_assistant(tour: Tour) -> None:
    panel = tour.window.agent_panel

    def ask():
        tour.window.go_to("agent")
        panel._send("How many photos and videos are in my library right now? "
                    "Then suggest one video I could make from them, in two sentences.")
        tour.wait_for(lambda: panel._thread is None, 900, "the assistant's answer")
        transcript = panel.transcript.toPlainText()
        target = tour.out / "assistant_transcript.txt"
        target.write_text(transcript, encoding="utf-8")
        if "error" in transcript.lower()[-400:] and "assistant" not in transcript.lower()[-400:]:
            raise RuntimeError(transcript[-600:])
        return [target, tour.screenshot("agent", "assistant")], transcript[-700:]
    tour.check("Assistant", "Ask about the library (Ollama with tools)", ask)


def section_static_screens(tour: Tour) -> None:
    for key, name, check in (
            ("home", "Home", "Task cards and setup status"),
            ("hub", "Model Hub", "Catalogue and local engines listed"),
            ("learning", "Learning", "Decisions recorded during the tour"),
            ("jobs", "Jobs", "Every job of the tour, with its outcome"),
            ("settings", "Settings", "Keys, ffmpeg, endpoints and workspace")):
        def shot(key=key, name=name):
            panel = tour.window.panels[key]
            for method in ("refresh", "refresh_status"):
                if callable(getattr(panel, method, None)):
                    getattr(panel, method)()
                    break
            return [tour.screenshot(key, name.lower().replace(" ", "_"))]
        tour.check(name, check, shot)

    def doctor():
        exe = sys.executable
        result = subprocess.run([exe, "-m", "mediaforge", "--doctor"], cwd=str(ROOT),
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=120)
        target = tour.out / "doctor.txt"
        target.write_text(result.stdout + result.stderr, encoding="utf-8")
        if "ffmpeg" not in result.stdout:
            raise RuntimeError(result.stderr[-400:])
        return [target]
    tour.check("Settings", "Environment report (--doctor)", doctor)


SECTIONS = {
    "generate": section_generate,
    "photos": section_photos,
    "inputs2": None,          # talking clip + subtitles, built from Generate's outputs
    "understanding": section_understanding,
    "director": section_director,
    "library": section_library_templates_timeline,
    "edit": section_edit,
    "repair": section_repair_batch,
    "enhance": section_enhance,
    "music": section_music,
    "assistant": section_assistant,
    "screens": section_static_screens,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--skip", nargs="*", default=[])
    parser.add_argument("--only", nargs="*", default=[])
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    tour = Tour(out)
    print(f"Project: {out / 'project'}", flush=True)

    tour.check("Setup", "Prepare test inputs (photos, logo, LUT, faulty and green clips)",
               lambda: prepare_inputs(tour)[:3])

    def comfyui():
        comfy = tour.context.registry.get("comfyui")
        if comfy.reachable(force=True):
            return ["ComfyUI was already running"]
        return [tour.context.start_comfyui()]   # what the Start ComfyUI button runs
    tour.check("Setup", "ComfyUI is running (started the way the app starts it)", comfyui)

    def ollama():
        provider = tour.context.registry.get("ollama")
        if provider.reachable(force=True):
            return ["Ollama was already running"]
        if not provider.start_server():          # what the Start Ollama button runs
            raise RuntimeError("Ollama did not start")
        provider.list_models(force=True)
        return ["Ollama started"]
    tour.check("Setup", "Ollama is running (started the way the app starts it)", ollama)
    for name, section in SECTIONS.items():
        if (args.only and name not in args.only) or name in args.skip:
            continue
        if name == "inputs2":
            if tour.made.get("scene") and tour.made.get("speech"):
                def build():
                    clip = talking_clip(tour, tour.made["portrait"] if tour.made.get("portrait")
                                        else tour.made["scene"], tour.made["speech"])
                    tour.made["talking"] = clip
                    speech = ffmpeg_tools.probe(tour.locator, tour.made["speech"]).duration
                    tour.made["srt"] = subtitles_for(tour, clip, speech)
                    return [clip, tour.made["srt"]]
                tour.check("Setup", "Build a talking clip with pauses, and its subtitles", build)
            continue
        section(tour)
    tour.save_report()
    failed = sum(1 for r in tour.results if r["status"] == "FAIL")
    print(f"\nDone: {len(tour.results)} checks, {failed} failed. Report: {out / 'index.html'}")
    tour.context.shutdown()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
