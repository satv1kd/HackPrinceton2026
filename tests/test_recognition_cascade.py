"""Task 17: the recognition cascade and its pluggable stages."""

import io
import json
from types import SimpleNamespace as NS

import numpy as np
import pytest
import soundfile as sf

from app.models.recognition import MatchCandidate, MatchResult
from app.services.attempts import AttemptStore
from app.services.recognition import RecognitionService, merge_unconfident, normalize_text
from app.services.recognition_stages import PhraseMatchStage, StageResult


def phrase(pid, score, source="phrase"):
    return MatchCandidate(type="phrase", source=source, phrase_id=pid, text=pid.title(), score=score)


def generated(text, source="stt", score=0.5):
    return MatchCandidate(type="generated", source=source, text=text, score=score)


class FakeStage:
    def __init__(self, name, candidates=(), confident=False, plausible=False, may_confirm=False,
                 available=True, needs_phonetic=False, raises=False):
        self.name, self.may_confirm = name, may_confirm
        self._candidates, self._confident, self._plausible = list(candidates), confident, plausible
        self._available, self._needs_phonetic, self._raises = available, needs_phonetic, raises
        self.calls = 0

    def available(self):
        return self._available

    def enabled(self, ctx):
        return ctx.phonetic_mode if self._needs_phonetic else True

    def run(self, ctx):
        self.calls += 1
        if self._raises:
            raise RuntimeError("model exploded")
        return StageResult(self.name, self._candidates, self._confident, self._plausible)


def wav():
    buf = io.BytesIO()
    sf.write(buf, (0.3 * np.sin(np.arange(8000) * 0.05)).astype(np.float32), 16000, format="WAV")
    return buf.getvalue()


def make_service(tmp_path, stages, phonetic_mode=False):
    def process(content, mime):
        audio, sr = sf.read(io.BytesIO(content), dtype="float32")
        return NS(samples=audio, sr=sr, wav_bytes=content)

    prefs = NS(get=lambda: NS(phonetic_mode=phonetic_mode))
    extractor = NS(extract=lambda audio, sr: np.ones((4, 2)))
    return RecognitionService(NS(process=process), extractor, AttemptStore(tmp_path), stages,
                              preference_store=prefs)


def test_confident_phrase_stops_the_cascade(tmp_path):
    first = FakeStage("phrase", [phrase("water", 0.1)], confident=True, may_confirm=True)
    later = FakeStage("stt", [generated("hello")])
    result = make_service(tmp_path, [first, later]).recognize(wav(), "audio/wav")
    assert result.confident and result.stage == "phrase"
    assert later.calls == 0


def test_unsure_phrase_falls_through(tmp_path):
    first = FakeStage("phrase", [phrase("water", 0.9)], may_confirm=True)
    later = FakeStage("stt", [generated("can I get a coffee")])
    result = make_service(tmp_path, [first, later]).recognize(wav(), "audio/wav")
    assert later.calls == 1 and not result.confident
    # DTW's best was not plausible, so the transcript leads.
    assert [c.text for c in result.candidates] == ["can I get a coffee", "Water"]
    assert result.stage == "stt"


def test_phonetic_stage_follows_the_toggle(tmp_path):
    phon = FakeStage("phonetic", [generated("red", "phonetic")], needs_phonetic=True)
    base = FakeStage("phrase", [], may_confirm=True)
    make_service(tmp_path, [base, phon], phonetic_mode=False).recognize(wav(), "audio/wav")
    assert phon.calls == 0
    result = make_service(tmp_path, [base, phon], phonetic_mode=True).recognize(wav(), "audio/wav")
    assert phon.calls == 1 and result.phonetic_mode is True and result.stage == "phonetic"


def test_unavailable_and_failing_stages_are_skipped(tmp_path):
    base = FakeStage("phrase", [phrase("water", 0.3)], plausible=True, may_confirm=True)
    missing = FakeStage("phonetic", [generated("x", "phonetic")], available=False)
    broken = FakeStage("stt", raises=True)
    result = make_service(tmp_path, [base, missing, broken]).recognize(wav(), "audio/wav")
    assert missing.calls == 0 and broken.calls == 1
    assert [c.phrase_id for c in result.candidates] == ["water"]


def test_generated_stages_can_never_be_confident(tmp_path):
    base = FakeStage("phrase", [], may_confirm=True)
    cocky = FakeStage("stt", [generated("hello")], confident=True)  # may_confirm=False
    result = make_service(tmp_path, [base, cocky]).recognize(wav(), "audio/wav")
    assert result.confident is False and result.candidates[0].text == "hello"


def test_attempt_record_keeps_stage_info(tmp_path):
    base = FakeStage("phrase", [phrase("water", 0.9)], may_confirm=True)
    stt = FakeStage("stt", [generated("hello")])
    service = make_service(tmp_path, [base, stt])
    result = service.recognize(wav(), "audio/wav")
    meta = service.attempt_store.get(result.attempt_id)
    assert meta["stage"] == "stt" and meta["extras"]["stages_run"] == ["phrase", "stt"]
    assert meta["candidates"][0]["source"] == "stt"


def test_requires_a_stage(tmp_path):
    with pytest.raises(ValueError):
        RecognitionService(None, None, AttemptStore(tmp_path), [])


# -- merge rule ------------------------------------------------------------

def test_merge_corroborated_phrase_first_and_dedupes():
    dtw = StageResult("phrase", [phrase("water", 0.4), phrase("coffee", 0.6)], plausible=True)
    stt = StageResult("stt", [phrase("water", 0.1, source="stt"), generated("Water please")])
    merged = merge_unconfident([dtw, stt])
    # Transcript and DTW agree on "water"; DTW's duplicate is dropped.
    assert [(c.phrase_id, c.source) for c in merged] == [("water", "stt"), (None, "stt"), ("coffee", "phrase")]


def test_merge_never_drops_transcript_behind_plausible_phrases():
    dtw = StageResult("phrase", [phrase("a", 0.2), phrase("b", 0.3), phrase("c", 0.4)], plausible=True)
    stt = StageResult("stt", [generated("hello there")])
    merged = merge_unconfident([dtw, stt])
    assert [c.text for c in merged] == ["A", "hello there", "B"]


def test_merge_caps_at_three_and_dedupes_text():
    stt = StageResult("stt", [generated("Hi!"), generated("hi"), generated("a"), generated("b"), generated("c")])
    assert [c.text for c in merge_unconfident([stt])] == ["Hi!", "a", "b"]


def test_normalize_text():
    assert normalize_text("  I want WATER, please! ") == "i want water please"
    assert normalize_text("don't") == "don't"


# -- the real phrase stage -------------------------------------------------

def test_phrase_stage_tags_source_and_plausibility(tmp_path):
    matcher = NS(absolute_max=0.5, match=lambda f: MatchResult(
        attempt_id="x", confident=False, candidates=[MatchCandidate(phrase_id="w", text="W", score=0.4)]))
    ctx = NS(features=np.ones((2, 2)), extras={})
    out = PhraseMatchStage(matcher, tmp_path).run(ctx)
    assert out.plausible and out.candidates[0].source == "phrase"
    assert ctx.extras["phrase_result"] is out


def test_api_response_includes_new_fields(client):
    r = client.post("/api/recognize", files={"audio": ("a.wav", wav(), "audio/wav")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stage"] == "phrase" and body["phonetic_mode"] is False
