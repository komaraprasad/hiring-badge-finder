// READ-ONLY check #2 for "fast mode": about 5 GET requests, the same kinds the page makes. Downloads one report.
// Run it exactly like check #1: on a LinkedIn people search with results, Console, context "top", paste, Enter.
(async () => {
  const report = { when: new Date().toISOString(), page: location.pathname + location.search, steps: [] };
  const log = (step, data) => { report.steps.push({ step, ...data }); console.log('[probe2]', step, data); };
  const csrf = (document.cookie.match(/JSESSIONID="?([^";]+)"?/) || [])[1];
  if (!csrf) return console.log('Not signed in to LinkedIn in this tab.');
  const H = { 'csrf-token': csrf, 'x-restli-protocol-version': '2.0.0', accept: 'application/vnd.linkedin.normalized+json+2.1' };
  const pause = (ms) => new Promise((r) => setTimeout(r, ms));
  const cut = (o, n = 220) => JSON.parse(JSON.stringify(o, (k, v) => (typeof v === 'string' && v.length > n ? v.slice(0, n) + '…' : v)));
  const getJson = async (url) => {
    const t0 = performance.now();
    const res = await fetch(url, { headers: H, credentials: 'include' });
    const json = await res.json().catch(() => null);
    return { status: res.status, ms: Math.round(performance.now() - t0), json };
  };

  // A. Search result pages 2 and 3 fetched directly (in parallel), as plain HTML: do they hold the cards?
  const base = new URL(location.href);
  const pageUrl = (n) => { const u = new URL(base); u.searchParams.set('page', String(n)); return u.toString(); };
  const t0 = performance.now();
  const pages = await Promise.all([2, 3].map(async (n) => {
    const s = performance.now();
    const res = await fetch(pageUrl(n), { credentials: 'include' });
    const html = await res.text();
    const profiles = [...new Set(html.match(/linkedin\.com\/in\/[A-Za-z0-9\-_%]+/g) || [])];
    const i = html.indexOf('profile-framedphoto');
    return { page: n, status: res.status, final_url: res.url.slice(0, 200), ms: Math.round(performance.now() - s), bytes: html.length,
             profiles: profiles.length, framedphoto: (html.match(/profile-framedphoto/g) || []).length,
             displayphoto: (html.match(/profile-displayphoto/g) || []).length,
             hiring_opportunities: (html.match(/hiring-opportunities/g) || []).length,
             see_open_roles: (html.match(/See open roles/g) || []).length,
             around_first_frame: i >= 0 ? html.slice(Math.max(0, i - 900), i + 300).replace(/\s+/g, ' ') : '' };
  }));
  log('A search pages fetched directly', { total_ms: Math.round(performance.now() - t0), pages });
  await pause(1500);

  // B. One profile's data: one full example of each kind of entry (job, company, industry ...).
  const slug = (document.querySelector('main a[href*="/in/"]') || { href: '' }).href.split('/in/')[1]?.split(/[/?]/)[0] || '';
  let companyId = '';
  if (slug) {
    const r = await getJson('/voyager/api/identity/dash/profiles?q=memberIdentity&memberIdentity=' + encodeURIComponent(slug) +
      '&decorationId=com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-93');
    const byType = {};
    for (const e of (r.json && r.json.included) || []) {
      const t = e.$type || '?';
      byType[t] = byType[t] || { count: 0, examples: [] };
      byType[t].count++;
      if (byType[t].examples.length < (/Position$/.test(t) ? 3 : 1)) byType[t].examples.push(cut(e));
    }
    const keep = Object.fromEntries(Object.entries(byType).filter(([t]) => /Position|Company|Industry|Profile$|Geo|DateRange/i.test(t)));
    log('B profile entries', { status: r.status, ms: r.ms, types: Object.fromEntries(Object.entries(byType).map(([t, v]) => [t, v.count])), examples: keep });
    companyId = (JSON.stringify(r.json || {}).match(/"companyUrn":"urn:li:fsd_company:(\d+)"/) || [])[1] || '';
    await pause(1500);
  } else log('B profile entries', { note: 'no profile link on the page' });

  // C. One company: size range and industry name, two ways (the full one is slow: 3.4 s in check #1).
  if (companyId) {
    const full = await getJson('/voyager/api/organization/companies/' + companyId);
    const d = (full.json && full.json.data) || {};
    log('C1 company full', { status: full.status, ms: full.ms, name: d.name, universalName: d.universalName, staffCount: d.staffCount,
      staffCountRange: cut(d.staffCountRange || null), companyIndustries: d['*companyIndustries'], url: d.companyPageUrl,
      industries_included: cut(((full.json && full.json.included) || []).filter((x) => /Industry/.test(x.$type || ''))) });
    await pause(1500);
    const dash = await getJson('/voyager/api/voyagerOrganizationDashCompanies/urn%3Ali%3Afsd_company%3A' + companyId);
    const dd = (dash.json && dash.json.data) || {};
    log('C2 company dash', { status: dash.status, ms: dash.ms, keys: Object.keys(dd).slice(0, 80),
      employeeCountRange: cut(dd.employeeCountRange || null), industry: cut(dd['*industryV2Taxonomy'] || dd['*industry'] || null),
      name: dd.name, url: dd.url, websiteUrl: dd.websiteUrl,
      industries_included: cut(((dash.json && dash.json.included) || []).filter((x) => /Industry/.test(x.$type || ''))) });
  } else log('C company', { note: 'no company id found' });

  const blob = new Blob([JSON.stringify(report, null, 1)], { type: 'application/json' });
  const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: 'hbf-fast-probe-2.json' });
  document.body.appendChild(a); a.click(); a.remove();
  console.log('[probe2] DONE. hbf-fast-probe-2.json is in your Downloads folder.');
})();
