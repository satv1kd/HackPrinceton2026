"""Tasks 22-23: the pronunciation lexicon, coverage checker and enrollment set."""

import pytest

from app.phonetics.coverage import check_coverage, total_requirements, requirements_met
from app.phonetics.enrollment import TAKES_PER_PHRASE, load_enrollment_set
from app.phonetics.inventory import PHONEMES, validate_sequence
from app.phonetics.lexicon import Lexicon, LexiconEntry, normalize, tokenize


@pytest.fixture
def lexicon():
    return Lexicon([
        LexiconEntry("water", 1e-4, [["W", "AO", "T", "ER"], ["W", "AA", "T", "ER"]]),
        LexiconEntry("i", 1e-2, [["AY"]]),
        LexiconEntry("want", 1e-3, [["W", "AA", "N", "T"]]),
    ])


def test_normalize_and_tokenize():
    assert normalize("Don’t!") == "don't"
    assert tokenize("I want  water, please—now") == ["i", "want", "water", "please", "now"]


def test_pronounce_and_transcribe(lexicon):
    assert lexicon.pronounce("WATER")[0] == ["W", "AO", "T", "ER"]
    assert len(lexicon.pronounce("water")) == 2
    assert lexicon.transcribe_text("I want water") == [["AY"], ["W", "AA", "N", "T"], ["W", "AO", "T", "ER"]]
    assert lexicon.flat_phonemes("i want") == ["AY", "W", "AA", "N", "T"]


def test_unknown_word(lexicon, monkeypatch):
    import app.phonetics.lexicon as lx
    monkeypatch.setattr(lx, "cmudict_pronunciations", lambda word, holder=None: [])
    assert lexicon.pronounce("wawa") == []
    assert lexicon.transcribe_text("I want wawa") is None


def test_add_rejects_bad_symbols():
    with pytest.raises(ValueError):
        Lexicon([LexiconEntry("x", 1.0, [["XX"]])])


def test_save_load_round_trip(lexicon, tmp_path):
    path = tmp_path / "words.tsv"
    lexicon.save(path)
    loaded = Lexicon.load(path)
    assert len(loaded) == 3
    assert loaded.pronounce("water") == lexicon.pronounce("water")
    assert loaded.entry("i").frequency == pytest.approx(1e-2)


def test_malformed_file(tmp_path):
    bad = tmp_path / "w.tsv"
    bad.write_text("water\tnot-a-number\tW AO T ER\n")
    with pytest.raises(ValueError):
        Lexicon.load(bad)


# -- coverage checker ----------------------------------------------------------

def test_coverage_reports_missing_phoneme_and_positions():
    report = check_coverage([[["S", "IY"], ["T", "IY", "S"]]], min_occurrences=1)
    assert "ZH" in report.missing
    assert "S" not in report.missing_initial and "S" not in report.missing_final
    assert "T" in report.missing_final and "NG" not in report.missing_initial  # NG exempt initially
    assert not report.ok


def test_requirements_progress_is_monotonic():
    goal = total_requirements(2)
    one = requirements_met([[["S", "IY"]]], 2)
    two = requirements_met([[["S", "IY"]], [["T", "AA"]]], 2)
    assert 0 < one < two < goal


# -- the shipped enrollment set -----------------------------------------------

def test_enrollment_set_covers_every_sound():
    phrases = load_enrollment_set()
    report = check_coverage([p.words for p in phrases], min_occurrences=2)
    assert report.ok, report.table()
    assert set(report.counts) == set(PHONEMES)


def test_enrollment_phrases_are_short_and_well_formed():
    phrases = load_enrollment_set()
    assert 10 <= len(phrases) <= 30
    assert TAKES_PER_PHRASE == 2
    for phrase in phrases:
        assert 1 <= len(phrase.text.split()) <= 6, phrase.text
        assert len(phrase.words) == len(tokenize(phrase.text)), phrase.text
        assert phrase.emoji
        for word in phrase.words:
            validate_sequence(word)
    assert len({p.id for p in phrases}) == len(phrases)


def test_enrollment_set_matches_cmudict_when_available():
    pytest.importorskip("cmudict")
    from app.phonetics.lexicon import cmudict_pronunciations
    for phrase in load_enrollment_set():
        for word, phonemes in zip(tokenize(phrase.text), phrase.words):
            assert list(phonemes) in cmudict_pronunciations(word), (phrase.text, word)
