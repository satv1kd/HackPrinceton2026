#!/usr/bin/env python
"""Choose the enrollment phrases: the fewest everyday phrases covering every sound.

Usage:
    python scripts/choose_enrollment_phrases.py            # choose and write the set
    python scripts/choose_enrollment_phrases.py --check    # only report coverage of the current set

Greedy set cover over CANDIDATES: repeatedly take the phrase that satisfies
the most still-unmet requirements (every phoneme >= MIN_PHONEME_OCCURRENCES,
every consonant word-initially and word-finally where English allows it),
breaking ties toward shorter phrases. Then drop any chosen phrase whose
removal keeps the set covered. Writes app/phonetics/enrollment_set.json with
each word's phonemes, so the app needs no dictionary at runtime.

Needs cmudict (make install-phonetics). Edit CANDIDATES - short, everyday,
useful in Talk mode, easy to say - and re-run.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.phonetics.coverage import check_coverage, requirements_met, total_requirements  # noqa: E402
from app.phonetics.enrollment import SET_PATH, load_enrollment_set  # noqa: E402
from app.phonetics.lexicon import cmudict_pronunciations, tokenize  # noqa: E402

# (text, emoji). Everyday phrases someone would actually want to say.
CANDIDATES = [
    ("I want water", "💧"), ("Can I have some juice", "🧃"), ("Thank you very much", "🙏"),
    ("I need help", "🆘"), ("Where is the bathroom", "🚻"), ("I feel sick", "🤒"),
    ("My head hurts", "🤕"), ("Yes please", "👍"), ("No thank you", "🙅"),
    ("Good morning", "🌅"), ("See you tomorrow", "👋"), ("I'm hungry", "🍽️"),
    ("I'm thirsty", "🥤"), ("Call my mom", "📞"), ("Turn on the TV", "📺"),
    ("Turn on the television", "📺"), ("I'm tired", "😴"), ("Can we go outside", "🌳"),
    ("Wash my hands", "🧼"), ("Put on my shoes", "👟"), ("Let's eat lunch", "🥪"),
    ("I love you", "❤️"), ("Brush my teeth", "🪥"), ("Watch the news", "📰"),
    ("Change the channel", "🎛️"), ("Come with me", "🤝"), ("Look at this book", "📖"),
    ("Is it cold outside", "🧥"), ("Shut the door", "🚪"), ("Open the garage", "🚗"),
    ("Orange juice please", "🍊"), ("The boy has a toy", "🧸"), ("How are you doing", "😊"),
    ("Where is the dog", "🐕"), ("Push the button", "🔘"), ("Zip my coat", "🧥"),
    ("Wait for me", "✋"), ("I'm okay", "👌"), ("Pizza for dinner", "🍕"),
    ("Give me a hug", "🤗"), ("Fix my chair", "🪑"), ("Pass the salt", "🧂"),
    ("Hot chocolate", "☕"), ("Read me a story", "📚"), ("I'm cold", "🥶"),
    ("Turn off the light", "💡"), ("Take a bath", "🛁"), ("Feed the cat", "🐈"),
    ("Where's my phone", "📱"), ("I'm in pain", "😣"), ("Stop that", "✋"),
    ("Let's go home", "🏠"), ("Join us for lunch", "🥪"), ("It's my birthday", "🎂"),
    ("That's enough", "🛑"), ("Big hug", "🤗"), ("Choose a movie", "🎬"),
    ("Usually I sleep late", "🛌"),
    ("Clean up the room", "🧹"), ("Grab my bag", "👜"), ("Sing a song", "🎵"),
    ("I enjoy music", "🎶"), ("Cook some eggs", "🍳"),
]


def transcribe(text):
    words = []
    for word in tokenize(text):
        prons = cmudict_pronunciations(word)
        if not prons:
            raise SystemExit(f"no CMUdict pronunciation for {word!r} in {text!r}")
        words.append(prons[0])
    return words


def choose(candidates, min_occ):
    pool = [(text, emoji, transcribe(text)) for text, emoji in candidates]
    chosen, goal = [], total_requirements(min_occ)
    while True:
        current = requirements_met([c[2] for c in chosen], min_occ)
        if current >= goal:
            break
        best = max(
            (c for c in pool if c not in chosen),
            key=lambda c: (requirements_met([x[2] for x in chosen] + [c[2]], min_occ) - current,
                           -sum(len(w) for w in c[2])),
        )
        if requirements_met([x[2] for x in chosen] + [best[2]], min_occ) == current:
            report = check_coverage([c[2] for c in chosen], min_occ)
            raise SystemExit("the candidate pool cannot cover everything:\n" + report.table())
        chosen.append(best)
    # Prune: drop phrases that are no longer needed, longest first.
    for item in sorted(chosen, key=lambda c: -sum(len(w) for w in c[2])):
        rest = [c for c in chosen if c is not item]
        if check_coverage([c[2] for c in rest], min_occ).ok:
            chosen = rest
    return chosen


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:32]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--min", type=int, default=get_settings().MIN_PHONEME_OCCURRENCES)
    args = parser.parse_args()

    if args.check:
        phrases = load_enrollment_set()
        report = check_coverage([p.words for p in phrases], args.min)
        print(report.table())
        return 0 if report.ok else 1

    chosen = choose(CANDIDATES, args.min)
    report = check_coverage([c[2] for c in chosen], args.min)
    payload = {
        "about": "Generated by scripts/choose_enrollment_phrases.py - the fewest everyday "
                 "phrases from its candidate pool that cover every English phoneme "
                 f"(>= {args.min} times) and every consonant word-initially and word-finally "
                 "where English allows. Phonemes are CMUdict, canonical stress-free ARPAbet.",
        "phrases": [{"id": slug(text), "text": text, "emoji": emoji, "words": words}
                    for text, emoji, words in chosen],
    }
    SET_PATH.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    load_enrollment_set.cache_clear()
    for text, emoji, _ in chosen:
        print(f"  {emoji} {text}")
    print()
    print(report.table())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
