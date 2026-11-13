/* Hiring Badge Finder - background service worker (Manifest V3).
 *
 * - Opens each search in a background tab inside a collapsed tab group.
 * - Does every wait for the page script (hidden tabs slow down their own timers).
 * - Sends data to the dashboard server for the page script (avoids CORS problems).
 * Nothing here talks to LinkedIn except by opening the search tab.
 */
importScripts('config.js');   // HBF_CONFIG: location ID table

const GROUP_TITLE = 'Hiring Badge Finder';
const DEFAULT_SERVER = 'http://localhost:8000';
const SEARCH_PREFIX = 'https://www.linkedin.com/search/results/people/';
const COMPANY_SEARCH = 'https://www.linkedin.com/search/results/companies/';
const COMPANY_PREFIX = 'https://www.linkedin.com/company/';
const API_TIMEOUT_MS = 25000;
const MAX_SLEEP_MS = 60000;

// ---------------------------------------------------------------- storage
/** The Options-page address, or the default. Only a fallback: a search launched from a dashboard
 *  talks to that dashboard's own address (see dashboardOrigin), whatever port it runs on. */
async function getServerUrl() {
  const { serverUrl } = await chrome.storage.sync.get('serverUrl');
  return (serverUrl || DEFAULT_SERVER).replace(/\/+$/, '');
}

/** The launching dashboard's origin when it is a local dashboard page or this extension's own
 *  deployed dashboard (DEFAULT_SERVER, set when downloaded from it), else ''. */
function dashboardOrigin(tab) {
  try {
    const o = new URL(tab.url).origin;
    if (DEFAULT_SERVER.startsWith('https://') && o === new URL(DEFAULT_SERVER).origin) return o;
    return /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(o) ? o : '';
  } catch (e) { return ''; }
}

/** Server address for a search tab: the dashboard it was launched from, else the fallback. */
async function serverForTab(tabId) {
  const jobs = await getJobs();
  const j = jobs[tabId];
  return (j && j.serverUrl) || getServerUrl();
}

/** Tell the dashboard page (if open) that the server could not be reached, naming the address. */
async function reportUnreachable(dashboardTabId, base, path, error) {
  if (dashboardTabId == null) return;
  const message = `Could not reach the dashboard server at ${base} (${path}: ${error}). ` +
    'Is it still running? Start it again and launch the search again.';
  try { await chrome.tabs.sendMessage(dashboardTabId, { type: 'server-error', message }); } catch (e) { /* page closed */ }
}

async function getBrowserId() {
  const { browserId } = await chrome.storage.local.get('browserId');
  if (browserId) return browserId;
  const id = crypto.randomUUID();
  await chrome.storage.local.set({ browserId: id });
  return id;
}

async function getJobs() {
  const { jobs } = await chrome.storage.session.get('jobs');
  return jobs || {};
}

// Read-change-write of the tab->job map is done one at a time, so two searches
// started together can never overwrite each other's entry.
let jobsLock = Promise.resolve();
function updateJobs(change) {
  const run = jobsLock.then(async () => {
    const jobs = await getJobs();
    change(jobs);
    await chrome.storage.session.set({ jobs });
  });
  jobsLock = run.catch(() => {});
  return run;
}

// -------------------------------------------------------------- server calls
// While a hosted server restarts (e.g. a Render deploy, ~30-90 s) calls fail with no answer or with the
// host's 502/503/504 page. Those are retried for up to ~2 minutes so a running search survives it.
// A call that timed out after reaching the server is NOT retried (it may have been saved already).
const RETRY_STATUSES = [502, 503, 504];
const RETRY_WAITS_MS = [3000, 6000, 10000, 15000, 20000, 25000, 25000];   // each < 30 s: an idle worker may be stopped

async function callServer(path, method, body, serverUrl) {
  let r = await callServerOnce(path, method, body, serverUrl);
  for (const wait of RETRY_WAITS_MS) {
    if (!(r.unreachable || RETRY_STATUSES.includes(r.status))) break;
    await new Promise((done) => setTimeout(done, wait));
    r = await callServerOnce(path, method, body, serverUrl);
  }
  return r;
}

async function callServerOnce(path, method, body, serverUrl) {
  const base = (serverUrl || await getServerUrl()).replace(/\/+$/, '');
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), API_TIMEOUT_MS);
  try {
    const res = await fetch(base + path, {
      method: method || 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: method === 'GET' ? undefined : JSON.stringify(body || {}),
      signal: ctl.signal,
    });
    let json = null;
    try { json = await res.json(); } catch (e) { /* no body */ }
    return { ok: res.ok, status: res.status, json, url: base };
  } catch (e) {
    // No answer at all (connection refused / reset): safe to retry. A timeout (abort) is not.
    return { ok: false, status: 0, error: String(e && e.message || e), url: base,
             unreachable: !ctl.signal.aborted };
  } finally {
    clearTimeout(timer);
  }
}

// ------------------------------------------------------------- tab handling
async function putInGroup(tabId, windowId) {
  try {
    const groups = await chrome.tabGroups.query({ title: GROUP_TITLE, windowId });
    let groupId;
    if (groups.length) {
      groupId = await chrome.tabs.group({ tabIds: [tabId], groupId: groups[0].id });
    } else {
      groupId = await chrome.tabs.group({ tabIds: [tabId], createProperties: { windowId } });
    }
    await chrome.tabGroups.update(groupId, { title: GROUP_TITLE, color: 'purple', collapsed: true });
    return groupId;
  } catch (e) {
    return null; // grouping is cosmetic; the search still runs
  }
}

// ------------------------------------------------------- location IDs
// LinkedIn filters a search by its own location ID (geoUrn). IDs come from the fixed table in
// config.js, or were taught once by the person: they picked the place in LinkedIn's own Locations
// filter and this worker read the ID from that tab's address (see teachPlace). LinkedIn's location
// lookup is never called.
const normPlace = (s) => String(s || '').toLowerCase().replace(/\s+/g, ' ').trim();
const cityOf = (s) => normPlace(String(s || '').split(',')[0]);

async function getLearned() {
  const { geoLearned } = await chrome.storage.local.get('geoLearned');
  return geoLearned || {};
}

/** { ids, missing }: one ID per place, and the places that have none yet.
 *  `known` = places already set up on the dashboard server (shared by everyone using it).
 *  A place without an ID is never widened to the whole country any more: that read pages of
 *  people from other cities (e.g. Mumbai searched as India: 119 read, 1 saved). */
function inIndiaOrNoCountry(p) {           // "Hyderabad, Pakistan" must not get Hyderabad, India's number
  const parts = String(p || '').split(',');
  return parts.length === 1 || parts[parts.length - 1].trim().toLowerCase() === 'india';
}
async function resolvePlaces(places, known) {
  const G = (self.HBF_CONFIG && self.HBF_CONFIG.geo) || {};
  const table = G.ids || {};
  const learned = await getLearned();
  const shared = {};
  Object.entries(known || {}).forEach(([k, v]) => { if (/^\d+$/.test(String(v))) shared[normPlace(k)] = String(v); });
  const ids = [], missing = [];
  for (const p of places || []) {
    const id = learned[normPlace(p)] || shared[normPlace(p)] || table[normPlace(p)] || (inIndiaOrNoCountry(p) ? table[cityOf(p)] : '');
    if (id) ids.push(String(id)); else missing.push(String(p));
  }
  return { ids, missing };
}

/** The single location ID in a LinkedIn search address, or '' (none, or more than one). */
function geoIdFromUrl(href) {
  try {
    const raw = new URL(href).searchParams.get('geoUrn');
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) && list.length === 1 && /^\d+$/.test(String(list[0])) ? String(list[0]) : '';
  } catch (e) { return ''; }
}

// ------------------------------------------- automatic set-up of unknown places (one hidden tab)
// The plain people search the set-up tab returns to before every place, so places never stack.
const SETUP_BASE_URL = SEARCH_PREFIX + '?origin=GLOBAL_SEARCH_HEADER';
const MAX_PLACES = 10;
const setupWaiters = new Map();         // set-up tab id -> resolve(result from the page)

/** Waits for the page's result. `ms` only limits how long the page may stay silent: once it has
 *  picked up its task (started) it gets SETUP_WORK_MS, so a working page is never reloaded. */
const SETUP_WORK_MS = 180000;
function waitSetupResult(tabId, ms) {
  return new Promise((resolve) => {
    const give = (silent, reason) => { setupWaiters.delete(tabId); resolve({ ok: false, silent, reason }); };
    let timer = setTimeout(() => give(true, 'LinkedIn did not answer in time'), ms);
    const w = (r) => { clearTimeout(timer); setupWaiters.delete(tabId); resolve(r); };
    w.started = () => { clearTimeout(timer); timer = setTimeout(() => give(false, 'the set-up on LinkedIn took too long'), SETUP_WORK_MS); };
    setupWaiters.set(tabId, w);
  });
}

/** Resolve `places` one at a time in one hidden LinkedIn tab; tell the dashboard about each step.
 *  Stops at the first place that fails (and keeps the tab when LinkedIn shows a block page). */
async function resolvePlacesOnLinkedIn(places, senderTab) {
  places = (places || []).map((p) => String(p || '').trim()).filter(Boolean);
  if (places.length > MAX_PLACES) return { ok: false, error: 'At most ' + MAX_PLACES + ' places per search.' };
  const dash = senderTab ? senderTab.id : null;
  const notify = (m) => { if (dash != null) chrome.tabs.sendMessage(dash, Object.assign({ type: 'resolve-progress' }, m)).catch(() => {}); };
  let tab = null;
  const done = [];
  try {
    for (let i = 0; i < places.length; i++) {
      const place = places[i];
      notify({ place, index: i + 1, total: places.length, status: 'setting-up' });
      if (i > 0) await new Promise((r) => setTimeout(r, 4000 + Math.random() * 4000));   // 4-8 s between places
      const { setupTabs } = await chrome.storage.session.get('setupTabs');
      const map = setupTabs || {};
      if (!tab) {
        const createProps = { url: 'about:blank', active: false };
        if (senderTab && senderTab.windowId != null) createProps.windowId = senderTab.windowId;
        tab = await chrome.tabs.create(createProps);
        await putInGroup(tab.id, tab.windowId);
      }
      map[tab.id] = { place };
      await chrome.storage.session.set({ setupTabs: map });
      let waiting = waitSetupResult(tab.id, 25000);
      await chrome.tabs.update(tab.id, { url: SETUP_BASE_URL });          // plain search: nothing stacked
      let r = await waiting;
      if (r.silent) {                                                     // the page never answered: load it once more
        waiting = waitSetupResult(tab.id, 65000);
        await chrome.tabs.update(tab.id, { url: SETUP_BASE_URL });
        r = await waiting;
      }
      if (!r.ok) {
        notify({ place, index: i + 1, total: places.length, status: 'failed', reason: r.reason || 'unknown problem',
                 offered: r.offered || [], blocked: !!r.blocked });
        if (r.blocked) { await showTab(tab.id); tab = null; }            // keep it open for the person to read
        return { ok: false, place, reason: r.reason, offered: r.offered || [], blocked: !!r.blocked, done };
      }
      const learned = await getLearned();
      learned[normPlace(place)] = r.id;
      await chrome.storage.local.set({ geoLearned: learned });
      done.push({ place, id: r.id, label: r.label });
      notify({ place, index: i + 1, total: places.length, status: 'ready', id: r.id, label: r.label });
    }
    return { ok: true, done };
  } finally {
    const { setupTabs } = await chrome.storage.session.get('setupTabs');
    if (dash != null && tab && setupTabs && setupTabs[tab.id] && setupTabs[tab.id].front) {
      chrome.tabs.update(dash, { active: true }).catch(() => {});       // back to the dashboard
    }
    if (tab) {
      if (setupTabs) { delete setupTabs[tab.id]; await chrome.storage.session.set({ setupTabs }); }
      chrome.tabs.remove(tab.id).catch(() => {});
    }
  }
}

/** Open a LinkedIn people search in front so the person can pick `place` in the Locations filter
 *  (the manual fallback for a place the automatic set-up failed on). */
async function teachPlace(place, senderTab, problem) {
  place = String(place || '').trim();
  if (!place) return { ok: false, error: 'No place given.' };
  const city = String(place.split(',')[0]).trim();
  const url = SEARCH_PREFIX + '?keywords=' + encodeURIComponent(city) + '&origin=GLOBAL_SEARCH_HEADER';
  const createProps = { url, active: true };
  if (senderTab && senderTab.windowId != null) createProps.windowId = senderTab.windowId;
  const tab = await chrome.tabs.create(createProps);
  const { teachTabs } = await chrome.storage.session.get('teachTabs');
  const next = teachTabs || {};
  next[tab.id] = { place, dashboardTabId: senderTab ? senderTab.id : null, startUrl: url, problem: problem || '' };
  await chrome.storage.session.set({ teachTabs: next });
  return { ok: true, tabId: tab.id };
}

// Watch only the tabs opened by teachPlace: when the address gets exactly one location ID
// (the person picked a place and pressed Show results), save it for that place.
chrome.tabs.onUpdated.addListener(async (tabId, changeInfo) => {
  if (!changeInfo.url || !changeInfo.url.startsWith(SEARCH_PREFIX)) return;
  const id = geoIdFromUrl(changeInfo.url);
  if (id) await learnPlace(tabId, id, 'picked');
});

/** Save `id` for the place a set-up tab was opened for, tell the dashboard, close the tab.
 *  how: 'picked' (the person chose it in LinkedIn's filter) or 'found' (read from the page). */
async function learnPlace(tabId, id, how) {
  const { teachTabs } = await chrome.storage.session.get('teachTabs');
  const t = teachTabs && teachTabs[tabId];
  if (!t || !/^\d+$/.test(String(id))) return false;
  const learned = await getLearned();
  learned[normPlace(t.place)] = id;
  await chrome.storage.local.set({ geoLearned: learned });
  delete teachTabs[tabId];
  await chrome.storage.session.set({ teachTabs });
  if (t.dashboardTabId != null) {
    try { await chrome.tabs.sendMessage(t.dashboardTabId, { type: 'geo-learned', place: t.place, id }); } catch (e) { /* dashboard closed */ }
    // Done with LinkedIn: bring the dashboard back to the front and close the set-up tab.
    try { await chrome.tabs.update(t.dashboardTabId, { active: true }); } catch (e) { /* dashboard closed */ }
  }
  setTimeout(() => { chrome.tabs.remove(tabId).catch(() => {}); }, how === 'found' ? 1500 : 600);
  return true;
}

async function openSearch(msg, senderTab) {
  let url = String(msg.url || '');
  // Every place needs its LinkedIn location ID, so LinkedIn itself filters by the chosen places.
  // Any place without one: open nothing (no reads used) and say which places need setting up.
  const base = String(msg.geoUrlBase || '');
  let geoIds = null;
  if (base.startsWith(SEARCH_PREFIX) && Array.isArray(msg.locations) && msg.locations.length) {
    const geo = await resolvePlaces(msg.locations, msg.known);
    if (geo.missing.length) {
      return { ok: false, needPlaces: geo.missing,
               error: 'LinkedIn location not set up yet for: ' + geo.missing.join(', ') };
    }
    geoIds = geo.ids;
    url = base + '&geoUrn=' + encodeURIComponent(JSON.stringify([...new Set(geoIds)]));
  }
  if (!url.startsWith(SEARCH_PREFIX)) {
    return { ok: false, error: 'Only LinkedIn people-search addresses can be opened.' };
  }
  if (!msg.jobId) return { ok: false, error: 'Missing search id.' };

  // A fast page can ask "which search am I?" before the line below has run, so
  // getJob answers "pending" for a few seconds after any search is opened.
  await chrome.storage.session.set({ lastOpenAt: Date.now() });

  const server = (senderTab && dashboardOrigin(senderTab)) || await getServerUrl();
  const createProps = { url, active: false };
  if (senderTab && senderTab.windowId != null) createProps.windowId = senderTab.windowId;
  const tab = await chrome.tabs.create(createProps);   // active:false - focus never moves
  await updateJobs((jobs) => {
    jobs[tab.id] = { jobId: msg.jobId, groupId: null, startedAt: Date.now(),
                     dashboardTabId: senderTab ? senderTab.id : null,
                     activelyHiring: url.includes('activelyHiringForJobTitles=') ,
                     serverUrl: server };
  });
  await putInGroup(tab.id, tab.windowId);

  // Lead details (Company, Industry, Size, Website) run AT THE SAME TIME as the search, in
  // HBF_CONFIG.pacing.detailTabs hidden tabs of the same group (1.42.0: 4, chosen by the user for
  // speed). The server hands each person to ONE tab. Every tab keeps its own random 4-8 s pause
  // before every page; a LinkedIn notice in any tab stops the whole run.
  if ((HBF_CONFIG.fastDetails || {}).on) {
    // Fast: one helper tab reads LinkedIn's profile data (see startFastDetails).
    try {
      const dprops = { url: HBF_CONFIG.fastDetails.homeUrl, active: false };
      if (tab.windowId != null) dprops.windowId = tab.windowId;
      const dtab = await chrome.tabs.create(dprops);
      await updateJobs((jobs) => {
        jobs[dtab.id] = { jobId: msg.jobId, groupId: null, startedAt: Date.now(), fast: true,
                          dashboardTabId: senderTab ? senderTab.id : null, serverUrl: server };
      });
      await putInGroup(dtab.id, dtab.windowId);
      await waitTabLoaded(dtab.id, 15000);
      startFastDetails(dtab.id);
    } catch (e) { /* no details tab: the search still runs */ }
  }
  for (let k = 0; k < ((HBF_CONFIG.fastDetails || {}).on ? 0 : (HBF_CONFIG.pacing.detailTabs || 1)); k++) {
    try {
      const dprops = { url: 'about:blank', active: false };
      if (tab.windowId != null) dprops.windowId = tab.windowId;
      const dtab = await chrome.tabs.create(dprops);
      await updateJobs((jobs) => {
        jobs[dtab.id] = { jobId: msg.jobId, groupId: null, startedAt: Date.now(), details: true,
                          dashboardTabId: senderTab ? senderTab.id : null, serverUrl: server };
      });
      await putInGroup(dtab.id, dtab.windowId);
      startCompanies(dtab.id).catch(() => closeTabLater(dtab.id, 3000));
    } catch (e) { /* one tab fewer: the others and the search still run */ }
  }

  // One ID per place turns the server's text filter off; [] keeps it on.
  const g = await callServer('/api/search/geo', 'POST', { job_id: msg.jobId, geo_ids: geoIds || [], widened: false }, server);
  if (!g.ok && g.status === 0) await reportUnreachable(senderTab && senderTab.id, server, '/api/search/geo', g.error);
  return { ok: true, tabId: tab.id };
}

/** Resolves when the tab has finished loading (or after `ms`, whatever comes first). */
async function waitTabLoaded(tabId, ms) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    try { if ((await chrome.tabs.get(tabId)).status === 'complete') return true; } catch (e) { return false; }
    await new Promise((r) => setTimeout(r, 250));
  }
  return false;
}

/** Safety pause (with progress messages), then load the next results page in the same tab. */
async function turnPage(tabId, jobId, page, url, waitMs) {
  const wait = Math.max(0, Math.min(waitMs, MAX_SLEEP_MS));
  const server = await serverForTab(tabId);
  // The Stop check runs DURING the pause (a far server takes ~1 s to answer), not after it.
  const status = callServer('/api/status?job_id=' + encodeURIComponent(jobId), 'GET', null, server);
  for (let left = wait; left > 0; left -= 2000) {
    callServer('/api/heartbeat', 'POST', { job_id: jobId, message: `page ${page}: waiting ${Math.ceil(left / 1000)}s before the next page` }, server);
    await new Promise((r) => setTimeout(r, Math.min(2000, left)));
  }
  const jobs = await getJobs();
  if (!jobs[tabId] || jobs[tabId].jobId !== jobId) return;          // stopped or closed meanwhile
  const st = await status;
  if (st.ok && st.json && st.json.status !== 'running') return;     // Stop was pressed during the pause
  callServer('/api/heartbeat', 'POST', { job_id: jobId, message: `page ${page + 1}: loading` }, server);
  try { await chrome.tabs.update(tabId, { url }); } catch (e) { /* tab closed */ }
}

// ------------------------------------------------- company details (after a search)
// After a search, the same hidden tab fills Company Industry / Company Size. The server hands out
// one task at a time:
//   a company NAME  -> LinkedIn company search -> the ONE exact-name result -> its About page
//   a PERSON        -> their profile -> the link of their CURRENT company -> that About page
// Before every page: the random 4-8 s pause, and the server counts the page (it refuses past its
// per-run and per-day limits). Any LinkedIn notice (limit, sign-in, check) stops everything.
const PROFILE_PREFIX = 'https://www.linkedin.com/in/';
const DETAIL_ANSWER_MS = 60000;      // a page that never answers (e.g. redirected away) ends the details

function companyBeat(j, message) {
  return callServer('/api/heartbeat', 'POST', { job_id: j.jobId, message }, j.serverUrl);
}
const coSet = (tabId, change) => updateJobs((jobs) => { if (jobs[tabId] && jobs[tabId].co) Object.assign(jobs[tabId].co, change); });

async function startCompanies(tabId) {
  const j = (await getJobs())[tabId];
  if (!j) return closeTabLater(tabId, 3000);
  await updateJobs((jobs) => { if (jobs[tabId]) jobs[tabId].co = { task: null, step: '', name: '', slug: '', byLink: false, found: 0, tasks: 0, n: 0 }; });
  return detailsNext(tabId);
}

async function detailsFinish(tabId, why) {
  const jobs = await getJobs();
  const j = jobs[tabId];
  let others = 0;
  if (j) {
    for (const [id, o] of Object.entries(jobs)) {
      if (Number(id) !== tabId && o.details && o.jobId === j.jobId && o.co) others += 1;
    }
  }
  await updateJobs((all) => { if (all[tabId]) all[tabId].co = null; });
  // Only the LAST company tab says "done" (the dashboard's Export button waits for it).
  if (j && !others) await companyBeat(j, 'Lead details: done' + (why ? ` (${why})` : ''));
  closeTabLater(tabId, 3000);
}

async function detailsNext(tabId) {
  const j = (await getJobs())[tabId];
  if (!j || !j.co) return;
  const st = await callServer('/api/status?job_id=' + encodeURIComponent(j.jobId), 'GET', null, j.serverUrl);
  const status = st.ok && st.json ? st.json.status : '';
  if (status === 'error' || status === 'stopped') return detailsFinish(tabId, 'the search was stopped');
  const r = await callServer('/api/details/next?job_id=' + encodeURIComponent(j.jobId), 'GET', null, j.serverUrl);
  const task = r.ok && r.json ? r.json.task : null;
  if (!task && r.ok && status === 'running' && (j.co.waits || 0) < 600) {
    // The search is still reading: the next saved lead comes soon. Check again in 1 s.
    await coSet(tabId, { waits: (j.co.waits || 0) + 1 });
    await new Promise((res) => setTimeout(res, 1000));
    return detailsNext(tabId);
  }
  if (!task) return detailsFinish(tabId, r.ok ? '' : 'dashboard not reachable');
  let step = '';
  if (task.kind === 'company' && typeof task.name === 'string' && task.name.trim()) step = 'search';
  if (task.kind === 'profile' && /\/in\/[^/?#]+/.test(String(task.url || ''))) step = 'profile';
  if (!step) return detailsFinish(tabId, 'unexpected task');
  await coSet(tabId, { task, step, name: task.name || '', slug: '', byLink: false, tasks: j.co.tasks + 1 });
  return detailsGo(tabId);
}

async function detailsGo(tabId) {
  const j = (await getJobs())[tabId];
  if (!j || !j.co || !j.co.task) return;
  const co = j.co;
  let url, label;
  if (co.step === 'search') {
    url = COMPANY_SEARCH + '?keywords=' + encodeURIComponent(co.name) + '&origin=GLOBAL_SEARCH_HEADER';
    label = `company search for ${co.name}`;
  } else if (co.step === 'about') {
    url = COMPANY_PREFIX + encodeURIComponent(co.slug) + '/about/';
    label = `${co.name || 'the company'}'s About page`;
  } else {
    // The person's own Experience page: every job listed at once (the profile page draws its
    // Experience section only when scrolled into view, which a hidden tab never is).
    url = PROFILE_PREFIX + String(co.task.url).match(/\/in\/([^/?#]+)/)[1] + '/details/experience/';
    label = 'a lead\'s Experience page (to see where they work)';
  }
  const min = HBF_CONFIG.pacing.minWaitMs, max = HBF_CONFIG.pacing.maxWaitMs;
  const wait = Math.round(min + Math.random() * (max - min));
  // Each step has a token. The safety alarm is set BEFORE the pause: if the browser puts this worker
  // to sleep during the pause or the page never answers, the alarm retries the step once, then ends.
  const token = (co.token || 0) + 1;
  await coSet(tabId, { token });
  chrome.alarms.create(`hbf-detail:${tabId}:${token}`, { when: Date.now() + wait + DETAIL_ANSWER_MS });
  await companyBeat(j, `Lead details: waiting ${Math.ceil(wait / 1000)}s, then ${label}`);
  await new Promise((res) => setTimeout(res, wait));
  const again = (await getJobs())[tabId];
  if (!again || !again.co || again.co.token !== token) return;   // stopped, closed, or retried meanwhile
  const ok = await callServer('/api/details/page', 'POST', { job_id: j.jobId }, j.serverUrl);
  if (!ok.ok || !ok.json || !ok.json.ok) {
    return detailsFinish(tabId, (ok.json && ok.json.reason) || 'dashboard not reachable');
  }
  await coSet(tabId, { n: co.n + 1 });
  try { await chrome.tabs.update(tabId, { url }); } catch (e) { /* tab closed */ }
}

chrome.alarms.onAlarm.addListener(async (alarm) => {
  const m = /^hbf-detail:(\d+):(\d+)$/.exec(alarm.name);
  if (!m) return;
  const tabId = Number(m[1]);
  const k = (await getJobs())[tabId];
  if (!k || !k.co || k.co.token !== Number(m[2])) return;            // that step answered: nothing to do
  if ((k.co.retries || 0) < 1) {
    await coSet(tabId, { retries: (k.co.retries || 0) + 1 });
    await companyBeat(k, 'Lead details: a step did not answer, trying it once more');
    return detailsGo(tabId);
  }
  detailsFinish(tabId, 'a LinkedIn page did not answer');
});

async function detailsResult(tabId, msg) {
  const j = (await getJobs())[tabId];
  if (!j || !j.co || !j.co.task || msg.step !== j.co.step) return { ok: false, error: 'not expected' };
  const co = j.co;
  const server = j.serverUrl;
  const slugOk = (s) => typeof s === 'string' && /^[\w.\-%]+$/.test(s) && s.length <= 150;
  const pageOf = (s) => 'https://www.linkedin.com/company/' + s + '/';
  const saveCompany = (body) => callServer('/api/companies', 'POST',
    Object.assign({ job_id: j.jobId, name: co.name, by_link: co.byLink }, body), server);
  let next = null;                                // { step, name, slug, byLink } or null = next task

  if (co.step === 'profile') {
    const company = String(msg.company || '').slice(0, 200);
    const title = String(msg.title || '').slice(0, 200);
    const link = slugOk(msg.slug) ? pageOf(msg.slug) : '';
    const r = await callServer('/api/details/profile', 'POST',
      { job_id: j.jobId, person_id: co.task.person_id, company, title, company_url: link,
        note: String(msg.why || '').slice(0, 200) }, server);
    const ans = (r.ok && r.json) || {};
    await companyBeat(j, 'Lead details: profile read, ' + (company ? `works at ${company}`
      : link ? 'current company link found, reading its page' : `no current company found: ${msg.why || 'not shown'} (left empty)`));
    // No readable name but the exact company link: its About page gives the name.
    if (ans.about && link) next = { step: 'about', name: company || msg.slug, slug: msg.slug, byLink: true };
    else if (ans.search) next = { step: 'search', name: ans.search, slug: '', byLink: false };
  } else if (co.step === 'search') {
    if (slugOk(msg.slug)) {
      next = { step: 'about', name: co.name, slug: msg.slug, byLink: false };
    } else {
      await saveCompany({ status: 'not_found', note: String(msg.why || '').slice(0, 200) });
      await companyBeat(j, `Lead details: ${co.name}: ${msg.why || 'not found'}, left empty`);
    }
  } else {
    const industry = String(msg.industry || ''), size = String(msg.size || '');
    if (industry || size) {
      await saveCompany({ status: 'found', industry, size, linkedin_url: pageOf(co.slug),
                          page_name: String(msg.page_name || '').slice(0, 200), website: String(msg.website || '').slice(0, 200) });
      await coSet(tabId, { found: co.found + 1 });
      await companyBeat(j, `Lead details: ${co.name}: ${[industry, size].filter(Boolean).join(', ')}`);
    } else {
      if (!co.byLink) await saveCompany({ status: 'not_found', note: String(msg.why || 'About page not readable').slice(0, 200) });
      await companyBeat(j, `Lead details: ${co.name || 'company'}: About page could not be read, left empty`);
    }
  }
  await coSet(tabId, { retries: 0, token: (co.token || 0) + 1000 });   // answered: its alarm no longer applies
  if (next) {
    await coSet(tabId, next);
    detailsGo(tabId);
  } else {
    detailsNext(tabId);
  }
  return { ok: true };
}

// ------------------------------------------------- FAST lead details (1.46.0)
// One hidden helper tab on a plain LinkedIn address; from it, LinkedIn's own profile data is read for
// each lead (one request, ~0.5 s): current job -> company name, LinkedIn page, industry, size.
// HBF_CONFIG.fastDetails.workers readers share the tab, each with its own random pause. Same server
// endpoints, limits and stop rules as the classic page-by-page method, which takes over by itself
// when this data cannot be read (LinkedIn changed it).
const FD = () => HBF_CONFIG.fastDetails || {};
const fastWorkers = new Map();        // helper tab id -> running reader count (this worker's memory)

/** Runs INSIDE the LinkedIn helper tab (page origin, the person's own session). Self-contained. */
async function readLeadInPage(slug, pathTemplate) {
  const csrf = (document.cookie.match(/JSESSIONID="?([^";]+)"?/) || [])[1];
  if (!csrf) return { kind: 'signin', text: 'You are not signed in to LinkedIn in this browser.' };
  let res;
  try {
    res = await fetch(pathTemplate.replace('{slug}', encodeURIComponent(slug)), {
      credentials: 'include',
      headers: { 'csrf-token': csrf, 'x-restli-protocol-version': '2.0.0', accept: 'application/vnd.linkedin.normalized+json+2.1' },
    });
  } catch (e) { return { kind: 'error', text: 'request failed' }; }
  const path = (() => { try { return new URL(res.url).pathname; } catch (e) { return ''; } })();
  if (/^\/(checkpoint|authwall|login|uas)\b/.test(path)) return { kind: 'limit', text: 'LinkedIn is asking for a sign-in or security check.' };
  if (res.status === 429 || res.status === 999) return { kind: 'limit', text: `LinkedIn refused the request (status ${res.status}).` };
  if (res.status === 401 || res.status === 403) return { kind: 'signin', text: `LinkedIn refused the request (status ${res.status}); sign in again.` };
  if (res.status === 404 || res.status === 410) return { kind: 'ok', why: 'profile not available' };
  if (!res.ok) return { kind: 'format', text: 'status ' + res.status };
  let data;
  try { data = await res.json(); } catch (e) { return { kind: 'format', text: 'not JSON' }; }
  const inc = Array.isArray(data.included) ? data.included : [];
  if (!inc.some((e) => /identity\.profile\.Profile$/.test(e.$type || ''))) return { kind: 'format', text: 'no profile in the data' };
  const byUrn = new Map(inc.filter((e) => e.entityUrn).map((e) => [e.entityUrn, e]));
  const when = (p) => { const s = (p.dateRange && p.dateRange.start) || {}; return (s.year || 0) * 100 + (s.month || 0); };
  const current = inc.filter((e) => /identity\.profile\.Position$/.test(e.$type || '') && e.dateRange && e.dateRange.start && !e.dateRange.end)
    .sort((a, b) => when(b) - when(a));
  if (!current.length) return { kind: 'ok', why: 'no current job on the profile' };
  const p = current[0];
  const co = byUrn.get(p.companyUrn || p['*company'] || '') || {};
  const industry = (co.industryUrns || []).map((u) => (byUrn.get(u) || {}).name).find(Boolean) || '';
  const r = co.employeeCountRange || {};
  const n = (x) => Number(x).toLocaleString('en-US');
  const size = r.start ? (r.end ? `${n(r.start)}-${n(r.end)} employees` : `${n(r.start)}+ employees`) : '';
  const url = /^https:\/\/www\.linkedin\.com\/company\/[^/?#\s]+\/?$/.test(co.url || '') ? co.url : '';
  return { kind: 'ok', title: String(p.title || ''), company: String(p.companyName || co.name || ''),
           pageName: String(co.name || ''), url, industry, size, why: url ? '' : 'current job has no LinkedIn company page' };
}

async function startFastDetails(tabId) {
  const j = (await getJobs())[tabId];
  if (!j || !j.fast) return;
  const want = FD().workers || 2;
  const have = fastWorkers.get(tabId) || 0;
  for (let k = have; k < want; k++) {
    fastWorkers.set(tabId, (fastWorkers.get(tabId) || 0) + 1);
    fastReader(tabId, k).catch(() => {}).finally(() => fastReaderEnded(tabId));
  }
}

async function fastReaderEnded(tabId) {
  const left = (fastWorkers.get(tabId) || 1) - 1;
  if (left > 0) { fastWorkers.set(tabId, left); return; }
  fastWorkers.delete(tabId);
  const j = (await getJobs())[tabId];
  if (!j || !j.fast) return;
  await updateJobs((all) => { if (all[tabId]) all[tabId].fast = false; });
  if (j.fastFallback) {
    // LinkedIn's data could not be read: the classic page-by-page method finishes the job in this tab.
    await companyBeat(j, 'Lead details: switching to reading pages (slower)');
    await updateJobs((all) => { if (all[tabId]) all[tabId].details = true; });
    return startCompanies(tabId);
  }
  await companyBeat(j, 'Lead details: done' + (j.fastWhy ? ` (${j.fastWhy})` : ''));
  closeTabLater(tabId, 3000);
}

async function fastStop(tabId, change) {
  await updateJobs((all) => { if (all[tabId]) Object.assign(all[tabId], change); });
}

async function fastReader(tabId, k) {
  const fd = FD();
  let formatFails = 0;
  let idle = 0;
  let done = null;                       // the lead just read: saved by the NEXT server call (one call per lead)
  let lastRead = 0;                      // when this reader last asked LinkedIn
  if (k) await new Promise((r) => setTimeout(r, 400 * k));            // readers start a little apart
  for (;;) {
    const j = (await getJobs())[tabId];
    if (!j || !j.fast || j.fastStop) {
      if (done && j) await callServer('/api/details/fast', 'POST', { job_id: j.jobId, done }, j.serverUrl);
      return;
    }
    // ONE call: saves the previous lead, checks the run, counts and hands out the next lead.
    const r = await callServer('/api/details/fast', 'POST', { job_id: j.jobId, done }, j.serverUrl);
    if (!r.ok || !r.json) return fastStop(tabId, { fastStop: true, fastWhy: 'dashboard not reachable' });
    done = null;
    const { status, task, stop } = r.json;
    if (stop) return fastStop(tabId, { fastStop: true, fastWhy: stop });
    if (!task) {
      if (status === 'running' && idle < 600) { idle += 1; await new Promise((res) => setTimeout(res, 1000)); continue; }
      return;                                                         // search over and nothing left
    }
    idle = 0;
    const slug = (String(task.url || '').match(/\/in\/([^/?#]+)/) || [])[1];
    if (task.kind !== 'profile' || !slug) continue;

    // Cautious pace: at least a random 1-2.5 s between this reader's LinkedIn requests. Time spent
    // waiting for the server already counts towards it (LinkedIn never sees requests closer together).
    const gap = Math.round(fd.minWaitMs + Math.random() * (fd.maxWaitMs - fd.minWaitMs));
    const left = lastRead + gap - Date.now();
    if (left > 0) await new Promise((res) => setTimeout(res, left));
    lastRead = Date.now();

    let got;
    try {
      const [res] = await chrome.scripting.executeScript({ target: { tabId }, func: readLeadInPage,
                                                           args: [decodeURIComponent(slug), fd.profileUrl] });
      got = (res && res.result) || { kind: 'format', text: 'no answer from the page' };
    } catch (e) {
      got = { kind: 'format', text: 'helper tab not ready (' + String(e && e.message || e).slice(0, 80) + ')' };
    }

    if (got.kind === 'limit' || got.kind === 'signin') {
      // Same rule as the search: any LinkedIn notice stops the whole run and shows the tab.
      await fastStop(tabId, { fastStop: true, fastWhy: 'LinkedIn showed a notice' });
      await companyBeat(j, 'Lead details stopped: ' + got.text);
      await callServer('/api/error', 'POST', { job_id: j.jobId, message: 'LinkedIn showed a notice while reading lead details: ' + got.text }, j.serverUrl);
      try { await chrome.tabs.update(tabId, { url: 'https://www.linkedin.com/feed/' }); } catch (e) { /* closed */ }
      await showTab(tabId);
      return;
    }
    if (got.kind !== 'ok') {
      formatFails += 1;
      companyBeat(j, `Lead details: could not read LinkedIn's data (${got.text})`);
      if (formatFails >= 2) return fastStop(tabId, { fastStop: true, fastFallback: true });
      continue;                                                       // the server hands this lead out again later
    }
    formatFails = 0;
    done = { person_id: task.person_id, company: (got.company || '').slice(0, 200), title: (got.title || '').slice(0, 200),
             company_url: got.url || '', page_name: (got.pageName || '').slice(0, 200), industry: (got.industry || '').slice(0, 120),
             size: (got.size || '').slice(0, 60), note: (got.why || '').slice(0, 300) };
  }
}

// The browser may stop this worker between events; readers that died are restarted from here.
chrome.alarms.create('hbf-fast-watch', { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== 'hbf-fast-watch') return;
  const jobs = await getJobs();
  for (const [id, j] of Object.entries(jobs)) {
    if (j.fast && !j.fastStop && !fastWorkers.has(Number(id))) startFastDetails(Number(id));
  }
});

async function closeTabLater(tabId, ms) {
  setTimeout(async () => {
    try { await chrome.tabs.remove(tabId); } catch (e) { /* already closed */ }
  }, ms);
}

async function showTab(tabId) {
  // Un-collapse the group so the person can see a LinkedIn warning. Focus is not moved.
  try {
    const tab = await chrome.tabs.get(tabId);
    if (tab.groupId && tab.groupId !== -1) {
      await chrome.tabGroups.update(tab.groupId, { collapsed: false });
    }
  } catch (e) { /* ignore */ }
}

chrome.tabs.onRemoved.addListener((tabId) => {
  updateJobs((jobs) => { delete jobs[tabId]; }).catch(() => {});
});

// ----------------------------------------------------------------- messages
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    try {
      switch (msg && msg.type) {
        case 'open': {    // from dashboard_bridge.js
          // The search sends its results to this dashboard's own address (openSearch keeps it on
          // the search); the Options value is left alone and only used as a fallback.
          return sendResponse(await openSearch(msg, sender.tab));
        }

        case 'teachInfo': { // from content_search.js: is this tab a location set-up tab? for which place?
          const { teachTabs } = await chrome.storage.session.get('teachTabs');
          const t = sender.tab && teachTabs && teachTabs[sender.tab.id];
          return sendResponse(t ? { ok: true, place: t.place, problem: t.problem || '' } : { ok: false });
        }

        case 'geoFound': {  // from content_search.js: the place's ID was read from the set-up page
          const ok = sender.tab ? await learnPlace(sender.tab.id, String(msg.id || ''), 'found') : false;
          return sendResponse({ ok });
        }

        case 'teach':     // from dashboard_bridge.js: set up a place's LinkedIn location by hand
          return sendResponse(await teachPlace(msg.place, sender.tab, msg.problem));

        case 'resolve':   // from dashboard_bridge.js: set up every unknown place automatically
          return sendResponse(await resolvePlacesOnLinkedIn(msg.places, sender.tab));

        case 'setupTask': { // from content_search.js: is this the hidden set-up tab? which place?
          const { setupTabs } = await chrome.storage.session.get('setupTabs');
          const t = sender.tab && setupTabs && setupTabs[sender.tab.id];
          const w = sender.tab && setupWaiters.get(sender.tab.id);
          if (t && w) w.started();
          return sendResponse(t && w ? { ok: true, place: t.place } : { ok: false });
        }

        case 'setupToFront': { // the suggestions did not appear in the hidden tab: show it, come back afterwards
          if (sender.tab) {
            // A tab in a collapsed group or an unfocused window stays hidden: open the group, raise the window.
            if (sender.tab.groupId != null && sender.tab.groupId >= 0) {
              await chrome.tabGroups.update(sender.tab.groupId, { collapsed: false }).catch(() => {});
            }
            await chrome.tabs.update(sender.tab.id, { active: true });
            await chrome.windows.update(sender.tab.windowId, { focused: true }).catch(() => {});
            const { setupTabs } = await chrome.storage.session.get('setupTabs');
            if (setupTabs && setupTabs[sender.tab.id]) { setupTabs[sender.tab.id].front = true; await chrome.storage.session.set({ setupTabs }); }
          }
          return sendResponse({ ok: true });
        }

        case 'setupResult': { // from content_search.js: the outcome for one place
          const w = sender.tab && setupWaiters.get(sender.tab.id);
          if (w) w(msg);
          return sendResponse({ ok: true });
        }

        case 'geo-check': { // from dashboard_bridge.js: which places still need setting up
          const geo = await resolvePlaces(msg.locations || [], msg.known);
          return sendResponse({ ok: true, needPlaces: geo.missing });
        }

        case 'geo-list': {  // from dashboard_bridge.js: places set up by the person
          return sendResponse({ ok: true, places: await getLearned() });
        }

        case 'geo-forget': {
          const learned = await getLearned();
          delete learned[normPlace(msg.place)];
          await chrome.storage.local.set({ geoLearned: learned });
          return sendResponse({ ok: true, places: learned });
        }

        case 'stop': {    // from dashboard_bridge.js
          const jobs = await getJobs();
          for (const [tabId, j] of Object.entries(jobs)) {
            if (j.jobId === msg.jobId) { try { await chrome.tabs.remove(Number(tabId)); } catch (e) {} }
          }
          return sendResponse({ ok: true });
        }

        case 'getJob': {  // from content_search.js
          const jobs = await getJobs();
          const j = sender.tab && jobs[sender.tab.id];
          if (!j) {
            const { lastOpenAt } = await chrome.storage.session.get('lastOpenAt');
            return sendResponse({ ok: false, pending: !!lastOpenAt && Date.now() - lastOpenAt < 10000 });
          }
          return sendResponse({ ok: true, jobId: j.jobId, activelyHiring: !!j.activelyHiring,
                                serverUrl: j.serverUrl || await getServerUrl(),
                                browserId: await getBrowserId() });
        }

        case 'sleep': {   // timers in hidden tabs are throttled; the worker's are not
          const ms = Math.max(0, Math.min(Number(msg.ms) || 0, MAX_SLEEP_MS));
          await new Promise((r) => setTimeout(r, ms));
          return sendResponse({ ok: true });
        }

        case 'api': {     // content script -> server, through here to avoid CORS
          const path = String(msg.path || '');
          if (!path.startsWith('/api/')) return sendResponse({ ok: false, error: 'bad path' });
          const body = Object.assign({}, msg.body || {});
          if (path === '/api/ingest') body.browser_id = await getBrowserId();
          const jobs = await getJobs();
          const j = sender.tab && jobs[sender.tab.id];
          const server = (j && j.serverUrl) || await getServerUrl();
          const r = await callServer(path, msg.method || 'POST', body, server);
          if (!r.ok && r.status === 0 && j) await reportUnreachable(j.dashboardTabId, server, path, r.error);
          return sendResponse(r);
        }

        case 'done': {    // finished normally: close the tab after 3 s
          // The details tab (Company Industry / Size) carries on by itself until its tasks are done.
          sendResponse({ ok: true });
          if (sender.tab) closeTabLater(sender.tab.id, 3000);   // the details tab carries on by itself
          return;
        }

        case 'companyTask': { // from content_company.js: is this tab doing a company-details step? which?
          const j = sender.tab && (await getJobs())[sender.tab.id];
          if (!j || !j.co || !j.co.task) return sendResponse({ ok: false });
          return sendResponse({ ok: true, step: j.co.step, name: j.co.name });
        }

        case 'companyResult':
          if (!sender.tab) return sendResponse({ ok: false });
          return sendResponse(await detailsResult(sender.tab.id, msg));

        case 'companyBlocked': { // LinkedIn limit / sign-in: stop at once and keep the tab for the person
          const j = sender.tab && (await getJobs())[sender.tab.id];
          if (j && j.co) {
            await updateJobs((jobs) => { if (jobs[sender.tab.id]) delete jobs[sender.tab.id].co; });
            await companyBeat(j, 'Lead details stopped: ' + String(msg.text || 'LinkedIn showed a notice').slice(0, 200));
            await showTab(sender.tab.id);
            // Same rule as the search: any LinkedIn notice stops the whole run.
            await callServer('/api/error', 'POST', { job_id: j.jobId,
              message: 'LinkedIn showed a notice while reading company details: ' + String(msg.text || '').slice(0, 200) }, j.serverUrl);
          }
          return sendResponse({ ok: true });
        }

        case 'nextPage': {         // the tab has read its page; the pause and the next load happen HERE
          // A hidden tab can be frozen by the browser (Edge efficiency mode / sleeping tabs), so it must
          // not do the waiting itself: runs stopped at "page 1: waiting 2s" until the tab was opened.
          const jobs = await getJobs();
          const j = sender.tab && jobs[sender.tab.id];
          if (!j) return sendResponse({ ok: false, error: 'not a search tab' });
          const n = Math.floor(Number(msg.page));
          if (!(n >= 2 && n <= 100)) return sendResponse({ ok: false, error: 'bad page number' });
          const u = new URL(sender.tab.url);
          u.searchParams.set('page', String(n));
          if (!u.href.startsWith(SEARCH_PREFIX)) return sendResponse({ ok: false, error: 'not a search page' });
          sendResponse({ ok: true });
          turnPage(sender.tab.id, j.jobId, n - 1, u.href, Number(msg.waitMs) || 0);
          return;
        }

        case 'restartAtPageOne': { // continued page was empty: same search, page 1, same tab
          const jobs = await getJobs();
          if (!sender.tab || !jobs[sender.tab.id]) return sendResponse({ ok: false, error: 'not a search tab' });
          const u = new URL(sender.tab.url);
          if (!(Number(u.searchParams.get('page') || '1') > 1)) return sendResponse({ ok: false, error: 'already page 1' });
          u.searchParams.delete('page');
          if (!u.href.startsWith(SEARCH_PREFIX)) return sendResponse({ ok: false, error: 'not a search page' });
          await chrome.tabs.update(sender.tab.id, { url: u.href });
          return sendResponse({ ok: true });
        }

        case 'attention': // LinkedIn needs the person (captcha, limit, sign-in): keep the tab
          if (sender.tab) await showTab(sender.tab.id);
          return sendResponse({ ok: true });

        default:
          return sendResponse({ ok: false, error: 'unknown message' });
      }
    } catch (e) {
      try { sendResponse({ ok: false, error: String(e && e.message || e) }); } catch (_) {}
    }
  })();
  return true; // keep the channel open for the async answer
});
