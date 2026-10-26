'use strict';
let token = location.hash.slice(1) || sessionStorage.getItem('oiu-token') || '';
if (location.hash) { sessionStorage.setItem('oiu-token', token); history.replaceState(null, '', location.pathname); }
const $ = id => document.getElementById(id);
const tiles = new Map();
const removedDevices = new Set();
let captureOrigin = location.origin;
let setupCheckedAt = 0;
async function api(path, body, image = false, method = body ? 'POST' : 'GET') {
  const res = await fetch(path, {method, headers: {Authorization: 'Bearer ' + token, 'Content-Type': 'application/json'}, ...(body ? {body: JSON.stringify(body)} : {})});
  if (!res.ok) { const result = await res.json(); throw new Error(result.error || res.status); }
  return image ? res.blob() : res.json();
}
function failure(error) { $('error').textContent = error.message; }
async function copyLink(url) {
  try {
    if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(url); return; }
  } catch (_) { /* Clipboard permission denied: try the legacy selection API. */ }
  const previous = document.activeElement;
  const selection = document.getSelection();
  const ranges = selection ? Array.from({length: selection.rangeCount}, (_, i) => selection.getRangeAt(i).cloneRange()) : [];
  const textarea = document.createElement('textarea');
  textarea.value = url; textarea.readOnly = true;
  textarea.className = 'clipboard-fallback';
  document.body.append(textarea);
  try {
    textarea.select();
    if (!document.execCommand('copy')) throw new Error('Could not copy link. Select and copy the link manually.');
  } finally {
    textarea.remove(); previous?.focus();
    if (selection) { selection.removeAllRanges(); ranges.forEach(range => selection.addRange(range)); }
  }
}
function sharingLink(label, url) {
  const root = document.createElement('section'); root.className = 'sharing-link';
  const row = document.createElement('div'); row.className = 'link-actions';
  const link = document.createElement('a'); link.href = url; link.textContent = url;
  row.append(link);
  if (typeof navigator.share === 'function') {
    const share = document.createElement('button'); share.type = 'button'; share.textContent = 'Share';
    share.onclick = async () => {
      $('error').textContent = '';
      try { await navigator.share({title: 'Open iPhone Use', url}); }
      catch (error) { if (error.name !== 'AbortError') failure(error); }
    };
    row.append(share);
  }
  const copy = document.createElement('button'); copy.type = 'button'; copy.textContent = 'Copy';
  copy.onclick = async () => {
    $('error').textContent = '';
    try { await copyLink(url); $('error').textContent = 'Link copied.'; }
    catch (error) { failure(error); }
  };
  row.append(copy);
  const caption = document.createElement('p'); caption.textContent = label;
  root.append(caption, row);
  try {
    const qr = qrcode(0, 'M'); qr.addData(url); qr.make();
    const canvas = document.createElement('canvas'); canvas.className = 'link-qr';
    canvas.setAttribute('role', 'img'); canvas.setAttribute('aria-label', `QR code: ${label}`);
    const size = qr.getModuleCount(), scale = 4, margin = 4;
    canvas.width = canvas.height = (size + margin * 2) * scale;
    const context = canvas.getContext('2d');
    context.fillStyle = '#fff'; context.fillRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = '#000';
    for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
      if (qr.isDark(y, x)) context.fillRect((x + margin) * scale, (y + margin) * scale, scale, scale);
    }
    root.append(canvas);
  } catch (error) { failure(new Error(`QR code unavailable: ${error.message || error}`)); }
  return root;
}
$('login').onsubmit = event => {event.preventDefault(); $('error').textContent = ''; token = $('token').value; sessionStorage.setItem('oiu-token', token); refresh();};
let candidateKey = '';
const adding = new Set();
function bluetoothCandidates(candidates) {
  const key = JSON.stringify(candidates);
  if (candidateKey === key) return;
  candidateKey = key;
  const rows = candidates.map(peer => {
    const row = document.createElement('p');
    const label = document.createElement('span'); label.textContent = `${peer.name} • Bluetooth HID connected${peer.name_source === 'fallback' ? ' (name unavailable)' : ''} `;
    const button = document.createElement('button'); button.type = 'button';
    button.textContent = peer.available ? 'Add phone' : 'Already added'; button.disabled = !peer.available || adding.has(peer.id);
    button.onclick = async () => {
      if (adding.has(peer.id)) return;
      adding.add(peer.id); button.disabled = true;
      try { await api('/api/devices', {bt_id: peer.id}); candidateKey = ''; await refresh(); }
      catch (error) { failure(error); }
      finally { adding.delete(peer.id); if (peer.available) button.disabled = false; }
    };
    row.append(label, button); return row;
  });
  if (!rows.length) { const empty = document.createElement('p'); empty.textContent = 'No Bluetooth HID phones connected. Pair/connect an iPhone first (keyboard and pointer).'; rows.push(empty); }
  $('bluetooth-list').replaceChildren(...rows);
}
async function showFrame(t, frame) {
  const bytes = Uint8Array.from(atob(frame.base64), c => c.charCodeAt(0));
  const next = URL.createObjectURL(new Blob([bytes], {type: 'image/jpeg'}));
  const img = t.root.querySelector('img'); img.src = next;
  try { await img.decode(); }
  catch (error) { URL.revokeObjectURL(next); throw error; }
  if (t.removed) { URL.revokeObjectURL(next); return; }
  img.hidden = false;
  if (t.url) URL.revokeObjectURL(t.url);
  t.url = next; t.frame = frame.frameID;
}
function tile(device) {
  const root = document.createElement('article'); root.className = 'phone';
  // Static markup only. Names, IDs and backend errors are always textContent.
  root.innerHTML = '<h2></h2><p class="status"></p><p class="placeholder" role="status">Waiting for screen sharing</p><img class="screen" alt="Live iPhone screen" hidden><div class="device-link"></div><p class="result" role="status"></p>';
  const value = {root, device, url: null, frame: null, sharingURL: null};
  const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = 'Remove';
  value.removeButton = remove;
  remove.onclick = async () => {
    if (remove.disabled || !confirm(`Remove ${value.device.name}? Its sharing link will stop working and control will stop.`)) return;
    remove.disabled = true; $('error').textContent = '';
    try {
      const result = await api(`/api/devices/${value.device.device_id}`, null, false, 'DELETE');
      value.removed = true; removedDevices.add(value.device.device_id);
      if (value.url) URL.revokeObjectURL(value.url);
      value.url = null; value.frame = null;
      root.remove(); tiles.delete(value.device.device_id);
      $('error').textContent = result.message;
      await refresh();
    } catch (error) { failure(error); }
    finally { remove.disabled = false; }
  };
  const act = async (operation, arguments_ = {}) => {
    try {
      const result = await api(`/api/devices/${value.device.device_id}/action`, {operation, arguments: {expectedFrameID: value.frame, ...arguments_}});
      root.querySelector('.result').textContent = result.message || JSON.stringify(result);
      if (result.frame && !value.removed) await showFrame(value, result.frame);
      return result;
    } catch (error) { root.querySelector('.result').textContent = error.message; return null; }
  };
  const img = root.querySelector('img');
  // Prepare the HID session automatically before the first tap on this tile.
  img.onclick = async event => {
    if (value.acting || !value.device.bluetooth_connected || value.removed) return;
    value.acting = true;
    try {
      const r = img.getBoundingClientRect(), point = {x: (event.clientX-r.left)/r.width, y: (event.clientY-r.top)/r.height};
      if (!value.prepared) {
        const prepared = await act('ensure_session');
        if (!prepared?.ok) return;
        value.prepared = true;
      }
      await act('pointer_tap', point);
    } finally { value.acting = false; }
  };
  $('grid').append(root); return value;
}
let refreshing = false;
async function refresh() {
  if (!token || refreshing) return;
  refreshing = true;
  try {
    const registered = await api('/api/devices');
    if (!setupCheckedAt || Date.now() - setupCheckedAt >= 5000) {
      const setup = await api('/api/setup');
      captureOrigin = setup.capture_origin || location.origin; setupCheckedAt = Date.now();
    }
    try {
      bluetoothCandidates(await api('/api/bluetooth')); $('bluetooth-error').textContent = '';
    } catch (error) {
      bluetoothCandidates([]); $('bluetooth-error').textContent = error.message;
    }
    const devices = registered.filter(d => !removedDevices.has(d.device_id));
    $('login').hidden = true; $('workspace').hidden = false;
    $('count').textContent = `${devices.filter(d => d.capture_active).length} sharing / ${devices.length} phones`;
    $('empty').hidden = devices.length > 0;
    const ids = new Set(devices.map(d => d.device_id));
    for (const [id, t] of tiles) {
      if (!ids.has(id)) {
        if (t.url) URL.revokeObjectURL(t.url);
        t.root.remove(); tiles.delete(id);
      }
    }
    for (const d of devices) {
      if (!tiles.has(d.device_id)) tiles.set(d.device_id, tile(d));
      const t = tiles.get(d.device_id); t.device = d;
      t.root.querySelector('h2').textContent = d.name;
      t.root.classList.toggle('live', d.capture_active);
      const sharingState = !d.frame_id ? 'Waiting for screen sharing' : d.capture_active ? 'Live' : 'Sharing paused';
      if (!d.bluetooth_connected || !d.capture_active) t.prepared = false;
      t.root.querySelector('.status').textContent = `Bluetooth: ${d.bluetooth_connected ? 'connected' : 'connection required'} • ${d.device_id.slice(0,8)} • ${sharingState}${d.age_ms == null ? '' : ` • ${d.age_ms} ms`}\n${d.control_status || 'Control unavailable'}`;
      t.root.querySelector('.placeholder').hidden = !!d.frame_id;
      const sharingURL = captureOrigin + d.capture_path;
      if (t.sharingURL !== sharingURL || t.sharingName !== d.name) {
        const link = sharingLink(`Private sharing link for ${d.name}:`, sharingURL);
        link.querySelector('.link-actions').append(t.removeButton);
        t.root.querySelector('.device-link').replaceChildren(link);
        t.sharingURL = sharingURL; t.sharingName = d.name;
      }
      if (!d.frame_id) {
        const img = t.root.querySelector('img'); img.hidden = true; img.removeAttribute('src');
        if (t.url) URL.revokeObjectURL(t.url);
        t.url = null; t.frame = null;
      }
    }
    for (const d of devices) {
      const t = tiles.get(d.device_id);
      if (t && !t.removed && !t.loading && !t.acting && d.frame_id && t.frame !== d.frame_id) {
        t.loading = true;
        (async () => {
          try {
            const snapshot = await api(`/api/devices/${d.device_id}/snapshot`);
            if (t.removed || t.acting) return;
            await showFrame(t, snapshot.frame);
          } catch (error) { t.root.querySelector('.result').textContent = error.message; }
          finally { t.loading = false; }
        })();
      }
    }
  } catch (error) {failure(error); if (error.message.includes('credential')) {$('login').hidden = false; $('workspace').hidden = true;}}
  finally { refreshing = false; }
}
refresh(); setInterval(refresh, 500);
