/* Hiring Badge Finder - runs on LinkedIn people-search result pages.
 *
 * Reads the result cards that LinkedIn already shows on screen. It opens no
 * profile pages and makes no requests to LinkedIn itself. Every wait goes
 * through the background worker (hidden tabs slow down their own timers) and
 * every wait has a timeout, so nothing can hang.
 */
(function () {
  'use strict';
  if (window.__hbfLoaded) return;
  window.__hbfLoaded = true;

  const CFG = self.HBF_CONFIG;
  const T = CFG.timeouts;

  // ------------------------------------------------------------ messaging
  function send(msg, timeoutMs) {
    return new Promise((resolve) => {
      let finished = false;
      const timer = setTimeout(() => {
        if (!finished) { finished = true; resolve({ ok: false, error: 'timeout' }); }
      }, timeoutMs || T.messageMs);
      try {
        chrome.runtime.sendMessage(msg, (res) => {
          if (finished) return;
          finished = true;
          clearTimeout(timer);
          if (chrome.runtime.lastError) resolve({ ok: false, error: chrome.runtime.lastError.message });
          else resolve(res || { ok: false, error: 'no answer' });
        });
      } catch (e) {
        finished = true; clearTimeout(timer);
        resolve({ ok: false, error: String(e && e.message || e) });
      }
    });
  }
  const sleep = (ms) => send({ type: 'sleep', ms }, ms + 5000);

  // ----------------------------------------------------------- card reading
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const re = (src, flags) => new RegExp(src, flags || 'i');

  // Block elements start a new text line, as the browser shows them.
  const BLOCK_TAGS = new Set(['DIV', 'P', 'LI', 'UL', 'OL', 'SECTION', 'ARTICLE', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6',
    'BUTTON', 'FIGURE', 'HEADER', 'FOOTER', 'MAIN', 'BR', 'TR', 'TABLE']);
  /** The element's text, one line per block, like innerText. Works for the drawn page AND for a results page
   *  read from LinkedIn's own HTML (not drawn, so innerText has no line breaks there). */
  function cardText(el) {
    if (!el) return '';
    if (el.ownerDocument === document) return el.innerText || '';
    const out = [];
    const walk = (n) => {
      if (n.nodeType === 3) { out.push(n.nodeValue); return; }
      if (n.nodeType !== 1 || /^(SCRIPT|STYLE|TEMPLATE|NOSCRIPT)$/.test(n.tagName)) return;
      const block = BLOCK_TAGS.has(n.tagName);
      if (block) out.push('\n');
      for (const c of n.childNodes) walk(c);
      if (block) out.push('\n');
    };
    walk(el);
    return out.join('').split('\n').map((x) => x.replace(/\s+/g, ' ').trim()).filter(Boolean).join('\n');
  }

  // '/in/<slug>' only: '/in/ann/opportunities/hiring-opportunities/' -> '/in/ann'.
  function profilePath(a) {
    try {
      const m = new URL(a.getAttribute('href') || a.href, location.href).pathname.match(/^\/in\/([^/]+)/i);
      return m ? '/in/' + m[1].toLowerCase() : '';
    } catch (e) { return ''; }
  }
  function distinctProfiles(el) {
    const set = new Set();
    el.querySelectorAll(CFG.profileLink).forEach((a) => { const p = profilePath(a); if (p) set.add(p); });
    return set;
  }

  /** Any layout: the element whose direct children hold the most DIFFERENT people is the
   *  results list; those children are the cards. Mutual-connection avatar groups hold only
   *  2-3 people, so the real list (about 10) wins. Needs no LinkedIn markers. */
  function repeatedList(rootEl) {
    const kidsOf = new Map();                       // parent -> its children that contain a person link
    rootEl.querySelectorAll(CFG.profileLink).forEach((a) => {
      if (!profilePath(a) || a.closest('header, nav, aside, footer')) return;   // not page chrome or sidebars
      for (let el = a; el.parentElement && el.parentElement !== document.documentElement; el = el.parentElement) {
        const par = el.parentElement;
        if (!kidsOf.has(par)) kidsOf.set(par, new Set());
        kidsOf.get(par).add(el);
      }
    });
    let best = [];
    let bestPeople = 2;                             // a results list shows at least 3 people
    kidsOf.forEach((kids) => {
      const items = Array.from(kids);
      const people = new Set(items.map((k) => Array.from(k.querySelectorAll(CFG.profileLink)).map(profilePath).find(Boolean)));
      // every result card shows a connection degree ("• 2nd", "3rd+")
      const allCards = items.every((k) => /\b(1st|2nd|3rd\+?)\b/.test(cardText(k)));
      if (allCards && people.size === items.length && people.size > bestPeople) { bestPeople = people.size; best = items; }
    });
    return best;
  }

  // Which LinkedIn page design the last findCards() read: 'current' or 'older'.
  // The older design ignores the "Actively hiring" filter and only draws results while the tab is visible.
  let lastLayout = 'current';
  // How many DIFFERENT people the results list itself holds, counted from the main card marker only
  // (the last findCards() call). The fallback finders can add people who are only MENTIONED on the page
  // (a mutual connection or an "X is hiring" line with a linked name), which must not hide a cut-off page.
  let lastPrimaryPeople = 0;

  /** Find one container element per person. */
  /** Different people among cards (by each card's first person link). */
  const peopleIn = (els) => new Set(els.map((c) => Array.from(c.querySelectorAll(CFG.profileLink))
    .map(profilePath).find(Boolean)).filter(Boolean)).size;

  /** Layout-free cards: for every person whose NAME is a link (photo-only links, e.g. mutual connections,
   *  are not people of their own), the largest block around it that holds no other person. Works on any
   *  page design, whatever wraps a result. */
  function climbCards(rootEl, namedOnly) {
    const cards = new Map(); // element -> profile path
    rootEl.querySelectorAll(CFG.profileLink).forEach((a) => {
      const p = profilePath(a);
      if (!p || a.closest('header, nav, [role="navigation"], [role="banner"], aside, footer')) return;
      if (namedOnly && !clean(cardText(a))) return;
      let el = a;
      while (el.parentElement && el.parentElement !== document.body && el.parentElement !== rootEl &&
             distinctProfiles(el.parentElement).size <= 1) {
        el = el.parentElement;
      }
      if (!cards.has(el)) cards.set(el, p);
    });
    // one card per person (keeps the largest container when two overlap)
    const byPerson = new Map();
    cards.forEach((p, el) => {
      const prev = byPerson.get(p);
      if (!prev || el.contains(prev)) byPerson.set(p, el);
    });
    return Array.from(byPerson.values());
  }

  /** Find one container element per person. */
  function findCards(rootEl) {
    rootEl = rootEl || document;
    lastPrimaryPeople = 0;
    if (CFG.cardSelector) {
      const pick = (sel, needImage) => {
        let found = [];
        try { found = Array.from(rootEl.querySelectorAll(sel)); } catch (e) { return []; }
        found = found.filter((c) => distinctProfiles(c).size >= 1 && (!needImage || c.querySelector('img')));
        // a list item nested inside another card (e.g. a mutual-connections list) is not a person card
        return found.filter((c) => !found.some((o) => o !== c && o.contains(c)));
      };
      const cards = pick(CFG.cardSelector, false);
      lastPrimaryPeople = peopleIn(cards);
      lastLayout = 'current';
      // Fewer than a page of different people from the usual markers: the results may be drawn another way
      // for this account (seen: a box of 2 people; and LinkedIn's AI search, whose listitems are small parts
      // INSIDE one person's card). Then the finder that sees the MOST different people is the results list.
      if (cards.length && peopleIn(cards) < CFG.pacing.fullPage) {
        // A real repeated results list with a full page of people wins; the link-based cards only when
        // nothing else finds enough (they would also take people from small boxes beside the results).
        const list = repeatedList(rootEl);
        if (peopleIn(list) >= CFG.pacing.fullPage && peopleIn(list) > peopleIn(cards)) return list;
        let best = peopleIn(list) > peopleIn(cards) ? list : cards;
        const climbed = climbCards(rootEl, true);
        if (peopleIn(climbed) > peopleIn(best)) best = climbed;
        if (best !== cards) return best;
      }
      if (cards.length) return cards;
      lastLayout = 'older';             // whatever is found below is LinkedIn's older page design
      // Older LinkedIn layout: only when the main marker finds nothing, and only real lists (2+ people).
      for (const sel of CFG.fallbackCardSelectors || []) {
        const alt = pick(sel, true);
        if (alt.length >= 2) return alt;
      }
      return repeatedList(rootEl);
    }
    return climbCards(rootEl, false);
  }

  function firstText(card, selectors) {
    for (const sel of selectors || []) {
      let el = null;
      try { el = card.querySelector(sel); } catch (e) { /* bad selector in config */ }
      const t = el ? clean(el.textContent) : '';
      if (t) return t;
    }
    return '';
  }

  // LinkedIn repeats names for screen readers ("Ann Lee Ann Lee"); keep one copy.
  function dedupeRepeat(s) {
    const t = clean(s);
    const n = t.length;
    if (n < 2) return t;
    // "Ann LeeAnn Lee" (no separator)
    if (n % 2 === 0 && t.slice(0, n / 2) === t.slice(n / 2)) return t.slice(0, n / 2);
    // "Ann Lee Ann Lee" (one space between the copies)
    if (n % 2 === 1) {
      const half = (n - 1) / 2;
      if (t[half] === ' ' && t.slice(0, half) === t.slice(half + 1)) return t.slice(0, half);
    }
    return t;
  }

  const DEGREE_RE = /^([•·]\s*)?(1st|2nd|3rd\+?|3rd)$/i;
  const DEGREE_SUFFIX_RE = /\s*[•·]\s*(1st|2nd|3rd\+?)\s*$/i;
  // Real line order: name, "• 2nd", headline, location, action button, summary, ...
  // The action button text is dropped here so it can never become the location.
  const NOISE_RE = /^(connect|follow|message|pending|see open roles|view .* profile|•|\.{3}|more)$/i;
  // Screen-reader-only lines in the older layout ("2nd degree connection", "Status is offline").
  const SR_NOISE_RE = /^((1st|2nd|3rd\+?) degree connection|status is \w+)$/i;

  const ACTION_RE = /^(connect|follow|message|pending)$/i;
  // "City, State, Country" or "Greater X Area": used only when one line sits before the button
  const LOCATION_LIKE_RE = /^[^|@#•:]{2,60},\s*[^|@#•:]{2,60}$|\b(area|region)$/i;

  /** Name / headline / location from the card's text lines when no selectors are set.
   *  Headline and location are the lines before the action button; anything after it
   *  (summary, mutual connections, followers) is never used, so a missing line stays empty. */
  function readFromLines(card, name) {
    const lines = cardText(card).split('\n').map(clean).filter(Boolean);
    let degree = '';
    let sawAction = false;
    const rest = [];
    for (const line of lines) {
      if (DEGREE_RE.test(line)) { degree = line.replace(/[•·]/, '').trim(); continue; }
      const suffix = line.match(DEGREE_SUFFIX_RE);   // "Ann Lee • 2nd" on one line
      if (suffix && dedupeRepeat(line.slice(0, suffix.index).trim()) === name) { degree = suffix[1]; continue; }
      if (ACTION_RE.test(line)) { sawAction = true; break; }
      if (NOISE_RE.test(line) || SR_NOISE_RE.test(line)) continue;
      if (/^linkedin member$/i.test(line)) continue;   // a private profile's stand-in name, not a headline
      if (dedupeRepeat(line) === name || line === name) continue;
      rest.push(line);
    }
    if (sawAction && rest.length === 1 && LOCATION_LIKE_RE.test(rest[0])) {
      return { degree, headline: '', location: rest[0] };      // no headline line
    }
    return { degree, headline: rest[0] || '', location: rest[1] || '' };
  }

  function hasBadge(card) {
    const B = CFG.badge;
    const textRe = re(B.attrTextRegex);
    const classSrcRe = re(B.classOrSrcRegex);
    // Only the person's own photo counts: the first photo that is not inside a link to
    // someone else (a mutual connection's avatar).
    const own = Array.from(card.querySelectorAll(CFG.profileLink)).map(profilePath).find(Boolean) || '';
    let photo = null;
    try {
      photo = CFG.photo ? Array.from(card.querySelectorAll(CFG.photo)).find((img) => {
        const a = img.closest('a[href]');
        return !a || !card.contains(a) || !profilePath(a) || profilePath(a) === own;
      }) || null : null;
    } catch (e) { /* bad selector */ }
    if (photo) {
      if ((B.selectors || []).some((sel) => { try { return photo.matches(sel); } catch (e) { return false; } })) return true;
      if (classSrcRe.test(photo.getAttribute('src') || '') || classSrcRe.test(photo.getAttribute('class') || '')) return true;
      // LinkedIn's own HTML (read without drawing) may carry the photo address in srcset or a data-* attribute
      if (Array.from(photo.attributes).some((at) => classSrcRe.test(at.value || ''))) return true;
      return (B.attrs || []).some((a) => textRe.test(photo.getAttribute(a) || ''));
    }
    // No own photo: check the card's elements, skipping anything inside a link to
    // another person (mutual-connection avatars).
    const others = (el) => { const a = el.closest('a[href]'); return !!a && card.contains(a) && !!profilePath(a) && profilePath(a) !== own; };
    const all = [card].concat(Array.from(card.querySelectorAll('*'))).filter((el) => !others(el));
    for (const sel of B.selectors || []) {
      try { if (all.some((el) => el.matches(sel))) return true; } catch (e) { /* bad selector */ }
    }
    for (const el of all) {
      for (const a of B.attrs || []) {
        const v = el.getAttribute && el.getAttribute(a);
        if (v && textRe.test(v)) return true;
      }
      const cls = typeof el.className === 'string' ? el.className : (el.getAttribute && el.getAttribute('class')) || '';
      if (cls && classSrcRe.test(cls)) return true;
      if (el.tagName === 'IMG') {
        const src = el.getAttribute('src') || '';
        if (src && classSrcRe.test(src)) return true;
      }
    }
    return false;
  }

  function hasHiringLink(card) {
    const hrefRe = CFG.openRolesHrefRegex ? re(CFG.openRolesHrefRegex) : null;
    return !!hrefRe && Array.from(card.querySelectorAll('a[href]')).some((a) => hrefRe.test(a.getAttribute('href')));
  }

  function hasOpenRoles(card) {
    if (hasHiringLink(card)) return true;
    return re(CFG.openRolesRegex).test(cardText(card)) ||
      Array.from(card.querySelectorAll('a,button')).some((el) => re(CFG.openRolesRegex).test(el.textContent || ''));
  }

  function readCard(card) {
    const links = Array.from(card.querySelectorAll(CFG.profileLink));
    const profilePathName = links.map(profilePath).find(Boolean) || '';
    const profile_url = profilePathName ? 'https://www.linkedin.com' + profilePathName : '';

    // The first /in/ link is often the photo (no text), so take the first link to the
    // same person that has text (mutual-connection links point to other people).
    let name = dedupeRepeat(firstText(card, CFG.name));
    if (!name) {
      for (const l of links.filter((x) => profilePath(x) === profilePathName)) {
        const first = cardText(l).split('\n').map(clean)
          .find((x) => x && !SR_NOISE_RE.test(x) && !NOISE_RE.test(x));
        const t = dedupeRepeat(first || '');
        if (t) { name = t; break; }
      }
    }
    // "Ann Lee • 2nd" (name and degree on one line): keep the name, use the degree.
    const deg = name.match(DEGREE_SUFFIX_RE);
    if (deg) name = name.slice(0, deg.index).trim();
    const fromLines = readFromLines(card, name);
    const headline = firstText(card, CFG.headline) || fromLines.headline;
    const location_ = firstText(card, CFG.location) || fromLines.location;
    const degree = firstText(card, CFG.degree) || fromLines.degree || (deg ? deg[1] : '');

    // Badge = framed photo AND hiring link; a framed photo without it (another frame
    // type) is skipped: neither a badge nor "open roles".
    const framed = hasBadge(card);
    const badge = framed && (!CFG.badge.requireHiringLink || hasHiringLink(card));
    const framedSkipped = framed && !badge;

    return {
      name,
      profile_url,
      headline,
      location: location_,
      degree,
      badge_frame: badge,
      open_roles: framedSkipped ? false : hasOpenRoles(card),
      framed_skipped: framedSkipped,      // removed before sending (server schema unchanged)
      card_text: clean(cardText(card)).slice(0, 1500),
      current: readCurrent(card),
    };
  }

  /** "Current: Talent Partner at Acme" -> "Talent Partner at Acme"; '' when the card has no such line. */
  function readCurrent(card) {
    const rx = re(CFG.currentLineRegex);
    for (const line of cardText(card).split('\n')) {
      const m = clean(line).match(rx);
      if (m) return m[1].slice(0, 300);
    }
    return '';
  }

  /** People to send, plus how many framed photos were skipped for having no hiring link. */
  function readAll(rootEl) {
    const cards = findCards(rootEl);
    const once = new Set();
    const people = cards.map(readCard).filter((p) => p.profile_url && !once.has(p.profile_url) && once.add(p.profile_url));
    const skipped = people.filter((p) => p.framed_skipped).length;
    people.forEach((p) => { delete p.framed_skipped; });
    // cardCount includes private "LinkedIn Member" cards; primaryPeople counts only the results list's own people
    return { people, skipped, cardCount: cards.length, primaryPeople: lastPrimaryPeople };
  }

  // ------------------------------------------- LinkedIn's page data (React Server Components stream)
  // LinkedIn's newer pages are built with React Server Components: the server sends the page as React's own
  // standard stream of rows ("1fb:["$","div",null,{...props, children}]"), and the browser draws it. A
  // background tab draws only part of it, but the stream holds the whole page. It is turned back into the
  // page here, exactly as the browser would draw it, and read with the SAME card rules as a drawn page.
  // Nothing below depends on LinkedIn's markup, only on React's stream format, so LinkedIn's redesigns
  // change this rebuilt page just as they change the drawn page.
  const REF_RE = /^\$[L@]?([0-9a-fA-F]{1,8})$/;

  /** The stream text inside the page's scripts (raw, and inside escaped JavaScript strings). */
  function streamTexts(doc) {
    const texts = [];
    for (const s of doc.querySelectorAll('script')) {
      const t = s.textContent || '';
      if (t.includes('["$"')) texts.push(t);
      if (!t.includes('\\"$\\"')) continue;
      for (const m of t.matchAll(/"((?:[^"\\]|\\[\s\S])*)"/g)) {
        if (m[1].length > 20 && m[1].includes('\\"$\\"')) { try { texts.push(JSON.parse('"' + m[1] + '"')); } catch (e) { /* not a string */ } }
      }
    }
    return texts;
  }

  /** Stream rows by id: "<hex id>:<JSON>" (module, text and hint rows are skipped). */
  function streamRows(texts) {
    const rows = new Map();
    for (const t of texts) {
      const starts = Array.from(t.matchAll(/(?:^|\n)([0-9a-fA-F]{1,8}):(?=[[{])/g));
      starts.forEach((m, i) => {
        const id = m[1];
        if (rows.has(id)) return;
        const from = m.index + m[0].length;
        let body = t.slice(from, i + 1 < starts.length ? starts[i + 1].index : t.length).trim();
        try { rows.set(id, JSON.parse(body)); return; } catch (e) { /* trailing text: trim below */ }
        body = body.slice(0, body.lastIndexOf(body[0] === '[' ? ']' : '}') + 1);
        try { rows.set(id, JSON.parse(body)); } catch (e) { /* not a JSON row */ }
      });
    }
    return rows;
  }

  const TAG_OK = /^[a-z][a-z0-9]*$/;
  const ATTR_OK = /^(href|role|alt|src|srcset|id|title|class|aria-[a-z-]+|data-[a-z0-9-]+)$/;
  const isElement = (v) => Array.isArray(v) && v[0] === '$' && typeof v[1] === 'string';

  /** The stream turned into elements (inside a box that is never shown). */
  function buildFromStream(rows, doc) {
    const referenced = new Set();
    const scan = (v, d) => {
      if (v == null || d > 200) return;
      if (typeof v === 'string') { const m = v.match(REF_RE); if (m) referenced.add(m[1]); return; }
      if (typeof v === 'object') for (const x of Object.values(v)) scan(x, d + 1);
    };
    rows.forEach((v) => scan(v, 0));
    const box = doc.createElement('div');
    let budget = 300000;                                      // never runs away on a huge page
    const imageOf = (props) => {                              // LinkedIn's image component: { rootUrl, imageRenditions }
      const seen = [props];
      for (let i = 0; i < seen.length && i < 40; i++) {
        const o = seen[i];
        if (o && typeof o.rootUrl === 'string') {
          const r = Array.isArray(o.imageRenditions) && o.imageRenditions[0];
          return o.rootUrl + (r && typeof r.suffixUrl === 'string' ? r.suffixUrl : '');
        }
        if (o && typeof o === 'object' && !isElement(o)) for (const x of Object.values(o)) if (x && typeof x === 'object') seen.push(x);
      }
      return '';
    };
    const build = (v, parent, depth, path) => {
      if (--budget < 0 || depth > 120 || v == null || typeof v === 'boolean') return;
      if (typeof v === 'number') { parent.appendChild(doc.createTextNode(String(v))); return; }
      if (typeof v === 'string') {
        const m = v.match(REF_RE);
        if (m) { if (!path.has(m[1]) && rows.has(m[1])) build(rows.get(m[1]), parent, depth + 1, new Set(path).add(m[1])); return; }
        if (/^\$[A-Za-z]/.test(v) && v.length < 16) return;                       // other stream markers ($undefined ...)
        parent.appendChild(doc.createTextNode(v.startsWith('$$') ? v.slice(1) : v));
        return;
      }
      if (!Array.isArray(v)) { if (typeof v === 'object' && 'children' in v) build(v.children, parent, depth + 1, path); return; }
      if (!isElement(v)) { v.forEach((x) => build(x, parent, depth + 1, path)); return; }
      const type = v[1];
      const props = v[3] && typeof v[3] === 'object' && !Array.isArray(v[3]) ? v[3] : {};
      const html = TAG_OK.test(type);
      // A component is drawn as what it contains; one with an address is a link.
      const el = doc.createElement(html ? type : (typeof props.href === 'string' ? 'a' : 'div'));
      for (const [k, val] of Object.entries(props)) {
        if (typeof val !== 'string' && typeof val !== 'number') continue;
        const name = k === 'className' ? 'class' : k;
        if (ATTR_OK.test(name) && !/^\s*javascript:/i.test(String(val))) { try { el.setAttribute(name, String(val)); } catch (e) { /* bad name */ } }
      }
      if (!html) {
        const src = imageOf(props);
        if (src) { const f = doc.createElement('figure'); const img = doc.createElement('img'); img.setAttribute('src', src); f.appendChild(img); el.appendChild(f); }
        // a component's other element-valued props (slots such as title / subtitle) are part of what it draws
        for (const [k, val] of Object.entries(props)) {
          if (k === 'children' || val == null || typeof val !== 'object' && !(typeof val === 'string' && REF_RE.test(val))) continue;
          if (isElement(val) || (Array.isArray(val) && val.some((x) => isElement(x) || (typeof x === 'string' && REF_RE.test(x)))) ||
              (typeof val === 'string' && REF_RE.test(val))) build(val, el, depth + 1, path);
        }
      }
      if ('children' in props) build(props.children, el, depth + 1, path);
      parent.appendChild(el);
    };
    rows.forEach((v, id) => { if (!referenced.has(id) && isElement(v)) build(v, box, 0, new Set([id])); });
    return box;
  }

  let lastServed = null;          // what the served page gave (counts only), for the page report

  /** This results page exactly as LinkedIn's server sends it (one request for the same address, with the
   *  person's own session), read with the same card rules: first its HTML, then its React stream.
   *  null when it cannot be read. */
  async function readServedPage(page) {
    lastServed = { status: 'not read' };
    try {
      const res = await fetch(location.href, { credentials: 'include', headers: { accept: 'text/html,application/xhtml+xml' } });
      if (!res.ok || !/\/search\/results\/people/.test(res.url)) {                // redirected (sign-in, check): not results
        lastServed = { status: 'http ' + res.status };
        return null;
      }
      const doc = new DOMParser().parseFromString(await res.text(), 'text/html');
      let best = readAll(doc);
      lastServed = { status: 'ok', htmlCards: best.cardCount, streamRows: 0, streamCards: 0 };
      if (best.cardCount < expectedOnPage(page)) {
        const rows = streamRows(streamTexts(doc));
        lastServed.streamRows = rows.size;
        if (rows.size) {
          const fromStream = readAll(buildFromStream(rows, doc));
          lastServed.streamCards = fromStream.cardCount;
          if (fromStream.cardCount > best.cardCount) best = fromStream;
        }
      }
      return best;
    } catch (e) {
      lastServed = { status: 'error ' + String(e && e.message || e).slice(0, 80) };
      return null;
    }
  }

  /** The page's STRUCTURE only, for fixing a layout the tool reads badly: element names, roles,
   *  class names and counts. No names, headlines, links or other personal text. */
  async function pageStructure(cardCount, total) {
    const shape = (el) => !el ? '' : [el.tagName.toLowerCase(), el.getAttribute('role') ? 'role=' + el.getAttribute('role') : '',
      el.getAttribute('data-view-name') ? 'view=' + el.getAttribute('data-view-name') : '',
      el.getAttribute('data-testid') ? 'testid=' + el.getAttribute('data-testid') : '',
      clean(typeof el.className === 'string' ? el.className : '').slice(0, 80)].filter(Boolean).join(' ');
    const people = new Set(Array.from(document.querySelectorAll(CFG.profileLink)).map(profilePath).filter(Boolean));
    // Where the person links sit: the chain of parents above the first few (shapes only).
    const chains = Array.from(document.querySelectorAll(CFG.profileLink)).filter((a) => profilePath(a)).slice(0, 4).map((a) => {
      const out = [];
      for (let el = a.parentElement, k = 0; el && el !== document.body && k < 9; el = el.parentElement, k++) out.push(shape(el));
      return out;
    });
    const buttons = Array.from(document.querySelectorAll('button, a')).filter((el) =>
      /next|page|more|show/i.test((el.getAttribute('aria-label') || '') + ' ' + clean(el.textContent).slice(0, 30)))
      .slice(0, 12).map((el) => shape(el) + ' | ' + clean(el.getAttribute('aria-label') || el.textContent).slice(0, 30) +
        (el.disabled || el.getAttribute('aria-disabled') === 'true' ? ' (disabled)' : ''));
    // Nothing is requested from LinkedIn here (the page script never loads anything itself).
    return {
      ext: chrome.runtime.getManifest().version, visibility: document.visibilityState, total,
      cardsRead: cardCount, peopleLinks: people.size,
      listitems: document.querySelectorAll('[role="listitem"]').length, lis: document.querySelectorAll('li').length,
      genericList: repeatedList(document).length, layout: lastLayout,
      scrollHeight: document.documentElement.scrollHeight, innerHeight: window.innerHeight,
      cardShapes: findCards().slice(0, 3).map((c) => shape(c) + ' < ' + shape(c.parentElement)),
      chains, buttons,
    };
  }

  // -------------------------------------------------------------- page state
  function pageState() {
    const S = CFG.states;
    const path = location.pathname;
    if (re(S.checkpointPathRegex).test(path)) return { kind: 'limit', text: 'LinkedIn is asking for a security check.' };
    if (re(S.signInPathRegex).test(path) || document.querySelector('input#username, form.login__form')) {
      return { kind: 'signin', text: 'You are not signed in to LinkedIn in this browser.' };
    }
    const body = clean(document.body ? document.body.innerText : '').slice(0, 20000);
    if (re(S.commercialLimitRegex).test(body)) {
      return { kind: 'limit', text: 'LinkedIn shows the free-account "commercial use limit". Try again next month or use another account.' };
    }
    if (re(S.limitRegex).test(body)) {
      return { kind: 'limit', text: 'LinkedIn is showing a limit or "unusual activity" notice.' };
    }
    if (re(S.noResultsRegex).test(body) && findCards().length === 0) {
      return { kind: 'empty', text: 'LinkedIn found no results.' };
    }
    return { kind: 'ok', text: '' };
  }

  function findNext() {
    for (const sel of CFG.next.selectors) {
      let el = null;
      try { el = document.querySelector(sel); } catch (e) { /* bad selector */ }
      if (el) return el;
    }
    const textRe = re(CFG.next.textRegex);
    return Array.from(document.querySelectorAll('button,a')).find((el) => textRe.test(clean(el.textContent))) || null;
  }
  function nextEnabled(el) {
    return !!el && !el.disabled && el.getAttribute('aria-disabled') !== 'true';
  }

  /** LinkedIn's own result count ("About 1,234 results"), or null when the page does not show it. */
  function resultsTotal() {
    const m = clean(document.querySelector('main') ? document.querySelector('main').innerText : document.body.innerText)
      .slice(0, 5000).match(/(?:about\s+)?([\d,.]+)\s*\+?\s+results?\b/i);
    if (!m) return null;
    const n = Number(m[1].replace(/[,.]/g, ''));
    return Number.isFinite(n) ? n : null;
  }

  /** People this page should show (10, fewer on the last page), from LinkedIn's result count. */
  function expectedOnPage(page) {
    const total = resultsTotal();
    return total == null ? 10 : Math.max(0, Math.min(10, total - (page - 1) * 10));
  }

  /** Scroll every scrolling box on the page (not the page itself) to its end, so lists inside them load more. */
  function scrollBoxesToEnd() {
    const boxes = [];
    for (const el of document.querySelectorAll('main, main *, [data-testid="lazy-column"], [data-testid="lazy-column"] *, body > div, body > div *')) {
      if (boxes.length >= 40) break;
      if (el.scrollHeight <= el.clientHeight + 20) continue;
      const oy = getComputedStyle(el).overflowY;
      if (oy === 'auto' || oy === 'scroll' || oy === 'overlay') boxes.push(el);
    }
    for (const el of boxes) {
      try {
        el.scrollTop = el.scrollHeight;
        el.dispatchEvent(new Event('scroll'));            // LinkedIn's own listeners load the next people
      } catch (e) { /* not scrollable after all */ }
    }
    return boxes.length;
  }

  // ------------------------------------------------------- render all cards
  async function renderAllCards(page, patience) {
    const R = CFG.render;
    const patient = R.patientChecks * (patience || 1);
    const want = expectedOnPage(page || 1);
    document.documentElement.style.zoom = R.zoom;     // smaller page = every card is on screen
    try {
      // Stop when the count has not grown for two checks in a row (one quiet check can just mean the
      // page is slow to add more cards). While fewer cards than the page should have are drawn
      // (some accounts / browsers draw the rest of a hidden tab slowly), keep scrolling much longer.
      let last = -1;
      let steady = 0;
      for (let i = 0; i < R.scrollTries + R.extraTries * (patience || 1); i++) {
        const cards = findCards();
        if (cards.length && cards.length === last) {
          steady += 1;
          if (steady >= 2 && cards.length >= want) break;
          if (steady >= patient) break;
          if (i >= R.scrollTries && cards.length >= want) break;
        } else {
          steady = 0;
        }
        last = cards.length;
        const tail = cards[cards.length - 1];
        if (tail) tail.scrollIntoView({ block: 'end' });
        window.scrollTo(0, document.documentElement.scrollHeight);
        // Newer LinkedIn pages are one window tall: the results sit in their OWN scrolling box, and more
        // people load only when that box is scrolled down (what a person does with the mouse wheel).
        scrollBoxesToEnd();
        await sleep(R.scrollGapMs);
      }
    } finally {
      document.documentElement.style.zoom = '';
      window.scrollTo(0, 0);
    }
  }

  // ------------------------------------------------- location set-up tab
  // Opened by the dashboard for a city that has no LinkedIn location ID yet. Nothing is clicked or
  // typed on LinkedIn. 1) Read the page LinkedIn already shows: its embedded data may name the city
  // with its location ID (urn:li:geo:<id> / urn:li:fs_geo:<id>). Exactly one ID next to the city's
  // name -> saved, done. 2) Otherwise show a banner telling the person what to click; the background
  // worker saves the ID when the address gets &geoUrn= after they pick the city.
  const cityName = (place) => String(place || '').split(',')[0].trim();

  /** Location IDs written next to the city's name in the page source (closest name wins). */
  function findPlaceIds(html, place) {
    const city = cityName(place).toLowerCase();
    if (!city) return [];
    const ids = new Set();
    const re = /urn:li:(?:fs_|fsd_)?geo:(\d{3,15})/g;
    let m;
    // a "name"/"text"/"title" value that is the city, e.g. "text":"Mumbai, Maharashtra, India"
    const named = new RegExp('"(?:text|name|title|defaultlocalizedname|displayname|localizedname)"\\s*:\\s*(?:\\{[^}]*?"text"\\s*:\\s*)?"' +
      city.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '(?:,[^"]*)?"');
    const hits = [];
    while ((m = re.exec(html))) hits.push({ id: m[1], at: m.index, end: m.index + m[0].length });
    hits.forEach((h, i) => {
      // only this record: from this ID up to the next ID (at most 300 characters)
      const stop = Math.min(i + 1 < hits.length ? hits[i + 1].at : html.length, h.end + 300);
      if (named.test(html.slice(h.end, stop).toLowerCase())) ids.add(h.id);
    });
    return [...ids];
  }

  function showSetUpBanner(place, problem, working) {
    const old = document.getElementById('hbf-setup-banner');
    if (old) old.remove();
    const b = document.createElement('div');
    b.id = 'hbf-setup-banner';
    b.setAttribute('role', 'status');
    b.style.cssText = 'position:fixed;z-index:2147483647;left:50%;top:84px;transform:translateX(-50%);max-width:560px;' +
      'background:#5b3df5;color:#fff;font:15px/1.5 system-ui,Segoe UI,sans-serif;padding:16px 20px;border-radius:12px;' +
      'box-shadow:0 18px 40px -12px rgba(27,26,46,.55)';
    const title = document.createElement('b'); title.textContent = 'Hiring Badge Finder: one quick step for ' + cityName(place);
    title.style.cssText = 'display:block;font-size:16px;margin-bottom:6px';
    const steps = document.createElement('div');
    steps.textContent = working ||
      ((problem ? 'Could not do it automatically (' + problem + '). Please do it once: ' : '') +
       '1. Click "Locations" in the filter bar below.  2. Untick anything else and choose "' + place +
       '".  3. Click "Show results".  This tab then closes and your search starts by itself. Needed only once for this city.');
    b.append(title, steps);
    document.documentElement.appendChild(b);
  }

  // ---- Automatic set-up of one place (hidden set-up tab; this tab reads NO people).
  // Markers verified on a real LinkedIn page (Nov 2026, narrow layout):
  //   Locations chip  : div[role="button"][aria-expanded] wrapping div[aria-label="Filter by Locations"]
  //   location box    : [popover] input[placeholder="Add a location"]  (never input[data-testid="typeahead-input"])
  //   options         : [role="listbox"][data-testid="typeahead-results-container"] > [role="option"]
  //   chosen place    : [popover] input[type="checkbox"]:checked with aria-label = the place
  //   Show results    : an <a> in the popover; afterwards the address gains geoUrn=["<id>"]
  const visible = (el) => !!el && el.getClientRects().length > 0;
  const textOf = (el) => (el.textContent || '').replace(/\s+/g, ' ').trim();
  const normName = (t) => String(t || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const pause = (min, max) => sleep(min + Math.random() * (max - min));
  async function waitForEl(find, ms) {
    const end = Date.now() + ms;
    for (;;) {
      const el = find();
      if (el) return el;
      if (Date.now() > end) return null;
      await sleep(300);
    }
  }
  function clickLikeAPerson(el) {
    ['pointerdown', 'mousedown', 'pointerup', 'mouseup'].forEach((t) =>
      el.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window })));
    el.click();
  }
  function typeInto(input, text) {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    setter.call(input, text);                                  // works with LinkedIn's React inputs
    const key = text.slice(-1);
    input.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key }));
    input.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: key }));
    input.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key }));
  }
  /** One character through the browser's own editing command (the page sees a genuine input event);
   *  falls back to setting the value if the browser refuses, e.g. the box could not take focus. */
  let typingWay = 'none';
  function typeChar(input, typed, ch) {
    let ok = false;
    try { ok = document.activeElement === input && document.execCommand('insertText', false, ch) && input.value === typed; } catch (e) { ok = false; }
    if (ok) { typingWay = 'browser'; return; }
    typingWay = 'simulated';
    typeInto(input, typed);
  }
  function clearBox(input) {
    try { if (document.activeElement === input) { input.select(); document.execCommand('delete'); } } catch (e) { /* below */ }
    if (input.value) typeInto(input, '');
  }
  const chipByLabel = (label) => {
    const inner = document.querySelector('div[aria-label="' + label + '"]');
    return inner ? inner.closest('[role="button"][aria-expanded]') : null;
  };
  const locationsChip = () => chipByLabel('Filter by Locations');
  const checkedPlaces = () => Array.from(document.querySelectorAll('[popover] input[type="checkbox"]:checked'))
    .map((c) => c.getAttribute('aria-label') || '');
  // Any listed suggestion counts: the pick still needs an exact name, and the tick is verified after it.
  const placeOptions = () => {
    const all = Array.from(document.querySelectorAll('[role="listbox"] [role="option"]'));
    const shown = all.filter(visible);
    return shown.length ? shown : all;
  };
  // LinkedIn may keep other, closed copies of this box in the page: only the one on screen counts.
  const locationBoxes = () => Array.from(document.querySelectorAll('[popover] input[placeholder="Add a location"]'));
  const liveBox = () => locationBoxes().find(visible) || null;
  /** The box on screen; reopens the Locations filter if LinkedIn closed it. */
  async function openBox() {
    const open = liveBox();
    if (open) return open;
    const chip = locationsChip();
    if (!chip) return null;
    if (chip.getAttribute('aria-expanded') === 'true') {
      const late = await waitForEl(liveBox, 1500);
      if (late) return late;
      clickLikeAPerson(chip);                                  // open but empty: close it first
      await sleep(500);
    }
    clickLikeAPerson(locationsChip() || chip);
    return waitForEl(liveBox, 6000);
  }
  const blockedText = () => { const st = pageState(); return st.kind === 'signin' || st.kind === 'limit' ? st.text : ''; };

  /** Which option to take for what the user typed: an exact full-name match, or for a short name
   *  (no comma) the one option whose first part equals it. Anything else: none (never includes()). */
  function chooseOption(requested, texts) {
    const want = normName(requested);
    const full = texts.filter((t) => normName(t) === want);
    if (full.length === 1) return { pick: full[0] };
    if (full.length > 1) return { pick: null };
    if (requested.includes(',')) return { pick: null };
    const short = texts.filter((t) => normName(t.split(',')[0]) === want);
    return { pick: short.length === 1 ? short[0] : null };
  }

  /** LinkedIn's own location lookup, from this LinkedIn page. The session value is used only as this
   *  one request's header: never stored or sent anywhere else. An ID counts only if its name matches
   *  exactly (chooseOption); the first hit is never trusted. */
  async function lookupPlace(place) {
    const m = document.cookie.match(/JSESSIONID="?([^";]+)"?/);
    if (!m) return { note: 'no session value' };
    const q = encodeURIComponent(cityName(place));
    let res;
    for (const addr of ['/voyager/api/typeahead/hits?q=blended&query=' + q + '&type=GEO',
                        '/voyager/api/typeahead/hitsV2?keywords=' + q + '&origin=OTHER&q=type&type=GEO']) {
      try {
        res = await fetch(addr, {
          headers: { 'csrf-token': m[1], 'x-restli-protocol-version': '2.0.0', accept: 'application/vnd.linkedin.normalized+json+2.1' },
          credentials: 'include',
        });
      } catch (e) { return { note: 'request failed' }; }
      if (res.status !== 404 && res.status !== 400) break;      // only a wrong address tries the next one
    }
    if (res.status === 429 || res.status === 999 || /\/(checkpoint|authwall|login)/.test(new URL(res.url || location.href).pathname)) {
      return { blocked: 'LinkedIn refused the location lookup (status ' + res.status + '). Stopped; do not retry today.' };
    }
    if (!res.ok) return { note: 'status ' + res.status };
    let data;
    try { data = await res.json(); } catch (e) { return { note: 'unreadable answer' }; }
    const list = [].concat(data.elements || [], (data.data && data.data.elements) || []);
    const hits = list.map((el) => {
      const urn = String(el.targetUrn || el.entityUrn || el.objectUrn || '');
      const idm = urn.match(/geo:(\d+)$/i);
      const t = el.text && (el.text.text || el.text);
      return idm && typeof t === 'string' ? { id: idm[1], text: t } : null;
    }).filter(Boolean);
    const { pick } = chooseOption(place, hits.map((h) => h.text));
    if (!pick) return { note: hits.length + ' hits, no exact name' };
    return { id: hits.find((h) => h.text === pick).id, label: pick };
  }

  async function autoSetUp(place) {
    const result = (r) => send(Object.assign({ type: 'setupResult', place }, r));
    const fail = (reason, extra) => result(Object.assign({ ok: false, reason }, extra || {}));
    const gap = () => pause(800, 1800);
    let b = blockedText();
    if (b) return fail(b, { blocked: true });
    const looked = await lookupPlace(place);
    if (looked.blocked) return fail(looked.blocked, { blocked: true });
    if (looked.id) return result({ ok: true, id: looked.id, label: looked.label });
    let chip = await waitForEl(locationsChip, 15000);
    if (!chip) { b = blockedText(); return fail(b || 'the Locations filter was not found on the LinkedIn page', b ? { blocked: true } : {}); }
    await gap(); clickLikeAPerson(chip);
    chip = await waitForEl(() => { const c = locationsChip(); return c && c.getAttribute('aria-expanded') === 'true' ? c : null; }, 6000);
    if (!chip) return fail('the Locations filter did not open');
    let input = await waitForEl(liveBox, 6000);
    if (!input) return fail('the "Add a location" box was not found');
    const already = checkedPlaces();
    if (already.length) return fail('LinkedIn already has a place selected (' + already.join(', ') + '); nothing was changed');
    // A hidden tab may not fire real focus events, and suggestion lists often start from them.
    const focusBox = () => {
      clickLikeAPerson(input); window.focus(); input.focus();
      input.dispatchEvent(new FocusEvent('focus')); input.dispatchEvent(new FocusEvent('focusin', { bubbles: true }));
    };
    // Type like a person, then wait until the suggestions stop changing (up to 20 s).
    const typeAndWait = async (text) => {
      await sleep(400);                                // LinkedIn may redraw the box just after opening
      const box = await openBox();                     // always the box on screen right now
      if (!box) return false;
      input = box;
      focusBox(); clearBox(input); focusBox();
      let typed = '';
      for (const ch of text) { typed += ch; typeChar(input, typed, ch); await pause(90, 200); }
      let last = '', stable = 0;
      for (let i = 0; i < 66 && stable < 3; i++) {
        await sleep(300);
        const now = placeOptions().map(textOf).join('|');
        if (now && now === last) stable += 1; else stable = 0;
        last = now;
      }
      return placeOptions().length > 0;
    };
    await gap();
    const cityOnly = cityName(place);                  // the city alone; the option is still matched exactly
    let listed = await typeAndWait(cityOnly);
    if (!listed && cityOnly !== place) listed = await typeAndWait(place);
    if (!listed) {                                     // some pages only suggest in a visible tab: try once in front
      await send({ type: 'setupToFront' });
      await waitForEl(() => document.visibilityState === 'visible', 5000);
      await sleep(1000);
      listed = await typeAndWait(cityOnly);
    }
    const opts = placeOptions();
    const texts = opts.map(textOf);
    const { pick } = chooseOption(place, texts);
    if (!pick) {
      return fail(texts.length ? 'LinkedIn offered: ' + texts.join(' / ') + '. Type the full name exactly as shown.'
                               : 'LinkedIn offered no place for "' + place + '" [box="' + input.value + '", box in focus=' +
                                 (document.activeElement === input) + ', box still on page=' + input.isConnected +
                                 ', box on screen=' + visible(input) + ', boxes=' + locationBoxes().length +
                                 ', typing=' + typingWay + ', tab ' + document.visibilityState + ', lists=' +
                                 document.querySelectorAll('[role="listbox"]').length + ', options anywhere=' +
                                 document.querySelectorAll('[role="option"]').length + ', saw=' +
                                 Array.from(document.querySelectorAll('[role="option"]')).slice(0, 4).map((o) => textOf(o).slice(0, 50)).join(' / ') +
                                 ', lookup=' + looked.note + ']', { offered: texts });
    }
    const option = opts[texts.indexOf(pick)];
    await gap(); clickLikeAPerson(option);
    const ticked = await waitForEl(() => { const c = checkedPlaces(); return c.length ? c : null; }, 6000);
    if (!ticked || ticked.length !== 1 || normName(ticked[0]) !== normName(pick)) {
      return fail('LinkedIn shows ' + (ticked && ticked.length ? ticked.join(', ') : 'nothing') + ' selected instead of "' + pick + '"; nothing else was clicked');
    }
    const show = Array.from(document.querySelectorAll('[popover] a, [popover] button'))
      .find((a) => visible(a) && /^show\b/i.test(textOf(a)));
    if (!show) return fail('the "Show results" link was not found');
    await gap(); clickLikeAPerson(show);
    let id = '';
    for (let i = 0; i < 50 && !id; i++) {           // up to 15 s
      await sleep(300);
      try {
        const list = JSON.parse(new URL(location.href).searchParams.get('geoUrn') || '[]');
        if (Array.isArray(list) && list.length === 1 && /^\d+$/.test(String(list[0]))) id = String(list[0]);
      } catch (e) { /* not yet */ }
    }
    b = blockedText();
    if (b) return fail(b, { blocked: true });
    if (!id) return fail('LinkedIn did not apply the place (no location ID in the address after 15 s)');
    const shown = await waitForEl(() => Array.from(document.querySelectorAll('[role="button"][aria-expanded]'))
      .find((c) => normName(textOf(c)) === normName(pick)), 6000);
    if (!shown) return fail('the Locations filter does not show "' + pick + '" after applying it');
    return result({ ok: true, id, label: pick });
  }

  // ---- Manual set-up (fallback after an automatic failure): banner, the person picks the place.
  async function setUpPlace(place, problem) {
    showSetUpBanner(place, problem);
    for (let i = 0; i < 60; i++) {                 // keep looking for 2 minutes
      const ids = findPlaceIds(document.documentElement.innerHTML, place);
      if (ids.length === 1) { await send({ type: 'geoFound', id: ids[0] }); return; }
      await sleep(2000);
    }
  }

  // ---- Actively hiring through the chip, when LinkedIn dropped the address parameter (once).
  async function applyActivelyHiringByUi() {
    const chip = chipByLabel('Premium Actively hiring');
    if (!chip) return false;
    await pause(800, 1800); clickLikeAPerson(chip);
    const any = await waitForEl(() => Array.from(document.querySelectorAll('[popover] input[type="checkbox"]'))
      .find((c) => /^any job title$/i.test(c.getAttribute('aria-label') || '')), 6000);
    if (!any) return false;
    if (!any.checked) { await pause(800, 1800); clickLikeAPerson(any); }
    const show = Array.from(document.querySelectorAll('[popover] a, [popover] button'))
      .find((a) => visible(a) && /^show\b/i.test(textOf(a)));
    if (!show) return false;
    await pause(800, 1800); clickLikeAPerson(show);
    for (let i = 0; i < 50; i++) {
      await sleep(300);
      if (/activelyHiringForJobTitles=/.test(location.search)) return true;
    }
    return false;
  }

  // --------------------------------------------------------------- the loop
  let job = null;

  const api = (path, body, method) => send({ type: 'api', path, method: method || 'POST', body }, 30000);
  const beat = (message) => api('/api/heartbeat', { job_id: job.jobId, message });

  /** Poll for a condition every 500 ms with a heartbeat every 2 s. Always ends. */
  /** LinkedIn's "load more results" control in the results area (never the top bar's "More" menu). */
  function findMore() {
    const re2 = re(CFG.loadMore.textRegex);
    return Array.from(document.querySelectorAll('main button, main a, [role="main"] button, [role="main"] a, button, a'))
      .find((el) => !el.closest('header, nav, [role="navigation"], [role="banner"], aside, footer') &&
        !/^(https?:\/\/(?!www\.linkedin\.com\/search\/)|\/(?!search\/|$))/.test(el.getAttribute('href') || '') &&  // a link elsewhere: not "load more"
        re2.test(clean(el.getAttribute('aria-label') || el.textContent).slice(0, 40)) &&
        !el.disabled && el.getAttribute('aria-disabled') !== 'true') || null;
  }

  /** The "More" design: click it, read each newly loaded batch, send only people not sent before (no double
   *  reads), with the same pause as between pages, until the target, the end of the results or a LinkedIn
   *  notice. */
  async function loadMoreResults(page, sent, verdict) {
    const L = CFG.loadMore;
    for (let i = 1; i <= L.maxClicks; i++) {
      const btn = findMore();
      if (!btn) break;
      const before = findCards().length;
      await sleep(CFG.pacing.minWaitMs + Math.random() * (CFG.pacing.maxWaitMs - CFG.pacing.minWaitMs));
      clickLikeAPerson(btn);
      const grew = await waitFor(() => findCards().length > before || pageState().kind !== 'ok', L.waitMs,
        `loading more results (${i})`);
      const st = pageState();
      if (st.kind === 'signin' || st.kind === 'limit') return fail(st.kind, st.text);
      if (!grew) break;
      await renderAllCards(page);
      const fresh = readAll().people.filter((p) => !sent.has(p.profile_url));
      fresh.forEach((p) => sent.add(p.profile_url));
      if (!fresh.length) continue;
      const badges = fresh.filter((p) => p.badge_frame).length;
      beat(`more results ${i}: ${fresh.length} new cards, ${badges} badges`);
      const res = await api('/api/ingest', { job_id: job.jobId, people: fresh });
      if (!res.ok) {
        return fail('server', `Could not reach the dashboard at ${res.url || 'its address'} (${res.error || 'status ' + res.status}). Is it running?`);
      }
      verdict = res.json || {};
      beat(`more results ${i}: saved ${verdict.saved}/${verdict.target}`);
      if (!verdict.continue) { await send({ type: 'done' }); return; }
    }
    await api('/api/search/finish', { job_id: job.jobId, reason: 'no more pages' });
    await send({ type: 'done' });
  }

  async function waitFor(cond, timeoutMs, label) {
    const start = Date.now();
    let n = 0;
    while (Date.now() - start < timeoutMs) {
      if (cond()) return true;
      if (n % 4 === 0) beat(label);            // not awaited: a slow server never blocks reading
      n += 1;
      await sleep(500);
    }
    return cond();
  }

  async function fail(kind, text) {
    await api('/api/error', { job_id: job.jobId, message: text });
    await api('/api/search/finish', { job_id: job.jobId, reason: String(text).slice(0, 110) });
    await send({ type: 'attention' });
  }

  function firstProfile() {
    const c = findCards()[0];
    return c ? (Array.from(c.querySelectorAll(CFG.profileLink)).map(profilePath).find(Boolean) || '') : '';
  }

  async function run() {
    // The background worker may still be registering this tab: ask again for up to 10 s.
    let info = await send({ type: 'getJob' });
    for (let i = 0; i < 20 && !info.ok && info.pending; i++) {
      await sleep(500);
      info = await send({ type: 'getJob' });
    }
    if (!info.ok) {                             // not a search job: maybe a location set-up tab
      const task = await send({ type: 'setupTask' });
      if (task && task.ok) {                                                  // automatic, hidden tab
        try { await autoSetUp(task.place); }
        catch (e) { await send({ type: 'setupResult', place: task.place, ok: false, reason: 'set-up error: ' + (e && e.message || e) }); }
        return;
      }
      const teach = await send({ type: 'teachInfo' });
      if (teach && teach.ok) await setUpPlace(teach.place, teach.problem);  // manual fallback, in front
      return;                                   // else an ordinary LinkedIn tab, not ours
    }
    job = info;
    try { self.HBF_APPLY_OVERRIDES(info.cfg); } catch (e) { /* the built-in markers are used */ }

    // LinkedIn drops filters an account may not use; say so instead of failing quietly.
    let ahMissing = job.activelyHiring && !/activelyHiringForJobTitles=/.test(location.search);
    const ahFirst = (() => { try { return !Number(sessionStorage.getItem('hbf-pages-' + job.jobId) || '0'); } catch (e) { return true; } })();
    if (ahMissing && ahFirst) {
      // LinkedIn dropped the address parameter: try its own chip once; never block the run on it
      await beat('Applying the "Actively hiring" filter through LinkedIn\'s filter bar…');
      if (await applyActivelyHiringByUi()) { ahMissing = false; await beat('"Actively hiring" filter applied.'); }
    }
    if (ahMissing) {
      await beat('Warning: LinkedIn ignored the "Actively hiring" filter (it may need Premium). Results are not pre-filtered; the frame check still applies.');
      if (ahFirst) await api('/api/search/note', { job_id: job.jobId, note: 'filter not applied' });
    }
    const ahHint = job.activelyHiring && !ahMissing
      ? ' The "Actively hiring" filter was on: if your account has no Premium, switch it off on the dashboard and try again.' : '';

    if (CFG.verified === false) {
      await beat('Warning: badge markers are not checked against real LinkedIn pages yet. Some badges may be missed.');
    }

    // One page per page load. Going to the next page is a fresh load of "&page=N" in this same tab:
    // clicking Next makes LinkedIn redraw in place, which browsers pause in hidden tabs (runs got
    // stuck on "page 2: waiting for results"). Fresh loads always draw, like page 1 does.
    const maxPages = CFG.pacing.maxPages;
    const page = Number(new URLSearchParams(location.search).get('page') || '1');
    const doneKey = 'hbf-pages-' + job.jobId;            // pages this run has read, kept across loads of this tab
    let pagesDone = 0;
    try { pagesDone = Number(sessionStorage.getItem(doneKey) || '0'); } catch (e) { /* storage blocked */ }
    const firstOfRun = pagesDone === 0;
    // Continued search (&page=N): if its first page is empty now, go back to page 1 once.
    const rewind = async () => {
      await api('/api/search/rewind', { job_id: job.jobId });
      await send({ type: 'restartAtPageOne' });
    };
    const finish = async (reason) => {
      await api('/api/search/finish', { job_id: job.jobId, reason });
      await send({ type: 'done' });
    };

    let state = pageState();
    if (state.kind === 'ok') {
      const ready = await waitFor(() => findCards().length > 0 || pageState().kind !== 'ok', T.cardsMs,
        `page ${page}: waiting for results`);
      state = pageState();
      if (!ready && state.kind === 'ok') {
        if (firstOfRun && page > 1) return rewind();
        // A later page with nothing to read (we went on without a Next button): the results have ended.
        if (!firstOfRun) return finish('no more pages');
        const shown = new Set(Array.from(document.querySelectorAll(CFG.profileLink)).map(profilePath).filter(Boolean)).size;
        const facts = `[ext ${chrome.runtime.getManifest().version}; ${shown} person links, ` +
          `${document.querySelectorAll('[role="listitem"]').length} listitems, ${document.querySelectorAll('li').length} li]`;
        if (shown > 1) {
          return fail('cards', `Page ${page}: LinkedIn shows ${shown} people, but this page layout could not be read. ` +
            `Please send this message to your assistant. ${facts}`);
        }
        return fail('cards', `Page ${page}: no results appeared. If LinkedIn shows people when you open this ` +
          'search yourself, it served its older page, which only shows results in a visible tab: use fewer job ' +
          `titles (1 or 2) and launch again.${ahHint} ${facts}`);
      }
    }
    if (state.kind === 'signin' || state.kind === 'limit') return fail(state.kind, state.text);
    if (state.kind === 'empty') {
      if (firstOfRun && page > 1) return rewind();
      const geoHint = /[?&]geoUrn=/.test(location.search)
        ? ' Also check the location: if LinkedIn\'s Locations filter does not show your city, its saved number is wrong.' : '';
      if (firstOfRun && (ahHint || geoHint)) await beat('LinkedIn found no results.' + ahHint + geoHint);
      return finish('no more pages');
    }

    await renderAllCards(page);
    let { people, skipped, cardCount, primaryPeople } = readAll();
    // Free accounts: LinkedIn's "Actively hiring" filter is a Premium filter, and on a free account LinkedIn
    // shows only the first 2-3 people with it (the rest are kept for Premium). Seen on every free account
    // tested (Nov 2026). Search again WITHOUT the filter, in this tab, before any read is counted: every
    // person is still checked for the #HIRING frame and "See open roles", so the leads are just as exact.
    // The count that matters is the results list's own people: a name that is only MENTIONED inside or beside
    // a card (mutual connection, "X is hiring") makes the page look fuller than LinkedIn's cut-off list is.
    const cutOff = cardCount <= CFG.freeAccount.maxCards ||
      (primaryPeople >= 1 && primaryPeople <= CFG.freeAccount.maxCards && cardCount < CFG.pacing.fullPage);
    const ahCut = firstOfRun && job.activelyHiring && /[?&]activelyHiringForJobTitles=/.test(location.search) && cutOff;
    if (ahCut && !CFG.freeAccount.dropFilter) {                // default: the filter always stays on
      await beat(`page ${page}: LinkedIn showed only ${Math.min(cardCount, primaryPeople || cardCount)} people with its "Actively hiring" filter ` +
        'on this account (a Premium filter). The filter stays on; use an account where LinkedIn shows the full list for more people.');
      await api('/api/search/note', { job_id: job.jobId, note: 'Actively hiring filter shows only a few people on this account' });
    }
    if (ahCut && CFG.freeAccount.dropFilter) {
      await beat(`page ${page}: LinkedIn showed only ${Math.min(cardCount, primaryPeople || cardCount)} people with its "Actively hiring" filter ` +
        '(a Premium filter: free accounts get only the first few). Searching again without it; every person ' +
        'is still checked for the #HIRING frame.');
      await api('/api/search/note', { job_id: job.jobId, note: 'filter not applied (free account)' });
      const r = await send({ type: 'dropActivelyHiring' });
      if (r && r.ok) return;                                   // this tab now loads the search without the filter
    }
    let fromHtml = false;
    if (cardCount < expectedOnPage(page)) {
      // The tab drew fewer people than the page holds (LinkedIn's newer page draws the rest only when they
      // are scrolled into view, which a background tab never does). The SAME page as LinkedIn's server sends
      // it holds every result: read them from there, with the same rules (the method other tools use too).
      const served = await readServedPage(page);
      if (served && served.cardCount > cardCount) {
        ({ people, skipped, cardCount } = served);
        fromHtml = true;
      }
    }
    let shown = false;
    if (cardCount < expectedOnPage(page) && (document.visibilityState === 'hidden' || CFG.visibleRead.assumeHidden) && CFG.visibleRead.on) {
      // Some accounts' LinkedIn pages send only the first people and load the rest only while the page is
      // ON SCREEN (nothing loads them in a background tab). Show this tab for a few seconds, let LinkedIn
      // load its own results, read them with the same rules, then go back to the dashboard.
      beat(`page ${page}: LinkedIn loads the rest of this page only on screen - showing the LinkedIn tab for a few seconds`);
      const r = await send({ type: 'showForReading' });
      if (r && r.ok) {
        shown = true;
        try {
          await waitFor(() => document.visibilityState === 'visible', CFG.visibleRead.waitVisibleMs, `page ${page}: waiting for the tab to show`);
          await renderAllCards(page, 4);              // on screen LinkedIn loads them now: allow it a few seconds
          const seen = readAll();
          if (seen.cardCount > cardCount) ({ people, skipped, cardCount } = seen);
        } finally {
          await send({ type: 'doneReading' });
        }
        if (document.visibilityState === 'hidden' && cardCount < expectedOnPage(page)) {
          beat(`page ${page}: the browser kept the tab hidden (minimised or covered window?) - keep the browser window on screen during a search`);
        }
      }
    }
    const total = resultsTotal();
    const more = total != null && total > page * 10;          // LinkedIn says there are more pages
    if (fromHtml) beat(`page ${page}: the tab drew only part of the results; read all ${cardCount} from LinkedIn's page data`);
    if (cardCount < expectedOnPage(page)) {
      // Fewer people read than LinkedIn says this page has: send the page's STRUCTURE (counts and tag
      // patterns, no names or links) to the dashboard, so the cause can be fixed without anyone running
      // anything. Also says it in the run log.
      try { await api('/api/search/diagnostics', { job_id: job.jobId, page, data: Object.assign(await pageStructure(cardCount, total), { fromHtml, shown, served: lastServed }) }); }
      catch (e) { /* never stops the run */ }
      beat(`page ${page}: read ${cardCount} of ${expectedOnPage(page)} people LinkedIn shows` +
           (total != null ? ` (about ${total} results)` : '') + ' - page details sent for checking');
    }
    if (firstOfRun && lastLayout === 'older' && job.activelyHiring) {
      // Nothing is saved and no reads are counted: these results are not filtered to people who hire.
      return fail('layout', 'LinkedIn showed its older page for this search, which ignores the "Actively hiring" ' +
        'filter (results would be mostly people who are not hiring). Stopped to save your daily reads. ' +
        'Use fewer job titles (1 or 2) and launch again.');
    }
    const badges = people.filter((p) => p.badge_frame).length;
    const roles = people.filter((p) => p.open_roles).length;
    beat(`page ${page}: ${people.length} cards, ${badges} badges, ${skipped} framed without open roles (skipped), ${roles} with open roles`);

    const res = await api('/api/ingest', { job_id: job.jobId, people });
    if (!res.ok) {
      return fail('server', `Could not reach the dashboard at ${res.url || 'its address'} (${res.error || 'status ' + res.status}). Is it running?`);
    }
    pagesDone += 1;
    try { sessionStorage.setItem(doneKey, String(pagesDone)); } catch (e) { /* storage blocked */ }
    const verdict = res.json || {};
    beat(`page ${page}: saved ${verdict.saved}/${verdict.target}`);
    if (!verdict.continue) { await send({ type: 'done' }); return; }
    // LinkedIn's AI-powered people search (rolled out account by account): the top matches first, a "More"
    // control instead of pages. Load more the way a person does, reading each batch with the same rules.
    if (cardCount < expectedOnPage(page) && findMore()) {
      return loadMoreResults(page, new Set(people.map((p) => p.profile_url)), verdict);
    }
    if (pagesDone >= maxPages) return finish('page limit reached');
    // A Next button that is there but switched off = the last page. No Next button we recognise (another
    // page design) on a full page = carry on by address; an empty next page then ends the search.
    // LinkedIn's own result count, when shown, wins: it says whether more pages exist even when this
    // page drew only a few cards or no Next button.
    const nextBtn = findNext();
    if (!more && nextBtn && !nextEnabled(nextBtn)) return finish('no more pages');
    if (!more && !nextBtn && cardCount < CFG.pacing.fullPage) return finish('no more pages');

    // Random 4-8 s pause, then the next page. Done by the background worker: this hidden tab may be
    // frozen by the browser at any moment, and must not be the one waiting.
    const wait = CFG.pacing.minWaitMs + Math.random() * (CFG.pacing.maxWaitMs - CFG.pacing.minWaitMs);
    const go = await send({ type: 'nextPage', page: page + 1, waitMs: Math.round(wait) });
    if (!go.ok) return fail('cards', `Page ${page + 1} could not be opened (${go.error || 'no answer'}).`);
    // The background worker pauses and loads the next page; the script starts again there.
  }

  // Test hook: lets the test page call the readers directly.
  window.__HBF = { findCards, readCard, readAll, hasBadge, hasHiringLink, hasOpenRoles, pageState, findPlaceIds, chooseOption };

  run().catch(async (e) => {
    try { if (job) await fail('error', 'Error: ' + (e && e.message || e)); } catch (_) { /* nothing more to do */ }
  });
})();
