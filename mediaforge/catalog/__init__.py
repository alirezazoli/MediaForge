"""Data-driven catalogues.

`models.json` and `localtools.json` ship with the app but are copied into the user's
config folder on first run. The user copy wins, so editing a model id or adding a whole
new model never requires touching code or waiting for an update.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from ..config import app_data_dir

BUNDLED_DIR = Path(__file__).resolve().parent


def user_catalog_dir() -> Path:
    directory = app_data_dir() / "catalog"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def catalog_path(name: str) -> Path:
    """Returns the user-editable copy, seeding it from the bundled one on first use."""
    user_copy = user_catalog_dir() / name
    bundled = BUNDLED_DIR / name
    if not user_copy.exists() and bundled.exists():
        shutil.copy2(bundled, user_copy)
    return user_copy if user_copy.exists() else bundled


def load_catalog(name: str) -> dict[str, Any]:
    path = catalog_path(name)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        # A user typo in their own catalogue must not brick the app: fall back to bundled.
        bundled = BUNDLED_DIR / name
        if path != bundled and bundled.exists():
            print(f"[catalog] {path.name} is invalid ({error}); using the bundled copy.")
            return json.loads(bundled.read_text(encoding="utf-8"))
        raise


def reset_catalog(name: str) -> Path:
    """Restores the bundled copy, discarding user edits."""
    user_copy = user_catalog_dir() / name
    bundled = BUNDLED_DIR / name
    if bundled.exists():
        shutil.copy2(bundled, user_copy)
    return user_copy


def load_models() -> dict[str, Any]:
    return load_catalog("models.json")


def load_local_tools() -> dict[str, Any]:
    return load_catalog("localtools.json")
