"""Task 24: enrollment phrases on the calibration page, two takes each,
saved as Talk phrases."""

import pytest
from fastapi.testclient import TestClient

from app.phonetics.enrollment import TAKES_PER_PHRASE, load_enrollment_set
from tests.conftest import tone, wav_bytes

PHRASE = load_enrollment_set()[0]


@pytest.fixture
def client(data_dir):
    from app.main import app

    with TestClient(app) as c:
        yield c


def upload(client, take=1, text="anything the page says", phrase_id=PHRASE.id, seconds=1.5):
    return client.post("/api/calibration/sentences",
                       data={"sentence_id": phrase_id, "text": text, "take": str(take)},
                       files={"audio": ("t.wav", wav_bytes(tone(seconds)), "audio/wav")})


def talk_phrases(client):
    return [p for p in client.get("/api/phrases").json() if p["origin"] == "enrollment"]


def test_enrollment_set_endpoint(client):
    body = client.get("/api/calibration/enrollment-set").json()
    assert body["takes_per_phrase"] == TAKES_PER_PHRASE == 2
    assert [p["id"] for p in body["phrases"]] == [p.id for p in load_enrollment_set()]
    first = body["phrases"][0]
    assert first["text"] == PHRASE.text and first["emoji"] == PHRASE.emoji
    assert first["phonemes"] == PHRASE.phonemes and first["recorded_takes"] == []


def test_two_takes_become_two_samples_of_one_talk_phrase(client):
    one = upload(client, take=1).json()
    two = upload(client, take=2).json()
    assert (one["take"], two["take"]) == (1, 2)
    assert one["text"] == PHRASE.text
    phrases = talk_phrases(client)
    assert len(phrases) == 1
    talk = phrases[0]
    assert talk["text"] == PHRASE.text and talk["emoji"] == PHRASE.emoji
    assert talk["enrollment_key"] == PHRASE.id and talk["phonemes"] == PHRASE.phonemes
    assert talk["sample_count"] == 2 and one["phrase_id"] == talk["id"]
    status = client.get("/api/calibration/enrollment-set").json()["phrases"][0]
    assert status["recorded_takes"] == [1, 2]


def test_server_text_wins_for_enrollment_phrases(client):
    body = upload(client, text="something else entirely").json()
    assert body["text"] == " ".join(PHRASE.text.split())


def test_re_recording_a_take_replaces_only_its_sample(client):
    first = upload(client, take=1).json()
    upload(client, take=2)
    talk_id = first["phrase_id"]
    before = {s["id"] for s in client.get(f"/api/phrases/{talk_id}").json()["samples"]}
    upload(client, take=1)
    after = {s["id"] for s in client.get(f"/api/phrases/{talk_id}").json()["samples"]}
    assert len(after) == 2 and len(before & after) == 1  # take 2's sample survived


def test_takes_have_their_own_audio(client):
    upload(client, take=1, seconds=1.0)
    upload(client, take=2, seconds=2.0)
    one = client.get(f"/api/calibration/sentences/{PHRASE.id}/audio?take=1")
    two = client.get(f"/api/calibration/sentences/{PHRASE.id}/audio?take=2")
    assert one.status_code == two.status_code == 200
    assert len(two.content) > len(one.content)
    assert client.get(f"/api/calibration/sentences/{PHRASE.id}/audio?take=3").status_code == 404


def test_deleting_takes_removes_their_talk_samples(client):
    upload(client, take=1)
    upload(client, take=2)
    talk_id = talk_phrases(client)[0]["id"]
    assert client.delete(f"/api/calibration/sentences/{PHRASE.id}?take=2").status_code == 204
    assert client.get(f"/api/phrases/{talk_id}").json()["sample_count"] == 1
    assert client.delete(f"/api/calibration/sentences/{PHRASE.id}").status_code == 204
    assert client.get(f"/api/phrases/{talk_id}").json()["sample_count"] == 0
    assert client.delete(f"/api/calibration/sentences/{PHRASE.id}").status_code == 404


def test_user_deleting_the_talk_phrase_does_not_break_re_recording(client):
    talk_id = upload(client, take=1).json()["phrase_id"]
    assert client.delete(f"/api/phrases/{talk_id}").status_code == 204
    again = upload(client, take=1)
    assert again.status_code == 200, again.text
    assert len(talk_phrases(client)) == 1


def test_free_text_sentences_still_work_without_a_talk_phrase(client):
    body = upload(client, phrase_id="custom1", text="anything at all").json()
    assert body["phrase_id"] is None and talk_phrases(client) == []


def test_bad_take_numbers_are_rejected(client):
    assert upload(client, take=0).status_code == 422
    assert upload(client, take=9).status_code == 422


def test_a_saved_take_is_handed_to_the_sound_engine(client, monkeypatch):
    from app.deps import get_phoneme_service, get_profile_trainer

    queued, changed = [], []
    service = get_phoneme_service()
    monkeypatch.setattr(service, "recognize_file_async",
                        lambda path, cache_id, on_done=None: (queued.append(cache_id), on_done(None)))
    monkeypatch.setattr(get_profile_trainer(), "take_changed", changed.append)
    assert upload(client, take=2).status_code == 200
    assert queued == changed == [f"cal-{PHRASE.id}-t2"]
