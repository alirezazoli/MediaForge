"""Finding, starting and stopping a local ComfyUI -- against a stand-in, never the real one.

The stand-in is a real folder with a real virtual environment and a main.py that answers
/system_stats the way ComfyUI does and starts a child process, so the test also proves
that stopping takes the whole process tree down.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from mediaforge import comfyserver

FAKE_MAIN = textwrap.dedent('''
    import subprocess, sys
    from http.server import BaseHTTPRequestHandler, HTTPServer

    port = int(sys.argv[sys.argv.index("--port") + 1])
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    open("child.pid", "w").write(str(child.pid))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/system_stats" else 404)
            self.end_headers()
            self.wfile.write(b"{}")
        def log_message(self, *args):
            pass

    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
''')


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _alive(pid: int) -> bool:
    result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True,
                            text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return str(pid) in result.stdout


@pytest.fixture(scope="module")
def fake_comfy(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("comfy") / "ComfyUI"
    root.mkdir()
    (root / "main.py").write_text(FAKE_MAIN, encoding="utf-8")
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(root / "venv")],
                   check=True, capture_output=True)
    return root


def test_finds_an_install_by_folder_and_its_python(fake_comfy):
    install = comfyserver.find_install(str(fake_comfy))
    assert install is not None and install.root == fake_comfy
    assert install.python.name == "python.exe" and not install.portable


def test_a_folder_without_main_py_is_not_comfyui(tmp_path):
    found = comfyserver.find_install(str(tmp_path))
    assert found is None or found.root != tmp_path


def test_wan_files_are_checked_on_disk(fake_comfy):
    install = comfyserver.find_install(str(fake_comfy))
    assert not comfyserver.has_wan22_files(install)
    models = fake_comfy / "models"
    for folder, name in (("diffusion_models", "wan2.2_ti2v_5B_fp16.safetensors"),
                         ("text_encoders", "umt5_xxl_fp8_e4m3fn_scaled.safetensors"),
                         ("vae", "wan2.2_vae.safetensors")):
        (models / folder).mkdir(parents=True, exist_ok=True)
        (models / folder / name).write_bytes(b"")
    assert comfyserver.has_wan22_files(install)


@pytest.mark.skipif(sys.platform != "win32", reason="tree stop is checked with tasklist")
def test_starts_answers_and_stops_with_its_children(fake_comfy, tmp_path):
    install = comfyserver.find_install(str(fake_comfy))
    url = f"http://127.0.0.1:{_free_port()}"
    process = comfyserver.start(install, url, tmp_path / "comfyui.log")
    try:
        assert comfyserver.wait_until_up(url, process, timeout=30)
        deadline = time.time() + 10
        while not (fake_comfy / "child.pid").exists() and time.time() < deadline:
            time.sleep(0.1)
        child = int((fake_comfy / "child.pid").read_text())
        assert _alive(child)
    finally:
        comfyserver.stop(process)
    assert process.poll() is not None
    assert not _alive(child), "the child must not outlive the server"


def test_a_server_that_exits_is_reported_quickly(fake_comfy, tmp_path):
    install = comfyserver.find_install(str(fake_comfy))
    broken = comfyserver.ComfyInstall(install.root, install.python, False)
    (tmp_path / "main.py").write_text("raise SystemExit(3)", encoding="utf-8")
    process = subprocess.Popen([str(broken.python), str(tmp_path / "main.py")])
    started = time.time()
    assert not comfyserver.wait_until_up("http://127.0.0.1:9", process, timeout=30)
    assert time.time() - started < 10


def test_photos_banner_offers_to_start_an_installed_comfyui(window, fake_comfy, monkeypatch):
    from mediaforge import autopilot

    panel = window.photo_panel
    install = comfyserver.find_install(str(fake_comfy))
    monkeypatch.setattr(window.context, "comfy_install", lambda: install)
    monkeypatch.setattr(comfyserver, "has_wan22_files", lambda found: True)
    monkeypatch.setattr(type(window.context.registry.get("comfyui")), "reachable",
                        lambda self, force=False: False)
    monkeypatch.setattr(autopilot, "best_ai_model", lambda registry: (None, "none"))
    panel._refresh_ai_banner()
    assert "start_comfyui" in panel.ai_banner.text()

    started = []
    monkeypatch.setattr(panel, "start_comfyui", lambda: started.append(True))
    panel._go_from_banner("start_comfyui")
    assert started == [True]
