"""Find music you are actually allowed to put in a video.

The hard part is not searching, it is licensing. Most Creative Commons "music" indexed
online is **No-Derivatives**, and putting a track under a video is an adaptation — so ND
is unusable here no matter how good it sounds. This module normalises every source's
licence into one shape, states plainly what each one permits, and defaults to hiding the
ones you cannot legally use.

Sources needing no key: Openverse, ccMixter, Internet Archive.
Sources needing a free key: Jamendo, Pixabay.

Nothing downloads until asked; search only fetches metadata.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore

USER_AGENT = "MediaForgeStudio/1.0 (desktop video app)"
RETRIES = 3
BACKOFF = 1.4


# ======================================================================================
# Licensing
# ======================================================================================

@dataclass(frozen=True)
class Licence:
    code: str                 # cc0, by, by-sa, by-nc, by-nd, by-nc-sa, by-nc-nd, pdm, custom
    name: str
    url: str = ""
    commercial: bool = True
    modify: bool = True       # may be adapted -- required to use as a soundtrack
    attribution: bool = True
    share_alike: bool = False

    @property
    def usable_in_video(self) -> bool:
        """A soundtrack is an adaptation, so No-Derivatives can never qualify."""
        return self.modify

    def summary(self) -> str:
        parts = []
        parts.append("commercial ok" if self.commercial else "non-commercial only")
        parts.append("can be edited" if self.modify else "NO EDITS ALLOWED")
        if self.attribution:
            parts.append("credit required")
        if self.share_alike:
            parts.append("share-alike")
        return " · ".join(parts)


LICENCES: dict[str, Licence] = {
    "cc0": Licence("cc0", "CC0 1.0 (public domain)",
                   "https://creativecommons.org/publicdomain/zero/1.0/",
                   True, True, False),
    "pdm": Licence("pdm", "Public Domain Mark",
                   "https://creativecommons.org/publicdomain/mark/1.0/",
                   True, True, False),
    "by": Licence("by", "CC BY (attribution)",
                  "https://creativecommons.org/licenses/by/4.0/", True, True, True),
    "by-sa": Licence("by-sa", "CC BY-SA (attribution, share-alike)",
                     "https://creativecommons.org/licenses/by-sa/4.0/",
                     True, True, True, True),
    "by-nc": Licence("by-nc", "CC BY-NC (non-commercial)",
                     "https://creativecommons.org/licenses/by-nc/4.0/",
                     False, True, True),
    "by-nc-sa": Licence("by-nc-sa", "CC BY-NC-SA (non-commercial, share-alike)",
                        "https://creativecommons.org/licenses/by-nc-sa/4.0/",
                        False, True, True, True),
    "by-nd": Licence("by-nd", "CC BY-ND — no edits, unusable as a soundtrack",
                     "https://creativecommons.org/licenses/by-nd/4.0/", True, False, True),
    "by-nc-nd": Licence("by-nc-nd", "CC BY-NC-ND — no edits, unusable as a soundtrack",
                        "https://creativecommons.org/licenses/by-nc-nd/4.0/",
                        False, False, True),
    "pixabay": Licence("pixabay", "Pixabay Content Licence",
                       "https://pixabay.com/service/license-summary/", True, True, False),
    "jamendo-free": Licence("jamendo-free", "Jamendo, free for non-commercial",
                            "https://licensing.jamendo.com/", False, True, True),
    "custom": Licence("custom", "Unknown licence — check before using", "", False, False, True),
}


def parse_licence(raw: str, version: str = "") -> Licence:
    """Turns any source's licence string or URL into one normalised Licence."""
    text = (raw or "").strip().lower()
    if not text:
        return LICENCES["custom"]

    if "publicdomain/zero" in text or text in ("cc0", "zero"):
        return LICENCES["cc0"]
    if "publicdomain/mark" in text or "public domain" in text or text == "pdm":
        return LICENCES["pdm"]
    if "pixabay" in text:
        return LICENCES["pixabay"]

    # Match the longest code first so "by-nc-nd" never degrades into "by".
    match = re.search(r"\b(by-nc-nd|by-nc-sa|by-nd|by-nc|by-sa|by)\b", text)
    if match:
        return LICENCES.get(match.group(1), LICENCES["custom"])

    # ccMixter spells them out in words.
    words = text
    if "attribution" in words:
        non_commercial = "noncommercial" in words or "non-commercial" in words
        no_derivatives = "noderiv" in words or "no deriv" in words
        share = "sharealike" in words or "share alike" in words
        code = "by"
        if non_commercial:
            code += "-nc"
        if share:
            code += "-sa"
        if no_derivatives:
            code = "by-nc-nd" if non_commercial else "by-nd"
        return LICENCES.get(code, LICENCES["custom"])

    return LICENCES["custom"]


# ======================================================================================
# Results
# ======================================================================================

@dataclass
class Track:
    title: str
    artist: str
    source: str
    licence: Licence
    audio_url: str
    page_url: str = ""
    duration_seconds: float = 0.0
    size_bytes: int = 0
    identifier: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def pretty_duration(self) -> str:
        if not self.duration_seconds:
            return "?"
        minutes, seconds = divmod(int(self.duration_seconds), 60)
        return f"{minutes}:{seconds:02d}"

    @property
    def pretty_size(self) -> str:
        if not self.size_bytes:
            return "?"
        megabytes = self.size_bytes / (1024 * 1024)
        return f"{megabytes:.1f} MB"

    def attribution_text(self) -> str:
        """The credit line to put in your description. Required for every CC-BY variant."""
        if not self.licence.attribution:
            return f"“{self.title}” — {self.artist or 'unknown'} ({self.licence.name}). No credit required."
        pieces = [f"“{self.title}”"]
        if self.artist:
            pieces.append(f"by {self.artist}")
        pieces.append(f"is licensed under {self.licence.name}")
        if self.page_url:
            pieces.append(f"— {self.page_url}")
        if self.licence.url:
            pieces.append(f"({self.licence.url})")
        return " ".join(pieces)


@dataclass
class SearchOutcome:
    tracks: list[Track] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    hidden_unusable: int = 0
    hidden_non_commercial: int = 0
    sources_tried: int = 0
    sources_failed: int = 0

    @property
    def all_sources_failed(self) -> bool:
        return self.sources_tried > 0 and self.sources_failed == self.sources_tried

    def explain_empty(self) -> str:
        """Why there is nothing to show. 'No results' and 'could not ask' differ."""
        if self.tracks:
            return ""
        if self.all_sources_failed:
            return ("Could not reach any music source — this is a network problem, not an "
                    "empty result. Details: " + "; ".join(self.errors[:2]))
        if self.errors:
            return ("Some sources failed: " + "; ".join(self.errors[:2])
                    + ". The rest returned nothing for this search.")
        if self.hidden_unusable or self.hidden_non_commercial:
            parts = []
            if self.hidden_unusable:
                parts.append(f"{self.hidden_unusable} were No-Derivatives")
            if self.hidden_non_commercial:
                parts.append(f"{self.hidden_non_commercial} were non-commercial")
            return ("Everything found was filtered out by licence — "
                    + " and ".join(parts) + ". Loosen the filters or try other words.")
        return "Nothing matched. Try different words, or tick more sources."


# ======================================================================================
# HTTP with patience
# ======================================================================================

class RateLimited(RuntimeError):
    """The source asked us to slow down. Distinct so the UI can say so precisely."""


def _get(url: str, params: dict | None = None, proxy: str = "", timeout: int = 25,
         stream: bool = False):
    """Retries with backoff, and waits out a rate limit rather than reporting nothing.

    These APIs are anonymous, so heavy use trips a 429. Returning an empty list in that
    case would read as "no such music exists", which is a different and misleading claim.
    """
    if requests is None:
        raise RuntimeError("The `requests` package is required for music search.")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    last: Exception | None = None

    for attempt in range(RETRIES):
        try:
            response = requests.get(
                url, params=params or {}, headers={"User-Agent": USER_AGENT},
                timeout=timeout, proxies=proxies, stream=stream,
            )
        except Exception as error:      # SSL resets and DNS blips are common here
            last = error
            time.sleep(BACKOFF * (attempt + 1))
            continue

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "")
            wait = float(retry_after) if retry_after.isdigit() else BACKOFF * (attempt + 2)
            if attempt < RETRIES - 1:
                time.sleep(min(wait, 10.0))
                continue
            raise RateLimited(
                "the source is rate limiting anonymous requests. Wait a minute and try "
                "again, or add a free API key for a source with higher limits."
            )
        return response

    raise RuntimeError(f"{type(last).__name__}: {last}")


# ======================================================================================
# Sources
# ======================================================================================

@dataclass
class Source:
    key: str
    name: str
    needs_key: bool
    homepage: str
    note: str = ""


SOURCES = [
    Source("openverse", "Openverse", False, "https://openverse.org",
           "Aggregates Freesound, Jamendo, Wikimedia. Broadest, but much of its music "
           "is No-Derivatives."),
    Source("ccmixter", "ccMixter", False, "https://ccmixter.org",
           "Remix-friendly by design, so almost everything here can be edited."),
    Source("archive", "Internet Archive", False, "https://archive.org",
           "Huge and free, but files can be very large and licences vary."),
    Source("jamendo", "Jamendo", True, "https://devportal.jamendo.com",
           "Best catalogue of proper production music. Free client id."),
    Source("pixabay", "Pixabay", True, "https://pixabay.com/api/docs/",
           "Its own licence allows commercial use with no attribution. Free key."),
]

NO_KEY_SOURCES = [source.key for source in SOURCES if not source.needs_key]


def search_openverse(query: str, limit: int, proxy: str = "",
                     modifiable_only: bool = True) -> list[Track]:
    params: dict[str, Any] = {"q": query, "page_size": min(limit, 20)}
    if modifiable_only:
        # Ask the server to exclude No-Derivatives; results are re-checked locally too.
        params["license"] = "cc0,pdm,by,by-sa,by-nc,by-nc-sa"
    response = _get("https://api.openverse.org/v1/audio/", params, proxy)
    if response.status_code != 200:
        raise RuntimeError(f"Openverse returned HTTP {response.status_code}")

    tracks: list[Track] = []
    for entry in response.json().get("results", []):
        url = entry.get("url") or ""
        if not url:
            continue
        # Openverse reports duration in milliseconds.
        duration = float(entry.get("duration") or 0) / 1000.0
        tracks.append(Track(
            title=entry.get("title") or "Untitled",
            artist=entry.get("creator") or "",
            source=f"Openverse / {entry.get('source', '')}",
            licence=parse_licence(entry.get("license", ""), entry.get("license_version", "")),
            audio_url=url,
            page_url=entry.get("foreign_landing_url") or entry.get("detail_url") or "",
            duration_seconds=duration,
            identifier=str(entry.get("id") or ""),
            tags=[tag.get("name", "") for tag in (entry.get("tags") or [])][:6],
        ))
    return tracks


def search_ccmixter(query: str, limit: int, proxy: str = "") -> list[Track]:
    response = _get("https://ccmixter.org/api/query",
                    {"search": query, "limit": min(limit, 20), "f": "json"}, proxy)
    if response.status_code != 200:
        raise RuntimeError(f"ccMixter returned HTTP {response.status_code}")
    try:
        entries = response.json()
    except ValueError as error:
        raise RuntimeError(f"ccMixter sent something that is not JSON: {error}") from error

    tracks: list[Track] = []
    for entry in entries if isinstance(entries, list) else []:
        files = entry.get("files") or []
        audio = next(
            (item for item in files
             if str(item.get("download_url", "")).lower().endswith((".mp3", ".ogg", ".flac"))),
            None,
        )
        if audio is None:
            continue
        tracks.append(Track(
            title=entry.get("upload_name") or "Untitled",
            artist=entry.get("user_real_name") or entry.get("user_name") or "",
            source="ccMixter",
            licence=parse_licence(entry.get("license_name", "")),
            audio_url=audio.get("download_url", ""),
            page_url=entry.get("file_page_url") or entry.get("upload_page_url") or "",
            duration_seconds=float(audio.get("file_seconds") or 0),
            size_bytes=int(audio.get("file_size") or 0),
            identifier=str(entry.get("upload_id") or ""),
        ))
    return tracks


def search_archive(query: str, limit: int, proxy: str = "") -> list[Track]:
    """Searches broadly, then filters on licence locally.

    Archive's Lucene syntax does not reliably support wildcards inside licenceurl, so a
    server-side licence filter silently returns nothing. Filtering here is slower but
    actually works.
    """
    expression = (f'mediatype:(audio) AND collection:(audio_music) AND ({query})')
    response = _get("https://archive.org/advancedsearch.php", {
        "q": expression,
        "fl[]": ["identifier", "title", "creator", "licenseurl", "downloads"],
        "rows": min(limit * 3, 50), "page": 1, "output": "json",
    }, proxy)
    if response.status_code != 200:
        raise RuntimeError(f"Internet Archive returned HTTP {response.status_code}")

    documents = response.json().get("response", {}).get("docs", [])
    tracks: list[Track] = []
    for document in documents:
        licence = parse_licence(document.get("licenseurl", ""))
        if licence.code == "custom":
            continue                      # unknown terms are not worth the risk
        identifier = document.get("identifier", "")
        creator = document.get("creator", "")
        if isinstance(creator, list):
            creator = ", ".join(creator[:2])
        tracks.append(Track(
            title=document.get("title") or identifier,
            artist=creator or "",
            source="Internet Archive",
            licence=licence,
            audio_url="",                 # resolved lazily; the item may hold many files
            page_url=f"https://archive.org/details/{identifier}",
            identifier=identifier,
        ))
        if len(tracks) >= limit:
            break
    return tracks


def resolve_archive_audio(track: Track, proxy: str = "") -> Track:
    """Finds the smallest usable audio file inside an Archive item."""
    if track.audio_url or not track.identifier:
        return track
    response = _get(f"https://archive.org/metadata/{track.identifier}", proxy=proxy)
    if response.status_code != 200:
        raise RuntimeError(f"Could not read the Archive item ({response.status_code}).")
    payload = response.json()
    candidates = [
        item for item in payload.get("files", [])
        if str(item.get("name", "")).lower().endswith((".mp3", ".ogg"))
    ]
    if not candidates:
        raise RuntimeError("That Archive item has no mp3 or ogg file.")
    # Prefer the smallest: originals can be hundreds of megabytes.
    candidates.sort(key=lambda item: int(item.get("size") or 0) or 1 << 40)
    chosen = candidates[0]
    track.audio_url = f"https://archive.org/download/{track.identifier}/{chosen['name']}"
    track.size_bytes = int(chosen.get("size") or 0)
    track.duration_seconds = float(chosen.get("length") or 0) if str(
        chosen.get("length", "")).replace(".", "").isdigit() else track.duration_seconds
    return track


def search_jamendo(query: str, limit: int, client_id: str, proxy: str = "") -> list[Track]:
    if not client_id:
        raise RuntimeError("Jamendo needs a free client id (Settings → API keys).")
    response = _get("https://api.jamendo.com/v3.0/tracks", {
        "client_id": client_id, "format": "json", "limit": min(limit, 20),
        "search": query, "audioformat": "mp32", "include": "musicinfo licenses",
    }, proxy)
    if response.status_code != 200:
        raise RuntimeError(f"Jamendo returned HTTP {response.status_code}")

    tracks: list[Track] = []
    for entry in response.json().get("results", []):
        licence_url = entry.get("license_ccurl") or ""
        tracks.append(Track(
            title=entry.get("name") or "Untitled",
            artist=entry.get("artist_name") or "",
            source="Jamendo",
            licence=parse_licence(licence_url) if licence_url else LICENCES["jamendo-free"],
            audio_url=entry.get("audiodownload") or entry.get("audio") or "",
            page_url=entry.get("shareurl") or "",
            duration_seconds=float(entry.get("duration") or 0),
            identifier=str(entry.get("id") or ""),
        ))
    return tracks


def search_pixabay(query: str, limit: int, api_key: str, proxy: str = "") -> list[Track]:
    if not api_key:
        raise RuntimeError("Pixabay needs a free API key (Settings → API keys).")
    response = _get("https://pixabay.com/api/", {
        "key": api_key, "q": query, "per_page": min(max(limit, 3), 20),
    }, proxy)
    if response.status_code != 200:
        raise RuntimeError(f"Pixabay returned HTTP {response.status_code}")
    tracks: list[Track] = []
    for entry in response.json().get("hits", []):
        url = entry.get("audio") or entry.get("previewURL") or ""
        if not url:
            continue
        tracks.append(Track(
            title=entry.get("tags") or "Untitled",
            artist=entry.get("user") or "",
            source="Pixabay",
            licence=LICENCES["pixabay"],
            audio_url=url,
            page_url=entry.get("pageURL") or "",
            duration_seconds=float(entry.get("duration") or 0),
            identifier=str(entry.get("id") or ""),
        ))
    return tracks


# ======================================================================================
# Public API
# ======================================================================================

def search(
    query: str,
    sources: Iterable[str] | None = None,
    limit_per_source: int = 12,
    allow_non_commercial: bool = True,
    allow_no_derivatives: bool = False,
    credentials: Any = None,
    proxy: str = "",
    progress: Callable[[float, str], None] = lambda fraction, message: None,
) -> SearchOutcome:
    """Searches the chosen sources and filters by what the licence actually permits."""
    outcome = SearchOutcome()
    chosen = list(sources or NO_KEY_SOURCES)
    if not query.strip():
        outcome.errors.append("Type something to search for.")
        return outcome

    outcome.sources_tried = len(chosen)
    for index, key in enumerate(chosen):
        progress(index / max(1, len(chosen)), f"Searching {key}")
        try:
            if key == "openverse":
                found = search_openverse(query, limit_per_source, proxy,
                                         modifiable_only=not allow_no_derivatives)
            elif key == "ccmixter":
                found = search_ccmixter(query, limit_per_source, proxy)
            elif key == "archive":
                found = search_archive(query, limit_per_source, proxy)
            elif key == "jamendo":
                found = search_jamendo(query, limit_per_source,
                                       credentials.get("jamendo") if credentials else "", proxy)
            elif key == "pixabay":
                found = search_pixabay(query, limit_per_source,
                                       credentials.get("pixabay") if credentials else "", proxy)
            else:
                continue
        except RateLimited as error:
            outcome.errors.append(f"{key}: {error}")
            outcome.sources_failed += 1
            continue
        except Exception as error:
            outcome.errors.append(f"{key}: {error}")
            outcome.sources_failed += 1
            continue

        for track in found:
            if not track.licence.usable_in_video and not allow_no_derivatives:
                outcome.hidden_unusable += 1
                continue
            if not track.licence.commercial and not allow_non_commercial:
                outcome.hidden_non_commercial += 1
                continue
            outcome.tracks.append(track)

    progress(1.0, f"{len(outcome.tracks)} usable tracks")
    return outcome


def download(track: Track, destination: Path, proxy: str = "",
             progress: Callable[[float, str], None] = lambda fraction, message: None,
             max_bytes: int = 120 * 1024 * 1024) -> Path:
    """Fetches one track. Refuses absurdly large files rather than filling the disk."""
    if not track.audio_url:
        track = resolve_archive_audio(track, proxy)
    if not track.audio_url:
        raise RuntimeError("That track has no downloadable audio file.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    progress(0.05, f"Fetching {track.title}")
    response = _get(track.audio_url, proxy=proxy, timeout=120, stream=True)
    if response.status_code != 200:
        raise RuntimeError(f"Download failed with HTTP {response.status_code}")

    total = int(response.headers.get("Content-Length") or 0)
    if total and total > max_bytes:
        raise RuntimeError(
            f"That file is {total / (1024 * 1024):.0f} MB, which is too large for a "
            f"music bed. Pick another version or another track."
        )

    written = 0
    with destination.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=1 << 16):
            if not chunk:
                continue
            handle.write(chunk)
            written += len(chunk)
            if written > max_bytes:
                handle.close()
                destination.unlink(missing_ok=True)
                raise RuntimeError("The file kept growing past the size limit; stopped.")
            if total:
                progress(min(0.98, written / total), f"{written // 1024} KB")

    track.size_bytes = written
    progress(1.0, "Downloaded")
    return destination


def write_credits(tracks: Iterable[Track], destination: Path) -> Path:
    """Writes the attribution file. CC-BY is only satisfied if the credit is published."""
    lines = ["Music credits", "=" * 40, ""]
    for track in tracks:
        lines.append(track.attribution_text())
        lines.append("")
    destination.write_text("\n".join(lines), encoding="utf-8")
    return destination
