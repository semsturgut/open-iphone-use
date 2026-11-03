'use strict';
const $ = id => document.getElementById(id);
const tiles = new Map();
async function api(path, body, method = body ? 'POST' : 'GET') {
  const res = await fetch(path, {method, headers: {'Content-Type': 'application/json'}, ...(body ? {body: JSON.stringify(body)} : {})});
  const result = await res.json();
  if (!res.ok) throw new Error(result.error || res.status);
  return result;
}
function failure(error) { $('error').textContent = error.message; }
let candidatesKey = '', candidatesAt = 0;
const adding = new Set();
function showCandidates(candidates) {
  const key = JSON.stringify(candidates);
  if (candidatesKey === key) return;
  candidatesKey = key;
  const rows = candidates.map(phone => {
    const row = document.createElement('p');
    const label = document.createElement('span');
    label.textContent = `${phone.name || 'iPhone (trust this Mac on the phone)'}${phone.ios ? ` • iOS ${phone.ios}` : ''} • ${phone.connection} `;
    const button = document.createElement('button'); button.type = 'button';
    button.textContent = phone.available ? 'Add phone' : 'Added'; button.disabled = !phone.available || adding.has(phone.udid);
    button.onclick = async () => {
      if (adding.has(phone.udid)) return;
      adding.add(phone.udid); button.disabled = true;
      try { await api('/api/devices', {udid: phone.udid}); candidatesKey = ''; candidatesAt = 0; await refresh(); }
      catch (error) { failure(error); }
      finally { adding.delete(phone.udid); if (phone.available) button.disabled = false; }
    };
    row.append(label, button); return row;
  });
  if (!rows.length) { const empty = document.createElement('p'); empty.textContent = 'No phones found. Connect the iPhone by cable and trust this Mac.'; rows.push(empty); }
  $('candidates').replaceChildren(...rows);
}
function tile(device) {
  const root = document.createElement('article'); root.className = 'phone';
  // Static markup only. Names and backend errors are always textContent.
  root.innerHTML = '<div class="phone-head"><h2></h2><button type="button" class="remove">Remove</button></div><p class="status"></p><p class="placeholder" role="status">Connecting</p><img class="screen" alt="Live iPhone screen" hidden><p class="result" role="status"></p>';
  const value = {root, device};
  const remove = root.querySelector('.remove');
  remove.onclick = async () => {
    if (remove.disabled || !confirm(`Remove ${value.device.name}? Control of this phone will stop.`)) return;
    remove.disabled = true; $('error').textContent = '';
    try {
      const result = await api(`/api/devices/${value.device.device_id}`, null, 'DELETE');
      value.removed = true; root.remove(); tiles.delete(value.device.device_id);
      $('error').textContent = result.message; candidatesKey = ''; candidatesAt = 0;
      await refresh();
    } catch (error) { failure(error); }
    finally { remove.disabled = false; }
  };
  // The live view has no frame ID of its own: act on the newest frame, retrying once
  // if that frame predates the previous input.
  const act = async (operation, arguments_ = {}) => {
    const result = root.querySelector('.result');
    try {
      for (let attempt = 0; attempt < 2; attempt++) {
        const frame = (await api('/api/devices')).find(d => d.device_id === value.device.device_id)?.frame_id;
        try { const r = await api(`/api/devices/${value.device.device_id}/action`, {operation, arguments: {expectedFrameID: frame, ...arguments_}}); result.textContent = r.message || ''; return r; }
        catch (error) { if (attempt || !/before your last action/.test(error.message)) throw error; await new Promise(r => setTimeout(r, 300)); }
      }
    } catch (error) { result.textContent = error.message; return null; }
  };
  const img = root.querySelector('img');
  img.onclick = async event => {
    if (value.acting || value.removed) return;
    value.acting = true;
    try {
      const r = img.getBoundingClientRect();
      await act('pointer_tap', {x: (event.clientX - r.left) / r.width, y: (event.clientY - r.top) / r.height});
    } finally { value.acting = false; }
  };
  $('grid').append(root); return value;
}
let refreshing = false;
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const devices = await api('/api/devices');
    if (Date.now() - candidatesAt >= 2000) {
      candidatesAt = Date.now();
      try { showCandidates(await api('/api/candidates')); $('candidates-error').textContent = ''; }
      catch (error) { showCandidates([]); $('candidates-error').textContent = error.message; }
    }
    $('count').textContent = `${devices.filter(d => d.capture_active).length} live / ${devices.length} phones`;
    $('empty').hidden = devices.length > 0;
    const ids = new Set(devices.map(d => d.device_id));
    for (const [id, t] of tiles) if (!ids.has(id)) { t.root.remove(); tiles.delete(id); }
    for (const d of devices) {
      if (!tiles.has(d.device_id)) tiles.set(d.device_id, tile(d));
      const t = tiles.get(d.device_id); t.device = d;
      t.root.querySelector('h2').textContent = d.name;
      t.root.classList.toggle('live', d.capture_active);
      t.root.querySelector('.status').textContent = `${d.control_status}${d.capture_active ? ` • ${d.age_ms} ms` : ''}`;
      t.root.querySelector('.placeholder').hidden = !!d.frame_id;
      const img = t.root.querySelector('img');
      if (d.frame_id && img.hidden) { img.src = `/api/devices/${d.device_id}/stream`; img.hidden = false; }
      if (!d.frame_id && !img.hidden) { img.hidden = true; img.removeAttribute('src'); }
    }
    $('error').textContent = '';
  } catch (error) { failure(error); }
  finally { refreshing = false; }
}
refresh(); setInterval(refresh, 1000);
