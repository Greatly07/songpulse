import ctypes
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser

import keyboard
from PyQt6.QtCore import QPointF, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer

    HAS_MULTIMEDIA = True
except Exception:
    HAS_MULTIMEDIA = False

from .config import find_default_ytdlp_app
from .theme import extract_cover_gradient_colors, rounded_pixmap
from .widgets import FlowLayout, get_song_link
from .workers import (
    SongDetailEnrichWorker,
    build_song_platform_links,
    resolve_youtube_watch_url,
)


def _format_ms(ms: int) -> str:
    sec = max(0, int(ms // 1000))
    return f"{sec // 60}:{sec % 60:02d}"


def _make_play_icon(playing: bool, color_hex: str, size=16) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QBrush(QColor(color_hex)))
    p.setPen(Qt.PenStyle.NoPen)
    s = float(size)
    if playing:
        p.drawRoundedRect(int(s * 0.22), int(s * 0.18), int(s * 0.20), int(s * 0.64), 2, 2)
        p.drawRoundedRect(int(s * 0.58), int(s * 0.18), int(s * 0.20), int(s * 0.64), 2, 2)
    else:
        tri = QPolygonF(
            [
                QPointF(s * 0.28, s * 0.18),
                QPointF(s * 0.82, s * 0.50),
                QPointF(s * 0.28, s * 0.82),
            ]
        )
        p.drawPolygon(tri)
    p.end()
    return QIcon(pix)


def _get_visible_hwnds() -> set:
    hwnds = set()
    try:
        user32 = ctypes.windll.user32
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def _cb(hwnd, _lp):
            if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
                hwnds.add(int(hwnd))
            return True

        user32.EnumWindows(WNDENUMPROC(_cb), 0)
    except Exception:
        pass
    return hwnds


def _force_focus_window(hwnd: int):
    """Bypasses Windows foreground-focus restrictions reliably via AttachThreadInput."""
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        fg_hwnd = user32.GetForegroundWindow()
        fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None)
        target_tid = user32.GetWindowThreadProcessId(hwnd, None)
        cur_tid = kernel32.GetCurrentThreadId()

        if fg_tid and fg_tid != cur_tid:
            user32.AttachThreadInput(cur_tid, fg_tid, True)
        if target_tid and target_tid != cur_tid:
            user32.AttachThreadInput(cur_tid, target_tid, True)

        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)

        if target_tid and target_tid != cur_tid:
            user32.AttachThreadInput(cur_tid, target_tid, False)
        if fg_tid and fg_tid != cur_tid:
            user32.AttachThreadInput(cur_tid, fg_tid, False)
    except Exception:
        pass


def _find_window_by_title_hint(hint: str):
    """Fallback to find an already-open yt-dlp downloader window by title."""
    found = []
    try:
        user32 = ctypes.windll.user32
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        hint_l = hint.lower()

        def _cb(hwnd, _lp):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value.lower()
                    if any(k in title for k in ("yt-dlp", "ytdlp", "downloader", hint_l)):
                        found.append(int(hwnd))
            return True

        user32.EnumWindows(WNDENUMPROC(_cb), 0)
    except Exception:
        pass
    return found[0] if found else None


def _launch_and_autopaste_ytdlp(exe_path: str, url: str):
    """Launches a single yt-dlp downloader instance, passes the URL arg, and sends Ctrl+V."""
    before = _get_visible_hwnds()
    work_dir = os.path.dirname(exe_path) or None

    if exe_path.lower().endswith((".py", ".pyw")):
        py_exe = sys.executable
        if py_exe.lower().endswith("python.exe"):
            pyw = py_exe[:-10] + "pythonw.exe"
            if os.path.exists(pyw):
                py_exe = pyw
        subprocess.Popen([py_exe, exe_path, url], cwd=work_dir)
    else:
        subprocess.Popen([exe_path, url], cwd=work_dir)

    def _paste_worker():
        target_hwnd = None
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            time.sleep(0.25)
            new_hwnds = _get_visible_hwnds() - before
            if new_hwnds:
                target_hwnd = next(iter(new_hwnds))
                break

        if not target_hwnd:
            base_hint = os.path.splitext(os.path.basename(exe_path))[0]
            target_hwnd = _find_window_by_title_hint(base_hint)

        if target_hwnd:
            time.sleep(0.9)
            _force_focus_window(target_hwnd)
            time.sleep(0.25)
            try:
                keyboard.send("ctrl+v")
            except Exception:
                pass

    threading.Thread(target=_paste_worker, daemon=True).start()


class SongDetailPage(QWidget):
    """Hidden detail tab inside Library with dynamic album-art gradient, audio preview & yt-dlp."""

    back_requested = pyqtSignal()
    song_updated = pyqtSignal(str, dict)
    ytdlp_path_changed = pyqtSignal(str)

    def __init__(self, p_icons: dict, get_settings_fn, parent=None):
        super().__init__(parent)
        self.p_icons = p_icons
        self.get_settings = get_settings_fn
        self.song = None
        self.dark = True
        self.palette_colors = extract_cover_gradient_colors("", True)
        self.enrich_worker = None
        self._seeking = False

        # Audio preview player
        self.player = None
        self.audio_output = None
        if HAS_MULTIMEDIA:
            self.player = QMediaPlayer(self)
            self.audio_output = QAudioOutput(self)
            self.audio_output.setVolume(0.75)
            self.player.setAudioOutput(self.audio_output)
            self.player.positionChanged.connect(self._on_pos_changed)
            self.player.durationChanged.connect(self._on_dur_changed)
            self.player.playbackStateChanged.connect(self._on_playback_state)
            self.player.errorOccurred.connect(self._on_media_error)

        self._build_ui()

    def _build_ui(self):
        root_lay = QVBoxLayout(self)
        root_lay.setContentsMargins(0, 0, 0, 0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget { background: transparent; border: none; }")

        inner = QWidget()
        inner.setAutoFillBackground(False)
        outer = QHBoxLayout(inner)
        outer.setContentsMargins(24, 18, 24, 24)

        col_widget = QWidget()
        col_widget.setMaximumWidth(860)
        col_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lay = QVBoxLayout(col_widget)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        # Top Action Bar: Back + Download with yt-dlp
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)
        self.back_btn = QPushButton("←  Back to Library")
        self.back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_btn.clicked.connect(self._on_back)

        self.ytdlp_btn = QPushButton("⬇  Download with yt-dlp")
        self.ytdlp_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ytdlp_btn.clicked.connect(self._on_download_ytdlp)

        top_bar.addWidget(self.back_btn)
        top_bar.addStretch()
        top_bar.addWidget(self.ytdlp_btn)
        lay.addLayout(top_bar)

        # Hero Card: Large Cover + Title + Clickable Artist & Album Links
        self.hero_panel = QFrame()
        hero_lay = QHBoxLayout(self.hero_panel)
        hero_lay.setContentsMargins(18, 18, 18, 18)
        hero_lay.setSpacing(20)

        self.cover_lbl = QLabel()
        self.cover_lbl.setFixedSize(164, 164)
        hero_lay.addWidget(self.cover_lbl, alignment=Qt.AlignmentFlag.AlignTop)

        hero_info = QVBoxLayout()
        hero_info.setSpacing(6)

        self.title_lbl = QLabel("")
        self.title_lbl.setWordWrap(True)

        self.artist_link_btn = QPushButton("")
        self.artist_link_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.artist_link_btn.setFlat(True)
        self.artist_link_btn.clicked.connect(lambda: self._open_field_url("artist_url"))

        self.album_link_btn = QPushButton("")
        self.album_link_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.album_link_btn.setFlat(True)
        self.album_link_btn.clicked.connect(lambda: self._open_field_url("album_url"))

        self.found_lbl = QLabel("")
        self.found_lbl.setWordWrap(True)

        self.ytdlp_status_lbl = QLabel("")
        self.ytdlp_status_lbl.setWordWrap(True)

        hero_info.addWidget(self.title_lbl)
        hero_info.addWidget(self.artist_link_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        hero_info.addWidget(self.album_link_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        hero_info.addSpacing(4)
        hero_info.addWidget(self.found_lbl)
        hero_info.addWidget(self.ytdlp_status_lbl)
        hero_info.addStretch()
        hero_lay.addLayout(hero_info, 1)
        lay.addWidget(self.hero_panel)

        # Audio Preview Playback Card
        self.player_panel = QFrame()
        pl_lay = QVBoxLayout(self.player_panel)
        pl_lay.setContentsMargins(16, 14, 16, 14)
        pl_lay.setSpacing(8)

        pl_head = QHBoxLayout()
        self.pl_title = QLabel("Audio Preview")
        self.pl_status = QLabel("Loading preview...")
        pl_head.addWidget(self.pl_title)
        pl_head.addStretch()
        pl_head.addWidget(self.pl_status)
        pl_lay.addLayout(pl_head)

        controls_row = QHBoxLayout()
        controls_row.setSpacing(10)

        self.play_btn = QPushButton()
        self.play_btn.setFixedSize(38, 38)
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.clicked.connect(self._toggle_play)

        self.time_cur_lbl = QLabel("0:00")
        self.time_cur_lbl.setFixedWidth(34)

        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 1000)
        self.seek_slider.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.seek_slider.sliderReleased.connect(self._on_seek_released)

        self.time_tot_lbl = QLabel("0:30")
        self.time_tot_lbl.setFixedWidth(34)

        self.vol_lbl = QLabel("Vol")
        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(75)
        self.vol_slider.setFixedWidth(78)
        self.vol_slider.valueChanged.connect(self._on_volume_changed)

        controls_row.addWidget(self.play_btn)
        controls_row.addWidget(self.time_cur_lbl)
        controls_row.addWidget(self.seek_slider, 1)
        controls_row.addWidget(self.time_tot_lbl)
        controls_row.addSpacing(8)
        controls_row.addWidget(self.vol_lbl)
        controls_row.addWidget(self.vol_slider)
        pl_lay.addLayout(controls_row)
        lay.addWidget(self.player_panel)

        # Detailed Track Info Grid Card
        self.meta_panel = QFrame()
        meta_outer = QVBoxLayout(self.meta_panel)
        meta_outer.setContentsMargins(16, 14, 16, 14)
        meta_outer.setSpacing(10)

        self.meta_heading = QLabel("Track Details")
        meta_outer.addWidget(self.meta_heading)

        self.meta_grid = QGridLayout()
        self.meta_grid.setHorizontalSpacing(18)
        self.meta_grid.setVerticalSpacing(8)
        self.meta_grid.setColumnStretch(1, 1)
        meta_outer.addLayout(self.meta_grid)
        lay.addWidget(self.meta_panel)

        # All Platforms Card
        self.links_panel = QFrame()
        links_outer = QVBoxLayout(self.links_panel)
        links_outer.setContentsMargins(16, 14, 16, 14)
        links_outer.setSpacing(10)

        self.links_heading = QLabel("Listen & Open On")
        links_outer.addWidget(self.links_heading)

        self.platforms_flow = FlowLayout(margin=0, h_spacing=7, v_spacing=7)
        links_outer.addLayout(self.platforms_flow)
        lay.addWidget(self.links_panel)

        # Lyrics Card (shown only when lyrics are available)
        self.lyrics_panel = QFrame()
        lyr_lay = QVBoxLayout(self.lyrics_panel)
        lyr_lay.setContentsMargins(16, 14, 16, 14)
        lyr_lay.setSpacing(8)
        self.lyrics_heading = QLabel("Lyrics Snippet")
        self.lyrics_lbl = QLabel("")
        self.lyrics_lbl.setWordWrap(True)
        self.lyrics_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lyr_lay.addWidget(self.lyrics_heading)
        lyr_lay.addWidget(self.lyrics_lbl)
        lay.addWidget(self.lyrics_panel)

        lay.addStretch(1)

        outer.addStretch(1)
        outer.addWidget(col_widget, 10)
        outer.addStretch(1)
        self.scroll.setWidget(inner)
        root_lay.addWidget(self.scroll)

    # --- PAINT DYNAMIC ALBUM COVER GRADIENT ---
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QLinearGradient(0, 0, self.width() * 0.65, self.height())
        grad.setColorAt(0.0, self.palette_colors["g1"])
        grad.setColorAt(0.55, self.palette_colors["g2"])
        grad.setColorAt(1.0, self.palette_colors["base"])
        p.fillRect(self.rect(), QBrush(grad))
        p.end()

    # --- LOAD SONG & APPLY CONTRAST-SAFE STYLING ---
    def load_song(self, song: dict, dark: bool):
        self.stop_audio()
        self.song = dict(song)
        self.dark = dark
        self.ytdlp_status_lbl.setText("")

        self.palette_colors = extract_cover_gradient_colors(
            self.song.get("cover_path", ""), dark
        )
        self._apply_dynamic_styles()
        self._render_song_fields()
        self._setup_audio_source()
        self.update()

        # Enrich missing preview audio, clickable artist/album links & direct YouTube watch URL
        if self.enrich_worker and self.enrich_worker.isRunning():
            try:
                self.enrich_worker.enriched.disconnect()
            except Exception:
                pass
        self.enrich_worker = SongDetailEnrichWorker(self.song)
        self.enrich_worker.enriched.connect(self._on_enriched)
        self.enrich_worker.start()

    def set_theme_dark(self, dark: bool):
        self.dark = dark
        if self.song:
            self.palette_colors = extract_cover_gradient_colors(
                self.song.get("cover_path", ""), dark
            )
            self._apply_dynamic_styles()
            self._render_song_fields()
            self.update()

    def _apply_dynamic_styles(self):
        ac = self.palette_colors["accent"]
        ac_fg = self.palette_colors["accent_text"]

        if self.dark:
            panel_bg = "rgba(14, 16, 22, 0.62)"
            panel_b = "rgba(255, 255, 255, 0.13)"
            fg = "#F8FAFC"
            muted = "#CBD5E1"
            link_fg = "#93C5FD"
            btn_bg = "rgba(255, 255, 255, 0.10)"
            btn_hover = "rgba(255, 255, 255, 0.18)"
            btn_b = "rgba(255, 255, 255, 0.18)"
            groove = "rgba(255, 255, 255, 0.18)"
        else:
            panel_bg = "rgba(255, 255, 255, 0.78)"
            panel_b = "rgba(15, 23, 42, 0.13)"
            fg = "#0F172A"
            muted = "#334155"
            link_fg = "#1D4ED8"
            btn_bg = "rgba(15, 23, 42, 0.06)"
            btn_hover = "rgba(15, 23, 42, 0.12)"
            btn_b = "rgba(15, 23, 42, 0.16)"
            groove = "rgba(15, 23, 42, 0.18)"

        panel_qss = (
            f"QFrame {{ background-color: {panel_bg}; border: 1px solid {panel_b}; border-radius: 14px; }}"
        )
        for panel in (
            self.hero_panel,
            self.player_panel,
            self.meta_panel,
            self.links_panel,
            self.lyrics_panel,
        ):
            panel.setStyleSheet(panel_qss)

        glass_btn_qss = (
            f"QPushButton {{ background-color: {btn_bg}; color: {fg}; border: 1px solid {btn_b}; "
            f"border-radius: 8px; padding: 6px 12px; font-size: 12px; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: {btn_hover}; border-color: {ac}; }}"
        )
        self.back_btn.setStyleSheet(glass_btn_qss)

        self.ytdlp_btn.setStyleSheet(
            f"QPushButton {{ background-color: {ac}; color: {ac_fg}; border: none; "
            f"border-radius: 8px; padding: 7px 14px; font-size: 12px; font-weight: 700; }}"
            f"QPushButton:hover {{ opacity: 0.9; border: 1px solid {fg}; }}"
        )

        self.play_btn.setStyleSheet(
            f"QPushButton {{ background-color: {ac}; color: {ac_fg}; border: none; border-radius: 19px; }}"
            f"QPushButton:disabled {{ background-color: {btn_bg}; }}"
        )
        is_playing = (
            self.player is not None
            and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        )
        self.play_btn.setIcon(_make_play_icon(is_playing, ac_fg, 16))
        self.play_btn.setIconSize(QSize(16, 16))

        slider_qss = (
            f"QSlider::groove:horizontal {{ height: 5px; background: {groove}; border-radius: 2px; }}"
            f"QSlider::sub-page:horizontal {{ background: {ac}; border-radius: 2px; }}"
            f"QSlider::handle:horizontal {{ background: {fg}; width: 13px; height: 13px; "
            f"margin: -4px 0; border-radius: 6px; }}"
        )
        self.seek_slider.setStyleSheet(slider_qss)
        self.vol_slider.setStyleSheet(slider_qss)

        self.title_lbl.setStyleSheet(f"color: {fg}; font-size: 22px; font-weight: 700; border: none; background: transparent;")
        link_qss = (
            f"QPushButton {{ color: {link_fg}; background: transparent; border: none; "
            f"padding: 1px 0; text-align: left; font-size: 14px; font-weight: 600; }}"
            f"QPushButton:hover {{ text-decoration: underline; }}"
        )
        self.artist_link_btn.setStyleSheet(link_qss)
        self.album_link_btn.setStyleSheet(
            link_qss.replace("font-size: 14px;", "font-size: 12px; font-weight: 500;")
        )

        muted_qss = f"color: {muted}; font-size: 11px; border: none; background: transparent;"
        self.found_lbl.setStyleSheet(muted_qss)
        self.ytdlp_status_lbl.setStyleSheet(f"color: {link_fg}; font-size: 11px; font-weight: 600; border: none; background: transparent;")
        self.pl_status.setStyleSheet(muted_qss)
        self.time_cur_lbl.setStyleSheet(muted_qss)
        self.time_tot_lbl.setStyleSheet(muted_qss)
        self.vol_lbl.setStyleSheet(muted_qss)

        head_qss = f"color: {fg}; font-size: 14px; font-weight: 700; border: none; background: transparent;"
        self.pl_title.setStyleSheet(head_qss)
        self.meta_heading.setStyleSheet(head_qss)
        self.links_heading.setStyleSheet(head_qss)
        self.lyrics_heading.setStyleSheet(head_qss)
        self.lyrics_lbl.setStyleSheet(f"color: {fg}; font-size: 12px; line-height: 1.5; border: none; background: transparent;")

        self._glass_btn_qss = glass_btn_qss
        self._fg_hex = fg
        self._muted_hex = muted
        self._link_hex = link_fg

    def _render_song_fields(self):
        if not self.song:
            return
        s = self.song
        self.cover_lbl.setPixmap(rounded_pixmap(s.get("cover_path", ""), 164, 14))
        self.title_lbl.setText(s.get("title", "Unknown Title"))

        artist = s.get("artist", "Unknown Artist")
        self.artist_link_btn.setText(f"{artist}  ↗")
        self.artist_link_btn.setToolTip(f"Open artist page for {artist}")

        album = s.get("album", "")
        if album:
            self.album_link_btn.setText(f"Album: {album}  ↗")
            self.album_link_btn.setToolTip(f"Open album page for {album}")
            self.album_link_btn.show()
        else:
            self.album_link_btn.hide()

        ts = s.get("timestamp", "")
        method = s.get("method", "")
        found_txt = f"Identified on {ts}" if ts else ""
        if method:
            found_txt = f"{found_txt}  •  {method}" if found_txt else method
        self.found_lbl.setText(found_txt)

        # Rebuild Track Details grid
        while self.meta_grid.count():
            it = self.meta_grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

        dur_str = _format_ms(s["duration_ms"]) if s.get("duration_ms") else ""
        rows = [
            ("Artist", artist, s.get("artist_url") or self._fallback_artist_url()),
            ("Album", album, s.get("album_url") or (self._fallback_album_url() if album else "")),
            ("Release Date", s.get("released", ""), ""),
            ("Genre", s.get("genre", ""), ""),
            ("Record Label", s.get("label", ""), ""),
            ("Duration", dur_str, ""),
            ("ISRC", s.get("isrc", ""), ""),
            ("Identified On", ts, ""),
        ]
        row_idx = 0
        for label_txt, val_txt, url in rows:
            if not val_txt:
                continue
            k_lbl = QLabel(label_txt)
            k_lbl.setStyleSheet(f"color: {self._muted_hex}; font-size: 12px; font-weight: 600; border: none; background: transparent;")
            self.meta_grid.addWidget(k_lbl, row_idx, 0, alignment=Qt.AlignmentFlag.AlignTop)

            if url:
                v_btn = QPushButton(f"{val_txt}  ↗")
                v_btn.setFlat(True)
                v_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                v_btn.setStyleSheet(
                    f"QPushButton {{ color: {self._link_hex}; background: transparent; border: none; "
                    f"padding: 0; text-align: left; font-size: 12px; font-weight: 600; }}"
                    f"QPushButton:hover {{ text-decoration: underline; }}"
                )
                v_btn.clicked.connect(lambda checked=False, u=url: webbrowser.open(u))
                self.meta_grid.addWidget(v_btn, row_idx, 1, alignment=Qt.AlignmentFlag.AlignLeft)
            else:
                v_lbl = QLabel(val_txt)
                v_lbl.setWordWrap(True)
                v_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                v_lbl.setStyleSheet(f"color: {self._fg_hex}; font-size: 12px; border: none; background: transparent;")
                self.meta_grid.addWidget(v_lbl, row_idx, 1)
            row_idx += 1

        # Rebuild Platform Links
        while self.platforms_flow.count():
            it = self.platforms_flow.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

        for pname, icon_key, url in build_song_platform_links(s):
            btn = QPushButton(pname)
            btn.setIcon(self.p_icons.get(icon_key, self.p_icons["web"]))
            btn.setIconSize(QSize(13, 13))
            btn.setStyleSheet(self._glass_btn_qss)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, u=url: webbrowser.open(u))
            self.platforms_flow.addWidget(btn)

        copy_btn = QPushButton("Copy Link")
        copy_btn.setIcon(self.p_icons["copy"])
        copy_btn.setIconSize(QSize(13, 13))
        copy_btn.setStyleSheet(self._glass_btn_qss)
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_url = get_song_link(s)
        copy_btn.clicked.connect(lambda: self._copy_btn_feedback(copy_btn, copy_url))
        self.platforms_flow.addWidget(copy_btn)

        # Lyrics snippet
        lyrics = (s.get("lyrics") or "").strip()
        if lyrics:
            self.lyrics_lbl.setText(lyrics)
            self.lyrics_panel.show()
        else:
            self.lyrics_panel.hide()

    def _copy_btn_feedback(self, btn, text):
        QApplication.clipboard().setText(text)
        orig = btn.text()
        btn.setText("Copied!")
        QTimer.singleShot(1400, lambda: btn.setText(orig))

    def _fallback_artist_url(self):
        artist = (self.song or {}).get("artist", "")
        return f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(artist)}" if artist else ""

    def _fallback_album_url(self):
        s = self.song or {}
        q = f"{s.get('artist', '')} {s.get('album', '')}".strip()
        return f"https://music.youtube.com/search?q={urllib.parse.quote_plus(q)}" if q else ""

    def _open_field_url(self, key: str):
        if not self.song:
            return
        url = self.song.get(key)
        if not url:
            url = self._fallback_artist_url() if key == "artist_url" else self._fallback_album_url()
        if url:
            webbrowser.open(url)

    def _on_enriched(self, song_id: str, updates: dict):
        if not self.song or self.song.get("id") != song_id:
            return
        had_preview = bool(self.song.get("preview_url"))
        self.song.update(updates)
        self._render_song_fields()
        if not had_preview and self.song.get("preview_url"):
            self._setup_audio_source()
        elif not self.song.get("preview_url"):
            self.pl_status.setText("No audio preview available for this track")
            self.play_btn.setEnabled(False)
        self.song_updated.emit(song_id, updates)

    # --- AUDIO PREVIEW PLAYBACK ---
    def _setup_audio_source(self):
        self.seek_slider.setValue(0)
        self.time_cur_lbl.setText("0:00")
        self.time_tot_lbl.setText("0:30")

        if not HAS_MULTIMEDIA or self.player is None:
            self.pl_status.setText("QtMultimedia unavailable")
            self.play_btn.setEnabled(False)
            return

        url = (self.song or {}).get("preview_url", "")
        if url:
            self.player.setSource(QUrl(url))
            self.play_btn.setEnabled(True)
            self.pl_status.setText("30s preview ready")
        else:
            self.play_btn.setEnabled(False)
            self.pl_status.setText("Fetching audio preview...")

    def _toggle_play(self):
        if not self.player:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def stop_audio(self):
        if self.player:
            try:
                self.player.stop()
            except Exception:
                pass

    def _on_pos_changed(self, pos: int):
        if not self._seeking:
            self.seek_slider.setValue(pos)
        self.time_cur_lbl.setText(_format_ms(pos))

    def _on_dur_changed(self, dur: int):
        if dur > 0:
            self.seek_slider.setRange(0, dur)
            self.time_tot_lbl.setText(_format_ms(dur))

    def _on_seek_released(self):
        self._seeking = False
        if self.player:
            self.player.setPosition(self.seek_slider.value())

    def _on_volume_changed(self, val: int):
        if self.audio_output:
            self.audio_output.setVolume(val / 100.0)

    def _on_playback_state(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setIcon(_make_play_icon(playing, self.palette_colors["accent_text"], 16))
        if playing:
            self.pl_status.setText("Playing preview...")
        elif state == QMediaPlayer.PlaybackState.PausedState:
            self.pl_status.setText("Paused")
        else:
            self.pl_status.setText("30s preview ready")

    def _on_media_error(self, _err, err_str):
        self.pl_status.setText(f"Preview unavailable ({err_str or 'playback error'})")

    # --- YT-DLP DOWNLOADER INTEGRATION ---
    def _on_download_ytdlp(self):
        if not self.song:
            return
        settings = self.get_settings()
        exe_path = (settings.get("ytdlp_app_path") or "").strip()

        if not exe_path or not os.path.exists(exe_path):
            detected = find_default_ytdlp_app()
            if detected:
                exe_path = detected
                self.ytdlp_path_changed.emit(exe_path)
            else:
                picked, _ = QFileDialog.getOpenFileName(
                    self,
                    "Locate your yt-dlp Downloader app",
                    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                    "App or Script (*.exe *.pyw *.py *.bat);;All Files (*.*)",
                )
                if not picked:
                    return
                exe_path = picked
                self.ytdlp_path_changed.emit(exe_path)

        # Prefer direct youtube.com/watch?v=... link; resolve quickly if not cached yet
        url = self.song.get("youtube_video_url") or ""
        if not url:
            self.ytdlp_status_lbl.setText("Finding direct YouTube link...")
            QApplication.processEvents()
            url = resolve_youtube_watch_url(
                self.song.get("artist", ""), self.song.get("title", "")
            )
            if url:
                self.song["youtube_video_url"] = url
                self.song_updated.emit(self.song.get("id", ""), {"youtube_video_url": url})
            else:
                url = self.song.get("youtube_url") or ""

        if not url:
            QMessageBox.warning(self, "No Link Found", "Could not find a YouTube link for this song.")
            return

        QApplication.clipboard().setText(url)
        try:
            _launch_and_autopaste_ytdlp(exe_path, url)
            self.ytdlp_status_lbl.setText("Opened yt-dlp Downloader & pasted YouTube link!")
            QTimer.singleShot(4500, lambda: self.ytdlp_status_lbl.setText(""))
        except Exception as e:
            QMessageBox.warning(self, "Launch Failed", f"Could not open yt-dlp Downloader:\n{e}")

    def _on_back(self):
        self.stop_audio()
        self.back_requested.emit()