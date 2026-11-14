"""Real tasks 1–8 API path, including ffmpeg and MFCC feature extraction."""
from datetime import timedelta

import pytest

from app.deps import get_store, get_feature_cache
from app.services.attempts import utcnow
from tests.conftest import encode, tone, wav_bytes


@pytest.mark.parametrize('fmt,mime', [('webm', 'audio/webm;codecs=opus'), ('mp4', 'audio/mp4')])
def test_enroll_recognize_feedback_undo_with_browser_formats(client, data_dir, fmt, mime):
    phrases = []
    recordings = []
    for text, frequency in [('Water please', 180), ('I need help', 370), ('Hello there', 620)]:
        created = client.post('/api/phrases', data={'text': text, 'emoji': '💬'})
        assert created.status_code == 201
        phrase_id = created.json()['id']
        phrases.append(phrase_id)
        content = encode(tone(.8, frequency, sr=48000), fmt)
        recordings.append(content)
        for _ in range(3):
            response = client.post(f'/api/phrases/{phrase_id}/samples', files={'audio': (f'clip.{fmt}', content, mime)})
            assert response.status_code == 201, response.text
    recognized = client.post('/api/recognize', files={'audio': (f'clip.{fmt}', recordings[0], mime)})
    assert recognized.status_code == 200, recognized.text
    result = recognized.json()
    assert len(result['candidates']) == 3
    assert result['candidates'][0]['phrase_id'] == phrases[0]
    key = result['attempt_id']
    assert (data_dir / 'attempts' / key / 'features.npy').exists()
    body = {'attempt_id': key, 'correct': True, 'label': {'type': 'phrase', 'phrase_id': phrases[0]}}
    response = client.post('/api/feedback', json=body)
    assert response.status_code == 200, response.text
    assert client.get(f'/api/phrases/{phrases[0]}').json()['sample_count'] == 4
    assert client.post('/api/feedback', json=body).json() == response.json()
    assert client.post('/api/feedback/undo', json={'attempt_id': key, 'event_id': response.json()['event_id']}).status_code == 200
    assert client.get(f'/api/phrases/{phrases[0]}').json()['sample_count'] == 3
    # A correction labels the actual phrase even when the displayed guess was wrong.
    corrected = client.post('/api/feedback', json={**body, 'correct': False, 'label': {'type': 'phrase', 'phrase_id': phrases[1]}})
    assert corrected.status_code == 200
    assert client.get(f'/api/phrases/{phrases[1]}').json()['sample_count'] == 4


def test_real_store_eviction_and_full_undo(client, data_dir):
    from app.main import app
    store = get_store()
    phrase = store.create_phrase('Water')
    audio = wav_bytes(tone(.8))
    original = [store.add_sample(phrase.id, audio, source=source, duration_sec=.8)
                for source in ['enroll', 'feedback', 'feedback']]
    app.state.feedback_store.max_samples = 3
    result = client.post('/api/recognize', files={'audio': ('clip.wav', audio, 'audio/wav')}).json()
    response = client.post('/api/feedback', json={'attempt_id': result['attempt_id'], 'correct': True,
        'label': {'type': 'phrase', 'phrase_id': phrase.id}})
    assert response.status_code == 200, response.text
    assert len(store.get_phrase(phrase.id).samples) == 3
    assert not store.sample_path(phrase.id, original[1].id).exists()
    assert not get_feature_cache().path_for(phrase.id, original[1].id).exists()
    assert client.post('/api/feedback/undo', json={'attempt_id': result['attempt_id']}).status_code == 200
    assert store.get_phrase(phrase.id).samples == original
    assert store.sample_path(phrase.id, original[1].id).read_bytes() == audio


def test_empty_library_errors_and_expired_undo(client, monkeypatch):
    audio = wav_bytes(tone(.8))
    result = client.post('/api/recognize', files={'audio': ('clip.wav', audio, 'audio/wav')}).json()
    assert result['candidates'] == [] and result['confident'] is False
    assert client.post('/api/feedback', json={'attempt_id': result['attempt_id'], 'correct': False}).status_code == 200
    future = utcnow() + timedelta(seconds=30)
    monkeypatch.setattr('app.services.feedback.utcnow', lambda: future)
    assert client.post('/api/feedback/undo', json={'attempt_id': result['attempt_id']}).status_code == 400
    assert client.post('/api/recognize', files={'audio': ('clip.wav', b'broken audio', 'audio/wav')}).status_code == 422
    assert client.get('/').status_code == 200
    assert 'Hold to talk' in client.get('/').text
