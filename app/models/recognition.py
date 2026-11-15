"""Wire models owned by tasks 5–8; leaves task 2's schemas.py untouched."""
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


#: Which recognizer produced a candidate (Task 17). ``type`` says what it is
#: (a saved phrase or generated text); ``source`` says who found it - a saved
#: phrase can also be found by speech-to-text or the phonetic decoder.
CandidateSource = Literal['phrase', 'phonetic', 'stt']


class MatchCandidate(BaseModel):
    type: Literal['phrase', 'generated'] = 'phrase'
    source: CandidateSource = 'phrase'
    phrase_id: str | None = None
    text: str
    emoji: str | None = None
    image: str | None = None
    score: float = Field(ge=0, allow_inf_nan=False)


class MatchResult(BaseModel):
    attempt_id: str
    confident: bool
    candidates: list[MatchCandidate]
    #: Recognizer behind the first candidate ('phrase' when nothing matched).
    stage: CandidateSource = 'phrase'
    phonetic_mode: bool = False
    #: When phonetic mode is on but the user's-sounds stage could not run, why.
    phonetic_skipped_reason: str | None = None
    #: What local speech-to-text heard, verbatim (None when unavailable).
    #: Distinct from the candidates: a candidate may be a saved phrase
    #: substituted for these words, and the user deserves to see both.
    transcript: str | None = None


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
