"""Audio sources: device, per-process tap (macOS), and file replay (SPEC §7.2)."""

from __future__ import annotations

import platform
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import numpy as np

from livecaster.audio.devices import audiotee_binary, resolve_device
from livecaster.log import get_logger

log = get_logger(__name__)

TARGET_RATE = 16_000
FRAME_SAMPLES = 512  # 32 ms at 16 kHz — the Silero VAD frame

OnFrames = Callable[[np.ndarray, float], None]


class AudioSource(Protocol):  # pragma: no cover - structural typing only
    name: str

    def start(self, on_frames: OnFrames) -> None: ...
    def stop(self) -> None: ...


class _Resampler:
    """Lazy soxr resampler; a pass-through when the rates already match."""

    def __init__(self, in_rate: int, out_rate: int = TARGET_RATE) -> None:
        self.in_rate = in_rate
        self.out_rate = out_rate
        self._stream = None
        if in_rate != out_rate:
            import soxr

            self._stream = soxr.ResampleStream(in_rate, out_rate, 1, dtype="float32")

    def __call__(self, block: np.ndarray) -> np.ndarray:
        if self._stream is None:
            return block.astype(np.float32, copy=False)
        return self._stream.resample_chunk(block.astype(np.float32, copy=False))


class _FrameSlicer:
    """Buffers arbitrary blocks and hands out exactly ``FRAME_SAMPLES`` at a time."""

    def __init__(self, on_frames: OnFrames, rate: int = TARGET_RATE) -> None:
        self.on_frames = on_frames
        self.rate = rate
        self._buf = np.zeros(0, dtype=np.float32)
        self._next_t: float | None = None

    def push(self, block: np.ndarray, t: float) -> None:
        if self._next_t is None:
            self._next_t = t
        if block.size:
            self._buf = np.concatenate([self._buf, block.astype(np.float32, copy=False)])
        while self._buf.size >= FRAME_SAMPLES:
            frame = self._buf[:FRAME_SAMPLES]
            self._buf = self._buf[FRAME_SAMPLES:]
            self.on_frames(frame, self._next_t)
            self._next_t += FRAME_SAMPLES / self.rate

    def flush(self) -> None:
        if self._buf.size and self._next_t is not None:
            pad = np.zeros(FRAME_SAMPLES - self._buf.size, dtype=np.float32)
            self.on_frames(np.concatenate([self._buf, pad]), self._next_t)
            self._buf = np.zeros(0, dtype=np.float32)


def _to_mono(data: np.ndarray, channel_index: int | None) -> np.ndarray:
    if data.ndim == 1:
        return data
    if channel_index is None:
        return data.mean(axis=1)
    idx = min(channel_index, data.shape[1] - 1)
    return data[:, idx]


class DeviceSource:
    """PortAudio input stream, resampled to 16 kHz mono float32."""

    def __init__(
        self,
        name: str,
        device: str | int | None = None,
        channel_index: int | None = None,
        sample_rate: int | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.name = name
        self.device_spec = device
        self.channel_index = channel_index
        self.requested_rate = sample_rate
        self.clock = clock or time.monotonic
        self._stream = None
        self._slicer: _FrameSlicer | None = None
        self._resampler: _Resampler | None = None
        self.rate = TARGET_RATE
        self.channels = 1

    def start(self, on_frames: OnFrames) -> None:
        import sounddevice as sd

        device = resolve_device(self.device_spec)
        info = sd.query_devices(device, "input") if device is not None else sd.query_devices(kind="input")
        max_in = int(info["max_input_channels"])
        self.channels = max_in if self.channel_index is not None else min(max_in, 2)
        rate = self.requested_rate or TARGET_RATE
        try:
            sd.check_input_settings(device=device, channels=self.channels, samplerate=rate)
        except Exception:
            rate = int(info["default_samplerate"])
            log.info("%s: device refused 16 kHz, opening at %d Hz and resampling", self.name, rate)
        self.rate = rate
        self._resampler = _Resampler(rate, TARGET_RATE)
        self._slicer = _FrameSlicer(on_frames)

        def callback(indata, frames, time_info, status):  # noqa: ANN001
            if status:
                log.debug("%s: portaudio status %s", self.name, status)
            mono = _to_mono(np.asarray(indata, dtype=np.float32), self.channel_index)
            assert self._resampler is not None and self._slicer is not None
            self._slicer.push(self._resampler(mono), self.clock())

        self._stream = sd.InputStream(
            device=device,
            channels=self.channels,
            samplerate=rate,
            dtype="float32",
            blocksize=0,
            callback=callback,
        )
        self._stream.start()
        log.info("%s: capturing from device %s at %d Hz", self.name, info["name"], rate)

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # pragma: no cover
                log.debug("%s: error closing stream", self.name, exc_info=True)
            self._stream = None
        if self._slicer is not None:
            self._slicer.flush()


class AudioTeeSource:
    """Per-process system-audio tap through the AudioTee helper (macOS 14.2+)."""

    def __init__(
        self,
        name: str,
        target: str,
        clock: Callable[[], float] | None = None,
        binary: str | None = None,
    ) -> None:
        self.name = name
        self.target = target
        self.clock = clock or time.monotonic
        self.binary = binary or audiotee_binary()
        self._proc: subprocess.Popen[bytes] | None = None
        self._thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._on_frames: OnFrames | None = None

    def resolve_pids(self) -> list[int]:
        if self.target.isdigit():
            return [int(self.target)]
        try:
            res = subprocess.run(["pgrep", "-x", self.target], capture_output=True, text=True, timeout=5)
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(f"cannot resolve process {self.target!r}: {exc}") from exc
        pids = [int(p) for p in res.stdout.split() if p.strip().isdigit()]
        if not pids:
            raise RuntimeError(
                f"no running process named {self.target!r}. Start it first, or use "
                f"`livecaster devices` to see candidates."
            )
        return pids

    def start(self, on_frames: OnFrames) -> None:
        if platform.system() != "Darwin":
            raise RuntimeError("AudioTee is macOS only; use a loopback device on this platform")
        if not self.binary:
            raise RuntimeError(
                "audiotee binary not found. Run ./helpers/audiotee/build.sh, or use BlackHole "
                'with source = "device:BlackHole 2ch".'
            )
        self._on_frames = on_frames
        self._stop.clear()
        self._spawn()
        self._thread = threading.Thread(target=self._read_loop, name=f"audiotee-{self.name}", daemon=True)
        self._thread.start()

    def _spawn(self) -> None:
        pids = self.resolve_pids()
        cmd = [
            str(self.binary),
            "--sample-rate",
            str(TARGET_RATE),
            "--chunk-duration",
            "0.1",
            "--include-processes",
            *[str(p) for p in pids],
        ]
        log.info("%s: %s", self.name, " ".join(cmd))
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                log.info("audiotee[%s]: %s", self.name, line)

    def _read_loop(self) -> None:
        assert self._on_frames is not None
        slicer = _FrameSlicer(self._on_frames)
        while not self._stop.is_set():
            proc = self._proc
            if proc is None or proc.stdout is None:
                break
            chunk = proc.stdout.read(FRAME_SAMPLES * 2)
            if not chunk:
                if self._stop.is_set():
                    break
                log.warning("%s: audiotee exited, restarting in 1 s", self.name)
                time.sleep(1.0)
                try:
                    self._spawn()
                except Exception as exc:
                    log.error("%s: cannot restart audiotee: %s", self.name, exc)
                    break
                continue
            pcm = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
            slicer.push(pcm, self.clock())
        slicer.flush()

    def stop(self) -> None:
        self._stop.set()
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:  # pragma: no cover
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None


class FileSource:
    """Replay a sound file, paced at ``speed`` × real time (``speed=0`` = as fast as possible)."""

    def __init__(
        self,
        name: str,
        path: str | Path,
        speed: float = 1.0,
        channel_index: int | None = None,
        clock: Callable[[], float] | None = None,
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        self.name = name
        self.path = Path(path)
        self.speed = speed
        self.channel_index = channel_index
        self.clock = clock
        self.on_finished = on_finished
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.duration_s = 0.0

    def _read(self) -> tuple[np.ndarray, int]:
        import soundfile as sf

        try:
            data, rate = sf.read(str(self.path), dtype="float32", always_2d=True)
        except Exception:
            data, rate = self._read_with_ffmpeg()
        return _to_mono(data, self.channel_index), int(rate)

    def _read_with_ffmpeg(self) -> tuple[np.ndarray, int]:
        cmd = [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(self.path),
            "-f",
            "f32le",
            "-ac",
            "1",
            "-ar",
            str(TARGET_RATE),
            "-",
        ]
        res = subprocess.run(cmd, capture_output=True, check=True)
        return np.frombuffer(res.stdout, dtype=np.float32).reshape(-1, 1), TARGET_RATE

    def start(self, on_frames: OnFrames) -> None:
        data, rate = self._read()
        resampler = _Resampler(rate, TARGET_RATE)
        audio = resampler(data)
        self.duration_s = len(audio) / TARGET_RATE
        self._stop.clear()

        def run() -> None:
            slicer = _FrameSlicer(on_frames)
            started = time.monotonic()
            frame_dur = FRAME_SAMPLES / TARGET_RATE
            n = 0
            for i in range(0, len(audio), FRAME_SAMPLES):
                if self._stop.is_set():
                    break
                chunk = audio[i : i + FRAME_SAMPLES]
                if chunk.size < FRAME_SAMPLES:
                    chunk = np.concatenate([chunk, np.zeros(FRAME_SAMPLES - chunk.size, dtype=np.float32)])
                t = n * frame_dur
                slicer.push(chunk, t)
                n += 1
                if self.speed > 0:
                    target = started + (t + frame_dur) / self.speed
                    delay = target - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
            slicer.flush()
            if self.on_finished is not None and not self._stop.is_set():
                self.on_finished()

        self._thread = threading.Thread(target=run, name=f"file-{self.name}", daemon=True)
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None


def make_source(
    name: str,
    spec: str,
    *,
    channel_index: int | None = None,
    clock: Callable[[], float] | None = None,
    speed: float = 1.0,
    on_finished: Callable[[], None] | None = None,
) -> AudioSource:
    """Build a source from a config string: ``device:…``, ``audiotee:…`` or ``file:…``."""
    scheme, _, rest = spec.partition(":")
    scheme = scheme.strip().lower()
    rest = rest.strip()
    if scheme == "device":
        return DeviceSource(name, rest or None, channel_index=channel_index, clock=clock)
    if scheme == "audiotee":
        return AudioTeeSource(name, rest, clock=clock)
    if scheme == "file":
        return FileSource(
            name, rest, speed=speed, channel_index=channel_index, clock=clock, on_finished=on_finished
        )
    raise ValueError(f"unknown audio source {spec!r} (expected device:, audiotee: or file:)")
