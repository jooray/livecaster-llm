"""Audio sources, resampling, recorder, device resolution (M4, M5)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from livecaster.audio.recorder import WavRecorder
from livecaster.audio.sources import (
    FRAME_SAMPLES,
    TARGET_RATE,
    AudioTeeSource,
    FileSource,
    _FrameSlicer,
    _Resampler,
    _to_mono,
    make_source,
)
from livecaster.audio.vad import EnergyVAD, make_vad


def test_frame_slicer_emits_fixed_frames_with_increasing_time():
    got: list[tuple[int, float]] = []
    slicer = _FrameSlicer(lambda frame, t: got.append((frame.size, t)))
    slicer.push(np.zeros(1300, dtype=np.float32), 5.0)
    assert [g[0] for g in got] == [FRAME_SAMPLES, FRAME_SAMPLES]
    assert got[0][1] == 5.0
    assert got[1][1] == pytest.approx(5.0 + FRAME_SAMPLES / TARGET_RATE)
    slicer.flush()
    assert len(got) == 3
    assert got[2][0] == FRAME_SAMPLES


def test_resampler_passthrough_and_downsample():
    block = np.ones(1600, dtype=np.float32)
    assert _Resampler(16_000, 16_000)(block).shape == block.shape
    out = _Resampler(48_000, 16_000)(np.ones(4800, dtype=np.float32))
    assert out.dtype == np.float32
    assert 1000 < out.size <= 1700


def test_to_mono():
    stereo = np.array([[1.0, 3.0], [2.0, 4.0]], dtype=np.float32)
    assert list(_to_mono(stereo, None)) == [2.0, 3.0]
    assert list(_to_mono(stereo, 1)) == [3.0, 4.0]
    assert list(_to_mono(stereo, 9)) == [3.0, 4.0]  # clamped
    mono = np.array([1.0, 2.0], dtype=np.float32)
    assert list(_to_mono(mono, 0)) == [1.0, 2.0]


def test_file_source_reads_a_wav(tmp_path: Path):
    path = tmp_path / "x.wav"
    sf.write(str(path), np.linspace(-0.5, 0.5, 8000, dtype=np.float32), 16_000)
    frames: list[np.ndarray] = []
    finished = []
    source = FileSource("Host", path, speed=0.0, on_finished=lambda: finished.append(True))
    source.start(lambda frame, t: frames.append(frame))
    source.join(10)
    source.stop()
    assert finished == [True]
    assert source.duration_s == pytest.approx(0.5, abs=0.01)
    assert len(frames) >= 15
    assert all(f.size == FRAME_SAMPLES for f in frames)


def test_file_source_resamples(tmp_path: Path):
    path = tmp_path / "x.wav"
    sf.write(str(path), np.zeros(48_000, dtype=np.float32), 48_000)
    source = FileSource("Host", path, speed=0.0)
    frames: list[np.ndarray] = []
    source.start(lambda frame, t: frames.append(frame))
    source.join(10)
    source.stop()
    assert source.duration_s == pytest.approx(1.0, abs=0.05)


def test_make_source_dispatches(tmp_path: Path):
    path = tmp_path / "x.wav"
    sf.write(str(path), np.zeros(1600, dtype=np.float32), 16_000)
    assert isinstance(make_source("Host", f"file:{path}"), FileSource)
    from livecaster.audio.sources import DeviceSource

    assert isinstance(make_source("Host", "device:default"), DeviceSource)
    assert isinstance(make_source("Guest", "audiotee:Google Chrome"), AudioTeeSource)
    with pytest.raises(ValueError, match="unknown audio source"):
        make_source("Host", "carrier-pigeon:x")


def test_audiotee_reports_a_missing_process():
    source = AudioTeeSource("Guest", "definitely-not-running-app-name", binary="/bin/true")
    with pytest.raises(RuntimeError, match="no running process"):
        source.resolve_pids()


def test_audiotee_accepts_a_pid():
    assert AudioTeeSource("Guest", "1234", binary="/bin/true").resolve_pids() == [1234]


def test_recorder_writes_and_suffixes(tmp_path: Path):
    recorder = WavRecorder(WavRecorder.unique_path(tmp_path, "host"))
    assert recorder.path.name == "host.wav"
    recorder.open()
    recorder.write(np.full(16_000, 0.2, dtype=np.float32))
    recorder.close()
    audio, rate = sf.read(str(recorder.path))
    assert rate == 16_000 and len(audio) == 16_000
    assert recorder.seconds == pytest.approx(1.0)

    second = WavRecorder(WavRecorder.unique_path(tmp_path, "host"))
    assert second.path.name == "host.2.wav"


def test_recorder_write_before_open_is_a_no_op(tmp_path: Path):
    WavRecorder(tmp_path / "x.wav").write(np.zeros(10, dtype=np.float32))


def test_energy_vad():
    vad = EnergyVAD(threshold=0.05)
    assert vad(np.zeros(512, dtype=np.float32)) == 0.0
    assert vad(np.full(512, 0.5, dtype=np.float32)) == 1.0


def test_make_vad_returns_something_callable():
    vad = make_vad()
    value = vad(np.zeros(512, dtype=np.float32))
    assert 0.0 <= value <= 1.0
    assert make_vad("energy").__class__ is EnergyVAD


def test_silero_vad_prefers_speech_over_silence():
    from livecaster.audio.vad import VAD

    try:
        vad = VAD()
    except Exception:  # pragma: no cover - onnxruntime or the model is missing
        pytest.skip("pysilero-vad unavailable")
    silence = vad(np.zeros(512, dtype=np.float32))
    noise = np.random.default_rng(0).normal(0, 0.3, 512).astype(np.float32)
    assert 0.0 <= silence <= 1.0
    assert 0.0 <= vad(noise) <= 1.0


def test_device_listing_does_not_raise():
    from livecaster.audio.devices import list_devices, resolve_device

    devices = list_devices()
    assert isinstance(devices, list)
    assert resolve_device(None) is None
    assert resolve_device("3") == 3
    with pytest.raises(ValueError):
        resolve_device("no-such-device-anywhere-12345")


def test_audiotee_candidates_shape():
    from livecaster.audio.devices import audiotee_candidates

    for candidate in audiotee_candidates():
        assert isinstance(candidate["name"], str)
        assert all(isinstance(p, int) for p in candidate["pids"])


def test_file_source_reports_a_missing_file(tmp_path: Path):
    source = FileSource("Host", tmp_path / "nope.wav", speed=0.0)
    with pytest.raises(FileNotFoundError, match="audio file not found"):
        source.start(lambda frame, t: None)


def test_file_source_reports_an_undecodable_file(tmp_path: Path):
    path = tmp_path / "broken.wav"
    path.write_bytes(b"this is not audio")
    source = FileSource("Host", path, speed=0.0)
    with pytest.raises(RuntimeError, match="cannot decode"):
        source.start(lambda frame, t: None)


def test_recorder_ensure_open_takes_the_real_rate(tmp_path: Path):
    recorder = WavRecorder(tmp_path / "native.wav", samplerate=16_000)
    recorder.ensure_open(48_000)
    recorder.ensure_open(8_000)  # already open, rate stays
    recorder.write(np.full(4800, 0.1, dtype=np.float32))
    recorder.close()
    audio, rate = sf.read(str(tmp_path / "native.wav"))
    assert rate == 48_000
    assert len(audio) == 4800
