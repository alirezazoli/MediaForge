"""What the app has learned about how you work.

Records two things: the choices you *changed* (a strong signal) and the choices you
accepted unchanged (a weak one). Recommendations shift toward your taste only once
there is real evidence, and every applied preference is stated in plain language so it
never feels like the app quietly changed its mind.

Storage is SQLite in the config folder, so it survives projects, restarts and upgrades,
and grows without limit. Nothing leaves the machine.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from .config import app_data_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS choices (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL    NOT NULL,
    field      TEXT    NOT NULL,   -- move | atmosphere | transform | ai_model | aspect ...
    scene      TEXT    NOT NULL DEFAULT '',
    proposed   TEXT    NOT NULL DEFAULT '',
    chosen     TEXT    NOT NULL,
    changed    INTEGER NOT NULL DEFAULT 0,  -- 1 = user overrode a proposal
    project    TEXT    NOT NULL DEFAULT '',
    context    TEXT    NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS renders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL    NOT NULL,
    project    TEXT    NOT NULL DEFAULT '',
    shots      INTEGER NOT NULL DEFAULT 0,
    seconds    REAL    NOT NULL DEFAULT 0,
    aspect     TEXT    NOT NULL DEFAULT '',
    fps        INTEGER NOT NULL DEFAULT 0,
    crossfade  REAL    NOT NULL DEFAULT 0,
    used_ai    INTEGER NOT NULL DEFAULT 0,
    detail     TEXT    NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_choices_field ON choices(field, scene);
CREATE INDEX IF NOT EXISTS idx_choices_time  ON choices(created_at);
"""

# A change is worth far more than a silent acceptance: the user had to act.
WEIGHT_CHANGED = 3.0
WEIGHT_ACCEPTED = 1.0

# Half-life for how much an old choice still counts, in days. Taste drifts.
HALF_LIFE_DAYS = 60.0

# Guard rails, so one afternoon of experimenting does not become a rule.
MIN_OBSERVATIONS = 3
MIN_SHARE = 0.55


@dataclass
class Preference:
    field: str
    scene: str
    value: str
    share: float          # 0..1, how dominant this choice is
    observations: int
    weight: float

    @property
    def confident(self) -> bool:
        return self.observations >= MIN_OBSERVATIONS and self.share >= MIN_SHARE

    def sentence(self) -> str:
        where = f" for {self.scene.replace('_', ' ')} shots" if self.scene else ""
        return (f"you chose {self.field} “{self.value}”{where} in "
                f"{self.share:.0%} of {self.observations} decisions")


class LearningStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (app_data_dir() / "learning.db")
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self.connection.commit()
        self.enabled = True

    def close(self) -> None:
        try:
            self.connection.close()
        except sqlite3.Error:
            pass

    # -- recording ---------------------------------------------------------------------
    def record(
        self, field_name: str, chosen: str, *, proposed: str = "", scene: str = "",
        changed: bool | None = None, project: str = "", context: dict | None = None,
    ) -> None:
        if not self.enabled or not chosen:
            return
        if changed is None:
            changed = bool(proposed) and proposed != chosen
        self.connection.execute(
            "INSERT INTO choices(created_at, field, scene, proposed, chosen, changed, "
            "project, context) VALUES(?,?,?,?,?,?,?,?)",
            (time.time(), field_name, scene or "", proposed or "", chosen,
             1 if changed else 0, project, json.dumps(context or {}, default=str)),
        )
        self.connection.commit()

    def record_many(self, entries: Iterable[dict]) -> None:
        for entry in entries:
            self.record(**entry)

    def record_render(
        self, *, project: str, shots: int, seconds: float, aspect: str, fps: int,
        crossfade: float, used_ai: bool, detail: dict | None = None,
    ) -> None:
        if not self.enabled:
            return
        self.connection.execute(
            "INSERT INTO renders(created_at, project, shots, seconds, aspect, fps, "
            "crossfade, used_ai, detail) VALUES(?,?,?,?,?,?,?,?,?)",
            (time.time(), project, shots, seconds, aspect, fps, crossfade,
             1 if used_ai else 0, json.dumps(detail or {}, default=str)),
        )
        self.connection.commit()

    # -- reading -------------------------------------------------------------------------
    @staticmethod
    def _decay(created_at: float, now: float) -> float:
        age_days = max(0.0, (now - created_at) / 86400.0)
        return 0.5 ** (age_days / HALF_LIFE_DAYS)

    def preference(self, field_name: str, scene: str = "") -> Preference | None:
        """The user's habitual choice, or None when there is not enough evidence.

        Scene-specific evidence wins. The general fallback is only allowed once the user
        has expressed the same taste across at least two different kinds of shot --
        otherwise a rule learned purely about exteriors would silently govern interiors,
        which is over-generalising from one situation.
        """
        specific = self._weighted_best(field_name, scene) if scene else None
        if specific and specific.confident:
            return specific

        if self._distinct_scenes(field_name) < 2:
            return None
        general = self._weighted_best(field_name, "")
        return general if general and general.confident else None

    def _distinct_scenes(self, field_name: str) -> int:
        row = self.connection.execute(
            "SELECT COUNT(DISTINCT scene) AS n FROM choices WHERE field = ? AND scene <> ''",
            (field_name,),
        ).fetchone()
        return int(row["n"] or 0)

    def _weighted_best(self, field_name: str, scene: str) -> Preference | None:
        query = "SELECT chosen, changed, created_at FROM choices WHERE field = ?"
        parameters: list[Any] = [field_name]
        if scene:
            query += " AND scene = ?"
            parameters.append(scene)
        rows = self.connection.execute(query, parameters).fetchall()
        if not rows:
            return None

        now = time.time()
        weights: dict[str, float] = {}
        counts: dict[str, int] = {}
        for row in rows:
            weight = (WEIGHT_CHANGED if row["changed"] else WEIGHT_ACCEPTED)
            weight *= self._decay(row["created_at"], now)
            weights[row["chosen"]] = weights.get(row["chosen"], 0.0) + weight
            counts[row["chosen"]] = counts.get(row["chosen"], 0) + 1

        total = sum(weights.values())
        if total <= 0:
            return None
        value = max(weights, key=lambda key: weights[key])
        return Preference(
            field=field_name, scene=scene, value=value,
            share=weights[value] / total, observations=counts[value],
            weight=weights[value],
        )

    def all_preferences(self, fields: Sequence[str] | None = None) -> list[Preference]:
        fields = fields or ["move", "atmosphere", "transform", "aspect", "ai_model",
                            "seconds_each", "fit", "music_volume"]
        scenes = [row["scene"] for row in self.connection.execute(
            "SELECT DISTINCT scene FROM choices"
        )]
        found: list[Preference] = []
        for field_name in fields:
            for scene in scenes:
                entry = self._weighted_best(field_name, scene)
                if entry and entry.confident:
                    found.append(entry)
        return sorted(found, key=lambda item: -item.weight)

    def numeric_preference(self, field_name: str) -> float | None:
        """Median of numeric choices -- used for pace, volume and similar dials."""
        rows = self.connection.execute(
            "SELECT chosen, created_at FROM choices WHERE field = ? ORDER BY created_at DESC "
            "LIMIT 60", (field_name,)
        ).fetchall()
        values: list[float] = []
        for row in rows:
            try:
                values.append(float(row["chosen"]))
            except (TypeError, ValueError):
                continue
        if len(values) < MIN_OBSERVATIONS:
            return None
        values.sort()
        middle = len(values) // 2
        return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2

    # -- reporting -------------------------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        choices = self.connection.execute("SELECT COUNT(*) AS n FROM choices").fetchone()["n"]
        changes = self.connection.execute(
            "SELECT COUNT(*) AS n FROM choices WHERE changed = 1"
        ).fetchone()["n"]
        renders = self.connection.execute("SELECT COUNT(*) AS n FROM renders").fetchone()["n"]
        row = self.connection.execute(
            "SELECT SUM(seconds) AS s, SUM(shots) AS c FROM renders"
        ).fetchone()
        first = self.connection.execute(
            "SELECT MIN(created_at) AS t FROM choices"
        ).fetchone()["t"]
        return {
            "choices": choices,
            "overrides": changes,
            "renders": renders,
            "total_seconds": float(row["s"] or 0.0),
            "total_shots": int(row["c"] or 0),
            "since": first,
            "size_kb": self.path.stat().st_size // 1024 if self.path.exists() else 0,
        }

    def report(self) -> str:
        info = self.stats()
        lines = [
            f"Decisions recorded : {info['choices']}  ({info['overrides']} were your corrections)",
            f"Videos rendered    : {info['renders']}  "
            f"({info['total_shots']} shots, {info['total_seconds']:.0f}s total)",
            f"Database           : {info['size_kb']} KB at {self.path}",
            "",
        ]
        preferences = self.all_preferences()
        if not preferences:
            lines.append("Nothing learned yet. Make a few videos and correct what you "
                         "disagree with — corrections count for three times as much as "
                         "silent acceptance.")
            return "\n".join(lines)

        lines.append("What it has learned:")
        for preference in preferences:
            marker = "applied" if preference.confident else "watching"
            lines.append(f"  [{marker}] {preference.sentence()}")
        lines += [
            "",
            f"A preference is only applied after {MIN_OBSERVATIONS} observations and a "
            f"{MIN_SHARE:.0%} majority. Choices fade by half every {HALF_LIFE_DAYS:.0f} days.",
        ]
        return "\n".join(lines)

    def forget(self, field_name: str = "", scene: str = "") -> int:
        query, parameters = "DELETE FROM choices WHERE 1=1", []
        if field_name:
            query += " AND field = ?"
            parameters.append(field_name)
        if scene:
            query += " AND scene = ?"
            parameters.append(scene)
        cursor = self.connection.execute(query, parameters)
        self.connection.commit()
        return cursor.rowcount

    def forget_everything(self) -> None:
        self.connection.executescript(
            "DELETE FROM choices; DELETE FROM renders; VACUUM;"
        )
        self.connection.commit()

    def export(self, destination: Path) -> Path:
        payload = {
            "stats": self.stats(),
            "choices": [dict(row) for row in self.connection.execute(
                "SELECT * FROM choices ORDER BY created_at"
            )],
            "renders": [dict(row) for row in self.connection.execute(
                "SELECT * FROM renders ORDER BY created_at"
            )],
        }
        destination.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return destination


# ======================================================================================
# Applying what was learned
# ======================================================================================

@dataclass
class AppliedPreference:
    field: str
    scene: str
    was: str
    now: str
    sentence: str


def apply_to_analysis(store: LearningStore | None, analysis) -> list[AppliedPreference]:
    """Nudges one photo's recommendation toward the user's habits.

    Returns what it changed so the caller can explain itself. The analysis keeps its
    original reasoning text; the learned reason is appended rather than replacing it,
    because both are true.
    """
    if store is None or not store.enabled or analysis is None:
        return []

    applied: list[AppliedPreference] = []
    scene = getattr(analysis, "scene", "")

    move = store.preference("move", scene)
    if move and move.value != analysis.move:
        alternatives = list(getattr(analysis, "move_alternatives", []))
        # Keep the previous first choice available as a fallback for the variety pass.
        if analysis.move not in alternatives:
            alternatives.insert(0, analysis.move)
        applied.append(AppliedPreference("move", scene, analysis.move, move.value,
                                         move.sentence()))
        analysis.move_alternatives = [a for a in alternatives if a != move.value]
        analysis.move = move.value
        analysis.move_reason += f"  Adjusted because {move.sentence()}."

    atmosphere = store.preference("atmosphere", scene)
    if atmosphere and atmosphere.value != analysis.atmosphere:
        applied.append(AppliedPreference("atmosphere", scene, analysis.atmosphere,
                                         atmosphere.value, atmosphere.sentence()))
        analysis.atmosphere = atmosphere.value
        analysis.atmosphere_reason += f"  Adjusted because {atmosphere.sentence()}."

    return applied


def observe_plan(store: LearningStore | None, shots, project: str = "") -> None:
    """Records the plan as accepted-unless-changed at the moment of rendering."""
    if store is None or not store.enabled:
        return
    for shot in shots:
        analysis = getattr(shot, "analysis", None)
        scene = getattr(analysis, "scene", "") if analysis else ""
        proposed_move = getattr(analysis, "move", "") if analysis else ""
        proposed_atmosphere = getattr(analysis, "atmosphere", "") if analysis else ""
        store.record("move", shot.move, proposed=proposed_move, scene=scene, project=project)
        store.record("atmosphere", shot.atmosphere, proposed=proposed_atmosphere,
                     scene=scene, project=project)
        store.record("transform", shot.transform, scene=scene, project=project,
                     changed=False)
        if shot.ai_model:
            store.record("ai_model", shot.ai_model, scene=scene, project=project,
                         changed=False)
