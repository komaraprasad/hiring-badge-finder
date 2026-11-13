// READ-ONLY. Paste into the browser console (F12) on a LinkedIn people-search results page.
// It makes no requests and changes nothing. It describes the filter bar and the Locations menu so
// the extension can find them reliably. Steps:
//   1. Paste this, press Enter. It reports the filter bar at once.
//   2. Within 60 seconds: click "Locations" yourself, type "Mumbai" in its box and wait until
//      LinkedIn shows suggestions. Don't choose one.
//   3. The full report is copied to the clipboard by itself ("HBF report copied" appears in the console).
//      Paste it to your assistant.
(() => {
  const trim = (s, n) => (s || '').replace(/\s+/g, ' ').trim().slice(0, n);
  const visible = el => !!el && el.getClientRects().length > 0;
  const attrs = el => {
    const o = {};
    for (const a of el.attributes) {
      if (/^(class|role|type|placeholder|aria-[\w-]+|data-[\w-]+|componentkey|id|for|name|value|tabindex)$/.test(a.name)) {
        o[a.name] = trim(a.value, 120);
      }
    }
    return o;
  };
  const chain = (el, n) => {           // the element and a few parents (tag + key attributes)
    const out = [];
    for (let e = el, i = 0; e && i < n; e = e.parentElement, i++) out.push({ tag: e.tagName.toLowerCase(), ...attrs(e) });
    return out;
  };
  const describe = el => ({ text: trim(el.textContent, 80), chain: chain(el, 4) });

  // 1) filter bar: every visible clickable thing whose text is short (pills like People, Locations)
  const clickables = () => [...document.querySelectorAll('button, [role="button"], a, label, [tabindex]')]
    .filter(visible).filter(e => { const t = trim(e.textContent, 60); return t && t.length < 40; });
  const filterBar = clickables()
    .filter(e => /^(people|actively hiring|locations?|current compan|all filters|1st|2nd|3rd)/i.test(trim(e.textContent, 40)))
    .map(describe);

  const report = { url: location.pathname + location.search.replace(/keywords=[^&]*/, 'keywords=…'), filterBar };

  // 2) watch for the Locations menu: inputs, options and buttons that appear after the person clicks
  const before = new Set(document.querySelectorAll('*'));
  let done = false;
  const finish = () => {
    if (done) return; done = true;
    const fresh = [...document.querySelectorAll('*')].filter(e => !before.has(e) && visible(e));
    report.menu = {
      inputs: [...document.querySelectorAll('input')].filter(visible)
        .filter(i => i.type !== 'hidden').map(i => ({ ...describe(i), valueLength: (i.value || '').length })),
      options: fresh.filter(e => /mumbai/i.test(e.textContent || '') && (e.textContent || '').length < 120)
        .filter(e => ![...e.children].some(c => /mumbai/i.test(c.textContent || '')))   // innermost only
        .slice(0, 12).map(describe),
      checkboxes: [...document.querySelectorAll('input[type="checkbox"], [role="checkbox"], [role="option"]')]
        .filter(visible).slice(0, 15).map(e => ({ ...describe(e), checked: e.checked ?? e.getAttribute('aria-checked') ?? e.getAttribute('aria-selected') })),
      buttons: fresh.filter(e => /^(show|reset|apply|cancel)/i.test(trim(e.textContent, 30)) && (e.tagName === 'BUTTON' || e.getAttribute('role') === 'button'))
        .slice(0, 6).map(describe),
    };
    const json = JSON.stringify(report, null, 1);
    try { copy(json); console.log('HBF report copied to the clipboard (' + json.length + ' characters).'); }
    catch (e) { console.log(json); }
  };
  console.log('HBF: filter bar found (' + filterBar.length + ' items). Now click "Locations" and type Mumbai; report in up to 60 s.');
  const obs = new MutationObserver(() => {
    const opts = [...document.querySelectorAll('*')].filter(e => !before.has(e) && visible(e) && /mumbai/i.test(e.textContent || ''));
    if (opts.length) { obs.disconnect(); setTimeout(finish, 1500); }
  });
  obs.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
  setTimeout(() => { obs.disconnect(); finish(); }, 60000);
})();
