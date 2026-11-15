"""Tests for the phoneme recognizer module (Task 15).

These run without ``torch``/``transformers``: the model sits behind the
:class:`PhonemeBackend` protocol and is replaced with a fake here, so caching,
symbol mapping and the audio contract are all tested without a 1 GB download.
A real-model smoke test lives in ``scripts/measure_phoneme_latency.py``.
"""

from __future__ import annotations

import json
import wave

import numpy as np
import pytest

from app.phonetics import mapping
from app.phonetics.recognizer import (
    BackendResult,
    PhonemeRecognizer,
    PhonemeRecognizerError,
    read_pcm_wav,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def write_wav(path, samples=None, rate=16_000, channels=1, width=2):
    """Write a WAV file, deliberately allowing malformed ones for tests."""
    if samples is None:
        duration = 0.5
        t = np.linspace(0, duration, int(rate * duration), endpoint=False)
        samples = (0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(samples.tobytes())
    return path


class FakeBackend:
    """Stands in for the wav2vec2 checkpoint."""

    def __init__(self, raw_text="aɪ wɑnt wɔːtɚ", name="fake-model", frames=8, vocab=4):
        self.name = name
        self.raw_text = raw_text
        self.calls = 0
        self._frames = frames
        self._vocab = vocab

    def transcribe(self, samples, sample_rate):
        self.calls += 1
        posteriors = np.full((self._frames, self._vocab), 0.25, dtype=np.float32)
        posteriors[0, 0] = 0.9
        return BackendResult(
            raw_text=self.raw_text,
            posteriors=posteriors,
            posterior_labels=[f"tok{i}" for i in range(self._vocab)],
            frame_rate_hz=self._frames / (len(samples) / sample_rate),
        )


@pytest.fixture
def wav(tmp_path):
    return write_wav(tmp_path / "clip.wav")


@pytest.fixture
def recognizer(tmp_path):
    return PhonemeRecognizer(backend=FakeBackend(), cache_dir=tmp_path / "phonemes")


# ---------------------------------------------------------------------------
# Audio contract (Task 3's output)
# ---------------------------------------------------------------------------

def test_reads_16k_mono_16bit_wav(wav):
    samples, rate = read_pcm_wav(wav)
    assert rate == 16_000
    assert samples.dtype == np.float32
    assert len(samples) == 8_000
    assert -1.0 <= samples.min() and samples.max() < 1.0


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"channels": 2}, "channels"),
        ({"width": 1}, "8-bit"),
        ({"rate": 44_100}, "44100 Hz"),
    ],
)
def test_rejects_audio_that_skipped_preprocessing(tmp_path, kwargs, expected):
    """Anything not 16 kHz mono 16-bit means Task 3 was bypassed."""
    path = write_wav(tmp_path / "bad.wav", **kwargs)
    with pytest.raises(PhonemeRecognizerError) as excinfo:
        read_pcm_wav(path)
    assert expected in str(excinfo.value)
    assert "Task 3" in str(excinfo.value)


def test_missing_and_non_wav_files_raise_clearly(tmp_path):
    with pytest.raises(PhonemeRecognizerError, match="no such audio file"):
        read_pcm_wav(tmp_path / "absent.wav")
    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"not a wav at all")
    with pytest.raises(PhonemeRecognizerError, match="not a readable WAV"):
        read_pcm_wav(junk)


# ---------------------------------------------------------------------------
# Recognition and mapping
# ---------------------------------------------------------------------------

def test_output_is_canonical_phonemes(recognizer, wav):
    result = recognizer.recognize(wav, cache_id="s1")
    assert result.phonemes == ["AY", "W", "AA", "N", "T", "W", "AO", "T", "ER"]
    assert result.raw_phonemes == ["aɪ", "w", "ɑ", "n", "t", "w", "ɔː", "t", "ɚ"]


def test_non_english_symbols_are_approximated_and_unknown_ones_reported(tmp_path, wav):
    """The model is multilingual. A non-English phone maps to the nearest
    English one (revision 2); a symbol with no approximation is dropped but
    *reported* on the result and in the cache, never silently - a silent drop
    would look like the speaker deleting a sound."""
    rec = PhonemeRecognizer(backend=FakeBackend(raw_text="hɛɮoʊ ☃"), cache_dir=tmp_path / "phonemes")
    result = rec.recognize(wav, cache_id="s1")
    assert result.phonemes == ["HH", "EH", "L", "OW"]
    assert result.dropped == ["☃"]
    assert rec.recognize(wav, cache_id="s1").dropped == ["☃"]          # from the cache


def test_latency_and_duration_are_reported(recognizer, wav):
    result = recognizer.recognize(wav)
    assert result.audio_duration_sec == pytest.approx(0.5)
    assert result.inference_sec >= 0.0
    assert result.real_time_factor == pytest.approx(result.inference_sec / 0.5)


def test_no_cache_id_means_no_cache_files(recognizer, wav):
    recognizer.recognize(wav)
    assert not recognizer.cache_dir.exists() or not list(recognizer.cache_dir.iterdir())


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

def test_second_call_hits_the_cache(recognizer, wav):
    first = recognizer.recognize(wav, cache_id="s1")
    second = recognizer.recognize(wav, cache_id="s1")
    assert recognizer.backend.calls == 1
    assert second.from_cache is True
    assert first.from_cache is False
    assert second.phonemes == first.phonemes


def test_posteriors_round_trip_through_npy(recognizer, wav):
    first = recognizer.recognize(wav, cache_id="s1")
    second = recognizer.recognize(wav, cache_id="s1")
    assert first.posteriors is not None
    np.testing.assert_array_equal(second.posteriors, first.posteriors)
    assert second.posterior_labels == first.posterior_labels
    assert second.frame_rate_hz == pytest.approx(first.frame_rate_hz)


def test_cache_layout_matches_the_prd(recognizer, wav):
    recognizer.recognize(wav, cache_id="attempt-123")
    assert (recognizer.cache_dir / "attempt-123.json").exists()
    assert (recognizer.cache_dir / "attempt-123.posteriors.npy").exists()
    assert not list(recognizer.cache_dir.glob("*.tmp"))  # atomic write cleaned up


def test_want_posteriors_false_skips_them(recognizer, wav):
    result = recognizer.recognize(wav, cache_id="s1", want_posteriors=False)
    assert result.posteriors is None
    assert not (recognizer.cache_dir / "s1.posteriors.npy").exists()


def test_use_cache_false_recomputes(recognizer, wav):
    recognizer.recognize(wav, cache_id="s1")
    recognizer.recognize(wav, cache_id="s1", use_cache=False)
    assert recognizer.backend.calls == 2


def test_a_different_model_invalidates_the_cache(tmp_path, wav):
    cache = tmp_path / "phonemes"
    PhonemeRecognizer(backend=FakeBackend(name="model-a"), cache_dir=cache).recognize(
        wav, cache_id="s1"
    )
    newer = FakeBackend(name="model-b")
    result = PhonemeRecognizer(backend=newer, cache_dir=cache).recognize(wav, cache_id="s1")
    assert newer.calls == 1
    assert result.from_cache is False
    assert result.model_name == "model-b"


def test_a_mapping_revision_bump_invalidates_the_cache(recognizer, wav, monkeypatch):
    """A corrected mapping must not leave wrongly-mapped phonemes in the cache."""
    recognizer.recognize(wav, cache_id="s1")
    monkeypatch.setattr(mapping, "MAPPING_REVISION", mapping.MAPPING_REVISION + 1)
    result = recognizer.recognize(wav, cache_id="s1")
    assert recognizer.backend.calls == 2
    assert result.from_cache is False


def test_corrupt_cache_metadata_is_discarded_not_fatal(recognizer, wav):
    recognizer.recognize(wav, cache_id="s1")
    (recognizer.cache_dir / "s1.json").write_text("{ not json")
    result = recognizer.recognize(wav, cache_id="s1")
    assert recognizer.backend.calls == 2
    assert result.phonemes[0] == "AY"


def test_missing_posteriors_file_forces_recompute(recognizer, wav):
    recognizer.recognize(wav, cache_id="s1")
    (recognizer.cache_dir / "s1.posteriors.npy").unlink()
    recognizer.recognize(wav, cache_id="s1")
    assert recognizer.backend.calls == 2


def test_clear_cache_removes_both_files(recognizer, wav):
    recognizer.recognize(wav, cache_id="s1")
    recognizer.clear_cache("s1")
    assert not (recognizer.cache_dir / "s1.json").exists()
    assert not (recognizer.cache_dir / "s1.posteriors.npy").exists()
    recognizer.clear_cache("s1")  # idempotent


def test_cached_metadata_is_human_readable(recognizer, wav):
    recognizer.recognize(wav, cache_id="s1")
    metadata = json.loads((recognizer.cache_dir / "s1.json").read_text())
    assert metadata["model_name"] == "fake-model"
    assert metadata["mapping_revision"] == mapping.MAPPING_REVISION
    assert metadata["phonemes"][0] == "AY"
    assert metadata["has_posteriors"] is True


# ---------------------------------------------------------------------------
# Health reporting (feeds /api/health in Task 1)
# ---------------------------------------------------------------------------

def test_is_model_available_true_for_an_injected_backend(recognizer):
    assert recognizer.is_model_available() is True


def test_is_model_available_false_when_the_model_cannot_load(tmp_path):
    from app.phonetics.recognizer import ModelUnavailableError

    class Broken:
        name = "broken"

        def load(self):
            raise ModelUnavailableError("weights not downloaded")

        def transcribe(self, samples, sample_rate):  # pragma: no cover
            raise AssertionError("should not be reached")

    assert PhonemeRecognizer(backend=Broken(), cache_dir=tmp_path).is_model_available() is False


def test_importing_the_module_does_not_require_torch():
    """Task 13's send proxy and Task 17's checker import this package; neither
    should pay for a multi-gigabyte dependency."""
    import sys

    assert "torch" not in sys.modules
    assert "transformers" not in sys.modules
