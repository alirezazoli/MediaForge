"""Model Hub: browse every model in the catalogue, see what's open vs API-only,
install local engines, and edit the catalogue without touching code.
"""

from __future__ import annotations

import subprocess
import sys

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from .. import localai
from ..catalog import catalog_path, reset_catalog
from ..context import AppContext
from . import theme

def openness_colour(kind: str) -> str:
    return {"open": theme.FREE, "partial": theme.WARNING}.get(kind, theme.TEXT_MUTED)

MODEL_COLUMNS = ["Model", "Vendor", "Weights", "Capabilities", "Routes"]
TOOL_COLUMNS = ["Engine", "Category", "Status", "Size", "Licence"]


class HubPanel(QWidget):
    status = Signal(str)
    catalog_changed = Signal()

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self._build()
        self.refresh()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_models_tab(), "Models")
        self.tabs.addTab(self._build_tools_tab(), "Local engines")
        self.tabs.addTab(self._build_editor_tab(), "Edit catalogue")
        layout.addWidget(self.tabs)

    # -- models ---------------------------------------------------------------------------
    def _build_models_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        toolbar = QHBoxLayout()
        self.model_search = QLineEdit()
        self.model_search.setPlaceholderText("Search model or vendor")
        self.model_search.textChanged.connect(self._refresh_models)
        self.openness_filter = QComboBox()
        self.openness_filter.addItems(["All", "Open weights only", "API only"])
        self.openness_filter.currentTextChanged.connect(self._refresh_models)
        toolbar.addWidget(QLabel("Filter"))
        toolbar.addWidget(self.openness_filter)
        toolbar.addWidget(self.model_search, 1)
        layout.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Vertical)

        splitter.setChildrenCollapsible(False)

        splitter.setHandleWidth(8)
        self.model_table = QTableWidget(0, len(MODEL_COLUMNS))
        self.model_table.setHorizontalHeaderLabels(MODEL_COLUMNS)
        self.model_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.model_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.model_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.model_table.currentCellChanged.connect(self._show_model_detail)
        self.model_table.setMinimumHeight(220)
        splitter.addWidget(self.model_table)

        detail_host = QWidget()
        detail_host.setMinimumHeight(150)
        detail_layout = QVBoxLayout(detail_host)
        self.model_detail = QTextEdit()
        self.model_detail.setReadOnly(True)
        detail_layout.addWidget(self.model_detail)
        repo_row = QHBoxLayout()
        self.open_repo_button = QPushButton("Open repository / model page")
        self.open_repo_button.clicked.connect(self._open_repo)
        repo_row.addWidget(self.open_repo_button)
        repo_row.addStretch(1)
        detail_layout.addLayout(repo_row)
        splitter.addWidget(detail_host)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)
        return page

    def _catalog_provider(self):
        return self.context.registry.get("catalog")

    def _refresh_models(self) -> None:
        provider = self._catalog_provider()
        self.model_table.setRowCount(0)
        if provider is None:
            return
        needle = self.model_search.text().strip().lower()
        mode = self.openness_filter.currentText()

        self._model_rows: list[dict] = []
        for spec in provider.models:
            entry = provider.entry(spec.id) or {}
            openness = entry.get("openness", "closed")
            if mode == "Open weights only" and openness not in ("open", "partial"):
                continue
            if mode == "API only" and openness != "closed":
                continue
            haystack = f"{entry.get('label', '')} {entry.get('vendor', '')} {spec.id}".lower()
            if needle and needle not in haystack:
                continue
            self._model_rows.append(entry)

        for row, entry in enumerate(self._model_rows):
            self.model_table.insertRow(row)
            name_item = QTableWidgetItem(entry.get("label", entry.get("id", "")))
            name_item.setData(Qt.ItemDataRole.UserRole, entry.get("id"))
            self.model_table.setItem(row, 0, name_item)
            self.model_table.setItem(row, 1, QTableWidgetItem(entry.get("vendor", "")))

            openness = entry.get("openness", "closed")
            weights_item = QTableWidgetItem(
                {"open": "open weights", "partial": "partly open", "closed": "closed (API)"}
                .get(openness, openness)
            )
            weights_item.setToolTip(openness_colour(openness))
            self.model_table.setItem(row, 2, weights_item)
            self.model_table.setItem(
                row, 3, QTableWidgetItem(", ".join(entry.get("capabilities", [])))
            )
            routes = entry.get("routes", [])
            self.model_table.setItem(
                row, 4,
                QTableWidgetItem(", ".join(sorted({r.get("provider", "") for r in routes})))
            )
        self.status.emit(f"{len(self._model_rows)} models in the catalogue")

    def _selected_model_entry(self) -> dict | None:
        row = self.model_table.currentRow()
        if row < 0 or row >= len(getattr(self, "_model_rows", [])):
            return None
        return self._model_rows[row]

    def _show_model_detail(self) -> None:
        entry = self._selected_model_entry()
        if not entry:
            self.model_detail.clear()
            return
        lines = [
            f"{entry.get('label', '')}   ({entry.get('vendor', '')})",
            "",
            f"Weights   : {entry.get('openness', 'closed')}",
            f"Capabilities: {', '.join(entry.get('capabilities', []))}",
        ]
        if entry.get("repo"):
            lines.append(f"Repository: {entry['repo']}")
        if entry.get("notes"):
            lines += ["", entry["notes"]]
        lines += ["", "Routes:"]
        for route in entry.get("routes", []):
            mark = "verified" if route.get("verified") else "UNVERIFIED endpoint id"
            provider = self.context.registry.get(
                self._catalog_provider().ROUTE_TO_PROVIDER.get(route.get("provider", ""), "")
            )
            key_state = "no key" if (provider and not provider.configured) else "ready"
            lines.append(
                f"  {route.get('provider', ''):<12} {route.get('endpoint', ''):<55} "
                f"[{mark}] [{key_state}]"
            )
        self.model_detail.setPlainText("\n".join(lines))

    def _open_repo(self) -> None:
        entry = self._selected_model_entry()
        if not entry:
            return
        url = entry.get("repo") or "https://fal.ai/models"
        QDesktopServices.openUrl(QUrl(url))

    # -- local tools --------------------------------------------------------------------
    def _build_tools_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        banner = QLabel(
            "These run <b>on your machine, free, offline</b>. Nothing downloads until you "
            "press Install. Sizes are approximate."
        )
        banner.setWordWrap(True)
        banner.setObjectName("okText")
        layout.addWidget(banner)

        splitter = QSplitter(Qt.Orientation.Vertical)

        splitter.setChildrenCollapsible(False)

        splitter.setHandleWidth(8)
        self.tool_table = QTableWidget(0, len(TOOL_COLUMNS))
        self.tool_table.setHorizontalHeaderLabels(TOOL_COLUMNS)
        self.tool_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tool_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tool_table.currentCellChanged.connect(self._show_tool_detail)
        self.tool_table.setMinimumHeight(220)
        splitter.addWidget(self.tool_table)

        detail_host = QWidget()
        detail_host.setMinimumHeight(150)
        detail_layout = QVBoxLayout(detail_host)
        self.tool_detail = QTextEdit()
        self.tool_detail.setReadOnly(True)
        detail_layout.addWidget(self.tool_detail)
        button_row = QHBoxLayout()
        install_button = QPushButton("Install selected")
        install_button.setObjectName("primary")
        install_button.clicked.connect(self._install_selected)
        repo_button = QPushButton("Open repository")
        repo_button.clicked.connect(self._open_tool_repo)
        folder_button = QPushButton("Open install folder")
        folder_button.clicked.connect(self._open_tool_folder)
        refresh_button = QPushButton("Re-check")
        refresh_button.clicked.connect(self.refresh)
        for button in (install_button, repo_button, folder_button, refresh_button):
            button_row.addWidget(button)
        button_row.addStretch(1)
        detail_layout.addLayout(button_row)
        splitter.addWidget(detail_host)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)
        return page

    def _refresh_tools(self) -> None:
        self.tool_table.setRowCount(0)
        for row, tool in enumerate(self.context.local_tools.tools):
            self.tool_table.insertRow(row)
            name_item = QTableWidgetItem(tool.name)
            name_item.setData(Qt.ItemDataRole.UserRole, tool.id)
            self.tool_table.setItem(row, 0, name_item)
            self.tool_table.setItem(row, 1, QTableWidgetItem(tool.category))
            self.tool_table.setItem(row, 2, QTableWidgetItem(tool.status))
            self.tool_table.setItem(row, 3, QTableWidgetItem(f"{tool.size_mb} MB"))
            self.tool_table.setItem(row, 4, QTableWidgetItem(tool.license))

    def _selected_tool(self) -> localai.LocalTool | None:
        row = self.tool_table.currentRow()
        if row < 0:
            return None
        item = self.tool_table.item(row, 0)
        return self.context.local_tools.get(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _show_tool_detail(self) -> None:
        tool = self._selected_tool()
        if not tool:
            self.tool_detail.clear()
            return
        lines = [
            tool.name, "",
            tool.summary, "",
            f"Category  : {tool.category}",
            f"Media     : {', '.join(tool.media)}",
            f"Licence   : {tool.license}",
            f"Repository: {tool.repo}",
            f"Install   : {tool.install_kind}  (~{tool.size_mb} MB)",
            f"Status    : {tool.status}",
        ]
        executable = tool.executable_path()
        if executable:
            lines.append(f"Executable: {executable}")
        if tool.install_kind == "manual":
            lines += ["", "This one is too large or too setup-heavy to install automatically.",
                      "Open the repository and follow its instructions."]
        self.tool_detail.setPlainText("\n".join(lines))

    def _install_selected(self) -> None:
        tool = self._selected_tool()
        if not tool:
            return
        if tool.installed:
            QMessageBox.information(self, "Already installed", f"{tool.name} is ready.")
            return
        if tool.install_kind == "manual":
            QMessageBox.information(
                self, "Manual install",
                f"{tool.name} must be installed by hand.\n\n{tool.repo}\n\n"
                "Opening the repository now.",
            )
            QDesktopServices.openUrl(QUrl(tool.repo))
            return

        answer = QMessageBox.question(
            self, "Install engine",
            f"Download and install {tool.name}?\n\n"
            f"Source : {tool.repo}\n"
            f"Licence: {tool.license}\n"
            f"Size   : about {tool.size_mb} MB\n"
            f"Method : {tool.install_kind}",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        def work(job_context):
            return localai.install_tool(
                tool, progress=lambda fraction, message: job_context.progress(fraction, message)
            )

        job = self.context.jobs.submit(f"Install {tool.name}", work, category="install")
        self.status.emit(f"Job {job.id}: installing {tool.name}")

    def _open_tool_repo(self) -> None:
        tool = self._selected_tool()
        if tool and tool.repo:
            QDesktopServices.openUrl(QUrl(tool.repo))

    def _open_tool_folder(self) -> None:
        tool = self._selected_tool()
        if not tool:
            return
        folder = tool.root if tool.root.exists() else localai.tools_dir()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # -- catalogue editor -----------------------------------------------------------------
    def _build_editor_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel(
            "The catalogue is plain JSON in your config folder. Add a model released "
            "tomorrow, fix an endpoint that returns 404, or point a model at a different "
            "backend — no code change, no update needed."
        ))
        paths = QLabel(
            f"<code>{catalog_path('models.json')}</code><br>"
            f"<code>{catalog_path('localtools.json')}</code>"
        )
        paths.setWordWrap(True)
        paths.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(paths)

        button_row = QHBoxLayout()
        for label, handler in (
            ("Edit models.json", lambda: self._edit("models.json")),
            ("Edit localtools.json", lambda: self._edit("localtools.json")),
            ("Open config folder", self._open_config_folder),
            ("Reload catalogue", self._reload_catalog),
            ("Reset models.json to shipped", lambda: self._reset("models.json")),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            button_row.addWidget(button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        self.editor_help = QTextEdit()
        self.editor_help.setReadOnly(True)
        self.editor_help.setPlainText(
            "Adding a model — append to the \"models\" array:\n\n"
            "{\n"
            '  "id": "my-new-model",\n'
            '  "label": "Some Model 4.0",\n'
            '  "vendor": "SomeCorp",\n'
            '  "openness": "closed",\n'
            '  "capabilities": ["text_to_video", "image_to_video"],\n'
            '  "options_preset": "video_audio",\n'
            '  "routes": [\n'
            '    {"provider": "fal", "endpoint": "fal-ai/somecorp/some-model-4",\n'
            '     "capability": "text_to_video", "verified": false}\n'
            "  ]\n"
            "}\n\n"
            "Valid capabilities:\n"
            "  text_to_image, image_edit, text_to_video, image_to_video, video_extend,\n"
            "  lip_sync, motion_transfer, upscale, interpolate, denoise, restore, matte,\n"
            "  separate_audio, text_to_speech, transcribe, llm, video_understanding\n\n"
            "Valid route providers:\n"
            "  fal, replicate, google, volcengine, dashscope, comfyui\n\n"
            "Press 'Reload catalogue' after saving. No restart needed."
        )
        layout.addWidget(self.editor_help, 1)
        return page

    def _edit(self, name: str) -> None:
        path = catalog_path(name)
        if sys.platform == "win32":
            subprocess.Popen(["cmd", "/c", "start", "", str(path)], shell=False)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        self.status.emit(f"Opened {path}")

    def _open_config_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(catalog_path("models.json").parent)))

    def _reload_catalog(self) -> None:
        self.context.reload_providers()
        self.refresh()
        self.catalog_changed.emit()
        self.status.emit("Catalogue reloaded")

    def _reset(self, name: str) -> None:
        if QMessageBox.question(
            self, "Reset catalogue",
            f"Overwrite your {name} with the version that shipped with the app?\n"
            "Your edits to that file will be lost.",
        ) != QMessageBox.StandardButton.Yes:
            return
        reset_catalog(name)
        self._reload_catalog()

    # -- shared --------------------------------------------------------------------------
    def refresh(self) -> None:
        self._refresh_models()
        self._refresh_tools()
