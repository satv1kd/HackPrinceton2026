"""Contract tests with test doubles for the teammate-owned tasks 1–4."""
import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from uuid import uuid4

import numpy as np
import pytest
import soundfile as sf
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.recognition import MatchCandidate, MatchResult
from app.services.attempts import AttemptStore, utcnow
from app.services.feedback import FeedbackStore, FeedbackConflict, IntegrationRequired, read_events
from app.services.matcher import DTWMatcher, cosine_distance
from app.services.task_io import atomic_json
from app.task_integration import install_tasks_5_8


class PhraseStoreDouble:
    def __init__(self, root):
        self.root, self.phrases = root, {}

    def phrase(self, key):
        phrase = NS(id=key, text=key, emoji=None, image=None, samples=[])
        self.phrases[key] = phrase
        return phrase

    def list_phrases(self):
        return list(self.phrases.values())

    def get_phrase(self, key):
        return self.phrases.get(key)

    def sample_path(self, phrase_id, sample_id):
        return self.root / phrase_id / (sample_id + '.wav')

    def add_sample(self, phrase_id, audio_bytes, source='enroll'):
        sample = NS(id=str(uuid4()), source=source, created_at=utcnow().isoformat())
        self.restore_sample(phrase_id, vars(sample), audio_bytes)
        return sample

    def restore_sample(self, phrase_id, metadata, audio_bytes):
        phrase = self.phrases[phrase_id]
        if not any(s.id == metadata['id'] for s in phrase.samples):
            phrase.samples.append(NS(**metadata))
        path = self.sample_path(phrase_id, metadata['id'])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(audio_bytes)

    def delete_sample(self, phrase_id, sample_id):
        phrase = self.phrases[phrase_id]
        phrase.samples = [s for s in phrase.samples if s.id != sample_id]
        self.sample_path(phrase_id, sample_id).unlink(missing_ok=True)
        return True


class CacheDouble:
    def __init__(self):
        self.features, self.invalidated = {}, []

    def get_or_extract(self, phrase_id, sample_id, path):
        return self.features[(phrase_id, sample_id)]

    def invalidate(self, phrase_id, sample_id):
        self.invalidated.append((phrase_id, sample_id))


@pytest.fixture
def stack(tmp_path):
    phrases = PhraseStoreDouble(tmp_path / 'audio')
    phrases.phrase('water')
    cache = CacheDouble()
    attempts = AttemptStore(tmp_path)
    feedback = FeedbackStore(tmp_path, phrases, attempts, cache, max_samples=3)
    return NS(root=tmp_path, phrases=phrases, cache=cache, attempts=attempts, feedback=feedback)


def attempt(stack):
    key = str(uuid4())
    result = MatchResult(attempt_id=key, confident=True,
        candidates=[MatchCandidate(phrase_id='water', text='Water', score=0.1)])
    stack.attempts.save(key, b'cleaned audio', np.ones((4, 2)), result)
    return key


def matcher(stack, **options):
    config = NS(**dict({'MARGIN': .15, 'ABSOLUTE_MAX': .5}, **options))
    return DTWMatcher(config, stack.phrases, stack.cache)


def template(stack, phrase_id, frames):
    if not stack.phrases.get_phrase(phrase_id):
        stack.phrases.phrase(phrase_id)
    sample = stack.phrases.add_sample(phrase_id, b'audio')
    stack.cache.features[(phrase_id, sample.id)] = np.array(frames, dtype=float)


def test_matcher_ranks_distinct_phrases_using_minimum_and_top_three(stack):
    for key, frame in [('water', [1, 0]), ('coffee', [0, 1]), ('help', [-1, 0]), ('hello', [-1, 1])]:
        template(stack, key, [frame] * 4)
    template(stack, 'water', [[-1, 0]] * 4)
    result = matcher(stack).match(np.array([[1, 0]] * 7))
    assert result.confident
    assert len(result.candidates) == 3
    assert result.candidates[0].phrase_id == 'water'
    assert result.candidates[0].score == 0
    assert len({c.phrase_id for c in result.candidates}) == 3


def test_exact_ties_are_not_confident(stack):
    template(stack, 'water', [[1, 0]] * 4)
    template(stack, 'coffee', [[1, 0]] * 4)
    assert not matcher(stack).match([[1, 0]] * 4).confident


def test_empty_invalid_template_and_absolute_threshold(stack):
    assert matcher(stack).match([[1, 0]]).candidates == []
    template(stack, 'water', [[0, 1]])
    assert not matcher(stack).match([[1, 0]]).confident
    template(stack, 'coffee', [[float('nan'), 0]])
    assert len(matcher(stack).match([[1, 0]]).candidates) == 1


@pytest.mark.parametrize('features', [[], [[float('inf')]], [1, 2]])
def test_invalid_queries_rejected(stack, features):
    with pytest.raises(ValueError): matcher(stack).match(features)


def test_distance_and_path_normalization(stack):
    assert cosine_distance(np.zeros(2), np.zeros(2)) == 0
    assert cosine_distance(np.zeros(2), np.ones(2)) == 1
    template(stack, 'water', [[1, 1]] * 3)
    m = matcher(stack, DTW_METRIC='euclidean', ABSOLUTE_MAX=3)
    assert m.match([[2, 1]] * 3).candidates[0].score == pytest.approx(1)
    assert m.match([[2, 1]] * 9).candidates[0].score == pytest.approx(1)


def test_attempt_files_and_duplicate_protection(stack):
    key = attempt(stack)
    assert stack.attempts.get(key)['confident']
    assert np.load(stack.attempts.path(key) / 'features.npy', allow_pickle=False).shape == (4, 2)
    with pytest.raises(ValueError): stack.attempts.get('../../escape')
    result = MatchResult(attempt_id=key, confident=False, candidates=[])
    with pytest.raises(FileExistsError): stack.attempts.save(key, b'a', [[1]], result)


@pytest.mark.parametrize('correct', [True, False])
def test_labels_teach_even_when_correcting_and_retries_are_idempotent(stack, correct):
    key = attempt(stack)
    label = {'type': 'phrase', 'phrase_id': 'water'}
    first = stack.feedback.record(key, correct, label)
    assert first['sample_added']
    assert stack.feedback.record(key, correct, label) == first
    assert len(stack.phrases.get_phrase('water').samples) == 1
    assert len(read_events(stack.feedback.feedback_file)) == 1
    with pytest.raises(FeedbackConflict): stack.feedback.record(key, not correct, label)
    assert stack.feedback.undo(key, first['event_id'])
    assert stack.feedback.undo(key, first['event_id'])
    assert not stack.phrases.get_phrase('water').samples


def test_retention_and_undo_restore_original_sample(stack):
    p = stack.phrases
    enrollment = p.add_sample('water', b'enrollment')
    old = p.add_sample('water', b'old', source='feedback')
    recent = p.add_sample('water', b'recent', source='feedback')
    key = attempt(stack)
    response = stack.feedback.record(key, True, {'type': 'phrase', 'phrase_id': 'water'})
    ids = {s.id for s in p.get_phrase('water').samples}
    assert len(ids) == 3 and old.id not in ids and enrollment.id in ids
    stack.feedback.undo(key, response['event_id'])
    samples = {s.id: s for s in p.get_phrase('water').samples}
    assert set(samples) == {enrollment.id, old.id, recent.id}
    assert samples[old.id].created_at == old.created_at
    assert p.sample_path('water', old.id).read_bytes() == b'old'


def test_enrollment_never_evicted(stack):
    for _ in range(3): stack.phrases.add_sample('water', b'enrollment')
    result = stack.feedback.record(attempt(stack), True, {'type': 'phrase', 'phrase_id': 'water'})
    assert not result['sample_added']
    assert len(stack.phrases.get_phrase('water').samples) == 3


def test_missing_restore_contract_fails_before_mutating(stack):
    for _ in range(3): stack.phrases.add_sample('water', b'a', source='feedback')
    stack.phrases.restore_sample = None
    with pytest.raises(IntegrationRequired):
        stack.feedback.record(attempt(stack), True, {'type': 'phrase', 'phrase_id': 'water'})
    assert len(stack.phrases.get_phrase('water').samples) == 3


def test_undo_protected_template_cannot_be_evicted(stack):
    stack.feedback.max_samples = 1
    stack.feedback.record(attempt(stack), True, {'type': 'phrase', 'phrase_id': 'water'})
    with pytest.raises(FeedbackConflict):
        stack.feedback.record(attempt(stack), True, {'type': 'phrase', 'phrase_id': 'water'})


def test_rollback_after_partial_add(stack, monkeypatch):
    original = stack.phrases.add_sample
    def broken(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError('simulated interruption after saving audio')
    monkeypatch.setattr(stack.phrases, 'add_sample', broken)
    with pytest.raises(OSError): stack.feedback.record(attempt(stack), True, {'type': 'phrase', 'phrase_id': 'water'})
    assert stack.phrases.get_phrase('water').samples == []
    assert read_events(stack.feedback.feedback_file) == []


def test_restart_recovers_pending_add(stack):
    key = attempt(stack)
    atomic_json(stack.feedback.transaction_file, {'event': {'event_id': str(uuid4()), 'action': 'record'},
        'snapshot': {'phrase_id': 'water', 'before_ids': [], 'evicted': []}})
    stack.phrases.add_sample('water', b'interrupted', source='feedback')
    restarted = FeedbackStore(stack.root, stack.phrases, stack.attempts)
    restarted.maintenance()
    assert stack.phrases.get_phrase('water').samples == []
    assert stack.attempts.get(key)


def test_expired_undo_and_cleanup(stack, monkeypatch):
    unlabeled = attempt(stack)
    labeled = attempt(stack)
    stack.feedback.record(labeled, False, {'type': 'text', 'text': 'Please help'})
    assert stack.attempts.cleanup_unlabeled(timedelta(0)) == 1
    assert stack.attempts.get(unlabeled) is None
    assert stack.attempts.get(labeled)
    future = utcnow() + timedelta(seconds=20)
    monkeypatch.setattr('app.services.feedback.utcnow', lambda: future)
    with pytest.raises(ValueError, match='expired'): stack.feedback.undo(labeled)


def test_concurrent_duplicate_feedback_has_one_effect(stack):
    key = attempt(stack)
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: stack.feedback.record(key, True,
            {'type': 'phrase', 'phrase_id': 'water'}), range(8)))
    assert len({r['event_id'] for r in responses}) == 1
    assert len(stack.phrases.get_phrase('water').samples) == 1


def wav():
    buffer = io.BytesIO()
    sf.write(buffer, np.sin(np.arange(8000) * .05), 16000, format='WAV')
    return buffer.getvalue()


def test_api_pipeline_and_validation(stack, monkeypatch):
    template(stack, 'water', [[1, 0]] * 4)
    app = FastAPI()
    config = NS(MARGIN=.15, ABSOLUTE_MAX=.5, MAX_SAMPLES_PER_PHRASE=10)
    def process(content, mime):
        from app.services.audio import AudioProcessingError
        try:
            audio, sr = sf.read(io.BytesIO(content))
        except sf.LibsndfileError as exc:
            raise AudioProcessingError('bad audio') from exc
        return NS(samples=audio, sr=sr, wav_bytes=content)
    preprocessor = NS(process=process)
    extractor = NS(extract=lambda audio, sr: np.array([[1, 0]] * 4))
    install_tasks_5_8(app, config=config, phrase_store=stack.phrases, preprocessor=preprocessor,
        extractor=extractor, feature_cache=stack.cache, data_dir=stack.root)
    with TestClient(app) as client:
        assert client.post('/api/recognize').status_code == 422
        assert client.post('/api/recognize', files={'audio': ('x.txt', b'a', 'text/plain')}).status_code == 415
        assert client.post('/api/recognize', files={'audio': ('x.wav', b'', 'audio/wav')}).status_code == 422
        assert client.post('/api/recognize', files={'audio': ('x.wav', b'bad', 'audio/wav')}).status_code == 422
        response = client.post('/api/recognize', files={'audio': ('x.wav', wav(), 'audio/wav')})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['candidates'][0]['type'] == 'phrase'
        assert (stack.attempts.path(data['attempt_id']) / 'audio.wav').exists()
        body = {'attempt_id': data['attempt_id'], 'correct': True, 'label': {'type': 'phrase', 'phrase_id': 'water'}}
        saved = client.post('/api/feedback', json=body)
        assert saved.status_code == 200
        assert client.post('/api/feedback', json={**body, 'correct': 'false'}).status_code == 422
        assert client.post('/api/feedback', json={**body, 'label': None}).status_code == 400
        assert client.post('/api/feedback', json={**body, 'attempt_id': str(uuid4())}).status_code == 404
        assert client.post('/api/feedback/undo', json={'attempt_id': data['attempt_id'], 'event_id': saved.json()['event_id']}).status_code == 200
        monkeypatch.setattr('app.routers.recognize.MAX_UPLOAD_BYTES', 5)
        assert client.post('/api/recognize', files={'audio': ('x.wav', b'123456', 'audio/wav')}).status_code == 413
