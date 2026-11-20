import os
import sys

# Prevent crashes in pythonw.exe / frozen --noconsole builds when libs write to stdout/stderr
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import ctypes

# Import config first so CUDA 13.x DLL paths & onnxruntime preloading run before PyQt6/workers
from songpulse.config import (
    APP_DIR,
    APP_NAME,
    APP_USER_MODEL_ID,
    ICON_CACHE_FILE,
    SINGLE_INSTANCE_KEY,
    set_window_always_on_top,
    set_windows_titlebar_theme,
)

from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication

from songpulse.main_window import SongPulseWindow
from songpulse.theme import create_app_icon, export_multi_res_ico


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

    if not os.path.exists(ICON_CACHE_FILE):
        try:
            export_multi_res_ico(ICON_CACHE_FILE)
        except Exception:
            pass

    app_icon = create_app_icon()
    app.setWindowIcon(app_icon)

    # Single-instance guard
    sock = QLocalSocket()
    sock.connectToServer(SINGLE_INSTANCE_KEY)
    if sock.waitForConnected(250):
        sock.write(b"SHOW")
        sock.flush()
        sock.waitForBytesWritten(250)
        sock.disconnectFromServer()
        sys.exit(0)

    QLocalServer.removeServer(SINGLE_INSTANCE_KEY)
    server = QLocalServer()
    server.listen(SINGLE_INSTANCE_KEY)

    window = SongPulseWindow(app_icon)

    def on_new_connection():
        client = server.nextPendingConnection()
        if client and client.waitForReadyRead(250):
            if client.readAll().data() == b"SHOW":
                window.show_and_focus()
        if client:
            client.disconnectFromServer()

    server.newConnection.connect(on_new_connection)

    if "--tray" not in sys.argv:
        window.show()
        set_windows_titlebar_theme(int(window.winId()), bool(window.last_dark_state))
        if window.is_pinned:
            set_window_always_on_top(int(window.winId()), True)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()