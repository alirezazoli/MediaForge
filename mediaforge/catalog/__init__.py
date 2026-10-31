"""Data-driven catalogues.

`models.json` and `localtools.json` ship with the app and are copied into the user's
config folder on first run, so adding a model never requires touching code.

The user copy wins -- but only if the user actually changed it. A plain "user copy always
wins" rule means someone who never opened the file stays on the catalogue from the day
they first installed, and no update can ever bring them a new model or a corrected
package name. So each seeded copy is fingerprinted: while the file still matches what we
seeded, it follows the bundled catalogue through upgrades; the moment the user edits it,
it is theirs and is never overwritten.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path
from typing import Any

from ..config import app_data_dir

BUNDLED_DIR = Path(__file__).resolve().parent
log = logging.getLogger(__name__)

# Fingerprints of catalogues shipped before seeding kept a marker. A user copy that
# matches one of these byte for byte was never edited and may be refreshed; anything else
# without a marker is assumed to carry edits and is left alone.
PREVIOUSLY_SHIPPED = {
    "models.json": {"7cd2124cbcf18d501028eff1cf134ea5be2a38500800ee4e8fc8f73651a13a63"},
    "localtools.json": {"0277f18d337e4f5535fdd35512bec2010c16f3a4ed70809bf4c979ea2bf9da08"},
    "templates.json": {"45c2307fe2c8244d910cb1843015a01ccd8f728543ed24285d0367526043610c"},
}


def user_catalog_dir() -> Path:
    directory = app_data_dir() / "catalog"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seed_marker(user_copy: Path) -> Path:
    return user_copy.with_name(f".{user_copy.name}.seeded")


def _seed(bundled: Path, user_copy: Path) -> None:
    shutil.copy2(bundled, user_copy)
    _seed_marker(user_copy).write_text(_fingerprint(user_copy), encoding="utf-8")


def user_has_edited(name: str) -> bool:
    user_copy = user_catalog_dir() / name
    marker = _seed_marker(user_copy)
    if not user_copy.exists():
        return False
    if not marker.exists():
        # Seeded by a version that kept no fingerprint. Unedited only if it matches a
        # catalogue we actually shipped; otherwise it might hold edits we cannot see.
        return _fingerprint(user_copy) not in PREVIOUSLY_SHIPPED.get(name, set())
    return marker.read_text(encoding="utf-8").strip() != _fingerprint(user_copy)


def catalog_path(name: str) -> Path:
    """The user's copy, seeded on first use and refreshed on upgrade if untouched."""
    user_copy = user_catalog_dir() / name
    bundled = BUNDLED_DIR / name
    if not bundled.exists():
        return user_copy
    if not user_copy.exists():
        _seed(bundled, user_copy)
    elif not user_has_edited(name) and _fingerprint(user_copy) != _fingerprint(bundled):
        log.info("Catalogue %s refreshed from the bundled copy", name)
        _seed(bundled, user_copy)
    return user_copy


def load_catalog(name: str) -> dict[str, Any]:
    path = catalog_path(name)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        # A typo in the user's own catalogue must not brick the app: fall back to bundled.
        bundled = BUNDLED_DIR / name
        if path != bundled and bundled.exists():
            log.warning("%s is invalid (%s); using the bundled copy.", path.name, error)
            return json.loads(bundled.read_text(encoding="utf-8"))
        raise


def reset_catalog(name: str) -> Path:
    """Restores the bundled copy, discarding user edits."""
    user_copy = user_catalog_dir() / name
    bundled = BUNDLED_DIR / name
    if bundled.exists():
        _seed(bundled, user_copy)
    return user_copy


def load_models() -> dict[str, Any]:
    return load_catalog("models.json")


def load_local_tools() -> dict[str, Any]:
    return load_catalog("localtools.json")
