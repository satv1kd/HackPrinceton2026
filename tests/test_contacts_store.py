"""Tests for the contacts store (Task 13)."""

from __future__ import annotations

import json

import pytest

from app.stores.contacts import Contact, ContactsError, ContactStore, normalize_handle

MOM = {"id": "mom", "name": "Mom", "handle": "+15551234567", "emoji": "👩"}
CAFE = {"id": "cafe", "name": "Café", "handle": "cafe@example.com"}


@pytest.fixture
def store(tmp_path):
    return ContactStore(path=tmp_path / "contacts.json")


def write(store, records):
    store.path.write_text(json.dumps(records))
    return store


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def test_missing_file_is_not_an_error(store):
    """Normal before the user sets any contacts up; sending is just absent."""
    assert store.load() == []


def test_loads_contacts(store):
    write(store, [MOM, CAFE])
    contacts = store.load()
    assert [c.id for c in contacts] == ["mom", "cafe"]
    assert contacts[0].name == "Mom"
    assert contacts[0].emoji == "👩"
    assert contacts[1].emoji is None
    assert contacts[1].is_email


def test_accepts_an_object_wrapper(store):
    store.path.write_text(json.dumps({"contacts": [MOM]}))
    assert len(store.load()) == 1


def test_get_returns_one_or_none(store):
    write(store, [MOM, CAFE])
    assert store.get("mom").name == "Mom"
    assert store.get("nobody") is None


# ---------------------------------------------------------------------------
# Validation: a typo should not become a silent delivery failure
# ---------------------------------------------------------------------------

def test_invalid_json_raises(store):
    store.path.write_text("{ not json")
    with pytest.raises(ContactsError, match="not valid JSON"):
        store.load()


def test_wrong_top_level_type_raises(store):
    store.path.write_text(json.dumps("mom"))
    with pytest.raises(ContactsError, match="must hold a list"):
        store.load()


@pytest.mark.parametrize(
    "record, expected",
    [
        ({"name": "Mom", "handle": "+15551234567"}, "missing required field"),
        ({"id": "mom", "handle": "+15551234567"}, "missing required field"),
        ({"id": "mom", "name": "Mom"}, "missing required field"),
        ({"id": "mom", "name": "  ", "handle": "+15551234567"}, "missing required field"),
        ({"id": "Mom!", "name": "Mom", "handle": "+15551234567"}, "must be lowercase"),
        ({"id": "mom", "name": "Mom", "handle": "not-a-number"}, "neither an email"),
        ({"id": "mom", "name": "Mom", "handle": "12345"}, "neither an email"),
        ({"id": "mom", "name": "Mom", "handle": "mom@@example.com"}, "malformed email"),
    ],
)
def test_malformed_contacts_raise(store, record, expected):
    write(store, [record])
    with pytest.raises(ContactsError, match=expected):
        store.load()


def test_duplicate_ids_raise(store):
    write(store, [MOM, dict(MOM, name="Mum")])
    with pytest.raises(ContactsError, match="duplicate contact id"):
        store.load()


def test_a_non_object_entry_raises(store):
    write(store, ["mom"])
    with pytest.raises(ContactsError, match="expected an object"):
        store.load()


# ---------------------------------------------------------------------------
# Handles
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, normalized",
    [
        ("(555) 123-4567", "5551234567"),
        ("+1 555-123-4567", "+15551234567"),
        ("  +15551234567 ", "+15551234567"),
        ("Mom@Example.com", "Mom@Example.com"),  # Apple IDs left untouched
    ],
)
def test_handle_normalization(raw, normalized):
    assert normalize_handle(raw) == normalized


def test_handle_hint_masks_the_number():
    assert Contact("mom", "Mom", "+15551234567").handle_hint == "…4567"
    assert Contact("cafe", "Café", "cafe@example.com").handle_hint == "c…@example.com"
    assert Contact("short", "Short", "1234567").handle_hint == "…4567"


def test_public_projection_withholds_the_handle():
    """A result card may be held up to a stranger; it should not carry a
    phone number."""
    public = Contact("mom", "Mom", "+15551234567", "👩").to_public_json()
    assert public == {"id": "mom", "name": "Mom", "emoji": "👩", "handle_hint": "…4567"}
    assert "handle" not in public
    assert "+15551234567" not in json.dumps(public)


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------

def test_save_round_trips(store):
    contacts = [Contact("mom", "Mom", "+15551234567", "👩"), Contact("cafe", "Café", "c@e.com")]
    store.save(contacts)
    assert store.load() == contacts


def test_save_is_atomic_and_leaves_no_temp_file(store):
    store.save([Contact("mom", "Mom", "+15551234567")])
    assert store.path.exists()
    assert not list(store.path.parent.glob("*.tmp"))


def test_save_creates_the_directory(tmp_path):
    store = ContactStore(path=tmp_path / "nested" / "deeper" / "contacts.json")
    store.save([Contact("mom", "Mom", "+15551234567")])
    assert store.load()[0].name == "Mom"


def test_save_refuses_to_write_what_load_would_reject(store):
    """Otherwise a bad write would only surface on the next read."""
    with pytest.raises(ContactsError):
        store.save([Contact("Bad Id!", "Mom", "+15551234567")])
    assert not store.path.exists()


def test_saved_emoji_is_not_escaped(store):
    store.save([Contact("mom", "Mom", "+15551234567", "👩")])
    assert "👩" in store.path.read_text()
