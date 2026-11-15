"""Calibration: record the sentence whole, cut it into words afterwards.

The live word-by-word cursor is gone - it turned reading aloud into a typing
test. These tests cover what replaced it: the alignment between the words the
passage asked for and the words speech-to-text actually heard, and the clips
that come out of it.
"""

from types import SimpleNamespace as NS

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.services.calibration import (
    CalibrationStore,
    align_words,
    cut_words,
    expected_words,
    normalize_word,
    slug,
)
from app.stt import Transcript, Word
from tests.conftest import SR, tone, wav_bytes


def word(text, start, end, probability=0.9):
    return Word(text, start, end, probability)


# -- alignment ---------------------------------------------------------------

def test_identical_reading_pairs_every_word():
    expected = expected_words("Both of my thumbs ache")
    heard = ["both", "of", "my", "thumbs", "ache"]
    assert align_words(expected, heard) == {i: (i, "exact") for i in range(5)}


def test_punctuation_and_case_do_not_break_pairing():
    assert align_words(["The", "sun", "rose."], ["the", "Sun", "ROSE"]) == {
        0: (0, "exact"), 1: (1, "exact"), 2: (2, "exact")}
    assert normalize_word("Thumbs,") == "thumbs" and slug("Thumbs,") == "thumbs"


def test_a_differently_said_word_is_kept_not_discarded():
    # "thumbs" came back as "tums". That clip is the whole point of
    # calibration, so it is paired positionally rather than thrown away.
    pairs = align_words(["both", "of", "my", "thumbs", "ache"],
                        ["both", "of", "my", "tums", "ache"])
    assert pairs[3] == (3, "positional")
    assert all(pairs[i][1] == "exact" for i in (0, 1, 2, 4))


def test_a_dropped_word_leaves_its_neighbours_placed():
    pairs = align_words(["please", "ask", "them", "to", "open"],
                        ["please", "ask", "to", "open"])
    assert 2 not in pairs                      # "them" was never heard
    assert pairs[0] == (0, "exact") and pairs[4] == (3, "exact")


def test_a_ragged_stretch_is_left_unplaced_rather_than_guessed():
    pairs = align_words(["measure", "twice", "before", "you", "choose"],
                        ["measure", "mice", "spice", "nice", "before", "you", "choose"])
    assert pairs[0] == (0, "exact")
    assert 1 not in pairs                      # three heard words for one expected
    assert pairs[2] == (4, "exact")


def test_nothing_heard_places_nothing():
    assert align_words(["a", "b"], []) == {}


# -- cutting -----------------------------------------------------------------

def test_clips_are_padded_and_clamped_to_the_take():
    audio = tone(2.0)
    clips = cut_words(audio, SR, ["hello", "there"],
                      [word("hello", 0.0, 0.5), word("there", 0.6, 2.0)])
    assert clips[0].start == 0.0 and clips[0].end == pytest.approx(0.54)
    assert clips[1].end == 2.0                 # never past the end of the audio
    assert [c.match for c in clips] == ["exact", "exact"]


def test_an_unplaced_word_has_no_timings():
    clips = cut_words(tone(1.0), SR, ["one", "two"], [word("one", 0.0, 0.4)])
    assert clips[1].expected == "two" and clips[1].start is None
    assert clips[1].match is None and clips[1].path is None


def test_a_vanishingly_short_word_is_not_cut():
    clips = cut_words(tone(1.0), SR, ["one"], [word("one", 0.5, 0.501)])
    # 1 ms of audio is not a word recording; padding alone must not rescue it.
    assert clips[0].start is None or (clips[0].end - clips[0].start) >= 0.08


# -- the store ---------------------------------------------------------------

def test_saving_writes_the_take_and_one_clip_per_word(tmp_path):
    store = CalibrationStore(tmp_path)
    record = store.save_sentence(
        "s04", "Both of my thumbs", tone(2.0), SR, transcript="both of my tums",
        heard=[word("both", 0.0, 0.4), word("of", 0.4, 0.6),
               word("my", 0.6, 0.9), word("tums", 1.0, 1.6)])
    assert record.placed_count == 4
    assert [w.expected for w in record.words] == ["Both", "of", "my", "thumbs"]
    assert record.words[3].heard == "tums" and record.words[3].match == "positional"
    files = sorted(p.name for p in (tmp_path / "calibration" / "s04" / "words").iterdir())
    assert files == ["00-both.wav", "01-of.wav", "02-my.wav", "03-thumbs.wav"]
    assert (tmp_path / "calibration" / "s04" / "sentence.wav").exists()
    # Clips are named for the expected word, so later work can find "thumbs"
    # even though the recognizer called it something else.
    assert store.resolve(record.words[3].path) is not None


def test_re_recording_replaces_the_previous_take(tmp_path):
    store = CalibrationStore(tmp_path)
    store.save_sentence("s01", "one two three", tone(2.0), SR,
                        heard=[word("one", 0, .4), word("two", .4, .8), word("three", .8, 1.4)])
    store.save_sentence("s01", "one two three", tone(2.0), SR,
                        heard=[word("one", 0, .4)])
    files = sorted(p.name for p in (tmp_path / "calibration" / "s01" / "words").iterdir())
    assert files == ["00-one.wav"]             # no leftovers from the first take
    assert len(store.list_sentences()) == 1


def test_delete_and_missing_lookups(tmp_path):
    store = CalibrationStore(tmp_path)
    store.save_sentence("s01", "one", tone(1.0), SR, heard=[word("one", 0, .5)])
    assert store.get_sentence("s01") is not None
    assert [r["id"] for r in store.delete_sentence("s01")] == ["s01"]
    assert store.delete_sentence("s01") == []
    assert store.get_sentence("s01") is None
    assert store.list_sentences() == []


def test_a_sentence_id_cannot_escape_the_data_directory(tmp_path):
    store = CalibrationStore(tmp_path)
    record = store.save_sentence("../../etc/s1", "one", tone(1.0), SR,
                                 heard=[word("one", 0, .5)])
    assert record.id == "etcs1"
    assert (tmp_path / "calibration" / "etcs1").is_dir()
    with pytest.raises(ValueError):
        store.save_sentence("///", "one", tone(1.0), SR)
    # And a path that points outside never resolves to a file.
    assert store.resolve("../../../etc/passwd") is None


def test_a_take_with_no_transcript_is_still_kept(tmp_path):
    store = CalibrationStore(tmp_path)
    record = store.save_sentence("s01", "one two", tone(1.5), SR)
    assert record.placed_count == 0 and record.audio_path is not None
    assert [w.match for w in record.words] == [None, None]


# -- through the API ---------------------------------------------------------

class FakeBackend:
    """Stands in for faster-whisper: says what the test tells it to."""

    def __init__(self, transcript=None, available=True):
        self.transcript = transcript
        self._available = available
        self.vocabulary = None

    def is_available(self):
        return self._available

    def transcribe(self, audio, sr, vocabulary=None):
        self.vocabulary = list(vocabulary or [])
        return self.transcript


@pytest.fixture
def cal_client(data_dir):
    from app.deps import get_stt_backend
    from app.main import app

    backend = FakeBackend(Transcript(
        text="both of my tums ache",
        words=[word("both", 0.0, 0.3), word("of", 0.3, 0.5), word("my", 0.5, 0.8),
               word("tums", 0.9, 1.3), word("ache", 1.4, 1.9)],
        avg_logprob=-0.2, no_speech_prob=0.01))
    app.dependency_overrides[get_stt_backend] = lambda: backend
    try:
        with TestClient(app) as client:
            client.backend = backend
            yield client
    finally:
        app.dependency_overrides.pop(get_stt_backend, None)


def _upload(client, sentence_id="s04", text="Both of my thumbs ache", seconds=2.0):
    return client.post("/api/calibration/sentences",
                       data={"sentence_id": sentence_id, "text": text},
                       files={"audio": ("take.wav", wav_bytes(tone(seconds)), "audio/wav")})


def test_api_cuts_the_sentence_into_words(cal_client):
    response = _upload(cal_client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["word_count"] == 5 and body["placed_count"] == 5
    assert body["transcript"] == "both of my tums ache"
    assert body["words"][3] == {
        "index": 3, "expected": "thumbs", "heard": "tums", "start": 0.86, "end": 1.34,
        "match": "positional", "probability": 0.9, "clip": True}
    # The sentence text is handed to the recognizer as a vocabulary hint.
    assert cal_client.backend.vocabulary == ["Both of my thumbs ache"]


def test_api_serves_the_take_and_each_word_clip(cal_client):
    _upload(cal_client)
    whole = cal_client.get("/api/calibration/sentences/s04/audio")
    assert whole.status_code == 200 and whole.headers["content-type"] == "audio/wav"
    clip = cal_client.get("/api/calibration/sentences/s04/words/3")
    assert clip.status_code == 200 and len(clip.content) > 44   # past the WAV header
    assert cal_client.get("/api/calibration/sentences/s04/words/99").status_code == 404
    assert cal_client.get("/api/calibration/sentences/nope/audio").status_code == 404


def test_api_lists_and_deletes(cal_client):
    _upload(cal_client, "s04")
    _upload(cal_client, "s09", "Please ask them to open the window")
    assert sorted(s["id"] for s in cal_client.get("/api/calibration/sentences").json()) == ["s04", "s09"]
    assert cal_client.delete("/api/calibration/sentences/s04").status_code == 204
    assert [s["id"] for s in cal_client.get("/api/calibration/sentences").json()] == ["s09"]
    assert cal_client.delete("/api/calibration/sentences/s04").status_code == 404


def test_api_keeps_the_take_when_speech_to_text_is_missing(cal_client):
    cal_client.backend._available = False
    body = _upload(cal_client).json()
    assert body["placed_count"] == 0 and body["transcribed"] is False
    assert body["word_count"] == 5          # every word is listed, just unplaced
    assert cal_client.get("/api/calibration/sentences/s04/audio").status_code == 200


def test_api_rejects_silence_with_a_kind_status(cal_client):
    response = cal_client.post("/api/calibration/sentences",
                               data={"sentence_id": "s04", "text": "one two"},
                               files={"audio": ("take.wav", wav_bytes(np.zeros(SR, np.float32)), "audio/wav")})
    assert response.status_code == 422


def test_api_allows_a_sentence_far_longer_than_a_talk_phrase(cal_client):
    # MAX_CLIP_SEC caps a Talk recording at 10s; a slow reader must not be cut off.
    body = _upload(cal_client, seconds=20.0).json()
    assert body["duration_sec"] > 10
