from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF

TEMPLATE_WAVEFORM_PRESETS = {
    "miku": {
        "style": "pulse_line",
        "mirror": True,
        "idle_dots": True,
        "flip_hz": True,
        "hair_ties": True,
        "right_alt_white": False,
        "teto_bow": False,
        "color1": "#39C5BB",
        "color2": "#38BDF8",
    },
    "teto": {
        "style": "horizontal",
        "mirror": True,
        "idle_dots": True,
        "flip_hz": False,
        "hair_ties": False,
        "right_alt_white": True,
        "teto_bow": True,
        "color1": "#E11D48",
        "color2": "#FB7185",
    },
}


def resolve_template_waveform(theme_cfg: dict | None) -> dict:
    """Resolves template-specific default waveform settings by preset, theme id, or theme name."""
    cfg = theme_cfg or {}
    wf = dict(cfg.get("waveform") or {})

    candidates = [
        wf.get("preset", ""),
        cfg.get("id", ""),
        cfg.get("name", ""),
    ]
    matched_key = ""
    for raw in candidates:
        norm = str(raw or "").strip().lower()
        if norm.startswith("custom:"):
            norm = norm.split("custom:", 1)[1].strip()
        if norm in TEMPLATE_WAVEFORM_PRESETS:
            matched_key = norm
            break
        for preset_key in TEMPLATE_WAVEFORM_PRESETS:
            if preset_key in norm:
                matched_key = preset_key
                break
        if matched_key:
            break

    if not matched_key:
        return wf

    merged = dict(TEMPLATE_WAVEFORM_PRESETS[matched_key])
    for k, v in wf.items():
        if k == "style" and (not v or v == "auto"):
            continue
        if v is not None:
            merged[k] = v
    return merged


def paint_miku_hair_ties(p: QPainter, width: float, height: float, cx: float, cy: float):
    """Draws Miku's thick black-and-pink diamond ponytail ornaments centered right on the left & right sides of the Listen button."""
    p.save()
    clip = QPainterPath()
    clip.addRect(0.0, 0.0, float(width), float(height))
    btn_hole = QPainterPath()
    btn_hole.addEllipse(QPointF(cx, cy), 65.5, 65.5)
    p.setClipPath(clip.subtracted(btn_hole))

    rx = 18.0
    ry = 30.0
    for side in (-1, 1):
        dcx = cx + side * 66.0
        pts = QPolygonF(
            [
                QPointF(dcx, cy - ry),
                QPointF(dcx + rx, cy),
                QPointF(dcx, cy + ry),
                QPointF(dcx - rx, cy),
            ]
        )
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(
            QPen(
                QColor(244, 63, 94, 72),
                13.0,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.SquareCap,
                Qt.PenJoinStyle.MiterJoin,
            )
        )
        p.drawPolygon(pts)
        p.setPen(
            QPen(
                QColor("#141720"),
                9.4,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.SquareCap,
                Qt.PenJoinStyle.MiterJoin,
            )
        )
        p.drawPolygon(pts)
        p.setPen(
            QPen(
                QColor("#F43F5E"),
                3.6,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.SquareCap,
                Qt.PenJoinStyle.MiterJoin,
            )
        )
        p.drawPolygon(pts)

    p.restore()


def paint_miku_centerline_bars(
    p: QPainter, cx: float, cy: float, btn_gap: float, bars_per_side: int, step: float
):
    """Draws Miku's subtle thin straight gray centerline connecting the Listen button behind & between bar waveforms."""
    end_x_dist = btn_gap + (bars_per_side - 1) * step
    p.setPen(
        QPen(
            QColor(186, 200, 214, 72),
            0.8,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
        )
    )
    for side in (-1, 1):
        p.drawLine(
            QPointF(cx + side * 66.0, cy),
            QPointF(cx + side * end_x_dist, cy),
        )


def paint_miku_centerline_pulse(p: QPainter, cx: float, cy: float, end_dist: float):
    """Draws Miku's subtle light gray centerline behind the pulse waveform, fading out cleanly toward the outside."""
    for side in (-1, 1):
        lx0 = cx + side * 65.0
        lx1 = cx + side * end_dist
        line_grad = QLinearGradient(QPointF(lx0, cy), QPointF(lx1, cy))
        line_grad.setColorAt(0.0, QColor(186, 200, 214, 72))
        line_grad.setColorAt(0.88, QColor(186, 200, 214, 68))
        line_grad.setColorAt(1.0, QColor(186, 200, 214, 0))
        p.setPen(
            QPen(
                QBrush(line_grad),
                0.8,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.FlatCap,
            )
        )
        p.drawLine(QPointF(lx0, cy), QPointF(lx1, cy))


def paint_teto_bow(p: QPainter, width: float, height: float, cx: float, cy: float):
    """Draws Kasane Teto's white double-ear ribbon hairtie perched on the top-right of the Listen button."""
    p.save()
    clip = QPainterPath()
    clip.addRect(0.0, 0.0, float(width), float(height))
    btn_hole = QPainterPath()
    btn_hole.addEllipse(QPointF(cx, cy), 65.0, 65.0)
    p.setClipPath(clip.subtracted(btn_hole))

    p.translate(cx + 47.0, cy - 44.5)
    p.rotate(30.0)

    # Left/inner shorter pointed white ribbon ear
    ear_left = QPainterPath()
    ear_left.moveTo(0.0, 2.0)
    ear_left.cubicTo(-13.0, -7.0, -18.0, -18.0, -11.0, -31.0)
    ear_left.cubicTo(-4.0, -22.0, 0.0, -12.0, 4.0, -1.0)
    ear_left.closeSubpath()

    # Right/main taller pointed white ribbon ear sweeping upward-right
    ear_right = QPainterPath()
    ear_right.moveTo(1.0, -1.0)
    ear_right.cubicTo(3.0, -18.0, 10.0, -30.0, 20.0, -38.0)
    ear_right.cubicTo(24.0, -23.0, 21.0, -10.0, 8.0, 4.0)
    ear_right.closeSubpath()

    # Soft white outer halo so the bow lifts cleanly off the dark crimson background
    glow_pen = QPen(
        QColor(255, 245, 248, 56),
        7.0,
        Qt.PenStyle.SolidLine,
        Qt.PenCapStyle.RoundCap,
        Qt.PenJoinStyle.RoundJoin,
    )
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(glow_pen)
    p.drawPath(ear_left)
    p.drawPath(ear_right)

    # Crisp anime ink outline + bright white gradient fill for both ears
    outline_pen = QPen(
        QColor("#1A1116"),
        2.2,
        Qt.PenStyle.SolidLine,
        Qt.PenCapStyle.RoundCap,
        Qt.PenJoinStyle.RoundJoin,
    )
    grad_l = QLinearGradient(QPointF(-12.0, -30.0), QPointF(2.0, 2.0))
    grad_l.setColorAt(0.0, QColor("#FFFFFF"))
    grad_l.setColorAt(0.72, QColor("#F1F5F9"))
    grad_l.setColorAt(1.0, QColor("#CBD5E1"))
    p.setPen(outline_pen)
    p.setBrush(QBrush(grad_l))
    p.drawPath(ear_left)

    grad_r = QLinearGradient(QPointF(18.0, -36.0), QPointF(5.0, 3.0))
    grad_r.setColorAt(0.0, QColor("#FFFFFF"))
    grad_r.setColorAt(0.68, QColor("#F8FAFC"))
    grad_r.setColorAt(1.0, QColor("#CBD5E1"))
    p.setBrush(QBrush(grad_r))
    p.drawPath(ear_right)

    # Subtle silver crease lines inside the ribbon loops
    crease_pen = QPen(QColor("#94A3B8"), 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(crease_pen)
    fold_l = QPainterPath()
    fold_l.moveTo(-2.0, -3.0)
    fold_l.quadTo(-9.0, -13.0, -9.5, -22.0)
    p.drawPath(fold_l)
    fold_r = QPainterPath()
    fold_r.moveTo(6.0, -3.0)
    fold_r.quadTo(12.0, -16.0, 16.5, -28.0)
    p.drawPath(fold_r)

    # Small rounded ribbon knot sitting on the top-right curve of the button
    p.save()
    p.translate(4.0, 0.5)
    p.rotate(42.0)
    knot_grad = QLinearGradient(QPointF(-6.0, -5.0), QPointF(6.0, 5.0))
    knot_grad.setColorAt(0.0, QColor("#FFFFFF"))
    knot_grad.setColorAt(1.0, QColor("#CBD5E1"))
    p.setPen(outline_pen)
    p.setBrush(QBrush(knot_grad))
    p.drawRoundedRect(QRectF(-7.5, -4.8, 15.0, 9.6), 4.2, 4.2)
    p.restore()

    p.restore()
