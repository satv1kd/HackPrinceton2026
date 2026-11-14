// A start() call owns the entire capture; every stop source shares one completion.
export class Recorder {
  constructor({ minDuration = 300, maxDuration = 10000, mediaDevices = globalThis.navigator?.mediaDevices,
    RecorderClass = globalThis.MediaRecorder, now = () => performance.now() } = {}) {
    this.minDuration = minDuration;
    this.maxDuration = maxDuration;
    this.mediaDevices = mediaDevices;
    this.RecorderClass = RecorderClass;
    this.now = now;
    this.session = null;
  }

  getMimeType() {
    return ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'].find(
      type => this.RecorderClass.isTypeSupported(type)) || '';
  }

  async start(onStarted = () => {}) {
    if (this.session) throw new Error('A recording is already in progress.');
    if (!this.mediaDevices?.getUserMedia || !this.RecorderClass) {
      throw new Error('Recording needs a supported browser on HTTPS or localhost.');
    }
    const session = { cancelled: false, stopping: false, stream: null, recorder: null };
    this.session = session;
    try {
      session.stream = await this.mediaDevices.getUserMedia({ audio: true });
      if (session.cancelled) return null; // Released while the permission dialog was open.
      const mimeType = this.getMimeType();
      const recorder = new this.RecorderClass(session.stream, mimeType ? { mimeType } : {});
      session.recorder = recorder;
      const chunks = [];
      const completed = new Promise((resolve, reject) => {
        recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
        recorder.onerror = event => reject(event.error || new Error('The microphone stopped working.'));
        recorder.onstop = () => {
          if (session.cancelled) return resolve(null);
          const duration = (session.stoppedAt ?? this.now()) - session.startedAt;
          if (duration < this.minDuration) return reject(new Error('Didn’t catch that — hold a little longer.'));
          const actualType = recorder.mimeType || chunks[0]?.type || mimeType;
          const blob = new Blob(chunks, { type: actualType });
          if (!blob.size) return reject(new Error('No audio was recorded. Please try again.'));
          resolve({ blob, duration, mimeType: actualType });
        };
      });
      session.startedAt = this.now();
      recorder.start();
      session.timer = setTimeout(() => this.stop(), this.maxDuration);
      onStarted();
      return await completed;
    } finally {
      clearTimeout(session.timer);
      if (session.recorder?.state === 'recording') session.recorder.stop();
      session.stream?.getTracks().forEach(track => track.stop());
      if (this.session === session) this.session = null;
    }
  }

  stop() {
    const session = this.session;
    if (!session || session.stopping) return;
    if (!session.recorder) { session.cancelled = true; return; }
    session.stopping = true;
    session.stoppedAt = this.now();
    clearTimeout(session.timer);
    if (session.recorder.state !== 'inactive') session.recorder.stop();
  }

  cancel() {
    if (this.session) this.session.cancelled = true;
    this.stop();
  }
}
