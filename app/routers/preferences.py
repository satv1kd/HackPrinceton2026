"""User preferences endpoints (Task 16): the phonetic-mode switch."""

from typing import Annotated, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.deps import get_phonetic_readiness, get_preference_store
from app.services.preferences import (
    PhoneticReadiness,
    PreferencesUpdate,
    PreferenceStore,
)

router = APIRouter(prefix="/api", tags=["preferences"])

Store = Annotated[PreferenceStore, Depends(get_preference_store)]
ReadinessDep = Annotated[PhoneticReadiness, Depends(get_phonetic_readiness)]


class PreferencesOut(BaseModel):
    phonetic_mode: bool
    #: Whether phonetic mode can actually run right now. The switch may be on
    #: while this is false; recognition then skips the phonetic stage.
    phonetic_available: bool
    phonetic_unavailable_reason: Optional[str] = None


def _out(store: PreferenceStore, readiness: PhoneticReadiness) -> PreferencesOut:
    prefs = store.get()
    state = readiness.evaluate()
    return PreferencesOut(
        phonetic_mode=prefs.phonetic_mode,
        phonetic_available=state.ready,
        phonetic_unavailable_reason=state.reason,
    )


@router.get("/preferences", response_model=PreferencesOut)
def get_preferences(store: Store, readiness: ReadinessDep):
    return _out(store, readiness)


@router.put("/preferences", response_model=PreferencesOut)
def put_preferences(body: PreferencesUpdate, store: Store, readiness: ReadinessDep):
    # Turning the mode on while it is unavailable is allowed and stored: the
    # user's intent survives until the engine is ready.
    store.update(**body.model_dump())
    return _out(store, readiness)
