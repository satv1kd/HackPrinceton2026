"""Tests for scripts/tune_thresholds.py.

`evaluate` takes feature matrices directly, so these build them by hand
instead of synthesising audio: the arithmetic under test is the leave-one-out
pairing and the relative margin, not MFCC extraction.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.tune_thresholds import (  # noqa: E402
    ABSOLUTE_MAX_HEADROOM,
    MARGIN_SAFETY,
    distance_between,
    evaluate,
    summarise,
)


def features(*rows):
    """A feature matrix of unit-ish frames, as the extractor would produce."""
    return np.array(rows, dtype=np.float64)


# Three directions in feature space: A and B are close, C is far from both.
A = features([1.0, 0.0, 0.0], [1.0, 0.0, 0.0])
A_NEAR = features([0.99, 0.10, 0.0], [0.99, 0.10, 0.0])
B = features([0.0, 1.0, 0.0], [0.0, 1.0, 0.0])
C = features([0.0, 0.0, 1.0], [0.0, 0.0, 1.0])


def corpus():
    return {
        "p1": ("I want water", {"s1": A, "s2": A_NEAR}),
        "p2": ("Please call the nurse", {"s3": B, "s4": B.copy()}),
    }


def test_each_sample_is_held_out_exactly_once():
    trials = evaluate(corpus())
    assert len(trials) == 4
    assert {t["sample_id"] for t in trials} == {"s1", "s2", "s3", "s4"}


def test_correct_distance_ignores_the_sample_itself():
    # Matching a sample against itself would score 0 and make every phrase
    # look perfectly recognisable, which is the whole trap this avoids.
    trial = next(t for t in evaluate(corpus()) if t["sample_id"] == "s1")
    assert trial["correct"] == pytest.approx(distance_between(A, A_NEAR))
    assert trial["correct"] > 0


def test_margin_matches_the_matchers_relative_formula():
    trial = next(t for t in evaluate(corpus()) if t["sample_id"] == "s1")
    expected = (trial["rival"] - trial["correct"]) / trial["rival"]
    assert trial["margin"] == pytest.approx(expected)
    assert 0 < trial["margin"] < 1


def test_identical_samples_give_a_zero_distance_not_an_error():
    # s3 and s4 are the same array; cosine_distance must stay finite.
    trial = next(t for t in evaluate(corpus()) if t["sample_id"] == "s3")
    assert trial["correct"] == pytest.approx(0.0, abs=1e-9)
    assert trial["margin"] == pytest.approx(1.0, abs=1e-6)


def test_a_sample_closer_to_another_phrase_is_flagged():
    # The failure thresholds cannot fix: an "I want water" recording that
    # lands nearer the nurse phrase than its own. The report has to say so
    # rather than quietly suggesting a looser ceiling.
    confusable = {
        "p1": ("I want water", {"s1": B, "s2": C}),
        "p2": ("Please call the nurse", {"s3": B.copy(), "s4": B.copy()}),
    }
    trials = evaluate(confusable)
    flagged = [t for t in trials if not t["would_rank_first"]]
    assert flagged, "a sample matching the wrong phrase must be flagged"
    assert flagged[0]["phrase"] == "I want water"
    assert flagged[0]["rival_phrase"] == "Please call the nurse"


def test_single_sample_phrases_contribute_no_trials():
    loaded = {"p1": ("Only one", {"s1": A}), "p2": ("Two here", {"s2": B, "s3": B.copy()})}
    trials = evaluate(loaded)
    assert {t["phrase"] for t in trials} == {"Two here"}


def test_a_lone_phrase_has_no_rival_and_no_margin():
    trials = evaluate({"p1": ("Alone", {"s1": A, "s2": A_NEAR})})
    assert len(trials) == 2
    assert all(t["margin"] is None and t["rival"] is None for t in trials)
    # With nothing to confuse it with, it still ranks first.
    assert all(t["would_rank_first"] for t in trials)


def test_summarise_reports_the_spread():
    assert summarise([0.1, 0.5, 0.3]) == {
        "n": 3,
        "min": 0.1,
        "median": 0.3,
        "max": 0.5,
    }


def test_suggestions_leave_room_in_the_safe_direction():
    # The constants must widen the ceiling and narrow the required gap;
    # inverting either would make the app speak more without being asked.
    assert ABSOLUTE_MAX_HEADROOM > 1
    assert 0 < MARGIN_SAFETY < 1
