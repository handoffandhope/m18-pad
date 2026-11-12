// End-to-end check: extension logic (background.js + stale.js, run under node with a fake `chrome`)
// <-> the real native host process (Python, real 4-byte framing over pipes) <-> the daemon stub on :8732.
//   node test_e2e.mjs [path-to-python]
// Needs nothing from Chrome, the pad or the network (Decisions sorting is switched off for this run).
import { spawn } from 'node:child_process';
import assert from 'node:assert/strict';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const PY = process.argv[2] || process.env.M18_PYTHON || 'python';
const PORT = 8732, API = `http://127.0.0.1:${PORT}`;
const DAY = 86400000, T0 = Date.UTC(2026, 10, 11, 12, 0, 0);
let clock = T0; globalThis.__now = () => clock;

// ---- the stub -------------------------------------------------------------------------------------
const stub = spawn(PY, ['-I', path.join(here, 'test_stub.py'), String(PORT)], { stdio: ['ignore', 'pipe', 'inherit'] });
await new Promise(r => stub.stdout.once('data', r));
const j = (m, p, b) => fetch(API + p, { method: m, headers: { 'X-M18': '1', 'content-type': 'application/json' }, body: b ? JSON.stringify(b) : undefined }).then(r => r.json());
const cards = async () => (await j('GET', '/_test/cards')).cards;
const act = (card, action) => j('POST', '/_test/action', { card, action });
const waitFor = async (fn, what, ms = 6000) => { const end = Date.now() + ms; for (;;) { const v = await fn(); if (v) return v; if (Date.now() > end) throw new Error('timeout: ' + what); await new Promise(r => setTimeout(r, 50)); } };

// ---- fake chrome, with connectNative wired to the real host over pipes --------------------------------
const listeners = () => { const a = []; return { addListener: f => a.push(f), fire: (...x) => a.forEach(f => f(...x)) }; };
const store = {}, closedTabs = [], restored = [], reading = [], sessionsClosed = [];
let tabs = [];
let hostProc = null;
const chrome = globalThis.chrome = {
  storage: { local: { get: async k => ({ [k]: store[k] && JSON.parse(store[k]) }), set: async o => { for (const k in o) store[k] = JSON.stringify(o[k]); } } },
  tabs: {
    query: async () => tabs.map(t => ({ ...t })),
    get: async id => { const t = tabs.find(x => x.id === id); if (!t) throw new Error('no tab'); return t; },
    remove: async id => { const t = tabs.find(x => x.id === id); tabs = tabs.filter(x => x.id !== id); closedTabs.push(id); sessionsClosed.unshift({ tab: { url: t.url, sessionId: 's' + id } }); },
    onActivated: listeners(), onRemoved: listeners(),
  },
  windows: { WINDOW_ID_NONE: -1, onFocusChanged: listeners() },
  alarms: { create() {}, onAlarm: listeners() },
  permissions: { contains: async () => false },
  readingList: { addEntry: async e => { reading.push(e); }, removeEntry: async () => {} },
  sessions: { getRecentlyClosed: async () => sessionsClosed, restore: async id => { restored.push(id); } },
  runtime: {
    onStartup: listeners(), onInstalled: listeners(), getURL: p => 'chrome-extension://x' + p,
    connectNative(name) {
      assert.equal(name, 'com.m18pad.tabs');
      hostProc = spawn(PY, ['-I', path.join(here, 'host', 'm18_tabs_host.py')], { env: { ...process.env, M18_API_PORT: String(PORT), M18_TABS_DECISIONS: '0' }, stdio: ['pipe', 'pipe', 'inherit'] });
      const onMessage = listeners(), onDisconnect = listeners();
      let buf = Buffer.alloc(0);
      hostProc.stdout.on('data', d => {                      // native messaging framing: u32 LE length + JSON
        buf = Buffer.concat([buf, d]);
        while (buf.length >= 4 && buf.length >= 4 + buf.readUInt32LE(0)) {
          const n = buf.readUInt32LE(0); const msg = JSON.parse(buf.subarray(4, 4 + n).toString('utf8')); buf = buf.subarray(4 + n);
          onMessage.fire(msg);
        }
      });
      hostProc.on('exit', () => onDisconnect.fire());
      return { onMessage, onDisconnect, postMessage(m) { const b = Buffer.from(JSON.stringify(m), 'utf8'); const h = Buffer.alloc(4); h.writeUInt32LE(b.length); hostProc.stdin.write(Buffer.concat([h, b])); } };
    },
  },
};

const tab = (id, days, extra = {}) => ({ id, url: `https://site${id}.example.com/page?q=secret`, title: `Page ${id} with a rather long title`, lastAccessed: T0 - days * DAY, ...extra });
tabs = [
  tab(1, 9), tab(7, 12), tab(8, 5), tab(9, 6), tab(10, 7), tab(11, 8),     // 6 stale web tabs, oldest first: 7,1,11,10,9,8
  tab(2, 30, { pinned: true }), tab(3, 30, { audible: true }), tab(4, 30, { incognito: true }),
  tab(5, 30, { url: 'chrome://extensions' }), tab(6, 1),                  // never offered: pinned, audible, incognito, chrome://, fresh
];

const bg = await import(pathToFileURL(path.join(here, 'extension', 'background.js')).href);
const m18 = globalThis.__m18;
const state = () => m18.load();
const next = async (expectId) => {                                        // one tick, then wait for the card
  await m18.tick();
  const c = await waitFor(async () => (await cards())[`tabs-${expectId}`], `card for tab ${expectId}`);
  return c;
};

let step = 0; const ok = s => console.log(`ok ${++step} ${s}`);

// 1. oldest stale tab first; card has the contract fields; excluded tabs never appear; one open card at a time
let c = await next(7);
assert.deepEqual(Object.keys(c).filter(k => k !== 'image_png_b64').sort(), ['actions', 'feed', 'id', 'lines', 'priority', 'title', 'ttl_s']);
assert.equal(c.feed, 'tabs'); assert.equal(c.title, 'example.com'); assert.equal(c.lines[1], '12 days');
assert.deepEqual(c.actions.map(a => a.id), ['keep', 'close', 'later', 'skip']);
assert.ok(!JSON.stringify(c).includes('secret'), 'query string must not reach the card');
await m18.tick(); assert.equal(Object.keys(await cards()).length, 1);
ok('oldest first, contract fields, one open card at a time, excluded tabs skipped');

// 2. Keep -> tab stays, no card for 14 days
await act('tabs-7', 'keep');
await waitFor(async () => (await state()).open === null, 'open cleared');
assert.ok(tabs.some(t => t.id === 7));
assert.equal((await state()).tabs[7].until, T0 + 14 * DAY);
ok('Keep resets the clock for 14 days');

// 3. Close -> tab removed, undo card shown, Undo restores it
c = await next(1); await act('tabs-1', 'close');
await waitFor(async () => closedTabs.includes(1), 'tab 1 closed');
await waitFor(async () => (await cards())['tabs-undo'], 'undo card');
await act('tabs-undo', 'undo');
await waitFor(async () => restored.length === 1, 'restore');
assert.deepEqual(restored, ['s1']);
ok('Close closes the tab, Undo restores it through chrome.sessions');

// 4. Later -> reading list entry, then closed
c = await next(11); await act('tabs-11', 'later');
await waitFor(async () => closedTabs.includes(11), 'tab 11 closed');
assert.equal(reading.length, 1); assert.equal(reading[0].url, 'https://site11.example.com/page?q=secret');
ok('Later saves to the reading list, then closes');

// 5. Skip -> stays open, asked again tomorrow; cap of 5 cards a day
c = await next(10); await act('tabs-10', 'skip');
await waitFor(async () => (await state()).open === null, 'open cleared');
assert.ok(tabs.some(t => t.id === 10));
c = await next(9);                                                           // 5th card of the day
await act('tabs-9', 'skip'); await waitFor(async () => (await state()).open === null, 'open cleared');
await m18.tick(); await new Promise(r => setTimeout(r, 300));
assert.deepEqual(Object.keys(await cards()).filter(k => k !== 'tabs-undo'), [], 'cap: no 6th card today');
ok('Skip leaves the tab; at most 5 new cards a day');

// 6. Next day the skipped tabs come back, oldest first (10 was 7d, 9 was 6d, 8 was 5d)
clock += 25 * 3600000;
c = await next(10);
ok('next day: skipped tab asked again, oldest first');

// 7. Using the tab (activating it) withdraws its open card
chrome.tabs.onActivated.fire({ tabId: 10 });
await waitFor(async () => !(await cards())['tabs-10'], 'card withdrawn');
assert.equal((await state()).open, null);
ok('activating the tab withdraws its card');

hostProc.stdin.end(); stub.kill();
console.log('ALL PASSED');
process.exit(0);
