import ctypes
import json
import os
import subprocess
import sys
import threading
import winreg
import winsound

import numpy as np
import soundfile as sf

# Register CUDA runtime directory & preload ONNX DLLs before audio_separator imports onnxruntime
for _cuda_ver in ("v13.5", "v13.4", "v13.3", "v13.2", "v13.1", "v13.0", "v12.8", "v12.6"):
    _cuda_x64 = rf"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\{_cuda_ver}\bin\x64"
    _cuda_bin = rf"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\{_cuda_ver}\bin"
    for _p in (_cuda_x64, _cuda_bin):
        if os.path.isdir(_p):
            try:
                os.add_dll_directory(_p)
            except Exception:
                pass
            if _p not in os.environ.get("PATH", ""):
                os.environ["PATH"] = _p + os.pathsep + os.environ.get("PATH", "")

try:
    import onnxruntime as ort
    if hasattr(ort, "preload_dlls"):
        ort.preload_dlls()
except Exception:
    ort = None

# --- APP METADATA & PATHS ---
APP_NAME = "SongPulse"
APP_VERSION = "1.0.0"
APP_USER_MODEL_ID = f"Greatly07.SongPulse.{APP_VERSION}"
GITHUB_REPO = "Greatly07/songpulse"

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
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
ICON_CACHE_FILE = os.path.join(DATA_DIR, "songpulse.ico")
PING_SOUND_FILE = os.path.join(DATA_DIR, "ping.wav")
SINGLE_INSTANCE_KEY = "SongPulse_SingleInstance_LocalServer_v1"

for _d in (DATA_DIR, COVERS_DIR, TEMP_DIR, MODELS_DIR, ICONS_DIR):
    os.makedirs(_d, exist_ok=True)

SAMPLE_RATE = 44100
CHUNK_FRAMES = 4410  # 0.1s audio chunks
UVR_MODEL_NAME = "UVR-MDX-NET-Inst_HQ_3.onnx"

DEFAULT_SETTINGS = {
    "theme": "auto",  # "auto", "dark", "light"
    "hotkey": "ctrl+alt+s",
    "hotkey_mode": "live",  # "buffer" or "live"
    "buffer_seconds": 12,
    "live_max_seconds": 20,
    "use_ai_separation": False,
    "minimize_to_tray": True,
    "save_window_size": True,
    "window_width": 900,
    "window_height": 650,
    "always_on_top": False,
    "show_notifications": True,
    "force_overlay_notification": True,
    "play_ping_sound": True,
    "auto_copy": False,
    "monitor_all_devices": True,  # Distinct from empty target_devices (= 0 selected)
    "target_devices": [],
    "gpu_auto_cpu": True,
    "gpu_threshold": 65,
    "ytdlp_app_path": "",
}


def load_settings():
    data = DEFAULT_SETTINGS.copy()
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    if "live_seconds" in loaded and "live_max_seconds" not in loaded:
                        loaded["live_max_seconds"] = max(12, int(loaded["live_seconds"]))
                    if "monitor_all_devices" not in loaded:
                        loaded["monitor_all_devices"] = len(loaded.get("target_devices", [])) == 0
                    data.update(loaded)
        except Exception:
            pass
    return data


def save_settings(settings):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except Exception:
        pass


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
                cmd = f'"{sys.executable}" --tray'
            else:
                exe = sys.executable
                if exe.lower().endswith("python.exe"):
                    pyw = exe[:-10] + "pythonw.exe"
                    if os.path.exists(pyw):
                        exe = pyw
                entry = os.path.join(APP_DIR, "song_id_app.pyw")
                cmd = f'"{exe}" "{entry}" --tray'
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
    if ort is None:
        return False
    try:
        return "CUDAExecutionProvider" in ort.get_available_providers()
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

    # 1. Check Windows Uninstall Registry (catches Inno Setup per-user AppData installs instantly)
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

    # 2. Scan AppData, Programs, Program Files, and sibling folders
    local_app = os.environ.get("LOCALAPPDATA", "")
    roaming_app = os.environ.get("APPDATA", "")
    search_roots = [
        os.path.join(local_app, "Programs"),
        local_app,
        roaming_app,
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