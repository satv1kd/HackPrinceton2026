"""Disk-backed phrase store: `phrases.json` + audio/image files, no database.

Layout (relative to DATA_DIR):
  phrases.json
  audio/<phrase_id>/<sample_id>.wav
  images/<phrase_id>.<ext>

All read-modify-write cycles on phrases.json happen under one re-entrant lock,
and every write is atomic (temp file in the same directory + fsync + os.replace),
so a crash can never leave a half-written phrases.json behind.
"""

import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Optional

from app.models.schemas import Phrase, Sample

ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"}


class NotFoundError(KeyError):
    pass


def atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class PhraseStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.phrases_file = self.data_dir / "phrases.json"
        self.audio_dir = self.data_dir / "audio"
        self.images_dir = self.data_dir / "images"
        self._lock = threading.RLock()
        for d in (self.data_dir, self.audio_dir, self.images_dir):
            d.mkdir(parents=True, exist_ok=True)
        if not self.phrases_file.exists():
            atomic_write_json(self.phrases_file, [])

    # -- internal -----------------------------------------------------------
    def _load(self) -> list[Phrase]:
        with open(self.phrases_file, encoding="utf-8") as f:
            return [Phrase.model_validate(p) for p in json.load(f)]

    def _save(self, phrases: list[Phrase]) -> None:
        atomic_write_json(self.phrases_file, [p.model_dump(mode="json") for p in phrases])

    @staticmethod
    def _find(phrases: list[Phrase], phrase_id: str) -> Phrase:
        for p in phrases:
            if p.id == phrase_id:
                return p
        raise NotFoundError(phrase_id)

    # -- paths --------------------------------------------------------------
    def sample_path(self, phrase_id: str, sample_id: str) -> Path:
        return self.audio_dir / phrase_id / f"{sample_id}.wav"

    def image_path(self, phrase: Phrase) -> Optional[Path]:
        return self.data_dir / phrase.image if phrase.image else None

    # -- phrases ------------------------------------------------------------
    def list_phrases(self) -> list[Phrase]:
        with self._lock:
            return self._load()

    def get_phrase(self, phrase_id: str) -> Phrase:
        with self._lock:
            return self._find(self._load(), phrase_id)

    def create_phrase(
        self,
        text: str,
        emoji: Optional[str] = None,
        personal_word: bool = False,
        origin: str = "custom",
        phonemes: Optional[list[str]] = None,
    ) -> Phrase:
        text = text.strip()
        if not text:
            raise ValueError("Phrase text must not be empty")
        phrase = Phrase(
            text=text,
            emoji=emoji or None,
            personal_word=personal_word,
            origin=origin,
            phonemes=None if personal_word else phonemes,
        )
        with self._lock:
            phrases = self._load()
            phrases.append(phrase)
            self._save(phrases)
        return phrase

    def delete_phrase(self, phrase_id: str) -> None:
        with self._lock:
            phrases = self._load()
            phrase = self._find(phrases, phrase_id)
            phrases.remove(phrase)
            self._save(phrases)
            shutil.rmtree(self.audio_dir / phrase_id, ignore_errors=True)
            img = self.image_path(phrase)
            if img:
                img.unlink(missing_ok=True)

    def save_image(self, phrase_id: str, image_bytes: bytes, ext: str) -> Phrase:
        ext = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
        if ext not in ALLOWED_IMAGE_EXTS:
            raise ValueError(f"Unsupported image type {ext!r}")
        with self._lock:
            phrases = self._load()
            phrase = self._find(phrases, phrase_id)
            old = self.image_path(phrase)
            rel = f"images/{phrase_id}{ext}"
            atomic_write_bytes(self.data_dir / rel, image_bytes)
            if old and old != self.data_dir / rel:
                old.unlink(missing_ok=True)
            phrase.image = rel
            self._save(phrases)
            return phrase

    # -- samples ------------------------------------------------------------
    def add_sample(
        self,
        phrase_id: str,
        wav_bytes: bytes,
        source: str = "enroll",
        duration_sec: Optional[float] = None,
    ) -> Sample:
        """Store an already-preprocessed WAV as a new sample of the phrase.

        When the phrase exceeds `max_samples`, the oldest non-enrollment samples are evicted
        (handled by `evict_overflow`, called separately so callers can clean caches).
        """
        sample = Sample(source=source, duration_sec=duration_sec)
        with self._lock:
            phrases = self._load()
            phrase = self._find(phrases, phrase_id)
            atomic_write_bytes(self.sample_path(phrase_id, sample.id), wav_bytes)
            phrase.samples.append(sample)
            self._save(phrases)
        return sample

    def delete_sample(self, phrase_id: str, sample_id: str) -> None:
        with self._lock:
            phrases = self._load()
            phrase = self._find(phrases, phrase_id)
            before = len(phrase.samples)
            phrase.samples = [s for s in phrase.samples if s.id != sample_id]
            if len(phrase.samples) == before:
                raise NotFoundError(sample_id)
            self._save(phrases)
            self.sample_path(phrase_id, sample_id).unlink(missing_ok=True)

    def evict_overflow(self, phrase_id: str, max_samples: int) -> list[str]:
        """Drop the oldest "feedback" samples beyond `max_samples`. Enrollment samples are
        never evicted (PRD F4). Returns the evicted sample ids."""
        with self._lock:
            phrases = self._load()
            phrase = self._find(phrases, phrase_id)
            overflow = len(phrase.samples) - max_samples
            if overflow <= 0:
                return []
            feedback = sorted(
                (s for s in phrase.samples if s.source == "feedback"), key=lambda s: s.created_at
            )
            evicted = {s.id for s in feedback[:overflow]}
            if not evicted:
                return []
            phrase.samples = [s for s in phrase.samples if s.id not in evicted]
            self._save(phrases)
            for sid in evicted:
                self.sample_path(phrase_id, sid).unlink(missing_ok=True)
            return sorted(evicted)
