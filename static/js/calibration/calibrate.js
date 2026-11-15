/* Wires the calibration flow to the page. Everything testable lives in
   flow.js; this file only supplies the browser's parts. */

import { Recorder } from '../recorder.js';
import { CalibrationApi } from './api.js';
import { CalibrationFlow, MAX_SENTENCE_MS } from './flow.js';
import { PASSAGE } from './passage.js';
import { MicMonitor } from './vad.js';

new CalibrationFlow({
  doc: document,
  passage: PASSAGE,
  api: new CalibrationApi(),
  makeRecorder: () => new Recorder({ minDuration: 400, maxDuration: MAX_SENTENCE_MS }),
  makeMonitor: options => new MicMonitor(options),
}).attach();
