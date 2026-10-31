"""Edit panel: one ffmpeg operation on one file, as four steps.

File, tool, settings, result. The tools are tiles grouped by what they do to the
picture, because forty operations in one dropdown is a list nobody reads. The result
reports in place, and can be handed straight back in as the next source -- trim, then
grade, then add a title, without a trip through the Library between each.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from .. import ffmpeg_extra, ffmpeg_tools
from ..context import AppContext
from ..jobs import Job, JobState
from ..project import Asset
from .stepper import Step, StepFlow
from .widgets import (
    Card, FilePicker, JobStatusCard, MediaPreview, TileGrid, centred_page, show_in_stack,
)
from . import icons, theme

# (heading, [(key, tile label, glyph, what it does)])
TOOL_GROUPS: list[tuple[str, list[tuple[str, str, str, str]]]] = [
    ("Cut & time", [
        ("trim", "Trim", "trim", "Keep one stretch of the clip, from a start to an end time."),
        ("jumpcut", "Remove silence", "silence",
         "Cuts out the pauses in speech, the way a jump-cut edit does by hand."),
        ("scenesplit", "Split by scene", "scenes",
         "Finds every cut in the footage and saves each scene as its own clip."),
        ("speed", "Speed", "speed", "Faster or slower, with the pitch of the audio kept."),
        ("reverse", "Reverse", "reverse", "Plays the clip backwards."),
        ("loop", "Loop", "loop", "Repeats the clip, or plays it forward then back."),
        ("fade", "Fade in / out", "fade", "Fades picture and sound from and to black."),
    ]),
    ("Frame", [
        ("social", "Reframe for social", "phone",
         "Vertical, square or 4:5, with a blurred fill instead of black bars."),
        ("crop", "Crop", "crop", "Keeps one rectangle of the frame."),
        ("rotate", "Rotate / flip", "rotate", "Turns a sideways phone clip upright, or mirrors it."),
        ("transcode", "Resize / convert", "convert", "Changes size, frame rate, codec or quality."),
    ]),
    ("Look", [
        ("grade", "Colour", "palette", "Brightness, contrast, saturation and gamma."),
        ("lut", "Apply LUT", "layers", "Applies a .cube colour look from a camera or grading app."),
        ("sharpen", "Sharpen", "sharpen", "Brings back edge detail in soft footage."),
        ("denoise", "Reduce noise", "sparkle", "Removes grain from low-light footage."),
        ("deband", "Fix banding", "deband", "Smooths the steps in skies and gradients."),
        ("deinterlace", "Deinterlace", "lines",
         "Removes the comb lines from old TV or camcorder video."),
        ("tonemap", "HDR to SDR", "hdr", "Makes an HDR phone clip look right on ordinary screens."),
    ]),
    ("Stabilise", [
        ("vidstab", "Stabilise (best)", "camera",
         "Two passes: measures the camera shake, then smooths the path."),
        ("stabilise", "Stabilise (fast)", "camera", "One pass. Quicker, less smooth."),
    ]),
    ("Layer", [
        ("text", "Title / text", "text", "Writes a title or caption onto the picture."),
        ("overlay", "Logo / watermark", "stamp", "Places an image, such as a logo, over the video."),
        ("pip", "Picture in picture", "pip", "A second video in a corner of the first."),
        ("splitscreen", "Split screen", "split", "Two videos side by side or stacked."),
        ("chromakey", "Green screen", "key", "Replaces a green or blue background."),
        ("blurregion", "Blur an area", "blur", "Hides a face, a number plate or a screen."),
        ("subtitles", "Burn subtitles", "captions",
         "Writes an .srt file permanently into the picture."),
    ]),
    ("Sound", [
        ("audio", "Add music", "music", "Adds or replaces the soundtrack."),
        ("loudness", "Loudness", "volume", "Sets the level to a broadcast or streaming target."),
        ("cleanspeech", "Clean up voice", "mic", "Gates, de-noises and evens out speech."),
        ("extract_audio", "Extract audio", "headphones", "Saves the sound as an MP3."),
    ]),
    ("Stills & export", [
        ("kenburns", "Photo to video", "photo", "Turns a still into a slow zoom shot."),
        ("frames", "Extract frames", "frames", "Saves a still every few seconds."),
        ("contactsheet", "Contact sheet", "grid", "One image with a grid of frames from the clip."),
        ("waveform", "Waveform image", "wave", "A picture of the soundtrack's shape."),
        ("convertimage", "Convert image", "image", "Changes an image's format or size."),
        ("gif", "GIF", "gif", "A small looping GIF for chat or the web."),
        ("export", "Export for…", "export",
         "Ready-made settings for YouTube, Instagram, TikTok and more."),
    ]),
]
TOOLS = {key: (label, glyph, tip) for _, entries in TOOL_GROUPS
         for key, label, glyph, tip in entries}
STEP_FILE, STEP_TOOL, STEP_SETTINGS, STEP_RESULT = range(4)


class EditPanel(QWidget):
    status = Signal(str)
    library_changed = Signal()

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self.asset: Asset | None = None
        self._build()
        self._on_operation_changed()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.flow = StepFlow([
            Step("file", "File", "Pick the clip or image to edit."),
            Step("tool", "Tool", "Choose what to do to it."),
            Step("settings", "Settings", "Adjust, then continue."),
            Step("result", "Result", "Run it and check what came out."),
        ], finish_label="Run")

        # ---- step 1: the file ---------------------------------------------------------
        file_page = QWidget()
        layout = QVBoxLayout(file_page)
        layout.setSpacing(theme.SPACE_3)
        source_card = Card("Source")
        source_row = QHBoxLayout()
        self.source_label = QLabel("Nothing selected. Choose a file, or send one here "
                                   "from the Library.")
        self.source_label.setWordWrap(True)
        self.source_label.setTextFormat(Qt.TextFormat.RichText)
        source_row.addWidget(self.source_label, 1)
        pick_button = QPushButton("Choose file")
        pick_button.setObjectName("tonal")
        icons.button(pick_button, "folder", "@ACCENT")
        pick_button.clicked.connect(self._pick_file)
        source_row.addWidget(pick_button, 0, Qt.AlignmentFlag.AlignTop)
        source_card.body.addLayout(source_row)
        layout.addWidget(source_card)
        self.preview = MediaPreview()
        self.preview.setMinimumHeight(300)
        layout.addWidget(self.preview, 1)
        self.flow.add_page(centred_page(file_page))

        # ---- step 2: the tool ---------------------------------------------------------
        tool_page = QWidget()
        layout = QVBoxLayout(tool_page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tiles = TileGrid(TOOL_GROUPS)
        self.tiles.chosen.connect(self._on_tile_chosen)
        layout.addWidget(self.tiles)
        layout.addStretch(1)
        self.flow.add_page(centred_page(tool_page, 1080))

        # ---- step 3: its settings -----------------------------------------------------
        settings_page = QWidget()
        layout = QVBoxLayout(settings_page)
        layout.setSpacing(theme.SPACE_3)
        heading_row = QHBoxLayout()
        heading_row.setSpacing(theme.SPACE_2)
        self.tool_glyph = QLabel()
        heading_row.addWidget(self.tool_glyph)
        self.tool_title = QLabel("")
        self.tool_title.setObjectName("h2")
        heading_row.addWidget(self.tool_title)
        heading_row.addStretch(1)
        change_button = QPushButton("Change tool")
        change_button.setObjectName("text")
        change_button.clicked.connect(lambda: self.flow.go_to(STEP_TOOL))
        heading_row.addWidget(change_button)
        layout.addLayout(heading_row)
        self.tool_hint = QLabel("")
        self.tool_hint.setObjectName("subtle")
        self.tool_hint.setWordWrap(True)
        layout.addWidget(self.tool_hint)

        parameters_card = Card("Settings")
        self.parameter_stack = QStackedWidget()
        self._pages: dict[str, QWidget] = {}
        for key in TOOLS:
            page = self._build_page(key)
            self._pages[key] = page
            self.parameter_stack.addWidget(page)
        parameters_card.body.addWidget(self.parameter_stack)
        layout.addWidget(parameters_card)
        layout.addStretch(1)
        self.flow.add_page(centred_page(settings_page, 760))

        # ---- step 4: the result -------------------------------------------------------
        result_page = QWidget()
        layout = QVBoxLayout(result_page)
        layout.setSpacing(theme.SPACE_3)
        self.job_card = JobStatusCard("Output")
        self.job_card.cancel_requested.connect(self.context.jobs.cancel)
        again = QPushButton("Edit the result")
        again.setObjectName("tonal")
        icons.button(again, "edit", "@ACCENT")
        again.setToolTip("Use what just came out as the source for another tool.")
        again.clicked.connect(self._continue_from_result)
        self.job_card.add_action(again)
        layout.addWidget(self.job_card)
        self.result_preview = MediaPreview()
        self.result_preview.setMinimumHeight(280)
        layout.addWidget(self.result_preview, 1)
        self.command_preview = QLabel("")
        self.command_preview.setWordWrap(True)
        self.command_preview.setObjectName("mono")
        self.command_preview.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.command_preview)
        self.flow.add_page(centred_page(result_page))

        outer.addWidget(self.flow, 1)
        self.flow.step_changed.connect(self._step_changed)
        self.flow.finished.connect(self.run)
        self.tiles.select("trim")
        self.flow.go_to(STEP_FILE)

    def _step_changed(self, index: int) -> None:
        key = self.tiles.current()
        if index == STEP_FILE:
            self.flow.set_status(self.asset.label if self.asset else "Pick a file to edit.")
            self.flow.set_next_enabled(self.asset is not None)
        elif index == STEP_TOOL:
            self.flow.set_status(f"Selected: {TOOLS[key][0]}" if key else "Choose a tool.")
            self.flow.set_next_enabled(bool(key))
        elif index == STEP_SETTINGS:
            self.flow.set_status("")
            self.flow.set_next_enabled(True)
        else:
            self.flow.set_next_enabled(self.asset is not None)
            if self.job_card.job_id is None and self.asset is not None and key:
                self.job_card.show_ready(
                    f"<b>{TOOLS[key][0]}</b> on <b>{self.asset.path.name}</b>. "
                    "The original is never changed; the result is saved to the "
                    "project's renders folder.")
                self.flow.set_status("Press Run to start.")

    def _on_tile_chosen(self, key: str) -> None:
        self._on_operation_changed()
        self.flow.go_to(STEP_SETTINGS)

    # -- parameter pages ----------------------------------------------------------------
    def _build_page(self, key: str) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        widgets: dict[str, QWidget] = {}

        def add_double(name: str, label: str, value: float, low: float, high: float,
                       step: float = 0.1) -> None:
            box = QDoubleSpinBox()
            box.setRange(low, high)
            box.setSingleStep(step)
            box.setDecimals(3)
            box.setValue(value)
            form.addRow(label, box)
            widgets[name] = box

        def add_int(name: str, label: str, value: int, low: int, high: int) -> None:
            box = QSpinBox()
            box.setRange(low, high)
            box.setValue(value)
            form.addRow(label, box)
            widgets[name] = box

        def add_choice(name: str, label: str, choices: list[str], default: str = "") -> None:
            box = QComboBox()
            box.addItems(choices)
            if default:
                box.setCurrentText(default)
            form.addRow(label, box)
            widgets[name] = box

        def add_file(name: str, label: str, filter_spec: str) -> None:
            picker = FilePicker("", filter_spec)
            form.addRow(label, picker)
            widgets[name] = picker

        def add_check(name: str, label: str, value: bool) -> None:
            box = QCheckBox()
            box.setChecked(value)
            form.addRow(label, box)
            widgets[name] = box

        if key == "trim":
            start = QLineEdit("00:00:00.00")
            end = QLineEdit("00:00:05.00")
            form.addRow("Start (hh:mm:ss)", start)
            form.addRow("End (hh:mm:ss)", end)
            widgets["start"], widgets["end"] = start, end
            add_check("reencode", "Re-encode (accurate cut)", True)
        elif key == "social":
            add_choice("aspect", "Aspect", ["9:16", "1:1", "4:5", "16:9"], "9:16")
            add_check("blur_pad", "Blurred background fill", True)
        elif key == "transcode":
            add_int("width", "Width (0 = keep)", 1920, 0, 7680)
            add_int("height", "Height (0 = auto)", 0, 0, 4320)
            add_int("fps", "FPS (0 = keep)", 0, 0, 120)
            add_int("crf", "Quality CRF (lower = better)", 20, 0, 51)
            add_choice("codec", "Codec", ["libx264", "libx265", "libvpx-vp9"], "libx264")
        elif key == "grade":
            add_double("brightness", "Brightness (-1..1)", 0.0, -1.0, 1.0, 0.05)
            add_double("contrast", "Contrast (0..3)", 1.0, 0.0, 3.0, 0.05)
            add_double("saturation", "Saturation (0..3)", 1.0, 0.0, 3.0, 0.05)
            add_double("gamma", "Gamma (0.1..3)", 1.0, 0.1, 3.0, 0.05)
        elif key == "speed":
            add_double("factor", "Speed factor", 1.5, 0.1, 20.0, 0.1)
        elif key == "audio":
            add_file("audio", "Audio file", "Audio (*.mp3 *.wav *.m4a *.aac)")
            add_double("volume", "Music volume", 1.0, 0.0, 3.0, 0.05)
            add_check("keep_original", "Keep original audio underneath", False)
        elif key == "subtitles":
            add_file("subtitles", "Subtitle file", "Subtitles (*.srt *.ass *.vtt)")
            add_int("font_size", "Font size", 28, 8, 120)
            add_int("margin_v", "Bottom margin", 60, 0, 400)
        elif key == "overlay":
            add_file("image", "Overlay image", "Images (*.png *.jpg *.webp)")
            add_choice("position", "Position",
                       ["bottom-right", "bottom-left", "top-right", "top-left", "center"])
            add_int("scale_percent", "Size (% of frame width)", 20, 1, 100)
            add_double("opacity", "Opacity", 1.0, 0.0, 1.0, 0.05)
        elif key == "frames":
            add_double("every_seconds", "One frame every N seconds", 1.0, 0.04, 60.0, 0.25)
            add_int("width", "Frame width", 1280, 64, 7680)
        elif key == "gif":
            add_int("fps", "FPS", 12, 4, 30)
            add_int("width", "Width", 480, 64, 1920)
        elif key == "kenburns":
            add_double("seconds", "Duration (seconds)", 5.0, 0.5, 60.0, 0.5)
            add_int("fps", "FPS", 30, 12, 60)
            add_int("width", "Width", 1920, 320, 3840)
            add_int("height", "Height", 1080, 240, 2160)
            add_double("zoom_to", "Zoom to", 1.15, 1.0, 2.0, 0.01)
        elif key == "jumpcut":
            add_int("threshold_db", "Silence threshold (dB)", -35, -70, -10)
            add_double("min_len", "Minimum silence length (s)", 0.6, 0.1, 5.0, 0.1)
            add_double("padding", "Keep padding around speech (s)", 0.08, 0.0, 1.0, 0.02)
        elif key == "sharpen":
            add_double("amount", "Amount", 1.0, 0.0, 5.0, 0.1)
            add_int("radius", "Radius (odd)", 5, 3, 23)
        elif key == "denoise":
            add_choice("method", "Method", list(ffmpeg_extra.DENOISE_METHODS.keys()))
            add_double("strength", "Strength", 3.0, 0.1, 20.0, 0.5)
        elif key == "deband":
            add_double("strength", "Threshold", 0.02, 0.001, 0.2, 0.005)
        elif key == "deinterlace":
            add_check("double_rate", "Double the frame rate (50i -> 50p)", False)
        elif key == "tonemap":
            form.addRow(QLabel("Converts HDR/BT.2020 to SDR/BT.709 with Hable tonemapping."))
        elif key == "vidstab":
            add_int("shakiness", "Shakiness (1 calm .. 10 wild)", 5, 1, 10)
            add_int("accuracy", "Accuracy (1..15)", 15, 1, 15)
            add_int("smoothing", "Smoothing (frames)", 30, 1, 200)
            add_double("zoom", "Extra zoom %", 0.0, -20.0, 20.0, 1.0)
        elif key == "crop":
            add_int("width", "Width", 1280, 2, 7680)
            add_int("height", "Height", 720, 2, 4320)
            add_int("x", "X offset", 0, 0, 7680)
            add_int("y", "Y offset", 0, 0, 4320)
        elif key == "rotate":
            add_choice("rotation", "Rotate",
                       ["none", "90 clockwise", "90 counter-clockwise", "180"], "90 clockwise")
            add_choice("flip", "Flip", ["none", "horizontal", "vertical"], "none")
        elif key == "fade":
            add_double("fade_in", "Fade in (s)", 0.5, 0.0, 10.0, 0.1)
            add_double("fade_out", "Fade out (s)", 0.5, 0.0, 10.0, 0.1)
            add_check("audio_too", "Fade audio as well", True)
        elif key == "reverse":
            add_check("with_audio", "Reverse audio too", True)
        elif key == "loop":
            add_int("times", "Repeat count", 3, 2, 50)
            add_check("boomerang", "Boomerang (forward then back)", False)
        elif key == "lut":
            add_file("lut", "LUT file", "LUT (*.cube *.3dl)")
        elif key == "text":
            title = QLineEdit("Your title here")
            form.addRow("Text", title)
            widgets["text"] = title
            add_choice("position", "Position",
                       ["bottom", "top", "center", "top-left", "bottom-left"], "bottom")
            add_int("font_size", "Font size", 48, 8, 200)
            add_choice("colour", "Colour", ["white", "black", "yellow", "red", "#2f6feb"])
            add_check("box", "Dark box behind text", True)
            add_double("start", "Show from (s, 0 = always)", 0.0, 0.0, 3600.0, 0.5)
            add_double("end", "Show until (s, 0 = always)", 0.0, 0.0, 3600.0, 0.5)
        elif key == "pip":
            add_file("inset", "Inset video", "Video (*.mp4 *.mov *.mkv *.webm)")
            add_choice("position", "Position",
                       ["bottom-right", "bottom-left", "top-right", "top-left"])
            add_int("scale_percent", "Inset size (%)", 30, 5, 90)
            add_int("border", "Border (px)", 3, 0, 30)
        elif key == "splitscreen":
            add_file("right", "Second video", "Video (*.mp4 *.mov *.mkv *.webm)")
            add_choice("orientation", "Layout", ["horizontal", "vertical"])
            add_int("width", "Output width", 1920, 320, 7680)
            add_int("height", "Output height", 1080, 240, 4320)
        elif key == "chromakey":
            add_file("background", "Background image or video", "Media (*.*)")
            add_choice("key_colour", "Key colour", ["0x00FF00", "0x0000FF", "0x00B140"])
            add_double("similarity", "Similarity", 0.30, 0.01, 1.0, 0.01)
            add_double("blend", "Edge blend", 0.10, 0.0, 1.0, 0.01)
        elif key == "blurregion":
            add_int("x", "X", 0, 0, 7680)
            add_int("y", "Y", 0, 0, 4320)
            add_int("width", "Width", 320, 8, 7680)
            add_int("height", "Height", 240, 8, 4320)
            add_int("strength", "Blur strength", 20, 1, 100)
        elif key == "loudness":
            add_double("target_lufs", "Target LUFS", -14.0, -30.0, -5.0, 0.5)
        elif key == "cleanspeech":
            add_int("gate_db", "Noise gate (dB)", -30, -70, -10)
        elif key == "contactsheet":
            add_int("columns", "Columns", 4, 1, 10)
            add_int("rows", "Rows", 4, 1, 10)
            add_int("width", "Cell width", 480, 80, 1920)
        elif key == "waveform":
            add_int("width", "Width", 1600, 200, 4000)
            add_int("height", "Height", 400, 100, 1200)
        elif key == "convertimage":
            add_choice("format", "Format", [".png", ".jpg", ".webp", ".bmp", ".tiff"], ".png")
            add_int("width", "Width (0 = keep)", 0, 0, 12000)
            add_int("height", "Height (0 = auto)", 0, 0, 12000)
            add_int("quality", "Quality", 92, 1, 100)
        elif key == "export":
            add_choice("preset", "Preset", list(ffmpeg_extra.EXPORT_PRESETS.keys()),
                       "YouTube 1080p")
            add_choice("hardware", "Hardware encoder", ["none", "nvidia", "amd", "intel"], "none")
        elif key == "scenesplit":
            add_double("threshold", "Scene threshold (0.1 sensitive .. 0.6 strict)",
                       0.30, 0.05, 0.9, 0.05)
            add_check("add_to_timeline", "Add the resulting clips to the timeline", True)
        elif key in ("stabilise", "extract_audio"):
            form.addRow(QLabel("No parameters."))

        page.setProperty("widgets", widgets)
        return page

    def _page_values(self, key: str) -> dict:
        widgets: dict[str, QWidget] = self._pages[key].property("widgets") or {}
        values = {}
        for name, widget in widgets.items():
            if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                values[name] = widget.value()
            elif isinstance(widget, QComboBox):
                values[name] = widget.currentText()
            elif isinstance(widget, QCheckBox):
                values[name] = widget.isChecked()
            elif isinstance(widget, FilePicker):
                values[name] = widget.path()
            elif isinstance(widget, QLineEdit):
                values[name] = widget.text()
        return values

    def _on_operation_changed(self) -> None:
        key = self.tiles.current()
        if key and key in self._pages:
            show_in_stack(self.parameter_stack, self._pages[key])
            label, glyph, tip = TOOLS[key]
            self.tool_title.setText(label)
            self.tool_hint.setText(tip)
            self.tool_glyph.setPixmap(icons.pixmap(glyph, "@ACCENT", 22))
        self.command_preview.setText("")
        self.job_card.show_ready("")

    # -- source ---------------------------------------------------------------------------
    def set_asset(self, asset: Asset) -> None:
        self.asset = asset
        self.source_label.setText(
            f"<b>{asset.label}</b><br>"
            f"<span style='color:{theme.TEXT_FAINT}'>{asset.path}</span><br>"
            f"{asset.kind} &middot; {asset.resolution} &middot; "
            f"{ffmpeg_tools.format_timecode(asset.duration)}"
        )
        self.preview.show_path(asset.path)
        if asset.duration:
            widgets = self._pages["trim"].property("widgets")
            widgets["end"].setText(ffmpeg_tools.format_timecode(asset.duration))
        self.job_card.show_ready("")
        self._step_changed(self.flow.current())

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose media", "", "Media (*.*)")
        if not path:
            return
        if self.context.project is None:
            QMessageBox.information(self, "No project", "Create or open a project first.")
            return
        asset_id = self.context.catalogue(Path(path), source="import")
        asset = self.context.require_project().asset(asset_id)
        if asset:
            self.set_asset(asset)
            self.library_changed.emit()

    def _continue_from_result(self) -> None:
        """Hands the finished output back in as the source for the next tool."""
        result = self.job_card.result_path
        if result is None or not result.is_file() or self.context.project is None:
            return
        asset_id = self.context.catalogue(result, source="edit")
        asset = self.context.require_project().asset(asset_id)
        if asset:
            self.set_asset(asset)
            self.flow.go_to(STEP_TOOL)

    def on_job_update(self, job: Job) -> None:
        """Progress and outcome of an edit job, shown on the Result step."""
        if not self.job_card.show_job(job):
            return
        if job.state == JobState.DONE:
            if isinstance(job.result, Path) and job.result.is_file():
                self.result_preview.show_path(job.result)
            self.library_changed.emit()
            self.flow.set_status("Done. Pick another tool, or edit the result further.")
        elif job.state in (JobState.FAILED, JobState.CANCELLED):
            self.flow.set_status("")
        self.flow.set_next_enabled(job.state not in (JobState.QUEUED, JobState.RUNNING))

    # -- run --------------------------------------------------------------------------------
    def run(self) -> None:
        if not self.context.locator.available:
            QMessageBox.warning(
                self, "ffmpeg missing",
                "ffmpeg and ffprobe were not found.\n\n"
                "Install with:  winget install Gyan.FFmpeg\n"
                "or set the path in Settings.",
            )
            return
        if self.asset is None or not self.asset.path.exists():
            QMessageBox.information(self, "No source", "Pick a file to edit first.")
            self.flow.go_to(STEP_FILE)
            return

        key = self.tiles.current()
        values = self._page_values(key)
        project = self.context.require_project()
        source = self.asset.path
        duration = self.asset.duration
        locator = self.context.locator

        def output(suffix: str, tag: str) -> Path:
            base = project.renders_dir / f"{source.stem}_{tag}{suffix}"
            counter = 1
            while base.exists():
                base = project.renders_dir / f"{source.stem}_{tag}_{counter}{suffix}"
                counter += 1
            return base

        try:
            command, extra_work = self._build_command(key, values, source, duration, output, locator)
        except (ValueError, FileNotFoundError) as error:
            QMessageBox.warning(self, "Cannot run", str(error))
            self.flow.go_to(STEP_SETTINGS)
            return

        self.command_preview.setText(command.display() if command is not None else "")
        context = self.context

        def work(job_context):
            if extra_work is not None:
                return extra_work(job_context)
            code, log = ffmpeg_tools.run_with_progress(
                command,
                on_progress=lambda fraction, label: job_context.progress(fraction, label),
                should_cancel=lambda: job_context.cancelled,
            )
            if code != 0:
                raise RuntimeError(f"ffmpeg exited with {code}:\n{log}")
            if command.output.is_file():
                context.catalogue(command.output, source="edit")
            return command.output

        label = TOOLS[key][0]
        self.flow.go_to(STEP_RESULT)
        job = self.context.jobs.submit(f"{label}: {source.name}", work, category="edit")
        self.job_card.track(job.id, f"<b>{label}</b> on <b>{source.name}</b>")
        self.flow.set_next_enabled(False)
        self.flow.set_status("")
        self.status.emit(f"Job {job.id}: {label}")

    def _build_command(self, key, values, source, duration, output, locator):
        """Returns (FfmpegCommand | None, custom_work | None)."""
        if key == "trim":
            start = ffmpeg_tools.parse_timecode(values["start"])
            end = ffmpeg_tools.parse_timecode(values["end"])
            if end <= start:
                raise ValueError("End must be after start.")
            return ffmpeg_tools.trim(locator, source, output(source.suffix, "trim"), start, end,
                                     reencode=values["reencode"]), None
        if key == "social":
            return ffmpeg_tools.social_crop(
                locator, source, output(".mp4", values["aspect"].replace(":", "x")),
                aspect=values["aspect"], blur_pad=values["blur_pad"], duration_hint=duration
            ), None
        if key == "transcode":
            return ffmpeg_tools.transcode(
                locator, source, output(".mp4", "transcode"), width=values["width"],
                height=values["height"], fps=values["fps"], crf=values["crf"],
                codec=values["codec"], duration_hint=duration
            ), None
        if key == "grade":
            return ffmpeg_tools.color_grade(
                locator, source, output(".mp4", "grade"), brightness=values["brightness"],
                contrast=values["contrast"], saturation=values["saturation"],
                gamma=values["gamma"], duration_hint=duration
            ), None
        if key == "speed":
            return ffmpeg_tools.speed_change(
                locator, source, output(".mp4", "speed"), factor=values["factor"],
                duration_hint=duration
            ), None
        if key == "stabilise":
            return ffmpeg_tools.stabilise(locator, source, output(".mp4", "stable"),
                                          duration_hint=duration), None
        if key == "audio":
            audio = Path(values["audio"])
            if not audio.is_file():
                raise FileNotFoundError("Select an audio file.")
            return ffmpeg_tools.mux_audio(
                locator, source, audio, output(".mp4", "audio"), video_duration=duration,
                music_volume=values["volume"], keep_original=values["keep_original"]
            ), None
        if key == "subtitles":
            subtitles = Path(values["subtitles"])
            if not subtitles.is_file():
                raise FileNotFoundError("Select a subtitle file.")
            return ffmpeg_tools.burn_subtitles(
                locator, source, subtitles, output(".mp4", "subbed"), duration_hint=duration,
                font_size=values["font_size"], margin_v=values["margin_v"]
            ), None
        if key == "overlay":
            image = Path(values["image"])
            if not image.is_file():
                raise FileNotFoundError("Select an overlay image.")
            return ffmpeg_tools.overlay_image(
                locator, source, image, output(".mp4", "overlay"), position=values["position"],
                scale_percent=values["scale_percent"], opacity=values["opacity"],
                duration_hint=duration
            ), None
        if key == "extract_audio":
            return ffmpeg_tools.extract_audio(locator, source, output(".mp3", "audio"),
                                              duration_hint=duration), None
        if key == "frames":
            folder = self.context.require_project().media_dir / f"{source.stem}_frames"
            return ffmpeg_tools.extract_frames(
                locator, source, folder, every_seconds=values["every_seconds"],
                width=values["width"]
            ), None
        if key == "gif":
            return ffmpeg_tools.to_gif(locator, source, output(".gif", "clip"), fps=values["fps"],
                                       width=values["width"], duration_hint=duration), None
        if key == "kenburns":
            if self.asset and self.asset.kind != "image":
                raise ValueError("Ken Burns needs a still image as the source.")
            return ffmpeg_tools.ken_burns(
                locator, source, output(".mp4", "kenburns"), seconds=values["seconds"],
                fps=values["fps"], width=values["width"], height=values["height"],
                zoom_to=values["zoom_to"]
            ), None

        if key == "sharpen":
            return ffmpeg_extra.sharpen(locator, source, output(".mp4", "sharp"),
                                        values["amount"], int(values["radius"]), duration), None
        if key == "denoise":
            return ffmpeg_extra.denoise_video(locator, source, output(".mp4", "denoise"),
                                              values["method"], values["strength"], duration), None
        if key == "deband":
            return ffmpeg_extra.deband(locator, source, output(".mp4", "deband"),
                                       values["strength"], duration), None
        if key == "deinterlace":
            return ffmpeg_extra.deinterlace(locator, source, output(".mp4", "progressive"),
                                            values["double_rate"], duration), None
        if key == "tonemap":
            return ffmpeg_extra.tonemap_hdr(locator, source, output(".mp4", "sdr"),
                                            duration), None
        if key == "crop":
            return ffmpeg_extra.crop(locator, source, output(".mp4", "crop"),
                                     int(values["width"]), int(values["height"]),
                                     int(values["x"]), int(values["y"]), duration), None
        if key == "rotate":
            return ffmpeg_extra.rotate_flip(locator, source, output(".mp4", "rotate"),
                                            values["rotation"], values["flip"], duration), None
        if key == "fade":
            return ffmpeg_extra.fade(locator, source, output(".mp4", "fade"), duration or 5.0,
                                     values["fade_in"], values["fade_out"],
                                     values["audio_too"]), None
        if key == "reverse":
            return ffmpeg_extra.reverse(locator, source, output(".mp4", "reverse"),
                                        values["with_audio"], duration), None
        if key == "loop":
            return ffmpeg_extra.loop_clip(locator, source, output(".mp4", "loop"),
                                          int(values["times"]), values["boomerang"],
                                          duration), None
        if key == "lut":
            lut = Path(values["lut"])
            if not lut.is_file():
                raise FileNotFoundError("Select a .cube LUT file.")
            return ffmpeg_extra.apply_lut(locator, source, lut, output(".mp4", "lut"),
                                          duration), None
        if key == "text":
            if not values["text"].strip():
                raise ValueError("Type the text to burn in.")
            return ffmpeg_extra.add_text(
                locator, source, output(".mp4", "text"), values["text"], values["position"],
                int(values["font_size"]), values["colour"], values["box"],
                values["start"], values["end"], duration
            ), None
        if key == "pip":
            inset = Path(values["inset"])
            if not inset.is_file():
                raise FileNotFoundError("Select the inset video.")
            return ffmpeg_extra.picture_in_picture(
                locator, source, inset, output(".mp4", "pip"), values["position"],
                int(values["scale_percent"]), int(values["border"]), duration
            ), None
        if key == "splitscreen":
            other = Path(values["right"])
            if not other.is_file():
                raise FileNotFoundError("Select the second video.")
            return ffmpeg_extra.split_screen(
                locator, source, other, output(".mp4", "split"), values["orientation"],
                int(values["width"]), int(values["height"]), duration
            ), None
        if key == "chromakey":
            background = Path(values["background"])
            if not background.is_file():
                raise FileNotFoundError("Select a background image or video.")
            return ffmpeg_extra.chroma_key(
                locator, source, background, output(".mp4", "keyed"), values["key_colour"],
                values["similarity"], values["blend"], duration
            ), None
        if key == "blurregion":
            return ffmpeg_extra.blur_region(
                locator, source, output(".mp4", "blurred"), int(values["x"]), int(values["y"]),
                int(values["width"]), int(values["height"]), int(values["strength"]), duration
            ), None
        if key == "loudness":
            return ffmpeg_extra.normalise_audio(locator, source, output(".mp4", "loud"),
                                                values["target_lufs"], duration), None
        if key == "cleanspeech":
            return ffmpeg_extra.clean_speech(locator, source, output(".mp4", "speech"),
                                             int(values["gate_db"]), duration), None
        if key == "contactsheet":
            return ffmpeg_extra.contact_sheet(locator, source, output(".jpg", "sheet"),
                                              int(values["columns"]), int(values["rows"]),
                                              int(values["width"])), None
        if key == "waveform":
            return ffmpeg_extra.waveform_image(locator, source, output(".png", "waveform"),
                                               int(values["width"]), int(values["height"])), None
        if key == "convertimage":
            return ffmpeg_extra.convert_image(
                locator, source, output(values["format"], "converted"),
                int(values["width"]), int(values["height"]), int(values["quality"])
            ), None
        if key == "export":
            preset = ffmpeg_extra.EXPORT_PRESETS[values["preset"]]
            tag = values["preset"].split()[0].lower().replace("/", "")
            return ffmpeg_extra.export_with_preset(
                locator, source, output(preset["container"], tag), values["preset"],
                duration, values["hardware"]
            ), None

        if key == "vidstab":
            destination = output(".mp4", "stable")
            transforms = self.context.require_project().root / "cache" / f"{source.stem}.trf"
            context = self.context

            def work(job_context):
                job_context.progress(0.05, "Pass 1: analysing camera motion")
                analyse = ffmpeg_extra.stabilise_analyse(
                    locator, source, transforms, int(values["shakiness"]),
                    int(values["accuracy"]), duration
                )
                code, log = ffmpeg_tools.run_with_progress(
                    analyse,
                    on_progress=lambda fraction, label: job_context.progress(
                        0.05 + fraction * 0.45, f"Analysing {label}"
                    ),
                    should_cancel=lambda: job_context.cancelled,
                )
                if code != 0:
                    if "vidstabdetect" in log or "No such filter" in log:
                        raise RuntimeError(
                            "This ffmpeg build has no libvidstab. Use the single-pass "
                            "'deshake' option instead, or install a full build "
                            "(the Gyan full build includes it)."
                        )
                    raise RuntimeError(f"Stabilisation pass 1 failed:\n{log}")

                job_context.progress(0.5, "Pass 2: applying the smoothed path")
                apply = ffmpeg_extra.stabilise_apply(
                    locator, source, transforms, destination, int(values["smoothing"]),
                    values["zoom"], duration
                )
                code, log = ffmpeg_tools.run_with_progress(
                    apply,
                    on_progress=lambda fraction, label: job_context.progress(
                        0.5 + fraction * 0.5, f"Stabilising {label}"
                    ),
                    should_cancel=lambda: job_context.cancelled,
                )
                if code != 0:
                    raise RuntimeError(f"Stabilisation pass 2 failed:\n{log}")
                context.catalogue(destination, source="edit")
                transforms.unlink(missing_ok=True)
                return destination

            return None, work

        if key == "scenesplit":
            context = self.context
            project = self.context.require_project()
            add_to_timeline = values["add_to_timeline"]

            def work(job_context):
                job_context.progress(0.1, "Detecting scene changes")
                cuts = ffmpeg_extra.scene_detect(locator, source, values["threshold"])
                total = duration or ffmpeg_tools.probe(locator, source).duration
                boundaries = [0.0] + [cut for cut in cuts if 0.2 < cut < total - 0.2] + [total]
                if len(boundaries) < 3:
                    raise RuntimeError(
                        "No scene changes found. Lower the threshold and try again."
                    )
                produced = []
                for index in range(len(boundaries) - 1):
                    job_context.raise_if_cancelled()
                    start, end = boundaries[index], boundaries[index + 1]
                    if end - start < 0.3:
                        continue
                    job_context.progress(0.15 + 0.8 * index / len(boundaries),
                                         f"Scene {index + 1}")
                    piece = output(".mp4", f"scene{index + 1:02d}")
                    command = ffmpeg_tools.trim(locator, source, piece, start, end, True)
                    code, log = ffmpeg_tools.run_with_progress(
                        command, should_cancel=lambda: job_context.cancelled
                    )
                    if code != 0:
                        raise RuntimeError(f"Cutting scene {index + 1} failed:\n{log}")
                    asset_id = context.catalogue(piece, source="edit")
                    if add_to_timeline:
                        project.append_clip(asset_id, 0.0, end - start)
                    produced.append(piece)
                return f"Split into {len(produced)} scenes:\n" + "\n".join(
                    str(path) for path in produced
                )

            return None, work

        if key == "jumpcut":
            destination = output(".mp4", "jumpcut")
            context = self.context

            def work(job_context):
                job_context.progress(0.1, "Analysing audio for silence")
                silences = ffmpeg_tools.detect_silence(
                    locator, source, threshold_db=int(values["threshold_db"]),
                    min_len=values["min_len"],
                )
                total = duration or ffmpeg_tools.probe(locator, source).duration
                keep = ffmpeg_tools.invert_ranges(silences, total, padding=values["padding"])
                if not keep:
                    raise RuntimeError(
                        "Every part of the clip registered as silence. Lower the threshold."
                    )
                job_context.progress(0.25, f"Keeping {len(keep)} segments")
                command = ffmpeg_tools.keep_ranges(locator, source, destination, keep)
                code, log = ffmpeg_tools.run_with_progress(
                    command,
                    on_progress=lambda fraction, label: job_context.progress(
                        0.25 + fraction * 0.75, label
                    ),
                    should_cancel=lambda: job_context.cancelled,
                )
                if code != 0:
                    raise RuntimeError(f"ffmpeg exited with {code}:\n{log}")
                context.catalogue(destination, source="edit")
                removed = total - sum(end - start for start, end in keep)
                return f"Removed {removed:.1f}s of silence -> {destination}"

            return None, work

        raise ValueError(f"Unknown operation '{key}'")
