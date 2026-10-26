'use strict';
let token = sessionStorage.getItem('oiu-token') || '';
const $ = id => document.getElementById(id);
const tiles = new Map();
async function api(path, body, image = false) {
  const res = await fetch(path, {headers: {Authorization: 'Bearer ' + token, 'Content-Type': 'application/json'}, ...(body ? {method: 'POST', body: JSON.stringify(body)} : {})});
  if (!res.ok) { const result = await res.json(); throw new Error(result.error || res.status); }
  return image ? res.blob() : res.json();
}
function failure(error) { $('error').textContent = error.message; }
$('login').onsubmit = event => {event.preventDefault(); token = $('token').value; sessionStorage.setItem('oiu-token', token); refresh();};
$('add').onsubmit = async event => {
  event.preventDefault();
  try {
    const device = await api('/api/devices', {name: $('name').value});
    const link = document.createElement('a');
    link.href = device.capture_path; link.textContent = location.origin + device.capture_path;
    $('invite').replaceChildren(document.createTextNode('Private sharing link for this phone: '), link);
    $('name').value = ''; await refresh();
  } catch (error) { failure(error); }
};
function tile(device) {
  const root = document.createElement('article'); root.className = 'phone';
  // Static markup only. Names, IDs and backend errors are always textContent.
  root.innerHTML = '<h2></h2><p class="status"></p><img class="screen" alt="Live iPhone screen" hidden><div class="controls"></div><form><input aria-label="Text to type" placeholder="Text to type" maxlength="500"><button>Type</button></form><p class="result" role="status"></p>';
  const value = {root, device, url: null, frame: null};
  const act = async (operation, arguments_ = {}) => {
    try {
      const result = await api(`/api/devices/${value.device.device_id}/action`, {operation, arguments: {expectedFrameID: value.frame, ...arguments_}});
      root.querySelector('.result').textContent = result.message || JSON.stringify(result);
    } catch (error) { root.querySelector('.result').textContent = error.message; }
  };
  for (const [label, op, args] of [['Prepare', 'ensure_session', {}], ['Home', 'send_key', {key: 'cmd+h'}], ['Scroll up', 'scroll', {dy: -100}], ['Scroll down', 'scroll', {dy: 100}], ['Release input', 'stop_control', {}]]) {
    const button = document.createElement('button'); button.textContent = label;
    button.onclick = () => act(op, args); root.querySelector('.controls').append(button);
  }
  const img = root.querySelector('img');
  img.onclick = event => {const r = img.getBoundingClientRect(); act('pointer_tap', {x: (event.clientX-r.left)/r.width, y: (event.clientY-r.top)/r.height});};
  root.querySelector('form').onsubmit = event => {event.preventDefault(); const input = root.querySelector('input'); act('type_text', {text: input.value});};
  $('grid').append(root); return value;
}
let refreshing = false;
async function refresh() {
  if (!token || refreshing) return;
  refreshing = true;
  try {
    const devices = await api('/api/devices');
    $('login').hidden = true; $('workspace').hidden = false; $('error').textContent = '';
    $('count').textContent = `${devices.filter(d => d.capture_active).length} sharing / ${devices.length} phones`;
    $('empty').hidden = devices.some(d => d.frame_id);
    for (const d of devices) {
      if (!d.frame_id) continue; // A tile appears automatically on first upload.
      if (!tiles.has(d.device_id)) tiles.set(d.device_id, tile(d));
      const t = tiles.get(d.device_id); t.device = d;
      t.root.querySelector('h2').textContent = d.name;
      t.root.classList.toggle('live', d.capture_active);
      t.root.querySelector('.status').textContent = `${d.capture_active ? 'Live' : 'Sharing paused'} • ${d.age_ms} ms\n${d.control_status || "Control unavailable"}`;
      if (t.frame !== d.frame_id) {
        // Use snapshot metadata and JPEG atomically so a click references the image displayed.
        const snapshot = await api(`/api/devices/${d.device_id}/snapshot`);
        const bytes = Uint8Array.from(atob(snapshot.frame.base64), c => c.charCodeAt(0));
        const next = URL.createObjectURL(new Blob([bytes], {type: 'image/jpeg'}));
        const img = t.root.querySelector('img'); img.src = next; await img.decode(); img.hidden = false;
        if (t.url) URL.revokeObjectURL(t.url); t.url = next; t.frame = snapshot.frame.frameID;
      }
    }
  } catch (error) {failure(error); if (error.message.includes('credential')) {$('login').hidden = false; $('workspace').hidden = true;}}
  finally { refreshing = false; }
}
refresh(); setInterval(refresh, 500);
