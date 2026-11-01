"""Assistant screen: chat with a local model that can actually operate the app."""

from __future__ import annotations

import html

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSplitter,
    QTextBrowser, QVBoxLayout, QWidget,
)

from ..agent import Agent, AgentEvent, AgentMemory, Tool
from ..agent_tools import build_tools
from ..context import AppContext
from . import theme

SUGGESTIONS = [
    "What can you do?",
    "Look at my photos and tell me what you see",
    "Plan a 20 second vertical video, camera moves only",
    "Make everything rainy and moody",
    "Which video models can I actually use right now?",
    "What have you learned about how I work?",
]


class AgentWorker(QObject):
    """Runs the agent loop off the UI thread, forwarding each event as a signal."""

    event = Signal(object)
    finished = Signal()

    def __init__(self, agent: Agent, message: str) -> None:
        super().__init__()
        self.agent = agent
        self.message = message
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            for item in self.agent.run(self.message, should_cancel=lambda: self._cancelled):
                self.event.emit(item)
        except Exception as error:
            self.event.emit(AgentEvent("error", f"{type(error).__name__}: {error}"))
        finally:
            self.finished.emit()


class AgentPanel(QWidget):
    status = Signal(str)

    def __init__(self, context: AppContext, photo_panel, main_window) -> None:
        super().__init__()
        self.context = context
        self.photo_panel = photo_panel
        # Tools need a way back to navigation without importing the window everywhere.
        setattr(photo_panel, "_main_window", main_window)

        self.memory = AgentMemory()
        self.agent = Agent(context, build_tools(context, photo_panel), self.memory)
        self.agent.confirm_hook = self._confirm
        self._thread: QThread | None = None
        self._worker: AgentWorker | None = None

        self._build()
        self.refresh()
        self._load_history()

    # -- construction -----------------------------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        heading = QLabel("Assistant")
        heading.setObjectName("h1")
        header.addWidget(heading)
        header.addStretch(1)
        header.addWidget(QLabel("Model"))
        self.model_box = QComboBox()
        self.model_box.setMinimumWidth(240)
        self.model_box.currentIndexChanged.connect(self._model_changed)
        header.addWidget(self.model_box)
        self.start_button = QPushButton("Start Ollama")
        self.start_button.clicked.connect(self._start_server)
        header.addWidget(self.start_button)
        refresh_button = QPushButton("Refresh")
        refresh_button.setObjectName("ghost")
        refresh_button.clicked.connect(self.refresh)
        header.addWidget(refresh_button)
        layout.addLayout(header)

        self.banner = QLabel("")
        self.banner.setWordWrap(True)
        layout.addWidget(self.banner)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)

        self.transcript = QTextBrowser()
        self.transcript.setOpenExternalLinks(True)
        self.transcript.setMinimumHeight(300)
        splitter.addWidget(self.transcript)

        lower = QWidget()
        lower.setMinimumHeight(120)
        lower_layout = QVBoxLayout(lower)
        lower_layout.setContentsMargins(0, 0, 0, 0)

        suggestion_row = QHBoxLayout()
        for text in SUGGESTIONS[:3]:
            button = QPushButton(text)
            button.setObjectName("ghost")
            button.clicked.connect(lambda _=False, t=text: self._send(t))
            suggestion_row.addWidget(button)
        suggestion_row.addStretch(1)
        lower_layout.addLayout(suggestion_row)

        suggestion_row2 = QHBoxLayout()
        for text in SUGGESTIONS[3:]:
            button = QPushButton(text)
            button.setObjectName("ghost")
            button.clicked.connect(lambda _=False, t=text: self._send(t))
            suggestion_row2.addWidget(button)
        suggestion_row2.addStretch(1)
        lower_layout.addLayout(suggestion_row2)

        input_row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask it to do something with your photos...")
        self.input.returnPressed.connect(lambda: self._send(self.input.text()))
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("primary")
        self.send_button.clicked.connect(lambda: self._send(self.input.text()))
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("danger")
        self.stop_button.setVisible(False)
        self.stop_button.clicked.connect(self._stop)
        clear_button = QPushButton("Clear chat")
        clear_button.setObjectName("ghost")
        clear_button.clicked.connect(self._clear)
        input_row.addWidget(self.input, 1)
        input_row.addWidget(self.send_button)
        input_row.addWidget(self.stop_button)
        input_row.addWidget(clear_button)
        lower_layout.addLayout(input_row)

        self.footer = QLabel("")
        self.footer.setObjectName("faint")
        self.footer.setWordWrap(True)
        lower_layout.addWidget(self.footer)

        splitter.addWidget(lower)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

    # -- state --------------------------------------------------------------------------------
    def refresh(self) -> None:
        provider = self.context.registry.get("ollama")
        ok, reason = self.agent.available()

        self.model_box.blockSignals(True)
        self.model_box.clear()
        models = provider.list_models(force=True) if provider and provider.reachable() else []
        for model in models:
            self.model_box.addItem(model.label(), model.name)
            if not model.supports_tools:
                index = self.model_box.count() - 1
                item = self.model_box.model().item(index)
                if item is not None:
                    item.setToolTip("This model cannot call tools — conversation only.")
        preferred = self.context.settings.agent_model
        if preferred:
            index = self.model_box.findData(preferred)
            if index >= 0:
                self.model_box.setCurrentIndex(index)
        self.model_box.blockSignals(False)

        self.start_button.setVisible(bool(provider and provider.installed and not ok))
        self.send_button.setEnabled(ok)

        if ok:
            model = next((m for m in models if m.name == self.agent.chosen_model()), None)
            if model and not model.supports_tools:
                self._set_banner(
                    f"{model.name} can talk but cannot operate the app. Pick a model "
                    f"marked [tools] to let it actually do things.", theme.WARNING)
            else:
                self._set_banner(
                    f"Ready — {self.agent.chosen_model()} running locally, offline, "
                    f"and it can operate the app.", theme.FREE)
        else:
            self._set_banner(reason, theme.WARNING)

        count = self.memory.count(self.context.project_key)
        self.footer.setText(
            f"{len(self.agent.tools)} tools available. {count} messages remembered for this "
            f"project — history is kept between sessions. Anything that writes a file or "
            f"costs money asks first."
        )

    def _set_banner(self, text: str, colour: str) -> None:
        self.banner.setText(text)
        self.banner.setStyleSheet(
            # Blended to plain hex: Qt reads "#RRGGBB" + two more digits as #AARRGGBB, which
            # turned this banner into a maroon band with unreadable text.
            f"background:{theme.tint(colour, 0.12)}; color:{theme.TEXT};"
            f"border:1px solid {theme.tint(colour, 0.40)}; border-radius:8px; padding:8px;"
        )

    def _model_changed(self) -> None:
        name = self.model_box.currentData()
        if not name:
            return
        self.agent.model = name
        self.context.settings.agent_model = name
        self.context.settings.save()
        self.refresh()

    def _start_server(self) -> None:
        provider = self.context.registry.get("ollama")
        if provider is None:
            return
        self.status.emit("Starting Ollama...")
        self._append("system", "Starting the local model server...")
        if provider.start_server():
            self._append("system", "Ollama is running.")
            self.status.emit("Ollama started")
        else:
            self._append("error", "Could not start Ollama. Try running `ollama serve` "
                                  "in a terminal.")
        self.refresh()

    # -- conversation ---------------------------------------------------------------------------
    def _load_history(self) -> None:
        for row in self.memory.transcript(self.context.project_key, limit=60):
            self._append(row["role"], row["content"])

    def _append(self, role: str, text: str, tool: str = "") -> None:
        colours = {
            "user": theme.ACCENT, "assistant": theme.TEXT, "system": theme.TEXT_MUTED,
            "error": theme.DANGER, "tool": theme.FREE, "status": theme.TEXT_FAINT,
        }
        names = {"user": "You", "assistant": "Assistant", "system": "App",
                 "error": "Problem", "tool": tool or "tool", "status": ""}
        colour = colours.get(role, theme.TEXT)
        label = names.get(role, role)
        body = html.escape(text).replace("\n", "<br>")

        if role == "status":
            self.transcript.append(
                f"<div style='color:{theme.TEXT_FAINT}; font-size:11px'>{body}</div>")
        elif role == "tool":
            self.transcript.append(
                f"<div style='margin:4px 0'>"
                f"<span style='color:{theme.FREE}; font-weight:600'>▸ {html.escape(label)}</span>"
                f"<div style='color:{theme.TEXT_MUTED}; font-family:Consolas,monospace;"
                f"font-size:11px; white-space:pre-wrap'>{body}</div></div>")
        else:
            self.transcript.append(
                f"<div style='margin:6px 0'>"
                f"<b style='color:{colour}'>{html.escape(label)}</b><br>"
                f"<span style='color:{theme.TEXT}'>{body}</span></div>")
        self.transcript.verticalScrollBar().setValue(
            self.transcript.verticalScrollBar().maximum())

    def _send(self, text: str) -> None:
        text = (text or "").strip()
        if not text or self._thread is not None:
            return
        ok, reason = self.agent.available()
        if not ok:
            QMessageBox.information(self, "Assistant unavailable", reason)
            return

        self.input.clear()
        self._append("user", text)
        self._set_busy(True)

        self._thread = QThread()
        self._worker = AgentWorker(self.agent, text)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.event.connect(self._on_event)
        self._worker.finished.connect(self._on_finished)
        self._thread.start()

    def _on_event(self, event: AgentEvent) -> None:
        if event.kind == "status":
            self._append("status", event.text)
        elif event.kind == "tool_call":
            arguments = ", ".join(f"{key}={value!r}" for key, value in event.arguments.items())
            self._append("tool", f"calling with ({arguments})" if arguments else "calling",
                         tool=event.tool)
        elif event.kind == "tool_result":
            self._append("tool", event.text[:1200], tool=f"{event.tool} result")
        elif event.kind == "answer":
            self._append("assistant", event.text)
        elif event.kind == "error":
            self._append("error", event.text)

    def _on_finished(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)
        self._thread = None
        self._worker = None
        self._set_busy(False)
        self.refresh()

    def _set_busy(self, busy: bool) -> None:
        self.send_button.setVisible(not busy)
        self.stop_button.setVisible(busy)
        self.input.setEnabled(not busy)

    def _stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._append("system", "Stopping after the current step...")

    def _clear(self) -> None:
        if QMessageBox.question(
            self, "Clear chat",
            "Delete the conversation history for this project?",
        ) != QMessageBox.StandardButton.Yes:
            return
        self.memory.clear(self.context.project_key)
        self.transcript.clear()
        self.refresh()

    # -- confirmation -----------------------------------------------------------------------------
    def _confirm(self, tool: Tool, arguments: dict) -> bool:
        """Called from the worker thread; Qt dialogs need the UI thread, so this is
        marshalled by blocking on a queued call."""
        from PySide6.QtCore import QMetaObject, Qt as QtCore_Qt

        # Simpler and safe: build the text here, ask on the UI thread via invokeMethod.
        detail = ", ".join(f"{key}={value}" for key, value in arguments.items())
        self._pending_question = (
            f"The assistant wants to run '{tool.name}'.\n\n{tool.description}\n"
            f"{tool.danger_note}\n\n{('Arguments: ' + detail) if detail else ''}\n\nAllow it?"
        )
        QMetaObject.invokeMethod(self, "_ask_confirm",
                                 QtCore_Qt.ConnectionType.BlockingQueuedConnection)
        return bool(getattr(self, "_confirm_answer", False))

    def _ask_confirm(self) -> None:
        answer = QMessageBox.question(
            self, "Allow this action?", getattr(self, "_pending_question", ""),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        self._confirm_answer = answer == QMessageBox.StandardButton.Yes


# Qt needs the slot registered for invokeMethod to find it by name.
from PySide6.QtCore import Slot  # noqa: E402

AgentPanel._ask_confirm = Slot()(AgentPanel._ask_confirm)
