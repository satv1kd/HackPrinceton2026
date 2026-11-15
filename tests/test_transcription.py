"""Talk-mode transcription: the Transcriber wrapper and how its output is
merged with saved-phrase matches. A fake Whisper model keeps this fast and
deterministic; the real model is exercised by hand (see config.py notes)."""

from types import SimpleNamespace

import numpy as np
import pytest

from app.models.recognition import MatchCandidate, MatchResult
from app.services.recognition import merge_results, texts_agree
from app.services.transcriber import Transcriber, Transcript, build_prompt

ABS_MAX = 0.75


def phrase(id, text, personal=False, emoji=None):
    return SimpleNamespace(id=id, text=text, emoji=emoji, image=None, personal_word=personal)


PHRASES = [phrase('water', 'Water please', emoji='💧'), phrase('help', 'I need help'),
           phrase('wawa', 'wawa', personal=True, emoji='🥤')]


def match(*cands, confident=False):
    return MatchResult(attempt_id='a1', confident=confident, candidates=[
        MatchCandidate(phrase_id=pid, text=text, score=score) for pid, text, score in cands])


def said(text, logprob=-0.2, no_speech=0.01):
    return Transcript(text, avg_logprob=logprob, no_speech_prob=no_speech, inference_sec=0.1)


# ---- Transcript quality gate ---------------------------------------------

def test_usable_transcript_rules():
    assert said('Can I get a coffee').usable
    assert not said('').usable
    assert not said('   .  ').usable
    assert not said('Water please', no_speech=0.9).usable
    assert not said('blah', logprob=-2.5).usable
    assert not said('Thank you.', no_speech=0.45).usable  # classic silence hallucination
    assert said('Thank you.', no_speech=0.02).usable      # ...but people do say it


def test_prompt_is_deduped_and_bounded():
    assert build_prompt([]) is None
    assert build_prompt(['Water please', 'water please', 'wawa']) == 'Water please. wawa.'
    long = build_prompt([f'phrase number {i}' for i in range(200)])
    assert len(long) <= 410


def test_texts_agree_ignores_case_and_punctuation():
    assert texts_agree('Water, please!', 'water please')
    assert texts_agree('I need help', 'I need help.')
    assert not texts_agree('I need a sandwich', 'I need help')


# ---- merge rules -----------------------------------------------------------

def test_transcript_leads_when_it_is_not_a_saved_phrase():
    # The user's complaint: random speech must not come back as "Water please".
    m = match(('water', 'Water please', 0.30), ('help', 'I need help', 0.50), confident=True)
    r = merge_results(m, said('Can I get a large coffee'), PHRASES, ABS_MAX)
    assert r.confident is True
    assert r.candidates[0].type == 'generated'
    assert r.candidates[0].text == 'Can I get a large coffee'
    assert [c.phrase_id for c in r.candidates[1:]] == ['water', 'help']  # alternatives
    assert r.transcript == 'Can I get a large coffee'


def test_implausible_phrase_matches_are_not_offered_as_alternatives():
    m = match(('water', 'Water please', 0.30), ('help', 'I need help', 1.40))
    r = merge_results(m, said('See you tomorrow'), PHRASES, ABS_MAX)
    assert [c.phrase_id for c in r.candidates[1:]] == ['water']


def test_agreement_with_a_saved_phrase_shows_the_phrase():
    m = match(('help', 'I need help', 0.40), ('water', 'Water please', 0.45))
    r = merge_results(m, said('Water, please.'), PHRASES, ABS_MAX)
    assert r.confident is True
    assert r.candidates[0].type == 'phrase' and r.candidates[0].phrase_id == 'water'
    assert all(c.phrase_id != 'water' for c in r.candidates[1:])


def test_agreement_is_found_beyond_the_dtw_top_three():
    m = match(('help', 'I need help', 0.40))
    r = merge_results(m, said('water please'), PHRASES, ABS_MAX)
    assert r.candidates[0].phrase_id == 'water'
    assert r.candidates[0].emoji == '💧'


def test_confident_personal_word_beats_the_transcript():
    # Whisper can spell "wawa" (or mishear it) but cannot know it means water.
    m = match(('wawa', 'wawa', 0.20), ('water', 'Water please', 0.60), confident=True)
    r = merge_results(m, said('Wow, wow.'), PHRASES, ABS_MAX)
    assert r.confident is True
    assert r.candidates[0].phrase_id == 'wawa'
    assert r.candidates[1].type == 'generated' and r.candidates[1].text == 'Wow, wow.'


def test_no_usable_words_downgrades_a_standard_phrase_to_unsure():
    m = match(('water', 'Water please', 0.30), ('help', 'I need help', 0.50), confident=True)
    r = merge_results(m, said('', no_speech=0.95), PHRASES, ABS_MAX)
    assert r.confident is False                     # noise never announced as certain
    assert [c.phrase_id for c in r.candidates] == ['water', 'help']
    assert r.transcript is None


def test_no_usable_words_keeps_a_confident_personal_word():
    m = match(('wawa', 'wawa', 0.20), confident=True)
    assert merge_results(m, None, PHRASES, ABS_MAX).confident is True


def test_empty_phrase_library_still_returns_what_was_said():
    r = merge_results(match(), said('Hello there'), [], ABS_MAX)
    assert r.confident is True
    assert [(c.type, c.text) for c in r.candidates] == [('generated', 'Hello there')]


# ---- Transcriber wrapper with a fake model ---------------------------------

class FakeWhisper:
    def __init__(self, segments):
        self.segments, self.calls = segments, []

    def transcribe(self, audio, **kwargs):
        self.calls.append(kwargs)
        return iter(self.segments), None


def seg(text, logprob=-0.3, no_speech=0.02):
    return SimpleNamespace(text=text, avg_logprob=logprob, no_speech_prob=no_speech)


def test_transcriber_joins_segments_and_passes_vocabulary():
    fake = FakeWhisper([seg(' I want'), seg(' some water.')])
    t = Transcriber(model_factory=lambda: fake)
    r = t.transcribe(np.zeros(16000, np.float32), hints=['Water please', 'wawa'])
    assert r.text == 'I want some water.'
    assert r.usable
    assert fake.calls[0]['initial_prompt'] == 'Water please. wawa.'
    assert fake.calls[0]['language'] == 'en'


def test_transcriber_reports_unavailable_instead_of_crashing():
    def broken():
        raise ImportError('faster_whisper not installed')
    t = Transcriber(model_factory=broken)
    assert t.transcribe(np.zeros(16000, np.float32)) is None
    assert t.available is False
    assert 'ImportError' in t.status['error']


def test_transcription_is_off_for_settings_that_do_not_opt_in():
    assert Transcriber.from_settings(SimpleNamespace()) is None
    assert Transcriber.from_settings(SimpleNamespace(TRANSCRIBE_ENABLED=False)) is None
    t = Transcriber.from_settings(SimpleNamespace(TRANSCRIBE_ENABLED=True, WHISPER_MODEL='tiny.en'))
    assert t.model_name == 'tiny.en'


def test_recognize_endpoint_returns_transcript_first(client, data_dir):
    """End to end through FastAPI with a fake model injected."""
    from app.main import app
    from tests.conftest import tone, wav_bytes
    fake = FakeWhisper([seg(' Can I sit here?')])
    app.state.recognition_service.transcriber = Transcriber(model_factory=lambda: fake)
    response = client.post('/api/recognize', files={'audio': ('clip.wav', wav_bytes(tone(.8)), 'audio/wav')})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['transcript'] == 'Can I sit here?'
    assert body['candidates'][0] == {'type': 'generated', 'phrase_id': None, 'text': 'Can I sit here?',
                                     'emoji': None, 'image': None, 'score': 0.3}
    assert body['confident'] is True
    # Feedback on a generated result is stored as a text label.
    fb = client.post('/api/feedback', json={'attempt_id': body['attempt_id'], 'correct': True,
                                           'label': {'type': 'text', 'text': 'Can I sit here?'}})
    assert fb.status_code == 200, fb.text
