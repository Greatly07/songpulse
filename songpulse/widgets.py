import math
import webbrowser

from PyQt6.QtCore import (
    QEvent,
    QPoint,
    QPointF,
    QRect,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAction,
    QColor,
    QFontMetrics,
    QPainter,
    QPen,
    QStandardItem,
    QStandardItemModel,
)
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .config import set_window_always_on_top
from .theme import rounded_pixmap
from .workers import build_song_platform_links


class ElidedLabel(QLabel):
    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full_text = text
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str):
        self._full_text = text or ""
        self.setToolTip(self._full_text)
        self._update_elided()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_elided()

    def _update_elided(self):
        fm = QFontMetrics(self.font())
        avail = max(10, self.width() - 2)
        elided = fm.elidedText(self._full_text, Qt.TextElideMode.ElideRight, avail)
        super().setText(elided)


class HelpBadge(QLabel):
    def __init__(self, tooltip: str, parent=None):
        super().__init__("?", parent)
        self.setObjectName("HelpBadge")
        self.setFixedSize(17, 17)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.WhatsThisCursor)


class FlowLayout(QLayout):
    def __init__(self, parent=None, margin=0, h_spacing=6, v_spacing=6):
        super().__init__(parent)
        self._items = []
        self._h_space = h_spacing
        self._v_space = v_spacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        eff = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = eff.x()
        y = eff.y()
        line_height = 0

        for item in self._items:
            w = item.sizeHint().width()
            h = item.sizeHint().height()
            next_x = x + w + self._h_space
            if next_x - self._h_space > eff.right() and line_height > 0:
                x = eff.x()
                y += line_height + self._v_space
                next_x = x + w + self._h_space
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = next_x
            line_height = max(line_height, h)

        return y + line_height - rect.y() + m.bottom()


class ResponsiveLinksBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(32)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._entries = []

        self.more_btn = QPushButton("More ▾", self)
        self.more_btn.setObjectName("PlatformBtn")
        self.more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_btn.setFixedHeight(28)
        self.more_btn.hide()

        self.more_menu = QMenu(self)
        self.more_btn.setMenu(self.more_menu)

    def set_items(self, items):
        for entry in self._entries:
            entry["btn"].deleteLater()
        self._entries.clear()

        for label, icon, callback in items:
            btn = QPushButton(label, self)
            btn.setObjectName("PlatformBtn")
            if icon:
                btn.setIcon(icon)
                btn.setIconSize(QSize(13, 13))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(28)
            btn.clicked.connect(lambda checked=False, cb=callback, b=btn: cb(b))
            self._entries.append(
                {"btn": btn, "label": label, "icon": icon, "callback": callback}
            )

        self._relayout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self):
        avail_w = max(80, self.width())
        spacing = 6
        more_w = max(64, self.more_btn.sizeHint().width() + 8)

        widths = [max(44, e["btn"].sizeHint().width() + 2) for e in self._entries]
        total_w = sum(widths) + spacing * max(0, len(widths) - 1)

        self.more_menu.clear()

        if total_w <= avail_w:
            x = 0
            for e, w in zip(self._entries, widths):
                e["btn"].setGeometry(x, 2, w, 28)
                e["btn"].show()
                x += w + spacing
            self.more_btn.hide()
            return

        x = 0
        overflow_start = len(self._entries)
        for idx, (e, w) in enumerate(zip(self._entries, widths)):
            if x + w + spacing + more_w <= avail_w:
                e["btn"].setGeometry(x, 2, w, 28)
                e["btn"].show()
                x += w + spacing
            else:
                overflow_start = idx
                break

        for e in self._entries[overflow_start:]:
            e["btn"].hide()
            act = QAction(e["icon"], e["label"], self.more_menu)
            act.triggered.connect(lambda checked=False, cb=e["callback"]: cb(None))
            self.more_menu.addAction(act)

        self.more_btn.setGeometry(x, 2, more_w, 28)
        self.more_btn.show()


def get_song_link(song: dict) -> str:
    """Returns the best direct URL for a song (direct YouTube video, Shazam, or YouTube search)."""
    if not song:
        return ""
    return (
        song.get("youtube_video_url")
        or song.get("shazam_url")
        or song.get("youtube_url")
        or ""
    )


class ClickableCard(QFrame):
    """Card frame that triggers clicked when pressing anywhere outside child buttons."""

    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SongCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._glow = QGraphicsDropShadowEffect(self)
        self._glow.setBlurRadius(20)
        self._glow.setOffset(0, 0)
        self._glow.setColor(QColor(59, 130, 246, 135))
        self._glow.setEnabled(False)
        self.setGraphicsEffect(self._glow)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            return
        super().mousePressEvent(event)

    def enterEvent(self, event):
        self._glow.setEnabled(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._glow.setEnabled(False)
        super().leaveEvent(event)


class HistoryRowCard(QFrame):
    def __init__(self, song, p_icons, details_icon, on_details, on_delete, parent=None):
        super().__init__(parent)
        self.setObjectName("SongCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._glow = QGraphicsDropShadowEffect(self)
        self._glow.setBlurRadius(20)
        self._glow.setOffset(0, 0)
        self._glow.setColor(QColor(59, 130, 246, 135))
        self._glow.setEnabled(False)
        self.setGraphicsEffect(self._glow)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._compact = None
        self._link_btns = []
        
        self._song = song
        self._on_details = on_details
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 10, 10, 10)
        row.setSpacing(10)

        cover = QLabel()
        cover.setFixedSize(48, 48)
        cover.setPixmap(rounded_pixmap(song.get("cover_path", ""), 48, 8))
        row.addWidget(cover)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        text_col.setContentsMargins(0, 0, 0, 0)

        t_lbl = ElidedLabel(song.get("title", "Unknown"))
        t_lbl.setStyleSheet("font-size: 13px; font-weight: 600;")

        sub_parts = [song.get("artist", "Unknown")]
        if song.get("released"):
            sub_parts.append(song["released"])
        if song.get("album"):
            sub_parts.append(song["album"])
        sub_lbl = ElidedLabel(" • ".join(sub_parts))
        sub_lbl.setObjectName("Muted")
        sub_lbl.setStyleSheet("font-size: 11px;")

        ts_lbl = ElidedLabel(song.get("timestamp", ""))
        ts_lbl.setObjectName("Muted")
        ts_lbl.setStyleSheet("font-size: 10px;")

        cover.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        t_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        sub_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        ts_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        text_col.addWidget(t_lbl)
        text_col.addWidget(sub_lbl)
        text_col.addWidget(ts_lbl)
        row.addLayout(text_col, 1)

        all_links = build_song_platform_links(song)
        for pname, icon_key, url in all_links[:2]:
            b = QPushButton(pname)
            b.setObjectName("PlatformBtn")
            b.setIcon(p_icons.get(icon_key, p_icons["web"]))
            b.setIconSize(QSize(13, 13))
            b.setToolTip(f"Open on {pname}")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda checked=False, u=url: webbrowser.open(u))
            row.addWidget(b)
            self._link_btns.append((b, pname))

        self.more_btn = None
        if len(all_links) > 2:
            self.more_btn = QPushButton("More ▾")
            self.more_btn.setObjectName("PlatformBtn")
            self.more_btn.setToolTip("More music platforms")
            self.more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            m = QMenu(self.more_btn)
            for pname, icon_key, url in all_links[2:]:
                act = QAction(p_icons.get(icon_key, p_icons["web"]), pname, m)
                act.triggered.connect(lambda checked=False, u=url: webbrowser.open(u))
                m.addAction(act)
            self.more_btn.setMenu(m)
            row.addWidget(self.more_btn)

        self.details_btn = QPushButton("Details")
        self.details_btn.setObjectName("PlatformBtn")
        self.details_btn.setIcon(details_icon)
        self.details_btn.setIconSize(QSize(13, 13))
        self.details_btn.setToolTip("View song details")
        self.details_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.details_btn.clicked.connect(lambda checked=False: on_details(song))
        row.addWidget(self.details_btn)

        del_btn = QPushButton("✕")
        del_btn.setObjectName("IconDangerBtn")
        del_btn.setFixedSize(28, 28)
        del_btn.setToolTip("Remove from library")
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.clicked.connect(lambda checked=False: on_delete(song.get("id")))
        row.addWidget(del_btn)

        self._apply_compact_mode(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_compact_mode(self.width() < 620)

    def _apply_compact_mode(self, compact: bool):
        if compact == self._compact:
            return
        self._compact = compact
        if compact:
            for b, _name in self._link_btns:
                b.setText("")
                b.setFixedSize(28, 28)
            if self.more_btn:
                self.more_btn.setText("⋯")
                self.more_btn.setFixedSize(28, 28)
            self.details_btn.setText("")
            self.details_btn.setFixedSize(28, 28)
        else:
            for b, name in self._link_btns:
                b.setText(name)
                b.setMinimumSize(0, 28)
                b.setMaximumSize(16777215, 28)
            if self.more_btn:
                self.more_btn.setText("More ▾")
                self.more_btn.setMinimumSize(0, 28)
                self.more_btn.setMaximumSize(16777215, 28)
            self.details_btn.setText("Details")
            self.details_btn.setMinimumSize(0, 28)
            self.details_btn.setMaximumSize(16777215, 28)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_details(self._song)
            return
        super().mousePressEvent(event)

    def enterEvent(self, event):
        self._glow.setEnabled(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._glow.setEnabled(False)
        super().leaveEvent(event)


class PulseIdentifyWidget(QWidget):
    """Circular Identify button surrounded by 28 thick, sleek pill equalizer bars (hidden when silent)."""

    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(228, 228)
        self._phase = 0.0
        self._scanning = False
        self._dark = True
        self._bar_count = 28
        self._bars = [0.0] * self._bar_count
        self._energy = 0.0

        self.btn = QPushButton("Listen", self)
        self.btn.setObjectName("IdentifyBtn")
        self.btn.setFixedSize(132, 132)
        self.btn.move((228 - 132) // 2, (228 - 132) // 2)
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.clicked.connect(self.clicked.emit)

    def setText(self, text: str):
        self.btn.setText(text)

    def setEnabled(self, enabled: bool):
        self.btn.setEnabled(enabled)

    def set_scanning(self, scanning: bool):
        self._scanning = scanning

    def set_dark(self, dark: bool):
        self._dark = dark
        self.update()

    def clear_waveform(self):
        self._bars = [0.0] * self._bar_count
        self._energy = 0.0
        self.update()

    def step_animation(self, spectrum_bands, rms_level, has_outputs=True):
        if not has_outputs and not self._scanning:
            if self._energy > 0.0 or any(b > 0.0 for b in self._bars):
                self.clear_waveform()
            return

        self._phase += 0.16 if self._scanning else 0.06
        self._energy = self._energy * 0.72 + rms_level * 0.28

        n = self._bar_count
        for i in range(n):
            if self._scanning:
                angle_wave = 0.5 + 0.5 * math.sin(self._phase * 2.0 + i * 0.55)
                target = 0.22 + 0.62 * angle_wave
            elif spectrum_bands is not None and len(spectrum_bands) == n:
                live_val = float(spectrum_bands[i])
                target = min(1.0, live_val) if live_val >= 0.04 else 0.0
            else:
                target = 0.0

            alpha = 0.55 if target > self._bars[i] else 0.24
            val = self._bars[i] * (1.0 - alpha) + target * alpha
            self._bars[i] = 0.0 if val < 0.025 else val

        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx = self.width() / 2.0
        cy = self.height() / 2.0
        inner_r = 74.0
        max_bar_len = 32.0

        # Subtle pulsing halo when scanning or loud audio plays
        ripple_strength = 1.0 if self._scanning else min(0.75, self._energy * 1.5)
        if ripple_strength > 0.08:
            for ring_idx in range(2):
                prog = (self._phase * 0.28 + ring_idx * 0.5) % 1.0
                radius = inner_r + prog * 34.0
                alpha = int((1.0 - prog) * 90 * ripple_strength)
                ring_color = QColor("#60A5FA" if self._dark else "#2563EB")
                ring_color.setAlpha(max(0, min(255, alpha)))
                p.setPen(QPen(ring_color, 1.8))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(cx, cy), radius, radius)

        # Draw thick, smooth rounded equalizer pills only for active bands
        for i, amp in enumerate(self._bars):
            if amp < 0.03:
                continue
            theta = (2.0 * math.pi * i) / self._bar_count - (math.pi / 2.0)
            bar_len = 3.0 + amp * max_bar_len
            x1 = cx + math.cos(theta) * inner_r
            y1 = cy + math.sin(theta) * inner_r
            x2 = cx + math.cos(theta) * (inner_r + bar_len)
            y2 = cy + math.sin(theta) * (inner_r + bar_len)

            blend = 0.5 + 0.5 * math.sin(theta + self._phase * 0.35)
            r = int(59 + (139 - 59) * blend)
            g = int(130 + (92 - 130) * blend)
            b = 246

            # Soft outer glow
            glow = QColor(r, g, b, int(45 + amp * 55))
            p.setPen(QPen(glow, 11.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

            # Crisp thick core pill
            core = QColor(r, g, b, int(165 + min(90, amp * 110)))
            p.setPen(QPen(core, 7.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        p.end()


class NoWheelComboBox(QComboBox):
    """Dropdown that ignores mouse-wheel scrolling so Settings scrolls smoothly."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        event.ignore()


class NoWheelSpinBox(QSpinBox):
    """Spinbox that ignores mouse-wheel scrolling so durations don't change accidentally."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        event.ignore()


class CheckableComboBox(NoWheelComboBox):
    selection_changed = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setModel(QStandardItemModel(self))
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().installEventFilter(self)
        self.view().viewport().installEventFilter(self)
        self._block_hide = False

    def eventFilter(self, obj, event):
        if obj == self.lineEdit() and event.type() == QEvent.Type.MouseButtonRelease:
            if self.view().isVisible():
                self.hidePopup()
            else:
                self.showPopup()
            return True

        if obj == self.view().viewport() and event.type() == QEvent.Type.MouseButtonRelease:
            index = self.view().indexAt(event.pos())
            if index.isValid():
                item = self.model().itemFromIndex(index)
                if item and item.isCheckable():
                    new_state = (
                        Qt.CheckState.Unchecked
                        if item.checkState() == Qt.CheckState.Checked
                        else Qt.CheckState.Checked
                    )
                    item.setCheckState(new_state)
                    self._update_display_text()
                    self.selection_changed.emit(self.checked_items())
                    self._block_hide = True
                    return True
        return super().eventFilter(obj, event)

    def hidePopup(self):
        if self._block_hide:
            self._block_hide = False
            return
        super().hidePopup()

    def set_items(self, all_devices, selected_devices, monitor_all=True):
        self.model().clear()
        for dev in all_devices:
            item = QStandardItem(dev)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            is_checked = monitor_all or (dev in selected_devices)
            item.setData(
                Qt.CheckState.Checked if is_checked else Qt.CheckState.Unchecked,
                Qt.ItemDataRole.CheckStateRole,
            )
            self.model().appendRow(item)
        self._update_display_text()

    def checked_items(self):
        items = []
        for row in range(self.model().rowCount()):
            it = self.model().item(row)
            if it and it.checkState() == Qt.CheckState.Checked:
                items.append(it.text())
        return items

    def all_items(self):
        return [self.model().item(r).text() for r in range(self.model().rowCount())]

    def _update_display_text(self):
        checked = self.checked_items()
        total = self.model().rowCount()
        if total == 0:
            text = "No audio outputs found — click Refresh"
        elif not checked:
            text = "None selected (no audio will be captured)"
        elif len(checked) == total:
            text = f"All ({total}): " + ", ".join(checked)
        else:
            text = ", ".join(checked)
        self.lineEdit().setText(text)
        self.lineEdit().setCursorPosition(0)
        self.setToolTip("\n".join(checked) if checked else "No outputs selected")


class HotkeyRecorderEdit(QLineEdit):
    hotkey_recorded = pyqtSignal(str)

    def __init__(self, initial="", parent=None):
        super().__init__(initial, parent)
        self._recording = False

    def start_recording(self):
        self._recording = True
        self.setPlaceholderText("Press your shortcut...")
        self.clear()
        self.setFocus()

    def stop_recording(self):
        self._recording = False
        self.setPlaceholderText("e.g. ctrl+alt+s")

    def keyPressEvent(self, event):
        if not self._recording:
            super().keyPressEvent(event)
            return

        key = event.key()
        if key in (
            Qt.Key.Key_Control,
            Qt.Key.Key_Shift,
            Qt.Key.Key_Alt,
            Qt.Key.Key_Meta,
        ):
            return

        if key == Qt.Key.Key_Escape:
            self.stop_recording()
            return

        parts = []
        mods = event.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier:
            parts.append("ctrl")
        if mods & Qt.KeyboardModifier.AltModifier:
            parts.append("alt")
        if mods & Qt.KeyboardModifier.ShiftModifier:
            parts.append("shift")

        key_name = ""
        if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F24:
            key_name = f"f{key - Qt.Key.Key_F1 + 1}"
        elif Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            key_name = chr(key).lower()
        elif Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            key_name = chr(key)
        else:
            special_map = {
                Qt.Key.Key_Space: "space",
                Qt.Key.Key_Tab: "tab",
                Qt.Key.Key_Return: "enter",
                Qt.Key.Key_Enter: "enter",
                Qt.Key.Key_Backspace: "backspace",
                Qt.Key.Key_Insert: "insert",
                Qt.Key.Key_Delete: "delete",
                Qt.Key.Key_Home: "home",
                Qt.Key.Key_End: "end",
                Qt.Key.Key_PageUp: "page up",
                Qt.Key.Key_PageDown: "page down",
                Qt.Key.Key_Pause: "pause",
                Qt.Key.Key_QuoteLeft: "`",
                Qt.Key.Key_Minus: "-",
                Qt.Key.Key_Equal: "=",
                Qt.Key.Key_BracketLeft: "[",
                Qt.Key.Key_BracketRight: "]",
            }
            key_name = special_map.get(key, "")

        if key_name:
            parts.append(key_name)
            combo = "+".join(parts)
            self.setText(combo)
            self.stop_recording()
            self.hotkey_recorded.emit(combo)


class SubtleStatusHUD(QWidget):
    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.pill = QFrame()
        self.pill.setObjectName("HudPill")
        pill_lay = QHBoxLayout(self.pill)
        pill_lay.setContentsMargins(12, 8, 14, 8)
        pill_lay.setSpacing(8)

        self.dot = QLabel("●")
        self.dot.setStyleSheet("color: #3B82F6; font-size: 11px;")
        self.label = QLabel("SongPulse")
        self.label.setStyleSheet("font-size: 12px; font-weight: 600;")

        pill_lay.addWidget(self.dot)
        pill_lay.addWidget(self.label)
        layout.addWidget(self.pill)

        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide)

    def show_message(self, text, dark=True, dot_color="#3B82F6", auto_hide_ms=0):
        bg = "rgba(22, 24, 30, 238)" if dark else "rgba(255, 255, 255, 246)"
        fg = "#F4F4F5" if dark else "#111827"
        border = "#2E3240" if dark else "#CBD4E1"
        self.pill.setStyleSheet(
            f"QFrame#HudPill {{ background-color: {bg}; border: 1px solid {border}; border-radius: 16px; }}"
        )
        self.dot.setStyleSheet(f"color: {dot_color}; font-size: 11px;")
        self.label.setStyleSheet(f"color: {fg}; font-size: 12px; font-weight: 600;")
        self.label.setText(text)
        self.adjustSize()

        screen = QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            x = avail.right() - self.width() - 16
            y = avail.bottom() - self.height() - 16
            self.move(x, y)

        self.hide_timer.stop()
        self.show()
        set_window_always_on_top(int(self.winId()), True)
        if auto_hide_ms > 0:
            self.hide_timer.start(auto_hide_ms)


class SongResultPopup(QWidget):
    """Bottom-right song notification that bypasses DND and stays visible for 12 seconds."""

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedWidth(360)
        self._open_url = ""
        self._copy_text = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.card = QFrame()
        self.card.setObjectName("PopupCard")
        lay = QHBoxLayout(self.card)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(12)

        self.cover_lbl = QLabel()
        self.cover_lbl.setFixedSize(62, 62)
        lay.addWidget(self.cover_lbl, alignment=Qt.AlignmentFlag.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(3)

        head_row = QHBoxLayout()
        head_row.setSpacing(6)
        self.kicker = QLabel("SONG FOUND")
        self.kicker.setStyleSheet("font-size: 10px; font-weight: 700; color: #3B82F6;")
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(20, 20)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; color: #9CA3AF; font-size: 12px; }"
            "QPushButton:hover { color: #F87171; }"
        )
        close_btn.clicked.connect(self.hide)
        head_row.addWidget(self.kicker)
        head_row.addStretch()
        head_row.addWidget(close_btn)

        self.title_lbl = ElidedLabel("")
        self.sub_lbl = ElidedLabel("")

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.open_btn = QPushButton("Open in Browser")
        self.open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_btn.clicked.connect(self._on_open)

        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.clicked.connect(self._on_copy)

        btn_row.addWidget(self.open_btn)
        btn_row.addWidget(self.copy_btn)
        btn_row.addStretch()

        col.addLayout(head_row)
        col.addWidget(self.title_lbl)
        col.addWidget(self.sub_lbl)
        col.addSpacing(4)
        col.addLayout(btn_row)
        lay.addLayout(col, 1)
        outer.addWidget(self.card)

        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide)

    def enterEvent(self, event):
        self.hide_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hide_timer.start(4000)
        super().leaveEvent(event)

    def _on_open(self):
        if self._open_url:
            webbrowser.open(self._open_url)
        self.hide()

    def _on_copy(self):
        if self._copy_text:
            QApplication.clipboard().setText(self._copy_text)
            self.copy_btn.setText("Copied!")
            QTimer.singleShot(1200, lambda: self.copy_btn.setText("Copy"))

    def show_song(self, song: dict, dark=True):
        bg = "#171921" if dark else "#FFFFFF"
        border = "#2F3344" if dark else "#CBD4E1"
        fg = "#F4F4F5" if dark else "#111827"
        sub_fg = "#9CA3AF" if dark else "#526075"
        btn_bg = "#252834" if dark else "#EDF1F7"
        btn_fg = "#E4E4E7" if dark else "#1E293B"
        btn_b = "#363A4C" if dark else "#CBD4E1"

        self.card.setStyleSheet(
            f"QFrame#PopupCard {{ background-color: {bg}; border: 1px solid {border}; border-radius: 14px; }}"
        )
        self.title_lbl.setStyleSheet(f"color: {fg}; font-size: 14px; font-weight: 700;")
        self.sub_lbl.setStyleSheet(f"color: {sub_fg}; font-size: 12px;")
        btn_qss = (
            f"QPushButton {{ background: {btn_bg}; color: {btn_fg}; border: 1px solid {btn_b}; "
            f"border-radius: 6px; padding: 4px 10px; font-size: 11px; font-weight: 600; }}"
            f"QPushButton:hover {{ border-color: #3B82F6; }}"
        )
        self.open_btn.setStyleSheet(btn_qss)
        self.copy_btn.setStyleSheet(btn_qss)
        self.copy_btn.setText("Copy")

        self.cover_lbl.setPixmap(rounded_pixmap(song.get("cover_path", ""), 62, 9))
        self.title_lbl.setText(song.get("title", "Unknown Title"))
        artist_line = song.get("artist", "Unknown Artist")
        if song.get("released"):
            artist_line += f" • {song['released']}"
        self.sub_lbl.setText(artist_line)

        self._open_url = song.get("shazam_url") or song.get("youtube_url") or ""
        self._copy_text = get_song_link(song) or f"{song.get('artist', '')} - {song.get('title', '')}"
        self.open_btn.setVisible(bool(self._open_url))

        self.adjustSize()
        screen = QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            x = avail.right() - self.width() - 16
            y = avail.bottom() - self.height() - 16
            self.move(x, y)

        self.hide_timer.stop()
        self.show()
        set_window_always_on_top(int(self.winId()), True)
        self.hide_timer.start(12000)  # 12 seconds (~5s longer than previous 7s)