"""Task 18: the local speech-to-text backend."""

import shutil
import subprocess
import sys
from types import SimpleNamespace as NS

import numpy as np
import pytest

from app.stt import (
    FasterWhisperBackend,
    SpeechToTextError,
    SpeechToTextUnavailable,
    Transcript,
    transcript_from_segments,
    validate_audio,
)


def test_import_is_lazy():
    # Run in a fresh interpreter: other tests may already have imported it.
    code = "import sys, app.stt; print('faster_whisper' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def seg(text, logprob=-0.3, no_speech=0.01, words=()):
    return NS(text=text, avg_logprob=logprob, no_speech_prob=no_speech,
              words=[NS(word=w, start=i * 0.3, end=i * 0.3 + 0.25, probability=0.9) for i, w in enumerate(words)])


def test_transcript_from_segments():
    t = transcript_from_segments([seg(" I want ", -0.2, 0.01, [" I", " want"]),
                                  seg(" water. ", -0.4, 0.05, [" water."])], inference_sec=0.5)
    assert t.text == "I want water."
    assert [w.text for w in t.words] == ["I", "want", "water."]
    assert t.avg_logprob == pytest.approx(-0.3) and t.no_speech_prob == 0.05
    assert t.inference_sec == 0.5 and not t.is_empty


def test_empty_transcript():
    t = transcript_from_segments([])
    assert t.is_empty and t.avg_logprob is None and t.no_speech_prob is None


@pytest.mark.parametrize("audio,sr", [(np.zeros(16000, np.float32), 8000),
                                      (np.zeros(0, np.float32), 16000),
                                      (np.zeros((2, 100), np.float32), 16000),
                                      (np.array([np.nan], np.float32), 16000)])
def test_validate_audio_rejects(audio, sr):
    with pytest.raises(SpeechToTextError):
        validate_audio(audio, sr)


def test_unavailable_without_package(monkeypatch):
    monkeypatch.setattr(FasterWhisperBackend, "package_installed", staticmethod(lambda: False))
    backend = FasterWhisperBackend()
    assert backend.is_available() is False


def test_load_failure_marks_unavailable(monkeypatch):
    if not FasterWhisperBackend.package_installed():
        pytest.skip("faster-whisper not installed")
    import faster_whisper

    def boom(*a, **k):
        raise RuntimeError("no snapshot on disk")

    monkeypatch.setattr(faster_whisper, "WhisperModel", boom)
    backend = FasterWhisperBackend(offline=True)
    with pytest.raises(SpeechToTextUnavailable):
        backend.load()
    assert backend.is_available() is False


def test_transcribe_with_fake_model():
    backend = FasterWhisperBackend()
    calls = []

    class FakeModel:
        def transcribe(self, audio, **kwargs):
            calls.append(kwargs)
            return iter([seg("hello there", words=["hello", "there"])]), None

    backend._model = FakeModel()
    t = backend.transcribe(np.ones(16000, np.float32) * 0.1, 16000)
    assert t.text == "hello there" and t.inference_sec >= 0
    assert calls[0]["language"] == "en" and calls[0]["word_timestamps"] is True


def test_settings_wiring(monkeypatch, data_dir):
    from app.config import get_settings
    from app.deps import get_stt_backend

    monkeypatch.setenv("STT_MODEL_SIZE", "tiny.en")
    get_settings.cache_clear()
    get_stt_backend.cache_clear()
    backend = get_stt_backend()
    assert backend.model_size == "tiny.en" and backend.is_loaded is False


# -- health ----------------------------------------------------------------

def test_health_reports_stt(client):
    stt = client.get("/api/health").json()["checks"]["stt"]
    assert stt["ok"] is True and stt["enabled"] is False and stt["model"] == "base.en"
    assert "installed" in stt


def test_health_stt_missing_does_not_degrade(monkeypatch):
    from app.services.health import HealthChecker
    import importlib.util as iu

    real = iu.find_spec
    monkeypatch.setattr(iu, "find_spec", lambda name, *a: None if name == "faster_whisper" else real(name, *a))
    check = HealthChecker(NS(STT_ENABLED=True, STT_MODEL_SIZE="base.en")).check_stt()
    assert check == {"ok": True, "enabled": True, "installed": False, "model": "base.en",
                     "note": "not installed: make install-stt"}


# -- real model (opt-in by environment) -------------------------------------

def _real_model_or_skip():
    if not FasterWhisperBackend.package_installed():
        pytest.skip("faster-whisper not installed (make install-stt)")
    if shutil.which("say") is None:
        pytest.skip("needs macOS `say` to synthesize speech")
    backend = FasterWhisperBackend("base.en", offline=True)
    try:
        backend.load()
    except SpeechToTextUnavailable:
        pytest.skip("base.en not downloaded (scripts/download_models.py --stt)")
    return backend


def test_real_transcription(tmp_path):
    backend = _real_model_or_skip()
    from app.services.audio import AudioPreprocessor

    aiff = tmp_path / "s.aiff"
    subprocess.run(["say", "-o", str(aiff), "I want water"], check=True)
    processed = AudioPreprocessor(20, 0.3, 10).process(aiff.read_bytes(), "audio/aiff")
    t = backend.transcribe(processed.samples, processed.sr)
    assert "water" in t.text.lower()
    assert t.words and t.no_speech_prob < 0.5
    assert t.inference_sec < 5


def test_hotwords_hint_is_a_short_deduplicated_word_list():
    from app.stt import MAX_PROMPT_CHARS, build_prompt

    assert build_prompt(["Satvik", "satvik", " wawa ", ""]) == "Satvik, wawa"
    assert build_prompt([]) is None
    assert len(build_prompt([f"word{i}" for i in range(200)])) <= MAX_PROMPT_CHARS
