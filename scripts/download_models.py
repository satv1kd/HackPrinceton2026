#!/usr/bin/env python
"""Pre-download the models Heard uses, so a demo never waits on the network.

Usage:
    python scripts/download_models.py --stt        # faster-whisper (STT_MODEL_SIZE)
    python scripts/download_models.py --stt --check  # only report whether it is present

After downloading, set STT_OFFLINE=true in .env to guarantee the server never
tries the network at request time. The phoneme model (Task 25) will join this
script with --phonemes.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.stt import FasterWhisperBackend, SpeechToTextUnavailable  # noqa: E402


def stt(check_only: bool) -> bool:
    settings = get_settings()
    if not FasterWhisperBackend.package_installed():
        print("faster-whisper is not installed. Run: make install-stt")
        return False
    backend = FasterWhisperBackend(
        settings.STT_MODEL_SIZE, settings.STT_DEVICE, settings.STT_COMPUTE_TYPE, offline=check_only)
    started = time.perf_counter()
    try:
        backend.load()
    except SpeechToTextUnavailable as error:
        print(f"STT model {settings.STT_MODEL_SIZE!r}: NOT available\n  {error}")
        return False
    action = "present" if check_only else "ready"
    print(f"STT model {settings.STT_MODEL_SIZE!r}: {action} ({time.perf_counter() - started:.1f}s to load)")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stt", action="store_true", help="faster-whisper speech-to-text model")
    parser.add_argument("--check", action="store_true", help="only check; never download")
    args = parser.parse_args()
    if not args.stt:
        parser.error("choose at least one model, e.g. --stt")
    ok = stt(args.check)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
