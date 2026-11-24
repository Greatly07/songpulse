import asyncio
from datetime import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import numpy as np
import soundfile as sf
from shazamio import Shazam
from PyQt6.QtCore import QThread, pyqtSignal

from .config import (
    AI_MODEL_CATALOG,
    AI_RUNTIME_DIR,
    APP_NAME,
    APP_VERSION,
    COVERS_DIR,
    GITHUB_REPO,
    MODELS_DIR,
    SAMPLE_RATE,
    SAVED_CLIP_WAV,
    TEMP_DIR,
    TOOLS_DIR,
    UVR_MODEL_NAME,
    YTDLP_GITHUB_REPO,
    _resolve_ytm_audio_only_video_id,
    find_default_ytdlp_app,
    get_model_file_path,
    get_nvidia_gpu_info,
    get_whisper_model_dir,
    is_ai_engine_installed,
    is_cuda_provider_available,
    is_model_downloaded,
    is_uvr_engine_installed,
    is_whisper_engine_installed,
    is_whisper_model_id,
    parse_version_tuple,
    setup_runtime_paths,
    split_artists,
)


def _create_shazam(attempts: int = 1, max_timeout: float = 1.5) -> Shazam:
    """Builds a Shazam client with bounded fast retries instead of shazamio's 20-retry stall."""
    try:
        from aiohttp_retry import ExponentialRetry
        from shazamio.client import HTTPClient

        client = HTTPClient(
            retry_options=ExponentialRetry(
                attempts=max(1, attempts),
                start_timeout=0.35,
                max_timeout=max_timeout,
                statuses={500, 502, 503, 504, 429},
            )
        )
        return Shazam(http_client=client)
    except Exception:
        return Shazam()


def _download_url_with_progress(
    url: str,
    dest_path: str,
    progress_cb=None,
    cancel_evt: threading.Event | None = None,
    timeout: int = 20,
):
    """Downloads a file to dest_path atomically via a .part file with live progress & instant cancel."""
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    part_path = dest_path + ".part"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            downloaded = 0
            chunk_size = 65536
            with open(part_path, "wb") as f:
                while True:
                    if cancel_evt and cancel_evt.is_set():
                        raise InterruptedError("Download cancelled.")
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb:
                        pct = int((downloaded * 100) / total) if total > 0 else -1
                        mb_done = downloaded / (1024 * 1024)
                        if total > 0:
                            mb_tot = total / (1024 * 1024)
                            progress_cb(
                                pct,
                                f"Downloading... {mb_done:.1f} / {mb_tot:.1f} MB ({pct}%)",
                            )
                        else:
                            progress_cb(-1, f"Downloading... {mb_done:.1f} MB")
        if os.path.exists(dest_path):
            os.remove(dest_path)
        os.replace(part_path, dest_path)
    except Exception:
        if os.path.exists(part_path):
            try:
                os.remove(part_path)
            except OSError:
                pass
        raise


def download_cover(url):
    if not url:
        return ""
    filename = hashlib.md5(url.encode("utf-8")).hexdigest() + ".jpg"
    local_path = os.path.join(COVERS_DIR, filename)
    if os.path.exists(local_path):
        return filename
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            with open(local_path, "wb") as f:
                f.write(resp.read())
        return filename
    except Exception:
        return ""


def build_song_platform_links(song: dict):
    if song.get("platform_links"):
        return [
            (it["label"], it.get("icon", "web"), it["url"])
            for it in song["platform_links"]
            if it.get("url")
        ]

    title = song.get("title", "")
    artist = song.get("artist", "")
    query = urllib.parse.quote_plus(f"{artist} {title}".strip())
    links = []
    if song.get("shazam_url"):
        links.append(("Shazam", "shazam", song["shazam_url"]))
    links.append(("YouTube", "youtube", song.get("youtube_url") or f"https://www.youtube.com/results?search_query={query}"))
    links.append(("YT Music", "yt_music", song.get("yt_music_url") or f"https://music.youtube.com/search?q={query}"))
    links.append(("Spotify", "spotify", song.get("spotify_url") or f"https://open.spotify.com/search/{query}"))
    links.append(("Apple Music", "apple", song.get("apple_url") or f"https://music.apple.com/search?term={query}"))
    links.append(("SoundCloud", "soundcloud", song.get("soundcloud_url") or f"https://soundcloud.com/search?q={query}"))
    links.append(("Deezer", "deezer", song.get("deezer_url") or f"https://www.deezer.com/search/{query}"))
    links.append(("Amazon", "amazon", song.get("amazon_url") or f"https://music.amazon.com/search/{query}"))
    links.append(("Tidal", "tidal", song.get("tidal_url") or f"https://listen.tidal.com/search?q={query}"))
    return links


def _build_song_dict_from_metadata(
    title: str,
    artist: str,
    album: str = "",
    released: str = "",
    genre: str = "",
    label: str = "",
    cover_url: str = "",
    preview_url: str = "",
    shazam_url: str = "",
    apple_url: str = "",
    artist_url: str = "",
    album_url: str = "",
    duration_ms: int = 0,
    lyrics: str = "",
    method_label: str = "",
):
    query = urllib.parse.quote_plus(f"{artist} {title}".strip())
    urls = {
        "shazam": shazam_url or f"https://www.shazam.com/search/{query}",
        "youtube": f"https://www.youtube.com/results?search_query={query}",
        "yt_music": f"https://music.youtube.com/search?q={query}",
        "spotify": f"https://open.spotify.com/search/{query}",
        "apple": apple_url or f"https://music.apple.com/search?term={query}",
        "soundcloud": f"https://soundcloud.com/search?q={query}",
        "deezer": f"https://www.deezer.com/search/{query}",
        "amazon": f"https://music.amazon.com/search/{query}",
        "tidal": f"https://listen.tidal.com/search?q={query}",
    }
    platform_links = [
        {"label": lbl, "icon": key, "url": urls[key]}
        for lbl, key in [
            ("Shazam", "shazam"),
            ("YouTube", "youtube"),
            ("YT Music", "yt_music"),
            ("Spotify", "spotify"),
            ("Apple Music", "apple"),
            ("SoundCloud", "soundcloud"),
            ("Deezer", "deezer"),
            ("Amazon", "amazon"),
            ("Tidal", "tidal"),
        ]
        if urls.get(key)
    ]
    parsed_artists = split_artists(artist)
    return {
        "id": hashlib.md5(f"{artist}-{title}-{time.time()}".encode()).hexdigest()[:12],
        "title": title,
        "artist": artist,
        "artists": parsed_artists,
        "shazam_artists": [],
        "album": album,
        "released": released,
        "genre": genre,
        "label": label,
        "cover_url": cover_url,
        "cover_path": download_cover(cover_url),
        "shazam_url": urls["shazam"],
        "youtube_url": urls["youtube"],
        "yt_music_url": urls["yt_music"],
        "spotify_url": urls["spotify"],
        "apple_url": urls["apple"],
        "platform_links": platform_links,
        "method": method_label,
        "timestamp": datetime.now().strftime("%b %d, %Y • %I:%M:%S %p"),
        "preview_url": preview_url,
        "artist_url": artist_url or (parsed_artists[0]["url"] if parsed_artists else ""),
        "album_url": album_url
        or (
            f"https://music.youtube.com/search?q={urllib.parse.quote_plus(f'{artist} {album}'.strip())}"
            if album
            else ""
        ),
        "youtube_song_url": "",
        "youtube_video_url": "",
        "isrc": "",
        "duration_ms": duration_ms,
        "lyrics": lyrics,
        "liked": False,
    }


def parse_shazam_response(out, method_label=""):
    if not isinstance(out, dict):
        return None
    track = out.get("track")
    if not track:
        return None

    title = track.get("title", "Unknown Title")
    artist = track.get("subtitle", "Unknown Artist")
    genre = track.get("genres", {}).get("primary", "")
    images = track.get("images", {})
    cover_url = images.get("coverart") or images.get("background") or ""
    shazam_url = track.get("url", "")

    album, label, released = "", "", ""
    for section in track.get("sections", []):
        if section.get("type") == "SONG":
            for item in section.get("metadata", []):
                k = item.get("title", "").lower()
                v = item.get("text", "")
                if "album" in k:
                    album = v
                elif "label" in k:
                    label = v
                elif "released" in k:
                    released = v

    query = urllib.parse.quote_plus(f"{artist} {title}")
    urls = {
        "shazam": shazam_url,
        "youtube": f"https://www.youtube.com/results?search_query={query}",
        "yt_music": f"https://music.youtube.com/search?q={query}",
        "spotify": f"https://open.spotify.com/search/{query}",
        "apple": f"https://music.apple.com/search?term={query}",
        "soundcloud": f"https://soundcloud.com/search?q={query}",
        "deezer": f"https://www.deezer.com/search/{query}",
        "amazon": f"https://music.amazon.com/search/{query}",
        "tidal": f"https://listen.tidal.com/search?q={query}",
    }
    extra_providers = []

    hub = track.get("hub", {})
    for opt in hub.get("options", []):
        for act in opt.get("actions", []):
            uri = act.get("uri", "")
            if uri.startswith("https://music.apple.com"):
                urls["apple"] = uri
                break

    for prov in hub.get("providers", []):
        ptype = (prov.get("type") or "").lower()
        caption = (prov.get("caption") or prov.get("type") or "").strip()
        web_uri = ""
        for act in prov.get("actions", []):
            uri = act.get("uri", "")
            if uri.startswith("https://"):
                web_uri = uri
                break
        if not web_uri:
            continue

        if "spotify" in ptype:
            urls["spotify"] = web_uri
        elif "youtube" in ptype:
            urls["youtube"] = web_uri
        elif "deezer" in ptype:
            urls["deezer"] = web_uri
        elif "amazon" in ptype:
            urls["amazon"] = web_uri
        elif "soundcloud" in ptype:
            urls["soundcloud"] = web_uri
        elif "tidal" in ptype:
            urls["tidal"] = web_uri
        elif "apple" in ptype:
            urls["apple"] = web_uri
        elif caption:
            extra_providers.append({"label": caption.title(), "icon": "web", "url": web_uri})

    platform_links = []
    for lbl, key in [
        ("Shazam", "shazam"),
        ("YouTube", "youtube"),
        ("YT Music", "yt_music"),
        ("Spotify", "spotify"),
        ("Apple Music", "apple"),
        ("SoundCloud", "soundcloud"),
        ("Deezer", "deezer"),
        ("Amazon", "amazon"),
        ("Tidal", "tidal"),
    ]:
        if urls.get(key):
            platform_links.append({"label": lbl, "icon": key, "url": urls[key]})
    platform_links.extend(extra_providers)

    cover_path = download_cover(cover_url)

    preview_url = ""
    for act in hub.get("actions", []):
        uri = act.get("uri", "")
        if act.get("type") == "uri" and uri.startswith("http"):
            preview_url = uri
            break

    lyrics_lines = []
    for section in track.get("sections", []):
        if section.get("type") == "LYRICS":
            lyrics_lines = section.get("text", []) or []
            break

    shazam_artists = track.get("artists") or []
    parsed_artists = split_artists(artist, shazam_artists)
    artist_url = (
        parsed_artists[0]["url"]
        if parsed_artists
        else f"https://music.youtube.com/search?q={urllib.parse.quote_plus(artist)}"
    )

    album_url = (
        f"https://music.youtube.com/search?q={urllib.parse.quote_plus(f'{artist} {album}'.strip())}"
        if album
        else ""
    )

    return {
        "id": hashlib.md5(f"{artist}-{title}-{time.time()}".encode()).hexdigest()[:12],
        "title": title,
        "artist": artist,
        "artists": parsed_artists,
        "shazam_artists": shazam_artists,
        "album": album,
        "released": released,
        "genre": genre,
        "label": label,
        "cover_url": cover_url,
        "cover_path": cover_path,
        "shazam_url": urls["shazam"],
        "youtube_url": urls["youtube"],
        "yt_music_url": urls["yt_music"],
        "spotify_url": urls["spotify"],
        "apple_url": urls["apple"],
        "platform_links": platform_links,
        "method": method_label,
        "timestamp": datetime.now().strftime("%b %d, %Y • %I:%M:%S %p"),
        "preview_url": preview_url,
        "artist_url": artist_url,
        "album_url": album_url,
        "youtube_song_url": "",
        "youtube_video_url": "",
        "isrc": track.get("isrc", ""),
        "duration_ms": 0,
        "lyrics": "\n".join(lyrics_lines[:40]) if lyrics_lines else "",
        "liked": False,
    }


def _import_separator():
    setup_runtime_paths()
    from audio_separator.separator import Separator

    return Separator


class ModelManager:
    """Lazy, optional, multi-model AI manager supporting UVR models and local Whisper lyrics rescue."""

    def __init__(self, initial_model: str = UVR_MODEL_NAME):
        self.sep_gpu = None
        self.sep_cpu = None
        self.whisper_model = None
        self.loaded_whisper_key = ""
        self.loaded_gpu_model = ""
        self.loaded_cpu_model = ""
        self.active_model = initial_model or UVR_MODEL_NAME
        self.gpu_available = is_cuda_provider_available()
        self.lock = threading.Lock()
        self.last_gpu_time = None
        self.last_cpu_time = None

    def refresh_capabilities(self):
        setup_runtime_paths()
        self.gpu_available = is_cuda_provider_available()

    def is_engine_available(self) -> bool:
        return is_ai_engine_installed()

    def set_active_model(self, model_filename: str):
        model_filename = (model_filename or UVR_MODEL_NAME).strip()
        with self.lock:
            if model_filename != self.active_model:
                self.active_model = model_filename
                self.sep_gpu = None
                self.sep_cpu = None
                self.loaded_gpu_model = ""
                self.loaded_cpu_model = ""

    def unload(self):
        with self.lock:
            self.sep_gpu = None
            self.sep_cpu = None
            self.whisper_model = None
            self.loaded_whisper_key = ""
            self.loaded_gpu_model = ""
            self.loaded_cpu_model = ""

    def _ensure_ready_locally(self, model_filename: str):
        if is_whisper_model_id(model_filename):
            if not is_whisper_engine_installed():
                raise RuntimeError(
                    "Whisper engine (faster-whisper) is not installed. Install it in Settings -> AI Models."
                )
            if not is_model_downloaded(model_filename):
                raise RuntimeError(
                    f"Model '{model_filename}' is not downloaded. Download it in Settings -> AI Models."
                )
            return

        if not is_uvr_engine_installed():
            raise RuntimeError(
                "Optional AI Engine is not installed. Go to Settings -> AI Models to install it."
            )
        if not is_model_downloaded(model_filename):
            raise RuntimeError(
                f"AI model '{model_filename}' is not downloaded. Download it in Settings -> AI Models."
            )
        if not shutil.which("ffmpeg"):
            raise RuntimeError(
                "FFmpeg was not found on PATH (required by the AI audio separator). "
                "Re-install the AI Engine pack in Settings -> AI Models or install FFmpeg."
            )

    def load_whisper(self, model_id: str, force_cpu: bool = False):
        self._ensure_ready_locally(model_id)
        setup_runtime_paths()
        from faster_whisper import WhisperModel

        wdir = get_whisper_model_dir(model_id)
        use_cuda = self.gpu_available and not force_cpu
        key = f"{model_id}|{'cuda' if use_cuda else 'cpu'}"
        with self.lock:
            if self.whisper_model is None or self.loaded_whisper_key != key:
                try:
                    self.whisper_model = WhisperModel(
                        wdir,
                        device="cuda" if use_cuda else "cpu",
                        compute_type="float16" if use_cuda else "int8",
                    )
                except Exception:
                    self.whisper_model = WhisperModel(wdir, device="cpu", compute_type="int8")
                    key = f"{model_id}|cpu"
                self.loaded_whisper_key = key
            return self.whisper_model

    def load_default_gpu(self, model_filename: str | None = None):
        target = model_filename or self.active_model
        self._ensure_ready_locally(target)
        if is_whisper_model_id(target):
            return self.load_whisper(target, force_cpu=False)
        Separator = _import_separator()
        with self.lock:
            self.gpu_available = is_cuda_provider_available()
            if self.sep_gpu is None or self.loaded_gpu_model != target:
                sep = Separator(
                    log_level=40,
                    output_dir=TEMP_DIR,
                    model_file_dir=MODELS_DIR,
                )
                if self.gpu_available:
                    sep.onnx_execution_provider = [
                        "CUDAExecutionProvider",
                        "CPUExecutionProvider",
                    ]
                else:
                    sep.onnx_execution_provider = ["CPUExecutionProvider"]
                sep.load_model(model_filename=os.path.basename(target))
                self.sep_gpu = sep
                self.loaded_gpu_model = target
            return self.sep_gpu

    def get_cpu_separator(self, model_filename: str | None = None):
        target = model_filename or self.active_model
        self._ensure_ready_locally(target)
        if is_whisper_model_id(target):
            return self.load_whisper(target, force_cpu=True)
        Separator = _import_separator()
        with self.lock:
            if self.sep_cpu is None or self.loaded_cpu_model != target:
                sep = Separator(
                    log_level=40,
                    output_dir=TEMP_DIR,
                    model_file_dir=MODELS_DIR,
                )
                sep.onnx_execution_provider = ["CPUExecutionProvider"]
                sep.load_model(model_filename=os.path.basename(target))
                self.sep_cpu = sep
                self.loaded_cpu_model = target
            return self.sep_cpu


class ModelLoaderThread(QThread):
    loaded = pyqtSignal(bool, str)
    failed = pyqtSignal(str)

    def __init__(self, manager: ModelManager, model_filename: str):
        super().__init__()
        self.manager = manager
        self.model_filename = model_filename

    def run(self):
        try:
            self.manager.load_default_gpu(self.model_filename)
            self.loaded.emit(self.manager.gpu_available, self.model_filename)
        except Exception as e:
            self.failed.emit(str(e))


class ModelDownloadWorker(QThread):
    """Downloads a UVR or Whisper AI model into MODELS_DIR with progress & cancellation."""

    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(str)
    cancelled = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, model_filename: str, url: str = ""):
        super().__init__()
        self.model_filename = model_filename.strip()
        self.url = url
        self.cancel_evt = threading.Event()

    def request_cancel(self):
        self.cancel_evt.set()

    def run(self):
        try:
            if is_whisper_model_id(self.model_filename):
                if not is_whisper_engine_installed():
                    raise RuntimeError(
                        "faster-whisper is not installed in this Python environment. "
                        "Install optional AI dependencies to use Whisper Lyrics Rescue."
                    )
                size = self.model_filename.split(":", 1)[1].strip()
                out_dir = get_whisper_model_dir(self.model_filename)
                os.makedirs(out_dir, exist_ok=True)
                self.progress.emit(
                    -1, f"Downloading Whisper '{size}' model to models folder..."
                )
                from faster_whisper import download_model

                download_model(size, output_dir=out_dir)
                if self.cancel_evt.is_set():
                    shutil.rmtree(out_dir, ignore_errors=True)
                    self.cancelled.emit()
                    return
                self.finished_ok.emit(self.model_filename)
                return

            base_fn = os.path.basename(self.model_filename)
            url = self.url
            if not url:
                for entry in AI_MODEL_CATALOG:
                    if entry["filename"] == base_fn:
                        url = entry["url"]
                        break

            dest_path = get_model_file_path(base_fn)
            if url:
                self.progress.emit(0, f"Connecting to download {base_fn}...")
                _download_url_with_progress(
                    url,
                    dest_path,
                    progress_cb=lambda pct, msg: self.progress.emit(pct, msg),
                    cancel_evt=self.cancel_evt,
                    timeout=30,
                )
            elif is_uvr_engine_installed():
                self.progress.emit(-1, f"Fetching {base_fn} via audio-separator...")
                Separator = _import_separator()
                sep = Separator(log_level=40, output_dir=TEMP_DIR, model_file_dir=MODELS_DIR)
                sep.download_model_files(base_fn)
            else:
                raise RuntimeError("No download URL available for this model.")

            if self.cancel_evt.is_set():
                self.cancelled.emit()
                return
            self.finished_ok.emit(base_fn)
        except InterruptedError:
            self.cancelled.emit()
        except Exception as e:
            self.error.emit(str(e))


class AiRuntimeDownloadWorker(QThread):
    """Downloads & extracts the optional SongPulse AI Engine pack from GitHub Releases."""

    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal()
    cancelled = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.cancel_evt = threading.Event()

    def request_cancel(self):
        self.cancel_evt.set()

    def run(self):
        zip_path = os.path.join(TEMP_DIR, "SongPulse_AI_Pack.zip")
        try:
            self.progress.emit(-1, f"Checking GitHub Releases ({GITHUB_REPO}) for AI Engine pack...")
            api_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
            req = urllib.request.Request(
                api_url,
                headers={
                    "User-Agent": f"{APP_NAME}/{APP_VERSION}",
                    "Accept": "application/vnd.github+json",
                },
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            pack_url = ""
            for asset in data.get("assets", []):
                name = (asset.get("name") or "").lower()
                if "ai_pack" in name and name.endswith(".zip"):
                    pack_url = asset.get("browser_download_url", "")
                    break

            if not pack_url:
                raise RuntimeError(
                    "No SongPulse_AI_Pack .zip asset was found on the latest GitHub Release.\n"
                    "If running from source, install optional AI with:\n"
                    "pip install -r requirements-ai.txt"
                )

            _download_url_with_progress(
                pack_url,
                zip_path,
                progress_cb=lambda pct, msg: self.progress.emit(pct, msg),
                cancel_evt=self.cancel_evt,
                timeout=30,
            )

            if self.cancel_evt.is_set():
                self.cancelled.emit()
                return

            self.progress.emit(-1, "Extracting AI Engine files...")
            os.makedirs(AI_RUNTIME_DIR, exist_ok=True)
            with zipfile.ZipFile(zip_path, "r") as zf:
                members = zf.infolist()
                total = max(1, len(members))
                for idx, m in enumerate(members, 1):
                    if self.cancel_evt.is_set():
                        self.cancelled.emit()
                        return
                    zf.extract(m, AI_RUNTIME_DIR)
                    if idx % 50 == 0 or idx == total:
                        pct = int((idx * 100) / total)
                        self.progress.emit(pct, f"Extracting AI Engine... ({pct}%)")

            setup_runtime_paths()
            self.finished_ok.emit()
        except InterruptedError:
            self.cancelled.emit()
        except Exception as e:
            self.error.emit(str(e))
        finally:
            if os.path.exists(zip_path):
                try:
                    os.remove(zip_path)
                except OSError:
                    pass


class AppUpdateWorker(QThread):
    """Downloads the latest SongPulse release and runs a direct silent in-place update."""

    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(str)  # installer path launched
    cancelled = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, download_url: str, version_str: str = ""):
        super().__init__()
        self.download_url = download_url
        self.version_str = version_str
        self.cancel_evt = threading.Event()

    def request_cancel(self):
        self.cancel_evt.set()

    def run(self):
        fname = os.path.basename(urllib.parse.urlparse(self.download_url).path) or "SongPulse_Update.exe"
        dest = os.path.join(TEMP_DIR, fname)
        try:
            self.progress.emit(0, f"Downloading SongPulse v{self.version_str or 'latest'}...")
            _download_url_with_progress(
                self.download_url,
                dest,
                progress_cb=lambda pct, msg: self.progress.emit(pct, msg),
                cancel_evt=self.cancel_evt,
                timeout=30,
            )
            if self.cancel_evt.is_set():
                self.cancelled.emit()
                return

            if dest.lower().endswith(".exe"):
                self.progress.emit(100, "Installing update silently and relaunching...")
                subprocess.Popen(
                    [
                        dest,
                        "/VERYSILENT",
                        "/SUPPRESSMSGBOXES",
                        "/NORESTART",
                        "/CLOSEAPPLICATIONS",
                    ]
                )
                self.finished_ok.emit(dest)
            else:
                raise RuntimeError("Release asset is not a direct Windows updater (.exe).")
        except InterruptedError:
            self.cancelled.emit()
        except Exception as e:
            self.error.emit(str(e))


class YtDlpInstallerWorker(QThread):
    """Downloads & installs the latest Greatly07/yt-dlp-downloader release from GitHub."""

    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(str)
    cancelled = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.cancel_evt = threading.Event()

    def request_cancel(self):
        self.cancel_evt.set()

    def run(self):
        dl_path = ""
        try:
            self.progress.emit(-1, f"Checking latest release of {YTDLP_GITHUB_REPO}...")
            api_url = f"https://api.github.com/repos/{YTDLP_GITHUB_REPO}/releases/latest"
            req = urllib.request.Request(
                api_url,
                headers={
                    "User-Agent": f"{APP_NAME}/{APP_VERSION}",
                    "Accept": "application/vnd.github+json",
                },
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            assets = data.get("assets") or []
            chosen = None
            for pred in (
                lambda n: n.endswith(".exe") and ("setup" in n or "install" in n),
                lambda n: n.endswith(".exe"),
                lambda n: n.endswith(".zip"),
            ):
                for a in assets:
                    name = (a.get("name") or "").lower()
                    if pred(name):
                        chosen = a
                        break
                if chosen:
                    break

            if not chosen or not chosen.get("browser_download_url"):
                raise RuntimeError(
                    f"No Windows installer or executable found on {YTDLP_GITHUB_REPO} releases."
                )

            fname = chosen["name"]
            url = chosen["browser_download_url"]
            dl_path = os.path.join(TEMP_DIR, fname)

            _download_url_with_progress(
                url,
                dl_path,
                progress_cb=lambda pct, msg: self.progress.emit(pct, msg),
                cancel_evt=self.cancel_evt,
                timeout=30,
            )

            if self.cancel_evt.is_set():
                self.cancelled.emit()
                return

            low_name = fname.lower()
            installed_exe = ""

            if low_name.endswith(".exe") and ("setup" in low_name or "install" in low_name):
                self.progress.emit(-1, "Running yt-dlp Downloader installer...")
                proc = subprocess.run(
                    [dl_path, "/VERYSILENT", "/NORESTART", "/CURRENTUSER"],
                    timeout=180,
                )
                if proc.returncode != 0:
                    subprocess.run([dl_path], timeout=300)
                installed_exe = find_default_ytdlp_app()
            elif low_name.endswith(".exe"):
                target_dir = os.path.join(TOOLS_DIR, "yt-dlp-downloader")
                os.makedirs(target_dir, exist_ok=True)
                installed_exe = os.path.join(target_dir, fname)
                shutil.copy2(dl_path, installed_exe)
            elif low_name.endswith(".zip"):
                self.progress.emit(-1, "Extracting yt-dlp Downloader...")
                target_dir = os.path.join(TOOLS_DIR, "yt-dlp-downloader")
                os.makedirs(target_dir, exist_ok=True)
                with zipfile.ZipFile(dl_path, "r") as zf:
                    zf.extractall(target_dir)
                for root, _dirs, files in os.walk(target_dir):
                    for f in files:
                        fl = f.lower()
                        if fl.endswith(".exe") and fl != "yt-dlp.exe" and not fl.startswith("unins"):
                            installed_exe = os.path.join(root, f)
                            break
                    if installed_exe:
                        break

            if not installed_exe:
                installed_exe = find_default_ytdlp_app()

            if not installed_exe:
                raise RuntimeError(
                    "Installation finished, but the executable could not be auto-detected. "
                    "Please click Browse to select it manually."
                )

            self.finished_ok.emit(installed_exe)
        except InterruptedError:
            self.cancelled.emit()
        except Exception as e:
            self.error.emit(str(e))
        finally:
            if dl_path and os.path.exists(dl_path):
                try:
                    os.remove(dl_path)
                except OSError:
                    pass


class BenchmarkWorker(QThread):
    progress = pyqtSignal(str)
    finished_bench = pyqtSignal(object, object)
    error = pyqtSignal(str)

    def __init__(self, manager: ModelManager, model_filename: str | None = None):
        super().__init__()
        self.manager = manager
        self.model_filename = model_filename or manager.active_model

    def run(self):
        bench_wav = os.path.join(TEMP_DIR, "bench_sample.wav")
        created = []
        try:
            t = np.linspace(0, 8, SAMPLE_RATE * 8, dtype=np.float32)
            tone = 0.25 * np.sin(2 * np.pi * 440.0 * t)
            stereo = np.stack([tone, tone], axis=1)
            sf.write(bench_wav, stereo, SAMPLE_RATE)

            gpu_sec = None
            if is_whisper_model_id(self.model_filename):
                if self.manager.gpu_available:
                    self.progress.emit("Testing Whisper on GPU...")
                    wm = self.manager.load_whisper(self.model_filename, force_cpu=False)
                    t0 = time.perf_counter()
                    list(wm.transcribe(bench_wav, beam_size=1)[0])
                    gpu_sec = time.perf_counter() - t0
                    self.manager.last_gpu_time = gpu_sec
                self.progress.emit("Testing Whisper on CPU...")
                wm_c = self.manager.load_whisper(self.model_filename, force_cpu=True)
                t0 = time.perf_counter()
                list(wm_c.transcribe(bench_wav, beam_size=1)[0])
                cpu_sec = time.perf_counter() - t0
                self.manager.last_cpu_time = cpu_sec
                self.finished_bench.emit(gpu_sec, cpu_sec)
                return

            if self.manager.gpu_available:
                self.progress.emit("Testing GPU speed...")
                sep_g = self.manager.load_default_gpu(self.model_filename)
                t0 = time.perf_counter()
                out_g = sep_g.separate(bench_wav)
                gpu_sec = time.perf_counter() - t0
                self.manager.last_gpu_time = gpu_sec
                created.extend(
                    f if os.path.isabs(f) else os.path.join(TEMP_DIR, f) for f in out_g
                )

            self.progress.emit("Testing CPU speed...")
            sep_c = self.manager.get_cpu_separator(self.model_filename)
            t0 = time.perf_counter()
            out_c = sep_c.separate(bench_wav)
            cpu_sec = time.perf_counter() - t0
            self.manager.last_cpu_time = cpu_sec
            created.extend(
                f if os.path.isabs(f) else os.path.join(TEMP_DIR, f) for f in out_c
            )

            self.finished_bench.emit(gpu_sec, cpu_sec)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            for f in [bench_wav] + created:
                if f and os.path.exists(f):
                    try:
                        os.remove(f)
                    except OSError:
                        pass


class UpdateCheckWorker(QThread):
    result = pyqtSignal(dict)
    error = pyqtSignal(str)

    def run(self):
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": f"{APP_NAME}/{APP_VERSION}",
                    "Accept": "application/vnd.github+json",
                },
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            tag = data.get("tag_name", "0.0.0")
            html_url = data.get("html_url", f"https://github.com/{GITHUB_REPO}/releases")
            notes = (data.get("body") or "").strip()
            download_url = ""
            for asset in data.get("assets", []):
                name = (asset.get("name") or "").lower()
                if name.endswith(".exe"):
                    download_url = asset.get("browser_download_url", "")
                    break

            is_newer = parse_version_tuple(tag) > parse_version_tuple(APP_VERSION)
            self.result.emit(
                {
                    "is_newer": is_newer,
                    "latest_version": tag.lstrip("vV"),
                    "release_url": html_url,
                    "download_url": download_url or html_url,
                    "has_direct_exe": bool(download_url),
                    "notes": notes,
                }
            )
        except urllib.error.HTTPError as e:
            if e.code == 404:
                self.error.emit(f"No releases published on GitHub yet ({GITHUB_REPO}).")
            else:
                self.error.emit(f"GitHub returned HTTP {e.code}.")
        except Exception as e:
            self.error.emit(f"Could not check for updates: {e}")


def _http_get_json(url: str, timeout: int = 6):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"{APP_NAME}/{APP_VERSION}",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def _http_get_bytes(url: str, timeout: int = 6) -> bytes:
    if not url:
        return b""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception:
        return b""


def _match_song_by_whisper_lyrics(
    wav_path: str,
    model_mgr: ModelManager,
    model_id: str,
    force_cpu: bool = False,
    cancel_evt: threading.Event | None = None,
):
    """Transcribes sung lyrics locally via faster-whisper and queries LRCLIB + iTunes."""
    wm = model_mgr.load_whisper(model_id, force_cpu=force_cpu)
    t0 = time.perf_counter()
    segments, _info = wm.transcribe(wav_path, beam_size=3, vad_filter=True)
    lines = []
    for seg in segments:
        if cancel_evt and cancel_evt.is_set():
            return None
        txt = (seg.text or "").strip()
        if txt:
            lines.append(txt)
    elapsed = time.perf_counter() - t0
    if force_cpu or not model_mgr.gpu_available:
        model_mgr.last_cpu_time = elapsed
    else:
        model_mgr.last_gpu_time = elapsed

    transcript = " ".join(lines).strip()
    words = [w for w in re.findall(r"[A-Za-z0-9'\u3040-\u30ff\u4e00-\u9fff]+", transcript) if len(w) > 1]
    if len(words) < 3:
        return None

    query_snippet = " ".join(words[:14])
    q = urllib.parse.quote_plus(query_snippet)

    title, artist, album, lyrics_txt = "", "", "", ""

    # 1. Search LRCLIB (free open lyrics database)
    try:
        lrc_hits = _http_get_json(f"https://lrclib.net/api/search?q={q}", timeout=6)
        if isinstance(lrc_hits, list) and lrc_hits:
            best = lrc_hits[0]
            title = (best.get("trackName") or "").strip()
            artist = (best.get("artistName") or "").strip()
            album = (best.get("albumName") or "").strip()
            lyrics_txt = (best.get("plainLyrics") or "")[:1200]
    except Exception:
        pass

    if not title or not artist:
        return None

    # 2. Enrich cover art, preview URL, genre & release date via iTunes Search
    cover_url, preview_url, genre, released, apple_url, artist_url, album_url = "", "", "", "", "", "", ""
    duration_ms = 0
    try:
        it_q = urllib.parse.quote_plus(f"{artist} {title}")
        it = _http_get_json(
            f"https://itunes.apple.com/search?term={it_q}&entity=song&limit=3",
            timeout=6,
        )
        hits = it.get("results") or []
        if hits:
            h = hits[0]
            cover_url = (h.get("artworkUrl100") or "").replace("100x100bb", "400x400bb")
            preview_url = h.get("previewUrl") or ""
            genre = h.get("primaryGenreName") or ""
            released = (h.get("releaseDate") or "")[:10]
            apple_url = h.get("trackViewUrl") or ""
            artist_url = h.get("artistViewUrl") or ""
            album_url = h.get("collectionViewUrl") or ""
            duration_ms = int(h.get("trackTimeMillis") or 0)
            if not album:
                album = h.get("collectionName") or ""
    except Exception:
        pass

    return _build_song_dict_from_metadata(
        title=title,
        artist=artist,
        album=album,
        released=released,
        genre=genre,
        cover_url=cover_url,
        preview_url=preview_url,
        apple_url=apple_url,
        artist_url=artist_url,
        album_url=album_url,
        duration_ms=duration_ms,
        lyrics=lyrics_txt or transcript,
        method_label=f"Whisper Lyrics ({elapsed:.1f}s)",
    )


def _write_speed_variant(raw_wav: str, speed_factor: float, out_wav: str) -> bool:
    """Creates a resampled speed-shifted WAV to rescue sped-up/nightcore or slowed+reverb tracks."""
    try:
        from scipy.signal import resample

        data, sr = sf.read(raw_wav, dtype="float32")
        if data is None or len(data) < sr * 2:
            return False
        target_len = max(sr * 2, int(round(len(data) / speed_factor)))
        shifted = resample(data, target_len, axis=0).astype(np.float32)
        sf.write(out_wav, shifted, sr)
        return True
    except Exception:
        return False


def _format_identify_error(exc: Exception) -> str:
    """Converts raw shazamio/aiohttp exceptions into short, clean user-facing messages."""
    raw = str(exc or "").strip()
    low = raw.lower()
    ename = type(exc).__name__.lower()

    if (
        "429" in low
        or "too many requests" in low
        or "rate limit" in low
        or "faileddecodejson" in ename
        or "failed to decode json" in low
        or ("amp.shazam.com" in low and ("403" in low or "status" in low or "json" in low))
    ):
        return "Shazam rate limit reached. Wait a few seconds and try again."

    if (
        "clientconnectorerror" in ename
        or "cannot connect" in low
        or "getaddrinfo" in low
        or "connection" in low
        or "network" in low
        or "ssl" in low
    ):
        return "Could not reach Shazam. Check your internet connection."

    if "timeout" in ename or "timed out" in low:
        return "Recognition timed out. Please try again."

    # Strip any raw URLs so long API endpoints never spill onto the UI
    cleaned = re.sub(r"https?://\S+", "", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" :,.-")
    if not cleaned:
        return "Song recognition failed. Please try again."
    if len(cleaned) > 95:
        cleaned = cleaned[:92].rstrip() + "..."
    return cleaned


class IdentifyWorker(QThread):
    """Song identification worker with non-blocking live probes, AI rescue on Live & Buffer, and instant cancel."""

    status_update = pyqtSignal(str)
    song_found = pyqtSignal(dict)
    no_match = pyqtSignal()
    cancelled = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(
        self,
        engine,
        model_mgr: ModelManager,
        settings: dict,
        mode: str,
        cancel_evt: threading.Event,
        custom_wav_path: str = "",
        test_models: list[str] | None = None,
    ):
        super().__init__()
        self.engine = engine
        self.model_mgr = model_mgr
        self.settings = dict(settings)
        self.mode = mode
        self.cancel_evt = cancel_evt
        self.custom_wav_path = custom_wav_path or ""
        self.test_models = list(test_models) if test_models is not None else []
        self._loop = None
        self._active_task = None
        self._loop_lock = threading.Lock()

    def request_cancel(self):
        """Immediately signals cancellation and aborts any in-flight Shazam HTTP request."""
        self.cancel_evt.set()
        with self._loop_lock:
            loop = self._loop
            task = self._active_task
        if loop and not loop.is_closed() and task and not task.done():
            try:
                loop.call_soon_threadsafe(task.cancel)
            except Exception:
                pass

    async def _close_shazam(self, shazam: Shazam):
        try:
            hc = getattr(shazam, "http_client", None)
            if hc is not None and hasattr(hc, "close"):
                await hc.close()
        except Exception:
            pass

    async def _recognize_cancellable(self, shazam: Shazam, wav_path: str, timeout: float = 10.0):
        if self.cancel_evt.is_set():
            raise asyncio.CancelledError()

        task = asyncio.create_task(shazam.recognize(wav_path))
        with self._loop_lock:
            self._active_task = task

        deadline = time.monotonic() + timeout
        try:
            while not task.done():
                if self.cancel_evt.is_set():
                    task.cancel()
                    try:
                        await task
                    except BaseException:
                        pass
                    raise asyncio.CancelledError()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    task.cancel()
                    try:
                        await task
                    except BaseException:
                        pass
                    return None
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=min(0.08, remaining))
                except asyncio.TimeoutError:
                    continue
            return task.result()
        finally:
            with self._loop_lock:
                if self._active_task is task:
                    self._active_task = None

    def _should_use_cpu_fallback(self):
        use_cpu = not self.model_mgr.gpu_available
        note = ""
        if self.model_mgr.gpu_available and self.settings.get("gpu_auto_cpu", True):
            info = get_nvidia_gpu_info()
            threshold = int(self.settings.get("gpu_threshold", 65))
            if info and info["util"] >= threshold:
                use_cpu = True
                note = f"GPU busy at {info['util']}%"
        return use_cpu, note

    def _run_ai_separation_files(self, raw_wav: str, model_name: str, generated_files: list):
        """Runs UVR model separation and returns [(wav_path, status_msg, method_label)]."""
        use_cpu, gpu_note = self._should_use_cpu_fallback()
        if use_cpu:
            why = f" ({gpu_note})" if gpu_note else ""
            self.status_update.emit(f"Running AI rescue on CPU{why}...")
            sep = self.model_mgr.get_cpu_separator(model_name)
            t0 = time.perf_counter()
            out_files = sep.separate(raw_wav)
            elapsed = time.perf_counter() - t0
            self.model_mgr.last_cpu_time = elapsed
            method_label = f"AI Rescue on CPU ({elapsed:.1f}s)"
        else:
            self.status_update.emit("Running AI rescue on GPU...")
            sep = self.model_mgr.load_default_gpu(model_name)
            t0 = time.perf_counter()
            out_files = sep.separate(raw_wav)
            elapsed = time.perf_counter() - t0
            self.model_mgr.last_gpu_time = elapsed
            method_label = f"AI Rescue on GPU ({elapsed:.1f}s)"

        resolved = [
            f if os.path.isabs(f) else os.path.join(TEMP_DIR, f) for f in out_files
        ]
        generated_files.extend(resolved)

        # Prefer clean music / dry / no-reverb / no-noise / instrumental stem first
        primary = next(
            (
                f
                for f in resolved
                if any(
                    k in os.path.basename(f).lower()
                    for k in ("no_reverb", "no reverb", "no_noise", "no noise", "no_crowd", "no crowd", "instrumental", "music", "dry")
                )
            ),
            resolved[0],
        )
        secondary = next((f for f in resolved if f != primary), None)
        out = [(primary, "Finding song match...", method_label)]
        if secondary:
            out.append((secondary, "Checking secondary stem...", method_label + " • Stem 2"))
        return out

    def _model_display_name(self, model_name: str) -> str:
        for entry in AI_MODEL_CATALOG:
            if entry.get("filename") == model_name:
                return entry.get("name", model_name)
        return os.path.splitext(os.path.basename(model_name or UVR_MODEL_NAME))[0]

    async def _run_ai_rescue_async(
        self,
        shazam: Shazam,
        raw_wav: str,
        generated_files: list,
        model_override: str | None = None,
        include_speed_sweep: bool = True,
        label_prefix: str = "",
    ):
        """Runs the configured local AI rescue model (UVR or Whisper) + optional speed sweep."""
        model_name = model_override or self.settings.get("ai_model", UVR_MODEL_NAME)
        model_disp = self._model_display_name(model_name)
        use_cpu, _note = self._should_use_cpu_fallback()

        if is_whisper_model_id(model_name):
            self.status_update.emit(f"Transcribing lyrics with {model_disp}...")
            loop = asyncio.get_running_loop()
            res = await loop.run_in_executor(
                None,
                lambda: _match_song_by_whisper_lyrics(
                    raw_wav, self.model_mgr, model_name, force_cpu=use_cpu, cancel_evt=self.cancel_evt
                ),
            )
            if self.cancel_evt.is_set():
                raise asyncio.CancelledError()
            if res:
                if label_prefix:
                    res["method"] = f"{label_prefix} • {model_disp} • {res.get('method', '')}".strip(" •")
                return res
        else:
            loop = asyncio.get_running_loop()
            stems = await loop.run_in_executor(
                None,
                lambda: self._run_ai_separation_files(raw_wav, model_name, generated_files),
            )
            if self.cancel_evt.is_set():
                raise asyncio.CancelledError()
            for wav_p, msg, lbl in stems:
                self.status_update.emit(msg)
                out = await self._recognize_cancellable(shazam, wav_p, timeout=10.0)
                full_lbl = f"{label_prefix} • {model_disp} • {lbl}" if label_prefix else lbl
                res = parse_shazam_response(out, full_lbl)
                if res:
                    return res

        if include_speed_sweep:
            # Speed/pitch rescue sweep for sped-up / slowed+reverb tracks (0 extra download)
            for factor, tag in ((0.84, "Sped-Up Rescue"), (1.18, "Slowed Rescue")):
                if self.cancel_evt.is_set():
                    raise asyncio.CancelledError()
                sp_wav = os.path.join(TEMP_DIR, f"speed_{int(factor * 100)}.wav")
                if _write_speed_variant(raw_wav, factor, sp_wav):
                    generated_files.append(sp_wav)
                    self.status_update.emit(f"Checking speed-adjusted audio ({tag})...")
                    out = await self._recognize_cancellable(shazam, sp_wav, timeout=8.0)
                    full_tag = f"{label_prefix} • {tag}" if label_prefix else f"AI • {tag}"
                    res = parse_shazam_response(out, full_tag)
                    if res:
                        return res
        return None

    async def _run_live_async(self, raw_wav: str, generated_files: list):
        max_sec = max(6, int(self.settings.get("live_max_seconds", 20)))
        use_ai = bool(self.settings.get("use_ai_separation", False))
        shazam = _create_shazam(attempts=1, max_timeout=1.0)
        start_t = time.monotonic()
        next_probe = 4.5
        probe_interval = 5.5
        probe_timeout = 6.5

        probe_task = None
        probe_started_t = 0.0
        probe_elapsed_snapshot = 0
        final_probe_fired = False
        last_status = ""
        rate_limited = False

        try:
            while True:
                if self.cancel_evt.is_set():
                    if probe_task and not probe_task.done():
                        probe_task.cancel()
                        try:
                            await probe_task
                        except BaseException:
                            pass
                    self.cancelled.emit()
                    return

                elapsed = time.monotonic() - start_t
                shown_sec = min(max_sec, int(elapsed) + 1)

                if probe_task is not None:
                    if probe_task.done():
                        try:
                            out = probe_task.result()
                        except asyncio.CancelledError:
                            if self.cancel_evt.is_set():
                                self.cancelled.emit()
                                return
                            out = None
                        except Exception as probe_err:
                            pe_low = str(probe_err).lower()
                            pe_name = type(probe_err).__name__.lower()
                            if (
                                "429" in pe_low
                                or "too many requests" in pe_low
                                or "faileddecodejson" in pe_name
                                or "failed to decode json" in pe_low
                            ):
                                rate_limited = True
                            out = None
                        finally:
                            with self._loop_lock:
                                if self._active_task is probe_task:
                                    self._active_task = None
                            probe_task = None

                        if out:
                            result = parse_shazam_response(
                                out, f"Live ({max(1, probe_elapsed_snapshot)}s)"
                            )
                            if result:
                                self.song_found.emit(result)
                                return

                        if rate_limited:
                            self.error.emit("Shazam rate limit reached. Wait a few seconds and try again.")
                            return

                        if final_probe_fired or elapsed >= max_sec:
                            # If AI rescue is enabled, run local AI rescue on the captured live clip!
                            if use_ai and os.path.exists(raw_wav) and not self.cancel_evt.is_set():
                                ai_res = await self._run_ai_rescue_async(shazam, raw_wav, generated_files)
                                if ai_res:
                                    self.song_found.emit(ai_res)
                                    return
                            self.no_match.emit()
                            return
                        next_probe = max(next_probe, elapsed + probe_interval)
                    else:
                        in_flight_age = time.monotonic() - probe_started_t
                        if in_flight_age >= probe_timeout:
                            probe_task.cancel()
                            try:
                                await probe_task
                            except BaseException:
                                pass
                            with self._loop_lock:
                                if self._active_task is probe_task:
                                    self._active_task = None
                            probe_task = None
                            if final_probe_fired or elapsed >= max_sec:
                                if use_ai and os.path.exists(raw_wav) and not self.cancel_evt.is_set():
                                    ai_res = await self._run_ai_rescue_async(shazam, raw_wav, generated_files)
                                    if ai_res:
                                        self.song_found.emit(ai_res)
                                        return
                                self.no_match.emit()
                                return
                            next_probe = elapsed + 1.5

                if probe_task is None and (elapsed >= next_probe or elapsed >= max_sec):
                    sample_len = min(max(4.0, elapsed), 12.0)
                    is_final = elapsed >= max_sec
                    if self.engine.dump_recent_to_wav(raw_wav, seconds=sample_len):
                        probe_elapsed_snapshot = min(max_sec, int(elapsed))
                        probe_started_t = time.monotonic()
                        final_probe_fired = is_final
                        probe_task = asyncio.create_task(shazam.recognize(raw_wav))
                        with self._loop_lock:
                            self._active_task = probe_task
                    elif is_final:
                        self.no_match.emit()
                        return
                    else:
                        next_probe = elapsed + 2.0

                if probe_task is not None:
                    msg = "Finishing song check..." if elapsed >= max_sec else f"Listening & checking... {shown_sec}s / {max_sec}s"
                else:
                    msg = f"Listening live... {shown_sec}s / {max_sec}s"

                if msg != last_status:
                    last_status = msg
                    self.status_update.emit(msg)

                await asyncio.sleep(0.08)
        finally:
            if probe_task and not probe_task.done():
                probe_task.cancel()
                try:
                    await probe_task
                except BaseException:
                    pass
            with self._loop_lock:
                self._active_task = None
            await self._close_shazam(shazam)

    async def _run_buffer_async(self, raw_wav: str, buf_sec: int, generated_files: list):
        use_ai = bool(self.settings.get("use_ai_separation", False))
        shazam = _create_shazam(attempts=2, max_timeout=1.2)
        try:
            if use_ai:
                ai_res = await self._run_ai_rescue_async(shazam, raw_wav, generated_files)
                if ai_res:
                    self.song_found.emit(ai_res)
                    return
                if self.cancel_evt.is_set():
                    self.cancelled.emit()
                    return
                self.status_update.emit("Checking original audio...")
                out = await self._recognize_cancellable(shazam, raw_wav, timeout=10.0)
                res = parse_shazam_response(out, "Original Audio Fallback")
                if res:
                    self.song_found.emit(res)
                else:
                    self.no_match.emit()
                return

            self.status_update.emit("Finding song match...")
            out = await self._recognize_cancellable(shazam, raw_wav, timeout=12.0)
            res = parse_shazam_response(out, f"Last {buf_sec}s")
            if res:
                self.song_found.emit(res)
            else:
                self.no_match.emit()
        finally:
            await self._close_shazam(shazam)

    async def _run_saved_clip_async(self, clip_wav: str, generated_files: list):
        shazam = _create_shazam(attempts=2, max_timeout=1.2)
        models = [m for m in self.test_models if m]
        try:
            if not models or models == ["__raw__"]:
                self.status_update.emit("Testing saved clip (Original Audio)...")
                out = await self._recognize_cancellable(shazam, clip_wav, timeout=12.0)
                res = parse_shazam_response(out, "Saved Clip (Original)")
                if res:
                    self.song_found.emit(res)
                else:
                    self.no_match.emit()
                return

            total = len(models)
            for idx, model_name in enumerate(models, 1):
                if self.cancel_evt.is_set():
                    self.cancelled.emit()
                    return
                if model_name == "__raw__":
                    self.status_update.emit(f"Testing saved clip (Original Audio) [{idx}/{total}]...")
                    out = await self._recognize_cancellable(shazam, clip_wav, timeout=12.0)
                    res = parse_shazam_response(out, "Saved Clip (Original)")
                    if res:
                        self.song_found.emit(res)
                        return
                    continue

                disp = self._model_display_name(model_name)
                self.status_update.emit(f"Testing saved clip on {disp} [{idx}/{total}]...")
                ai_res = await self._run_ai_rescue_async(
                    shazam,
                    clip_wav,
                    generated_files,
                    model_override=model_name,
                    include_speed_sweep=(idx == total),
                    label_prefix="Saved Clip",
                )
                if ai_res:
                    self.song_found.emit(ai_res)
                    return

            if self.cancel_evt.is_set():
                self.cancelled.emit()
                return
            self.no_match.emit()
        finally:
            await self._close_shazam(shazam)

    def run(self):
        raw_wav = os.path.join(TEMP_DIR, "snapshot.wav")
        generated_files = []
        started_temp_capture = False

        if self.mode != "saved_clip" and not self.engine.monitored_names:
            self.error.emit("No audio output selected. Choose an output in Settings.")
            return

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._loop_lock:
            self._loop = loop

        try:
            if self.mode == "saved_clip":
                clip_wav = self.custom_wav_path or SAVED_CLIP_WAV
                if not os.path.isfile(clip_wav):
                    self.error.emit("No saved audio clip found. Click 'Save Last Xs' first.")
                    return
                loop.run_until_complete(self._run_saved_clip_async(clip_wav, generated_files))
                return

            if self.mode == "live":
                if hasattr(self.engine, "begin_live_capture"):
                    self.engine.begin_live_capture()
                    started_temp_capture = True
                loop.run_until_complete(self._run_live_async(raw_wav, generated_files))
                return

            buf_sec = max(5, int(self.settings.get("buffer_seconds", 12)))
            self.status_update.emit(f"Grabbing last {buf_sec}s of audio...")
            if not self.engine.dump_recent_to_wav(raw_wav, seconds=buf_sec):
                self.error.emit("No recent audio found. Make sure audio is playing.")
                return

            if self.cancel_evt.is_set():
                self.cancelled.emit()
                return

            loop.run_until_complete(self._run_buffer_async(raw_wav, buf_sec, generated_files))

        except asyncio.CancelledError:
            self.cancelled.emit()
        except Exception as e:
            if self.cancel_evt.is_set():
                self.cancelled.emit()
            else:
                self.error.emit(_format_identify_error(e))
        finally:
            if started_temp_capture and hasattr(self.engine, "end_live_capture"):
                self.engine.end_live_capture()
            with self._loop_lock:
                self._active_task = None
                self._loop = None
            try:
                loop.close()
            except Exception:
                pass
            cleanup_targets = list(generated_files)
            if self.mode != "saved_clip":
                cleanup_targets.append(raw_wav)
            for f in cleanup_targets:
                if f and os.path.exists(f):
                    try:
                        os.remove(f)
                    except OSError:
                        pass


def resolve_youtube_song_url(artist: str, title: str) -> str:
    """Finds the direct YouTube Music audio-only Song (ATV / Art Track) watch URL."""
    vid = _resolve_ytm_audio_only_video_id(f"{artist} {title}".strip())
    return f"https://music.youtube.com/watch?v={vid}" if vid else ""


def resolve_youtube_watch_url(artist: str, title: str) -> str:
    """Finds the top direct youtube.com/watch?v=... link."""
    query = urllib.parse.quote_plus(f"{artist} {title} official audio".strip())
    url = f"https://www.youtube.com/results?search_query={query}"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        m = re.search(r'"videoId":"([A-Za-z0-9_-]{11})"', html)
        if m:
            return f"https://www.youtube.com/watch?v={m.group(1)}"
    except Exception:
        pass
    return ""


def resolve_ytdlp_download_url(song: dict) -> tuple[str, dict]:
    """Resolves the audio-only song link first, falling back to the video link if unavailable."""
    if not song:
        return "", {}

    artist = song.get("artist", "")
    title = song.get("title", "")
    updates = {}

    song_url = (song.get("youtube_song_url") or "").strip()
    if not song_url:
        song_url = resolve_youtube_song_url(artist, title)
        if song_url:
            song["youtube_song_url"] = song_url
            updates["youtube_song_url"] = song_url
    if song_url:
        return song_url, updates

    video_url = (song.get("youtube_video_url") or "").strip()
    if not video_url:
        yt_raw = (song.get("youtube_url") or "").strip()
        if "watch?v=" in yt_raw or "youtu.be/" in yt_raw:
            video_url = yt_raw
        else:
            video_url = resolve_youtube_watch_url(artist, title)
        if video_url:
            song["youtube_video_url"] = video_url
            updates["youtube_video_url"] = video_url
    if video_url:
        return video_url, updates

    fallback = (song.get("yt_music_url") or song.get("youtube_url") or "").strip()
    return fallback, updates


def _normalize_artist_name(name: str) -> str:
    """Fold diacritics and strip punctuation/whitespace for strict artist name matching."""
    text = unicodedata.normalize("NFKD", (name or "").strip())
    ascii_folded = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[^\w]", "", ascii_folded)


def _artist_names_match(a: str, b: str) -> bool:
    """Return True if two artist names match (case/diacritic/punctuation-insensitive)."""
    a_clean = (a or "").strip().casefold()
    b_clean = (b or "").strip().casefold()
    if not a_clean or not b_clean:
        return False
    if a_clean == b_clean:
        return True
    a_norm = _normalize_artist_name(a)
    b_norm = _normalize_artist_name(b)
    return bool(a_norm and a_norm == b_norm)


class ArtistInfoWorker(QThread):
    """Fetches rich in-app artist info (photo, bio, stats, top tracks & albums) with zero API keys."""

    loaded = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, artist_name: str, fallback_url: str = ""):
        super().__init__()
        self.artist_name = (artist_name or "").strip()
        self.fallback_url = fallback_url

    def run(self):
        if not self.artist_name:
            self.error.emit("No artist name provided.")
            return

        info = {
            "kind": "artist",
            "name": self.artist_name,
            "url": self.fallback_url
            or f"https://music.youtube.com/search?q={urllib.parse.quote_plus(self.artist_name)}",
            "genre": "",
            "fans": 0,
            "nb_album": 0,
            "bio": "",
            "image_bytes": b"",
            "top_tracks": [],
            "albums": [],
            "sources": [],
        }

        q = urllib.parse.quote_plus(self.artist_name)

        fallback_art_url = ""

        # Primary: Apple Music / iTunes (broader indie/electronic/doujin catalog)
        try:
            it = _http_get_json(
                f"https://itunes.apple.com/search?term={q}&entity=musicArtist&limit=5",
                timeout=5,
            )
            results = it.get("results") or []
            matching_it = [
                r for r in results if _artist_names_match(r.get("artistName") or "", self.artist_name)
            ]
            hit = next(
                (r for r in matching_it if r.get("primaryGenreName")),
                matching_it[0] if matching_it else None,
            )
            if hit:
                if "iTunes" not in info["sources"]:
                    info["sources"].append("iTunes")
                info["name"] = hit.get("artistName") or info["name"]
                if hit.get("primaryGenreName"):
                    info["genre"] = hit["primaryGenreName"]
                if hit.get("artistLinkUrl") and (not self.fallback_url or "search" in info["url"]):
                    info["url"] = hit["artistLinkUrl"]

                it_aid = hit.get("artistId")
                if it_aid:
                    try:
                        it_songs = _http_get_json(
                            f"https://itunes.apple.com/lookup?id={it_aid}&entity=song&limit=10",
                            timeout=5,
                        )
                        for item in (it_songs.get("results") or []):
                            if item.get("wrapperType") == "track" and item.get("kind") == "song":
                                if not fallback_art_url:
                                    fallback_art_url = (item.get("artworkUrl100") or "").replace(
                                        "100x100bb", "300x300bb"
                                    )
                                if len(info["top_tracks"]) < 6:
                                    info["top_tracks"].append(
                                        {
                                            "title": item.get("trackName", ""),
                                            "album": item.get("collectionName", ""),
                                            "duration": int(item.get("trackTimeMillis") or 0) // 1000,
                                            "preview": item.get("previewUrl", ""),
                                            "url": item.get("trackViewUrl", ""),
                                        }
                                    )
                    except Exception:
                        pass

                    try:
                        it_albs = _http_get_json(
                            f"https://itunes.apple.com/lookup?id={it_aid}&entity=album&limit=10",
                            timeout=5,
                        )
                        seen_alb = set()
                        for item in (it_albs.get("results") or []):
                            if item.get("wrapperType") == "collection":
                                atitle = (item.get("collectionName") or "").strip()
                                if atitle and atitle.lower() not in seen_alb:
                                    seen_alb.add(atitle.lower())
                                    info["albums"].append(
                                        {
                                            "title": atitle,
                                            "release_date": (item.get("releaseDate") or "")[:4],
                                            "record_type": (item.get("collectionType") or "").title(),
                                            "url": item.get("collectionViewUrl", ""),
                                        }
                                    )
                        if info["albums"]:
                            info["nb_album"] = len(info["albums"])
                    except Exception:
                        pass
        except Exception:
            pass

        # Secondary enrichment: Deezer (artist portrait, fan count, and fallback tracks/albums)
        try:
            dz = _http_get_json(f"https://api.deezer.com/search/artist?q={q}&limit=10", timeout=6)
            hits = dz.get("data") or []
            best = next(
                (h for h in hits if _artist_names_match(h.get("name") or "", self.artist_name)),
                None,
            )

            if best:
                if "Deezer" not in info["sources"]:
                    info["sources"].append("Deezer")
                aid = best.get("id")
                info["name"] = best.get("name") or info["name"]
                info["fans"] = int(best.get("nb_fan") or 0)
                if best.get("nb_album"):
                    info["nb_album"] = int(best["nb_album"])
                if best.get("link") and not self.fallback_url and "search" in info["url"]:
                    info["url"] = best["link"]
                pic = best.get("picture_big") or best.get("picture_medium") or best.get("picture") or ""
                if pic:
                    info["image_bytes"] = _http_get_bytes(pic, timeout=5)

                if aid and not info["top_tracks"]:
                    try:
                        top_data = _http_get_json(f"https://api.deezer.com/artist/{aid}/top?limit=6", timeout=5)
                        for tr in (top_data.get("data") or []):
                            info["top_tracks"].append(
                                {
                                    "title": tr.get("title", ""),
                                    "album": (tr.get("album") or {}).get("title", ""),
                                    "duration": int(tr.get("duration") or 0),
                                    "preview": tr.get("preview", ""),
                                    "url": tr.get("link", ""),
                                }
                            )
                    except Exception:
                        pass

                if aid and not info["albums"]:
                    try:
                        alb_data = _http_get_json(f"https://api.deezer.com/artist/{aid}/albums?limit=8", timeout=5)
                        seen_alb = set()
                        for al in (alb_data.get("data") or []):
                            atitle = (al.get("title") or "").strip()
                            if atitle and atitle.lower() not in seen_alb:
                                seen_alb.add(atitle.lower())
                                info["albums"].append(
                                    {
                                        "title": atitle,
                                        "release_date": (al.get("release_date") or "")[:4],
                                        "record_type": (al.get("record_type") or "").title(),
                                        "url": al.get("link", ""),
                                    }
                                )
                    except Exception:
                        pass
        except Exception:
            pass

        if not info["image_bytes"] and fallback_art_url:
            try:
                info["image_bytes"] = _http_get_bytes(fallback_art_url, timeout=5)
            except Exception:
                pass

        try:
            wiki_slug = urllib.parse.quote(self.artist_name.replace(" ", "_"))
            wk = _http_get_json(
                f"https://en.wikipedia.org/api/rest_v1/page/summary/{wiki_slug}",
                timeout=5,
            )
            extract = (wk.get("extract") or "").strip()
            desc = (wk.get("description") or "").lower()
            music_hints = (
                "singer", "musician", "band", "rapper", "producer", "composer",
                "dj", "vocalist", "songwriter", "music", "duo", "group", "artist", "vocaloid",
            )
            if extract and (
                any(h in desc for h in music_hints)
                or any(h in extract.lower()[:220] for h in music_hints)
            ):
                if len(extract) > 340:
                    cut = extract[:340].rfind(".")
                    extract = extract[: cut + 1] if cut > 120 else extract[:337].rstrip() + "..."
                info["bio"] = extract
                if "Wikipedia" not in info["sources"]:
                    info["sources"].append("Wikipedia")
        except Exception:
            pass

        self.loaded.emit(info)


class AlbumInfoWorker(QThread):
    """Fetches rich in-app album info (cover, release date, label, full tracklist & previews)."""

    loaded = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, album_name: str, artist_name: str = "", fallback_url: str = ""):
        super().__init__()
        self.album_name = (album_name or "").strip()
        self.artist_name = (artist_name or "").strip()
        self.fallback_url = fallback_url

    def run(self):
        if not self.album_name:
            self.error.emit("No album name provided.")
            return

        query_txt = f"{self.artist_name} {self.album_name}".strip()
        info = {
            "kind": "album",
            "title": self.album_name,
            "artist": self.artist_name,
            "url": self.fallback_url
            or f"https://music.youtube.com/search?q={urllib.parse.quote_plus(query_txt)}",
            "release_date": "",
            "genre": "",
            "track_count": 0,
            "copyright": "",
            "image_bytes": b"",
            "tracks": [],
            "sources": [],
        }

        q = urllib.parse.quote_plus(query_txt)

        try:
            it = _http_get_json(
                f"https://itunes.apple.com/search?term={q}&entity=album&limit=5",
                timeout=6,
            )
            results = it.get("results") or []
            if results:
                if "iTunes" not in info["sources"]:
                    info["sources"].append("iTunes")
                alb_low = self.album_name.lower()
                hit = next(
                    (r for r in results if alb_low in (r.get("collectionName") or "").lower()),
                    results[0],
                )
                cid = hit.get("collectionId")
                info["title"] = hit.get("collectionName") or info["title"]
                info["artist"] = hit.get("artistName") or info["artist"]
                if hit.get("collectionViewUrl") and (not self.fallback_url or "search" in info["url"]):
                    info["url"] = hit["collectionViewUrl"]
                if hit.get("releaseDate"):
                    info["release_date"] = hit["releaseDate"][:10]
                if hit.get("primaryGenreName"):
                    info["genre"] = hit["primaryGenreName"]
                if hit.get("trackCount"):
                    info["track_count"] = int(hit["trackCount"])
                if hit.get("copyright"):
                    info["copyright"] = hit["copyright"]

                art = (hit.get("artworkUrl100") or "").replace("100x100bb", "300x300bb")
                if art:
                    info["image_bytes"] = _http_get_bytes(art, timeout=5)

                if cid:
                    try:
                        lk = _http_get_json(
                            f"https://itunes.apple.com/lookup?id={cid}&entity=song&limit=60",
                            timeout=6,
                        )
                        for item in (lk.get("results") or []):
                            if item.get("wrapperType") == "track" and item.get("kind") == "song":
                                info["tracks"].append(
                                    {
                                        "number": int(item.get("trackNumber") or len(info["tracks"]) + 1),
                                        "title": item.get("trackName", ""),
                                        "artist": item.get("artistName", ""),
                                        "duration_ms": int(item.get("trackTimeMillis") or 0),
                                        "preview_url": item.get("previewUrl", ""),
                                        "url": item.get("trackViewUrl", ""),
                                    }
                                )
                    except Exception:
                        pass
        except Exception:
            pass

        if not info["tracks"]:
            try:
                dz = _http_get_json(f"https://api.deezer.com/search/album?q={q}&limit=5", timeout=6)
                hits = dz.get("data") or []
                if hits:
                    if "Deezer" not in info["sources"]:
                        info["sources"].append("Deezer")
                    hit = hits[0]
                    aid = hit.get("id")
                    if not info["image_bytes"]:
                        cov = hit.get("cover_big") or hit.get("cover_medium") or ""
                        if cov:
                            info["image_bytes"] = _http_get_bytes(cov, timeout=5)
                    if aid:
                        det = _http_get_json(f"https://api.deezer.com/album/{aid}", timeout=6)
                        if det.get("release_date") and not info["release_date"]:
                            info["release_date"] = det["release_date"]
                        if det.get("label") and not info["copyright"]:
                            info["copyright"] = det["label"]
                        if det.get("nb_tracks") and not info["track_count"]:
                            info["track_count"] = int(det["nb_tracks"])
                        if det.get("link") and "search" in info["url"]:
                            info["url"] = det["link"]
                        for idx, tr in enumerate(((det.get("tracks") or {}).get("data") or []), 1):
                            info["tracks"].append(
                                {
                                    "number": idx,
                                    "title": tr.get("title", ""),
                                    "artist": (tr.get("artist") or {}).get("name", ""),
                                    "duration_ms": int(tr.get("duration") or 0) * 1000,
                                    "preview_url": tr.get("preview", ""),
                                    "url": tr.get("link", ""),
                                }
                            )
            except Exception:
                pass

        self.loaded.emit(info)


class SongDetailEnrichWorker(QThread):
    """Fills in missing preview audio, clickable artist/album links, duration & direct YouTube URL."""

    enriched = pyqtSignal(str, dict)

    def __init__(self, song: dict):
        super().__init__()
        self.song = dict(song)

    def run(self):
        song_id = self.song.get("id", "")
        title = self.song.get("title", "")
        artist = self.song.get("artist", "")
        updates = {}

        need_itunes = (
            not self.song.get("preview_url")
            or not self.song.get("album_url")
            or not self.song.get("duration_ms")
        )
        if need_itunes and (title or artist):
            try:
                term = urllib.parse.quote_plus(f"{artist} {title}".strip())
                api = f"https://itunes.apple.com/search?term={term}&entity=song&limit=3"
                req = urllib.request.Request(api, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=6) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                results = data.get("results") or []
                if results:
                    hit = results[0]
                    if not self.song.get("preview_url") and hit.get("previewUrl"):
                        updates["preview_url"] = hit["previewUrl"]
                    if hit.get("artistViewUrl"):
                        updates["artist_url"] = hit["artistViewUrl"]
                    if hit.get("collectionViewUrl"):
                        updates["album_url"] = hit["collectionViewUrl"]
                    if not self.song.get("album") and hit.get("collectionName"):
                        updates["album"] = hit["collectionName"]
                    if not self.song.get("genre") and hit.get("primaryGenreName"):
                        updates["genre"] = hit["primaryGenreName"]
                    if not self.song.get("released") and hit.get("releaseDate"):
                        updates["released"] = hit["releaseDate"][:10]
                    if hit.get("trackTimeMillis"):
                        updates["duration_ms"] = int(hit["trackTimeMillis"])
            except Exception:
                pass

        yt_song_existing = self.song.get("youtube_song_url") or ""
        if not yt_song_existing and (title or artist):
            song_url = resolve_youtube_song_url(artist, title)
            if song_url:
                updates["youtube_song_url"] = song_url

        yt_existing = self.song.get("youtube_video_url") or ""
        if not yt_existing:
            yt_url = self.song.get("youtube_url") or ""
            if "watch?v=" in yt_url or "youtu.be/" in yt_url:
                updates["youtube_video_url"] = yt_url
            else:
                watch_url = resolve_youtube_watch_url(artist, title)
                if watch_url:
                    updates["youtube_video_url"] = watch_url

        if updates:
            self.enriched.emit(song_id, updates)
