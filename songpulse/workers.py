import asyncio
from datetime import datetime
import hashlib
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request

import numpy as np
import soundfile as sf
from audio_separator.separator import Separator
from shazamio import Shazam
from PyQt6.QtCore import QThread, pyqtSignal

from .config import (
    APP_NAME,
    APP_VERSION,
    COVERS_DIR,
    GITHUB_REPO,
    MODELS_DIR,
    SAMPLE_RATE,
    TEMP_DIR,
    UVR_MODEL_NAME,
    get_nvidia_gpu_info,
    is_cuda_provider_available,
    parse_version_tuple,
)


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


def parse_shazam_response(out, method_label=""):
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

    # Extract 30s audio preview URL from Shazam hub actions
    preview_url = ""
    for act in hub.get("actions", []):
        uri = act.get("uri", "")
        if act.get("type") == "uri" and uri.startswith("http"):
            preview_url = uri
            break

    # Extract lyrics snippet if available
    lyrics_lines = []
    for section in track.get("sections", []):
        if section.get("type") == "LYRICS":
            lyrics_lines = section.get("text", []) or []
            break

    # Build clickable artist & album URLs
    artist_q = urllib.parse.quote_plus(artist)
    artist_url = f"https://www.youtube.com/results?search_query={artist_q}"
    artists_list = track.get("artists") or []
    if artists_list and artists_list[0].get("adamid"):
        artist_url = f"https://www.shazam.com/artist/-/{artists_list[0]['adamid']}"

    album_url = (
        f"https://music.youtube.com/search?q={urllib.parse.quote_plus(f'{artist} {album}'.strip())}"
        if album
        else ""
    )

    return {
        "id": hashlib.md5(f"{artist}-{title}-{time.time()}".encode()).hexdigest()[:12],
        "title": title,
        "artist": artist,
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
        "youtube_video_url": "",
        "isrc": track.get("isrc", ""),
        "duration_ms": 0,
        "lyrics": "\n".join(lyrics_lines[:40]) if lyrics_lines else "",
    }


class ModelManager:
    def __init__(self):
        self.sep_gpu = None
        self.sep_cpu = None
        self.gpu_available = is_cuda_provider_available()
        self.lock = threading.Lock()
        self.last_gpu_time = None
        self.last_cpu_time = None

    def load_default_gpu(self):
        with self.lock:
            if self.sep_gpu is None:
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
                sep.load_model(model_filename=UVR_MODEL_NAME)
                self.sep_gpu = sep
            return self.sep_gpu

    def get_cpu_separator(self):
        with self.lock:
            if self.sep_cpu is None:
                sep = Separator(
                    log_level=40,
                    output_dir=TEMP_DIR,
                    model_file_dir=MODELS_DIR,
                )
                sep.onnx_execution_provider = ["CPUExecutionProvider"]
                sep.load_model(model_filename=UVR_MODEL_NAME)
                self.sep_cpu = sep
            return self.sep_cpu


class ModelLoaderThread(QThread):
    loaded = pyqtSignal(bool)
    failed = pyqtSignal(str)

    def __init__(self, manager: ModelManager):
        super().__init__()
        self.manager = manager

    def run(self):
        try:
            self.manager.load_default_gpu()
            self.loaded.emit(self.manager.gpu_available)
        except Exception as e:
            self.failed.emit(str(e))


class BenchmarkWorker(QThread):
    progress = pyqtSignal(str)
    finished_bench = pyqtSignal(object, object)
    error = pyqtSignal(str)

    def __init__(self, manager: ModelManager):
        super().__init__()
        self.manager = manager

    def run(self):
        bench_wav = os.path.join(TEMP_DIR, "bench_sample.wav")
        created = []
        try:
            t = np.linspace(0, 8, SAMPLE_RATE * 8, dtype=np.float32)
            tone = 0.25 * np.sin(2 * np.pi * 440.0 * t)
            stereo = np.stack([tone, tone], axis=1)
            sf.write(bench_wav, stereo, SAMPLE_RATE)

            gpu_sec = None
            if self.manager.gpu_available:
                self.progress.emit("Testing GPU speed...")
                sep_g = self.manager.load_default_gpu()
                t0 = time.perf_counter()
                out_g = sep_g.separate(bench_wav)
                gpu_sec = time.perf_counter() - t0
                self.manager.last_gpu_time = gpu_sec
                created.extend(
                    f if os.path.isabs(f) else os.path.join(TEMP_DIR, f) for f in out_g
                )

            self.progress.emit("Testing CPU speed...")
            sep_c = self.manager.get_cpu_separator()
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
            download_url = html_url
            for asset in data.get("assets", []):
                name = (asset.get("name") or "").lower()
                if name.endswith(".exe"):
                    download_url = asset.get("browser_download_url", html_url)
                    break

            is_newer = parse_version_tuple(tag) > parse_version_tuple(APP_VERSION)
            self.result.emit(
                {
                    "is_newer": is_newer,
                    "latest_version": tag.lstrip("vV"),
                    "release_url": html_url,
                    "download_url": download_url,
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


class IdentifyWorker(QThread):
    status_update = pyqtSignal(str)
    song_found = pyqtSignal(dict)
    no_match = pyqtSignal()
    cancelled = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, engine, model_mgr: ModelManager, settings: dict, mode: str, cancel_evt: threading.Event):
        super().__init__()
        self.engine = engine
        self.model_mgr = model_mgr
        self.settings = settings
        self.mode = mode
        self.cancel_evt = cancel_evt

    def run(self):
        if not self.engine.monitored_names:
            self.error.emit("No audio output selected. Choose an output in Settings.")
            return

        raw_wav = os.path.join(TEMP_DIR, "snapshot.wav")
        generated_files = []
        try:
            shazam = Shazam()

            if self.mode == "live":
                max_sec = max(6, int(self.settings.get("live_max_seconds", 20)))
                start_t = time.monotonic()
                next_probe = 4.0

                while True:
                    if self.cancel_evt.is_set():
                        self.cancelled.emit()
                        return

                    elapsed = time.monotonic() - start_t
                    shown_sec = min(max_sec, int(elapsed) + 1)
                    self.status_update.emit(f"Listening live... {shown_sec}s / {max_sec}s")

                    if elapsed >= next_probe or elapsed >= max_sec:
                        sample_len = min(elapsed, 12.0)
                        if self.engine.dump_recent_to_wav(raw_wav, seconds=sample_len):
                            self.status_update.emit(
                                f"Checking song ({int(elapsed)}s / {max_sec}s)..."
                            )
                            out = asyncio.run(shazam.recognize(raw_wav))
                            if self.cancel_evt.is_set():
                                self.cancelled.emit()
                                return
                            result = parse_shazam_response(
                                out, f"Live ({int(elapsed)}s)"
                            )
                            if result:
                                self.song_found.emit(result)
                                return

                        if elapsed >= max_sec:
                            self.no_match.emit()
                            return
                        next_probe = elapsed + 3.5

                    time.sleep(0.2)

            buf_sec = max(5, int(self.settings.get("buffer_seconds", 12)))
            self.status_update.emit(f"Grabbing last {buf_sec}s of audio...")
            if not self.engine.dump_recent_to_wav(raw_wav, seconds=buf_sec):
                self.error.emit("No recent audio found. Make sure audio is playing.")
                return

            if self.cancel_evt.is_set():
                self.cancelled.emit()
                return

            use_ai = bool(self.settings.get("use_ai_separation", False))
            method_label = f"Last {buf_sec}s"
            result = None

            if use_ai:
                use_cpu_fallback = False
                gpu_util_note = ""
                if self.model_mgr.gpu_available and self.settings.get("gpu_auto_cpu", True):
                    info = get_nvidia_gpu_info()
                    threshold = int(self.settings.get("gpu_threshold", 65))
                    if info and info["util"] >= threshold:
                        use_cpu_fallback = True
                        gpu_util_note = f"GPU busy at {info['util']}%"

                if use_cpu_fallback or not self.model_mgr.gpu_available:
                    why = f" ({gpu_util_note})" if gpu_util_note else ""
                    self.status_update.emit(f"Cleaning voice & SFX on CPU{why}...")
                    sep = self.model_mgr.get_cpu_separator()
                    t0 = time.perf_counter()
                    out_files = sep.separate(raw_wav)
                    elapsed = time.perf_counter() - t0
                    self.model_mgr.last_cpu_time = elapsed
                    method_label = f"AI Cleanup on CPU ({elapsed:.1f}s)"
                else:
                    self.status_update.emit("Cleaning voice & SFX on GPU...")
                    sep = self.model_mgr.load_default_gpu()
                    t0 = time.perf_counter()
                    out_files = sep.separate(raw_wav)
                    elapsed = time.perf_counter() - t0
                    self.model_mgr.last_gpu_time = elapsed
                    method_label = f"AI Cleanup on GPU ({elapsed:.1f}s)"

                resolved = [
                    f if os.path.isabs(f) else os.path.join(TEMP_DIR, f)
                    for f in out_files
                ]
                generated_files.extend(resolved)

                if self.cancel_evt.is_set():
                    self.cancelled.emit()
                    return

                inst_file = next(
                    (f for f in resolved if "instrumental" in os.path.basename(f).lower()),
                    resolved[0],
                )
                vocal_file = next(
                    (f for f in resolved if "vocals" in os.path.basename(f).lower()),
                    None,
                )

                self.status_update.emit("Finding song match...")
                out = asyncio.run(shazam.recognize(inst_file))
                result = parse_shazam_response(out, method_label)

                if not result and vocal_file and not self.cancel_evt.is_set():
                    self.status_update.emit("Checking vocals track...")
                    out = asyncio.run(shazam.recognize(vocal_file))
                    result = parse_shazam_response(out, method_label + " • Vocals")

                if not result and not self.cancel_evt.is_set():
                    self.status_update.emit("Checking original audio...")
                    out = asyncio.run(shazam.recognize(raw_wav))
                    result = parse_shazam_response(out, "Original Audio Fallback")
            else:
                self.status_update.emit("Finding song match...")
                out = asyncio.run(shazam.recognize(raw_wav))
                result = parse_shazam_response(out, method_label)

            if self.cancel_evt.is_set():
                self.cancelled.emit()
                return

            if result:
                self.song_found.emit(result)
            else:
                self.no_match.emit()

        except Exception as e:
            self.error.emit(str(e))
        finally:
            for f in [raw_wav] + generated_files:
                if f and os.path.exists(f):
                    try:
                        os.remove(f)
                    except OSError:
                        pass


def resolve_youtube_watch_url(artist: str, title: str) -> str:
    """Finds the top direct youtube.com/watch?v=... link so yt-dlp can download it directly."""
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


class SongDetailEnrichWorker(QThread):
    """Fills in missing preview audio, clickable artist/album links, duration & direct YouTube URL."""

    enriched = pyqtSignal(str, dict)  # (song_id, updated_fields)

    def __init__(self, song: dict):
        super().__init__()
        self.song = dict(song)

    def run(self):
        song_id = self.song.get("id", "")
        title = self.song.get("title", "")
        artist = self.song.get("artist", "")
        updates = {}

        # 1. Enrich audio preview, duration, release date, artist & album links via iTunes Search API
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

        # 2. Resolve a direct YouTube watch URL for yt-dlp downloads
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