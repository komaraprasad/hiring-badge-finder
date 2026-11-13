// READ-ONLY. Paste into the browser console (F12) on a LinkedIn people-search results page.
// It makes no requests. It copies a JSON report to the clipboard.
// Run it on a page that has people WITH the #HIRING frame and people WITHOUT it.
(() => {
  const trim = (s, n) => (s || '').replace(/\s+/g, ' ').trim().slice(0, n);
  const path = a => { try { return new URL(a.href).pathname.replace(/\/$/, ''); } catch (e) { return ''; } };
  const desc = el => ({
    tag: el.tagName.toLowerCase(),
    class: trim(el.getAttribute('class'), 150),
    role: el.getAttribute('role') || '',
    dataView: el.getAttribute('data-view-name') || '',
    componentkey: trim(el.getAttribute('componentkey'), 60),
  });
  const profPaths = el => new Set([...el.querySelectorAll('a[href*="/in/"]')].map(path).filter(p => p.startsWith('/in/')));

  // Card container: climb from each /in/ link until the parent holds more than one distinct person.
  const cards = new Map();
  document.querySelectorAll('a[href*="/in/"]').forEach(a => {
    const p = path(a);
    if (!p.startsWith('/in/')) return;
    let el = a;
    while (el.parentElement && profPaths(el.parentElement).size <= 1) el = el.parentElement;
    if (!cards.has(el)) cards.set(el, p);
  });

  const markerRe = /frame|hiring|open/i;
  const out = { url: location.pathname, cardCount: cards.size, cards: [], pageInfo: {} };

  [...cards.keys()].forEach(card => {
    const link = card.querySelector('a[href*="/in/"]');
    const img = card.querySelector('img');
    const markers = [];
    [card, ...card.querySelectorAll('*')].forEach(el => {
      for (const at of el.attributes) {
        if (at.name === 'href') continue;
        if (markerRe.test(at.value)) {
          markers.push({ ...desc(el), attr: at.name, value: trim(at.value, 150), alt: trim(el.getAttribute('alt'), 100), src: trim(el.getAttribute('src'), 150) });
          break;
        }
      }
    });
    const leaves = [...card.querySelectorAll('*')]
      .filter(el => el.children.length === 0 && trim(el.textContent, 5))
      .slice(0, 14)
      .map(el => ({ ...desc(el), text: trim(el.textContent, 70) }));
    out.cards.push({
      profileLink: link ? link.href.split('?')[0] : '',
      nameText: link ? trim(link.innerText, 80) : '',
      container: desc(card),
      photo: img ? { alt: trim(img.alt, 100), class: trim(img.className, 150), src: trim(img.getAttribute('src'), 150) } : null,
      markers: markers.slice(0, 15),
      hasSeeOpenRoles: /see open roles/i.test(card.innerText),
      first300: trim(card.innerText, 300),
      leaves,
    });
  });

  out.pageInfo.nextButtons = [...document.querySelectorAll('button, a')]
    .filter(el => /^next$/i.test(trim(el.innerText, 20)) || /next/i.test(el.getAttribute('aria-label') || ''))
    .map(el => ({ ...desc(el), aria: trim(el.getAttribute('aria-label'), 60), text: trim(el.innerText, 20), disabled: el.disabled || el.getAttribute('aria-disabled') }));
  out.pageInfo.listParent = cards.size ? desc([...cards.keys()][0].parentElement) : null;

  copy(JSON.stringify(out, null, 1));
  console.log('Copied. Cards found:', cards.size);
})();
