"""Keyboard shortcuts and the command palette.

Every action lives in one table so the palette, the menu and the key bindings can never
drift apart -- add a row and all three pick it up.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QSortFilterProxyModel, Qt
from PySide6.QtGui import QAction, QKeySequence, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QDialog, QLineEdit, QListView, QVBoxLayout, QWidget,
)


@dataclass
class Command:
    id: str
    title: str
    group: str
    shortcut: str
    handler: Callable[[], None]
    description: str = ""

    @property
    def search_text(self) -> str:
        return f"{self.group} {self.title} {self.description} {self.shortcut}".lower()


def build_commands(window) -> list[Command]:
    """The single source of truth for what the app can do from the keyboard."""
    def go(key: str) -> Callable[[], None]:
        return lambda: window.go_to(key)

    commands: list[Command] = [
        # -- file ------------------------------------------------------------------
        Command("file.new", "New project", "File", "Ctrl+N", window.new_project),
        Command("file.open", "Open project", "File", "Ctrl+O", window.open_project),
        Command("file.folder", "Open project folder", "File", "Ctrl+Shift+O",
                window.open_project_folder),
        Command("file.import", "Import media files", "File", "Ctrl+I",
                lambda: window.library_panel.import_files()),
        Command("file.import_folder", "Import a whole folder", "File", "Ctrl+Shift+I",
                lambda: window.library_panel.import_folder()),
        Command("file.quit", "Quit", "File", "Ctrl+Q", window.close),

        # -- navigation ------------------------------------------------------------
        Command("go.home", "Go to Home", "Go", "Ctrl+0", go("home")),
        Command("go.photos", "Go to Photos to video", "Go", "Ctrl+1", go("photos")),
        Command("go.edit", "Go to Edit", "Go", "Ctrl+2", go("edit")),
        Command("go.enhance", "Go to Enhance", "Go", "Ctrl+3", go("enhance")),
        Command("go.repair", "Go to Repair & upscale", "Go", "Ctrl+Shift+U", go("repair")),
        Command("go.batch", "Go to Batch repair", "Go", "Ctrl+Shift+F", go("batch")),
        Command("go.generate", "Go to Generate", "Go", "Ctrl+4", go("generate")),
        Command("go.director", "Go to Director", "Go", "Ctrl+5", go("director")),
        Command("go.agent", "Go to the Assistant", "Go", "Ctrl+/", go("agent")),
        Command("go.music", "Go to Find music", "Go", "Ctrl+M", go("music")),
        Command("go.library", "Go to Library", "Go", "Ctrl+6", go("library")),
        Command("go.timeline", "Go to Timeline", "Go", "Ctrl+7", go("timeline")),
        Command("go.templates", "Go to Templates", "Go", "Ctrl+8", go("templates")),
        Command("go.hub", "Go to Model Hub", "Go", "Ctrl+9", go("hub")),
        Command("go.learning", "Go to Learning", "Go", "Ctrl+L", go("learning")),
        Command("go.jobs", "Go to Jobs", "Go", "Ctrl+J", go("jobs")),
        Command("go.settings", "Go to Settings", "Go", "Ctrl+,", go("settings")),
        Command("go.next", "Next section", "Go", "Ctrl+Tab", lambda: window.go_relative(1)),
        Command("go.prev", "Previous section", "Go", "Ctrl+Shift+Tab",
                lambda: window.go_relative(-1)),
        Command("go.advanced", "Show or hide advanced sections", "Go", "Ctrl+Shift+A",
                window.toggle_advanced),
        Command("go.sidebar", "Hide or show the sidebar", "Go", "Ctrl+\\",
                window.toggle_sidebar, "Gives the panels the full window width"),

        # -- photos to video --------------------------------------------------------
        Command("photos.add", "Add photos to the clip", "Photos", "Ctrl+Shift+P",
                lambda: window.photo_panel.add_photos()),
        Command("photos.create", "Create the photo clip", "Photos", "Ctrl+Shift+Return",
                lambda: window.photo_panel.create()),
        Command("photos.auto", "Auto: decide everything from the photos", "Photos", "Ctrl+B",
                lambda: window.photo_panel.run_autopilot(),
                "Analyses each photo and picks move, atmosphere, pace and AI model"),
        Command("photos.analyse", "Re-analyse the selected photo", "Photos", "Ctrl+Shift+B",
                lambda: window.photo_panel._analyse_current()),

        # -- create ----------------------------------------------------------------
        Command("run.generate", "Generate now", "Create", "Ctrl+Return",
                lambda: window.generate_panel.generate()),
        Command("run.director", "Run the Director pipeline", "Create", "Ctrl+Shift+D",
                lambda: window.director_panel.run(plan_only=False)),
        Command("run.shotlist", "Write a shotlist only", "Create", "Ctrl+Alt+D",
                lambda: window.director_panel.run(plan_only=True)),

        # -- edit / render ---------------------------------------------------------
        Command("run.edit", "Run the selected edit operation", "Edit", "Ctrl+R",
                lambda: window.edit_panel.run()),
        Command("run.enhance", "Run the selected enhance operation", "Edit", "Ctrl+Shift+R",
                lambda: window.enhance_panel.run()),
        Command("edit.send", "Send library selection to Edit", "Edit", "Ctrl+E",
                window.send_selection_to_edit),
        Command("enhance.send", "Send library selection to Enhance", "Edit", "Ctrl+Shift+E",
                window.send_selection_to_enhance),
        Command("repair.scan", "Analyse the video for noise and dead air", "Edit", "Ctrl+U",
                lambda: window.repair_panel.scan(),
                "Measures noise and softness, and finds black silent sections"),
        Command("repair.run", "Repair the analysed video", "Edit", "Ctrl+Alt+U",
                lambda: window.repair_panel.run()),
        Command("repair.send", "Send library selection to Repair", "Edit", "Ctrl+Alt+E",
                window.send_selection_to_repair),
        Command("batch.scan", "Analyse every file in the batch", "Edit", "Ctrl+Alt+B",
                lambda: window.batch_panel.scan()),
        Command("batch.run", "Repair the ticked batch files", "Edit", "Ctrl+Alt+R",
                lambda: window.batch_panel.run()),
        Command("timeline.add", "Add selection to the timeline", "Timeline", "Ctrl+T",
                lambda: window.library_panel._add_to_timeline()),
        Command("timeline.render", "Render the timeline", "Timeline", "Ctrl+Shift+X",
                lambda: window.timeline_panel.render()),
        Command("timeline.up", "Move clip earlier", "Timeline", "Ctrl+Up",
                lambda: window.timeline_panel._move(-1)),
        Command("timeline.down", "Move clip later", "Timeline", "Ctrl+Down",
                lambda: window.timeline_panel._move(1)),
        Command("timeline.remove", "Remove clip from the timeline", "Timeline", "Ctrl+Delete",
                lambda: window.timeline_panel._remove()),

        # -- playback --------------------------------------------------------------
        Command("play.toggle", "Play / pause the preview", "Playback", "Space",
                window.toggle_playback),
        Command("play.stop", "Stop playback", "Playback", "Ctrl+.", window.stop_playback),

        # -- jobs ------------------------------------------------------------------
        Command("jobs.cancel", "Cancel the selected job", "Jobs", "Ctrl+Shift+C",
                lambda: window.jobs_panel._cancel_selected()),
        Command("jobs.clear", "Clear finished jobs", "Jobs", "Ctrl+Shift+K",
                lambda: window.jobs_panel._clear_finished()),

        # -- app -------------------------------------------------------------------
        Command("app.palette", "Command palette", "App", "Ctrl+K", window.show_command_palette,
                "Search every action in the app"),
        Command("app.refresh", "Refresh library", "App", "F5", window.refresh_all),
        Command("app.reload_catalog", "Reload the model catalogue", "App", "Ctrl+Shift+L",
                lambda: window.hub_panel._reload_catalog()),
        Command("app.shortcuts", "Show all keyboard shortcuts", "App", "F1",
                window.show_shortcuts),
        Command("app.fullscreen", "Toggle full screen", "App", "F11", window.toggle_fullscreen),
        Command("app.theme", "Switch light / dark theme", "App", "Ctrl+Shift+T",
                window.toggle_theme),
        Command("app.reset_sidebar", "Reset the sidebar order", "App", "",
                lambda: window.sidebar.reset_order()),
    ]
    return commands


def install(window, commands: list[Command]) -> None:
    """Registers every command as a window-level QAction so the keys work anywhere."""
    for command in commands:
        action = QAction(command.title, window)
        if command.shortcut:
            action.setShortcut(QKeySequence(command.shortcut))
        action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        action.triggered.connect(command.handler)
        window.addAction(action)


class CommandPalette(QDialog):
    """Ctrl+K: type a few letters, hit Enter."""

    def __init__(self, commands: list[Command], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Command palette")
        self.setModal(True)
        self.resize(640, 460)
        self._commands = commands

        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Type a command...")
        layout.addWidget(self.search)

        self.model = QStandardItemModel()
        for index, command in enumerate(commands):
            item = QStandardItem(
                f"{command.group}  ·  {command.title}"
                + (f"        {command.shortcut}" if command.shortcut else "")
            )
            item.setData(index, Qt.ItemDataRole.UserRole)
            item.setEditable(False)
            self.model.appendRow(item)

        self.proxy = QSortFilterProxyModel()
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

        self.view = QListView()
        self.view.setModel(self.proxy)
        self.view.setEditTriggers(QListView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.view, 1)

        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._activate_first)
        self.view.activated.connect(self._activate_index)
        self.search.setFocus()
        if self.proxy.rowCount():
            self.view.setCurrentIndex(self.proxy.index(0, 0))

    def _filter(self, text: str) -> None:
        self.proxy.setFilterFixedString(text.strip())
        if self.proxy.rowCount():
            self.view.setCurrentIndex(self.proxy.index(0, 0))

    def _activate_first(self) -> None:
        if self.proxy.rowCount():
            self._activate_index(self.view.currentIndex()
                                 if self.view.currentIndex().isValid()
                                 else self.proxy.index(0, 0))

    def _activate_index(self, index) -> None:
        if not index.isValid():
            return
        source = self.proxy.mapToSource(index)
        command_index = self.model.itemFromIndex(source).data(Qt.ItemDataRole.UserRole)
        self.accept()
        self._commands[command_index].handler()

    def keyPressEvent(self, event) -> None:
        # Down/Up from the search box should drive the list, not move the caret.
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up) and self.search.hasFocus():
            self.view.setFocus()
            self.view.keyPressEvent(event)
            return
        super().keyPressEvent(event)


def shortcuts_html(commands: list[Command]) -> str:
    groups: dict[str, list[Command]] = {}
    for command in commands:
        groups.setdefault(command.group, []).append(command)

    parts = ["<style>td{padding:2px 14px 2px 0}</style>"]
    for group, entries in groups.items():
        parts.append(f"<h3 style='margin-bottom:4px'>{group}</h3><table>")
        for command in entries:
            key = command.shortcut or "-"
            parts.append(
                f"<tr><td><code>{key}</code></td><td>{command.title}</td></tr>"
            )
        parts.append("</table>")
    return "".join(parts)
