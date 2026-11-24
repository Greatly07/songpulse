import hashlib
import json
import os
import re
import shutil

from .config import APP_DIR, THEME_PHOTOS_DIR, THEMES_DIR
from .waveforms import resolve_template_waveform

TEMPLATE_FILENAME = "_template.json"

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
        "darken_black": True,
    },
    "sidebar": {
        "type": "gradient",  # "solid", "gradient", or "image"
        "color1": "#0D1E36",
        "color2": "#102840",
        "image": "",
        "blur": 8,
        "glass_alpha": 135,
        "darken_black": True,
    },
    "miniplayer": {
        "use_main_bg": True,
        "type": "gradient",  # "solid", "gradient", or "image"
        "color1": "#0B1628",
        "color2": "#132E4A",
        "angle": 135,
        "image": "",
        "blur": 6,
        "overlay_alpha": 115,
        "darken_black": True,
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


def theme_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "_", (name or "custom_theme").strip().lower()).strip("_")
    if not slug or slug == "_template":
        return "custom_theme"
    return slug


def _bundled_themes_roots() -> list[str]:
    return [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "themes"),
        os.path.join(APP_DIR, "_internal", "songpulse", "assets", "themes"),
        os.path.join(APP_DIR, "songpulse", "assets", "themes"),
    ]


def ensure_theme_template_and_assets():
    os.makedirs(THEMES_DIR, exist_ok=True)
    os.makedirs(THEME_PHOTOS_DIR, exist_ok=True)
    tmpl_path = os.path.join(THEMES_DIR, TEMPLATE_FILENAME)
    if not os.path.exists(tmpl_path):
        try:
            with open(tmpl_path, "w", encoding="utf-8") as f:
                json.dump(THEME_TEMPLATE, f, indent=2)
        except Exception:
            pass

    for root in _bundled_themes_roots():
        if not os.path.isdir(root):
            continue
        try:
            for entry in os.scandir(root):
                if not entry.is_dir() or entry.name.startswith((".", "_")):
                    continue
                dest_dir = os.path.join(THEMES_DIR, entry.name)
                os.makedirs(dest_dir, exist_ok=True)
                for f_entry in os.scandir(entry.path):
                    if not f_entry.is_file():
                        continue
                    dest_file = os.path.join(dest_dir, f_entry.name)
                    try:
                        if (not os.path.isfile(dest_file)) or (
                            os.path.getsize(dest_file) != os.path.getsize(f_entry.path)
                        ):
                            shutil.copy2(f_entry.path, dest_file)
                    except OSError:
                        pass
        except OSError:
            pass


def resolve_theme_image_path(image_path: str, base_dir: str = "") -> str:
    """Resolves a theme image path (relative to template dir, THEMES_DIR, THEME_PHOTOS_DIR, or absolute)."""
    raw = str(image_path or "").strip()
    if not raw:
        return ""
    if os.path.isabs(raw):
        if os.path.isfile(raw):
            return os.path.normpath(raw)
        in_photos = os.path.join(THEME_PHOTOS_DIR, os.path.basename(raw))
        if os.path.isfile(in_photos):
            return os.path.normpath(in_photos)
        return ""

    candidates = []
    if base_dir:
        candidates.append(os.path.join(base_dir, raw))
    candidates.append(os.path.join(THEMES_DIR, raw))
    candidates.append(os.path.join(THEME_PHOTOS_DIR, os.path.basename(raw)))
    for cand in candidates:
        if os.path.isfile(cand):
            return os.path.normpath(cand)
    return ""


def import_theme_photo(source_path: str) -> str:
    """Copies an external image into THEME_PHOTOS_DIR with a content-hashed filename and returns 'photos/<name>'."""
    raw = str(source_path or "").strip()
    if not raw:
        return ""
    os.makedirs(THEME_PHOTOS_DIR, exist_ok=True)

    resolved = resolve_theme_image_path(raw)
    src = resolved if resolved else raw
    if not os.path.isfile(src):
        return raw

    photos_abs = os.path.normcase(os.path.abspath(THEME_PHOTOS_DIR))
    src_abs = os.path.normcase(os.path.abspath(src))
    if os.path.dirname(src_abs) == photos_abs:
        return f"photos/{os.path.basename(src)}"

    stem, ext = os.path.splitext(os.path.basename(src))
    safe_stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", stem).strip("_") or "photo"
    ext = ext.lower() if ext else ".png"

    h = hashlib.sha256()
    try:
        with open(src, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        digest = h.hexdigest()[:12]
    except OSError:
        digest = hashlib.sha256(src.encode("utf-8", errors="ignore")).hexdigest()[:12]

    dest_name = f"{safe_stem}_{digest}{ext}"
    dest_path = os.path.join(THEME_PHOTOS_DIR, dest_name)
    if not os.path.isfile(dest_path):
        shutil.copy2(src, dest_path)
    return f"photos/{dest_name}"


def _find_template_json(tid: str) -> tuple[str, str]:
    """Returns (json_path, template_dir) for a standalone template theme folder."""
    search_dirs = [os.path.join(THEMES_DIR, tid)]
    for root in _bundled_themes_roots():
        search_dirs.append(os.path.join(root, tid))
    for tdir in search_dirs:
        if not os.path.isdir(tdir):
            continue
        for fname in (f"{tid}.json", "theme.json"):
            cand = os.path.join(tdir, fname)
            if os.path.isfile(cand):
                return cand, tdir
    return "", ""


def list_template_themes() -> list[tuple[str, str]]:
    """Discovers standalone template themes in DATA_DIR/themes/<id>/."""
    ensure_theme_template_and_assets()
    found = {}
    if os.path.isdir(THEMES_DIR):
        try:
            for entry in os.scandir(THEMES_DIR):
                if (
                    not entry.is_dir()
                    or entry.name == "photos"
                    or entry.name.startswith((".", "_"))
                ):
                    continue
                tid = entry.name
                jpath, _tdir = _find_template_json(tid)
                if not jpath:
                    continue
                disp = tid.replace("_", " ").replace("-", " ").title()
                try:
                    with open(jpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        disp = str(data.get("display_name") or data.get("name") or disp).strip()
                except Exception:
                    pass
                found[tid] = disp
        except OSError:
            pass

    ordered = []
    for preferred in ("miku", "teto"):
        if preferred in found:
            ordered.append((preferred, found.pop(preferred)))
    for tid in sorted(found, key=lambda k: found[k].lower()):
        ordered.append((tid, found[tid]))
    return ordered


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
    ]
    choices.extend(list_template_themes())
    choices.extend(list_custom_themes())
    return choices


def find_theme_name_conflict(name: str, ignore_theme_key: str = "") -> str | None:
    """Returns a conflicting theme display name if `name` collides with a built-in, template, or custom theme."""
    ensure_theme_template_and_assets()
    clean = (name or "").strip()
    if not clean:
        return None
    low = clean.lower()
    cand_slug = theme_slug(clean)

    for key, label in [("auto", "Match Windows"), ("dark", "Dark"), ("light", "Light")]:
        if low in (key, label.lower()) or cand_slug == key:
            return label
    if low == "_template" or cand_slug == "_template":
        return "_template"

    for tid, disp in list_template_themes():
        jpath, _tdir = _find_template_json(tid)
        t_names = {tid.lower(), disp.lower()}
        if jpath:
            try:
                with open(jpath, "r", encoding="utf-8") as f:
                    tdata = json.load(f)
                if isinstance(tdata, dict):
                    if tdata.get("name"):
                        t_names.add(str(tdata["name"]).strip().lower())
                    if tdata.get("display_name"):
                        t_names.add(str(tdata["display_name"]).strip().lower())
            except Exception:
                pass
        if low in t_names or cand_slug == tid.lower():
            return disp

    for ckey, cdisp in list_custom_themes():
        if ignore_theme_key and ckey == ignore_theme_key:
            continue
        ctid = ckey.split("custom:", 1)[1].strip()
        if low == cdisp.lower() or low == ctid.lower() or cand_slug == ctid.lower():
            return cdisp

    return None


def load_raw_custom_theme(theme_key: str) -> dict | None:
    """Loads the raw JSON dictionary for a saved custom theme (`custom:<id>`)."""
    if not theme_key or not str(theme_key).startswith("custom:"):
        return None
    tid = str(theme_key).split("custom:", 1)[1].strip()
    path = os.path.join(THEMES_DIR, f"{tid}.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else None
    except Exception:
        return None


def _normalize_loaded_theme(raw: dict, theme_id: str, base_dir: str = "") -> dict:
    merged = json.loads(json.dumps(THEME_TEMPLATE))
    merged["id"] = theme_id
    merged["name"] = raw.get("name") or theme_id
    merged["base_dark"] = bool(raw.get("base_dark", True))

    for sec in ("background", "sidebar", "colors", "waveform"):
        if isinstance(raw.get(sec), dict):
            merged[sec].update(raw[sec])

    raw_mini = raw.get("miniplayer")
    if isinstance(raw_mini, dict):
        merged["miniplayer"].update(raw_mini)
        use_main = bool(raw_mini.get("use_main_bg", False))
    else:
        use_main = True

    for sec in ("background", "sidebar", "miniplayer"):
        sec_cfg = merged.get(sec, {})
        if sec_cfg.get("type") == "image":
            resolved = resolve_theme_image_path(sec_cfg.get("image", ""), base_dir=base_dir)
            if resolved:
                sec_cfg["image"] = resolved
            else:
                sec_cfg["image"] = ""
                sec_cfg["type"] = "gradient"
        elif sec_cfg.get("image"):
            resolved = resolve_theme_image_path(sec_cfg.get("image", ""), base_dir=base_dir)
            sec_cfg["image"] = resolved

    if use_main:
        bg_copy = json.loads(json.dumps(merged["background"]))
        bg_copy["use_main_bg"] = True
        merged["miniplayer"] = bg_copy
    else:
        merged["miniplayer"]["use_main_bg"] = False

    merged["waveform"] = resolve_template_waveform(merged)
    return merged


def load_theme_config(theme_key: str, system_dark: bool) -> dict:
    """Resolves any theme key into a complete normalized theme config dictionary."""
    ensure_theme_template_and_assets()
    key = (theme_key or "auto").strip()

    if key.startswith("custom:"):
        tid = key.split("custom:", 1)[1].strip()
        path = os.path.join(THEMES_DIR, f"{tid}.json")
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    return _normalize_loaded_theme(raw, key, base_dir=THEMES_DIR)
            except Exception:
                pass
    elif key not in ("auto", "dark", "light"):
        jpath, tdir = _find_template_json(key)
        if jpath:
            try:
                with open(jpath, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    return _normalize_loaded_theme(raw, key, base_dir=tdir)
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
            "miniplayer": {"use_main_bg": True, "type": "solid", "color1": "#111216", "color2": "#111216", "angle": 135, "image": "", "blur": 0, "overlay_alpha": 0},
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
        "miniplayer": {"use_main_bg": True, "type": "solid", "color1": "#E9EDF3", "color2": "#E9EDF3", "angle": 135, "image": "", "blur": 0, "overlay_alpha": 0},
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


def save_custom_theme(name: str, data: dict, previous_theme_key: str = "") -> str:
    """Saves a custom theme JSON inside DATA_DIR/themes and returns its theme_key ('custom:<slug>')."""
    ensure_theme_template_and_assets()
    slug = theme_slug(name)
    path = os.path.join(THEMES_DIR, f"{slug}.json")
    payload = dict(data)
    payload["name"] = (name or slug).strip()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    if previous_theme_key and str(previous_theme_key).startswith("custom:"):
        old_slug = str(previous_theme_key).split("custom:", 1)[1].strip()
        if old_slug and old_slug != slug:
            old_path = os.path.join(THEMES_DIR, f"{old_slug}.json")
            if os.path.isfile(old_path):
                try:
                    os.remove(old_path)
                except OSError:
                    pass

    return f"custom:{slug}"
