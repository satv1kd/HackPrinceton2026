#!/usr/bin/env python3
"""Measure phoneme recognizer latency on this machine (Task 15).

The PRD flags model latency on a laptop CPU as a risk and asks for it to be
measured early, before the decoder is built on top of it. This is that
measurement, and it doubles as the pre-demo check that the weights are
present locally.

Usage::

    python scripts/measure_phoneme_latency.py data/audio/**/*.wav
    python scripts/measure_phoneme_latency.py --model <checkpoint> clips/
    python scripts/measure_phoneme_latency.py --budget 1.5 clips/

Exits non-zero if the median real-time factor exceeds ``--budget``, so it can
run in CI once a fixture set exists.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path
from typing import List

# Make `python scripts/...` work without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.phonetics.recognizer import (  # noqa: E402
    DEFAULT_MODEL,
    PhonemeRecognizer,
    PhonemeRecognizerError,
)


def collect_wavs(targets: List[str]) -> List[Path]:
    paths: List[Path] = []
    for target in targets:
        path = Path(target)
        if path.is_dir():
            paths.extend(sorted(path.rglob("*.wav")))
        elif path.is_file():
            paths.append(path)
        else:
            print(f"skipping {target}: not a file or directory", file=sys.stderr)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="+", help="WAV files or directories")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--device", default=None, help="cpu, mps or cuda (default: auto)")
    parser.add_argument(
        "--budget",
        type=float,
        default=None,
        help="fail if the median real-time factor exceeds this",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=1,
        help="clips to run and discard first (default: 1)",
    )
    args = parser.parse_args()

    wavs = collect_wavs(args.targets)
    if not wavs:
        print("no WAV files found", file=sys.stderr)
        return 2

    from app.phonetics.recognizer import Wav2Vec2PhonemeBackend

    backend = Wav2Vec2PhonemeBackend(args.model, device=args.device)
    recognizer = PhonemeRecognizer(backend=backend, model_name=args.model)

    print(f"model: {args.model}")
    try:
        backend.load()
    except PhonemeRecognizerError as error:
        print(f"\ncannot run: {error}", file=sys.stderr)
        return 1
    print(f"device: {backend._device}\nclips: {len(wavs)}\n")

    # Caching is bypassed throughout: we are timing inference, not the cache.
    for path in wavs[: args.warmup]:
        try:
            recognizer.recognize(path, use_cache=False)
        except PhonemeRecognizerError as error:
            print(f"warmup on {path.name} failed: {error}", file=sys.stderr)

    latencies: List[float] = []
    factors: List[float] = []
    failures = 0

    for path in wavs:
        try:
            result = recognizer.recognize(path, use_cache=False)
        except PhonemeRecognizerError as error:
            print(f"{path.name}: FAILED  {error}")
            failures += 1
            continue
        latencies.append(result.inference_sec)
        if result.real_time_factor is not None:
            factors.append(result.real_time_factor)
        print(
            f"{path.name:<32} {result.audio_duration_sec:5.2f}s audio  "
            f"{result.inference_sec:6.3f}s infer  "
            f"rtf {result.real_time_factor:5.2f}  "
            f"{len(result.phonemes):3d} phonemes  "
            f"{' '.join(result.phonemes[:8])}"
        )

    if not latencies:
        print("\nevery clip failed", file=sys.stderr)
        return 1

    latencies.sort()
    median_factor = statistics.median(factors) if factors else float("nan")

    def percentile(values: List[float], fraction: float) -> float:
        index = min(len(values) - 1, int(round(fraction * (len(values) - 1))))
        return values[index]

    print(
        "\n"
        f"clips measured : {len(latencies)} ({failures} failed)\n"
        f"inference p50  : {percentile(latencies, 0.50):.3f}s\n"
        f"inference p90  : {percentile(latencies, 0.90):.3f}s\n"
        f"inference max  : {latencies[-1]:.3f}s\n"
        f"real-time p50  : {median_factor:.2f}x"
    )

    if args.budget is not None:
        verdict = "PASS" if median_factor <= args.budget else "FAIL"
        print(f"budget {args.budget:.2f}x -> {verdict}")
        if verdict == "FAIL":
            return 1
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
