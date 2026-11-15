"""Task 26: phoneme alignment."""

import random

import pytest

from app.phonetics.aligner import AlignedPair, align, default_sub_cost
from app.phonetics.inventory import DELETION, INSERTION, PHONEMES


def pairs(alignment):
    return [(p.intended, p.recognized) for p in alignment.pairs]


def test_identical_sequences():
    a = align(["W", "AO", "T", "ER"], ["W", "AO", "T", "ER"])
    assert a.cost == 0 and a.accuracy() == 1.0
    assert pairs(a) == [("W", "W"), ("AO", "AO"), ("T", "T"), ("ER", "ER")]


def test_substitution_deletion_insertion():
    assert pairs(align(["W", "AO", "T", "ER"], ["W", "AA", "ER"])) == [
        ("W", "W"), ("AO", "AA"), ("T", DELETION), ("ER", "ER")]
    assert pairs(align(["S", "IY"], ["S", "AH", "IY"])) == [("S", "S"), (INSERTION, "AH"), ("IY", "IY")]


def test_similar_sounds_are_cheaper():
    assert default_sub_cost("L", "W") < default_sub_cost("L", "K")
    assert default_sub_cost("P", "B") < default_sub_cost("P", "IY") == 1.0
    # "red" said as "wed": aligned as one cheap substitution, not delete+insert.
    a = align(["R", "EH", "D"], ["W", "EH", "D"])
    assert pairs(a)[0] == ("R", "W") and a.cost < 1.0


def test_final_consonant_deletion_pattern():
    a = align(["K", "AE", "T"], ["K", "AE"])
    assert pairs(a)[-1] == ("T", DELETION) and a.accuracy() == pytest.approx(2 / 3)


def test_ties_are_deterministic():
    runs = {tuple(pairs(align(["AA", "B"], ["B", "AA"]))) for _ in range(5)}
    assert len(runs) == 1


def test_empty_sides():
    assert pairs(align([], ["AH"])) == [(INSERTION, "AH")]
    assert pairs(align(["AH"], [])) == [("AH", DELETION)]
    assert align([], []).pairs == [] and align([], []).accuracy() == 0.0


def test_invalid_symbols_raise():
    with pytest.raises(ValueError):
        align(["XX"], ["AH"])


def test_custom_costs_are_used():
    a = align(["R"], ["W"], sub_cost=lambda e, r: 0.0 if e == r else 5.0, del_cost=1.0, ins_cost=1.0)
    # Substitution is dearer than delete + insert here, so it is split in two.
    assert sorted(pairs(a)) == sorted([("R", DELETION), (INSERTION, "W")]) and a.cost == 2.0


def test_cost_equals_sum_of_pair_costs():
    rng = random.Random(7)
    for _ in range(50):
        e = [rng.choice(PHONEMES) for _ in range(rng.randint(0, 8))]
        r = [rng.choice(PHONEMES) for _ in range(rng.randint(0, 8))]
        a = align(e, r)
        total = sum(1.0 if p.intended == INSERTION or p.recognized == DELETION
                    else default_sub_cost(p.intended, p.recognized) for p in a.pairs)
        assert a.cost == pytest.approx(total)
        assert [p.intended for p in a.pairs if p.intended != INSERTION] == e
        assert [p.recognized for p in a.pairs if p.recognized != DELETION] == r
