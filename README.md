# SongPulse

A lightweight Windows desktop music identifier that listens directly to your PC's system audio—even while wearing headphones or using multiple audio outputs—with optional local AI voice & game SFX removal.

---

## Features

- **Multi-Output Desktop Audio Capture:** Listens directly to one or more Windows playback devices simultaneously via WASAPI loopback (e.g., direct headphones/audio interface + FxSound) without needing a microphone or virtual audio cables.
- **Two Listening Modes:**
  - **Listen (Live):** Continuously monitors playing audio and stops automatically as soon as a match is found (up to your configured max listen limit), with a **Cancel** button.
  - **Identify Last (Rolling Buffer):** Keeps the last 5–30 seconds of desktop audio in RAM so you can identify a track you _just_ heard immediately.
- **Optional Local AI Voice & SFX Removal:** Strips spoken dialogue, Discord chat, or game sound effects before scanning using a local UVR MDX-Net model (`UVR-MDX-NET-Inst_HQ_3.onnx`), with vocal-stem and raw-audio fallbacks.
- **Smart GPU Load Guard:** Runs AI separation on your NVIDIA GPU (CUDA) when idle and automatically switches to CPU if GPU utilization exceeds your configured threshold (default `65%`) to avoid frame drops while gaming. Includes a built-in GPU/CPU speed test.
- **Modern Fluent UI & System Tray Integration:**
  - Live audio-reactive radial equalizer around the **Listen** button (hides cleanly when silent).
  - Collapsible animated sidebar, **Always on Top** pin toggle, and automatic Windows Light/Dark theme matching (with manual overrides).
  - Global keyboard shortcut (`Ctrl+Alt+S` by default) that works over fullscreen games and apps.
  - Non-intrusive bottom-right status pill, forced bottom-right **Song Found** popup (bypasses Windows Do Not Disturb), and optional ping chime.
  - Minimize to system tray, optional Windows auto-startup, and clean force-quit on close (`X`).
- **Searchable Library & Dynamic Song Details:**
  - Saves identified songs locally with portable album cover caching.
  - Clicking any song opens a detailed view with a contrast-safe background gradient extracted from the album art, clickable Artist/Album links, track metadata (album, release date, genre, label, duration, ISRC, lyrics snippet), and a built-in **30-second audio preview player**.
  - Quick links to Shazam, YouTube, YouTube Music, Spotify, Apple Music, SoundCloud, Deezer, Amazon Music, and Tidal, plus one-click **Copy Link** and **Download with yt-dlp** integration.
- **Built-in GitHub Update Checker:** Checks GitHub Releases directly from **Settings → Updates**.

---

## What SongPulse Uses

| Category                          | Tools / Libraries / APIs                                                                              | Purpose                                                                                                                                               |
| :-------------------------------- | :---------------------------------------------------------------------------------------------------- | :---------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Language & UI**                 | Python 3.11+, `PyQt6`, `PyQt6.QtMultimedia`                                                           | Native Windows Fluent-style interface, custom vector icons/waveform, and 30s audio preview playback                                                   |
| **Audio Engine**                  | `soundcard`, `soundfile`, `numpy`                                                                     | Multi-device Windows WASAPI loopback capture, rolling RAM ring buffer, FFT spectrum visualizer, and WAV mixing                                        |
| **Music Recognition**             | `shazamio`                                                                                            | Acoustic fingerprinting and core song metadata (title, artist, album, label, genre, cover art, lyrics)                                                |
| **Metadata & Preview Enrichment** | iTunes Search API, YouTube Search                                                                     | Fetches 30-second audio previews, track duration, clickable artist/album pages, and direct `youtube.com/watch?v=` links                               |
| **Local AI Separation**           | `audio-separator`, `UVR-MDX-NET-Inst_HQ_3.onnx`, `onnxruntime-gpu`, `torch`, `torchvision`, `librosa` | Local vocal/dialogue & SFX removal with CUDA GPU acceleration and automatic CPU fallback                                                              |
| **Windows Integration**           | `keyboard`, `winotify`, `ctypes`, `winreg`, `winsound`, `nvidia-smi`                                  | Global shortcuts, Windows toast fallback, DWM dark titlebars, Always-on-Top pinning, startup registry entry, ping chime, and live GPU load monitoring |
| **Packaging**                     | `PyInstaller`, `Inno Setup 6`                                                                         | Standalone `SongPulse.exe` bundle and per-user Windows installer                                                                                      |

---

## Installation

### Option 1: Windows Installer (Recommended)

1. Download `SongPulse_Setup_v1.0.0.exe` from the [Releases](https://github.com/Greatly07/songpulse/releases/latest) page.
2. Run the installer (installs per-user to `%LOCALAPPDATA%\Programs\SongPulse`—no admin rights required).
3. Launch **SongPulse** from your Start Menu.

> **Note for GPU Acceleration:** AI Voice & SFX Removal works out of the box on CPU. For faster NVIDIA GPU acceleration (~1–2s), ensure you have an up-to-date NVIDIA driver and **CUDA Toolkit 13.x** installed.

### Option 2: Run from Source

1. **Requirements:** Windows 10/11 (64-bit) and Python 3.11+.
2. Clone the repository:
   ```bash
   git clone [https://github.com/Greatly07/songpulse.git](https://github.com/Greatly07/songpulse.git)
   cd songpulse
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
   _(If you do not have an NVIDIA GPU, install `audio-separator[cpu]` instead of `audio-separator[gpu]` and `onnxruntime-gpu`.)_
4. Launch the app:
   ```bash
   python song_id_app.pyw
   ```
   _(Or double-click `song_id_app.pyw` to run without a console window.)_

---

## Building the Windows Installer

1. Install [Inno Setup 6](https://jrsoftware.org/isdl.php) and PyInstaller (`pip install pyinstaller`).
2. Run `build_release.bat` from the project root.
3. The script automatically generates `songpulse.ico`, bundles `dist\SongPulse\SongPulse.exe` via PyInstaller, and compiles the installer to `release\SongPulse_Setup_v1.0.0.exe`.

---

## Project Structure

```text
SongID/
├── song_id_app.pyw          # Entry point & single-instance guard
├── songpulse/
│   ├── __init__.py
│   ├── config.py            # Paths, CUDA setup, settings/history I/O, Windows & GPU helpers
│   ├── audio.py             # Multi-output WASAPI rolling buffer & live FFT spectrum
│   ├── workers.py           # Shazam matching, UVR model manager, benchmark & GitHub updater
│   ├── theme.py             # Dark/Light QSS themes, vector icons & album-cover color extraction
│   ├── widgets.py           # Waveform button, responsive link bars, hover cards & popups
│   ├── detail_view.py       # Dynamic-gradient Song Details view, audio player & yt-dlp handoff
│   └── main_window.py       # Main window, animated sidebar, tray icon & live Settings
├── build_release.bat        # One-click PyInstaller + Inno Setup build script
├── SongPulse_Setup.iss      # Inno Setup per-user installer script
├── requirements.txt
└── README.md
```

---

## Data Storage

- **Installed `.exe`:** Saves settings, song library (`history.json`), cached album covers, and the downloaded UVR model in `%LOCALAPPDATA%\SongPulse` so updates never erase your data.
- **Running from source (`.pyw`):** Saves all runtime data in a local `data/` folder inside the project directory.
