"""Wire models owned by tasks 5–8; leaves task 2's schemas.py untouched."""
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MatchCandidate(BaseModel):
    type: Literal['phrase', 'generated'] = 'phrase'
    phrase_id: str | None = None
    text: str
    emoji: str | None = None
    image: str | None = None
    score: float = Field(ge=0, allow_inf_nan=False)


class MatchResult(BaseModel):
    attempt_id: str
    confident: bool
    candidates: list[MatchCandidate]


class PhraseLabel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    type: Literal['phrase']
    phrase_id: str = Field(min_length=1, max_length=128)


class TextLabel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    type: Literal['text']
    text: str = Field(min_length=1, max_length=500)


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    attempt_id: UUID
    correct: bool = Field(strict=True)
    label: Annotated[PhraseLabel | TextLabel, Field(discriminator='type')] | None = None


class UndoRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    attempt_id: UUID
    event_id: UUID | None = None
