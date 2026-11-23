import json
import os
import re
import shutil

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPen, QPixmap, QRadialGradient

from .config import APP_DIR, THEMES_DIR

TEMPLATE_FILENAME = "_template.json"
MIKU_SIDEBAR_IMG = os.path.join(THEMES_DIR, "miku_sidebar.png")
_BUNDLED_MIKU_ASSET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "miku_sidebar.png")
TETO_SIDEBAR_IMG = os.path.join(THEMES_DIR, "teto_sidebar.png")
_BUNDLED_TETO_ASSET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "teto_sidebar.png")
MIKU_MINI_IMG = os.path.join(THEMES_DIR, "miku_miniplayer.png")
_BUNDLED_MIKU_MINI_ASSET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "miku_miniplayer.png")
TETO_MINI_IMG = os.path.join(THEMES_DIR, "teto_miniplayer.png")
_BUNDLED_TETO_MINI_ASSET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "teto_miniplayer.png")

THEME_TEMPLATE = {
    "_comment": "SongPulse Custom Theme Template. Copy and rename this file (e.g. my_theme.json) in the themes folder, or use Settings -> Create Theme.",
    "name": "My Custom Theme",
    "base_dark": True,
    "background": {
        "type": "gradient",  # "solid", "gradient", or "image"
        "color1": "#0B1628",
        "color2": "#132E4A",
        "angle": 135,
        "image": "",
        "blur": 6,
        "overlay_alpha": 115,
    },
    "sidebar": {
        "type": "gradient",  # "solid", "gradient", or "image"
        "color1": "#0D1E36",
        "color2": "#102840",
        "image": "",
        "blur": 8,
        "glass_alpha": 135,
    },
    "colors": {
        "accent": "#38BDF8",
        "accent_secondary": "#39C5BB",
        "card_bg": "rgba(18, 28, 44, 0.72)",
        "card_border": "rgba(148, 210, 255, 0.22)",
        "text": "#F4F8FF",
        "muted": "#94B3CE",
    },
    "waveform": {
        "style": "auto",  # "auto", "radial", "horizontal", "pulse_line", "bar", "dots"
        "mirror": False,
        "idle_dots": False,
        "color1": "#38BDF8",
        "color2": "#8B5CF6",
    },
}


def ensure_miku_sidebar_art() -> str:
    """Returns Miku character sidebar image (prefers bundled character art, copies to themes folder)."""
    os.makedirs(THEMES_DIR, exist_ok=True)
    for cand in (
        _BUNDLED_MIKU_ASSET,
        os.path.join(APP_DIR, "_internal", "songpulse", "assets", "miku_sidebar.png"),
        os.path.join(APP_DIR, "songpulse", "assets", "miku_sidebar.png"),
    ):
        if os.path.isfile(cand):
            try:
                if (not os.path.isfile(MIKU_SIDEBAR_IMG)) or (
                    os.path.getsize(MIKU_SIDEBAR_IMG) != os.path.getsize(cand)
                ):
                    shutil.copy2(cand, MIKU_SIDEBAR_IMG)
            except OSError:
                return cand
            return MIKU_SIDEBAR_IMG
    if os.path.isfile(MIKU_SIDEBAR_IMG):
        return MIKU_SIDEBAR_IMG
    try:
        os.makedirs(THEMES_DIR, exist_ok=True)
        w, h = 420, 960
        pix = QPixmap(w, h)
        pix.fill(QColor("#061822"))
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Deep cyan-blue base gradient
        bg = QLinearGradient(0, 0, w, h)
        bg.setColorAt(0.0, QColor("#041926"))
        bg.setColorAt(0.45, QColor("#0A3B54"))
        bg.setColorAt(0.85, QColor("#126E7D"))
        bg.setColorAt(1.0, QColor("#062333"))
        p.fillRect(0, 0, w, h, QBrush(bg))

        # Soft glowing vocaloid aura orbs
        for cx, cy, rad, col in [
            (310, 180, 220, QColor(57, 197, 187, 130)),
            (80, 520, 250, QColor(56, 189, 248, 120)),
            (340, 810, 230, QColor(244, 114, 182, 80)),
            (180, 320, 170, QColor(125, 211, 252, 95)),
        ]:
            rg = QRadialGradient(QPointF(cx, cy), rad)
            rg.setColorAt(0.0, col)
            rg.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.setBrush(QBrush(rg))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(cx, cy), rad, rad)

        # Stylized sweeping twin-tail turquoise ribbons & equalizer accent stripes
        p.setPen(QPen(QColor(57, 197, 187, 145), 14, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(QRectF(-90, 90, 380, 620), -30 * 16, 130 * 16)
        p.setPen(QPen(QColor(125, 211, 252, 110), 8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(QRectF(60, 240, 420, 660), 110 * 16, 125 * 16)

        # Subtle magenta hair-tie accent bars ("01" vibe)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(244, 114, 182, 165)))
        p.drawRoundedRect(QRectF(36, 730, 54, 10), 5, 5)
        p.drawRoundedRect(QRectF(36, 748, 34, 8), 4, 4)

        # Subtle vertical soundwave motif
        for idx, bh in enumerate([32, 68, 112, 84, 140, 96, 56, 36]):
            x = 240 + idx * 18
            y = 660 - bh / 2
            p.setBrush(QBrush(QColor(186, 248, 244, 95)))
            p.drawRoundedRect(QRectF(x, y, 8, bh), 4, 4)

        p.end()
        pix.save(MIKU_SIDEBAR_IMG, "PNG")
    except Exception:
        return ""
    return MIKU_SIDEBAR_IMG


def ensure_teto_sidebar_art() -> str:
    """Returns Teto character sidebar cutout (prefers bundled character art, copies to themes folder)."""
    os.makedirs(THEMES_DIR, exist_ok=True)
    for cand in (
        _BUNDLED_TETO_ASSET,
        os.path.join(APP_DIR, "_internal", "songpulse", "assets", "teto_sidebar.png"),
        os.path.join(APP_DIR, "songpulse", "assets", "teto_sidebar.png"),
    ):
        if os.path.isfile(cand):
            try:
                if (not os.path.isfile(TETO_SIDEBAR_IMG)) or (
                    os.path.getsize(TETO_SIDEBAR_IMG) != os.path.getsize(cand)
                ):
                    shutil.copy2(cand, TETO_SIDEBAR_IMG)
            except OSError:
                return cand
            return TETO_SIDEBAR_IMG
    if os.path.isfile(TETO_SIDEBAR_IMG):
        return TETO_SIDEBAR_IMG
    return ""


def _ensure_bundled_theme_art(dest_path: str, bundled_path: str, filename: str) -> str:
    """Resolves a bundled character art image and syncs it into THEMES_DIR."""
    os.makedirs(THEMES_DIR, exist_ok=True)
    for cand in (
        bundled_path,
        os.path.join(APP_DIR, "_internal", "songpulse", "assets", filename),
        os.path.join(APP_DIR, "songpulse", "assets", filename),
    ):
        if os.path.isfile(cand):
            try:
                if (not os.path.isfile(dest_path)) or (
                    os.path.getsize(dest_path) != os.path.getsize(cand)
                ):
                    shutil.copy2(cand, dest_path)
            except OSError:
                return cand
            return dest_path
    if os.path.isfile(dest_path):
        return dest_path
    return ""


def ensure_miku_miniplayer_art() -> str:
    """Returns Miku miniplayer 1:1 background image."""
    return _ensure_bundled_theme_art(
        MIKU_MINI_IMG, _BUNDLED_MIKU_MINI_ASSET, "miku_miniplayer.png"
    )


def ensure_teto_miniplayer_art() -> str:
    """Returns Teto miniplayer 1:1 background image."""
    return _ensure_bundled_theme_art(
        TETO_MINI_IMG, _BUNDLED_TETO_MINI_ASSET, "teto_miniplayer.png"
    )


def ensure_theme_template_and_assets():
    os.makedirs(THEMES_DIR, exist_ok=True)
    tmpl_path = os.path.join(THEMES_DIR, TEMPLATE_FILENAME)
    if not os.path.exists(tmpl_path):
        try:
            with open(tmpl_path, "w", encoding="utf-8") as f:
                json.dump(THEME_TEMPLATE, f, indent=2)
        except Exception:
            pass
    ensure_miku_sidebar_art()
    ensure_teto_sidebar_art()
    ensure_miku_miniplayer_art()
    ensure_teto_miniplayer_art()


def get_builtin_miku_theme() -> dict:
    img = ensure_miku_sidebar_art()
    mini_img = ensure_miku_miniplayer_art()
    return {
        "id": "miku",
        "name": "Miku",
        "base_dark": True,
        "background": {
            "type": "gradient",
            "color1": "#061926",
            "color2": "#0C3E59",
            "angle": 145,
            "image": "",
            "blur": 0,
            "overlay_alpha": 0,
        },
        "sidebar": {
            "type": "image" if img else "gradient",
            "color1": "#061C2B",
            "color2": "#0C485C",
            "image": img,
            "fit": "cutout",
            "blur": 1,
            "glass_alpha": 42,
        },
        "miniplayer": {
            "type": "image" if mini_img else "gradient",
            "color1": "#061C2B",
            "color2": "#0C485C",
            "angle": 145,
            "image": mini_img,
            "fit": "cover",
            "blur": 1,
            "overlay_alpha": 52,
            "glass_alpha": 52,
        },
        "colors": {
            "accent": "#39C5BB",
            "accent_secondary": "#38BDF8",
            "card_bg": "rgba(9, 29, 44, 0.72)",
            "card_border": "rgba(57, 197, 187, 0.30)",
            "text": "#F0FDFA",
            "muted": "#99D6DC",
        },
        "waveform": {
            "style": "pulse_line",
            "mirror": True,
            "idle_dots": True,
            "flip_hz": True,
            "hair_ties": True,
            "color1": "#39C5BB",
            "color2": "#38BDF8",
        },
    }


def get_builtin_teto_theme() -> dict:
    img = ensure_teto_sidebar_art()
    mini_img = ensure_teto_miniplayer_art()
    return {
        "id": "teto",
        "name": "Teto",
        "base_dark": True,
        "background": {
            "type": "gradient",
            "color1": "#1D070F",
            "color2": "#4A1224",
            "angle": 145,
            "image": "",
            "blur": 0,
            "overlay_alpha": 0,
        },
        "sidebar": {
            "type": "image" if img else "gradient",
            "color1": "#1A060D",
            "color2": "#3B0E1D",
            "image": img,
            "fit": "cutout",
            "blur": 1,
            "glass_alpha": 42,
        },
        "miniplayer": {
            "type": "image" if mini_img else "gradient",
            "color1": "#1A060D",
            "color2": "#3B0E1D",
            "angle": 145,
            "image": mini_img,
            "fit": "cover",
            "blur": 1,
            "overlay_alpha": 52,
            "glass_alpha": 52,
        },
        "colors": {
            "accent": "#E11D48",
            "accent_secondary": "#FB7185",
            "card_bg": "rgba(34, 10, 19, 0.72)",
            "card_border": "rgba(244, 63, 94, 0.30)",
            "text": "#FFF5F7",
            "muted": "#E2AAB8",
        },
        "waveform": {
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


def list_custom_themes() -> list[tuple[str, str]]:
    """Returns [(theme_key, display_name)] for all user .json themes in DATA_DIR/themes."""
    ensure_theme_template_and_assets()
    out = []
    try:
        for entry in sorted(os.scandir(THEMES_DIR), key=lambda e: e.name.lower()):
            if (
                entry.is_file()
                and entry.name.lower().endswith(".json")
                and entry.name.lower() != TEMPLATE_FILENAME
            ):
                tid = os.path.splitext(entry.name)[0]
                disp = tid.replace("_", " ").replace("-", " ").title()
                try:
                    with open(entry.path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict) and data.get("name"):
                            disp = str(data["name"]).strip()
                except Exception:
                    pass
                out.append((f"custom:{tid}", disp))
    except OSError:
        pass
    return out


def list_all_theme_choices() -> list[tuple[str, str]]:
    choices = [
        ("auto", "Match Windows"),
        ("dark", "Dark"),
        ("light", "Light"),
        ("miku", "Miku (Blue Gradient + Pulse Line)"),
        ("teto", "Teto (Crimson Gradient + Split Wave)"),
    ]
    choices.extend(list_custom_themes())
    return choices


def load_theme_config(theme_key: str, system_dark: bool) -> dict:
    """Resolves any theme key into a complete normalized theme config dictionary."""
    ensure_theme_template_and_assets()
    key = (theme_key or "auto").strip()

    if key == "miku":
        return get_builtin_miku_theme()
    if key == "teto":
        return get_builtin_teto_theme()

    if key.startswith("custom:"):
        tid = key.split("custom:", 1)[1].strip()
        path = os.path.join(THEMES_DIR, f"{tid}.json")
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    merged = json.loads(json.dumps(THEME_TEMPLATE))
                    merged["id"] = key
                    merged["name"] = raw.get("name") or tid
                    merged["base_dark"] = bool(raw.get("base_dark", True))
                    for sec in ("background", "sidebar", "colors", "waveform"):
                        if isinstance(raw.get(sec), dict):
                            merged[sec].update(raw[sec])
                    return merged
            except Exception:
                pass

    dark = system_dark if key == "auto" else (key != "light")
    if dark:
        return {
            "id": "dark",
            "name": "Dark",
            "base_dark": True,
            "background": {"type": "solid", "color1": "#111216", "color2": "#111216", "angle": 135, "image": "", "blur": 0, "overlay_alpha": 0},
            "sidebar": {"type": "solid", "color1": "#17181D", "color2": "#17181D", "image": "", "blur": 0, "glass_alpha": 255},
            "colors": {
                "accent": "#2563EB",
                "accent_secondary": "#8B5CF6",
                "card_bg": "#1A1B21",
                "card_border": "#272932",
                "text": "#F4F4F5",
                "muted": "#9CA3AF",
            },
            "waveform": {"style": "radial", "mirror": False, "idle_dots": False, "color1": "#3B82F6", "color2": "#8B5CF6"},
        }
    return {
        "id": "light",
        "name": "Light",
        "base_dark": False,
        "background": {"type": "solid", "color1": "#E9EDF3", "color2": "#E9EDF3", "angle": 135, "image": "", "blur": 0, "overlay_alpha": 0},
        "sidebar": {"type": "solid", "color1": "#F4F6FA", "color2": "#F4F6FA", "image": "", "blur": 0, "glass_alpha": 255},
        "colors": {
            "accent": "#2563EB",
            "accent_secondary": "#7C3AED",
            "card_bg": "#FFFFFF",
            "card_border": "#D4DBE6",
            "text": "#111827",
            "muted": "#526075",
        },
        "waveform": {"style": "radial", "mirror": False, "idle_dots": False, "color1": "#2563EB", "color2": "#7C3AED"},
    }


def save_custom_theme(name: str, data: dict) -> str:
    """Saves a custom theme JSON inside DATA_DIR/themes and returns its theme_key ('custom:<slug>')."""
    ensure_theme_template_and_assets()
    slug = re.sub(r"[^a-z0-9_-]+", "_", (name or "custom_theme").strip().lower()).strip("_")
    if not slug or slug == "_template":
        slug = "custom_theme"
    path = os.path.join(THEMES_DIR, f"{slug}.json")
    payload = dict(data)
    payload["name"] = (name or slug).strip()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return f"custom:{slug}"
