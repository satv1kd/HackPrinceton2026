/**
 * Spoken output (Task 10).
 *
 * Tries the server's ElevenLabs voice, falls back to the browser's own.
 * A 503 from /api/tts is the documented signal to fall back, not an error.
 *
 * iOS note: Safari will not play audio that was not started by a user
 * gesture. Speaking happens on the release of hold-to-talk or on a Yes /
 * picker tap, all of which are gestures, so this works — but `unlock()` is
 * called on the first gesture anyway, because Safari also needs the audio
 * element to have been touched once before programmatic play is allowed.
 */

export class TTSPlayer {
  constructor() {
    this.audio = null;
    this.unlocked = false;
    this.lastObjectUrl = null;
  }

  /**
   * The audio element, created on first use.
   *
   * Deliberately not built in the constructor: `TalkMode` constructs a player
   * eagerly, and an environment without `Audio` — a test harness, or a
   * browser that blocks media — would then fail at construction and take the
   * whole result card with it. Speech is the one thing here that is allowed
   * to be missing, so its absence degrades to the browser voice or silence.
   */
  element() {
    if (this.audio) return this.audio;
    if (typeof Audio !== 'function') return null;
    this.audio = new Audio();
    this.audio.preload = 'auto';
    return this.audio;
  }

  /** Let Safari know the audio element is user-initiated. Harmless elsewhere. */
  unlock() {
    if (this.unlocked) return;
    const audio = this.element();
    if (!audio) return;
    this.unlocked = true;
    // A zero-length play attempt is enough to mark the element as permitted.
    audio.play().catch(() => {});
    audio.pause();
  }

  /**
   * Speak `text`. Resolves to 'elevenlabs', 'browser', or 'silent'.
   * Never rejects: failing to speak must not break the result card.
   */
  async speak(text) {
    const message = (text || '').trim();
    if (!message) return 'silent';

    this.stop();

    try {
      const audio = this.element();
      const response = audio
        ? await fetch(`/api/tts?text=${encodeURIComponent(message)}`)
        : null;
      if (response?.ok) {
        const blob = await response.blob();
        if (blob.size > 0) {
          this.lastObjectUrl = URL.createObjectURL(blob);
          audio.src = this.lastObjectUrl;
          await audio.play();
          return 'elevenlabs';
        }
      }
      // 503 is expected: no key, no quota, or no network at the server.
    } catch {
      // Network failure or autoplay refusal; the browser voice still works.
    }

    return this.speakWithBrowser(message) ? 'browser' : 'silent';
  }

  speakWithBrowser(text) {
    const synthesis = window.speechSynthesis;
    if (!synthesis || typeof SpeechSynthesisUtterance === 'undefined') return false;
    synthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    // Slightly slower than default: these phrases are being said to someone
    // who needs to catch them first time.
    utterance.rate = 0.9;
    utterance.pitch = 1.0;
    synthesis.speak(utterance);
    return true;
  }

  /** Stop whatever is speaking and release the last blob. */
  stop() {
    try {
      if (this.audio) {
        this.audio.pause();
        this.audio.currentTime = 0;
      }
    } catch {
      /* no source yet */
    }
    if (this.lastObjectUrl) {
      URL.revokeObjectURL(this.lastObjectUrl);
      this.lastObjectUrl = null;
    }
    window.speechSynthesis?.cancel();
  }
}
