from collections import deque
import ctypes
import threading
import warnings

import numpy as np
import soundfile as sf

from .config import CHUNK_FRAMES, SAMPLE_RATE, list_speakers_safe

warnings.filterwarnings("ignore", message="data discontinuity in recording")

class AudioBufferEngine:
    def __init__(self, target_devices=None, monitor_all=True, buffer_seconds=12):
        self.target_devices = list(target_devices or [])
        self.monitor_all = bool(monitor_all)
        self.buffer_seconds = max(5, int(buffer_seconds))
        self.buffers = {}
        self.locks = {}
        self.device_rms = {}
        self.device_bands = {}
        self.monitored_names = []
        self.all_speaker_names = []
        self._stop_event = threading.Event()
        self._threads = []

    def _max_chunks(self):
        return int((SAMPLE_RATE / CHUNK_FRAMES) * max(self.buffer_seconds, 20))

    def start(self):
        self.stop()
        self._stop_event = threading.Event()
        self.buffers.clear()
        self.locks.clear()
        self.device_rms.clear()
        self.device_bands.clear()
        self.monitored_names.clear()

        self.all_speaker_names = list_speakers_safe()
        for name in self.all_speaker_names:
            should_monitor = self.monitor_all or (name in self.target_devices)
            if should_monitor:
                self.monitored_names.append(name)
                self.buffers[name] = deque(maxlen=self._max_chunks())
                self.locks[name] = threading.Lock()
                self.device_rms[name] = 0.0
                t = threading.Thread(
                    target=self._loopback_worker,
                    args=(name, self._stop_event),
                    daemon=True,
                )
                t.start()
                self._threads.append(t)

    def reconfigure(self, target_devices, monitor_all, buffer_seconds):
        self.target_devices = list(target_devices or [])
        self.monitor_all = bool(monitor_all)
        self.buffer_seconds = max(5, int(buffer_seconds))
        self.start()

    def stop(self):
        self._stop_event.set()
        self._threads.clear()
        self.device_rms.clear()
        self.device_bands.clear()

    def get_live_waveform(self, bar_count=28):
        """Returns symmetrical spectrum bands and RMS, or all zeros if silent/no output selected."""
        if not self.monitored_names or not self.device_rms:
            return [0.0] * bar_count, 0.0

        max_rms = max(self.device_rms.values(), default=0.0)
        if max_rms < 0.0008:
            return [0.0] * bar_count, 0.0

        rms = min(1.0, max_rms * 10.0)
        combined = np.zeros(bar_count, dtype=np.float32)
        for bands in list(self.device_bands.values()):
            if bands is not None and len(bands) == bar_count:
                combined = np.maximum(combined, bands)
        return combined.tolist(), rms

    def _loopback_worker(self, dev_name, stop_evt):
        import soundcard as sc

        try:
            ctypes.windll.ole32.CoInitializeEx(None, 0)
        except Exception:
            pass

        while not stop_evt.is_set():
            try:
                mic = sc.get_microphone(id=str(dev_name), include_loopback=True)
                with mic.recorder(samplerate=SAMPLE_RATE, channels=2) as recorder:
                    while not stop_evt.is_set():
                        data = recorder.record(numframes=CHUNK_FRAMES)
                        lock = self.locks.get(dev_name)
                        buf = self.buffers.get(dev_name)
                        if lock and buf is not None:
                            with lock:
                                buf.append(data)

                        # Compute 14 musical bands and mirror left/right into 28 smooth bars
                        try:
                            mono = np.mean(data, axis=1)
                            rms = float(np.sqrt(np.mean(mono * mono)))
                            self.device_rms[dev_name] = rms
                            if rms >= 0.0008 and len(mono) >= 1024:
                                windowed = mono[-1024:] * np.hanning(1024)
                                spec = np.abs(np.fft.rfft(windowed))[2:170]  # 168 bins
                                half = spec.reshape(14, 12).mean(axis=1)
                                tilt = np.linspace(0.95, 1.55, 14, dtype=np.float32)
                                half = np.log1p(half * 13.0 * tilt) / 2.25
                                half[half < 0.06] = 0.0
                                half = np.clip(half, 0.0, 1.0)
                                # Symmetrical ring: right side top->bottom, left side bottom->top
                                full = np.concatenate([half, half[::-1]])
                                self.device_bands[dev_name] = full
                            else:
                                self.device_bands[dev_name] = np.zeros(28, dtype=np.float32)
                        except Exception:
                            pass
            except Exception:
                stop_evt.wait(2.0)

    def dump_recent_to_wav(self, out_path, seconds=None):
        if not self.monitored_names:
            return False

        req_chunks = (
            max(1, int((SAMPLE_RATE / CHUNK_FRAMES) * seconds))
            if seconds
            else int((SAMPLE_RATE / CHUNK_FRAMES) * self.buffer_seconds)
        )
        streams = []
        for dev_name, buf in list(self.buffers.items()):
            lock = self.locks.get(dev_name)
            if not lock:
                continue
            with lock:
                if len(buf) > 0:
                    chunks = list(buf)[-req_chunks:]
                    streams.append(np.concatenate(chunks, axis=0))

        if not streams:
            return False

        min_len = min(s.shape[0] for s in streams)
        if min_len == 0:
            return False

        combined = np.zeros((min_len, 2), dtype=np.float32)
        for s in streams:
            combined += s[-min_len:]

        peak = np.max(np.abs(combined))
        if peak > 1.0:
            combined /= peak

        sf.write(out_path, combined, SAMPLE_RATE)
        return True