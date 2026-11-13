/* Runs on the dashboard page. Marks the extension as installed and passes
 * the dashboard's messages ("open", "stop", and the location set-up ones) to the background worker.
 * Always answers: if the extension cannot be reached (for example it was just
 * reloaded), the dashboard gets an error instead of waiting forever. */
(function () {
  const root = document.documentElement;
  root.dataset.hbfExt = '1';
  root.dataset.hbfExtVersion = chrome.runtime.getManifest().version;

  const TYPES = ['open', 'stop', 'teach', 'geo-list', 'geo-forget', 'geo-check', 'resolve'];
  window.addEventListener('message', (event) => {
    if (event.source !== window) return;
    const d = event.data;
    if (!d || d.source !== 'hbf-dashboard') return;
    if (!TYPES.includes(d.type)) return;

    const answer = (ok, error, extra) => window.postMessage(Object.assign({
      source: 'hbf-extension', type: d.type + '-result', jobId: d.jobId, ok, error: error || '',
    }, extra || {}), '*');
    try {
      chrome.runtime.sendMessage({ type: d.type, url: d.url, jobId: d.jobId, place: d.place, known: d.known,
                                   places: d.places, problem: d.problem,
                                   geoUrlBase: d.geoUrlBase, locations: d.locations }, (res) => {
        const err = chrome.runtime.lastError;
        answer(!err && !!(res && res.ok), err ? err.message : (res && res.error) || '',
               { needPlaces: res && res.needPlaces, places: res && res.places, place: d.place,
                 done: res && res.done, reason: res && res.reason, offered: res && res.offered,
                 blocked: res && res.blocked, failedPlace: res && res.place });
      });
    } catch (e) {
      answer(false, 'the extension was reloaded or is off (' + (e && e.message || e) + ')');
    }
  });

  // From the background worker: a place was set up in LinkedIn's Locations filter.
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg && msg.type === 'resolve-progress') {
      window.postMessage(Object.assign({}, msg, { source: 'hbf-extension' }), '*');
    }
    if (msg && msg.type === 'server-error') {
      window.postMessage({ source: 'hbf-extension', type: 'server-error', message: msg.message }, '*');
    }
    if (msg && msg.type === 'geo-learned') {
      window.postMessage({ source: 'hbf-extension', type: 'geo-learned', place: msg.place, id: msg.id }, '*');
    }
  });
})();
