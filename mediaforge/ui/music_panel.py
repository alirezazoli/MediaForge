"""Music search: find a track you are allowed to use, and see why you are allowed.

The licence is not fine print here, it is the main column. A No-Derivatives track cannot
legally sit under a video, so those are hidden unless you deliberately ask for them.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from .. import musicsearch
from ..context import AppContext
from ..musicsearch import SOURCES, Track
from . import theme
from .widgets import MediaPreview

COLUMNS = ["Title", "Artist", "Licence", "Use", "Length", "Source"]

MOODS = [
    "", "cinematic ambient", "uplifting corporate", "calm piano", "epic orchestral",
    "lo-fi chill", "documentary strings", "minimal electronic", "warm acoustic",
    "tense drone", "hopeful morning",
]


class MusicPanel(QWidget):
    status = Signal(str)
    library_changed = Signal()
    music_chosen = Signal(object)      # Path, wired to the photo panel

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self._tracks: list[Track] = []
        self._downloaded: list[Track] = []
        self._job_id: int | None = None
        self._build()

    # -- construction -----------------------------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)

        heading = QLabel("Find music")
        heading.setObjectName("h1")
        layout.addWidget(heading)

        sub = QLabel(
            "Searches free, openly licensed music. Nothing downloads until you press "
            "Download, and every track shows exactly what its licence allows."
        )
        sub.setObjectName("subtle")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        search_row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("Describe the mood — 'calm piano', 'epic orchestral'")
        self.query.returnPressed.connect(self.search)
        self.mood_box = QComboBox()
        self.mood_box.addItem("Suggestions...")
        self.mood_box.addItems([mood for mood in MOODS if mood])
        self.mood_box.currentTextChanged.connect(self._mood_picked)
        self.search_button = QPushButton("Search")
        self.search_button.setObjectName("primary")
        self.search_button.clicked.connect(self.search)
        search_row.addWidget(self.query, 1)
        search_row.addWidget(self.mood_box)
        search_row.addWidget(self.search_button)
        layout.addLayout(search_row)

        filter_row = QHBoxLayout()
        self.commercial_only = QCheckBox("Commercial use only")
        self.commercial_only.setToolTip(
            "Hides non-commercial licences. Tick this if the video is for a client, "
            "a product, or anything that makes money."
        )
        self.allow_nd = QCheckBox("Include No-Derivatives (cannot legally be used)")
        self.allow_nd.setToolTip(
            "A soundtrack is an adaptation, so ND-licensed music cannot be put under a "
            "video. Shown only for reference."
        )
        filter_row.addWidget(self.commercial_only)
        filter_row.addWidget(self.allow_nd)
        filter_row.addStretch(1)
        filter_row.addWidget(QLabel("Sources"))
        self.source_boxes: dict[str, QCheckBox] = {}
        for source in SOURCES:
            box = QCheckBox(source.name)
            box.setChecked(not source.needs_key)
            box.setToolTip(f"{source.note}\n{source.homepage}"
                           + ("\nNeeds a free API key." if source.needs_key else ""))
            self.source_boxes[source.key] = box
            filter_row.addWidget(box)
        layout.addLayout(filter_row)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setMinimumHeight(220)
        self.table.currentCellChanged.connect(self._show_detail)
        splitter.addWidget(self.table)

        lower = QWidget()
        lower.setMinimumHeight(200)
        lower_layout = QHBoxLayout(lower)

        detail_side = QVBoxLayout()
        detail_side.addWidget(QLabel("Licence and credit"))
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        detail_side.addWidget(self.detail, 1)

        button_row = QHBoxLayout()
        self.download_button = QPushButton("Download")
        self.download_button.setObjectName("primary")
        self.download_button.clicked.connect(self.download_selected)
        self.use_button = QPushButton("Download and use in my video")
        self.use_button.clicked.connect(lambda: self.download_selected(use_it=True))
        copy_button = QPushButton("Copy credit")
        copy_button.setObjectName("ghost")
        copy_button.clicked.connect(self._copy_credit)
        page_button = QPushButton("Open source page")
        page_button.setObjectName("ghost")
        page_button.clicked.connect(self._open_page)
        for button in (self.download_button, self.use_button, copy_button, page_button):
            button_row.addWidget(button)
        button_row.addStretch(1)
        detail_side.addLayout(button_row)
        lower_layout.addLayout(detail_side, 3)

        preview_side = QVBoxLayout()
        preview_side.addWidget(QLabel("Preview downloaded"))
        self.preview = MediaPreview()
        preview_side.addWidget(self.preview, 1)
        lower_layout.addLayout(preview_side, 2)

        splitter.addWidget(lower)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        self.footer = QLabel("")
        self.footer.setObjectName("faint")
        self.footer.setWordWrap(True)
        layout.addWidget(self.footer)

    def _mood_picked(self, text: str) -> None:
        if text and text != "Suggestions...":
            self.query.setText(text)
            self.search()

    # -- search --------------------------------------------------------------------------------
    def search(self) -> None:
        query = self.query.text().strip()
        if not query:
            self.status.emit("Type a mood or style to search for")
            return
        sources = [key for key, box in self.source_boxes.items() if box.isChecked()]
        if not sources:
            QMessageBox.information(self, "No sources", "Tick at least one source.")
            return

        allow_nc = not self.commercial_only.isChecked()
        allow_nd = self.allow_nd.isChecked()
        credentials = self.context.credentials
        proxy = self.context.settings.proxy_url

        self.search_button.setEnabled(False)
        self.search_button.setText("Searching...")

        def work(job_context):
            return musicsearch.search(
                query, sources=sources, allow_non_commercial=allow_nc,
                allow_no_derivatives=allow_nd, credentials=credentials, proxy=proxy,
                progress=lambda fraction, message: job_context.progress(fraction, message),
            )

        job = self.context.jobs.submit(f"Music search: {query}", work, category="music")
        self._search_job = job.id
        self.status.emit(f"Searching for '{query}'")

    def show_results(self, outcome) -> None:
        self.search_button.setEnabled(True)
        self.search_button.setText("Search")
        self._tracks = outcome.tracks
        self.table.setRowCount(0)

        for row, track in enumerate(self._tracks):
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(track.title[:70]))
            self.table.setItem(row, 1, QTableWidgetItem(track.artist[:36]))

            licence_item = QTableWidgetItem(track.licence.code)
            licence_item.setToolTip(track.licence.name)
            self.table.setItem(row, 2, licence_item)

            use_item = QTableWidgetItem(track.licence.summary())
            colour = theme.FREE if track.licence.commercial else theme.WARNING
            if not track.licence.usable_in_video:
                colour = theme.DANGER
            use_item.setForeground(QBrush(QColor(colour)))
            self.table.setItem(row, 3, use_item)

            self.table.setItem(row, 4, QTableWidgetItem(track.pretty_duration))
            self.table.setItem(row, 5, QTableWidgetItem(track.source[:28]))

        if not self._tracks:
            # "Nothing found" and "could not ask" are different claims; say which.
            self.footer.setText(outcome.explain_empty())
            colour = theme.WARNING if outcome.errors else theme.TEXT_MUTED
            self.footer.setStyleSheet(f"color:{colour};")
            self.status.emit("No usable tracks")
            self.detail.clear()
            return

        self.footer.setObjectName("faint")
        notes = [f"{len(self._tracks)} usable tracks"]
        if outcome.hidden_unusable:
            notes.append(f"{outcome.hidden_unusable} hidden as No-Derivatives "
                         f"(cannot be used under a video)")
        if outcome.hidden_non_commercial:
            notes.append(f"{outcome.hidden_non_commercial} hidden as non-commercial")
        if outcome.errors:
            notes.append("problems: " + "; ".join(outcome.errors[:2]))
        self.footer.setText(" · ".join(notes))
        self.status.emit(f"{len(self._tracks)} tracks found")

        if self._tracks:
            self.table.setCurrentCell(0, 0)

    def search_failed(self, message: str) -> None:
        self.search_button.setEnabled(True)
        self.search_button.setText("Search")
        self.footer.setText(f"Search failed: {message[:200]}")

    # -- detail ----------------------------------------------------------------------------------
    def current_track(self) -> Track | None:
        row = self.table.currentRow()
        return self._tracks[row] if 0 <= row < len(self._tracks) else None

    def _show_detail(self) -> None:
        track = self.current_track()
        if track is None:
            self.detail.clear()
            return
        licence = track.licence
        lines = [
            track.title,
            f"by {track.artist}" if track.artist else "",
            "",
            f"Licence : {licence.name}",
            f"Allows  : {licence.summary()}",
            f"Terms   : {licence.url or 'see the source page'}",
            "",
        ]
        if not licence.usable_in_video:
            lines += ["WARNING: this licence forbids adaptations. Putting it under a "
                      "video is an adaptation, so you may not use this track.", ""]
        elif not licence.commercial:
            lines += ["This is non-commercial only. Fine for personal work; not for a "
                      "client project or anything monetised.", ""]
        if licence.share_alike:
            lines += ["Share-alike: anything you publish using it must carry the same "
                      "licence.", ""]
        lines += ["Credit line to publish with your video:", "",
                  track.attribution_text(), "",
                  f"Source: {track.source}", track.page_url]
        self.detail.setPlainText("\n".join(line for line in lines if line is not None))

    def _copy_credit(self) -> None:
        track = self.current_track()
        if track is None:
            return
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(track.attribution_text())
        self.status.emit("Credit copied to the clipboard")

    def _open_page(self) -> None:
        track = self.current_track()
        if track and track.page_url:
            QDesktopServices.openUrl(QUrl(track.page_url))

    # -- download ---------------------------------------------------------------------------------
    def download_selected(self, use_it: bool = False) -> None:
        track = self.current_track()
        if track is None:
            return
        if self.context.project is None:
            QMessageBox.information(self, "No project", "Open a project first.")
            return
        if not track.licence.usable_in_video:
            QMessageBox.warning(
                self, "This track cannot be used",
                f"{track.licence.name}\n\nA soundtrack counts as an adaptation, and this "
                f"licence forbids adaptations. Downloading it would not make it legal to "
                f"use.",
            )
            return

        warning = ""
        if not track.licence.commercial:
            warning = ("\n\nThis track is NON-COMMERCIAL only. Do not use it for a client "
                       "or anything monetised.")
        if track.licence.attribution:
            warning += ("\n\nYou must publish this credit:\n" + track.attribution_text())

        answer = QMessageBox.question(
            self, "Download this track?",
            f"{track.title}\nby {track.artist or 'unknown'}\n\n"
            f"Licence: {track.licence.name}\n"
            f"Allows: {track.licence.summary()}\n"
            f"From: {track.source}{warning}\n\nDownload it now?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        project = self.context.require_project()
        folder = project.root / "music"
        safe = "".join(ch for ch in track.title if ch.isalnum() or ch in " -_")[:60].strip()
        destination = folder / f"{safe or 'track'}.mp3"
        counter = 1
        while destination.exists():
            destination = folder / f"{safe or 'track'}_{counter}.mp3"
            counter += 1

        proxy = self.context.settings.proxy_url
        context = self.context

        def work(job_context):
            path = musicsearch.download(
                track, destination, proxy=proxy,
                progress=lambda fraction, message: job_context.progress(fraction, message),
            )
            context.catalogue(path, source="music", label=f"{track.title} ({track.licence.code})")
            musicsearch.write_credits([track], folder / "CREDITS.txt")
            return {"path": path, "track": track, "use": use_it}

        job = self.context.jobs.submit(f"Download: {track.title[:40]}", work,
                                       category="music_download")
        self._job_id = job.id
        self.status.emit(f"Downloading {track.title}")

    def download_finished(self, result) -> None:
        path = result.get("path")
        track = result.get("track")
        if isinstance(path, Path) and path.exists():
            self._downloaded.append(track)
            self.preview.show_path(path)
            self.library_changed.emit()
            self.footer.setText(
                f"Saved to {path}. Credit written to music/CREDITS.txt."
            )
            if result.get("use"):
                self.music_chosen.emit(path)
                self.status.emit(f"Using {path.name} in the video")
