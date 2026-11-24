import copy
import hashlib
import os
import shutil
import threading
import time
import webbrowser

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
from PyQt6.QtGui import QAction, QFont, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .audio import WAVEFORM_BARS, AudioBufferEngine
from .config import (
    AI_MODEL_CATALOG,
    AI_MODEL_EXTENSIONS,
    APP_NAME,
    APP_VERSION,
    DEFAULT_SETTINGS,
    GITHUB_REPO,
    ICON_CACHE_FILE,
    ICON_COLOR_PRESETS,
    MODELS_DIR,
    PREFERRED_INFO_SITES,
    SAVED_CLIP_WAV,
    TEXT_SIZE_PRESETS,
    THEMES_DIR,
    UVR_MODEL_NAME,
    build_site_url,
    ensure_ping_sound,
    find_default_ytdlp_app,
    format_bytes,
    get_downloaded_ai_models,
    get_model_file_path,
    get_nvidia_gpu_info,
    get_saved_theme_colors,
    get_storage_breakdown,
    get_text_scale,
    is_ai_engine_installed,
    is_model_downloaded,
    is_model_ready,
    is_startup_enabled,
    is_whisper_model_id,
    is_windows_dark_mode,
    list_speakers_safe,
    load_history,
    load_playlists,
    load_settings,
    open_external_url,
    play_ping_sound,
    save_history,
    save_playlists,
    save_settings,
    set_startup_enabled,
    set_window_always_on_top,
    set_windows_titlebar_theme,
)
from .detail_view import (
    SongDetailPage,
    _force_focus_window,
    launch_ytdlp_downloader,
    show_download_complete_popup,
)
from .dialogs import (
    CustomThemeDialog,
    DownloadProgressDialog,
    GithubPresetsDialog,
    StorageCleanupDialog,
    prompt_install_or_locate_ytdlp,
)
from .theme import (
    apply_font_scale_to_widget_tree,
    build_palette,
    build_stylesheet,
    create_app_icon,
    create_app_pixmap,
    create_platform_icon,
    create_ui_icon,
    create_ytdlp_icon,
    ensure_chevron_icons,
    export_multi_res_ico,
    resolve_icon_colors,
    rounded_pixmap,
)
from .themes_manager import (
    ensure_theme_template_and_assets,
    list_all_theme_choices,
    load_theme_config,
)
from .widgets import (
    AudioOutputListWidget,
    CheckableComboBox,
    ClickableCard,
    FlowLayout,
    FlowWidget,
    GlassSidebarFrame,
    HelpBadge,
    HistoryRowCard,
    HotkeyRecorderEdit,
    MiniIdentifyWindow,
    NoWheelComboBox,
    NoWheelDoubleSpinBox,
    NoWheelSlider,
    NoWheelSpinBox,
    PulseIdentifyWidget,
    ResponsiveLinksBar,
    ScrollThroughMenu,
    SongResultPopup,
    SubtleStatusHUD,
    ThemedBackgroundWidget,
    get_song_link,
)
from .workers import (
    AiRuntimeDownloadWorker,
    AppUpdateWorker,
    BenchmarkWorker,
    IdentifyWorker,
    ModelDownloadWorker,
    ModelLoaderThread,
    ModelManager,
    UpdateCheckWorker,
    build_song_platform_links,
    resolve_youtube_watch_url,
    resolve_ytdlp_download_url,
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
    gpu_info_signal = pyqtSignal(object)
    ytdlp_download_finished = pyqtSignal(str, int, str)

    SIDEBAR_EXPANDED_W = 204
    SIDEBAR_COLLAPSED_W = 62

    def __init__(self, app_icon: QIcon):
        super().__init__()
        self.app_icon = app_icon
        self.chevrons = ensure_chevron_icons()
        ensure_ping_sound()
        ensure_theme_template_and_assets()

        self.settings = load_settings()
        self._saved_settings_snapshot = copy.deepcopy(self.settings)
        self._saved_startup_snapshot = is_startup_enabled()
        self._saved_outputs_snapshot = ([], [])

        self._audio_reconfig_timer = QTimer(self)
        self._audio_reconfig_timer.setSingleShot(True)
        self._audio_reconfig_timer.timeout.connect(self._apply_audio_reconfig_now)

        self.history = load_history()
        self.playlists = load_playlists()
        self.active_playlist_key = "all"  # "all", "liked", or playlist_id
        self.selected_song_ids: list[str] = []
        self._lib_cards = []
        self._ai_section_expanded = False

        self.model_mgr = ModelManager(
            initial_model=self.settings.get("ai_model", UVR_MODEL_NAME)
        )
        self.worker = None
        self.cancel_evt = threading.Event()
        self.bench_worker = None
        self.update_worker = None
        self.model_loader = None
        self.is_processing = False
        self.has_unsaved_settings = False
        self.tray_hint_shown = False
        self.last_dark_state = None
        self._last_theme_sig = None
        self._last_wf_sig = None
        self._last_icon_colors = None
        self._current_theme_cfg = None
        self._preview_theme_override = None
        self._cached_gpu_info = None
        self._gpu_poll_busy = False
        self.registered_hotkey = None

        self.sidebar_collapsed = False
        self.sidebar_user_collapsed = False
        self.is_pinned = bool(self.settings.get("always_on_top", False))

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(self.app_icon)
        self.setMinimumSize(464, 480)
        if self.settings.get("save_window_size", True):
            self.resize(
                int(self.settings.get("window_width", 920)),
                int(self.settings.get("window_height", 660)),
            )
        else:
            self.resize(920, 660)

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
        self.song_popup.clicked.connect(self._on_notification_clicked)

        self.engine = AudioBufferEngine(
            target_devices=self.settings.get("target_devices", []),
            monitor_all=self.settings.get("monitor_all_devices", True),
            buffer_seconds=self.settings.get("buffer_seconds", 12),
            enable_buffer=self.settings.get("enable_audio_buffer", True),
        )
        self.engine.start()
        if not self.settings.get("remembered_devices"):
            init_rem = list(self.engine.all_speaker_names or [])
            if init_rem:
                self.settings["remembered_devices"] = init_rem
                self._saved_settings_snapshot["remembered_devices"] = list(init_rem)
                save_settings(self.settings)

        self.sidebar_anim = QVariantAnimation(self)
        self.sidebar_anim.setDuration(200)
        self.sidebar_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.sidebar_anim.valueChanged.connect(self._on_sidebar_anim_step)
        self.sidebar_anim.finished.connect(self._on_sidebar_anim_finished)

        self._build_ui()
        self.mini_window = MiniIdentifyWindow(self.app_icon)
        self.mini_window.listen_clicked.connect(lambda: self.trigger_identify(mode="live"))
        self.mini_window.buffer_clicked.connect(lambda: self.trigger_identify(mode="buffer"))
        self.mini_window.cancel_clicked.connect(self.cancel_identify)
        self.mini_window.ai_toggled.connect(self._on_mini_ai_toggled)
        self.mini_window.restore_requested.connect(self._exit_mini_mode)
        self.mini_window.set_ai_checked_silent(self.quick_ai_chk.isChecked())
        self._build_tray()
        self.apply_theme(force=True)
        self._apply_sidebar_state(self.width() < 760, animate=False)
        self._update_pin_button_ui()
        self._refresh_identify_header()

        self.hotkey_signal.connect(self._on_hotkey_triggered)
        self.gpu_info_signal.connect(self._on_gpu_info_ready)
        self.ytdlp_download_finished.connect(self._on_ytdlp_download_finished)
        self._register_hotkey(self.settings.get("hotkey", "ctrl+alt+s"))

        self.wave_timer = QTimer(self)
        self.wave_timer.timeout.connect(self._tick_waveform)
        self.wave_timer.start(20)

        self.periodic_timer = QTimer(self)
        self.periodic_timer.timeout.connect(self._on_periodic_tick)
        self.periodic_timer.start(2500)

        self._refresh_ai_footer_status()
        if self.settings.get("use_ai_separation", False) and is_model_ready(
            self.settings.get("ai_model", UVR_MODEL_NAME)
        ):
            self._start_model_preload(self.settings.get("ai_model", UVR_MODEL_NAME))

    # --- UI CONSTRUCTION ---
    def _build_ui(self):
        self.root_widget = ThemedBackgroundWidget()
        self.setCentralWidget(self.root_widget)

        main_layout = QHBoxLayout(self.root_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Collapsible Frosted-Glass Sidebar
        self.sidebar = GlassSidebarFrame()
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

        self.ai_foot_widget = QWidget()
        ai_foot = QHBoxLayout(self.ai_foot_widget)
        ai_foot.setContentsMargins(4, 0, 2, 0)
        ai_foot.setSpacing(6)
        self.model_dot = QLabel("●")
        self.model_dot.setStyleSheet("color: #71717A; font-size: 10px;")
        self.model_status_lbl = QLabel("AI: Optional")
        self.model_status_lbl.setObjectName("Muted")
        self.model_status_lbl.setStyleSheet("font-size: 11px;")
        self.model_status_lbl.setWordWrap(True)
        self.model_help = HelpBadge(
            "Optional local AI rescue for reverb, noise, crowd, or lyrics matching."
        )
        ai_foot.addWidget(self.model_dot)
        ai_foot.addWidget(self.model_status_lbl, 1)
        ai_foot.addWidget(self.model_help)
        side_layout.addWidget(self.ai_foot_widget)

        main_layout.addWidget(self.sidebar)

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
        self.detail_page.like_toggled.connect(self._toggle_song_liked)
        self.detail_page.volume_changed.connect(self._on_detail_volume_changed)
        self.pages.addWidget(self.detail_page)
        main_layout.addWidget(self.pages, 1)

        self._set_active_nav(0)

    def _refresh_theme_icons(self):
        dark = bool(self.last_dark_state)
        idle_col = "#A1A1AA" if dark else "#475569"
        active_col = "#FFFFFF"
        ctrl_col = "#D4D4D8" if dark else "#334155"
        accent = (self._current_theme_cfg or {}).get("colors", {}).get("accent", "#3B82F6")
        pin_col = accent if self.is_pinned else ctrl_col

        self.collapse_btn.setIcon(create_ui_icon("sidebar", ctrl_col, 16))
        self.pin_btn.setIcon(create_ui_icon("pin", pin_col, 15))
        self.details_ui_icon = create_ui_icon("details", ctrl_col, 13)
        if hasattr(self, "mini_mode_btn"):
            self.mini_mode_btn.setIcon(create_ui_icon("mini_mode", ctrl_col, 14))

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
        self._saved_settings_snapshot["always_on_top"] = self.is_pinned
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
        self._sync_lib_header_responsive()
        self._reposition_mini_mode_btn()

    def eventFilter(self, obj, event):
        if obj in (getattr(self, "id_page", None), getattr(getattr(self, "id_scroll", None), "viewport", lambda: None)()):
            if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
                QTimer.singleShot(0, self._reposition_mini_mode_btn)
        return super().eventFilter(obj, event)

    def _reposition_mini_mode_btn(self):
        if not hasattr(self, "mini_mode_btn") or not hasattr(self, "id_scroll"):
            return
        self.mini_mode_btn.adjustSize()
        vp_w = self.id_scroll.viewport().width()
        if vp_w <= 0 and hasattr(self, "id_page"):
            vp_w = self.id_page.width()
        if vp_w <= 0:
            return
        content_right = min(vp_w - 22, (vp_w + 1280) // 2)
        x = max(8, content_right - self.mini_mode_btn.width())
        y = 18
        self.mini_mode_btn.move(x, y)
        self.mini_mode_btn.raise_()

    # --- IDENTIFY PAGE ---
    def _build_identify_page(self):
        page = QWidget()
        page.setObjectName("Page")
        self.id_page = page
        page_lay = QVBoxLayout(page)
        page_lay.setContentsMargins(0, 0, 0, 0)
        page_lay.setSpacing(0)

        scroll_inner = QWidget()
        outer = QHBoxLayout(scroll_inner)
        outer.setContentsMargins(22, 18, 22, 18)

        center_col = QWidget()
        center_col.setObjectName("Page")
        center_col.setMaximumWidth(1280)
        center_col.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(center_col)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        header_top = QHBoxLayout()
        header_top.setContentsMargins(0, 0, 128, 0)
        header_top.setSpacing(10)

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

        header_top.addLayout(title_col, 1)
        layout.addLayout(header_top)

        self.no_output_banner = QFrame()
        self.no_output_banner.setObjectName("ErrorBanner")
        no_out_lay = QHBoxLayout(self.no_output_banner)
        no_out_lay.setContentsMargins(10, 7, 10, 7)
        no_out_lay.setSpacing(10)
        no_out_lbl = QLabel("No audio output selected. Pick an output in Settings to listen.")
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

        btn_wrap = QVBoxLayout()
        btn_wrap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        btn_wrap.setSpacing(10)

        pulse_row = QHBoxLayout()
        pulse_row.setContentsMargins(0, 0, 0, 0)
        pulse_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pulse_btn = PulseIdentifyWidget()
        self.pulse_btn.setText("Listen")
        self.pulse_btn.clicked.connect(lambda: self.trigger_identify(mode="live"))
        pulse_row.addWidget(self.pulse_btn)
        btn_wrap.addLayout(pulse_row)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("DangerBtn")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self.cancel_identify)
        self.cancel_btn.hide()
        btn_wrap.addWidget(self.cancel_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self.ai_warn_banner = QFrame()
        self.ai_warn_banner.setObjectName("WarnBanner")
        warn_lay = QHBoxLayout(self.ai_warn_banner)
        warn_lay.setContentsMargins(12, 6, 12, 6)
        warn_lbl = QLabel(
            "AI Rescue is ON for tricky tracks (reverb, noise, sped-up, or speech). Turn off for normal clean music."
        )
        warn_lbl.setObjectName("WarnText")
        warn_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        warn_lbl.setWordWrap(True)
        warn_lay.addWidget(warn_lbl)
        btn_wrap.addWidget(self.ai_warn_banner)

        buffer_row = QHBoxLayout()
        buffer_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        buffer_row.setSpacing(10)

        self.buffer_btn = QPushButton("Identify Last 12s")
        self.buffer_btn.setObjectName("SecondaryActionBtn")
        self.buffer_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.buffer_btn.clicked.connect(lambda: self.trigger_identify(mode="buffer"))

        self.save_clip_btn = QPushButton("Save Last 12s")
        self.save_clip_btn.setObjectName("SecondaryActionBtn")
        self.save_clip_btn.setToolTip(
            "Save the last X seconds of audio so you can test it on multiple AI models"
        )
        self.save_clip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_clip_btn.clicked.connect(self._save_recent_clip_for_testing)

        self.quick_ai_chk = QCheckBox("Tricky Track AI Rescue")
        self.quick_ai_chk.setChecked(bool(self.settings.get("use_ai_separation", False)))
        self.quick_ai_chk.stateChanged.connect(self._on_quick_ai_toggled)

        ai_help = HelpBadge(
            "Runs your selected local AI model (De-Reverb, De-Noise, Crowd, or Whisper Lyrics)\n"
            "on both Identify Last and Listen Live when normal matching misses."
        )

        buffer_row.addWidget(self.buffer_btn)
        buffer_row.addWidget(self.save_clip_btn)
        buffer_row.addWidget(self.quick_ai_chk)
        buffer_row.addWidget(ai_help)
        btn_wrap.addLayout(buffer_row)

        self.saved_clip_bar = QFrame()
        self.saved_clip_bar.setObjectName("Card")
        self.saved_clip_bar.setMaximumWidth(640)
        sc_lay = QHBoxLayout(self.saved_clip_bar)
        sc_lay.setContentsMargins(12, 7, 10, 7)
        sc_lay.setSpacing(8)

        self.saved_clip_lbl = QLabel("Saved clip ready")
        self.saved_clip_lbl.setObjectName("Muted")
        self.saved_clip_lbl.setStyleSheet("font-size: 11px; font-weight: 600;")

        self.saved_clip_model_combo = NoWheelComboBox()
        self.saved_clip_model_combo.setMinimumWidth(185)

        self.test_saved_clip_btn = QPushButton("Test Saved Clip")
        self.test_saved_clip_btn.setObjectName("PrimaryBtn")
        self.test_saved_clip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.test_saved_clip_btn.clicked.connect(self._test_saved_clip)

        self.clear_saved_clip_btn = QPushButton("✕")
        self.clear_saved_clip_btn.setObjectName("IconDangerBtn")
        self.clear_saved_clip_btn.setFixedSize(26, 26)
        self.clear_saved_clip_btn.setToolTip("Delete saved test clip")
        self.clear_saved_clip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_saved_clip_btn.clicked.connect(self._clear_saved_clip)

        sc_lay.addWidget(self.saved_clip_lbl)
        sc_lay.addWidget(self.saved_clip_model_combo, 1)
        sc_lay.addWidget(self.test_saved_clip_btn)
        sc_lay.addWidget(self.clear_saved_clip_btn)
        btn_wrap.addWidget(self.saved_clip_bar, alignment=Qt.AlignmentFlag.AlignCenter)
        self._saved_clip_meta = ""
        self._refresh_saved_clip_ui()

        self.ai_warn_banner.setVisible(self.quick_ai_chk.isChecked())

        self.live_status_lbl = QLabel("Ready. Click Listen or press your shortcut anytime.")
        self.live_status_lbl.setObjectName("Muted")
        self.live_status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.live_status_lbl.setWordWrap(True)
        self.live_status_lbl.setStyleSheet("font-size: 12px;")
        btn_wrap.addWidget(self.live_status_lbl)

        layout.addLayout(btn_wrap)
        layout.addSpacing(8)

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

        self.result_card.setMaximumWidth(820)
        res_wrap = QHBoxLayout()
        res_wrap.setContentsMargins(0, 0, 0, 0)
        res_wrap.addStretch(1)
        res_wrap.addWidget(self.result_card, 100)
        res_wrap.addStretch(1)
        layout.addLayout(res_wrap)
        layout.addStretch(2)

        outer.addStretch(0)
        outer.addWidget(center_col, 1)
        outer.addStretch(0)

        self.id_scroll = _make_clean_scroll(scroll_inner, horizontal_off=True)
        page_lay.addWidget(self.id_scroll, 1)

        self.mini_mode_btn = QPushButton(" Mini Window", page)
        self.mini_mode_btn.setObjectName("PlatformBtn")
        self.mini_mode_btn.setIconSize(QSize(14, 14))
        self.mini_mode_btn.setToolTip("Hide main window to tray and open compact always-on-top Mini Window")
        self.mini_mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mini_mode_btn.clicked.connect(self._enter_mini_mode)
        self.mini_mode_btn.raise_()

        self.id_page.installEventFilter(self)
        self.id_scroll.viewport().installEventFilter(self)
        self.id_scroll.verticalScrollBar().rangeChanged.connect(lambda *_: self._reposition_mini_mode_btn())
        QTimer.singleShot(0, self._reposition_mini_mode_btn)

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
            if hasattr(self, "mini_window"):
                self.mini_window.pulse_btn.clear_waveform()

        hk = self.settings.get("hotkey", "ctrl+alt+s").upper()
        buf_enabled = bool(self.settings.get("enable_audio_buffer", True))
        buf_s = int(self.settings.get("buffer_seconds", 12))
        hk_mode = (
            "Listen Live"
            if (self.settings.get("hotkey_mode") == "live" or not buf_enabled)
            else f"Last {buf_s}s"
        )
        out_txt = (
            "No audio output selected"
            if not has_outputs
            else f"Listening to {count} audio output" + ("" if count == 1 else "s")
        )
        self.sub_status_lbl.setText(f"{out_txt} • Shortcut: {hk} ({hk_mode})")
        self.buffer_btn.setVisible(buf_enabled)
        self.save_clip_btn.setVisible(buf_enabled)
        if hasattr(self, "mini_window"):
            self.mini_window.buffer_btn.setVisible(buf_enabled)
        if not self.is_processing:
            self.pulse_btn.setText("Listen")
            self.buffer_btn.setText(f"Identify Last {buf_s}s")
            self.save_clip_btn.setText(f"Save Last {buf_s}s")
            if hasattr(self, "mini_window"):
                self.mini_window.pulse_btn.setText("Listen")
                self.mini_window.buffer_btn.setText(f"Identify Last {buf_s}s")

    # --- LIBRARY PAGE (STICKY HEADER + FULL-WIDTH SCROLLING + PLAYLISTS + BATCH SELECT) ---
    def _build_library_page(self):
        page = QWidget()
        page.setObjectName("Page")
        page_lay = QVBoxLayout(page)
        page_lay.setContentsMargins(0, 0, 0, 0)
        page_lay.setSpacing(0)

        # Sticky top bar: stays fixed while scrolling so batch actions always follow on screen
        self.lib_sticky_header = QWidget()
        self.lib_sticky_header.setObjectName("Page")
        self.lib_sticky_header.setFixedHeight(52)
        self.lib_sticky_header.setMinimumWidth(0)
        sticky_outer = QHBoxLayout(self.lib_sticky_header)
        sticky_outer.setContentsMargins(22, 14, 22, 8)
        sticky_outer.setSpacing(0)

        header_col = QWidget()
        header_col.setObjectName("Page")
        header_col.setMaximumWidth(980)
        header_col.setMinimumWidth(0)
        header_col.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        top = QHBoxLayout(header_col)
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)

        self.lib_title_wrap = QWidget()
        self.lib_title_wrap.setObjectName("Page")
        title_lay = QHBoxLayout(self.lib_title_wrap)
        title_lay.setContentsMargins(0, 0, 0, 0)
        title_lay.setSpacing(4)
        h1 = QLabel("Library")
        h1.setStyleSheet("font-size: 20px; font-weight: 700;")
        self.lib_count_lbl = QLabel("")
        self.lib_count_lbl.setObjectName("Muted")
        self.lib_count_lbl.setStyleSheet("font-size: 12px; margin-left: 4px;")
        title_lay.addWidget(h1)
        title_lay.addWidget(self.lib_count_lbl)

        # Sticky batch action bar (shown when 1+ songs are checked)
        self.batch_bar = QWidget()
        self.batch_bar.setObjectName("Page")
        batch_lay = QHBoxLayout(self.batch_bar)
        batch_lay.setContentsMargins(0, 0, 0, 0)
        batch_lay.setSpacing(7)

        self.batch_sel_lbl = QLabel("")
        self.batch_sel_lbl.setObjectName("Muted")
        self.batch_sel_lbl.setStyleSheet("font-size: 11px; font-weight: 600; margin-right: 2px;")

        self.batch_add_pl_btn = QPushButton("Add to Playlist ▾")
        self.batch_add_pl_btn.setObjectName("StickyActionBtn")
        self.batch_add_pl_btn.setFixedHeight(28)
        self.batch_add_pl_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.batch_add_pl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.batch_add_pl_btn.clicked.connect(self._show_batch_add_playlist_menu)

        self.batch_ytdlp_btn = QPushButton(" Batch Download (yt-dlp)")
        self.batch_ytdlp_btn.setObjectName("StickyActionBtn")
        self.batch_ytdlp_btn.setIcon(create_ytdlp_icon(14))
        self.batch_ytdlp_btn.setIconSize(QSize(14, 14))
        self.batch_ytdlp_btn.setFixedHeight(28)
        self.batch_ytdlp_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.batch_ytdlp_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.batch_ytdlp_btn.clicked.connect(self._batch_download_selected)

        self.batch_clear_btn = QPushButton("Clear Selected")
        self.batch_clear_btn.setObjectName("StickyDangerBtn")
        self.batch_clear_btn.setFixedHeight(28)
        self.batch_clear_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.batch_clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.batch_clear_btn.clicked.connect(self._clear_selected_songs)

        batch_lay.addWidget(self.batch_sel_lbl)
        batch_lay.addWidget(self.batch_add_pl_btn)
        batch_lay.addWidget(self.batch_ytdlp_btn)
        batch_lay.addWidget(self.batch_clear_btn)
        self.batch_bar.hide()

        self.clear_all_btn = QPushButton("Clear All")
        self.clear_all_btn.setObjectName("StickyDangerBtn")
        self.clear_all_btn.setFixedHeight(28)
        self.clear_all_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.clear_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_all_btn.clicked.connect(self.clear_history)

        top.addWidget(self.lib_title_wrap)
        top.addStretch(1)
        top.addWidget(self.batch_bar)
        top.addWidget(self.clear_all_btn)

        sticky_outer.addStretch(0)
        sticky_outer.addWidget(header_col, 1)
        sticky_outer.addStretch(0)
        page_lay.addWidget(self.lib_sticky_header)

        # Full-width scroll area so scrolling on the empty left/right sides always works!
        scroll_inner = QWidget()
        outer = QHBoxLayout(scroll_inner)
        outer.setContentsMargins(22, 4, 22, 18)
        outer.setSpacing(0)

        center_col = QWidget()
        center_col.setObjectName("Page")
        center_col.setMaximumWidth(980)
        center_col.setMinimumWidth(0)
        center_col.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(center_col)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # Playlist tabs row (Identified Songs, Liked Songs, custom playlists, + New Playlist)
        pl_bar = QHBoxLayout()
        pl_bar.setSpacing(6)
        self.playlist_tabs_flow = FlowWidget(h_spacing=6, v_spacing=6)
        pl_bar.addWidget(self.playlist_tabs_flow, 1)
        layout.addLayout(pl_bar)

        # Search + Sort + Custom playlist actions row
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(8)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search by song, artist, album, or genre...")
        self.search_input.textChanged.connect(self.refresh_library)

        self.lib_sort_combo = NoWheelComboBox()
        self.lib_sort_combo.addItems(
            [
                "Manual Order",
                "Date (Newest)",
                "Date (Oldest)",
                "Title (A-Z)",
                "Artist (A-Z)",
                "Genre",
                "Album",
            ]
        )
        self.lib_sort_combo.setCurrentIndex(1)
        self.lib_sort_combo.currentIndexChanged.connect(self._on_library_sort_changed)

        self.pl_download_btn = QPushButton("Download Playlist")
        self.pl_download_btn.setObjectName("PlatformBtn")
        self.pl_download_btn.setToolTip(
            "Download this playlist into a new folder with the playlist name using yt-dlp Downloader"
        )
        self.pl_download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pl_download_btn.clicked.connect(self._download_active_playlist)

        self.pl_rename_btn = QPushButton("Rename")
        self.pl_rename_btn.setObjectName("PlatformBtn")
        self.pl_rename_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pl_rename_btn.clicked.connect(self._rename_active_playlist)
        self.pl_rename_btn.hide()

        self.pl_del_btn = QPushButton("Delete Playlist")
        self.pl_del_btn.setObjectName("DangerBtn")
        self.pl_del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pl_del_btn.clicked.connect(self._delete_active_playlist)
        self.pl_del_btn.hide()

        ctrl_row.addWidget(self.search_input, 1)
        ctrl_row.addWidget(self.lib_sort_combo)
        ctrl_row.addWidget(self.pl_download_btn)
        ctrl_row.addWidget(self.pl_rename_btn)
        ctrl_row.addWidget(self.pl_del_btn)
        layout.addLayout(ctrl_row)

        # Select All row directly above songs (comfortable clickable padding)
        sel_row = QHBoxLayout()
        sel_row.setContentsMargins(6, 2, 4, 0)
        sel_row.setSpacing(8)
        self.select_all_chk = QCheckBox("Select All")
        self.select_all_chk.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.select_all_chk.setStyleSheet("QCheckBox { padding: 6px 10px; }")
        self.select_all_chk.setCursor(Qt.CursorShape.PointingHandCursor)
        self.select_all_chk.stateChanged.connect(self._on_select_all_toggled)
        self.selected_count_lbl = QLabel("")
        self.selected_count_lbl.setObjectName("Muted")
        self.selected_count_lbl.setStyleSheet("font-size: 11px;")
        sel_row.addWidget(self.select_all_chk)
        sel_row.addWidget(self.selected_count_lbl)
        sel_row.addStretch()
        layout.addLayout(sel_row)

        # Song list container (no nested scroll; entire page scrolls smoothly including left/right sides!)
        self.lib_list_layout = QVBoxLayout()
        self.lib_list_layout.setContentsMargins(4, 4, 4, 8)
        self.lib_list_layout.setSpacing(8)
        layout.addLayout(self.lib_list_layout)
        layout.addStretch(1)

        outer.addStretch(0)
        outer.addWidget(center_col, 1)
        outer.addStretch(0)

        self.lib_scroll = _make_clean_scroll(scroll_inner, horizontal_off=True)
        page_lay.addWidget(self.lib_scroll, 1)

        self._rebuild_playlist_tabs()
        return page

    # --- SETTINGS PAGE ---
    def _build_settings_page(self):
        page = QWidget()
        page.setObjectName("Page")
        page_lay = QVBoxLayout(page)
        page_lay.setContentsMargins(0, 0, 0, 0)
        page_lay.setSpacing(0)

        # Full-width scroll area so scrolling anywhere on the sides of Settings works smoothly!
        scroll_inner = QWidget()
        scroll_h = QHBoxLayout(scroll_inner)
        scroll_h.setContentsMargins(22, 18, 22, 16)

        center_col = QWidget()
        center_col.setObjectName("Page")
        center_col.setMaximumWidth(920)
        center_col.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(center_col)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        top = QHBoxLayout()
        h1 = QLabel("Settings")
        h1.setStyleSheet("font-size: 20px; font-weight: 700;")
        top.addWidget(h1)
        layout.addLayout(top)

        # 1. Look, Theme & Waveform
        app_card = QFrame()
        app_card.setObjectName("Card")
        app_lay = QVBoxLayout(app_card)
        app_lay.setContentsMargins(16, 14, 16, 14)
        app_lay.setSpacing(10)

        sec1 = QLabel("Look, Theme & Waveform")
        sec1.setStyleSheet("font-size: 14px; font-weight: 600;")
        app_lay.addWidget(sec1)

        theme_row = QHBoxLayout()
        theme_row.setSpacing(8)
        theme_row.addWidget(QLabel("Theme"))
        theme_row.addStretch()
        self.theme_combo = NoWheelComboBox()
        self.theme_combo.setMinimumWidth(190)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_combo_changed)
        create_theme_btn = QPushButton("Create Theme")
        create_theme_btn.setObjectName("PlatformBtn")
        create_theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        create_theme_btn.clicked.connect(self._open_create_theme_dialog)
        self.edit_theme_btn = QPushButton("Edit Theme")
        self.edit_theme_btn.setObjectName("PlatformBtn")
        self.edit_theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.edit_theme_btn.clicked.connect(self._open_edit_theme_dialog)
        self.sync_presets_btn = QPushButton("Get Presets")
        self.sync_presets_btn.setObjectName("PlatformBtn")
        self.sync_presets_btn.setToolTip("Browse and install available custom theme presets from GitHub")
        self.sync_presets_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sync_presets_btn.clicked.connect(self._open_github_presets_dialog)
        themes_dir_btn = QPushButton("Themes Folder")
        themes_dir_btn.setObjectName("PlatformBtn")
        themes_dir_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        themes_dir_btn.clicked.connect(lambda: os.startfile(THEMES_DIR))
        theme_row.addWidget(self.theme_combo)
        app_lay.addLayout(theme_row)

        theme_btns_row = QHBoxLayout()
        theme_btns_row.setSpacing(6)
        theme_btns_row.addStretch()
        theme_btns_row.addWidget(create_theme_btn)
        theme_btns_row.addWidget(self.edit_theme_btn)
        theme_btns_row.addWidget(self.sync_presets_btn)
        theme_btns_row.addWidget(themes_dir_btn)
        app_lay.addLayout(theme_btns_row)

        icon_row = QHBoxLayout()
        icon_row.setSpacing(8)
        icon_row.addLayout(
            _label_with_help(
                "App Icon Color",
                "Changes the SongPulse icon in the taskbar, titlebar, tray, and sidebar live.",
            )
        )
        icon_row.addStretch()
        self.icon_color_combo = NoWheelComboBox()
        self.icon_color_combo.setMinimumWidth(190)
        for pid, plbl, pc1, pc2 in ICON_COLOR_PRESETS:
            if pc1 and pc2:
                self.icon_color_combo.addItem(QIcon(create_app_pixmap(18, pc1, pc2)), plbl, pid)
            else:
                self.icon_color_combo.addItem(plbl, pid)
        self.icon_color_combo.currentIndexChanged.connect(self._mark_unsaved)
        icon_row.addWidget(self.icon_color_combo)
        app_lay.addLayout(icon_row)

        text_sz_row = QHBoxLayout()
        text_sz_row.setSpacing(8)
        text_sz_row.addLayout(
            _label_with_help(
                "Text Size",
                "Scales interface text across SongPulse for easier reading.",
            )
        )
        text_sz_row.addStretch()
        self.text_size_combo = NoWheelComboBox()
        self.text_size_combo.setMinimumWidth(190)
        for tid, tlbl, _scale in TEXT_SIZE_PRESETS:
            self.text_size_combo.addItem(tlbl, tid)
        self.text_size_combo.currentIndexChanged.connect(self._mark_unsaved)
        text_sz_row.addWidget(self.text_size_combo)
        app_lay.addLayout(text_sz_row)

        wf_row = QHBoxLayout()
        wf_row.setSpacing(8)
        wf_row.addWidget(QLabel("Waveform Shape"))
        wf_row.addStretch()
        self.wf_style_combo = NoWheelComboBox()
        self.wf_style_combo.addItems(
            [
                "Auto (Follow Theme)",
                "Radial (Around Button)",
                "Horizontal Split (Left & Right)",
                "Pulse Line (Left & Right)",
                "Bottom Bar",
                "Dots Ring",
            ]
        )
        self.wf_style_combo.currentIndexChanged.connect(self._mark_unsaved)
        wf_row.addWidget(self.wf_style_combo)
        app_lay.addLayout(wf_row)

        wf_opts_row = QHBoxLayout()
        wf_opts_row.setSpacing(14)
        self.wf_mirror_chk = QCheckBox("Mirror waveform")
        self.wf_mirror_chk.stateChanged.connect(self._mark_unsaved)
        self.wf_idle_dots_chk = QCheckBox("Show dots when silent")
        self.wf_idle_dots_chk.stateChanged.connect(self._mark_unsaved)
        wf_opts_row.addWidget(self.wf_mirror_chk)
        wf_opts_row.addWidget(self.wf_idle_dots_chk)
        wf_opts_row.addStretch()
        app_lay.addLayout(wf_opts_row)

        maxw_row = QHBoxLayout()
        maxw_row.setSpacing(10)
        maxw_row.addWidget(QLabel("Waveform Max Width"))
        self.wf_maxw_slider = NoWheelSlider(Qt.Orientation.Horizontal)
        self.wf_maxw_slider.setRange(260, 900)
        self.wf_maxw_slider.setSingleStep(10)
        self.wf_maxw_slider.setPageStep(40)
        self.wf_maxw_spin = NoWheelSpinBox()
        self.wf_maxw_spin.setRange(260, 900)
        self.wf_maxw_spin.setSingleStep(10)
        self.wf_maxw_spin.setSuffix(" px")
        self.wf_maxw_spin.setFixedWidth(92)
        self.wf_maxw_slider.valueChanged.connect(self._on_wf_maxw_slider_changed)
        self.wf_maxw_spin.valueChanged.connect(self._on_wf_maxw_spin_changed)
        maxw_row.addWidget(self.wf_maxw_slider, 1)
        maxw_row.addWidget(self.wf_maxw_spin)
        app_lay.addLayout(maxw_row)

        step_row = QHBoxLayout()
        step_row.setSpacing(10)
        step_row.addLayout(
            _label_with_help(
                "Waveform Spacing (Step)",
                "Adjust distance between waveform bars or zig-zag peaks (Auto uses each style's default).",
            )
        )
        self.wf_step_slider = NoWheelSlider(Qt.Orientation.Horizontal)
        self.wf_step_slider.setRange(0, 240)  # 0 = Auto, 40..240 = 4.0..24.0 px
        self.wf_step_slider.setSingleStep(5)
        self.wf_step_spin = NoWheelDoubleSpinBox()
        self.wf_step_spin.setRange(0.0, 24.0)
        self.wf_step_spin.setDecimals(1)
        self.wf_step_spin.setSingleStep(0.5)
        self.wf_step_spin.setSpecialValueText("Auto")
        self.wf_step_spin.setSuffix(" px")
        self.wf_step_spin.setFixedWidth(92)
        self.wf_step_auto_btn = QPushButton("Auto")
        self.wf_step_auto_btn.setObjectName("PlatformBtn")
        self.wf_step_auto_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.wf_step_auto_btn.clicked.connect(lambda: self.wf_step_spin.setValue(0.0))
        self.wf_step_slider.valueChanged.connect(self._on_wf_step_slider_changed)
        self.wf_step_spin.valueChanged.connect(self._on_wf_step_spin_changed)
        step_row.addWidget(self.wf_step_slider, 1)
        step_row.addWidget(self.wf_step_spin)
        step_row.addWidget(self.wf_step_auto_btn)
        app_lay.addLayout(step_row)

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

        self.startup_chk = QCheckBox("Start automatically with Windows")
        self.startup_chk.stateChanged.connect(self._mark_unsaved)
        sys_lay.addWidget(self.startup_chk)

        self.start_tray_chk = QCheckBox("Always minimize to tray when opening SongPulse")
        self.start_tray_chk.stateChanged.connect(self._mark_unsaved)
        sys_lay.addWidget(self.start_tray_chk)

        self.min_tray_chk = QCheckBox("Hide to system tray when minimized")
        self.min_tray_chk.stateChanged.connect(self._mark_unsaved)
        sys_lay.addWidget(self.min_tray_chk)

        self.notif_chk = QCheckBox("Show notification when a song is found")
        self.notif_chk.stateChanged.connect(self._mark_unsaved)
        sys_lay.addWidget(self.notif_chk)

        self.always_hud_chk = QCheckBox(
            "Always show bottom-right status pill while identifying (even when focused)"
        )
        self.always_hud_chk.stateChanged.connect(self._mark_unsaved)
        sys_lay.addWidget(self.always_hud_chk)

        force_row = QHBoxLayout()
        self.force_overlay_chk = QCheckBox(
            "Force bottom-right popup (shows even when Do Not Disturb is on)"
        )
        self.force_overlay_chk.stateChanged.connect(self._mark_unsaved)
        force_row.addWidget(self.force_overlay_chk)
        force_row.addWidget(
            HelpBadge(
                "Shows SongPulse's own bottom-right popup card so Windows Do Not Disturb cannot hide it."
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

        site_row = QHBoxLayout()
        site_row.addLayout(
            _label_with_help(
                "Preferred Music Site",
                "Choose which music website opens when right-clicking 'Listen in' on a song or clicking an artist/album name in the info popup.",
            )
        )
        site_row.addStretch()
        self.info_site_combo = NoWheelComboBox()
        for _key, label in PREFERRED_INFO_SITES:
            self.info_site_combo.addItem(label, _key)
        self.info_site_combo.currentIndexChanged.connect(self._mark_unsaved)
        site_row.addWidget(self.info_site_combo)
        sys_lay.addLayout(site_row)

        hk_row = QHBoxLayout()
        hk_row.addLayout(
            _label_with_help(
                "Keyboard Shortcut",
                "Global shortcut that works inside fullscreen games and other apps.",
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
                "Choose whether the global shortcut scans the recent audio buffer or listens live.",
            )
        )
        hk_mode_row.addStretch()
        self.hk_mode_combo = NoWheelComboBox()
        self.hk_mode_combo.addItems(["Use recent audio buffer", "Listen live"])
        self.hk_mode_combo.currentIndexChanged.connect(self._mark_unsaved)
        hk_mode_row.addWidget(self.hk_mode_combo)
        sys_lay.addLayout(hk_mode_row)

        yt_row = QHBoxLayout()
        yt_row.addLayout(
            _label_with_help(
                "yt-dlp Downloader App",
                "Path to your yt-dlp downloader (.exe or .pyw).",
            )
        )
        self.ytdlp_path_input = QLineEdit()
        self.ytdlp_path_input.setPlaceholderText("Not set (will ask on first click)")
        self.ytdlp_path_input.textChanged.connect(self._mark_unsaved)
        yt_find_btn = QPushButton("Auto-Find")
        yt_find_btn.setObjectName("PlatformBtn")
        yt_find_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        yt_find_btn.clicked.connect(self._autofind_ytdlp_app)
        yt_browse_btn = QPushButton("Browse")
        yt_browse_btn.setObjectName("PlatformBtn")
        yt_browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        yt_browse_btn.clicked.connect(self._browse_ytdlp_app)
        yt_row.addWidget(self.ytdlp_path_input, 1)
        yt_row.addWidget(yt_find_btn)
        yt_row.addWidget(yt_browse_btn)
        sys_lay.addLayout(yt_row)

        self.ytdlp_quick_mp3_chk = QCheckBox(
            "Quickly download MP3 with yt-dlp Downloader Music preset (auto-opens and closes after downloading)"
        )
        self.ytdlp_quick_mp3_chk.stateChanged.connect(self._mark_unsaved)
        sys_lay.addWidget(self.ytdlp_quick_mp3_chk)
        layout.addWidget(sys_card)

        # 3. Audio & Timing
        aud_card = QFrame()
        aud_card.setObjectName("Card")
        aud_lay = QVBoxLayout(aud_card)
        aud_lay.setContentsMargins(16, 14, 16, 14)
        aud_lay.setSpacing(10)

        sec3 = QLabel("Audio & Buffer")
        sec3.setStyleSheet("font-size: 14px; font-weight: 600;")
        aud_lay.addWidget(sec3)

        dev_hdr = QHBoxLayout()
        dev_hdr.setSpacing(6)
        self.audio_outputs_collapse_btn = QPushButton("▶  Remembered Audio Outputs")
        self.audio_outputs_collapse_btn.setObjectName("CollapseBtn")
        self.audio_outputs_collapse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.audio_outputs_collapse_btn.clicked.connect(self._toggle_audio_outputs_section)
        dev_hdr.addWidget(self.audio_outputs_collapse_btn)
        dev_hdr.addWidget(
            HelpBadge(
                "Check the speakers/headphones SongPulse should listen to. Disconnected outputs stay remembered until you remove them; Scan checks for new outputs."
            )
        )
        dev_hdr.addStretch()
        self.dev_status_lbl = QLabel("")
        self.dev_status_lbl.setObjectName("Muted")
        self.dev_status_lbl.setStyleSheet("font-size: 11px;")
        dev_hdr.addWidget(self.dev_status_lbl)
        scan_btn = QPushButton("Scan")
        scan_btn.setObjectName("PlatformBtn")
        scan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        scan_btn.setToolTip("Scan for newly connected audio outputs")
        scan_btn.clicked.connect(self._refresh_devices_dropdown)
        dev_hdr.addWidget(scan_btn)
        aud_lay.addLayout(dev_hdr)

        self.audio_outputs_list = AudioOutputListWidget()
        self.audio_outputs_list.changed.connect(self._mark_unsaved)
        self.audio_outputs_list.changed.connect(self._update_audio_outputs_collapse_header)
        aud_lay.addWidget(self.audio_outputs_list)
        self._set_audio_outputs_expanded(False)

        self.enable_buf_chk = QCheckBox(
            "Keep recent audio buffer in RAM (disable to save RAM; Listen Live still works)"
        )
        self.enable_buf_chk.stateChanged.connect(self._mark_unsaved)
        aud_lay.addWidget(self.enable_buf_chk)

        buf_row = QHBoxLayout()
        buf_row.addLayout(
            _label_with_help(
                "Audio Buffer Length",
                "Seconds of recent audio kept in RAM when the buffer is enabled (10 to 15s is ideal).",
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
                "Maximum time Listen Live waits before finishing.",
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

        # 4. Collapsible Optional AI Models
        ai_card = QFrame()
        ai_card.setObjectName("Card")
        ai_lay = QVBoxLayout(ai_card)
        ai_lay.setContentsMargins(16, 12, 16, 12)
        ai_lay.setSpacing(8)

        ai_head_row = QHBoxLayout()
        self.ai_collapse_btn = QPushButton("▶  AI Models (Tricky Tracks)")
        self.ai_collapse_btn.setObjectName("CollapseBtn")
        self.ai_collapse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ai_collapse_btn.clicked.connect(self._toggle_ai_section)
        ai_head_row.addWidget(self.ai_collapse_btn, 1)

        self.add_custom_model_btn = QPushButton("+ Add Custom")
        self.add_custom_model_btn.setObjectName("PlatformBtn")
        self.add_custom_model_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_custom_model_btn.clicked.connect(self._add_custom_ai_model)
        ai_head_row.addWidget(self.add_custom_model_btn)
        ai_lay.addLayout(ai_head_row)

        self.ai_body_widget = QWidget()
        ai_body_lay = QVBoxLayout(self.ai_body_widget)
        ai_body_lay.setContentsMargins(0, 4, 0, 0)
        ai_body_lay.setSpacing(8)

        eng_row = QHBoxLayout()
        self.ai_engine_lbl = QLabel("")
        self.ai_engine_lbl.setWordWrap(True)
        self.ai_engine_lbl.setStyleSheet("font-size: 12px; font-weight: 600;")
        self.install_ai_engine_btn = QPushButton("Download AI Engine")
        self.install_ai_engine_btn.setObjectName("PrimaryBtn")
        self.install_ai_engine_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.install_ai_engine_btn.clicked.connect(self._download_ai_engine)
        eng_row.addWidget(self.ai_engine_lbl, 1)
        eng_row.addWidget(self.install_ai_engine_btn)
        ai_body_lay.addLayout(eng_row)

        self.ai_models_container = QVBoxLayout()
        self.ai_models_container.setSpacing(6)
        self._ai_model_radio_group = QButtonGroup(self)
        ai_body_lay.addLayout(self.ai_models_container)

        self.ai_body_widget.hide()
        self.add_custom_model_btn.hide()
        ai_lay.addWidget(self.ai_body_widget)
        layout.addWidget(ai_card)

        # 5. Performance & GPU
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
            "Switch AI rescue to CPU while gaming or under high GPU load"
        )
        self.gpu_auto_cpu_chk.stateChanged.connect(self._mark_unsaved)
        gpu_chk_row.addWidget(self.gpu_auto_cpu_chk)
        gpu_chk_row.addStretch()
        gpu_lay.addLayout(gpu_chk_row)

        thresh_row = QHBoxLayout()
        thresh_row.addLayout(
            _label_with_help(
                "GPU Load Limit",
                "If GPU usage meets or exceeds this value, AI runs on CPU instead.",
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
        self.gpu_speed_lbl = QLabel("AI speed: Run a test when an AI model is downloaded")
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

        # 6. Storage & Clean-Up
        stor_card = QFrame()
        stor_card.setObjectName("Card")
        stor_lay = QVBoxLayout(stor_card)
        stor_lay.setContentsMargins(16, 14, 16, 14)
        stor_lay.setSpacing(8)

        sec_stor = QLabel("Storage & Clean-Up")
        sec_stor.setStyleSheet("font-size: 14px; font-weight: 600;")
        stor_lay.addWidget(sec_stor)

        stor_row = QHBoxLayout()
        self.storage_summary_lbl = QLabel("Calculating app storage...")
        self.storage_summary_lbl.setObjectName("Muted")
        self.storage_summary_lbl.setWordWrap(True)
        self.storage_summary_lbl.setStyleSheet("font-size: 12px;")

        cleanup_btn = QPushButton("Clean Up Storage...")
        cleanup_btn.setObjectName("PlatformBtn")
        cleanup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cleanup_btn.clicked.connect(self._open_storage_cleanup)
        stor_row.addWidget(self.storage_summary_lbl, 1)
        stor_row.addWidget(cleanup_btn)
        stor_lay.addLayout(stor_row)
        layout.addWidget(stor_card)

        # 7. Updates (Direct in-app self-updater)
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

        # 8. Reset Defaults (moved to the very bottom of Settings scroll content!)
        reset_card = QFrame()
        reset_card.setObjectName("Card")
        reset_lay = QHBoxLayout(reset_card)
        reset_lay.setContentsMargins(16, 12, 16, 12)
        reset_lbl = QLabel("Restore all SongPulse settings back to default values.")
        reset_lbl.setObjectName("Muted")
        reset_lbl.setWordWrap(True)
        reset_lbl.setStyleSheet("font-size: 12px;")
        reset_btn = QPushButton("Reset Defaults")
        reset_btn.setObjectName("DangerBtn")
        reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_btn.clicked.connect(self._reset_settings)
        reset_lay.addWidget(reset_lbl, 1)
        reset_lay.addWidget(reset_btn)
        layout.addWidget(reset_card)

        layout.addStretch(1)

        scroll_h.setSpacing(0)
        scroll_h.addStretch(0)
        scroll_h.addWidget(center_col, 1)
        scroll_h.addStretch(0)

        self.settings_scroll = _make_clean_scroll(scroll_inner, horizontal_off=True)
        page_lay.addWidget(self.settings_scroll, 1)

        # Sticky footer bar: Discard Changes + Save Settings
        footer_wrap = QWidget()
        footer_outer = QHBoxLayout(footer_wrap)
        footer_outer.setContentsMargins(22, 8, 30, 14)
        footer_outer.setSpacing(0)

        footer_inner = QWidget()
        footer_inner.setMaximumWidth(920)
        footer_inner.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        footer = QHBoxLayout(footer_inner)
        footer.setContentsMargins(10, 0, 10, 0)
        footer.setSpacing(10)

        self.discard_changes_btn = QPushButton("Discard Changes")
        self.discard_changes_btn.setObjectName("DangerBtn")
        self.discard_changes_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.discard_changes_btn.clicked.connect(self._discard_unsaved_settings)
        self.discard_changes_btn.hide()

        self.unsaved_lbl = QLabel("")
        self.unsaved_lbl.setStyleSheet("color: #D97706; font-size: 11px; font-weight: 600;")

        save_btn = QPushButton("Save Settings")
        save_btn.setObjectName("PrimaryBtn")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.clicked.connect(self._save_settings_from_ui)

        footer.addWidget(self.discard_changes_btn)
        footer.addStretch()
        footer.addWidget(self.unsaved_lbl)
        footer.addWidget(save_btn)

        footer_outer.addStretch(0)
        footer_outer.addWidget(footer_inner, 1)
        footer_outer.addStretch(0)
        page_lay.addWidget(footer_wrap)

        self._populate_settings_ui()
        return page

    def _set_audio_outputs_expanded(self, expanded: bool):
        self._audio_outputs_expanded = bool(expanded)
        self.audio_outputs_list.setVisible(self._audio_outputs_expanded)
        self._update_audio_outputs_collapse_header()

    def _toggle_audio_outputs_section(self):
        self._set_audio_outputs_expanded(not self._audio_outputs_expanded)

    def _update_audio_outputs_collapse_header(self):
        if not hasattr(self, "audio_outputs_collapse_btn"):
            return
        arrow = "▼" if getattr(self, "_audio_outputs_expanded", False) else "▶"
        n_sel = len(self.audio_outputs_list.get_selected()) if hasattr(self, "audio_outputs_list") else 0
        self.audio_outputs_collapse_btn.setText(
            f"{arrow}  Remembered Audio Outputs  •  {n_sel} active"
        )

    def _set_ai_section_expanded(self, expanded: bool):
        self._ai_section_expanded = bool(expanded)
        self.ai_body_widget.setVisible(self._ai_section_expanded)
        self.add_custom_model_btn.setVisible(self._ai_section_expanded)
        self._update_ai_collapse_header()

    def _toggle_ai_section(self):
        self._set_ai_section_expanded(not self._ai_section_expanded)

    def _update_ai_collapse_header(self):
        if not hasattr(self, "ai_collapse_btn"):
            return
        arrow = "▼" if self._ai_section_expanded else "▶"
        n_dl = len(get_downloaded_ai_models())
        self.ai_collapse_btn.setText(f"{arrow}  AI Models (Tricky Tracks)  •  {n_dl} downloaded")

    def _sync_edit_theme_btn(self):
        if not hasattr(self, "edit_theme_btn") or not hasattr(self, "theme_combo"):
            return
        cur_key = str(self.theme_combo.currentData() or "")
        is_custom = cur_key.startswith("custom:")
        self.edit_theme_btn.setEnabled(is_custom)
        self.edit_theme_btn.setToolTip(
            "Edit this saved custom theme"
            if is_custom
            else "Select a custom theme to edit it"
        )

    def _refresh_theme_dropdown(self, select_key: str):
        self.theme_combo.blockSignals(True)
        self.theme_combo.clear()
        choices = list_all_theme_choices()
        target_idx = 0
        for idx, (key, label) in enumerate(choices):
            self.theme_combo.addItem(label, key)
            if key == select_key:
                target_idx = idx
        self.theme_combo.setCurrentIndex(target_idx)
        self.theme_combo.blockSignals(False)
        self._sync_edit_theme_btn()

    def _sync_saved_theme_colors(self):
        saved_cols = get_saved_theme_colors()
        self.settings["saved_theme_colors"] = saved_cols
        self._saved_settings_snapshot["saved_theme_colors"] = list(saved_cols)

    def _on_theme_combo_changed(self, *_args):
        if getattr(self, "_suppress_unsaved", False):
            return
        if hasattr(self, "wf_style_combo") and self.wf_style_combo.currentIndex() == 0:
            self._sync_theme_waveform_checkboxes(self.theme_combo.currentData() or "auto")
        self._mark_unsaved()

    def _sync_theme_waveform_checkboxes(self, theme_key: str):
        if self.settings.get("waveform_style", "auto") != "auto":
            return
        if (
            theme_key == self._saved_settings_snapshot.get("theme", "auto")
            and self._saved_settings_snapshot.get("waveform_style", "auto") == "auto"
        ):
            mirror = bool(self._saved_settings_snapshot.get("waveform_mirror", False))
            idle_dots = bool(self._saved_settings_snapshot.get("waveform_idle_dots", False))
        else:
            cfg = load_theme_config(theme_key, is_windows_dark_mode())
            wf = cfg.get("waveform", {})
            mirror = bool(wf.get("mirror", False))
            idle_dots = bool(wf.get("idle_dots", False))
        self.settings["waveform_mirror"] = mirror
        self.settings["waveform_idle_dots"] = idle_dots
        if hasattr(self, "wf_mirror_chk"):
            self.wf_mirror_chk.blockSignals(True)
            self.wf_mirror_chk.setChecked(mirror)
            self.wf_mirror_chk.blockSignals(False)
        if hasattr(self, "wf_idle_dots_chk"):
            self.wf_idle_dots_chk.blockSignals(True)
            self.wf_idle_dots_chk.setChecked(idle_dots)
            self.wf_idle_dots_chk.blockSignals(False)

    def _apply_and_save_custom_theme(self, theme_key: str):
        cfg = load_theme_config(theme_key, is_windows_dark_mode())
        wf = cfg.get("waveform", {})
        mirror = bool(wf.get("mirror", False))
        idle_dots = bool(wf.get("idle_dots", False))

        self._suppress_unsaved = True
        try:
            self._refresh_theme_dropdown(theme_key)
            if hasattr(self, "wf_style_combo"):
                self.wf_style_combo.setCurrentIndex(0)
            if hasattr(self, "wf_mirror_chk"):
                self.wf_mirror_chk.setChecked(mirror)
            if hasattr(self, "wf_idle_dots_chk"):
                self.wf_idle_dots_chk.setChecked(idle_dots)
        finally:
            self._suppress_unsaved = False

        self.settings["theme"] = theme_key
        self.settings["waveform_style"] = "auto"
        self.settings["waveform_mirror"] = mirror
        self.settings["waveform_idle_dots"] = idle_dots

        self._saved_settings_snapshot["theme"] = theme_key
        self._saved_settings_snapshot["waveform_style"] = "auto"
        self._saved_settings_snapshot["waveform_mirror"] = mirror
        self._saved_settings_snapshot["waveform_idle_dots"] = idle_dots
        save_settings(self._saved_settings_snapshot)

        self.apply_theme(force=True)
        self._refresh_storage_summary()

        if not self._has_unsaved_settings_changes():
            self.has_unsaved_settings = False
            self.discard_changes_btn.hide()
            self.unsaved_lbl.setText("Theme saved & applied!")
            QTimer.singleShot(
                2000,
                lambda: self.unsaved_lbl.setText("") if not self.has_unsaved_settings else None,
            )

    def _open_github_presets_dialog(self):
        dlg = GithubPresetsDialog(parent=self)
        if dlg.exec() and dlg.installed_names:
            if hasattr(self, "theme_combo"):
                cur_key = str(self.theme_combo.currentData() or self.settings.get("theme", "auto"))
                self._suppress_unsaved = True
                try:
                    self._refresh_theme_dropdown(cur_key)
                finally:
                    self._suppress_unsaved = False
            self._refresh_storage_summary()
            names_txt = ", ".join(dlg.installed_names)
            QMessageBox.information(
                self,
                "Presets Installed",
                f"Installed custom theme preset(s): {names_txt}\n\n"
                "Select them from the Theme dropdown in Settings.",
            )

    def _open_create_theme_dialog(self):
        dlg = CustomThemeDialog(
            parent=self,
            on_preview=self.preview_theme_config,
            on_stop_preview=self.stop_theme_preview,
        )
        accepted = dlg.exec()
        self._sync_saved_theme_colors()
        if accepted and dlg.created_theme_key:
            self._apply_and_save_custom_theme(dlg.created_theme_key)

    def _open_edit_theme_dialog(self):
        cur_key = str(self.theme_combo.currentData() or "")
        if not cur_key.startswith("custom:"):
            return
        dlg = CustomThemeDialog(
            parent=self,
            edit_theme_key=cur_key,
            on_preview=self.preview_theme_config,
            on_stop_preview=self.stop_theme_preview,
        )
        accepted = dlg.exec()
        self._sync_saved_theme_colors()
        if accepted and dlg.created_theme_key:
            self._apply_and_save_custom_theme(dlg.created_theme_key)

    def _on_wf_maxw_slider_changed(self, val: int):
        self.wf_maxw_spin.blockSignals(True)
        self.wf_maxw_spin.setValue(val)
        self.wf_maxw_spin.blockSignals(False)
        self._mark_unsaved()

    def _on_wf_maxw_spin_changed(self, val: int):
        self.wf_maxw_slider.blockSignals(True)
        self.wf_maxw_slider.setValue(val)
        self.wf_maxw_slider.blockSignals(False)
        self._mark_unsaved()

    def _on_wf_step_slider_changed(self, raw: int):
        val = 0.0 if raw < 40 else (raw / 10.0)
        self.wf_step_spin.blockSignals(True)
        self.wf_step_spin.setValue(val)
        self.wf_step_spin.blockSignals(False)
        self._mark_unsaved()

    def _on_wf_step_spin_changed(self, val: float):
        if 0.0 < val < 4.0:
            val = 4.0
            self.wf_step_spin.blockSignals(True)
            self.wf_step_spin.setValue(val)
            self.wf_step_spin.blockSignals(False)
        raw = 0 if val < 4.0 else int(round(val * 10.0))
        self.wf_step_slider.blockSignals(True)
        self.wf_step_slider.setValue(raw)
        self.wf_step_slider.blockSignals(False)
        self._mark_unsaved()

    def _populate_settings_ui(self):
        self._suppress_unsaved = True
        try:
            self._refresh_theme_dropdown(self.settings.get("theme", "auto"))

            ic_pref = self.settings.get("icon_color", "theme")
            for idx in range(self.icon_color_combo.count()):
                if self.icon_color_combo.itemData(idx) == ic_pref:
                    self.icon_color_combo.setCurrentIndex(idx)
                    break

            ts_pref = self.settings.get("text_size", "small")
            for idx in range(self.text_size_combo.count()):
                if self.text_size_combo.itemData(idx) == ts_pref:
                    self.text_size_combo.setCurrentIndex(idx)
                    break

            wf_map = {"auto": 0, "radial": 1, "horizontal": 2, "pulse_line": 3, "bar": 4, "dots": 5}
            self.wf_style_combo.setCurrentIndex(
                wf_map.get(self.settings.get("waveform_style", "auto"), 0)
            )
            self.wf_mirror_chk.setChecked(bool(self.settings.get("waveform_mirror", False)))
            self.wf_idle_dots_chk.setChecked(bool(self.settings.get("waveform_idle_dots", False)))
            max_w = int(self.settings.get("waveform_max_width", 620))
            self.wf_maxw_spin.setValue(max_w)
            self.wf_maxw_slider.setValue(max_w)
            wf_step = float(self.settings.get("waveform_step", 0.0) or 0.0)
            self.wf_step_spin.setValue(wf_step)
            self.wf_step_slider.setValue(0 if wf_step < 4.0 else int(round(wf_step * 10.0)))

            self.save_win_size_chk.setChecked(
                bool(self.settings.get("save_window_size", True))
            )
            self.startup_chk.setChecked(is_startup_enabled())
            self.start_tray_chk.setChecked(
                bool(self.settings.get("start_minimized_to_tray", False))
            )
            self.min_tray_chk.setChecked(
                bool(self.settings.get("minimize_to_tray", True))
            )
            self.notif_chk.setChecked(
                bool(self.settings.get("show_notifications", True))
            )
            self.always_hud_chk.setChecked(
                bool(self.settings.get("always_show_status_hud", True))
            )
            self.force_overlay_chk.setChecked(
                bool(self.settings.get("force_overlay_notification", True))
            )
            self.ping_chk.setChecked(bool(self.settings.get("play_ping_sound", True)))
            self.autocopy_chk.setChecked(bool(self.settings.get("auto_copy", False)))

            pref_site = self.settings.get("preferred_info_site", "auto")
            for idx in range(self.info_site_combo.count()):
                if self.info_site_combo.itemData(idx) == pref_site:
                    self.info_site_combo.setCurrentIndex(idx)
                    break

            self.hk_input.setText(self.settings.get("hotkey", "ctrl+alt+s"))
            self.hk_mode_combo.setCurrentIndex(
                1 if self.settings.get("hotkey_mode") == "live" else 0
            )
            self.enable_buf_chk.setChecked(
                bool(self.settings.get("enable_audio_buffer", True))
            )
            self.buf_spin.setValue(int(self.settings.get("buffer_seconds", 12)))
            self.buf_spin.setEnabled(self.enable_buf_chk.isChecked())
            self.live_spin.setValue(int(self.settings.get("live_max_seconds", 20)))
            self.gpu_auto_cpu_chk.setChecked(
                bool(self.settings.get("gpu_auto_cpu", True))
            )
            self.gpu_thresh_spin.setValue(int(self.settings.get("gpu_threshold", 65)))

            connected_devs = self.engine.all_speaker_names or list_speakers_safe()
            remembered = list(self.settings.get("remembered_devices") or [])
            if not remembered:
                remembered = list(connected_devs)
            for td in self.settings.get("target_devices") or []:
                if td and td not in remembered:
                    remembered.append(td)
            self.audio_outputs_list.set_devices(
                remembered,
                self.settings.get("target_devices", []),
                connected_devs,
                monitor_all=bool(self.settings.get("monitor_all_devices", True)),
            )
            self._saved_outputs_snapshot = (
                self.audio_outputs_list.get_remembered(),
                self.audio_outputs_list.get_selected(),
            )
            self._update_audio_outputs_collapse_header()
            self.dev_status_lbl.setText("")
            self.has_unsaved_settings = False
            self.unsaved_lbl.setText("")
            self.discard_changes_btn.hide()
            self.ytdlp_path_input.setText(self.settings.get("ytdlp_app_path", ""))
            self.ytdlp_quick_mp3_chk.setChecked(
                bool(self.settings.get("ytdlp_quick_download_mp3", True))
            )
        finally:
            self._suppress_unsaved = False
        self._rebuild_ai_models_ui()
        self._refresh_storage_summary()
        self._refresh_saved_clip_ui()
        self._update_gpu_indicator()
        self._refresh_gpu_info_async()

    def _has_unsaved_settings_changes(self) -> bool:
        snap = self._saved_settings_snapshot
        wf_styles = ["auto", "radial", "horizontal", "pulse_line", "bar", "dots"]
        cur_wf = wf_styles[max(0, self.wf_style_combo.currentIndex())]
        cur_hk = self.hk_input.text().strip().lower()
        saved_hk = str(snap.get("hotkey", "ctrl+alt+s")).strip().lower()
        cur_hk_mode = "live" if self.hk_mode_combo.currentIndex() == 1 else "buffer"
        cur_outputs = (
            self.audio_outputs_list.get_remembered(),
            self.audio_outputs_list.get_selected(),
        )
        checks = [
            (self.theme_combo.currentData() or "auto") != snap.get("theme", "auto"),
            (self.icon_color_combo.currentData() or "theme") != snap.get("icon_color", "theme"),
            (self.text_size_combo.currentData() or "small") != snap.get("text_size", "small"),
            cur_wf != snap.get("waveform_style", "auto"),
            self.wf_mirror_chk.isChecked() != bool(snap.get("waveform_mirror", False)),
            self.wf_idle_dots_chk.isChecked() != bool(snap.get("waveform_idle_dots", False)),
            self.wf_maxw_spin.value() != int(snap.get("waveform_max_width", 620)),
            round(float(self.wf_step_spin.value()), 1) != round(float(snap.get("waveform_step", 0.0) or 0.0), 1),
            self.save_win_size_chk.isChecked() != bool(snap.get("save_window_size", True)),
            self.startup_chk.isChecked() != bool(self._saved_startup_snapshot),
            self.start_tray_chk.isChecked() != bool(snap.get("start_minimized_to_tray", False)),
            self.min_tray_chk.isChecked() != bool(snap.get("minimize_to_tray", True)),
            self.notif_chk.isChecked() != bool(snap.get("show_notifications", True)),
            self.always_hud_chk.isChecked() != bool(snap.get("always_show_status_hud", True)),
            self.force_overlay_chk.isChecked() != bool(snap.get("force_overlay_notification", True)),
            self.ping_chk.isChecked() != bool(snap.get("play_ping_sound", True)),
            self.autocopy_chk.isChecked() != bool(snap.get("auto_copy", False)),
            (self.info_site_combo.currentData() or "auto") != snap.get("preferred_info_site", "auto"),
            bool(cur_hk) and cur_hk != saved_hk,
            cur_hk_mode != snap.get("hotkey_mode", "live"),
            self.ytdlp_path_input.text().strip() != str(snap.get("ytdlp_app_path", "")).strip(),
            self.ytdlp_quick_mp3_chk.isChecked() != bool(snap.get("ytdlp_quick_download_mp3", True)),
            cur_outputs != self._saved_outputs_snapshot,
            self.enable_buf_chk.isChecked() != bool(snap.get("enable_audio_buffer", True)),
            self.buf_spin.value() != int(snap.get("buffer_seconds", 12)),
            self.live_spin.value() != int(snap.get("live_max_seconds", 20)),
            self.gpu_auto_cpu_chk.isChecked() != bool(snap.get("gpu_auto_cpu", True)),
            self.gpu_thresh_spin.value() != int(snap.get("gpu_threshold", 65)),
        ]
        return any(checks)

    def _mark_unsaved(self, *_args):
        if getattr(self, "_suppress_unsaved", False):
            return
        self._apply_live_settings()
        dirty = self._has_unsaved_settings_changes()
        self.has_unsaved_settings = dirty
        if dirty:
            self.unsaved_lbl.setText("Previewing unsaved changes")
            self.discard_changes_btn.show()
        else:
            self.unsaved_lbl.setText("")
            self.discard_changes_btn.hide()

    def _apply_live_settings(self):
        old_devices = list(self.settings.get("target_devices", []))
        old_all = bool(self.settings.get("monitor_all_devices", True))
        old_buf = int(self.settings.get("buffer_seconds", 12))
        old_buf_en = bool(self.settings.get("enable_audio_buffer", True))

        self.settings["theme"] = self.theme_combo.currentData() or "auto"
        self._sync_edit_theme_btn()
        self.settings["icon_color"] = self.icon_color_combo.currentData() or "theme"
        self.settings["text_size"] = self.text_size_combo.currentData() or "small"
        wf_styles = ["auto", "radial", "horizontal", "pulse_line", "bar", "dots"]
        self.settings["waveform_style"] = wf_styles[max(0, self.wf_style_combo.currentIndex())]
        self.settings["waveform_mirror"] = self.wf_mirror_chk.isChecked()
        self.settings["waveform_idle_dots"] = self.wf_idle_dots_chk.isChecked()
        self.settings["waveform_max_width"] = self.wf_maxw_spin.value()
        self.settings["waveform_step"] = round(float(self.wf_step_spin.value()), 1)

        self.settings["save_window_size"] = self.save_win_size_chk.isChecked()
        self.settings["start_minimized_to_tray"] = self.start_tray_chk.isChecked()
        self.settings["minimize_to_tray"] = self.min_tray_chk.isChecked()
        self.settings["show_notifications"] = self.notif_chk.isChecked()
        self.settings["always_show_status_hud"] = self.always_hud_chk.isChecked()
        self.settings["force_overlay_notification"] = self.force_overlay_chk.isChecked()
        self.settings["play_ping_sound"] = self.ping_chk.isChecked()
        self.settings["auto_copy"] = self.autocopy_chk.isChecked()
        self.settings["preferred_info_site"] = self.info_site_combo.currentData() or "auto"
        self.settings["hotkey_mode"] = (
            "live" if self.hk_mode_combo.currentIndex() == 1 else "buffer"
        )
        self.settings["enable_audio_buffer"] = self.enable_buf_chk.isChecked()
        self.buf_spin.setEnabled(self.settings["enable_audio_buffer"])
        self.settings["buffer_seconds"] = self.buf_spin.value()
        self.settings["live_max_seconds"] = self.live_spin.value()
        self.settings["ytdlp_app_path"] = self.ytdlp_path_input.text().strip()
        self.settings["ytdlp_quick_download_mp3"] = self.ytdlp_quick_mp3_chk.isChecked()
        self.settings["gpu_auto_cpu"] = self.gpu_auto_cpu_chk.isChecked()
        self.settings["gpu_threshold"] = self.gpu_thresh_spin.value()

        remembered_devs = self.audio_outputs_list.get_remembered()
        checked_devs = self.audio_outputs_list.get_selected()
        self.settings["remembered_devices"] = remembered_devs
        if (remembered_devs, checked_devs) == getattr(self, "_saved_outputs_snapshot", None) and bool(
            self._saved_settings_snapshot.get("monitor_all_devices", False)
        ):
            self.settings["monitor_all_devices"] = True
            self.settings["target_devices"] = list(
                self._saved_settings_snapshot.get("target_devices", [])
            )
        else:
            self.settings["monitor_all_devices"] = False
            self.settings["target_devices"] = checked_devs

        self.apply_theme(force=False)
        self._update_gpu_indicator()
        self._refresh_identify_header()

        if (
            self.settings["target_devices"] != old_devices
            or self.settings["monitor_all_devices"] != old_all
            or self.settings["buffer_seconds"] != old_buf
            or self.settings["enable_audio_buffer"] != old_buf_en
        ):
            self._audio_reconfig_timer.start(250)

    def _apply_audio_reconfig_now(self):
        self.engine.reconfigure(
            self.settings.get("target_devices", []),
            self.settings.get("monitor_all_devices", True),
            self.settings.get("buffer_seconds", 12),
            enable_buffer=self.settings.get("enable_audio_buffer", True),
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
        self._apply_audio_reconfig_now()

        self._saved_settings_snapshot = copy.deepcopy(self.settings)
        self._saved_startup_snapshot = self.startup_chk.isChecked()
        self._saved_outputs_snapshot = (
            self.audio_outputs_list.get_remembered(),
            self.audio_outputs_list.get_selected(),
        )
        self.has_unsaved_settings = False
        self.discard_changes_btn.hide()
        self._refresh_identify_header()
        self.unsaved_lbl.setText("Saved!")
        QTimer.singleShot(
            2000,
            lambda: self.unsaved_lbl.setText("") if not self.has_unsaved_settings else None,
        )

    def _discard_unsaved_settings(self):
        self._audio_reconfig_timer.stop()
        prev_devices = list(self.settings.get("target_devices", []))
        prev_all = bool(self.settings.get("monitor_all_devices", True))
        prev_buf = int(self.settings.get("buffer_seconds", 12))
        prev_buf_en = bool(self.settings.get("enable_audio_buffer", True))

        self.settings = copy.deepcopy(self._saved_settings_snapshot)
        self._register_hotkey(self.settings.get("hotkey", "ctrl+alt+s"))
        set_startup_enabled(self._saved_startup_snapshot)

        if (
            self.settings.get("target_devices", []) != prev_devices
            or bool(self.settings.get("monitor_all_devices", True)) != prev_all
            or int(self.settings.get("buffer_seconds", 12)) != prev_buf
            or bool(self.settings.get("enable_audio_buffer", True)) != prev_buf_en
        ):
            self._apply_audio_reconfig_now()

        self._populate_settings_ui()
        self.apply_theme(force=True)
        self._refresh_identify_header()
        self.unsaved_lbl.setText("Changes discarded")
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
        self.settings = copy.deepcopy(DEFAULT_SETTINGS)
        save_settings(self.settings)
        self._saved_settings_snapshot = copy.deepcopy(self.settings)
        self._register_hotkey(self.settings["hotkey"])
        self._apply_audio_reconfig_now()
        self._disable_ai_checkbox_cleanly()
        self.is_pinned = False
        set_window_always_on_top(int(self.winId()), False)
        self._update_pin_button_ui()
        self.detail_page.set_volume(int(self.settings.get("preview_volume", 50)))
        self._populate_settings_ui()
        self.apply_theme(force=True)
        self._refresh_identify_header()
        self.unsaved_lbl.setText("Defaults restored")
        QTimer.singleShot(
            2000,
            lambda: self.unsaved_lbl.setText("") if not self.has_unsaved_settings else None,
        )

    def _refresh_devices_dropdown(self):
        connected = list_speakers_safe()
        self.engine.all_speaker_names = connected
        added = self.audio_outputs_list.merge_discovered(connected, auto_check_new=True)
        self._update_audio_outputs_collapse_header()
        if added:
            n = len(added)
            self.dev_status_lbl.setText(f"Found {n} new output{'s' if n != 1 else ''}")
            self._mark_unsaved()
        else:
            self.dev_status_lbl.setText("No new outputs found")
        QTimer.singleShot(3000, lambda: self.dev_status_lbl.setText(""))

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
            chosen = prompt_install_or_locate_ytdlp(self)
            if chosen:
                self.ytdlp_path_input.setText(chosen)

    def _on_ytdlp_path_picked(self, path: str):
        self.settings["ytdlp_app_path"] = path
        self._saved_settings_snapshot["ytdlp_app_path"] = path
        save_settings(self.settings)
        if hasattr(self, "ytdlp_path_input"):
            self._suppress_unsaved = True
            self.ytdlp_path_input.setText(path)
            self._suppress_unsaved = False

    def _on_detail_volume_changed(self, vol: int):
        vol = max(0, min(100, int(vol)))
        self.settings["preview_volume"] = vol
        self._saved_settings_snapshot["preview_volume"] = vol
        save_settings(self._saved_settings_snapshot)

    # --- OPTIONAL AI MODELS & STORAGE CLEAN-UP ---
    def _all_ai_model_entries(self) -> list[dict]:
        entries = [dict(m) for m in AI_MODEL_CATALOG]
        known = {e["filename"] for e in entries}

        for fn in list(self.settings.get("custom_ai_models", [])) + get_downloaded_ai_models():
            item_id = str(fn).strip()
            if not item_id or item_id in known:
                continue
            if is_whisper_model_id(item_id) or item_id.lower().endswith(AI_MODEL_EXTENSIONS):
                known.add(item_id)
                entries.append(
                    {
                        "filename": item_id,
                        "name": os.path.splitext(os.path.basename(item_id))[0],
                        "tag": "Custom",
                        "description": "User-added custom rescue model.",
                        "size_mb": 0,
                        "type": "Custom",
                        "url": "",
                    }
                )
        return entries

    def _rebuild_ai_models_ui(self):
        if not hasattr(self, "ai_models_container"):
            return

        engine_ok = is_ai_engine_installed()
        self.model_mgr.refresh_capabilities()
        if engine_ok:
            hw = "GPU Ready (CUDA)" if self.model_mgr.gpu_available else "CPU Mode"
            self.ai_engine_lbl.setText(f"AI Engine: Installed ({hw})")
            self.install_ai_engine_btn.hide()
        else:
            self.ai_engine_lbl.setText(
                "AI Engine: Not installed (optional, saves ~800 MB)"
            )
            self.install_ai_engine_btn.show()

        for btn in list(self._ai_model_radio_group.buttons()):
            self._ai_model_radio_group.removeButton(btn)
        while self.ai_models_container.count():
            it = self.ai_models_container.takeAt(0)
            if it and it.widget():
                it.widget().deleteLater()

        active_model = self.settings.get("ai_model", UVR_MODEL_NAME)
        for entry in self._all_ai_model_entries():
            fn = entry["filename"]
            downloaded = is_model_downloaded(fn)
            if downloaded:
                p = get_model_file_path(fn)
                if os.path.isdir(p):
                    fsize = sum(
                        os.path.getsize(os.path.join(r, f))
                        for r, _d, fs in os.walk(p)
                        for f in fs
                    )
                else:
                    fsize = os.path.getsize(p)
            else:
                fsize = 0

            row_frame = QFrame()
            row_frame.setObjectName("SongCard")
            row_lay = QHBoxLayout(row_frame)
            row_lay.setContentsMargins(10, 8, 10, 8)
            row_lay.setSpacing(10)

            radio = QRadioButton()
            radio.setEnabled(downloaded)
            radio.setChecked(downloaded and fn == active_model)
            radio.setToolTip(
                "Select active AI model" if downloaded else "Download first to select"
            )
            radio.toggled.connect(
                lambda checked, m=fn: self._on_select_ai_model(m) if checked else None
            )
            self._ai_model_radio_group.addButton(radio)
            row_lay.addWidget(radio, alignment=Qt.AlignmentFlag.AlignTop)

            info_col = QVBoxLayout()
            info_col.setSpacing(2)
            tag_txt = f"  •  {entry['tag']}" if entry.get("tag") else ""
            name_lbl = QLabel(f"{entry['name']}{tag_txt}")
            name_lbl.setStyleSheet("font-size: 12px; font-weight: 600;")
            desc_lbl = QLabel(entry.get("description", ""))
            desc_lbl.setObjectName("Muted")
            desc_lbl.setWordWrap(True)
            desc_lbl.setStyleSheet("font-size: 11px;")

            if downloaded:
                st_txt = f"Downloaded • {format_bytes(fsize)} • {fn}"
            else:
                est = f"~{entry['size_mb']} MB" if entry.get("size_mb") else "Custom"
                st_txt = f"Not downloaded ({est}) • {fn}"
            st_lbl = QLabel(st_txt)
            st_lbl.setObjectName("Muted")
            st_lbl.setStyleSheet("font-size: 10px;")

            info_col.addWidget(name_lbl)
            info_col.addWidget(desc_lbl)
            info_col.addWidget(st_lbl)
            row_lay.addLayout(info_col, 1)

            if downloaded:
                del_btn = QPushButton("Delete")
                del_btn.setObjectName("DangerBtn")
                del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                del_btn.clicked.connect(lambda checked=False, m=fn: self._delete_ai_model(m))
                row_lay.addWidget(del_btn)
            else:
                dl_btn = QPushButton("Download")
                dl_btn.setObjectName("PlatformBtn")
                dl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                dl_btn.clicked.connect(
                    lambda checked=False, m=fn, u=entry.get("url", ""): self._download_ai_model(m, u)
                )
                row_lay.addWidget(dl_btn)

            self.ai_models_container.addWidget(row_frame)

        self._update_ai_collapse_header()
        self._refresh_ai_footer_status()

    def _on_select_ai_model(self, filename: str):
        self.settings["ai_model"] = filename
        self._saved_settings_snapshot["ai_model"] = filename
        save_settings(self.settings)
        self.model_mgr.set_active_model(filename)
        self._refresh_ai_footer_status()

    def _download_ai_engine(self):
        worker = AiRuntimeDownloadWorker()
        dlg = DownloadProgressDialog(
            "Download Optional AI Engine",
            "Preparing to download optional AI rescue engine...",
            worker,
            parent=self,
        )
        dlg.exec()
        if dlg.succeeded:
            self.model_mgr.refresh_capabilities()
            self._rebuild_ai_models_ui()
            self._refresh_storage_summary()
            self._update_gpu_indicator()
            QMessageBox.information(
                self,
                "AI Engine Installed",
                "Optional AI Engine installed. You can now download any rescue model below.",
            )
        elif not dlg.was_cancelled and dlg.error_msg:
            QMessageBox.warning(self, "Download Failed", dlg.error_msg)

    def _download_ai_model(self, filename: str, url: str = ""):
        worker = ModelDownloadWorker(filename, url)
        dlg = DownloadProgressDialog(
            f"Downloading {filename}",
            f"Downloading AI model {filename}...",
            worker,
            parent=self,
        )
        dlg.exec()
        if dlg.succeeded:
            self._on_select_ai_model(filename)
            self._rebuild_ai_models_ui()
            self._refresh_storage_summary()
            self._update_gpu_indicator()
        elif not dlg.was_cancelled and dlg.error_msg:
            QMessageBox.warning(
                self, "Model Download Failed", f"Could not download {filename}:\n{dlg.error_msg}"
            )

    def _delete_ai_model(self, filename: str):
        reply = QMessageBox.question(
            self,
            "Delete AI Model",
            f"Delete '{filename}' to free disk space?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        path = get_model_file_path(filename)
        try:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            elif os.path.isfile(path):
                os.remove(path)
        except OSError as e:
            QMessageBox.warning(self, "Delete Failed", str(e))
            return

        self.model_mgr.unload()
        if self.settings.get("ai_model") == filename:
            remaining = get_downloaded_ai_models()
            if remaining:
                self._on_select_ai_model(remaining[0])
            else:
                self._disable_ai_checkbox_cleanly()
        self._rebuild_ai_models_ui()
        self._refresh_storage_summary()
        self._refresh_saved_clip_ui()

    def _add_custom_ai_model(self):
        picked, _ = QFileDialog.getOpenFileName(
            self,
            "Import Custom UVR AI Model",
            os.path.expanduser("~"),
            "UVR AI Models (*.onnx *.pth *.ckpt);;All Files (*.*)",
        )
        if not picked:
            return
        base = os.path.basename(picked)
        dest = os.path.join(MODELS_DIR, base)
        try:
            if os.path.abspath(picked) != os.path.abspath(dest):
                shutil.copy2(picked, dest)
        except Exception as e:
            QMessageBox.warning(self, "Import Failed", f"Could not copy model file:\n{e}")
            return

        customs = list(self.settings.get("custom_ai_models", []))
        if base not in customs:
            customs.append(base)
            self.settings["custom_ai_models"] = customs
            self._saved_settings_snapshot["custom_ai_models"] = list(customs)
        self._on_select_ai_model(base)
        self._rebuild_ai_models_ui()
        self._refresh_storage_summary()
        self._refresh_saved_clip_ui()
        QMessageBox.information(
            self, "Model Added", f"Custom model '{base}' imported and selected."
        )

    def _disable_ai_checkbox_cleanly(self):
        self.settings["use_ai_separation"] = False
        self._saved_settings_snapshot["use_ai_separation"] = False
        save_settings(self.settings)
        if hasattr(self, "quick_ai_chk"):
            self.quick_ai_chk.blockSignals(True)
            self.quick_ai_chk.setChecked(False)
            self.quick_ai_chk.blockSignals(False)
            self.ai_warn_banner.setVisible(False)
        if hasattr(self, "mini_window"):
            self.mini_window.set_ai_checked_silent(False)

    def _refresh_storage_summary(self):
        if not hasattr(self, "storage_summary_lbl"):
            return
        info = get_storage_breakdown(self.history)
        parts = [f"{len(info['models'])} AI model(s)", f"{len(info['all_cover_files'])} cover(s)"]
        if info.get("all_theme_photo_files"):
            parts.append(f"{len(info['all_theme_photo_files'])} theme photo(s)")
        if info["temp_bytes"] > 0:
            parts.append(f"{format_bytes(info['temp_bytes'])} temp")
        if info["ai_runtime_bytes"] > 0:
            parts.append(f"{format_bytes(info['ai_runtime_bytes'])} AI engine")
        self.storage_summary_lbl.setText(
            f"Total SongPulse data: {format_bytes(info['total_bytes'])}  •  " + ", ".join(parts)
        )

    def _open_storage_cleanup(self):
        dlg = StorageCleanupDialog(self.history, parent=self)
        dlg.exec()
        if dlg.cleared_history:
            self.history = []
            self.selected_song_ids.clear()
            self.result_card.hide()
            self.refresh_library()
        elif dlg.cleared_covers:
            self.refresh_library()
        if dlg.deleted_models or dlg.removed_ai_runtime:
            self.model_mgr.unload()
            self.model_mgr.refresh_capabilities()
            if not is_model_ready(self.settings.get("ai_model", UVR_MODEL_NAME)):
                remaining = [m for m in get_downloaded_ai_models() if is_model_ready(m)]
                if remaining:
                    self._on_select_ai_model(remaining[0])
                else:
                    self._disable_ai_checkbox_cleanly()
            self._rebuild_ai_models_ui()
            self._update_gpu_indicator()
        self._refresh_storage_summary()

    def _refresh_ai_footer_status(self):
        if not hasattr(self, "model_dot"):
            return
        engine_ok = is_ai_engine_installed()
        downloaded = get_downloaded_ai_models()
        active = self.settings.get("ai_model", UVR_MODEL_NAME)
        if not engine_ok:
            self.model_dot.setStyleSheet("color: #71717A; font-size: 10px;")
            self.model_status_lbl.setText("AI: Optional")
            self.ai_foot_widget.setToolTip(
                "Optional AI Rescue engine is not installed. Go to Settings -> AI Models."
            )
        elif not downloaded:
            self.model_dot.setStyleSheet("color: #71717A; font-size: 10px;")
            self.model_status_lbl.setText("AI: No model")
            self.ai_foot_widget.setToolTip(
                "No AI model downloaded. Download one in Settings -> AI Models."
            )
        else:
            mode = "GPU" if self.model_mgr.gpu_available else "CPU"
            model_short = active if is_model_downloaded(active) else downloaded[0]
            self.model_dot.setStyleSheet("color: #22C55E; font-size: 10px;")
            self.model_status_lbl.setText(f"AI Ready ({mode})")
            self.ai_foot_widget.setToolTip(f"AI Rescue Ready ({mode}) • {model_short}")

    def _start_model_preload(self, model_filename: str):
        self.model_dot.setStyleSheet("color: #F59E0B; font-size: 10px;")
        self.model_status_lbl.setText("Loading AI...")
        self.model_loader = ModelLoaderThread(self.model_mgr, model_filename)
        self.model_loader.loaded.connect(self._on_model_loaded)
        self.model_loader.failed.connect(self._on_model_failed)
        self.model_loader.start()

    # --- LIVE WAVEFORM & PERIODIC TICK ---
    def _tick_waveform(self):
        main_active = self.isVisible() and self.pages.currentIndex() == 0
        mini_active = hasattr(self, "mini_window") and self.mini_window.isVisible()
        if not main_active and not mini_active:
            return
        has_out = len(self.engine.monitored_names) > 0
        if has_out:
            bands, left_b, right_b, rms = self.engine.get_live_waveform_stereo(WAVEFORM_BARS)
        else:
            bands, left_b, right_b, rms = None, None, None, 0.0
        if main_active:
            self.pulse_btn.step_animation(
                bands,
                rms,
                has_outputs=has_out,
                left_bands=left_b,
                right_bands=right_b,
            )
        if mini_active:
            self.mini_window.pulse_btn.step_animation(
                bands,
                rms,
                has_outputs=has_out,
                left_bands=left_b,
                right_bands=right_b,
            )

    def _on_periodic_tick(self):
        if self.settings.get("theme", "auto") == "auto":
            self.apply_theme()
        if self.isVisible() and self.pages.currentIndex() == 2:
            self._refresh_gpu_info_async()

    # --- GPU SPEED INDICATOR & BENCHMARK ---
    def _refresh_gpu_info_async(self):
        if self._gpu_poll_busy:
            return
        self._gpu_poll_busy = True

        def _poll():
            try:
                info = get_nvidia_gpu_info()
            except Exception:
                info = None
            self.gpu_info_signal.emit(info)

        threading.Thread(target=_poll, daemon=True).start()

    def _on_gpu_info_ready(self, info):
        self._gpu_poll_busy = False
        self._cached_gpu_info = info
        self._update_gpu_indicator()

    def _update_gpu_indicator(self):
        if not hasattr(self, "gpu_live_lbl"):
            return
        provider = "GPU Ready (CUDA)" if self.model_mgr.gpu_available else "CPU Only"
        info = self._cached_gpu_info
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
        active = self.settings.get("ai_model", UVR_MODEL_NAME)
        if not is_model_ready(active):
            ready = [m for m in get_downloaded_ai_models() if is_model_ready(m)]
            if ready:
                active = ready[0]
            else:
                QMessageBox.information(
                    self,
                    "AI Model Required",
                    "Download an AI model in Settings -> AI Models before running the speed test.",
                )
                return
        self.bench_btn.setEnabled(False)
        self.bench_btn.setText("Testing...")
        self.bench_worker = BenchmarkWorker(self.model_mgr, model_filename=active)
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

    # --- DIRECT IN-APP GITHUB UPDATER ---
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
            self.upd_status_lbl.setText(
                f"Update available: v{latest} (installed: v{APP_VERSION})"
            )
            box = QMessageBox(self)
            box.setWindowTitle("Update Available")
            box.setIcon(QMessageBox.Icon.NoIcon)
            box.setIconPixmap(
                self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation).pixmap(36, 36)
            )
            box.setText(f"SongPulse v{latest} is available!")
            if info["notes"]:
                box.setInformativeText(info["notes"][:450])
            btn_update = None
            if info.get("has_direct_exe"):
                btn_update = box.addButton("Update Now", QMessageBox.ButtonRole.AcceptRole)
            btn_browser = box.addButton("View Release", QMessageBox.ButtonRole.ActionRole)
            box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
            if btn_update:
                box.setDefaultButton(btn_update)
            box.exec()

            clicked = box.clickedButton()
            if btn_update and clicked == btn_update:
                self._run_direct_update(info["download_url"], latest)
            elif clicked == btn_browser:
                webbrowser.open(info["release_url"])
        else:
            self.upd_status_lbl.setText(f"Up to date (v{APP_VERSION} • latest: v{latest})")

    def _run_direct_update(self, download_url: str, version_str: str):
        worker = AppUpdateWorker(download_url, version_str)
        dlg = DownloadProgressDialog(
            f"Updating SongPulse to v{version_str}",
            f"Downloading SongPulse v{version_str}...",
            worker,
            parent=self,
        )
        dlg.exec()
        if dlg.succeeded:
            self.quit_app(force=True)
        elif not dlg.was_cancelled and dlg.error_msg:
            err_box = QMessageBox(self)
            err_box.setWindowTitle("Update Failed")
            err_box.setIcon(QMessageBox.Icon.NoIcon)
            err_box.setIconPixmap(
                self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning).pixmap(36, 36)
            )
            err_box.setText(dlg.error_msg)
            err_box.exec()

    def _on_update_error(self, err):
        self.upd_btn.setEnabled(True)
        self.upd_btn.setText("Check for Updates")
        self.upd_status_lbl.setText(err)

    # --- SYSTEM TRAY & WINDOW STATE ---
    def _build_tray(self):
        self.tray = QSystemTrayIcon(self.app_icon, self)
        self.tray.setToolTip(f"{APP_NAME} (Background)")

        menu = QMenu()
        act_open = QAction("Open SongPulse", self)
        act_open.triggered.connect(self.show_and_focus)

        act_mini = QAction("Open Mini Window", self)
        act_mini.triggered.connect(self._enter_mini_mode)

        act_buf = QAction("Identify Last Audio", self)
        act_buf.triggered.connect(lambda: self.trigger_identify(mode="buffer"))

        act_live = QAction("Listen Live", self)
        act_live.triggered.connect(lambda: self.trigger_identify(mode="live"))

        act_quit = QAction("Quit", self)
        act_quit.triggered.connect(self.quit_app)

        menu.addAction(act_open)
        menu.addAction(act_mini)
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
        if hasattr(self, "mini_window") and self.mini_window.isVisible():
            self.mini_window.hide()
        self.setWindowState(
            (self.windowState() & ~Qt.WindowState.WindowMinimized)
            | Qt.WindowState.WindowActive
        )
        self.show()
        self.showNormal()
        self.raise_()
        self.activateWindow()
        QApplication.setActiveWindow(self)
        _force_focus_window(int(self.winId()))
        set_windows_titlebar_theme(int(self.winId()), bool(self.last_dark_state))
        if self.is_pinned:
            set_window_always_on_top(int(self.winId()), True)

    def _on_notification_clicked(self, song: dict):
        """Brings SongPulse cleanly into foreground focus on the song's detail view with active hover/glow."""
        if song:
            self._open_song_details(song)
        self.show_and_focus()
        # Re-assert foreground focus & mouse hover tracking right after popup closes
        QTimer.singleShot(35, self._focus_after_notification)

    def _focus_after_notification(self):
        self.show_and_focus()
        self.detail_page.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self.detail_page.update()

    def open_song_by_id(self, song_id: str):
        target = next((s for s in self.history if s.get("id") == song_id), None)
        if target is None and self.current_result_song and self.current_result_song.get("id") == song_id:
            target = self.current_result_song
        if target is None and self.history:
            target = self.history[0]
        if target:
            self._open_song_details(target)
        self.show_and_focus()
        QTimer.singleShot(35, self._focus_after_notification)

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
                if hasattr(self, "mini_window") and self.mini_window.isVisible():
                    QTimer.singleShot(0, self.mini_window.ensure_no_taskbar)
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
        if hasattr(self, "mini_window"):
            self.mini_window.hide()
            if hasattr(self.mini_window, "_taskbar_owner"):
                self.mini_window._taskbar_owner.close()
        self.tray.hide()
        QApplication.quit()
        if force:
            os._exit(0)

    # --- THEME & CUSTOM WAVEFORM ---
    def preview_theme_config(self, cfg: dict):
        self._preview_theme_override = copy.deepcopy(cfg) if cfg else None
        self.apply_theme(force=True)

    def stop_theme_preview(self):
        if self._preview_theme_override is not None:
            self._preview_theme_override = None
            self.apply_theme(force=True)

    def apply_theme(self, force: bool = False):
        if self._preview_theme_override is not None:
            cfg = self._preview_theme_override
            theme_key = cfg.get("id", "custom:__preview__")
        else:
            theme_key = self.settings.get("theme", "auto")
            sys_dark = is_windows_dark_mode()
            cfg = load_theme_config(theme_key, sys_dark)
        dark = bool(cfg.get("base_dark", True))

        wf_pref = self.settings.get("waveform_style", "auto")
        wf_theme = cfg.get("waveform", {})
        if self._preview_theme_override is not None:
            eff_style = wf_theme.get("style", "radial")
            eff_mirror = bool(wf_theme.get("mirror", False))
            eff_idle_dots = bool(wf_theme.get("idle_dots", False))
            eff_flip_hz = bool(wf_theme.get("flip_hz", eff_mirror))
        else:
            eff_style = wf_theme.get("style", "radial") if wf_pref == "auto" else wf_pref
            eff_mirror = bool(self.settings.get("waveform_mirror", False))
            eff_idle_dots = bool(self.settings.get("waveform_idle_dots", False))
            if wf_theme.get("teto_bow") and "flip_hz" in wf_theme:
                eff_flip_hz = bool(wf_theme.get("flip_hz", False))
            else:
                eff_flip_hz = bool(eff_mirror)
        eff_max_w = int(self.settings.get("waveform_max_width", 620))
        eff_step = float(self.settings.get("waveform_step", 0.0) or 0.0)
        icon_pref = self.settings.get("icon_color", "theme")
        font_scale = get_text_scale(self.settings.get("text_size", "small"))

        # Separate visual stylesheet signature from lightweight waveform & icon signatures
        import json as _json
        cfg_digest = hashlib.md5(
            _json.dumps(cfg, sort_keys=True, default=str).encode("utf-8", errors="ignore")
        ).hexdigest()
        theme_sig = (
            theme_key,
            dark,
            cfg_digest,
            font_scale,
        )
        wf_sig = (
            eff_style,
            eff_mirror,
            eff_idle_dots,
            eff_max_w,
            eff_step,
            wf_theme.get("color1", cfg.get("colors", {}).get("accent", "#3B82F6")),
            wf_theme.get("color2", cfg.get("colors", {}).get("accent_secondary", "#8B5CF6")),
            eff_flip_hz,
            bool(wf_theme.get("hair_ties", False)),
            bool(wf_theme.get("right_alt_white", False)),
            bool(wf_theme.get("teto_bow", False)),
        )
        ic1, ic2 = resolve_icon_colors(icon_pref, cfg)
        icon_changed = force or (self._last_icon_colors != (ic1, ic2))
        theme_changed = force or (theme_sig != self._last_theme_sig)
        wf_changed = force or (wf_sig != self._last_wf_sig)

        if not (theme_changed or wf_changed or icon_changed):
            return

        self._current_theme_cfg = cfg
        self.last_dark_state = dark
        app = QApplication.instance()

        if theme_changed:
            self._last_theme_sig = theme_sig
            self._active_font_scale = font_scale
            self.root_widget.set_background_config(cfg.get("background", {}))
            self.sidebar.set_sidebar_config(cfg.get("sidebar", {}))
            app.setFont(QFont("Segoe UI", max(9, int(round(10 * font_scale)))))
            app.setPalette(build_palette(dark, cfg))
            app.setStyleSheet(build_stylesheet(dark, self.chevrons, cfg, font_scale=font_scale))
            set_windows_titlebar_theme(int(self.winId()), dark)
            self.pulse_btn.set_dark(dark)
            self.detail_page.set_theme_dark(dark, theme_cfg=cfg)
            self.hud.set_theme(dark, cfg)
            self.song_popup.set_theme(dark, cfg)
            apply_font_scale_to_widget_tree(self, font_scale)
            apply_font_scale_to_widget_tree(self.hud, font_scale)
            apply_font_scale_to_widget_tree(self.song_popup, font_scale)
            if hasattr(self, "mini_window"):
                apply_font_scale_to_widget_tree(self.mini_window, font_scale)
            self._refresh_theme_icons()
            QTimer.singleShot(0, self._reposition_mini_mode_btn)
            if self.pages.currentIndex() == 1:
                self.refresh_library()

        if icon_changed:
            self._last_icon_colors = (ic1, ic2)
            themed_icon = create_app_icon(ic1, ic2)
            self.app_icon = themed_icon
            app.setWindowIcon(themed_icon)
            self.setWindowIcon(themed_icon)
            if hasattr(self, "mini_window"):
                self.mini_window.setWindowIcon(themed_icon)
            if hasattr(self, "tray"):
                self.tray.setIcon(themed_icon)
            if hasattr(self, "brand_icon"):
                self.brand_icon.setPixmap(create_app_pixmap(24, ic1, ic2))
            try:
                export_multi_res_ico(ICON_CACHE_FILE, ic1, ic2)
            except Exception:
                pass

        if wf_changed:
            self._last_wf_sig = wf_sig
            self.pulse_btn.configure_waveform(
                style=eff_style,
                mirror=eff_mirror,
                idle_dots=eff_idle_dots,
                max_width=eff_max_w,
                color1=wf_theme.get("color1", cfg.get("colors", {}).get("accent", "#3B82F6")),
                color2=wf_theme.get("color2", cfg.get("colors", {}).get("accent_secondary", "#8B5CF6")),
                flip_hz=eff_flip_hz,
                hair_ties=bool(wf_theme.get("hair_ties", False)),
                right_alt_white=bool(wf_theme.get("right_alt_white", False)),
                teto_bow=bool(wf_theme.get("teto_bow", False)),
                step=eff_step,
            )

        if (theme_changed or wf_changed) and hasattr(self, "mini_window"):
            wf_kwargs = {
                "style": eff_style,
                "mirror": eff_mirror,
                "idle_dots": eff_idle_dots,
                "max_width": eff_max_w,
                "color1": wf_theme.get("color1", cfg.get("colors", {}).get("accent", "#3B82F6")),
                "color2": wf_theme.get("color2", cfg.get("colors", {}).get("accent_secondary", "#8B5CF6")),
                "flip_hz": eff_flip_hz,
                "hair_ties": bool(wf_theme.get("hair_ties", False)),
                "right_alt_white": bool(wf_theme.get("right_alt_white", False)),
                "teto_bow": bool(wf_theme.get("teto_bow", False)),
                "step": eff_step,
            }
            self.mini_window.apply_theme_config(dark, cfg, wf_kwargs)

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
            box.setInformativeText("Save them before leaving?")
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
            self._rebuild_playlist_tabs()
            self.refresh_library()
        elif idx == 2:
            # Always collapse AI Models section when opening Settings
            self._set_ai_section_expanded(False)
            self._rebuild_ai_models_ui()
            self._refresh_storage_summary()
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
        prefix = "Reidentified on" if song.get("reidentified") else "Identified on"
        time_line = f"{prefix} {ts}" if ts else ""
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
            link_items.append((pname, icon, lambda _b=None, u=url: open_external_url(u)))

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

    # --- LIBRARY REWORK (PLAYLISTS, MANUAL REORDER, SORT, MULTI-SELECT) ---
    def _get_active_custom_playlist(self):
        if self.active_playlist_key in ("all", "liked"):
            return None
        return next(
            (p for p in self.playlists if p.get("id") == self.active_playlist_key),
            None,
        )

    def _rebuild_playlist_tabs(self):
        self.playlist_tabs_flow.clear()
        tabs = [("all", "Identified Songs"), ("liked", "Liked Songs")]
        for pl in self.playlists:
            tabs.append((pl["id"], pl.get("name", "Playlist")))

        for key, label in tabs:
            btn = QPushButton(label)
            btn.setObjectName("PlaylistTab")
            btn.setProperty("active", key == self.active_playlist_key)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, k=key: self._select_playlist_tab(k))
            self.playlist_tabs_flow.addWidget(btn)

        add_btn = QPushButton("+ New Playlist")
        add_btn.setObjectName("PlatformBtn")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.clicked.connect(self._create_new_playlist)
        self.playlist_tabs_flow.addWidget(add_btn)
        QTimer.singleShot(0, self.playlist_tabs_flow._sync_height)

    def _select_playlist_tab(self, key: str):
        self.active_playlist_key = key
        self.selected_song_ids.clear()
        pl = self._get_active_custom_playlist()
        is_custom = pl is not None
        self.pl_rename_btn.setVisible(is_custom)
        self.pl_del_btn.setVisible(is_custom)
        self.clear_all_btn.setVisible(key == "all")

        # Default sort mode: Manual Order for custom playlists, Date (Newest) for All/Liked
        self.lib_sort_combo.blockSignals(True)
        if is_custom:
            sort_map = {"manual": 0, "date_desc": 1, "date_asc": 2, "title": 3, "artist": 4, "genre": 5, "album": 6}
            self.lib_sort_combo.setCurrentIndex(sort_map.get(pl.get("sort_mode", "manual"), 0))
        elif self.lib_sort_combo.currentIndex() == 0:
            self.lib_sort_combo.setCurrentIndex(1)
        self.lib_sort_combo.blockSignals(False)

        self._rebuild_playlist_tabs()
        self.refresh_library()

    def _create_new_playlist(self) -> dict | None:
        name, ok = QInputDialog.getText(self, "New Playlist", "Playlist name:")
        if not ok or not name.strip():
            return None
        pl = {
            "id": hashlib.md5(f"{name}-{time.time()}".encode()).hexdigest()[:10],
            "name": name.strip(),
            "song_ids": [],
            "sort_mode": "manual",
        }
        self.playlists.append(pl)
        save_playlists(self.playlists)
        self._select_playlist_tab(pl["id"])
        return pl

    def _rename_active_playlist(self):
        pl = self._get_active_custom_playlist()
        if not pl:
            return
        name, ok = QInputDialog.getText(
            self, "Rename Playlist", "New name:", text=pl.get("name", "")
        )
        if ok and name.strip():
            pl["name"] = name.strip()
            save_playlists(self.playlists)
            self._rebuild_playlist_tabs()

    def _delete_active_playlist(self):
        pl = self._get_active_custom_playlist()
        if not pl:
            return
        reply = QMessageBox.question(
            self,
            "Delete Playlist",
            f"Delete playlist '{pl.get('name', '')}'? Songs will remain in Identified Songs.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.playlists = [p for p in self.playlists if p.get("id") != pl["id"]]
        save_playlists(self.playlists)
        self._select_playlist_tab("all")

    def _on_library_sort_changed(self, idx: int):
        pl = self._get_active_custom_playlist()
        if pl is not None:
            modes = ["manual", "date_desc", "date_asc", "title", "artist", "genre", "album"]
            pl["sort_mode"] = modes[max(0, min(idx, len(modes) - 1))]
            save_playlists(self.playlists)
        self.refresh_library()

    def _get_visible_library_songs(self) -> list[dict]:
        by_id = {s.get("id"): s for s in self.history if s.get("id")}
        pl = self._get_active_custom_playlist()

        if self.active_playlist_key == "liked":
            base = [s for s in self.history if s.get("liked")]
        elif pl is not None:
            base = [by_id[sid] for sid in pl.get("song_ids", []) if sid in by_id]
        else:
            base = list(self.history)

        query = self.search_input.text().strip().lower()
        if query:
            base = [
                s
                for s in base
                if query in s.get("title", "").lower()
                or query in s.get("artist", "").lower()
                or query in s.get("album", "").lower()
                or query in s.get("genre", "").lower()
            ]

        sort_idx = self.lib_sort_combo.currentIndex()
        if sort_idx == 0:  # Manual Order
            return base
        if sort_idx == 1:  # Date (Newest)
            return base
        if sort_idx == 2:  # Date (Oldest)
            return list(reversed(base))
        if sort_idx == 3:  # Title
            return sorted(base, key=lambda s: (s.get("title") or "").lower())
        if sort_idx == 4:  # Artist
            return sorted(base, key=lambda s: (s.get("artist") or "").lower())
        if sort_idx == 5:  # Genre
            return sorted(base, key=lambda s: (s.get("genre") or "zzz").lower())
        if sort_idx == 6:  # Album
            return sorted(base, key=lambda s: (s.get("album") or "zzz").lower())
        return base

    def _sync_lib_header_responsive(self):
        if not hasattr(self, "lib_sticky_header"):
            return
        avail_w = max(0, self.lib_sticky_header.width() - 44)
        count = len(self.selected_song_ids)
        if count > 0:
            need_full = self.lib_title_wrap.sizeHint().width() + self.batch_bar.sizeHint().width() + 24
            hide_title = avail_w > 0 and avail_w < need_full
            self.lib_title_wrap.setVisible(not hide_title)
            self.batch_sel_lbl.setVisible(avail_w == 0 or avail_w >= 540)
        else:
            self.lib_title_wrap.setVisible(True)
            self.batch_sel_lbl.setVisible(False)

    def _update_batch_ui(self):
        count = len(self.selected_song_ids)
        show_batch = count > 0
        show_clear_all = (not show_batch) and self.active_playlist_key == "all"

        self.lib_sticky_header.setUpdatesEnabled(False)
        try:
            # Hide outgoing widget first so both are never visible simultaneously
            if show_batch:
                self.clear_all_btn.hide()
                self.batch_sel_lbl.setText(f"{count} selected")
                self.batch_bar.show()
            else:
                self.batch_bar.hide()
                self.batch_sel_lbl.setText("")
                self.clear_all_btn.setVisible(show_clear_all)
            self._sync_lib_header_responsive()
        finally:
            self.lib_sticky_header.setUpdatesEnabled(True)

        self.selected_count_lbl.setText(f"{count} selected" if count > 0 else "")

        total_visible = len(self._lib_cards)
        self.select_all_chk.blockSignals(True)
        self.select_all_chk.setChecked(total_visible > 0 and count == total_visible)
        self.select_all_chk.blockSignals(False)

    def _on_card_selection_toggled(self, song_id: str, checked: bool):
        if checked:
            if song_id not in self.selected_song_ids:
                self.selected_song_ids.append(song_id)
        else:
            if song_id in self.selected_song_ids:
                self.selected_song_ids.remove(song_id)
        self._update_batch_ui()

    def _on_select_all_toggled(self, state: int):
        check_all = state == Qt.CheckState.Checked.value
        if check_all:
            for card in self._lib_cards:
                card.set_checked_silent(True)
                if card._song_id not in self.selected_song_ids:
                    self.selected_song_ids.append(card._song_id)
        else:
            self.selected_song_ids.clear()
            for card in self._lib_cards:
                card.set_checked_silent(False)
        self._update_batch_ui()

    def _toggle_song_liked(self, song_id: str):
        new_liked = None
        for s in self.history:
            if s.get("id") == song_id:
                new_liked = not bool(s.get("liked", False))
                s["liked"] = new_liked
                save_history(self.history)
                break
        if self.pages.currentIndex() == 1:
            if self.active_playlist_key == "liked":
                vbar = self.lib_scroll.verticalScrollBar()
                scroll_pos = vbar.value()
                self.refresh_library()
                QTimer.singleShot(0, lambda p=scroll_pos: vbar.setValue(p))
            elif new_liked is not None:
                for card in self._lib_cards:
                    if card._song_id == song_id:
                        card.set_liked(new_liked)
                        break

    def _get_song_listen_target(self, song: dict) -> tuple[str, str, str]:
        """Resolves (site_display_name, icon_key, url) using the user's preferred site setting."""
        pref = (self.settings.get("preferred_info_site") or "auto").lower().strip()
        site_names = dict(PREFERRED_INFO_SITES)
        links = build_song_platform_links(song)
        links_by_key = {ikey: (lbl, url) for lbl, ikey, url in links}

        if pref != "auto":
            site_label = site_names.get(pref, "YouTube")
            icon_key = pref if pref in self.p_icons else "web"
            if pref == "youtube" and song.get("youtube_video_url"):
                url = song["youtube_video_url"]
            elif pref in links_by_key:
                url = links_by_key[pref][1]
            else:
                q = f"{song.get('artist', '')} {song.get('title', '')}".strip()
                url = build_site_url("song", q, pref, get_song_link(song))
            return site_label, icon_key, url

        if song.get("youtube_video_url"):
            return "YouTube", "youtube", song["youtube_video_url"]
        if song.get("shazam_url"):
            return "Shazam", "shazam", song["shazam_url"]
        if links:
            lbl, ikey, url = links[0]
            return site_names.get(ikey, lbl), ikey, url
        q = f"{song.get('artist', '')} {song.get('title', '')}".strip()
        return "YouTube", "youtube", build_site_url("song", q, "youtube", "")

    def _show_song_context_menu(self, song: dict, global_pos):
        if not self.isActiveWindow():
            self.raise_()
            self.activateWindow()
            _force_focus_window(int(self.winId()))
            QTimer.singleShot(
                15,
                lambda s=song, pos=global_pos: self._open_song_context_menu_now(s, pos),
            )
            return
        self._open_song_context_menu_now(song, global_pos)

    def _open_song_context_menu_now(self, song: dict, global_pos):
        song_id = song.get("id", "")
        live_song = next((s for s in self.history if s.get("id") == song_id), song)
        liked = bool(live_song.get("liked", False))
        icon_col = "#D4D4D8" if self.last_dark_state else "#475569"

        menu = ScrollThroughMenu(self.lib_scroll, self)
        menu.setMinimumWidth(220)

        like_icon = create_ui_icon(
            "heart_filled" if liked else "heart",
            "#F43F5E" if liked else icon_col,
            14,
        )
        act_like = QAction(like_icon, "Unlike" if liked else "Like", menu)
        act_like.triggered.connect(lambda checked=False, sid=song_id: self._toggle_song_liked(sid))
        menu.addAction(act_like)

        site_label, site_icon_key, listen_url = self._get_song_listen_target(live_song)
        act_listen = QAction(
            self.p_icons.get(site_icon_key, self.p_icons["web"]),
            f"Listen in {site_label}",
            menu,
        )
        act_listen.setEnabled(bool(listen_url))
        act_listen.triggered.connect(
            lambda checked=False, u=listen_url: open_external_url(u) if u else None
        )
        menu.addAction(act_listen)

        pl_menu = ScrollThroughMenu(self.lib_scroll, menu, install_app_filter=False)
        pl_menu.setTitle("Add to Playlist")
        pl_menu.setIcon(create_ui_icon("playlist_add", icon_col, 14))
        pl_menu.setMinimumWidth(185)
        menu.addMenu(pl_menu)
        if self.playlists:
            for pl in self.playlists:
                in_pl = song_id in pl.get("song_ids", [])
                pname = pl.get("name", "Playlist")
                act_pl = QAction(f"✓  {pname}" if in_pl else pname, pl_menu)
                if in_pl:
                    act_pl.setEnabled(False)
                else:
                    act_pl.triggered.connect(
                        lambda checked=False, sid=song_id, pid=pl["id"]: self._add_song_to_playlist(sid, pid)
                    )
                pl_menu.addAction(act_pl)
            pl_menu.addSeparator()

        act_new_pl = QAction("+ Create New Playlist...", pl_menu)
        act_new_pl.triggered.connect(
            lambda checked=False, sid=song_id: self._create_playlist_with_song(sid)
        )
        pl_menu.addAction(act_new_pl)

        menu.addSeparator()
        act_dl = QAction(create_ui_icon("download", icon_col, 14), "Download", menu)
        act_dl.triggered.connect(
            lambda checked=False, s=live_song: self._download_single_song(s)
        )
        menu.addAction(act_dl)

        def _on_menu_hide():
            HistoryRowCard._suppress_next_card_click = True
            HistoryRowCard._suppress_until = time.monotonic() + 0.18

        menu.aboutToHide.connect(_on_menu_hide)
        HistoryRowCard._context_menu_open = True
        HistoryRowCard._suppress_next_card_click = False
        chosen = None
        try:
            chosen = menu.exec(global_pos)
        finally:
            HistoryRowCard._context_menu_open = False
            if chosen is not None:
                HistoryRowCard._suppress_next_card_click = False
            else:
                HistoryRowCard._suppress_next_card_click = True
                HistoryRowCard._suppress_until = time.monotonic() + 0.18

    def _add_song_to_playlist(self, song_id: str, playlist_id: str):
        pl = next((p for p in self.playlists if p.get("id") == playlist_id), None)
        if not pl or not song_id:
            return
        ids = list(pl.get("song_ids", []))
        if song_id not in ids:
            ids.append(song_id)
            pl["song_ids"] = ids
            save_playlists(self.playlists)
        self.hud.show_message(
            f"Added to '{pl.get('name', 'Playlist')}'",
            dark=bool(self.last_dark_state),
            dot_color="#22C55E",
            auto_hide_ms=2200,
        )
        if self.active_playlist_key == playlist_id:
            self.refresh_library()

    def _create_playlist_with_song(self, song_id: str):
        if not song_id:
            return
        name, ok = QInputDialog.getText(self, "New Playlist", "Playlist name:")
        if not ok or not name.strip():
            return
        pl = {
            "id": hashlib.md5(f"{name}-{time.time()}".encode()).hexdigest()[:10],
            "name": name.strip(),
            "song_ids": [song_id],
            "sort_mode": "manual",
        }
        self.playlists.append(pl)
        save_playlists(self.playlists)
        self._rebuild_playlist_tabs()
        self.hud.show_message(
            f"Added to new playlist '{pl['name']}'",
            dark=bool(self.last_dark_state),
            dot_color="#22C55E",
            auto_hide_ms=2400,
        )

    def _download_single_song(self, song: dict):
        if not song:
            return
        exe_path = self._ensure_ytdlp_exe_path()
        if not exe_path:
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            urls = self._resolve_songs_for_ytdlp([song])
        finally:
            QApplication.restoreOverrideCursor()

        if not urls:
            QMessageBox.warning(self, "No Link Found", "Could not find a YouTube link for this song.")
            return

        quick_mp3 = bool(self.settings.get("ytdlp_quick_download_mp3", True))
        QApplication.clipboard().setText(urls[0])
        try:
            launch_ytdlp_downloader(
                exe_path,
                urls,
                playlist_name="",
                quick_mp3=quick_mp3,
                on_complete_cb=lambda folder, cnt, pl: self.ytdlp_download_finished.emit(folder, cnt, pl),
            )
            title = song.get("title") or "song"
            self.hud.show_message(
                f"Downloading '{title}' with yt-dlp Downloader",
                dark=bool(self.last_dark_state),
                dot_color="#22C55E",
                auto_hide_ms=3400,
            )
        except Exception as e:
            QMessageBox.warning(self, "Launch Failed", f"Could not open yt-dlp Downloader:\n{e}")

    def _move_playlist_song(self, song_id: str, delta: int):
        pl = self._get_active_custom_playlist()
        if not pl:
            return
        ids = list(pl.get("song_ids", []))
        if song_id not in ids:
            return
        idx = ids.index(song_id)
        new_idx = max(0, min(len(ids) - 1, idx + delta))
        if new_idx == idx:
            return
        ids.insert(new_idx, ids.pop(idx))
        pl["song_ids"] = ids
        save_playlists(self.playlists)
        self.refresh_library()

    def _drop_reorder_playlist_song(self, dragged_id: str, target_id: str):
        pl = self._get_active_custom_playlist()
        if not pl:
            return
        ids = list(pl.get("song_ids", []))
        if dragged_id in ids and target_id in ids:
            ids.remove(dragged_id)
            t_idx = ids.index(target_id)
            ids.insert(t_idx, dragged_id)
            pl["song_ids"] = ids
            save_playlists(self.playlists)
            self.refresh_library()

    def _show_batch_add_playlist_menu(self):
        if not self.selected_song_ids:
            return
        menu = ScrollThroughMenu(self.lib_scroll, self)
        act_like = QAction("Liked Songs", menu)
        act_like.triggered.connect(self._add_selected_to_liked)
        menu.addAction(act_like)

        if self.playlists:
            menu.addSeparator()
            for pl in self.playlists:
                act = QAction(pl.get("name", "Playlist"), menu)
                act.triggered.connect(
                    lambda checked=False, pid=pl["id"]: self._add_selected_to_playlist(pid)
                )
                menu.addAction(act)

        menu.addSeparator()
        act_new = QAction("+ Create New Playlist...", menu)
        act_new.triggered.connect(self._create_playlist_with_selected)
        menu.addAction(act_new)
        menu.exec(self.batch_add_pl_btn.mapToGlobal(self.batch_add_pl_btn.rect().bottomLeft()))

    def _add_selected_to_liked(self):
        for s in self.history:
            if s.get("id") in self.selected_song_ids:
                s["liked"] = True
        save_history(self.history)
        self.selected_song_ids.clear()
        self.refresh_library()

    def _add_selected_to_playlist(self, playlist_id: str):
        pl = next((p for p in self.playlists if p.get("id") == playlist_id), None)
        if not pl:
            return
        ids = list(pl.get("song_ids", []))
        for sid in self.selected_song_ids:
            if sid and sid not in ids:
                ids.append(sid)
        pl["song_ids"] = ids
        save_playlists(self.playlists)
        self.selected_song_ids.clear()
        self.refresh_library()

    def _create_playlist_with_selected(self):
        selected_copy = list(self.selected_song_ids)
        pl = self._create_new_playlist()
        if pl and selected_copy:
            pl["song_ids"] = selected_copy
            save_playlists(self.playlists)
            self.refresh_library()

    def _clear_selected_songs(self):
        if not self.selected_song_ids:
            return
        n = len(self.selected_song_ids)
        pl = self._get_active_custom_playlist()
        if pl is not None:
            pl["song_ids"] = [sid for sid in pl.get("song_ids", []) if sid not in self.selected_song_ids]
            save_playlists(self.playlists)
            self.selected_song_ids.clear()
            self.refresh_library()
            return

        if self.active_playlist_key == "liked":
            for s in self.history:
                if s.get("id") in self.selected_song_ids:
                    s["liked"] = False
            save_history(self.history)
            self.selected_song_ids.clear()
            self.refresh_library()
            return

        reply = QMessageBox.question(
            self,
            "Clear Selected Songs",
            f"Remove {n} selected song(s) from your library?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.history = [s for s in self.history if s.get("id") not in self.selected_song_ids]
        for p in self.playlists:
            p["song_ids"] = [sid for sid in p.get("song_ids", []) if sid not in self.selected_song_ids]
        save_history(self.history)
        save_playlists(self.playlists)
        self.selected_song_ids.clear()
        self.refresh_library()

    def _ensure_ytdlp_exe_path(self) -> str:
        exe_path = (self.settings.get("ytdlp_app_path") or "").strip()
        if not exe_path or not os.path.exists(exe_path):
            detected = find_default_ytdlp_app()
            if detected:
                exe_path = detected
                self._on_ytdlp_path_picked(exe_path)
            else:
                chosen = prompt_install_or_locate_ytdlp(self)
                if not chosen:
                    return ""
                exe_path = chosen
                self._on_ytdlp_path_picked(exe_path)
        return exe_path

    def _resolve_songs_for_ytdlp(self, songs: list[dict]) -> list[str]:
        urls = []
        changed = False
        for s in songs:
            u, updates = resolve_ytdlp_download_url(s)
            if updates:
                changed = True
            if u:
                urls.append(u)
        if changed:
            save_history(self.history)
        return urls

    def _download_active_playlist(self):
        songs = self._get_visible_library_songs()
        if not songs:
            QMessageBox.information(self, "Empty Playlist", "There are no songs in this playlist to download.")
            return

        exe_path = self._ensure_ytdlp_exe_path()
        if not exe_path:
            return

        pl = self._get_active_custom_playlist()
        if pl is not None:
            pl_name = (pl.get("name") or "Playlist").strip() or "Playlist"
        elif self.active_playlist_key == "liked":
            pl_name = "Liked Songs"
        else:
            pl_name = "Identified Songs"

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            urls = self._resolve_songs_for_ytdlp(songs)
        finally:
            QApplication.restoreOverrideCursor()

        if not urls:
            QMessageBox.warning(self, "No Links", "Could not resolve YouTube links for this playlist.")
            return

        quick_mp3 = bool(self.settings.get("ytdlp_quick_download_mp3", True))
        QApplication.clipboard().setText("\n".join(urls))
        try:
            launch_ytdlp_downloader(
                exe_path,
                urls,
                playlist_name=pl_name,
                quick_mp3=quick_mp3,
                on_complete_cb=lambda folder, cnt, pl: self.ytdlp_download_finished.emit(folder, cnt, pl),
            )
            self.hud.show_message(
                f"Downloading playlist '{pl_name}' ({len(urls)} song(s)) with yt-dlp Downloader",
                dark=bool(self.last_dark_state),
                dot_color="#22C55E",
                auto_hide_ms=3400,
            )
        except Exception as e:
            QMessageBox.warning(self, "Launch Failed", f"Could not open yt-dlp Downloader:\n{e}")

    def _on_ytdlp_download_finished(self, saved_folder: str, count: int, playlist_name: str):
        show_download_complete_popup(self, saved_folder, count, playlist_name)

    def _batch_download_selected(self):
        if not self.selected_song_ids:
            return
        exe_path = self._ensure_ytdlp_exe_path()
        if not exe_path:
            return

        by_id = {s.get("id"): s for s in self.history if s.get("id")}
        songs = [by_id[sid] for sid in self.selected_song_ids if sid in by_id]

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            urls = self._resolve_songs_for_ytdlp(songs)
        finally:
            QApplication.restoreOverrideCursor()

        if not urls:
            QMessageBox.warning(self, "No Links", "Could not resolve YouTube links for selected songs.")
            return

        quick_mp3 = bool(self.settings.get("ytdlp_quick_download_mp3", True))
        QApplication.clipboard().setText("\n".join(urls))
        try:
            launch_ytdlp_downloader(
                exe_path,
                urls,
                playlist_name="",
                quick_mp3=quick_mp3,
                on_complete_cb=lambda folder, cnt, pl: self.ytdlp_download_finished.emit(folder, cnt, pl),
            )
            self.hud.show_message(
                f"Batch downloading {len(urls)} song(s) in selected order with yt-dlp Downloader",
                dark=bool(self.last_dark_state),
                dot_color="#22C55E",
                auto_hide_ms=3400,
            )
        except Exception as e:
            QMessageBox.warning(self, "Launch Failed", f"Could not open yt-dlp Downloader:\n{e}")

    def refresh_library(self):
        filtered = self._get_visible_library_songs()
        self.lib_count_lbl.setText(f"({len(filtered)})")

        while self.lib_list_layout.count():
            item = self.lib_list_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self._lib_cards.clear()

        visible_ids = {s.get("id") for s in filtered}
        self.selected_song_ids = [sid for sid in self.selected_song_ids if sid in visible_ids]

        if not filtered:
            hk = self.settings.get("hotkey", "ctrl+alt+s").upper()
            if self.active_playlist_key == "liked":
                msg = "No liked songs yet. Click ♡ on any song to add it here."
            elif self._get_active_custom_playlist() is not None:
                msg = "This playlist is empty. Select songs in Identified Songs and click Add to Playlist."
            else:
                msg = f"No saved songs yet. Press {hk} when you hear a song!"
            empty = QLabel(msg)
            empty.setObjectName("Muted")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("padding: 40px; font-size: 13px;")
            self.lib_list_layout.addWidget(empty)
            self._update_batch_ui()
            return

        pl = self._get_active_custom_playlist()
        allow_reorder = pl is not None and self.lib_sort_combo.currentIndex() == 0
        del_tip = (
            "Remove from playlist"
            if pl is not None
            else ("Remove from Liked Songs" if self.active_playlist_key == "liked" else "Remove from library")
        )
        details_icon = getattr(self, "details_ui_icon", create_ui_icon("details", "#D4D4D8", 13))
        vp_w = self.lib_scroll.viewport().width() if hasattr(self, "lib_scroll") else 0
        init_compact = bool(vp_w > 0 and (vp_w - 52) < 680)

        for song in filtered:
            sid = song.get("id", "")
            card = HistoryRowCard(
                song,
                self.p_icons,
                details_icon,
                on_details=self._open_song_details,
                on_delete=self.delete_history_item,
                checked=sid in self.selected_song_ids,
                allow_reorder=allow_reorder,
                delete_tooltip=del_tip,
                initial_compact=init_compact,
            )
            card.selection_toggled.connect(self._on_card_selection_toggled)
            card.like_toggled.connect(self._toggle_song_liked)
            card.context_menu_requested.connect(self._show_song_context_menu)
            if allow_reorder:
                card.move_requested.connect(self._move_playlist_song)
                card.drop_reorder.connect(self._drop_reorder_playlist_song)
            self.lib_list_layout.addWidget(card)
            self._lib_cards.append(card)

        self._update_batch_ui()
        font_scale = getattr(self, "_active_font_scale", 1.0)
        if abs(font_scale - 1.0) >= 0.01:
            for i in range(self.lib_list_layout.count()):
                w = self.lib_list_layout.itemAt(i).widget()
                if w is not None:
                    apply_font_scale_to_widget_tree(w, font_scale)

    def _open_song_details(self, song):
        self.detail_page.load_song(
            song, dark=bool(self.last_dark_state), theme_cfg=self._current_theme_cfg
        )
        font_scale = getattr(self, "_active_font_scale", 1.0)
        if abs(font_scale - 1.0) >= 0.01:
            apply_font_scale_to_widget_tree(self.detail_page, font_scale)
        self.pages.setCurrentIndex(3)
        for i, btn in enumerate(self.nav_buttons):
            btn.setProperty("active", i == 1)
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
        pl = self._get_active_custom_playlist()
        if pl is not None:
            pl["song_ids"] = [sid for sid in pl.get("song_ids", []) if sid != song_id]
            save_playlists(self.playlists)
            self.refresh_library()
            return
        if self.active_playlist_key == "liked":
            for s in self.history:
                if s.get("id") == song_id:
                    s["liked"] = False
            save_history(self.history)
            self.refresh_library()
            return
        self.history = [s for s in self.history if s.get("id") != song_id]
        for p in self.playlists:
            p["song_ids"] = [sid for sid in p.get("song_ids", []) if sid != song_id]
        save_history(self.history)
        save_playlists(self.playlists)
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
            self.selected_song_ids.clear()
            save_history(self.history)
            self.refresh_library()

    # --- IDENTIFICATION WORKFLOW ---
    def _on_model_loaded(self, cuda_ok, model_name=""):
        self._refresh_ai_footer_status()
        self._update_gpu_indicator()

    def _on_model_failed(self, err):
        self.model_dot.setStyleSheet("color: #EF4444; font-size: 10px;")
        self.model_status_lbl.setText("AI unavailable")
        self.ai_foot_widget.setToolTip(f"AI Rescue unavailable:\n{err}")
        try:
            from .config import DATA_DIR

            with open(os.path.join(DATA_DIR, "ai_error.log"), "w", encoding="utf-8") as f:
                f.write(str(err))
        except Exception:
            pass

    def _on_quick_ai_toggled(self, state):
        enabled = state == Qt.CheckState.Checked.value
        if enabled:
            active = self.settings.get("ai_model", UVR_MODEL_NAME)
            ready = [m for m in get_downloaded_ai_models() if is_model_ready(m)]
            if not is_model_ready(active) and not ready:
                self._disable_ai_checkbox_cleanly()
                box = QMessageBox(self)
                box.setWindowTitle("AI Rescue Model Not Ready")
                box.setIcon(QMessageBox.Icon.Information)
                box.setText("No local AI rescue model is downloaded yet.")
                box.setInformativeText(
                    "Download a model (De-Reverb, De-Noise, Crowd, or Whisper Lyrics) in Settings -> AI Models."
                )
                open_btn = box.addButton("Open AI Settings", QMessageBox.ButtonRole.AcceptRole)
                box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
                box.exec()
                if box.clickedButton() == open_btn:
                    self.switch_page(2)
                    self._set_ai_section_expanded(True)
                return
            if not is_model_ready(active) and ready:
                self._on_select_ai_model(ready[0])

        self.settings["use_ai_separation"] = enabled
        self._saved_settings_snapshot["use_ai_separation"] = enabled
        self.ai_warn_banner.setVisible(enabled)
        if hasattr(self, "mini_window"):
            self.mini_window.set_ai_checked_silent(enabled)
        save_settings(self.settings)

    def _on_mini_ai_toggled(self, enabled: bool):
        if hasattr(self, "quick_ai_chk") and self.quick_ai_chk.isChecked() != enabled:
            self.quick_ai_chk.setChecked(enabled)

    def _enter_mini_mode(self):
        if not hasattr(self, "mini_window"):
            return
        self.hide()
        self.mini_window.ensure_no_taskbar()
        self.mini_window.setWindowIcon(self.app_icon)
        self.mini_window.show()
        self.mini_window.raise_()
        self.mini_window.activateWindow()
        set_windows_titlebar_theme(int(self.mini_window.winId()), bool(self.last_dark_state))
        set_window_always_on_top(int(self.mini_window.winId()), self.mini_window.is_pinned)
        QTimer.singleShot(0, self.mini_window.ensure_no_taskbar)

    def _exit_mini_mode(self):
        if hasattr(self, "mini_window"):
            self.mini_window.hide()
        self.show_and_focus()

    def _save_recent_clip_for_testing(self):
        if self.is_processing:
            return
        if not self.engine.monitored_names:
            self.live_status_lbl.setText("No audio output selected. Pick an output in Settings.")
            return
        buf_s = max(5, int(self.settings.get("buffer_seconds", 12)))
        if not self.engine.dump_recent_to_wav(SAVED_CLIP_WAV, seconds=buf_s):
            self.live_status_lbl.setText("No recent audio found to save. Make sure audio is playing.")
            return
        stamp = time.strftime("%I:%M:%S %p").lstrip("0")
        self._saved_clip_meta = f"Saved {buf_s}s ({stamp})"
        self._refresh_saved_clip_ui()
        self.live_status_lbl.setText(
            f"Saved last {buf_s}s clip. Pick any AI model below to test without losing the audio."
        )

    def _clear_saved_clip(self):
        if os.path.isfile(SAVED_CLIP_WAV):
            try:
                os.remove(SAVED_CLIP_WAV)
            except OSError:
                pass
        self._saved_clip_meta = ""
        self._refresh_saved_clip_ui()

    def _refresh_saved_clip_ui(self):
        if not hasattr(self, "saved_clip_bar"):
            return
        exists = os.path.isfile(SAVED_CLIP_WAV)
        self.saved_clip_bar.setVisible(exists)
        if not exists:
            return

        buf_s = int(self.settings.get("buffer_seconds", 12))
        self.saved_clip_lbl.setText(self._saved_clip_meta or f"Saved {buf_s}s clip")

        prev_data = self.saved_clip_model_combo.currentData()
        self.saved_clip_model_combo.blockSignals(True)
        self.saved_clip_model_combo.clear()

        ready_models = [m for m in get_downloaded_ai_models() if is_model_ready(m)]
        if len(ready_models) > 1:
            self.saved_clip_model_combo.addItem(
                f"All Downloaded AI Models ({len(ready_models)})", "__all__"
            )

        catalog_names = {e["filename"]: e["name"] for e in AI_MODEL_CATALOG}
        for m in ready_models:
            disp = catalog_names.get(m, os.path.splitext(os.path.basename(m))[0])
            self.saved_clip_model_combo.addItem(disp, m)

        self.saved_clip_model_combo.addItem("Original Audio (No AI)", "__raw__")

        if prev_data:
            for idx in range(self.saved_clip_model_combo.count()):
                if self.saved_clip_model_combo.itemData(idx) == prev_data:
                    self.saved_clip_model_combo.setCurrentIndex(idx)
                    break
        self.saved_clip_model_combo.blockSignals(False)

    def _test_saved_clip(self):
        if not os.path.isfile(SAVED_CLIP_WAV):
            self._refresh_saved_clip_ui()
            return
        sel = self.saved_clip_model_combo.currentData() or "__raw__"
        if sel == "__all__":
            models = [m for m in get_downloaded_ai_models() if is_model_ready(m)]
            if not models:
                models = ["__raw__"]
        else:
            models = [sel]
        self.trigger_identify(mode="saved_clip", test_models=models)

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
        if mode == "buffer" and not self.settings.get("enable_audio_buffer", True):
            mode = "live"
        self.trigger_identify(mode=mode, from_hotkey=True)

    def trigger_identify(self, mode="live", from_hotkey=False, test_models=None):
        if self.is_processing:
            return
        if mode == "buffer" and not self.settings.get("enable_audio_buffer", True):
            mode = "live"
        if mode != "saved_clip" and not self.engine.monitored_names:
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
        self.cancel_evt = threading.Event()
        self.pulse_btn.setEnabled(False)
        self.pulse_btn.set_scanning(True)
        scan_label = "Listening..." if mode == "live" else "Scanning..."
        self.pulse_btn.setText(scan_label)
        self.buffer_btn.setEnabled(False)
        self.save_clip_btn.setEnabled(False)
        self.test_saved_clip_btn.setEnabled(False)
        self.clear_saved_clip_btn.setEnabled(False)
        self.quick_ai_chk.setEnabled(False)
        self.cancel_btn.show()
        if hasattr(self, "mini_window"):
            self.mini_window.pulse_btn.setEnabled(False)
            self.mini_window.pulse_btn.set_scanning(True)
            self.mini_window.pulse_btn.setText(scan_label)
            self.mini_window.buffer_btn.setEnabled(False)
            self.mini_window.ai_chk.setEnabled(False)
            self.mini_window.cancel_btn.show()

        if (
            self.settings.get("always_show_status_hud", True)
            or from_hotkey
            or not self.isVisible()
        ):
            label = "SongPulse • Listening live..." if mode == "live" else "SongPulse • Scanning audio..."
            self.hud.show_message(label, dark=bool(self.last_dark_state), dot_color="#3B82F6")

        self.worker = IdentifyWorker(
            self.engine,
            self.model_mgr,
            self.settings,
            mode=mode,
            cancel_evt=self.cancel_evt,
            custom_wav_path=SAVED_CLIP_WAV if mode == "saved_clip" else "",
            test_models=test_models,
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
        if self.worker is not None and hasattr(self.worker, "request_cancel"):
            self.worker.request_cancel()
        self.cancel_btn.setEnabled(False)
        if hasattr(self, "mini_window"):
            self.mini_window.cancel_btn.setEnabled(False)
        self.live_status_lbl.setText("Cancelling...")

    def _on_worker_status(self, text):
        if self.cancel_evt.is_set():
            return
        self.live_status_lbl.setText(text)
        if self.hud.isVisible() or self.settings.get("always_show_status_hud", True):
            self.hud.show_message(text, dark=bool(self.last_dark_state), dot_color="#3B82F6")

    def _is_song_in_library(self, new_song: dict) -> bool:
        title = (new_song.get("title") or "").strip().lower()
        artist = (new_song.get("artist") or "").strip().lower()
        isrc = (new_song.get("isrc") or "").strip().lower()
        shazam_url = (new_song.get("shazam_url") or "").strip().lower()
        for existing in self.history:
            if isrc and (existing.get("isrc") or "").strip().lower() == isrc:
                return True
            if shazam_url and (existing.get("shazam_url") or "").strip().lower() == shazam_url:
                return True
            if (
                title
                and artist
                and (existing.get("title") or "").strip().lower() == title
                and (existing.get("artist") or "").strip().lower() == artist
            ):
                return True
        return False

    def _on_song_found(self, song):
        if self.cancel_evt.is_set():
            return
        self.hud.hide()
        song["reidentified"] = self._is_song_in_library(song)
        self.history.insert(0, song)
        save_history(self.history)
        self.display_song_card(song)
        self.live_status_lbl.setText(f"Found: {song['artist']} - {song['title']}")

        if self.settings.get("auto_copy", False):
            QApplication.clipboard().setText(f"{song['artist']} - {song['title']}")

        if self.settings.get("play_ping_sound", True):
            play_ping_sound()

        if self.settings.get("show_notifications", True):
            if self.settings.get("force_overlay_notification", True):
                self.song_popup.show_song(song, dark=bool(self.last_dark_state))
            else:
                sub = f"{song['artist']} - {song['title']}"
                if song.get("released"):
                    sub += f" ({song['released']})"
                icon_path = song.get("cover_path") or ICON_CACHE_FILE
                app_launch = f"songpulse://song/{song.get('id', '')}" if song.get("id") else ""
                self._toast(
                    "Song Found!",
                    sub,
                    launch_url=app_launch,
                    browser_url=song.get("shazam_url"),
                    icon_path=icon_path,
                )

        if self.pages.currentIndex() == 1:
            self.refresh_library()
        if self.pages.currentIndex() == 2:
            self._update_gpu_indicator()

    def _on_no_match(self):
        if self.cancel_evt.is_set():
            return
        self.live_status_lbl.setText("No match found. Try again during a clearer part of the song.")
        if (
            self.hud.isVisible()
            or self.settings.get("always_show_status_hud", True)
            or not self.isVisible()
        ):
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
        if self.cancel_evt.is_set():
            return
        self.live_status_lbl.setText(f"Error: {msg}")
        if (
            self.hud.isVisible()
            or self.settings.get("always_show_status_hud", True)
            or not self.isVisible()
        ):
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
        self.save_clip_btn.setEnabled(True)
        self.test_saved_clip_btn.setEnabled(True)
        self.clear_saved_clip_btn.setEnabled(True)
        self.quick_ai_chk.setEnabled(True)
        if hasattr(self, "mini_window"):
            self.mini_window.pulse_btn.set_scanning(False)
            self.mini_window.pulse_btn.setEnabled(True)
            self.mini_window.cancel_btn.hide()
            self.mini_window.cancel_btn.setEnabled(True)
            self.mini_window.buffer_btn.setEnabled(True)
            self.mini_window.ai_chk.setEnabled(True)
        self._refresh_identify_header()

    def _toast(self, title, msg, launch_url=None, browser_url=None, icon_path=None):
        try:
            kwargs = {
                "app_id": APP_NAME,
                "title": title,
                "msg": msg,
                "duration": "long",
            }
            if launch_url:
                kwargs["launch"] = launch_url
            if icon_path and os.path.exists(icon_path):
                kwargs["icon"] = os.path.abspath(icon_path)
            toast = Notification(**kwargs)
            if launch_url:
                toast.add_actions(label="View in SongPulse", launch=launch_url)
            if browser_url:
                toast.add_actions(label="Open in Browser", launch=browser_url)
            toast.show()
        except Exception:
            pass
