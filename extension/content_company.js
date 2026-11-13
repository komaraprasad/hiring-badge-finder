/*
 * Company details (industry, size), read after a search has finished, in the same hidden tab.
 *
 * Runs on LinkedIn company-search and company pages, but does something ONLY in a tab the
 * background worker is using for this (it answers 'companyTask'); a company page the person
 * opens themselves is left alone. It only READS the page that is shown: no clicks, no typing,
 * no requests. The background worker does every page load, with the 4-8 s pause before each.
 *
 *   step 'search': LinkedIn company search for the name -> the ONE result whose name is the
 *                  same (ignoring case, punctuation and Pvt/Ltd/Inc...). None or several -> not found.
 *   step 'about' : that company's About page -> the values next to "Industry" and "Company size".
 * A value that cannot be read stays empty; nothing is guessed.
 */
(function () {
  const CFG = self.HBF_CONFIG;
  const C = CFG.company;
  const S = CFG.states;
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const re = (src, flags) => new RegExp(src, flags || 'i');

  function send(msg, ms) {
    return new Promise((resolve) => {
      const timer = setTimeout(() => resolve({ ok: false, error: 'no answer' }), ms || CFG.timeouts.messageMs);
      try {
        chrome.runtime.sendMessage(msg, (r) => {
          clearTimeout(timer);
          resolve(chrome.runtime.lastError ? { ok: false, error: chrome.runtime.lastError.message } : (r || { ok: false }));
        });
      } catch (e) { clearTimeout(timer); resolve({ ok: false, error: String(e && e.message || e) }); }
    });
  }
  const sleep = (ms) => send({ type: 'sleep', ms }, ms + 5000);   // hidden tabs' own timers are throttled

  function blocked() {
    const path = location.pathname;
    if (re(S.checkpointPathRegex).test(path)) return 'LinkedIn is asking for a security check.';
    if (re(S.signInPathRegex).test(path) || document.querySelector('input#username, form.login__form')) {
      return 'You are not signed in to LinkedIn in this browser.';
    }
    const body = clean(document.body ? document.body.innerText : '').slice(0, 20000);
    if (re(S.commercialLimitRegex).test(body)) return 'LinkedIn shows the "commercial use limit".';
    if (re(S.limitRegex).test(body)) return 'LinkedIn is showing a limit or "unusual activity" notice.';
    return '';
  }

  function sameName(s) {
    return clean(String(s || '').toLowerCase().replace(re(C.legalSuffixRegex, 'gi'), ' ').replace(/[^\p{L}\p{N}&+ ]/gu, ' '));
  }
  const area = () => document.querySelector('main') || document.body;

  /** slug -> names shown for it, from every company link in the results. */
  function companyLinks() {
    const out = new Map();
    area().querySelectorAll('a[href]').forEach((a) => {
      let m = null;
      try { m = new URL(a.href, location.href).pathname.match(re(C.linkPathRegex)); } catch (e) { /* bad link */ }
      if (!m) return;
      const slug = decodeURIComponent(m[1]);
      if (!/^[\w.\-%]+$/.test(slug)) return;
      const name = clean((a.innerText || a.textContent || '').split('\n')[0]);
      if (!out.has(slug)) out.set(slug, new Set());
      if (name) out.get(slug).add(name);
    });
    return out;
  }

  function pickCompany(name) {
    const want = sameName(name);
    if (!want) return { slug: '', why: 'no name' };
    // One result card can link to the same company twice (/company/acme/ and /company/12345/), so
    // results are counted per CARD: the exact name on exactly one card = that company.
    const cards = new Map();                            // card element -> slugs on it
    let exact = 0;
    area().querySelectorAll('a[href]').forEach((a) => {
      let m = null;
      try { m = new URL(a.href, location.href).pathname.match(re(C.linkPathRegex)); } catch (e) { /* bad link */ }
      if (!m) return;
      const slug = decodeURIComponent(m[1]);
      if (!/^[\w.\-%]+$/.test(slug)) return;
      const card = a.closest('li, [role="listitem"]') || a;
      if (!cards.has(card)) cards.set(card, { slugs: new Set(), named: false });
      const c = cards.get(card);
      c.slugs.add(slug);
      const text = clean((a.innerText || a.textContent || '').split('\n')[0]);
      if (sameName(text) === want) c.named = true;
    });
    const hits = [...cards.values()].filter((c) => c.named);
    exact = hits.length;
    // The same company page linked from several places (layouts without one element per result)
    // is still ONE company: count the different pages that carry the exact name.
    const pages = new Set();
    hits.forEach((h) => h.slugs.forEach((s) => pages.add(s)));
    const named = [...pages].filter((s) => !/^\d+$/.test(s));
    if (exact > 1 && named.length === 1 && hits.every((h) => h.slugs.has(named[0]))) {
      return { slug: named[0], why: 'exact name (one company page, linked more than once)' };
    }
    if (exact === 1) {
      const slugs = [...hits[0].slugs];
      return { slug: slugs.find((s) => !/^\d+$/.test(s)) || slugs[0], why: 'exact name' };
    }
    const slugs = hits.map((h) => [...h.slugs].join('+')).join(', ');
    return { slug: '', why: exact ? `${exact} different companies with that name (${slugs})`.slice(0, 280)
                                  : `no result named exactly "${name}" (${cards.size} results)` };
  }

  function lines() {
    return String(area().innerText || '').split('\n').map(clean).filter(Boolean);
  }

  function readAbout() {
    const isLabel = (t, labels) => labels.includes(clean(t).toLowerCase());
    const sizeOk = (t) => re(C.sizeRegex).test(clean(t));
    const industryOk = (t) => {
      const v = clean(t);
      return v.length >= 2 && v.length <= 100 && !isLabel(v, C.industryLabels) && !isLabel(v, C.sizeLabels) && !sizeOk(v);
    };
    let industry = '', size = '';
    // 1. <dt>label</dt><dd>value</dd>
    area().querySelectorAll('dt').forEach((dt) => {
      let dd = dt.nextElementSibling;
      while (dd && dd.tagName !== 'DD' && dd.tagName !== 'DT') dd = dd.nextElementSibling;
      if (!dd || dd.tagName !== 'DD') return;
      const first = clean(String(dd.innerText || dd.textContent || '').split('\n')[0]);
      if (!industry && isLabel(dt.textContent, C.industryLabels) && industryOk(first)) industry = first;
      if (!size && isLabel(dt.textContent, C.sizeLabels) && sizeOk(first)) size = first;
    });
    // 2. Any layout: the line right after the label.
    const ls = lines();
    for (let i = 0; i < ls.length - 1; i++) {
      if (!industry && isLabel(ls[i], C.industryLabels) && industryOk(ls[i + 1])) industry = ls[i + 1];
      if (!size && isLabel(ls[i], C.sizeLabels) && sizeOk(ls[i + 1])) size = ls[i + 1];
    }
    // The company's own name (page heading) and its "Website" value.
    // The company's own name: the page title "(3) Microsoft: About | LinkedIn" (always there, even in
    // a hidden tab), else the share title, else the heading. Real run (Nov 2026): the heading gave
    // nothing for 25 of 25 companies.
    const fromTitle = (s) => clean(String(s || '').replace(/^\(\d+\+?\)\s*/, '').split(/\s+\|\s+linkedin\b/i)[0]
      .replace(/\s*[:\-–|]\s*(about|overview|home|jobs|people|posts|life)\s*$/i, ''));
    const og = document.querySelector('meta[property="og:title"]');
    const h1 = document.querySelector('main h1, h1');
    let name = fromTitle(document.title);
    if (!name || /^linkedin$/i.test(name)) name = fromTitle(og && og.getAttribute('content'));
    if (!name && h1) name = clean(String(h1.textContent || '').split('\n')[0]);
    name = /^linkedin$/i.test(name) ? '' : name.slice(0, 200);
    let website = '';
    for (let i = 0; i < ls.length - 1 && !website; i++) {
      if (clean(ls[i]).toLowerCase() === 'website' && /^(?:https?:\/\/)?(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+/i.test(ls[i + 1])) website = ls[i + 1];
    }
    if (!website) {
      area().querySelectorAll('dt').forEach((dt) => {
        if (website || clean(dt.textContent).toLowerCase() !== 'website') return;
        let dd = dt.nextElementSibling;
        while (dd && dd.tagName !== 'DD') dd = dd.nextElementSibling;
        const a = dd && dd.querySelector('a[href]');
        if (a) website = clean(a.innerText || a.textContent || a.getAttribute('href'));
      });
    }
    return { industry, size, name, website: website.slice(0, 200) };
  }

  // ------------------------------------------------------------ profile
  // Where the person works NOW, as their own profile shows it. Two places, both LinkedIn's own:
  //   1. the top card's "Current company: X" button
  //   2. the first Experience entry, only if its dates say "Present"; its /company/<id>/ link is
  //      the exact company page (no name matching needed).
  // Nothing current shown -> empty. A past job is never used.
  function uniqLines(el) {
    const out = [];
    String(el.innerText || '').split('\n').map(clean).filter(Boolean).forEach((l) => {
      if (out[out.length - 1] !== l) out.push(l);       // screen-reader copies repeat each line
    });
    return out;
  }
  function experienceSection() {
    // The person's own Experience page (/in/<x>/details/experience/): the whole main area is the list.
    if (/\/details\/experience/.test(location.pathname)) return area();
    const anchor = document.getElementById('experience');
    const sec = anchor && (anchor.closest('section') || anchor.parentElement);
    if (sec) return sec;
    return Array.from(document.querySelectorAll('section')).find((s) => {
      const h = s.querySelector('h2, h3');
      return h && clean(h.innerText || h.textContent).toLowerCase().split('\n')[0] === C.experienceHeading;
    }) || null;
  }
  // The first job = the first list item that has dates (a year or "Present"); an outer item with
  // several roles at one company comes before its inner items, so it is found first.
  const DATED = /\b(19|20)\d{2}\b/;
  function firstEntry(sec) {
    return Array.from(sec.querySelectorAll('li')).find((li) => {
      const ls = uniqLines(li);
      return ls.length >= 2 && ls.some((l) => DATED.test(l) || re(C.presentRegex).test(l));
    }) || null;
  }
  function companyLinkIn(el) {
    for (const a of el.querySelectorAll('a[href]')) {
      let m = null;
      try { m = new URL(a.href, location.href).pathname.match(re(C.linkPathRegex)); } catch (e) { /* bad link */ }
      if (m && /^[\w.\-%]+$/.test(decodeURIComponent(m[1]))) {
        const img = a.querySelector('img[alt]');
        return { slug: decodeURIComponent(m[1]), alt: img ? clean(img.getAttribute('alt')).replace(/\s*logo$/i, '') : '' };
      }
    }
    return null;
  }
  const isMeta = (l) => re(C.employmentTypeRegex).test(l) || re(C.durationRegex).test(l);

  // "Jan 2023 - Present · 2 yrs", "2019 – 2022", "Mar 2024 - Present": a job's dates.
  const RANGE = /\b(?:[a-z]{3,9}\.?\s+)?(?:19|20)\d{2}\s*[-–—]\s*(?:present|(?:[a-z]{3,9}\.?\s+)?(?:19|20)\d{2})\b/i;

  /** The latest job from the page TEXT, in LinkedIn's on-screen order (no markup assumed):
   *    one role:      Title / Company · Full-time / Jan 2023 - Present
   *    several roles: Company / Full-time · 3 yrs / Title / Jan 2023 - Present  */
  function jobFromText(ls) {
    const k = ls.findIndex((l) => RANGE.test(l));
    if (k < 0) return { found: false };
    if (!re(C.presentRegex).test(ls[k])) return { found: true, current: false };
    const prev = ls[k - 1] || '', prev2 = ls[k - 2] || '', prev3 = ls[k - 3] || '';
    let company = '', title = '';
    if (isMeta(prev2)) { company = prev3; title = prev; }
    else if (isMeta(prev)) { company = ''; title = prev2; }          // e.g. 'Self-employed': no employer
    else { title = prev2; company = clean(prev.split('·')[0]); }
    if (isMeta(company) || company.toLowerCase() === C.experienceHeading) company = '';
    return { found: true, current: true, company, title };
  }

  function linkFor(name) {
    const want = sameName(name), hits = new Set();
    if (!want) return '';
    area().querySelectorAll('a[href]').forEach((a) => {
      let m = null;
      try { m = new URL(a.href, location.href).pathname.match(re(C.linkPathRegex)); } catch (e) { /* bad link */ }
      if (!m) return;
      const slug = decodeURIComponent(m[1]);
      const img = a.querySelector('img[alt]');
      const texts = [clean((a.innerText || a.textContent || '').split('\n')[0]),
                     img ? clean(img.getAttribute('alt')).replace(/\s*logo$/i, '') : ''];
      if (/^[\w.\-%]+$/.test(slug) && texts.some((x) => x && sameName(x) === want)) hits.add(slug);
    });
    const list = [...hits];
    const named = list.filter((s) => !/^\d+$/.test(s));
    if (named.length === 1) return named[0];               // one company page (maybe also by number)
    return list.length === 1 ? list[0] : '';
  }

  function pageFacts() {
    const ls = uniqLines(area());
    return `[page: ${location.pathname.replace(/\/in\/[^/]+/, '/in/x')}, ${ls.length} lines, ` +
      `${ls.filter((l) => RANGE.test(l)).length} date lines, ${area().querySelectorAll('li').length} li, ` +
      `${area().querySelectorAll('a[href*="/company/"]').length} company links, text ${String(area().innerText || '').length} chars]`;
  }

  /** Where the person works NOW, from LinkedIn's own company LINK (no reading of line order, which
   *  differs between layouts and gave job titles as companies on real pages, Nov 2026):
   *    the FIRST link to a company page on the Experience page belongs to the latest job;
   *    it counts only if that job's part of the page says "Present";
   *    the company name is the link's own text or its logo's name.
   *  A current job without a company link, or no "Present": empty (never guessed). */
  function companyGroups() {
    const groups = [];
    area().querySelectorAll('a[href]').forEach((a) => {
      let m = null;
      try { m = new URL(a.href, location.href).pathname.match(re(C.linkPathRegex)); } catch (e) { /* bad link */ }
      if (!m) return;
      const slug = decodeURIComponent(m[1]);
      if (!/^[\w.\-%]+$/.test(slug)) return;
      const img = a.querySelector('img[alt]');
      const names = [clean(String(a.innerText || a.textContent || '').split('\n')[0]),
                     img ? clean(img.getAttribute('alt')).replace(/\s*logo$/i, '') : '']
        .filter((n) => n && !isMeta(n) && !RANGE.test(n) && n.length <= 150);
      const last = groups[groups.length - 1];
      if (last && last.slug === slug) { last.links.push(a); last.names.push(...names); }
      else groups.push({ slug, links: [a], names });
    });
    return groups;
  }

  /** Text from the start of `from` up to (not including) `to`, or to the end of the page area. */
  function textBetween(from, to) {
    try {
      const r = document.createRange();
      r.setStartBefore(from);
      if (to) r.setEndBefore(to); else r.setEndAfter(area().lastChild || area());
      return r.toString();
    } catch (e) { return ''; }
  }

  // "Eightfold.ai · Full-time": the company line of a single-role entry.
  const EMP_LINE = /^(.+?)\s*·\s*(?:full-time|part-time|contract|contractual|internship|freelance|self-employed|permanent|apprenticeship|seasonal|trainee)\b/i;

  /** The company's name for one Experience group, most reliable first:
   *   1. the logo's own name (alt "Eightfold.ai logo"),
   *   2. the "Company · Full-time" line (single-role entry: Title / Company · Full-time / dates),
   *   3. the link's first line ONLY when the entry has no such line (several roles: Company / ...).
   *  The first line of a single-role entry is the JOB TITLE (real pages, Nov 2026): never used. */
  function groupName(group, lines) {
    for (const a of group.links) {
      const img = a.querySelector('img[alt]');
      const alt = img ? clean(img.getAttribute('alt')).replace(/\s*(?:company\s+)?logo$/i, '') : '';
      if (alt && alt.length <= 150 && !isMeta(alt)) return alt;
    }
    const emp = lines.map((l) => EMP_LINE.exec(l)).find(Boolean);
    if (emp) return clean(emp[1]);
    return clean(String(group.links[0].innerText || group.links[0].textContent || '').split('\n')[0]);
  }
  /** The job title in that entry: the line right above its dates, skipping "· Full-time" and
   *  duration lines and the company's own name. */
  function groupTitle(lines, company) {
    const k = lines.findIndex((l) => RANGE.test(l));
    for (let i = k - 1; i >= 0; i--) {
      const l = lines[i];
      if (isMeta(l) || EMP_LINE.test(l) || l === company || l.length > 150) continue;
      return l;
    }
    return '';
  }

  function readProfile() {
    const groups = companyGroups();
    const first = groups[0];
    let company = '', title = '', slug = '', why = '';
    if (!first) {
      why = 'no company link on the Experience page ';
    } else {
      const part = textBetween(first.links[0], groups[1] ? groups[1].links[0] : null);
      const ls = [];
      part.split('\n').map(clean).filter(Boolean).forEach((l) => { if (ls[ls.length - 1] !== l) ls.push(l); });
      const name = groupName(first, ls);
      if (!re(C.presentRegex).test(part)) why = 'latest company on the page has no "Present" (no current job shown) ';
      else { company = name; slug = first.slug; title = groupTitle(ls, name); }   // no name: the link still counts
    }
    if (!company && !slug) why += pageFacts();
    return { company, title: title.slice(0, 200), slug, why: why.slice(0, 290) };
  }

  function ready(step) {
    if (blocked()) return true;
    if (step === 'profile') return companyGroups().length > 0 && uniqLines(area()).some((l) => RANGE.test(l));
    if (step === 'search') {
      return companyLinks().size > 0 || re(S.noResultsRegex).test(clean(document.body ? document.body.innerText : ''));
    }
    const ls = lines().map((l) => l.toLowerCase());
    return ls.some((l) => C.industryLabels.includes(l) || C.sizeLabels.includes(l));
  }

  async function run() {
    const task = await send({ type: 'companyTask' });
    if (!task || !task.ok) return;                      // not our tab: the person's own browsing
    const end = Date.now() + C.contentMs;
    let scrolls = 0;
    // Smaller page = more of it on screen, so parts drawn only when visible are drawn (as for results).
    if (task.step === 'profile') { try { document.documentElement.style.zoom = CFG.render.zoom; } catch (e) { /* ignore */ } }
    while (!ready(task.step) && Date.now() < end) {
      // The Experience section is drawn only when scrolled near: move down step by step.
      if (task.step === 'profile' && scrolls < 12) {
        scrolls += 1;
        try { window.scrollTo(0, (document.body.scrollHeight * scrolls) / 12); } catch (e) { /* ignore */ }
      }
      await sleep(500);
    }
    const stop = blocked();
    if (stop) return send({ type: 'companyBlocked', text: stop });
    if (task.step === 'profile') {
      const got = readProfile();
      return send({ type: 'companyResult', step: 'profile', company: got.company, title: got.title, slug: got.slug, why: got.why });
    }
    if (task.step === 'search') {
      const pick = pickCompany(task.name);
      return send({ type: 'companyResult', step: 'search', name: task.name, slug: pick.slug, why: pick.why });
    }
    const got = readAbout();
    return send({ type: 'companyResult', step: 'about', name: task.name, industry: got.industry, size: got.size,
                  page_name: got.name, website: got.website,
                  why: got.industry || got.size ? '' : 'no "Industry" / "Company size" on the About page' });
  }

  window.__HBF_COMPANY = { pickCompany, readAbout, readProfile, sameName };
  run().catch(() => {});
})();
