from collections import deque
import ctypes
import threading
import warnings

import numpy as np
import soundfile as sf

from .config import CHUNK_FRAMES, SAMPLE_RATE, list_speakers_safe

warnings.filterwarnings("ignore", message="data discontinuity in recording")

WAVEFORM_BARS = 24
_FFT_SIZE = 2048


def _build_log_band_edges(bar_count: int, fft_size: int, sample_rate: int):
    """Precomputes logarithmically spaced FFT bin edges (~65 Hz to ~10.5 kHz) for balanced lively bars."""
    freqs = np.fft.rfftfreq(fft_size, d=1.0 / sample_rate)
    edge_freqs = np.geomspace(65.0, 10500.0, bar_count + 1)
    edges = []
    max_bin = len(freqs) - 1
    prev_hi = 1
    for i in range(bar_count):
        lo = int(np.searchsorted(freqs, edge_freqs[i], side="left"))
        hi = int(np.searchsorted(freqs, edge_freqs[i + 1], side="right"))
        lo = max(prev_hi, min(lo, max_bin - 1))
        hi = max(lo + 1, min(hi, max_bin))
        edges.append((lo, hi))
        prev_hi = lo + 1 if hi <= lo + 1 else hi
    return edges


_BAND_EDGES = _build_log_band_edges(WAVEFORM_BARS, _FFT_SIZE, SAMPLE_RATE)
_BAND_WEIGHTS = np.geomspace(0.42, 2.35, WAVEFORM_BARS).astype(np.float32)
_HANN_WINDOW = np.hanning(_FFT_SIZE).astype(np.float32)


class AudioBufferEngine:
    def __init__(
        self,
        target_devices=None,
        monitor_all=True,
        buffer_seconds=12,
        enable_buffer=True,
    ):
        self.target_devices = list(target_devices or [])
        self.monitor_all = bool(monitor_all)
        self.buffer_seconds = max(5, int(buffer_seconds))
        self.enable_buffer = bool(enable_buffer)
        self._live_capture_active = False
        self.buffers = {}
        self.locks = {}
        self.device_rms = {}
        self.device_bands = {}
        self.device_bands_lr = {}
        self.monitored_names = []
        self.all_speaker_names = []
        self._stop_event = threading.Event()
        self._threads = []

    def _max_chunks(self):
        return int((SAMPLE_RATE / CHUNK_FRAMES) * max(self.buffer_seconds, 20))

    def begin_live_capture(self):
        """Enables temporary chunk recording during Listen Live even when idle RAM buffer is disabled."""
        self._live_capture_active = True
        if not self.enable_buffer:
            for name, lock in list(self.locks.items()):
                buf = self.buffers.get(name)
                if lock and buf is not None:
                    with lock:
                        buf.clear()

    def end_live_capture(self):
        self._live_capture_active = False
        if not self.enable_buffer:
            for name, lock in list(self.locks.items()):
                buf = self.buffers.get(name)
                if lock and buf is not None:
                    with lock:
                        buf.clear()

    def start(self):
        self.stop()
        self._stop_event = threading.Event()
        self.buffers.clear()
        self.locks.clear()
        self.device_rms.clear()
        self.device_bands.clear()
        self.device_bands_lr.clear()
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

    def reconfigure(self, target_devices, monitor_all, buffer_seconds, enable_buffer=None):
        self.target_devices = list(target_devices or [])
        self.monitor_all = bool(monitor_all)
        self.buffer_seconds = max(5, int(buffer_seconds))
        if enable_buffer is not None:
            self.enable_buffer = bool(enable_buffer)
        self.start()

    def stop(self):
        self._stop_event.set()
        self._threads.clear()
        self.device_rms.clear()
        self.device_bands.clear()
        self.device_bands_lr.clear()

    def get_live_waveform(self, bar_count=WAVEFORM_BARS):
        """Returns independent spectrum bands and RMS, or all zeros if silent/no output selected."""
        combined, _l, _r, rms = self.get_live_waveform_stereo(bar_count)
        return combined, rms

    def get_live_waveform_stereo(self, bar_count=WAVEFORM_BARS):
        """Returns (combined_bands, left_bands, right_bands, rms) with near-zero overhead."""
        if not self.monitored_names or not self.device_rms:
            zeros = [0.0] * bar_count
            return zeros, zeros, zeros, 0.0

        max_rms = max(self.device_rms.values(), default=0.0)
        if max_rms < 0.0008:
            zeros = [0.0] * bar_count
            return zeros, zeros, zeros, 0.0

        rms = min(1.0, max_rms * 10.0)
        comb_l = np.zeros(bar_count, dtype=np.float32)
        comb_r = np.zeros(bar_count, dtype=np.float32)
        for pair in list(self.device_bands_lr.values()):
            if pair is not None:
                bl, br = pair
                if bl is not None and len(bl) == bar_count:
                    comb_l = np.maximum(comb_l, bl)
                if br is not None and len(br) == bar_count:
                    comb_r = np.maximum(comb_r, br)
        combined = np.maximum(comb_l, comb_r)
        return combined.tolist(), comb_l.tolist(), comb_r.tolist(), rms

    def _loopback_worker(self, dev_name, stop_evt):
        import soundcard as sc

        try:
            ctypes.windll.ole32.CoInitializeEx(None, 0)
        except Exception:
            pass

        fft_ring_lr = np.zeros((2, _FFT_SIZE), dtype=np.float32)
        running_peak = 0.15

        while not stop_evt.is_set():
            try:
                mic = sc.get_microphone(id=str(dev_name), include_loopback=True)
                with mic.recorder(samplerate=SAMPLE_RATE, channels=2) as recorder:
                    while not stop_evt.is_set():
                        data = recorder.record(numframes=CHUNK_FRAMES)
                        # Only retain PCM chunks in RAM when rolling buffer OR active live listen is enabled
                        if self.enable_buffer or self._live_capture_active:
                            lock = self.locks.get(dev_name)
                            buf = self.buffers.get(dev_name)
                            if lock and buf is not None:
                                with lock:
                                    buf.append(data)

                        try:
                            if data.ndim == 2 and data.shape[1] >= 2:
                                left = data[:, 0].astype(np.float32, copy=False)
                                right = data[:, 1].astype(np.float32, copy=False)
                            else:
                                left = data.reshape(-1).astype(np.float32, copy=False)
                                right = left

                            rms_l = float(np.sqrt(np.mean(left * left)))
                            rms_r = float(np.sqrt(np.mean(right * right)))
                            rms = max(rms_l, rms_r)
                            self.device_rms[dev_name] = rms

                            n_in = len(left)
                            if n_in >= _FFT_SIZE:
                                fft_ring_lr[0, :] = left[-_FFT_SIZE:]
                                fft_ring_lr[1, :] = right[-_FFT_SIZE:]
                            else:
                                fft_ring_lr = np.roll(fft_ring_lr, -n_in, axis=1)
                                fft_ring_lr[0, -n_in:] = left
                                fft_ring_lr[1, -n_in:] = right

                            if rms >= 0.0008:
                                # Single vectorized 2-channel FFT keeps stereo overhead negligible
                                specs = np.abs(np.fft.rfft(fft_ring_lr * _HANN_WINDOW, axis=1))
                                raw_lr = np.empty((2, WAVEFORM_BARS), dtype=np.float32)
                                for idx, (lo, hi) in enumerate(_BAND_EDGES):
                                    seg_l = specs[0, lo:hi]
                                    seg_r = specs[1, lo:hi]
                                    raw_lr[0, idx] = float(np.sqrt(np.mean(seg_l * seg_l)))
                                    raw_lr[1, idx] = float(np.sqrt(np.mean(seg_r * seg_r)))

                                weighted = raw_lr * _BAND_WEIGHTS[np.newaxis, :]
                                bands_lr = np.log1p(weighted * 5.2)

                                if rms_l < 0.0008:
                                    bands_lr[0, :] = 0.0
                                if rms_r < 0.0008:
                                    bands_lr[1, :] = 0.0

                                active_peak = float(np.percentile(np.maximum(bands_lr[0], bands_lr[1]), 92))
                                if active_peak > running_peak:
                                    running_peak = running_peak * 0.55 + active_peak * 0.45
                                else:
                                    running_peak = max(
                                        0.18, running_peak * 0.96 + active_peak * 0.04
                                    )

                                norm_lr = bands_lr / max(running_peak * 1.15, 0.22)
                                norm_lr = np.power(np.clip(norm_lr, 0.0, 1.0), 1.10)
                                norm_lr[norm_lr < 0.02] = 0.0
                                norm_l = norm_lr[0]
                                norm_r = norm_lr[1]
                                self.device_bands_lr[dev_name] = (norm_l, norm_r)
                                self.device_bands[dev_name] = np.maximum(norm_l, norm_r)
                            else:
                                zeros = np.zeros(WAVEFORM_BARS, dtype=np.float32)
                                self.device_bands_lr[dev_name] = (zeros, zeros)
                                self.device_bands[dev_name] = zeros
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
