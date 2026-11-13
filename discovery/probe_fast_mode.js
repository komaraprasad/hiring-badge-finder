// READ-ONLY check for "fast mode". About 5 GET requests to LinkedIn, the same ones the page itself makes.
// It changes nothing, sends nothing anywhere, and downloads one report file to your computer.
//
// 1. In the browser profile with the tool's LinkedIn account, open a people search that shows #HIRING people,
//    e.g. https://www.linkedin.com/search/results/people/?keywords=recruiter&geoUrn=%5B%22105214831%22%5D
//    Wait until the results are visible. Scroll to the bottom of the results once.
// 2. Press F12, open "Console". If Chrome asks, type: allow pasting   and press Enter.
// 3. Paste this whole file and press Enter. Wait for "DONE" (about 10 seconds).
// 4. A file "hbf-fast-probe.json" appears in Downloads. Tell Claude it is there.
(async () => {
  const report = { when: new Date().toISOString(), page: location.pathname + location.search, steps: [] };
  const log = (step, data) => { report.steps.push({ step, ...data }); console.log('[probe]', step, data); };
  const csrf = (document.cookie.match(/JSESSIONID="?([^";]+)"?/) || [])[1];
  if (!csrf) return console.log('Not signed in to LinkedIn in this tab (no session). Sign in and try again.');
  const H = { 'csrf-token': csrf, 'x-restli-protocol-version': '2.0.0', accept: 'application/vnd.linkedin.normalized+json+2.1' };
  const pause = (ms) => new Promise((r) => setTimeout(r, ms));
  const get = async (url) => {
    const t0 = performance.now();
    try {
      const res = await fetch(url, { headers: H, credentials: 'include' });
      const text = await res.text();
      let json = null; try { json = JSON.parse(text); } catch (e) { /* not JSON */ }
      return { status: res.status, ms: Math.round(performance.now() - t0), bytes: text.length, json, text: json ? '' : text.slice(0, 300) };
    } catch (e) { return { status: 0, error: String(e) }; }
  };
  // Every place in a JSON tree whose key or text matches `re`, with a short value (for the report).
  const findPaths = (obj, re, max = 40) => {
    const out = [];
    const walk = (o, path) => {
      if (out.length >= max || o == null) return;
      if (typeof o === 'string') { if (re.test(o)) out.push({ path, value: o.slice(0, 160) }); return; }
      if (typeof o !== 'object') return;
      for (const [k, v] of Object.entries(o)) {
        const p = path + (Array.isArray(o) ? `[${k}]` : '.' + k);
        if (re.test(k) && (typeof v !== 'object' || v === null)) out.push({ path: p, value: String(v).slice(0, 160) });
        walk(v, p);
      }
    };
    walk(obj, '$');
    return out;
  };
  const shape = (o, depth = 0) => {       // key names only, no values (keeps the report small)
    if (depth > 3 || o == null || typeof o !== 'object') return typeof o;
    if (Array.isArray(o)) return o.length ? [shape(o[0], depth + 1), `x${o.length}`] : [];
    return Object.fromEntries(Object.keys(o).slice(0, 40).map((k) => [k, shape(o[k], depth + 1)]));
  };

  // 1. Which internal data requests did this page make?
  const urls = [...new Set(performance.getEntriesByType('resource').map((e) => e.name)
    .filter((u) => /\/voyager\/api\/|\/graphql|flagship-web|\/rsc|sdui/i.test(u)))];
  log('1 page data requests', { count: urls.length, urls: urls.slice(0, 60).map((u) => u.slice(0, 400)) });

  // 2. Re-read the search-results request(s) and look for people, the frame and the open-roles link.
  const searchUrls = urls.filter((u) => /search/i.test(u) && /(graphql|voyager)/i.test(u)).slice(0, 2);
  let firstProfileSlug = '';
  for (const u of searchUrls) {
    const r = await get(u);
    const profiles = r.json ? [...new Set(JSON.stringify(r.json).match(/linkedin\.com\/in\/[A-Za-z0-9\-_%]+/g) || [])] : [];
    firstProfileSlug = firstProfileSlug || (profiles[0] || '').split('/in/')[1] || '';
    log('2 search data', { url: u.slice(0, 400), status: r.status, ms: r.ms, bytes: r.bytes, profiles_found: profiles.length,
      framed_photo: r.json ? findPaths(r.json, /framedphoto|frame/i, 25) : [],
      hiring_link_or_text: r.json ? findPaths(r.json, /hiring/i, 25) : [],
      shape: r.json ? shape(r.json) : r.text });
    await pause(1500);
  }
  if (!searchUrls.length) log('2 search data', { note: 'no search data request seen; the results may be built into the page itself' });

  // 3. One person's profile data (current job + company link), like the Experience page gives.
  const slug = firstProfileSlug || (document.querySelector('a[href*="/in/"]') || { href: '' }).href.split('/in/')[1]?.split(/[/?]/)[0] || '';
  if (slug) {
    const pu = '/voyager/api/identity/dash/profiles?q=memberIdentity&memberIdentity=' + encodeURIComponent(slug) +
      '&decorationId=com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-93';
    const r = await get(pu);
    log('3 profile data', { status: r.status, ms: r.ms, bytes: r.bytes,
      positions: r.json ? findPaths(r.json, /companyName|^title$|dateRange|company(Urn)?$|industry|employeeCount|staffCount|universalName/i, 40) : [],
      frame: r.json ? findPaths(r.json, /framedphoto|frameType|hiring/i, 20) : [],
      shape: r.json ? shape(r.json) : r.text });
    await pause(1500);

    // 4. One company's data (Industry / Size), by the first company id found in the profile.
    const companyId = r.json ? (JSON.stringify(r.json).match(/urn:li:fsd_company:(\d+)/) || [])[1] : '';
    if (companyId) {
      const cu = '/voyager/api/organization/companies/' + companyId;
      const c = await get(cu);
      log('4 company data', { status: c.status, ms: c.ms, bytes: c.bytes,
        fields: c.json ? findPaths(c.json, /^name$|industr|staffCount|employeeCount|companyPageUrl|universalName/i, 30) : [],
        shape: c.json ? shape(c.json) : c.text });
    } else log('4 company data', { note: 'no company id in the profile data' });
  } else log('3 profile data', { note: 'no profile link found on this page' });

  const blob = new Blob([JSON.stringify(report, null, 1)], { type: 'application/json' });
  const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: 'hbf-fast-probe.json' });
  document.body.appendChild(a); a.click(); a.remove();
  console.log('[probe] DONE. hbf-fast-probe.json is in your Downloads folder.');
})();
