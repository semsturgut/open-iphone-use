'use strict';
let token = location.hash.slice(1) || sessionStorage.getItem('oiu-token') || '';
if (location.hash) { sessionStorage.setItem('oiu-token', token); history.replaceState(null, '', location.pathname); }
const $ = id => document.getElementById(id);
const tiles = new Map();
let captureOrigin = location.origin;
let setupCheckedAt = 0;
async function api(path, body, image = false) {
  const res = await fetch(path, {headers: {Authorization: 'Bearer ' + token, 'Content-Type': 'application/json'}, ...(body ? {method: 'POST', body: JSON.stringify(body)} : {})});
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
$('add').onsubmit = async event => {
  event.preventDefault();
  $('error').textContent = '';
  try {
    await api('/api/devices', {name: $('name').value});
    $('name').value = ''; await refresh();
  } catch (error) { failure(error); }
};
function tile(device) {
  const root = document.createElement('article'); root.className = 'phone';
  // Static markup only. Names, IDs and backend errors are always textContent.
  root.innerHTML = '<h2></h2><p class="status"></p><p class="placeholder" role="status">Waiting for screen sharing</p><img class="screen" alt="Live iPhone screen" hidden><div class="device-link"></div><p class="result" role="status"></p>';
  const value = {root, device, url: null, frame: null, sharingURL: null};
  const act = async (operation, arguments_ = {}) => {
    try {
      const result = await api(`/api/devices/${value.device.device_id}/action`, {operation, arguments: {expectedFrameID: value.frame, ...arguments_}});
      root.querySelector('.result').textContent = result.message || JSON.stringify(result);
    } catch (error) { root.querySelector('.result').textContent = error.message; }
  };
  const img = root.querySelector('img');
  // Prepare the HID session automatically before the first tap on this tile.
  img.onclick = async event => {const r = img.getBoundingClientRect(); const point = {x: (event.clientX-r.left)/r.width, y: (event.clientY-r.top)/r.height}; if (!value.prepared) {await act('ensure_session'); value.prepared = true;} act('pointer_tap', point);};
  $('grid').append(root); return value;
}
let refreshing = false;
async function refresh() {
  if (!token || refreshing) return;
  refreshing = true;
  try {
    const devices = await api('/api/devices');
    if (!$('join').firstChild || Date.now() - setupCheckedAt >= 5000) {
      const setup = await api('/api/setup');
      const origin = setup.capture_origin || location.origin;
      if (!$('join').firstChild || origin !== captureOrigin) {
        $('join').replaceChildren(sharingLink('Open this private link in Safari on each iPhone:', origin + setup.join_path));
      }
      captureOrigin = origin; setupCheckedAt = Date.now();
    }
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
      t.root.querySelector('.status').textContent = `${d.device_id.slice(0,8)} • ${sharingState}${d.age_ms == null ? '' : ` • ${d.age_ms} ms`}\n${d.control_status || 'Control unavailable'}`;
      t.root.querySelector('.placeholder').hidden = !!d.frame_id;
      const sharingURL = captureOrigin + d.capture_path;
      if (t.sharingURL !== sharingURL) {
        t.root.querySelector('.device-link').replaceChildren(sharingLink(`Private sharing link for ${d.name}:`, sharingURL));
        t.sharingURL = sharingURL;
      }
      if (!d.frame_id) {
        const img = t.root.querySelector('img'); img.hidden = true; img.removeAttribute('src');
        if (t.url) URL.revokeObjectURL(t.url);
        t.url = null; t.frame = null;
      }
    }
    // Render every registered phone before waiting for any live snapshot.
    for (const d of devices) {
      const t = tiles.get(d.device_id);
      if (d.frame_id && t.frame !== d.frame_id) {
        try {
          // Use snapshot metadata and JPEG atomically so a click references the image displayed.
          const snapshot = await api(`/api/devices/${d.device_id}/snapshot`);
          const bytes = Uint8Array.from(atob(snapshot.frame.base64), c => c.charCodeAt(0));
          const next = URL.createObjectURL(new Blob([bytes], {type: 'image/jpeg'}));
          const img = t.root.querySelector('img'); img.src = next; await img.decode(); img.hidden = false;
          if (t.url) URL.revokeObjectURL(t.url); t.url = next; t.frame = snapshot.frame.frameID;
        } catch (error) { t.root.querySelector('.result').textContent = error.message; }
      }
    }
  } catch (error) {failure(error); if (error.message.includes('credential')) {$('login').hidden = false; $('workspace').hidden = true;}}
  finally { refreshing = false; }
}
refresh(); setInterval(refresh, 500);
