'use strict';
const TOKEN = document.querySelector('meta[name="panel-token"]').content;
const $ = id => document.getElementById(id);
const el = (tag, props = {}, ...kids) => { const n = Object.assign(document.createElement(tag), props); for (const k of kids) if (k != null) n.append(k); return n; };

// Geometry of the daemon's preview PNG (m18lib/render.py render_preview): 792 x 650.
const W = 792, H = 650, K = 128, GAP = 18, M = 40;
const keyBox = i => { const r = Math.floor((i - 1) / 5), c = (i - 1) % 5; return [M + c * (K + GAP), M + 16 + r * (K + GAP), K, K]; };
const btnBox = i => { const by = M + 16 + 3 * K + 2 * GAP + 24, bw = Math.floor((W - 2 * M - 2 * GAP) / 3); return [M + i * (bw + GAP), by, bw, 70]; };

let S = null, page = null, follow = true, shownV = -1, shownAt = 0, selKey = null, reqSeen = 0, reqId = null, busy = false;

async function api(path, body) {
  const opt = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Panel-Token': TOKEN }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  return r.json();
}
function toast(text, bad = false) {
  const t = $('toast');
  t.replaceChildren(el('span', { textContent: text }));
  t.classList.toggle('bad', bad);
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, bad ? 9000 : 4000);
}
const report = (res, ok) => {
  if (res.ok) toast(res.unchanged ? 'Already on the pad' : ok + (res.daemon === 'rejected' ? ', but the daemon rejected it' : res.daemon === 'offline' ? ' (daemon is off)' : ''), res.daemon === 'rejected');
  else toast((res.errors || ['Failed']).slice(0, 2).join(' · '), true);
};

// ---------------------------------------------------------------- pad picture
function buildHits() {
  const box = $('hits');
  for (let i = 1; i <= 18; i++) {
    const [x, y, w, h] = i <= 15 ? keyBox(i) : btnBox(i - 16), name = i <= 15 ? String(i) : `b${i - 15}`;
    const b = el('button', { className: 'hit' + (i > 15 ? ' btn' : ''), type: 'button', title: `Key ${name}` });
    b.dataset.key = name;
    b.setAttribute('aria-label', `Key ${name}`);
    Object.assign(b.style, { left: `${x / W * 100}%`, top: `${y / H * 100}%`, width: `${w / W * 100}%`, height: `${h / H * 100}%` });
    b.onclick = () => showKey(name);
    box.append(b);
  }
}
function currentPage() { return follow ? (S?.daemon?.page || S?.home) : page; }
function loadPad(force = false) {
  const p = currentPage();
  if (!p) return;
  const url = `/api/pad.png?page=${encodeURIComponent(p)}&v=${S.v}-${Date.now()}`;
  const img = new Image();
  img.onload = () => { $('pad').src = img.src; };
  img.src = url;
  shownV = S.v; shownAt = Date.now();
  if (force && selKey) showKey(selKey);
}
async function showKey(name) {
  selKey = name;
  document.querySelectorAll('.hit').forEach(h => h.classList.toggle('sel', h.dataset.key === name));
  const d = await api(`/api/keys?page=${encodeURIComponent(currentPage() || '')}`), k = d.keys?.[name], box = $('keyinfo');
  if (!k) return;
  const label = name.startsWith('b') ? `Button ${name}` : `Key ${name}`;
  const change = el('button', { type: 'button', className: 'ghost change', textContent: k.empty ? 'Put something here' : 'Change this key' });
  change.onclick = () => { const t = $('asktext'); t.value = `${label} on page ${d.page}: `; t.focus(); t.setSelectionRange(t.value.length, t.value.length); };
  if (k.empty) { box.replaceChildren(el('h3', { textContent: 'Empty' }), el('div', { className: 'k', textContent: `${label} · ${d.page}` }), change); return; }
  const dl = el('dl');
  for (const [t, v] of [['Tap', k.tap], ['Hold', k.hold], ['Shows', k.live]]) if (v) dl.append(el('dt', { textContent: t }), el('dd', { textContent: v }));
  const extra = [k.inherited ? 'From the page it inherits' : '', k.physical_only ? 'Only a real press works' : ''].filter(Boolean).join(' · ');
  box.replaceChildren(...[el('h3', { textContent: k.title }), el('div', { className: 'k', textContent: `${label} · ${d.page}` }), dl,
    k.note || extra ? el('p', { className: 'note', textContent: [k.note, extra].filter(Boolean).join(' · ') }) : null, change].filter(Boolean));
}
function drawPages() {
  const nav = $('pages'), live = S.daemon?.page, cur = currentPage();
  nav.replaceChildren(...(S.pages || []).map(p => {
    const b = el('button', { type: 'button', textContent: p, className: (p === cur ? 'sel' : '') + (p === live ? ' live' : '') });
    b.title = p === live ? 'On the pad now' : 'Show this page';
    b.onclick = () => { follow = p === live; page = p; drawPages(); loadPad(true); };
    return b;
  }));
}

// ---------------------------------------------------------------- sets and themes
const setLabel = n => n === '_previous' ? 'Previous' : n.charAt(0).toUpperCase() + n.slice(1).replace(/[-_]/g, ' ');
let setSig = '';
function drawSets() {
  const sig = JSON.stringify(S.sets) + S.v;
  if (sig === setSig) return;
  setSig = sig;
  const grid = $('sets');
  const cards = S.sets.map(s => {
    const c = el('button', { type: 'button', className: 'setcard' + (s.active ? ' on' : '') });
    c.append(el('img', { src: `/api/set.png?name=${encodeURIComponent(s.name)}&m=${s.mtime}`, alt: '', loading: 'lazy' }),
      el('div', { className: 'nm' }, el('span', { textContent: setLabel(s.name) }), s.active ? el('span', { className: 'tag', textContent: 'On' }) : null));
    c.title = s.active ? 'On the pad now' : `Put ${setLabel(s.name)} on the pad`;
    c.onclick = async () => {
      if (s.active || busy) return;
      busy = true; c.classList.add('busy');
      try { report(await api('/api/apply', { name: s.name }), `${setLabel(s.name)} is on`); } finally { busy = false; tick(); }
    };
    return c;
  });
  const add = el('div', { className: 'setcard add' });
  const plus = el('button', { type: 'button', className: 'plus', textContent: '+', title: 'Save the current pad as a set' });
  const form = el('form', {}, el('input', { name: 'n', placeholder: 'Save current as…', maxLength: 32, ariaLabel: 'Set name' }));
  plus.onclick = () => form.n.focus();
  form.onsubmit = async e => {
    e.preventDefault();
    const name = form.n.value.trim();
    if (!name) return;
    const res = await api('/api/save', { name });
    if (res.ok) { toast(`Saved as ${setLabel(res.name)}`); form.n.value = ''; tick(); } else report(res);
  };
  add.append(plus, form);
  grid.replaceChildren(...cards, add);
}
function drawThemes() {
  const box = $('themes');
  box.replaceChildren(...Object.entries(S.themes).map(([name, color]) => {
    const b = el('button', { type: 'button', className: name === S.theme ? 'sel' : '', title: `${name} look` }, el('i'), name);
    b.firstChild.style.background = color;
    b.onclick = async () => { if (name === S.theme || busy) return; busy = true; try { report(await api('/api/theme', { name }), `${name} look is on`); } finally { busy = false; tick(); } };
    return b;
  }));
  document.documentElement.style.setProperty('--accent', S.themes[S.theme] || '#4caf2a');
}

// ---------------------------------------------------------------- request box
$('ask').onsubmit = async e => {
  e.preventDefault();
  const text = $('asktext').value.trim();
  if (!text) return;
  const res = await api('/api/request', { text });
  if (!res.ok) return report(res);
  reqId = res.id; reqSeen = 0;
  $('progress').replaceChildren(); $('progress').hidden = false; $('result').hidden = true;
  tick();
};
$('stop').onclick = () => api('/api/stop', {});
$('undo').onclick = async () => { const res = await api('/api/undo', {}); report(res, 'Undone'); tick(); };

async function pollRequest() {
  const r = await api(`/api/request?since=${reqSeen}`);
  if (!r.id) return;
  if (r.id !== reqId) { reqId = r.id; reqSeen = 0; $('progress').replaceChildren(); return pollRequest(); }
  const list = $('progress');
  for (const e of r.events) list.append(el('li', { className: e.t, textContent: e.text }));
  if (r.events.length) { list.hidden = false; list.scrollTop = list.scrollHeight; }
  reqSeen += r.events.length;
  const running = r.state === 'running' || r.state === 'stopping';
  $('go').disabled = running; $('stop').hidden = !running;
  $('askstate').textContent = running ? (r.state === 'stopping' ? 'Stopping…' : 'Working…') : '';
  if (!running && r.result && $('result').dataset.id !== r.id) drawResult(r);
}
function drawResult(r) {
  const box = $('result'), x = r.result;
  box.dataset.id = r.id;
  const kids = [];
  if (x.error) kids.push(el('p', { className: 'err', textContent: x.error }));
  else if (r.state === 'stopped') kids.push(el('p', { className: 'err', textContent: 'Stopped.' }));
  for (const p of x.pages || []) {
    const fig = side => {
      const f = el('figure');
      const img = el('img', { src: `/api/request.png?id=${r.id}&side=${side}&page=${encodeURIComponent(p)}`, alt: `${side} ${p}` });
      img.onerror = () => img.replaceWith(el('div', { className: 'none muted small', textContent: 'new page' }));
      const shot = el('div', { className: 'shot' }, img);
      if (side === 'after') for (const k of x.keys?.[p] || []) {
        const [bx, by, bw, bh] = k <= 15 ? keyBox(k) : btnBox(k - 16), m = el('span', { className: 'mark' + (k > 15 ? ' btn' : '') });
        Object.assign(m.style, { left: `${bx / W * 100}%`, top: `${by / H * 100}%`, width: `${bw / W * 100}%`, height: `${bh / H * 100}%` });
        shot.append(m);
      }
      f.append(shot, el('figcaption', { textContent: `${side === 'before' ? 'Before' : 'After'} · ${p}` }));
      return f;
    };
    kids.push(el('div', { className: 'pair' }, fig('before'), fig('after')));
  }
  if (!x.error && !(x.pages || []).length) kids.push(el('p', { className: 'muted small', textContent: 'No change to the pad.' }));
  if (x.plugins_changed?.length) kids.push(el('p', { className: 'muted small', textContent: `Also changed plugins: ${x.plugins_changed.join(', ')} (Undo leaves these)` }));
  const cost = [x.seconds != null ? `${Math.round(x.seconds)} s` : '', x.cost_usd != null ? `$${x.cost_usd.toFixed(2)}` : ''].filter(Boolean).join(' · ');
  const row = el('div', { className: 'row' }, el('span', { className: 'muted small', textContent: cost }));
  if (x.undo) { const u = el('button', { type: 'button', className: 'ghost', textContent: 'Undo' }); u.onclick = async () => { const res = await api('/api/undo', {}); report(res, 'Undone'); if (res.ok) u.disabled = true; tick(); }; row.append(u); }
  kids.push(row);
  box.replaceChildren(...kids);
  box.hidden = false;
}

// ---------------------------------------------------------------- loop
async function tick() {
  try {
    S = await api('/api/state');
  } catch { $('dot').className = 'dot'; return; }
  const d = S.daemon;
  $('dot').className = 'dot' + (d ? (d.connected && !d.dry_run ? ' on' : ' mock') : '');
  $('dot').title = d ? (d.connected ? (d.dry_run ? 'mock daemon' : 'pad connected') : 'pad not connected') : 'daemon is off: showing the file';
  if (!follow && !(S.pages || []).includes(page)) follow = true;
  drawPages(); drawThemes(); drawSets();
  $('undo').hidden = !S.undo;
  if (S.undo) $('undo').title = `Undo ${S.undo.label}`;
  if (S.v !== shownV || Date.now() - shownAt > 10000) loadPad(S.v !== shownV);
  if (S.request.id) await pollRequest();
}
buildHits();
tick();
setInterval(() => { if (!document.hidden) tick(); }, 1500);
