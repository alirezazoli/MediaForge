"""The plumbing an installed build depends on: profile isolation, catalogue upgrades,
ffmpeg precedence, assets, logging and the single-instance guard."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from mediaforge import catalog, config, enginepython, paths, runtime


# -- isolation ----------------------------------------------------------------------------
def test_profile_and_workspace_follow_the_override():
    home = os.environ["MEDIAFORGE_HOME"]
    assert str(config.app_data_dir()).startswith(home)
    assert str(config.settings_path()).startswith(home)
    assert str(config.default_workspace()).startswith(home)
    assert str(config.Settings().workspace_path).startswith(home)


def test_settings_path_is_resolved_on_each_call(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIAFORGE_HOME", str(tmp_path))
    assert config.settings_path() == tmp_path / "MediaForge" / "settings.json"
    assert config.default_workspace() == tmp_path / "MediaForge Projects"


def test_machine_level_engines_ignore_the_override():
    # Installed engines are gigabytes; a test profile must not trigger a reinstall.
    assert not str(enginepython.engine_env_dir()).startswith(os.environ["MEDIAFORGE_HOME"])


# -- catalogue upgrades -------------------------------------------------------------------
@pytest.fixture()
def fresh_catalog(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIAFORGE_HOME", str(tmp_path))
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    (bundled / "models.json").write_text(json.dumps({"models": [{"id": "a"}]}), "utf-8")
    monkeypatch.setattr(catalog, "BUNDLED_DIR", bundled)
    monkeypatch.setattr(catalog, "PREVIOUSLY_SHIPPED", {})
    return bundled


def test_an_unedited_catalogue_follows_upgrades(fresh_catalog):
    assert catalog.load_models() == {"models": [{"id": "a"}]}
    (fresh_catalog / "models.json").write_text(json.dumps({"models": [{"id": "b"}]}), "utf-8")
    assert catalog.load_models() == {"models": [{"id": "b"}]}


def test_an_edited_catalogue_is_never_overwritten(fresh_catalog):
    catalog.load_models()
    user_copy = catalog.user_catalog_dir() / "models.json"
    user_copy.write_text(json.dumps({"models": [{"id": "mine"}]}), "utf-8")
    (fresh_catalog / "models.json").write_text(json.dumps({"models": [{"id": "b"}]}), "utf-8")
    assert catalog.load_models() == {"models": [{"id": "mine"}]}
    assert catalog.user_has_edited("models.json")


def test_a_broken_user_catalogue_falls_back_to_the_bundled_one(fresh_catalog):
    catalog.load_models()
    (catalog.user_catalog_dir() / "models.json").write_text("{ not json", "utf-8")
    assert catalog.load_models() == {"models": [{"id": "a"}]}


def test_reset_restores_the_bundled_catalogue(fresh_catalog):
    catalog.load_models()
    (catalog.user_catalog_dir() / "models.json").write_text('{"models": []}', "utf-8")
    catalog.reset_catalog("models.json")
    assert catalog.load_models() == {"models": [{"id": "a"}]}
    assert not catalog.user_has_edited("models.json")


def test_shipped_catalogues_are_valid_json():
    for name in ("models.json", "localtools.json", "templates.json"):
        path = Path(catalog.__file__).parent / name
        if path.exists():
            json.loads(path.read_text(encoding="utf-8"))


# -- ffmpeg and assets --------------------------------------------------------------------
def test_bundled_ffmpeg_outranks_path(monkeypatch, tmp_path):
    from mediaforge.ffmpeg_tools import FfmpegLocator

    suffix = ".exe" if sys.platform == "win32" else ""
    (tmp_path / f"ffmpeg{suffix}").write_bytes(b"")
    monkeypatch.setattr(paths, "bundled_ffmpeg_dir", lambda: tmp_path)
    assert FfmpegLocator._resolve("ffmpeg", "") == str(tmp_path / f"ffmpeg{suffix}")


def test_an_explicit_setting_outranks_the_bundled_copy(monkeypatch, tmp_path):
    from mediaforge.ffmpeg_tools import FfmpegLocator

    suffix = ".exe" if sys.platform == "win32" else ""
    bundled, chosen = tmp_path / "bundled", tmp_path / "chosen"
    for folder in (bundled, chosen):
        folder.mkdir()
        (folder / f"ffmpeg{suffix}").write_bytes(b"")
    monkeypatch.setattr(paths, "bundled_ffmpeg_dir", lambda: bundled)
    assert FfmpegLocator._resolve("ffmpeg", str(chosen)) == str(chosen / f"ffmpeg{suffix}")


def test_assets_ship_with_the_package():
    assert paths.icon_path().is_file()
    assert paths.asset_path("check.png").is_file()
    assert paths.asset_path("missing.png") == Path()


def test_engine_python_is_this_interpreter_from_source():
    assert enginepython.engine_python() == sys.executable
    assert enginepython._is_store_stub(r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\python.exe")


# -- runtime ------------------------------------------------------------------------------
def test_logging_writes_to_the_profile():
    path = runtime.configure_logging()
    import logging
    logging.getLogger("mediaforge.test").warning("probe line")
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert "probe line" in path.read_text(encoding="utf-8")
    assert str(path).startswith(os.environ["MEDIAFORGE_HOME"])


def test_a_second_instance_hands_over(qapp):
    first = runtime.SingleInstance()
    assert first.is_primary
    activated = []
    first.on_activate = lambda: activated.append(True)
    second = runtime.SingleInstance()
    assert not second.is_primary
    from conftest import wait_until
    assert wait_until(qapp, lambda: activated, 5)
    first._server.close()
