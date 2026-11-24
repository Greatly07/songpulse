import os
import sys

# Prevent crashes in pythonw.exe / frozen --noconsole builds when libs write to stdout/stderr
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import ctypes

from songpulse.config import (
    APP_DIR,
    APP_NAME,
    APP_USER_MODEL_ID,
    ICON_CACHE_FILE,
    SINGLE_INSTANCE_KEY,
    is_windows_dark_mode,
    load_settings,
    migrate_startup_command,
    register_uri_protocol,
    set_window_always_on_top,
    set_windows_titlebar_theme,
)

from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication

from songpulse.theme import create_app_icon, export_multi_res_ico, resolve_icon_colors
from songpulse.themes_manager import load_theme_config
from songpulse.widgets import LoadingSplash


def _extract_song_id_arg(argv: list[str]) -> str:
    for arg in argv[1:]:
        low = arg.lower()
        if low.startswith("songpulse://song/"):
            return arg.split("songpulse://song/", 1)[1].strip("/ ")
    return ""


def main():
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)

    if "--make-icon" in sys.argv:
        out_ico = os.path.join(APP_DIR, "songpulse.ico")
        export_multi_res_ico(out_ico)
        print(f"Icon saved to: {out_ico}")
        sys.exit(0)

    register_uri_protocol()
    migrate_startup_command()

    open_song_id = _extract_song_id_arg(sys.argv)
    ipc_payload = f"OPEN_SONG:{open_song_id}".encode("utf-8") if open_song_id else b"SHOW"

    # Single-instance guard
    sock = QLocalSocket()
    sock.connectToServer(SINGLE_INSTANCE_KEY)
    if sock.waitForConnected(250):
        sock.write(ipc_payload)
        sock.flush()
        sock.waitForBytesWritten(250)
        sock.disconnectFromServer()
        sys.exit(0)

    saved_cfg = load_settings()
    start_tray = ("--tray" in sys.argv) or (
        bool(saved_cfg.get("start_minimized_to_tray", False)) and not open_song_id
    )

    # Show sleek startup loading card while SongPulse initializes
    theme_pref = saved_cfg.get("theme", "auto")
    sys_dark = is_windows_dark_mode()
    init_theme_cfg = load_theme_config(theme_pref, sys_dark)
    ic1, ic2 = resolve_icon_colors(saved_cfg.get("icon_color", "theme"), init_theme_cfg)

    splash = None
    if not start_tray:
        splash = LoadingSplash(
            dark=bool(init_theme_cfg.get("base_dark", True)),
            icon_colors=(ic1, ic2),
        )
        splash.show()
        app.processEvents()

    try:
        export_multi_res_ico(ICON_CACHE_FILE, ic1, ic2)
    except Exception:
        pass

    app_icon = create_app_icon(ic1, ic2)
    app.setWindowIcon(app_icon)

    QLocalServer.removeServer(SINGLE_INSTANCE_KEY)
    server = QLocalServer()
    server.listen(SINGLE_INSTANCE_KEY)

    from songpulse.main_window import SongPulseWindow

    window = SongPulseWindow(app_icon)

    def on_new_connection():
        client = server.nextPendingConnection()
        if client and client.waitForReadyRead(250):
            msg = client.readAll().data().decode("utf-8", errors="ignore").strip()
            if msg.startswith("OPEN_SONG:"):
                sid = msg.split("OPEN_SONG:", 1)[1].strip()
                window.open_song_by_id(sid)
            elif msg == "SHOW":
                window.show_and_focus()
        if client:
            client.disconnectFromServer()

    server.newConnection.connect(on_new_connection)

    if splash is not None:
        splash.close()
        splash.deleteLater()

    if open_song_id:
        window.open_song_by_id(open_song_id)
    elif not start_tray:
        window.show()
        set_windows_titlebar_theme(int(window.winId()), bool(window.last_dark_state))
        if window.is_pinned:
            set_window_always_on_top(int(window.winId()), True)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
