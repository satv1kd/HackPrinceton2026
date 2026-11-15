"""Task 27: the personal sound profile."""

import math
from types import SimpleNamespace as NS

import pytest

from app.phonetics.aligner import align
from app.phonetics.enrollment import load_enrollment_set
from app.phonetics.inventory import DELETION, PHONEMES
from app.phonetics.profile import PRIOR, OUTCOMES, ProfileStore, SoundProfile
from app.phonetics.service import take_cache_id
from app.phonetics.training import ProfileTrainer, TrainingExample, build_profile, profile_readiness


def test_prior_rows_are_distributions_that_favour_identity():
    for p in PHONEMES:
        row = PRIOR[p]
        assert sum(row.values()) == pytest.approx(1.0)
        assert row[p] == max(row.values()) and all(v > 0 for v in row.values())
    assert PRIOR["L"]["W"] > PRIOR["L"]["K"]          # similar sounds get more prior mass


def test_learned_substitution():
    # This person's /r/ comes out as [w] every time.
    alignments = [align(["R", "EH", "D"], ["W", "EH", "D"]) for _ in range(6)]
    profile = SoundProfile.build(alignments, alpha=1.0)
    assert profile.probability("R", "W") > 0.8 > profile.probability("R", "R")
    assert profile.cost("R", "W") < profile.cost("R", "R")
    # Untouched sounds stay near the prior.
    assert profile.probability("S", "S") == pytest.approx(PRIOR["S"]["S"])


def test_rows_are_distributions_and_never_zero():
    profile = SoundProfile.build([align(["K", "AE", "T"], ["K", "AE"])] * 3, alpha=1.0)
    for p in PHONEMES:
        total = sum(profile.probability(p, r) for r in OUTCOMES)
        assert total == pytest.approx(1.0)
        assert min(profile.probability(p, r) for r in OUTCOMES) > 0
    assert profile.probability("T", DELETION) > 0.5      # final consonant deletion learned
    assert math.isfinite(profile.insertion_cost("AH"))


def test_alpha_controls_how_fast_data_wins():
    data = [align(["R"], ["W"])] * 2
    weak, strong = SoundProfile.build(data, alpha=0.5), SoundProfile.build(data, alpha=20)
    assert weak.probability("R", "W") > strong.probability("R", "W")


def test_top_confusions():
    profile = SoundProfile.build([align(["R", "IY"], ["W", "IY"])] * 4)
    top = profile.top_confusions()
    assert top[0]["intended"] == "R" and top[0]["heard"] == "W" and top[0]["examples"] == 4


def test_store_versions_and_round_trip(tmp_path):
    store = ProfileStore(tmp_path)
    assert store.load() is None and store.current_version() is None
    first = store.save(SoundProfile.build([align(["R"], ["W"])]))
    second = store.save(SoundProfile.build([align(["S"], ["S"])]))
    assert (first.version, second.version, store.current_version()) == (1, 2, 2)
    store.activate(1)
    assert store.load().probability("R", "W") == pytest.approx(first.probability("R", "W"))
    assert store.versions() == [1, 2]
    with pytest.raises(FileNotFoundError):
        store.activate(9)


def test_readiness():
    assert "calibration phrases" in profile_readiness(None, 2)
    thin = build_profile([TrainingExample("take", "k", ["S", "IY"], ["S", "IY"])], alpha=1)
    assert "still need examples" in profile_readiness(thin, 2)
    phrases = load_enrollment_set()
    full = build_profile([TrainingExample("take", p.id, p.phonemes, p.phonemes)
                          for p in phrases for _ in range(2)], alpha=1)
    assert profile_readiness(full, 2) is None


class FakeCalibration:
    def __init__(self, records):
        self.records = records

    def list_sentences(self):
        return self.records


class FakePhonemes:
    def __init__(self, heard):
        self.heard = heard

    def cached(self, cache_id, want_posteriors=False):
        phonemes = self.heard.get(cache_id)
        return NS(phonemes=phonemes) if phonemes else None


def test_trainer_builds_from_enrollment_takes_only(tmp_path):
    phrase = load_enrollment_set()[0]
    heard = [("W" if p == "R" else p) for p in phrase.phonemes]
    records = [{"id": phrase.id, "take": 1}, {"id": phrase.id, "take": 2},
               {"id": "free-text", "take": 1}, {"id": load_enrollment_set()[1].id, "take": 1}]
    phonemes = FakePhonemes({take_cache_id(phrase.id, 1): heard, take_cache_id(phrase.id, 2): heard,
                             take_cache_id("free-text", 1): ["AH"]})
    trainer = ProfileTrainer(tmp_path, FakeCalibration(records), phonemes, alpha=1.0, min_occurrences=2)
    profile = trainer.rebuild()
    assert profile.sources == {"take": 2}            # free text and unrecognized takes skipped
    assert trainer.current().version == 1
    assert trainer.readiness() is not None           # one phrase is not enough


def test_trainer_with_no_data(tmp_path):
    trainer = ProfileTrainer(tmp_path, FakeCalibration([]), FakePhonemes({}), 1.0, 2)
    assert trainer.rebuild() is None and trainer.current() is None


def test_profile_api(client):
    body = client.get("/api/profile").json()
    assert body["ready"] is False and body["version"] is None
    assert len(body["sounds"]) == 39 and body["sounds"][0]["example"]
    assert body["sounds"][0]["status"] == "not_yet"
    assert client.post("/api/profile/rebuild").status_code == 200


def _tone_take(record, calibration_store, phoneme_service):
    """Stands in for training.take_segments: three synthetic sounds per take."""
    import numpy as np

    t = np.arange(4800) / 16000
    audio = np.concatenate([(0.3 * np.sin(2 * np.pi * f * t)).astype(np.float32) for f in (200, 800, 2000)])
    segs = [NS(phoneme=p, start_sec=a, end_sec=a + 0.3, confidence=0.9)
            for p, a in (("S", 0.0), ("IY", 0.3), ("T", 0.6))]
    return take_cache_id(record["id"], record["take"]), audio, 16000, segs


def test_take_changes_are_batched_and_the_bank_updated_in_place(tmp_path, monkeypatch):
    from app.phonetics import training

    phrase = load_enrollment_set()[0]
    records = [{"id": phrase.id, "take": 1}, {"id": phrase.id, "take": 2}]
    ids = [take_cache_id(phrase.id, 1), take_cache_id(phrase.id, 2)]
    phonemes = FakePhonemes({i: list(phrase.phonemes) for i in ids})
    monkeypatch.setattr(training, "take_segments", _tone_take)
    trainer = ProfileTrainer(tmp_path, FakeCalibration(records), phonemes, 1.0, 2, refresh_delay=60)

    for cache_id in ids:
        trainer.take_changed(cache_id)
    assert trainer.current() is None                     # waiting for the burst to end
    assert trainer.flush().sources == {"take": 2}
    assert ProfileStore(tmp_path).versions() == [1]      # two saves, one refresh
    assert trainer.sound_bank().coverage() == {"S": 2, "IY": 2, "T": 2}

    # Deleting a take updates the saved bank without rebuilding it from every take.
    monkeypatch.setattr(training, "build_sound_bank", lambda *a: pytest.fail("full rebuild"))
    records.pop()
    phonemes.heard.pop(ids[1])
    trainer.take_changed(ids[1])
    trainer.flush()
    trainer._bank = None                                  # read it back from disk
    assert trainer.sound_bank().coverage() == {"S": 1, "IY": 1, "T": 1}
    assert trainer.flush() is None                        # nothing pending


def test_take_changes_refresh_on_their_own(tmp_path, monkeypatch):
    import time

    from app.phonetics import training

    phrase = load_enrollment_set()[0]
    cache_id = take_cache_id(phrase.id, 1)
    monkeypatch.setattr(training, "take_segments", _tone_take)
    trainer = ProfileTrainer(tmp_path, FakeCalibration([{"id": phrase.id, "take": 1}]),
                             FakePhonemes({cache_id: list(phrase.phonemes)}), 1.0, 2, refresh_delay=0.05)
    trainer.take_changed(cache_id)
    for _ in range(100):
        if trainer.current() is not None:
            break
        time.sleep(0.02)
    assert trainer.current().sources == {"take": 1}
