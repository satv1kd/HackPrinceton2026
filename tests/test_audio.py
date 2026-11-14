import io
import time

import numpy as np
import pytest
import soundfile as sf

from app.services.audio import (
    AudioPreprocessor,
    AudioProcessingError,
    AudioTooShortError,
    ext_for_mime,
)
from tests.conftest import encode, tone, wav_bytes, with_silence


@pytest.fixture
def prep():
    return AudioPreprocessor(trim_top_db=20, min_clip_sec=0.3, max_clip_sec=10.0)


def _read(wav: bytes):
    info = sf.info(io.BytesIO(wav))
    audio, sr = sf.read(io.BytesIO(wav), dtype="float32")
    return info, audio, sr


@pytest.mark.parametrize(
    "fmt,mime",
    [("webm", "audio/webm;codecs=opus"), ("mp4", "audio/mp4")],
    ids=["chrome-webm-opus", "safari-mp4-aac"],
)
def test_browser_formats_become_16k_mono_wav(prep, fmt, mime):
    raw = encode(tone(1.0, sr=48000), fmt, sr=48000)
    out = prep.process(raw, mime)
    info, audio, sr = _read(out.wav_bytes)
    assert (info.samplerate, info.channels, info.subtype) == (16000, 1, "PCM_16")
    assert out.sr == 16000 and out.samples.ndim == 1
    assert 0.8 < out.duration_sec < 1.2


def test_unknown_mime_still_decodes(prep):
    out = prep.process(encode(tone(1.0, sr=48000), "webm"), None)
    assert out.sr == 16000


def test_silence_is_trimmed(prep):
    raw = wav_bytes(with_silence(tone(1.0), before=2.0, after=2.0))
    out = prep.process(raw, "audio/wav")
    assert 0.9 < out.duration_sec < 1.2  # 5s in, ~1s of speech out


def test_too_short_is_rejected(prep):
    with pytest.raises(AudioTooShortError):
        prep.process(wav_bytes(with_silence(tone(0.1), 0.5, 0.5)), "audio/wav")


def test_pure_silence_is_rejected(prep):
    with pytest.raises(AudioTooShortError):
        prep.process(wav_bytes(np.zeros(16000, np.float32)), "audio/wav")


def test_long_clip_is_capped(prep):
    out = prep.process(wav_bytes(tone(12.0)), "audio/wav")
    assert out.truncated
    assert out.duration_sec == pytest.approx(10.0, abs=0.01)


def test_peak_normalized(prep):
    out = prep.process(wav_bytes(tone(1.0, amp=0.05)), "audio/wav")
    _, audio, _ = _read(out.wav_bytes)
    assert np.max(np.abs(audio)) == pytest.approx(0.99, abs=0.01)


@pytest.mark.parametrize("payload", [b"", b"this is not audio at all" * 10])
def test_corrupt_input_raises(prep, payload):
    with pytest.raises(AudioProcessingError):
        prep.process(payload, "audio/webm")


def test_ext_for_mime():
    assert ext_for_mime("audio/webm;codecs=opus") == ".webm"
    assert ext_for_mime("audio/mp4") == ".m4a"
    assert ext_for_mime(None) == ".bin"


def test_processing_is_fast(prep):
    raw = encode(tone(3.0, sr=48000), "mp4")
    prep.process(raw, "audio/mp4")  # warm up librosa/numba
    start = time.perf_counter()
    prep.process(raw, "audio/mp4")
    assert time.perf_counter() - start < 0.5
