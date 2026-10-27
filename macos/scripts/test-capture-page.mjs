import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const html = fs.readFileSync(new URL('../Sources/OpenIPhoneUseCore/Resources/capture.html', import.meta.url), 'utf8');
assert.equal(html, fs.readFileSync(new URL('../../server/oiu/web/capture.html', import.meta.url), 'utf8'));
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

const listeners = () => ({ handlers: {}, addEventListener(name, cb) { this.handlers[name] = cb; }, removeEventListener(name) { delete this.handlers[name]; } });
const element = () => {
  const classes = new Set();
  return {
    ...listeners(), classes, textContent: '', dataset: {}, disabled: false,
    classList: {
      contains: name => classes.has(name),
      toggle: (name, on) => { const next = on === undefined ? !classes.has(name) : on; if (next) classes.add(name); else classes.delete(name); return next; },
      add: name => classes.add(name),
      remove: name => classes.delete(name),
    },
  };
};

const start = element();
const stop = { ...element(), disabled: true };
const calibrateButton = { ...element() };
const boardBack = { ...element() };
const landing = element();
const live = element();
const headline = element();
const hint = element();
const dot = element();
const board = element();
board.getBoundingClientRect = () => ({ left: 0, top: 0, width: 400, height: 800 });
const boardHits = element();
const status = element();

const wake = { requested: 0, released: 0 };
const audio = { contexts: 0, oscillators: 0, closed: 0, requestedAudio: [], destinations: 0 };
const silentTrack = { kind: 'audio', readyState: 'live', stop() {} };
const audioTrack = { kind: 'audio', readyState: 'live', stop() {} };
const videoTrack = { kind: 'video', readyState: 'live', muted: false, handlers: {}, addEventListener(n, cb) { this.handlers[n] = cb; }, stop() {} };

let videoFrameCallback, intervalCallback, heldResponse, holdNextFrame = false, failCalibration = false;
let intervalDelay, rejectConstraint;
const displayConstraints = [];
let nextTimer = 0; const timers = new Map();
let nowMs = 1_700_000_000_000;
const tick = ms => { nowMs += ms; };
class FakeDate extends Date {
  constructor(...args) { if (args.length) super(...args); else super(nowMs); }
  static now() { return nowMs; }
}

const requests = [];
const video = {
  readyState: 4, videoWidth: 900, videoHeight: 1948, srcObject: null,
  paused: false, muted: true, volume: 1, currentTime: 0, plays: 0,
  async play() { video.plays += 1; video.paused = false; },
  requestVideoFrameCallback(cb) { videoFrameCallback = cb; return 1; },
  cancelVideoFrameCallback() { videoFrameCallback = null; },
};

const document = {
  ...listeners(), hidden: false,
  querySelector(selector) {
    return ({ '#start': start, '#stop': stop, '#preview': video, '#status': status,
              '#landing': landing, '#live': live,
              '#headline': headline, '#hint': hint, '#dot': dot,
              '#board': board, '#hits': boardHits,
              '#calibrate': calibrateButton, '#boardBack': boardBack })[selector];
  },
  createElement() { return { getContext: () => ({ drawImage() {} }), toBlob: cb => cb({ size: 100 }) }; },
};

class FakeAudioContext {
  constructor() { audio.contexts += 1; this.state = 'running'; this.destination = {}; }
  resume() { return Promise.resolve(); }
  createOscillator() {
    audio.oscillators += 1;
    return { connect: () => ({ connect: () => {} }), start: () => {}, stop: () => {} };
  }
  createGain() { return { gain: { value: 1 }, connect: () => ({ connect: () => {} }), disconnect: () => {} }; }
  createMediaStreamDestination() {
    audio.destinations += 1;
    return { stream: { getAudioTracks: () => [silentTrack] }, disconnect: () => {} };
  }
  close() { audio.closed += 1; this.state = 'closed'; return Promise.resolve(); }
}

const fakeWindow = { ...listeners(), AudioContext: FakeAudioContext, innerWidth: 400, innerHeight: 800 };
vm.runInNewContext(script, {
  document,
  window: fakeWindow,
  location: { search: '?token=test' },
  navigator: {
    wakeLock: { async request(kind) {
      assert.equal(kind, 'screen'); wake.requested += 1;
      return { addEventListener() {}, release() { wake.released += 1; } };
    } },
    mediaDevices: {
      async getDisplayMedia(constraints) {
        displayConstraints.push(constraints);
        audio.requestedAudio.push(constraints.audio);
        if (rejectConstraint && constraints.video !== true) {
          const error = new Error('Unsupported frame rate'); error.name = rejectConstraint; throw error;
        }
        // iOS returns no audio track for display capture, like the real device.
        return { getVideoTracks: () => [videoTrack], getAudioTracks: () => [], getTracks: () => [videoTrack] };
      },
      async getUserMedia() {
        audio.micRequested = (audio.micRequested || 0) + 1;
        return { getAudioTracks: () => [audioTrack] };
      },
    },
  },
  URLSearchParams, AbortController, Date: FakeDate, JSON, String, Error,
  MediaStream: class {
    constructor(tracks) { this.tracks = tracks; }
    getVideoTracks() { return this.tracks.filter(t => t.kind !== 'audio'); }
    getAudioTracks() { return this.tracks.filter(t => t.kind === 'audio'); }
    getTracks() { return this.tracks; }
  },
  setInterval(cb, delay) { intervalCallback = cb; intervalDelay = delay; return 1; }, clearInterval() { intervalCallback = null; },
  setTimeout(cb, delay) { const id = ++nextTimer; timers.set(id, { cb, delay }); return id; },
  clearTimeout(id) { timers.delete(id); },
  async fetch(path, options) {
    requests.push({ path, options });
    if (path === '/api/calibrate') return { ok: true, async json() { return failCalibration ? { ok: false, message: 'Calibration failed: keep Safari visible' } : { ok: true, message: 'Ready' }; } };
    if (path === '/api/frame' && holdNextFrame) {
      holdNextFrame = false;
      return new Promise(resolve => { heldResponse = resolve; });
    }
    return { ok: true, async json() { return { frameID: 'frame-' + requests.length, fps: 1, boardEpoch: 0, boardOpen: false }; } };
  },
});

const frameRequests = () => requests.filter(r => r.path === '/api/frame');
const stopRequests = reason => requests.filter(r => r.path === '/api/stop' && JSON.parse(r.options.body).reason === reason);
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

// The landing screen is what the owner sees before sharing.
assert.equal(landing.classList.contains('off'), false, 'the landing screen starts visible');
assert.equal(live.classList.contains('on'), false, 'the live section starts hidden');

await start.handlers.click();
assert.equal(displayConstraints[0].video.frameRate.ideal, 30);
assert.equal(intervalDelay, 16, 'fallback ticks must support 30 FPS and server rate changes');
assert.equal(wake.requested, 1, 'sharing requests a screen wake lock when supported');
assert.equal(audio.requestedAudio[0], false,
  'display capture must never ask for audio: a live microphone session stops other apps from autoplaying videos');
assert.ok(!audio.micRequested, 'no microphone may be requested, in any fallback');
assert.equal(audio.contexts, 1, 'an audio session must start with sharing');
assert.equal(audio.destinations, 1, 'the silent track must be routed to a stream destination');
assert.equal(video.muted, false, 'the preview element must not be muted, or iOS pauses it in the background');
assert.equal(video.srcObject.getAudioTracks().length, 1,
  'the preview element must carry exactly one audio track');
assert.equal(video.srcObject.getAudioTracks()[0], silentTrack,
  'the preview element must carry the silent track, never the live microphone');
assert.equal(board.classList.contains('on'), false,
  'the board must not open by itself: a calibrated phone does not need it, and it covers the screen');
// The Mac asks for the board with an epoch when a calibration is actually wanted.
holdNextFrame = true;
tick(1_000);
const wantBoard = intervalCallback();
await flush();
heldResponse({ ok: true, async json() { return { frameID: 'want', fps: 30, boardEpoch: 4, boardOpen: true }; } });
await wantBoard;
await flush();
assert.equal(requests.filter(r => r.path === '/api/calibrate').length, 1,
  'first received frame must request automatic calibration exactly once');
assert.equal(board.classList.contains('on'), true, 'a Mac epoch must still be able to open the board');
assert.equal(landing.classList.contains('off'), true, 'the landing screen must give way to the live section');
assert.equal(live.classList.contains('on'), true, 'the live section must appear once sharing starts');

await intervalCallback();
assert.equal(frameRequests().length, 1, 'a live track must upload without waiting for a frame callback');
await intervalCallback();
assert.equal(frameRequests().length, 1, 'the upload rate must be throttled, not free-running');
tick(1_000);

const beforeBackground = frameRequests().length;
document.hidden = true;
tick(1_000);
await intervalCallback();
assert.equal(frameRequests().length, beforeBackground + 1,
  'hiding the page must not stop uploads while iOS screen sharing remains active');
assert.equal(stopRequests('background').length, 0, 'hiding the page must never tell the receiver to stop');
document.hidden = false;

videoFrameCallback = null;
tick(1_000);
await intervalCallback();
assert.equal(frameRequests().length, beforeBackground + 2, 'a missing frame callback must not block uploads');

videoTrack.muted = true;
const beforeMute = frameRequests().length;
tick(1_000);
await intervalCallback();
assert.equal(frameRequests().length, beforeMute, 'a muted track must not publish');
videoTrack.muted = false;
videoTrack.handlers.unmute();
tick(1_000);
await intervalCallback();
assert.equal(frameRequests().length, beforeMute + 1);

holdNextFrame = true;
tick(1_000);
const pending = intervalCallback();
await flush();
assert.ok(heldResponse);
const held = frameRequests().at(-1);
document.hidden = true;
assert.equal(held.options.signal.aborted, false, 'hiding the page must not abort an in-flight upload');
heldResponse({ ok: true, async json() { return { frameID: 'late', fps: 1, boardEpoch: 0, boardOpen: false }; } });
await pending;
document.hidden = false;

// Repeating the same epoch must not fight a manual toggle: adopt it once, then leave the page be.
board.classList.remove('on');
holdNextFrame = true;
tick(1_000);
const closedAgain = intervalCallback();
await flush();
heldResponse({ ok: true, async json() { return { frameID: 'r', fps: 30, boardEpoch: 4, boardOpen: true }; } });
await closedAgain;
assert.equal(board.classList.contains('on'), true, 'an unchanged epoch must be ignored after it was applied');
holdNextFrame = true;
tick(1_000);
const repeatTick = intervalCallback();
await flush();
heldResponse({ ok: true, async json() { return { frameID: 'repeat', fps: 30, boardEpoch: 4, boardOpen: true }; } });
await repeatTick;
assert.equal(board.classList.contains('on'), true, 'an unchanged epoch must be ignored');

// A later epoch is a real request.
holdNextFrame = true;
tick(1_000);
const closeTick = intervalCallback();
await flush();
heldResponse({ ok: true, async json() { return { frameID: 'close', fps: 1, boardEpoch: 2, boardOpen: false }; } });
await closeTick;
assert.equal(board.classList.contains('on'), false, 'a new epoch must apply the requested board state');

holdNextFrame = true;
tick(1_000);
const sameTick = intervalCallback();
await flush();
heldResponse({ ok: true, async json() { return { frameID: 'same', fps: 1, boardEpoch: 2, boardOpen: true }; } });
await sameTick;
assert.equal(board.classList.contains('on'), false, 'an unchanged epoch must be ignored');

holdNextFrame = true;
tick(1_000);
const openTick = intervalCallback();
await flush();
heldResponse({ ok: true, async json() { return { frameID: 'open', fps: 1, boardEpoch: 3, boardOpen: true }; } });
await openTick;
assert.equal(board.classList.contains('on'), true, 'a further epoch must reopen the board');

// Board-local fractions are mapped into screen fractions by the receiver.
board.handlers.pointerdown({ clientX: 100, clientY: 400 });
await flush();
const tap = requests.filter(r => r.path === '/api/tap').at(-1);
assert.ok(tap, 'a tap on the board must be reported to the Mac');
assert.deepEqual(JSON.parse(tap.options.body), { x: 0.25, y: 0.5, geometry: {client: {x:100,y:400}, board: {left:0,top:0,width:400,height:800}, visualViewport:null} }, 'tap reports fractions and DOM geometry');
assert.equal(JSON.parse(status.textContent).board, true, 'the status must say the board is up');
fakeWindow.visualViewport = {offsetLeft: 3, offsetTop: 11, width: 320, height: 640, scale: 1.5};
board.getBoundingClientRect = () => ({ left: 20, top: 80, width: 320, height: 640 });
board.handlers.pointerdown({ clientX: 100, clientY: 400 });
await flush();
assert.deepEqual(JSON.parse(requests.filter(r => r.path === '/api/tap').at(-1).options.body),
  { x: 0.25, y: 0.5, geometry: {client: {x:100,y:400}, board: {left:20,top:80,width:320,height:640}, visualViewport:{offsetLeft:3,offsetTop:11,width:320,height:640,scale:1.5}} }, 'Safari viewport offsets/zoom must not change board-local tap fractions');
const leases = [...timers.values()].filter(timer => timer.delay === 10000);
assert.equal(leases.length, 1, 'an open board has one renewable lease');
leases[0].cb();
assert.equal(board.classList.contains('on'), false, 'a stalled server must close the board locally');
assert.match(hint.textContent, /Calibration interrupted/);



video.paused = true;
const playsBefore = video.plays;
tick(1_000);
await intervalCallback();
assert.ok(video.plays > playsBefore, 'a paused preview element must be played again');

// The owner can calibrate by hand and come back out, without the Mac deciding for them.
board.classList.remove('on');
calibrateButton.handlers.click();
await flush();
assert.equal(JSON.parse(requests.filter(r => r.path === '/api/calibrate').at(-1).options.body).force, true,
  'Calibrate must request actual Mac calibration, not just display the board');
board.classList.add('on');
boardBack.handlers.click();
assert.equal(board.classList.contains('on'), false, 'the Back button must close the board again');
board.classList.add('on');
failCalibration = true;
calibrateButton.handlers.click();
await flush();
assert.equal(board.classList.contains('on'), false, 'failed calibration closes the board immediately');
assert.match(hint.textContent, /Calibration failed/);
failCalibration = false;


stop.handlers.click();
assert.equal(intervalCallback, null);
assert.equal(videoFrameCallback, null);
assert.equal(wake.released, 1, 'stop releases the screen wake lock');
assert.equal(audio.closed, 1, 'stopping the share must close the audio session');
assert.equal(board.classList.contains('on'), false, 'stopping the share must close the board');
assert.equal(JSON.parse(requests.at(-1).options.body).reason, 'stopped');
console.log('Landing-to-board flow, audio keep-alive, background continuation, board epochs, taps and stop checks passed');

// Safari constraint rejection falls back without audio, and the fallback timer obeys
// updated rates while allowing only one upload in flight.
for (const name of ['TypeError', 'OverconstrainedError']) {
  rejectConstraint = name;
  const before = displayConstraints.length;
  await start.handlers.click();
  assert.equal(displayConstraints[before].video.frameRate.ideal, 30);
  assert.equal(displayConstraints[before + 1].video, true);
  assert.equal(displayConstraints[before + 1].audio, false);
  holdNextFrame = true;
  tick(1000);
  const pending = intervalCallback();
  await flush();
  const uploads = frameRequests().length;
  tick(34);
  await intervalCallback();
  assert.equal(frameRequests().length, uploads, 'never overlap uploads');
  heldResponse({ ok: true, async json() { return { fps: 30 }; } });
  await pending;
  tick(34);
  await intervalCallback();
  assert.equal(frameRequests().length, uploads + 1, '30 FPS reply must allow the next upload after 34ms');
  tick(34);
  await intervalCallback();
  assert.equal(frameRequests().length, uploads + 1, 'a 1 FPS reply must reduce the upload rate');
  stop.handlers.click();
}
assert.ok(audio.requestedAudio.every(value => value === false));
console.log('30 FPS capture, constraint fallback, dynamic rate gating and serialized uploads passed');
