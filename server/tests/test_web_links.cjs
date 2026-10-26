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
const context = vm.createContext({
  document, navigator, location: {hash: '', origin: 'http://127.0.0.1:27893'},
  sessionStorage: {getItem: () => '', setItem() {}}, setInterval() {},
  fetch: async (url, options) => {
    requests.push(url);
    const data = url === '/api/setup' ? {capture_origin: 'https://192.168.1.2:27891', join_path: '/join?token=private'}
      : options.method === 'POST' ? {name: 'Test phone', capture_path: '/capture?token=phone'} : [];
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
  const invite = document.getElementById('invite');
  assert.equal(invite.children.length, 2);
  for (const item of invite.children) {
    assert.equal(item.children[1].children[0].href, 'https://192.168.1.2:27891/capture?token=phone');
    assert.equal(buttons(item)[0].textContent, 'Copy'); assert.equal(item.children[2].tagName, 'canvas');
  }
  console.log('Web sharing controls passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
