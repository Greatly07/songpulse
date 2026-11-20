import os
import threading
import webbrowser
import copy

import keyboard
from winotify import Notification
from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    pyqtSignal,
)
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QFileDialog,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)
from .detail_view import SongDetailPage

from .audio import AudioBufferEngine
from .config import (
    APP_NAME,
    APP_VERSION,
    DEFAULT_SETTINGS,
    GITHUB_REPO,
    ICON_CACHE_FILE,
    find_default_ytdlp_app,
    ensure_ping_sound,
    get_nvidia_gpu_info,
    is_startup_enabled,
    is_windows_dark_mode,
    list_speakers_safe,
    load_history,
    load_settings,
    play_ping_sound,
    save_history,
    save_settings,
    set_startup_enabled,
    set_window_always_on_top,
    set_windows_titlebar_theme,
)
from .theme import (
    build_palette,
    build_stylesheet,
    create_platform_icon,
    create_ui_icon,
    ensure_chevron_icons,
    rounded_pixmap,
)
from .widgets import (
    CheckableComboBox,
    ClickableCard,
    FlowLayout,
    HelpBadge,
    HistoryRowCard,
    HotkeyRecorderEdit,
    NoWheelComboBox,
    NoWheelSpinBox,
    PulseIdentifyWidget,
    ResponsiveLinksBar,
    SongResultPopup,
    SubtleStatusHUD,
    get_song_link,
)
from .workers import (
    BenchmarkWorker,
    IdentifyWorker,
    ModelLoaderThread,
    ModelManager,
    UpdateCheckWorker,
    build_song_platform_links,
)


def _make_clean_scroll(inner_widget, horizontal_off=True):
    inner_widget.setObjectName("ScrollContent")
    inner_widget.setAutoFillBackground(False)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    if horizontal_off:
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.viewport().setAutoFillBackground(False)
    scroll.setWidget(inner_widget)
    return scroll


def _label_with_help(text: str, tip: str):
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    row.addWidget(QLabel(text))
    row.addWidget(HelpBadge(tip))
    return row


class SongPulseWindow(QMainWindow):
    hotkey_signal = pyqtSignal()

    SIDEBAR_EXPANDED_W = 204
    SIDEBAR_COLLAPSED_W = 62

    def __init__(self, app_icon: QIcon):
        super().__init__()
        self.app_icon = app_icon
        self.chevrons = ensure_chevron_icons()
        ensure_ping_sound()

        self.settings = load_settings()
        self._saved_settings_snapshot = copy.deepcopy(self.settings)
        self._saved_startup_snapshot = is_startup_enabled()

        self._audio_reconfig_timer = QTimer(self)
        self._audio_reconfig_timer.setSingleShot(True)
        self._audio_reconfig_timer.timeout.connect(self._apply_audio_reconfig_now)

        self.history = load_history()
        self.model_mgr = ModelManager()
        self.worker = None
        self.cancel_evt = threading.Event()
        self.bench_worker = None
        self.update_worker = None
        self.is_processing = False
        self.has_unsaved_settings = False
        self.tray_hint_shown = False
        self.last_dark_state = None
        self.registered_hotkey = None

        self.sidebar_collapsed = False
        self.sidebar_user_collapsed = False
        self.is_pinned = bool(self.settings.get("always_on_top", False))

        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setWindowIcon(self.app_icon)
        self.setMinimumSize(540, 480)
        if self.settings.get("save_window_size", True):
            self.resize(
                int(self.settings.get("window_width", 900)),
                int(self.settings.get("window_height", 650)),
            )
        else:
            self.resize(900, 650)

        self.p_icons = {
            k: create_platform_icon(k, 14)
            for k in (
                "shazam",
                "youtube",
                "yt_music",
                "spotify",
                "apple",
                "soundcloud",
                "deezer",
                "amazon",
                "tidal",
                "web",
                "copy",
            )
        }

        self.hud = SubtleStatusHUD()
        self.song_popup = SongResultPopup()

        self.engine = AudioBufferEngine(
            target_devices=self.settings.get("target_devices", []),
            monitor_all=self.settings.get("monitor_all_devices", True),
            buffer_seconds=self.settings.get("buffer_seconds", 12),
        )
        self.engine.start()

        # Smooth sidebar width animator
        self.sidebar_anim = QVariantAnimation(self)
        self.sidebar_anim.setDuration(200)
        self.sidebar_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.sidebar_anim.valueChanged.connect(self._on_sidebar_anim_step)
        self.sidebar_anim.finished.connect(self._on_sidebar_anim_finished)

        self._build_ui()
        self._build_tray()
        self.apply_theme()
        self._apply_sidebar_state(self.width() < 760, animate=False)
        self._update_pin_button_ui()

        self.hotkey_signal.connect(self._on_hotkey_triggered)
        self._register_hotkey(self.settings.get("hotkey", "ctrl+alt+s"))

        self.wave_timer = QTimer(self)
        self.wave_timer.timeout.connect(self._tick_waveform)
        self.wave_timer.start(30)

        self.periodic_timer = QTimer(self)
        self.periodic_timer.timeout.connect(self._on_periodic_tick)
        self.periodic_timer.start(2500)

        self.model_loader = ModelLoaderThread(self.model_mgr)
        self.model_loader.loaded.connect(self._on_model_loaded)
        self.model_loader.failed.connect(self._on_model_failed)
        self.model_loader.start()

    # --- UI CONSTRUCTION ---
    def _build_ui(self):
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)

        main_layout = QHBoxLayout(root)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Collapsible Sidebar (Gemini-style top bar: Brand left, Pin + Sidebar Toggle right)
        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(self.SIDEBAR_EXPANDED_W)
        side_layout = QVBoxLayout(self.sidebar)
        side_layout.setContentsMargins(10, 14, 10, 14)
        side_layout.setSpacing(6)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(2, 0, 0, 6)
        header_row.setSpacing(6)

        self.brand_icon = QLabel()
        self.brand_icon.setPixmap(self.app_icon.pixmap(24, 24))
        self.brand_lbl = QLabel(APP_NAME)
        self.brand_lbl.setStyleSheet("font-size: 15px; font-weight: 700;")

        self.pin_btn = QPushButton()
        self.pin_btn.setObjectName("SidebarIconBtn")
        self.pin_btn.setFixedSize(28, 28)
        self.pin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pin_btn.clicked.connect(self._toggle_always_on_top)

        self.collapse_btn = QPushButton()
        self.collapse_btn.setObjectName("SidebarIconBtn")
        self.collapse_btn.setFixedSize(28, 28)
        self.collapse_btn.setToolTip("Collapse / expand sidebar")
        self.collapse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.collapse_btn.clicked.connect(self._toggle_sidebar_manual)

        header_row.addWidget(self.brand_icon)
        header_row.addWidget(self.brand_lbl)
        self.header_stretch_spacer = QWidget()
        self.header_stretch_spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        header_row.addWidget(self.header_stretch_spacer)
        header_row.addWidget(self.pin_btn)
        header_row.addWidget(self.collapse_btn)
        side_layout.addLayout(header_row)
        side_layout.addSpacing(4)

        # Nav Tab Buttons with Icons
        self.nav_defs = [
            ("Identify", "identify"),
            ("Library", "library"),
            ("Settings", "settings"),
        ]
        self.nav_buttons = []
        for idx, (label, _icon_key) in enumerate(self.nav_defs):
            btn = QPushButton(f"  {label}")
            btn.setObjectName("NavBtn")
            btn.setIconSize(QSize(16, 16))
            btn.setToolTip(label)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, i=idx: self.switch_page(i))
            side_layout.addWidget(btn)
            self.nav_buttons.append(btn)

        side_layout.addStretch()

        # Footer AI Status
        self.ai_foot_widget = QWidget()
        ai_foot = QHBoxLayout(self.ai_foot_widget)
        ai_foot.setContentsMargins(4, 0, 2, 0)
        ai_foot.setSpacing(6)
        self.model_dot = QLabel("●")
        self.model_dot.setStyleSheet("color: #F59E0B; font-size: 10px;")
        self.model_status_lbl = QLabel("Loading AI...")
        self.model_status_lbl.setObjectName("Muted")
        self.model_status_lbl.setStyleSheet("font-size: 11px;")
        self.model_status_lbl.setWordWrap(True)
        self.model_help = HelpBadge(
            "Local AI model used only when 'Remove Voice & SFX' is turned on."
        )
        ai_foot.addWidget(self.model_dot)
        ai_foot.addWidget(self.model_status_lbl, 1)
        ai_foot.addWidget(self.model_help)
        side_layout.addWidget(self.ai_foot_widget)

        main_layout.addWidget(self.sidebar)

        # Stacked Pages
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_identify_page())
        self.pages.addWidget(self._build_library_page())
        self.pages.addWidget(self._build_settings_page())   
        self.detail_page = SongDetailPage(
            self.p_icons,
            get_settings_fn=lambda: self.settings,
        )
        self.detail_page.back_requested.connect(lambda: self.switch_page(1))
        self.detail_page.song_updated.connect(self._on_song_enriched)
        self.detail_page.ytdlp_path_changed.connect(self._on_ytdlp_path_picked)
        self.pages.addWidget(self.detail_page)  # Hidden index 3 (inside Library)
        main_layout.addWidget(self.pages, 1)

        self._set_active_nav(0)

    def _refresh_theme_icons(self):
        dark = bool(self.last_dark_state)
        idle_col = "#A1A1AA" if dark else "#475569"
        active_col = "#FFFFFF"
        ctrl_col = "#D4D4D8" if dark else "#334155"
        pin_col = "#3B82F6" if self.is_pinned else ctrl_col

        self.collapse_btn.setIcon(create_ui_icon("sidebar", ctrl_col, 16))
        self.pin_btn.setIcon(create_ui_icon("pin", pin_col, 15))
        self.details_ui_icon = create_ui_icon("details", ctrl_col, 13)

        cur_idx = 1 if self.pages.currentIndex() == 3 else self.pages.currentIndex()
        for idx, ((_label, ikey), btn) in enumerate(zip(self.nav_defs, self.nav_buttons)):
            col = active_col if idx == cur_idx else idle_col
            btn.setIcon(create_ui_icon(ikey, col, 16))

    # --- ANIMATED SIDEBAR ---
    def _toggle_sidebar_manual(self):
        self.sidebar_user_collapsed = not self.sidebar_collapsed
        self._apply_sidebar_state(self.sidebar_user_collapsed, animate=True)

    def _set_sidebar_content_collapsed(self, collapsed: bool):
        self.brand_icon.setVisible(not collapsed)
        self.brand_lbl.setVisible(not collapsed)
        self.header_stretch_spacer.setVisible(not collapsed)
        self.pin_btn.setVisible(not collapsed)
        self.model_status_lbl.setVisible(not collapsed)
        self.model_help.setVisible(not collapsed)

        for (label, _), btn in zip(self.nav_defs, self.nav_buttons):
            btn.setText("" if collapsed else f"  {label}")
            btn.setProperty("collapsed", collapsed)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _apply_sidebar_state(self, collapsed: bool, animate: bool = True):
        self.sidebar_collapsed = collapsed
        target_w = self.SIDEBAR_COLLAPSED_W if collapsed else self.SIDEBAR_EXPANDED_W

        self.sidebar_anim.stop()
        if collapsed:
            self._set_sidebar_content_collapsed(True)

        if not animate:
            self.sidebar.setFixedWidth(target_w)
            self._set_sidebar_content_collapsed(collapsed)
            return

        self.sidebar_anim.setStartValue(self.sidebar.width())
        self.sidebar_anim.setEndValue(target_w)
        self.sidebar_anim.start()

    def _on_sidebar_anim_step(self, value):
        w = int(value)
        self.sidebar.setFixedWidth(w)
        if not self.sidebar_collapsed and w >= 145 and not self.brand_lbl.isVisible():
            self._set_sidebar_content_collapsed(False)

    def _on_sidebar_anim_finished(self):
        self._set_sidebar_content_collapsed(self.sidebar_collapsed)

    def _toggle_always_on_top(self):
        self.is_pinned = not self.is_pinned
        self.settings["always_on_top"] = self.is_pinned
        save_settings(self.settings)
        set_window_always_on_top(int(self.winId()), self.is_pinned)
        self._update_pin_button_ui()

    def _update_pin_button_ui(self):
        self.pin_btn.setProperty("pinned", self.is_pinned)
        self.pin_btn.style().unpolish(self.pin_btn)
        self.pin_btn.style().polish(self.pin_btn)
        self.pin_btn.setToolTip(
            "Pinned always on top (click to unpin)"
            if self.is_pinned
            else "Pin window always on top"
        )
        self._refresh_theme_icons()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.width() < 760:
            if not self.sidebar_collapsed:
                self._apply_sidebar_state(True, animate=True)
        elif not self.sidebar_user_collapsed and self.sidebar_collapsed:
            self._apply_sidebar_state(False, animate=True)


    # --- IDENTIFY PAGE ---
    def _build_identify_page(self):
        page = QWidget()
        page.setObjectName("Page")
        page_lay = QVBoxLayout(page)
        page_lay.setContentsMargins(0, 0, 0, 0)

        scroll_inner = QWidget()
        outer = QHBoxLayout(scroll_inner)
        outer.setContentsMargins(22, 18, 22, 18)

        center_col = QWidget()
        center_col.setObjectName("Page")
        center_col.setMaximumWidth(860)
        center_col.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(center_col)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # Clean Header
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        h1 = QLabel("Identify Song")
        h1.setStyleSheet("font-size: 20px; font-weight: 700;")
        self.sub_status_lbl = QLabel("")
        self.sub_status_lbl.setObjectName("Muted")
        self.sub_status_lbl.setStyleSheet("font-size: 11px;")
        self.sub_status_lbl.setWordWrap(True)
        title_col.addWidget(h1)
        title_col.addWidget(self.sub_status_lbl)
        layout.addLayout(title_col)

        # No-Output Error Banner
        self.no_output_banner = QFrame()
        self.no_output_banner.setObjectName("ErrorBanner")
        no_out_lay = QHBoxLayout(self.no_output_banner)
        no_out_lay.setContentsMargins(10, 7, 10, 7)
        no_out_lay.setSpacing(10)
        no_out_lbl = QLabel(
            "⚠ No audio output selected — SongPulse cannot hear anything right now."
        )
        no_out_lbl.setObjectName("ErrorText")
        no_out_lbl.setWordWrap(True)
        fix_out_btn = QPushButton("Select Output")
        fix_out_btn.setObjectName("PlatformBtn")
        fix_out_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fix_out_btn.clicked.connect(lambda: self.switch_page(2))
        no_out_lay.addWidget(no_out_lbl, 1)
        no_out_lay.addWidget(fix_out_btn)
        layout.addWidget(self.no_output_banner)

        layout.addStretch(1)

        # Main Circular "Listen" (Live) Button + Waveform
        btn_wrap = QVBoxLayout()
        btn_wrap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        btn_wrap.setSpacing(10)

        self.pulse_btn = PulseIdentifyWidget()
        self.pulse_btn.setText("Listen")
        self.pulse_btn.clicked.connect(lambda: self.trigger_identify(mode="live"))
        btn_wrap.addWidget(self.pulse_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("DangerBtn")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self.cancel_identify)
        self.cancel_btn.hide()
        btn_wrap.addWidget(self.cancel_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # AI Voice/SFX Warning sits right between "Listen" and "Identify Last"
        self.ai_warn_banner = QFrame()
        self.ai_warn_banner.setObjectName("WarnBanner")
        warn_lay = QHBoxLayout(self.ai_warn_banner)
        warn_lay.setContentsMargins(12, 6, 12, 6)
        warn_lbl = QLabel(
            "⚠ Voice & SFX Removal is ON — specifically strips speech & sound effects. "
            "Not recommended for normal songs."
        )
        warn_lbl.setObjectName("WarnText")
        warn_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        warn_lbl.setWordWrap(True)
        warn_lay.addWidget(warn_lbl)
        btn_wrap.addWidget(self.ai_warn_banner)

        # Identify Last Buffer Button + AI Voice/SFX Removal Checkbox
        buffer_row = QHBoxLayout()
        buffer_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        buffer_row.setSpacing(10)

        self.buffer_btn = QPushButton("Identify Last 12s")
        self.buffer_btn.setObjectName("SecondaryActionBtn")
        self.buffer_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.buffer_btn.clicked.connect(lambda: self.trigger_identify(mode="buffer"))

        self.quick_ai_chk = QCheckBox("Remove Voice && SFX (AI)")
        self.quick_ai_chk.setChecked(bool(self.settings.get("use_ai_separation", False)))
        self.quick_ai_chk.stateChanged.connect(self._on_quick_ai_toggled)

        ai_help = HelpBadge(
            "Applies to 'Identify Last' only — specifically removes spoken voices and game SFX.\n"
            "Not recommended for normal music; turn ON only when speech or loud SFX covers the song."
        )

        buffer_row.addWidget(self.buffer_btn)
        buffer_row.addWidget(self.quick_ai_chk)
        buffer_row.addWidget(ai_help)
        btn_wrap.addLayout(buffer_row)

        self.ai_warn_banner.setVisible(self.quick_ai_chk.isChecked())

        self.live_status_lbl = QLabel("Ready — click Listen or press your shortcut anytime")
        self.live_status_lbl.setObjectName("Muted")
        self.live_status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.live_status_lbl.setStyleSheet("font-size: 12px;")
        btn_wrap.addWidget(self.live_status_lbl)

        layout.addLayout(btn_wrap)
        layout.addSpacing(8)

        # Song Result Card (clicking anywhere on the card opens the hidden Song Details tab)
        self.current_result_song = None
        self.result_card = ClickableCard()
        self.result_card.clicked.connect(self._open_current_result_details)
        self.result_card.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        card_lay = QHBoxLayout(self.result_card)
        card_lay.setContentsMargins(16, 16, 16, 16)
        card_lay.setSpacing(16)

        self.res_cover = QLabel()
        self.res_cover.setFixedSize(112, 112)
        card_lay.addWidget(self.res_cover, alignment=Qt.AlignmentFlag.AlignTop)

        info_col = QVBoxLayout()
        info_col.setSpacing(5)
        self.res_title = QLabel("")
        self.res_title.setStyleSheet("font-size: 17px; font-weight: 700;")
        self.res_title.setWordWrap(True)

        self.res_artist = QLabel("")
        self.res_artist.setObjectName("Muted")
        self.res_artist.setStyleSheet("font-size: 13px; font-weight: 500;")
        self.res_artist.setWordWrap(True)

        self.res_time = QLabel("")
        self.res_time.setObjectName("Muted")
        self.res_time.setStyleSheet("font-size: 11px;")
        self.res_time.setWordWrap(True)

        for _w in (self.res_cover, self.res_title, self.res_artist, self.res_time):
            _w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self.meta_flow = FlowLayout(margin=0, h_spacing=5, v_spacing=5)
        self.links_bar = ResponsiveLinksBar()

        info_col.addWidget(self.res_title)
        info_col.addWidget(self.res_artist)
        info_col.addWidget(self.res_time)
        info_col.addLayout(self.meta_flow)
        info_col.addSpacing(4)
        info_col.addWidget(self.links_bar)
        card_lay.addLayout(info_col, 1)

        layout.addWidget(self.result_card)
        layout.addStretch(2)

        outer.addStretch(1)
        outer.addWidget(center_col, 10)
        outer.addStretch(1)

        page_lay.addWidget(_make_clean_scroll(scroll_inner, horizontal_off=True))

        self._refresh_identify_header()
        if self.history:
            self.display_song_card(self.history[0])
        else:
            self.result_card.hide()

        return page

    def _refresh_identify_header(self):
        count = len(self.engine.monitored_names)
        has_outputs = count > 0
        self.no_output_banner.setVisible(not has_outputs)
        if not has_outputs:
            self.pulse_btn.clear_waveform()

        hk = self.settings.get("hotkey", "ctrl+alt+s").upper()
        buf_s = int(self.settings.get("buffer_seconds", 12))
        hk_mode = "Listen Live" if self.settings.get("hotkey_mode") == "live" else f"Last {buf_s}s"
        out_txt = (
            "No audio output selected"
            if not has_outputs
            else f"Listening to {count} audio output" + ("" if count == 1 else "s")
        )
        self.sub_status_lbl.setText(f"{out_txt} • Shortcut: {hk} ({hk_mode})")
        if not self.is_processing:
            self.pulse_btn.setText("Listen")
            self.buffer_btn.setText(f"Identify Last {buf_s}s")

    # --- LIBRARY PAGE ---
    def _build_library_page(self):
        page = QWidget()
        page.setObjectName("Page")
        outer = QHBoxLayout(page)
        outer.setContentsMargins(22, 20, 22, 18)

        center_col = QWidget()
        center_col.setObjectName("Page")
        center_col.setMaximumWidth(980)
        layout = QVBoxLayout(center_col)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        top = QHBoxLayout()
        h1 = QLabel("Saved Songs")
        h1.setStyleSheet("font-size: 20px; font-weight: 700;")
        self.lib_count_lbl = QLabel("")
        self.lib_count_lbl.setObjectName("Muted")
        self.lib_count_lbl.setStyleSheet("font-size: 12px; margin-left: 6px;")

        clear_btn = QPushButton("Clear All")
        clear_btn.setObjectName("DangerBtn")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self.clear_history)

        top.addWidget(h1)
        top.addWidget(self.lib_count_lbl)
        top.addStretch()
        top.addWidget(clear_btn)
        layout.addLayout(top)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search by song, artist, album, or genre...")
        self.search_input.textChanged.connect(self.refresh_library)
        layout.addWidget(self.search_input)

        self.lib_container = QWidget()
        self.lib_list_layout = QVBoxLayout(self.lib_container)
        self.lib_list_layout.setContentsMargins(6, 6, 8, 6)
        self.lib_list_layout.setSpacing(8)
        self.lib_list_layout.addStretch()

        scroll = _make_clean_scroll(self.lib_container, horizontal_off=True)
        layout.addWidget(scroll, 1)

        outer.addStretch(1)
        outer.addWidget(center_col, 10)
        outer.addStretch(1)
        return page

    # --- SETTINGS PAGE ---
    def _build_settings_page(self):
        page = QWidget()
        page.setObjectName("Page")
        outer_h = QHBoxLayout(page)
        outer_h.setContentsMargins(22, 18, 22, 16)

        center_col = QWidget()
        center_col.setObjectName("Page")
        center_col.setMaximumWidth(920)
        outer = QVBoxLayout(center_col)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        top = QHBoxLayout()
        h1 = QLabel("Settings")
        h1.setStyleSheet("font-size: 20px; font-weight: 700;")
        self.unsaved_lbl = QLabel("")
        self.unsaved_lbl.setStyleSheet("color: #D97706; font-size: 11px; font-weight: 600;")
        top.addWidget(h1)
        top.addStretch()
        top.addWidget(self.unsaved_lbl)
        outer.addLayout(top)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(12)

        # 1. Look & Window
        app_card = QFrame()
        app_card.setObjectName("Card")
        app_lay = QVBoxLayout(app_card)
        app_lay.setContentsMargins(16, 14, 16, 14)
        app_lay.setSpacing(10)

        sec1 = QLabel("Look & Window")
        sec1.setStyleSheet("font-size: 14px; font-weight: 600;")
        app_lay.addWidget(sec1)

        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("Theme"))
        theme_row.addStretch()
        self.theme_combo = NoWheelComboBox()
        self.theme_combo.addItems(["Match Windows", "Dark", "Light"])
        self.theme_combo.currentIndexChanged.connect(self._mark_unsaved)
        theme_row.addWidget(self.theme_combo)
        app_lay.addLayout(theme_row)

        self.save_win_size_chk = QCheckBox("Remember window size")
        self.save_win_size_chk.stateChanged.connect(self._mark_unsaved)
        app_lay.addWidget(self.save_win_size_chk)
        layout.addWidget(app_card)

        # 2. Startup, Notifications & Shortcuts
        sys_card = QFrame()
        sys_card.setObjectName("Card")
        sys_lay = QVBoxLayout(sys_card)
        sys_lay.setContentsMargins(16, 14, 16, 14)
        sys_lay.setSpacing(10)

        sec2 = QLabel("Startup, Notifications & Shortcuts")
        sec2.setStyleSheet("font-size: 14px; font-weight: 600;")
        sys_lay.addWidget(sec2)

        self.startup_chk = QCheckBox("Start automatically when Windows boots (hidden in tray)")
        self.startup_chk.stateChanged.connect(self._mark_unsaved)
        sys_lay.addWidget(self.startup_chk)

        self.min_tray_chk = QCheckBox("Hide to system tray when minimized")
        self.min_tray_chk.stateChanged.connect(self._mark_unsaved)
        sys_lay.addWidget(self.min_tray_chk)

        self.notif_chk = QCheckBox("Show notification when a song is found")
        self.notif_chk.stateChanged.connect(self._mark_unsaved)
        sys_lay.addWidget(self.notif_chk)

        force_row = QHBoxLayout()
        self.force_overlay_chk = QCheckBox(
            "Force bottom-right popup (shows even when Do Not Disturb is on)"
        )
        self.force_overlay_chk.stateChanged.connect(self._mark_unsaved)
        force_row.addWidget(self.force_overlay_chk)
        force_row.addWidget(
            HelpBadge(
                "Shows SongPulse's own bottom-right popup card so Windows Do Not Disturb / Gaming Mode\n"
                "cannot hide it. Turn off to use standard Windows notifications instead."
            )
        )
        force_row.addStretch()
        sys_lay.addLayout(force_row)

        self.ping_chk = QCheckBox("Play ping sound when a song is found")
        self.ping_chk.stateChanged.connect(self._mark_unsaved)
        sys_lay.addWidget(self.ping_chk)

        self.autocopy_chk = QCheckBox("Auto-copy 'Artist - Title' to clipboard")
        self.autocopy_chk.stateChanged.connect(self._mark_unsaved)
        sys_lay.addWidget(self.autocopy_chk)

        hk_row = QHBoxLayout()
        hk_row.addLayout(
            _label_with_help(
                "Keyboard Shortcut",
                "Global shortcut that works even inside fullscreen games or other apps.",
            )
        )
        hk_row.addStretch()
        self.hk_input = HotkeyRecorderEdit()
        self.hk_input.setFixedWidth(135)
        self.hk_input.textChanged.connect(self._mark_unsaved)
        self.hk_record_btn = QPushButton("Record")
        self.hk_record_btn.setObjectName("PlatformBtn")
        self.hk_record_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hk_record_btn.clicked.connect(self._toggle_hotkey_record)
        self.hk_input.hotkey_recorded.connect(lambda _: self._apply_live_hotkey())
        self.hk_input.editingFinished.connect(self._apply_live_hotkey)
        hk_row.addWidget(self.hk_input)
        hk_row.addWidget(self.hk_record_btn)
        sys_lay.addLayout(hk_row)

        hk_mode_row = QHBoxLayout()
        hk_mode_row.addLayout(
            _label_with_help(
                "When Shortcut Is Pressed",
                "'Use recent audio buffer' scans what you JUST heard immediately.\n"
                "'Listen live' records fresh audio until matched or time limit is reached.",
            )
        )
        hk_mode_row.addStretch()
        self.hk_mode_combo = NoWheelComboBox()
        self.hk_mode_combo.addItems(["Use recent audio buffer", "Listen live (No AI)"])
        self.hk_mode_combo.currentIndexChanged.connect(self._mark_unsaved)
        hk_mode_row.addWidget(self.hk_mode_combo)
        sys_lay.addLayout(hk_mode_row)
        layout.addWidget(sys_card)

        yt_row = QHBoxLayout()
        yt_row.addLayout(
            _label_with_help(
                "yt-dlp Downloader App",
                "Path to your yt-dlp downloader (.exe or .pyw). SongPulse opens it and auto-pastes the song's YouTube link.",
            )
        )
        self.ytdlp_path_input = QLineEdit()
        self.ytdlp_path_input.setPlaceholderText("Not set (will ask on first click)")
        self.ytdlp_path_input.textChanged.connect(self._mark_unsaved)
        yt_browse_btn = QPushButton("Browse")
        yt_browse_btn.setObjectName("PlatformBtn")
        yt_browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        yt_browse_btn.clicked.connect(self._browse_ytdlp_app)
        yt_row.addWidget(self.ytdlp_path_input, 1)
        yt_row.addWidget(yt_browse_btn)
        sys_lay.addLayout(yt_row)

        yt_find_btn = QPushButton("Auto-Find")
        yt_find_btn.setObjectName("PlatformBtn")
        yt_find_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        yt_find_btn.clicked.connect(self._autofind_ytdlp_app)
        yt_row.addWidget(self.ytdlp_path_input, 1)
        yt_row.addWidget(yt_find_btn)
        yt_row.addWidget(yt_browse_btn)

        # 3. Audio & Timing
        aud_card = QFrame()
        aud_card.setObjectName("Card")
        aud_lay = QVBoxLayout(aud_card)
        aud_lay.setContentsMargins(16, 14, 16, 14)
        aud_lay.setSpacing(10)

        sec3 = QLabel("Audio & Timing")
        sec3.setStyleSheet("font-size: 14px; font-weight: 600;")
        aud_lay.addWidget(sec3)

        dev_row = QHBoxLayout()
        dev_row.addLayout(
            _label_with_help(
                "Audio Outputs to Listen To",
                "Pick which speakers/headphones SongPulse listens to.\n"
                "You can check multiple outputs (e.g. direct headphones + FxSound).",
            )
        )
        self.dev_combo = CheckableComboBox()
        self.dev_combo.setMinimumWidth(200)
        self.dev_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.dev_combo.selection_changed.connect(lambda _: self._mark_unsaved())
        ref_btn = QPushButton("Refresh")
        ref_btn.setObjectName("PlatformBtn")
        ref_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ref_btn.clicked.connect(self._refresh_devices_dropdown)
        dev_row.addWidget(self.dev_combo, 1)
        dev_row.addWidget(ref_btn)
        aud_lay.addLayout(dev_row)

        buf_row = QHBoxLayout()
        buf_row.addLayout(
            _label_with_help(
                "Audio History Length",
                "How many seconds of recent audio SongPulse keeps ready in RAM (10–15s is ideal).",
            )
        )
        buf_row.addStretch()
        self.buf_spin = NoWheelSpinBox()
        self.buf_spin.setRange(5, 30)
        self.buf_spin.setSuffix(" sec")
        self.buf_spin.valueChanged.connect(self._mark_unsaved)
        buf_row.addWidget(self.buf_spin)
        aud_lay.addLayout(buf_row)

        live_row = QHBoxLayout()
        live_row.addLayout(
            _label_with_help(
                "Max Live Listen Time",
                "Listen Live keeps checking every few seconds until it finds a match,\n"
                "or automatically gives up after this many seconds.",
            )
        )
        live_row.addStretch()
        self.live_spin = NoWheelSpinBox()
        self.live_spin.setRange(6, 60)
        self.live_spin.setSuffix(" sec")
        self.live_spin.valueChanged.connect(self._mark_unsaved)
        live_row.addWidget(self.live_spin)
        aud_lay.addLayout(live_row)
        layout.addWidget(aud_card)

        # 4. Performance & GPU
        gpu_card = QFrame()
        gpu_card.setObjectName("Card")
        gpu_lay = QVBoxLayout(gpu_card)
        gpu_lay.setContentsMargins(16, 14, 16, 14)
        gpu_lay.setSpacing(10)

        sec4 = QLabel("Performance & GPU")
        sec4.setStyleSheet("font-size: 14px; font-weight: 600;")
        gpu_lay.addWidget(sec4)

        gpu_chk_row = QHBoxLayout()
        self.gpu_auto_cpu_chk = QCheckBox(
            "Switch AI cleanup to CPU while gaming / under high GPU load"
        )
        self.gpu_auto_cpu_chk.stateChanged.connect(self._mark_unsaved)
        gpu_chk_row.addWidget(self.gpu_auto_cpu_chk)
        gpu_chk_row.addWidget(
            HelpBadge(
                "When your graphics card is busy (e.g. in a heavy game), SongPulse runs\n"
                "AI voice/SFX cleanup on your CPU instead so your FPS doesn't drop."
            )
        )
        gpu_chk_row.addStretch()
        gpu_lay.addLayout(gpu_chk_row)

        thresh_row = QHBoxLayout()
        thresh_row.addLayout(
            _label_with_help(
                "GPU Load Limit",
                "If GPU usage is at or above this percentage, AI cleanup uses your CPU.",
            )
        )
        thresh_row.addStretch()
        self.gpu_thresh_spin = NoWheelSpinBox()
        self.gpu_thresh_spin.setRange(10, 100)
        self.gpu_thresh_spin.setSuffix(" %")
        self.gpu_thresh_spin.valueChanged.connect(self._mark_unsaved)
        thresh_row.addWidget(self.gpu_thresh_spin)
        gpu_lay.addLayout(thresh_row)

        self.gpu_live_lbl = QLabel("Checking GPU...")
        self.gpu_live_lbl.setObjectName("Muted")
        self.gpu_live_lbl.setStyleSheet("font-size: 12px;")
        self.gpu_live_lbl.setWordWrap(True)
        gpu_lay.addWidget(self.gpu_live_lbl)

        speed_row = QHBoxLayout()
        self.gpu_speed_lbl = QLabel("AI speed: Run a quick test or scan a song with AI on")
        self.gpu_speed_lbl.setObjectName("Muted")
        self.gpu_speed_lbl.setStyleSheet("font-size: 12px;")
        self.gpu_speed_lbl.setWordWrap(True)
        self.bench_btn = QPushButton("Run Speed Test")
        self.bench_btn.setObjectName("PlatformBtn")
        self.bench_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bench_btn.clicked.connect(self._run_speed_benchmark)
        speed_row.addWidget(self.gpu_speed_lbl, 1)
        speed_row.addWidget(self.bench_btn)
        gpu_lay.addLayout(speed_row)
        layout.addWidget(gpu_card)

        # 5. Updates
        upd_card = QFrame()
        upd_card.setObjectName("Card")
        upd_lay = QVBoxLayout(upd_card)
        upd_lay.setContentsMargins(16, 14, 16, 14)
        upd_lay.setSpacing(8)

        sec5 = QLabel("Updates")
        sec5.setStyleSheet("font-size: 14px; font-weight: 600;")
        upd_lay.addWidget(sec5)

        upd_row = QHBoxLayout()
        self.upd_status_lbl = QLabel(f"Version {APP_VERSION} • GitHub: {GITHUB_REPO}")
        self.upd_status_lbl.setObjectName("Muted")
        self.upd_status_lbl.setStyleSheet("font-size: 12px;")
        self.upd_status_lbl.setWordWrap(True)

        self.upd_btn = QPushButton("Check for Updates")
        self.upd_btn.setObjectName("PlatformBtn")
        self.upd_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.upd_btn.clicked.connect(self._check_for_updates)
        upd_row.addWidget(self.upd_status_lbl, 1)
        upd_row.addWidget(self.upd_btn)
        upd_lay.addLayout(upd_row)
        layout.addWidget(upd_card)

        layout.addStretch()
        scroll = _make_clean_scroll(content, horizontal_off=True)
        outer.addWidget(scroll, 1)

        footer = QHBoxLayout()
        reset_btn = QPushButton("Reset Defaults")
        reset_btn.setObjectName("DangerBtn")
        reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_btn.clicked.connect(self._reset_settings)

        save_btn = QPushButton("Save Settings")
        save_btn.setObjectName("PrimaryBtn")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.clicked.connect(self._save_settings_from_ui)

        self.discard_btn = QPushButton("Discard")
        self.discard_btn.setObjectName("PlatformBtn")
        self.discard_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.discard_btn.clicked.connect(self._discard_unsaved_settings)
        self.discard_btn.hide()

        footer.addWidget(reset_btn)
        footer.addStretch()
        footer.addWidget(save_btn)
        outer.addLayout(footer)

        outer_h.addStretch(1)
        outer_h.addWidget(center_col, 10)
        outer_h.addStretch(1)

        self._populate_settings_ui()
        return page

    def _populate_settings_ui(self):
        self._suppress_unsaved = True
        try:
            mode_map = {"auto": 0, "dark": 1, "light": 2}
            self.theme_combo.setCurrentIndex(
                mode_map.get(self.settings.get("theme", "auto"), 0)
            )
            self.save_win_size_chk.setChecked(
                bool(self.settings.get("save_window_size", True))
            )
            self.startup_chk.setChecked(is_startup_enabled())
            self.min_tray_chk.setChecked(
                bool(self.settings.get("minimize_to_tray", True))
            )
            self.notif_chk.setChecked(
                bool(self.settings.get("show_notifications", True))
            )
            self.force_overlay_chk.setChecked(
                bool(self.settings.get("force_overlay_notification", True))
            )
            self.ping_chk.setChecked(bool(self.settings.get("play_ping_sound", True)))
            self.autocopy_chk.setChecked(bool(self.settings.get("auto_copy", False)))
            self.hk_input.setText(self.settings.get("hotkey", "ctrl+alt+s"))
            self.hk_mode_combo.setCurrentIndex(
                1 if self.settings.get("hotkey_mode") == "live" else 0
            )
            self.buf_spin.setValue(int(self.settings.get("buffer_seconds", 12)))
            self.live_spin.setValue(int(self.settings.get("live_max_seconds", 20)))
            self.gpu_auto_cpu_chk.setChecked(
                bool(self.settings.get("gpu_auto_cpu", True))
            )
            self.gpu_thresh_spin.setValue(int(self.settings.get("gpu_threshold", 65)))

            all_devs = self.engine.all_speaker_names or list_speakers_safe()
            self.dev_combo.set_items(
                all_devs,
                self.settings.get("target_devices", []),
                monitor_all=bool(self.settings.get("monitor_all_devices", True)),
            )
            self.has_unsaved_settings = False
            self.unsaved_lbl.setText("")
            self.ytdlp_path_input.setText(self.settings.get("ytdlp_app_path", ""))
        finally:
            self._suppress_unsaved = False
        self._update_gpu_indicator()

    def _mark_unsaved(self, *_args):
        if getattr(self, "_suppress_unsaved", False):
            return
        self.has_unsaved_settings = True
        self.unsaved_lbl.setText("Previewing unsaved changes")
        if hasattr(self, "discard_btn"):
            self.discard_btn.show()
        self._apply_live_settings()

    def _apply_live_settings(self):
        """Applies Settings UI values immediately in memory without writing settings.json yet."""
        old_devices = list(self.settings.get("target_devices", []))
        old_all = bool(self.settings.get("monitor_all_devices", True))
        old_buf = int(self.settings.get("buffer_seconds", 12))

        themes = ["auto", "dark", "light"]
        self.settings["theme"] = themes[self.theme_combo.currentIndex()]
        self.settings["save_window_size"] = self.save_win_size_chk.isChecked()
        self.settings["minimize_to_tray"] = self.min_tray_chk.isChecked()
        self.settings["show_notifications"] = self.notif_chk.isChecked()
        self.settings["force_overlay_notification"] = self.force_overlay_chk.isChecked()
        self.settings["play_ping_sound"] = self.ping_chk.isChecked()
        self.settings["auto_copy"] = self.autocopy_chk.isChecked()
        self.settings["hotkey_mode"] = (
            "live" if self.hk_mode_combo.currentIndex() == 1 else "buffer"
        )
        self.settings["buffer_seconds"] = self.buf_spin.value()
        self.settings["live_max_seconds"] = self.live_spin.value()
        self.settings["ytdlp_app_path"] = self.ytdlp_path_input.text().strip()
        self.settings["gpu_auto_cpu"] = self.gpu_auto_cpu_chk.isChecked()
        self.settings["gpu_threshold"] = self.gpu_thresh_spin.value()

        checked_devs = self.dev_combo.checked_items()
        all_devs = self.dev_combo.all_items()
        if all_devs and len(checked_devs) == len(all_devs):
            self.settings["monitor_all_devices"] = True
            self.settings["target_devices"] = []
        else:
            self.settings["monitor_all_devices"] = False
            self.settings["target_devices"] = checked_devs

        # Instant visual & telemetry updates
        self.apply_theme()
        self._update_gpu_indicator()
        self._refresh_identify_header()

        # Debounce audio engine restarts so rapid clicks / spinbox stepping stay smooth
        if (
            self.settings["target_devices"] != old_devices
            or self.settings["monitor_all_devices"] != old_all
            or self.settings["buffer_seconds"] != old_buf
        ):
            self._audio_reconfig_timer.start(250)

    def _apply_audio_reconfig_now(self):
        self.engine.reconfigure(
            self.settings.get("target_devices", []),
            self.settings.get("monitor_all_devices", True),
            self.settings.get("buffer_seconds", 12),
        )
        self._refresh_identify_header()

    def _apply_live_hotkey(self):
        if getattr(self, "_suppress_unsaved", False):
            return
        new_hk = self.hk_input.text().strip().lower()
        cur_hk = self.settings.get("hotkey", "ctrl+alt+s")
        if not new_hk or new_hk == cur_hk:
            return
        if self._register_hotkey(new_hk):
            self.settings["hotkey"] = new_hk
            self._refresh_identify_header()

    def _save_settings_from_ui(self):
        self._audio_reconfig_timer.stop()
        self._apply_live_settings()

        # Validate & commit shortcut
        old_saved_hk = self._saved_settings_snapshot.get("hotkey", "ctrl+alt+s")
        new_hk = self.hk_input.text().strip().lower()
        if new_hk and new_hk != self.settings.get("hotkey"):
            if self._register_hotkey(new_hk):
                self.settings["hotkey"] = new_hk
            else:
                QMessageBox.warning(
                    self,
                    "Invalid Shortcut",
                    f"Could not register '{new_hk}'. Keeping '{old_saved_hk}'.",
                )
                self._register_hotkey(old_saved_hk)
                self.settings["hotkey"] = old_saved_hk
                self._suppress_unsaved = True
                self.hk_input.setText(old_saved_hk)
                self._suppress_unsaved = False

        if self.settings["save_window_size"] and not self.isMaximized():
            self.settings["window_width"] = self.width()
            self.settings["window_height"] = self.height()

        set_startup_enabled(self.startup_chk.isChecked())
        save_settings(self.settings)

        # Ensure audio engine matches saved settings immediately
        self._apply_audio_reconfig_now()

        # Refresh saved snapshots
        self._saved_settings_snapshot = copy.deepcopy(self.settings)
        self._saved_startup_snapshot = self.startup_chk.isChecked()
        self.has_unsaved_settings = False
        self.discard_btn.hide()
        self._refresh_identify_header()
        self.unsaved_lbl.setText("Saved!")
        QTimer.singleShot(
            2000,
            lambda: self.unsaved_lbl.setText("") if not self.has_unsaved_settings else None,
        )

    def _discard_unsaved_settings(self):
        """Reverts every live-previewed change back to the last saved state."""
        self._audio_reconfig_timer.stop()
        prev_devices = list(self.settings.get("target_devices", []))
        prev_all = bool(self.settings.get("monitor_all_devices", True))
        prev_buf = int(self.settings.get("buffer_seconds", 12))

        self.settings = copy.deepcopy(self._saved_settings_snapshot)
        self._register_hotkey(self.settings.get("hotkey", "ctrl+alt+s"))
        set_startup_enabled(self._saved_startup_snapshot)

        if (
            self.settings.get("target_devices", []) != prev_devices
            or bool(self.settings.get("monitor_all_devices", True)) != prev_all
            or int(self.settings.get("buffer_seconds", 12)) != prev_buf
        ):
            self._apply_audio_reconfig_now()

        self._populate_settings_ui()
        self.apply_theme()
        self._refresh_identify_header()
        self.discard_btn.hide()
        self.unsaved_lbl.setText("Changes reverted")
        QTimer.singleShot(
            2000,
            lambda: self.unsaved_lbl.setText("") if not self.has_unsaved_settings else None,
        )

    def _reset_settings(self):
        reply = QMessageBox.question(
            self,
            "Reset Settings",
            "Reset all settings back to default values?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._audio_reconfig_timer.stop()
        self.settings = DEFAULT_SETTINGS.copy()
        save_settings(self.settings)
        self._saved_settings_snapshot = copy.deepcopy(self.settings)
        self._register_hotkey(self.settings["hotkey"])
        self._apply_audio_reconfig_now()
        self.quick_ai_chk.setChecked(self.settings["use_ai_separation"])
        self.is_pinned = False
        set_window_always_on_top(int(self.winId()), False)
        self._update_pin_button_ui()
        self._populate_settings_ui()
        self.apply_theme()
        self._refresh_identify_header()
        self.discard_btn.hide()
        self.unsaved_lbl.setText("Defaults restored")
        QTimer.singleShot(
            2000,
            lambda: self.unsaved_lbl.setText("") if not self.has_unsaved_settings else None,
        )

    def _refresh_devices_dropdown(self):
        all_devs = list_speakers_safe()
        self.engine.all_speaker_names = all_devs
        current_checked = self.dev_combo.checked_items()
        self.dev_combo.set_items(all_devs, current_checked, monitor_all=False)
        self._mark_unsaved()

    def _toggle_hotkey_record(self):
        if self.hk_input._recording:
            self.hk_input.stop_recording()
            self.hk_record_btn.setText("Record")
        else:
            self.hk_input.start_recording()
            self.hk_record_btn.setText("Press keys...")

    def _browse_ytdlp_app(self):
        picked, _ = QFileDialog.getOpenFileName(
            self,
            "Select yt-dlp Downloader App",
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "App or Script (*.exe *.pyw *.py *.bat);;All Files (*.*)",
        )
        if picked:
            self.ytdlp_path_input.setText(picked)

    def _autofind_ytdlp_app(self):
        found = find_default_ytdlp_app()
        if found:
            self.ytdlp_path_input.setText(found)
        else:
            QMessageBox.information(
                self,
                "Not Found Automatically",
                "Could not auto-detect yt-dlp Downloader. Click Browse (opens AppData directly) to select it.",
            )

    def _on_ytdlp_path_picked(self, path: str):
        self.settings["ytdlp_app_path"] = path
        save_settings(self.settings)
        if hasattr(self, "ytdlp_path_input"):
            self._suppress_unsaved = True
            self.ytdlp_path_input.setText(path)
            self._suppress_unsaved = False

    # --- LIVE WAVEFORM & PERIODIC TICK ---
    def _tick_waveform(self):
        if not self.isVisible() or self.pages.currentIndex() != 0:
            return
        has_out = len(self.engine.monitored_names) > 0
        bands, rms = self.engine.get_live_waveform(28) if has_out else (None, 0.0)
        self.pulse_btn.step_animation(bands, rms, has_outputs=has_out)

    def _on_periodic_tick(self):
        if self.settings.get("theme", "auto") == "auto":
            self.apply_theme()
        if self.isVisible() and self.pages.currentIndex() == 2:
            self._update_gpu_indicator()

    # --- GPU SPEED INDICATOR & BENCHMARK ---
    def _update_gpu_indicator(self):
        provider = "GPU Ready (CUDA)" if self.model_mgr.gpu_available else "CPU Only"
        info = get_nvidia_gpu_info()
        if info:
            thresh = self.gpu_thresh_spin.value() if hasattr(self, "gpu_thresh_spin") else 65
            state = (
                "Will use CPU (GPU busy)"
                if self.gpu_auto_cpu_chk.isChecked() and info["util"] >= thresh
                else "Will use GPU"
            )
            self.gpu_live_lbl.setText(
                f"{provider} • {info['name']} • Load: {info['util']}% "
                f"• VRAM: {info['mem_used']}/{info['mem_total']} MB • {state}"
            )
        else:
            self.gpu_live_lbl.setText(f"{provider} • Live GPU load unavailable")

        parts = []
        if self.model_mgr.last_gpu_time is not None:
            g = self.model_mgr.last_gpu_time
            rating = "Fast" if g < 2.5 else ("Medium" if g < 5.0 else "Slow")
            parts.append(f"GPU: {g:.2f}s ({rating})")
        if self.model_mgr.last_cpu_time is not None:
            c = self.model_mgr.last_cpu_time
            parts.append(f"CPU: {c:.2f}s")
        if parts:
            self.gpu_speed_lbl.setText("AI speed: " + "  |  ".join(parts))

    def _run_speed_benchmark(self):
        if self.bench_worker and self.bench_worker.isRunning():
            return
        self.bench_btn.setEnabled(False)
        self.bench_btn.setText("Testing...")
        self.bench_worker = BenchmarkWorker(self.model_mgr)
        self.bench_worker.progress.connect(self.gpu_speed_lbl.setText)
        self.bench_worker.finished_bench.connect(self._on_bench_done)
        self.bench_worker.error.connect(self._on_bench_error)
        self.bench_worker.start()

    def _on_bench_done(self, gpu_sec, cpu_sec):
        self.bench_btn.setEnabled(True)
        self.bench_btn.setText("Run Speed Test")
        self._update_gpu_indicator()

    def _on_bench_error(self, err):
        self.bench_btn.setEnabled(True)
        self.bench_btn.setText("Run Speed Test")
        self.gpu_speed_lbl.setText(f"Speed test failed: {err}")

    # --- GITHUB UPDATER ---
    def _check_for_updates(self):
        if self.update_worker and self.update_worker.isRunning():
            return
        self.upd_btn.setEnabled(False)
        self.upd_btn.setText("Checking...")
        self.upd_status_lbl.setText(f"Checking GitHub ({GITHUB_REPO})...")
        self.update_worker = UpdateCheckWorker()
        self.update_worker.result.connect(self._on_update_result)
        self.update_worker.error.connect(self._on_update_error)
        self.update_worker.start()

    def _on_update_result(self, info):
        self.upd_btn.setEnabled(True)
        self.upd_btn.setText("Check for Updates")
        latest = info["latest_version"]
        if info["is_newer"]:
            self.upd_status_lbl.setText(f"Update available: v{latest} (installed: v{APP_VERSION})")
            msg = f"SongPulse v{latest} is available!\n\n"
            if info["notes"]:
                msg += info["notes"][:400] + "\n\n"
            msg += "Open the download link in your browser?"
            reply = QMessageBox.question(
                self,
                "Update Available",
                msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                webbrowser.open(info["download_url"])
        else:
            self.upd_status_lbl.setText(f"Up to date (v{APP_VERSION} • latest: v{latest})")

    def _on_update_error(self, err):
        self.upd_btn.setEnabled(True)
        self.upd_btn.setText("Check for Updates")
        self.upd_status_lbl.setText(err)

    # --- SYSTEM TRAY & WINDOW STATE ---
    def _build_tray(self):
        self.tray = QSystemTrayIcon(self.app_icon, self)
        self.tray.setToolTip(f"{APP_NAME} v{APP_VERSION} — Listening in background")

        menu = QMenu()
        act_open = QAction("Open SongPulse", self)
        act_open.triggered.connect(self.show_and_focus)

        act_buf = QAction("Identify Last Audio", self)
        act_buf.triggered.connect(lambda: self.trigger_identify(mode="buffer"))

        act_live = QAction("Listen Live (No AI)", self)
        act_live.triggered.connect(lambda: self.trigger_identify(mode="live"))

        act_quit = QAction("Quit", self)
        act_quit.triggered.connect(self.quit_app)

        menu.addAction(act_open)
        menu.addAction(act_buf)
        menu.addAction(act_live)
        menu.addSeparator()
        menu.addAction(act_quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_and_focus()

    def show_and_focus(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()
        set_windows_titlebar_theme(int(self.winId()), bool(self.last_dark_state))
        if self.is_pinned:
            set_window_always_on_top(int(self.winId()), True)

    def _persist_window_size(self):
        if (
            self._saved_settings_snapshot.get("save_window_size", True)
            and not self.isMinimized()
            and not self.isMaximized()
        ):
            self._saved_settings_snapshot["window_width"] = self.width()
            self._saved_settings_snapshot["window_height"] = self.height()
            save_settings(self._saved_settings_snapshot)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.WindowStateChange:
            if self.isMinimized() and self.settings.get("minimize_to_tray", True):
                QTimer.singleShot(0, self.hide)
                if not self.tray_hint_shown:
                    self.tray_hint_shown = True
                    self.hud.show_message(
                        "SongPulse minimized to system tray",
                        dark=bool(self.last_dark_state),
                        auto_hide_ms=2200,
                    )
        super().changeEvent(event)

    def closeEvent(self, event):
        event.accept()
        self.quit_app(force=True)

    def quit_app(self, force=True):
        self.cancel_evt.set()
        self.detail_page.stop_audio()
        self._persist_window_size()
        self.engine.stop()
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        self.hud.hide()
        self.song_popup.hide()
        self.tray.hide()
        QApplication.quit()
        if force:
            os._exit(0)

    # --- THEME ---
    def apply_theme(self):
        mode = self.settings.get("theme", "auto")
        dark = is_windows_dark_mode() if mode == "auto" else (mode == "dark")
        if dark != self.last_dark_state:
            self.last_dark_state = dark
            app = QApplication.instance()
            app.setPalette(build_palette(dark))
            app.setStyleSheet(build_stylesheet(dark, self.chevrons))
            set_windows_titlebar_theme(int(self.winId()), dark)
            self.pulse_btn.set_dark(dark)
            self.detail_page.set_theme_dark(dark)
            self._refresh_theme_icons()
            if self.pages.currentIndex() == 1:
                self.refresh_library()

    # --- NAVIGATION WITH UNSAVED SETTINGS GUARD ---
    def _set_active_nav(self, idx):
        self.pages.setCurrentIndex(idx)
        for i, btn in enumerate(self.nav_buttons):
            btn.setProperty("active", i == idx)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self._refresh_theme_icons()

    def switch_page(self, idx):
        current = self.pages.currentIndex()
        if idx == current:
            return

        if current == 2 and self.has_unsaved_settings:
            box = QMessageBox(self)
            box.setWindowTitle("Unsaved Settings")
            box.setText("You have unsaved changes in Settings.")
            box.setInformativeText("Do you want to save them before leaving?")
            btn_save = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
            btn_discard = box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
            btn_cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(btn_save)
            box.exec()

            clicked = box.clickedButton()
            if clicked == btn_cancel:
                return
            if clicked == btn_save:
                self._save_settings_from_ui()
            elif clicked == btn_discard:
                self._discard_unsaved_settings()

        self.detail_page.stop_audio()

        self._set_active_nav(idx)
        if idx == 1:
            self.refresh_library()
        elif idx == 2:
            self._update_gpu_indicator()

    # --- RESULT CARD ---
    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _open_current_result_details(self):
     if self.current_result_song:
         self._open_song_details(self.current_result_song)

    def display_song_card(self, song):
        self.current_result_song = song
        self.result_card.show()
        self.res_cover.setPixmap(rounded_pixmap(song.get("cover_path", ""), 112, 12))
        self.res_title.setText(song.get("title", "Unknown Title"))
        self.res_artist.setText(song.get("artist", "Unknown Artist"))

        ts = song.get("timestamp", "")
        method = song.get("method", "")
        time_line = f"Found on {ts}" if ts else ""
        if method:
            time_line = f"{time_line}  •  {method}" if time_line else method
        self.res_time.setText(time_line)

        self._clear_layout(self.meta_flow)
        for label_name, val in [
            ("Album", song.get("album")),
            ("Released", song.get("released")),
            ("Genre", song.get("genre")),
            ("Label", song.get("label")),
        ]:
            if val:
                chip = QLabel(f"{label_name}: {val}")
                chip.setObjectName("MetaChip")
                chip.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                self.meta_flow.addWidget(chip)

        link_items = []
        for pname, icon_key, url in build_song_platform_links(song):
            icon = self.p_icons.get(icon_key, self.p_icons["web"])
            link_items.append((pname, icon, lambda _b=None, u=url: webbrowser.open(u)))

        copy_url = get_song_link(song)
        link_items.append(
            (
                "Copy Link",
                self.p_icons["copy"],
                lambda b=None, txt=copy_url: self._copy_to_clipboard(txt, b),
            )
        )
        link_items.append(
            (
                "Full Details",
                getattr(self, "details_ui_icon", self.p_icons["web"]),
                lambda _b=None, s=song: self._open_song_details(s),
            )
        )
        self.links_bar.set_items(link_items)

    def _copy_to_clipboard(self, text, btn=None):
        QApplication.clipboard().setText(text)
        if btn:
            orig = btn.text()
            btn.setText("Copied!")
            QTimer.singleShot(1400, lambda: btn.setText(orig))

    # --- LIBRARY TAB ---
    def refresh_library(self):
        query = self.search_input.text().strip().lower()
        filtered = [
            s
            for s in self.history
            if not query
            or query in s.get("title", "").lower()
            or query in s.get("artist", "").lower()
            or query in s.get("album", "").lower()
            or query in s.get("genre", "").lower()
        ]
        self.lib_count_lbl.setText(f"({len(filtered)} songs)")

        while self.lib_list_layout.count() > 1:
            item = self.lib_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not filtered:
            hk = self.settings.get("hotkey", "ctrl+alt+s").upper()
            empty = QLabel(f"No saved songs yet. Press {hk} when you hear a song!")
            empty.setObjectName("Muted")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("padding: 40px; font-size: 13px;")
            self.lib_list_layout.insertWidget(0, empty)
            return

        details_icon = getattr(self, "details_ui_icon", create_ui_icon("details", "#D4D4D8", 13))
        for idx, song in enumerate(filtered):
            card = HistoryRowCard(
                song,
                self.p_icons,
                details_icon,
                on_details=self._open_song_details,
                on_delete=self.delete_history_item,
            )
            self.lib_list_layout.insertWidget(idx, card)

    def _open_song_details(self, song):
        self.detail_page.load_song(song, dark=bool(self.last_dark_state))
        self.pages.setCurrentIndex(3)
        for i, btn in enumerate(self.nav_buttons):
            btn.setProperty("active", i == 1)  # Keep Library tab highlighted
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self._refresh_theme_icons()

    def _on_song_enriched(self, song_id: str, updates: dict):
        for item in self.history:
            if item.get("id") == song_id:
                item.update(updates)
                save_history(self.history)
                break

    def delete_history_item(self, song_id):
        self.history = [s for s in self.history if s.get("id") != song_id]
        save_history(self.history)
        self.refresh_library()

    def clear_history(self):
        if not self.history:
            return
        reply = QMessageBox.question(
            self,
            "Clear Saved Songs",
            "Delete all saved songs from your library?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.history = []
            save_history(self.history)
            self.refresh_library()

    # --- IDENTIFICATION WORKFLOW ---
    def _on_model_loaded(self, cuda_ok):
        mode = "GPU" if cuda_ok else "CPU"
        self.model_dot.setStyleSheet("color: #22C55E; font-size: 10px;")
        self.model_status_lbl.setText(f"AI Ready ({mode})")
        self.ai_foot_widget.setToolTip(f"AI Cleanup Ready ({mode})")
        self._update_gpu_indicator()

    def _on_model_failed(self, err):
        self.model_dot.setStyleSheet("color: #EF4444; font-size: 10px;")
        self.model_status_lbl.setText("AI unavailable")
        self.ai_foot_widget.setToolTip(f"AI Cleanup unavailable:\n{err}")
        try:
            from .config import DATA_DIR
            with open(os.path.join(DATA_DIR, "ai_error.log"), "w", encoding="utf-8") as f:
                f.write(str(err))
        except Exception:
            pass

    def _on_quick_ai_toggled(self, state):
        enabled = state == Qt.CheckState.Checked.value
        self.settings["use_ai_separation"] = enabled
        self.ai_warn_banner.setVisible(enabled)
        save_settings(self.settings)

    def _register_hotkey(self, hk):
        if self.registered_hotkey:
            try:
                keyboard.remove_hotkey(self.registered_hotkey)
            except Exception:
                pass
        try:
            self.registered_hotkey = keyboard.add_hotkey(
                hk, lambda: self.hotkey_signal.emit()
            )
            return True
        except Exception:
            self.registered_hotkey = None
            return False

    def _on_hotkey_triggered(self):
        mode = self.settings.get("hotkey_mode", "live")
        self.trigger_identify(mode=mode, from_hotkey=True)

    def trigger_identify(self, mode="live", from_hotkey=False):
        if self.is_processing:
            return
        if not self.engine.monitored_names:
            msg = "No audio output selected. Pick an output in Settings."
            self.live_status_lbl.setText(msg)
            self.hud.show_message(
                f"SongPulse • {msg}",
                dark=bool(self.last_dark_state),
                dot_color="#EF4444",
                auto_hide_ms=3200,
            )
            return

        self.is_processing = True
        self.cancel_evt.clear()
        self.pulse_btn.setEnabled(False)
        self.pulse_btn.set_scanning(True)
        self.pulse_btn.setText("Listening..." if mode == "live" else "Scanning...")
        self.buffer_btn.setEnabled(False)
        self.quick_ai_chk.setEnabled(False)
        self.cancel_btn.show()

        if from_hotkey or not self.isVisible():
            label = "SongPulse • Listening live..." if mode == "live" else "SongPulse • Scanning audio..."
            self.hud.show_message(label, dark=bool(self.last_dark_state), dot_color="#3B82F6")

        self.worker = IdentifyWorker(
            self.engine, self.model_mgr, self.settings, mode=mode, cancel_evt=self.cancel_evt
        )
        self.worker.status_update.connect(self._on_worker_status)
        self.worker.song_found.connect(self._on_song_found)
        self.worker.no_match.connect(self._on_no_match)
        self.worker.cancelled.connect(self._on_cancelled)
        self.worker.error.connect(self._on_error)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()

    def cancel_identify(self):
        if not self.is_processing:
            return
        self.cancel_evt.set()
        self.cancel_btn.setEnabled(False)
        self.live_status_lbl.setText("Cancelling...")

    def _on_worker_status(self, text):
        self.live_status_lbl.setText(text)
        if self.hud.isVisible():
            self.hud.show_message(text, dark=bool(self.last_dark_state), dot_color="#3B82F6")

    def _on_song_found(self, song):
        self.hud.hide()
        self.history.insert(0, song)
        save_history(self.history)
        self.display_song_card(song)
        self.live_status_lbl.setText(f"Found: {song['artist']} — {song['title']}")

        if self.settings.get("auto_copy", False):
            QApplication.clipboard().setText(f"{song['artist']} - {song['title']}")

        if self.settings.get("play_ping_sound", True):
            play_ping_sound()

        if self.settings.get("show_notifications", True):
            if self.settings.get("force_overlay_notification", True):
                self.song_popup.show_song(song, dark=bool(self.last_dark_state))
            else:
                sub = f"{song['artist']} — {song['title']}"
                if song.get("released"):
                    sub += f" ({song['released']})"
                icon_path = song.get("cover_path") or ICON_CACHE_FILE
                self._toast("Song Found!", sub, song.get("shazam_url"), icon_path=icon_path)

        if self.pages.currentIndex() == 1:
            self.refresh_library()
        if self.pages.currentIndex() == 2:
            self._update_gpu_indicator()

    def _on_no_match(self):
        self.live_status_lbl.setText("No match found. Try again during a clearer part of the song.")
        if self.hud.isVisible() or not self.isVisible():
            self.hud.show_message(
                "SongPulse • No match found",
                dark=bool(self.last_dark_state),
                dot_color="#F59E0B",
                auto_hide_ms=2600,
            )

    def _on_cancelled(self):
        self.live_status_lbl.setText("Cancelled.")
        self.hud.hide()

    def _on_error(self, msg):
        self.live_status_lbl.setText(f"Error: {msg}")
        if self.hud.isVisible() or not self.isVisible():
            self.hud.show_message(
                f"SongPulse • {msg}",
                dark=bool(self.last_dark_state),
                dot_color="#EF4444",
                auto_hide_ms=3000,
            )

    def _on_worker_finished(self):
        self.is_processing = False
        self.pulse_btn.set_scanning(False)
        self.pulse_btn.setEnabled(True)
        self.cancel_btn.hide()
        self.cancel_btn.setEnabled(True)
        self.buffer_btn.setEnabled(True)
        self.quick_ai_chk.setEnabled(True)
        self._refresh_identify_header()

    def _toast(self, title, msg, launch_url=None, icon_path=None):
        try:
            kwargs = {
                "app_id": APP_NAME,
                "title": title,
                "msg": msg,
                "duration": "long",  # ~12-25s Windows toast fallback
            }
            if icon_path and os.path.exists(icon_path):
                kwargs["icon"] = os.path.abspath(icon_path)
            toast = Notification(**kwargs)
            if launch_url:
                toast.add_actions(label="Open in Browser", launch=launch_url)
            toast.show()
        except Exception:
            pass