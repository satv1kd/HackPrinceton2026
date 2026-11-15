"""Task 19: the speech-to-text stage and whole-utterance phrase substitution."""

import shutil
from functools import lru_cache
import subprocess
from types import SimpleNamespace as NS

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.models.recognition import MatchCandidate
from app.services.recognition_stages import RecognitionContext, SpeechToTextStage, StageResult
from app.stt import FasterWhisperBackend, SpeechToTextUnavailable, Transcript
from tests.conftest import tone, wav_bytes


class FakeBackend:
    def __init__(self, text, avg_logprob=-0.3, no_speech_prob=0.01, available=True):
        self.transcript = Transcript(text=text, avg_logprob=avg_logprob, no_speech_prob=no_speech_prob)
        self._available, self.calls = available, 0

    def is_available(self):
        return self._available

    def transcribe(self, audio, sr):
        self.calls += 1
        return self.transcript


class Phrases:
    def __init__(self, *items):
        self.items = [NS(id=pid, text=text, emoji="💧", image=None) for pid, text in items]

    def list_phrases(self):
        return self.items


def ctx(**extras):
    return RecognitionContext(attempt_id="a", audio=np.ones(16000, np.float32), sr=16000,
                              wav_bytes=b"", features=np.ones((4, 2)), extras=dict(extras))


def stage(text, *phrases, **kw):
    return SpeechToTextStage(FakeBackend(text, **kw), Phrases(*phrases), phrase_match_min=85)


def test_transcript_matching_a_phrase_is_subbed_in():
    c = ctx()
    out = stage("I want water.", ("w", "I want water"), ("c", "Coffee please")).run(c)
    assert [(x.type, x.source, x.phrase_id, x.text) for x in out.candidates] == [
        ("phrase", "stt", "w", "I want water")]
    assert out.candidates[0].emoji == "💧" and out.candidates[0].score == 0
    assert c.extras["transcript"] == "I want water."


def test_close_match_keeps_transcript_as_alternative():
    out = stage("I want a water", ("w", "I want water")).run(ctx())
    assert [(x.type, x.text) for x in out.candidates] == [("phrase", "I want water"), ("generated", "I want a water")]


def test_no_close_phrase_gives_generated_text():
    out = stage("can I get a coffee", ("w", "I want water")).run(ctx())
    assert [(x.type, x.source, x.text) for x in out.candidates] == [("generated", "stt", "can I get a coffee")]
    assert out.candidates[0].score == pytest.approx(0.3)


def test_threshold_boundary():
    # rapidfuzz ratio("i want water", "i want waters") ~= 96; "i want wader" ~= 91.7
    just_below = SpeechToTextStage(FakeBackend("I want wader"), Phrases(("w", "I want water")), phrase_match_min=92)
    assert just_below.run(ctx()).candidates[0].type == "generated"
    at = SpeechToTextStage(FakeBackend("I want wader"), Phrases(("w", "I want water")), phrase_match_min=91)
    assert at.run(ctx()).candidates[0].type == "phrase"


def test_personal_word_matches_too():
    out = stage("Wawa.", ("ww", "wawa")).run(ctx())
    assert out.candidates[0].phrase_id == "ww"


def test_dtw_rank_breaks_ties():
    dtw = StageResult("phrase", [MatchCandidate(phrase_id="b", text="hi", score=0.9),
                                 MatchCandidate(phrase_id="a", text="hi", score=1.0)])
    out = stage("hi", ("a", "hi"), ("b", "hi")).run(ctx(phrase_result=dtw))
    assert [x.phrase_id for x in out.candidates] == ["b", "a"]


@pytest.mark.parametrize("text,no_speech", [("", 0.0), ("   ", 0.0), ("thank you", 0.95)])
def test_empty_or_no_speech_gives_nothing(text, no_speech):
    assert stage(text, ("w", "I want water"), no_speech_prob=no_speech).run(ctx()).candidates == []


def test_never_confident_and_availability():
    s = stage("I want water", ("w", "I want water"))
    assert s.run(ctx()).confident is False and s.may_confirm is False
    assert SpeechToTextStage(FakeBackend("x", available=False), Phrases()).available() is False
    assert SpeechToTextStage(FakeBackend("x"), Phrases(), enabled=False).available() is False


# -- end to end through the app ----------------------------------------------

@pytest.fixture
def stt_client(monkeypatch, data_dir):
    """The real app with STT on and a fake backend that says what the test sets."""
    import app.deps as deps
    from app.main import app

    backend = FakeBackend("")
    monkeypatch.setenv("STT_ENABLED", "true")
    deps.reset_caches()
    monkeypatch.setattr(deps, "get_stt_backend", lru_cache(lambda: backend))
    with TestClient(app) as client:
        client.backend = backend
        yield client


def _recognize(client):
    r = client.post("/api/recognize", files={"audio": ("a.wav", wav_bytes(tone(1.0)), "audio/wav")})
    assert r.status_code == 200, r.text
    return r.json()


def test_api_unmatched_utterance_uses_stt(stt_client):
    stt_client.backend.transcript = Transcript(text="Can I get a coffee", avg_logprob=-0.2)
    body = _recognize(stt_client)
    assert body["stage"] == "stt" and body["confident"] is False
    assert body["candidates"][0] == {"type": "generated", "source": "stt", "phrase_id": None,
                                     "text": "Can I get a coffee", "emoji": None, "image": None, "score": 0.2}


def test_api_substituted_phrase_and_feedback_teaches_dtw(stt_client):
    p = stt_client.post("/api/phrases", data={"text": "I want water", "emoji": "💧"}).json()
    stt_client.backend.transcript = Transcript(text="I want water.", avg_logprob=-0.1)
    body = _recognize(stt_client)
    top = body["candidates"][0]
    assert (top["type"], top["source"], top["phrase_id"]) == ("phrase", "stt", p["id"])
    saved = stt_client.post("/api/feedback", json={
        "attempt_id": body["attempt_id"], "correct": True,
        "label": {"type": "phrase", "phrase_id": p["id"]}})
    assert saved.status_code == 200, saved.text
    # The STT-found recording became a template for the user's own phrase.
    assert stt_client.get(f"/api/phrases/{p['id']}").json()["sample_count"] == 1


def test_api_generated_feedback_stores_text_label(stt_client, data_dir):
    stt_client.backend.transcript = Transcript(text="see you tomorrow")
    body = _recognize(stt_client)
    saved = stt_client.post("/api/feedback", json={
        "attempt_id": body["attempt_id"], "correct": True,
        "label": {"type": "text", "text": "see you tomorrow"}})
    assert saved.status_code == 200
    assert "see you tomorrow" in (data_dir / "feedback.jsonl").read_text()


def test_api_stt_disabled_never_transcribes(client, monkeypatch):
    # The default fixture turns STT off.
    body = _recognize(client)
    assert body["stage"] == "phrase" and body["candidates"] == []


# -- the real model, when present --------------------------------------------

def test_real_model_substitutes_saved_phrase(monkeypatch, data_dir, tmp_path):
    if not FasterWhisperBackend.package_installed() or shutil.which("say") is None:
        pytest.skip("needs faster-whisper and macOS `say`")
    backend = FasterWhisperBackend("base.en", offline=True)
    try:
        backend.load()
    except SpeechToTextUnavailable:
        pytest.skip("base.en not downloaded")
    import app.deps as deps
    from app.main import app

    monkeypatch.setenv("STT_ENABLED", "true")
    deps.reset_caches()
    monkeypatch.setattr(deps, "get_stt_backend", lru_cache(lambda: backend))
    aiff = tmp_path / "s.aiff"
    subprocess.run(["say", "-o", str(aiff), "I want water"], check=True)
    with TestClient(app) as client:
        p = client.post("/api/phrases", data={"text": "I want water"}).json()
        r = client.post("/api/recognize", files={"audio": ("s.aiff", aiff.read_bytes(), "audio/wav")})
        assert r.status_code == 200, r.text
        top = r.json()["candidates"][0]
        assert (top["type"], top["source"], top["phrase_id"]) == ("phrase", "stt", p["id"])
