"""Contacts store for iMessage sending (Task 13).

The PRD keeps contacts in ``data/contacts.json`` with no database and no auth
(single user). A UI editor is a nice-to-have; hand-editing the file is the
supported path for the MVP, which means this loader has to be forgiving about
a *missing* file and unforgiving about a *malformed* one — a typo in a phone
number would otherwise surface as a silent delivery failure at demo time.

Contact shape, from the PRD::

    {"id": "mom", "name": "Mom", "handle": "+15551234567", "emoji": "👩"}
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from app import settings_bridge

logger = logging.getLogger(__name__)

DEFAULT_CONTACTS_PATH = "data/contacts.json"

_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_SEPARATORS = re.compile(r"[\s().\-]")

#: Guards writes, as the PRD requires of every JSON file on disk.
_WRITE_LOCK = threading.Lock()


class ContactsError(ValueError):
    """``contacts.json`` is malformed."""


@dataclass(frozen=True)
class Contact:
    """One iMessage recipient."""

    id: str
    name: str
    handle: str
    emoji: Optional[str] = None

    def to_json(self) -> dict:
        record = {"id": self.id, "name": self.name, "handle": self.handle}
        if self.emoji:
            record["emoji"] = self.emoji
        return record

    def to_public_json(self) -> dict:
        """The shape sent to the browser.

        The raw handle is withheld and replaced by a display hint. The client
        sends by ``contact_id``, so it never needs the number, and the result
        card has no reason to put a full phone number on a screen that may be
        held up to a stranger at a counter.
        """
        return {
            "id": self.id,
            "name": self.name,
            "emoji": self.emoji,
            "handle_hint": self.handle_hint,
        }

    @property
    def handle_hint(self) -> str:
        """A masked handle, safe to display: ``+1555…4567`` or ``m…@me.com``."""
        if "@" in self.handle:
            local, _, domain = self.handle.partition("@")
            head = local[0] if local else ""
            return f"{head}…@{domain}"
        digits = _PHONE_SEPARATORS.sub("", self.handle)
        return f"…{digits[-4:]}" if len(digits) > 4 else digits

    @property
    def is_email(self) -> bool:
        return "@" in self.handle


def normalize_handle(handle: str) -> str:
    """Strip separators from a phone number; leave an email address alone.

    ``"(555) 123-4567"`` -> ``"5551234567"``. Apple IDs are left untouched
    because local parts are case- and punctuation-sensitive.
    """
    handle = handle.strip()
    if "@" in handle:
        return handle
    return _PHONE_SEPARATORS.sub("", handle)


def _validate(record: dict, index: int) -> Contact:
    if not isinstance(record, dict):
        raise ContactsError(f"contact {index} is {type(record).__name__}, expected an object")

    missing = [key for key in ("id", "name", "handle") if not str(record.get(key, "")).strip()]
    if missing:
        raise ContactsError(
            f"contact {index} is missing required field(s): {', '.join(missing)}"
        )

    contact_id = str(record["id"]).strip()
    if not _ID_PATTERN.match(contact_id):
        raise ContactsError(
            f"contact id {contact_id!r} must be lowercase letters, digits, '-' or '_' "
            "(it appears in URLs and filenames)"
        )

    handle = normalize_handle(str(record["handle"]))
    if "@" in handle:
        if not _EMAIL_PATTERN.match(handle):
            raise ContactsError(f"contact {contact_id!r} has a malformed email handle: {handle!r}")
    else:
        digits = handle.lstrip("+")
        if not digits.isdigit() or not 7 <= len(digits) <= 15:
            raise ContactsError(
                f"contact {contact_id!r} has handle {record['handle']!r}, which is "
                "neither an email address nor a 7-15 digit phone number"
            )

    emoji = record.get("emoji")
    return Contact(
        id=contact_id,
        name=str(record["name"]).strip(),
        handle=handle,
        emoji=str(emoji).strip() if emoji else None,
    )


class ContactStore:
    """Reads and writes ``data/contacts.json``."""

    def __init__(self, path=None):
        self.path = Path(path) if path else Path(
            settings_bridge.get("CONTACTS_PATH", DEFAULT_CONTACTS_PATH)
        )

    def load(self) -> List[Contact]:
        """Load every contact.

        A missing file is normal before the user has set any up and yields an
        empty list. A *malformed* file raises, because quietly dropping a
        contact would look to the user like a send button that vanished.
        """
        if not self.path.exists():
            logger.info("no contacts file at %s; sending is unavailable", self.path)
            return []
        try:
            raw = json.loads(self.path.read_text())
        except json.JSONDecodeError as error:
            raise ContactsError(f"{self.path} is not valid JSON: {error}") from error
        except OSError as error:
            raise ContactsError(f"cannot read {self.path}: {error}") from error

        if isinstance(raw, dict):
            raw = raw.get("contacts", [])
        if not isinstance(raw, list):
            raise ContactsError(
                f"{self.path} must hold a list of contacts, or an object with a "
                f"'contacts' list; found {type(raw).__name__}"
            )

        contacts = [_validate(record, index) for index, record in enumerate(raw)]

        seen: Dict[str, int] = {}
        for index, contact in enumerate(contacts):
            if contact.id in seen:
                raise ContactsError(
                    f"duplicate contact id {contact.id!r} at positions "
                    f"{seen[contact.id]} and {index}"
                )
            seen[contact.id] = index
        return contacts

    def get(self, contact_id: str) -> Optional[Contact]:
        """One contact by id, or ``None``."""
        for contact in self.load():
            if contact.id == contact_id:
                return contact
        return None

    def save(self, contacts: Iterable[Contact]) -> None:
        """Write the contacts list atomically (temp file, then rename)."""
        payload = [contact.to_json() for contact in contacts]
        # Round-trip through validation so save() can never write a file that
        # load() would reject.
        for index, record in enumerate(payload):
            _validate(record, index)
        with _WRITE_LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
            temporary.replace(self.path)
