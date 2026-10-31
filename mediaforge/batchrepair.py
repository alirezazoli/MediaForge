"""Repairing a whole folder.

A batch is not a loop around the single-file path with a progress bar bolted on. Three
things have to behave differently once there is more than one file:

  1. **One bad file must not abandon the other forty-nine.** Every failure is recorded
     against its own row and the run carries on. A batch that stops dead on file three
     and throws away the work already done is worse than no batch at all.

  2. **Each file gets its own measurement.** Noise, softness and dead air differ per
     clip, so the settings are derived per file. Forcing one set of settings across a
     folder is available, but it is a deliberate choice rather than the default.

  3. **Files with nothing wrong are skipped, not re-encoded.** Running everything through
     an encoder "for consistency" costs quality on exactly the files that had none to
     spare.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from . import repair
from .ffmpeg_tools import VIDEO_SUFFIXES, FfmpegLocator, format_timecode

Progress = Callable[[float, str], None]
Cancel = Callable[[], bool]


def _noop(fraction: float, message: str) -> None:
    return None


class ItemState(str, Enum):
    PENDING = "pending"
    SCANNED = "scanned"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class BatchItem:
    path: Path
    state: ItemState = ItemState.PENDING
    report: repair.Report | None = None
    plan: repair.Plan | None = None
    output: Path | None = None
    error: str = ""
    note: str = ""

    @property
    def name(self) -> str:
        return self.path.name

    def summary(self) -> str:
        """One line for the table, whatever stage this row is at."""
        if self.state is ItemState.FAILED:
            return f"Failed — {self.error.splitlines()[0][:90]}"
        if self.state is ItemState.SKIPPED:
            return self.note or "Nothing to fix"
        if self.state is ItemState.DONE:
            return f"Repaired → {self.output.name}" if self.output else "Repaired"
        if self.state is ItemState.CANCELLED:
            return "Cancelled"
        if self.report is None:
            return "Not analysed"
        return "; ".join(self.plan.describe()) if self.plan else "Analysed"

    def findings(self) -> str:
        if self.report is None:
            return ""
        report = self.report
        parts = [f"{format_timecode(report.info.duration)}", report.info.resolution,
                 report.measurement.noise_word, report.measurement.detail_word]
        if report.dead:
            parts.append(f"{report.dead_seconds:.1f}s dead")
        return " · ".join(parts)


def find_videos(folder: Path, recursive: bool = True) -> list[Path]:
    """Video files in a folder, in a stable order.

    Sorted by name rather than by full path, so a batch reads in the order the folder
    looks like it is in -- `sorted(glob("**/*"))` puts every sub-folder's contents after
    everything at the top level, which is stable but not what anyone expects to see.

    Anything already written by a previous repair is excluded, or running the batch twice
    quietly produces `clip_repaired_repaired.mp4`. The match is deliberately exact: only
    the suffix this tool itself writes, so a user's own `wedding_repaired_final.mp4` is
    still picked up.
    """
    pattern = "**/*" if recursive else "*"
    found = [
        path for path in folder.glob(pattern)
        if path.is_file()
        and path.suffix.lower() in VIDEO_SUFFIXES
        and not _is_our_output(path)
    ]
    return sorted(found, key=lambda path: (path.name.lower(), str(path)))


_OUR_OUTPUT = re.compile(r"_repaired(_\d+)?$")


def _is_our_output(path: Path) -> bool:
    """True only for names `output_path` could have produced."""
    return bool(_OUR_OUTPUT.search(path.stem))


@dataclass
class BatchOutcome:
    items: list[BatchItem] = field(default_factory=list)
    cancelled: bool = False

    def counts(self) -> dict[str, int]:
        tally: dict[str, int] = {}
        for item in self.items:
            tally[item.state.value] = tally.get(item.state.value, 0) + 1
        return tally

    def sentence(self) -> str:
        tally = self.counts()
        parts = []
        for state, label in (("done", "repaired"), ("skipped", "skipped"),
                             ("failed", "failed"), ("cancelled", "cancelled")):
            if tally.get(state):
                parts.append(f"{tally[state]} {label}")
        head = ", ".join(parts) or "nothing to do"
        if tally.get("failed"):
            head += ". The failures are listed against their own rows; everything else "\
                    "still ran."
        return head

    @property
    def outputs(self) -> list[Path]:
        return [item.output for item in self.items if item.output]


def scan_all(locator: FfmpegLocator, paths: list[Path], silence_db: int = repair.SILENCE_DB,
             min_dead: float = repair.MIN_DEAD_SECONDS, upscaler_installed: bool = False,
             tool_id: str = "realesrgan", progress: Progress = _noop,
             cancel: Cancel | None = None,
             on_item: Callable[[BatchItem], None] | None = None) -> BatchOutcome:
    """Measures every file. A file that cannot be read is marked and the rest continue."""
    outcome = BatchOutcome(items=[BatchItem(path) for path in paths])
    total = max(1, len(outcome.items))

    for index, item in enumerate(outcome.items):
        if cancel and cancel():
            outcome.cancelled = True
            for remaining in outcome.items[index:]:
                if remaining.state is ItemState.PENDING:
                    remaining.state = ItemState.CANCELLED
            break
        progress(index / total, f"Analysing {item.name} ({index + 1} of {total})")
        try:
            item.report = repair.scan(locator, item.path, silence_db=silence_db,
                                      min_dead=min_dead)
            item.plan = repair.recommend(item.report, upscaler_installed=upscaler_installed,
                                         tool_id=tool_id)
            item.state = ItemState.SCANNED
        except Exception as error:
            item.state = ItemState.FAILED
            item.error = str(error) or error.__class__.__name__
        if on_item:
            on_item(item)

    progress(1.0, f"Analysed {total} file(s)")
    return outcome


def apply_shared_plan(items: list[BatchItem], template: repair.Plan) -> None:
    """Forces one set of settings onto every file, keeping each file's own cut list.

    The cuts are never shared. They are timestamps into a particular video, so copying
    them between files would delete arbitrary, unrelated seconds -- the one mistake in a
    batch tool that cannot be undone by re-running it.
    """
    for item in items:
        if item.report is None:
            continue
        item.plan = repair.Plan(
            cut_dead=template.cut_dead,
            cuts=list(item.report.dead),
            denoise=template.denoise,
            denoise_strength=template.denoise_strength,
            deband=template.deband,
            sharpen=template.sharpen,
            ai_upscale=template.ai_upscale,
            ai_suggested=template.ai_suggested,
            ai_tool=template.ai_tool,
            ai_model=template.ai_model,
            ai_gpu=template.ai_gpu,
            ai_scale=template.ai_scale,
            crf=template.crf,
            reasons=["Settings applied to every file in the batch."],
        )


def output_path(source: Path, destination_dir: Path) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    candidate = destination_dir / f"{source.stem}_repaired.mp4"
    counter = 1
    while candidate.exists():
        candidate = destination_dir / f"{source.stem}_repaired_{counter}.mp4"
        counter += 1
    return candidate


def repair_all(locator: FfmpegLocator, items: list[BatchItem], destination_dir: Path,
               local_tools=None, progress: Progress = _noop, cancel: Cancel | None = None,
               on_item: Callable[[BatchItem], None] | None = None,
               on_output: Callable[[Path, BatchItem], None] | None = None) -> BatchOutcome:
    """Runs each file's plan. Failures are recorded, never raised out of the loop."""
    outcome = BatchOutcome(items=items)
    workable = [item for item in items if item.report is not None and item.plan is not None]
    total = max(1, len(workable))

    for index, item in enumerate(workable):
        if cancel and cancel():
            outcome.cancelled = True
            for remaining in workable[index:]:
                if remaining.state in (ItemState.PENDING, ItemState.SCANNED):
                    remaining.state = ItemState.CANCELLED
            break

        if not item.plan.does_anything:
            item.state = ItemState.SKIPPED
            item.note = "Already clean — left alone rather than re-encoded"
            if on_item:
                on_item(item)
            continue

        item.state = ItemState.RUNNING
        if on_item:
            on_item(item)

        base = index / total
        span = 1.0 / total
        try:
            destination = output_path(item.path, destination_dir)
            result = repair.repair(
                locator, item.path, destination, item.plan, info=item.report.info,
                local_tools=local_tools,
                progress=lambda fraction, message, b=base, s=span, n=item.name:
                    progress(b + s * fraction, f"{n}: {message}"),
                cancel=cancel,
            )
            item.output = result
            item.state = ItemState.DONE
            if on_output:
                on_output(result, item)
        except Exception as error:
            message = str(error) or error.__class__.__name__
            if "Cancelled" in message:
                item.state = ItemState.CANCELLED
                outcome.cancelled = True
            else:
                # One unreadable or half-written file is not a reason to throw away the
                # forty already finished.
                item.state = ItemState.FAILED
                item.error = message
        if on_item:
            on_item(item)

    progress(1.0, outcome.sentence())
    return outcome


def estimate_batch(items: list[BatchItem], per_frame: float) -> tuple[int, float]:
    """Total frames the AI stage would process across the batch, and the seconds for it."""
    frames = 0
    for item in items:
        if item.report is None or item.plan is None or not item.plan.ai_upscale:
            continue
        frames += item.report.frames_after_cuts(
            item.plan.cuts if item.plan.cut_dead else []
        )
    return frames, frames * per_frame
