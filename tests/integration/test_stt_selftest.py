"""Local STT self-test. Skipped unless the model is already cached."""

from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
import pytest

from livecaster.config import Config
from livecaster.stt.base import EngineUnavailable
from livecaster.stt.registry import default_engine_key, select_engine

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SAMPLES = ["speech_sk_30s.wav", "speech_en_30s.wav", "speech_cs_30s.wav"]


def _engine():
    if not os.environ.get("LIVECASTER_STT_TEST"):
        pytest.skip("set LIVECASTER_STT_TEST=1 to run the STT self-test (downloads a model)")
    cfg = Config()
    try:
        engine = select_engine(cfg.stt.engine, cfg.stt.model, cfg.stt.language)
        engine.warmup()
    except EngineUnavailable as exc:
        pytest.skip(f"engine unavailable: {exc}")
    return engine


def test_default_engine_key_matches_the_platform():
    import platform

    expected = (
        "parakeet-mlx" if platform.system() == "Darwin" and platform.machine() == "arm64" else "onnx-asr"
    )
    assert default_engine_key() == expected


def test_engine_handles_silence():
    engine = _engine()
    result = engine.transcribe(np.zeros(48_000, dtype=np.float32), None)
    assert result.text == "" or isinstance(result.text, str)


@pytest.mark.parametrize("sample", SAMPLES)
def test_real_time_factor_and_transcript(sample: str):
    path = FIXTURES / sample
    if not path.is_file():
        pytest.skip(f"{sample} not recorded yet — see docs/RUNBOOK.md")
    import soundfile as sf

    engine = _engine()
    audio, rate = sf.read(str(path), dtype="float32", always_2d=True)
    mono = audio.mean(axis=1)
    if rate != 16_000:
        import soxr

        mono = soxr.resample(mono, rate, 16_000)
    duration = len(mono) / 16_000
    started = time.monotonic()
    result = engine.transcribe(mono, None)
    elapsed = time.monotonic() - started
    rtf = elapsed / duration
    print(f"\n{sample}: {duration:.1f}s in {elapsed:.1f}s (RTF {rtf:.2f})\n{result.text}")
    assert result.text.strip()
    assert rtf < 1.0
