/* Best-effort wrapper around the browser's SpeechRecognition.

   This is the least trusted layer of progress tracking: for many Heard users
   it mishears constantly, and in Firefox it does not exist at all. So the
   contract is small — deliver a cumulative transcript string when there is
   one, say nothing when there isn't, and never surface an error. The tracker
   and the voice-activity fallback do the rest. */

export function createAsr({ lang = 'en-US', onTranscript } = {}) {
  const Recognition = globalThis.SpeechRecognition || globalThis.webkitSpeechRecognition;
  if (!Recognition) {
    return { supported: false, start() {}, stop() {}, reset() {} };
  }

  let finals = '';
  let running = false;
  let recognition = null;

  function build() {
    const r = new Recognition();
    r.lang = lang;
    r.continuous = true;
    r.interimResults = true;
    r.maxAlternatives = 1;
    r.onresult = event => {
      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const text = event.results[i][0]?.transcript || '';
        if (event.results[i].isFinal) finals += ' ' + text;
        else interim += ' ' + text;
      }
      onTranscript?.((finals + ' ' + interim).trim());
    };
    // Chrome ends recognition after a few seconds of silence; keep it alive
    // for as long as the sentence is open. Errors ("no-speech", "aborted",
    // "network") are expected weather here, never shown to the user.
    r.onend = () => { if (running) { try { r.start(); } catch { /* already starting */ } } };
    r.onerror = () => {};
    return r;
  }

  return {
    supported: true,
    start() {
      if (running) return;
      running = true;
      recognition = build();
      try { recognition.start(); } catch { running = false; }
    },
    stop() {
      running = false;
      try { recognition?.stop(); } catch { /* not started */ }
      recognition = null;
    },
    reset() { finals = ''; },
  };
}
