import ctypes
import html
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser

import keyboard
from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
    QPolygonF,
)
from PyQt6.QtWidgets import (
    QApplication,
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

from .config import TEMP_DIR, build_site_url, find_default_ytdlp_app, split_artists
from .dialogs import prompt_install_or_locate_ytdlp
from .theme import create_ui_icon, extract_cover_gradient_colors, rounded_pixmap
from .widgets import ElidedLabel, ElidedLinkButton, FlowWidget, get_song_link
from .workers import (
    AlbumInfoWorker,
    ArtistInfoWorker,
    SongDetailEnrichWorker,
    build_song_platform_links,
    resolve_youtube_watch_url,
)


def _format_ms(ms: int) -> str:
    sec = max(0, int(ms // 1000))
    return f"{sec // 60}:{sec % 60:02d}"


def _format_count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _extract_yt_video_id(url: str) -> str:
    if not url:
        return ""
    m = re.search(r"(?:v=|youtu\.be/|embed/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else ""


def _find_edge_exe() -> str:
    for p in (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ):
        if os.path.isfile(p):
            return p
    return ""


def _open_lightweight_web_player(title: str, artist: str, watch_url: str, dark: bool):
    """Opens a sleek, lightweight native Edge App-Mode web player for full track playback (0 MB app bloat)."""
    vid = _extract_yt_video_id(watch_url)
    edge = _find_edge_exe()
    if vid and edge:
        player_html = os.path.join(TEMP_DIR, "songpulse_player.html")
        bg = "#0E1117" if dark else "#F8FAFC"
        fg = "#F8FAFC" if dark else "#0F172A"
        safe_title = html.escape(f"{artist} - {title}".strip(" -") or "SongPulse Player")
        doc = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{safe_title}</title>
<style>
  html, body {{ margin:0; padding:0; width:100%; height:100%; background:{bg}; color:{fg}; font-family:'Segoe UI',sans-serif; overflow:hidden; }}
  iframe {{ width:100vw; height:100vh; border:0; display:block; }}
</style></head>
<body>
  <iframe src="https://www.youtube.com/embed/{vid}?autoplay=1&rel=0"
    allow="autoplay; encrypted-media; picture-in-picture" allowfullscreen></iframe>
</body></html>"""
        try:
            with open(player_html, "w", encoding="utf-8") as f:
                f.write(doc)
            file_uri = QUrl.fromLocalFile(os.path.abspath(player_html)).toString()
            subprocess.Popen(
                [edge, f"--app={file_uri}", "--window-size=500,320"],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return
        except Exception:
            pass
    if watch_url:
        if edge:
            try:
                subprocess.Popen([edge, f"--app={watch_url}", "--window-size=520,380"])
                return
            except Exception:
                pass
        webbrowser.open(watch_url)


def _rounded_pixmap_from_bytes(img_bytes: bytes, size: int, radius: int) -> QPixmap:
    pix = QPixmap()
    if img_bytes:
        pix.loadFromData(img_bytes)
    if pix.isNull():
        return QPixmap()
    scaled = pix.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x_off = (scaled.width() - size) // 2
    y_off = (scaled.height() - size) // 2
    cropped = scaled.copy(x_off, y_off, size, size)
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    p.setClipPath(clip)
    p.drawPixmap(0, 0, cropped)
    p.end()
    return out


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
        user32.SetFocus(hwnd)

        if target_tid and target_tid != cur_tid:
            user32.AttachThreadInput(cur_tid, target_tid, False)
        if fg_tid and fg_tid != cur_tid:
            user32.AttachThreadInput(cur_tid, fg_tid, False)
    except Exception:
        pass


def _find_window_by_title_hint(hint: str):
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
    """Launches yt-dlp downloader, passes the URL arg, and sends Ctrl+V."""
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


class _InfoPopupBackdrop(QWidget):
    """Dimmed in-app overlay backdrop with spacious responsive modal card."""

    dismissed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("background-color: rgba(0, 0, 0, 0.58);")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.card = QFrame()
        self.card.setObjectName("InfoPopupCard")
        self.card.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        lay.addWidget(self.card, alignment=Qt.AlignmentFlag.AlignCenter)

    def sync_card_size(self):
        w = max(340, min(640, int(self.width() * 0.80)))
        h = max(320, min(560, int(self.height() * 0.80)))
        self.card.setFixedSize(w, h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.sync_card_size()

    def mousePressEvent(self, event):
        if not self.card.geometry().contains(event.position().toPoint()):
            self.dismissed.emit()
            return
        super().mousePressEvent(event)


class SongDetailPage(QWidget):
    """Dynamic album-art gradient detail view with in-app Artist/Album modal popup, full web preview & yt-dlp."""

    back_requested = pyqtSignal()
    song_updated = pyqtSignal(str, dict)
    ytdlp_path_changed = pyqtSignal(str)
    like_toggled = pyqtSignal(str)

    def __init__(self, p_icons: dict, get_settings_fn, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.p_icons = p_icons
        self.get_settings = get_settings_fn
        self.song = None
        self.dark = True
        self.theme_cfg = None
        self.palette_colors = extract_cover_gradient_colors("", True)
        self.enrich_worker = None
        self.info_worker = None
        self._active_info_url = ""
        self._active_info_data = None
        self._seeking = False

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
        self._build_info_popup_overlay()

    def _build_ui(self):
        root_lay = QVBoxLayout(self)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # Sticky Top Action Bar
        self.sticky_header = QWidget()
        self.sticky_header.setStyleSheet("background: transparent; border: none;")
        sticky_outer = QHBoxLayout(self.sticky_header)
        sticky_outer.setContentsMargins(20, 14, 20, 10)

        sticky_col = QWidget()
        sticky_col.setMaximumWidth(860)
        sticky_col.setStyleSheet("background: transparent; border: none;")
        top_bar = QHBoxLayout(sticky_col)
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.setSpacing(8)
        self.back_btn = QPushButton("←  Back")
        self.back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_btn.clicked.connect(self._on_back)

        self.like_btn = QPushButton("♡  Like")
        self.like_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.like_btn.clicked.connect(self._on_like_clicked)

        self.ytdlp_btn = QPushButton("⬇  Download with yt-dlp")
        self.ytdlp_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ytdlp_btn.clicked.connect(self._on_download_ytdlp)

        top_bar.addWidget(self.back_btn)
        top_bar.addStretch()
        top_bar.addWidget(self.like_btn)
        top_bar.addWidget(self.ytdlp_btn)

        sticky_outer.addStretch(1)
        sticky_outer.addWidget(sticky_col, 10)
        sticky_outer.addStretch(1)
        root_lay.addWidget(self.sticky_header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget { background: transparent; border: none; }"
        )

        inner = QWidget()
        inner.setAutoFillBackground(False)
        outer = QHBoxLayout(inner)
        outer.setContentsMargins(20, 6, 20, 24)

        col_widget = QWidget()
        col_widget.setMaximumWidth(860)
        col_widget.setMinimumWidth(0)
        col_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lay = QVBoxLayout(col_widget)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        # Hero Card
        self.hero_panel = QFrame()
        self.hero_panel.setMinimumWidth(0)
        hero_lay = QHBoxLayout(self.hero_panel)
        hero_lay.setContentsMargins(16, 16, 16, 16)
        hero_lay.setSpacing(18)

        self.cover_lbl = QLabel()
        self.cover_lbl.setFixedSize(148, 148)
        hero_lay.addWidget(self.cover_lbl, alignment=Qt.AlignmentFlag.AlignTop)

        hero_info_wrap = QWidget()
        hero_info_wrap.setMinimumWidth(0)
        hero_info_wrap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        hero_info_wrap.setStyleSheet("background: transparent; border: none;")
        hero_info = QVBoxLayout(hero_info_wrap)
        hero_info.setContentsMargins(0, 0, 0, 0)
        hero_info.setSpacing(6)

        self.title_lbl = QLabel("")
        self.title_lbl.setWordWrap(True)
        self.title_lbl.setMinimumWidth(0)
        self.title_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.title_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.hero_artists_flow = FlowWidget(h_spacing=6, v_spacing=4)

        self.album_link_btn = ElidedLinkButton("")
        self.album_link_btn.clicked.connect(self._on_album_clicked)

        self.found_lbl = QLabel("")
        self.found_lbl.setWordWrap(True)
        self.found_lbl.setMinimumWidth(0)

        self.ytdlp_status_lbl = QLabel("")
        self.ytdlp_status_lbl.setWordWrap(True)
        self.ytdlp_status_lbl.setMinimumWidth(0)

        hero_info.addWidget(self.title_lbl)
        hero_info.addWidget(self.hero_artists_flow)
        hero_info.addWidget(self.album_link_btn)
        hero_info.addSpacing(2)
        hero_info.addWidget(self.found_lbl)
        hero_info.addWidget(self.ytdlp_status_lbl)
        hero_info.addStretch()
        hero_lay.addWidget(hero_info_wrap, 1)
        lay.addWidget(self.hero_panel)

        # Audio Preview + Lightweight Full Track Web Player Card
        self.player_panel = QFrame()
        self.player_panel.setMinimumWidth(0)
        pl_lay = QVBoxLayout(self.player_panel)
        pl_lay.setContentsMargins(16, 14, 16, 14)
        pl_lay.setSpacing(8)

        pl_head = QHBoxLayout()
        pl_head.setSpacing(8)
        self.pl_title = QLabel("Audio Preview")
        self.pl_status = ElidedLabel("Loading preview...")
        self.pl_status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.full_player_btn = QPushButton("Full Player")
        self.full_player_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.full_player_btn.setToolTip("Open lightweight full-track web player")
        self.full_player_btn.clicked.connect(self._open_full_web_player)
        pl_head.addWidget(self.pl_title)
        pl_head.addWidget(self.pl_status, 1)
        pl_head.addWidget(self.full_player_btn)
        pl_lay.addLayout(pl_head)

        controls_row = QHBoxLayout()
        controls_row.setSpacing(8)

        self.play_btn = QPushButton()
        self.play_btn.setFixedSize(38, 38)
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.clicked.connect(self._toggle_play)

        self.time_cur_lbl = QLabel("0:00")
        self.time_cur_lbl.setFixedWidth(34)

        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 1000)
        self.seek_slider.setMinimumWidth(60)
        self.seek_slider.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.seek_slider.sliderReleased.connect(self._on_seek_released)

        self.time_tot_lbl = QLabel("0:30")
        self.time_tot_lbl.setFixedWidth(34)

        self.vol_lbl = QLabel("Vol")
        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(75)
        self.vol_slider.setFixedWidth(68)
        self.vol_slider.valueChanged.connect(self._on_volume_changed)

        controls_row.addWidget(self.play_btn)
        controls_row.addWidget(self.time_cur_lbl)
        controls_row.addWidget(self.seek_slider, 1)
        controls_row.addWidget(self.time_tot_lbl)
        controls_row.addSpacing(4)
        controls_row.addWidget(self.vol_lbl)
        controls_row.addWidget(self.vol_slider)
        pl_lay.addLayout(controls_row)
        lay.addWidget(self.player_panel)

        # Track Details Grid Card
        self.meta_panel = QFrame()
        self.meta_panel.setMinimumWidth(0)
        meta_outer = QVBoxLayout(self.meta_panel)
        meta_outer.setContentsMargins(16, 14, 16, 14)
        meta_outer.setSpacing(10)

        self.meta_heading = QLabel("Track Details")
        meta_outer.addWidget(self.meta_heading)

        self.meta_grid = QGridLayout()
        self.meta_grid.setHorizontalSpacing(16)
        self.meta_grid.setVerticalSpacing(8)
        self.meta_grid.setColumnStretch(1, 1)
        meta_outer.addLayout(self.meta_grid)
        lay.addWidget(self.meta_panel)

        # All Platforms Card
        self.links_panel = QFrame()
        self.links_panel.setMinimumWidth(0)
        links_outer = QVBoxLayout(self.links_panel)
        links_outer.setContentsMargins(16, 14, 16, 14)
        links_outer.setSpacing(10)

        self.links_heading = QLabel("Listen On")
        links_outer.addWidget(self.links_heading)

        self.platforms_flow = FlowWidget(h_spacing=7, v_spacing=7)
        links_outer.addWidget(self.platforms_flow)
        lay.addWidget(self.links_panel)

        # Lyrics Card
        self.lyrics_panel = QFrame()
        self.lyrics_panel.setMinimumWidth(0)
        lyr_lay = QVBoxLayout(self.lyrics_panel)
        lyr_lay.setContentsMargins(16, 14, 16, 14)
        lyr_lay.setSpacing(8)
        self.lyrics_heading = QLabel("Lyrics")
        self.lyrics_lbl = QLabel("")
        self.lyrics_lbl.setWordWrap(True)
        self.lyrics_lbl.setMinimumWidth(0)
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

    def _build_info_popup_overlay(self):
        """In-app centered popup card overlay for Artist & Album info (not a separate OS window)."""
        self.info_backdrop = _InfoPopupBackdrop(self)
        self.info_backdrop.dismissed.connect(self._close_info_popup)
        self.info_backdrop.hide()

        card_lay = QVBoxLayout(self.info_backdrop.card)
        card_lay.setContentsMargins(18, 14, 18, 16)
        card_lay.setSpacing(10)

        top = QHBoxLayout()
        self.popup_kicker = QLabel("ARTIST")
        self.popup_close_btn = QPushButton("✕")
        self.popup_close_btn.setFixedSize(26, 26)
        self.popup_close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.popup_close_btn.clicked.connect(self._close_info_popup)
        top.addWidget(self.popup_kicker)
        top.addStretch()
        top.addWidget(self.popup_close_btn)
        card_lay.addLayout(top)

        self.popup_scroll = QScrollArea()
        self.popup_scroll.setWidgetResizable(True)
        self.popup_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.popup_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.popup_scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget { background: transparent; border: none; }"
        )
        self.popup_body_widget = QWidget()
        self.popup_body_lay = QVBoxLayout(self.popup_body_widget)
        self.popup_body_lay.setContentsMargins(0, 0, 6, 0)
        self.popup_body_lay.setSpacing(10)
        self.popup_body_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.popup_scroll.setWidget(self.popup_body_widget)
        card_lay.addWidget(self.popup_scroll, 1)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QLinearGradient(0, 0, self.width() * 0.65, self.height())
        grad.setColorAt(0.0, self.palette_colors["g1"])
        grad.setColorAt(0.55, self.palette_colors["g2"])
        grad.setColorAt(1.0, self.palette_colors["base"])
        p.fillRect(self.rect(), QBrush(grad))
        p.end()

    def load_song(self, song: dict, dark: bool, theme_cfg: dict | None = None):
        self.stop_audio()
        self._close_info_popup()
        self.song = dict(song)
        self.dark = dark
        if theme_cfg is not None:
            self.theme_cfg = theme_cfg
        self.ytdlp_status_lbl.setText("")

        self.palette_colors = extract_cover_gradient_colors(
            self.song.get("cover_path", ""), dark, theme_cfg=self.theme_cfg
        )
        self._apply_dynamic_styles()
        self._render_song_fields()
        self._setup_audio_source()
        self.update()
        QTimer.singleShot(0, self._resync_flow_widgets)

        if self.enrich_worker and self.enrich_worker.isRunning():
            try:
                self.enrich_worker.enriched.disconnect()
            except Exception:
                pass
        self.enrich_worker = SongDetailEnrichWorker(self.song)
        self.enrich_worker.enriched.connect(self._on_enriched)
        self.enrich_worker.start()

    def _resync_flow_widgets(self):
        self.hero_artists_flow._sync_height()
        self.platforms_flow._sync_height()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "info_backdrop"):
            self.info_backdrop.setGeometry(self.rect())
            self.info_backdrop.sync_card_size()
        QTimer.singleShot(0, self._resync_flow_widgets)

    def set_theme_dark(self, dark: bool, theme_cfg: dict | None = None):
        self.dark = dark
        if theme_cfg is not None:
            self.theme_cfg = theme_cfg
        if self.song:
            self.palette_colors = extract_cover_gradient_colors(
                self.song.get("cover_path", ""), dark, theme_cfg=self.theme_cfg
            )
            self._apply_dynamic_styles()
            self._render_song_fields()
            if self._active_info_data is not None and self.info_backdrop.isVisible():
                self._render_popup_data(self._active_info_data)
            self.update()

    def _apply_dynamic_styles(self):
        ac = self.palette_colors["accent"]
        ac_fg = self.palette_colors["accent_text"]

        if self.dark:
            panel_bg = "rgba(14, 16, 22, 0.62)"
            popup_bg = "rgba(18, 21, 30, 0.96)"
            panel_b = "rgba(255, 255, 255, 0.13)"
            fg = "#F8FAFC"
            muted = "#CBD5E1"
            link_fg = "#93C5FD"
            chip_bg = "rgba(147, 197, 253, 0.14)"
            chip_hover = "rgba(147, 197, 253, 0.25)"
            chip_b = "rgba(147, 197, 253, 0.30)"
            btn_bg = "rgba(255, 255, 255, 0.10)"
            btn_hover = "rgba(255, 255, 255, 0.18)"
            btn_b = "rgba(255, 255, 255, 0.18)"
            groove = "rgba(255, 255, 255, 0.18)"
            row_hi = "rgba(59, 130, 246, 0.20)"
        else:
            panel_bg = "rgba(255, 255, 255, 0.78)"
            popup_bg = "rgba(255, 255, 255, 0.97)"
            panel_b = "rgba(15, 23, 42, 0.13)"
            fg = "#0F172A"
            muted = "#334155"
            link_fg = "#1D4ED8"
            chip_bg = "rgba(29, 78, 216, 0.08)"
            chip_hover = "rgba(29, 78, 216, 0.16)"
            chip_b = "rgba(29, 78, 216, 0.25)"
            btn_bg = "rgba(15, 23, 42, 0.06)"
            btn_hover = "rgba(15, 23, 42, 0.12)"
            btn_b = "rgba(15, 23, 42, 0.16)"
            groove = "rgba(15, 23, 42, 0.18)"
            row_hi = "rgba(37, 99, 235, 0.12)"

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

        self.info_backdrop.card.setStyleSheet(
            f"QFrame#InfoPopupCard {{ background-color: {popup_bg}; border: 1px solid {panel_b}; border-radius: 16px; }}"
        )

        glass_btn_qss = (
            f"QPushButton {{ background-color: {btn_bg}; color: {fg}; border: 1px solid {btn_b}; "
            f"border-radius: 8px; padding: 6px 12px; font-size: 12px; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: {btn_hover}; border-color: {ac}; }}"
        )
        self.back_btn.setStyleSheet(glass_btn_qss)
        self.like_btn.setStyleSheet(glass_btn_qss)
        self.full_player_btn.setStyleSheet(
            f"QPushButton {{ background-color: {btn_bg}; color: {fg}; border: 1px solid {btn_b}; "
            f"border-radius: 7px; padding: 4px 10px; font-size: 11px; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: {btn_hover}; border-color: {ac}; }}"
        )
        self.popup_close_btn.setStyleSheet(
            f"QPushButton {{ background-color: {btn_bg}; color: {muted}; border: 1px solid {btn_b}; "
            f"border-radius: 7px; padding: 0px; font-size: 12px; font-weight: 700; }}"
            f"QPushButton:hover {{ color: #F87171; border-color: #F87171; }}"
        )
        self.popup_kicker.setStyleSheet(
            f"color: {link_fg}; font-size: 11px; font-weight: 700; border: none; background: transparent;"
        )

        self.ytdlp_btn.setStyleSheet(
            f"QPushButton {{ background-color: {ac}; color: {ac_fg}; border: none; "
            f"border-radius: 8px; padding: 7px 14px; font-size: 12px; font-weight: 700; }}"
            f"QPushButton:hover {{ border: 1px solid {fg}; }}"
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

        self.title_lbl.setStyleSheet(
            f"color: {fg}; font-size: 21px; font-weight: 700; border: none; background: transparent;"
        )
        self._artist_chip_qss = (
            f"QPushButton {{ background-color: {chip_bg}; color: {link_fg}; border: 1px solid {chip_b}; "
            f"border-radius: 11px; padding: 3px 10px; font-size: 12px; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: {chip_hover}; border-color: {link_fg}; }}"
        )
        self._album_link_qss = (
            f"QPushButton {{ color: {link_fg}; background: transparent; border: none; "
            f"padding: 2px 0; text-align: left; font-size: 12px; font-weight: 600; }}"
            f"QPushButton:hover {{ text-decoration: underline; }}"
        )
        self.album_link_btn.setStyleSheet(self._album_link_qss)

        muted_qss = f"color: {muted}; font-size: 11px; border: none; background: transparent;"
        self.found_lbl.setStyleSheet(muted_qss)
        self.ytdlp_status_lbl.setStyleSheet(
            f"color: {link_fg}; font-size: 11px; font-weight: 600; border: none; background: transparent;"
        )
        self.pl_status.setStyleSheet(muted_qss)
        self.time_cur_lbl.setStyleSheet(muted_qss)
        self.time_tot_lbl.setStyleSheet(muted_qss)
        self.vol_lbl.setStyleSheet(muted_qss)

        head_qss = f"color: {fg}; font-size: 14px; font-weight: 700; border: none; background: transparent;"
        self.pl_title.setStyleSheet(head_qss)
        self.meta_heading.setStyleSheet(head_qss)
        self.links_heading.setStyleSheet(head_qss)
        self.lyrics_heading.setStyleSheet(head_qss)
        self.lyrics_lbl.setStyleSheet(
            f"color: {fg}; font-size: 12px; line-height: 1.5; border: none; background: transparent;"
        )

        self._glass_btn_qss = glass_btn_qss
        self._fg_hex = fg
        self._muted_hex = muted
        self._link_hex = link_fg
        self._row_hi = row_hi
        self._ext_icon = create_ui_icon("external", link_fg, 13)

    def _get_song_artists(self) -> list[dict]:
        s = self.song or {}
        artists = s.get("artists")
        if isinstance(artists, list) and artists:
            return artists
        return split_artists(s.get("artist", ""), s.get("shazam_artists"))

    def _sync_like_btn(self):
        liked = bool((self.song or {}).get("liked", False))
        self.like_btn.setText("♥  Liked" if liked else "♡  Like")
        if liked:
            self.like_btn.setStyleSheet(
                self._glass_btn_qss + " QPushButton { color: #F43F5E; border-color: rgba(244,63,94,0.45); }"
            )
        else:
            self.like_btn.setStyleSheet(self._glass_btn_qss)

    def _on_like_clicked(self):
        if not self.song:
            return
        self.song["liked"] = not bool(self.song.get("liked", False))
        self._sync_like_btn()
        self.like_toggled.emit(self.song.get("id", ""))

    def _render_song_fields(self):
        if not self.song:
            return
        s = self.song
        self._sync_like_btn()
        self.cover_lbl.setPixmap(rounded_pixmap(s.get("cover_path", ""), 148, 14))
        self.title_lbl.setText(s.get("title", "Unknown Title"))

        self.hero_artists_flow.clear()
        artists = self._get_song_artists()
        for art in artists:
            name = art.get("name", "")
            url = art.get("url") or (
                s.get("artist_url") if len(artists) == 1 else self._fallback_artist_url(name)
            )
            btn = QPushButton(name)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(self._artist_chip_qss)
            btn.setToolTip(f"View {name}")
            btn.clicked.connect(
                lambda checked=False, n=name, u=url: self._open_artist_popup(n, u)
            )
            self.hero_artists_flow.addWidget(btn)

        album = s.get("album", "")
        if album:
            self.album_link_btn.setText(f"Album: {album}")
            self.album_link_btn.setToolTip(f"View {album}")
            self.album_link_btn.show()
        else:
            self.album_link_btn.hide()

        ts = s.get("timestamp", "")
        method = s.get("method", "")
        found_txt = f"Identified on {ts}" if ts else ""
        if method:
            found_txt = f"{found_txt}  •  {method}" if found_txt else method
        self.found_lbl.setText(found_txt)

        while self.meta_grid.count():
            it = self.meta_grid.takeAt(0)
            if it and it.widget():
                it.widget().deleteLater()

        row_idx = 0
        if artists:
            k_lbl = QLabel("Artists" if len(artists) > 1 else "Artist")
            k_lbl.setStyleSheet(
                f"color: {self._muted_hex}; font-size: 12px; font-weight: 600; border: none; background: transparent;"
            )
            self.meta_grid.addWidget(k_lbl, row_idx, 0, alignment=Qt.AlignmentFlag.AlignTop)

            art_flow = FlowWidget(h_spacing=6, v_spacing=4)
            for art in artists:
                name = art.get("name", "")
                url = art.get("url") or (
                    s.get("artist_url") if len(artists) == 1 else self._fallback_artist_url(name)
                )
                abtn = QPushButton(name)
                abtn.setCursor(Qt.CursorShape.PointingHandCursor)
                abtn.setStyleSheet(self._artist_chip_qss)
                abtn.clicked.connect(
                    lambda checked=False, n=name, u=url: self._open_artist_popup(n, u)
                )
                art_flow.addWidget(abtn)
            self.meta_grid.addWidget(art_flow, row_idx, 1)
            row_idx += 1

        if album:
            k_lbl = QLabel("Album")
            k_lbl.setStyleSheet(
                f"color: {self._muted_hex}; font-size: 12px; font-weight: 600; border: none; background: transparent;"
            )
            self.meta_grid.addWidget(k_lbl, row_idx, 0, alignment=Qt.AlignmentFlag.AlignTop)
            alb_btn = ElidedLinkButton(album)
            alb_btn.setStyleSheet(self._album_link_qss)
            alb_btn.clicked.connect(self._on_album_clicked)
            self.meta_grid.addWidget(alb_btn, row_idx, 1)
            row_idx += 1

        dur_str = _format_ms(s["duration_ms"]) if s.get("duration_ms") else ""
        for label_txt, val_txt in [
            ("Release Date", s.get("released", "")),
            ("Genre", s.get("genre", "")),
            ("Record Label", s.get("label", "")),
            ("Duration", dur_str),
            ("ISRC", s.get("isrc", "")),
            ("Identified On", ts),
        ]:
            if not val_txt:
                continue
            k_lbl = QLabel(label_txt)
            k_lbl.setStyleSheet(
                f"color: {self._muted_hex}; font-size: 12px; font-weight: 600; border: none; background: transparent;"
            )
            self.meta_grid.addWidget(k_lbl, row_idx, 0, alignment=Qt.AlignmentFlag.AlignTop)

            v_lbl = QLabel(val_txt)
            v_lbl.setWordWrap(True)
            v_lbl.setMinimumWidth(0)
            v_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            v_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            v_lbl.setStyleSheet(
                f"color: {self._fg_hex}; font-size: 12px; border: none; background: transparent;"
            )
            self.meta_grid.addWidget(v_lbl, row_idx, 1)
            row_idx += 1

        self.platforms_flow.clear()
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

        lyrics = (s.get("lyrics") or "").strip()
        if lyrics:
            self.lyrics_lbl.setText(lyrics)
            self.lyrics_panel.show()
        else:
            self.lyrics_panel.hide()

        QTimer.singleShot(0, self._resync_flow_widgets)

    # --- IN-APP POPUP FOR ARTIST & ALBUM INFO ---
    def _clear_popup_body(self):
        while self.popup_body_lay.count():
            it = self.popup_body_lay.takeAt(0)
            if it is None:
                continue
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                self._clear_nested_layout(it.layout())

    def _clear_nested_layout(self, layout):
        while layout.count():
            it = layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                self._clear_nested_layout(it.layout())

    def _close_info_popup(self):
        self._active_info_url = ""
        self._active_info_data = None
        if self.info_worker and self.info_worker.isRunning():
            try:
                self.info_worker.loaded.disconnect()
                self.info_worker.error.disconnect()
            except Exception:
                pass
        if hasattr(self, "info_backdrop"):
            self.info_backdrop.hide()

    def _preferred_site_url(self, kind: str, query: str, default_url: str) -> str:
        site = (self.get_settings() or {}).get("preferred_info_site", "auto")
        return build_site_url(kind, query, site, default_url)

    def _open_artist_popup(self, artist_name: str, artist_url: str):
        self._active_info_url = self._preferred_site_url("artist", artist_name, artist_url)
        self._active_info_data = None
        self.popup_kicker.setText("ARTIST")
        self._show_popup_loading(f"Loading {artist_name}...")

        if self.info_worker and self.info_worker.isRunning():
            try:
                self.info_worker.loaded.disconnect()
                self.info_worker.error.disconnect()
            except Exception:
                pass
        self.info_worker = ArtistInfoWorker(artist_name, artist_url)
        self.info_worker.loaded.connect(self._on_popup_loaded)
        self.info_worker.error.connect(self._on_popup_error)
        self.info_worker.start()

    def _on_album_clicked(self):
        if not self.song:
            return
        album = (self.song.get("album") or "").strip()
        if not album:
            return
        artist = (self.song.get("artist") or "").strip()
        album_url = self.song.get("album_url") or self._fallback_album_url()
        self._open_album_popup(album, artist, album_url)

    def _open_album_popup(self, album: str, artist: str, album_url: str):
        self._active_info_url = self._preferred_site_url("album", f"{artist} {album}".strip(), album_url)
        self._active_info_data = None
        self.popup_kicker.setText("ALBUM")
        self._show_popup_loading(f"Loading {album}...")

        if self.info_worker and self.info_worker.isRunning():
            try:
                self.info_worker.loaded.disconnect()
                self.info_worker.error.disconnect()
            except Exception:
                pass
        self.info_worker = AlbumInfoWorker(album, artist, album_url)
        self.info_worker.loaded.connect(self._on_popup_loaded)
        self.info_worker.error.connect(self._on_popup_error)
        self.info_worker.start()

    def _show_popup_loading(self, text: str):
        self._clear_popup_body()
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {self._muted_hex}; font-size: 12px; border: none; background: transparent;"
        )
        self.popup_body_lay.addWidget(lbl)
        self.info_backdrop.setGeometry(self.rect())
        self.info_backdrop.sync_card_size()
        self.info_backdrop.raise_()
        self.info_backdrop.show()

    def _on_popup_error(self, err: str):
        self._clear_popup_body()
        lbl = QLabel(f"Could not load details: {err}")
        lbl.setWordWrap(True)
        lbl.setStyleSheet(
            f"color: {self._muted_hex}; font-size: 12px; border: none; background: transparent;"
        )
        self.popup_body_lay.addWidget(lbl)

    def _on_popup_loaded(self, data: dict):
        self._active_info_data = data
        kind = data.get("kind", "artist")
        q = data.get("name", "") if kind == "artist" else f"{data.get('artist', '')} {data.get('title', '')}".strip()
        self._active_info_url = self._preferred_site_url(kind, q, data.get("url", self._active_info_url))
        self._render_popup_data(data)

    def _render_popup_data(self, data: dict):
        self._clear_popup_body()
        kind = data.get("kind")
        url = self._active_info_url or data.get("url", "")

        head = QHBoxLayout()
        head.setSpacing(14)

        pix = _rounded_pixmap_from_bytes(
            data.get("image_bytes", b""), 76, 38 if kind == "artist" else 10
        )
        if pix.isNull() and kind == "album" and self.song:
            pix = rounded_pixmap(self.song.get("cover_path", ""), 76, 10)
        if not pix.isNull():
            img_lbl = QLabel()
            img_lbl.setFixedSize(76, 76)
            img_lbl.setPixmap(pix)
            img_lbl.setStyleSheet("border: none; background: transparent;")
            head.addWidget(img_lbl, alignment=Qt.AlignmentFlag.AlignTop)

        meta_col = QVBoxLayout()
        meta_col.setSpacing(4)

        # Clickable artist name or album title opens in browser
        title_text = data.get("name") if kind == "artist" else data.get("title", "")
        title_btn = ElidedLinkButton(title_text)
        title_btn.setIcon(self._ext_icon)
        title_btn.setIconSize(QSize(13, 13))
        title_btn.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        title_btn.setStyleSheet(
            f"QPushButton {{ color: {self._fg_hex}; background: transparent; border: none; "
            f"padding: 0; text-align: left; font-size: 16px; font-weight: 700; }}"
            f"QPushButton:hover {{ color: {self._link_hex}; text-decoration: underline; }}"
        )
        title_btn.setToolTip(f"Open {title_text}")
        title_btn.clicked.connect(lambda: webbrowser.open(url) if url else None)
        meta_col.addWidget(title_btn)

        stats_parts = []
        if kind == "artist":
            if data.get("genre"):
                stats_parts.append(data["genre"])
            if data.get("fans"):
                stats_parts.append(f"{_format_count(data['fans'])} fans")
            if data.get("nb_album"):
                stats_parts.append(f"{data['nb_album']} releases")
        else:
            if data.get("artist"):
                stats_parts.append(data["artist"])
            if data.get("release_date"):
                stats_parts.append(data["release_date"])
            if data.get("genre"):
                stats_parts.append(data["genre"])
            if data.get("track_count"):
                stats_parts.append(f"{data['track_count']} tracks")

        if stats_parts:
            st_lbl = QLabel("  •  ".join(stats_parts))
            st_lbl.setWordWrap(True)
            st_lbl.setMinimumWidth(0)
            st_lbl.setStyleSheet(
                f"color: {self._muted_hex}; font-size: 12px; border: none; background: transparent;"
            )
            meta_col.addWidget(st_lbl)

        if kind == "album" and data.get("copyright"):
            cp_lbl = QLabel(data["copyright"])
            cp_lbl.setWordWrap(True)
            cp_lbl.setMinimumWidth(0)
            cp_lbl.setStyleSheet(
                f"color: {self._muted_hex}; font-size: 11px; border: none; background: transparent;"
            )
            meta_col.addWidget(cp_lbl)

        meta_col.addStretch()
        head.addLayout(meta_col, 1)
        self.popup_body_lay.addLayout(head)

        if kind == "artist":
            if data.get("bio"):
                bio_lbl = QLabel(data["bio"])
                bio_lbl.setWordWrap(True)
                bio_lbl.setMinimumWidth(0)
                bio_lbl.setStyleSheet(
                    f"color: {self._fg_hex}; font-size: 12px; line-height: 1.45; border: none; background: transparent;"
                )
                self.popup_body_lay.addWidget(bio_lbl)

            top_tracks = data.get("top_tracks") or []
            if top_tracks:
                sub_h = QLabel("Popular Tracks")
                sub_h.setStyleSheet(
                    f"color: {self._fg_hex}; font-size: 12px; font-weight: 700; margin-top: 2px; border: none; background: transparent;"
                )
                self.popup_body_lay.addWidget(sub_h)
                for idx, tr in enumerate(top_tracks[:6], 1):
                    r_lay = QHBoxLayout()
                    r_lay.setSpacing(8)
                    num_lbl = QLabel(f"{idx}.")
                    num_lbl.setFixedWidth(20)
                    num_lbl.setStyleSheet(
                        f"color: {self._muted_hex}; font-size: 11px; border: none; background: transparent;"
                    )
                    t_lbl = ElidedLabel(tr.get("title", ""))
                    t_lbl.setStyleSheet(
                        f"color: {self._fg_hex}; font-size: 12px; font-weight: 500; border: none; background: transparent;"
                    )
                    dur = int(tr.get("duration") or 0)
                    d_lbl = QLabel(f"{dur // 60}:{dur % 60:02d}" if dur else "")
                    d_lbl.setStyleSheet(
                        f"color: {self._muted_hex}; font-size: 11px; border: none; background: transparent;"
                    )
                    r_lay.addWidget(num_lbl)
                    r_lay.addWidget(t_lbl, 1)
                    r_lay.addWidget(d_lbl)
                    if tr.get("preview") and HAS_MULTIMEDIA:
                        pbtn = QPushButton("▶")
                        pbtn.setFixedSize(24, 22)
                        pbtn.setCursor(Qt.CursorShape.PointingHandCursor)
                        pbtn.setStyleSheet(self._artist_chip_qss.replace("padding: 3px 10px;", "padding: 0;"))
                        pbtn.clicked.connect(
                            lambda checked=False, u=tr["preview"], tn=tr.get("title", ""): self._play_custom_preview(u, tn)
                        )
                        r_lay.addWidget(pbtn)
                    self.popup_body_lay.addLayout(r_lay)

            albums = data.get("albums") or []
            if albums:
                alb_h = QLabel("Releases")
                alb_h.setStyleSheet(
                    f"color: {self._fg_hex}; font-size: 12px; font-weight: 700; margin-top: 2px; border: none; background: transparent;"
                )
                self.popup_body_lay.addWidget(alb_h)
                alb_flow = FlowWidget(h_spacing=6, v_spacing=6)
                for al in albums[:8]:
                    label = al["title"]
                    if al.get("release_date"):
                        label += f" ({al['release_date']})"
                    btn = QPushButton(label)
                    btn.setCursor(Qt.CursorShape.PointingHandCursor)
                    btn.setStyleSheet(self._glass_btn_qss)
                    btn.clicked.connect(
                        lambda checked=False, at=al["title"], an=data.get("name", ""), au=al.get("url", ""): self._open_album_popup(at, an, au)
                    )
                    alb_flow.addWidget(btn)
                self.popup_body_lay.addWidget(alb_flow)
                QTimer.singleShot(0, alb_flow._sync_height)

        elif kind == "album":
            tracks = data.get("tracks") or []
            if tracks:
                tr_h = QLabel(f"Tracklist ({len(tracks)})")
                tr_h.setStyleSheet(
                    f"color: {self._fg_hex}; font-size: 12px; font-weight: 700; margin-top: 2px; border: none; background: transparent;"
                )
                self.popup_body_lay.addWidget(tr_h)
                cur_title_low = ((self.song or {}).get("title") or "").strip().lower()
                for tr in tracks[:30]:
                    is_current = cur_title_low and cur_title_low in (tr.get("title") or "").lower()
                    row_frame = QFrame()
                    row_frame.setStyleSheet(
                        f"QFrame {{ background-color: {self._row_hi if is_current else 'transparent'}; "
                        f"border: none; border-radius: 6px; }}"
                    )
                    r_lay = QHBoxLayout(row_frame)
                    r_lay.setContentsMargins(6, 3, 6, 3)
                    r_lay.setSpacing(8)

                    num_lbl = QLabel(f"{tr.get('number', '')}.")
                    num_lbl.setFixedWidth(24)
                    num_lbl.setStyleSheet(
                        f"color: {self._muted_hex}; font-size: 11px; border: none; background: transparent;"
                    )
                    t_lbl = ElidedLabel(tr.get("title", ""))
                    t_lbl.setStyleSheet(
                        f"color: {self._link_hex if is_current else self._fg_hex}; "
                        f"font-size: 12px; font-weight: {'700' if is_current else '500'}; "
                        f"border: none; background: transparent;"
                    )
                    dur_ms = int(tr.get("duration_ms") or 0)
                    d_lbl = QLabel(_format_ms(dur_ms) if dur_ms else "")
                    d_lbl.setStyleSheet(
                        f"color: {self._muted_hex}; font-size: 11px; border: none; background: transparent;"
                    )
                    r_lay.addWidget(num_lbl)
                    r_lay.addWidget(t_lbl, 1)
                    r_lay.addWidget(d_lbl)

                    if tr.get("preview_url") and HAS_MULTIMEDIA:
                        pbtn = QPushButton("▶")
                        pbtn.setFixedSize(24, 22)
                        pbtn.setCursor(Qt.CursorShape.PointingHandCursor)
                        pbtn.setStyleSheet(self._artist_chip_qss.replace("padding: 3px 10px;", "padding: 0;"))
                        pbtn.clicked.connect(
                            lambda checked=False, u=tr["preview_url"], tn=tr.get("title", ""): self._play_custom_preview(u, tn)
                        )
                        r_lay.addWidget(pbtn)
                    self.popup_body_lay.addWidget(row_frame)
            else:
                no_tr = QLabel("No tracklist available.")
                no_tr.setStyleSheet(
                    f"color: {self._muted_hex}; font-size: 12px; border: none; background: transparent;"
                )
                self.popup_body_lay.addWidget(no_tr)

    def _open_full_web_player(self):
        if not self.song:
            return
        self.stop_audio()
        url = self.song.get("youtube_video_url") or ""
        if not url:
            self.pl_status.setText("Finding full track...")
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
            self.pl_status.setText("No track link found")
            return
        self.pl_status.setText("Opened Full Player")
        _open_lightweight_web_player(
            self.song.get("title", ""),
            self.song.get("artist", ""),
            url,
            dark=self.dark,
        )

    def _play_custom_preview(self, preview_url: str, title: str):
        if not HAS_MULTIMEDIA or not self.player or not preview_url:
            return
        self.player.stop()
        self.player.setSource(QUrl(preview_url))
        self.play_btn.setEnabled(True)
        self.pl_status.setText(f"Previewing: {title}")
        self.player.play()

    def _copy_btn_feedback(self, btn, text):
        QApplication.clipboard().setText(text)
        orig = btn.text()
        btn.setText("Copied!")
        QTimer.singleShot(1400, lambda: btn.setText(orig))

    def _fallback_artist_url(self, artist_name: str = ""):
        artist = artist_name or (self.song or {}).get("artist", "")
        return f"https://music.youtube.com/search?q={urllib.parse.quote_plus(artist)}" if artist else ""

    def _fallback_album_url(self):
        s = self.song or {}
        q = f"{s.get('artist', '')} {s.get('album', '')}".strip()
        return f"https://music.youtube.com/search?q={urllib.parse.quote_plus(q)}" if q else ""

    def _on_enriched(self, song_id: str, updates: dict):
        if not self.song or self.song.get("id") != song_id:
            return
        had_preview = bool(self.song.get("preview_url"))
        self.song.update(updates)
        self._render_song_fields()
        if not had_preview and self.song.get("preview_url"):
            self._setup_audio_source()
        elif not self.song.get("preview_url"):
            self.pl_status.setText("Use Full Player for full track")
            self.play_btn.setEnabled(False)
        self.song_updated.emit(song_id, updates)

    def _setup_audio_source(self):
        self.seek_slider.setValue(0)
        self.time_cur_lbl.setText("0:00")
        self.time_tot_lbl.setText("0:30")

        if not HAS_MULTIMEDIA or self.player is None:
            self.pl_status.setText("Use Full Player")
            self.play_btn.setEnabled(False)
            return

        url = (self.song or {}).get("preview_url", "")
        if url:
            self.player.setSource(QUrl(url))
            self.play_btn.setEnabled(True)
            self.pl_status.setText("30s preview ready")
        else:
            self.play_btn.setEnabled(False)
            self.pl_status.setText("Loading preview...")

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
            if not self.pl_status.text().startswith("Previewing:"):
                self.pl_status.setText("Playing preview...")
        elif state == QMediaPlayer.PlaybackState.PausedState:
            self.pl_status.setText("Paused")
        else:
            self.pl_status.setText("30s preview ready")

    def _on_media_error(self, _err, err_str):
        self.pl_status.setText(f"Preview unavailable ({err_str or 'error'})")

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
                chosen = prompt_install_or_locate_ytdlp(self)
                if not chosen:
                    return
                exe_path = chosen
                self.ytdlp_path_changed.emit(exe_path)

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
        self._close_info_popup()
        self.back_requested.emit()
