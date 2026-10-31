"""Library panel: import, browse, preview, tag and stage media."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from ..context import AppContext
from ..ffmpeg_tools import AUDIO_SUFFIXES, IMAGE_SUFFIXES, VIDEO_SUFFIXES, format_timecode
from ..project import Asset
from .widgets import ResponsiveSplitter, MediaPreview
from . import theme

MEDIA_FILTER = (
    "Media ("
    + " ".join(f"*{suffix}" for suffix in sorted(IMAGE_SUFFIXES | VIDEO_SUFFIXES | AUDIO_SUFFIXES))
    + ");;All files (*.*)"
)


class LibraryPanel(QWidget):
    asset_selected = Signal(object)      # Asset | None
    send_to_edit = Signal(object)        # Asset
    status = Signal(str)

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self._assets: list[Asset] = []
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        import_button = QPushButton("Import files")
        import_button.clicked.connect(self.import_files)
        import_folder_button = QPushButton("Import folder")
        import_folder_button.clicked.connect(self.import_folder)

        self.kind_filter = QComboBox()
        self.kind_filter.addItems(["All", "video", "image", "audio"])
        self.kind_filter.currentTextChanged.connect(self.refresh)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search name or tag")
        self.search_box.textChanged.connect(self.refresh)

        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh)

        toolbar.addWidget(import_button)
        toolbar.addWidget(import_folder_button)
        toolbar.addWidget(QLabel("Type"))
        toolbar.addWidget(self.kind_filter)
        toolbar.addWidget(self.search_box, 1)
        toolbar.addWidget(refresh_button)
        layout.addLayout(toolbar)

        splitter = ResponsiveSplitter()

        splitter.setChildrenCollapsible(False)

        splitter.setHandleWidth(8)

        self.list_widget = QListWidget()
        self.list_widget.setViewMode(QListWidget.ViewMode.IconMode)
        self.list_widget.setIconSize(QSize(180, 110))
        self.list_widget.setGridSize(QSize(200, 160))
        self.list_widget.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_widget.setMovement(QListWidget.Movement.Static)
        self.list_widget.currentItemChanged.connect(self._on_selection)
        self.list_widget.setMinimumWidth(360)
        splitter.addWidget(self.list_widget)

        side = QWidget()
        side_layout = QVBoxLayout(side)
        self.preview = MediaPreview()
        side_layout.addWidget(self.preview, 1)

        self.details = QLabel("Nothing selected")
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        side_layout.addWidget(self.details)

        self.tags_box = QLineEdit()
        self.tags_box.setPlaceholderText("Tags (comma separated) -- press Enter to save")
        self.tags_box.returnPressed.connect(self._save_tags)
        side_layout.addWidget(self.tags_box)

        buttons = QHBoxLayout()
        for label, handler in (
            ("Send to Edit", self._send_to_edit),
            ("Add to timeline", self._add_to_timeline),
            ("Favourite", self._toggle_favourite),
            ("Remove", self._remove_selected),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            buttons.addWidget(button)
        side_layout.addLayout(buttons)

        splitter.addWidget(side)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        side.setMinimumWidth(320)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)
        layout.addWidget(splitter, 1)

    # -- data ------------------------------------------------------------------------
    def refresh(self) -> None:
        self.list_widget.clear()
        if self.context.project is None:
            self.details.setText("No project open.")
            return
        kind = self.kind_filter.currentText()
        self._assets = self.context.project.assets(
            kind="" if kind == "All" else kind, search=self.search_box.text().strip()
        )
        for asset in self._assets:
            marker = "* " if asset.favourite else ""
            item = QListWidgetItem(f"{marker}{asset.label}")
            item.setData(Qt.ItemDataRole.UserRole, asset.id)
            item.setToolTip(f"{asset.path}\n{asset.resolution}  {asset.pretty_size}")
            if asset.thumbnail and Path(asset.thumbnail).exists():
                item.setIcon(QIcon(QPixmap(asset.thumbnail)))
            elif asset.kind == "image" and asset.exists:
                item.setIcon(QIcon(QPixmap(str(asset.path))))
            if not asset.exists:
                item.setText(f"[missing] {asset.label}")
            self.list_widget.addItem(item)
        self.status.emit(f"{len(self._assets)} assets")

    def current_asset(self) -> Asset | None:
        item = self.list_widget.currentItem()
        if not item:
            return None
        asset_id = item.data(Qt.ItemDataRole.UserRole)
        return next((asset for asset in self._assets if asset.id == asset_id), None)

    def selected_assets(self) -> list[Asset]:
        ids = {item.data(Qt.ItemDataRole.UserRole) for item in self.list_widget.selectedItems()}
        return [asset for asset in self._assets if asset.id in ids]

    # -- actions ---------------------------------------------------------------------
    def import_files(self) -> None:
        if self.context.project is None:
            QMessageBox.information(self, "No project", "Create or open a project first.")
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Import media", "", MEDIA_FILTER)
        self._ingest([Path(path) for path in paths])

    def import_folder(self) -> None:
        if self.context.project is None:
            QMessageBox.information(self, "No project", "Create or open a project first.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Import folder")
        if not folder:
            return
        known = IMAGE_SUFFIXES | VIDEO_SUFFIXES | AUDIO_SUFFIXES
        paths = [
            path for path in sorted(Path(folder).rglob("*"))
            if path.is_file() and path.suffix.lower() in known
        ]
        self._ingest(paths)

    def _ingest(self, paths: list[Path]) -> None:
        if not paths:
            return
        project = self.context.require_project()

        def work(job_context):
            imported = 0
            for index, source in enumerate(paths):
                job_context.raise_if_cancelled()
                job_context.progress(index / len(paths), source.name)
                try:
                    destination = project.import_file(source, copy=True)
                    self.context.catalogue(destination, source="import")
                    imported += 1
                except Exception as error:  # keep going: one bad file should not stop a batch
                    job_context.progress(index / len(paths), f"skipped {source.name}: {error}")
            return imported

        job = self.context.jobs.submit(f"Import {len(paths)} file(s)", work, category="import")
        self.status.emit(f"Importing {len(paths)} file(s) (job {job.id})")

    def _on_selection(self) -> None:
        asset = self.current_asset()
        self.asset_selected.emit(asset)
        if not asset:
            self.details.setText("Nothing selected")
            self.preview.show_path(None)
            return
        self.preview.show_path(asset.path if asset.exists else None)
        self.tags_box.setText(asset.tags)
        self.details.setText(
            f"<b>{asset.label}</b><br>"
            f"{asset.kind} &middot; {asset.resolution} &middot; "
            f"{format_timecode(asset.duration)} &middot; {asset.fps or '-'} fps<br>"
            f"{asset.pretty_size} &middot; source: {asset.source}<br>"
            f"<span style='color:{theme.TEXT_FAINT}'>{asset.path}</span>"
        )

    def _save_tags(self) -> None:
        asset = self.current_asset()
        if asset:
            self.context.require_project().set_tags(asset.id, self.tags_box.text().strip())
            self.refresh()

    def _toggle_favourite(self) -> None:
        asset = self.current_asset()
        if asset:
            self.context.require_project().toggle_favourite(asset.id)
            self.refresh()

    def _send_to_edit(self) -> None:
        asset = self.current_asset()
        if asset:
            self.send_to_edit.emit(asset)

    def _add_to_timeline(self) -> None:
        project = self.context.require_project()
        added = 0
        for asset in self.selected_assets():
            if asset.kind in ("video", "image"):
                project.append_clip(asset.id, 0.0, asset.duration or 4.0)
                added += 1
        self.status.emit(f"Added {added} clip(s) to the timeline")

    def _remove_selected(self) -> None:
        assets = self.selected_assets()
        if not assets:
            return
        answer = QMessageBox.question(
            self, "Remove assets",
            f"Remove {len(assets)} asset(s) from the library?\n\n"
            "Choose 'Yes' to remove the catalogue entry only. Files stay on disk.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        project = self.context.require_project()
        for asset in assets:
            project.remove_asset(asset.id, delete_file=False)
        self.refresh()
