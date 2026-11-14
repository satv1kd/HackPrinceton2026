"""Transactional feedback journal with compensating template updates.

Uses the foundation's PhraseStore plus its sample-restoration extension.
"""
import base64
import json
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from app.services.attempts import utcnow
from app.services.task_io import atomic_bytes, atomic_json, attempt_key, data_lock
from app.services.storage import NotFoundError


class FeedbackConflict(ValueError):
    pass


class IntegrationRequired(RuntimeError):
    pass


def read_events(path):
    if not Path(path).exists():
        return []
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def active_feedback(events):
    active = {}
    for event in events:
        if event['action'] == 'record':
            active[event['attempt_id']] = event
        elif active.get(event['attempt_id'], {}).get('event_id') == event['target_event_id']:
            active.pop(event['attempt_id'])
    return active


class FeedbackStore:
    def __init__(self, data_dir, phrase_store, attempt_store, feature_cache=None,
                 max_samples=10, undo_seconds=10):
        if max_samples < 1 or undo_seconds <= 0:
            raise ValueError('Invalid feedback limits')
        self.data_dir, self.phrase_store = Path(data_dir), phrase_store
        self.attempt_store, self.feature_cache = attempt_store, feature_cache
        self.max_samples, self.undo_seconds = max_samples, undo_seconds
        self.feedback_file = self.data_dir / 'feedback.jsonl'
        self.transaction_file = self.data_dir / 'feedback-transaction.json'
        self.backup_dir = self.data_dir / 'feedback-backups'
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def _append(self, event):
        events = read_events(self.feedback_file)
        if any(e['event_id'] == event['event_id'] for e in events):
            return
        # The MVP log is small. Atomic replacement prevents partial JSONL tails.
        events.append(event)
        atomic_bytes(self.feedback_file, ''.join(json.dumps(e, allow_nan=False) + '\n' for e in events).encode())

    def _backup_path(self, event_id):
        return self.backup_dir / (attempt_key(event_id) + '.json')

    def _delete_sample(self, phrase_id, sample_id):
        try:
            self.phrase_store.delete_sample(phrase_id, sample_id)
        except NotFoundError:
            pass  # Replaying a partially completed undo must be safe.
        if self.feature_cache:
            self.feature_cache.invalidate(phrase_id, sample_id)

    def _restore(self, snapshot):
        if not snapshot:
            return
        try:
            phrase = self.phrase_store.get_phrase(snapshot['phrase_id'])
        except NotFoundError:
            return
        # Explicit phrase deletion takes precedence over undo.
        if phrase is None:
            return
        current = {s.id for s in phrase.samples}
        for sample_id in current - set(snapshot['before_ids']):
            # Only remove this transaction's new sample during immediate recovery.
            # For later undo the committed added_id is supplied instead.
            if snapshot.get('added_id') is None or sample_id == snapshot['added_id']:
                self._delete_sample(phrase.id, sample_id)
        for old in snapshot['evicted']:
            if old['metadata']['id'] not in current:
                self.phrase_store.restore_sample(phrase.id, old['metadata'], base64.b64decode(old['audio']))

    def _recover(self):
        if not self.transaction_file.exists():
            return
        tx = json.loads(self.transaction_file.read_text())
        committed = any(e['event_id'] == tx['event']['event_id'] for e in read_events(self.feedback_file))
        if not committed:
            self._restore(tx['snapshot'])
            if tx['event']['action'] == 'undo':
                self._append(tx['event'])
        self.transaction_file.unlink()

    def _snapshot(self, phrase):
        enrolled = [s for s in phrase.samples if s.source == 'enroll']
        feedback = sorted((s for s in phrase.samples if s.source == 'feedback'),
                          key=lambda s: (str(s.created_at), s.id))
        if len(enrolled) >= self.max_samples:
            return None  # Enrollment is never evicted; still record the label.
        count = max(0, len(phrase.samples) + 1 - self.max_samples)
        if count and not callable(getattr(self.phrase_store, 'restore_sample', None)):
            raise IntegrationRequired('PhraseStore.restore_sample is required for reversible sample eviction')
        protected = set()
        for event in active_feedback(read_events(self.feedback_file)).values():
            if event['sample_added'] and utcnow() <= datetime.fromisoformat(event['undo_until']):
                backup = json.loads(self._backup_path(event['event_id']).read_text())
                if backup['phrase_id'] == phrase.id:
                    protected.add(backup['added_id'])
        if any(sample.id in protected for sample in feedback[:count]):
            raise FeedbackConflict('The previous response is still undoable. Please retry in a few seconds.')
        evicted = []
        for sample in feedback[:count]:
            metadata = sample.model_dump(mode='json') if hasattr(sample, 'model_dump') else dict(vars(sample))
            metadata['created_at'] = str(metadata['created_at'])
            evicted.append({'metadata': metadata, 'audio': base64.b64encode(
                Path(self.phrase_store.sample_path(phrase.id, sample.id)).read_bytes()).decode()})
        return {'phrase_id': phrase.id, 'before_ids': [s.id for s in phrase.samples], 'evicted': evicted}

    @staticmethod
    def _response(event):
        return {'retrain_triggered': False, 'event_id': event['event_id'],
                'undo_until': event['undo_until'], 'sample_added': event['sample_added']}

    def record(self, attempt_id, correct, label=None):
        key = attempt_key(attempt_id)
        with data_lock(self.data_dir):
            self._recover()
            attempt = self.attempt_store.get(key)
            if attempt is None:
                raise FileNotFoundError('Recognition attempt not found or expired')
            if correct and label is None:
                raise ValueError('A positive response requires an explicit label')
            phrase = None
            if label and label['type'] == 'phrase':
                try:
                    phrase = self.phrase_store.get_phrase(label['phrase_id'])
                except NotFoundError as exc:
                    raise FileNotFoundError('Phrase no longer exists') from exc
                if phrase is None:
                    raise FileNotFoundError('Phrase no longer exists')
            previous = active_feedback(read_events(self.feedback_file)).get(key)
            if previous:
                if previous['correct'] == correct and previous['label'] == label:
                    return self._response(previous)  # Network retry, no duplicate sample.
                raise FeedbackConflict('Undo existing feedback before changing it')
            now = utcnow()
            event = {'action': 'record', 'event_id': str(uuid4()), 'attempt_id': key,
                'timestamp': now.isoformat(), 'undo_until': (now + timedelta(seconds=self.undo_seconds)).isoformat(),
                'correct': correct, 'label': label,
                'shown': attempt['candidates'][0] if attempt['candidates'] else None,
                'recognized_phonemes': attempt.get('recognized_phonemes'),
                'used_for_profile_version': None, 'sample_added': False}
            snapshot = self._snapshot(phrase) if phrase else None
            atomic_json(self.transaction_file, {'event': event, 'snapshot': snapshot})
            try:
                if snapshot is not None:
                    sample = self.phrase_store.add_sample(phrase.id,
                        (self.attempt_store.path(key) / 'audio.wav').read_bytes(), source='feedback')
                    snapshot['added_id'] = sample.id
                    event['sample_added'] = True
                    # Recovery can identify an interrupted add via before_ids.
                    atomic_json(self.transaction_file, {'event': event, 'snapshot': snapshot})
                    for old in snapshot['evicted']:
                        self._delete_sample(phrase.id, old['metadata']['id'])
                    atomic_json(self._backup_path(event['event_id']), snapshot)
                self._append(event)
            except Exception:
                self._recover()
                raise
            self.transaction_file.unlink()
            return self._response(event)

    def undo(self, attempt_id, event_id=None):
        key = attempt_key(attempt_id)
        with data_lock(self.data_dir):
            self._recover()
            events = read_events(self.feedback_file)
            event = active_feedback(events).get(key)
            if event_id and any(e['action'] == 'undo' and e['target_event_id'] == event_id and e['attempt_id'] == key for e in events):
                return True
            if not event or (event_id and event['event_id'] != event_id):
                raise FeedbackConflict('No matching feedback to undo')
            if utcnow() > datetime.fromisoformat(event['undo_until']):
                raise ValueError('The undo window has expired')
            backup = self._backup_path(event['event_id'])
            snapshot = json.loads(backup.read_text()) if event['sample_added'] else None
            undo_event = {'action': 'undo', 'event_id': str(uuid4()), 'attempt_id': key,
                          'timestamp': utcnow().isoformat(), 'target_event_id': event['event_id']}
            atomic_json(self.transaction_file, {'event': undo_event, 'snapshot': snapshot})
            self._recover()  # Undo is replayable after process interruption.
            return True

    def maintenance(self):
        with data_lock(self.data_dir):
            self._recover()
            active = active_feedback(read_events(self.feedback_file))
            keep = {e['event_id'] for e in active.values() if utcnow() <= datetime.fromisoformat(e['undo_until'])}
            for backup in self.backup_dir.glob('*.json'):
                if backup.stem not in keep:
                    backup.unlink()
        return self.attempt_store.cleanup_unlabeled()
