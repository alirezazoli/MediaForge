"""Process-level plumbing: logging, crash reporting, one running instance.

A windowed build has no console. Without this module an exception in a slot, a worker
thread or startup simply vanishes -- the window closes or a button does nothing, and
there is nothing for anyone to look at afterwards. Every error now lands in a log file
under the config folder, and an unhandled one is shown to the user with the path to it.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
import traceback
from pathlib import Path

from .config import app_data_dir
from .paths import APP_ID, APP_NAME, __version__, is_frozen

log = logging.getLogger("mediaforge")


def log_dir() -> Path:
    directory = app_data_dir() / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def log_file() -> Path:
    return log_dir() / "mediaforge.log"


def configure_logging() -> Path:
    """Rotating file log, plus the console when there is one.

    Rotation keeps it from growing without bound on a machine that runs the app for
    months: five files of two megabytes is enough history to diagnose anything recent.
    """
    root = logging.getLogger()
    if any(getattr(handler, "_mediaforge", False) for handler in root.handlers):
        return log_file()
    root.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    file_handler = logging.handlers.RotatingFileHandler(
        log_file(), maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler._mediaforge = True          # type: ignore[attr-defined]
    root.addHandler(file_handler)

    # A windowed build has sys.stderr = None; a StreamHandler on None raises on every
    # record, so the console handler only exists when there is a console.
    if sys.stderr is not None:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(formatter)
        console.setLevel(logging.WARNING)
        console._mediaforge = True           # type: ignore[attr-defined]
        root.addHandler(console)

    log.info("%s %s starting (frozen=%s, python=%s)", APP_NAME, __version__, is_frozen(),
             sys.version.split()[0])
    return log_file()


def _show_crash(summary: str) -> None:
    """Tells the user, if there is a GUI to tell them with."""
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
    except ImportError:
        return
    if QApplication.instance() is None:
        return
    box = QMessageBox()
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle(f"{APP_NAME} -- something went wrong")
    box.setText("An unexpected error occurred. Your projects are saved; the action that "
                "failed did not finish.")
    box.setInformativeText(f"{summary}\n\nDetails were written to:\n{log_file()}")
    box.exec()


def install_crash_hooks() -> None:
    """Routes uncaught exceptions -- main thread, worker threads, Qt -- into the log."""

    def on_exception(kind, value, tb) -> None:
        if issubclass(kind, KeyboardInterrupt):
            sys.__excepthook__(kind, value, tb)
            return
        log.critical("Unhandled exception\n%s", "".join(traceback.format_exception(kind, value, tb)))
        _show_crash(f"{kind.__name__}: {value}")

    def on_thread_exception(args) -> None:
        # Worker threads cannot open a dialog; the job system already reports their
        # failures in the UI. This catches the ones that escape it.
        log.error("Unhandled exception in thread %s\n%s", args.thread.name if args.thread else "?",
                  "".join(traceback.format_exception(args.exc_type, args.exc_value,
                                                     args.exc_traceback)))

    sys.excepthook = on_exception
    threading.excepthook = on_thread_exception

    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    except ImportError:
        return

    levels = {QtMsgType.QtDebugMsg: logging.DEBUG, QtMsgType.QtInfoMsg: logging.INFO,
              QtMsgType.QtWarningMsg: logging.WARNING, QtMsgType.QtCriticalMsg: logging.ERROR,
              QtMsgType.QtFatalMsg: logging.CRITICAL}
    qt_log = logging.getLogger("qt")

    def on_qt_message(kind, context, message) -> None:
        qt_log.log(levels.get(kind, logging.INFO), message)

    qInstallMessageHandler(on_qt_message)


def prepare_console() -> None:
    """Makes `MediaForge.exe --doctor` print, and print any path, in a terminal.

    The installed exe is a windowed program, so it starts with no stdout at all and the
    command-line options would print nothing; attaching to the parent's console, when
    there is one, fixes that without shipping a second console build. And a Windows
    console defaults to a legacy code page, where printing a path in Persian or any
    other non-Latin script raises UnicodeEncodeError halfway through the report.
    """
    if sys.platform == "win32" and sys.stdout is None:
        try:
            import ctypes
            if ctypes.windll.kernel32.AttachConsole(-1):      # ATTACH_PARENT_PROCESS
                sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
                sys.stderr = sys.stdout
        except (AttributeError, OSError):
            pass
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")


def set_windows_app_id() -> None:
    """Gives the taskbar our own identity, so the pinned icon is ours and not python.exe's."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except (AttributeError, OSError):
        pass


# ======================================================================================
# One instance
# ======================================================================================
_SERVER_NAME = "MediaForgeStudio-single-instance"


class SingleInstance:
    """Lets a second launch hand over to the window that is already open.

    Two instances would share one learning database, one settings file and one job
    queue on disk, and the last one to close would silently overwrite the other's
    settings. A second launch instead asks the first to come to the front, then exits.
    """

    def __init__(self) -> None:
        from PySide6.QtNetwork import QLocalServer, QLocalSocket

        self._server: QLocalServer | None = None
        self.on_activate = lambda: None

        probe = QLocalSocket()
        probe.connectToServer(_SERVER_NAME)
        if probe.waitForConnected(300):
            probe.write(b"activate")
            probe.waitForBytesWritten(300)
            probe.disconnectFromServer()
            self.is_primary = False
            return

        self.is_primary = True
        QLocalServer.removeServer(_SERVER_NAME)    # a stale socket after a crash
        self._server = QLocalServer()
        self._server.newConnection.connect(self._on_connection)
        if not self._server.listen(_SERVER_NAME):
            log.warning("Single-instance server could not listen: %s",
                        self._server.errorString())

    def _on_connection(self) -> None:
        if self._server is None:
            return
        connection = self._server.nextPendingConnection()
        if connection is not None:
            connection.readyRead.connect(lambda: (connection.readAll(), self.on_activate()))
