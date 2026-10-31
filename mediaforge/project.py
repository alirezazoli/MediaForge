"""A project is a folder plus a SQLite catalogue.

Layout on disk:
    <project>/
        project.mfproj        -- sqlite catalogue (assets, generations, timeline)
        media/                -- imported and generated files
        renders/              -- exported results
        cache/thumbs/         -- thumbnails
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    path          TEXT NOT NULL UNIQUE,
    kind          TEXT NOT NULL,
    label         TEXT NOT NULL DEFAULT '',
    duration      REAL NOT NULL DEFAULT 0,
    width         INTEGER NOT NULL DEFAULT 0,
    height        INTEGER NOT NULL DEFAULT 0,
    fps           REAL NOT NULL DEFAULT 0,
    size_bytes    INTEGER NOT NULL DEFAULT 0,
    thumbnail     TEXT NOT NULL DEFAULT '',
    tags          TEXT NOT NULL DEFAULT '',
    favourite     INTEGER NOT NULL DEFAULT 0,
    source        TEXT NOT NULL DEFAULT 'import',
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS generations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    provider      TEXT NOT NULL,
    model_id      TEXT NOT NULL,
    capability    TEXT NOT NULL,
    prompt        TEXT NOT NULL DEFAULT '',
    options_json  TEXT NOT NULL DEFAULT '{}',
    output_paths  TEXT NOT NULL DEFAULT '[]',
    text_output   TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'done',
    error         TEXT NOT NULL DEFAULT '',
    elapsed       REAL NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS timeline (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    position      INTEGER NOT NULL,
    asset_id      INTEGER NOT NULL,
    in_point      REAL NOT NULL DEFAULT 0,
    out_point     REAL NOT NULL DEFAULT 0,
    transition    TEXT NOT NULL DEFAULT 'cut',
    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_assets_kind ON assets(kind);
CREATE INDEX IF NOT EXISTS idx_timeline_pos ON timeline(position);
"""


@dataclass
class Asset:
    id: int
    path: Path
    kind: str
    label: str
    duration: float
    width: int
    height: int
    fps: float
    size_bytes: int
    thumbnail: str
    tags: str
    favourite: bool
    source: str
    created_at: float

    @property
    def exists(self) -> bool:
        return self.path.exists()

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}" if self.width else "-"

    @property
    def pretty_size(self) -> str:
        size = float(self.size_bytes)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} GB"


@dataclass
class Clip:
    id: int
    position: int
    asset_id: int
    in_point: float
    out_point: float
    transition: str


class Project:
    EXTENSION = ".mfproj"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        for subdirectory in ("media", "renders", "cache/thumbs"):
            (self.root / subdirectory).mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / f"project{self.EXTENSION}"
        self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self.connection.commit()
        self.set_meta("name", self.get_meta("name") or self.root.name)

    # -- paths ---------------------------------------------------------------------
    @property
    def name(self) -> str:
        return self.get_meta("name") or self.root.name

    @property
    def media_dir(self) -> Path:
        return self.root / "media"

    @property
    def renders_dir(self) -> Path:
        return self.root / "renders"

    @property
    def thumbs_dir(self) -> Path:
        return self.root / "cache" / "thumbs"

    def close(self) -> None:
        self.connection.close()

    # -- meta ----------------------------------------------------------------------
    def get_meta(self, key: str) -> str:
        row = self.connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else ""

    def set_meta(self, key: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.connection.commit()

    # -- assets --------------------------------------------------------------------
    def add_asset(
        self, path: Path, kind: str, *, label: str = "", duration: float = 0.0, width: int = 0,
        height: int = 0, fps: float = 0.0, size_bytes: int = 0, thumbnail: str = "",
        source: str = "import", tags: str = "",
    ) -> int:
        cursor = self.connection.execute(
            """INSERT INTO assets(path, kind, label, duration, width, height, fps, size_bytes,
                                  thumbnail, tags, source, created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(path) DO UPDATE SET
                   duration=excluded.duration, width=excluded.width, height=excluded.height,
                   fps=excluded.fps, size_bytes=excluded.size_bytes,
                   thumbnail=excluded.thumbnail""",
            (str(path), kind, label or path.stem, duration, width, height, fps, size_bytes,
             thumbnail, tags, source, time.time()),
        )
        self.connection.commit()
        if cursor.lastrowid:
            return int(cursor.lastrowid)
        row = self.connection.execute("SELECT id FROM assets WHERE path = ?", (str(path),)).fetchone()
        return int(row["id"])

    def import_file(self, source_path: Path, copy: bool = True) -> Path:
        """Copies into media/ so the project folder stays self-contained."""
        if not copy:
            return source_path
        destination = self.media_dir / source_path.name
        counter = 1
        while destination.exists() and destination.stat().st_size != source_path.stat().st_size:
            destination = self.media_dir / f"{source_path.stem}-{counter}{source_path.suffix}"
            counter += 1
        if not destination.exists():
            shutil.copy2(source_path, destination)
        return destination

    def assets(self, kind: str = "", search: str = "", favourites_only: bool = False) -> list[Asset]:
        query = "SELECT * FROM assets WHERE 1=1"
        parameters: list[Any] = []
        if kind:
            query += " AND kind = ?"
            parameters.append(kind)
        if search:
            query += " AND (label LIKE ? OR path LIKE ? OR tags LIKE ?)"
            pattern = f"%{search}%"
            parameters += [pattern, pattern, pattern]
        if favourites_only:
            query += " AND favourite = 1"
        query += " ORDER BY created_at DESC"
        return [_row_to_asset(row) for row in self.connection.execute(query, parameters)]

    def asset(self, asset_id: int) -> Asset | None:
        row = self.connection.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        return _row_to_asset(row) if row else None

    def remove_asset(self, asset_id: int, delete_file: bool = False) -> None:
        asset = self.asset(asset_id)
        self.connection.execute("DELETE FROM timeline WHERE asset_id = ?", (asset_id,))
        self.connection.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
        self.connection.commit()
        if delete_file and asset and asset.path.exists():
            asset.path.unlink(missing_ok=True)

    def toggle_favourite(self, asset_id: int) -> None:
        self.connection.execute(
            "UPDATE assets SET favourite = 1 - favourite WHERE id = ?", (asset_id,)
        )
        self.connection.commit()

    def set_tags(self, asset_id: int, tags: str) -> None:
        self.connection.execute("UPDATE assets SET tags = ? WHERE id = ?", (tags, asset_id))
        self.connection.commit()

    # -- generations ---------------------------------------------------------------
    def log_generation(
        self, provider: str, model_id: str, capability: str, prompt: str, options: dict,
        outputs: Iterable[Path], text: str = "", status: str = "done", error: str = "",
        elapsed: float = 0.0,
    ) -> int:
        cursor = self.connection.execute(
            """INSERT INTO generations(provider, model_id, capability, prompt, options_json,
                                       output_paths, text_output, status, error, elapsed, created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (provider, model_id, capability, prompt, json.dumps(options, default=str),
             json.dumps([str(path) for path in outputs]), text, status, error, elapsed, time.time()),
        )
        self.connection.commit()
        return int(cursor.lastrowid or 0)

    def generations(self, limit: int = 300) -> list[dict]:
        rows = self.connection.execute(
            "SELECT * FROM generations ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        history = []
        for row in rows:
            entry = dict(row)
            entry["output_paths"] = json.loads(entry["output_paths"] or "[]")
            entry["options"] = json.loads(entry["options_json"] or "{}")
            history.append(entry)
        return history

    # -- timeline ------------------------------------------------------------------
    def timeline(self) -> list[Clip]:
        return [
            Clip(row["id"], row["position"], row["asset_id"], row["in_point"],
                 row["out_point"], row["transition"])
            for row in self.connection.execute("SELECT * FROM timeline ORDER BY position")
        ]

    def append_clip(self, asset_id: int, in_point: float = 0.0, out_point: float = 0.0,
                    transition: str = "cut") -> int:
        row = self.connection.execute("SELECT COALESCE(MAX(position), -1) AS m FROM timeline").fetchone()
        cursor = self.connection.execute(
            "INSERT INTO timeline(position, asset_id, in_point, out_point, transition) "
            "VALUES(?,?,?,?,?)",
            (int(row["m"]) + 1, asset_id, in_point, out_point, transition),
        )
        self.connection.commit()
        return int(cursor.lastrowid or 0)

    def update_clip(self, clip_id: int, **fields: Any) -> None:
        allowed = {"in_point", "out_point", "transition", "position"}
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{key} = ?" for key in updates)
        self.connection.execute(
            f"UPDATE timeline SET {assignments} WHERE id = ?", (*updates.values(), clip_id)
        )
        self.connection.commit()

    def remove_clip(self, clip_id: int) -> None:
        self.connection.execute("DELETE FROM timeline WHERE id = ?", (clip_id,))
        self.connection.commit()
        self._renumber()

    def move_clip(self, clip_id: int, delta: int) -> None:
        clips = self.timeline()
        index = next((i for i, clip in enumerate(clips) if clip.id == clip_id), -1)
        target = index + delta
        if index < 0 or not 0 <= target < len(clips):
            return
        clips[index], clips[target] = clips[target], clips[index]
        for position, clip in enumerate(clips):
            self.connection.execute(
                "UPDATE timeline SET position = ? WHERE id = ?", (position, clip.id)
            )
        self.connection.commit()

    def clear_timeline(self) -> None:
        self.connection.execute("DELETE FROM timeline")
        self.connection.commit()

    def _renumber(self) -> None:
        for position, clip in enumerate(self.timeline()):
            self.connection.execute(
                "UPDATE timeline SET position = ? WHERE id = ?", (position, clip.id)
            )
        self.connection.commit()


def _row_to_asset(row: sqlite3.Row) -> Asset:
    return Asset(
        id=row["id"], path=Path(row["path"]), kind=row["kind"], label=row["label"],
        duration=row["duration"], width=row["width"], height=row["height"], fps=row["fps"],
        size_bytes=row["size_bytes"], thumbnail=row["thumbnail"], tags=row["tags"],
        favourite=bool(row["favourite"]), source=row["source"], created_at=row["created_at"],
    )


def open_or_create(root: Path) -> Project:
    return Project(root)
