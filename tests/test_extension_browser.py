"""Real-browser test: the REAL extension files, loaded into Chrome, run against
the mock results page from tests/harness.py.

What this does NOT prove (see harness.py): the mock markup is an assumption, and
the server side is a stand-in over the real Storage class, not app/main.py.

Needs Playwright and a Chrome that allows --load-extension (Chrome for Testing
or Chromium; normal branded Chrome 137+ ignores the flag). Set CHROME_PATH to
choose the browser. Skipped when none is found.
"""
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright  # noqa: E402

from app.storage import Storage  # noqa: E402
from tests.harness import Harness  # noqa: E402

EXT_SRC = Path(__file__).resolve().parent.parent / "extension"
SEARCH_PREFIX = "https://www.linkedin.com/search/results/people/"


def find_chrome():
    cands = [os.environ.get("CHROME_PATH", ""), "/usr/bin/google-chrome", shutil.which("chromium") or "",
             shutil.which("chromium-browser") or ""]
    return next((c for c in cands if c and os.path.exists(c)), None)


CHROME = find_chrome()
pytestmark = pytest.mark.skipif(CHROME is None, reason="no Chrome/Chromium that can load extensions")


def build_test_extension(dest: Path, base: str, cards_timeout_ms: int | None = None,
                         keep_default_server: bool = False) -> Path:
    """Copy the extension and point it at the local mock page and stand-in server.

    Only addresses are changed (and, for one test, the 'no cards' timeout).
    The reading, pacing and waiting code is the real code.
    """
    shutil.copytree(EXT_SRC, dest)
    m = json.loads((dest / "manifest.json").read_text())
    for c in m["content_scripts"]:            # the search reader and the page-visibility helper
        if "content_search.js" in c["js"] or "page_visibility.js" in c["js"]:
            c["matches"].append("http://localhost/search/results/people*")
    co = next(c for c in m["content_scripts"] if "content_company.js" in c["js"])
    co["matches"] += ["http://localhost/search/results/companies*", "http://localhost/company/*", "http://localhost/in/*"]
    (dest / "manifest.json").write_text(json.dumps(m))
    bg = (dest / "background.js").read_text()
    bg = bg.replace(f"'{SEARCH_PREFIX}'", f"'{base}/search/results/people/'")
    bg = bg.replace("'https://www.linkedin.com/search/results/companies/'", f"'{base}/search/results/companies/'")
    bg = bg.replace("const COMPANY_PREFIX = 'https://www.linkedin.com/company/'", f"const COMPANY_PREFIX = '{base}/company/'")
    bg = bg.replace("const PROFILE_PREFIX = 'https://www.linkedin.com/in/'", f"const PROFILE_PREFIX = '{base}/in/'")
    if not keep_default_server:          # else the real default (localhost:8000) stays, as shipped
        bg = bg.replace("'http://localhost:8000'", f"'{base}'")
    (dest / "background.js").write_text(bg)
    # The set-up tests need unknown cities: keep only Bengaluru and Kolkata (for the table's own test).
    cfg = (dest / "config.js").read_text()
    cfg = "\n".join(l for l in cfg.split("\n") if "supplied by the user" not in l or "'kolkata'" in l)
    (dest / "config.js").write_text(cfg)
    if cards_timeout_ms:
        cfg = (dest / "config.js").read_text()
        cfg = cfg.replace("cardsMs: 20000", f"cardsMs: {cards_timeout_ms}")
        (dest / "config.js").write_text(cfg)
    return dest


class Env:
    def __init__(self, tmp: Path, cards_timeout_ms=None, keep_default_server=False):
        self.storage = Storage(str(tmp / "b.db"))
        self.h = Harness(self.storage).start()
        self.ext = build_test_extension(tmp / "ext", self.h.base, cards_timeout_ms, keep_default_server)
        self.pw = sync_playwright().start()
        self.ctx = self.pw.chromium.launch_persistent_context(
            str(tmp / "profile"), executable_path=CHROME, headless=False,
            args=["--headless=new", "--no-sandbox", f"--disable-extensions-except={self.ext}",
                  f"--load-extension={self.ext}"])
        self.dash = self.ctx.new_page()
        self.dash.goto(self.h.base + "/")
        self.dash.wait_for_function("document.documentElement.dataset.hbfExt === '1'", timeout=15000)

    def worker(self):
        for _ in range(50):
            if self.ctx.service_workers:
                return self.ctx.service_workers[0]
            time.sleep(0.2)
        raise AssertionError("extension service worker did not start")

    def launch(self, job_id, query=""):
        url = f"{self.h.base}/search/results/people/?keywords=x{query}"
        self.dash.evaluate("([url, jobId]) => window.postMessage({source:'hbf-dashboard', type:'open', url, jobId}, '*')",
                           [url, job_id])

    def launch_geo(self, job_id, locations, extra=""):
        """Like the dashboard: keywords URL plus the base URL the extension adds &geoUrn= to."""
        url = f"{self.h.base}/search/results/people/?keywords=x{extra}"
        self.dash.evaluate("([url, jobId, base, locs]) => window.postMessage({source:'hbf-dashboard', type:'open',"
                           " url, jobId, geoUrlBase: base, locations: locs}, '*')",
                           [url, job_id, url + "&nogeo=1", locations])

    def search_tab_url(self, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            tabs = self.worker().evaluate("chrome.tabs.query({}).then(t => t.map(x => x.url))")
            u = next((u for u in tabs if "/search/results/people" in u), None)
            if u:
                return u
            time.sleep(0.2)
        raise AssertionError("search tab never opened")

    def wait_job(self, job_id, timeout=90):
        end = time.time() + timeout
        while time.time() < end:
            j = self.storage.get_job(job_id)
            if j and j["status"] != "running":
                return j
            time.sleep(0.3)
        raise AssertionError("search did not finish: it hung. heartbeats=" + repr(self.h.heartbeats[-5:]))

    def close(self):
        try:
            self.ctx.close()
        finally:
            self.pw.stop()
            self.h.stop()


@pytest.fixture()
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.close()


def new_job(env, target=25):
    return env.storage.create_job(["Engineering Manager"], ["Bengaluru"], [], target,
                                  text_filter_places=["Bengaluru"])


def test_bridge_marks_extension_installed(env):
    ds = env.dash.evaluate("({...document.documentElement.dataset})")
    assert ds["hbfExt"] == "1"
    assert ds["hbfExtVersion"] == json.loads((EXT_SRC / "manifest.json").read_text())["version"]


def test_two_page_run_reads_every_card_and_detects_badges(env):
    job_id = new_job(env)
    t0 = time.time()
    env.launch(job_id)

    # while it runs: the LinkedIn tab must be a background tab inside a collapsed group
    seen_tab = None
    end = time.time() + 40
    # the tab is created first and grouped a moment later, so wait for both
    while time.time() < end and (seen_tab is None or seen_tab["groupId"] == -1):
        tabs = env.worker().evaluate("chrome.tabs.query({}).then(t => t.map(x => ({url:x.url, active:x.active, groupId:x.groupId})))")
        seen_tab = next((t for t in tabs if "/search/results/people" in t["url"]), None)
        time.sleep(0.2)
    assert seen_tab is not None, "search tab never opened"
    assert seen_tab["active"] is False
    assert seen_tab["groupId"] != -1
    groups = env.worker().evaluate("chrome.tabGroups.query({}).then(g => g.map(x => ({title:x.title, collapsed:x.collapsed})))")
    assert {"title": "Hiring Badge Finder", "collapsed": True} in groups

    job = env.wait_job(job_id)
    elapsed = time.time() - t0

    assert env.h.errors == []
    assert job["status"] == "done" and job["end_reason"] == "no more pages"
    c = job
    assert (c["pages"], c["total_read"], c["saved"]) == (2, 20, 5)
    assert c["duplicates"] == 1                # person-1 appears again on page 2 (different capitalisation)
    assert not any("showing the LinkedIn tab" in m for m in env.h.heartbeats)   # every card drawn: never shown
    assert c["not_confirmed"] == 3             # persons 4, 5 (open roles) and 6 (hiring words only), no frame
    assert c["not_hiring"] == 11
    assert c["other_locations"] == 0 and c["already_collected"] == 0

    people = {p["name"]: p for p in env.storage.all_leads({"job_id": job_id})}
    assert set(people) == {"Person 1", "Person 2", "Person 3", "Person 11", "Person 12"}
    assert all(p["status"] == "Badge confirmed" for p in people.values())
    assert people["Person 1"]["evidence"] == "frame + See open roles + 'Senior Engineering Manager - Hiring SDETs for my team'"
    assert people["Person 2"]["evidence"] == "frame + See open roles"
    assert people["Person 3"]["headline"] == "Software Engineer at Company3"
    # action button text (Connect/Follow/Message) never becomes the location
    assert all(p["location"] == "Bengaluru, Karnataka, India" for p in people.values())
    # one lead per person: mutual connections and /opportunities/ links are not people
    assert {p["profile_url"] for p in people.values()} == {
        f"https://www.linkedin.com/in/person-{n}" for n in (1, 2, 3, 11, 12)}
    # headline has no ' at ': title and company come from the card's "Current: Team lead at Company1" line
    assert (people["Person 1"]["title"], people["Person 1"]["company"]) == ("Team lead", "Company1")
    assert people["Person 11"]["title"] == "Software Engineer"          # the headline still wins when it has both
    assert next(p for p in env.h.ingested if p["name"] == "Person 1")["current"] == "Team lead at Company1"
    assert people["Person 11"]["company"] == "Company11"
    assert people["Person 1"]["location"] == "Bengaluru, Karnataka, India"
    assert people["Person 1"]["profile_url"] == "https://www.linkedin.com/in/person-1"   # query string removed

    # heartbeats shown on the dashboard
    hb = env.h.heartbeats
    assert not any(h.startswith("Warning: badge markers are not checked") for h in hb)   # verified: true
    # 10 cards, not one per /in/ link; person 8 is framed without a hiring link
    assert "page 1: 10 cards, 3 badges, 1 framed without open roles (skipped), 5 with open roles" in hb
    assert "page 2: 10 cards, 3 badges, 0 framed without open roles (skipped), 3 with open roles" in hb
    # person 8 (other frame type) is neither saved nor sent as a badge
    assert "Person 8" not in people
    p8 = next(p for p in env.h.ingested if p["name"] == "Person 8")
    assert (p8["badge_frame"], p8["open_roles"]) == (False, False)
    assert all("framed_skipped" not in p for p in env.h.ingested)     # server schema unchanged
    assert any(h.startswith("page 1: waiting ") for h in hb)

    # pacing: at least 4 s between result pages (random 4 to 8 s)
    assert elapsed >= 4.0
    assert elapsed < 60, f"run took {elapsed:.0f}s"

    # tab closes by itself a few seconds after finishing
    end = time.time() + 15
    while time.time() < end:
        tabs = env.worker().evaluate("chrome.tabs.query({}).then(t => t.map(x => x.url))")
        if not any("/search/results/people" in u for u in tabs):
            break
        time.sleep(0.5)
    else:
        raise AssertionError("search tab was not closed")

    # the dashboard tab was never replaced
    assert env.dash.url.rstrip("/") == env.h.base


def test_lazy_cards_are_all_rendered(env):
    """The mock page shows only 5 cards until scrolled; the run must still read all 10."""
    job_id = new_job(env, target=25)
    env.launch(job_id)
    job = env.wait_job(job_id)
    assert job["total_read"] == 20           # 10 + 10, not 5 + 5


def test_stops_at_target(env):
    job_id = new_job(env, target=3)
    env.launch(job_id)
    job = env.wait_job(job_id)
    assert job["saved"] == 3 and job["end_reason"] == "target reached" and job["pages"] == 1


@pytest.mark.parametrize("variant,needle,reason", [
    ("limit", "unusual activity", None),
    ("commercial", "commercial use limit", None),
])
def test_limit_pages_stop_immediately_with_clear_message(env, variant, needle, reason):
    job_id = new_job(env)
    env.launch(job_id, f"&variant={variant}")
    job = env.wait_job(job_id, timeout=40)
    assert job["status"] == "error"
    assert needle in job["error"].lower()
    assert job["saved"] == 0 and job["pages"] == 0
    # the tab is kept (not closed) so the person can see LinkedIn's notice
    time.sleep(4)
    tabs = env.worker().evaluate("chrome.tabs.query({}).then(t => t.map(x => x.url))")
    assert any("/search/results/people" in u for u in tabs)


def test_no_results_page_ends_cleanly(env):
    job_id = new_job(env)
    env.launch(job_id, "&variant=empty")
    job = env.wait_job(job_id, timeout=40)
    assert job["status"] == "done" and job["end_reason"] == "no more pages" and job["saved"] == 0
    assert env.h.errors == []


def test_stop_button_closes_the_tab(env):
    job_id = new_job(env)
    env.launch(job_id)
    end = time.time() + 30
    while time.time() < end and not env.h.heartbeats:
        time.sleep(0.2)
    env.storage.stop_job(job_id)
    env.dash.evaluate("(id) => window.postMessage({source:'hbf-dashboard', type:'stop', jobId:id}, '*')", job_id)
    end = time.time() + 15
    while time.time() < end:
        tabs = env.worker().evaluate("chrome.tabs.query({}).then(t => t.map(x => x.url))")
        if not any("/search/results/people" in u for u in tabs):
            break
        time.sleep(0.3)
    else:
        raise AssertionError("tab stayed open after Stop")
    assert env.storage.get_job(job_id)["status"] == "stopped"


def test_refuses_to_open_non_linkedin_addresses(env):
    job_id = new_job(env)
    env.dash.evaluate("(id) => window.postMessage({source:'hbf-dashboard', type:'open', url:'https://example.com/x', jobId:id}, '*')", job_id)
    env.dash.wait_for_function("window.__events && window.__events.length > 0", timeout=10000)
    ev = env.dash.evaluate("window.__events[0]")
    assert ev["ok"] is False and "LinkedIn" in ev["error"]
    tabs = env.worker().evaluate("chrome.tabs.query({}).then(t => t.map(x => x.url))")
    assert not any("example.com" in u for u in tabs)


def test_no_cards_ever_appearing_does_not_hang(tmp_path):
    e = Env(tmp_path, cards_timeout_ms=3000)
    try:
        job_id = new_job(e)
        e.launch(job_id, "&variant=nocards")
        job = e.wait_job(job_id, timeout=40)
        assert job["status"] == "error" and "no results appeared" in job["error"]
    finally:
        e.close()


def test_two_searches_at_once_do_not_mix(env):
    """Two searches started in the same moment each run to the end, in their own tab."""
    a, b = new_job(env), new_job(env)
    env.launch(a)
    env.launch(b)
    ja, jb = env.wait_job(a, timeout=120), env.wait_job(b, timeout=120)
    assert env.h.errors == []
    for j in (ja, jb):
        assert j["status"] == "done" and j["pages"] == 2 and j["total_read"] == 20
    # the same 5 badge people were on both searches: each is saved by one search and
    # counted as "already collected" by the other
    assert ja["saved"] + jb["saved"] == 5
    assert ja["already_collected"] + jb["already_collected"] == 5
    owners = {p["job_id"] for p in env.storage.all_leads()}
    assert owners <= {a, b}
    # heartbeats of both searches reached the server
    assert env.h.heartbeats.count(
        "page 1: 10 cards, 3 badges, 1 framed without open roles (skipped), 5 with open roles") == 2


@pytest.mark.parametrize("places", [["Bengaluru, Karnataka, India"], ["bangalore"],
                                    ["Bengaluru", "BANGALORE, India"]])
def test_known_places_use_geo_ids(env, places):
    job_id = new_job(env)
    env.launch_geo(job_id, places)
    url = env.search_tab_url()
    assert "&nogeo=1&geoUrn=%5B%22105214831%22%5D" in url        # base URL + one deduplicated ID
    env.wait_job(job_id)
    assert env.h.geo_calls[-1] == {"job_id": job_id, "geo_ids": ["105214831"] * len(places), "widened": False}


def open_result(env, job_id, places, timeout=15):
    """Launch like the dashboard and return the extension's answer to it."""
    env.dash.evaluate("window.__res = null; window.addEventListener('message', e => {"
                      " if (e.data && e.data.type === 'open-result') window.__res = e.data; })")
    env.launch_geo(job_id, places)
    env.dash.wait_for_function("window.__res !== null", timeout=timeout * 1000)
    return env.dash.evaluate("window.__res")


def search_tabs(env):
    return [u for u in env.worker().evaluate("chrome.tabs.query({}).then(t => t.map(x => x.url))")
            if "/search/results/people" in u]


def test_place_without_id_is_refused_and_nothing_opens(env):
    job_id = new_job(env)
    res = open_result(env, job_id, ["Bengaluru", "Pune, Maharashtra, India"])
    assert res["ok"] is False and res["needPlaces"] == ["Pune, Maharashtra, India"]
    time.sleep(1)
    assert search_tabs(env) == []                                   # no LinkedIn page, no reads
    assert env.h.ingested == []


def test_taught_place_is_used_for_the_linkedin_filter(env):
    # the person clicks "Set up on LinkedIn" for Pune
    env.dash.evaluate("window.__t = null; window.addEventListener('message', e => {"
                      " if (e.data && (e.data.type === 'teach-result' || e.data.type === 'geo-learned')) window.__t = e.data; });"
                      " window.postMessage({source:'hbf-dashboard', type:'teach', jobId:'t1', place:'Pune, Maharashtra, India'}, '*')")
    env.dash.wait_for_function("window.__t && window.__t.type === 'teach-result'", timeout=15000)
    assert env.dash.evaluate("window.__t.ok") is True
    tab_url = search_tabs(env)[0]
    assert "keywords=Pune" in tab_url and "geoUrn" not in tab_url
    # ... and picks Pune in LinkedIn's Locations filter: the address gets its location ID
    env.worker().evaluate("""async (id) => { const [t] = await chrome.tabs.query({}).then(ts => ts.filter(x => x.url.includes('/search/results/people')));
        await chrome.tabs.update(t.id, { url: t.url + '&geoUrn=' + encodeURIComponent(JSON.stringify([id])) }); }""", "114806696")
    env.dash.wait_for_function("window.__t && window.__t.type === 'geo-learned'", timeout=15000)
    assert env.dash.evaluate("window.__t.place") == "Pune, Maharashtra, India"
    learned = env.worker().evaluate("chrome.storage.local.get('geoLearned').then(x => x.geoLearned)")
    assert learned == {"pune, maharashtra, india": "114806696"}
    # a search for Pune now filters by that ID on LinkedIn
    job_id = new_job(env)
    env.launch_geo(job_id, ["Pune, Maharashtra, India"])
    deadline = time.time() + 20
    while time.time() < deadline and not any("nogeo=1" in u for u in search_tabs(env)):
        time.sleep(0.3)
    url = next(u for u in search_tabs(env) if "nogeo=1" in u)
    assert "geoUrn=%5B%22114806696%22%5D" in url
    env.wait_job(job_id)
    assert env.h.geo_calls[-1] == {"job_id": job_id, "geo_ids": ["114806696"], "widened": False}


def test_actively_hiring_kept_gives_no_warning(env):
    job_id = new_job(env)
    env.launch(job_id, "&activelyHiringForJobTitles=%5B%22-100%22%5D")
    env.wait_job(job_id)
    assert not any("Actively hiring" in h for h in env.h.heartbeats)


def test_actively_hiring_dropped_by_linkedin_is_reported(env):
    job_id = new_job(env)
    env.launch(job_id, "&activelyHiringForJobTitles=%5B%22-100%22%5D&dropah=1")
    job = env.wait_job(job_id)
    assert job["status"] == "done" and job["total_read"] == 20     # the run still works
    assert any(h.startswith('Warning: LinkedIn ignored the "Actively hiring" filter') for h in env.h.heartbeats)


def test_card_edge_cases_read_accurately(env):
    """Task 4: no photo, photo without frame, 3rd+, #hiring without frame, no location,
    no headline, name repeated for screen readers, '@' in the headline."""
    env.storage.set_settings(save_only_confirmed=False)
    job_id = new_job(env)
    env.launch(job_id, "&variant=edge")
    job = env.wait_job(job_id)
    assert env.h.errors == [] and job["total_read"] == 7
    got = {p["name"]: p for p in env.h.ingested}
    assert set(got) == {"Edge Nophoto", "Edge Plain", "Edge Hashtag", "Edge Noloc", "Edge Nohead", "Edge Twice",
                        "Edge Inline"}
    # "Edge Inline • 2nd" on one line: name without the suffix, degree kept
    assert (got["Edge Inline"]["degree"], got["Edge Inline"]["headline"]) == ("2nd", "EM at Acme")
    assert {n: p["badge_frame"] for n, p in got.items()} == {
        "Edge Nophoto": False,      # no own photo; a mutual connection's framed avatar must not count
        "Edge Plain": False,        # photo, no frame
        "Edge Hashtag": False,      # '#hiring' in the headline is not a frame
        "Edge Noloc": True, "Edge Nohead": True, "Edge Twice": True, "Edge Inline": False}
    assert got["Edge Nophoto"]["profile_url"] == "https://www.linkedin.com/in/edge-nophoto"
    assert (got["Edge Nophoto"]["headline"], got["Edge Nophoto"]["location"]) == (
        "Engineering Manager at Acme", "Bengaluru, Karnataka, India")
    assert got["Edge Plain"]["degree"] == "3rd+"
    # a missing line stays empty; the summary paragraph never fills the gap
    assert (got["Edge Noloc"]["headline"], got["Edge Noloc"]["location"]) == ("Director at Initech", "")
    assert (got["Edge Nohead"]["headline"], got["Edge Nohead"]["location"]) == ("", "Greater Bengaluru Area")
    assert (got["Edge Twice"]["headline"], got["Edge Twice"]["location"]) == (
        "Staff Architect@Globex | Ex Initech", "Bengaluru, Karnataka, India")

    leads = {p["name"]: p for p in env.storage.all_leads({"job_id": job_id})}
    assert leads["Edge Hashtag"]["status"] == "Likely hiring"
    assert "#hiring" in leads["Edge Hashtag"]["evidence"]
    assert "Edge Noloc" not in leads        # empty location can't pass the Bengaluru text filter
    assert "Edge Plain" not in leads and "Edge Nophoto" not in leads   # no hiring signal


def test_continued_search_reads_only_the_new_page(env):
    job_id = new_job(env)
    env.launch(job_id, "&page=2")                 # continue at page 2 (the last page of the mock)
    job = env.wait_job(job_id)
    assert env.h.errors == [] and env.h.rewinds == []
    assert (job["pages"], job["total_read"], job["end_reason"]) == (1, 10, "no more pages")
    assert {p["name"] for p in env.h.ingested} >= {"Person 11", "Person 12"}
    assert not any(p["name"] == "Person 2" for p in env.h.ingested)   # page 1 was not read again


def test_empty_continued_page_falls_back_to_page_one(env):
    job_id = new_job(env)
    env.launch(job_id, "&page=5")                 # beyond the end: empty
    job = env.wait_job(job_id)
    assert env.h.rewinds == [job_id] and env.h.errors == []
    assert job["status"] == "done" and job["pages"] == 2 and job["total_read"] == 20   # pages 1 and 2, same job



def test_older_linkedin_layout_is_read_by_the_fallback(env):
    """LinkedIn sometimes serves an older results layout (no role="listitem")."""
    job_id = new_job(env)
    env.launch(job_id, "&variant=classic")
    job = env.wait_job(job_id)
    assert env.h.errors == []
    assert (job["pages"], job["total_read"]) == (1, 10)                 # 10 cards, not one per link
    hb = env.h.heartbeats
    assert "page 1: 10 cards, 3 badges, 1 framed without open roles (skipped), 5 with open roles" in hb
    got = {p["name"]: p for p in env.h.ingested}
    assert set(got) == {f"Person {n}" for n in range(1, 11)}            # never "Status is offline"
    p1 = got["Person 1"]
    assert p1["headline"] == "Senior Engineering Manager - Hiring SDETs for my team"   # not "3rd+ degree connection"
    assert (p1["location"], p1["degree"]) == ("Bengaluru, Karnataka, India", "3rd+")
    assert p1["profile_url"] == "https://www.linkedin.com/in/person-1"
    assert got["Person 9"]["badge_frame"] is False                     # its mutual's framed avatar doesn't count
    assert {p["name"] for p in env.storage.all_leads({"job_id": job_id})} == {"Person 1", "Person 2", "Person 3"}


def test_people_shown_but_unreadable_gives_a_clear_message(tmp_path):
    e = Env(tmp_path, cards_timeout_ms=3000)
    try:
        job_id = new_job(e)
        e.launch(job_id, "&variant=unreadable")
        job = e.wait_job(job_id, timeout=40)
        assert job["status"] == "error"
        assert "LinkedIn shows 3 people, but this page layout could not be read" in job["error"]
    finally:
        e.close()



def test_any_layout_is_read_from_the_page_structure(env):
    """No li, no role="listitem", no <figure>: the repeated list of people is found anyway,
    and a sidebar of other people is ignored."""
    job_id = new_job(env)
    env.launch(job_id, "&variant=divlayout")
    job = env.wait_job(job_id)
    assert env.h.errors == [] and (job["pages"], job["total_read"]) == (1, 10)
    names = {p["name"] for p in env.h.ingested}
    assert names == {f"Person {n}" for n in range(1, 11)}              # no "Side Person", no "Me"
    assert "page 1: 10 cards, 3 badges, 1 framed without open roles (skipped), 5 with open roles" in env.h.heartbeats


def test_small_listitem_box_does_not_hide_the_real_results(env):
    """Another account's page: 2 people in a box marked role="listitem", the real results drawn as
    plain divs, no Next button the tool knows. All results are read, every page, until the end."""
    job_id = new_job(env)
    env.launch(job_id, "&variant=decoy")
    job = env.wait_job(job_id)
    assert env.h.errors == []
    assert job["status"] == "done" and job["end_reason"] == "no more pages"
    assert (job["pages"], job["total_read"]) == (2, 20)                 # was (1, 2): only the box was read
    assert not any(p["name"].startswith("Decoy") for p in env.h.ingested)


def test_results_the_tab_never_draws_are_read_from_linkedins_page_data(env):
    """The other account's case: the tab draws only 2 of 10 people and shows no count or Next button.
    Every person is read from the page as LinkedIn's server sends it, with the same fields and badge rule
    as a fully drawn page, and every page is read."""
    job_id = new_job(env)
    env.launch(job_id, "&variant=served")
    job = env.wait_job(job_id, timeout=90)
    assert env.h.errors == []
    assert (job["pages"], job["total_read"], job["saved"]) == (2, 20, 5)       # the same as a fully drawn run
    hb = env.h.heartbeats
    assert "page 1: 10 cards, 3 badges, 1 framed without open roles (skipped), 5 with open roles" in hb
    assert any("read all 10 from LinkedIn's page data" in m for m in hb)
    got = {p["name"]: p for p in env.h.ingested}
    p1 = got["Person 1"]                                                   # same fields as the drawn page gives
    assert p1["headline"] == "Senior Engineering Manager - Hiring SDETs for my team"
    assert p1["location"] == "Bengaluru, Karnataka, India"
    assert p1["profile_url"] == "https://www.linkedin.com/in/person-1"
    assert got["Person 9"]["badge_frame"] is False                         # its mutual's framed avatar doesn't count


def test_results_loaded_only_when_visible_are_loaded_in_the_background_tab(env):
    """The other accounts' page: only 2 people arrive; LinkedIn's page loads the rest when its "load more"
    area is visible, which a background tab never is. The page is told it is visible, loads its own
    results, and every person on every page is read with the same rules."""
    job_id = new_job(env)
    env.launch(job_id, "&variant=lazyload")
    job = env.wait_job(job_id, timeout=90)
    assert env.h.errors == []
    assert (job["pages"], job["total_read"], job["saved"]) == (2, 20, 5)       # was (1, 2, ...)
    assert "page 1: 10 cards, 3 badges, 1 framed without open roles (skipped), 5 with open roles" in env.h.heartbeats
    assert env.h.diagnostics == []


def test_page_loading_only_on_screen_is_shown_briefly_then_dashboard_comes_back(env):
    """Accounts whose LinkedIn page loads the rest only while on screen: the LinkedIn tab is brought to the
    front for a few seconds, every person is read with the same rules, and the dashboard tab is in front again."""
    env.h.overrides = {"visibleRead": {"assumeHidden": True}}          # headless test browser: tabs never hidden
    job_id = new_job(env)
    env.launch(job_id, "&variant=onscreen")
    shown_pages, end = set(), time.time() + 90
    while time.time() < end and env.storage.get_job(job_id)["status"] == "running":
        tabs = env.worker().evaluate("chrome.tabs.query({}).then(t => t.map(x => ({url:x.url, active:x.active})))")
        for t in tabs:
            if "variant=onscreen" in t["url"] and t["active"]:
                pg = next((p for p in env.ctx.pages if p.url == t["url"]), None)
                if pg:
                    try:
                        pg.evaluate("window.__loadRest && window.__loadRest()")   # what LinkedIn does on screen
                        shown_pages.add(t["url"])
                    except Exception:
                        pass
        time.sleep(0.3)
    job = env.wait_job(job_id, timeout=30)
    assert env.h.errors == []
    assert len(shown_pages) == 2                                           # both pages were brought to the front
    assert (job["pages"], job["total_read"], job["saved"]) == (2, 20, 5)   # same as a fully drawn run
    assert any("showing the LinkedIn tab for a few seconds" in m for m in env.h.heartbeats)
    tabs = env.worker().evaluate("chrome.tabs.query({}).then(t => t.map(x => ({url:x.url, active:x.active})))")
    assert not any("/search/results/people" in t["url"] and t["active"] for t in tabs)   # LinkedIn tab not left in front


def test_visibility_help_is_only_on_search_pages_and_only_while_hidden():
    import json as _json
    m = _json.loads((EXT_SRC / "manifest.json").read_text(encoding="utf-8"))
    cs = next(c for c in m["content_scripts"] if c["js"] == ["page_visibility.js"])
    assert cs["matches"] == ["https://www.linkedin.com/search/results/people*"]
    assert cs["world"] == "MAIN" and cs["run_at"] == "document_start"
    src = (EXT_SRC / "page_visibility.js").read_text(encoding="utf-8")
    assert "if (!hidden() || !target) return;" in src and "if (!hidden()) return nativeRaf(cb);" in src
    assert "fetch(" not in src and "chrome." not in src and "postMessage" not in src   # changes visibility only


def test_results_only_in_the_react_stream_are_rebuilt_and_read(env):
    """LinkedIn's newer page (any account): the HTML holds 2 people, the other 8 exist only in the page's
    React Server Components stream. The page is rebuilt from the stream and read with the same rules:
    same counts, fields and badges as a fully drawn run."""
    job_id = new_job(env)
    env.launch(job_id, "&variant=stream")
    job = env.wait_job(job_id, timeout=90)
    assert env.h.errors == []
    assert (job["pages"], job["total_read"], job["saved"]) == (2, 20, 5)       # the same as a fully drawn run
    hb = env.h.heartbeats
    assert "page 1: 10 cards, 3 badges, 1 framed without open roles (skipped), 5 with open roles" in hb
    got = {p["name"]: p for p in env.h.ingested}
    assert {"Person 3", "Person 10", "Person 13", "Person 19"} <= set(got)  # people only in the stream, both pages
    p1 = got["Person 1"]
    assert p1["headline"] == "Senior Engineering Manager - Hiring SDETs for my team"
    assert p1["location"] == "Bengaluru, Karnataka, India" and p1["degree"] == "3rd+"
    assert p1["profile_url"] == "https://www.linkedin.com/in/person-1"
    assert p1["badge_frame"] is True and p1["open_roles"] is True
    assert got["Person 9"]["badge_frame"] is False                         # its mutual's framed avatar doesn't count
    assert env.h.diagnostics == []                                         # nothing was missing


def test_slowly_drawn_page_is_waited_for_and_every_page_read(env):
    """A page that draws 2 people first and the rest seconds later (hidden tab on another account):
    the tool waits for them, and LinkedIn's "About 20 results" keeps it going without a Next button."""
    job_id = new_job(env)
    env.launch(job_id, "&variant=slowdraw")
    job = env.wait_job(job_id, timeout=90)
    assert env.h.errors == []
    assert (job["pages"], job["total_read"]) == (2, 20)                 # was (1, 2)
    assert env.h.diagnostics == []                                      # nothing was missing


def test_page_drawing_too_few_people_sends_its_structure(env):
    """Only 2 of the people LinkedIn says it has are ever drawn: the run says so in the log and sends
    the page's structure (no names or links) to the dashboard, so nobody has to run anything."""
    job_id = new_job(env)
    env.launch(job_id, "&variant=twoonly")
    job = env.wait_job(job_id, timeout=120)
    assert env.h.errors == [] and job["pages"] == 2                      # "About 20 results": page 2 tried too
    assert any("read 2 of 10 people LinkedIn shows (about 20 results)" in m for m in env.h.heartbeats)
    d = env.h.diagnostics[0]
    assert d["page"] == 1 and d["data"]["cardsRead"] == 2 and d["data"]["total"] == 20
    sent = json.dumps(d["data"])
    assert "Person " not in sent and "/in/" not in sent                  # structure only, no personal data


def test_late_results_are_not_confused_with_a_sidebar(env):
    job_id = new_job(env)
    env.launch(job_id, "&variant=sidebarfirst")
    job = env.wait_job(job_id)
    assert env.h.errors == [] and job["total_read"] == 20
    assert not any(p["name"].startswith("Side Person") for p in env.h.ingested)



def test_older_layout_with_actively_hiring_stops_after_page_one(env):
    """The older page ignores the Actively hiring filter: stop instead of reading many non-hiring people."""
    job_id = new_job(env)
    env.launch(job_id, "&variant=classic&activelyHiringForJobTitles=%5B%22-100%22%5D")
    job = env.wait_job(job_id)
    assert job["status"] == "error" and "older page for this search" in job["error"]
    assert job["pages"] == 0 and job["total_read"] == 0 and env.h.ingested == []    # no reads used



@pytest.mark.parametrize("places,ids", [
    (["Bengaluru, Karnataka, India"], ["105214831"]),                      # the one captured ID
    (["bangalore", "Bengaluru, India"], ["105214831", "105214831"]),
])
def test_built_in_places_use_their_own_id(env, places, ids):
    job_id = new_job(env)
    env.launch_geo(job_id, places)
    url = env.search_tab_url()
    in_url = list(dict.fromkeys(ids))                                    # the address lists each ID once
    assert "geoUrn=" + json.dumps(in_url, separators=(",", ":")).replace('"', "%22").replace("[", "%5B").replace("]", "%5D").replace(",", "%2C") in url
    env.wait_job(job_id)
    assert env.h.geo_calls[-1] == {"job_id": job_id, "geo_ids": ids, "widened": False}   # one per place


# ------------------------------------------------------------ server address (item A)
def test_search_from_dashboard_on_other_port_works_without_options(tmp_path):
    """Extension as shipped (default address localhost:8000, Options value unset); the dashboard runs on
    another port. The search must report to the dashboard it was launched from."""
    env = Env(tmp_path, keep_default_server=True)
    try:
        assert ":8000" not in env.h.base                                      # a non-default port
        bg = (env.ext / "background.js").read_text()
        assert "const DEFAULT_SERVER = 'http://localhost:8000';" in bg        # shipped default kept
        assert env.worker().evaluate("chrome.storage.sync.get('serverUrl').then(x => x.serverUrl || null)") is None
        job_id = new_job(env)
        env.launch(job_id)
        job = env.wait_job(job_id)
        assert job["status"] == "done" and job["pages"] == 2 and job["saved"] > 0
        # the Options value is still unset (not overwritten by the launch)
        assert env.worker().evaluate("chrome.storage.sync.get('serverUrl').then(x => x.serverUrl || null)") is None
    finally:
        env.close()


def test_unreachable_dashboard_server_is_named_on_the_dashboard(env):
    """The dashboard page is open but its server has stopped: the page says which address was tried."""
    import http.server
    import threading

    class Page(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<!doctype html><title>dash</title><p>dashboard</p>"
            self.send_response(200); self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{srv.server_address[1]}"
    dash = env.ctx.new_page()
    dash.goto(origin + "/")
    dash.wait_for_function("document.documentElement.dataset.hbfExt === '1'", timeout=15000)
    srv.shutdown(); srv.server_close()                                       # the server is gone now
    dash.evaluate("window.__err = null; window.addEventListener('message', e => {"
                  " if (e.data && e.data.type === 'server-error') window.__err = e.data.message; })")
    job_id = new_job(env)
    url = f"{env.h.base}/search/results/people/?keywords=x"
    dash.evaluate("([url, jobId]) => window.postMessage({source:'hbf-dashboard', type:'open', url, jobId}, '*')", [url, job_id])
    dash.wait_for_function("window.__err !== null", timeout=30000)
    msg = dash.evaluate("window.__err")
    assert origin in msg and "Could not reach the dashboard server at" in msg



@pytest.mark.parametrize("place", ["Hyderabad, Telangana, India", "Chennai", "Visakhapatnam", "India"])
def test_places_without_a_captured_id_need_set_up(env, place):
    job_id = new_job(env)
    res = open_result(env, job_id, [place])
    assert res["ok"] is False and res["needPlaces"] == [place]



def test_place_known_on_the_dashboard_server_is_used(env):
    """A city set up once is shared through the dashboard server: this browser has never learned it."""
    job_id = new_job(env)
    url = f"{env.h.base}/search/results/people/?keywords=x"
    env.dash.evaluate("([url, jobId, base, known]) => window.postMessage({source:'hbf-dashboard', type:'open',"
                      " url, jobId, geoUrlBase: base, locations: ['Pune, Maharashtra, India'], known}, '*')",
                      [url, job_id, url + "&nogeo=1", {"pune, maharashtra, india": "114806696"}])
    tab = env.search_tab_url()
    assert "geoUrn=%5B%22114806696%22%5D" in tab
    env.wait_job(job_id)


def test_set_up_tab_closes_and_dashboard_comes_back(env):
    env.dash.evaluate("window.__t = null; window.addEventListener('message', e => {"
                      " if (e.data && e.data.type === 'geo-learned') window.__t = e.data; });"
                      " window.postMessage({source:'hbf-dashboard', type:'teach', jobId:'t2', place:'Pune'}, '*')")
    deadline = time.time() + 15
    while time.time() < deadline and not search_tabs(env):
        time.sleep(0.3)
    env.worker().evaluate("""async (id) => { const [t] = (await chrome.tabs.query({})).filter(x => x.url.includes('/search/results/people'));
        await chrome.tabs.update(t.id, { url: t.url + '&geoUrn=' + encodeURIComponent(JSON.stringify([id])) }); }""", "114806696")
    env.dash.wait_for_function("window.__t !== null", timeout=15000)
    deadline = time.time() + 10
    while time.time() < deadline and search_tabs(env):
        time.sleep(0.3)
    assert search_tabs(env) == []                                          # the LinkedIn set-up tab closed itself
    active = env.worker().evaluate("chrome.tabs.query({active: true}).then(t => t.map(x => x.url))")
    assert any(u.startswith(env.h.base + "/") and "/search/" not in u for u in active)   # dashboard in front



# ------------------------------------------- location set-up tab: automatic, banner, ambiguous
def _start_teach(env, place):
    env.dash.evaluate("([place]) => { window.__t = null; window.addEventListener('message', e => {"
                      " if (e.data && e.data.type === 'geo-learned') window.__t = e.data; });"
                      " window.postMessage({source:'hbf-dashboard', type:'teach', jobId:'t9', place}, '*'); }", [place])
    deadline = time.time() + 15
    while time.time() < deadline and not search_tabs(env):
        time.sleep(0.3)
    assert search_tabs(env), "set-up tab did not open"


def _in_teach_tab(env, js):
    """Run `js` (a function body) in the set-up tab's page and return its result."""
    return env.worker().evaluate("""async (code) => {
        const [t] = (await chrome.tabs.query({})).filter(x => x.url.includes('/search/results/people'));
        const [r] = await chrome.scripting.executeScript({ target: { tabId: t.id }, world: 'MAIN', func: (c) => (new Function(c))(), args: [code] });
        return r.result; }""", js)


def test_set_up_finds_the_city_id_on_the_page_by_itself(env):
    _start_teach(env, "Mumbai, Maharashtra, India")
    # LinkedIn's page data names the city with its location ID (as on a real results page)
    _in_teach_tab(env, "const c = document.createElement('code'); c.style.display = 'none';"
                       " c.textContent = JSON.stringify({ included: [{ entityUrn: 'urn:li:fs_geo:106164952',"
                       " text: 'Mumbai, Maharashtra, India' }, { entityUrn: 'urn:li:fs_geo:102713980', text: 'India' }] });"
                       " document.body.appendChild(c); return true;")
    env.dash.wait_for_function("window.__t !== null", timeout=20000)
    assert env.dash.evaluate("[window.__t.place, window.__t.id]") == ["Mumbai, Maharashtra, India", "106164952"]
    deadline = time.time() + 10
    while time.time() < deadline and search_tabs(env):
        time.sleep(0.3)
    assert search_tabs(env) == []                                          # closed by itself


def test_set_up_shows_a_banner_when_the_page_has_no_id_then_learns_the_pick(env):
    _start_teach(env, "Pune, Maharashtra, India")
    # this mock page has no Locations filter, so the automatic pick gives up and asks the person
    deadline = time.time() + 30
    text = ""
    while time.time() < deadline and 'Click "Locations"' not in (text or ""):
        text = _in_teach_tab(env, "const b = document.getElementById('hbf-setup-banner'); return b ? b.textContent : null;")
        time.sleep(0.5)
    assert 'Click "Locations"' in text and "Pune, Maharashtra, India" in text and "Show results" in text
    # the person picks Pune: the address gets its ID
    env.worker().evaluate("""async (id) => { const [t] = (await chrome.tabs.query({})).filter(x => x.url.includes('/search/results/people'));
        await chrome.tabs.update(t.id, { url: t.url + '&geoUrn=' + encodeURIComponent(JSON.stringify([id])) }); }""", "114806696")
    env.dash.wait_for_function("window.__t !== null", timeout=15000)
    assert env.dash.evaluate("window.__t.id") == "114806696"


def test_set_up_does_not_guess_when_the_page_is_ambiguous(env):
    _start_teach(env, "Mumbai, Maharashtra, India")
    _in_teach_tab(env, "const c = document.createElement('code'); c.style.display = 'none';"
                       " c.textContent = JSON.stringify([{ entityUrn: 'urn:li:geo:1111', text: 'Mumbai, Maharashtra, India' },"
                       " { entityUrn: 'urn:li:geo:2222', name: 'Mumbai, Maharashtra, India' }]);"
                       " document.body.appendChild(c); return true;")
    deadline = time.time() + 12
    banner = None
    while time.time() < deadline and not banner:
        banner = _in_teach_tab(env, "return !!document.getElementById('hbf-setup-banner');")
        time.sleep(0.5)
    assert banner                                                          # asks instead of guessing
    time.sleep(3)
    assert env.dash.evaluate("window.__t") is None
    assert env.worker().evaluate("chrome.storage.local.get('geoLearned').then(x => x.geoLearned || {})") == {}



# ===================== one Launch: automatic set-up of unknown places (tests a-i) =====================
OPTIONS = [["Navi Mumbai, Maharashtra, India", "999"], ["Mumbai, Maharashtra, India", "106164952"],
           ["Pune, Maharashtra, India", "114806696"], ["Hyderabad, Telangana, India", "105556991"]]
AH = "&activelyHiringForJobTitles=%5B%22-100%22%5D"


def _resolve(env, places, timeout=150):
    """Like the dashboard: ask the extension to set up `places`; return (answer, progress events)."""
    env.dash.evaluate("([places]) => { window.__res = null; window.__prog = [];"
                      " window.addEventListener('message', e => { const d = e.data; if (!d) return;"
                      "   if (d.type === 'resolve-result') window.__res = d;"
                      "   if (d.type === 'resolve-progress') window.__prog.push([d.place, d.status, Date.now()]); });"
                      " window.postMessage({source:'hbf-dashboard', type:'resolve', jobId:'r1', places}, '*'); }", [places])
    env.dash.wait_for_function("window.__res !== null", timeout=timeout * 1000)
    return env.dash.evaluate("window.__res"), env.dash.evaluate("window.__prog")


def _setup_loads(env):
    return [u for u in env.h.search_loads if "keywords=" not in u]         # the set-up tab's plain searches


def _geo_check(env, places):
    env.dash.evaluate("([places]) => { window.__chk = null; window.addEventListener('message', e => {"
                      " if (e.data && e.data.type === 'geo-check-result') window.__chk = e.data; });"
                      " window.postMessage({source:'hbf-dashboard', type:'geo-check', jobId:'c1', locations: places}, '*'); }", [places])
    env.dash.wait_for_function("window.__chk !== null", timeout=10000)
    return env.dash.evaluate("window.__chk.needPlaces")


def test_a_three_places_two_unknown_one_launch(env):
    env.h.filters = {"options": OPTIONS}
    places = ["Bengaluru, Karnataka, India", "Pune, Maharashtra, India", "Hyderabad, Telangana, India"]
    unknown = _geo_check(env, places)
    assert unknown == ["Pune, Maharashtra, India", "Hyderabad, Telangana, India"]
    res, prog = _resolve(env, unknown)
    assert res["ok"] is True
    assert [(d["place"], d["id"]) for d in res["done"]] == [("Pune, Maharashtra, India", "114806696"),
                                                             ("Hyderabad, Telangana, India", "105556991")]
    assert [p[:2] for p in prog] == [["Pune, Maharashtra, India", "setting-up"], ["Pune, Maharashtra, India", "ready"],
                                     ["Hyderabad, Telangana, India", "setting-up"], ["Hyderabad, Telangana, India", "ready"]]
    # one at a time, with the 4-8 s pause before the second place
    assert prog[2][2] - prog[1][2] >= 0 and prog[3][2] - prog[1][2] >= 4000
    assert len(_setup_loads(env)) == 2
    # then the people search: all 3 IDs and the Actively hiring parameter in one address
    job_id = new_job(env)
    env.launch_geo(job_id, places, extra=AH)
    url = env.search_tab_url()
    assert "geoUrn=%5B%22105214831%22%2C%22114806696%22%2C%22105556991%22%5D" in url and AH in url
    job = env.wait_job(job_id)
    assert job["status"] == "done" and job["total_read"] > 0 and env.h.ingested    # people read afterwards
    # (i) never touched: the People chip and the main search box
    assert not [t for t in env.h.touched if t.startswith(("people chip", "main search"))]


def test_b_short_name_takes_only_the_exact_city(env):
    env.h.filters = {"options": OPTIONS}                    # "Navi Mumbai" is listed first
    res, _ = _resolve(env, ["Mumbai"])
    assert res["ok"] is True and res["done"][0]["id"] == "106164952" and res["done"][0]["label"] == "Mumbai, Maharashtra, India"


def test_b_short_name_with_two_candidates_stops_and_lists_them(env):
    env.h.filters = {"options": [["Aurangabad, Maharashtra, India", "1"], ["Aurangabad, Bihar, India", "2"]]}
    res, prog = _resolve(env, ["Aurangabad"])
    assert res["ok"] is False and res["offered"] == ["Aurangabad, Maharashtra, India", "Aurangabad, Bihar, India"]
    assert "Type the full name exactly as shown" in res["reason"]
    assert not [t for t in env.h.touched if t.startswith("option") or t == "show"]          # clicked nothing
    assert prog[-1][1] == "failed"


def test_c_place_already_ticked_aborts(env):
    env.h.filters = {"options": OPTIONS, "prechecked": "Delhi, India"}
    res, _ = _resolve(env, ["Pune, Maharashtra, India"])
    assert res["ok"] is False and "already has a place selected (Delhi, India)" in res["reason"]
    assert not [t for t in env.h.touched if t.startswith("option") or t == "show"]


def test_c_wrong_box_ticked_aborts(env):
    env.h.filters = {"options": OPTIONS, "tickWrong": "Navi Mumbai, Maharashtra, India"}
    res, _ = _resolve(env, ["Mumbai, Maharashtra, India"])
    assert res["ok"] is False and "instead of" in res["reason"]
    assert "show" not in env.h.touched                                                  # Show results not clicked


def test_d_set_up_tab_never_stacks_places(env):
    env.h.filters = {"options": OPTIONS}
    res, _ = _resolve(env, ["Pune, Maharashtra, India", "Mumbai, Maharashtra, India"])
    assert res["ok"] is True
    loads = _setup_loads(env)
    assert len(loads) == 2 and all("geoUrn" not in u for u in loads)                    # back to the plain search each time


def test_e_cached_places_cost_no_linkedin_visit(env):
    env.h.filters = {"options": OPTIONS}
    _resolve(env, ["Pune, Maharashtra, India"])
    before = len(env.h.search_loads)
    assert _geo_check(env, ["Pune, Maharashtra, India", "Bengaluru"]) == []            # nothing to set up next time
    time.sleep(1)
    assert len(env.h.search_loads) == before


def test_f_dropped_actively_hiring_is_applied_through_the_chip(env):
    env.h.filters = {"options": OPTIONS, "ahUi": "ok"}
    job_id = new_job(env)
    env.launch(job_id, "&dropah=1" + AH)
    job = env.wait_job(job_id)
    assert job["status"] == "done"
    assert '"Actively hiring" filter applied.' in env.h.heartbeats
    assert env.h.notes == [] and not [b for b in env.h.heartbeats if "ignored the" in b]


def test_f_dropped_actively_hiring_and_ui_fails_continues_with_warning(env):
    env.h.filters = {"options": OPTIONS, "ahUi": "fail"}
    job_id = new_job(env)
    env.launch(job_id, "&dropah=1" + AH)
    job = env.wait_job(job_id)
    assert job["status"] == "done" and job["total_read"] > 0                            # never blocked by the filter
    assert any("ignored the \"Actively hiring\" filter" in b for b in env.h.heartbeats)
    assert env.h.notes == [{"job_id": job_id, "note": "filter not applied"}]


def test_g_limit_page_during_set_up_stops_and_keeps_the_tab(env):
    env.h.filters = {"options": OPTIONS, "block": True}
    res, prog = _resolve(env, ["Pune, Maharashtra, India", "Mumbai, Maharashtra, India"])
    assert res["ok"] is False and res["blocked"] is True and "unusual activity" in res["reason"], res
    assert [p[0] for p in prog if p[1] == "setting-up"] == ["Pune, Maharashtra, India"]   # stopped everything
    time.sleep(1)
    assert search_tabs(env), "the LinkedIn tab should be kept open"


def test_h_eleven_places_refused(env):
    res, _ = _resolve(env, [f"City{i}" for i in range(11)], timeout=20)
    assert res["ok"] is False and "At most 10 places" in res["error"]
    assert _setup_loads(env) == []


def test_suggestions_that_ignore_a_typed_full_name_still_resolve(env):
    env.h.filters = {"options": OPTIONS, "noCommas": True}
    res, _ = _resolve(env, ["Mumbai, Maharashtra, India"])
    assert res["ok"] is True and res["done"][0]["id"] == "106164952", res


def test_suggestions_that_need_a_focus_event_still_resolve(env):
    env.h.filters = {"options": OPTIONS, "needFocus": True}
    res, _ = _resolve(env, ["Mumbai, Maharashtra, India"])
    assert res["ok"] is True and res["done"][0]["id"] == "106164952", res


def test_suggestions_only_in_a_visible_tab_retry_in_front(env):
    env.h.filters = {"options": OPTIONS, "needVisible": True}
    res, _ = _resolve(env, ["Mumbai, Maharashtra, India"], timeout=120)
    assert res["ok"] is True and res["done"][0]["id"] == "106164952", res


def test_no_suggestions_reports_what_it_saw_and_is_not_reloaded(env):
    env.h.filters = {"options": []}
    res, _ = _resolve(env, ["Mumbai, Maharashtra, India"], timeout=170)
    assert res["ok"] is False and 'box="Mumbai"' in res["reason"] and "options anywhere=0" in res["reason"] and "box on screen=true" in res["reason"], res
    assert len(_setup_loads(env)) == 1                  # a page that is working is never reloaded


@pytest.mark.parametrize("trap", ["decoyBox", "replaceBox", "closeOnce", "trustedOnly"])
@pytest.mark.parametrize("place,opts", [
    ("Mumbai, Maharashtra, India", OPTIONS),
    ("Pune, Maharashtra, India", [["Pune, Maharashtra, India", "114806696"], ["Pune District, India", "1"]]),
])
def test_any_city_survives_how_the_page_treats_the_box(env, trap, place, opts):
    env.h.filters = {"options": opts, trap: True}
    res, _ = _resolve(env, [place], timeout=120)
    want = next(i for t, i in opts if t == place)
    assert res["ok"] is True and res["done"][0]["id"] == want, res


def _geo_hit(text, gid):
    return {"text": {"text": text}, "targetUrn": f"urn:li:fsd_geo:{gid}"}


def _session(env):
    env.ctx.add_cookies([{"name": "JSESSIONID", "value": "ajax:123", "url": env.h.base}])


def test_table_city_needs_no_set_up_but_another_country_does(env):
    assert _geo_check(env, ["Kolkata, West Bengal, India", "Kolkata", "Kolkata, Bangladesh"]) == ["Kolkata, Bangladesh"]


def test_lookup_gives_the_exact_city_without_touching_the_filter(env):
    _session(env)
    env.h.filters = {"options": OPTIONS}
    env.h.geo_api = {"status": 200, "body": {"elements": [
        _geo_hit("Mumbai District, India", "1"), _geo_hit("Mumbai, Maharashtra, India", "106164952")]}}
    res, _ = _resolve(env, ["Mumbai, Maharashtra, India"])
    assert res["ok"] is True and res["done"][0]["id"] == "106164952", res
    assert env.h.geo_calls[0]["csrf"] == "ajax:123" and len(env.h.geo_calls) == 1
    assert not [x for x in env.h.touched if "option" in x or x == "show"]


def test_lookup_without_an_exact_name_falls_back_to_the_filter(env):
    _session(env)
    env.h.filters = {"options": OPTIONS}
    env.h.geo_api = {"status": 200, "body": {"elements": [_geo_hit("Mumbai District, India", "1")]}}
    res, _ = _resolve(env, ["Mumbai, Maharashtra, India"])
    assert res["ok"] is True and res["done"][0]["id"] == "106164952", res


def test_lookup_refused_stops_at_once(env):
    _session(env)
    env.h.filters = {"options": OPTIONS}
    env.h.geo_api = {"status": 429, "body": {}}
    res, _ = _resolve(env, ["Mumbai, Maharashtra, India"])
    assert res["ok"] is False and res["blocked"] is True, res
    assert not [x for x in env.h.touched if "option" in x or x == "show"]


def test_suggestions_in_a_differently_built_list_are_still_found(env):
    env.h.filters = {"options": OPTIONS, "looseList": True}
    res, _ = _resolve(env, ["Mumbai, Maharashtra, India"])
    assert res["ok"] is True and res["done"][0]["id"] == "106164952", res


def test_lookup_tries_the_second_address_after_a_404(env):
    _session(env)
    env.h.filters = {"options": OPTIONS}
    env.h.geo_api_v2 = {"status": 200, "body": {"elements": [_geo_hit("Mumbai, Maharashtra, India", "106164952")]}}
    res, _ = _resolve(env, ["Mumbai, Maharashtra, India"])
    assert res["ok"] is True and res["done"][0]["id"] == "106164952", res
    assert len(env.h.geo_calls) == 2
    assert not [x for x in env.h.touched if "option" in x or x == "show"]


def test_no_linkedin_page_is_loaded_after_the_people_search(env):
    """With no lead-details task from the server, the tab closes right after the search."""
    env.h.details_on = True
    job_id = new_job(env, target=3)
    env.launch(job_id)
    env.wait_job(job_id)
    end = time.time() + 15
    while time.time() < end and any("/search/results/" in u for u in env.worker().evaluate("chrome.tabs.query({}).then(t => t.map(x => x.url))")):
        time.sleep(0.5)
    assert env.h.company_loads == []
    assert not any(b.startswith("Company details") for b in env.h.heartbeats)
    assert not any("/search/results/" in u for u in env.worker().evaluate("chrome.tabs.query({}).then(t => t.map(x => x.url))"))
