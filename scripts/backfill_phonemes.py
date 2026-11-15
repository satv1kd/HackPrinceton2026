#!/usr/bin/env python
"""Recognize phonemes for calibration takes that have none cached yet.

For takes recorded before the phonetics install (or while the server was
down). Safe to re-run: cached takes are skipped unless --force.

Usage:
    python scripts/backfill_phonemes.py [--force]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.phonetics.service import PhonemeService, take_cache_id  # noqa: E402
from app.services.calibration import CalibrationStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="recompute even cached takes")
    args = parser.parse_args()

    settings = get_settings()
    service = PhonemeService.from_settings(settings)
    reason = service.unavailable_reason()
    if reason:
        print(reason)
        return 1
    store = CalibrationStore(settings.DATA_DIR)
    done = skipped = 0
    started = time.perf_counter()
    for record in store.list_sentences():
        cache_id = take_cache_id(record["id"], int(record.get("take", 1) or 1))
        if service.cached(cache_id) and not args.force:
            skipped += 1
            continue
        path = store.resolve(record.get("audio_path") or "")
        if path is None:
            continue
        result = service.recognizer.recognize(path, cache_id=cache_id, use_cache=False)
        done += 1
        print(f"  {cache_id}: {' '.join(result.phonemes)}")
    print(f"recognized {done}, skipped {skipped} cached, in {time.perf_counter() - started:.1f}s")
    service.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
