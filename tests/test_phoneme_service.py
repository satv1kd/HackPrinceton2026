"""Task 25: the shared phoneme service and its calibration hook."""

import shutil
import subprocess
from functools import lru_cache

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.phonetics.enrollment import load_enrollment_set
from app.phonetics.recognizer import BackendResult, ModelUnavailableError, PhonemeRecognizer
from app.phonetics.service import PhonemeService, attempt_cache_id, model_in_cache, take_cache_id
from tests.conftest import tone, wav_bytes


class FakeBackend:
    """Says the IPA it is told to; records how often it ran."""

    name = "fake/model"

    def __init__(self, ipa="aɪ w ɑ n t", fail=False):
        self.ipa, self.fail, self.calls, self.is_loaded = ipa, fail, 0, False

    def load(self):
        if self.fail:
            raise ModelUnavailableError("weights missing. Download them")
        self.is_loaded = True

    def transcribe(self, samples, sample_rate):
        self.load()
        self.calls += 1
        return BackendResult(raw_text=self.ipa, posteriors=np.full((10, 3), 1 / 3, np.float32),
                             posterior_labels=["<pad>", "a", "b"], frame_rate_hz=50.0)


def service(tmp_path, backend=None, **kw):
    rec = PhonemeRecognizer(backend=backend or FakeBackend(), cache_dir=tmp_path / "ph", model_name="fake/model")
    return PhonemeService(rec, "fake/model", packages_installed=lambda: True,
                          model_present=lambda: True, **kw)


def test_recognize_samples_maps_to_canonical_and_caches(tmp_path):
    svc = service(tmp_path)
    result = svc.recognize_samples(np.zeros(16000, np.float32), 16000, cache_id="attempt-x")
    assert result.phonemes == ["AY", "W", "AA", "N", "T"]
    assert svc.cached("attempt-x").phonemes == result.phonemes
    svc.forget("attempt-x")
    assert svc.cached("attempt-x") is None
    svc.shutdown()


def test_wrong_rate_rejected(tmp_path):
    svc = service(tmp_path)
    with pytest.raises(Exception):
        svc.recognize_samples(np.zeros(8000, np.float32), 8000)
    svc.shutdown()


def test_async_file_recognition_replaces_cache(tmp_path):
    backend = FakeBackend()
    svc = service(tmp_path, backend)
    wav = tmp_path / "t.wav"
    wav.write_bytes(wav_bytes(tone(1.0)))
    done = []
    svc.recognize_file_async(wav, "cal-x-t1", on_done=done.append).result(timeout=10)
    backend.ipa = "h ə l oʊ"
    svc.recognize_file_async(wav, "cal-x-t1").result(timeout=10)   # re-recorded take
    assert done[0].phonemes == ["AY", "W", "AA", "N", "T"]
    assert svc.cached("cal-x-t1").phonemes == ["HH", "AH", "L", "OW"]
    svc.shutdown()


def test_unavailable_reasons_in_order(tmp_path):
    rec = PhonemeRecognizer(backend=FakeBackend(), cache_dir=tmp_path)
    assert "turned off" in PhonemeService(rec, "m", lambda: True, lambda: True, enabled=False).unavailable_reason()
    assert "isn't installed" in PhonemeService(rec, "m", lambda: False, lambda: True).unavailable_reason()
    assert "isn't downloaded" in PhonemeService(rec, "m", lambda: True, lambda: False).unavailable_reason()
    ok = PhonemeService(rec, "m", lambda: True, lambda: True)
    assert ok.available() and ok.recognize_file_async(tmp_path / "missing.wav", "x") is not None
    ok.shutdown()


def test_a_failed_load_makes_it_unavailable(tmp_path):
    svc = service(tmp_path, FakeBackend(fail=True))
    with pytest.raises(ModelUnavailableError):
        svc.recognize_samples(np.zeros(16000, np.float32), 16000)
    assert "failed to load" in svc.unavailable_reason()
    svc.shutdown()


def test_warm_up_loads_in_background(tmp_path):
    backend = FakeBackend()
    svc = service(tmp_path, backend)
    svc.warm_up().result(timeout=10)
    assert svc.is_loaded and svc.warm_up() is None
    svc.shutdown()


def test_ids():
    assert take_cache_id("i-want-water", 2) == "cal-i-want-water-t2"
    assert attempt_cache_id("abc") == "attempt-abc"
    assert model_in_cache("facebook/definitely-not-a-real-model-xyz") is False


def test_calibration_upload_queues_recognition(data_dir, tmp_path):
    import app.deps as deps
    from app.main import app

    backend = FakeBackend()
    svc = service(tmp_path, backend)
    phrase = load_enrollment_set()[0]
    app.dependency_overrides[deps.get_phoneme_service] = lambda: svc
    try:
        with TestClient(app) as client:
            r = client.post("/api/calibration/sentences",
                            data={"sentence_id": phrase.id, "text": phrase.text, "take": "2"},
                            files={"audio": ("t.wav", wav_bytes(tone(1.2)), "audio/wav")})
            assert r.status_code == 200, r.text
            svc.wait_idle()
            assert svc.cached(take_cache_id(phrase.id, 2)) is not None
            assert client.delete(f"/api/calibration/sentences/{phrase.id}?take=2").status_code == 204
            assert svc.cached(take_cache_id(phrase.id, 2)) is None
    finally:
        app.dependency_overrides.pop(deps.get_phoneme_service, None)
        svc.shutdown()


def test_health_reports_the_engine(client):
    check = client.get("/api/health").json()["checks"]["phoneme_model"]
    assert check["ok"] is True
    if check["installed"]:
        assert check["available"] is False and "turned off" in check["note"]


# -- the real model (opt-in by what is installed) -------------------------------

def test_real_model_recognizes_synthesized_speech(tmp_path, monkeypatch):
    if shutil.which("say") is None:
        pytest.skip("needs macOS say")
    from app.config import get_settings
    monkeypatch.setenv("PHONEMES_ENABLED", "true")
    get_settings.cache_clear()
    svc = PhonemeService.from_settings(get_settings())
    if not svc.available():
        pytest.skip(svc.unavailable_reason())
    from app.services.audio import AudioPreprocessor
    aiff = tmp_path / "s.aiff"
    subprocess.run(["say", "-o", str(aiff), "Thank you very much"], check=True)
    processed = AudioPreprocessor(20, 0.3, 10).process(aiff.read_bytes(), "audio/aiff")
    svc.recognizer.cache_dir = tmp_path / "ph"
    result = svc.recognize_samples(processed.samples, processed.sr, cache_id="real")
    assert result.phonemes[:2] == ["TH", "AE"] and "CH" in result.phonemes
    assert result.posteriors is not None and result.frame_rate_hz > 30
    svc.shutdown()
    get_settings.cache_clear()
