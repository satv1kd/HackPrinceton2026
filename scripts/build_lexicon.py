#!/usr/bin/env python
"""Build data/lexicon/words.tsv: common English words with their pronunciations.

Usage:
    python scripts/build_lexicon.py                 # LEXICON_SIZE words (default 5000)
    python scripts/build_lexicon.py --size 10000

Words from the enrollment phrases and the user's saved phrases are always
included, even when they are rare, so the decoder can say them. Needs
`make install-phonetics` (cmudict + wordfreq).

Sources: CMUdict pronunciations (BSD-style licence) and wordfreq frequencies
(code Apache-2.0, data CC-BY-SA 4.0). The output is generated locally and git
ignored, not redistributed.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.phonetics.enrollment import load_enrollment_set  # noqa: E402
from app.phonetics.lexicon import build_lexicon, default_path, tokenize  # noqa: E402


def forced_words(data_dir: Path) -> list[str]:
    words = [w for phrase in load_enrollment_set() for w in tokenize(phrase.text)]
    try:
        from app.services.storage import PhraseStore

        for phrase in PhraseStore(data_dir).list_phrases():
            if not phrase.personal_word:
                words += tokenize(phrase.text)
    except Exception:  # no phrases yet is fine
        pass
    return words


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--size", type=int, default=settings.LEXICON_SIZE)
    parser.add_argument("--out", type=Path, default=default_path(settings.DATA_DIR))
    args = parser.parse_args()

    started = time.perf_counter()
    lexicon = build_lexicon(args.size, forced_words(settings.DATA_DIR))
    lexicon.save(args.out)
    print(f"wrote {len(lexicon)} words to {args.out} in {time.perf_counter() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
