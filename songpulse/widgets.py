import ctypes
import ctypes.wintypes
import math
import os
import time
import webbrowser

from PyQt6.QtCore import (
    QAbstractNativeEventFilter,
    QEvent,
    QMimeData,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QDrag,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QRadialGradient,
    QStandardItem,
    QStandardItemModel,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMenu,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .audio import WAVEFORM_BARS
from .config import (
    APP_NAME,
    open_external_url,
    set_window_always_on_top,
    set_windows_titlebar_theme,
)
from .theme import (
    create_app_pixmap,
    create_ui_icon,
    get_cached_blurred_source,
    render_blurred_pixmap,
    rounded_pixmap,
)
from .themes_manager import resolve_theme_image_path
from .waveforms import (
    paint_miku_centerline_bars,
    paint_miku_centerline_pulse,
    paint_miku_hair_ties,
    paint_teto_bow,
)
from .workers import build_song_platform_links


def _paint_surface(
    painter: QPainter,
    rect: QRect,
    cfg: dict,
    cache_dict: dict,
    is_sidebar: bool = False,
    exact_thumb: bool = False,
):
    """Paints solid color, angled linear gradient, or photo/cutout with glass overlay."""
    mode = (cfg or {}).get("type", "solid")
    c1 = QColor((cfg or {}).get("color1", "#111216"))
    c2 = QColor((cfg or {}).get("color2", c1.name()))

    # Always paint the base gradient/solid behind images so transparent PNG cutouts look seamless!
    if mode == "gradient" or (mode == "image" and c1 != c2):
        angle_rad = math.radians(float(cfg.get("angle", 135)))
        cx, cy = rect.width() / 2.0, rect.height() / 2.0
        dx = math.cos(angle_rad) * cx
        dy = math.sin(angle_rad) * cy
        grad = QLinearGradient(cx - dx, cy - dy, cx + dx, cy + dy)
        grad.setColorAt(0.0, c1)
        grad.setColorAt(1.0, c2)
        painter.fillRect(rect, QBrush(grad))
    else:
        painter.fillRect(rect, c1)

    if mode == "image" and (cfg or {}).get("image"):
        img_path = str(cfg["image"])
        if not os.path.isfile(img_path):
            img_path = resolve_theme_image_path(img_path)
        blur = int(cfg.get("blur", 8 if is_sidebar else 6))
        fit_mode = str(cfg.get("fit", "cover"))
        if exact_thumb:
            key = (img_path, rect.width(), rect.height(), blur, fit_mode)
            pix = cache_dict.get(key)
            if pix is None:
                cache_dict.clear()
                pix = render_blurred_pixmap(
                    img_path, rect.width(), rect.height(), blur, fit_mode=fit_mode
                )
                cache_dict[key] = pix
            if not pix.isNull():
                painter.drawPixmap(rect.topLeft(), pix)
        else:
            pix = get_cached_blurred_source(img_path, blur_radius=blur)
            if not pix.isNull() and rect.width() > 2 and rect.height() > 2:
                painter.save()
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                iw = float(pix.width())
                ih = float(pix.height())
                w = float(rect.width())
                h = float(rect.height())
                if fit_mode == "cutout":
                    scale = max((w * 1.18) / max(1.0, iw), (h * 0.68) / max(1.0, ih))
                    nw = iw * scale
                    nh = ih * scale
                    paste_x = (w - nw) / 2.0
                    paste_y = max(h * 0.24, h - nh + nh * 0.08)
                    painter.setClipRect(rect)
                    painter.drawPixmap(
                        QRectF(rect.x() + paste_x, rect.y() + paste_y, nw, nh),
                        pix,
                        QRectF(0.0, 0.0, iw, ih),
                    )
                else:
                    scale = max(w / max(1.0, iw), h / max(1.0, ih))
                    sw = w / scale
                    sh = h / scale
                    sx = (iw - sw) / 2.0
                    sy = (ih - sh) / 2.0
                    painter.drawPixmap(QRectF(rect), pix, QRectF(sx, sy, sw, sh))
                painter.restore()

        if img_path:
            alpha = int(cfg.get("glass_alpha" if is_sidebar else "overlay_alpha", 115))
            if alpha > 0:
                a_val = max(0, min(240, alpha))
                if cfg.get("darken_black", False):
                    overlay = QColor(0, 0, 0, a_val)
                else:
                    overlay = QColor(c1.red(), c1.green(), c1.blue(), a_val)
                painter.fillRect(rect, overlay)


class ThemedBackgroundWidget(QWidget):
    """Root container that paints solid, gradient, or blurred photo theme backgrounds."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Root")
        self._bg_cfg = {"type": "solid", "color1": "#111216"}
        self._pix_cache = {}

    def set_background_config(self, cfg: dict):
        new_cfg = dict(cfg or {})
        old_sig = (self._bg_cfg.get("image"), self._bg_cfg.get("blur"), self._bg_cfg.get("fit"))
        new_sig = (new_cfg.get("image"), new_cfg.get("blur"), new_cfg.get("fit"))
        self._bg_cfg = new_cfg
        if old_sig != new_sig:
            self._pix_cache.clear()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        _paint_surface(p, self.rect(), self._bg_cfg, self._pix_cache, is_sidebar=False)
        p.end()


class GlassSidebarFrame(QFrame):
    """Sidebar frame supporting solid, gradient, or iPhone-style lightly blurred frosted photo."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self._sb_cfg = {"type": "solid", "color1": "#17181D"}
        self._pix_cache = {}

    def set_sidebar_config(self, cfg: dict):
        new_cfg = dict(cfg or {})
        old_sig = (self._sb_cfg.get("image"), self._sb_cfg.get("blur"), self._sb_cfg.get("fit"))
        new_sig = (new_cfg.get("image"), new_cfg.get("blur"), new_cfg.get("fit"))
        self._sb_cfg = new_cfg
        if old_sig != new_sig:
            self._pix_cache.clear()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        _paint_surface(p, self.rect(), self._sb_cfg, self._pix_cache, is_sidebar=True)
        # Subtle glass edge highlight on photo sidebars
        if self._sb_cfg.get("type") == "image":
            p.setPen(QPen(QColor(255, 255, 255, 34), 1))
            p.drawLine(self.width() - 1, 0, self.width() - 1, self.height())
        p.end()


class LoadingSplash(QWidget):
    """Compact startup window shown while SongPulse initializes."""

    def __init__(self, dark: bool = True, icon_colors: tuple[str, str] = ("#3B82F6", "#8B5CF6")):
        super().__init__(
            None,
            Qt.WindowType.SplashScreen
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(290, 118)

        c1, c2 = icon_colors or ("#3B82F6", "#8B5CF6")
        bg = "#151821" if dark else "#FFFFFF"
        border = "#2D3244" if dark else "#CBD5E1"
        fg = "#F8FAFC" if dark else "#0F172A"
        muted = "#94A3B8" if dark else "#475569"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)

        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background-color: {bg}; border: 1px solid {border}; border-radius: 16px; }}"
        )
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(12)
        icon_lbl = QLabel()
        icon_lbl.setPixmap(create_app_pixmap(38, c1, c2))
        icon_lbl.setFixedSize(38, 38)
        icon_lbl.setStyleSheet("border: none; background: transparent;")

        txt_col = QVBoxLayout()
        txt_col.setSpacing(2)
        title = QLabel(f"Opening {APP_NAME}...")
        title.setStyleSheet(
            f"color: {fg}; font-size: 14px; font-weight: 700; border: none; background: transparent;"
        )
        sub = QLabel("Starting audio engine")
        sub.setStyleSheet(
            f"color: {muted}; font-size: 11px; border: none; background: transparent;"
        )
        txt_col.addWidget(title)
        txt_col.addWidget(sub)

        top.addWidget(icon_lbl)
        top.addLayout(txt_col, 1)
        lay.addLayout(top)

        bar = QProgressBar()
        bar.setRange(0, 0)
        bar.setTextVisible(False)
        bar.setFixedHeight(5)
        bar.setStyleSheet(
            f"QProgressBar {{ background: {'rgba(255,255,255,0.10)' if dark else 'rgba(15,23,42,0.08)'}; "
            f"border: none; border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background: {c1}; border-radius: 2px; }}"
        )
        lay.addWidget(bar)
        outer.addWidget(card)

        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(
                geo.center().x() - self.width() // 2,
                geo.center().y() - self.height() // 2,
            )


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


class ElidedLinkButton(QPushButton):
    """Clickable flat link button that cleanly elides overflowing text with '...'."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full_text = text or ""
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setText(self._full_text)

    def setText(self, text: str):
        self._full_text = text or ""
        if not self.toolTip():
            self.setToolTip(self._full_text)
        self._update_elided()

    def sizeHint(self):
        fm = QFontMetrics(self.font())
        w = fm.horizontalAdvance(self._full_text) + 12
        h = max(22, fm.height() + 4)
        return QSize(w, h)

    def minimumSizeHint(self):
        fm = QFontMetrics(self.font())
        return QSize(0, max(22, fm.height() + 4))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_elided()

    def _update_elided(self):
        fm = QFontMetrics(self.font())
        icon_pad = 18 if not self.icon().isNull() else 4
        avail = max(24, self.width() - icon_pad)
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
        return self._do_layout(QRect(0, 0, max(1, width), 0), test_only=True)

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
        avail_w = max(1, eff.width())
        x = eff.x()
        y = eff.y()
        line_height = 0

        for item in self._items:
            hint = item.sizeHint()
            w = min(hint.width(), avail_w)
            h = hint.height()
            next_x = x + w + self._h_space
            if next_x - self._h_space > eff.right() and line_height > 0:
                x = eff.x()
                y += line_height + self._v_space
                next_x = x + w + self._h_space
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), QSize(w, h)))
            x = next_x
            line_height = max(line_height, h)

        return y + line_height - rect.y() + m.bottom()


class FlowWidget(QWidget):
    """Height-for-width container around FlowLayout so wrapped buttons resize parent cards cleanly."""

    def __init__(self, h_spacing=6, v_spacing=6, parent=None):
        super().__init__(parent)
        self._flow = FlowLayout(self, margin=0, h_spacing=h_spacing, v_spacing=v_spacing)
        self.setLayout(self._flow)
        sp = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        self.setMinimumWidth(0)

    def addWidget(self, widget: QWidget):
        self._flow.addWidget(widget)
        self._sync_height()

    def clear(self):
        while self._flow.count():
            it = self._flow.takeAt(0)
            if it and it.widget():
                it.widget().deleteLater()
        self._sync_height()

    def count(self) -> int:
        return self._flow.count()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._flow.heightForWidth(max(1, width))

    def sizeHint(self) -> QSize:
        w = self.width() if self.width() > 20 else 420
        return QSize(w, self.heightForWidth(w))

    def minimumSizeHint(self) -> QSize:
        w = self.width() if self.width() > 20 else 200
        return QSize(0, self.heightForWidth(w))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_height()

    def _sync_height(self):
        w = self.width()
        if w > 10:
            h = max(0, self._flow.heightForWidth(w))
            if self.minimumHeight() != h or self.maximumHeight() != h:
                self.setFixedHeight(h)
        self.updateGeometry()


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
    """Returns the best direct URL for a song."""
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
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
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


class _WinWheelNativeFilter(QAbstractNativeEventFilter):
    """Intercepts Windows WM_MOUSEWHEEL at the OS message loop while a popup menu is open."""

    def __init__(self, on_wheel_delta):
        super().__init__()
        self._on_wheel_delta = on_wheel_delta

    def nativeEventFilter(self, eventType, message):
        if eventType in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            try:
                msg = ctypes.wintypes.MSG.from_address(int(message))
                if msg.message == 0x020A:  # WM_MOUSEWHEEL
                    delta = ctypes.c_short((int(msg.wParam) >> 16) & 0xFFFF).value
                    if delta != 0 and self._on_wheel_delta(delta):
                        return True, 0
            except Exception:
                pass
        return False, 0


class ScrollThroughMenu(QMenu):
    """QMenu that keeps a target QScrollArea scrollable with the mouse wheel while open."""

    def __init__(
        self,
        scroll_area: QScrollArea | None = None,
        parent=None,
        install_app_filter: bool = True,
    ):
        super().__init__(parent)
        self._scroll_area = scroll_area
        self._install_app_filter = bool(install_app_filter)
        self._native_filter = None
        self._filter_installed = False

    def _scroll_by_angle_delta(self, ang_dy: int) -> bool:
        if self._scroll_area is None or ang_dy == 0:
            return False
        vbar = self._scroll_area.verticalScrollBar()
        if vbar is None or vbar.maximum() <= vbar.minimum():
            return False
        step = max(20, vbar.singleStep()) * max(1, QApplication.wheelScrollLines())
        px = int(round((ang_dy / 120.0) * step))
        if px == 0:
            px = 1 if ang_dy > 0 else -1
        vbar.setValue(vbar.value() - px)
        return True

    def _on_native_wheel(self, delta: int) -> bool:
        if not self.isVisible() or self._scroll_area is None:
            return False
        return self._scroll_by_angle_delta(delta)

    def _scroll_from_wheel(self, event) -> bool:
        if self._scroll_area is None:
            return False
        vbar = self._scroll_area.verticalScrollBar()
        if vbar is None or vbar.maximum() <= vbar.minimum():
            return False
        pix_dy = event.pixelDelta().y()
        if pix_dy != 0:
            vbar.setValue(vbar.value() - pix_dy)
            event.accept()
            return True
        ang_dy = event.angleDelta().y()
        if ang_dy != 0 and self._scroll_by_angle_delta(ang_dy):
            event.accept()
            return True
        return False

    def _detach_wheel_filter(self):
        if self._filter_installed and self._native_filter is not None:
            app = QApplication.instance()
            if app is not None:
                app.removeNativeEventFilter(self._native_filter)
            self._filter_installed = False

    def showEvent(self, event):
        super().showEvent(event)
        if self._install_app_filter and not self._filter_installed and self._scroll_area is not None:
            app = QApplication.instance()
            if app is not None:
                if self._native_filter is None:
                    self._native_filter = _WinWheelNativeFilter(self._on_native_wheel)
                app.installNativeEventFilter(self._native_filter)
                self._filter_installed = True

    def hideEvent(self, event):
        self._detach_wheel_filter()
        super().hideEvent(event)

    def closeEvent(self, event):
        self._detach_wheel_filter()
        super().closeEvent(event)

    def wheelEvent(self, event):
        if self._scroll_from_wheel(event):
            return
        super().wheelEvent(event)


class _CheckboxHitArea(QWidget):
    """Expands clickable hitbox around a small QCheckBox without enlarging the visual box."""

    def __init__(self, checkbox: QCheckBox, parent=None):
        super().__init__(parent)
        self.chk = checkbox
        self.setFixedSize(36, 48)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("background: transparent; border: none;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(checkbox)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.chk.toggle()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.chk.toggle()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mouseReleaseEvent(event)


class HistoryRowCard(QFrame):
    """Library song card with selection checkbox, Like heart, hover glow & optional manual reorder."""

    selection_toggled = pyqtSignal(str, bool)
    like_toggled = pyqtSignal(str)
    move_requested = pyqtSignal(str, int)  # (song_id, -1 up or +1 down)
    drop_reorder = pyqtSignal(str, str)  # (dragged_song_id, target_song_id)
    context_menu_requested = pyqtSignal(dict, QPoint)

    _context_menu_open = False
    _suppress_next_card_click = False
    _suppress_until = 0.0

    @classmethod
    def should_suppress_card_click(cls) -> bool:
        if cls._context_menu_open:
            return True
        if cls._suppress_next_card_click and time.monotonic() < cls._suppress_until:
            cls._suppress_next_card_click = False
            return True
        cls._suppress_next_card_click = False
        return False

    def __init__(
        self,
        song,
        p_icons,
        details_icon,
        on_details,
        on_delete,
        checked=False,
        allow_reorder=False,
        delete_tooltip="Remove from library",
        initial_compact=False,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("SongCard")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._glow = QGraphicsDropShadowEffect(self)
        self._glow.setBlurRadius(20)
        self._glow.setOffset(0, 0)
        self._glow.setColor(QColor(59, 130, 246, 135))
        self._glow.setEnabled(False)
        self.setGraphicsEffect(self._glow)
        self._compact = None
        self._link_btns = []

        self._song = song
        self._song_id = song.get("id", "")
        self._on_details = on_details
        self._allow_reorder = bool(allow_reorder)
        self._drag_start_pos = None
        self._right_press_pos = None
        self._last_ctx_ts = 0.0
        self.setAcceptDrops(self._allow_reorder)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        row = QHBoxLayout(self)
        row.setContentsMargins(6, 7, 10, 7)
        row.setSpacing(6)

        # Selection checkbox
        self.select_chk = QCheckBox()
        self.select_chk.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.select_chk.setChecked(checked)
        self.select_chk.setFixedSize(16, 16)
        self.select_chk.setCursor(Qt.CursorShape.PointingHandCursor)
        self.select_chk.setToolTip("Select song")
        self.select_chk.stateChanged.connect(
            lambda st: self.selection_toggled.emit(
                self._song_id, st == Qt.CheckState.Checked.value
            )
        )
        self._chk_hit = _CheckboxHitArea(self.select_chk)
        row.addWidget(self._chk_hit)

        # Optional manual reorder handle (up/down) for custom playlists
        if self._allow_reorder:
            mv_col = QVBoxLayout()
            mv_col.setContentsMargins(0, 0, 0, 0)
            mv_col.setSpacing(1)
            up_btn = QPushButton("▲")
            up_btn.setObjectName("SubtleIconBtn")
            up_btn.setFixedSize(18, 16)
            up_btn.setToolTip("Move up (or drag card)")
            up_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            up_btn.clicked.connect(lambda: self.move_requested.emit(self._song_id, -1))

            dn_btn = QPushButton("▼")
            dn_btn.setObjectName("SubtleIconBtn")
            dn_btn.setFixedSize(18, 16)
            dn_btn.setToolTip("Move down (or drag card)")
            dn_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            dn_btn.clicked.connect(lambda: self.move_requested.emit(self._song_id, 1))

            mv_col.addWidget(up_btn)
            mv_col.addWidget(dn_btn)
            row.addLayout(mv_col)

        cover = QLabel()
        cover.setFixedSize(46, 46)
        cover.setPixmap(rounded_pixmap(song.get("cover_path", ""), 46, 8))
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

        raw_ts = song.get("timestamp", "")
        if raw_ts:
            prefix = "Reidentified on" if song.get("reidentified") else "Identified on"
            ts_text = (
                raw_ts
                if raw_ts.lower().startswith(("identified on", "reidentified on"))
                else f"{prefix} {raw_ts}"
            )
        else:
            ts_text = ""
        ts_lbl = ElidedLabel(ts_text)
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

        # Like heart button
        liked = bool(song.get("liked", False))
        self.like_btn = QPushButton("♥" if liked else "♡")
        self.like_btn.setObjectName("SubtleIconBtn")
        self.like_btn.setFixedSize(28, 28)
        self.like_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.like_btn.setToolTip("Remove from Liked Songs" if liked else "Add to Liked Songs")
        if liked:
            self.like_btn.setStyleSheet("color: #F43F5E; font-size: 15px;")
        else:
            self.like_btn.setStyleSheet("font-size: 15px;")
        self.like_btn.clicked.connect(lambda: self.like_toggled.emit(self._song_id))
        row.addWidget(self.like_btn)

        all_links = build_song_platform_links(song)
        for pname, icon_key, url in all_links[:2]:
            b = QPushButton(pname)
            b.setObjectName("PlatformBtn")
            b.setIcon(p_icons.get(icon_key, p_icons["web"]))
            b.setIconSize(QSize(13, 13))
            b.setToolTip(f"Open on {pname}")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda checked=False, u=url: open_external_url(u))
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
                act.triggered.connect(lambda checked=False, u=url: open_external_url(u))
                m.addAction(act)
            self.more_btn.setMenu(m)
            row.addWidget(self.more_btn)

        self.details_btn = QPushButton("Details")
        self.details_btn.setObjectName("PlatformBtn")
        self.details_btn.setIcon(details_icon)
        self.details_btn.setIconSize(QSize(13, 13))
        self.details_btn.setToolTip("View song details")
        self.details_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.details_btn.clicked.connect(
            lambda checked=False: None
            if HistoryRowCard.should_suppress_card_click()
            else on_details(song)
        )
        row.addWidget(self.details_btn)

        del_btn = QPushButton("✕")
        del_btn.setObjectName("IconDangerBtn")
        del_btn.setFixedSize(28, 28)
        del_btn.setToolTip(delete_tooltip)
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.clicked.connect(lambda checked=False: on_delete(self._song_id))
        row.addWidget(del_btn)

        self._apply_compact_mode(bool(initial_compact))

    def set_liked(self, liked: bool):
        liked = bool(liked)
        self._song["liked"] = liked
        self.like_btn.setText("♥" if liked else "♡")
        self.like_btn.setToolTip(
            "Remove from Liked Songs" if liked else "Add to Liked Songs"
        )
        if liked:
            self.like_btn.setStyleSheet("color: #F43F5E; font-size: 15px;")
        else:
            self.like_btn.setStyleSheet("font-size: 15px;")

    def set_checked_silent(self, checked: bool):
        self.select_chk.blockSignals(True)
        self.select_chk.setChecked(checked)
        self.select_chk.blockSignals(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w = self.width()
        if self._compact is None:
            self._apply_compact_mode(w < 680)
        elif self._compact and w > 696:
            self._apply_compact_mode(False)
        elif not self._compact and w < 664:
            self._apply_compact_mode(True)

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

    def _emit_context_menu(self, global_pos: QPoint):
        now = time.monotonic()
        if now - self._last_ctx_ts < 0.25:
            return
        self._last_ctx_ts = now
        self.context_menu_requested.emit(self._song, global_pos)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if HistoryRowCard.should_suppress_card_click():
                self._drag_start_pos = None
                event.accept()
                return
            self._drag_start_pos = event.position().toPoint()
            return
        if event.button() == Qt.MouseButton.RightButton:
            self._right_press_pos = event.globalPosition().toPoint()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._allow_reorder
            and self._drag_start_pos is not None
            and (event.buttons() & Qt.MouseButton.LeftButton)
        ):
            dist = (event.position().toPoint() - self._drag_start_pos).manhattanLength()
            if dist >= QApplication.startDragDistance():
                self._drag_start_pos = None
                drag = QDrag(self)
                mime = QMimeData()
                mime.setData("application/x-songpulse-id", self._song_id.encode("utf-8"))
                drag.setMimeData(mime)
                drag.exec(Qt.DropAction.MoveAction)
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            pos = event.globalPosition().toPoint()
            had_press = self._right_press_pos is not None
            self._right_press_pos = None
            event.accept()
            if had_press:
                self._emit_context_menu(pos)
            return
        if event.button() == Qt.MouseButton.LeftButton and self._drag_start_pos is not None:
            self._drag_start_pos = None
            if not HistoryRowCard._context_menu_open:
                self._on_details(self._song)
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        self._emit_context_menu(event.globalPos())
        event.accept()

    def dragEnterEvent(self, event):
        if self._allow_reorder and event.mimeData().hasFormat("application/x-songpulse-id"):
            event.acceptProposedAction()
            self._glow.setEnabled(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._glow.setEnabled(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._glow.setEnabled(False)
        if self._allow_reorder and event.mimeData().hasFormat("application/x-songpulse-id"):
            dragged = bytes(event.mimeData().data("application/x-songpulse-id")).decode("utf-8", errors="ignore")
            if dragged and dragged != self._song_id:
                self.drop_reorder.emit(dragged, self._song_id)
            event.acceptProposedAction()
        else:
            event.ignore()

    def enterEvent(self, event):
        self._glow.setEnabled(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._glow.setEnabled(False)
        super().leaveEvent(event)


class PulseIdentifyWidget(QWidget):
    """Customizable Identify button + live audio waveform (radial, horizontal split, bottom bar, or dots)."""

    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase = 0.0
        self._scanning = False
        self._dark = True
        self._bar_count = WAVEFORM_BARS
        self._bars = [0.0] * self._bar_count
        self._bars_l = [0.0] * self._bar_count
        self._bars_r = [0.0] * self._bar_count
        self._energy = 0.0
        self._audio_active = False
        self._vis_alpha = 0.0

        # Customizable waveform properties
        self._wf_style = "radial"  # "radial", "horizontal", "pulse_line", "bar", "dots"
        self._wf_mirror = False
        self._wf_idle_dots = False
        self._wf_flip_hz = False
        self._wf_hair_ties = False
        self._wf_right_alt_white = False
        self._wf_teto_bow = False
        self._wf_max_width = 620
        self._wf_step = 0.0
        self._color1 = QColor("#3B82F6")
        self._color2 = QColor("#8B5CF6")

        self.btn = QPushButton("Listen", self)
        self.btn.setObjectName("IdentifyBtn")
        self.btn.setFixedSize(132, 132)
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.clicked.connect(self.clicked.emit)

        self._apply_geometry_mode()

    def configure_waveform(
        self,
        style: str = "radial",
        mirror: bool = False,
        idle_dots: bool = False,
        max_width: int = 620,
        color1: str = "#3B82F6",
        color2: str = "#8B5CF6",
        flip_hz: bool = False,
        hair_ties: bool = False,
        right_alt_white: bool = False,
        teto_bow: bool = False,
        step: float = 0.0,
    ):
        self._wf_style = (
            style
            if style in ("radial", "horizontal", "pulse_line", "bar", "dots")
            else "radial"
        )
        self._wf_mirror = bool(mirror)
        self._wf_idle_dots = bool(idle_dots)
        self._wf_flip_hz = bool(flip_hz)
        self._wf_hair_ties = bool(hair_ties)
        self._wf_right_alt_white = bool(right_alt_white)
        self._wf_teto_bow = bool(teto_bow)
        self._wf_max_width = max(260, min(900, int(max_width or 620)))
        self._wf_step = max(0.0, min(32.0, float(step or 0.0)))
        self._color1 = QColor(color1 or "#3B82F6")
        self._color2 = QColor(color2 or "#8B5CF6")
        self._apply_geometry_mode()
        self.update()

    def _apply_geometry_mode(self):
        if self._wf_style in ("horizontal", "pulse_line"):
            self.setMinimumSize(240, 184)
            self.setMaximumSize(self._wf_max_width, 184)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        elif self._wf_style == "bar":
            self.setMinimumSize(240, 218)
            self.setMaximumSize(self._wf_max_width, 218)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        else:
            self.setMinimumSize(236, 236)
            self.setMaximumSize(self._wf_max_width, 236)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._center_button()

    def _center_button(self):
        bx = (self.width() - self.btn.width()) // 2
        if self._wf_style == "bar":
            by = 12
        else:
            by = (self.height() - self.btn.height()) // 2
        self.btn.move(max(0, bx), max(0, by))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._center_button()

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
        self._bars_l = [0.0] * self._bar_count
        self._bars_r = [0.0] * self._bar_count
        self._energy = 0.0
        self._audio_active = False
        self._vis_alpha = 0.0
        self.update()

    def step_animation(
        self,
        spectrum_bands,
        rms_level,
        has_outputs=True,
        left_bands=None,
        right_bands=None,
    ):
        if not has_outputs and not self._scanning:
            self._audio_active = False
            self._vis_alpha = max(0.0, self._vis_alpha - 0.08)
            if (
                self._energy > 0.0
                or any(b > 0.0 for b in self._bars)
                or any(b > 0.0 for b in self._bars_l)
                or any(b > 0.0 for b in self._bars_r)
            ):
                self._bars = [b * 0.80 if b > 0.005 else 0.0 for b in self._bars]
                self._bars_l = [b * 0.80 if b > 0.005 else 0.0 for b in self._bars_l]
                self._bars_r = [b * 0.80 if b > 0.005 else 0.0 for b in self._bars_r]
                self._energy *= 0.80
                self.update()
            elif self._wf_idle_dots or self._vis_alpha > 0.0:
                self.update()
            return

        # Whilst listening (scanning), glide smoothly and slowly
        if self._scanning:
            self._phase += 0.055
            self._audio_active = True
            self._vis_alpha = min(1.0, self._vis_alpha + 0.14)
            self._energy = self._energy * 0.90 + 0.55 * 0.10
            n = self._bar_count
            for i in range(n):
                wave = 0.5 + 0.5 * math.sin(self._phase * 1.45 + i * 0.42)
                target = 0.18 + 0.58 * wave
                val = self._bars[i] * 0.86 + target * 0.14
                self._bars[i] = val
                self._bars_l[i] = val
                self._bars_r[i] = val
            self.update()
            return

        self._phase += 0.08
        self._energy = self._energy * 0.65 + rms_level * 0.35
        has_sound = bool(
            rms_level >= 0.0008
            and spectrum_bands is not None
            and any(float(v) >= 0.02 for v in spectrum_bands)
        )
        self._audio_active = has_sound

        # Fade visibility smoothly between audio and silence
        target_vis = 1.0 if has_sound else 0.0
        vis_rate = 0.28 if target_vis > self._vis_alpha else 0.08
        self._vis_alpha = self._vis_alpha * (1.0 - vis_rate) + target_vis * vis_rate
        if self._vis_alpha < 0.01 and not has_sound:
            self._vis_alpha = 0.0

        src_l = left_bands if (left_bands is not None and len(left_bands) == self._bar_count) else spectrum_bands
        src_r = right_bands if (right_bands is not None and len(right_bands) == self._bar_count) else spectrum_bands

        def _smooth_step(arr, src):
            n = self._bar_count
            for i in range(n):
                if has_sound and src is not None and len(src) == n:
                    live_val = float(src[i])
                    target = min(1.0, live_val) if live_val >= 0.015 else 0.0
                else:
                    target = 0.0

                cur = arr[i]
                if target > cur:
                    alpha = 0.72
                else:
                    alpha = 0.14 + 0.28 * cur
                val = cur * (1.0 - alpha) + target * alpha
                arr[i] = 0.0 if val < 0.004 else val

        _smooth_step(self._bars, spectrum_bands)
        _smooth_step(self._bars_l, src_l)
        _smooth_step(self._bars_r, src_r)

        self.update()

    def _bar_color(self, blend: float) -> tuple[int, int, int]:
        r = int(self._color1.red() + (self._color2.red() - self._color1.red()) * blend)
        g = int(self._color1.green() + (self._color2.green() - self._color1.green()) * blend)
        b = int(self._color1.blue() + (self._color2.blue() - self._color1.blue()) * blend)
        return r, g, b

    def _effective_bars(self, count: int) -> list[float]:
        src = self._bars
        n = len(src)
        if self._wf_mirror and count >= 2:
            half_c = count // 2
            half = [src[int((i / max(1, half_c)) * n) % n] for i in range(half_c)]
            if count % 2 == 1:
                return half + [half[-1]] + half[::-1]
            return half + half[::-1]
        return [src[int((i / max(1, count)) * n) % n] for i in range(count)]

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx = self.width() / 2.0
        cy = self.btn.y() + self.btn.height() / 2.0

        # Subtle outgoing ring pulse from the Listen button whenever audio is playing
        if self._wf_style != "radial" and not self._scanning and self._audio_active and self._energy > 0.015:
            self._paint_playing_ring_pulse(p, cx, cy)

        if self._wf_style == "horizontal":
            self._paint_horizontal_split(p, cx, cy)
            if self._wf_hair_ties:
                self._paint_miku_hair_ties(p, cx, cy)
        elif self._wf_style == "pulse_line":
            self._paint_pulse_line(p, cx, cy)
            if self._wf_hair_ties:
                self._paint_miku_hair_ties(p, cx, cy)
        elif self._wf_style == "bar":
            self._paint_bottom_bar(p, cx)
        elif self._wf_style == "dots":
            self._paint_dots_ring(p, cx, cy)
        else:
            self._paint_radial(p, cx, cy)

        if self._wf_teto_bow:
            self._paint_teto_bow(p, cx, cy)

        # Breathing glow around the Listen button while scanning
        if self._scanning:
            pulse = 0.5 + 0.5 * math.sin(self._phase * 1.45)
            glow_r = 76.0 + pulse * 8.0
            rg = QRadialGradient(QPointF(cx, cy), glow_r)
            c1 = QColor(self._color1)
            c2 = QColor(self._color2)
            c1.setAlpha(int(76 + pulse * 46))
            c2.setAlpha(int(30 + pulse * 24))
            rg.setColorAt(0.0, c1)
            rg.setColorAt(0.70, c1)
            rg.setColorAt(0.86, c2)
            rg.setColorAt(1.0, QColor(c2.red(), c2.green(), c2.blue(), 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(rg))
            p.drawEllipse(QPointF(cx, cy), glow_r, glow_r)

        p.end()

    def _paint_playing_ring_pulse(self, p: QPainter, cx: float, cy: float):
        """Paints outgoing ring pulses from the Listen button that taper thinner and fade out cleanly."""
        strength = max(0.55, min(1.0, self._energy * 4.5)) * self._vis_alpha
        if strength <= 0.02:
            return
        max_travel = 22.5
        for r_idx in range(2):
            prog = (self._phase * 0.115 + r_idx * 0.5) % 1.0
            radius = 66.0 + prog * max_travel
            alpha = int(((1.0 - prog) ** 1.35) * 215 * strength)
            if alpha <= 2:
                continue
            pen_w = 0.35 + 4.00 * ((1.0 - prog) ** 1.15)
            r, g, b = self._bar_color(prog * 0.55)
            ring_color = QColor(r, g, b, max(0, min(255, alpha)))
            p.setPen(QPen(ring_color, pen_w))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(cx, cy), radius, radius)

    def _paint_miku_hair_ties(self, p: QPainter, cx: float, cy: float):
        paint_miku_hair_ties(p, float(self.width()), float(self.height()), cx, cy)

    def _paint_teto_bow(self, p: QPainter, cx: float, cy: float):
        paint_teto_bow(p, float(self.width()), float(self.height()), cx, cy)

    def _paint_radial(self, p: QPainter, cx: float, cy: float):
        inner_r = 75.0
        max_bar_len = 29.0

        ripple_strength = 0.85 if self._scanning else min(0.75, self._energy * 1.5)
        if ripple_strength > 0.08:
            for ring_idx in range(2):
                prog = (self._phase * 0.25 + ring_idx * 0.5) % 1.0
                radius = inner_r + prog * 34.0
                alpha = int((1.0 - prog) * 85 * ripple_strength)
                ring_color = QColor(self._color1)
                ring_color.setAlpha(max(0, min(255, alpha)))
                p.setPen(QPen(ring_color, 1.8))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(cx, cy), radius, radius)

        vis = 1.0 if self._wf_idle_dots else self._vis_alpha
        if vis <= 0.01:
            return

        count = (
            max(12, min(48, int(round(360.0 / self._wf_step))))
            if self._wf_step >= 4.0
            else self._bar_count
        )
        bars = self._effective_bars(count)
        for i, amp in enumerate(bars):
            theta = (2.0 * math.pi * i) / count - (math.pi / 2.0)
            blend = 0.5 + 0.5 * math.sin(theta + self._phase * 0.35)
            r, g, b = self._bar_color(blend)

            bar_len = max(0.2, amp * max_bar_len) if self._wf_idle_dots else (amp * max_bar_len)
            x1 = cx + math.cos(theta) * inner_r
            y1 = cy + math.sin(theta) * inner_r
            x2 = cx + math.cos(theta) * (inner_r + bar_len)
            y2 = cy + math.sin(theta) * (inner_r + bar_len)

            core_w = 6.8 + min(1.0, amp * 3.0) * 3.2
            glow_w = core_w + 4.4
            idle_dim = 0.62 + 0.38 * self._vis_alpha if self._wf_idle_dots else vis
            glow_a = int((28 + amp * 68) * idle_dim)
            core_a = int((165 + min(90, amp * 105)) * idle_dim)

            p.setPen(QPen(QColor(r, g, b, glow_a), glow_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

            p.setPen(QPen(QColor(r, g, b, core_a), core_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    def _paint_horizontal_split(self, p: QPainter, cx: float, cy: float):
        """Two-way up-down vertical pill bars extending left & right of Listen button."""
        btn_gap = 102.0 if self._wf_hair_ties else 80.0
        step = self._wf_step if self._wf_step >= 4.0 else 16.0
        usable_w = min(self.width(), self._wf_max_width)
        side_avail = max(0.0, (usable_w / 2.0) - btn_gap - 4.0)
        bars_per_side = max(0, min(80, int(side_avail // step)))
        if bars_per_side == 0:
            return

        vis = 1.0 if self._wf_idle_dots else self._vis_alpha
        if vis <= 0.01:
            return

        # Miku theme: subtle thin straight gray centerline connecting the Listen button behind & between waveforms
        if self._wf_hair_ties:
            paint_miku_centerline_bars(p, cx, cy, btn_gap, bars_per_side, step)

        max_half_h = 46.0

        for side in (-1, 1):
            # True stereo L/R unless Mirrored is enabled
            if self._wf_mirror:
                channel_bars = self._bars
            else:
                channel_bars = self._bars_l if side == -1 else self._bars_r
            n_src = len(channel_bars)
            for idx in range(bars_per_side):
                x = cx + side * (btn_gap + idx * step)
                pos_idx = (bars_per_side - 1 - idx) if self._wf_flip_hz else idx
                src_i = int((pos_idx / max(1, bars_per_side)) * n_src) % n_src
                amp = channel_bars[src_i]

                if self._wf_flip_hz and bars_per_side > 1:
                    t_out = idx / float(bars_per_side - 1)
                    amp = min(1.0, amp * (0.68 + 0.42 * t_out))

                blend = idx / max(1, bars_per_side - 1) if bars_per_side > 1 else 0.5
                if self._wf_right_alt_white and side == 1 and (idx % 2 == 1):
                    r, g, b = 248, 250, 252
                else:
                    r, g, b = self._bar_color(blend)

                half_h = max(0.2, amp * max_half_h) if self._wf_idle_dots else max(0.1, amp * max_half_h)
                active_factor = min(1.0, amp * 4.0)
                core_w = 6.2 + active_factor * 1.4
                glow_w = core_w + 4.5

                # When idle (silent) with idle_dots on, softly dim the resting dots
                idle_dim = 0.62 + 0.38 * self._vis_alpha if self._wf_idle_dots else vis
                glow_a = int((26 + amp * 72) * idle_dim)
                core_a = int((175 + min(80, amp * 85)) * idle_dim)

                p.setPen(QPen(QColor(r, g, b, glow_a), glow_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(x, cy - half_h), QPointF(x, cy + half_h))

                p.setPen(QPen(QColor(r, g, b, core_a), core_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(x, cy - half_h), QPointF(x, cy + half_h))

    def _paint_pulse_line(self, p: QPainter, cx: float, cy: float):
        """Continuous stereo L/R zig-zag oscilloscope pulse line flowing from inside Listen button and fading out at outer edges."""
        start_gap = 60.0
        step = self._wf_step if self._wf_step >= 4.0 else 9.2
        usable_w = min(self.width(), self._wf_max_width)
        side_avail = max(0.0, (usable_w / 2.0) - start_gap - 3.0)
        peaks_per_side = max(0, min(100, int(side_avail // step)))
        if peaks_per_side < 2:
            return

        vis = 1.0 if self._wf_idle_dots else self._vis_alpha
        if vis <= 0.01:
            return

        end_dist = start_gap + (peaks_per_side - 1) * step

        p.save()
        clip = QPainterPath()
        clip.addRect(0.0, 0.0, float(self.width()), float(self.height()))
        btn_hole = QPainterPath()
        btn_hole.addEllipse(QPointF(cx, cy), 65.0, 65.0)
        p.setClipPath(clip.subtracted(btn_hole))

        # Miku subtle light gray centerline behind the waveform, fading out cleanly toward the outside
        if self._wf_hair_ties:
            paint_miku_centerline_pulse(p, cx, cy, end_dist)

        max_amp = 50.0
        idle_dim = 0.65 + 0.35 * self._vis_alpha if self._wf_idle_dots else vis

        def _sample_bar(src_bars, norm_pos: float) -> float:
            n_src = len(src_bars)
            if n_src <= 1:
                return src_bars[0] if n_src == 1 else 0.0
            f = max(0.0, min(float(n_src - 1), norm_pos * (n_src - 1)))
            i0 = int(f)
            i1 = min(n_src - 1, i0 + 1)
            frac = f - i0
            return src_bars[i0] * (1.0 - frac) + src_bars[i1] * frac

        for side in (-1, 1):
            # True stereo unless Mirrored is enabled
            if self._wf_mirror:
                channel_bars = self._bars
            else:
                channel_bars = self._bars_l if side == -1 else self._bars_r
            path = QPainterPath()
            path.moveTo(cx + side * (start_gap - step), cy)

            for idx in range(peaks_per_side):
                x = cx + side * (start_gap + idx * step)
                pos_idx = (peaks_per_side - 1 - idx) if self._wf_flip_hz else idx
                norm = pos_idx / float(max(1, peaks_per_side - 1))

                amp = _sample_bar(channel_bars, norm)
                if self._wf_flip_hz and peaks_per_side > 1:
                    t_out = idx / float(peaks_per_side - 1)
                    amp = min(1.0, amp * (0.68 + 0.42 * t_out))

                # Alternate peaks up and down across the centerline
                sign = -1.0 if (idx % 2 == 0) else 1.0
                y = cy + sign * amp * max_amp
                path.lineTo(x, y)

            x_in = cx + side * 66.0
            x_out = cx + side * end_dist

            # Tight soft neon glow fading out smoothly at the outer end
            glow_grad = QLinearGradient(QPointF(x_in, cy), QPointF(x_out, cy))
            g1 = QColor(self._color1)
            g2 = QColor(self._color2)
            g3 = QColor(self._color2)
            g1.setAlpha(int(64 * idle_dim))
            g2.setAlpha(int(74 * idle_dim))
            g3.setAlpha(0)
            glow_grad.setColorAt(0.0, g1)
            glow_grad.setColorAt(0.90, g2)
            glow_grad.setColorAt(1.0, g3)
            p.setBrush(Qt.BrushStyle.NoBrush)
            glow_pen = QPen(
                QBrush(glow_grad),
                4.6,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
            p.setPen(glow_pen)
            p.drawPath(path)

            # Thin, smooth bright core pulse line fading out smoothly at the outer end
            core_grad = QLinearGradient(QPointF(x_in, cy), QPointF(x_out, cy))
            c1 = QColor(self._color1)
            c2 = QColor(self._color2)
            c3 = QColor(self._color2)
            c1.setAlpha(int(245 * idle_dim))
            c2.setAlpha(int(250 * idle_dim))
            c3.setAlpha(0)
            core_grad.setColorAt(0.0, c1)
            core_grad.setColorAt(0.90, c2)
            core_grad.setColorAt(1.0, c3)
            core_pen = QPen(
                QBrush(core_grad),
                3.0,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
            p.setPen(core_pen)
            p.drawPath(path)

        p.restore()

    def _paint_bottom_bar(self, p: QPainter, cx: float):
        vis = 1.0 if self._wf_idle_dots else self._vis_alpha
        if vis <= 0.01:
            return
        usable_w = min(self.width() - 24, self._wf_max_width)
        step = self._wf_step if self._wf_step >= 4.0 else 15.0
        count = max(6, min(120, int(usable_w // step)))
        start_x = cx - ((count - 1) * step) / 2.0
        base_y = self.height() - 26.0
        max_h = 30.0
        bars = self._effective_bars(count)

        for i, amp in enumerate(bars):
            x = start_x + i * step
            blend = i / max(1, count - 1)
            r, g, b = self._bar_color(blend)
            h = max(0.2, amp * max_h) if self._wf_idle_dots else (amp * max_h)
            idle_dim = 0.62 + 0.38 * self._vis_alpha if self._wf_idle_dots else vis
            p.setPen(QPen(QColor(r, g, b, int((28 + amp * 65) * idle_dim)), 10.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(x, base_y - h), QPointF(x, base_y + h * 0.35))
            p.setPen(QPen(QColor(r, g, b, int(225 * idle_dim)), 6.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(x, base_y - h), QPointF(x, base_y + h * 0.35))

    def _paint_dots_ring(self, p: QPainter, cx: float, cy: float):
        vis = 1.0 if self._wf_idle_dots else self._vis_alpha
        if vis <= 0.01:
            return
        inner_r = 77.0
        count = (
            max(12, min(48, int(round(360.0 / self._wf_step))))
            if self._wf_step >= 4.0
            else self._bar_count
        )
        bars = self._effective_bars(count)
        for i, amp in enumerate(bars):
            theta = (2.0 * math.pi * i) / count - (math.pi / 2.0)
            blend = 0.5 + 0.5 * math.sin(theta + self._phase * 0.35)
            r, g, b = self._bar_color(blend)
            dist = inner_r + amp * 22.0
            rad = 3.4 + amp * 4.0
            x = cx + math.cos(theta) * dist
            y = cy + math.sin(theta) * dist
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(r, g, b, int((130 + amp * 125) * vis))))
            p.drawEllipse(QPointF(x, y), rad, rad)


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


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    """Double spinbox that ignores mouse-wheel scrolling."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        event.ignore()


class NoWheelSlider(QSlider):
    """Horizontal slider that ignores mouse-wheel scrolling so Settings scrolls cleanly."""

    def __init__(self, orientation=Qt.Orientation.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        event.ignore()


class AudioOutputListWidget(QWidget):
    """Remembered audio outputs list with checkboxes, connection state, and per-device delete buttons."""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._remembered: list[str] = []
        self._selected: set[str] = set()
        self._connected: set[str] = set()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        self._container = QFrame()
        self._container.setObjectName("SongCard")
        self._rows_lay = QVBoxLayout(self._container)
        self._rows_lay.setContentsMargins(8, 6, 8, 6)
        self._rows_lay.setSpacing(4)
        outer.addWidget(self._container)

    def set_devices(
        self,
        remembered: list[str],
        selected: list[str],
        connected: list[str] | set[str],
        monitor_all: bool = False,
    ):
        self._connected = set(connected or [])
        dedup = []
        for name in remembered or []:
            if name and name not in dedup:
                dedup.append(name)
        self._remembered = dedup
        if monitor_all:
            self._selected = set(dedup)
        else:
            self._selected = {d for d in (selected or []) if d in dedup}
        self._rebuild()

    def merge_discovered(self, connected: list[str], auto_check_new: bool = True) -> list[str]:
        """Updates live connection state and appends any connected outputs not yet remembered."""
        self._connected = set(connected or [])
        added = []
        for name in connected or []:
            if name and name not in self._remembered:
                self._remembered.append(name)
                if auto_check_new:
                    self._selected.add(name)
                added.append(name)
        self._rebuild()
        return added

    def get_remembered(self) -> list[str]:
        return list(self._remembered)

    def get_selected(self) -> list[str]:
        return [d for d in self._remembered if d in self._selected]

    def _on_toggle(self, name: str, checked: bool):
        if checked:
            self._selected.add(name)
        else:
            self._selected.discard(name)
        self.changed.emit()

    def _on_delete(self, name: str):
        self._remembered = [d for d in self._remembered if d != name]
        self._selected.discard(name)
        self._rebuild()
        self.changed.emit()

    def _rebuild(self):
        while self._rows_lay.count():
            it = self._rows_lay.takeAt(0)
            if it and it.widget():
                it.widget().deleteLater()

        if not self._remembered:
            empty = QLabel("No audio outputs remembered. Click Scan to find connected outputs.")
            empty.setObjectName("Muted")
            empty.setWordWrap(True)
            empty.setStyleSheet("font-size: 11px; padding: 6px 4px;")
            self._rows_lay.addWidget(empty)
            return

        for name in self._remembered:
            row_w = QWidget()
            rl = QHBoxLayout(row_w)
            rl.setContentsMargins(2, 2, 2, 2)
            rl.setSpacing(8)

            chk = QCheckBox()
            chk.setChecked(name in self._selected)
            chk.setCursor(Qt.CursorShape.PointingHandCursor)
            chk.toggled.connect(lambda state, n=name: self._on_toggle(n, state))
            rl.addWidget(chk)

            is_conn = name in self._connected
            dot = QLabel("●")
            dot.setFixedWidth(12)
            dot.setStyleSheet(
                f"color: {'#22C55E' if is_conn else '#71717A'}; font-size: 9px; background: transparent;"
            )
            dot.setToolTip("Connected" if is_conn else "Disconnected (remembered)")
            rl.addWidget(dot)

            lbl = ElidedLabel(name)
            lbl.setStyleSheet(
                "font-size: 12px; font-weight: 500;"
                if is_conn
                else "font-size: 12px; color: #94A3B8;"
            )
            rl.addWidget(lbl, 1)

            if not is_conn:
                tag = QLabel("Offline")
                tag.setObjectName("Muted")
                tag.setStyleSheet(
                    "font-size: 10px; padding: 1px 6px; border-radius: 4px; background: rgba(148, 163, 184, 0.14);"
                )
                rl.addWidget(tag)

            del_btn = QPushButton("✕")
            del_btn.setObjectName("IconDangerBtn")
            del_btn.setFixedSize(24, 24)
            del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            del_btn.setToolTip(f"Remove '{name}' from remembered outputs")
            del_btn.clicked.connect(lambda _c=False, n=name: self._on_delete(n))
            rl.addWidget(del_btn)

            self._rows_lay.addWidget(row_w)


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
            text = "No audio outputs found (click Scan)"
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


def _popup_theme_tokens(dark: bool, theme_cfg: dict | None = None) -> dict:
    """Derives cohesive background, border, text, button, and accent tokens for bottom-right notifications."""
    cfg = theme_cfg or {}
    t_id = cfg.get("id", "")
    bg_cfg = cfg.get("background", {})
    cols = cfg.get("colors", {})

    accent = cols.get("accent") or ("#3B82F6" if dark else "#2563EB")
    fg = cols.get("text") or ("#F4F4F5" if dark else "#111827")
    sub_fg = cols.get("muted") or ("#9CA3AF" if dark else "#526075")
    border = cols.get("card_border") or ("#2F3344" if dark else "#CBD4E1")

    use_grad = (
        bool(t_id and t_id not in ("dark", "light", "auto"))
        or bg_cfg.get("type") == "gradient"
    )
    if use_grad and bg_cfg.get("color1"):
        qc1 = QColor(bg_cfg.get("color1", "#171921"))
        qc2 = QColor(bg_cfg.get("color2", bg_cfg.get("color1", "#171921")))
        bg_css = (
            f"qlineargradient(x1:0, y1:0, x2:1, y2:1, "
            f"stop:0 rgba({qc1.red()}, {qc1.green()}, {qc1.blue()}, 244), "
            f"stop:1 rgba({qc2.red()}, {qc2.green()}, {qc2.blue()}, 244))"
        )
        btn_bg = "rgba(255, 255, 255, 0.10)" if dark else "rgba(15, 23, 42, 0.08)"
        btn_hover = "rgba(255, 255, 255, 0.18)" if dark else "rgba(15, 23, 42, 0.14)"
    else:
        bg_css = "rgba(23, 25, 33, 244)" if dark else "rgba(255, 255, 255, 248)"
        btn_bg = "#252834" if dark else "#EDF1F7"
        btn_hover = "#2E3242" if dark else "#E2E8F0"

    return {
        "bg": bg_css,
        "border": border,
        "fg": fg,
        "sub_fg": sub_fg,
        "accent": accent,
        "btn_bg": btn_bg,
        "btn_hover": btn_hover,
        "btn_fg": fg,
        "btn_border": border,
    }


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
        self._dark = True
        self._theme_cfg = None
        self._last_dot_color = None

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

    def _apply_style(self, dot_color=None):
        tok = _popup_theme_tokens(self._dark, self._theme_cfg)
        eff_dot = tok["accent"] if (not dot_color or dot_color == "#3B82F6") else dot_color
        self.pill.setStyleSheet(
            f"QFrame#HudPill {{ background: {tok['bg']}; border: 1px solid {tok['border']}; border-radius: 16px; }}"
        )
        self.dot.setStyleSheet(f"color: {eff_dot}; font-size: 11px; background: transparent;")
        self.label.setStyleSheet(f"color: {tok['fg']}; font-size: 12px; font-weight: 600; background: transparent;")

    def set_theme(self, dark: bool, theme_cfg: dict | None = None):
        self._dark = bool(dark)
        self._theme_cfg = theme_cfg
        self._apply_style(self._last_dot_color)

    def show_message(self, text, dark=True, dot_color="#3B82F6", auto_hide_ms=0, theme_cfg=None):
        self._dark = bool(dark)
        if theme_cfg is not None:
            self._theme_cfg = theme_cfg
        self._last_dot_color = dot_color
        self._apply_style(dot_color)
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
    """Bottom-right song notification that bypasses DND; clicking opens song details and closes it."""

    clicked = pyqtSignal(dict)

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
        self._song = None
        self._open_url = ""
        self._copy_text = ""
        self._dark = True
        self._theme_cfg = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.card = QFrame()
        self.card.setObjectName("PopupCard")
        self.card.setCursor(Qt.CursorShape.PointingHandCursor)
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
        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(20, 20)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self.hide)
        head_row.addWidget(self.kicker)
        head_row.addStretch()
        head_row.addWidget(self.close_btn)

        self.title_lbl = ElidedLabel("")
        self.sub_lbl = ElidedLabel("")

        for _w in (self.cover_lbl, self.kicker, self.title_lbl, self.sub_lbl):
            _w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.open_btn = QPushButton("Open")
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

    def _apply_style(self):
        tok = _popup_theme_tokens(self._dark, self._theme_cfg)
        self.card.setStyleSheet(
            f"QFrame#PopupCard {{ background: {tok['bg']}; border: 1px solid {tok['border']}; border-radius: 14px; }}"
            f"QFrame#PopupCard:hover {{ border-color: {tok['accent']}; }}"
        )
        self.kicker.setStyleSheet(
            f"font-size: 10px; font-weight: 700; color: {tok['accent']}; background: transparent;"
        )
        self.close_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {tok['sub_fg']}; font-size: 12px; }}"
            f"QPushButton:hover {{ color: {tok['accent']}; }}"
        )
        self.title_lbl.setStyleSheet(f"color: {tok['fg']}; font-size: 14px; font-weight: 700; background: transparent;")
        self.sub_lbl.setStyleSheet(f"color: {tok['sub_fg']}; font-size: 12px; background: transparent;")
        btn_qss = (
            f"QPushButton {{ background: {tok['btn_bg']}; color: {tok['btn_fg']}; border: 1px solid {tok['btn_border']}; "
            f"border-radius: 6px; padding: 4px 10px; font-size: 11px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {tok['btn_hover']}; border-color: {tok['accent']}; }}"
        )
        self.open_btn.setStyleSheet(btn_qss)
        self.copy_btn.setStyleSheet(btn_qss)

    def set_theme(self, dark: bool, theme_cfg: dict | None = None):
        self._dark = bool(dark)
        self._theme_cfg = theme_cfg
        self._apply_style()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._song is not None:
            song = self._song
            self.hide_timer.stop()
            self.hide()
            self.clicked.emit(song)
            return
        super().mousePressEvent(event)

    def enterEvent(self, event):
        self.hide_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hide_timer.start(4000)
        super().leaveEvent(event)

    def _on_open(self):
        if self._open_url:
            open_external_url(self._open_url)
        self.hide_timer.stop()
        self.hide()

    def _on_copy(self):
        if self._copy_text:
            QApplication.clipboard().setText(self._copy_text)
            self.copy_btn.setText("Copied!")
            QTimer.singleShot(1200, lambda: self.copy_btn.setText("Copy"))

    def show_song(self, song: dict, dark=True, theme_cfg=None):
        self._song = song
        self._dark = bool(dark)
        if theme_cfg is not None:
            self._theme_cfg = theme_cfg
        self._apply_style()
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
        self.hide_timer.start(12000)


class MiniIdentifyWindow(QWidget):
    """Compact, non-resizable, always-on-top mini window with Listen, Identify Last, AI Rescue, and live waveforms."""

    listen_clicked = pyqtSignal()
    buffer_clicked = pyqtSignal()
    cancel_clicked = pyqtSignal()
    ai_toggled = pyqtSignal(bool)
    restore_requested = pyqtSignal()

    def __init__(self, app_icon):
        super().__init__(
            None,
            Qt.WindowType.Window
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowCloseButtonHint,
        )
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon)
        self.setFixedSize(356, 356)
        self.is_pinned = True
        self._dark = True
        self._accent = "#3B82F6"

        root_lay = QVBoxLayout(self)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        self.bg_widget = ThemedBackgroundWidget()
        root_lay.addWidget(self.bg_widget)

        lay = QVBoxLayout(self.bg_widget)
        lay.setContentsMargins(4, 10, 4, 12)
        lay.setSpacing(8)

        # Top utility bar: Always-on-top Pin toggle + Bring back full app button
        top_row = QHBoxLayout()
        top_row.setContentsMargins(8, 0, 8, 0)
        top_row.setSpacing(6)

        self.pin_btn = QPushButton()
        self.pin_btn.setObjectName("SidebarIconBtn")
        self.pin_btn.setFixedSize(28, 28)
        self.pin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pin_btn.clicked.connect(self._toggle_pin)

        self.restore_btn = QPushButton(" Open App")
        self.restore_btn.setObjectName("PlatformBtn")
        self.restore_btn.setIconSize(QSize(13, 13))
        self.restore_btn.setToolTip("Bring back the main SongPulse window from tray")
        self.restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.restore_btn.clicked.connect(self.restore_requested.emit)

        top_row.addWidget(self.pin_btn)
        top_row.addStretch(1)
        top_row.addWidget(self.restore_btn)
        lay.addLayout(top_row)

        # Center waveform + Listen button
        self.pulse_btn = PulseIdentifyWidget()
        self.pulse_btn.setText("Listen")
        self.pulse_btn.clicked.connect(self.listen_clicked.emit)
        lay.addWidget(self.pulse_btn, 1)

        # Bottom controls: Identify Last Xs, AI Rescue toggle, and Cancel (while scanning)
        bot_row = QHBoxLayout()
        bot_row.setContentsMargins(0, 0, 0, 0)
        bot_row.setSpacing(8)

        self.buffer_btn = QPushButton("Identify Last 12s")
        self.buffer_btn.setObjectName("SecondaryActionBtn")
        self.buffer_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.buffer_btn.clicked.connect(self.buffer_clicked.emit)

        self.ai_chk = QCheckBox("AI Rescue")
        self.ai_chk.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ai_chk.stateChanged.connect(
            lambda st: self.ai_toggled.emit(st == Qt.CheckState.Checked.value)
        )

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("DangerBtn")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self.cancel_clicked.emit)
        self.cancel_btn.hide()

        bot_row.addStretch(1)
        bot_row.addWidget(self.buffer_btn)
        bot_row.addWidget(self.ai_chk)
        bot_row.addWidget(self.cancel_btn)
        bot_row.addStretch(1)
        lay.addLayout(bot_row)

        self._update_pin_ui()

    def _toggle_pin(self):
        self.set_pinned(not self.is_pinned)

    def set_pinned(self, pinned: bool):
        self.is_pinned = bool(pinned)
        if self.isVisible():
            set_window_always_on_top(int(self.winId()), self.is_pinned)
        self._update_pin_ui()

    def _update_pin_ui(self):
        self.pin_btn.setProperty("pinned", self.is_pinned)
        self.pin_btn.style().unpolish(self.pin_btn)
        self.pin_btn.style().polish(self.pin_btn)
        self.pin_btn.setToolTip(
            "Pinned always on top (click to unpin)"
            if self.is_pinned
            else "Pin mini window always on top"
        )
        ctrl_col = "#D4D4D8" if self._dark else "#334155"
        pin_col = self._accent if self.is_pinned else ctrl_col
        self.pin_btn.setIcon(create_ui_icon("pin", pin_col, 15))
        self.restore_btn.setIcon(create_ui_icon("restore_app", ctrl_col, 13))

    def set_ai_checked_silent(self, checked: bool):
        self.ai_chk.blockSignals(True)
        self.ai_chk.setChecked(bool(checked))
        self.ai_chk.blockSignals(False)

    def apply_theme_config(self, dark: bool, cfg: dict | None, wf_kwargs: dict | None = None):
        self._dark = bool(dark)
        self._accent = (cfg or {}).get("colors", {}).get("accent", "#3B82F6")
        mini_cfg = (cfg or {}).get("miniplayer") or {}
        bg_cfg = (
            (cfg or {}).get("background", {})
            if mini_cfg.get("use_main_bg")
            else (mini_cfg or (cfg or {}).get("background", {}))
        )
        self.bg_widget.set_background_config(bg_cfg)
        self.pulse_btn.set_dark(self._dark)
        if wf_kwargs:
            kw = dict(wf_kwargs)
            kw["max_width"] = max(260, self.width() - 8)
            self.pulse_btn.configure_waveform(**kw)
        if self.isVisible():
            set_windows_titlebar_theme(int(self.winId()), self._dark)
        self._update_pin_ui()

    def showEvent(self, event):
        super().showEvent(event)
        set_windows_titlebar_theme(int(self.winId()), self._dark)
        set_window_always_on_top(int(self.winId()), self.is_pinned)

    def closeEvent(self, event):
        event.accept()
        self.hide()
