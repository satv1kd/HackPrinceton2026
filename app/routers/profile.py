"""The personal sound profile, for the coverage screen and for debugging (Tasks 27, 31)."""

from typing import Annotated, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.deps import get_phoneme_service, get_profile_trainer
from app.phonetics.inventory import PHONEMES, is_vowel
from app.phonetics.mapping import CANONICAL_TO_IPA

router = APIRouter(prefix="/api/profile", tags=["profile"])

Trainer = Annotated[object, Depends(get_profile_trainer)]
Config = Annotated[Settings, Depends(get_settings)]

#: A familiar word for each sound, for people who don't read phonetic symbols.
EXAMPLE_WORDS = {
    "P": "pen", "B": "boy", "T": "toy", "D": "dog", "K": "cat", "G": "go", "CH": "chair",
    "JH": "juice", "F": "fish", "V": "very", "TH": "thank", "DH": "the", "S": "sun",
    "Z": "zip", "SH": "shoe", "ZH": "garage", "HH": "help", "M": "mom", "N": "no",
    "NG": "sing", "L": "light", "R": "red", "Y": "yes", "W": "water", "IY": "see",
    "IH": "sit", "EY": "day", "EH": "bed", "AE": "cat", "AA": "hot", "AO": "call",
    "OW": "go", "UH": "book", "UW": "food", "AH": "but", "ER": "her", "AW": "now",
    "AY": "my", "OY": "boy",
}


class SoundOut(BaseModel):
    phoneme: str
    ipa: str
    example: str
    vowel: bool
    examples: int
    #: Probability the recognizer hears this sound as itself, for this person.
    self_probability: float
    status: str               # "learned" | "needs_more" | "not_yet"
    pending: int = 0          # new examples since the last retrain (Task 31)


class ProfileOut(BaseModel):
    ready: bool
    reason: Optional[str] = None
    version: Optional[int] = None
    created_at: Optional[str] = None
    sources: Dict[str, int] = {}
    min_examples: int
    retrain_after: int
    sounds: List[SoundOut]
    patterns: List[dict] = []
    last_retrain: Optional[dict] = None


@router.get("", response_model=ProfileOut)
def get_profile(trainer: Trainer, settings: Config):
    profile = trainer.current()
    minimum = settings.MIN_PHONEME_OCCURRENCES
    pending = trainer.pending_counts() if hasattr(trainer, "pending_counts") else {}
    sounds = []
    for p in PHONEMES:
        n = int(round(profile.examples(p))) if profile else 0
        sounds.append(SoundOut(
            phoneme=p, ipa=CANONICAL_TO_IPA[p], example=EXAMPLE_WORDS[p], vowel=is_vowel(p),
            examples=n, self_probability=round(profile.probability(p, p), 3) if profile else 0.0,
            status="learned" if n >= minimum else "needs_more" if n else "not_yet",
            pending=int(pending.get(p, 0))))
    reason = get_phoneme_service().unavailable_reason() or trainer.readiness()
    return ProfileOut(
        ready=reason is None, reason=reason,
        version=profile.version if profile else None,
        created_at=profile.created_at if profile else None,
        sources=profile.sources if profile else {}, min_examples=minimum,
        retrain_after=settings.RETRAIN_MIN_NEW_EXAMPLES, sounds=sounds,
        patterns=profile.top_confusions() if profile else [],
        last_retrain=trainer.last_retrain() if hasattr(trainer, "last_retrain") else None)


@router.post("/rebuild", response_model=ProfileOut)
def rebuild(trainer: Trainer, settings: Config):
    """Rebuild from every recording now (normally automatic)."""
    trainer.rebuild()
    return get_profile(trainer, settings)
