"""User preferences that change at runtime (Task 16).

Settings (``app.config``) are fixed per process; preferences are what the user
flips in the UI and expects to survive a restart. Today that is one switch:
``phonetic_mode``, which adds the user's own sound profile to recognition
(PRD F6) between saved-phrase matching and the speech-to-text fallback.

Stored in ``DATA_DIR/preferences.json`` with the same atomic write as
``phrases.json``. A missing or unreadable file means "defaults", never an
error: a corrupt preference must not stop someone from being understood.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from pydantic import BaseModel, ConfigDict, ValidationError

from app.services.storage import atomic_write_json

logger = logging.getLogger(__name__)

PREFERENCES_FILENAME = "preferences.json"


class Preferences(BaseModel):
    model_config = ConfigDict(extra="ignore")

    phonetic_mode: bool = False


class PreferencesUpdate(BaseModel):
    """Body of ``PUT /api/preferences``. Fields left out are unchanged."""

    model_config = ConfigDict(extra="forbid")

    phonetic_mode: Optional[bool] = None


class PreferenceStore:
    def __init__(self, data_dir: Path, defaults: Optional[Preferences] = None):
        self.path = Path(data_dir) / PREFERENCES_FILENAME
        self.defaults = defaults or Preferences()
        self._lock = threading.Lock()

    def get(self) -> Preferences:
        with self._lock:
            return self._load()

    def update(self, **changes) -> Preferences:
        changes = {key: value for key, value in changes.items() if value is not None}
        with self._lock:
            updated = self._load().model_copy(update=changes)
            # Re-validate: model_copy does not.
            updated = Preferences.model_validate(updated.model_dump())
            atomic_write_json(self.path, updated.model_dump())
            return updated

    def _load(self) -> Preferences:
        if not self.path.exists():
            return self.defaults.model_copy()
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
            return Preferences.model_validate({**self.defaults.model_dump(), **stored})
        except (OSError, ValueError, ValidationError) as error:
            logger.warning("ignoring unreadable %s (%s); using defaults", self.path, error)
            return self.defaults.model_copy()


# ---------------------------------------------------------------------------
# Phonetic readiness
# ---------------------------------------------------------------------------
# Whether phonetic mode can actually run is a chain of conditions that later
# tasks fill in: phoneme model installed (25), sound set recorded (23/24),
# profile built (27). Each registers a check; the first failing one supplies
# the reason the UI shows next to the switch.


@dataclass(frozen=True)
class Readiness:
    ready: bool
    reason: Optional[str] = None


#: A check returns None when satisfied, or a user-facing reason when not.
ReadinessCheck = Callable[[], Optional[str]]


def _engine_not_built() -> Optional[str]:
    # Replaced by real checks as Tasks 25-30 land.
    return "The sound-learning engine isn't built yet."


class PhoneticReadiness:
    def __init__(self, checks: Optional[List[ReadinessCheck]] = None):
        self.checks: List[ReadinessCheck] = list(checks) if checks is not None else [_engine_not_built]

    def add(self, check: ReadinessCheck) -> None:
        self.checks.append(check)

    def evaluate(self) -> Readiness:
        for check in self.checks:
            try:
                reason = check()
            except Exception:  # a broken check must not break /api/preferences
                logger.exception("phonetic readiness check failed")
                reason = "Couldn't check whether sound learning is ready."
            if reason:
                return Readiness(False, reason)
        return Readiness(True)
