"""Task 30: the phonetic decoder as a recognition stage."""

import io
from types import SimpleNamespace as NS

import numpy as np
import pytest
import soundfile as sf

from app.models.recognition import MatchCandidate
from app.phonetics.lexicon import Lexicon, LexiconEntry
from app.phonetics.profile import SoundProfile
from app.services.attempts import AttemptStore
from app.services.recognition import RecognitionService
from app.services.recognition_stages import (PhoneticDecoderStage, RecognitionContext, StageResult,
                                             _sentence_case)

LEX = Lexicon([LexiconEntry(w, f, [p]) for w, f, p in [
    ("i", 1e-2, ["AY"]), ("want", 1e-3, ["W", "AA", "N", "T"]), ("water", 2e-4, ["W", "AO", "T", "ER"]),
    ("red", 3e-4, ["R", "EH", "D"]), ("bed", 1e-4, ["B", "EH", "D"]), ("wed", 2e-6, ["W", "EH", "D"]),
]])
WATER = ["AY", "W", "AA", "N", "T", "W", "AO", "T", "ER"]


class FakePhonemes:
    def __init__(self, heard, reason=None):
        self.heard, self.reason, self.calls = heard, reason, []

    def unavailable_reason(self):
        return self.reason

    def recognize_samples(self, samples, sr, cache_id=None, timeout=None):
        self.calls.append(cache_id)
        return NS(phonemes=list(self.heard))


class FakeTrainer:
    def __init__(self, reason=None):
        self.reason, self.profile = reason, SoundProfile(alpha=1.0, version=1)

    def readiness(self):
        return self.reason

    def current(self):
        return self.profile

    def sound_bank(self):
        return None


class FakePhrases:
    def __init__(self, *items):
        self.items = [NS(id=i, text=t, emoji="💧", image=None, personal_word=False) for i, t in items]

    def list_phrases(self):
        return self.items


def stage(heard=WATER, phrases=(), margin=0.0, max_cost=99.0, **kw):
    return PhoneticDecoderStage(FakePhonemes(heard, kw.get("phonemes_reason")),
                                FakeTrainer(kw.get("trainer_reason")),
                                kw.get("lexicon_loader", lambda: LEX), FakePhrases(*phrases),
                                lm_weight=0.3, max_words=6, margin=margin, max_cost=max_cost)


def ctx(mode=True):
    return RecognitionContext(attempt_id="a1", audio=np.ones(16000, np.float32), sr=16000,
                              wav_bytes=b"", features=np.ones((4, 2)), phonetic_mode=mode)


def test_runs_only_with_the_switch_on():
    s = stage()
    assert s.enabled(ctx(True)) and not s.enabled(ctx(False))


@pytest.mark.parametrize("kw,reason", [
    ({"phonemes_reason": "not installed"}, "not installed"),
    ({"trainer_reason": "record first"}, "record first"),
    ({"lexicon_loader": lambda: None}, "word list"),
])
def test_unavailable_until_everything_is_ready(kw, reason):
    s = stage(**kw)
    assert not s.available() and reason in s.unavailable_reason()


def test_decodes_into_generated_text_and_records_phonemes():
    c = ctx()
    out = stage(margin=0.0).run(c)
    assert [(x.type, x.source, x.text) for x in out.candidates][0] == ("generated", "phonetic", "I want water")
    assert c.extras["recognized_phonemes"] == WATER and c.extras["phonetic"]["sure"] is True
    assert out.final and out.plausible and not out.confident


def test_a_decoded_saved_phrase_comes_back_as_the_phrase():
    out = stage(phrases=[("p1", "I want water")]).run(ctx())
    top = out.candidates[0]
    assert (top.type, top.source, top.phrase_id, top.emoji) == ("phrase", "phonetic", "p1", "💧")


def test_unsure_lets_the_cascade_continue():
    out = stage(margin=1e6).run(ctx())
    assert out.candidates and not out.final and not out.plausible


def test_nothing_heard():
    assert stage(heard=[]).run(ctx()).candidates == []


def test_sentence_case():
    assert _sentence_case("i want water") == "I want water"
    assert _sentence_case("can i go i'm tired") == "Can I go I'm tired"


# -- in the cascade ----------------------------------------------------------

class FakeStage:
    def __init__(self, name, candidates=(), may_confirm=False, plausible=False):
        self.name, self.may_confirm, self.calls = name, may_confirm, 0
        self._candidates, self._plausible = list(candidates), plausible

    def available(self):
        return True

    def enabled(self, ctx):
        return True

    def run(self, ctx):
        self.calls += 1
        return StageResult(self.name, self._candidates, plausible=self._plausible)


def wav():
    buf = io.BytesIO()
    sf.write(buf, (0.3 * np.sin(np.arange(8000) * 0.05)).astype(np.float32), 16000, format="WAV")
    return buf.getvalue()


def service(tmp_path, stages, mode=True):
    def process(content, mime):
        audio, sr = sf.read(io.BytesIO(content), dtype="float32")
        return NS(samples=audio, sr=sr, wav_bytes=content)
    return RecognitionService(NS(process=process), NS(extract=lambda a, sr: np.ones((4, 2))),
                              AttemptStore(tmp_path), stages,
                              preference_store=NS(get=lambda: NS(phonetic_mode=mode)))


def test_sure_decoder_skips_speech_to_text(tmp_path):
    phrase = FakeStage("phrase", may_confirm=True)
    stt = FakeStage("stt", [MatchCandidate(type="generated", source="stt", text="hello", score=0.2)])
    result = service(tmp_path, [phrase, stage(margin=0.0), stt]).recognize(wav(), "audio/wav")
    assert stt.calls == 0 and result.stage == "phonetic" and result.confident is False
    meta = AttemptStore(tmp_path).get(result.attempt_id)
    assert meta["recognized_phonemes"] == WATER


def test_unsure_decoder_runs_speech_to_text_and_stt_leads(tmp_path):
    stt = FakeStage("stt", [MatchCandidate(type="generated", source="stt", text="I want water please", score=0.2)],
                    plausible=True)
    result = service(tmp_path, [FakeStage("phrase", may_confirm=True), stage(margin=1e6), stt]).recognize(wav(), "audio/wav")
    assert stt.calls == 1
    # The decoder failed its check, so the transcript leads; the decoder's
    # best guesses still fill the rest instead of being dropped.
    assert [c.source for c in result.candidates] == ["stt", "phonetic", "phonetic"]


def test_skipped_reason_reported_when_switch_on_but_not_ready(tmp_path):
    blocked = stage(trainer_reason="Record the 18 calibration phrases first.")
    result = service(tmp_path, [FakeStage("phrase", may_confirm=True), blocked]).recognize(wav(), "audio/wav")
    assert result.phonetic_skipped_reason == "Record the 18 calibration phrases first."
    off = service(tmp_path, [FakeStage("phrase", may_confirm=True), blocked], mode=False).recognize(wav(), "audio/wav")
    assert off.phonetic_skipped_reason is None
