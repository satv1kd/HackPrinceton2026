"""Task 16: settings consolidation and the phonetic-mode preference API."""

import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.config import Settings
from app.services.preferences import PhoneticReadiness, Preferences, PreferenceStore


def test_new_settings_defaults(monkeypatch):
    for key in ("STT_ENABLED", "PHONETIC_MODE_DEFAULT", "PHONEME_CACHE_DIR", "DATA_DIR"):
        monkeypatch.delenv(key, raising=False)
    s = Settings(_env_file=None)
    assert s.STT_ENABLED is True and s.STT_MODEL_SIZE == "base.en"
    assert s.STT_PHRASE_MATCH_MIN == 85.0 and s.PHONETIC_MODE_DEFAULT is False
    assert s.RETRAIN_MIN_NEW_EXAMPLES == 10 and s.MAX_DECODE_WORDS == 6
    assert s.phoneme_cache_dir == s.DATA_DIR / "phonemes"


def test_new_settings_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("STT_MODEL_SIZE", "tiny.en")
    monkeypatch.setenv("PHONETIC_MODE_DEFAULT", "true")
    monkeypatch.setenv("PHONEME_CACHE_DIR", str(tmp_path / "ph"))
    s = Settings(_env_file=None)
    assert s.STT_MODEL_SIZE == "tiny.en" and s.PHONETIC_MODE_DEFAULT is True
    assert s.phoneme_cache_dir == tmp_path / "ph"


def test_settings_bridge_is_gone():
    with pytest.raises(ImportError):
        __import__("app.settings_bridge")


def test_recognizer_reads_settings(monkeypatch, data_dir):
    from app.config import get_settings
    from app.phonetics.recognizer import PhonemeRecognizer

    monkeypatch.setenv("PHONEME_MODEL", "some/other-model")
    get_settings.cache_clear()
    rec = PhonemeRecognizer(backend=object())
    assert rec.model_name == "some/other-model"
    assert rec.cache_dir == data_dir / "phonemes"


# -- store -----------------------------------------------------------------

def test_missing_file_uses_defaults(tmp_path):
    store = PreferenceStore(tmp_path, Preferences(phonetic_mode=True))
    assert store.get().phonetic_mode is True
    assert not store.path.exists()


def test_update_persists(tmp_path):
    PreferenceStore(tmp_path).update(phonetic_mode=True)
    assert json.loads((tmp_path / "preferences.json").read_text()) == {"phonetic_mode": True}
    assert PreferenceStore(tmp_path).get().phonetic_mode is True


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    (tmp_path / "preferences.json").write_text("{not json")
    assert PreferenceStore(tmp_path).get().phonetic_mode is False


def test_failed_write_keeps_old_file(tmp_path, monkeypatch):
    store = PreferenceStore(tmp_path)
    store.update(phonetic_mode=True)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.update(phonetic_mode=False)
    assert json.loads(store.path.read_text()) == {"phonetic_mode": True}


def test_concurrent_updates_never_corrupt(tmp_path):
    store = PreferenceStore(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: store.update(phonetic_mode=bool(i % 2)), range(50)))
    assert isinstance(json.loads(store.path.read_text())["phonetic_mode"], bool)
    assert not list(tmp_path.glob("*.tmp"))


# -- readiness -------------------------------------------------------------

def test_readiness_default_not_built():
    state = PhoneticReadiness().evaluate()
    assert state.ready is False and "isn't built" in state.reason


def test_readiness_first_failing_reason_wins():
    r = PhoneticReadiness([lambda: None, lambda: "Record the sound set first", lambda: "later"])
    assert r.evaluate().reason == "Record the sound set first"
    assert PhoneticReadiness([lambda: None]).evaluate().ready is True


def test_readiness_broken_check_is_not_ready():
    def broken():
        raise RuntimeError("boom")

    assert PhoneticReadiness([broken]).evaluate().ready is False


# -- API -------------------------------------------------------------------

def test_api_get_and_put(client, data_dir):
    r = client.get("/api/preferences")
    assert r.status_code == 200
    assert r.json() == {
        "phonetic_mode": False,
        "phonetic_available": False,
        "phonetic_unavailable_reason": "The sound-learning engine isn't built yet.",
    }
    r = client.put("/api/preferences", json={"phonetic_mode": True})
    assert r.status_code == 200 and r.json()["phonetic_mode"] is True
    assert r.json()["phonetic_available"] is False  # stored, but not ready
    assert json.loads((data_dir / "preferences.json").read_text())["phonetic_mode"] is True
    assert client.get("/api/preferences").json()["phonetic_mode"] is True


@pytest.mark.parametrize("body", [{"phonetic_mode": "yes please"}, {"other": True}])
def test_api_rejects_bad_bodies(client, body):
    assert client.put("/api/preferences", json=body).status_code == 422


def test_api_default_from_settings(monkeypatch, data_dir):
    from fastapi.testclient import TestClient

    from app.deps import reset_caches
    from app.main import app

    monkeypatch.setenv("PHONETIC_MODE_DEFAULT", "true")
    reset_caches()
    with TestClient(app) as c:
        assert c.get("/api/preferences").json()["phonetic_mode"] is True
