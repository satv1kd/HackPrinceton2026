"""Task 29: the phonetic decoder."""

import time

import pytest

from app.phonetics.aligner import align
from app.phonetics.decoder import PhoneticDecoder, confidence
from app.phonetics.lexicon import Lexicon, LexiconEntry
from app.phonetics.profile import SoundProfile

WORDS = {
    "red": (3e-4, ["R", "EH", "D"]), "wed": (2e-6, ["W", "EH", "D"]), "bed": (1e-4, ["B", "EH", "D"]),
    "water": (2e-4, ["W", "AO", "T", "ER"]), "i": (1e-2, ["AY"]), "want": (1e-3, ["W", "AA", "N", "T"]),
    "can": (2e-3, ["K", "AE", "N"]), "an": (1e-3, ["AE", "N"]), "we": (3e-3, ["W", "IY"]),
    "go": (2e-3, ["G", "OW"]), "outside": (5e-5, ["AW", "T", "S", "AY", "D"]), "the": (5e-2, ["DH", "AH"]),
    "cat": (5e-5, ["K", "AE", "T"]), "sat": (3e-5, ["S", "AE", "T"]),
}


@pytest.fixture
def lexicon():
    return Lexicon([LexiconEntry(w, f, [p]) for w, (f, p) in WORDS.items()])


def profile_from(pairs, times=6):
    return SoundProfile.build([align(e, h) for e, h in pairs for _ in range(times)], alpha=1.0)


def neutral():
    return SoundProfile(alpha=1.0)


def texts(hyps):
    return [h.text for h in hyps]


def test_exact_input_decodes_to_the_words(lexicon):
    dec = PhoneticDecoder(lexicon, neutral(), lm_weight=0.3)
    assert texts(dec.decode(["AY", "W", "AA", "N", "T", "W", "AO", "T", "ER"]))[0] == "i want water"


def test_the_profile_turns_heard_w_into_meant_r(lexicon):
    heard = ["W", "EH", "D"]
    plain = PhoneticDecoder(lexicon, neutral(), lm_weight=0.0)
    assert texts(plain.decode(heard))[0] == "wed"
    # This person's /r/ comes out as [w]:
    personal = PhoneticDecoder(lexicon, profile_from([(["R", "EH", "D"], ["W", "EH", "D"])]), lm_weight=0.0)
    assert texts(personal.decode(heard))[0] == "red"


def test_a_dropped_first_sound_is_still_found(lexicon):
    dec = PhoneticDecoder(lexicon, profile_from([(["K", "AE", "T"], ["AE", "T"])]), lm_weight=0.0)
    assert "cat" in texts(dec.decode(["AE", "T"]))


def test_word_frequency_breaks_ties(lexicon):
    heard = ["W", "EH", "D"]
    no_lm = texts(PhoneticDecoder(lexicon, neutral(), lm_weight=0.0).decode(heard, k=3))
    strong_lm = texts(PhoneticDecoder(lexicon, neutral(), lm_weight=5.0).decode(heard, k=3))
    assert no_lm[0] == "wed" and strong_lm[0] != "wed"


def test_saved_phrases_are_always_considered(lexicon):
    heard = ["AE", "N", "W", "IY", "G", "OW", "AW", "T", "S", "AY", "D"]   # "can" lost its K
    without = PhoneticDecoder(lexicon, neutral(), lm_weight=0.3)
    with_phrase = PhoneticDecoder(lexicon, neutral(), lm_weight=0.3, phrase_texts=["Can we go outside"])
    assert texts(without.decode(heard))[0] == "an we go outside"
    assert texts(with_phrase.decode(heard))[0] == "can we go outside"


def test_max_words_and_k(lexicon):
    dec = PhoneticDecoder(lexicon, neutral(), lm_weight=0.3, max_words=2)
    hyps = dec.decode(["AY", "W", "AA", "N", "T", "W", "AO", "T", "ER"], k=2)
    assert len(hyps) <= 2 and all(len(h.words) <= 2 for h in hyps)
    assert PhoneticDecoder(lexicon, neutral()).decode([]) == []


def test_confidence():
    dec_hyps = PhoneticDecoder(Lexicon([LexiconEntry(w, f, [p]) for w, (f, p) in WORDS.items()]),
                               neutral()).decode(["S", "AE", "T"], k=3)
    margin, per_phoneme = confidence(dec_hyps, 3)
    assert margin >= 0 and per_phoneme > 0
    assert confidence([], 3)[1] == float("inf")


class FakeBank:
    def score(self, query, phonemes):
        # Strong enough to outweigh "wed"'s ~10-point lead in symbol cost.
        return 0.0 if phonemes == ["B", "EH", "D"] else 30.0


def test_sound_bank_rescoring_can_reorder(lexicon):
    heard = ["W", "EH", "D"]
    dec = PhoneticDecoder(lexicon, neutral(), lm_weight=0.0, sound_bank=FakeBank())
    assert texts(dec.decode(heard))[0] == "wed"                       # no features: no rescoring
    rescored = dec.decode(heard, query_features=[[0.0]])
    assert rescored[0].text == "bed" and rescored[0].acoustic == 0.0


def test_real_lexicon_speed():
    import pathlib
    path = pathlib.Path("data/lexicon/words.tsv")
    if not path.exists():
        pytest.skip("build the lexicon first (scripts/build_lexicon.py)")
    dec = PhoneticDecoder(Lexicon.load(path), neutral(), lm_weight=0.3, max_words=6)
    started = time.perf_counter()
    hyps = dec.decode("AY N IY D T UW G OW T UW DH AH D AA K T ER".split())
    assert time.perf_counter() - started < 3.0
    assert hyps[0].text.startswith("i need to go")
