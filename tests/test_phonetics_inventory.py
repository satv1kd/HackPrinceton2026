"""Tests for the canonical inventory and symbol mapping (Task 14)."""

from __future__ import annotations

import pytest

from app.phonetics import inventory as inv
from app.phonetics import mapping as mp


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

def test_inventory_is_the_39_phoneme_cmudict_set():
    assert len(inv.PHONEMES) == 39
    assert len(inv.CONSONANTS) == 24
    assert len(inv.VOWELS) == 15
    assert len(set(inv.PHONEMES)) == 39


def test_consonants_and_vowels_do_not_overlap():
    assert set(inv.CONSONANTS).isdisjoint(set(inv.VOWELS))


def test_every_phoneme_carries_features():
    """A phoneme without features would leave a hole in the Task 20 prior."""
    for phoneme in inv.PHONEMES:
        assert inv.is_vowel(phoneme) or inv.is_consonant(phoneme)
    assert set(inv.CONSONANT_FEATURES) == set(inv.CONSONANTS)
    assert set(inv.VOWEL_FEATURES) == set(inv.VOWELS)


def test_sentinels_are_not_phonemes():
    assert inv.DELETION not in inv.PHONEME_SET
    assert inv.INSERTION not in inv.PHONEME_SET


@pytest.mark.parametrize("bad", ["", "xx", "ah", "AH1", "ɑ", inv.DELETION])
def test_unknown_symbols_raise_rather_than_pass_silently(bad):
    with pytest.raises(ValueError):
        inv.is_vowel(bad)


def test_validate_sequence_accepts_canonical_and_rejects_anything_else():
    inv.validate_sequence(["AY", "W", "AA", "N", "T", "W", "AO", "T", "ER"])
    with pytest.raises(ValueError, match="not a canonical phoneme"):
        inv.validate_sequence(["AY", "AH0"])
    with pytest.raises(ValueError):
        inv.validate_sequence(["AY", inv.DELETION])


# ---------------------------------------------------------------------------
# Phonotactics (feeds the Task 17 coverage checker)
# ---------------------------------------------------------------------------

def test_phonotactic_exemptions_match_the_prd():
    assert not inv.allows_word_initial("NG")
    assert not inv.allows_word_initial("ZH")
    assert not inv.allows_word_final("HH")
    assert inv.allows_word_initial("S")
    assert inv.allows_word_final("NG")   # "sing"
    assert inv.allows_word_final("R")    # rhotic GA: "car" is K AA R


def test_phonotactic_position_is_meaningless_for_vowels():
    with pytest.raises(ValueError, match="vowel"):
        inv.allows_word_initial("IY")


def test_every_consonant_is_allowed_somewhere():
    for consonant in inv.CONSONANTS:
        assert inv.allows_word_initial(consonant) or inv.allows_word_final(consonant)


# ---------------------------------------------------------------------------
# Feature similarity (shapes the Task 20 prior)
# ---------------------------------------------------------------------------

def test_similarity_is_one_for_identity_and_bounded_everywhere():
    for a in inv.PHONEMES:
        assert inv.feature_similarity(a, a) == 1.0
        for b in inv.PHONEMES:
            assert 0.0 <= inv.feature_similarity(a, b) <= 1.0


def test_similarity_is_symmetric():
    for a in inv.PHONEMES:
        for b in inv.PHONEMES:
            assert inv.feature_similarity(a, b) == inv.feature_similarity(b, a)


def test_similarity_is_zero_across_the_vowel_consonant_divide():
    assert inv.feature_similarity("P", "IY") == 0.0
    assert inv.feature_similarity("AA", "K") == 0.0


def test_voicing_pairs_are_more_similar_than_unrelated_consonants():
    """The prior should rate /p/->/b/ likelier than /p/->/ng/."""
    assert inv.feature_similarity("P", "B") > inv.feature_similarity("P", "NG")
    assert inv.feature_similarity("S", "Z") > inv.feature_similarity("S", "M")
    # Gliding (/r/,/l/ -> /w/) is a documented process the prior should not
    # treat as maximally unlikely.
    assert inv.feature_similarity("R", "W") > 0.0


# ---------------------------------------------------------------------------
# Mapping: espeak IPA -> canonical
# ---------------------------------------------------------------------------

def test_every_espeak_symbol_maps_into_the_inventory():
    for symbol, canonical in mp.ESPEAK_TO_CANONICAL.items():
        assert canonical, f"{symbol!r} maps to nothing"
        for phoneme in canonical:
            assert phoneme in inv.PHONEME_SET


def test_no_canonical_phoneme_is_unreachable_from_the_recognizer():
    """If a phoneme can never be produced, the profile row for it stays at the
    prior forever and the coverage checker could never be satisfied."""
    reachable = {p for v in mp.ESPEAK_TO_CANONICAL.values() for p in v}
    assert reachable == inv.PHONEME_SET, sorted(inv.PHONEME_SET - reachable)


def test_affricates_tokenize_as_one_symbol_not_two():
    assert mp.tokenize_espeak("tʃ") == ["tʃ"]
    assert mp.espeak_to_canonical("tʃ") == ["CH"]
    assert mp.espeak_to_canonical("dʒ") == ["JH"]
    # ...but a genuine /t/ + /ʃ/ sequence across a boundary still works when
    # espeak separates them.
    assert mp.espeak_to_canonical("t ʃ") == ["T", "SH"]


def test_diphthongs_tokenize_as_one_symbol():
    assert mp.espeak_to_canonical("aɪ") == ["AY"]
    assert mp.espeak_to_canonical("oʊ") == ["OW"]
    assert mp.espeak_to_canonical("eɪ") == ["EY"]
    assert mp.espeak_to_canonical("ɔɪ") == ["OY"]
    assert mp.espeak_to_canonical("aʊ") == ["AW"]


def test_stress_and_length_marks_are_dropped():
    assert mp.espeak_to_canonical("ˈwɔːtɚ") == ["W", "AO", "T", "ER"]
    assert mp.espeak_to_canonical("ˌhɛˈloʊ") == ["HH", "EH", "L", "OW"]


def test_spaced_and_runtogether_output_agree():
    """Checkpoints differ in whether they space out phonemes; both must work."""
    assert mp.espeak_to_canonical("h ɛ l oʊ") == mp.espeak_to_canonical("hɛloʊ")


def test_both_script_g_and_ascii_g_reach_G():
    assert mp.espeak_to_canonical("ɡ") == ["G"]  # ɡ as espeak emits it
    assert mp.espeak_to_canonical("g") == ["G"]


def test_documented_normalizations():
    assert mp.espeak_to_canonical("ɾ") == ["T"]    # flap
    assert mp.espeak_to_canonical("ʔ") == ["T"]    # glottal stop
    assert mp.espeak_to_canonical("ə") == ["AH"]   # schwa is AH0 in CMUdict
    assert mp.espeak_to_canonical("ɚ") == ["ER"]
    assert mp.espeak_to_canonical("ɫ") == ["L"]    # dark l


def test_centering_diphthongs_expand_to_two_phonemes():
    assert mp.espeak_to_canonical("ɪə") == ["IH", "R"]


def test_unknown_symbol_raises_with_a_useful_message():
    with pytest.raises(mp.UnknownPhonemeError) as excinfo:
        mp.espeak_to_canonical("hɛɮoʊ")  # ɮ is not an English phone
    message = str(excinfo.value)
    assert "U+026E" in message
    assert "ESPEAK_TO_CANONICAL" in message


def test_a_realistic_utterance_round_trips():
    # "I want water" as en-us espeak would render it.
    assert mp.espeak_to_canonical("aɪ wɑnt wɔːtɚ") == [
        "AY", "W", "AA", "N", "T", "W", "AO", "T", "ER",
    ]
    inv.validate_sequence(mp.espeak_to_canonical("aɪ wɑnt wɔːtɚ"))


# ---------------------------------------------------------------------------
# Mapping: ARPAbet -> canonical
# ---------------------------------------------------------------------------

def test_arpabet_stress_digits_are_stripped():
    assert mp.arpabet_to_canonical(["AH0", "B", "AW1", "T"]) == ["AH", "B", "AW", "T"]


def test_arpabet_accepts_a_space_separated_string():
    assert mp.to_canonical("W AO1 T ER0", source="arpabet") == ["W", "AO", "T", "ER"]


def test_arpabet_is_case_insensitive_and_skips_blanks():
    assert mp.arpabet_to_canonical(["w", "ao1", "", "  ", "t", "er0"]) == [
        "W", "AO", "T", "ER",
    ]


def test_arpabet_rejects_unknown_symbols():
    with pytest.raises(mp.UnknownPhonemeError):
        mp.arpabet_to_canonical(["W", "AX"])    # AX is not in CMUdict's set
    with pytest.raises(mp.UnknownPhonemeError):
        mp.arpabet_to_canonical(["ɑ"])          # IPA passed as ARPAbet


def test_every_canonical_phoneme_survives_an_arpabet_round_trip():
    assert mp.arpabet_to_canonical(list(inv.PHONEMES)) == list(inv.PHONEMES)


def test_to_canonical_rejects_an_unknown_source():
    with pytest.raises(ValueError, match="unknown source"):
        mp.to_canonical("hɛloʊ", source="ipa")


def test_display_table_covers_the_inventory_exactly():
    assert set(mp.CANONICAL_TO_IPA) == inv.PHONEME_SET
