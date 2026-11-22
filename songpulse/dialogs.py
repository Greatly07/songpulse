import os
import shutil
import webbrowser

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .config import (
    HISTORY_FILE,
    THEMES_DIR,
    YTDLP_GITHUB_REPO,
    format_bytes,
    get_storage_breakdown,
)
from .themes_manager import TEMPLATE_FILENAME, ensure_theme_template_and_assets, save_custom_theme
from .workers import YtDlpInstallerWorker


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
    def __init__(self, initial_hex: str, parent=None):
        super().__init__(parent)
        self._color = QColor(initial_hex or "#38BDF8")
        self.setFixedSize(76, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self._pick)
        self._sync_style()

    def hex_value(self) -> str:
        return self._color.name()

    def _pick(self):
        picked = QColorDialog.getColor(self._color, self, "Choose Color")
        if picked.isValid():
            self._color = picked
            self._sync_style()

    def _sync_style(self):
        lum = 0.2126 * self._color.red() + 0.7152 * self._color.green() + 0.0722 * self._color.blue()
        fg = "#0F172A" if lum > 165 else "#FFFFFF"
        self.setText(self._color.name().upper())
        self.setStyleSheet(
            f"QPushButton {{ background-color: {self._color.name()}; color: {fg}; "
            f"border: 1px solid rgba(255,255,255,0.28); border-radius: 6px; font-size: 10px; font-weight: 700; }}"
        )


class CustomThemeDialog(QDialog):
    """Visual custom theme creator that saves clean JSON theme files into DATA_DIR/themes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        ensure_theme_template_and_assets()
        self.setWindowTitle("Create Custom Theme")
        self.setModal(True)
        self.setMinimumSize(500, 560)
        self.created_theme_key = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        hdr = QLabel("Create Custom Theme")
        hdr.setStyleSheet("font-size: 18px; font-weight: 700;")
        sub = QLabel(
            "Pick solid colors, gradients, or photos for your background and sidebar, or edit the JSON template directly."
        )
        sub.setObjectName("Muted")
        sub.setWordWrap(True)
        sub.setStyleSheet("font-size: 11px;")
        root.addWidget(hdr)
        root.addWidget(sub)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(2, 4, 8, 4)
        lay.setSpacing(10)

        # Name & Base
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Theme Name"))
        self.name_in = QLineEdit("My Custom Theme")
        r1.addWidget(self.name_in, 1)
        self.base_combo = QComboBox()
        self.base_combo.addItems(["Dark Base", "Light Base"])
        r1.addWidget(self.base_combo)
        lay.addLayout(r1)

        # Background card
        bg_card = QFrame()
        bg_card.setObjectName("Card")
        bg_l = QVBoxLayout(bg_card)
        bg_l.setContentsMargins(12, 10, 12, 10)
        bg_l.setSpacing(8)
        bg_h = QLabel("Main Background")
        bg_h.setStyleSheet("font-size: 13px; font-weight: 600;")
        bg_l.addWidget(bg_h)

        bg_r1 = QHBoxLayout()
        bg_r1.addWidget(QLabel("Style"))
        self.bg_type = QComboBox()
        self.bg_type.addItems(["Gradient", "Solid Color", "Picture"])
        bg_r1.addWidget(self.bg_type)
        bg_r1.addWidget(QLabel("Color 1"))
        self.bg_c1 = _ColorSwatchButton("#071B2A")
        bg_r1.addWidget(self.bg_c1)
        bg_r1.addWidget(QLabel("Color 2"))
        self.bg_c2 = _ColorSwatchButton("#104460")
        bg_r1.addWidget(self.bg_c2)
        bg_l.addLayout(bg_r1)

        bg_r2 = QHBoxLayout()
        bg_r2.addWidget(QLabel("Photo"))
        self.bg_img = QLineEdit()
        self.bg_img.setPlaceholderText("Optional background picture (.png / .jpg)")
        bg_br = QPushButton("Browse")
        bg_br.setObjectName("PlatformBtn")
        bg_br.clicked.connect(lambda: self._pick_img(self.bg_img))
        self.bg_blur = QSpinBox()
        self.bg_blur.setRange(0, 30)
        self.bg_blur.setValue(6)
        self.bg_blur.setPrefix("Blur ")
        bg_r2.addWidget(self.bg_img, 1)
        bg_r2.addWidget(bg_br)
        bg_r2.addWidget(self.bg_blur)
        bg_l.addLayout(bg_r2)
        lay.addWidget(bg_card)

        # Sidebar card
        sb_card = QFrame()
        sb_card.setObjectName("Card")
        sb_l = QVBoxLayout(sb_card)
        sb_l.setContentsMargins(12, 10, 12, 10)
        sb_l.setSpacing(8)
        sb_h = QLabel("Sidebar")
        sb_h.setStyleSheet("font-size: 13px; font-weight: 600;")
        sb_l.addWidget(sb_h)

        sb_r1 = QHBoxLayout()
        sb_r1.addWidget(QLabel("Style"))
        self.sb_type = QComboBox()
        self.sb_type.addItems(["Picture (Glass)", "Gradient", "Solid Color"])
        sb_r1.addWidget(self.sb_type)
        sb_r1.addWidget(QLabel("Color 1"))
        self.sb_c1 = _ColorSwatchButton("#0A2438")
        sb_r1.addWidget(self.sb_c1)
        sb_r1.addWidget(QLabel("Color 2"))
        self.sb_c2 = _ColorSwatchButton("#124E63")
        sb_r1.addWidget(self.sb_c2)
        sb_l.addLayout(sb_r1)

        sb_r2 = QHBoxLayout()
        sb_r2.addWidget(QLabel("Photo"))
        self.sb_img = QLineEdit()
        self.sb_img.setPlaceholderText("Optional sidebar photo (lightly blurred frosted look)")
        sb_br = QPushButton("Browse")
        sb_br.setObjectName("PlatformBtn")
        sb_br.clicked.connect(lambda: self._pick_img(self.sb_img))
        self.sb_blur = QSpinBox()
        self.sb_blur.setRange(0, 30)
        self.sb_blur.setValue(6)
        self.sb_blur.setPrefix("Blur ")
        sb_r2.addWidget(self.sb_img, 1)
        sb_r2.addWidget(sb_br)
        sb_r2.addWidget(self.sb_blur)
        sb_l.addLayout(sb_r2)
        lay.addWidget(sb_card)

        # Accents & Waveform card
        ac_card = QFrame()
        ac_card.setObjectName("Card")
        ac_l = QVBoxLayout(ac_card)
        ac_l.setContentsMargins(12, 10, 12, 10)
        ac_l.setSpacing(8)
        ac_h = QLabel("Accents & Waveform")
        ac_h.setStyleSheet("font-size: 13px; font-weight: 600;")
        ac_l.addWidget(ac_h)

        ac_r1 = QHBoxLayout()
        ac_r1.addWidget(QLabel("Accent 1"))
        self.ac1 = _ColorSwatchButton("#39C5BB")
        ac_r1.addWidget(self.ac1)
        ac_r1.addWidget(QLabel("Accent 2"))
        self.ac2 = _ColorSwatchButton("#38BDF8")
        ac_r1.addWidget(self.ac2)
        ac_r1.addStretch()
        ac_l.addLayout(ac_r1)

        wf_r = QHBoxLayout()
        wf_r.addWidget(QLabel("Waveform"))
        self.wf_style = QComboBox()
        self.wf_style.addItems(["Radial", "Horizontal Split", "Pulse Line", "Bottom Bar", "Dots"])
        wf_r.addWidget(self.wf_style)
        self.wf_mirror = QCheckBox("Mirrored")
        self.wf_idle_dots = QCheckBox("Idle dots when silent")
        wf_r.addWidget(self.wf_mirror)
        wf_r.addWidget(self.wf_idle_dots)
        wf_r.addStretch()
        ac_l.addLayout(wf_r)
        lay.addWidget(ac_card)

        lay.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        footer = QHBoxLayout()
        tmpl_btn = QPushButton("Open Template JSON")
        tmpl_btn.setObjectName("PlatformBtn")
        tmpl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        tmpl_btn.clicked.connect(self._open_template)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("PlatformBtn")
        cancel_btn.clicked.connect(self.reject)

        save_btn = QPushButton("Save & Apply Theme")
        save_btn.setObjectName("PrimaryBtn")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.clicked.connect(self._on_save)

        footer.addWidget(tmpl_btn)
        footer.addStretch()
        footer.addWidget(cancel_btn)
        footer.addWidget(save_btn)
        root.addLayout(footer)

    def _pick_img(self, target_edit: QLineEdit):
        picked, _ = QFileDialog.getOpenFileName(
            self,
            "Select Theme Picture",
            os.path.expanduser("~"),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp);;All Files (*.*)",
        )
        if picked:
            target_edit.setText(picked)

    def _open_template(self):
        tmpl = os.path.join(THEMES_DIR, TEMPLATE_FILENAME)
        if os.path.isfile(tmpl):
            os.startfile(tmpl)

    def _on_save(self):
        name = self.name_in.text().strip() or "My Custom Theme"
        dark = self.base_combo.currentIndex() == 0
        bg_map = {0: "gradient", 1: "solid", 2: "image"}
        sb_map = {0: "image", 1: "gradient", 2: "solid"}
        wf_map = {0: "radial", 1: "horizontal", 2: "pulse_line", 3: "bar", 4: "dots"}

        bg_mode = bg_map[self.bg_type.currentIndex()]
        sb_mode = sb_map[self.sb_type.currentIndex()]
        if bg_mode == "image" and not self.bg_img.text().strip():
            bg_mode = "gradient"
        if sb_mode == "image" and not self.sb_img.text().strip():
            sb_mode = "gradient"

        data = {
            "name": name,
            "base_dark": dark,
            "background": {
                "type": bg_mode,
                "color1": self.bg_c1.hex_value(),
                "color2": self.bg_c2.hex_value(),
                "angle": 135,
                "image": self.bg_img.text().strip(),
                "blur": self.bg_blur.value(),
                "overlay_alpha": 115,
            },
            "sidebar": {
                "type": sb_mode,
                "color1": self.sb_c1.hex_value(),
                "color2": self.sb_c2.hex_value(),
                "image": self.sb_img.text().strip(),
                "blur": self.sb_blur.value(),
                "glass_alpha": 120,
            },
            "colors": {
                "accent": self.ac1.hex_value(),
                "accent_secondary": self.ac2.hex_value(),
                "card_bg": "rgba(15, 24, 38, 0.72)" if dark else "rgba(255, 255, 255, 0.84)",
                "card_border": "rgba(255, 255, 255, 0.16)" if dark else "rgba(15, 23, 42, 0.14)",
                "text": "#F4F8FF" if dark else "#0F172A",
                "muted": "#9BB6CC" if dark else "#475569",
            },
            "waveform": {
                "style": wf_map[self.wf_style.currentIndex()],
                "mirror": self.wf_mirror.isChecked(),
                "idle_dots": self.wf_idle_dots.isChecked(),
                "color1": self.ac1.hex_value(),
                "color2": self.ac2.hex_value(),
            },
        }
        self.created_theme_key = save_custom_theme(name, data)
        self.accept()
