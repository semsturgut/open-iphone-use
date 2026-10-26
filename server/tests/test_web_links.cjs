// Dependency-free checks for browser API behavior; no physical AirDrop claim.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.textContent = ''; this.rects = []; }
  append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  get firstChild() { return this.children[0]; }
  setAttribute(key, value) { this[key] = value; }
  removeAttribute(key) { delete this[key]; }
  set innerHTML(markup) {
    // Only the fixed tile template is parsed; dynamic values use textContent.
    this.children = [];
    for (const match of markup.matchAll(/<(h2|p|img|div|form|input|button)\b([^>]*)>/g)) {
      const child = new Element(match[1]);
      child.className = /class="([^"]*)"/.exec(match[2])?.[1] || '';
      child.hidden = /\bhidden\b/.test(match[2]);
      this.append(child);
    }
  }
  querySelector(selector) {
    return this.children.find(child => selector.startsWith('.')
      ? child.className === selector.slice(1) : child.tagName === selector);
  }
  classList = {toggle() {}};
  async decode() {}
  focus() { document.activeElement = this; }
  select() { this.focus(); selected = this.value; }
  remove() { this.parent.children = this.parent.children.filter(child => child !== this); }
  getContext() { return {fillRect: (...args) => this.rects.push(args)}; }
}
let selected, copyResult = true;
const elements = new Map();
const document = {
  body: new Element('body'), activeElement: new Element('button'),
  getElementById(id) { if (!elements.has(id)) elements.set(id, new Element('div')); return elements.get(id); },
  createElement: tag => new Element(tag), getSelection: () => null,
  execCommand(command) { assert.equal(command, 'copy'); return copyResult; }
};
const navigator = {};
const requests = [];
let devices = [
  {device_id: 'phone-a', name: 'Phone A', frame_id: null, age_ms: null, capture_active: false,
   control_status: 'Pair iPhone, then Prepare', capture_path: '/capture?token=phone-a'},
  {device_id: 'phone-b', name: 'Phone B', frame_id: null, age_ms: null, capture_active: false,
   control_status: 'Paired; prepare pointer', capture_path: '/capture?token=phone-b'}
];
const revoked = [];
let blobCounter = 0;
let captureOrigin = 'https://192.168.1.2:27891';
const context = vm.createContext({
  document, navigator, location: {hash: '', origin: 'http://127.0.0.1:27893'},
  sessionStorage: {getItem: () => '', setItem() {}}, setInterval() {},
  Blob, atob, URL: {createObjectURL: () => 'blob:' + (++blobCounter), revokeObjectURL: url => revoked.push(url)},
  fetch: async (url, options) => {
    requests.push(url);
    assert.equal(options.headers.Authorization, 'Bearer operator');
    let data;
    if (url === '/api/setup') data = {capture_origin: captureOrigin, join_path: '/join?token=private'};
    else if (options.method === 'POST') {
      const id = 'phone-' + devices.length;
      data = {device_id: id, name: 'Test phone', frame_id: null, capture_path: '/capture?token=' + id};
      devices.push(data);
    } else if (url.endsWith('/snapshot')) {
      const device = devices.find(d => url.includes('/' + d.device_id + '/'));
      data = {frame: {frameID: device.frame_id, base64: 'aW1hZ2U='}};
    } else data = devices;
    return {ok: true, json: async () => data};
  }
});
const web = path.join(__dirname, '../oiu/web');
vm.runInContext(fs.readFileSync(path.join(web, 'qrcode.js'), 'utf8'), context);
vm.runInContext(fs.readFileSync(path.join(web, 'app.js'), 'utf8'), context);
const run = source => vm.runInContext(source, context);
const status = document.getElementById('error');
const buttons = root => root.children[1].children.filter(child => child.tagName === 'button');
(async () => {
  const url = 'https://192.168.1.2:27891/capture?token=' + 'a'.repeat(64);
  context.testURL = url;
  let shared, copied;
  navigator.share = async data => { shared = data; };
  navigator.clipboard = {writeText: async value => { copied = value; }};
  let root = run("sharingLink('Phone <one>', testURL)");
  assert.equal(root.children[0].textContent, 'Phone <one>');
  assert.equal(root.children[1].children[0].href, url);
  assert.deepEqual(buttons(root).map(b => b.textContent), ['Share', 'Copy']);
  const qr = root.children[2];
  assert.equal(qr.tagName, 'canvas');
  assert.equal(qr.width, qr.height);
  assert.ok(qr.rects.length > 100);
  assert.deepEqual(qr.rects[0], [0, 0, qr.width, qr.height]);
  // A four-module white border and integer module sizes make the QR scannable.
  assert.ok(qr.rects.slice(1).every(([x, y, w, h]) => x >= 16 && y >= 16 && w === 4 && h === 4));
  await buttons(root)[0].onclick();
  assert.equal(shared.title, 'Open iPhone Use'); assert.equal(shared.url, url);
  navigator.share = async () => { throw Object.assign(new Error('cancelled'), {name: 'AbortError'}); };
  await buttons(root)[0].onclick(); assert.equal(status.textContent, '');
  navigator.share = async () => { throw new Error('Sharing denied'); };
  await buttons(root)[0].onclick(); assert.equal(status.textContent, 'Sharing denied');
  await buttons(root)[1].onclick(); assert.equal(copied, url); assert.equal(status.textContent, 'Link copied.');
  navigator.clipboard.writeText = async () => { throw new Error('permission denied'); };
  const previous = document.activeElement;
  await buttons(root)[1].onclick(); assert.equal(selected, url);
  assert.equal(document.body.children.length, 0); assert.equal(document.activeElement, previous);
  delete navigator.clipboard; delete navigator.share;
  root = run("sharingLink('Phone two', testURL)");
  assert.deepEqual(buttons(root).map(b => b.textContent), ['Copy']);
  await buttons(root)[0].onclick(); assert.equal(selected, url);
  copyResult = false;
  await buttons(root)[0].onclick(); assert.match(status.textContent, /Could not copy/);
  assert.equal(document.body.children.length, 0); assert.equal(document.activeElement, previous);
  // Exercise the actual setup and create-link paths, including repeated creation.
  await run("token = 'operator'; refresh()");
  const join = document.getElementById('join');
  assert.equal(join.children[0].children[1].children[0].href, 'https://192.168.1.2:27891/join?token=private');
  status.textContent = 'Sharing denied';
  await run('refresh()'); assert.equal(status.textContent, 'Sharing denied');
  for (let i = 0; i < 2; i++) {
    document.getElementById('name').value = 'Test phone';
    await document.getElementById('add').onsubmit({preventDefault() {}});
  }
  const grid = document.getElementById('grid');
  assert.equal(grid.children.length, 4); // Includes registered phones that never uploaded.
  assert.equal(document.getElementById('empty').hidden, true);
  assert.equal(requests.filter(url => url.endsWith('/snapshot')).length, 0);
  for (let i = 0; i < devices.length; i++) {
    const tile = grid.children[i];
    assert.equal(tile.querySelector('.placeholder').hidden, false);
    assert.equal(tile.querySelector('img').hidden, true);
    assert.match(tile.querySelector('.status').textContent, /Waiting for screen sharing/);
    assert.doesNotMatch(tile.querySelector('.status').textContent, /null ms/);
    const item = tile.querySelector('.device-link').children[0];
    assert.equal(item.children[1].children[0].href, 'https://192.168.1.2:27891' + devices[i].capture_path);
    assert.equal(buttons(item)[0].textContent, 'Copy'); assert.equal(item.children[2].tagName, 'canvas');
  }
  assert.match(grid.children[0].querySelector('.status').textContent, /Pair iPhone/);
  assert.match(grid.children[1].querySelector('.status').textContent, /Paired; prepare/);
  // Frame ingress upgrades the same tile and keeps its sharing controls intact.
  const firstTile = grid.children[0], firstLink = firstTile.querySelector('.device-link').firstChild;
  Object.assign(devices[0], {frame_id: 'frame-1', capture_active: true, age_ms: 1});
  await run('refresh()');
  assert.equal(grid.children[0], firstTile);
  assert.equal(firstTile.querySelector('.placeholder').hidden, true);
  assert.equal(firstTile.querySelector('img').hidden, false);
  assert.equal(firstTile.querySelector('.device-link').firstChild, firstLink);
  assert.equal(run("tiles.get('phone-a').frame"), 'frame-1');
  await run('refresh()');
  assert.equal(requests.filter(url => url.endsWith('/snapshot')).length, 1);
  // After a restart, no frames remain but every phone and its link still does.
  Object.assign(devices[0], {frame_id: null, capture_active: false, age_ms: null});
  await run('refresh()');
  assert.equal(firstTile.querySelector('img').hidden, true);
  assert.equal(firstTile.querySelector('.placeholder').hidden, false);
  assert.ok(revoked.includes('blob:1'));
  // Simulate a fresh page: no retained tiles or creation responses.
  grid.replaceChildren(); run('tiles.clear()'); join.replaceChildren();
  await run('refresh()');
  assert.equal(grid.children.length, 4);
  for (let i = 0; i < devices.length; i++) {
    assert.equal(grid.children[i].querySelector('.device-link').firstChild.children[1].children[0].href,
      'https://192.168.1.2:27891' + devices[i].capture_path);
  }
  // A route change refreshes the invitation and every phone link without reloading.
  captureOrigin = 'https://172.20.10.2:27891';
  await run('setupCheckedAt = 0; refresh()');
  assert.equal(join.firstChild.children[1].children[0].href, captureOrigin + '/join?token=private');
  for (let i = 0; i < devices.length; i++) {
    assert.equal(grid.children[i].querySelector('.device-link').firstChild.children[1].children[0].href,
      captureOrigin + devices[i].capture_path);
  }
  devices = [];
  await run('refresh()');
  assert.equal(grid.children.length, 0);
  assert.equal(document.getElementById('empty').hidden, false);
  console.log('Web sharing controls passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
