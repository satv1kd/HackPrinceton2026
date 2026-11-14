import json
import os

import pytest

from app.services.storage import NotFoundError, PhraseStore, atomic_write_json


@pytest.fixture
def store(tmp_path):
    return PhraseStore(tmp_path / "data")


def test_init_creates_layout(store):
    assert json.loads(store.phrases_file.read_text()) == []
    assert store.audio_dir.is_dir() and store.images_dir.is_dir()


def test_failed_write_keeps_old_file(tmp_path, monkeypatch):
    path = tmp_path / "x.json"
    atomic_write_json(path, {"v": 1})

    def broken_replace(*a, **k):
        raise OSError("simulated crash before rename")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError):
        atomic_write_json(path, {"v": 2})
    assert json.loads(path.read_text()) == {"v": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]  # temp file cleaned up


def test_not_found(store):
    with pytest.raises(NotFoundError):
        store.get_phrase("nope")
    with pytest.raises(NotFoundError):
        store.delete_phrase("nope")
    p = store.create_phrase("hi")
    with pytest.raises(NotFoundError):
        store.delete_sample(p.id, "nope")


def test_evict_overflow_keeps_enrollment(store):
    p = store.create_phrase("hi")
    enroll = [store.add_sample(p.id, b"RIFF", "enroll") for _ in range(3)]
    fb = [store.add_sample(p.id, b"RIFF", "feedback") for _ in range(4)]
    evicted = store.evict_overflow(p.id, max_samples=5)
    assert set(evicted) == {fb[0].id, fb[1].id}  # oldest feedback samples go first
    remaining = {s.id for s in store.get_phrase(p.id).samples}
    assert {s.id for s in enroll} <= remaining
    assert not store.sample_path(p.id, fb[0].id).exists()
    # Cap below the enrollment count: all feedback goes, enrollment samples always stay.
    assert set(store.evict_overflow(p.id, max_samples=1)) == {fb[2].id, fb[3].id}
    assert {s.id for s in store.get_phrase(p.id).samples} == {s.id for s in enroll}
    assert store.evict_overflow(p.id, max_samples=1) == []
