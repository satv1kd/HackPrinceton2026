import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Recorder } from '../static/js/recorder.js';

const tick = () => new Promise(resolve => setImmediate(resolve));

function setup({ permission, supported = ['audio/webm;codecs=opus'], minDuration = 300, maxDuration = 10000 } = {}) {
  const state = { stops: 0, trackStops: 0, time: 0, instance: null };
  const stream = { getTracks: () => [{ stop: () => state.trackStops++ }] };
  class FakeMediaRecorder {
    static isTypeSupported(type) { return supported.includes(type); }
    constructor(_stream, options = {}) {
      this.mimeType = options.mimeType || 'audio/mp4';
      this.options = options;
      this.state = 'inactive';
      state.instance = this;
    }
    start() { this.state = 'recording'; }
    stop() {
      state.stops++;
      this.state = 'inactive';
      queueMicrotask(() => {
        this.ondataavailable({ data: new Blob(['recording'], { type: this.mimeType }) });
        this.onstop();
      });
    }
  }
  const recorder = new Recorder({ mediaDevices: { getUserMedia: () => permission ? permission(stream) : Promise.resolve(stream) },
    RecorderClass: FakeMediaRecorder, now: () => state.time, minDuration, maxDuration });
  return { recorder, state };
}

test('release produces final chunk once and releases the microphone', async () => {
  const { recorder, state } = setup();
  const capture = recorder.start();
  await tick();
  state.time = 1000;
  recorder.stop(); recorder.stop();
  const result = await capture;
  assert.equal(result.duration, 1000);
  assert.equal(await result.blob.text(), 'recording');
  assert.equal(result.mimeType, 'audio/webm;codecs=opus');
  assert.equal(state.stops, 1);
  assert.equal(state.trackStops, 1);
  assert.equal(recorder.session, null);
});

test('release before permission resolves never starts capture', async () => {
  let allow;
  const { recorder, state } = setup({ permission: stream => new Promise(resolve => { allow = () => resolve(stream); }) });
  const capture = recorder.start();
  recorder.stop(); allow();
  assert.equal(await capture, null);
  assert.equal(state.instance, null);
  assert.equal(state.trackStops, 1);
});

test('short capture is rejected and still releases tracks', async () => {
  const { recorder, state } = setup();
  const capture = recorder.start();
  await tick(); state.time = 100; recorder.stop();
  await assert.rejects(capture, /longer/);
  assert.equal(state.trackStops, 1);
});

test('maximum duration completes capture without a release event', async () => {
  const { recorder, state } = setup({ maxDuration: 15, minDuration: 1 });
  const capture = recorder.start();
  await tick(); state.time = 15;
  assert.equal((await capture).duration, 15);
  recorder.stop();
  assert.equal(state.stops, 1);
});

test('cancellation discards audio', async () => {
  const { recorder, state } = setup();
  const capture = recorder.start();
  await tick(); state.time = 1000; recorder.cancel();
  assert.equal(await capture, null);
  assert.equal(state.trackStops, 1);
});

test('Safari MIME and default recorder MIME are preserved', async () => {
  for (const supported of [['audio/mp4'], []]) {
    const { recorder, state } = setup({ supported });
    const capture = recorder.start();
    await tick(); state.time = 1000; recorder.stop();
    assert.equal((await capture).mimeType, 'audio/mp4');
    if (!supported.length) assert.deepEqual(state.instance.options, {});
  }
});

test('permission denial resets recorder so retry can proceed', async () => {
  const { recorder } = setup({ permission: () => Promise.reject(new Error('denied')) });
  await assert.rejects(recorder.start(), /denied/);
  assert.equal(recorder.session, null);
});

test('overlapping starts rejected without interrupting original', async () => {
  const { recorder } = setup();
  const capture = recorder.start();
  await assert.rejects(recorder.start(), /already/);
  recorder.cancel();
  assert.equal(await capture, null);
});

test('recorder errors reject capture and release tracks', async () => {
  const { recorder, state } = setup();
  const capture = recorder.start();
  await tick();
  state.instance.onerror({ error: new Error('hardware failure') });
  await assert.rejects(capture, /hardware failure/);
  assert.equal(state.trackStops, 1);
  assert.equal(recorder.session, null);
});
