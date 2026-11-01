"""The functions the assistant can call.

Every one of these drives the real application state that the buttons drive. They return
plain dictionaries, because a small local model reads compact JSON far more reliably than
prose, and because the UI shows the same output to the user.
"""

from __future__ import annotations

from pathlib import Path

from . import atmosphere as atmo
from . import autopilot, availability, musicsearch, repair, sceneanalysis
from .agent import Tool
from .ffmpeg_tools import IMAGE_SUFFIXES
from .photofilm import ALL_MOVES, ASPECTS, TRANSFORMS
from .providers import Capability

KIND_WORDS = {"image": "photo", "video": "video", "audio": "audio file"}


def build_tools(context, photo_panel) -> list[Tool]:
    """Binds tools to the live AppContext and the Photos-to-video panel."""

    # -- reading the world ---------------------------------------------------------------
    def project_info() -> dict:
        project = context.project
        return {
            "project": project.name if project else None,
            "folder": str(project.root) if project else None,
            "assets": len(project.assets()) if project else 0,
            "ffmpeg_ready": context.locator.available,
            "photos_loaded": photo_panel.strip.count(),
            "theme": context.settings.theme,
        }

    def list_photos() -> dict:
        shots = photo_panel.shots()
        return {
            "count": len(shots),
            "photos": [
                {
                    "index": index,
                    "name": shot.path.name,
                    "scene": getattr(shot.analysis, "scene", None) if shot.analysis else None,
                    "move": shot.move,
                    "atmosphere": shot.atmosphere,
                    "transform": shot.transform,
                    "seconds": round(shot.seconds, 2),
                }
                for index, shot in enumerate(shots)
            ],
        }

    def add_photos_from_folder(folder: str, limit: int = 60) -> dict:
        directory = Path(folder).expanduser()
        if not directory.is_dir():
            return {"error": f"'{folder}' is not a folder."}
        found = [path for path in sorted(directory.rglob("*"))
                 if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES][:int(limit)]
        if not found:
            return {"error": f"No readable photos in '{folder}'."}
        added = photo_panel.strip.add_paths(found)
        return {"added": added, "total_now": photo_panel.strip.count(),
                "names": [path.name for path in found[:10]]}

    def clear_photos() -> dict:
        photo_panel.strip.clear_all()
        return {"cleared": True, "total_now": 0}

    def analyse_photo(index: int) -> dict:
        shots = photo_panel.shots()
        if not 0 <= int(index) < len(shots):
            return {"error": f"No photo at index {index}. There are {len(shots)}."}
        shot = shots[int(index)]
        analysis = sceneanalysis.analyse(shot.path)
        shot.analysis = analysis
        photo_panel._refresh()
        return {
            "name": shot.path.name,
            "scene": analysis.scene,
            "confidence": round(analysis.scene_confidence, 2),
            "is_night": analysis.is_night,
            "brightness": round(analysis.brightness, 2),
            "sky_fraction": round(analysis.sky_fraction, 2),
            "recommended_move": analysis.move,
            "why": analysis.move_reason,
            "recommended_atmosphere": analysis.atmosphere,
            "suggested_seconds": analysis.suggested_seconds,
        }

    # -- changing the plan -------------------------------------------------------------------
    def set_shot(index: int, move: str = "", atmosphere: str = "", transform: str = "",
                 seconds: float = 0.0) -> dict:
        shots = photo_panel.shots()
        if not 0 <= int(index) < len(shots):
            return {"error": f"No photo at index {index}. There are {len(shots)}."}
        shot = shots[int(index)]
        changed = {}
        if move:
            if move not in ALL_MOVES:
                return {"error": f"'{move}' is not a move. Valid: {ALL_MOVES}"}
            shot.move = move
            changed["move"] = move
        if atmosphere:
            if atmosphere not in atmo.ATMOSPHERE_NAMES + ["Auto"]:
                return {"error": f"'{atmosphere}' is not an atmosphere. "
                                 f"Valid: {atmo.ATMOSPHERE_NAMES}"}
            shot.atmosphere = atmosphere
            changed["atmosphere"] = atmosphere
        if transform:
            if transform not in TRANSFORMS:
                return {"error": f"'{transform}' is not a transform. Valid: {TRANSFORMS}"}
            shot.transform = transform
            changed["transform"] = transform
        if seconds:
            shot.seconds = max(0.5, float(seconds))
            changed["seconds"] = shot.seconds
        photo_panel.inspector.set_shot(photo_panel.current_shot())
        photo_panel._refresh()
        return {"photo": shot.path.name, "changed": changed}

    def set_all_shots(move: str = "", atmosphere: str = "", transform: str = "",
                      seconds: float = 0.0) -> dict:
        shots = photo_panel.shots()
        if not shots:
            return {"error": "No photos loaded."}
        for index in range(len(shots)):
            outcome = set_shot(index, move, atmosphere, transform, seconds)
            if "error" in outcome:
                return outcome
        return {"applied_to": len(shots), "move": move or None,
                "atmosphere": atmosphere or None, "transform": transform or None,
                "seconds": seconds or None}

    def set_film_settings(aspect: str = "", seconds_each: float = 0.0,
                          crossfade: float = -1.0, fps: int = 0) -> dict:
        changed = {}
        if aspect:
            if aspect not in ASPECTS:
                return {"error": f"'{aspect}' is not a shape. Valid: {list(ASPECTS)}"}
            photo_panel.aspect_box.setCurrentText(aspect)
            changed["aspect"] = aspect
        if seconds_each:
            photo_panel.seconds_slider.setValue(int(round(float(seconds_each) * 10)))
            changed["seconds_each"] = float(seconds_each)
        if crossfade >= 0:
            photo_panel.crossfade_slider.setValue(int(round(float(crossfade) * 10)))
            changed["crossfade"] = float(crossfade)
        if fps:
            photo_panel.fps_box.setValue(int(fps))
            changed["fps"] = int(fps)
        photo_panel._refresh()
        settings = photo_panel.settings()
        return {"changed": changed, "now": {
            "aspect": settings.aspect, "seconds_each": settings.seconds_each,
            "crossfade": settings.crossfade, "fps": settings.fps,
            "estimated_total_seconds": round(settings.total_seconds(photo_panel.shots()), 1),
        }}

    def plan_video(use_ai: str = "never", target_seconds: float = 0.0,
                   reorder: bool = False) -> dict:
        shots = photo_panel.strip.paths()
        if not shots:
            return {"error": "No photos loaded. Add some first."}
        if use_ai not in ("auto", "always", "never"):
            return {"error": "use_ai must be 'auto', 'always' or 'never'."}
        plan = autopilot.plan(
            shots, context.registry, base_settings=photo_panel.settings(),
            target_seconds=float(target_seconds), use_ai=use_ai, reorder=bool(reorder),
            learning=context.learning,
        )
        photo_panel.apply_plan(plan)
        return {
            "shots": len(plan.shots),
            "order": [shot.path.name for shot in plan.shots],
            "moves": [shot.move for shot in plan.shots],
            "atmospheres": [shot.atmosphere for shot in plan.shots],
            "aspect": plan.settings.aspect,
            "seconds_each": plan.settings.seconds_each,
            "total_seconds": round(plan.settings.total_seconds(plan.shots), 1),
            "ai_available": plan.ai_available,
            "decisions": [f"{d.field_name}: {d.value}" for d in plan.decisions],
        }

    def render_video() -> dict:
        if not photo_panel.shots():
            return {"error": "No photos loaded."}
        if not context.locator.available:
            return {"error": "ffmpeg is not installed, so nothing can be rendered."}
        if context.project is None:
            return {"error": "No project is open."}
        photo_panel.create()
        settings = photo_panel.settings()
        return {
            "started": True,
            "shots": len(photo_panel.shots()),
            "estimated_seconds": round(settings.total_seconds(photo_panel.shots()), 1),
            "note": "Rendering runs in the background; the user watches progress on the "
                    "Photos screen. Do not claim it has finished.",
        }

    # -- knowledge about the app ------------------------------------------------------------
    def list_library(kind: str = "") -> dict:
        project = context.project
        if project is None:
            return {"error": "No project is open."}
        assets = project.assets(kind=kind)
        by_kind: dict[str, int] = {}
        for asset in assets:
            by_kind[asset.kind] = by_kind.get(asset.kind, 0) + 1
        # The tally comes first and in words: given only the list, an 8B local model
        # counted 51 "videos" in a library of photos, clips and audio.
        return {"count": len(assets), "by_kind": by_kind,
                "summary": ", ".join(
                    f"{count} {KIND_WORDS.get(name, name)}{'s' if count != 1 else ''}"
                    for name, count in sorted(by_kind.items())),
                "assets": [
            {"name": asset.label, "kind": asset.kind, "resolution": asset.resolution,
             "seconds": round(asset.duration, 2), "path": str(asset.path)}
            for asset in assets[:40]
        ]}

    def list_models(capability: str = "image_to_video", free_only: bool = False) -> dict:
        try:
            wanted = Capability(capability)
        except ValueError:
            return {"error": f"'{capability}' is not a capability. Valid: "
                             f"{[c.value for c in Capability]}"}
        catalog = context.registry.get("catalog")
        if catalog is None:
            return {"error": "The catalogue is unavailable."}
        found = []
        for spec in catalog.models_for(wanted):
            state = availability.model_availability(
                context.registry, "catalog", spec.id, wanted
            )
            if free_only and state.state is not availability.State.FREE:
                continue
            found.append({"id": spec.id, "state": state.state.value,
                          "reason": state.reason[:120]})
        return {"capability": capability, "count": len(found), "models": found[:30]}

    def list_options() -> dict:
        return {
            "moves": ALL_MOVES,
            "atmospheres": ["Auto"] + atmo.ATMOSPHERE_NAMES,
            "transforms": TRANSFORMS,
            "shapes": list(ASPECTS),
        }

    def learning_summary() -> dict:
        store = context.learning
        stats = store.stats()
        return {
            "enabled": store.enabled,
            "decisions_recorded": stats["choices"],
            "corrections": stats["overrides"],
            "videos_rendered": stats["renders"],
            "habits": [preference.sentence() for preference in store.all_preferences()],
        }

    def find_music(query: str, commercial_only: bool = False, limit: int = 8) -> dict:
        """Searches only; downloading stays a deliberate human action."""
        outcome = musicsearch.search(
            query, sources=musicsearch.NO_KEY_SOURCES, limit_per_source=int(limit),
            allow_non_commercial=not commercial_only,
            credentials=context.credentials, proxy=context.settings.proxy_url,
        )
        return {
            "found": len(outcome.tracks),
            "hidden_no_derivatives": outcome.hidden_unusable,
            "hidden_non_commercial": outcome.hidden_non_commercial,
            "errors": outcome.errors[:2],
            "tracks": [
                {"title": track.title[:60], "artist": track.artist[:40],
                 "licence": track.licence.code, "allows": track.licence.summary(),
                 "length": track.pretty_duration, "source": track.source}
                for track in outcome.tracks[:12]
            ],
            "note": "These are search results only. The user downloads a track from the "
                    "Find music screen, which shows the licence and required credit.",
        }

    def check_video_quality(path: str = "") -> dict:
        """Measures a video: noise, softness, and black-and-silent dead air."""
        window = getattr(photo_panel, "_main_window", None)
        panel = getattr(window, "repair_panel", None)
        target = Path(path).expanduser() if path else getattr(panel, "source", None)
        if target is None:
            return {"error": "No video given, and none is loaded in Repair & upscale."}
        if not target.exists():
            return {"error": f"'{target}' does not exist."}
        if not context.locator.available:
            return {"error": "ffmpeg is not installed, so nothing can be measured."}
        try:
            report = repair.scan(context.locator, target)
        except Exception as error:
            return {"error": str(error)[:200]}
        if panel is not None:
            panel.set_source(target)
            panel.show_report(report)
        plan = repair.recommend(
            report, upscaler_installed=bool(repair.installed_upscaler(context.local_tools)),
            tool_id=repair.installed_upscaler(context.local_tools) or "realesrgan",
        )
        return {
            "file": target.name,
            "length_seconds": round(report.info.duration, 1),
            "resolution": report.info.resolution,
            "has_audio": report.info.has_audio,
            "noise": report.measurement.noise_word,
            "noise_sigma": round(report.measurement.noise, 2),
            "detail": report.measurement.detail_word,
            "black_bars": (None if report.crop.is_full_frame
                           else f"{report.crop.width}x{report.crop.height} picture inside "
                                f"{report.info.resolution}"),
            "camera": report.shake.word,
            "interlaced": report.interlace.interlaced,
            "loudness_lufs": (round(report.audio.lufs, 1)
                              if report.audio.present else None),
            "audio_clipping": report.audio.is_clipping if report.audio.present else None,
            "dead_sections": [
                {"from": round(segment.start, 1), "to": round(segment.end, 1),
                 "seconds": round(segment.length, 1)} for segment in report.dead
            ],
            "dead_seconds": round(report.dead_seconds, 1),
            "findings": report.findings(),
            "suggested_steps": plan.describe(),
            "why": plan.reasons,
            "note": "Measured, nothing changed. Use repair_video to apply this, which "
                    "writes a new file and leaves the original alone.",
        }

    def repair_video(path: str = "", cut_dead: bool = True, ai_upscale: bool = False,
                     stabilise: bool | None = None, fix_loudness: bool | None = None) -> dict:
        window = getattr(photo_panel, "_main_window", None)
        panel = getattr(window, "repair_panel", None)
        if panel is None:
            return {"error": "The Repair screen is unavailable."}
        if path:
            panel.set_source(Path(path).expanduser())
        if panel.source is None:
            return {"error": "No video is loaded in Repair & upscale."}
        if panel.report is None or panel.report.source != panel.source:
            return {"error": "That video has not been analysed yet. Call "
                             "check_video_quality first — the settings come from the "
                             "measurement, so repairing without it would be guesswork."}
        panel.upscale_box.setChecked(bool(ai_upscale))
        # None means "leave the measured recommendation alone" -- the assistant should not
        # have to restate every setting to change one of them.
        if stabilise is not None:
            panel.stabilise_box.setChecked(bool(stabilise))
        if fix_loudness is not None:
            panel.loudness_box.setChecked(bool(fix_loudness))
        panel._set_all_cuts(bool(cut_dead))
        panel.run()
        return {"started": True, "file": panel.source.name,
                "steps": panel.current_plan().describe(),
                "note": "The user still has to confirm the dialog. Progress shows on the "
                        "Repair screen; the original file is not modified."}

    def switch_screen(name: str) -> dict:
        window = getattr(photo_panel, "_main_window", None)
        if window is None:
            return {"error": "Cannot switch screens from here."}
        if name not in window.panels:
            return {"error": f"'{name}' is not a screen. Valid: {list(window.panels)}"}
        window.go_to(name)
        return {"showing": name}

    # ------------------------------------------------------------------------------------
    return [
        Tool("get_project_info", "Look at the current project and app state.", {},
             project_info),
        Tool("list_photos",
             "List the photos loaded in Photos-to-video and their current settings.", {},
             list_photos),
        Tool("add_photos_from_folder",
             "Load every photo from a folder on disk into Photos-to-video.",
             {"folder": {"type": "string", "description": "Absolute folder path",
                         "required": True},
              "limit": {"type": "integer", "description": "Maximum photos to add"}},
             add_photos_from_folder),
        Tool("clear_photos", "Remove all loaded photos.", {}, clear_photos),
        Tool("analyse_photo",
             "Analyse one loaded photo: scene type, light, and what move suits it.",
             {"index": {"type": "integer", "description": "0-based index", "required": True}},
             analyse_photo),
        Tool("set_shot", "Change the settings of one loaded photo.",
             {"index": {"type": "integer", "required": True},
              "move": {"type": "string", "description": "Camera move"},
              "atmosphere": {"type": "string"},
              "transform": {"type": "string"},
              "seconds": {"type": "number"}},
             set_shot),
        Tool("set_all_shots", "Apply the same setting to every loaded photo.",
             {"move": {"type": "string"}, "atmosphere": {"type": "string"},
              "transform": {"type": "string"}, "seconds": {"type": "number"}},
             set_all_shots),
        Tool("set_film_settings",
             "Set the shape, pace, blend and frame rate of the whole video.",
             {"aspect": {"type": "string",
                         "description": "e.g. '16:9 landscape' or '9:16 vertical'"},
              "seconds_each": {"type": "number"},
              "crossfade": {"type": "number"},
              "fps": {"type": "integer"}},
             set_film_settings),
        Tool("plan_video",
             "Let the app analyse every photo and decide moves, atmosphere, pace and order.",
             {"use_ai": {"type": "string",
                         "description": "'never' (free), 'auto', or 'always'"},
              "target_seconds": {"type": "number",
                                 "description": "Desired total length, 0 for automatic"},
              "reorder": {"type": "boolean",
                          "description": "Allow reordering the photos for flow"}},
             plan_video),
        Tool("render_video",
             "Render the loaded photos into a finished video file.", {},
             render_video, confirm=True,
             danger_note="This writes a video file and can take minutes. If any shot is "
                         "set to 'AI video' with a paid model, it will also cost money."),
        Tool("list_library", "List the media already in the current project.",
             {"kind": {"type": "string", "description": "video, image or audio"}},
             list_library),
        Tool("list_models",
             "List AI models for a capability and whether they can actually run.",
             {"capability": {"type": "string",
                             "description": "e.g. image_to_video, text_to_image"},
              "free_only": {"type": "boolean"}},
             list_models),
        Tool("list_options",
             "List every valid move, atmosphere, transform and shape.", {}, list_options),
        Tool("learning_summary",
             "Report what the app has learned about this user's preferences.", {},
             learning_summary),
        Tool("find_music",
             "Search free, openly licensed music by mood. Returns results only; the user "
             "downloads from the Find music screen.",
             {"query": {"type": "string",
                        "description": "Mood or style, e.g. 'calm piano'", "required": True},
              "commercial_only": {"type": "boolean",
                                  "description": "Hide non-commercial licences"},
              "limit": {"type": "integer"}},
             find_music),
        Tool("check_video_quality",
             "Measure a video file: noise, softness, black bars, camera shake, "
             "interlacing, audio loudness and clipping, and where it is black or "
             "silent. Changes nothing.",
             {"path": {"type": "string",
                       "description": "Video file path; leave empty for the one already "
                                      "loaded in Repair & upscale"}},
             check_video_quality),
        Tool("repair_video",
             "Clean up a video that has already been measured: cut the dead air, reduce "
             "noise, and optionally AI-upscale it. Writes a new file.",
             {"path": {"type": "string", "description": "Video file path, or empty for "
                                                        "the loaded one"},
              "cut_dead": {"type": "boolean",
                           "description": "Remove the black and silent sections"},
              "ai_upscale": {"type": "boolean",
                             "description": "Also run the local AI upscaler; much slower"},
              "stabilise": {"type": "boolean",
                            "description": "Smooth a shaky camera; omit to keep what the "
                                           "measurement recommended"},
              "fix_loudness": {"type": "boolean",
                               "description": "Bring the audio to the loudness target; "
                                              "omit to keep the recommendation"}},
             repair_video, confirm=True,
             danger_note="This writes a new video file and can take a long time, "
                         "especially with the AI upscale. The original is not modified."),
        Tool("switch_screen", "Show a different screen in the app.",
             {"name": {"type": "string",
                       "description": "home, photos, edit, enhance, repair, generate, "
                                      "director, agent, music, library, timeline, "
                                      "templates, hub, learning, jobs, settings", "required": True}},
             switch_screen),
    ]
