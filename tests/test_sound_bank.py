"""Task 28: forced alignment and the sound bank."""

import numpy as np
import pytest

from app.phonetics.segmenter import force_align, phoneme_log_probs
from app.phonetics.sound_bank import SoundBank, Unit, fine_features

LABELS = ["<pad>", "s", "iː", "ɑ", "t", "ɹ", "r"]   # "r" and "ɹ" both mean /R/
RATE = 50.0


def posteriors_for(spans, frames):
    """One-hot posteriors: label index per frame range, blank elsewhere."""
    post = np.full((frames, len(LABELS)), 0.001)
    post[:, 0] = 1.0
    for label, a, b in spans:
        post[a:b, :] = 0.001
        post[a:b, LABELS.index(label)] = 1.0
    return post / post.sum(axis=1, keepdims=True)


def test_force_align_recovers_boundaries():
    post = posteriors_for([("s", 5, 10), ("iː", 12, 20), ("t", 25, 28)], 35)
    segs = force_align(post, LABELS, ["S", "IY", "T"], RATE)
    assert [s.phoneme for s in segs] == ["S", "IY", "T"]
    assert [round(s.start_sec * RATE) for s in segs] == [5, 12, 25]
    assert segs[0].end_sec == segs[1].start_sec          # contiguous, trailing blanks kept
    assert segs[-1].end_sec == pytest.approx(35 / RATE)
    assert all(s.confidence > 0.9 for s in segs)


def test_force_align_finds_a_sound_the_model_would_not_have_named():
    # Model only weakly sees /T/; forcing still places it, with low confidence.
    post = posteriors_for([("s", 2, 6), ("iː", 6, 12)], 20)
    post[14:16, LABELS.index("t")] = 0.2
    post = post / post.sum(axis=1, keepdims=True)
    segs = force_align(post, LABELS, ["S", "IY", "T"], RATE)
    assert segs[2].phoneme == "T" and segs[2].confidence < 0.5


def test_label_variants_count_for_one_phoneme():
    post = posteriors_for([("r", 3, 8)], 10)
    emit, _blank = phoneme_log_probs(post, LABELS, ["R"])
    assert emit[5, 0] > np.log(0.9)


def test_repeated_phonemes_need_a_blank_between():
    post = posteriors_for([("t", 2, 4), ("t", 6, 8)], 12)
    segs = force_align(post, LABELS, ["T", "T"], RATE)
    assert [round(s.start_sec * RATE) for s in segs] == [2, 6]


def test_too_few_frames():
    assert force_align(np.ones((1, len(LABELS))) / len(LABELS), LABELS, ["S", "IY"], RATE) == []


def tone(freq, seconds=0.3, sr=16000):
    t = np.arange(int(seconds * sr)) / sr
    return (0.3 * np.sin(2 * np.pi * freq * t) * (1 + 0.3 * np.sin(2 * np.pi * 4 * t))).astype(np.float32)


class Seg:
    def __init__(self, phoneme, start, end, confidence=0.9):
        self.phoneme, self.start_sec, self.end_sec, self.confidence = phoneme, start, end, confidence


def bank_from_tones():
    bank = SoundBank()
    audio = np.concatenate([tone(200), tone(800), tone(2000)])
    segs = [Seg("S", 0.0, 0.3), Seg("IY", 0.3, 0.6), Seg("T", 0.6, 0.9), Seg("AA", 0.0, 0.1, confidence=0.01)]
    assert bank.add_take("take-1", audio, 16000, segs) == 3       # the doubtful one is skipped
    bank.add_take("take-2", audio, 16000, segs[:3])
    bank.finalize()
    return bank, audio


def test_bank_chains_and_scores():
    bank, audio = bank_from_tones()
    assert bank.coverage() == {"S": 2, "IY": 2, "T": 2}
    template = bank.chain(["S", "IY", "T"])
    assert template.shape[1] == 39 and len(template) > 0
    assert bank.chain(["S", "ZH"]) is None and bank.score(fine_features(audio), ["ZH"]) is None
    query = fine_features(audio)
    assert bank.score(query, ["S", "IY", "T"]) < bank.score(query, ["T", "T", "T"])


def test_bank_save_load_and_remove(tmp_path):
    bank, _ = bank_from_tones()
    bank.save(tmp_path / "bank")
    loaded = SoundBank.load(tmp_path / "bank")
    assert loaded.coverage() == bank.coverage()
    np.testing.assert_array_equal(loaded.chain(["S"]), bank.chain(["S"]))
    loaded.remove_source("take-1")
    assert loaded.coverage() == {"S": 1, "IY": 1, "T": 1}
    assert SoundBank.load(tmp_path / "missing") is None


def test_finalize_caps_units():
    bank = SoundBank({"S": [Unit("S", f"t{i}", np.random.default_rng(i).normal(size=(5, 39)).astype(np.float32))
                            for i in range(9)]})
    bank.finalize()
    assert len(bank.units["S"]) == 5
