/* Wires the calibration flow to the page. Everything testable lives in
   flow.js; this file only supplies the browser's parts.

   The phrases come from the server (GET /api/calibration/enrollment-set):
   the short everyday phrases chosen to cover every sound of English, each
   recorded `takes_per_phrase` times. */

import { Recorder } from '../recorder.js';
import { CalibrationApi } from './api.js';
import { CalibrationFlow, MAX_SENTENCE_MS } from './flow.js';
import { MicMonitor } from './vad.js';

const api = new CalibrationApi();

async function main() {
  let set;
  try {
    set = await api.enrollmentSet();
  } catch (error) {
    const begin = document.getElementById('begin-btn');
    if (begin) begin.disabled = true;
    const lede = document.querySelector('#screen-intro .lede');
    if (lede) lede.textContent = `We couldn’t load the phrases just now (${error.message}). Reload the page to try again.`;
    return;
  }
  const count = document.getElementById('phrase-count');
  if (count) count.textContent = String(set.phrases.length);
  new CalibrationFlow({
    doc: document,
    passage: set.phrases,
    takes: set.takes_per_phrase,
    api,
    makeRecorder: () => new Recorder({ minDuration: 400, maxDuration: MAX_SENTENCE_MS }),
    makeMonitor: options => new MicMonitor(options),
  }).attach();
}

main();
