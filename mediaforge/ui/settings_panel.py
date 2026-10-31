"""Settings panel: credentials, ffmpeg location, provider endpoints, custom models."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QScrollArea, QSpinBox, QTextEdit, QVBoxLayout, QWidget,
)

from ..config import CREDENTIAL_SLOTS
from ..context import AppContext
from ..providers import PROVIDER_CLASSES
from .widgets import Card
from . import theme


class SettingsPanel(QWidget):
    status = Signal(str)
    providers_reloaded = Signal()

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self._key_edits: dict[str, QLineEdit] = {}
        self._build()
        self.load()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        host = QWidget()
        layout = QVBoxLayout(host)

        # -- credentials ------------------------------------------------------------
        credentials_card = Card("API keys")
        credentials_card.body.addWidget(QLabel(
            "Keys are stored obfuscated in your AppData folder. An environment variable of "
            "the matching name always overrides the stored value."
        ))
        form = QFormLayout()
        slot_owners = {
            provider_class.credential_slot: provider_class
            for provider_class in PROVIDER_CLASSES if provider_class.credential_slot
        }
        for slot, env_name in CREDENTIAL_SLOTS.items():
            owner = slot_owners.get(slot)
            edit = QLineEdit()
            edit.setEchoMode(QLineEdit.EchoMode.Password)
            edit.setPlaceholderText(f"env: {env_name}")
            self._key_edits[slot] = edit

            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(edit, 1)
            reveal = QPushButton("Show")
            reveal.setCheckable(True)
            reveal.toggled.connect(
                lambda checked, e=edit: e.setEchoMode(
                    QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
                )
            )
            row_layout.addWidget(reveal)
            if owner and owner.homepage:
                get_key = QPushButton("Get key")
                get_key.clicked.connect(
                    lambda _=False, url=owner.homepage: QDesktopServices.openUrl(QUrl(url))
                )
                row_layout.addWidget(get_key)
            label = owner.name if owner else slot.replace("_", " ").title()
            form.addRow(label, row)
        credentials_card.body.addLayout(form)
        layout.addWidget(credentials_card)

        # -- ffmpeg -----------------------------------------------------------------
        ffmpeg_card = Card("FFmpeg")
        self.ffmpeg_status = QLabel("")
        self.ffmpeg_status.setWordWrap(True)
        ffmpeg_card.body.addWidget(self.ffmpeg_status)

        ffmpeg_row = QHBoxLayout()
        self.ffmpeg_edit = QLineEdit()
        self.ffmpeg_edit.setPlaceholderText("Path to ffmpeg.exe, or a folder containing it")
        browse = QPushButton("Browse")
        browse.clicked.connect(self._browse_ffmpeg)
        install = QPushButton("Install with winget")
        install.clicked.connect(self._install_ffmpeg)
        detect = QPushButton("Re-detect")
        detect.clicked.connect(self._detect_ffmpeg)
        ffmpeg_row.addWidget(self.ffmpeg_edit, 1)
        for button in (browse, detect, install):
            ffmpeg_row.addWidget(button)
        ffmpeg_card.body.addLayout(ffmpeg_row)
        layout.addWidget(ffmpeg_card)

        # -- endpoints ---------------------------------------------------------------
        endpoints_card = Card("Endpoints and workspace")
        endpoint_form = QFormLayout()
        self.workspace_edit = QLineEdit()
        workspace_row = QWidget()
        workspace_layout = QHBoxLayout(workspace_row)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.addWidget(self.workspace_edit, 1)
        workspace_browse = QPushButton("Browse")
        workspace_browse.clicked.connect(self._browse_workspace)
        workspace_layout.addWidget(workspace_browse)
        endpoint_form.addRow("Projects folder", workspace_row)

        self.openai_base_edit = QLineEdit()
        endpoint_form.addRow("OpenAI-compatible base URL", self.openai_base_edit)
        self.comfy_edit = QLineEdit()
        endpoint_form.addRow("ComfyUI URL", self.comfy_edit)
        self.proxy_edit = QLineEdit()
        self.proxy_edit.setPlaceholderText("http://127.0.0.1:8080 -- leave blank for none")
        endpoint_form.addRow("HTTP proxy", self.proxy_edit)
        self.region_box = QComboBox()
        self.region_box.addItems(["international", "china"])
        endpoint_form.addRow("Volcengine Ark region", self.region_box)
        self.parallel_box = QSpinBox()
        self.parallel_box.setRange(1, 8)
        endpoint_form.addRow("Parallel jobs (restart to apply)", self.parallel_box)
        endpoints_card.body.addLayout(endpoint_form)
        layout.addWidget(endpoints_card)

        # -- custom models -----------------------------------------------------------
        custom_card = Card("Custom models (one per line)")
        custom_card.body.addWidget(QLabel(
            "Paste any fal.ai model id or Replicate owner/name. They appear in the Generate "
            "panel immediately, which is how you reach the wider open-source model catalogue."
        ))
        self.fal_models_box = QTextEdit()
        self.fal_models_box.setPlaceholderText("fal-ai/flux-pro/v1.1-ultra\nfal-ai/kling-video/...")
        self.fal_models_box.setMaximumHeight(90)
        self.replicate_models_box = QTextEdit()
        self.replicate_models_box.setPlaceholderText("stability-ai/sdxl\ntencent/hunyuan-video")
        self.replicate_models_box.setMaximumHeight(90)
        custom_card.body.addWidget(QLabel("fal.ai models"))
        custom_card.body.addWidget(self.fal_models_box)
        custom_card.body.addWidget(QLabel("Replicate models"))
        custom_card.body.addWidget(self.replicate_models_box)
        layout.addWidget(custom_card)

        button_row = QHBoxLayout()
        save_button = QPushButton("Save and reload providers")
        save_button.setObjectName("primary")
        save_button.clicked.connect(self.save)
        test_button = QPushButton("Show provider status")
        test_button.clicked.connect(self._show_status)
        button_row.addWidget(save_button)
        button_row.addWidget(test_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(host)
        outer.addWidget(scroll)

    # -- load / save ------------------------------------------------------------------
    def load(self) -> None:
        settings = self.context.settings
        self.workspace_edit.setText(settings.workspace)
        self.ffmpeg_edit.setText(settings.ffmpeg_path)
        self.openai_base_edit.setText(settings.openai_base_url)
        self.comfy_edit.setText(settings.comfyui_url)
        self.proxy_edit.setText(settings.proxy_url)
        self.region_box.setCurrentText(settings.seedance_region)
        self.parallel_box.setValue(settings.max_parallel_jobs)
        self.fal_models_box.setPlainText(settings.custom_fal_models)
        self.replicate_models_box.setPlainText(settings.custom_replicate_models)
        for slot, edit in self._key_edits.items():
            source = self.context.credentials.source_of(slot)
            if source.startswith("environment"):
                edit.setPlaceholderText(f"set by {source}")
                edit.setEnabled(False)
            else:
                edit.setText(self.context.credentials.get(slot))
        self._update_ffmpeg_status()

    def save(self) -> None:
        settings = self.context.settings
        settings.workspace = self.workspace_edit.text().strip() or settings.workspace
        settings.ffmpeg_path = self.ffmpeg_edit.text().strip()
        settings.ffprobe_path = settings.ffmpeg_path
        settings.openai_base_url = self.openai_base_edit.text().strip()
        settings.comfyui_url = self.comfy_edit.text().strip()
        settings.proxy_url = self.proxy_edit.text().strip()
        settings.seedance_region = self.region_box.currentText()
        settings.max_parallel_jobs = self.parallel_box.value()
        settings.custom_fal_models = self.fal_models_box.toPlainText()
        settings.custom_replicate_models = self.replicate_models_box.toPlainText()
        settings.save()

        for slot, edit in self._key_edits.items():
            if edit.isEnabled():
                self.context.credentials.set(slot, edit.text())

        self.context.reload_providers()
        self._update_ffmpeg_status()
        self.providers_reloaded.emit()
        self.status.emit("Settings saved, providers reloaded")

    # -- ffmpeg -----------------------------------------------------------------------
    def _update_ffmpeg_status(self) -> None:
        locator = self.context.locator
        if locator.available:
            self.ffmpeg_status.setText(
                f"<span style='color:{theme.SUCCESS}'>Found.</span> {locator.version()}<br>"
                f"<span style='color:{theme.TEXT_FAINT}'>{locator.ffmpeg}</span>"
            )
        else:
            self.ffmpeg_status.setText(
                f"<span style='color:{theme.DANGER}'>Not found.</span> Local editing, thumbnails and "
                "rendering are disabled until ffmpeg is installed. Generation via APIs still works."
            )

    def _browse_ffmpeg(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Locate ffmpeg", "", "ffmpeg (ffmpeg.exe ffmpeg);;All files (*.*)"
        )
        if path:
            self.ffmpeg_edit.setText(str(Path(path).parent))

    def _detect_ffmpeg(self) -> None:
        found = shutil.which("ffmpeg")
        if found:
            self.ffmpeg_edit.setText(str(Path(found).parent))
            self.save()
        else:
            QMessageBox.information(
                self, "Not found",
                "ffmpeg is not on PATH. Use 'Install with winget', or download a build from "
                "gyan.dev and point the path here.",
            )

    @staticmethod
    def _find_installer() -> tuple[str, str] | None:
        """Returns (label, command) for the first available package manager.

        winget usually lives in WindowsApps, which is often missing from PATH in
        non-interactive shells -- so look there directly before giving up on it.
        """
        winget = shutil.which("winget")
        if not winget:
            alias = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps" / "winget.exe"
            if alias.exists():
                winget = str(alias)
        if winget:
            return "winget", (
                f'"{winget}" install Gyan.FFmpeg '
                "--accept-source-agreements --accept-package-agreements"
            )

        choco = shutil.which("choco")
        if choco:
            return "Chocolatey (needs an elevated console)", f'"{choco}" install ffmpeg -y'
        return None

    def _install_ffmpeg(self) -> None:
        if sys.platform != "win32":
            QMessageBox.information(
                self, "Windows only",
                "Use your package manager instead, e.g.  brew install ffmpeg  or  "
                "apt install ffmpeg.",
            )
            return

        found = self._find_installer()
        if not found:
            QMessageBox.information(
                self, "No package manager found",
                "Neither winget nor Chocolatey is available.\n\n"
                "Download a build from https://www.gyan.dev/ffmpeg/builds/ , unzip it, and "
                "point the path field at the folder containing ffmpeg.exe.",
            )
            return

        label, command = found
        answer = QMessageBox.question(
            self, "Install ffmpeg",
            f"Run this with {label} in a new console window?\n\n    {command}\n\n"
            "Press 'Re-detect' here once it finishes.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            subprocess.Popen(["cmd", "/c", "start", "cmd", "/k", command], shell=False)
            self.status.emit(f"{label} launched in a separate window")
        except OSError as error:
            QMessageBox.warning(self, "Could not launch the installer", str(error))

    def _browse_workspace(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Projects folder",
                                                  self.workspace_edit.text())
        if folder:
            self.workspace_edit.setText(folder)

    def _show_status(self) -> None:
        lines = []
        for provider in self.context.registry:
            marker = "OK  " if provider.configured else "--  "
            capabilities = ", ".join(sorted(c.value for c in provider.capabilities()))
            source = (self.context.credentials.source_of(provider.credential_slot)
                      if provider.credential_slot else "no key needed")
            lines.append(f"{marker}{provider.name}\n     key: {source}\n     {capabilities}\n")
        QMessageBox.information(self, "Provider status", "\n".join(lines))
