"""Main window: wires the panels, the job queue and the menu together."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMainWindow, QMessageBox, QProgressBar,
    QStackedWidget, QWidget,
)

from ..agents import DirectorResult
from ..context import AppContext
from ..jobs import Job, JobState
from ..providers import GenerationResult
from . import shortcuts as shortcuts_module
from . import icons
from . import theme
from .agent_panel import AgentPanel
from .batch_panel import BatchPanel
from .director_panel import DirectorPanel
from .edit_panel import EditPanel
from .enhance_panel import EnhancePanel
from .generate_panel import GeneratePanel
from .home_panel import HomePanel
from .hub_panel import HubPanel
from .jobs_panel import JobsPanel
from .learning_panel import LearningPanel
from .library_panel import LibraryPanel
from .music_panel import MusicPanel
from .photo_clip_panel import PhotoClipPanel
from .repair_panel import RepairPanel
from .settings_panel import SettingsPanel
from . import sidebar as sidebar_module
from .sidebar import Sidebar
from .templates_panel import TemplatesPanel
from .timeline_panel import TimelinePanel
from .widgets import JobBridge

# Visual design lives in theme.py so every panel shares one set of tokens.


class MainWindow(QMainWindow):
    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self.setWindowTitle("MediaForge Studio")
        # Three columns whose own minimums add up to about 1280, plus the sidebar. Set
        # below that and Qt does not shrink them, it clips them -- which is exactly the
        # "windows inside each other" the layout used to show.
        self.setMinimumSize(1500, 760)
        self.resize(1680, 1000)
        self.setStyleSheet(theme.set_mode(context.settings.theme))

        self.bridge = JobBridge()
        self.bridge.job_changed.connect(self._on_job_changed, Qt.ConnectionType.QueuedConnection)
        self.context.jobs.add_listener(self.bridge.listener)

        self._build_panels()
        self.commands = shortcuts_module.build_commands(self)
        shortcuts_module.install(self, self.commands)
        self._build_menu()
        self._build_statusbar()
        self._open_last_or_default()

    # -- construction --------------------------------------------------------------------
    def _build_panels(self) -> None:
        self.stack = QStackedWidget()
        self.sidebar = Sidebar(
            order=self.context.settings.sidebar_order,
            mode=self.context.settings.theme,
        )
        self.sidebar.selected.connect(self.go_to)
        self.sidebar.order_changed.connect(self._save_sidebar_order)
        self.sidebar.theme_changed.connect(self.apply_theme)
        self.sidebar.collapsed_changed.connect(self._on_sidebar_collapsed)

        self.home_panel = HomePanel(self.context)
        self.photo_panel = PhotoClipPanel(self.context)
        self.library_panel = LibraryPanel(self.context)
        self.generate_panel = GeneratePanel(self.context)
        self.edit_panel = EditPanel(self.context)
        self.enhance_panel = EnhancePanel(self.context)
        self.repair_panel = RepairPanel(self.context)
        self.batch_panel = BatchPanel(self.context)
        self.timeline_panel = TimelinePanel(self.context)
        self.director_panel = DirectorPanel(self.context)
        self.templates_panel = TemplatesPanel(self.context)
        self.hub_panel = HubPanel(self.context)
        self.music_panel = MusicPanel(self.context)
        self.learning_panel = LearningPanel(self.context)
        self.jobs_panel = JobsPanel(self.context)
        self.settings_panel = SettingsPanel(self.context)
        self.agent_panel = AgentPanel(self.context, self.photo_panel, self)

        self.panels: dict[str, QWidget] = {
            "home": self.home_panel,
            "photos": self.photo_panel,
            "edit": self.edit_panel,
            "enhance": self.enhance_panel,
            "repair": self.repair_panel,
            "batch": self.batch_panel,
            "generate": self.generate_panel,
            "director": self.director_panel,
            "agent": self.agent_panel,
            "music": self.music_panel,
            "library": self.library_panel,
            "timeline": self.timeline_panel,
            "templates": self.templates_panel,
            "hub": self.hub_panel,
            "learning": self.learning_panel,
            "jobs": self.jobs_panel,
            "settings": self.settings_panel,
        }
        for panel in self.panels.values():
            self.stack.addWidget(panel)

        central = QWidget()
        central_layout = QHBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self.sidebar)
        central_layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.home_panel.navigate.connect(self.go_to)
        self.home_panel.status.connect(self._set_status)
        self.photo_panel.status.connect(self._set_status)
        self.photo_panel.library_changed.connect(self._refresh_library)
        self.library_panel.send_to_edit.connect(self._send_to_edit)
        self.library_panel.status.connect(self._set_status)
        self.generate_panel.status.connect(self._set_status)
        self.generate_panel.library_changed.connect(self._refresh_library)
        self.generate_panel.set_selection_provider(self.library_panel.selected_assets)
        self.generate_panel.open_settings.connect(lambda: self.go_to("settings"))
        self.edit_panel.status.connect(self._set_status)
        self.edit_panel.library_changed.connect(self._refresh_library)
        self.enhance_panel.status.connect(self._set_status)
        self.enhance_panel.library_changed.connect(self._refresh_library)
        self.repair_panel.status.connect(self._set_status)
        self.repair_panel.library_changed.connect(self._refresh_library)
        self.batch_panel.status.connect(self._set_status)
        self.batch_panel.library_changed.connect(self._refresh_library)
        self.timeline_panel.status.connect(self._set_status)
        self.director_panel.status.connect(self._set_status)
        self.director_panel.library_changed.connect(self._refresh_library)
        self.templates_panel.status.connect(self._set_status)
        self.templates_panel.timeline_changed.connect(self._on_template_applied)
        self.templates_panel.brief_requested.connect(self._apply_brief)
        self.templates_panel.set_selection_provider(self.library_panel.selected_assets)
        self.hub_panel.status.connect(self._set_status)
        self.hub_panel.catalog_changed.connect(self._on_providers_reloaded)
        self.music_panel.status.connect(self._set_status)
        self.music_panel.library_changed.connect(self._refresh_library)
        self.music_panel.music_chosen.connect(self._use_music)
        self.learning_panel.status.connect(self._set_status)
        self.agent_panel.status.connect(self._set_status)
        self.jobs_panel.status.connect(self._set_status)
        self.settings_panel.status.connect(self._set_status)
        self.settings_panel.providers_reloaded.connect(self._on_providers_reloaded)

        self.stack.currentChanged.connect(self._on_panel_changed)
        self.go_to("home")

    # -- navigation ------------------------------------------------------------------------
    def go_to(self, key: str) -> None:
        panel = self.panels.get(key)
        if panel is None:
            return
        self.stack.setCurrentWidget(panel)
        self.sidebar.select(key)

    def key_for(self, panel) -> str:
        return next((key for key, value in self.panels.items() if value is panel), "")

    def go_to_panel(self, panel) -> None:
        self.go_to(self.key_for(panel))

    def go_relative(self, delta: int) -> None:
        """Cycles only through what the sidebar is currently showing."""
        advanced = self.sidebar.mode_button.isChecked()
        visible = [key for key in self.panels
                   if advanced or sidebar_module.SECTION_OF.get(key) != "advanced"]
        current = self.key_for(self.stack.currentWidget())
        index = visible.index(current) if current in visible else 0
        self.go_to(visible[(index + delta) % len(visible)])

    def _build_menu(self) -> None:
        """Menus are generated from the command table, so a shortcut can never disagree
        with its menu entry -- and every command is discoverable in three places."""
        by_group: dict[str, list] = {}
        for command in self.commands:
            by_group.setdefault(command.group, []).append(command)

        for group in ("File", "Go", "Photos", "Create", "Edit", "Timeline", "Playback",
                      "Jobs", "App"):
            entries = by_group.get(group, [])
            if not entries:
                continue
            menu = self.menuBar().addMenu(f"&{group}")
            for command in entries:
                action = QAction(command.title, self)
                if command.shortcut:
                    # The window-level QAction owns the key; showing it here would make
                    # Qt report an ambiguous shortcut, so set text only.
                    action.setShortcut(QKeySequence(command.shortcut))
                    action.setShortcutVisibleInContextMenu(True)
                    action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
                action.triggered.connect(command.handler)
                menu.addAction(action)
            if group == "File":
                menu.addSeparator()
                self.recent_menu = menu.addMenu("Recent projects")
                self._rebuild_recent_menu()

        help_menu = self.menuBar().addMenu("&Help")
        for label, handler in (
            ("Keyboard shortcuts (F1)", self.show_shortcuts),
            ("Command palette (Ctrl+K)", self.show_command_palette),
            ("Provider status", self.settings_panel._show_status),
            ("Which models are free?", self._show_openness_help),
            ("Where are my files?", self._explain_layout),
            ("Getting started", self._show_help),
        ):
            action = QAction(label, self)
            action.triggered.connect(handler)
            help_menu.addAction(action)

    def _build_statusbar(self) -> None:
        self.status_label = QLabel("Ready")
        self.job_bar = QProgressBar()
        self.job_bar.setMaximumWidth(220)
        self.job_bar.setVisible(False)
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(self.job_bar)

    # -- projects -------------------------------------------------------------------------
    def _open_last_or_default(self) -> None:
        recent = self.context.settings.recent_projects
        target = Path(recent[0]) if recent and Path(recent[0]).exists() else None
        if target is None:
            target = self.context.settings.workspace_path / "Untitled Project"
        self._load_project(target)

    def _load_project(self, root: Path) -> None:
        try:
            project = self.context.open_project(root)
        except Exception as error:
            QMessageBox.critical(self, "Could not open project", str(error))
            return
        self.setWindowTitle(f"MediaForge Studio - {project.name}")
        self._refresh_library()
        self.timeline_panel.refresh()
        self.home_panel.refresh()
        self._rebuild_recent_menu()
        self._set_status(f"Project: {project.root}")

    def new_project(self) -> None:
        name, accepted = QInputDialog.getText(self, "New project", "Project name")
        if not accepted or not name.strip():
            return
        safe = "".join(character for character in name.strip()
                       if character.isalnum() or character in " -_").strip()
        self._load_project(self.context.settings.workspace_path / (safe or "Untitled Project"))

    def open_project(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Open project folder", self.context.settings.workspace
        )
        if folder:
            self._load_project(Path(folder))

    def open_project_folder(self) -> None:
        if self.context.project:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.context.project.root)))

    def _rebuild_recent_menu(self) -> None:
        self.recent_menu.clear()
        for path in self.context.settings.recent_projects:
            action = QAction(path, self)
            action.triggered.connect(lambda _=False, p=path: self._load_project(Path(p)))
            self.recent_menu.addAction(action)

    # -- job plumbing -----------------------------------------------------------------------
    def _on_job_changed(self, job: Job) -> None:
        self.jobs_panel.on_job_changed(job)
        active = self.context.jobs.active_count()
        self.job_bar.setVisible(active > 0)
        self.sidebar.badge_jobs(active)
        if active > 0:
            self.job_bar.setValue(int(job.progress * 100))
            self.job_bar.setFormat(f"{job.title[:28]} %p%")

        # The photo panel shows its own progress in place; it should never yank the user
        # to the Jobs screen, not even on failure.
        if job.category == "music":
            if job.state == JobState.DONE and job.result is not None:
                self.music_panel.show_results(job.result)
            elif job.state == JobState.FAILED:
                self.music_panel.search_failed(job.error)
            self._set_status(f"{job.title}: {job.message}")
            return
        if job.category == "music_download":
            if job.state == JobState.DONE and isinstance(job.result, dict):
                self.music_panel.download_finished(job.result)
            elif job.state == JobState.FAILED:
                self.music_panel.footer.setText(f"Download failed: {job.error[:200]}")
            self._set_status(f"{job.title}: {job.message}")
            return
        if job.category == "repair_scan":
            # Analysing is read-only, so a failure belongs on the panel that asked for it
            # rather than as a trip to the Jobs screen.
            if job.state == JobState.DONE and job.result is not None:
                self.repair_panel.show_report(job.result)
            elif job.state == JobState.FAILED:
                self.repair_panel.scan_failed(job.error)
            self._set_status(f"{job.title}: {job.message}")
            return
        if job.category == "batch_scan":
            if job.state == JobState.DONE and job.result is not None:
                self.batch_panel.show_scan(job.result)
            elif job.state == JobState.FAILED:
                self.batch_panel.scan_failed(job.error)
            self._set_status(f"{job.title}: {job.message}")
            return
        if job.category == "batch_repair":
            # A cancelled batch still finished real files; its outcome is a result, not
            # an error, and the panel has to show which rows survived.
            if job.state in (JobState.DONE, JobState.CANCELLED) and job.result is not None:
                self.batch_panel.show_results(job.result)
                self._refresh_library()
            elif job.state == JobState.FAILED:
                self.batch_panel.run_failed(job.error)
            self._set_status(f"{job.title}: {job.message}")
            return
        if job.category == "repair_estimate":
            if job.state == JobState.DONE and job.result is not None:
                self.repair_panel.show_estimate(job.result)
            elif job.state == JobState.FAILED:
                self.repair_panel.estimate_failed(job.error)
            self._set_status(f"{job.title}: {job.message}")
            return
        if job.category == "repair":
            if job.state == JobState.DONE and job.result is not None:
                self.repair_panel.show_result(job.result)
                self._refresh_library()
            elif job.state == JobState.FAILED:
                # A skipped AI stage still leaves a usable cleaned file, and the message
                # says where it is -- showing it beats hiding it in the Jobs list.
                QMessageBox.warning(self, "Repair did not finish", job.error[:800])
            self._set_status(f"{job.title}: {job.message}")
            return
        if job.category == "generate" and self.generate_panel.on_job_update(job):
            if job.state == JobState.DONE:
                self._refresh_library()
            self._set_status(f"{job.title}: {job.message or job.state.value}")
            return
        if job.category == "edit":
            # The Edit flow reports in place on its Result step, failures included.
            self.edit_panel.on_job_update(job)
            if job.state == JobState.DONE:
                self._refresh_library()
            self._set_status(f"{job.title}: {job.message or job.state.value}")
            return
        if job.category in ("photofilm", "autopilot"):
            self.photo_panel.on_job_update(job)
            if job.state == JobState.DONE:
                self._refresh_library()
            self._set_status(f"{job.title}: {job.message}")
            return

        if job.state == JobState.DONE:
            self._set_status(f"Job {job.id} finished: {job.title}")
            self._route_result(job)
        elif job.state == JobState.FAILED:
            self._set_status(f"Job {job.id} FAILED: {job.error.splitlines()[0][:120]}")
            self.go_to("jobs")
        elif job.state == JobState.RUNNING:
            self._set_status(f"Job {job.id}: {job.message}")

    def _route_result(self, job: Job) -> None:
        result = job.result
        if isinstance(result, GenerationResult):
            self.generate_panel.show_result(result)
            self._refresh_library()
        elif isinstance(result, DirectorResult):
            self.director_panel.show_result(result)
            self._refresh_library()
            self.timeline_panel.refresh()
        elif job.category == "enhance":
            self.enhance_panel.show_result(result)
        elif job.category == "install":
            self.hub_panel.refresh()
            self.enhance_panel.refresh_status()
            self.repair_panel.refresh_status()
            self.batch_panel.refresh_status()
            self._set_status(f"Installed: {result}")
        elif job.category in ("import", "edit", "render"):
            self._refresh_library()
            self.timeline_panel.refresh()

    # -- misc --------------------------------------------------------------------------------
    def _on_panel_changed(self, index: int) -> None:
        widget = self.stack.widget(index)
        if widget is self.timeline_panel:
            self.timeline_panel.refresh()
        elif widget is self.library_panel:
            self.library_panel.refresh()
        elif widget is self.home_panel:
            self.home_panel.refresh()
        elif widget is self.hub_panel:
            self.hub_panel.refresh()
        elif widget is self.learning_panel:
            self.learning_panel.refresh()
        elif widget is self.agent_panel:
            self.agent_panel.refresh()

    def _on_providers_reloaded(self) -> None:
        self.generate_panel._reload_capabilities()
        self.director_panel.reload_providers()
        self.hub_panel.refresh()
        self.enhance_panel.refresh_status()
        self.repair_panel.refresh_status()

    def _send_to_edit(self, asset) -> None:
        self.edit_panel.set_asset(asset)
        self.go_to("edit")

    def send_selection_to_edit(self) -> None:
        asset = self.library_panel.current_asset()
        if asset:
            self._send_to_edit(asset)
        else:
            self._set_status("Select an asset in the Library first")

    def send_selection_to_enhance(self) -> None:
        asset = self.library_panel.current_asset()
        if asset:
            self.enhance_panel.set_asset(asset)
            self.go_to("enhance")
        else:
            self._set_status("Select an asset in the Library first")

    def send_selection_to_repair(self) -> None:
        asset = self.library_panel.current_asset()
        if asset is None:
            self._set_status("Select a video in the Library first")
        elif asset.kind != "video":
            self._set_status(f"{asset.label} is {asset.kind}; Repair works on video")
        else:
            self.repair_panel.set_asset(asset)
            self.go_to("repair")

    def _on_template_applied(self) -> None:
        self.timeline_panel.refresh()
        project = self.context.project
        if project:
            # A template carries its own canvas; push it into the render controls.
            try:
                self.timeline_panel.width_box.setValue(int(project.get_meta("canvas_width") or 1920))
                self.timeline_panel.height_box.setValue(int(project.get_meta("canvas_height") or 1080))
                self.timeline_panel.fps_box.setValue(int(project.get_meta("canvas_fps") or 30))
                self.timeline_panel.crossfade_box.setValue(float(project.get_meta("crossfade") or 0.5))
            except ValueError:
                pass
        self.go_to("timeline")

    def _apply_brief(self, brief: str, canvas: dict) -> None:
        self.director_panel.brief_box.setPlainText(brief)
        width, height = canvas.get("width", 1920), canvas.get("height", 1080)
        aspect = "9:16" if height > width else ("1:1" if width == height else "16:9")
        index = self.director_panel.aspect_box.findText(aspect)
        if index >= 0:
            self.director_panel.aspect_box.setCurrentIndex(index)
        self.go_to("director")

    def toggle_playback(self) -> None:
        preview = self._active_preview()
        if preview:
            preview.toggle_play()

    def stop_playback(self) -> None:
        preview = self._active_preview()
        if preview:
            preview.stop()

    def _active_preview(self):
        return getattr(self.stack.currentWidget(), "preview", None)

    def refresh_all(self) -> None:
        self.library_panel.refresh()
        self.timeline_panel.refresh()
        self.hub_panel.refresh()
        self.enhance_panel.refresh_status()
        self.repair_panel.refresh_status()
        self.home_panel.refresh()
        self._set_status("Refreshed")

    def show_command_palette(self) -> None:
        palette = shortcuts_module.CommandPalette(self.commands, self)
        palette.exec()

    def show_shortcuts(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Keyboard shortcuts")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(shortcuts_module.shortcuts_html(self.commands))
        box.exec()

    def _use_music(self, path) -> None:
        """A track downloaded in Find music becomes the photo film's soundtrack."""
        self.photo_panel._music = path
        self.photo_panel.music_label.setText(path.name)
        self.photo_panel._refresh()
        self.go_to("photos")
        self._set_status(f"{path.name} set as the music track")

    def toggle_sidebar(self) -> None:
        self.sidebar.set_collapsed(not self.sidebar.collapsed)

    def _on_sidebar_collapsed(self, collapsed: bool) -> None:
        self.context.settings.sidebar_collapsed = collapsed
        self.context.settings.save()
        self._set_status("Sidebar hidden — Ctrl+\\ brings it back"
                         if collapsed else "Sidebar shown")

    def toggle_advanced(self) -> None:
        shown = not self.sidebar.mode_button.isChecked()
        self.sidebar.set_advanced_visible(shown)
        self.context.settings.sidebar_advanced = shown
        self.context.settings.save()

    def _save_sidebar_order(self, order: list) -> None:
        self.context.settings.sidebar_order = list(order)
        self.context.settings.save()
        self._set_status("Sidebar order saved")

    def apply_theme(self, mode: str) -> None:
        """Switches palette live. Panels that set inline colours redraw in refresh()."""
        self.setStyleSheet(theme.set_mode(mode))
        # Keep the dropdown honest: it showed 'dark' while the window was light
        # whenever the theme changed from anywhere other than that dropdown.
        if self.sidebar.theme_box.currentText() != mode:
            self.sidebar.theme_box.blockSignals(True)
            self.sidebar.theme_box.setCurrentText(mode)
            self.sidebar.theme_box.blockSignals(False)
        self.context.settings.theme = mode
        self.context.settings.save()
        for panel in self.panels.values():
            for method in ("refresh", "refresh_status", "_refresh"):
                hook = getattr(panel, method, None)
                if callable(hook):
                    try:
                        hook()
                    except Exception:
                        pass
                    break
        # A new stylesheet does not reach a widget that set its own colours at
        # construction; those have to be rebuilt and the tree repolished, or half the
        # window keeps the old theme.
        theme.restyle_tree(self)
        # Icons are painted pixmaps, so a new palette does not reach them on its
        # own; each widget remembers which glyph it carries and repaints it.
        icons.repaint_tree(self)
        self._set_status(f"{mode.capitalize()} theme")

    def toggle_theme(self) -> None:
        self.sidebar.theme_box.setCurrentText(
            "light" if theme.MODE == "dark" else "dark"
        )

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _refresh_library(self) -> None:
        self.library_panel.refresh()

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _explain_layout(self) -> None:
        project = self.context.project
        root = str(project.root) if project else "(no project open)"
        QMessageBox.information(
            self, "Where are my files?",
            f"Project folder:\n{root}\n\n"
            "  media/    imported and generated files\n"
            "  renders/  exported cuts and edits\n"
            "  cache/    thumbnails and staged segments\n"
            "  project.mfproj  the SQLite catalogue\n\n"
            "The folder is self-contained -- copy it anywhere and it still opens.",
        )

    def _show_openness_help(self) -> None:
        provider = self.context.registry.get("catalog")
        open_models, closed_models = [], []
        if provider:
            for spec in provider.models:
                entry = provider.entry(spec.id) or {}
                label = entry.get("label", spec.id)
                if entry.get("openness") in ("open", "partial"):
                    open_models.append(f"{label} — {entry.get('repo', '')}")
                else:
                    closed_models.append(label)

        QMessageBox.information(
            self, "Which models are free?",
            "<b>Open weights — free, run locally, no API key:</b><br>"
            + "<br>".join(open_models[:20])
            + "<br><br><b>Closed — API only, billed per use:</b><br>"
            + ", ".join(closed_models[:30])
            + "<br><br>Open models run through ComfyUI (Model Hub -> Local engines). "
              "Closed models need a fal.ai or Replicate key.",
        )

    def _show_help(self) -> None:
        QMessageBox.information(
            self, "Getting started",
            "1. Settings -> paste at least one API key, then Save.\n"
            "2. Settings -> install ffmpeg if it is not found (local editing needs it).\n"
            "3. File -> New project.\n"
            "4. Generate: pick a task, provider and model, write a prompt, press Generate.\n"
            "5. Director: write a brief and let it plan, generate and assemble a whole cut.\n"
            "6. Library -> select clips -> Add to timeline -> Timeline -> Render.\n\n"
            "Everything long-running goes to the Jobs tab, where it can be cancelled and "
            "errors are shown in full.",
        )

    def showEvent(self, event) -> None:
        # Qt can un-hide children when the top-level window is first shown, so the
        # sidebar's collapsed state is re-applied once the window actually appears.
        super().showEvent(event)
        if not getattr(self, "_shown_once", False):
            self._shown_once = True
            self.sidebar.set_advanced_visible(self.context.settings.sidebar_advanced)
            if getattr(self.context.settings, "sidebar_collapsed", False):
                self.sidebar.set_collapsed(True)

    def closeEvent(self, event) -> None:
        active = self.context.jobs.active_count()
        if active and QMessageBox.question(
            self, "Jobs running", f"{active} job(s) are still running. Quit anyway?"
        ) != QMessageBox.StandardButton.Yes:
            event.ignore()
            return
        self.context.shutdown()
        event.accept()
