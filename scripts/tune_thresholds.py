#!/usr/bin/env python
"""Choose MARGIN and ABSOLUTE_MAX from the speaker's own recordings.

``config.py`` ships defaults picked before anyone had recorded anything, and
says to tune them on real recordings. This turns that note into a command.

It needs no new recordings. Every enrolled phrase already has several samples
of the same words by the same person, so holding one out and matching it
against the rest reproduces exactly what recognition does at runtime:

    for each sample s of phrase P:
        correct = best DTW distance from s to P's *other* samples
        rival   = best DTW distance from s to every other phrase's samples
        margin  = (rival - correct) / rival      # as matcher.py computes it

``correct`` tells you how far apart two recordings of the same phrase by the
same person land, which is what ABSOLUTE_MAX has to sit above. ``margin``
tells you how much daylight there is between the right answer and the next
one, which is what MARGIN has to sit below.

What the two thresholds actually do (both only in ``_is_confident``):

  ABSOLUTE_MAX  the top match must score at or below it to be spoken without
                asking. It does NOT drop candidates — a worse score still
                appears in the top-3 picker. Too tight means more tapping;
                too loose means the wrong phrase is spoken aloud unasked.
  MARGIN        the *relative* gap to the runner-up, (second-best)/second.

Both failures are recoverable by tapping, but only one of them says the
wrong thing out loud, so this leans conservative.

Usage:
    python scripts/tune_thresholds.py                 # uses DATA_DIR
    python scripts/tune_thresholds.py --data-dir data/alt
    python scripts/tune_thresholds.py --json          # machine-readable
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.services.features import FeatureCache, get_extractor  # noqa: E402
from app.services.matcher import dtw_distance, feature_matrix  # noqa: E402
from app.services.storage import PhraseStore  # noqa: E402

# Keeping a true match out of the picker is worse than asking, so leave the
# ceiling above the worst same-phrase distance seen rather than level with it.
ABSOLUTE_MAX_HEADROOM = 1.15
# Sit below the weakest real margin so genuine matches still pass, with a
# little room for a recording worse than any in this set.
MARGIN_SAFETY = 0.8


def distance_between(a: np.ndarray, b: np.ndarray) -> float:
    """Path-normalised DTW distance, exactly as DTWMatcher scores it."""
    return dtw_distance(a, b)


def load_features(store: PhraseStore, cache: FeatureCache) -> dict:
    """{phrase_id: (text, {sample_id: features})} for every readable sample."""
    loaded: dict[str, tuple[str, dict[str, np.ndarray]]] = {}
    for phrase in store.list_phrases():
        samples: dict[str, np.ndarray] = {}
        for sample in phrase.samples:
            try:
                samples[sample.id] = feature_matrix(
                    cache.get_or_extract(
                        phrase.id, sample.id, store.sample_path(phrase.id, sample.id)
                    )
                )
            except (OSError, ValueError) as error:
                print(f"  ! skipping {phrase.text!r}/{sample.id}: {error}", file=sys.stderr)
        if samples:
            loaded[phrase.id] = (phrase.text, samples)
    return loaded


def evaluate(loaded: dict) -> list[dict]:
    """One leave-one-out trial per sample."""
    trials = []
    for phrase_id, (text, samples) in loaded.items():
        for sample_id, query in samples.items():
            own = [
                distance_between(query, other)
                for other_id, other in samples.items()
                if other_id != sample_id
            ]
            rivals = [
                (distance_between(query, other), rival_text)
                for rival_id, (rival_text, rival_samples) in loaded.items()
                if rival_id != phrase_id
                for other in rival_samples.values()
            ]
            if not own:
                # A phrase with one sample cannot be scored against itself.
                continue
            correct = min(own)
            best_rival, rival_text = min(rivals, default=(None, None))
            trials.append(
                {
                    "phrase": text,
                    "sample_id": sample_id,
                    "correct": correct,
                    "rival": best_rival,
                    "rival_phrase": rival_text,
                    # matcher.py's relative margin; None when nothing to rival it.
                    "margin": None
                    if not best_rival
                    else (best_rival - correct) / best_rival,
                    "would_rank_first": best_rival is None or correct < best_rival,
                }
            )
    return trials


def summarise(values: list[float]) -> dict:
    array = np.array(values, dtype=float)
    return {
        "n": int(array.size),
        "min": float(array.min()),
        "median": float(np.median(array)),
        "max": float(array.max()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--json", action="store_true", help="print JSON only")
    args = parser.parse_args()

    settings = get_settings()
    data_dir = args.data_dir or Path(settings.DATA_DIR)
    store = PhraseStore(data_dir)
    cache = FeatureCache(data_dir, get_extractor(settings.FEATURE_EXTRACTOR))

    loaded = load_features(store, cache)
    if not loaded:
        print(f"No phrases with samples in {data_dir}. Enrol some first.", file=sys.stderr)
        return 1

    trials = evaluate(loaded)
    if not trials:
        print(
            "Every phrase has only one sample, so nothing can be held out. "
            "Record at least two of each.",
            file=sys.stderr,
        )
        return 1

    # Named so the report can say why a phrase contributed nothing.
    single = [text for text, samples in loaded.values() if len(samples) < 2]
    correct = [t["correct"] for t in trials]
    margins = [t["margin"] for t in trials if t["margin"] is not None]
    misranked = [t for t in trials if not t["would_rank_first"]]

    suggested_max = round(max(correct) * ABSOLUTE_MAX_HEADROOM, 3)
    suggested_margin = round(min(margins) * MARGIN_SAFETY, 3) if margins else None

    report = {
        "data_dir": str(data_dir),
        "phrases": len(loaded),
        "trials": len(trials),
        "skipped_single_sample": single,
        "same_phrase_distance": summarise(correct),
        "relative_margin": summarise(margins) if margins else None,
        "misranked": [
            {"phrase": t["phrase"], "beaten_by": t["rival_phrase"]} for t in misranked
        ],
        "current": {"ABSOLUTE_MAX": settings.ABSOLUTE_MAX, "MARGIN": settings.MARGIN},
        "suggested": {"ABSOLUTE_MAX": suggested_max, "MARGIN": suggested_margin},
    }

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"\n{len(loaded)} phrases, {len(trials)} held-out trials, from {data_dir}")
    if single:
        print(
            f"  {len(single)} phrase(s) have only one recording and were skipped: "
            + ", ".join(repr(text) for text in single)
        )
    print()

    same = report["same_phrase_distance"]
    print("Same phrase, different recording (what ABSOLUTE_MAX must clear):")
    print(f"  best {same['min']:.3f}   median {same['median']:.3f}   worst {same['max']:.3f}")

    if margins:
        gap = report["relative_margin"]
        print("\nGap to the nearest other phrase (what MARGIN must sit under):")
        print(f"  tightest {gap['min']:.3f}   median {gap['median']:.3f}   widest {gap['max']:.3f}")
    else:
        print("\nOnly one phrase enrolled, so there is no rival to measure a margin against.")

    if misranked:
        print(f"\n{len(misranked)} recording(s) matched the WRONG phrase first:")
        for item in misranked:
            print(f"  {item['phrase']!r} lost to {item['beaten_by']!r}")
        print("  No threshold fixes this. Re-record those, or make the phrases")
        print("  less alike — thresholds only decide whether to ask, not what wins.")

    print(f"\nCurrently: ABSOLUTE_MAX={settings.ABSOLUTE_MAX}  MARGIN={settings.MARGIN}")
    print(f"Suggested: ABSOLUTE_MAX={suggested_max}", end="")
    print(f"  MARGIN={suggested_margin}" if suggested_margin is not None else "")

    if suggested_max > settings.ABSOLUTE_MAX:
        print(
            f"\n  ABSOLUTE_MAX is tighter than this speaker's own recordings "
            f"(worst {same['max']:.3f}). They will be asked to confirm things "
            f"the app actually got right."
        )
    if margins and suggested_margin is not None and suggested_margin < settings.MARGIN:
        print(
            f"\n  MARGIN is wider than the tightest real gap ({gap['min']:.3f}), "
            f"so those matches go to the picker instead of being spoken."
        )

    print(
        f"\n{len(trials)} trials is a small sample. Treat these as a starting "
        "point, and prefer asking too often over speaking the wrong words.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
