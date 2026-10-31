"""Atmosphere: the free half of what an AI video model adds.

An image-to-video model gives you two things — a real 3D camera move, and a change in the
air: rain, drifting cloud, glow, haze. The move needs the model. The air does not; ffmpeg
can do most of it, locally and free, over any moving shot.

Each entry returns a filter chain fragment that expects one video input and produces one
video output, so it can be appended to a shot's chain.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Atmosphere:
    name: str
    summary: str
    needs_extra_input: bool = False   # True when the chain synthesises a second source


# --------------------------------------------------------------------------------------
# Grades -- single-input colour treatments
# --------------------------------------------------------------------------------------

def _grade(chain: str) -> str:
    return chain


GRADE_CHAINS: dict[str, str] = {
    "None": "null",

    "Clean cinematic":
        "eq=contrast=1.06:saturation=1.04,unsharp=5:5:0.35:5:5:0.0",

    "Warm bloom":
        # Split, blur the highlights, screen them back over the original.
        "split=2[base][glow];"
        "[glow]curves=all='0/0 0.6/0.25 1/1',gblur=sigma=22[soft];"
        "[base][soft]blend=all_mode=screen:all_opacity=0.32,"
        "eq=saturation=1.06:gamma_r=1.03",

    "City night glow":
        "split=2[base][glow];"
        "[glow]curves=all='0/0 0.72/0.18 1/1',gblur=sigma=26[soft];"
        "[base][soft]blend=all_mode=screen:all_opacity=0.42,"
        "eq=contrast=1.10:saturation=1.08:brightness=0.015",

    "Golden haze":
        "split=2[base][glow];"
        "[glow]gblur=sigma=30,colorbalance=rs=0.12:gs=0.04:bs=-0.10[soft];"
        "[base][soft]blend=all_mode=screen:all_opacity=0.26,"
        "colorbalance=rs=0.05:bs=-0.05,eq=saturation=1.08",

    "Teal and orange":
        "colorbalance=rs=0.10:gs=0.01:bs=-0.06:rm=0.03:bm=-0.03:"
        "rh=-0.06:bh=0.10,eq=contrast=1.10:saturation=1.05",

    "Cool blue night":
        "colorbalance=rs=-0.08:bs=0.14:rm=-0.04:bm=0.08,"
        "eq=contrast=1.08:saturation=0.94:brightness=-0.01",

    "Faded film":
        "curves=all='0/0.06 0.5/0.5 1/0.95',eq=saturation=0.86:contrast=0.96",

    "High contrast mono":
        "hue=s=0,eq=contrast=1.25",
}

VIGNETTE = "vignette=angle=PI/5:mode=backward"
GRAIN = "noise=alls={strength}:allf=t+u"


# --------------------------------------------------------------------------------------
# Weather -- chains that synthesise and overlay a second layer
# --------------------------------------------------------------------------------------

def rain_chain(width: int, height: int, fps: int, intensity: float = 0.55) -> str:
    """Procedural rain: noise stretched into streaks and scrolled downward.

    `noise` on a black source gives speckle; a tall vertical blur turns each speck into a
    streak, and scrolling the layer sells the fall. No external asset needed.
    """
    speed = 1.6 + intensity * 2.0
    opacity = 0.10 + intensity * 0.22
    return (
        f"[atmo_src]format=gray,"
        f"boxblur=1:1:{max(2, int(6 + intensity * 10))}:1,"
        f"eq=contrast={1.6 + intensity:.2f}:brightness=-0.42,"
        f"scale={width}:{height * 2},"
        f"crop={width}:{height}:0:'mod(t*{speed * height:.0f},{height})',"
        f"format=yuva420p,colorchannelmixer=aa={opacity:.3f}[rainlayer];"
        f"[base][rainlayer]overlay=0:0:shortest=1"
    )


def snow_chain(width: int, height: int, fps: int, intensity: float = 0.5) -> str:
    speed = 0.28 + intensity * 0.5
    opacity = 0.18 + intensity * 0.30
    return (
        f"[atmo_src]format=gray,"
        f"eq=contrast={2.6 + intensity:.2f}:brightness=-0.46,"
        f"gblur=sigma=1.1,"
        f"scale={width}:{height * 2},"
        f"crop={width}:{height}:'mod(t*18,{max(1, width // 8)})':'mod(t*{speed * height:.0f},{height})',"
        f"format=yuva420p,colorchannelmixer=aa={opacity:.3f}[snowlayer];"
        f"[base][snowlayer]overlay=0:0:shortest=1"
    )


def fog_chain(width: int, height: int, fps: int, intensity: float = 0.45) -> str:
    """Soft drifting haze -- heavily blurred noise crawling sideways."""
    opacity = 0.10 + intensity * 0.26
    return (
        f"[atmo_src]format=gray,gblur=sigma=48,"
        f"eq=contrast=1.25:brightness=0.06,"
        f"scale={int(width * 1.6)}:{height},"
        f"crop={width}:{height}:'mod(t*14,{max(1, int(width * 0.6))})':0,"
        f"format=yuva420p,colorchannelmixer=aa={opacity:.3f}[foglayer];"
        f"[base][foglayer]overlay=0:0:shortest=1"
    )


WEATHER_BUILDERS = {
    "Rain": rain_chain,
    "Snow": snow_chain,
    "Fog drift": fog_chain,
}

# What the UI offers, in menu order.
ATMOSPHERES: list[Atmosphere] = [
    Atmosphere("None", "No treatment."),
    Atmosphere("Clean cinematic", "Slight contrast and micro-sharpening. Safe on anything."),
    Atmosphere("Warm bloom", "Highlights bloom softly. Suits warm interiors."),
    Atmosphere("City night glow", "Lifts lit windows so night exteriors read properly."),
    Atmosphere("Golden haze", "Warm haze in the air. Suits golden-hour exteriors."),
    Atmosphere("Teal and orange", "The commercial-trailer grade."),
    Atmosphere("Cool blue night", "Pushes toward cold moonlight."),
    Atmosphere("Faded film", "Lifted blacks, muted colour."),
    Atmosphere("High contrast mono", "Black and white, punchy."),
    Atmosphere("Rain", "Falling rain over the shot.", needs_extra_input=True),
    Atmosphere("Snow", "Drifting snow over the shot.", needs_extra_input=True),
    Atmosphere("Fog drift", "Haze crawling across the frame.", needs_extra_input=True),
]

ATMOSPHERE_NAMES = [entry.name for entry in ATMOSPHERES]
BY_NAME = {entry.name: entry for entry in ATMOSPHERES}


def needs_extra_input(name: str) -> bool:
    entry = BY_NAME.get(name)
    return bool(entry and entry.needs_extra_input)


def build_chain(
    name: str, width: int, height: int, fps: int, intensity: float = 0.5,
    vignette: bool = False, grain: float = 0.0,
) -> tuple[str, bool]:
    """Returns (filter fragment, whether a synthetic second input is required).

    The fragment consumes label [base] (and [atmo_src] when weather is used) and leaves
    its result unlabelled at the end so the caller can name it.
    """
    weather = WEATHER_BUILDERS.get(name)
    finishing: list[str] = []
    if vignette:
        finishing.append(VIGNETTE)
    if grain > 0:
        finishing.append(GRAIN.format(strength=max(1, int(grain * 24))))

    if weather:
        chain = weather(width, height, fps, intensity)
        if finishing:
            chain += "," + ",".join(finishing)
        return chain, True

    grade = GRADE_CHAINS.get(name, "null")
    # Grades that split internally already consume [base] themselves.
    if grade.startswith("split="):
        chain = f"[base]{grade}"
    else:
        chain = f"[base]{grade}"
    if finishing:
        chain += "," + ",".join(finishing)
    return chain, False


def describe(name: str) -> str:
    entry = BY_NAME.get(name)
    return entry.summary if entry else ""
