import io
import os
import re
import struct

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QPolygonF,
)

from .config import APP_DIR, ICON_COLOR_PRESETS, ICONS_DIR, resolve_cover_path


def resolve_icon_colors(icon_color_key: str = "theme", theme_cfg: dict | None = None) -> tuple[str, str]:
    """Resolves (color1, color2) for the SongPulse app icon based on user preference or active theme."""
    key = (icon_color_key or "theme").strip().lower()
    if key != "theme":
        for pid, _lbl, c1, c2 in ICON_COLOR_PRESETS:
            if pid == key and c1 and c2:
                return c1, c2
    if theme_cfg:
        t_id = (theme_cfg.get("id") or "").lower()
        cols = theme_cfg.get("colors", {})
        if t_id not in ("dark", "light", "auto") and cols.get("accent"):
            return (
                cols.get("accent", "#3B82F6"),
                cols.get("accent_secondary", cols.get("accent", "#8B5CF6")),
            )
    return "#3B82F6", "#8B5CF6"


def create_app_pixmap(size=64, color1="#3B82F6", color2="#8B5CF6"):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    pad = max(1, size // 28)
    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0.0, QColor(color1 or "#3B82F6"))
    grad.setColorAt(1.0, QColor(color2 or "#8B5CF6"))
    painter.setBrush(QBrush(grad))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(pad, pad, size - pad * 2, size - pad * 2)

    pen_w = max(2, int(size * 0.085))
    painter.setPen(
        QPen(
            QColor("#FFFFFF"),
            pen_w,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
        )
    )
    cx = size / 2.0
    cy = size / 2.0
    bars = [(-0.27, 0.16), (-0.135, 0.30), (0.0, 0.42), (0.135, 0.26), (0.27, 0.16)]
    for dx, h in bars:
        x = int(round(cx + dx * size))
        half_h = int(round((h * size) / 2.0))
        painter.drawLine(x, int(round(cy - half_h)), x, int(round(cy + half_h)))

    painter.end()
    return pixmap


def create_app_icon(color1="#3B82F6", color2="#8B5CF6"):
    icon = QIcon()
    for sz in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(create_app_pixmap(sz, color1, color2))
    return icon


def export_multi_res_ico(out_path, color1="#3B82F6", color2="#8B5CF6"):
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = []
    for sz in sizes:
        pix = create_app_pixmap(sz, color1, color2)
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        pix.save(buf, "PNG")
        frames.append((sz, bytes(ba)))

    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    entries = []
    blobs = []
    for sz, data in frames:
        dim = 0 if sz == 256 else sz
        entries.append(
            struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        )
        blobs.append(data)
        offset += len(data)

    with open(out_path, "wb") as f:
        f.write(header)
        for e in entries:
            f.write(e)
        for b in blobs:
            f.write(b)


def ensure_chevron_icons():
    specs = [
        ("down_dark.png", "down", "#A1A1AA"),
        ("up_dark.png", "up", "#A1A1AA"),
        ("down_light.png", "down", "#475569"),
        ("up_light.png", "up", "#475569"),
    ]
    paths = {}
    for fname, direction, color in specs:
        fpath = os.path.join(ICONS_DIR, fname)
        pix = QPixmap(12, 12)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(
            QColor(color),
            1.6,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
        p.setPen(pen)
        if direction == "down":
            p.drawPolyline(
                QPolygonF([QPointF(3.0, 4.5), QPointF(6.0, 7.5), QPointF(9.0, 4.5)])
            )
        else:
            p.drawPolyline(
                QPolygonF([QPointF(3.0, 7.5), QPointF(6.0, 4.5), QPointF(9.0, 7.5)])
            )
        p.end()
        pix.save(fpath, "PNG")
        paths[fname] = fpath.replace("\\", "/")
    return paths


def create_ui_icon(kind: str, color_hex="#E4E4E7", size=16):
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(
        QColor(color_hex),
        1.6,
        Qt.PenStyle.SolidLine,
        Qt.PenCapStyle.RoundCap,
        Qt.PenJoinStyle.RoundJoin,
    )
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    s = float(size)
    if kind == "identify":
        for x, h in [
            (0.16, 0.28),
            (0.33, 0.56),
            (0.50, 0.78),
            (0.67, 0.50),
            (0.84, 0.28),
        ]:
            p.drawLine(
                QPointF(s * x, s * (0.5 - h / 2)), QPointF(s * x, s * (0.5 + h / 2))
            )
    elif kind == "library":
        p.drawLine(QPointF(s * 0.20, s * 0.25), QPointF(s * 0.55, s * 0.25))
        p.drawLine(QPointF(s * 0.20, s * 0.50), QPointF(s * 0.48, s * 0.50))
        p.drawLine(QPointF(s * 0.20, s * 0.75), QPointF(s * 0.45, s * 0.75))
        p.drawLine(QPointF(s * 0.72, s * 0.72), QPointF(s * 0.72, s * 0.24))
        p.drawLine(QPointF(s * 0.72, s * 0.24), QPointF(s * 0.88, s * 0.30))
        p.setBrush(QBrush(QColor(color_hex)))
        p.drawEllipse(QPointF(s * 0.64, s * 0.74), s * 0.11, s * 0.09)
    elif kind == "settings":
        p.drawLine(QPointF(s * 0.16, s * 0.28), QPointF(s * 0.84, s * 0.28))
        p.drawLine(QPointF(s * 0.16, s * 0.52), QPointF(s * 0.84, s * 0.52))
        p.drawLine(QPointF(s * 0.16, s * 0.76), QPointF(s * 0.84, s * 0.76))
        p.setBrush(QBrush(QColor(color_hex)))
        p.drawEllipse(QPointF(s * 0.35, s * 0.28), s * 0.10, s * 0.10)
        p.drawEllipse(QPointF(s * 0.68, s * 0.52), s * 0.10, s * 0.10)
        p.drawEllipse(QPointF(s * 0.42, s * 0.76), s * 0.10, s * 0.10)
    elif kind == "sidebar":
        p.drawRoundedRect(
            QRectF(s * 0.12, s * 0.16, s * 0.76, s * 0.68), s * 0.16, s * 0.16
        )
        p.drawLine(QPointF(s * 0.38, s * 0.18), QPointF(s * 0.38, s * 0.82))
    elif kind == "pin":
        p.drawLine(QPointF(s * 0.32, s * 0.20), QPointF(s * 0.68, s * 0.20))
        p.drawLine(QPointF(s * 0.38, s * 0.20), QPointF(s * 0.34, s * 0.52))
        p.drawLine(QPointF(s * 0.62, s * 0.20), QPointF(s * 0.66, s * 0.52))
        p.drawLine(QPointF(s * 0.24, s * 0.54), QPointF(s * 0.76, s * 0.54))
        p.drawLine(QPointF(s * 0.50, s * 0.54), QPointF(s * 0.50, s * 0.88))
    elif kind == "details":
        p.drawEllipse(QRectF(s * 0.14, s * 0.14, s * 0.72, s * 0.72))
        p.drawLine(QPointF(s * 0.50, s * 0.46), QPointF(s * 0.50, s * 0.68))
        p.setBrush(QBrush(QColor(color_hex)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(s * 0.50, s * 0.32), s * 0.06, s * 0.06)
    elif kind == "external":
        # Clean minimal top-right arrow
        pen.setWidthF(1.5)
        p.setPen(pen)
        p.drawLine(QPointF(s * 0.28, s * 0.72), QPointF(s * 0.74, s * 0.26))
        p.drawPolyline(
            QPolygonF(
                [
                    QPointF(s * 0.40, s * 0.26),
                    QPointF(s * 0.74, s * 0.26),
                    QPointF(s * 0.74, s * 0.60),
                ]
            )
        )
    elif kind == "mini_mode":
        p.drawRoundedRect(
            QRectF(s * 0.12, s * 0.16, s * 0.76, s * 0.68), s * 0.12, s * 0.12
        )
        p.setBrush(QBrush(QColor(color_hex)))
        p.drawRoundedRect(
            QRectF(s * 0.46, s * 0.46, s * 0.34, s * 0.28), s * 0.06, s * 0.06
        )
    elif kind == "restore_app":
        p.drawRoundedRect(
            QRectF(s * 0.14, s * 0.18, s * 0.72, s * 0.64), s * 0.12, s * 0.12
        )
        p.drawLine(QPointF(s * 0.34, s * 0.66), QPointF(s * 0.66, s * 0.34))
        p.drawPolyline(
            QPolygonF(
                [
                    QPointF(s * 0.44, s * 0.34),
                    QPointF(s * 0.66, s * 0.34),
                    QPointF(s * 0.66, s * 0.56),
                ]
            )
        )
    elif kind in ("heart", "heart_filled"):
        path = QPainterPath()
        path.moveTo(s * 0.50, s * 0.82)
        path.cubicTo(s * 0.14, s * 0.58, s * 0.12, s * 0.24, s * 0.32, s * 0.22)
        path.cubicTo(s * 0.43, s * 0.21, s * 0.50, s * 0.31, s * 0.50, s * 0.35)
        path.cubicTo(s * 0.50, s * 0.31, s * 0.57, s * 0.21, s * 0.68, s * 0.22)
        path.cubicTo(s * 0.88, s * 0.24, s * 0.86, s * 0.58, s * 0.50, s * 0.82)
        if kind == "heart_filled":
            p.setBrush(QBrush(QColor(color_hex)))
            p.setPen(Qt.PenStyle.NoPen)
        p.drawPath(path)
    elif kind == "download":
        p.drawLine(QPointF(s * 0.50, s * 0.18), QPointF(s * 0.50, s * 0.66))
        p.drawPolyline(
            QPolygonF(
                [
                    QPointF(s * 0.30, s * 0.48),
                    QPointF(s * 0.50, s * 0.68),
                    QPointF(s * 0.70, s * 0.48),
                ]
            )
        )
        p.drawLine(QPointF(s * 0.20, s * 0.84), QPointF(s * 0.80, s * 0.84))
    elif kind == "playlist_add":
        p.drawLine(QPointF(s * 0.16, s * 0.28), QPointF(s * 0.68, s * 0.28))
        p.drawLine(QPointF(s * 0.16, s * 0.52), QPointF(s * 0.56, s * 0.52))
        p.drawLine(QPointF(s * 0.16, s * 0.76), QPointF(s * 0.46, s * 0.76))
        p.drawLine(QPointF(s * 0.74, s * 0.56), QPointF(s * 0.74, s * 0.86))
        p.drawLine(QPointF(s * 0.59, s * 0.71), QPointF(s * 0.89, s * 0.71))

    p.end()
    return QIcon(pix)


def create_ytdlp_icon(size: int = 16) -> QIcon:
    """Loads the bundled official yt-dlp .ico asset for download buttons."""
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "yt-dlp.ico"),
        os.path.join(APP_DIR, "_internal", "songpulse", "assets", "yt-dlp.ico"),
        os.path.join(APP_DIR, "songpulse", "assets", "yt-dlp.ico"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            icon = QIcon(path)
            if not icon.isNull():
                return icon
    return create_ui_icon("download", "#E4E4E7", size)


def create_platform_icon(kind: str, size=14):
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    colors = {
        "shazam": "#0088FF",
        "youtube": "#FF0000",
        "yt_music": "#FF0033",
        "spotify": "#1DB954",
        "apple": "#FA243C",
        "soundcloud": "#FF5500",
        "deezer": "#A238FF",
        "amazon": "#00A8E1",
        "tidal": "#18181B",
        "web": "#3B82F6",
        "copy": "#64748B",
    }
    bg = QColor(colors.get(kind, "#3B82F6"))
    p.setBrush(QBrush(bg))
    p.setPen(Qt.PenStyle.NoPen)

    if kind == "youtube":
        p.drawRoundedRect(QRectF(0, 2, size, size - 4), 3, 3)
        p.setBrush(QBrush(QColor("#FFFFFF")))
        tri = QPolygonF(
            [
                QPointF(size * 0.40, size * 0.30),
                QPointF(size * 0.72, size * 0.50),
                QPointF(size * 0.40, size * 0.70),
            ]
        )
        p.drawPolygon(tri)
    elif kind == "yt_music":
        p.drawEllipse(QRectF(0, 0, size, size))
        p.setPen(QPen(QColor("#FFFFFF"), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(2, 2, size - 4, size - 4))
        p.setBrush(QBrush(QColor("#FFFFFF")))
        p.setPen(Qt.PenStyle.NoPen)
        tri = QPolygonF(
            [
                QPointF(size * 0.42, size * 0.32),
                QPointF(size * 0.70, size * 0.50),
                QPointF(size * 0.42, size * 0.68),
            ]
        )
        p.drawPolygon(tri)
    elif kind == "spotify":
        p.drawEllipse(QRectF(0, 0, size, size))
        p.setPen(
            QPen(QColor("#FFFFFF"), 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        )
        p.drawArc(3, 4, size - 6, 4, 30 * 16, 120 * 16)
        p.drawArc(4, 7, size - 8, 4, 30 * 16, 120 * 16)
        p.drawArc(5, 10, size - 10, 3, 30 * 16, 120 * 16)
    elif kind == "shazam":
        p.drawEllipse(QRectF(0, 0, size, size))
        p.setPen(
            QPen(QColor("#FFFFFF"), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        )
        p.drawArc(3, 3, 6, 6, 40 * 16, 190 * 16)
        p.drawArc(size - 9, size - 9, 6, 6, 220 * 16, 190 * 16)
    elif kind == "apple":
        p.drawRoundedRect(QRectF(0, 0, size, size), 3, 3)
        p.setPen(
            QPen(QColor("#FFFFFF"), 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        )
        p.drawLine(QPointF(5, 9), QPointF(5, 4))
        p.drawLine(QPointF(10, 8), QPointF(10, 3))
        p.drawLine(QPointF(5, 4), QPointF(10, 3))
        p.setBrush(QBrush(QColor("#FFFFFF")))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(3, 8, 3, 3))
        p.drawEllipse(QRectF(8, 7, 3, 3))
    elif kind == "soundcloud":
        p.drawRoundedRect(QRectF(0, 1, size, size - 2), 4, 4)
        p.setPen(
            QPen(QColor("#FFFFFF"), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        )
        for x, y1, y2 in [(3.5, 9, 6), (6.0, 9, 4.5), (8.5, 9, 5), (11.0, 9, 6.5)]:
            p.drawLine(QPointF(x, y1), QPointF(x, y2))
    elif kind == "deezer":
        p.drawRoundedRect(QRectF(0, 0, size, size), 3, 3)
        p.setPen(
            QPen(QColor("#FFFFFF"), 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        )
        for x, y1, y2 in [(3.5, 10, 7.5), (6.5, 10, 4.5), (9.5, 10, 6), (11.5, 10, 8)]:
            p.drawLine(QPointF(x, y1), QPointF(x, y2))
    elif kind == "amazon":
        p.drawRoundedRect(QRectF(0, 0, size, size), 3, 3)
        p.setPen(
            QPen(QColor("#FFFFFF"), 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        )
        p.drawArc(3, 6, size - 6, 5, 200 * 16, 140 * 16)
        p.drawLine(QPointF(5, 4.5), QPointF(9, 4.5))
    elif kind == "tidal":
        p.setPen(QPen(QColor("#52525B"), 1.0))
        p.drawRoundedRect(QRectF(0.5, 0.5, size - 1, size - 1), 3, 3)
        p.setBrush(QBrush(QColor("#FFFFFF")))
        p.setPen(Qt.PenStyle.NoPen)
        for cx, cy in [(4.2, 5.5), (7.0, 5.5), (9.8, 5.5), (7.0, 8.5)]:
            d = 1.4
            diamond = QPolygonF(
                [
                    QPointF(cx, cy - d),
                    QPointF(cx + d, cy),
                    QPointF(cx, cy + d),
                    QPointF(cx - d, cy),
                ]
            )
            p.drawPolygon(diamond)
    elif kind == "copy":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor("#94A3B8"), 1.4))
        p.drawRoundedRect(QRectF(4, 4, 8, 8), 1.5, 1.5)
        p.drawRoundedRect(QRectF(2, 2, 7, 7), 1.5, 1.5)
    else:
        p.drawEllipse(QRectF(0, 0, size, size))
        p.setPen(QPen(QColor("#FFFFFF"), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(3.5, 2, size - 7, size - 4))
        p.drawLine(QPointF(2, size / 2), QPointF(size - 2, size / 2))

    p.end()
    return QIcon(pix)


_BLURRED_SRC_CACHE: dict[tuple[str, int, int, int], QPixmap] = {}


def _pil_rgba_to_pixmap(im) -> QPixmap:
    raw = im.tobytes("raw", "RGBA")
    qimg = QImage(raw, im.width, im.height, im.width * 4, QImage.Format.Format_RGBA8888).copy()
    return QPixmap.fromImage(qimg)


def get_cached_blurred_source(image_path: str, blur_radius: int = 0, max_dim: int = 1280) -> QPixmap:
    """Loads, optionally downscales, and blurs a theme image once per (path, mtime, blur_radius)."""
    if not image_path or not os.path.isfile(image_path):
        return QPixmap()
    norm = os.path.normcase(os.path.abspath(image_path))
    try:
        mtime_ns = os.stat(image_path).st_mtime_ns
    except OSError:
        mtime_ns = 0
    blur_i = max(0, int(blur_radius or 0))
    key = (norm, mtime_ns, blur_i, int(max_dim))
    cached = _BLURRED_SRC_CACHE.get(key)
    if cached is not None:
        return cached

    try:
        from PIL import Image, ImageFilter

        with Image.open(image_path) as im:
            im = im.convert("RGBA")
            iw, ih = im.size
            if max(iw, ih) > max_dim:
                scale = max_dim / float(max(iw, ih))
                nw = max(1, int(round(iw * scale)))
                nh = max(1, int(round(ih * scale)))
                im = im.resize((nw, nh), Image.Resampling.LANCZOS)
            if blur_i > 0:
                im = im.filter(ImageFilter.GaussianBlur(radius=float(blur_i)))
            pix = _pil_rgba_to_pixmap(im)
    except Exception:
        pix = QPixmap(image_path)

    if len(_BLURRED_SRC_CACHE) >= 12:
        _BLURRED_SRC_CACHE.pop(next(iter(_BLURRED_SRC_CACHE)))
    _BLURRED_SRC_CACHE[key] = pix
    return pix


def render_blurred_pixmap(
    image_path: str, width: int, height: int, blur_radius: int = 8, fit_mode: str = "cover"
) -> QPixmap:
    """Renders an image for sidebar/background themes. Supports cover-crop or transparent cutout fitting."""
    if not image_path or not os.path.isfile(image_path) or width <= 2 or height <= 2:
        return QPixmap()
    try:
        from PIL import Image, ImageFilter

        with Image.open(image_path) as im:
            im = im.convert("RGBA")
            iw, ih = im.size
            if fit_mode == "cutout":
                # Frame character cutout gracefully in the lower/middle area of the sidebar
                canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                scale = max((width * 1.18) / max(1, iw), (height * 0.68) / max(1, ih))
                nw = max(1, int(round(iw * scale)))
                nh = max(1, int(round(ih * scale)))
                resized = im.resize((nw, nh), Image.Resampling.LANCZOS)
                paste_x = (width - nw) // 2
                paste_y = max(int(height * 0.24), height - nh + int(nh * 0.08))
                canvas.alpha_composite(resized, (paste_x, paste_y))
                im = canvas
            else:
                scale = max(width / max(1, iw), height / max(1, ih))
                nw = max(1, int(round(iw * scale)))
                nh = max(1, int(round(ih * scale)))
                im = im.resize((nw, nh), Image.Resampling.LANCZOS)
                left = (nw - width) // 2
                top = (nh - height) // 2
                im = im.crop((left, top, left + width, top + height))
            if blur_radius and blur_radius > 0:
                im = im.filter(ImageFilter.GaussianBlur(radius=float(blur_radius)))
            return _pil_rgba_to_pixmap(im)
    except Exception:
        pix = QPixmap(image_path)
        if pix.isNull():
            return QPixmap()
        scaled = pix.scaled(
            width,
            height,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        return scaled.copy(
            (scaled.width() - width) // 2,
            (scaled.height() - height) // 2,
            width,
            height,
        )


def rounded_pixmap(path, size, radius=12):
    resolved = resolve_cover_path(path)
    pix = QPixmap(resolved) if resolved else QPixmap()
    if pix.isNull():
        pix = QPixmap(size, size)
        pix.fill(QColor("#27272A"))
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QColor("#71717A"))
        font = p.font()
        font.setPointSize(max(12, size // 4))
        p.setFont(font)
        p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "♪")
        p.end()

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
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    painter.setClipPath(clip)
    painter.drawPixmap(0, 0, cropped)
    painter.end()
    return out


def _blend_rgb(rgb, base_rgb, weight_base: float):
    return tuple(
        int(round(rgb[i] * (1.0 - weight_base) + base_rgb[i] * weight_base))
        for i in range(3)
    )


def _perceived_luminance(rgb) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def extract_cover_gradient_colors(
    cover_path: str, dark: bool, theme_cfg: dict | None = None
) -> dict:
    """Extracts 2 dominant cover colors + accent, blended with the active theme base colors."""
    resolved = resolve_cover_path(cover_path)
    default_c1 = (37, 99, 235)
    default_c2 = (124, 58, 237)
    c1, c2, accent = default_c1, default_c2, (59, 130, 246)

    if resolved and os.path.exists(resolved):
        pix = QPixmap(resolved)
        if not pix.isNull():
            img = pix.scaled(
                24,
                24,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ).toImage()

            top_samples = []
            bot_samples = []
            best_sat_score = -1.0

            for y in range(img.height()):
                for x in range(img.width()):
                    col = img.pixelColor(x, y)
                    r, g, b = col.red(), col.green(), col.blue()
                    sat = col.saturation()
                    val = col.value()
                    if y < 12:
                        top_samples.append((r, g, b))
                    else:
                        bot_samples.append((r, g, b))

                    if 45 <= val <= 235 and sat > 40:
                        score = sat * 1.4 - abs(val - 155) * 0.4
                        if score > best_sat_score:
                            best_sat_score = score
                            accent = (r, g, b)

            def _avg_filtered(samples, fallback):
                mid = [rgb for rgb in samples if 28 < _perceived_luminance(rgb) < 230]
                use = mid if len(mid) >= 12 else samples
                if not use:
                    return fallback
                n = len(use)
                return (
                    sum(p[0] for p in use) // n,
                    sum(p[1] for p in use) // n,
                    sum(p[2] for p in use) // n,
                )

            c1 = _avg_filtered(top_samples, default_c1)
            c2 = _avg_filtered(bot_samples, default_c2)

    if dark:
        base = (17, 18, 22)
        g1 = _blend_rgb(c1, base, 0.56)
        g2 = _blend_rgb(c2, base, 0.68)
        if _perceived_luminance(g1) > 88:
            g1 = _blend_rgb(g1, base, 0.45)
        if _perceived_luminance(g2) > 74:
            g2 = _blend_rgb(g2, base, 0.45)
        if _perceived_luminance(accent) < 85:
            accent = _blend_rgb(accent, (96, 165, 250), 0.50)
    else:
        base = (238, 242, 248)
        g1 = _blend_rgb(c1, base, 0.66)
        g2 = _blend_rgb(c2, base, 0.76)
        if _perceived_luminance(g1) < 195:
            g1 = _blend_rgb(g1, (255, 255, 255), 0.42)
        if _perceived_luminance(g2) < 205:
            g2 = _blend_rgb(g2, (255, 255, 255), 0.42)
        if _perceived_luminance(accent) > 175:
            accent = _blend_rgb(accent, (37, 99, 235), 0.50)

    accent_text = "#111827" if _perceived_luminance(accent) > 160 else "#FFFFFF"

    return {
        "g1": QColor(*g1),
        "g2": QColor(*g2),
        "base": QColor(*base),
        "accent": f"rgb({accent[0]}, {accent[1]}, {accent[2]})",
        "accent_text": accent_text,
    }


def build_palette(dark: bool, theme_cfg: dict | None = None) -> QPalette:
    """Sets an explicit QPalette so Windows Dark Mode never leaves white labels on Light Mode."""
    pal = QPalette()
    tc = (theme_cfg or {}).get("colors", {})
    accent = tc.get("accent", "#2563EB")
    if dark:
        pal.setColor(QPalette.ColorRole.Window, QColor("#111216"))
        pal.setColor(QPalette.ColorRole.WindowText, QColor(tc.get("text", "#F4F4F5")))
        pal.setColor(QPalette.ColorRole.Base, QColor("#1E2028"))
        pal.setColor(QPalette.ColorRole.AlternateBase, QColor("#1A1B21"))
        pal.setColor(QPalette.ColorRole.Text, QColor(tc.get("text", "#F4F4F5")))
        pal.setColor(QPalette.ColorRole.Button, QColor("#24262F"))
        pal.setColor(QPalette.ColorRole.ButtonText, QColor(tc.get("text", "#F4F4F5")))
        pal.setColor(QPalette.ColorRole.Highlight, QColor(accent))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    else:
        pal.setColor(QPalette.ColorRole.Window, QColor("#E9EDF3"))
        pal.setColor(QPalette.ColorRole.WindowText, QColor(tc.get("text", "#111827")))
        pal.setColor(QPalette.ColorRole.Base, QColor("#F5F7FA"))
        pal.setColor(QPalette.ColorRole.AlternateBase, QColor("#ECEFF4"))
        pal.setColor(QPalette.ColorRole.Text, QColor(tc.get("text", "#111827")))
        pal.setColor(QPalette.ColorRole.Button, QColor("#EDF0F5"))
        pal.setColor(QPalette.ColorRole.ButtonText, QColor(tc.get("text", "#111827")))
        pal.setColor(QPalette.ColorRole.Highlight, QColor(accent))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    return pal


_FONT_SIZE_RE = re.compile(r"font-size\s*:\s*(\d+(?:\.\d+)?)px")


def scale_qss_fonts(qss: str, scale: float) -> str:
    """Scales all `font-size: Npx` declarations in a QSS string by `scale`."""
    if not qss or abs(scale - 1.0) < 0.01:
        return qss

    def _repl(match):
        val = float(match.group(1))
        scaled = max(9, int(round(val * scale)))
        return f"font-size: {scaled}px"

    return _FONT_SIZE_RE.sub(_repl, qss)


def apply_font_scale_to_widget_tree(root, scale: float):
    """Scales inline widget stylesheets containing `font-size` without compounding."""
    if root is None:
        return
    from PyQt6.QtWidgets import QWidget

    widgets = [root] + root.findChildren(QWidget)
    for w in widgets:
        if w.property("_dynamic_qss"):
            continue
        base_qss = w.property("_base_qss")
        cur_qss = w.styleSheet() or ""
        if base_qss is None:
            if "font-size" not in cur_qss:
                continue
            base_qss = cur_qss
            w.setProperty("_base_qss", base_qss)
        target = scale_qss_fonts(base_qss, scale)
        if cur_qss != target:
            w.setStyleSheet(target)


def build_stylesheet(dark: bool, chevrons: dict, theme_cfg: dict | None = None, font_scale: float = 1.0):
    dn = chevrons["down_dark.png"] if dark else chevrons["down_light.png"]
    up = chevrons["up_dark.png"] if dark else chevrons["up_light.png"]

    tc = (theme_cfg or {}).get("colors", {})
    accent = tc.get("accent", "#2563EB")
    accent2 = tc.get("accent_secondary", "#7C3AED")

    if dark:
        c = {
            "bg": "#111216",
            "fg": tc.get("text", "#F4F4F5"),
            "sidebar_b": tc.get("card_border", "#24262E"),
            "nav_fg": tc.get("muted", "#A1A1AA"),
            "nav_hover": "rgba(255, 255, 255, 0.09)",
            "card": tc.get("card_bg", "#1A1B21"),
            "card_b": tc.get("card_border", "#272932"),
            "muted": tc.get("muted", "#9CA3AF"),
            "chip_bg": "rgba(255, 255, 255, 0.08)",
            "chip_fg": tc.get("text", "#D4D4D8"),
            "inp_bg": "rgba(20, 24, 34, 0.78)",
            "inp_b": tc.get("card_border", "#2E303C"),
            "btn_bg": "rgba(255, 255, 255, 0.08)",
            "btn_fg": tc.get("text", "#E4E4E7"),
            "btn_b": "rgba(255, 255, 255, 0.14)",
            "btn_hover": "rgba(255, 255, 255, 0.16)",
            "sec_bg": "rgba(255, 255, 255, 0.08)",
            "sec_b": "rgba(255, 255, 255, 0.16)",
            "sec_hover": "rgba(255, 255, 255, 0.15)",
            "id_b": "rgba(255, 255, 255, 0.22)",
            "id_dis_bg": "rgba(39, 41, 50, 0.85)",
            "id_dis_fg": "#71717A",
            "id_dis_b": "rgba(255, 255, 255, 0.08)",
            "danger_fg": "#F87171",
            "danger_b": "rgba(248, 113, 113, 0.35)",
            "danger_hover": "rgba(239, 68, 68, 0.18)",
            "warn_bg": "rgba(245, 158, 11, 0.14)",
            "warn_b": "rgba(245, 158, 11, 0.38)",
            "warn_fg": "#FBBF24",
            "err_bg": "rgba(239, 68, 68, 0.14)",
            "err_b": "rgba(239, 68, 68, 0.40)",
            "err_fg": "#F87171",
            "help_bg": "rgba(255, 255, 255, 0.09)",
            "help_fg": tc.get("muted", "#A1A1AA"),
            "help_b": "rgba(255, 255, 255, 0.16)",
            "menu_bg": "#181C26",
            "scroll_h": "rgba(210, 214, 220, 0.34)",
            "scroll_h_hover": "rgba(225, 229, 235, 0.56)",
            "song_hover_bg": "rgba(255, 255, 255, 0.08)",
            "song_hover_b": accent,
        }
    else:
        c = {
            "bg": "#E9EDF3",
            "fg": tc.get("text", "#111827"),
            "sidebar_b": tc.get("card_border", "#D6DCE6"),
            "nav_fg": tc.get("muted", "#475569"),
            "nav_hover": "rgba(15, 23, 42, 0.07)",
            "card": tc.get("card_bg", "#FFFFFF"),
            "card_b": tc.get("card_border", "#D4DBE6"),
            "muted": tc.get("muted", "#526075"),
            "chip_bg": "rgba(15, 23, 42, 0.06)",
            "chip_fg": "#334155",
            "inp_bg": "rgba(255, 255, 255, 0.90)",
            "inp_b": "#C8D1DF",
            "btn_bg": "rgba(15, 23, 42, 0.05)",
            "btn_fg": "#1E293B",
            "btn_b": "rgba(15, 23, 42, 0.14)",
            "btn_hover": "rgba(15, 23, 42, 0.11)",
            "sec_bg": "rgba(255, 255, 255, 0.85)",
            "sec_b": "#C8D1DF",
            "sec_hover": "#EDF1F7",
            "id_b": "#BFDBFE",
            "id_dis_bg": "#DCE2EC",
            "id_dis_fg": "#64748B",
            "id_dis_b": "#E9EDF3",
            "danger_fg": "#DC2626",
            "danger_b": "#F5B5B5",
            "danger_hover": "#FEE2E2",
            "warn_bg": "#FEF3C7",
            "warn_b": "#F59E0B",
            "warn_fg": "#92400E",
            "err_bg": "#FEE2E2",
            "err_b": "#EF4444",
            "err_fg": "#B91C1C",
            "help_bg": "#DDE3ED",
            "help_fg": "#334155",
            "help_b": "#C2CCDC",
            "menu_bg": "#FFFFFF",
            "scroll_h": "rgba(128, 134, 145, 0.38)",
            "scroll_h_hover": "rgba(95, 99, 104, 0.62)",
            "song_hover_bg": "rgba(37, 99, 235, 0.06)",
            "song_hover_b": accent,
        }

    ac_col = QColor(accent)
    ac_text = "#0B1220" if _perceived_luminance((ac_col.red(), ac_col.green(), ac_col.blue())) > 175 else "#FFFFFF"

    qss = f"""
QMainWindow {{
    background-color: {c['bg']};
    color: {c['fg']};
    font-family: "Segoe UI", sans-serif;
}}
QWidget#Root {{
    background-color: transparent;
    color: {c['fg']};
}}
QWidget {{
    color: {c['fg']};
}}
QLabel {{
    color: {c['fg']};
    background-color: transparent;
}}
QStackedWidget, QWidget#Page, QWidget#ScrollContent, QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget {{
    background-color: transparent;
    border: none;
}}
QFrame#Sidebar {{
    background-color: transparent;
    border-right: 1px solid {c['sidebar_b']};
}}
QPushButton#NavBtn {{
    text-align: left;
    padding: 9px 12px;
    border-radius: 10px;
    border: none;
    color: {c['nav_fg']};
    font-size: 13px;
    font-weight: 500;
    background: transparent;
}}
QPushButton#NavBtn:hover {{
    background-color: {c['nav_hover']};
    color: {c['fg']};
}}
QPushButton#NavBtn[active="true"] {{
    background-color: {accent};
    color: {ac_text};
    font-weight: 600;
}}
QPushButton#NavBtn[collapsed="true"] {{
    text-align: center;
    padding: 9px 0px;
}}
QPushButton#PlaylistTab {{
    background-color: {c['btn_bg']};
    color: {c['btn_fg']};
    border: 1px solid {c['btn_b']};
    border-radius: 14px;
    padding: 5px 12px;
    font-size: 12px;
    font-weight: 600;
}}
QPushButton#PlaylistTab:hover {{
    background-color: {c['btn_hover']};
    border-color: {accent};
}}
QPushButton#PlaylistTab[active="true"] {{
    background-color: {accent};
    color: {ac_text};
    border-color: {accent};
}}
QPushButton#CollapseBtn {{
    text-align: left;
    background: transparent;
    border: none;
    color: {c['fg']};
    font-size: 14px;
    font-weight: 600;
    padding: 2px 0px;
}}
QPushButton#CollapseBtn:hover {{
    color: {accent};
}}
QPushButton#SidebarIconBtn {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 0px;
}}
QPushButton#SidebarIconBtn:hover {{
    background-color: {c['nav_hover']};
}}
QPushButton#SidebarIconBtn[pinned="true"] {{
    background-color: rgba(59, 130, 246, 0.18);
    border: 1px solid {accent};
}}
QFrame#Card {{
    background-color: {c['card']};
    border: 1px solid {c['card_b']};
    border-radius: 14px;
}}
QFrame#SongCard {{
    background-color: {c['card']};
    border: 1px solid {c['card_b']};
    border-radius: 14px;
}}
QFrame#SongCard:hover {{
    background-color: {c['song_hover_bg']};
    border: 1px solid {c['song_hover_b']};
}}
QFrame#WarnBanner {{
    background-color: {c['warn_bg']};
    border: 1px solid {c['warn_b']};
    border-radius: 9px;
}}
QLabel#WarnText {{
    color: {c['warn_fg']};
    font-size: 11px;
    font-weight: 500;
}}
QFrame#ErrorBanner {{
    background-color: {c['err_bg']};
    border: 1px solid {c['err_b']};
    border-radius: 9px;
}}
QLabel#ErrorText {{
    color: {c['err_fg']};
    font-size: 11px;
    font-weight: 600;
}}
QLabel#HelpBadge {{
    background-color: {c['help_bg']};
    color: {c['help_fg']};
    border: 1px solid {c['help_b']};
    border-radius: 8px;
    font-size: 10px;
    font-weight: 700;
}}
QLabel#HelpBadge:hover {{
    background-color: {accent};
    color: {ac_text};
    border-color: {accent};
}}
QPushButton#IdentifyBtn {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {accent}, stop:1 {accent2});
    color: {ac_text};
    border: none;
    border-radius: 66px;
    font-size: 15px;
    font-weight: 700;
}}
QPushButton#IdentifyBtn:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {accent2}, stop:1 {accent});
    border: none;
}}
QPushButton#IdentifyBtn:disabled {{
    background: {c['id_dis_bg']};
    color: {c['id_dis_fg']};
    border: none;
}}
QPushButton#SecondaryActionBtn {{
    background-color: {c['sec_bg']};
    color: {c['fg']};
    border: 1px solid {c['sec_b']};
    border-radius: 16px;
    padding: 7px 16px;
    font-size: 12px;
    font-weight: 600;
}}
QPushButton#SecondaryActionBtn:hover {{
    background-color: {c['sec_hover']};
    border-color: {accent};
}}
QPushButton#SecondaryActionBtn:disabled {{
    color: {c['muted']};
}}
QPushButton#PrimaryBtn {{
    background-color: {accent};
    color: {ac_text};
    border: none;
    border-radius: 8px;
    padding: 8px 16px;
    font-size: 12px;
    font-weight: 600;
}}
QPushButton#PrimaryBtn:hover {{
    border: 1px solid {c['fg']};
}}
QPushButton#PlatformBtn {{
    background-color: {c['btn_bg']};
    color: {c['btn_fg']};
    border: 1px solid {c['btn_b']};
    border-radius: 7px;
    padding: 4px 9px;
    font-size: 11px;
    font-weight: 600;
}}
QPushButton#PlatformBtn:hover {{
    background-color: {c['btn_hover']};
    border-color: {accent};
}}
QPushButton#PlatformBtn:checked {{
    background-color: {accent};
    color: {ac_text};
    border-color: {accent};
}}
QPushButton#PlatformBtn::menu-indicator {{
    image: none;
    width: 0px;
}}
QPushButton#StickyActionBtn {{
    background-color: {'rgba(255, 255, 255, 0.10)' if dark else 'rgba(15, 23, 42, 0.06)'};
    color: {c['btn_fg']};
    border: 1px solid {'rgba(255, 255, 255, 0.22)' if dark else 'rgba(15, 23, 42, 0.20)'};
    border-radius: 8px;
    padding: 4px 11px;
    font-size: 11px;
    font-weight: 600;
}}
QPushButton#StickyActionBtn:hover {{
    background-color: {'rgba(255, 255, 255, 0.18)' if dark else 'rgba(15, 23, 42, 0.12)'};
    border-color: {accent};
}}
QPushButton#StickyActionBtn::menu-indicator {{
    image: none;
    width: 0px;
}}
QPushButton#StickyDangerBtn {{
    background-color: {'rgba(239, 68, 68, 0.11)' if dark else 'rgba(220, 38, 38, 0.07)'};
    color: {c['danger_fg']};
    border: 1px solid {'rgba(248, 113, 113, 0.45)' if dark else 'rgba(220, 38, 38, 0.38)'};
    border-radius: 8px;
    padding: 4px 11px;
    font-size: 11px;
    font-weight: 600;
}}
QPushButton#StickyDangerBtn:hover {{
    background-color: {c['danger_hover']};
    border-color: {c['danger_fg']};
}}
QPushButton#SubtleIconBtn {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 0px;
    color: {c['muted']};
    font-size: 12px;
    font-weight: 700;
}}
QPushButton#SubtleIconBtn:hover {{
    background-color: {c['btn_hover']};
    color: {c['fg']};
}}
QPushButton#DangerBtn {{
    background-color: transparent;
    color: {c['danger_fg']};
    border: 1px solid {c['danger_b']};
    border-radius: 8px;
    padding: 7px 12px;
    font-size: 12px;
    font-weight: 600;
}}
QPushButton#DangerBtn:hover {{
    background-color: {c['danger_hover']};
}}
QPushButton#IconDangerBtn {{
    background-color: transparent;
    color: {c['danger_fg']};
    border: 1px solid {c['danger_b']};
    border-radius: 7px;
    padding: 0px;
    font-size: 12px;
    font-weight: 700;
}}
QPushButton#IconDangerBtn:hover {{
    background-color: {c['danger_hover']};
}}
QLabel#Muted {{
    color: {c['muted']};
}}
QLabel#MetaChip {{
    background-color: {c['chip_bg']};
    color: {c['chip_fg']};
    border-radius: 6px;
    padding: 3px 8px;
    font-size: 11px;
}}
QLineEdit, QComboBox, QSpinBox {{
    background-color: {c['inp_bg']};
    color: {c['fg']};
    border: 1px solid {c['inp_b']};
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 12px;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border-color: {accent};
}}
QComboBox {{
    padding-right: 26px;
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 24px;
    border: none;
    background: transparent;
}}
QComboBox::down-arrow {{
    image: url("{dn}");
    width: 12px;
    height: 12px;
}}
QComboBox QAbstractItemView {{
    background-color: {c['menu_bg']};
    color: {c['fg']};
    border: 1px solid {c['btn_b']};
    selection-background-color: {accent};
    selection-color: {ac_text};
    padding: 4px;
    outline: none;
}}
QSpinBox {{
    padding-right: 22px;
}}
QSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 20px;
    border: none;
    background: transparent;
    margin-top: 2px;
    margin-right: 2px;
}}
QSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 20px;
    border: none;
    background: transparent;
    margin-bottom: 2px;
    margin-right: 2px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background-color: {c['nav_hover']};
    border-radius: 4px;
}}
QSpinBox::up-arrow {{
    image: url("{up}");
    width: 10px;
    height: 10px;
}}
QSpinBox::down-arrow {{
    image: url("{dn}");
    width: 10px;
    height: 10px;
}}
QMenu {{
    background-color: {c['menu_bg']};
    color: {c['fg']};
    border: 1px solid {c['btn_b']};
    border-radius: 10px;
    padding: 8px;
}}
QMenu::item {{
    padding: 8px 28px 8px 14px;
    margin: 2px 0px;
    border-radius: 6px;
    font-size: 12px;
}}
QMenu::item:selected {{
    background-color: {accent};
    color: {ac_text};
}}
QMenu::item:disabled {{
    color: {c['muted']};
}}
QMenu::icon {{
    padding-left: 8px;
}}
QMenu::separator {{
    height: 1px;
    background-color: {c['btn_b']};
    margin: 6px 8px;
}}
QMenu::right-arrow {{
    margin-right: 6px;
}}
QToolTip {{
    background-color: {c['menu_bg']};
    color: {c['fg']};
    border: 1px solid {accent};
    border-radius: 6px;
    padding: 6px 8px;
    font-size: 11px;
}}
QCheckBox {{
    color: {c['fg']};
    spacing: 8px;
    font-size: 12px;
}}
QRadioButton {{
    color: {c['fg']};
    spacing: 6px;
    font-size: 12px;
}}
QDialog {{
    background-color: {c['bg']};
    color: {c['fg']};
}}
QColorDialog QLabel {{
    color: {c['fg']};
    font-size: 13px;
    font-weight: 500;
    padding: 2px 4px 2px 2px;
}}
QColorDialog QSpinBox {{
    background-color: {c['inp_bg']};
    color: {c['fg']};
    border: 1px solid {c['inp_b']};
    border-radius: 7px;
    padding: 3px 22px 3px 8px;
    font-size: 13px;
    min-height: 22px;
}}
QColorDialog QSpinBox QLineEdit {{
    padding: 0px 2px;
    border: none;
    background: transparent;
    font-size: 13px;
}}
QColorDialog QLineEdit {{
    background-color: {c['inp_bg']};
    color: {c['fg']};
    border: 1px solid {c['inp_b']};
    border-radius: 7px;
    padding: 3px 8px;
    font-size: 13px;
    min-height: 22px;
}}
QColorDialog QPushButton {{
    background-color: {c['btn_bg']};
    color: {c['btn_fg']};
    border: 1px solid {c['btn_b']};
    border-radius: 7px;
    padding: 6px 14px;
    font-size: 13px;
    font-weight: 600;
    min-height: 26px;
}}
QColorDialog QPushButton:hover {{
    background-color: {c['btn_hover']};
    border-color: {accent};
}}
QColorDialog QDialogButtonBox QPushButton {{
    padding: 6px 26px;
    min-width: 88px;
}}
QProgressBar {{
    background-color: {c['inp_bg']};
    color: {c['fg']};
    border: 1px solid {c['inp_b']};
    border-radius: 6px;
    text-align: center;
    height: 18px;
    font-size: 11px;
    font-weight: 600;
}}
QProgressBar::chunk {{
    background-color: {accent};
    border-radius: 5px;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 2px 0px;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: {c['scroll_h']};
    border-radius: 4px;
    min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{
    background: {c['scroll_h_hover']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
    background: none;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
    margin: 0px 2px;
    border: none;
}}
QScrollBar::handle:horizontal {{
    background: {c['scroll_h']};
    border-radius: 4px;
    min-width: 32px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {c['scroll_h_hover']};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
    background: none;
}}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: none;
}}
"""
    return scale_qss_fonts(qss, font_scale)
