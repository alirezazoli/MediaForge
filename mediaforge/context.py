"""Shared application state passed to every panel."""

from __future__ import annotations

from pathlib import Path

from . import ffmpeg_extra, ffmpeg_tools
from .config import Credentials, Settings
from .jobs import JobQueue
from .learning import LearningStore
from .localai import LocalToolRegistry
from .project import Project
from .providers import Registry, build_registry


class AppContext:
    def __init__(self) -> None:
        self.settings = Settings.load()
        self.credentials = Credentials()
        self.registry: Registry = build_registry(self.credentials, self.settings)
        self.locator = ffmpeg_tools.FfmpegLocator(
            self.settings.ffmpeg_path, self.settings.ffprobe_path
        )
        self.local_tools = LocalToolRegistry()
        self.learning = LearningStore()
        self.learning.enabled = self.settings.learning_enabled
        self.jobs = JobQueue(max_workers=max(1, self.settings.max_parallel_jobs))
        self.project: Project | None = None
        self._encoders: dict[str, bool] | None = None

    @property
    def encoders(self) -> dict[str, bool]:
        """Probed once -- which hardware encoders this ffmpeg build actually has."""
        if self._encoders is None:
            self._encoders = ffmpeg_extra.available_encoders(self.locator)
        return self._encoders

    # -- lifecycle -------------------------------------------------------------------
    def open_project(self, root: Path) -> Project:
        if self.project:
            self.project.close()
        self.project = Project(root)
        self.settings.remember_project(str(root))
        return self.project

    def require_project(self) -> Project:
        if self.project is None:
            raise RuntimeError("Open or create a project first (File -> New Project).")
        return self.project

    def reload_providers(self) -> None:
        """Called after credentials, catalogue or provider settings change."""
        self.credentials.reload()
        self.registry = build_registry(self.credentials, self.settings)
        self.locator = ffmpeg_tools.FfmpegLocator(
            self.settings.ffmpeg_path, self.settings.ffprobe_path
        )
        self.local_tools.reload()
        self._encoders = None

    def shutdown(self) -> None:
        self.jobs.shutdown()
        if self.project:
            self.project.close()
        self.learning.close()
        self.settings.save()

    @property
    def project_key(self) -> str:
        """Stable identifier used to attribute learned choices to a project."""
        return self.project.name if self.project else ""

    # -- convenience -----------------------------------------------------------------
    def catalogue(self, path: Path, source: str = "generated", label: str = "") -> int:
        """Probes a file, makes a thumbnail, and records it in the project library."""
        project = self.require_project()
        kind = ffmpeg_tools.classify(path)
        duration = width = height = 0
        fps = 0.0
        if self.locator.available:
            try:
                info = ffmpeg_tools.probe(self.locator, path)
                duration, width, height, fps = info.duration, info.width, info.height, info.fps
                kind = info.kind
            except Exception:
                pass
        thumbnail = ""
        if kind in ("video", "image") and self.locator.available:
            candidate = project.thumbs_dir / f"{path.stem}.jpg"
            if ffmpeg_tools.make_thumbnail(self.locator, path, candidate):
                thumbnail = str(candidate)
        return project.add_asset(
            path, kind, label=label or path.stem, duration=duration, width=width, height=height,
            fps=fps, size_bytes=path.stat().st_size if path.exists() else 0,
            thumbnail=thumbnail, source=source,
        )
