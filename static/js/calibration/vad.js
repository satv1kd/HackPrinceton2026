/* Microphone level + voice-activity detection via the Web Audio API.

   One MicMonitor serves both the mic-check meter and the reading screen's
   voice-activity fallback. RMS energy from an AnalyserNode is compared
   against an adaptive noise floor (an EMA learned while the room is quiet),
   so a humming laptop fan does not count as speech but soft voices do. */

const TICK_MS = 80;

export class MicMonitor {
  constructor({ onTick } = {}) {
    this.onTick = onTick || (() => {});
    this.context = null;
    this.analyser = null;
    this.stream = null;
    this.timer = null;
    this.noiseFloor = 0.01;
    this.level = 0;
    this.active = false;
  }

  /** Requests the microphone. Throws the getUserMedia error on denial. */
  async start() {
    if (this.timer) return;
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const Context = globalThis.AudioContext || globalThis.webkitAudioContext;
    this.context = new Context();
    await this.context.resume().catch(() => {});
    const source = this.context.createMediaStreamSource(this.stream);
    this.analyser = this.context.createAnalyser();
    this.analyser.fftSize = 1024;
    source.connect(this.analyser);
    this.buffer = new Float32Array(this.analyser.fftSize);
    let last = performance.now();
    this.timer = setInterval(() => {
      const now = performance.now();
      const dt = now - last;
      last = now;
      this._measure(dt);
    }, TICK_MS);
  }

  _measure(dtMs) {
    this.analyser.getFloatTimeDomainData(this.buffer);
    let sum = 0;
    for (let i = 0; i < this.buffer.length; i++) sum += this.buffer[i] * this.buffer[i];
    const rms = Math.sqrt(sum / this.buffer.length);
    const threshold = this.noiseFloor * 2.5 + 0.006;
    this.active = rms > threshold;
    if (!this.active) {
      // Learn the room while it is quiet; never learn speech into the floor.
      this.noiseFloor = this.noiseFloor * 0.95 + rms * 0.05;
    }
    // A display level that reaches ~1.0 for ordinary speech.
    this.level = Math.min(1, rms * 12);
    this.onTick(this.level, this.active, dtMs);
  }

  stop() {
    clearInterval(this.timer);
    this.timer = null;
    this.stream?.getTracks().forEach(track => track.stop());
    this.stream = null;
    this.context?.close().catch(() => {});
    this.context = null;
    this.level = 0;
    this.active = false;
  }
}
