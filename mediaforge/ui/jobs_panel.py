"""Jobs panel: live progress, cancellation and error detail for every background task."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QProgressBar, QPushButton, QTableWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from ..context import AppContext
from ..jobs import Job, JobState
from . import theme

COLUMNS = ["Job", "Title", "State", "Progress", "Message"]

def state_colour(state: JobState) -> str:
    return {
        JobState.QUEUED: theme.TEXT_MUTED,
        JobState.RUNNING: theme.ACCENT,
        JobState.DONE: theme.SUCCESS,
        JobState.FAILED: theme.DANGER,
        JobState.CANCELLED: theme.WARNING,
    }.get(state, theme.TEXT_MUTED)


class JobsPanel(QWidget):
    status = Signal(str)

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self._rows: dict[int, int] = {}
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        cancel_button = QPushButton("Cancel selected")
        cancel_button.clicked.connect(self._cancel_selected)
        clear_button = QPushButton("Clear finished")
        clear_button.clicked.connect(self._clear_finished)
        toolbar.addWidget(cancel_button)
        toolbar.addWidget(clear_button)
        toolbar.addStretch(1)
        self.summary_label = QLabel("No jobs")
        toolbar.addWidget(self.summary_label)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.currentCellChanged.connect(self._show_detail)
        layout.addWidget(self.table, 2)

        layout.addWidget(QLabel("Detail"))
        self.detail_box = QTextEdit()
        self.detail_box.setReadOnly(True)
        self.detail_box.setPlaceholderText("Select a job to see its result or full error.")
        layout.addWidget(self.detail_box, 1)

    def on_job_changed(self, job: Job) -> None:
        if job.id not in self._rows:
            row = 0
            self.table.insertRow(row)
            for existing_id in self._rows:
                self._rows[existing_id] += 1
            self._rows[job.id] = row
            identifier = QTableWidgetItem(str(job.id))
            identifier.setData(Qt.ItemDataRole.UserRole, job.id)
            self.table.setItem(row, 0, identifier)
            self.table.setItem(row, 1, QTableWidgetItem(job.title))
            self.table.setItem(row, 2, QTableWidgetItem(""))
            self.table.setCellWidget(row, 3, QProgressBar())
            self.table.setItem(row, 4, QTableWidgetItem(""))

        row = self._rows[job.id]
        state_item = self.table.item(row, 2)
        if state_item:
            from PySide6.QtGui import QBrush, QColor

            state_item.setText(job.state.value)
            # Colour the state itself rather than forcing white, which vanishes on light.
            state_item.setForeground(QBrush(QColor(state_colour(job.state))))
        bar = self.table.cellWidget(row, 3)
        if isinstance(bar, QProgressBar):
            bar.setValue(int(job.progress * 100))
        message_item = self.table.item(row, 4)
        if message_item:
            message_item.setText(job.message)

        active = self.context.jobs.active_count()
        self.summary_label.setText(f"{active} active" if active else "Idle")
        if self.table.currentRow() == row:
            self._show_detail()

    def _selected_job(self) -> Job | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if not item:
            return None
        job_id = item.data(Qt.ItemDataRole.UserRole)
        return next((job for job in self.context.jobs.jobs() if job.id == job_id), None)

    def _show_detail(self) -> None:
        job = self._selected_job()
        if not job:
            self.detail_box.clear()
            return
        parts = [f"Job {job.id}: {job.title}", f"State: {job.state.value}", ""]
        if job.error:
            parts += ["ERROR", job.error, "", "Traceback", job.traceback]
        elif job.result is not None:
            parts += ["Result", str(job.result)[:4000]]
        else:
            parts.append(job.message)
        self.detail_box.setPlainText("\n".join(parts))

    def _cancel_selected(self) -> None:
        job = self._selected_job()
        if job and self.context.jobs.cancel(job.id):
            self.status.emit(f"Cancelling job {job.id}")

    def _clear_finished(self) -> None:
        self.context.jobs.clear_finished()
        self.table.setRowCount(0)
        self._rows.clear()
        self.detail_box.clear()
