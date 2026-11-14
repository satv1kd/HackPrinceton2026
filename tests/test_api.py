import json
from concurrent.futures import ThreadPoolExecutor

from tests.conftest import encode, tone, wav_bytes


def _create(client, text="I want water", **fields):
    r = client.post("/api/phrases", data={"text": text, **fields})
    assert r.status_code == 201, r.text
    return r.json()


def _upload(client, phrase_id, raw, mime="audio/webm", name="rec.webm"):
    return client.post(f"/api/phrases/{phrase_id}/samples", files={"audio": (name, raw, mime)})


def test_health_and_index(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok" and r.json()["ffmpeg"] is True
    r = client.get("/")
    assert r.status_code == 200 and "Heard" in r.text


def test_settings_from_env(monkeypatch, data_dir):
    from app.config import get_settings

    monkeypatch.setenv("MARGIN", "0.42")
    get_settings.cache_clear()
    s = get_settings()
    assert s.MARGIN == 0.42 and s.DATA_DIR == data_dir


def test_create_list_get_phrase(client, data_dir):
    p = _create(client, emoji="💧")
    assert p["origin"] == "custom" and p["sample_count"] == 0 and p["emoji"] == "💧"
    listed = client.get("/api/phrases").json()
    assert [x["id"] for x in listed] == [p["id"]]
    assert client.get(f"/api/phrases/{p['id']}").json()["text"] == "I want water"
    on_disk = json.loads((data_dir / "phrases.json").read_text())
    assert on_disk[0]["id"] == p["id"]


def test_personal_word_has_no_phonemes(client):
    p = _create(client, text="wawa", personal_word="true")
    assert p["personal_word"] is True and p["phonemes"] is None


def test_empty_text_rejected(client):
    assert client.post("/api/phrases", data={"text": "   "}).status_code == 422


def test_image_upload(client, data_dir):
    r = client.post(
        "/api/phrases",
        data={"text": "coffee"},
        files={"image": ("cup.png", b"\x89PNG fake", "image/png")},
    )
    p = r.json()
    assert r.status_code == 201 and p["image"] == f"images/{p['id']}.png"
    assert (data_dir / p["image"]).read_bytes() == b"\x89PNG fake"
    assert client.get(p["image_url"]).content == b"\x89PNG fake"


def test_bad_image_type_rejected_and_not_persisted(client):
    r = client.post(
        "/api/phrases", data={"text": "x"}, files={"image": ("evil.exe", b"MZ", "application/x")}
    )
    assert r.status_code == 422
    assert client.get("/api/phrases").json() == []


def test_add_sample_webm_and_mp4(client, data_dir):
    p = _create(client)
    for fmt, mime in (("webm", "audio/webm;codecs=opus"), ("mp4", "audio/mp4")):
        r = _upload(client, p["id"], encode(tone(1.0, sr=48000), fmt), mime, f"rec.{fmt}")
        assert r.status_code == 201, r.text
        s = r.json()
        assert s["source"] == "enroll" and 0.8 < s["duration_sec"] < 1.2
        assert (data_dir / "audio" / p["id"] / f"{s['id']}.wav").exists()
        assert (data_dir / "features" / "mfcc_13_deltas" / p["id"] / f"{s['id']}.npy").exists()
        assert client.get(f"/api/phrases/{p['id']}/samples/{s['id']}/audio").status_code == 200
    assert client.get(f"/api/phrases/{p['id']}").json()["sample_count"] == 2


def test_add_sample_errors(client):
    p = _create(client)
    short = _upload(client, p["id"], wav_bytes(tone(0.1)), "audio/wav", "a.wav")
    assert short.status_code == 422 and short.json()["detail"]["code"] == "too_short"
    bad = _upload(client, p["id"], b"garbage" * 50)
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "bad_audio"
    assert _upload(client, "missing", wav_bytes(tone(1.0))).status_code == 404
    assert client.get(f"/api/phrases/{p['id']}").json()["sample_count"] == 0


def test_delete_sample(client, data_dir):
    p = _create(client)
    s = _upload(client, p["id"], wav_bytes(tone(1.0)), "audio/wav", "a.wav").json()
    assert client.delete(f"/api/phrases/{p['id']}/samples/{s['id']}").status_code == 204
    assert not (data_dir / "audio" / p["id"] / f"{s['id']}.wav").exists()
    assert not (data_dir / "features" / "mfcc_13_deltas" / p["id"] / f"{s['id']}.npy").exists()
    assert client.delete(f"/api/phrases/{p['id']}/samples/{s['id']}").status_code == 404


def test_delete_phrase_removes_all_files(client, data_dir):
    r = client.post(
        "/api/phrases", data={"text": "tea"}, files={"image": ("t.jpg", b"jpg", "image/jpeg")}
    )
    p = r.json()
    _upload(client, p["id"], wav_bytes(tone(1.0)), "audio/wav", "a.wav")
    assert client.delete(f"/api/phrases/{p['id']}").status_code == 204
    assert not (data_dir / "audio" / p["id"]).exists()
    assert not (data_dir / "features" / "mfcc_13_deltas" / p["id"]).exists()
    assert not (data_dir / p["image"]).exists()
    assert client.get("/api/phrases").json() == []
    assert client.delete(f"/api/phrases/{p['id']}").status_code == 404


def test_concurrent_writes_lose_nothing(client, data_dir):
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: _create(client, text=f"phrase {i}"), range(40)))
    assert len(client.get("/api/phrases").json()) == 40
    # No leftover temp files from atomic writes.
    assert not [f for f in data_dir.iterdir() if f.name.endswith(".tmp")]
