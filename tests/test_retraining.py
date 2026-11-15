"""Task 31: learning from Yes/No feedback, with validation and rollback."""

import json
import time
from types import SimpleNamespace as NS

import pytest

from app.phonetics.lexicon import Lexicon, LexiconEntry
from app.phonetics.profile import ProfileStore, SoundProfile
from app.phonetics.training import (ProfileTrainer, TrainingExample, feedback_examples, is_holdout)

LEX = Lexicon([LexiconEntry(w, f, [p]) for w, f, p in [
    ("red", 3e-4, ["R", "EH", "D"]), ("wed", 2e-6, ["W", "EH", "D"]), ("rain", 1e-4, ["R", "EY", "N"]),
]])


def write_events(data_dir, events):
    (data_dir / "feedback.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))


def record(attempt_id, label, heard, event_id=None):
    return {"action": "record", "event_id": event_id or f"e-{attempt_id}", "attempt_id": attempt_id,
            "correct": True, "label": label, "recognized_phonemes": heard}


class Phrases:
    def __init__(self, *items):
        self.items = {p.id: p for p in items}

    def get_phrase(self, phrase_id):
        return self.items.get(phrase_id)


def test_feedback_examples_from_labels(tmp_path):
    phrases = Phrases(NS(id="p1", text="red rain", phonemes=["R", "EH", "D", "R", "EY", "N"], personal_word=False),
                      NS(id="ww", text="wawa", phonemes=None, personal_word=True))
    write_events(tmp_path, [
        record("a1", {"type": "phrase", "phrase_id": "p1"}, ["W", "EH", "D", "W", "EY", "N"]),
        record("a2", {"type": "text", "text": "red"}, ["W", "EH", "D"]),
        record("a3", {"type": "text", "text": "unknownword"}, ["AH"]),                 # cannot pronounce
        record("a4", {"type": "phrase", "phrase_id": "ww"}, ["W", "AA"]),              # personal word
        record("a5", {"type": "text", "text": "red"}, None),                            # phonetic mode was off
        {"action": "record", "event_id": "e6", "attempt_id": "a6", "correct": False, "label": None,
         "recognized_phonemes": ["AH"]},                                                # Skip
    ])
    found = {e.key: e for e in feedback_examples(tmp_path, phrases, lambda: LEX)}
    assert set(found) == {"a1", "a2"}
    assert found["a1"].expected == ["R", "EH", "D", "R", "EY", "N"] and found["a1"].text == "red rain"
    assert found["a2"].expected == ["R", "EH", "D"] and found["a2"].heard == ["W", "EH", "D"]


def test_holdout_split_is_stable():
    ids = [f"attempt-{i}" for i in range(200)]
    first = [is_holdout(i, 0.2) for i in ids]
    assert first == [is_holdout(i, 0.2) for i in ids]
    assert 20 < sum(first) < 60
    assert not any(is_holdout(i, 0.0) for i in ids)


class NoTakes:
    def list_sentences(self):
        return []


def trainer(tmp_path, examples, retrain_after=10, holdout=0.0):
    t = ProfileTrainer(tmp_path, NoTakes(), NS(cached=lambda *a, **k: None), alpha=1.0,
                       min_occurrences=2, feedback_examples=lambda: list(examples), lexicon_loader=lambda: LEX,
                       retrain_after=retrain_after, holdout_fraction=holdout)
    return t


def r_examples(n, prefix="a"):
    return [TrainingExample("feedback", f"{prefix}{i}", ["R", "EH", "D"], ["W", "EH", "D"], "red") for i in range(n)]


def test_pending_counts_and_threshold(tmp_path):
    examples = r_examples(9)
    t = trainer(tmp_path, examples)
    ProfileStore(tmp_path).save(SoundProfile())          # an initial profile from enrollment
    assert t.pending_counts()["R"] == 9 and not t.due()
    examples.append(r_examples(1, "b")[0])
    assert t.pending_counts()["R"] == 10 and t.due()
    result = t.retrain()
    assert result["status"] == "promoted"
    assert t.pending_counts() == {} and not t.due()       # learned: nothing pending
    assert t.current().probability("R", "W") > 0.5
    assert t.current().trained_on == sorted(e.key for e in examples)


def test_undo_reduces_pending(tmp_path):
    examples = r_examples(10)
    t = trainer(tmp_path, examples)
    ProfileStore(tmp_path).save(SoundProfile())
    assert t.due()
    examples.pop()                                        # an undone Yes leaves active_feedback
    assert not t.due()


def test_a_worse_candidate_is_rolled_back(tmp_path):
    t = trainer(tmp_path, r_examples(10), holdout=0.0)
    first = ProfileStore(tmp_path).save(SoundProfile())
    scores = iter([{"n": 5, "top1": 0.8, "top3": 1.0}, {"n": 5, "top1": 0.2, "top3": 0.4}])
    t.evaluate = lambda profile, examples: next(scores)
    t.split_feedback = lambda: (r_examples(10), r_examples(5, "h"))
    result = t.retrain()
    assert result["status"] == "rolled_back"
    assert ProfileStore(tmp_path).current_version() == first.version   # the old one stays active
    assert ProfileStore(tmp_path).versions() == [1, 2]                  # candidate kept for inspection
    assert t.last_retrain()["promoted"] is False


def test_evaluate_on_real_decoding(tmp_path):
    t = trainer(tmp_path, [])
    held = [TrainingExample("feedback", "h1", ["R", "EH", "D"], ["W", "EH", "D"], "red")]
    plain = t.evaluate(SoundProfile(alpha=1.0), held)
    learned = t.evaluate(SoundProfile.build([__import__("app.phonetics.aligner", fromlist=["align"]).align(
        ["R", "EH", "D"], ["W", "EH", "D"])] * 8, alpha=1.0), held)
    assert learned["top1"] >= plain["top1"] and learned["top3"] == 1.0


def test_on_feedback_runs_in_background(tmp_path):
    t = trainer(tmp_path, r_examples(10))
    assert t.on_feedback() is False                      # no profile from enrollment yet
    ProfileStore(tmp_path).save(SoundProfile())
    assert t.on_feedback() is True
    for _ in range(100):
        if t.last_retrain():
            break
        time.sleep(0.02)
    assert t.last_retrain()["trigger"] == "feedback"


def test_profile_api_reports_pending_and_retrain(client):
    body = client.get("/api/profile").json()
    assert body["retrain_after"] == 10 and all(s["pending"] == 0 for s in body["sounds"])
    assert client.post("/api/profile/retrain").json()["status"] == "no_data"
