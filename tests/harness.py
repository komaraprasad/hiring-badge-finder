"""Test harness: a mock LinkedIn results page plus a small stand-in server.

IMPORTANT LIMITS (also stated in the README and the final report):
* The mock page follows the STRUCTURE captured from one real signed-in LinkedIn
  people-search page (Nov 2026, narrow 734px layout): role=list / role=listitem
  cards, `figure img` with a profile-framedphoto / profile-displayphoto src,
  a /opportunities/hiring-opportunities/ link, mutual-connection avatars with
  their own /in/ links, and a data-testid Next button. Names, URLs and text are
  fake. Hashed class names are left out on purpose. The desktop layout and the
  real "no results" wording were not captured.
* The stand-in server implements the same /api endpoints on top of the real
  Storage class. It is NOT app/main.py (FastAPI), so these tests do not cover
  main.py. tests/test_api.py covers main.py when FastAPI is installed.
"""
from __future__ import annotations

import html
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PER_PAGE = 10


# ---------------------------------------------------------------- mock data
def people_for_page(page: int) -> list[dict]:
    """20 people on 2 pages. Page 2 repeats person-1 once (a duplicate)."""
    out = []
    for i in range(1, PER_PAGE + 1):
        n = (page - 1) * PER_PAGE + i
        p = {
            "n": n, "slug": f"person-{n}", "name": f"Person {n}",
            "headline": f"Software Engineer at Company{n}",
            "location": "Mumbai, Maharashtra, India" if n % 5 == 0 else "Bengaluru, Karnataka, India",
            "frame": False, "open_roles": False, "degree": "2nd", "button": "Connect",
            "mutuals": ["mutual-a"] if n % 3 == 0 else [], "followers": n % 4 == 0,
        }
        if n in (1, 2, 3, 11, 12):
            p["frame"] = True
        if n in (1, 2, 3, 4, 5, 11, 12):       # real page: every framed card had it
            p["open_roles"] = True
        if n == 1:                              # case (a)
            p.update(headline="Senior Engineering Manager - Hiring SDETs for my team",
                     degree="3rd+", button="Message")
        if n == 2:                              # case (b): no hiring words, mutual connections
            p.update(mutuals=["mutual-a", "mutual-b"], button="Follow")
        if n == 3:                              # case (d): 3 distinct /in/ slugs in one card
            p.update(mutuals=["mutual-a", "mutual-b"])
        if n == 9:                              # unframed person whose mutual's avatar IS framed
            p["mutuals"] = ["mutual-c"]
        if n == 6:                              # case (c): no frame, no open roles, text only
            p["headline"] = "We are hiring @ All Levels | Engineering Manager at Acme"
        if n == 7:
            p["headline"] = "Hiring process consultant"            # false positive
        if n == 8:                              # other frame type: framed src, NO hiring link
            p["frame"] = True
        if n == 13:
            p["headline"] = "Hiring Manager at Globex"               # job title, not a signal
        out.append(p)
    if page == 2:
        out[-1] = {"n": 1, "slug": "Person-1", "name": "Person 1 (again)",
                   "headline": "Senior Engineering Manager - Hiring SDETs for my team",
                   "location": "Bengaluru, Karnataka, India", "frame": True, "open_roles": True,
                   "degree": "3rd+", "button": "Message", "mutuals": [], "followers": False}
    return out


def edge_people() -> list[dict]:
    """Task 4 accuracy cases, one page, no Next."""
    base = {"frame": False, "open_roles": False, "degree": "2nd", "button": "Connect", "mutuals": [],
            "followers": False, "location": "Bengaluru, Karnataka, India", "photo": True, "name_sr": False}
    rows = [
        dict(n=101, slug="edge-nophoto", name="Edge Nophoto", headline="Engineering Manager at Acme",
             photo=False, mutuals=["mutual-c"]),                     # mutual-c's avatar IS framed
        dict(n=102, slug="edge-plain", name="Edge Plain", headline="QA Lead at Globex", degree="3rd+"),
        dict(n=103, slug="edge-hashtag", name="Edge Hashtag", headline="#hiring Engineering Manager | Platform"),
        dict(n=104, slug="edge-noloc", name="Edge Noloc", headline="Director at Initech", frame=True,
             open_roles=True, location=None, button="Follow"),
        dict(n=105, slug="edge-nohead", name="Edge Nohead", headline=None, frame=True, open_roles=True,
             location="Greater Bengaluru Area", button="Message"),
        dict(n=106, slug="edge-srname", name="Edge Twice", headline="Staff Architect@Globex | Ex Initech",
             frame=True, open_roles=True, name_sr=True),
        dict(n=107, slug="edge-inline", name="Edge Inline", headline="EM at Acme", degree_inline=True),
    ]
    return [{**base, **r} for r in rows]


# Expected results of reading the two pages (used by the browser test).
EXPECTED_FRAMES = {1: {1, 2, 3, 11, 12}, 2: {1, 2, 3, 11, 12}}


def photo_src(framed: bool, key: str) -> str:
    kind = "profile-framedphoto-shrink_100_100" if framed else "profile-displayphoto-scale_100_100"
    return f"/media/v2/{key}/{kind}/0/img.gif"


def card_html(p: dict) -> str:
    """One card in the real line order: name, degree, headline, location, action
    button, current-role summary, optional mutual connections, optional
    followers, optional "See open roles"."""
    href = f"/in/{p['slug']}/?miniProfileUrn=urn%3Ali%3Afs_miniProfile%3A{p['n']}"
    mutual = ""
    if p["mutuals"]:
        avatars = "".join(f'<a href="/in/{m}/"><figure><img alt="" src="{photo_src(m == "mutual-c", m)}"></figure></a>'
                          for m in p["mutuals"])
        names = " & ".join(m.replace("-", " ").title() for m in p["mutuals"])
        mutual = f'<div>{avatars}<p>{html.escape(names)} are mutual connections</p></div>'
    followers = f"<p>{p['n'] * 37} followers</p>" if p["followers"] else ""
    roles = (f'<a href="/in/{p["slug"]}/opportunities/hiring-opportunities/?origin=SRP">See open roles</a>'
             if p["open_roles"] else "")
    photo = (f'<a href="{href}"><figure><img alt="" src="{photo_src(p["frame"], p["slug"])}"></figure></a>'
             if p.get("photo", True) else "")
    name = html.escape(p["name"])
    if p.get("degree_inline"):  # name and degree on one line, as seen in a live CSV
        name = f"{name} • {p['degree']}"
    if p.get("name_sr"):        # LinkedIn-style copy of the name for screen readers
        name = f'<span aria-hidden="true">{name}</span><span class="sr-only">{name}</span>'
    head = f"<p>{html.escape(p['headline'])}</p>" if p["headline"] is not None else ""
    loc = f"<p>{html.escape(p['location'])}</p>" if p["location"] is not None else ""
    return f"""
<div role="listitem"><div>
  {photo}
  <div>
    <p><a href="{href}">{name}</a></p>
    {"" if p.get("degree_inline") else f"<p>• {p['degree']}</p>"}
    {head}
    {loc}
    <button>{p['button']}</button>
    <p>Current: Team lead at Company{p['n']}</p>
    {mutual}
    {followers}
    {roles}
  </div>
</div></div>"""


def classic_card_html(p: dict) -> str:
    """OLDER LinkedIn results layout (no role="listitem", no <figure>, screen-reader-only lines).
    Built from LinkedIn's historical classic markup, NOT captured from a live page."""
    href = f"/in/{p['slug']}/?miniProfileUrn=urn%3Ali%3Afs_miniProfile%3A{p['n']}"
    name = html.escape(p["name"])
    mutual = "".join(f'<a href="/in/{m}/"><img alt="" src="{photo_src(m == "mutual-c", m)}"></a>' for m in p["mutuals"])
    if mutual:
        mutual = f'<div>{mutual}<span>{len(p["mutuals"])} mutual connections</span></div>'
    roles = (f'<a href="/in/{p["slug"]}/opportunities/hiring-opportunities/">See open roles</a>'
             if p["open_roles"] else "")
    return f"""
<li class="reusable-search__result-container"><div class="entity-result">
  <div><a href="{href}"><div class="presence-entity">
    <img alt="{name}" src="{photo_src(p['frame'], p['slug'])}"><span class="sr-only">Status is offline</span></div></a></div>
  <div>
    <span><a href="{href}"><span dir="ltr"><span aria-hidden="true">{name}</span><span class="sr-only">View {name}’s profile</span></span></a></span>
    <span><span aria-hidden="true">• {p['degree']}</span><span class="sr-only">{p['degree']} degree connection</span></span>
    <div>{html.escape(p['headline'])}</div>
    <div>{html.escape(p['location'])}</div>
    <p>Current: Team lead at Company{p['n']}</p>
    {mutual}
    {roles}
  </div>
  <div><button>{p['button']}</button></div>
</div></li>"""


def rsc_stream(cards_html: list[str]) -> str:
    """The cards as a React Server Components stream, like LinkedIn's newer pages: rows "<id>:<json>",
    module rows, each card in its own row, links and photos as components (photos as LinkedIn's
    renderPayload with rootUrl + suffixUrl), the list row pointing at the card rows with "$<id>"."""
    from html.parser import HTMLParser

    class P(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.root = {"tag": "root", "attrs": {}, "kids": []}
            self.stack = [self.root]

        def handle_starttag(self, tag, attrs):
            node = {"tag": tag, "attrs": dict(attrs), "kids": []}
            self.stack[-1]["kids"].append(node)
            if tag not in ("img", "br"):
                self.stack.append(node)

        def handle_endtag(self, tag):
            if len(self.stack) > 1 and self.stack[-1]["tag"] == tag:
                self.stack.pop()

        def handle_data(self, data):
            if data.strip():
                self.stack[-1]["kids"].append(data.strip())

    def conv(node):
        if isinstance(node, str):
            return node
        kids = [conv(k) for k in node["kids"]]
        kids = kids[0] if len(kids) == 1 else kids
        a = node["attrs"]
        if node["tag"] == "figure":                      # the photo: LinkedIn's image component
            img = next(k for k in node["kids"] if isinstance(k, dict) and k["tag"] == "img")
            src = img["attrs"]["src"]
            cut = src.index("/0/")
            return ["$", "$L2", None, {"shape": "circle", "height": "6x",
                    "renderPayload": {"rootUrl": src[:cut], "imageRenditions": [{"width": 100, "suffixUrl": src[cut:]}]}}]
        if node["tag"] == "a":                           # links: a client component with an href prop
            return ["$", "$L3", None, {"href": a["href"], "children": kids}]
        props = {k: v for k, v in a.items() if k in ("role", "aria-hidden", "class")}
        if kids != []:
            props["children"] = kids
        return ["$", node["tag"], None, props]

    rows = ['1:I["(app-pages)/search","SearchPage"]', '2:I["(app-pages)/image","Image"]', '3:I["(app-pages)/link","Link"]']
    refs = []
    for i, c in enumerate(cards_html):
        p = P()
        p.feed(c)
        card = next(k for k in p.root["kids"] if isinstance(k, dict))
        rid = format(0x10 + i, "x")
        rows.append(f"{rid}:" + json.dumps(conv(card), separators=(",", ":")))
        refs.append("$" + rid)
    rows.append("0:" + json.dumps(["$", "main", None, {"children": ["$", "div", None, {"role": "list", "children": refs}]}],
                                  separators=(",", ":")))
    return "\n".join(rows) + "\n"


def results_page(page: int, variant: str = "") -> str:
    if variant == "empty":
        return "<!doctype html><title>Search</title><main><h2>No results found</h2></main>"
    if variant == "limit":
        return "<!doctype html><title>Search</title><main><p>We noticed unusual activity from your account.</p></main>"
    if variant == "commercial":
        return "<!doctype html><title>Search</title><main><p>You've reached the commercial use limit on search.</p></main>"
    if page > 2:            # past the end of the mock results
        return "<!doctype html><title>Search</title><main><h2>No results found</h2></main>"
    if variant == "classic":
        items = "".join(classic_card_html(p) for p in people_for_page(1))
        return ("<!doctype html><html><head><meta charset='utf-8'><title>Search</title>"
                "<style>.sr-only{position:absolute;left:-9999px} img{width:48px;height:48px}</style></head>"
                "<body><main><div>People</div><ul role='list'>" + items + "</ul>"
                "<button aria-label='Next' disabled>Next</button></main></body></html>")
    sidebar = ('<aside>' + "".join(f'<div><a href="/in/side-{i}/"><img alt="" src="{photo_src(True, f"side-{i}")}">'
               f'Side Person {i}</a><span>• 2nd</span><button>Connect</button></div>' for i in range(3)) + '</aside>')
    if variant == "divlayout":      # no li, no role, no figure: only plain divs (any future layout)
        items = "".join(classic_card_html(p).replace("<li ", "<div ").replace("</li>", "</div>")
                        for p in people_for_page(1))
        return ("<!doctype html><html><head><meta charset='utf-8'><title>Search</title>"
                "<style>.sr-only{position:absolute;left:-9999px} img{width:48px;height:48px}</style></head>"
                f"<body><header><a href='/in/me/'>Me</a></header><main>{sidebar}<div><div>{items}</div></div>"
                "<button aria-label='Next' disabled>Next</button></main></body></html>")
    if variant == "decoy":          # seen on another account: a small box of 2 people uses role="listitem", the
        # real 10 results are plain divs, and there is no Next button the tool knows
        decoys = "".join(f'<div role="listitem"><a href="/in/decoy-{i}/"><img alt="" src="{photo_src(True, f"decoy-{i}")}">'
                         f'Decoy Person {i}</a><span>• 2nd</span><button>Connect</button></div>' for i in range(2))
        items = "".join(classic_card_html(p).replace("<li ", "<div ").replace("</li>", "</div>")
                        for p in people_for_page(page))
        return ("<!doctype html><html><head><meta charset='utf-8'><title>Search</title>"
                "<style>.sr-only{position:absolute;left:-9999px} img{width:48px;height:48px}</style></head>"
                f"<body><main><section>{decoys}</section><div><div>{items}</div></div>"
                "<nav><span>1</span><span>2</span></nav></main></body></html>")
    if variant == "served":
        # LinkedIn's newer page on another account (seen Nov 2026): the server sends all 10 results, but a
        # background tab only ever DRAWS the first 2 (the rest are drawn when scrolled into view). No result
        # count, no Next button. Simulated: the HTML holds 10 cards, the page script removes all but 2.
        cards = "".join(card_html(p) for p in people_for_page(page))
        return f"""<!doctype html><html><head><meta charset="utf-8"><title>Search results</title>
<style>.sr-only{{position:absolute;left:-9999px}} figure{{margin:0;display:inline-block}} img{{width:48px;height:48px}}</style></head>
<body><main><div data-testid="lazy-column"><div role="list" id="list">{cards}</div></div><a href="#">More</a></main>
<script>for (const c of Array.from(document.querySelectorAll('#list > [role=listitem]')).slice(2)) c.remove();</script>
</body></html>"""
    if variant == "lazyload":
        # The other accounts' page (seen Nov 2026): the server sends only 2 people; the rest are loaded when
        # the "load more" area becomes VISIBLE (IntersectionObserver), and drawn in an animation frame. The area
        # is off-screen and the page cannot scroll, so the browser alone never loads them. No count, no Next.
        allc = [card_html(p) for p in people_for_page(page)]
        return f"""<!doctype html><html><head><meta charset="utf-8"><title>Search results</title>
<script>// like the real background tab on those accounts: the page sees itself as hidden
Object.defineProperty(Document.prototype, 'visibilityState', {{ get() {{ return 'hidden'; }} }});</script>
<style>html,body{{height:100%;overflow:hidden;margin:0}} .sr-only{{position:absolute;left:-9999px}}
figure{{margin:0;display:inline-block}} img{{width:48px;height:48px}} #more{{position:absolute;top:100000px;height:10px}}</style></head>
<body><main><div data-testid="lazy-column"><div role="list" id="list">{"".join(allc[:2])}</div><div id="more"></div></div></main>
<script>const REST = {json.dumps(allc[2:])};
const io = new IntersectionObserver((es) => {{
  if (!es.some((e) => e.isIntersecting)) return;
  io.disconnect();
  requestAnimationFrame(() => {{ for (const c of REST) document.getElementById('list').insertAdjacentHTML('beforeend', c); }});
}});
io.observe(document.getElementById('more'));</script>
</body></html>"""
    if variant == "innerscroll":
        # The failing accounts' page (report: page exactly one window tall): the results sit in their OWN
        # scrolling box; 2 people at first, 4 more each time that box is scrolled near its end. No Next, no count.
        allc = [card_html(p) for p in people_for_page(page)]
        return f"""<!doctype html><html><head><meta charset="utf-8"><title>Search results</title>
<style>html,body{{height:100%;overflow:hidden;margin:0}} .sr-only{{position:absolute;left:-9999px}}
figure{{margin:0;display:inline-block}} img{{width:48px;height:48px}} #box{{height:300px;overflow-y:auto}} #pad{{height:400px}}</style></head>
<body><main><div id="box" data-testid="lazy-column"><div role="list" id="list">{"".join(allc[:2])}</div><div id="pad"></div></div></main>
<script>const ALL = {json.dumps(allc)}; let shown = 2, busy = false; const box = document.getElementById('box');
box.addEventListener('scroll', () => {{
  if (busy || shown >= ALL.length || box.scrollTop + box.clientHeight < box.scrollHeight - 50) return;
  busy = true;
  setTimeout(() => {{ for (const c of ALL.slice(shown, shown + 4)) document.getElementById('list').insertAdjacentHTML('beforeend', c);
    shown += 4; busy = false; }}, 300);
}});</script>
</body></html>"""
    if variant == "aicards":
        # LinkedIn's AI search card design (seen on a failing account): each person is a <section> (no
        # role="listitem"); the page's role="listitem" elements are small parts INSIDE one person's card
        # (9 skill chips linking to that person's own skills page). No Next, no count.
        cards = [card_html(p).replace('<div role="listitem">', "<section>", 1).replace("</div></div>", "</div></section>", 1)
                 for p in people_for_page(page)]
        chips = "".join(f'<div role="listitem"><a href="/in/person-{(page - 1) * 10 + 1}/details/skills/">Skill {i}</a></div>'
                        for i in range(9))
        cards[0] = cards[0].replace("</section>", f"<div>{chips}</div></section>", 1)
        return f"""<!doctype html><html><head><meta charset="utf-8"><title>Search results</title>
<style>.sr-only{{position:absolute;left:-9999px}} figure{{margin:0;display:inline-block}} img{{width:48px;height:48px}}</style></head>
<body><nav><a href="/in/me/">Me</a></nav><main><div data-testid="lazy-column"><div>{"".join(cards)}</div></div></main></body></html>"""
    if variant == "aimore":
        # LinkedIn's AI-powered people search (some accounts): 2 top matches, then a "More" button that loads
        # 4 more each click (after a moment), no pages, no result count, no Next. Here: all 20 mock people.
        allc = [card_html(p) for p in people_for_page(1) + people_for_page(2)]
        return f"""<!doctype html><html><head><meta charset="utf-8"><title>Search results</title>
<style>.sr-only{{position:absolute;left:-9999px}} figure{{margin:0;display:inline-block}} img{{width:48px;height:48px}}</style></head>
<body><nav><a href="#">More</a></nav><main><div data-testid="lazy-column"><div role="list" id="list">{"".join(allc[:2])}</div>
<button id="more">More</button></div></main>
<script>const ALL = {json.dumps(allc)}; let shown = 2;
document.getElementById('more').addEventListener('click', () => setTimeout(() => {{
  for (const c of ALL.slice(shown, shown + 4)) document.getElementById('list').insertAdjacentHTML('beforeend', c);
  shown += 4; if (shown >= ALL.length) document.getElementById('more').remove();
}}, 600));</script>
</body></html>"""
    if variant == "onscreen":
        # The other accounts' page: the server sends 2 people, the rest load ONLY while the page is on screen.
        # The test calls __loadRest() once it sees this tab brought to the front (test browsers are headless).
        allc = [card_html(p) for p in people_for_page(page)]
        return f"""<!doctype html><html><head><meta charset="utf-8"><title>Search results</title>
<style>.sr-only{{position:absolute;left:-9999px}} figure{{margin:0;display:inline-block}} img{{width:48px;height:48px}}</style></head>
<body><main><div data-testid="lazy-column"><div role="list" id="list">{"".join(allc[:2])}</div></div><a href="#">More</a></main>
<script>const REST = {json.dumps(allc[2:])}; let done = false;
window.__loadRest = () => {{ if (done) return; done = true;
  for (const c of REST) document.getElementById('list').insertAdjacentHTML('beforeend', c); }};</script>
</body></html>"""
    if variant == "stream":
        # LinkedIn's newer page as its server sends it: the HTML holds only the first 2 people; all 10 are in
        # the React Server Components stream inside a script (an escaped JavaScript string). The tab draws 2.
        allc = [card_html(p) for p in people_for_page(page)]
        stream = json.dumps(rsc_stream(allc)).replace("</", "<\\/")
        return f"""<!doctype html><html><head><meta charset="utf-8"><title>Search results</title>
<style>.sr-only{{position:absolute;left:-9999px}} figure{{margin:0;display:inline-block}} img{{width:48px;height:48px}}</style></head>
<body><main><div data-testid="lazy-column"><div role="list" id="list">{"".join(allc[:2])}</div></div><a href="#">More</a></main>
<script>(self.__rsc = self.__rsc || []).push({stream});</script>
</body></html>"""
    if variant == "slowdraw":
        # Like LinkedIn: the server sends all 10 results; the page draws 2 at first and the rest 4 s later.
        cards = "".join(card_html(p) for p in people_for_page(page))
        return f"""<!doctype html><html><head><meta charset="utf-8"><title>Search results</title>
<style>.sr-only{{position:absolute;left:-9999px}} figure{{margin:0;display:inline-block}} img{{width:48px;height:48px}}</style></head>
<body><main><div>About 20 results</div><div role="list" id="list">{cards}</div></main>
<script>const late = Array.from(document.querySelectorAll('#list > [role=listitem]')).slice(2);
late.forEach((c) => c.remove()); setTimeout(() => late.forEach((c) => document.getElementById('list').appendChild(c)), 4000);</script>
</body></html>"""
    if variant == "twoonly":
        # Another account / browser: the page says "About 20 results" but draws only 2 people at first.
        # slowdraw: the other 8 appear after 4 s. twoonly: they never appear. No Next button the tool knows.
        cards = [card_html(p) for p in people_for_page(page)]
        later = "setTimeout(() => { for (const c of ALL.slice(2)) list.insertAdjacentHTML('beforeend', c); }, 4000);" \
            if variant == "slowdraw" else ""
        return f"""<!doctype html><html><head><meta charset="utf-8"><title>Search results</title>
<style>.sr-only{{position:absolute;left:-9999px}} figure{{margin:0;display:inline-block}} img{{width:48px;height:48px}}</style></head>
<body><main><div>About 20 results</div><div role="list" id="list"></div></main>
<script>const ALL = {json.dumps(cards)}; const list = document.getElementById('list');
for (const c of ALL.slice(0, 2)) list.insertAdjacentHTML('beforeend', c); {later}</script></body></html>"""
    if variant == "sidebarfirst":   # the newer layout's cards arrive late; a sidebar is already there
        page_html = results_page(page, "")
        return page_html.replace("<body>", "<body>" + sidebar, 1).replace(
            "showMore(); watchLast();\n", "setTimeout(() => { showMore(); watchLast(); }, 2500);\n", 1)
    if variant == "unreadable":     # people are shown, but in a structure the tool does not know
        divs = "".join(f'<div><a href="/in/u-{i}/">User {i}</a></div>' for i in range(3))
        return f"<!doctype html><title>Search</title><main>{divs}</main>"
    if variant == "nocards":
        return "<!doctype html><title>Search</title><main><p>Loading...</p></main>"
    cards = [card_html(p) for p in (edge_people() if variant == "edge" else people_for_page(page))]
    nxt = ('<button data-testid="pagination-controls-next-button-visible" id="next">Next</button>'
           if page == 1 and variant != "edge" else
           '<button data-testid="pagination-controls-next-button-visible" id="next" disabled>Next</button>')
    next_js = """
document.getElementById('next').addEventListener('click', () => {
  fetch('/search/results/people/fragment?page=2').then(r => r.text()).then(t => {
    document.getElementById('list').innerHTML = '';
    const d = new DOMParser().parseFromString(t, 'text/html');
    window.__setCards(JSON.parse(d.getElementById('data').textContent));
    history.pushState({}, '', '/search/results/people/?page=2');
    document.getElementById('next').disabled = true;
  });
});""" if page == 1 else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Search results</title>
<style>.sr-only{{position:absolute;left:-9999px}} [role=listitem]{{padding:12px;border-bottom:1px solid #ccc;min-height:120px}} figure{{margin:0;display:inline-block}} img{{width:48px;height:48px}}</style></head>
<body><h1>People</h1><div role="list" id="list"></div><div id="sentinel" style="height:5px"></div>
{nxt}
<script>
let ALL = {json.dumps(cards)};
const list = document.getElementById('list');
let shown = 0, io = null;
// Lazy rendering like a long result list: 5 cards at a time, the next 5 appear
// only once the last rendered card is on screen.
function showMore() {{ const stop = Math.min(ALL.length, shown + 5);
  for (; shown < stop; shown++) list.insertAdjacentHTML('beforeend', ALL[shown]); }}
function watchLast() {{
  if (io) io.disconnect();
  if (shown >= ALL.length) return;
  io = new IntersectionObserver((e) => {{ if (e.some(x => x.isIntersecting)) {{ showMore(); watchLast(); }} }});
  io.observe(list.lastElementChild);
}}
window.__setCards = (c) => {{ ALL = c; shown = 0; list.innerHTML = ''; showMore(); watchLast(); }};
showMore(); watchLast();
{next_js}
</script></body></html>"""


def fragment(page: int) -> str:
    cards = [card_html(p) for p in people_for_page(page)]
    return f'<!doctype html><script id="data" type="application/json">{json.dumps(cards).replace("</", "<\\/")}</script>'


def filter_bar(cfg: dict) -> str:
    """A stand-in for LinkedIn's filter bar, built from markup verified on a real page (Nov 2026):
    chips are div[role=button][aria-expanded] wrapping div[aria-label="Filter by Locations"] /
    div[aria-label="Premium Actively hiring"]; the location box is [popover] input[placeholder="Add a location"];
    options are [role=listbox][data-testid=typeahead-results-container] > [role=option]; a chosen place is a checked
    [popover] input[type=checkbox][aria-label=<place>]; "Show results" is an <a> whose href has no geoUrn, and the
    address gains geoUrn=["<id>"] afterwards. Touching the People chip or the main search box is reported."""
    return """
<div id="hbf-mock-bar">
  <input data-testid="typeahead-input" placeholder="Search" id="mainsearch">
  <div role="button" aria-expanded="false" id="peoplechip"><div aria-label="Filter by People">People</div></div>
  <div role="button" aria-expanded="false" id="locchip"><div aria-label="Filter by Locations">Locations</div></div>
  <div role="button" aria-expanded="false" id="ahchip"><div aria-label="Premium Actively hiring">Actively hiring</div></div>
</div>
<script>
(() => {
  const CFG = """ + json.dumps(cfg) + """;
  const touch = (what) => fetch('/touch?what=' + encodeURIComponent(what));
  document.getElementById('peoplechip').addEventListener('click', () => touch('people chip'));
  ['focus', 'input', 'click'].forEach((t) => document.getElementById('mainsearch').addEventListener(t, () => touch('main search ' + t)));
  const go = (url) => setTimeout(() => history.pushState({}, '', url), CFG.urlDelayMs || 800);
  // ---- Locations
  const loc = document.getElementById('locchip');
  if (CFG.decoyBox) {                       // a closed copy of the box, earlier in the page
    const d = document.createElement('div'); d.setAttribute('popover', '');
    const i = document.createElement('input'); i.placeholder = 'Add a location'; d.appendChild(i);
    document.body.appendChild(d);
  }
  let closedOnce = false;
  loc.addEventListener('click', () => {
    if (document.getElementById('locpop')) return;
    loc.setAttribute('aria-expanded', 'true');
    const pop = document.createElement('div'); pop.id = 'locpop'; pop.setAttribute('popover', '');
    const input = document.createElement('input'); input.placeholder = 'Add a location';
    let focused = false;
    const list = document.createElement('div'); list.setAttribute('role', 'listbox');
    if (!CFG.looseList) list.setAttribute('data-testid', 'typeahead-results-container');
    const boxes = document.createElement('div');
    const addBox = (label, id) => {
      const cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = true;
      cb.setAttribute('aria-label', label); cb.dataset.id = id || ''; boxes.appendChild(cb);
    };
    if (CFG.prechecked) addBox(CFG.prechecked, '1');
    const wire = (box) => { box.addEventListener('focus', () => { focused = true; }); box.addEventListener('input', onInput); };
    const onInput = (e) => {
      list.innerHTML = '';
      if (CFG.trustedOnly && !e.isTrusted) return;
      const box = e.target;
      const q = box.value.toLowerCase();
      if (q.length < 3) return;
      if (CFG.noCommas && q.includes(',')) return;
      if (CFG.needFocus && !focused) return;
      if (CFG.needVisible && document.visibilityState !== 'visible') return;
      (CFG.options || []).filter(([t]) => t.toLowerCase().includes(q.split(',')[0])).forEach(([t, id]) => {
        const o = document.createElement('div'); o.setAttribute('role', 'option'); o.textContent = t;
        o.addEventListener('click', () => {
          fetch('/touch?what=' + encodeURIComponent('option ' + t));
          if (CFG.tickWrong) addBox(CFG.tickWrong, '999'); else addBox(t, id);
          list.innerHTML = ''; box.value = '';
        });
        if (CFG.looseList) { const w = document.createElement('div'); w.appendChild(o); list.appendChild(w); }
        else list.appendChild(o);
      });
    };
    wire(input);
    if (CFG.replaceBox) setTimeout(() => {   // the page redraws the box just after opening it
      const n = input.cloneNode(); wire(n); input.replaceWith(n);
    }, 300);
    if (CFG.closeOnce && !closedOnce) {      // the page closes the filter by itself, once
      closedOnce = true;
      setTimeout(() => { pop.remove(); loc.setAttribute('aria-expanded', 'false'); }, 300);
    }
    const show = document.createElement('a'); show.textContent = 'Show results';
    show.href = location.pathname + location.search;           // no geoUrn in it, as on LinkedIn
    show.addEventListener('click', (e) => {
      e.preventDefault();
      fetch('/touch?what=show');
      const ids = Array.from(boxes.querySelectorAll('input:checked')).map((c) => c.dataset.id).filter(Boolean);
      const label = Array.from(boxes.querySelectorAll('input:checked')).map((c) => c.getAttribute('aria-label'))[0] || 'Locations';
      pop.remove(); loc.setAttribute('aria-expanded', 'false');
      loc.firstElementChild.textContent = label; loc.firstElementChild.setAttribute('aria-label', label);
      const u = new URL(location.href); u.searchParams.set('geoUrn', JSON.stringify(ids));
      go(u.pathname + u.search);
    });
    pop.append(input, list, boxes, show);
    document.body.appendChild(pop);
    pop.showPopover();                                          // LinkedIn shows its popovers this way
  });
  // ---- Actively hiring
  const ah = document.getElementById('ahchip');
  ah.addEventListener('click', () => {
    fetch('/touch?what=ah chip');
    if (CFG.ahUi === 'missing' || document.getElementById('ahpop')) return;
    ah.setAttribute('aria-expanded', 'true');
    const pop = document.createElement('div'); pop.id = 'ahpop'; pop.setAttribute('popover', '');
    const input = document.createElement('input'); input.placeholder = 'Hiring for job title';
    const any = document.createElement('input'); any.type = 'checkbox'; any.setAttribute('aria-label', 'Any job title');
    const show = document.createElement('a'); show.textContent = 'Show results'; show.href = location.pathname + location.search;
    show.addEventListener('click', (e) => {
      e.preventDefault(); pop.remove(); ah.setAttribute('aria-expanded', 'false');
      if (CFG.ahUi === 'ok' && any.checked) {
        const u = new URL(location.href); u.searchParams.delete('dropah');
        u.searchParams.set('activelyHiringForJobTitles', '["-100"]');
        go(u.pathname + u.search);
      }
    });
    pop.append(input, any, show);
    document.body.appendChild(pop);
    pop.showPopover();
  });
})();
</script>"""


GIF = bytes.fromhex("47494638396101000100800000ffffff00000021f90401000000002c00000000010001000002024401003b")

DASHBOARD = """<!doctype html><meta name="hbf-dashboard" content="1"><title>stand-in dashboard</title><body>
<script>
window.__events = [];
window.addEventListener('message', (e) => { if (e.data && e.data.source === 'hbf-extension') window.__events.push(e.data); });
</script></body>"""


# ---------------------------------------------------- company pages (assumed markup)
def company_search_page(results, variant=""):
    """Company search results: each result has a logo link (no text) and a name link."""
    if variant == "limit":
        return "<html><body><main><h1>We noticed unusual activity from your account</h1></main></body></html>"
    if not results:
        return "<html><body><main><h2>No results found</h2></main></body></html>"
    if variant == "divs":                      # no list items: every result is plain divs, name linked twice
        items = "".join(f'<div><div><a href="/company/{r[0]}/"><img alt="{r[1]} logo"></a></div>'
                        f'<div><a href="/company/{r[0]}/"><span>{r[1]}</span></a></div><div>Software Development</div></div>'
                        for r in results)
        return f"<html><body><main><div>{items}</div></main></body></html>"
    items = ""
    for r in results:
        s, n = r[0], r[1]
        logo = f"/company/{r[2]}/" if len(r) > 2 else f"/company/{s}/"     # optional numeric-id logo link
        items += (f'<li><a href="{logo}"><img alt=""></a><div><a href="/company/{s}/">{n}</a>'
                  f'<p>Software Development - Bengaluru</p><p>1K followers</p></div></li>')
    return f"<html><body><nav><a href='/company/linkedin/'>LinkedIn</a></nav><main><ul>{items}</ul></main></body></html>"


def company_about_page(industry, size, layout="dl"):
    if layout == "lines":
        body = (f"<section><h2>Overview</h2><p>We build things.</p><div>Industry</div><div>{industry}</div>"
                f"<div>Company size</div><div>{size}</div><div>Headquarters</div><div>Pune</div></section>")
    else:
        body = (f"<section><h2>Overview</h2><dl><dt>Website</dt><dd><a href='https://acme.example'>acme.example</a></dd>"
                f"<dt>Industry</dt><dd>{industry}</dd><dt>Company size</dt><dd>{size}</dd>"
                f"<dd>2,345 associated members</dd><dt>Headquarters</dt><dd>Pune</dd></dl></section>")
    return f"<html><body><main>{body}</main></body></html>"


VH = "position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)"


def _line(text):
    """One visible line plus LinkedIn's screen-reader copy of it (both end up in innerText)."""
    return f'<div><span aria-hidden="true">{text}</span><span style="{VH}">{text}</span></div>'


def open_roles_page(name: str, roles: list[tuple], needs_click: bool = False, box: str = "") -> str:
    """The person's profile with LinkedIn's "Open roles" pop-up (assumed markup, from a screenshot,
    Nov 2026). roles: (job id, title, posted text) - each role has a logo link and a title link."""
    items = ""
    for jid, title, posted in roles:
        items += (f'<li><a href="/jobs/view/{jid}/"><img alt="Company logo"></a><div>'
                  f'<a href="/jobs/view/{jid}/?trk=x">{_line(title)}</a>{_line("Electronic Arts (EA)")}'
                  f'{_line("Hyderabad (Hybrid)")}<div><img alt=""><span>2 connections</span></div>'
                  f'<div><time>{posted}</time></div></div></li>')
    dialog = (f'<div role="dialog" aria-modal="true"><h2>Open roles</h2><div><img alt=""><span>{name} is hiring</span></div>'
              f'<ul>{items}</ul><div>Hiring? Add job posts and the #Hiring photo frame to your profile.</div></div>')
    hiring_box = (f'<div class="box"><div>Hiring: {box}</div><div>Electronic Arts (EA) · Hyderabad</div>'
                  f'<a href="/jobs/view/999/">Show job</a></div>') if box else ""
    if needs_click:
        # Like real LinkedIn (Nov 2026): this address shows the plain profile; the pop-up opens only
        # when "See open roles" is clicked.
        link = ('<a id="sor" href="/in/x/opportunities/hiring-opportunities/">See open roles</a>' if roles else "")
        script = ("<script>const d = " + repr(dialog).replace("</", "<\/") + ";"
                  "const l = document.getElementById('sor'); if (l) l.addEventListener('click', (e) => {"
                  " e.preventDefault(); setTimeout(() => document.body.insertAdjacentHTML('beforeend', d), 300); });</script>")
        return f"<html><body><main><h1>{name}</h1><div>Recruiter at EA</div>{hiring_box}{link}</main>{script}</body></html>"
    return f"<html><body><main><h1>{name}</h1><div>Recruiter at EA</div>{hiring_box}</main>{dialog}</body></html>"


def profile_page(spec: dict) -> str:
    """A person's profile (assumed markup). spec keys:
    top: name for the top card's "Current company: X" button (or None)
    entries: Experience entries, first = most recent. Each: {"title", "company", "slug" (or ""),
             "dates", "grouped": bool}
    variant: "limit" | "lazy" (Experience drawn only after scrolling) | ""
    """
    if spec.get("variant") == "limit":
        return "<html><body><main><h1>We noticed unusual activity from your account</h1></main></body></html>"
    top = ""
    if spec.get("top"):
        n = spec["top"]
        top = (f'<ul><li><button aria-label="Current company: {n}. Click to skip to experience card">'
               f'<img alt="{n} logo"><span>{n}</span></button></li></ul>')
    items = ""
    for e in spec.get("entries", []):
        logo = f'<a href="/company/{e["slug"]}/"><img alt="{e["company"]} logo"></a>' if e.get("slug") else ""
        if e.get("grouped"):
            items += (f"<li>{logo}{_line(e['company'])}{_line('Full-time · 3 yrs 2 mos')}"
                      f"<ul><li>{_line(e['title'])}{_line(e['dates'])}</li>"
                      f"<li>{_line('Recruiter')}{_line('Jan 2020 - Dec 2022 · 3 yrs')}</li></ul></li>")
        else:
            items += (f"<li>{logo}{_line(e['title'])}{_line(e['company'] + ' · Full-time')}"
                      f"{_line(e['dates'])}{_line('Bengaluru, Karnataka, India')}</li>")
    exp = (f'<section><div id="experience"></div><div><h2>{_line("Experience")}</h2></div>'
           f'<ul>{items}</ul></section>')
    head = f"<section><h1>Some Person</h1><div>Talent Acquisition | Hiring</div>{top}</section>"
    if spec.get("variant") == "messy":         # like real pages: location, work mode and skills lines around the job
        rows = ""
        for e in spec.get("entries", []):
            rows += (f'<div><a href="/company/{e["slug"]}/"><img alt="{e["company"]} logo"></a>'
                     f'{_line("Lead Talent Partner")}{_line("Full-time · 2 yrs")}{_line("Chennai, Tamil Nadu, India · On-site")}'
                     f'{_line(e["title"])}{_line(e["dates"])}{_line("Skills: Sourcing, Hiring")}</div>')
        return f"<html><body><main><div>{_line('Experience')}</div><div>{rows}</div></main></body></html>"
    if spec.get("variant") == "divs":          # no list items at all: the job is only text lines in divs
        rows = ""
        for e in spec.get("entries", []):
            logo = f'<a href="/company/{e["slug"]}/"><img alt="{e["company"]} logo"></a>' if e.get("slug") else ""
            rows += f"<div>{logo}{_line(e['title'])}{_line(e['company'] + ' · Full-time')}{_line(e['dates'])}</div>"
        return f"<html><body><main><div>{_line('Experience')}</div><div>{rows}</div></main></body></html>"
    if spec.get("variant") == "details":       # /in/<x>/details/experience/: just the list, no anchor
        return f"<html><body><main><section><h2>Experience</h2><div><ul>{items}</ul></div></section></main></body></html>"
    if spec.get("variant") == "lazy":
        return ("<html><body><main>" + head + '<div style="height:3000px"></div><div id="later"></div></main>'
                "<script>const exp = " + repr(exp).replace("</", "<\\/") + ";"
                "addEventListener('scroll', () => { const d = document.getElementById('later');"
                " if (scrollY > 100 && !d.innerHTML) d.innerHTML = exp; });</script></body></html>")
    return f"<html><body><main>{head}{exp}</main></body></html>"


# -------------------------------------------------------------- stand-in API
class Harness:
    def __init__(self, storage, port: int = 0):
        self.storage = storage
        self.heartbeats: list[str] = []
        self.errors: list[str] = []
        self.diagnostics: list[dict] = []
        self.overrides: dict = {}            # served at /api/extension/config (server-delivered rules)
        self.finishes: list[str] = []
        self.geo_calls: list[dict] = []
        self.ingested: list[dict] = []          # every person exactly as the extension sent it
        self.ext_version: str | None = None     # served at /api/extension/info when set
        self.dashboard_loads = 0
        self.rewinds: list[str] = []
        self.notes: list[dict] = []
        self.geo_api: dict | None = None       # set by a test: the stand-in location lookup's answer
        self.geo_api_v2: dict | None = None
        self.geo_calls: list = []
        self.filters: dict | None = None       # set by a test: show the stand-in filter bar on search pages
        self.search_loads: list[str] = []      # every search page served (path?query)
        self.touched: list[str] = []           # what the stand-in filter bar saw being clicked/typed
        self.details_on = False                    # False: /api/details/* answer 404 (no company details)
        self.profiles: dict = {}                   # profile slug -> profile_page() spec
        self.company_results: dict = {}            # search keywords -> [(slug, shown name)]
        self.company_about: dict = {}              # slug -> (industry, size, layout)
        self.company_variant = ""                  # "limit": company search shows a limit notice
        self.companies: list[dict] = []            # every POST /api/companies
        self.profile_reads: list[dict] = []        # every POST /api/details/profile
        self.roles_pages: dict = {}                # person slug -> open-roles page HTML (or (status, html))
        self.companies_on = False                  # ... and company tasks (switched off in the product)
        self.roles_loads: list[str] = []           # every open-roles page requested (person slugs)
        self.order: list[str] = []                 # "search:<page>" / "roles:<slug>" in the order served
        self.company_loads: list[str] = []         # every company page served
        self.lock = threading.Lock()
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def _send(self, code, body, ctype="application/json"):
                data = body.encode() if isinstance(body, str) else body
                self.send_response(code)
                self.send_header("Content-Type", ctype + "; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                u = urlparse(self.path)
                q = parse_qs(u.query)
                if u.path == "/":
                    outer.dashboard_loads += 1
                    return self._send(200, DASHBOARD, "text/html")
                if u.path == "/api/extension/info" and outer.ext_version:
                    return self._send(200, json.dumps({"version": outer.ext_version}))
                if u.path.startswith("/media/"):
                    return self._send(200, GIF, "image/gif")
                if u.path in ("/voyager/api/typeahead/hits", "/voyager/api/typeahead/hitsV2"):
                    outer.geo_calls.append({"q": u.query, "csrf": self.headers.get("csrf-token")})
                    ans = (outer.geo_api if u.path.endswith("/hits") else outer.geo_api_v2) or {"status": 404, "body": {}}
                    data = json.dumps(ans["body"]).encode()
                    self.send_response(ans["status"])
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if u.path == "/touch":
                    outer.touched.append(q.get("what", [""])[0])
                    return self._send(200, "{}")
                if u.path == "/search/results/people/fragment":
                    return self._send(200, fragment(int(q.get("page", ["2"])[0])), "text/html")
                if u.path.startswith("/search/results/people") and "dropah" in q                         and "activelyHiringForJobTitles" in q:
                    # stands in for LinkedIn silently dropping a filter the account can't use
                    keep = "&".join(p for p in u.query.split("&") if not p.startswith("activelyHiringForJobTitles="))
                    self.send_response(302)
                    self.send_header("Location", f"{u.path}?{keep}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if u.path.startswith("/search/results/people") and "freeah" in q                         and "activelyHiringForJobTitles" in q:
                    # a free account: with the Premium "Actively hiring" filter LinkedIn shows only 2 people
                    outer.search_loads.append(u.path + "?" + u.query)
                    two = "".join(card_html(p) for p in people_for_page(1)[:2])
                    # freeahmention: two more people are only MENTIONED on the page, each with a linked name
                    # ("X is hiring"), as seen on a real free account: they are not results.
                    mentions = ("<div><a href='/in/mention-one/'>Mention One</a> is hiring</div>"
                                "<div><a href='/in/mention-two/'>Mention Two</a> is hiring</div>"
                                if "freeahmention" in q else "")
                    return self._send(200, "<!doctype html><title>Search</title><main><div role='list'>" + two +
                                      "</div>" + mentions + "<p>Upgrade to Premium to see more</p></main>", "text/html")
                if u.path.startswith("/search/results/people"):
                    outer.search_loads.append(u.path + "?" + u.query)
                    outer.order.append("search:" + q.get("page", ["1"])[0])
                    page = int(q.get("page", ["1"])[0])
                    html = results_page(page, q.get("variant", [""])[0])
                    if outer.filters is not None:
                        if outer.filters.get("block") and "keywords" not in q:       # the set-up tab's page
                            html = results_page(1, "limit")
                        else:
                            html = html.replace("<body>", "<body>" + filter_bar(outer.filters), 1) if "<body>" in html \
                                else html + filter_bar(outer.filters)
                    return self._send(200, html, "text/html")
                if u.path == "/api/details/next" and outer.details_on:
                    # like app/main.py: company tasks (only when a test asks for them)
                    jid = q["job_id"][0]
                    task = outer.storage.next_detail_task(jid) if outer.companies_on else None
                    return self._send(200, json.dumps({"task": task}))
                if u.path.startswith("/in/") and u.path.endswith("/opportunities/hiring-opportunities/"):
                    slug = u.path.split("/")[2]
                    outer.roles_loads.append(slug)
                    outer.order.append("roles:" + slug)
                    ans = outer.roles_pages.get(slug)
                    if ans is None:
                        return self._send(404, "<html><body>Not found</body></html>", "text/html")
                    code, body = ans if isinstance(ans, tuple) else (200, ans)
                    return self._send(code, body, "text/html")
                if u.path.startswith("/in/"):
                    outer.company_loads.append(u.path)
                    return self._send(200, profile_page(outer.profiles.get(u.path.split("/")[2], {})), "text/html")
                if u.path.startswith("/search/results/companies"):
                    outer.company_loads.append(u.path + "?" + u.query)
                    kw = q.get("keywords", [""])[0]
                    return self._send(200, company_search_page(outer.company_results.get(kw, []), outer.company_variant), "text/html")
                if u.path.startswith("/company/") and u.path.endswith("/about/"):
                    outer.company_loads.append(u.path)
                    slug = u.path.split("/")[2]
                    ind, size, layout = outer.company_about.get(slug, ("", "", "dl"))
                    return self._send(200, company_about_page(ind, size, layout), "text/html")
                if u.path == "/api/extension/config":
                    return self._send(200, json.dumps({"overrides": outer.overrides}))
                if u.path == "/api/settings":
                    return self._send(200, json.dumps(outer.storage.get_settings()))
                if u.path == "/api/status":
                    j = outer.storage.get_job(q["job_id"][0])
                    return self._send(200 if j else 404, json.dumps(j or {}))
                self._send(404, "{}")

            def do_POST(self):
                u = urlparse(self.path)
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                st = outer.storage
                with outer.lock:
                    if u.path == "/api/heartbeat":
                        outer.heartbeats.append(body["message"])
                        st.set_message(body["job_id"], body["message"])
                        return self._send(200, '{"ok":true}')
                    if u.path == "/api/error":
                        outer.errors.append(body["message"])
                        st.set_error(body["job_id"], body["message"])
                        return self._send(200, '{"ok":true}')
                    if u.path == "/api/search/finish":
                        outer.finishes.append(body["reason"])
                        st.finish_job(body["job_id"], body["reason"])
                        return self._send(200, '{"ok":true}')
                    if u.path == "/api/search/rewind":
                        outer.rewinds.append(body["job_id"])
                        return self._send(200, json.dumps({"ok": st.rewind_job(body["job_id"])}))
                    if u.path == "/api/search/note":
                        outer.notes.append(body)
                        return self._send(200, '{"ok":true}')
                    if u.path == "/api/search/diagnostics":
                        outer.diagnostics.append(body)
                        return self._send(200, '{"ok":true}')
                    if u.path == "/api/companies":
                        outer.companies.append(body)
                        st.save_company(body["name"], body.get("industry", ""), body.get("size", ""),
                                        body.get("linkedin_url", ""), body["status"],
                                        by_link=bool(body.get("by_link")) and bool(body.get("linkedin_url")),
                                        note=body.get("note", ""))
                        return self._send(200, '{"ok":true}')
                    if u.path == "/api/details/page" and outer.details_on:
                        return self._send(200, json.dumps({"ok": st.count_detail_page(body["job_id"], 60, 200)}))
                    if u.path == "/api/details/profile" and outer.details_on:
                        outer.profile_reads.append(body)
                        return self._send(200, json.dumps(st.save_profile_company(
                            body["job_id"], body["person_id"], body.get("company", ""), body.get("title", ""),
                            body.get("company_url", ""), body.get("note", ""))))
                    if u.path == "/api/search/geo":
                        outer.geo_calls.append(body)
                        return self._send(200, '{"ok":true}')
                if u.path == "/api/ingest":
                    with outer.lock:
                        outer.ingested.extend(body.get("people", []))
                    r = st.ingest(body["job_id"], body.get("people", []), body.get("browser_id", "default"))
                    return self._send(200, json.dumps({k: r[k] for k in ("continue", "saved", "total", "target", "reason")}))
                if u.path == "/api/stop":
                    st.stop_job(parse_qs(u.query)["job_id"][0])
                    return self._send(200, '{"ok":true}')
                self._send(404, "{}")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        return f"http://localhost:{self.port}"

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
