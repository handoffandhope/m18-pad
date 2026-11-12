// Pure staleness logic for the M18 stale-tabs extension. No chrome.* calls, so node can test it.
// A "tab" is the shape chrome.tabs.query returns; a "record" is what we keep per tab.

export const DAY = 86400000;
export const DEFAULTS = {
  staleDays: 3,        // not activated for this long -> stale
  keepDays: 14,        // Keep: no more asking for this long
  skipHours: 24,       // Skip: ask again tomorrow
  deferDays: 7,        // "related to work in progress": don't nag for this long
  retryMinutes: 10,    // an offer that failed (daemon down): try again later
  maxCardsPerDay: 5,
  cardTtlS: 12 * 3600, // card lifetime on the pad
};

export function dayKey(now) {
  const d = new Date(now);
  return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;
}

// Only ordinary web tabs are ever tracked; never incognito, never chrome:// or extension pages.
export function eligibleTab(t) {
  if (!t || t.incognito || typeof t.id !== 'number' || t.id < 0) return false;
  return /^https?:\/\//i.test(t.url || '');
}

export function newRecord(tab, now) {
  const last = Number.isFinite(tab.lastAccessed) ? Math.min(tab.lastAccessed, now) : now;
  return { first: last, last, url: tab.url, title: tab.title || '', audible: !!tab.audible, pinned: !!tab.pinned, until: 0 };
}

// Merge the live tab list into the records. Records of closed tabs are dropped. After a browser
// restart tab ids change, so an unmatched live tab adopts an orphan record with the same url.
export function reconcile(records, liveTabs, now) {
  const next = {};
  const live = liveTabs.filter(eligibleTab);
  const liveIds = new Set(live.map(t => String(t.id)));
  const orphans = Object.entries(records).filter(([id]) => !liveIds.has(id));
  for (const t of live) {
    const id = String(t.id);
    let rec = records[id];
    if (!rec) {
      const i = orphans.findIndex(([, r]) => r.url === t.url);
      if (i >= 0) rec = orphans.splice(i, 1)[0][1];
    }
    if (!rec) rec = newRecord(t, now);
    rec.url = t.url; rec.title = t.title || rec.title; rec.pinned = !!t.pinned; rec.audible = !!t.audible;
    if (rec.audible) rec.last = now; // playing sound counts as in use
    next[id] = rec;
  }
  return next;
}

export function isStale(rec, now, cfg = DEFAULTS) {
  if (rec.pinned || rec.audible) return false;
  if (now < (rec.until || 0)) return false;
  return now - rec.last >= cfg.staleDays * DAY;
}

// state: {tabs, cards: {day, n}, open}. Returns the tab id to offer next, or null.
export function pickNext(state, now, cfg = DEFAULTS) {
  if (state.open) return null;                                              // one open card at a time
  const used = state.cards && state.cards.day === dayKey(now) ? state.cards.n : 0;
  if (used >= cfg.maxCardsPerDay) return null;                              // at most 5 new cards a day
  const stale = Object.entries(state.tabs).filter(([, r]) => isStale(r, now, cfg));
  stale.sort((a, b) => a[1].last - b[1].last);                              // oldest first
  return stale.length ? stale[0][0] : null;
}

export function countCard(state, now) {
  const day = dayKey(now);
  state.cards = state.cards && state.cards.day === day ? state.cards : { day, n: 0 };
  state.cards.n++;
}

export const keep = (rec, now, cfg = DEFAULTS) => { rec.until = now + cfg.keepDays * DAY; };
export const skip = (rec, now, cfg = DEFAULTS) => { rec.until = now + cfg.skipHours * 3600000; };
export const defer = (rec, now, days) => { rec.until = now + days * DAY; };
export const retryLater = (rec, now, cfg = DEFAULTS) => { rec.until = now + cfg.retryMinutes * 60000; };

export function ageDays(rec, now) { return Math.max(0, Math.floor((now - rec.last) / DAY)); }
export function ageText(days) { return `${days} ${days === 1 ? 'day' : 'days'}`; }
