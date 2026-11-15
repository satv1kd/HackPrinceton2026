"""Pydantic models for persisted data and API responses."""

import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Sample(BaseModel):
    id: str = Field(default_factory=_new_id)
    source: Literal["enroll", "feedback"] = "enroll"
    duration_sec: Optional[float] = None
    created_at: datetime = Field(default_factory=_now)


class Phrase(BaseModel):
    id: str = Field(default_factory=_new_id)
    text: str
    emoji: Optional[str] = None
    image: Optional[str] = None  # path relative to DATA_DIR, e.g. "images/<id>.jpg"
    origin: Literal["enrollment", "custom", "pack"] = "custom"
    personal_word: bool = False
    phonemes: Optional[list[str]] = None
    #: For origin="enrollment": which built-in phrase this is (Task 24).
    enrollment_key: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)
    samples: list[Sample] = Field(default_factory=list)


class PhraseOut(Phrase):
    """Phrase as returned by the API, with convenience fields for the client."""

    sample_count: int
    image_url: Optional[str] = None

    @classmethod
    def from_phrase(cls, phrase: Phrase) -> "PhraseOut":
        image_url = f"/api/phrases/{phrase.id}/image" if phrase.image else None
        return cls(**phrase.model_dump(), sample_count=len(phrase.samples), image_url=image_url)
