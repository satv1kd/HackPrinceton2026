"""Calibration: one sentence in, per-word clips out.

The calibration page records a whole sentence at a time. Reading aloud is
already the hard part; asking someone to also keep a live cursor in sync made
the page into a typing test, which is the opposite of the point. So the cut
happens afterwards, here, from Whisper's word timestamps.

What comes out is *raw material*, not a verdict:

    data/calibration/<sentence-id>/
        sentence.wav        the whole take, cleaned 16 kHz mono
        words/00-the.wav    one clip per word we could place in time
        ...
    data/calibration/index.json   one record per sentence

Each clip is stored next to the word the passage *asked* for, plus what
Whisper heard there - which is often not the same word, and that difference is
exactly the signal later work needs. Nothing here scores anyone, and a word we
cannot place in time is simply reported as unplaced.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import soundfile as sf

from app.services.task_io import atomic_bytes, atomic_json, data_lock

#: A little air either side of each word, so a clip does not start mid-plosive.
WORD_PAD_SEC = 0.04
#: Floor on a clip's length: Whisper sometimes reports a 10 ms word.
MIN_WORD_SEC = 0.08
_PUNCTUATION = re.compile(r"[^\w']+", re.UNICODE)
INDEX_NAME = "index.json"


def normalize_word(word: str) -> str:
    """Compare on sound-ish identity: no case, no punctuation."""
    return _PUNCTUATION.sub("", str(word).strip().lower())


def slug(word: str, fallback: str = "word") -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", normalize_word(word)).strip("-")
    return cleaned[:24] or fallback


def expected_words(text: str) -> List[str]:
    return [w for w in str(text).split() if w.strip()]


def align_words(expected: Sequence[str], heard: Sequence[str]) -> Dict[int, Tuple[int, str]]:
    """Map each expected word index to the heard word index that is its take.

    ``{expected_index: (heard_index, "exact" | "positional")}``.

    Equal runs pair off one-for-one. A run the matcher calls a *replacement* of
    the same length also pairs positionally and is marked ``"positional"``:
    that is the ordinary case where someone's "thumbs" came back as "tums", and
    throwing it away would discard the one recording we most want. Insertions,
    deletions and ragged replacements are left unpaired rather than guessed at.
    """
    exp = [normalize_word(w) for w in expected]
    got = [normalize_word(w) for w in heard]
    pairs: Dict[int, Tuple[int, str]] = {}
    for tag, i1, i2, j1, j2 in SequenceMatcher(a=exp, b=got, autojunk=False).get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                pairs[i1 + offset] = (j1 + offset, "exact")
        elif tag == "replace" and (i2 - i1) == (j2 - j1):
            for offset in range(i2 - i1):
                pairs[i1 + offset] = (j1 + offset, "positional")
    return pairs


@dataclass
class WordClip:
    index: int
    #: The word the passage asked for.
    expected: str
    #: What the recognizer heard in that slot - deliberately kept even when it
    #: differs from ``expected``.
    heard: Optional[str] = None
    start: Optional[float] = None
    end: Optional[float] = None
    #: "exact", "positional", or None when the word could not be placed.
    match: Optional[str] = None
    #: Path relative to the data directory, or None when there is no clip.
    path: Optional[str] = None
    probability: Optional[float] = None

    @property
    def placed(self) -> bool:
        return self.path is not None


@dataclass
class SentenceRecord:
    id: str
    text: str
    recorded_at: str
    duration_sec: float
    #: Which take of this phrase (1-based). Enrollment asks for two.
    take: int = 1
    #: What the recognizer made of the whole take ("" when it could not run).
    transcript: str = ""
    audio_path: Optional[str] = None
    words: List[WordClip] = field(default_factory=list)
    #: The Talk-mode phrase and sample this take was saved as (Task 24).
    phrase_id: Optional[str] = None
    sample_id: Optional[str] = None

    @property
    def placed_count(self) -> int:
        return sum(1 for w in self.words if w.placed)

    def to_json(self) -> dict:
        data = asdict(self)
        data["words"] = [asdict(w) if not isinstance(w, dict) else w for w in self.words]
        data["placed_count"] = self.placed_count
        return data


def cut_words(audio: np.ndarray, sr: int, expected: Sequence[str], heard) -> List[WordClip]:
    """Slice ``audio`` into one clip per expected word, where timings allow.

    ``heard`` is a sequence of objects with ``text``/``start``/``end``
    (``app.stt.Word``). Returns the clips *without* writing anything, each
    carrying the samples' bounds; :func:`save_sentence` does the writing.
    """
    heard = list(heard or [])
    pairs = align_words(expected, [w.text for w in heard])
    clips: List[WordClip] = []
    total = len(audio) / sr if sr else 0.0
    for index, word in enumerate(expected):
        clip = WordClip(index=index, expected=word)
        paired = pairs.get(index)
        if paired is not None:
            source = heard[paired[0]]
            start = max(0.0, float(source.start) - WORD_PAD_SEC)
            end = min(total, float(source.end) + WORD_PAD_SEC)
            if end - start >= MIN_WORD_SEC:
                clip.heard = source.text
                clip.start, clip.end = round(start, 3), round(end, 3)
                clip.match = paired[1]
                clip.probability = round(float(getattr(source, "probability", 0.0) or 0.0), 4)
        clips.append(clip)
    return clips


def _wav_bytes(samples: np.ndarray, sr: int) -> bytes:
    buffer = io.BytesIO()
    sf.write(buffer, samples, sr, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


class CalibrationStore:
    """Sentence takes and their word clips on disk, one directory per sentence."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.root = self.data_dir / "calibration"

    @property
    def index_path(self) -> Path:
        return self.root / INDEX_NAME

    def take_folder(self, sentence_id: str, take: int = 1) -> Path:
        """Take 1 keeps the original layout (``<id>/``); later takes live in
        ``<id>/take-<n>/`` so the two never share clips."""
        folder = self.root / _safe_id(sentence_id)
        return folder if take == 1 else folder / f"take-{take}"

    def save_sentence(self, sentence_id: str, text: str, audio: np.ndarray, sr: int,
                      transcript: str = "", heard=(), take: int = 1) -> SentenceRecord:
        """Write the take, cut and write its word clips, update the index.

        Re-recording a take replaces it: the old clips are removed first, so a
        new take never leaves half of an old one behind. Other takes of the
        same phrase are untouched.
        """
        sentence_id = _safe_id(sentence_id)
        take = _valid_take(take)
        expected = expected_words(text)
        clips = cut_words(audio, sr, expected, heard)
        folder = self.take_folder(sentence_id, take)
        words_dir = folder / "words"
        with data_lock(self.data_dir):
            _clear(words_dir)
            audio_path = folder / "sentence.wav"
            atomic_bytes(audio_path, _wav_bytes(audio, sr))
            for clip in clips:
                if clip.start is None or clip.end is None:
                    continue
                segment = audio[int(clip.start * sr):int(clip.end * sr)]
                if segment.size == 0:
                    clip.start = clip.end = clip.match = clip.heard = None
                    continue
                name = f"{clip.index:02d}-{slug(clip.expected)}.wav"
                atomic_bytes(words_dir / name, _wav_bytes(segment, sr))
                clip.path = str((words_dir / name).relative_to(self.data_dir))
            record = SentenceRecord(
                id=sentence_id, text=" ".join(expected),
                recorded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                duration_sec=round(len(audio) / sr, 3) if sr else 0.0,
                take=take,
                transcript=transcript,
                audio_path=str(audio_path.relative_to(self.data_dir)),
                words=clips)
            records = {_key(r): r for r in self._read_index()}
            records[(sentence_id, take)] = record.to_json()
            atomic_json(self.index_path, list(records.values()))
        return record

    def update_record(self, sentence_id: str, take: int, **fields) -> Optional[dict]:
        """Set extra fields on one take's index record (e.g. its Talk sample)."""
        key = (_safe_id(sentence_id), _valid_take(take))
        with data_lock(self.data_dir):
            records = self._read_index()
            found = None
            for record in records:
                if _key(record) == key:
                    record.update(fields)
                    found = record
            if found is not None:
                atomic_json(self.index_path, records)
            return found

    def list_sentences(self) -> List[dict]:
        with data_lock(self.data_dir):
            return self._read_index()

    def get_sentence(self, sentence_id: str, take: int = 1) -> Optional[dict]:
        wanted = (_safe_id(sentence_id), _valid_take(take))
        return next((r for r in self.list_sentences() if _key(r) == wanted), None)

    def delete_sentence(self, sentence_id: str, take: Optional[int] = None) -> List[dict]:
        """Delete one take, or every take of the phrase when *take* is None.
        Returns the deleted records (empty when there was nothing)."""
        sentence_id = _safe_id(sentence_id)
        with data_lock(self.data_dir):
            records = self._read_index()
            doomed = [r for r in records if r.get("id") == sentence_id
                      and (take is None or _key(r)[1] == _valid_take(take))]
            if take is None:
                _clear(self.root / sentence_id)
            else:
                folder = self.take_folder(sentence_id, _valid_take(take))
                for sub in ("words",):
                    _clear(folder / sub)
                (folder / "sentence.wav").unlink(missing_ok=True)
                if folder != self.root / sentence_id:
                    _clear(folder)
            atomic_json(self.index_path, [r for r in records if r not in doomed])
        return doomed

    def resolve(self, relative: str) -> Optional[Path]:
        """Turn a stored relative path back into a file inside the data dir."""
        if not relative:
            return None
        candidate = (self.data_dir / relative).resolve()
        root = self.root.resolve()
        if root not in candidate.parents or not candidate.is_file():
            return None
        return candidate

    def _read_index(self) -> List[dict]:
        try:
            value = json.loads(self.index_path.read_text())
        except (OSError, ValueError):
            return []
        return value if isinstance(value, list) else []


MAX_TAKES = 5


def _valid_take(take) -> int:
    take = int(take)
    if not 1 <= take <= MAX_TAKES:
        raise ValueError(f"take must be between 1 and {MAX_TAKES}")
    return take


def _key(record: dict):
    return (record.get("id"), int(record.get("take", 1) or 1))


def _safe_id(sentence_id: str) -> str:
    """Sentence ids come from the page, so they never become a path escape."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "", str(sentence_id).strip())[:40]
    if not cleaned:
        raise ValueError("sentence id must contain letters or digits")
    return cleaned


def _clear(folder: Path) -> None:
    if not folder.exists():
        return
    for path in sorted(folder.rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    folder.rmdir()
