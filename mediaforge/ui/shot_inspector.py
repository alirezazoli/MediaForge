"""Per-photo inspector: change one shot without touching the rest.

Every control offers Auto. Auto is not blank — it shows what the analysis decided and
why, so choosing to override is an informed decision rather than a guess.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QTextEdit, QVBoxLayout, QWidget,
)

from .. import atmosphere as atmo
from .. import availability, sceneanalysis
from ..photofilm import ALL_MOVES, STYLE_HELP, Shot, TRANSFORM_HELP, TRANSFORMS
from ..providers import Capability
from . import theme
from .widgets import Card


class ShotInspector(QWidget):
    """Edits one Shot in place. Emits `changed` after any edit."""

    changed = Signal()
    analyse_requested = Signal()

    def __init__(self, context) -> None:
        super().__init__()
        self.context = context
        self._shot: Shot | None = None
        self._loading = False
        self._build()
        self.set_shot(None)

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setSpacing(10)

        self.title = QLabel("No photo selected")
        self.title.setObjectName("h2")
        self.title.setWordWrap(True)
        layout.addWidget(self.title)

        self.detected = QLabel("")
        self.detected.setObjectName("subtle")
        self.detected.setWordWrap(True)
        layout.addWidget(self.detected)

        # -- how to transform ----------------------------------------------------------
        transform_card = Card("How to animate this photo")
        self.transform_box = QComboBox()
        self.transform_box.addItems(TRANSFORMS)
        self.transform_box.currentTextChanged.connect(self._transform_changed)
        transform_card.body.addWidget(self.transform_box)
        self.transform_help = QLabel("")
        self.transform_help.setObjectName("faint")
        self.transform_help.setWordWrap(True)
        transform_card.body.addWidget(self.transform_help)
        layout.addWidget(transform_card)

        # -- AI model ------------------------------------------------------------------
        self.ai_card = Card("AI model")
        self.model_box = QComboBox()
        self.model_box.currentIndexChanged.connect(self._commit)
        self.ai_card.body.addWidget(self.model_box)
        self.model_state = QLabel("")
        self.model_state.setObjectName("faint")
        self.model_state.setWordWrap(True)
        self.ai_card.body.addWidget(self.model_state)
        self.ai_card.body.addWidget(QLabel("Prompt sent with the photo"))
        self.prompt_box = QTextEdit()
        self.prompt_box.setMaximumHeight(110)
        self.prompt_box.textChanged.connect(self._commit)
        self.ai_card.body.addWidget(self.prompt_box)
        regenerate = QPushButton("Rewrite prompt from the photo")
        regenerate.setObjectName("ghost")
        regenerate.clicked.connect(self._rewrite_prompt)
        self.ai_card.body.addWidget(regenerate)
        layout.addWidget(self.ai_card)

        # -- movement ------------------------------------------------------------------
        move_card = Card("Movement")
        self.move_box = QComboBox()
        self.move_box.addItems(ALL_MOVES)
        self.move_box.currentTextChanged.connect(self._move_changed)
        move_card.body.addWidget(self.move_box)
        self.move_help = QLabel("")
        self.move_help.setObjectName("faint")
        self.move_help.setWordWrap(True)
        move_card.body.addWidget(self.move_help)

        strength_row = QHBoxLayout()
        strength_row.addWidget(QLabel("Amount"))
        self.strength_box = QDoubleSpinBox()
        self.strength_box.setRange(0.02, 0.60)
        self.strength_box.setSingleStep(0.02)
        self.strength_box.setDecimals(2)
        self.strength_box.valueChanged.connect(self._commit)
        strength_row.addWidget(self.strength_box, 1)
        strength_row.addWidget(QLabel("Seconds"))
        self.seconds_box = QDoubleSpinBox()
        self.seconds_box.setRange(0.5, 30.0)
        self.seconds_box.setSingleStep(0.5)
        self.seconds_box.setDecimals(1)
        self.seconds_box.valueChanged.connect(self._commit)
        strength_row.addWidget(self.seconds_box, 1)
        move_card.body.addLayout(strength_row)
        layout.addWidget(move_card)

        # -- atmosphere ----------------------------------------------------------------
        atmosphere_card = Card("Atmosphere")
        self.atmosphere_box = QComboBox()
        self.atmosphere_box.addItem("Auto")
        self.atmosphere_box.addItems(atmo.ATMOSPHERE_NAMES)
        self.atmosphere_box.currentTextChanged.connect(self._atmosphere_changed)
        atmosphere_card.body.addWidget(self.atmosphere_box)
        self.atmosphere_help = QLabel("")
        self.atmosphere_help.setObjectName("faint")
        self.atmosphere_help.setWordWrap(True)
        atmosphere_card.body.addWidget(self.atmosphere_help)

        intensity_row = QHBoxLayout()
        intensity_row.addWidget(QLabel("Strength"))
        self.intensity_box = QDoubleSpinBox()
        self.intensity_box.setRange(0.0, 1.0)
        self.intensity_box.setSingleStep(0.05)
        self.intensity_box.setDecimals(2)
        self.intensity_box.valueChanged.connect(self._commit)
        intensity_row.addWidget(self.intensity_box, 1)
        self.vignette_check = QCheckBox("Vignette")
        self.vignette_check.toggled.connect(self._commit)
        intensity_row.addWidget(self.vignette_check)
        atmosphere_card.body.addLayout(intensity_row)

        grain_row = QHBoxLayout()
        grain_row.addWidget(QLabel("Film grain"))
        self.grain_box = QDoubleSpinBox()
        self.grain_box.setRange(0.0, 1.0)
        self.grain_box.setSingleStep(0.05)
        self.grain_box.setDecimals(2)
        self.grain_box.valueChanged.connect(self._commit)
        grain_row.addWidget(self.grain_box, 1)
        atmosphere_card.body.addLayout(grain_row)
        layout.addWidget(atmosphere_card)

        # -- why -----------------------------------------------------------------------
        why_card = Card("Why these settings")
        self.why_box = QTextEdit()
        self.why_box.setReadOnly(True)
        self.why_box.setMaximumHeight(190)
        why_card.body.addWidget(self.why_box)
        reanalyse = QPushButton("Re-analyse this photo")
        reanalyse.setObjectName("ghost")
        reanalyse.clicked.connect(self.analyse_requested.emit)
        why_card.body.addWidget(reanalyse)
        layout.addWidget(why_card)

        layout.addStretch(1)
        scroll.setWidget(host)
        outer.addWidget(scroll)

    # -- population ---------------------------------------------------------------------
    def set_shot(self, shot: Shot | None) -> None:
        self._shot = shot
        enabled = shot is not None
        for widget in (self.transform_box, self.model_box, self.prompt_box, self.move_box,
                       self.strength_box, self.seconds_box, self.atmosphere_box,
                       self.intensity_box, self.vignette_check, self.grain_box):
            widget.setEnabled(enabled)
        if shot is None:
            self.title.setText("No photo selected")
            self.detected.setText("Select a thumbnail to change just that shot.")
            self.why_box.clear()
            self.ai_card.setVisible(False)
            return

        self._loading = True
        self.title.setText(shot.path.name)

        analysis = shot.analysis
        if analysis is not None:
            self.detected.setText(analysis.summary())
            self.why_box.setPlainText(analysis.explanation())
        else:
            self.detected.setText("Not analysed yet.")
            self.why_box.setPlainText("Press 'Re-analyse this photo' to inspect it.")

        self._select(self.transform_box, shot.transform or "Auto")
        self._select(self.move_box, shot.move or "Auto")
        self._select(self.atmosphere_box, shot.atmosphere or "Auto")
        self.strength_box.setValue(shot.motion_strength)
        self.seconds_box.setValue(shot.seconds)
        self.intensity_box.setValue(shot.atmosphere_intensity)
        self.vignette_check.setChecked(shot.vignette)
        self.grain_box.setValue(shot.grain)
        self.prompt_box.setPlainText(shot.ai_prompt)

        self._refresh_models(shot)
        self._loading = False

        self._transform_changed(self.transform_box.currentText())
        self._move_changed(self.move_box.currentText())
        self._atmosphere_changed(self.atmosphere_box.currentText())

    @staticmethod
    def _select(box: QComboBox, text: str) -> None:
        index = box.findText(text)
        box.setCurrentIndex(index if index >= 0 else 0)

    def _refresh_models(self, shot: Shot) -> None:
        """Every image-to-video model, gated by whether it can actually run."""
        registry = self.context.registry
        catalog = registry.get("catalog")
        entries: list[tuple[str, object, availability.Availability]] = [
            ("Auto — best available", ("", ""),
             availability.Availability(availability.State.FREE,
                                       "Picks the best model you can actually run."))
        ]
        if catalog is not None:
            for spec in catalog.models_for(Capability.IMAGE_TO_VIDEO):
                state = availability.model_availability(
                    registry, "catalog", spec.id, Capability.IMAGE_TO_VIDEO
                )
                entries.append((spec.label, ("catalog", spec.id), state))

        current = (shot.ai_provider, shot.ai_model)
        availability.decorate_combo(self.model_box, entries)
        for index in range(self.model_box.count()):
            if self.model_box.itemData(index) == current:
                self.model_box.setCurrentIndex(index)
                break

        usable = sum(
            1 for _, _, state in entries[1:] if state.usable
        )
        if usable:
            self.model_state.setText(f"{usable} image-to-video models ready to use.")
            self.model_state.setObjectName("subtle")
        else:
            self.model_state.setText(
                "No image-to-video model is available. Add a fal.ai or Replicate key in "
                "Settings, or start ComfyUI. Until then this photo uses a camera move."
            )
            self.model_state.setObjectName("warnText")

    # -- reactions ------------------------------------------------------------------------
    def _transform_changed(self, text: str) -> None:
        self.transform_help.setText(TRANSFORM_HELP.get(text, ""))
        wants_ai = text in ("AI video", "Auto")
        self.ai_card.setVisible(wants_ai)
        self._commit()

    def _move_changed(self, text: str) -> None:
        if text == "Auto" and self._shot is not None and self._shot.analysis is not None:
            analysis = self._shot.analysis
            self.move_help.setText(f"Auto → {analysis.move}. {analysis.move_reason}")
        else:
            self.move_help.setText(STYLE_HELP.get(text, ""))
        self._commit()

    def _atmosphere_changed(self, text: str) -> None:
        if text == "Auto" and self._shot is not None and self._shot.analysis is not None:
            analysis = self._shot.analysis
            self.atmosphere_help.setText(
                f"Auto → {analysis.atmosphere}. {analysis.atmosphere_reason}"
            )
        else:
            self.atmosphere_help.setText(atmo.describe(text))
        self._commit()

    def _rewrite_prompt(self) -> None:
        if self._shot is None:
            return
        analysis = self._shot.analysis
        if analysis is None:
            analysis = sceneanalysis.analyse(self._shot.path)
            self._shot.analysis = analysis
        # Honour the move actually chosen, not just the one that was suggested.
        chosen = self.move_box.currentText()
        if chosen != "Auto":
            analysis.move = chosen
        chosen_atmosphere = self.atmosphere_box.currentText()
        if chosen_atmosphere != "Auto":
            analysis.atmosphere = chosen_atmosphere
        self.prompt_box.setPlainText(sceneanalysis.build_ai_prompt(analysis))

    def _commit(self) -> None:
        if self._loading or self._shot is None:
            return
        shot = self._shot
        before = (shot.transform, shot.move, shot.atmosphere)

        shot.transform = self.transform_box.currentText()
        shot.move = self.move_box.currentText()
        shot.atmosphere = self.atmosphere_box.currentText()
        shot.motion_strength = self.strength_box.value()
        shot.seconds = self.seconds_box.value()
        shot.atmosphere_intensity = self.intensity_box.value()
        shot.vignette = self.vignette_check.isChecked()
        shot.grain = self.grain_box.value()
        shot.ai_prompt = self.prompt_box.toPlainText()

        data = self.model_box.currentData()
        if isinstance(data, tuple):
            shot.ai_provider, shot.ai_model = data

        # "Auto" fields resolve at render time; store the analysis answer so the strip and
        # the estimate can show something concrete now.
        if shot.atmosphere == "Auto" and shot.analysis is not None:
            shot.atmosphere = shot.analysis.atmosphere
            self._loading = True
            self._select(self.atmosphere_box, shot.atmosphere)
            self._loading = False

        self._learn_from(before, shot)
        self.changed.emit()

    def _learn_from(self, before: tuple[str, str, str], shot: Shot) -> None:
        """Records only genuine overrides -- an edit the user actively made.

        The proposal is the analysis's own recommendation, so the store sees "we said X,
        they chose Y", which is what makes the signal worth three times an acceptance.
        """
        store = getattr(self.context, "learning", None)
        if store is None or not store.enabled:
            return

        analysis = shot.analysis
        scene = getattr(analysis, "scene", "") if analysis else ""
        project = getattr(self.context, "project_key", "")

        for index, (field_name, proposed) in enumerate((
            ("transform", ""),
            ("move", getattr(analysis, "move", "") if analysis else ""),
            ("atmosphere", getattr(analysis, "atmosphere", "") if analysis else ""),
        )):
            new_value = (shot.transform, shot.move, shot.atmosphere)[index]
            if new_value == before[index] or not new_value or new_value == "Auto":
                continue
            store.record(field_name, new_value, proposed=proposed, scene=scene,
                         changed=True, project=project)
