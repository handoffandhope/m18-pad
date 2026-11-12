// M18 Stale Tabs: service worker. Tracks when each tab was last activated, and offers one long-unused
// tab at a time to the M18 pad (through the native-messaging host) as a card: Keep / Close / Later / Skip.
// Reads only tab id, url, title, pinned/audible flags. No host permissions, no content scripts.
import * as S from './stale.js';

const HOST = 'com.m18pad.tabs';
const TICK = 'tick';
const nowMs = () => (globalThis.__now ? globalThis.__now() : Date.now());

// ---- state (chrome.storage.local, one key), serialised through a promise chain -------------------
const blank = () => ({ tabs: {}, cards: null, open: null, closed: [], cfg: {}, lastError: null });
async function load() { const r = await chrome.storage.local.get('state'); return Object.assign(blank(), r.state || {}); }
const save = s => chrome.storage.local.set({ state: s });
let chain = Promise.resolve();
function locked(fn) { const p = chain.then(fn, fn); chain = p.catch(() => {}); return p; }
const cfgOf = s => ({ ...S.DEFAULTS, ...(s.cfg || {}) });

// ---- native port ---------------------------------------------------------------------------------
let port = null, nextId = 1;
const pending = new Map();

function ensurePort() {
  if (port) return port;
  const p = chrome.runtime.connectNative(HOST);
  p.onMessage.addListener(onHostMessage);
  p.onDisconnect.addListener(() => {
    if (port === p) port = null;
    for (const [, w] of pending) w.reject(new Error('host disconnected'));
    pending.clear();
  });
  port = p;
  return p;
}
const post = msg => ensurePort().postMessage(msg);
function request(msg, timeoutMs = 25000) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('host timeout')); }, timeoutMs);
    pending.set(id, { resolve: v => { clearTimeout(timer); resolve(v); }, reject: e => { clearTimeout(timer); reject(e); } });
    try { post({ ...msg, req: id }); } catch (e) { clearTimeout(timer); pending.delete(id); reject(e); }
  });
}
function onHostMessage(msg) {
  if (msg && msg.reply_to && pending.has(msg.reply_to)) { pending.get(msg.reply_to).resolve(msg); pending.delete(msg.reply_to); return; }
  if (msg && msg.type === 'card_action') locked(() => handleAction(msg.card, msg.action)).catch(e => console.warn('action failed', e));
}

// ---- favicon, scaled up to 64x64 (local _favicon endpoint: no host permission needed) -------------
async function favicon(url) {
  try {
    const u = new URL(chrome.runtime.getURL('/_favicon/'));
    u.searchParams.set('pageUrl', url); u.searchParams.set('size', '32');
    const bmp = await createImageBitmap(await (await fetch(u)).blob());
    const c = new OffscreenCanvas(64, 64);
    c.getContext('2d').drawImage(bmp, 0, 0, 64, 64);
    const bytes = new Uint8Array(await (await c.convertToBlob({ type: 'image/png' })).arrayBuffer());
    let bin = ''; for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    return btoa(bin);
  } catch { return null; }
}

// ---- the periodic pass ---------------------------------------------------------------------------
function deleteCard(id) { try { post({ type: 'delete_card', id }); } catch { /* host gone; the card expires by ttl */ } }

async function tick() {
  return locked(async () => {
    const s = await load(), t = nowMs(), cfg = cfgOf(s);
    s.tabs = S.reconcile(s.tabs, await chrome.tabs.query({}), t);
    if (s.open && (t - s.open.at > cfg.cardTtlS * 1000 || !s.tabs[s.open.tabId])) {
      deleteCard(s.open.id);                                   // unanswered or tab gone: ask again tomorrow
      if (s.tabs[s.open.tabId]) S.skip(s.tabs[s.open.tabId], t, cfg);
      s.open = null;
    }
    const id = S.pickNext(s, t, cfg);
    if (id) await offer(s, id, t, cfg);
    await save(s);
  });
}

async function offer(s, id, t, cfg) {
  const rec = s.tabs[id], cardId = `tabs-${id}`;
  let reply;
  try {
    reply = await request({
      type: 'offer', card_id: cardId, ttl_s: cfg.cardTtlS,
      tab: { id: Number(id), title: rec.title, url: rec.url, age_days: S.ageDays(rec, t), favicon_b64: await favicon(rec.url) },
    }, 30000);
  } catch (e) { reply = { result: 'failed', error: String(e && e.message || e) }; }
  if (reply.result === 'posted') { S.countCard(s, t); s.open = { id: cardId, tabId: id, at: t }; s.lastError = null; }
  else if (reply.result === 'deferred') S.defer(rec, t, reply.days || cfg.deferDays);
  else { S.retryLater(rec, t, cfg); s.lastError = reply.error || 'offer failed'; }
}

// ---- what the buttons do -------------------------------------------------------------------------
async function saveForLater(rec) {
  try {
    if (chrome.readingList && chrome.readingList.addEntry) {
      try { await chrome.readingList.addEntry({ title: rec.title || rec.url, url: rec.url, hasBeenRead: false }); return 'readingList'; }
      catch (e) { if (!/duplicate/i.test(String(e && e.message))) throw e; return 'readingList'; }
    }
    if (chrome.bookmarks && await chrome.permissions.contains({ permissions: ['bookmarks'] })) {
      const found = await chrome.bookmarks.search({ title: 'Later' });
      let folder = found.find(b => !b.url);
      if (!folder) folder = await chrome.bookmarks.create({ title: 'Later' });
      await chrome.bookmarks.create({ parentId: folder.id, title: rec.title || rec.url, url: rec.url });
      return 'bookmarks';
    }
  } catch (e) { console.warn('save for later failed', e); }
  return null;
}

async function closeTab(s, tabId, rec, t, via) {
  s.closed = [{ url: rec.url, title: rec.title, at: t, via }, ...s.closed].slice(0, 20);
  delete s.tabs[tabId];
  await chrome.tabs.remove(Number(tabId));
  post({ type: 'post_card', card: {
    id: 'tabs-undo', feed: 'tabs', title: 'Closed', lines: [short(rec.title || rec.url, 22), 'Undo?'],
    actions: [{ id: 'undo', label: 'Undo' }, { id: 'ok', label: 'OK' }], priority: 2, ttl_s: 60 } });
}
const short = (x, n) => (x.length > n ? x.slice(0, n - 1) + '…' : x);

async function handleAction(card, action) {
  const s = await load(), t = nowMs(), cfg = cfgOf(s);
  if (card === 'tabs-undo') {
    if (action === 'undo' && s.closed.length) {
      const last = s.closed[0];
      const recent = await chrome.sessions.getRecentlyClosed({ maxResults: 25 });
      const hit = recent.find(x => x.tab && x.tab.url === last.url);
      if (hit) await chrome.sessions.restore(hit.tab.sessionId);
      if (last.via === 'readingList') { try { await chrome.readingList.removeEntry({ url: last.url }); } catch { /* ignore */ } }
      s.closed.shift();
    }
    return save(s);
  }
  if (!s.open || s.open.id !== card) return;                   // not the card we are waiting on
  const tabId = s.open.tabId, rec = s.tabs[tabId];
  s.open = null;
  let alive = true;
  try { await chrome.tabs.get(Number(tabId)); } catch { alive = false; }
  if (rec && alive) {
    if (action === 'keep') S.keep(rec, t, cfg);
    else if (action === 'close') await closeTab(s, tabId, rec, t, 'close');
    else if (action === 'later') {
      const via = await saveForLater(rec);
      if (via) await closeTab(s, tabId, rec, t, via);          // never close a page we failed to save
      else { S.skip(rec, t, cfg); s.lastError = 'Later failed: no reading list or bookmarks'; }
    } else S.skip(rec, t, cfg);                                // skip, or anything unknown
  }
  await save(s);
}

// ---- events --------------------------------------------------------------------------------------
async function markActive(tabId) {
  return locked(async () => {
    const s = await load(), rec = s.tabs[tabId];
    if (rec) { rec.last = nowMs(); rec.until = 0; }
    if (s.open && String(s.open.tabId) === String(tabId)) { deleteCard(s.open.id); s.open = null; }   // he is using it
    await save(s);
  });
}
chrome.tabs.onActivated.addListener(i => markActive(i.tabId));
chrome.windows.onFocusChanged.addListener(async w => {
  if (w === chrome.windows.WINDOW_ID_NONE) return;
  const [tab] = await chrome.tabs.query({ active: true, windowId: w });
  if (tab && S.eligibleTab(tab)) markActive(tab.id);
});
chrome.tabs.onRemoved.addListener(tabId => locked(async () => {
  const s = await load();
  delete s.tabs[tabId];
  if (s.open && String(s.open.tabId) === String(tabId)) { deleteCard(s.open.id); s.open = null; }
  await save(s);
}));

async function startup() {
  await locked(async () => {                                   // tab ids do not survive a browser restart
    const s = await load();
    if (s.open) { try { post({ type: 'delete_card', id: s.open.id }); } catch { /* ignore */ } s.open = null; await save(s); }
  });
  chrome.alarms.create(TICK, { periodInMinutes: 1 });
  return tick();
}
chrome.runtime.onStartup.addListener(() => startup().catch(console.warn));
chrome.runtime.onInstalled.addListener(() => startup().catch(console.warn));
chrome.alarms.onAlarm.addListener(a => { if (a.name === TICK) tick().catch(console.warn); });

globalThis.__m18 = { tick, handleAction: (c, a) => locked(() => handleAction(c, a)), load };   // test hook
