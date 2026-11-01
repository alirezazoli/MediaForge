"""Learning panel: see everything the app has inferred about you, and delete any of it.

A system that changes its behaviour based on you must be inspectable and reversible,
otherwise it is just unpredictability with extra steps.
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
    QSplitter, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from ..context import AppContext
from ..learning import MIN_OBSERVATIONS, MIN_SHARE

COLUMNS = ["Setting", "Scene", "Your habit", "Agreement", "Times seen", "Status"]


class LearningPanel(QWidget):
    status = Signal(str)

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self._preferences: list = []
        self._build()
        self.refresh()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)

        heading = QLabel("What the app has learned")
        heading.setObjectName("h1")
        layout.addWidget(heading)

        self.summary = QLabel("")
        self.summary.setObjectName("subtle")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        control_row = QHBoxLayout()
        self.enabled_check = QCheckBox("Learn from what I do")
        self.enabled_check.setToolTip(
            "Off: the app stops recording and stops adjusting its recommendations. "
            "Nothing already stored is deleted."
        )
        self.enabled_check.toggled.connect(self._toggle_enabled)
        control_row.addWidget(self.enabled_check)
        control_row.addStretch(1)
        for label, handler, style in (
            ("Refresh", self.refresh, "ghost"),
            ("Export to JSON", self._export, "ghost"),
            ("Open folder", self._open_folder, "ghost"),
            ("Forget selected", self._forget_selected, "danger"),
            ("Forget everything", self._forget_all, "danger"),
        ):
            button = QPushButton(label)
            if style:
                button.setObjectName(style)
            button.clicked.connect(handler)
            control_row.addWidget(button)
        layout.addLayout(control_row)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setMinimumHeight(220)
        splitter.addWidget(self.table)

        detail = QWidget()
        detail.setMinimumHeight(180)
        detail_layout = QVBoxLayout(detail)
        detail_layout.addWidget(QLabel("Detail"))
        self.report = QTextEdit()
        self.report.setReadOnly(True)
        detail_layout.addWidget(self.report)
        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        rules = QLabel(
            f"A habit is only applied once it has been seen at least {MIN_OBSERVATIONS} "
            f"times with a {MIN_SHARE:.0%} majority. Corrections you make count three times "
            f"as much as choices you simply accept, and everything fades by half every "
            f"60 days. Nothing is sent anywhere — this is a local database."
        )
        rules.setObjectName("faint")
        rules.setWordWrap(True)
        layout.addWidget(rules)

    # -- data --------------------------------------------------------------------------
    def refresh(self) -> None:
        store = self.context.learning
        self.enabled_check.blockSignals(True)
        self.enabled_check.setChecked(store.enabled)
        self.enabled_check.blockSignals(False)

        info = store.stats()
        since = ""
        if info.get("since"):
            days = max(0, int((time.time() - info["since"]) / 86400))
            since = f" over the last {days} day{'s' if days != 1 else ''}"
        self.summary.setText(
            f"{info['choices']} decisions recorded{since}, of which "
            f"{info['overrides']} were corrections you made. "
            f"{info['renders']} videos rendered "
            f"({info['total_shots']} shots, {info['total_seconds']:.0f} seconds). "
            f"Database is {info['size_kb']} KB."
        )

        self._preferences = store.all_preferences()
        self.table.setRowCount(0)
        for row, preference in enumerate(self._preferences):
            self.table.insertRow(row)
            first = QTableWidgetItem(preference.field)
            first.setData(Qt.ItemDataRole.UserRole, (preference.field, preference.scene))
            self.table.setItem(row, 0, first)
            self.table.setItem(row, 1, QTableWidgetItem(
                preference.scene.replace("_", " ") or "any"))
            self.table.setItem(row, 2, QTableWidgetItem(preference.value))
            self.table.setItem(row, 3, QTableWidgetItem(f"{preference.share:.0%}"))
            self.table.setItem(row, 4, QTableWidgetItem(str(preference.observations)))
            state = QTableWidgetItem("applied" if preference.confident else "watching")
            self.table.setItem(row, 5, state)

        self.report.setPlainText(store.report())
        if not self._preferences:
            self.status.emit("Nothing learned yet")

    # -- actions ---------------------------------------------------------------------------
    def _toggle_enabled(self, enabled: bool) -> None:
        self.context.learning.enabled = enabled
        self.context.settings.learning_enabled = enabled
        self.context.settings.save()
        self.status.emit("Learning on" if enabled else "Learning paused")
        self.refresh()

    def _forget_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._preferences):
            QMessageBox.information(self, "Nothing selected", "Pick a row to forget.")
            return
        preference = self._preferences[row]
        answer = QMessageBox.question(
            self, "Forget this habit",
            f"Delete every recorded '{preference.field}' choice for "
            f"{preference.scene.replace('_', ' ') or 'all scenes'}?\n\n"
            f"That is {preference.observations} decisions. This cannot be undone.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed = self.context.learning.forget(preference.field, preference.scene)
        self.refresh()
        self.status.emit(f"Forgot {removed} recorded choices")

    def _forget_all(self) -> None:
        answer = QMessageBox.question(
            self, "Forget everything",
            "Delete the entire learning database — every recorded choice and every "
            "render statistic?\n\nThis cannot be undone.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.context.learning.forget_everything()
        self.refresh()
        self.status.emit("Learning database cleared")

    def _export(self) -> None:
        target, _ = QFileDialog.getSaveFileName(
            self, "Export learning data", str(Path.home() / "mediaforge-learning.json"),
            "JSON (*.json)",
        )
        if not target:
            return
        path = self.context.learning.export(Path(target))
        self.status.emit(f"Exported to {path}")

    def _open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.context.learning.path.parent)))
