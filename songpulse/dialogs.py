import copy
import os
import shutil
import webbrowser

from PyQt6.QtCore import QEvent, QObject, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QFontMetrics, QPainter, QPainterPath, QPalette, QPen
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from .config import (
    HISTORY_FILE,
    THEMES_DIR,
    YTDLP_GITHUB_REPO,
    format_bytes,
    get_saved_theme_colors,
    get_storage_breakdown,
    record_saved_theme_colors,
    set_windows_titlebar_theme,
)
from .themes_manager import (
    TEMPLATE_FILENAME,
    ensure_theme_template_and_assets,
    find_theme_name_conflict,
    import_theme_photo,
    load_raw_custom_theme,
    resolve_theme_image_path,
    save_custom_theme,
)
from .theme import create_ui_icon
from .waveforms import (
    WAVEFORM_FLIP_HZ_STYLES,
    WAVEFORM_MIRROR_STYLES,
    resolve_template_waveform,
)
from .widgets import ElidedLabel, NoWheelComboBox, NoWheelSpinBox, _paint_surface
from .workers import GithubPresetsCatalogWorker, GithubPresetsInstallWorker
from .workers import YtDlpInstallerWorker


class _DelayedTooltipFilter(QObject):
    """Shows a widget's tooltip only after hovering for `delay_ms` (default 1000 ms)."""

    def __init__(self, widget: QWidget, text: str, delay_ms: int = 1000):
        super().__init__(widget)
        self._widget = widget
        self._text = text
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(delay_ms)
        self._timer.timeout.connect(self._show_tip)
        widget.installEventFilter(self)

    def _show_tip(self):
        if self._widget and self._widget.isVisible() and self._widget.underMouse():
            QToolTip.showText(QCursor.pos(), self._text, self._widget)

    def eventFilter(self, obj, event):
        et = event.type()
        if et == QEvent.Type.ToolTip:
            return True
        if et == QEvent.Type.Enter:
            self._timer.start()
        elif et in (
            QEvent.Type.Leave,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.Hide,
            QEvent.Type.Wheel,
            QEvent.Type.FocusOut,
        ):
            self._timer.stop()
            QToolTip.hideText()
        return False


def set_delayed_tooltip(widget: QWidget, text: str, delay_ms: int = 1000):
    if widget is None or not text:
        return
    widget._delayed_tip_filter = _DelayedTooltipFilter(widget, text, delay_ms=delay_ms)


def _alpha_to_pct(alpha: int) -> int:
    return max(0, min(100, int(round((max(0, min(240, int(alpha))) / 240.0) * 100))))


def _pct_to_alpha(pct: int) -> int:
    return max(0, min(240, int(round((max(0, min(100, int(pct))) / 100.0) * 240))))


class DownloadProgressDialog(QDialog):
    """Reusable modal progress dialog with live progress bar and instant Cancel support."""

    def __init__(self, title: str, initial_text: str, worker, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(420)
        self.worker = worker
        self.result_value = None
        self.succeeded = False
        self.was_cancelled = False
        self.error_msg = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)

        self.status_lbl = QLabel(initial_text)
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet("font-size: 12px;")
        lay.addWidget(self.status_lbl)

        self.pbar = QProgressBar()
        self.pbar.setRange(0, 0)
        self.pbar.setTextVisible(True)
        lay.addWidget(self.pbar)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("DangerBtn")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(self.cancel_btn)
        lay.addLayout(btn_row)

        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_ok)
        self.worker.cancelled.connect(self._on_cancelled)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_progress(self, pct: int, msg: str):
        self.status_lbl.setText(msg)
        if pct < 0:
            if self.pbar.maximum() != 0:
                self.pbar.setRange(0, 0)
        else:
            if self.pbar.maximum() != 100:
                self.pbar.setRange(0, 100)
            self.pbar.setValue(max(0, min(100, pct)))

    def _on_ok(self, *args):
        self.succeeded = True
        self.result_value = args[0] if args else True
        self.accept()

    def _on_cancelled(self):
        self.was_cancelled = True
        self.reject()

    def _on_error(self, err: str):
        self.error_msg = err
        self.reject()

    def _on_cancel(self):
        self.cancel_btn.setEnabled(False)
        self.status_lbl.setText("Cancelling...")
        if hasattr(self.worker, "request_cancel"):
            self.worker.request_cancel()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self._on_cancel()
            event.ignore()
            return
        super().closeEvent(event)


def prompt_install_or_locate_ytdlp(parent) -> str:
    """Shows a dialog when yt-dlp Downloader is missing: auto-install from GitHub or locate manually."""
    box = QMessageBox(parent)
    box.setWindowTitle("yt-dlp Downloader Not Found")
    box.setIcon(QMessageBox.Icon.Question)
    box.setText("SongPulse could not detect yt-dlp Downloader on your PC.")
    box.setInformativeText(
        f"Install the latest release automatically from GitHub ({YTDLP_GITHUB_REPO}), "
        f"or select its location manually?"
    )
    btn_install = box.addButton("Install Automatically", QMessageBox.ButtonRole.AcceptRole)
    btn_browse = box.addButton("Select Manually...", QMessageBox.ButtonRole.ActionRole)
    btn_cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(btn_install)
    box.exec()

    clicked = box.clickedButton()
    if clicked == btn_cancel or clicked is None:
        return ""

    if clicked == btn_browse:
        picked, _ = QFileDialog.getOpenFileName(
            parent,
            "Locate your yt-dlp Downloader app",
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "App or Script (*.exe *.pyw *.py *.bat);;All Files (*.*)",
        )
        return picked or ""

    if clicked == btn_install:
        worker = YtDlpInstallerWorker()
        dlg = DownloadProgressDialog(
            "Installing yt-dlp Downloader",
            f"Preparing to download from {YTDLP_GITHUB_REPO}...",
            worker,
            parent=parent,
        )
        dlg.exec()
        if dlg.succeeded and dlg.result_value:
            QMessageBox.information(
                parent,
                "Installation Complete",
                f"yt-dlp Downloader installed successfully.\n\nPath: {dlg.result_value}",
            )
            return str(dlg.result_value)
        if not dlg.was_cancelled and dlg.error_msg:
            err_box = QMessageBox(parent)
            err_box.setWindowTitle("Auto-Install Failed")
            err_box.setIcon(QMessageBox.Icon.Warning)
            err_box.setText("Could not automatically install yt-dlp Downloader.")
            err_box.setInformativeText(dlg.error_msg)
            open_gh = err_box.addButton("Open GitHub Releases", QMessageBox.ButtonRole.ActionRole)
            browse_fb = err_box.addButton("Select Manually...", QMessageBox.ButtonRole.ActionRole)
            err_box.addButton("Close", QMessageBox.ButtonRole.RejectRole)
            err_box.exec()
            if err_box.clickedButton() == open_gh:
                webbrowser.open(f"https://github.com/{YTDLP_GITHUB_REPO}/releases/latest")
            elif err_box.clickedButton() == browse_fb:
                picked, _ = QFileDialog.getOpenFileName(
                    parent,
                    "Locate your yt-dlp Downloader app",
                    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                    "App or Script (*.exe *.pyw *.py *.bat);;All Files (*.*)",
                )
                return picked or ""
    return ""


class StorageCleanupDialog(QDialog):
    """Interactive Clean-Up dialog allowing users to selectively free disk space."""

    def __init__(self, history: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Clean Up Storage")
        self.setModal(True)
        self.setMinimumSize(500, 480)
        self.history = history
        self.deleted_models = []
        self.cleared_history = False
        self.cleared_covers = False
        self.removed_ai_runtime = False
        self.freed_total = 0

        self._items = []

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        hdr = QLabel("Clean Up Storage")
        hdr.setStyleSheet("font-size: 18px; font-weight: 700;")
        sub = QLabel("Choose cached files, AI models, or optional components to remove.")
        sub.setObjectName("Muted")
        sub.setWordWrap(True)
        sub.setStyleSheet("font-size: 12px;")
        root.addWidget(hdr)
        root.addWidget(sub)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list_host = QWidget()
        self._list_lay = QVBoxLayout(self._list_host)
        self._list_lay.setContentsMargins(2, 4, 8, 4)
        self._list_lay.setSpacing(8)
        scroll.setWidget(self._list_host)
        root.addWidget(scroll, 1)

        footer = QHBoxLayout()
        self.summary_lbl = QLabel("Selected to free: 0 B")
        self.summary_lbl.setStyleSheet("font-size: 12px; font-weight: 600;")

        self.clean_btn = QPushButton("Clean Up Selected")
        self.clean_btn.setObjectName("DangerBtn")
        self.clean_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clean_btn.clicked.connect(self._run_cleanup)

        close_btn = QPushButton("Close")
        close_btn.setObjectName("PlatformBtn")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.accept)

        footer.addWidget(self.summary_lbl)
        footer.addStretch()
        footer.addWidget(self.clean_btn)
        footer.addWidget(close_btn)
        root.addLayout(footer)

        self._populate()

    def _add_section_label(self, text: str):
        lbl = QLabel(text)
        lbl.setStyleSheet("font-size: 12px; font-weight: 700; margin-top: 6px;")
        self._list_lay.addWidget(lbl)

    def _add_item_row(
        self,
        title: str,
        desc: str,
        size_bytes: int,
        action_fn,
        key: str,
        checked: bool = False,
        enabled: bool = True,
    ):
        card = QFrame()
        card.setObjectName("Card")
        row = QHBoxLayout(card)
        row.setContentsMargins(12, 9, 12, 9)
        row.setSpacing(10)

        chk = QCheckBox()
        chk.setChecked(checked and enabled and size_bytes > 0)
        chk.setEnabled(enabled and size_bytes > 0)
        chk.stateChanged.connect(self._update_summary)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        t_lbl = QLabel(title)
        t_lbl.setStyleSheet("font-size: 12px; font-weight: 600;")
        d_lbl = QLabel(desc)
        d_lbl.setObjectName("Muted")
        d_lbl.setWordWrap(True)
        d_lbl.setStyleSheet("font-size: 11px;")
        text_col.addWidget(t_lbl)
        text_col.addWidget(d_lbl)

        sz_lbl = QLabel(format_bytes(size_bytes))
        sz_lbl.setObjectName("Muted")
        sz_lbl.setStyleSheet("font-size: 12px; font-weight: 600;")

        row.addWidget(chk, alignment=Qt.AlignmentFlag.AlignTop)
        row.addLayout(text_col, 1)
        row.addWidget(sz_lbl, alignment=Qt.AlignmentFlag.AlignVCenter)
        self._list_lay.addWidget(card)
        self._items.append((chk, size_bytes, action_fn, key))

    def _populate(self):
        while self._list_lay.count():
            it = self._list_lay.takeAt(0)
            if it and it.widget():
                it.widget().deleteLater()
        self._items.clear()

        info = get_storage_breakdown(self.history)

        self._add_section_label("Temporary & Cache Files")
        self._add_item_row(
            f"Temporary Files ({len(info['temp_files'])} files)",
            "Audio snapshots, preview HTML, and leftover download temp files.",
            info["temp_bytes"],
            lambda files=list(info["temp_files"]): self._delete_files(files),
            "temp",
            checked=True,
        )
        self._add_item_row(
            f"Error & Debug Logs ({len(info['log_files'])} files)",
            "Diagnostic log files such as ai_error.log.",
            info["log_bytes"],
            lambda files=list(info["log_files"]): self._delete_files(files),
            "logs",
            checked=True,
        )
        self._add_item_row(
            f"Unused Cached Album Covers ({len(info['unused_cover_files'])} files)",
            "Cover art from songs already removed from your library.",
            info["unused_cover_bytes"],
            lambda files=list(info["unused_cover_files"]): self._delete_covers(files),
            "unused_covers",
            checked=True,
        )
        self._add_item_row(
            f"Unused Theme Photos ({len(info['unused_theme_photo_files'])} files)",
            "Saved custom theme pictures no longer referenced by any theme.",
            info["unused_theme_photo_bytes"],
            lambda files=list(info["unused_theme_photo_files"]): self._delete_files(files),
            "unused_theme_photos",
            checked=True,
        )
        self._add_item_row(
            f"All Cached Album Covers ({len(info['all_cover_files'])} files)",
            "Removes local album artwork thumbnails while keeping library entries.",
            info["all_cover_bytes"],
            lambda files=list(info["all_cover_files"]): self._delete_covers(files),
            "all_covers",
            checked=False,
        )

        self._add_section_label("Optional AI Models & Runtime")
        if info["models"]:
            for m in info["models"]:
                fn = m["filename"]
                fp = m["path"]
                is_dir = bool(m.get("is_dir"))
                self._add_item_row(
                    f"AI Model: {fn}",
                    "Downloaded rescue model. Can be re-downloaded anytime in Settings.",
                    m["bytes"],
                    lambda p=fp, name=fn, d=is_dir: self._delete_model(p, name, d),
                    f"model:{fn}",
                    checked=False,
                )
        else:
            empty_m = QLabel("No AI models downloaded (0 B used).")
            empty_m.setObjectName("Muted")
            empty_m.setStyleSheet("font-size: 11px; padding: 2px 4px;")
            self._list_lay.addWidget(empty_m)

        if info["model_meta_bytes"] > 0:
            self._add_item_row(
                f"AI Model Metadata Cache ({len(info['model_meta_files'])} files)",
                "Cached model catalog JSON/YAML configs.",
                info["model_meta_bytes"],
                lambda files=list(info["model_meta_files"]): self._delete_files(files),
                "model_meta",
                checked=False,
            )

        if info["ai_runtime_bytes"] > 0:
            self._add_item_row(
                "Downloaded AI Engine Runtime",
                "Optional AI engine libraries in AppData. Removing frees significant space.",
                info["ai_runtime_bytes"],
                lambda d=info["ai_runtime_dir"]: self._delete_ai_runtime(d),
                "ai_runtime",
                checked=False,
            )

        self._add_section_label("Library Data")
        self._add_item_row(
            f"Saved Song Library ({len(self.history)} songs)",
            "Permanently clears your saved song history (history.json).",
            info["history_bytes"],
            self._clear_history_file,
            "history",
            checked=False,
        )

        self._list_lay.addStretch()
        self._update_summary()

    def _update_summary(self):
        checked_keys = {key for chk, _sz, _fn, key in self._items if chk.isChecked()}
        total = 0
        for chk, sz, _fn, key in self._items:
            if not chk.isChecked():
                continue
            if key == "unused_covers" and "all_covers" in checked_keys:
                continue
            total += sz
        self.summary_lbl.setText(f"Selected to free: {format_bytes(total)}")
        self.clean_btn.setEnabled(total > 0)

    def _delete_files(self, files: list[str]) -> int:
        freed = 0
        for f in files:
            if os.path.isfile(f):
                try:
                    sz = os.path.getsize(f)
                    os.remove(f)
                    freed += sz
                except OSError:
                    pass
        return freed

    def _delete_covers(self, files: list[str]) -> int:
        freed = self._delete_files(files)
        if freed > 0:
            self.cleared_covers = True
        return freed

    def _delete_model(self, path: str, filename: str, is_dir: bool = False) -> int:
        if is_dir and os.path.isdir(path):
            sz = 0
            for root, _d, files in os.walk(path):
                for f in files:
                    try:
                        sz += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
            shutil.rmtree(path, ignore_errors=True)
            if sz > 0:
                self.deleted_models.append(filename)
            return sz
        freed = self._delete_files([path])
        if freed > 0:
            self.deleted_models.append(filename)
        return freed

    def _delete_ai_runtime(self, directory: str) -> int:
        if not os.path.isdir(directory):
            return 0
        sz = 0
        for root, _d, files in os.walk(directory):
            for f in files:
                try:
                    sz += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        try:
            shutil.rmtree(directory, ignore_errors=True)
            self.removed_ai_runtime = True
            return sz
        except OSError:
            return 0

    def _clear_history_file(self) -> int:
        freed = 0
        if os.path.isfile(HISTORY_FILE):
            try:
                freed = os.path.getsize(HISTORY_FILE)
                with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                    f.write("[]")
                self.cleared_history = True
                self.history = []
            except OSError:
                pass
        return freed

    def _run_cleanup(self):
        selected = [(chk, sz, fn, key) for chk, sz, fn, key in self._items if chk.isChecked()]
        if not selected:
            return
        reply = QMessageBox.question(
            self,
            "Confirm Clean-Up",
            f"Delete the {len(selected)} selected item(s) to free disk space?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        freed = 0
        for _chk, _sz, fn, _key in selected:
            freed += int(fn() or 0)

        self.freed_total += freed
        self._populate()
        QMessageBox.information(
            self,
            "Clean-Up Complete",
            f"Freed {format_bytes(freed)} of disk space.",
        )


class _ColorSwatchButton(QPushButton):
    color_changed = pyqtSignal(str)

    def __init__(self, initial_hex: str, parent=None):
        super().__init__(parent)
        self._color = QColor(initial_hex or "#38BDF8")
        self.setProperty("_dynamic_qss", True)
        self.setFixedSize(82, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self._pick)
        self._sync_style()

    def hex_value(self) -> str:
        return self._color.name()

    def set_color(self, hex_str: str):
        col = QColor(hex_str or "#38BDF8")
        if col.isValid():
            self._color = col
            self._sync_style()

    def _pick(self):
        dlg = QColorDialog(self._color, self)
        dlg.setWindowTitle("Choose Color")
        dlg.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)

        saved = get_saved_theme_colors()
        for idx in range(QColorDialog.customCount()):
            c = QColor(saved[idx]) if idx < len(saved) else QColor("#FFFFFF")
            QColorDialog.setCustomColor(idx, c)

        for lbl in dlg.findChildren(QLabel):
            txt = lbl.text().strip()
            if not txt:
                lbl.setFixedHeight(8)
                lbl.setStyleSheet("padding: 0px;")
            elif "custom" in txt.lower():
                lbl.setText("&Saved colors")
        for btn in dlg.findChildren(QPushButton):
            if "add" in btn.text().lower() and "custom" in btn.text().lower():
                btn.setVisible(False)
                break

        if dlg.layout() is not None:
            dlg.layout().setContentsMargins(14, 14, 14, 14)
            if dlg.layout().spacing() >= 0:
                dlg.layout().setSpacing(max(dlg.layout().spacing(), 8))
        for grid in dlg.findChildren(QGridLayout):
            grid.setHorizontalSpacing(6)
            grid.setVerticalSpacing(4)
            grid.setContentsMargins(4, 2, 4, 2)

        dlg.ensurePolished()
        for sb in dlg.findChildren(QSpinBox):
            sb_fm = QFontMetrics(sb.font())
            sb.setFixedWidth(sb_fm.horizontalAdvance("359") + 44)
        for le in dlg.findChildren(QLineEdit):
            if not isinstance(le.parentWidget(), QSpinBox):
                fm = QFontMetrics(le.font())
                le.setFixedWidth(fm.horizontalAdvance("#888888") + 28)

        set_windows_titlebar_theme(
            int(dlg.winId()),
            dlg.palette().color(QPalette.ColorRole.Window).lightness() < 128,
        )
        dlg.adjustSize()

        if dlg.exec():
            picked = dlg.currentColor()
            if picked.isValid():
                self._color = picked
                self._sync_style()
                record_saved_theme_colors([self._color.name()])
                self.color_changed.emit(self._color.name())

    def _sync_style(self):
        lum = 0.2126 * self._color.red() + 0.7152 * self._color.green() + 0.0722 * self._color.blue()
        fg = "#0F172A" if lum > 165 else "#FFFFFF"
        self.setText(self._color.name().upper())
        qss = (
            f"QPushButton {{ background-color: {self._color.name()}; color: {fg}; "
            f"border: 1px solid rgba(255,255,255,0.28); border-radius: 6px; padding: 2px 6px; "
            f"font-size: 11px; font-weight: 700; }}"
        )
        self.setProperty("_base_qss", qss)
        self.setStyleSheet(qss)
        self.update()


class _PhotoPreviewWidget(QFrame):
    """Clickable thumbnail that renders the chosen photo with blur & darken applied directly."""

    clicked = pyqtSignal()

    def __init__(self, w: int, h: int, is_sidebar: bool = False, parent=None):
        super().__init__(parent)
        self.setFixedSize(w, h)
        self._is_sidebar = bool(is_sidebar)
        self._cfg: dict = {}
        self._pix_cache: dict = {}
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_preview_config(self, cfg: dict):
        new_cfg = dict(cfg or {})
        old_sig = (self._cfg.get("image"), self._cfg.get("blur"), self._cfg.get("fit"))
        new_sig = (new_cfg.get("image"), new_cfg.get("blur"), new_cfg.get("fit"))
        self._cfg = new_cfg
        if old_sig != new_sig:
            self._pix_cache.clear()
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0.5, 0.5, rect.width() - 1.0, rect.height() - 1.0), 8.0, 8.0)
        p.setClipPath(clip)

        img_path = resolve_theme_image_path(self._cfg.get("image", ""))
        if img_path:
            preview_cfg = dict(self._cfg)
            preview_cfg["type"] = "image"
            preview_cfg["image"] = img_path
            preview_cfg["darken_black"] = True
            _paint_surface(
                p,
                rect,
                preview_cfg,
                self._pix_cache,
                is_sidebar=self._is_sidebar,
                exact_thumb=True,
            )
            p.setClipping(False)
            p.setPen(QPen(QColor(255, 255, 255, 55), 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(0.5, 0.5, rect.width() - 1.0, rect.height() - 1.0), 8.0, 8.0)
        else:
            p.fillRect(rect, QColor(255, 255, 255, 14))
            p.setClipping(False)
            pen = QPen(QColor(148, 163, 184, 110), 1.0, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(1.0, 1.0, rect.width() - 2.0, rect.height() - 2.0), 8.0, 8.0)
            p.setPen(QColor(148, 163, 184, 190))
            f = p.font()
            f.setPointSize(8)
            p.setFont(f)
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "No photo")
        p.end()


class CustomThemeDialog(QDialog):
    """Visual custom theme creator and editor with live preview, mini window, and photo persistence."""

    def __init__(self, parent=None, edit_theme_key: str = "", on_preview=None, on_stop_preview=None):
        super().__init__(parent)
        ensure_theme_template_and_assets()
        self._edit_theme_key = (edit_theme_key or "").strip()
        self._is_edit = bool(self._edit_theme_key)
        self._on_preview = on_preview
        self._on_stop_preview = on_stop_preview
        self._preview_active = False
        self._suppress_preview = True
        self._raw_edit_data = load_raw_custom_theme(self._edit_theme_key) if self._is_edit else None

        title = "Edit Custom Theme" if self._is_edit else "Create Custom Theme"
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumSize(560, 620)
        self.created_theme_key = ""
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(2000)
        self._preview_timer.timeout.connect(self._apply_debounced_preview)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        hdr = QLabel(title)
        hdr.setStyleSheet("font-size: 18px; font-weight: 700;")
        sub = QLabel(
            "Customize colors, blurred photos, mini window look, highlights, and live waveform."
        )
        sub.setObjectName("Muted")
        sub.setWordWrap(True)
        sub.setStyleSheet("font-size: 11px;")
        root.addWidget(hdr)
        root.addWidget(sub)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(2, 4, 8, 4)
        lay.setSpacing(10)

        # Name & Base
        r1 = QHBoxLayout()
        r1.setSpacing(8)
        r1.addWidget(QLabel("Theme Name"))
        self.name_in = QLineEdit()
        self.name_in.setPlaceholderText("My Custom Theme")
        r1.addWidget(self.name_in, 1)
        self.base_combo = NoWheelComboBox()
        self.base_combo.addItems(["Dark Base", "Light Base"])
        set_delayed_tooltip(
            self.base_combo,
            "Sets default text, card, and menu contrast (Dark or Light).",
        )
        self.base_combo.currentIndexChanged.connect(self._on_any_change)
        r1.addWidget(self.base_combo)
        lay.addLayout(r1)

        # Main Window Background
        self._bg_sec = self._build_surface_card(
            lay,
            title="Main Window Background",
            styles=[("Gradient", "gradient"), ("Solid Color", "solid"), ("Picture", "image")],
            style_tip="Choose Gradient, Solid Color, or Picture for the main window.",
            c1_default="#071B2A",
            c2_default="#104460",
            thumb_size=(80, 50),
            is_sidebar=False,
        )

        # Left Sidebar
        self._sb_sec = self._build_surface_card(
            lay,
            title="Left Sidebar",
            styles=[("Picture (Glass)", "image"), ("Gradient", "gradient"), ("Solid Color", "solid")],
            style_tip="Choose Picture (Glass), Gradient, or Solid Color for the left sidebar.",
            c1_default="#0A2438",
            c2_default="#124E63",
            thumb_size=(44, 64),
            is_sidebar=True,
        )

        # Mini Window
        self._mini_sec = self._build_surface_card(
            lay,
            title="Mini Window",
            styles=[("Gradient", "gradient"), ("Solid Color", "solid"), ("Picture", "image")],
            style_tip="Choose Gradient, Solid Color, or Picture for the Mini Window.",
            c1_default="#071B2A",
            c2_default="#104460",
            thumb_size=(56, 56),
            is_sidebar=False,
            with_use_main_chk=True,
        )

        # Buttons, Highlights & Waveform
        ac_card = QFrame()
        ac_card.setObjectName("Card")
        ac_l = QVBoxLayout(ac_card)
        ac_l.setContentsMargins(12, 10, 12, 10)
        ac_l.setSpacing(6)
        ac_h = QLabel("Buttons, Highlights & Waveform")
        ac_h.setStyleSheet("font-size: 13px; font-weight: 600;")
        ac_sub = QLabel("Listen button, active tabs, borders & audio waveform")
        ac_sub.setObjectName("Muted")
        ac_sub.setStyleSheet("font-size: 10px;")
        ac_l.addWidget(ac_h)
        ac_l.addWidget(ac_sub)

        ac_r1 = QHBoxLayout()
        ac_r1.setSpacing(8)
        ac1_lbl = QLabel("Main Highlight")
        set_delayed_tooltip(
            ac1_lbl,
            "Primary highlight used for the Listen button, active tabs, and waveform start.",
        )
        self.ac1 = _ColorSwatchButton("#39C5BB")
        set_delayed_tooltip(
            self.ac1,
            "Primary highlight used for the Listen button, active tabs, and waveform start.",
        )
        self.ac1.color_changed.connect(self._on_any_change)

        ac2_lbl = QLabel("Secondary Highlight")
        set_delayed_tooltip(
            ac2_lbl,
            "Secondary gradient color used for the Listen button and waveform end.",
        )
        self.ac2 = _ColorSwatchButton("#38BDF8")
        set_delayed_tooltip(
            self.ac2,
            "Secondary gradient color used for the Listen button and waveform end.",
        )
        self.ac2.color_changed.connect(self._on_any_change)

        ac_r1.addWidget(ac1_lbl)
        ac_r1.addWidget(self.ac1)
        ac_r1.addSpacing(8)
        ac_r1.addWidget(ac2_lbl)
        ac_r1.addWidget(self.ac2)
        ac_r1.addStretch()
        ac_l.addLayout(ac_r1)

        wf_r = QHBoxLayout()
        wf_r.setSpacing(8)
        wf_r.addWidget(QLabel("Waveform"))
        self.wf_style = NoWheelComboBox()
        self.wf_style.addItems(["Radial", "Horizontal Split", "Pulse Line", "Bottom Bar", "Dots"])
        set_delayed_tooltip(self.wf_style, "Default waveform shape when this theme is active.")
        self.wf_style.currentIndexChanged.connect(self._on_wf_style_changed)
        wf_r.addWidget(self.wf_style)
        self._wf_manual_mirror = False
        self._wf_manual_flip_hz = False
        self.wf_mirror = QCheckBox("Mirrored")
        set_delayed_tooltip(
            self.wf_mirror,
            "Mirror left and right sides using combined mono audio instead of stereo L/R (Horizontal Split & Pulse Line only).",
        )
        self.wf_mirror.stateChanged.connect(self._on_wf_opt_changed)
        self.wf_flip_hz = QCheckBox("Flip Hz")
        set_delayed_tooltip(
            self.wf_flip_hz,
            "Reverse frequency direction (Horizontal Split, Pulse Line & Bottom Bar only).",
        )
        self.wf_flip_hz.stateChanged.connect(self._on_wf_opt_changed)
        self.wf_idle_dots = QCheckBox("Idle dots when silent")
        set_delayed_tooltip(
            self.wf_idle_dots,
            "Keep resting dots/line visible when no audio is playing.",
        )
        self.wf_idle_dots.stateChanged.connect(self._on_any_change)
        wf_r.addWidget(self.wf_mirror)
        wf_r.addWidget(self.wf_flip_hz)
        wf_r.addWidget(self.wf_idle_dots)
        wf_r.addStretch()
        ac_l.addLayout(wf_r)
        lay.addWidget(ac_card)

        lay.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        tmpl_btn = QPushButton("Open Template JSON")
        tmpl_btn.setObjectName("PlatformBtn")
        tmpl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        tmpl_btn.clicked.connect(self._open_template)

        self.preview_btn = QPushButton("Preview: OFF")
        self.preview_btn.setObjectName("PlatformBtn")
        self.preview_btn.setCheckable(True)
        self.preview_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        set_delayed_tooltip(
            self.preview_btn,
            "Live-preview this theme on the app before saving (updates 2s after edits).",
        )
        self.preview_btn.toggled.connect(self._on_preview_toggled)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("DangerBtn")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)

        save_btn = QPushButton("Save && Apply")
        save_btn.setObjectName("PrimaryBtn")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.clicked.connect(self._on_save)

        footer.addWidget(tmpl_btn)
        footer.addStretch()
        footer.addWidget(self.preview_btn)
        footer.addWidget(cancel_btn)
        footer.addWidget(save_btn)
        root.addLayout(footer)

        if self._raw_edit_data:
            self._populate_from_raw(self._raw_edit_data)
        else:
            for sec in (self._bg_sec, self._sb_sec, self._mini_sec):
                self._sync_section_visibility(sec)
            self._sync_wf_checkboxes()
        self._suppress_preview = False

    def _build_surface_card(
        self,
        parent_lay: QVBoxLayout,
        title: str,
        styles: list[tuple[str, str]],
        style_tip: str,
        c1_default: str,
        c2_default: str,
        thumb_size: tuple[int, int],
        is_sidebar: bool = False,
        with_use_main_chk: bool = False,
    ) -> dict:
        card = QFrame()
        card.setObjectName("Card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(12, 10, 12, 10)
        cl.setSpacing(8)

        hdr_row = QHBoxLayout()
        hdr = QLabel(title)
        hdr.setStyleSheet("font-size: 13px; font-weight: 600;")
        hdr_row.addWidget(hdr)
        hdr_row.addStretch()

        use_main_chk = None
        if with_use_main_chk:
            use_main_chk = QCheckBox("Use Main Window Background")
            use_main_chk.setChecked(True)
            use_main_chk.setCursor(Qt.CursorShape.PointingHandCursor)
            set_delayed_tooltip(
                use_main_chk,
                "Mirror the Main Window Background in the Mini Window, or uncheck to customize it separately.",
            )
            hdr_row.addWidget(use_main_chk)
        cl.addLayout(hdr_row)

        content_w = QWidget()
        content_l = QVBoxLayout(content_w)
        content_l.setContentsMargins(0, 0, 0, 0)
        content_l.setSpacing(8)

        r1 = QHBoxLayout()
        r1.setSpacing(8)
        style_combo = NoWheelComboBox()
        for label, key in styles:
            style_combo.addItem(label, key)
        set_delayed_tooltip(style_combo, style_tip)

        c1_lbl = QLabel("Main Color")
        set_delayed_tooltip(c1_lbl, "Primary background or gradient start color.")
        c1_btn = _ColorSwatchButton(c1_default)
        set_delayed_tooltip(c1_btn, "Primary background or gradient start color.")

        c2_lbl = QLabel("Secondary Color")
        set_delayed_tooltip(c2_lbl, "Gradient end color.")
        c2_btn = _ColorSwatchButton(c2_default)
        set_delayed_tooltip(c2_btn, "Gradient end color.")

        r1.addWidget(style_combo)
        r1.addSpacing(4)
        r1.addWidget(c1_lbl)
        r1.addWidget(c1_btn)
        r1.addWidget(c2_lbl)
        r1.addWidget(c2_btn)
        r1.addStretch()
        content_l.addLayout(r1)

        photo_row_w = QWidget()
        pr = QHBoxLayout(photo_row_w)
        pr.setContentsMargins(0, 2, 0, 0)
        pr.setSpacing(8)

        preview = _PhotoPreviewWidget(thumb_size[0], thumb_size[1], is_sidebar=is_sidebar)
        set_delayed_tooltip(preview, "Live photo preview with Blur and Darken applied. Click to change.")

        name_col = QVBoxLayout()
        name_col.setSpacing(4)
        photo_lbl = ElidedLabel("No photo selected")
        photo_lbl.setObjectName("Muted")
        photo_lbl.setStyleSheet("font-size: 11px;")

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        br_btn = QPushButton("Browse")
        br_btn.setObjectName("PlatformBtn")
        br_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clr_btn = QPushButton("")
        clr_btn.setObjectName("IconDangerBtn")
        clr_btn.setIcon(create_ui_icon("close", "#F87171", 12))
        clr_btn.setIconSize(QSize(12, 12))
        clr_btn.setFixedSize(24, 24)
        clr_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        set_delayed_tooltip(clr_btn, "Remove selected photo")
        btn_row.addWidget(br_btn)
        btn_row.addWidget(clr_btn)
        btn_row.addStretch()

        name_col.addWidget(photo_lbl)
        name_col.addLayout(btn_row)

        blur_spin = NoWheelSpinBox()
        blur_spin.setRange(0, 30)
        blur_spin.setValue(6)
        blur_spin.setPrefix("Blur ")
        set_delayed_tooltip(blur_spin, "Gaussian blur radius applied to the photo (0–30).")

        darken_spin = NoWheelSpinBox()
        darken_spin.setRange(0, 100)
        darken_spin.setValue(_alpha_to_pct(115))
        darken_spin.setPrefix("Darken ")
        darken_spin.setSuffix("%")
        set_delayed_tooltip(darken_spin, "Darkens the photo so text and buttons stay readable (0–100%).")

        pr.addWidget(preview)
        pr.addLayout(name_col, 1)
        pr.addWidget(blur_spin)
        pr.addWidget(darken_spin)
        content_l.addWidget(photo_row_w)
        cl.addWidget(content_w)
        parent_lay.addWidget(card)

        sec = {
            "card": card,
            "content_w": content_w,
            "use_main_chk": use_main_chk,
            "style_combo": style_combo,
            "c1_btn": c1_btn,
            "c2_lbl": c2_lbl,
            "c2_btn": c2_btn,
            "photo_row_w": photo_row_w,
            "preview": preview,
            "photo_lbl": photo_lbl,
            "clr_btn": clr_btn,
            "blur_spin": blur_spin,
            "darken_spin": darken_spin,
            "image_path": "",
            "is_sidebar": is_sidebar,
        }

        style_combo.currentIndexChanged.connect(lambda _i=0, s=sec: self._on_section_changed(s))
        c1_btn.color_changed.connect(lambda _c="", s=sec: self._on_section_changed(s))
        c2_btn.color_changed.connect(lambda _c="", s=sec: self._on_section_changed(s))
        blur_spin.valueChanged.connect(lambda _v=0, s=sec: self._on_section_changed(s))
        darken_spin.valueChanged.connect(lambda _v=0, s=sec: self._on_section_changed(s))
        br_btn.clicked.connect(lambda _c=False, s=sec: self._pick_img_for_section(s))
        preview.clicked.connect(lambda s=sec: self._pick_img_for_section(s))
        clr_btn.clicked.connect(lambda _c=False, s=sec: self._clear_img_for_section(s))
        if use_main_chk is not None:
            use_main_chk.stateChanged.connect(lambda _st=0, s=sec: self._on_section_changed(s))

        return sec

    def _sync_section_visibility(self, sec: dict):
        if sec["use_main_chk"] is not None:
            sec["content_w"].setVisible(not sec["use_main_chk"].isChecked())
        mode = sec["style_combo"].currentData()
        show_c2 = mode != "solid"
        sec["c2_lbl"].setVisible(show_c2)
        sec["c2_btn"].setVisible(show_c2)
        sec["photo_row_w"].setVisible(mode == "image")
        has_img = bool(sec["image_path"])
        sec["clr_btn"].setVisible(has_img)
        sec["photo_lbl"].setText(os.path.basename(sec["image_path"]) if has_img else "No photo selected")
        sec["preview"].set_preview_config(self._section_dict(sec, for_preview=True))

    def _on_section_changed(self, sec: dict):
        self._sync_section_visibility(sec)
        self._on_any_change()

    def _sync_wf_checkboxes(self):
        wf_map = {0: "radial", 1: "horizontal", 2: "pulse_line", 3: "bar", 4: "dots"}
        style = wf_map.get(self.wf_style.currentIndex(), "radial")
        mirror_avail = style in WAVEFORM_MIRROR_STYLES
        flip_avail = style in WAVEFORM_FLIP_HZ_STYLES

        self.wf_mirror.blockSignals(True)
        self.wf_mirror.setChecked(self._wf_manual_mirror if mirror_avail else False)
        self.wf_mirror.setEnabled(mirror_avail)
        self.wf_mirror.blockSignals(False)

        self.wf_flip_hz.blockSignals(True)
        self.wf_flip_hz.setChecked(self._wf_manual_flip_hz if flip_avail else False)
        self.wf_flip_hz.setEnabled(flip_avail)
        self.wf_flip_hz.blockSignals(False)

    def _on_wf_style_changed(self, *_args):
        self._sync_wf_checkboxes()
        self._on_any_change()

    def _on_wf_opt_changed(self, *_args):
        if self.wf_mirror.isEnabled():
            self._wf_manual_mirror = self.wf_mirror.isChecked()
        if self.wf_flip_hz.isEnabled():
            self._wf_manual_flip_hz = self.wf_flip_hz.isChecked()
        self._on_any_change()

    def _on_any_change(self, *_args):
        if self._suppress_preview:
            return
        if self._preview_active:
            self.preview_btn.setText("Preview: Updating...")
            self._preview_timer.start()

    def _pick_img_for_section(self, sec: dict):
        picked, _ = QFileDialog.getOpenFileName(
            self,
            "Select Theme Picture",
            os.path.expanduser("~"),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp);;All Files (*.*)",
        )
        if picked:
            stored = import_theme_photo(picked)
            sec["image_path"] = stored
            self._sync_section_visibility(sec)
            self._on_any_change()

    def _clear_img_for_section(self, sec: dict):
        sec["image_path"] = ""
        self._sync_section_visibility(sec)
        self._on_any_change()

    def _section_dict(self, sec: dict, for_preview: bool = False) -> dict:
        mode = sec["style_combo"].currentData() or "gradient"
        img_val = sec["image_path"]
        if for_preview and img_val:
            img_val = resolve_theme_image_path(img_val) or img_val
        if mode == "image" and not img_val and not for_preview:
            mode = "gradient"
        alpha = _pct_to_alpha(sec["darken_spin"].value())
        out = {
            "type": mode,
            "color1": sec["c1_btn"].hex_value(),
            "color2": sec["c2_btn"].hex_value(),
            "image": img_val,
            "blur": sec["blur_spin"].value(),
            "darken_black": True,
        }
        if sec["is_sidebar"]:
            out["glass_alpha"] = alpha
        else:
            out["angle"] = 135
            out["overlay_alpha"] = alpha
        return out

    def _load_section_from_raw(self, sec: dict, raw_sec: dict, alpha_key: str):
        stype = str(raw_sec.get("type", "gradient"))
        for idx in range(sec["style_combo"].count()):
            if sec["style_combo"].itemData(idx) == stype:
                sec["style_combo"].setCurrentIndex(idx)
                break
        if raw_sec.get("color1"):
            sec["c1_btn"].set_color(str(raw_sec["color1"]))
        if raw_sec.get("color2"):
            sec["c2_btn"].set_color(str(raw_sec["color2"]))
        sec["image_path"] = str(raw_sec.get("image") or "")
        sec["blur_spin"].setValue(max(0, min(30, int(raw_sec.get("blur", 6)))))
        sec["darken_spin"].setValue(_alpha_to_pct(int(raw_sec.get(alpha_key, 115))))
        self._sync_section_visibility(sec)

    def _populate_from_raw(self, raw: dict):
        self.name_in.setText(str(raw.get("name") or ""))
        self.base_combo.setCurrentIndex(0 if raw.get("base_dark", True) else 1)

        bg = raw.get("background") if isinstance(raw.get("background"), dict) else {}
        sb = raw.get("sidebar") if isinstance(raw.get("sidebar"), dict) else {}
        mini = raw.get("miniplayer") if isinstance(raw.get("miniplayer"), dict) else None
        cols = raw.get("colors") if isinstance(raw.get("colors"), dict) else {}
        wf = raw.get("waveform") if isinstance(raw.get("waveform"), dict) else {}

        self._load_section_from_raw(self._bg_sec, bg, "overlay_alpha")
        self._load_section_from_raw(self._sb_sec, sb, "glass_alpha")

        if mini is None:
            self._mini_sec["use_main_chk"].setChecked(True)
            self._load_section_from_raw(self._mini_sec, bg, "overlay_alpha")
        else:
            self._mini_sec["use_main_chk"].setChecked(bool(mini.get("use_main_bg", False)))
            self._load_section_from_raw(self._mini_sec, mini, "overlay_alpha")

        if cols.get("accent"):
            self.ac1.set_color(str(cols["accent"]))
        if cols.get("accent_secondary"):
            self.ac2.set_color(str(cols["accent_secondary"]))

        wf_map = {"radial": 0, "horizontal": 1, "pulse_line": 2, "bar": 3, "dots": 4}
        self._wf_manual_mirror = bool(wf.get("mirror", False))
        self._wf_manual_flip_hz = bool(wf.get("flip_hz", False))
        self.wf_style.setCurrentIndex(wf_map.get(str(wf.get("style", "radial")), 0))
        self.wf_idle_dots.setChecked(bool(wf.get("idle_dots", False)))
        self._sync_wf_checkboxes()

    def _build_theme_payload(self, for_preview: bool = False) -> dict:
        name = self.name_in.text().strip() or "My Custom Theme"
        dark = self.base_combo.currentIndex() == 0
        wf_map = {0: "radial", 1: "horizontal", 2: "pulse_line", 3: "bar", 4: "dots"}

        bg_dict = self._section_dict(self._bg_sec, for_preview=for_preview)
        sb_dict = self._section_dict(self._sb_sec, for_preview=for_preview)

        if self._mini_sec["use_main_chk"].isChecked():
            mini_dict = copy.deepcopy(bg_dict)
            mini_dict["use_main_bg"] = True
        else:
            mini_dict = self._section_dict(self._mini_sec, for_preview=for_preview)
            mini_dict["use_main_bg"] = False

        # Preserve any advanced JSON keys when editing an existing theme
        raw_base = self._raw_edit_data if isinstance(self._raw_edit_data, dict) else {}
        if isinstance(raw_base.get("background"), dict):
            merged_bg = dict(raw_base["background"])
            merged_bg.update(bg_dict)
            bg_dict = merged_bg
        if isinstance(raw_base.get("sidebar"), dict):
            merged_sb = dict(raw_base["sidebar"])
            merged_sb.update(sb_dict)
            sb_dict = merged_sb
        if isinstance(raw_base.get("miniplayer"), dict) and not mini_dict["use_main_bg"]:
            merged_mini = dict(raw_base["miniplayer"])
            merged_mini.update(mini_dict)
            mini_dict = merged_mini

        colors_dict = {
            "accent": self.ac1.hex_value(),
            "accent_secondary": self.ac2.hex_value(),
            "card_bg": "rgba(15, 24, 38, 0.72)" if dark else "rgba(255, 255, 255, 0.84)",
            "card_border": "rgba(255, 255, 255, 0.16)" if dark else "rgba(15, 23, 42, 0.14)",
            "text": "#F4F8FF" if dark else "#0F172A",
            "muted": "#9BB6CC" if dark else "#475569",
        }
        wf_style = wf_map[self.wf_style.currentIndex()]
        wf_dict = dict(raw_base.get("waveform")) if isinstance(raw_base.get("waveform"), dict) else {}
        wf_dict.update(
            {
                "style": wf_style,
                "mirror": self.wf_mirror.isChecked() if wf_style in WAVEFORM_MIRROR_STYLES else False,
                "flip_hz": self.wf_flip_hz.isChecked() if wf_style in WAVEFORM_FLIP_HZ_STYLES else False,
                "idle_dots": self.wf_idle_dots.isChecked(),
                "color1": self.ac1.hex_value(),
                "color2": self.ac2.hex_value(),
            }
        )

        payload = {
            "id": self._edit_theme_key or "custom:__preview__",
            "name": name,
            "base_dark": dark,
            "background": bg_dict,
            "sidebar": sb_dict,
            "miniplayer": mini_dict,
            "colors": colors_dict,
            "waveform": wf_dict,
        }
        if for_preview:
            for sec_name in ("background", "sidebar", "miniplayer"):
                s = payload[sec_name]
                if s.get("type") == "image" and not s.get("image"):
                    s["type"] = "gradient"
            payload["waveform"] = resolve_template_waveform(payload)
        else:
            payload.pop("id", None)
        return payload

    def _on_preview_toggled(self, checked: bool):
        self._preview_timer.stop()
        self._preview_active = bool(checked)
        if self._preview_active:
            self.preview_btn.setText("Preview: ON")
            self._emit_preview()
        else:
            self.preview_btn.setText("Preview: OFF")
            self._stop_preview()

    def _apply_debounced_preview(self):
        if not self._preview_active:
            return
        self._emit_preview()
        self.preview_btn.setText("Preview: ON")

    def _emit_preview(self):
        if callable(self._on_preview):
            self._on_preview(self._build_theme_payload(for_preview=True))

    def _stop_preview(self):
        self._preview_timer.stop()
        self._preview_active = False
        if hasattr(self, "preview_btn"):
            self.preview_btn.setText("Preview: OFF")
        if callable(self._on_stop_preview):
            self._on_stop_preview()

    def reject(self):
        self._stop_preview()
        super().reject()

    def closeEvent(self, event):
        self._stop_preview()
        super().closeEvent(event)

    def _open_template(self):
        tmpl = os.path.join(THEMES_DIR, TEMPLATE_FILENAME)
        if os.path.isfile(tmpl):
            os.startfile(tmpl)

    def _on_save(self):
        name = self.name_in.text().strip() or "My Custom Theme"
        conflict = find_theme_name_conflict(name, ignore_theme_key=self._edit_theme_key)
        if conflict:
            QMessageBox.warning(
                self,
                "Theme Name Already Exists",
                f"A theme named '{conflict}' already exists.\nPlease choose a different name.",
            )
            self.name_in.setFocus()
            self.name_in.selectAll()
            return

        for sec in (self._bg_sec, self._sb_sec, self._mini_sec):
            if sec["image_path"]:
                sec["image_path"] = import_theme_photo(sec["image_path"])

        data = self._build_theme_payload(for_preview=False)
        record_saved_theme_colors(
            [
                self._bg_sec["c1_btn"].hex_value(),
                self._bg_sec["c2_btn"].hex_value(),
                self._sb_sec["c1_btn"].hex_value(),
                self._sb_sec["c2_btn"].hex_value(),
                self._mini_sec["c1_btn"].hex_value(),
                self._mini_sec["c2_btn"].hex_value(),
                self.ac1.hex_value(),
                self.ac2.hex_value(),
            ]
        )
        self._stop_preview()
        self.created_theme_key = save_custom_theme(
            name, data, previous_theme_key=self._edit_theme_key
        )
        self.accept()


class GithubPresetsDialog(QDialog):
    """Lets the user browse and selectively install undownloaded custom theme presets from GitHub."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Custom Theme Presets")
        self.setModal(True)
        self.setMinimumSize(440, 340)
        self.installed_names: list[str] = []
        self._presets: list[dict] = []
        self._checkboxes: list[tuple[QCheckBox, dict]] = []
        self._catalog_worker = None
        self._install_worker = None

        set_windows_titlebar_theme(
            int(self.winId()),
            self.palette().color(QPalette.ColorRole.Window).lightness() < 128,
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        hdr = QLabel("Custom Theme Presets")
        hdr.setStyleSheet("font-size: 17px; font-weight: 700;")
        sub = QLabel("Choose one or more available themes from GitHub to install.")
        sub.setObjectName("Muted")
        sub.setWordWrap(True)
        sub.setStyleSheet("font-size: 11px;")
        root.addWidget(hdr)
        root.addWidget(sub)

        self.status_lbl = QLabel("Checking GitHub for available custom theme presets...")
        self.status_lbl.setObjectName("Muted")
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet("font-size: 12px;")
        root.addWidget(self.status_lbl)

        self.pbar = QProgressBar()
        self.pbar.setRange(0, 0)
        self.pbar.setTextVisible(True)
        root.addWidget(self.pbar)

        self.select_all_chk = QCheckBox("Select All")
        self.select_all_chk.setCursor(Qt.CursorShape.PointingHandCursor)
        self.select_all_chk.stateChanged.connect(self._on_select_all_toggled)
        self.select_all_chk.hide()
        root.addWidget(self.select_all_chk)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_body = QWidget()
        self.list_lay = QVBoxLayout(self.list_body)
        self.list_lay.setContentsMargins(0, 2, 4, 2)
        self.list_lay.setSpacing(6)
        self.list_lay.addStretch(1)
        self.scroll.setWidget(self.list_body)
        self.scroll.hide()
        root.addWidget(self.scroll, 1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        footer.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("DangerBtn")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self.reject)

        self.install_btn = QPushButton("Install Selected")
        self.install_btn.setObjectName("PrimaryBtn")
        self.install_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.install_btn.setEnabled(False)
        self.install_btn.clicked.connect(self._on_install_clicked)

        footer.addWidget(self.cancel_btn)
        footer.addWidget(self.install_btn)
        root.addLayout(footer)

        self._fetch_catalog()

    def _fetch_catalog(self):
        self._catalog_worker = GithubPresetsCatalogWorker(parent=self)
        self._catalog_worker.catalog_ready.connect(self._on_catalog_ready)
        self._catalog_worker.error.connect(self._on_catalog_error)
        self._catalog_worker.start()

    def _on_catalog_ready(self, presets: list):
        self.pbar.hide()
        self._presets = list(presets or [])
        self._checkboxes.clear()

        while self.list_lay.count() > 1:
            it = self.list_lay.takeAt(0)
            if it and it.widget():
                it.widget().deleteLater()

        if not self._presets:
            self.status_lbl.setText(
                "All available custom theme presets from GitHub are already installed."
            )
            self.select_all_chk.hide()
            self.scroll.hide()
            self.install_btn.hide()
            self.cancel_btn.setText("Close")
            return

        self.status_lbl.setText(
            f"Found {len(self._presets)} undownloaded preset(s) available:"
        )
        self.select_all_chk.setVisible(len(self._presets) >= 2)
        self.scroll.show()

        for idx, preset in enumerate(self._presets):
            card = QFrame()
            card.setObjectName("SongCard")
            row = QHBoxLayout(card)
            row.setContentsMargins(10, 8, 12, 8)
            row.setSpacing(10)

            chk = QCheckBox(preset.get("name") or preset.get("slug") or "Preset")
            chk.setCursor(Qt.CursorShape.PointingHandCursor)
            chk.setStyleSheet("font-size: 13px; font-weight: 600;")
            chk.stateChanged.connect(self._sync_selection_state)
            row.addWidget(chk, 1)

            for col_hex in (preset.get("accent"), preset.get("accent_secondary")):
                if col_hex:
                    dot = QLabel()
                    dot.setFixedSize(12, 12)
                    dot.setStyleSheet(
                        f"background-color: {col_hex}; border-radius: 6px; "
                        f"border: 1px solid rgba(255,255,255,0.28);"
                    )
                    row.addWidget(dot)

            base_lbl = QLabel("Dark" if preset.get("base_dark", True) else "Light")
            base_lbl.setObjectName("MetaChip")
            row.addWidget(base_lbl)

            self.list_lay.insertWidget(idx, card)
            self._checkboxes.append((chk, preset))

        self._sync_selection_state()

    def _on_catalog_error(self, err: str):
        self.pbar.hide()
        self.status_lbl.setText(f"Could not fetch presets from GitHub:\n{err}")
        self.install_btn.hide()
        self.cancel_btn.setText("Close")

    def _on_select_all_toggled(self, state: int):
        checked = state == Qt.CheckState.Checked.value
        for chk, _preset in self._checkboxes:
            chk.blockSignals(True)
            chk.setChecked(checked)
            chk.blockSignals(False)
        self._sync_selection_state()

    def _sync_selection_state(self):
        selected_count = sum(1 for chk, _p in self._checkboxes if chk.isChecked())
        total = len(self._checkboxes)
        if total >= 2:
            self.select_all_chk.blockSignals(True)
            self.select_all_chk.setChecked(selected_count == total and total > 0)
            self.select_all_chk.blockSignals(False)
        self.install_btn.setEnabled(selected_count > 0)
        self.install_btn.setText(
            f"Install Selected ({selected_count})" if selected_count > 0 else "Install Selected"
        )

    def _on_install_clicked(self):
        selected = [preset for chk, preset in self._checkboxes if chk.isChecked()]
        if not selected:
            return
        self.install_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.select_all_chk.setEnabled(False)
        for chk, _p in self._checkboxes:
            chk.setEnabled(False)

        self.pbar.setRange(0, 100)
        self.pbar.setValue(0)
        self.pbar.show()
        self.status_lbl.setText("Starting download...")

        self._install_worker = GithubPresetsInstallWorker(selected, parent=self)
        self._install_worker.progress.connect(self._on_install_progress)
        self._install_worker.finished_install.connect(self._on_install_finished)
        self._install_worker.start()

    def _on_install_progress(self, pct: int, msg: str):
        self.status_lbl.setText(msg)
        self.pbar.setValue(max(0, min(100, pct)))

    def _on_install_finished(self, installed_names: list, err: str):
        self.installed_names = list(installed_names or [])
        if err:
            self.pbar.hide()
            self.cancel_btn.setEnabled(True)
            self.cancel_btn.setText("Close")
            self.status_lbl.setText(f"Download failed:\n{err}")
            return
        self.accept()
