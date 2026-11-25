import copy
import ctypes
import datetime
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
import winreg
import winsound

import numpy as np
import soundfile as sf

# --- APP METADATA & PATHS ---
APP_NAME = "SongPulse"
APP_VERSION = "1.2.0"
APP_USER_MODEL_ID = f"Greatly07.SongPulse.{APP_VERSION}"
GITHUB_REPO = "Greatly07/songpulse"
YTDLP_GITHUB_REPO = "Greatly07/yt-dlp-downloader"

IS_FROZEN = getattr(sys, "frozen", False)
if IS_FROZEN:
    APP_DIR = os.path.dirname(sys.executable)
    DATA_DIR = os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP_NAME
    )
else:
    APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    DATA_DIR = os.path.join(APP_DIR, "data")

COVERS_DIR = os.path.join(DATA_DIR, "covers")
TEMP_DIR = os.path.join(DATA_DIR, "temp")
MODELS_DIR = os.path.join(DATA_DIR, "models")
ICONS_DIR = os.path.join(DATA_DIR, "ui_icons")
THEMES_DIR = os.path.join(DATA_DIR, "themes")
THEME_PHOTOS_DIR = os.path.join(THEMES_DIR, "photos")
AI_RUNTIME_DIR = os.path.join(DATA_DIR, "ai_runtime")
TOOLS_DIR = os.path.join(DATA_DIR, "tools")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
PLAYLISTS_FILE = os.path.join(DATA_DIR, "playlists.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
ICON_CACHE_FILE = os.path.join(DATA_DIR, "songpulse.ico")
PING_SOUND_FILE = os.path.join(DATA_DIR, "ping.wav")
SAVED_CLIP_WAV = os.path.join(TEMP_DIR, "saved_test_clip.wav")
SINGLE_INSTANCE_KEY = "SongPulse_SingleInstance_LocalServer_v1"

for _d in (DATA_DIR, COVERS_DIR, TEMP_DIR, MODELS_DIR, ICONS_DIR, THEMES_DIR, THEME_PHOTOS_DIR):
    os.makedirs(_d, exist_ok=True)


def setup_runtime_paths():
    """Registers optional AI runtime, bundled tools/ffmpeg, and CUDA DLL paths cleanly."""
    if os.path.isdir(AI_RUNTIME_DIR) and AI_RUNTIME_DIR not in sys.path:
        sys.path.insert(0, AI_RUNTIME_DIR)

    dll_candidates = [
        os.path.join(APP_DIR, "bin"),
        os.path.join(APP_DIR, "_internal", "bin"),
        os.path.join(AI_RUNTIME_DIR, "bin"),
        os.path.join(AI_RUNTIME_DIR, "torch", "lib"),
        os.path.join(AI_RUNTIME_DIR, "onnxruntime", "capi"),
        os.path.join(TOOLS_DIR, "bin"),
        TOOLS_DIR,
    ]

    for base in (
        os.path.join(APP_DIR, "_internal", "nvidia"),
        os.path.join(AI_RUNTIME_DIR, "nvidia"),
    ):
        if os.path.isdir(base):
            try:
                for entry in os.scandir(base):
                    if entry.is_dir():
                        bdir = os.path.join(entry.path, "bin")
                        if os.path.isdir(bdir):
                            dll_candidates.append(bdir)
            except OSError:
                pass

    for _cuda_ver in ("v13.5", "v13.4", "v13.3", "v13.2", "v13.1", "v13.0", "v12.8", "v12.6"):
        dll_candidates.append(
            rf"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\{_cuda_ver}\bin\x64"
        )
        dll_candidates.append(
            rf"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\{_cuda_ver}\bin"
        )

    cur_path = os.environ.get("PATH", "")
    for p in dll_candidates:
        if os.path.isdir(p):
            try:
                os.add_dll_directory(p)
            except Exception:
                pass
            if p not in cur_path:
                cur_path = p + os.pathsep + cur_path
    os.environ["PATH"] = cur_path


setup_runtime_paths()

ort = None


def _try_load_ort():
    global ort
    if ort is not None:
        return ort
    try:
        import onnxruntime as _ort

        if hasattr(_ort, "preload_dlls"):
            _ort.preload_dlls()
        ort = _ort
    except Exception:
        ort = None
    return ort


_try_load_ort()

SAMPLE_RATE = 44100
CHUNK_FRAMES = 1470  # ~33ms chunks (30 Hz waveform updates)
UVR_MODEL_NAME = "Reverb_HQ_By_FoxJoy.onnx"

_UVR_PUBLIC_BASE = (
    "https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models"
)

# Curated tricky-track rescue models (De-Reverb, De-Noise/SFX, Crowd, Lyrics Whisper & Speech Stripper)
AI_MODEL_CATALOG = [
    {
        "filename": "Reverb_HQ_By_FoxJoy.onnx",
        "name": "De-Reverb HQ (FoxJoy)",
        "tag": "Recommended",
        "description": "Strips heavy reverb and room echo while keeping vocals and music intact. Great for slowed+reverb edits.",
        "size_mb": 66,
        "type": "MDX",
        "url": f"{_UVR_PUBLIC_BASE}/Reverb_HQ_By_FoxJoy.onnx",
    },
    {
        "filename": "UVR-DeEcho-DeReverb.pth",
        "name": "UVR De-Echo & De-Reverb",
        "tag": "Light De-Reverb",
        "description": "Compact VR model that cleans echo and reverb quickly on both GPU and CPU.",
        "size_mb": 35,
        "type": "VR",
        "url": f"{_UVR_PUBLIC_BASE}/UVR-DeEcho-DeReverb.pth",
    },
    {
        "filename": "UVR-DeNoise.pth",
        "name": "UVR DeNoise (SFX & Static)",
        "tag": "Noise & SFX",
        "description": "Removes ambient noise, static, and game sound effects while preserving the song.",
        "size_mb": 35,
        "type": "VR",
        "url": f"{_UVR_PUBLIC_BASE}/UVR-DeNoise.pth",
    },
    {
        "filename": "UVR-MDX-NET_Crowd_HQ_1.onnx",
        "name": "UVR Crowd & Chatter Remover",
        "tag": "Live / Streams",
        "description": "Isolates music from crowd noise and background chatter in live clips or streams.",
        "size_mb": 66,
        "type": "MDX",
        "url": f"{_UVR_PUBLIC_BASE}/UVR-MDX-NET_Crowd_HQ_1.onnx",
    },
    {
        "filename": "whisper:tiny",
        "name": "Whisper Lyrics Rescue (Tiny)",
        "tag": "Sped-Up / Covers",
        "description": "Transcribes sung lyrics locally and searches lyrics databases. Catches nightcore, slowed+reverb, and covers.",
        "size_mb": 75,
        "type": "WHISPER",
        "url": "",
    },
    {
        "filename": "whisper:base",
        "name": "Whisper Lyrics Rescue (Base)",
        "tag": "Lyrics Accurate",
        "description": "Higher accuracy local lyrics transcription for distorted, pitched, or live singing.",
        "size_mb": 145,
        "type": "WHISPER",
        "url": "",
    },
    {
        "filename": "UVR-MDX-NET-Inst_HQ_3.onnx",
        "name": "UVR Inst HQ 3 (Speech Stripper)",
        "tag": "Heavy Talking",
        "description": "Strips loud spoken voices and Discord commentary to expose background instrumentals.",
        "size_mb": 66,
        "type": "MDX",
        "url": f"{_UVR_PUBLIC_BASE}/UVR-MDX-NET-Inst_HQ_3.onnx",
    },
]

AI_MODEL_EXTENSIONS = (".onnx", ".pth", ".ckpt")

PREFERRED_INFO_SITES = [
    ("auto", "Auto (Default)"),
    ("youtube", "YouTube"),
    ("yt_music", "YouTube Music"),
    ("spotify", "Spotify"),
    ("apple", "Apple Music"),
    ("shazam", "Shazam"),
    ("soundcloud", "SoundCloud"),
    ("deezer", "Deezer"),
]

ICON_COLOR_PRESETS = [
    ("theme", "Match Active Theme", None, None),
    ("default", "Blue / Purple (Default)", "#3B82F6", "#8B5CF6"),
    ("miku", "Miku Cyan / Sky", "#39C5BB", "#38BDF8"),
    ("teto", "Teto Crimson / Rose", "#E11D48", "#FB7185"),
    ("emerald", "Emerald / Teal", "#10B981", "#06B6D4"),
    ("amber", "Amber / Orange", "#F59E0B", "#EA580C"),
    ("sakura", "Sakura Pink / Violet", "#EC4899", "#A855F7"),
    ("mono", "Monochrome Slate", "#52525B", "#18181B"),
]

TEXT_SIZE_PRESETS = [
    ("small", "Small (Default)", 1.0),
    ("medium", "Medium (115%)", 1.15),
    ("large", "Large (130%)", 1.30),
    ("xlarge", "Extra Large (145%)", 1.45),
]


def get_text_scale(text_size_key: str) -> float:
    key = (text_size_key or "small").strip().lower()
    for pid, _lbl, scale in TEXT_SIZE_PRESETS:
        if pid == key:
            return scale
    return 1.0


DEFAULT_SETTINGS = {
    "theme": "auto",  # "auto", "dark", "light", or "custom:<id>"
    "icon_color": "theme",  # "theme" or a preset id from ICON_COLOR_PRESETS
    "text_size": "small",  # "small", "medium", "large", "xlarge"
    "waveform_style": "auto",  # "auto", "radial", "horizontal", "pulse_line", "bar", "dots"
    "waveform_mirror": False,
    "waveform_flip_hz": False,
    "waveform_idle_dots": False,
    "waveform_max_width": 620,
    "waveform_step": 0.0,  # 0.0 = Auto (style default), else custom spacing in px
    "preferred_info_site": "auto",
    "hotkey": "ctrl+alt+s",
    "hotkey_mode": "live",  # "buffer" or "live"
    "enable_audio_buffer": True,
    "buffer_seconds": 12,
    "live_max_seconds": 20,
    "use_ai_separation": False,
    "ai_model": UVR_MODEL_NAME,
    "custom_ai_models": [],
    "minimize_to_tray": True,
    "start_minimized_to_tray": False,
    "save_window_size": True,
    "window_width": 854,
    "window_height": 528,
    "always_on_top": False,
    "show_notifications": True,
    "always_show_status_hud": True,
    "force_overlay_notification": True,
    "play_ping_sound": True,
    "auto_copy": False,
    "monitor_all_devices": True,
    "target_devices": [],
    "remembered_devices": [],
    "gpu_auto_cpu": True,
    "gpu_threshold": 65,
    "ytdlp_app_path": "",
    "ytdlp_quick_download_mp3": True,
    "preview_volume": 50,
    "saved_theme_colors": [],
}


def load_settings():
    data = copy.deepcopy(DEFAULT_SETTINGS)
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    if "live_seconds" in loaded and "live_max_seconds" not in loaded:
                        loaded["live_max_seconds"] = max(12, int(loaded["live_seconds"]))
                    if "monitor_all_devices" not in loaded:
                        loaded["monitor_all_devices"] = (
                            len(loaded.get("target_devices", [])) == 0
                        )
                    data.update(loaded)
        except Exception:
            pass
    if data.get("use_ai_separation") and not is_model_ready(
        data.get("ai_model", UVR_MODEL_NAME)
    ):
        data["use_ai_separation"] = False

    t_val = str(data.get("theme") or "auto").strip()
    if t_val in ("miku", "teto") and os.path.isfile(os.path.join(THEMES_DIR, f"{t_val}.json")):
        data["theme"] = f"custom:{t_val}"

    rem = list(data.get("remembered_devices") or [])
    for d in data.get("target_devices") or []:
        if d and d not in rem:
            rem.append(d)
    data["remembered_devices"] = rem
    return data


def is_ytmdesktop_installed() -> bool:
    """Checks whether YTMDesktop (ytmd:// protocol) is installed and available on Windows."""
    for root, subkey in (
        (winreg.HKEY_CURRENT_USER, r"Software\Classes\ytmd\shell\open\command"),
        (winreg.HKEY_CLASSES_ROOT, r"ytmd\shell\open\command"),
    ):
        try:
            with winreg.OpenKey(root, subkey) as key:
                cmd, _ = winreg.QueryValueEx(key, "")
            cmd = str(cmd or "").strip()
            if not cmd:
                continue
            try:
                parts = shlex.split(cmd, posix=False)
                exe = parts[0].strip('"') if parts else ""
            except Exception:
                exe = cmd.split('"')[1] if '"' in cmd else cmd.split()[0]
            if exe:
                try:
                    st = os.stat(exe)
                    if st.st_size > 0:
                        return True
                except PermissionError:
                    return True
                except OSError:
                    continue
        except OSError:
            continue
    return False


def _extract_ytm_song_video_id(node) -> tuple[str, str]:
    """Recursively extracts (atv_song_video_id, first_video_id) from a YouTube Music InnerTube JSON tree."""
    atv_id = ""
    first_id = ""

    def _walk(obj):
        nonlocal atv_id, first_id
        if atv_id:
            return
        if isinstance(obj, dict):
            vid = obj.get("videoId")
            if isinstance(vid, str) and len(vid) == 11:
                if not first_id:
                    first_id = vid
                vtype = (
                    obj.get("watchEndpointMusicSupportedConfigs", {})
                    .get("watchEndpointMusicConfig", {})
                    .get("musicVideoType", "")
                )
                if vtype == "MUSIC_VIDEO_TYPE_ATV":
                    atv_id = vid
                    return
            for v in obj.values():
                _walk(v)
                if atv_id:
                    return
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)
                if atv_id:
                    return

    _walk(node)
    return atv_id, first_id


def _resolve_ytm_audio_only_video_id(query_text: str) -> str:
    """Resolves the audio-only Song (ATV / Art Track / Provided to YouTube) videoId without falling back to generic video."""
    q = (query_text or "").strip()
    if not q:
        return ""

    first_ytm_id = ""
    # 1. Query YouTube Music's own InnerTube search filtered strictly to Songs (prefers MUSIC_VIDEO_TYPE_ATV)
    try:
        client_ver = f"1.{datetime.datetime.now().strftime('%Y%m%d')}.01.00"
        payload = json.dumps(
            {
                "context": {
                    "client": {
                        "clientName": "WEB_REMIX",
                        "clientVersion": client_ver,
                        "hl": "en",
                        "gl": "US",
                    }
                },
                "query": q,
                "params": "EgWKAQIIAWoMEAMQBBAJEAoQBRAV",
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            "https://music.youtube.com/youtubei/v1/search?prettyPrint=false",
            data=payload,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                "Content-Type": "application/json",
                "Origin": "https://music.youtube.com",
                "Referer": "https://music.youtube.com/",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        atv_id, first_ytm_id = _extract_ytm_song_video_id(data)
        if atv_id:
            return atv_id
    except Exception:
        pass

    # 2. Check auto-generated Topic / Art Track ("Provided to YouTube") on YouTube
    topic_q = f'{q} "Provided to YouTube"'
    search_url = (
        f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(topic_q)}"
    )
    try:
        req = urllib.request.Request(
            search_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            html_body = resp.read().decode("utf-8", errors="ignore")
        m = re.search(r'"videoId":"([A-Za-z0-9_-]{11})"', html_body)
        if m:
            return m.group(1)
    except Exception:
        pass

    return first_ytm_id or ""


def _resolve_ytm_song_video_id(query_text: str) -> str:
    """Resolves the audio-only Song (ATV / Art Track) videoId on YouTube Music, falling back to generic YouTube search."""
    q = (query_text or "").strip()
    if not q:
        return ""

    audio_id = _resolve_ytm_audio_only_video_id(q)
    if audio_id:
        return audio_id

    # Fallback: generic YouTube search
    search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(q)}"
    try:
        req = urllib.request.Request(
            search_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            html_body = resp.read().decode("utf-8", errors="ignore")
        m = re.search(r'"videoId":"([A-Za-z0-9_-]{11})"', html_body)
        if m:
            return m.group(1)
    except Exception:
        pass
    return ""


def _to_ytmdesktop_play_uri(url: str) -> str:
    """Returns a direct ytmd://play/<videoId> URI when URL already contains a YouTube video ID."""
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.netloc or "").lower()
        if "music.youtube.com" not in host:
            return ""
        path = (parsed.path or "").lstrip("/")
        if path == "watch":
            qs = urllib.parse.parse_qs(parsed.query or "")
            vid = (qs.get("v") or [""])[0]
            if vid:
                pl = (qs.get("list") or [""])[0]
                return f"ytmd://play/{vid}/{pl}" if pl else f"ytmd://play/{vid}"
    except Exception:
        pass
    return ""


def open_external_url(url: str, prefer_ytmd: bool = True):
    """Opens YT Music song links in YTMDesktop (audio-only Song track) when installed, else web browser."""
    if not url:
        return
    if prefer_ytmd:
        try:
            parsed = urllib.parse.urlparse(url)
            host = (parsed.netloc or "").lower()
        except Exception:
            host = ""

        if "music.youtube.com" in host and is_ytmdesktop_installed():
            direct_uri = _to_ytmdesktop_play_uri(url)
            if direct_uri:
                try:
                    os.startfile(direct_uri)
                    return
                except OSError:
                    pass

            path = (parsed.path or "").lstrip("/")
            if path == "search":
                qs = urllib.parse.parse_qs(parsed.query or "")
                q_val = (qs.get("q") or [""])[0]
                if q_val:
                    def _open_resolved():
                        vid = _resolve_ytm_song_video_id(q_val)
                        if vid:
                            try:
                                os.startfile(f"ytmd://play/{vid}")
                                return
                            except OSError:
                                pass
                        webbrowser.open(url)

                    threading.Thread(target=_open_resolved, daemon=True).start()
                    return

    webbrowser.open(url)


def save_settings(settings):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except Exception:
        pass


_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def get_saved_theme_colors() -> list[str]:
    """Returns up to 16 recently applied theme hex colors from settings.json."""
    raw = load_settings().get("saved_theme_colors", [])
    out = []
    seen = set()
    for col in raw if isinstance(raw, list) else []:
        s = str(col or "").strip().upper()
        if _HEX_COLOR_RE.match(s) and s not in seen:
            seen.add(s)
            out.append(s)
            if len(out) >= 16:
                break
    return out


def record_saved_theme_colors(colors: list[str]) -> list[str]:
    """Prepends valid hex colors (MRU, max 16) and persists saved_theme_colors to settings.json."""
    existing = get_saved_theme_colors()
    merged = []
    seen = set()
    for col in list(colors or []) + existing:
        s = str(col or "").strip().upper()
        if _HEX_COLOR_RE.match(s) and s not in seen:
            seen.add(s)
            merged.append(s)
            if len(merged) >= 16:
                break

    disk_data = {}
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    disk_data = loaded
        except Exception:
            disk_data = {}
    if not disk_data:
        disk_data = DEFAULT_SETTINGS.copy()
    disk_data["saved_theme_colors"] = merged
    save_settings(disk_data)
    return merged


def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            pass
    return []


def save_history(items):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def load_playlists() -> list[dict]:
    if os.path.exists(PLAYLISTS_FILE):
        try:
            with open(PLAYLISTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            pass
    return []


def save_playlists(playlists: list[dict]):
    try:
        with open(PLAYLISTS_FILE, "w", encoding="utf-8") as f:
            json.dump(playlists, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def is_whisper_model_id(model_id: str) -> bool:
    return bool(model_id and str(model_id).startswith("whisper:"))


def get_whisper_model_dir(model_id: str) -> str:
    size = str(model_id).split(":", 1)[1].strip() if ":" in str(model_id) else "tiny"
    return os.path.join(MODELS_DIR, f"whisper-{size}")


def is_whisper_engine_installed() -> bool:
    setup_runtime_paths()
    try:
        return importlib.util.find_spec("faster_whisper") is not None
    except Exception:
        return False


def is_ai_engine_installed() -> bool:
    """Checks whether audio_separator or faster_whisper is available."""
    setup_runtime_paths()
    try:
        return (
            importlib.util.find_spec("audio_separator") is not None
            or importlib.util.find_spec("faster_whisper") is not None
        )
    except Exception:
        return False


def is_uvr_engine_installed() -> bool:
    setup_runtime_paths()
    try:
        return importlib.util.find_spec("audio_separator") is not None
    except Exception:
        return False


def get_model_file_path(filename: str) -> str:
    if not filename:
        return ""
    if is_whisper_model_id(filename):
        return get_whisper_model_dir(filename)
    if os.path.isabs(filename) and os.path.isfile(filename):
        return filename
    return os.path.join(MODELS_DIR, os.path.basename(filename))


def is_model_downloaded(filename: str) -> bool:
    if not filename:
        return False
    if is_whisper_model_id(filename):
        wdir = get_whisper_model_dir(filename)
        if not os.path.isdir(wdir):
            return False
        for _r, _d, files in os.walk(wdir):
            if "model.bin" in files:
                return True
        return False
    p = get_model_file_path(filename)
    return bool(p and os.path.isfile(p) and os.path.getsize(p) > 1024 * 100)


def is_model_ready(filename: str) -> bool:
    if not is_model_downloaded(filename):
        return False
    if is_whisper_model_id(filename):
        return is_whisper_engine_installed()
    return is_uvr_engine_installed()


def get_downloaded_ai_models() -> list[str]:
    if not os.path.isdir(MODELS_DIR):
        return []
    found = []
    try:
        for entry in os.scandir(MODELS_DIR):
            if entry.is_file() and entry.name.lower().endswith(AI_MODEL_EXTENSIONS):
                if entry.stat().st_size > 1024 * 100:
                    found.append(entry.name)
            elif entry.is_dir() and entry.name.startswith("whisper-"):
                size = entry.name.split("whisper-", 1)[1]
                wid = f"whisper:{size}"
                if is_model_downloaded(wid):
                    found.append(wid)
    except OSError:
        pass
    return sorted(found)


def format_bytes(num_bytes: int) -> str:
    val = float(max(0, num_bytes))
    for unit in ("B", "KB", "MB", "GB"):
        if val < 1024.0 or unit == "GB":
            return f"{val:.1f} {unit}" if unit != "B" else f"{int(val)} B"
        val /= 1024.0
    return f"{val:.1f} GB"


def _dir_size(path: str) -> int:
    total = 0
    if not os.path.isdir(path):
        return 0
    for root, _dirs, files in os.walk(path):
        for fname in files:
            try:
                total += os.path.getsize(os.path.join(root, fname))
            except OSError:
                pass
    return total


def get_storage_breakdown(history: list | None = None) -> dict:
    """Calculates disk usage for each cleanable category in DATA_DIR."""
    used_covers = set()
    for song in (history or []):
        cp = song.get("cover_path") or ""
        if cp:
            used_covers.add(os.path.basename(cp))

    temp_files = []
    temp_bytes = 0
    if os.path.isdir(TEMP_DIR):
        for root, _d, files in os.walk(TEMP_DIR):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    sz = os.path.getsize(fp)
                    temp_bytes += sz
                    temp_files.append(fp)
                except OSError:
                    pass

    log_files = []
    log_bytes = 0
    if os.path.isdir(DATA_DIR):
        for entry in os.scandir(DATA_DIR):
            if entry.is_file() and entry.name.lower().endswith(".log"):
                try:
                    sz = entry.stat().st_size
                    log_bytes += sz
                    log_files.append(entry.path)
                except OSError:
                    pass

    unused_cover_files = []
    unused_cover_bytes = 0
    all_cover_files = []
    all_cover_bytes = 0
    if os.path.isdir(COVERS_DIR):
        for entry in os.scandir(COVERS_DIR):
            if entry.is_file():
                try:
                    sz = entry.stat().st_size
                    all_cover_bytes += sz
                    all_cover_files.append(entry.path)
                    if entry.name not in used_covers:
                        unused_cover_bytes += sz
                        unused_cover_files.append(entry.path)
                except OSError:
                    pass

    models = []
    model_meta_files = []
    model_meta_bytes = 0
    if os.path.isdir(MODELS_DIR):
        for entry in os.scandir(MODELS_DIR):
            if entry.is_file():
                try:
                    sz = entry.stat().st_size
                except OSError:
                    continue
                if entry.name.lower().endswith(AI_MODEL_EXTENSIONS):
                    models.append(
                        {"filename": entry.name, "path": entry.path, "bytes": sz, "is_dir": False}
                    )
                elif entry.name.lower().endswith((".json", ".yaml", ".yml")):
                    model_meta_bytes += sz
                    model_meta_files.append(entry.path)
            elif entry.is_dir() and entry.name.startswith("whisper-"):
                sz = _dir_size(entry.path)
                if sz > 0:
                    size_tag = entry.name.split("whisper-", 1)[1]
                    models.append(
                        {
                            "filename": f"whisper:{size_tag}",
                            "path": entry.path,
                            "bytes": sz,
                            "is_dir": True,
                        }
                    )
    models.sort(key=lambda m: m["bytes"], reverse=True)

    ai_runtime_bytes = _dir_size(AI_RUNTIME_DIR) if os.path.isdir(AI_RUNTIME_DIR) else 0
    history_bytes = os.path.getsize(HISTORY_FILE) if os.path.isfile(HISTORY_FILE) else 0

    used_theme_photos = set()
    used_theme_photo_basenames = set()
    if os.path.isdir(THEMES_DIR):
        for root, _dirs, files in os.walk(THEMES_DIR):
            for fname in files:
                if not fname.lower().endswith(".json") or fname.lower() == "_template.json":
                    continue
                jpath = os.path.join(root, fname)
                try:
                    with open(jpath, "r", encoding="utf-8") as f:
                        tdata = json.load(f)
                    if not isinstance(tdata, dict):
                        continue
                    for sec in ("background", "sidebar", "miniplayer"):
                        sec_cfg = tdata.get(sec)
                        if not isinstance(sec_cfg, dict):
                            continue
                        img_val = str(sec_cfg.get("image") or "").strip()
                        if not img_val:
                            continue
                        used_theme_photo_basenames.add(os.path.basename(img_val).lower())
                        norm_rel = os.path.normcase(os.path.normpath(img_val))
                        used_theme_photos.add(norm_rel)
                        if not os.path.isabs(img_val):
                            used_theme_photos.add(
                                os.path.normcase(os.path.abspath(os.path.join(root, img_val)))
                            )
                            used_theme_photos.add(
                                os.path.normcase(os.path.abspath(os.path.join(THEMES_DIR, img_val)))
                            )
                        else:
                            used_theme_photos.add(os.path.normcase(os.path.abspath(img_val)))
                except Exception:
                    pass

    _img_exts = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
    _legacy_template_imgs = {
        "miku_sidebar.png",
        "miku_miniplayer.png",
        "teto_sidebar.png",
        "teto_miniplayer.png",
    }
    all_theme_photo_files = []
    all_theme_photo_bytes = 0
    unused_theme_photo_files = []
    unused_theme_photo_bytes = 0

    def _scan_theme_photo_entry(fpath: str, fname: str):
        nonlocal all_theme_photo_bytes, unused_theme_photo_bytes
        try:
            sz = os.path.getsize(fpath)
        except OSError:
            return
        all_theme_photo_bytes += sz
        all_theme_photo_files.append(fpath)
        abs_norm = os.path.normcase(os.path.abspath(fpath))
        base_low = fname.lower()
        if abs_norm not in used_theme_photos and base_low not in used_theme_photo_basenames:
            unused_theme_photo_bytes += sz
            unused_theme_photo_files.append(fpath)

    if os.path.isdir(THEME_PHOTOS_DIR):
        for entry in os.scandir(THEME_PHOTOS_DIR):
            if entry.is_file() and entry.name.lower().endswith(_img_exts):
                _scan_theme_photo_entry(entry.path, entry.name)

    if os.path.isdir(THEMES_DIR):
        for entry in os.scandir(THEMES_DIR):
            if (
                entry.is_file()
                and entry.name.lower().endswith(_img_exts)
                and entry.name.lower() not in _legacy_template_imgs
            ):
                _scan_theme_photo_entry(entry.path, entry.name)

    total_bytes = (
        temp_bytes
        + log_bytes
        + all_cover_bytes
        + all_theme_photo_bytes
        + sum(m["bytes"] for m in models)
        + model_meta_bytes
        + ai_runtime_bytes
        + history_bytes
    )

    return {
        "temp_files": temp_files,
        "temp_bytes": temp_bytes,
        "log_files": log_files,
        "log_bytes": log_bytes,
        "unused_cover_files": unused_cover_files,
        "unused_cover_bytes": unused_cover_bytes,
        "all_cover_files": all_cover_files,
        "all_cover_bytes": all_cover_bytes,
        "unused_theme_photo_files": unused_theme_photo_files,
        "unused_theme_photo_bytes": unused_theme_photo_bytes,
        "all_theme_photo_files": all_theme_photo_files,
        "all_theme_photo_bytes": all_theme_photo_bytes,
        "models": models,
        "model_meta_files": model_meta_files,
        "model_meta_bytes": model_meta_bytes,
        "ai_runtime_bytes": ai_runtime_bytes,
        "ai_runtime_dir": AI_RUNTIME_DIR,
        "history_bytes": history_bytes,
        "total_bytes": total_bytes,
    }


def build_site_url(kind: str, query: str, site: str = "auto", default_url: str = "") -> str:
    """Builds an artist or album URL targeting the user's preferred music platform."""
    q = urllib.parse.quote_plus((query or "").strip())
    site = (site or "auto").lower().strip()
    if site == "auto" and default_url:
        return default_url
    if not q:
        return default_url
    if site == "youtube":
        return f"https://www.youtube.com/results?search_query={q}"
    if site == "yt_music":
        return f"https://music.youtube.com/search?q={q}"
    if site == "spotify":
        return f"https://open.spotify.com/search/{q}"
    if site == "apple":
        return f"https://music.apple.com/search?term={q}"
    if site == "shazam":
        return f"https://www.shazam.com/search/{q}"
    if site == "soundcloud":
        return f"https://soundcloud.com/search?q={q}"
    if site == "deezer":
        return f"https://www.deezer.com/search/{q}"
    return default_url or f"https://music.youtube.com/search?q={q}"


_PRESERVE_AMP_ARTISTS = {
    "simon & garfunkel",
    "above & beyond",
    "hall & oates",
    "daryl hall & john oates",
    "earth, wind & fire",
    "florence & the machine",
    "mumford & sons",
    "of monsters & men",
    "tegan & sara",
    "chase & status",
    "brooks & dunn",
    "ike & tina turner",
    "kool & the gang",
    "sly & the family stone",
    "tom petty & the heartbreakers",
    "bob marley & the wailers",
    "huey lewis & the news",
    "nico & vinz",
    "dan + shay",
    "florence + the machine",
    "belle & sebastian",
    "iron & wine",
    "she & him",
    "angus & julia stone",
    "sunshine & whiskey",
    "crosby, stills & nash",
    "crosby, stills, nash & young",
    "peter, paul & mary",
}

_ARTIST_SPLIT_RE = re.compile(
    r"\s*(?:,\s*&\s*|,\s+|\s+&\s+|\s+feat\.?\s+|\s+ft\.?\s+|\s+featuring\s+|\s+with\s+|\s+[x×]\s+|\s*/\s*|\s*;\s*)",
    re.IGNORECASE,
)


def split_artists(artist_str: str, shazam_artists: list | None = None) -> list[dict]:
    """Splits a multi-artist credit string into individual artist entries with individual URLs."""
    raw = (artist_str or "").strip()
    if not raw:
        return []

    adam_by_alias = {}
    ordered_adamids = []
    for a in (shazam_artists or []):
        if isinstance(a, dict) and a.get("adamid"):
            ordered_adamids.append(str(a["adamid"]))
            alias = (a.get("alias") or "").lower().replace("-", " ").strip()
            if alias:
                adam_by_alias[alias] = str(a["adamid"])

    if raw.lower() in _PRESERVE_AMP_ARTISTS:
        names = [raw]
    else:
        cleaned = re.sub(
            r"[\(\[]\s*(?:feat\.?|ft\.?|featuring|with)\s+([^\)\]]+)[\)\]]",
            r", \1",
            raw,
            flags=re.IGNORECASE,
        )
        parts = _ARTIST_SPLIT_RE.split(cleaned)
        names = []
        seen = set()
        for p in parts:
            n = p.strip(" ,&;-()")
            if n and n.lower() not in seen:
                seen.add(n.lower())
                names.append(n)
        if not names:
            names = [raw]

    result = []
    for idx, name in enumerate(names):
        norm = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
        adamid = adam_by_alias.get(norm, "")
        if not adamid and len(names) == 1 and ordered_adamids:
            adamid = ordered_adamids[0]
        elif not adamid and idx < len(ordered_adamids) and len(ordered_adamids) == len(names):
            adamid = ordered_adamids[idx]

        if adamid:
            url = f"https://www.shazam.com/artist/-/{adamid}"
        else:
            url = f"https://music.youtube.com/search?q={urllib.parse.quote_plus(name)}"
        result.append({"name": name, "adamid": adamid, "url": url})
    return result


def register_uri_protocol():
    """Registers per-user songpulse:// URI protocol in HKCU so Windows toast clicks open SongPulse."""
    try:
        if IS_FROZEN:
            cmd = f'"{sys.executable}" "%1"'
        else:
            exe = sys.executable
            if exe.lower().endswith("python.exe"):
                pyw = exe[:-10] + "pythonw.exe"
                if os.path.exists(pyw):
                    exe = pyw
            entry = os.path.join(APP_DIR, "songpulse_app.pyw")
            cmd = f'"{exe}" "{entry}" "%1"'

        base_key = r"Software\Classes\songpulse"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base_key) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "URL:SongPulse Protocol")
            winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, base_key + r"\shell\open\command"
        ) as ck:
            winreg.SetValueEx(ck, "", 0, winreg.REG_SZ, cmd)
        return True
    except Exception:
        return False


def ensure_ping_sound():
    if os.path.exists(PING_SOUND_FILE):
        return
    try:
        sr = 44100
        dur = 0.42
        t = np.linspace(0.0, dur, int(sr * dur), dtype=np.float32)

        env1 = np.exp(-t * 14.0) * np.minimum(1.0, t / 0.008)
        n1 = np.sin(2.0 * np.pi * 880.0 * t) * env1

        t2 = np.maximum(0.0, t - 0.095)
        mask2 = (t >= 0.095).astype(np.float32)
        env2 = np.exp(-t2 * 11.0) * np.minimum(1.0, t2 / 0.008) * mask2
        n2 = np.sin(2.0 * np.pi * 1318.51 * t2) * env2

        wave = (0.28 * n1 + 0.30 * n2).astype(np.float32)
        sf.write(PING_SOUND_FILE, wave, sr, subtype="PCM_16")
    except Exception:
        pass


def play_ping_sound():
    try:
        ensure_ping_sound()
        if os.path.exists(PING_SOUND_FILE):
            winsound.PlaySound(
                PING_SOUND_FILE, winsound.SND_FILENAME | winsound.SND_ASYNC
            )
    except Exception:
        pass


def is_windows_dark_mode():
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return val == 0
    except Exception:
        return True


def is_startup_enabled():
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_READ,
        )
        winreg.QueryValueEx(key, APP_NAME)
        winreg.CloseKey(key)
        return True
    except Exception:
        return False


def set_startup_enabled(enable: bool):
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
        )
        if enable:
            if IS_FROZEN:
                cmd = f'"{sys.executable}"'
            else:
                exe = sys.executable
                if exe.lower().endswith("python.exe"):
                    pyw = exe[:-10] + "pythonw.exe"
                    if os.path.exists(pyw):
                        exe = pyw
                entry = os.path.join(APP_DIR, "songpulse_app.pyw")
                cmd = f'"{exe}" "{entry}"'
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
        return True
    except Exception:
        return False


def migrate_startup_command():
    """Removes legacy --tray flag from an existing Windows startup Run entry."""
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            key_path,
            0,
            winreg.KEY_READ | winreg.KEY_SET_VALUE,
        )
        try:
            val, val_type = winreg.QueryValueEx(key, APP_NAME)
            if isinstance(val, str) and re.search(r"\s+--tray\s*$", val, flags=re.IGNORECASE):
                cleaned = re.sub(r"\s+--tray\s*$", "", val, flags=re.IGNORECASE)
                winreg.SetValueEx(key, APP_NAME, 0, val_type, cleaned)
        except FileNotFoundError:
            pass
        finally:
            winreg.CloseKey(key)
    except Exception:
        pass


def set_windows_titlebar_theme(hwnd, dark: bool):
    try:
        val = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 20, ctypes.byref(val), ctypes.sizeof(val)
        )
    except Exception:
        pass


def set_window_always_on_top(hwnd, enable: bool):
    try:
        user32 = ctypes.windll.user32
        user32.SetWindowPos.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        target = ctypes.c_void_p(-1 if enable else -2)
        user32.SetWindowPos(
            ctypes.c_void_p(int(hwnd)),
            target,
            0,
            0,
            0,
            0,
            0x0001 | 0x0002 | 0x0010,
        )
    except Exception:
        pass


def get_nvidia_gpu_info():
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        res = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            creationflags=flags,
        )
        if res.returncode != 0 or not res.stdout.strip():
            return None
        line = res.stdout.strip().splitlines()[0]
        parts = [p.strip() for p in line.split(",")]
        return {
            "name": parts[0],
            "util": int(float(parts[1])),
            "mem_used": int(float(parts[2])),
            "mem_total": int(float(parts[3])),
        }
    except Exception:
        return None


def is_cuda_provider_available():
    _ort = _try_load_ort()
    if _ort is None:
        return False
    try:
        return "CUDAExecutionProvider" in _ort.get_available_providers()
    except Exception:
        return False


def list_speakers_safe():
    names = []
    try:
        import soundcard as sc

        for sp in sc.all_speakers():
            if sp.name and sp.name not in names:
                names.append(sp.name)
        if not names:
            default_sp = sc.default_speaker()
            if default_sp and default_sp.name:
                names.append(default_sp.name)
    except Exception:
        pass

    if not names:
        def _worker():
            try:
                ctypes.windll.ole32.CoInitializeEx(None, 0)
            except Exception:
                pass
            try:
                import soundcard as sc

                for sp in sc.all_speakers():
                    if sp.name and sp.name not in names:
                        names.append(sp.name)
            except Exception:
                pass

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(timeout=5.0)

    return names


def parse_version_tuple(v_str):
    cleaned = v_str.strip().lstrip("vV")
    nums = []
    for part in cleaned.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        nums.append(int(digits) if digits else 0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums[:3])


def resolve_cover_path(cover_path: str) -> str:
    """Resolves album art relative to the current parent covers folder first (portable)."""
    if not cover_path:
        return ""
    candidate = os.path.join(COVERS_DIR, os.path.basename(cover_path))
    if os.path.exists(candidate):
        return candidate
    if os.path.exists(cover_path):
        return cover_path
    return ""


def find_default_ytdlp_app() -> str:
    """Auto-detects yt-dlp downloader GUI across Windows Registry, AppData, and Program Files."""
    def _is_gui_exe(path: str) -> bool:
        if not path or not os.path.isfile(path):
            return False
        base = os.path.basename(path).lower()
        return (
            base.endswith(".exe")
            and base != "yt-dlp.exe"
            and not base.startswith("unins")
            and "setup" not in base
        )

    reg_paths = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, subkey in reg_paths:
        try:
            with winreg.OpenKey(hive, subkey) as root:
                for i in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        name = winreg.EnumKey(root, i)
                        with winreg.OpenKey(root, name) as app_key:
                            try:
                                disp = str(winreg.QueryValueEx(app_key, "DisplayName")[0]).lower()
                            except OSError:
                                continue
                            if not any(k in disp for k in ("yt-dlp", "ytdlp", "yt_dlp")):
                                continue
                            try:
                                icon = str(winreg.QueryValueEx(app_key, "DisplayIcon")[0])
                                icon = icon.split(",")[0].strip().strip('"')
                                if _is_gui_exe(icon):
                                    return icon
                            except OSError:
                                pass
                            try:
                                loc = str(winreg.QueryValueEx(app_key, "InstallLocation")[0]).strip().strip('"')
                                if os.path.isdir(loc):
                                    for f in os.listdir(loc):
                                        cand = os.path.join(loc, f)
                                        if _is_gui_exe(cand):
                                            return cand
                            except OSError:
                                pass
                    except OSError:
                        continue
        except OSError:
            continue

    local_app = os.environ.get("LOCALAPPDATA", "")
    roaming_app = os.environ.get("APPDATA", "")
    search_roots = [
        os.path.join(local_app, "Programs"),
        local_app,
        roaming_app,
        TOOLS_DIR,
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.path.dirname(APP_DIR),
    ]
    keywords = ("yt-dlp", "ytdlp", "yt_dlp", "youtube-dl")
    for root_dir in search_roots:
        if not root_dir or not os.path.isdir(root_dir):
            continue
        try:
            for entry in os.scandir(root_dir):
                el = entry.name.lower()
                if entry.is_file() and any(k in el for k in keywords) and _is_gui_exe(entry.path):
                    return entry.path
                if entry.is_dir() and any(k in el for k in keywords):
                    try:
                        for sub in os.scandir(entry.path):
                            if sub.is_file() and _is_gui_exe(sub.path):
                                return sub.path
                    except OSError:
                        pass
        except OSError:
            continue

    return ""
