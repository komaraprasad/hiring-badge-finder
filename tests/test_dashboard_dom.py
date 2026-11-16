"""Dashboard page checks in a real browser.

The server is replaced by stub answers shaped like app/main.py's responses, so
this tests the page's own logic (banners, chips, launch message, live updates),
not the server. Needs Playwright and a Chrome/Chromium.
"""
import json
import os
import shutil
from pathlib import Path

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright  # noqa: E402

INDEX = (Path(__file__).resolve().parent.parent / "app" / "static" / "index.html").read_text(encoding="utf-8")
CHROME = next((c for c in [os.environ.get("CHROME_PATH", ""), "/usr/bin/google-chrome",
                           shutil.which("chromium") or ""] if c and os.path.exists(c)), None)
pytestmark = pytest.mark.skipif(CHROME is None, reason="no Chrome/Chromium found")

JOB = {"job_id": "job1", "roles": ["Engineering Manager"], "locations": ["Bengaluru"], "keywords": [],
       "target": 25, "status": "running", "saved": 0, "total_read": 0, "pages": 0, "not_hiring": 0,
       "not_confirmed": 0, "other_locations": 0, "already_collected": 0, "duplicates": 0, "message": "",
       "error": "", "end_reason": "", "summary": "0 of 25 saved · read 0 people on 0 pages · filtered out: "
       "0 not hiring, 0 other locations, 0 already collected, 0 duplicates", "short_reason": ""}
PERSON = {"id": 1, "profile_url": "https://www.linkedin.com/in/ann", "name": "Ann <b>Lee</b>",
          "headline": "EM at Acme", "company": "Acme", "location": "Bengaluru", "status": "Badge confirmed",
          "evidence": "frame", "first_found": "2026-11-03 21:00:00", "job_id": "job1"}


class Page:
    def __init__(self, sse_body="", ext_version=None, latest="1.0.0"):
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox", "--headless=new"])
        self.ctx = self.browser.new_context(viewport={"width": 390, "height": 800})  # phone width
        self.page = self.ctx.new_page()
        self.page.set_default_timeout(8000)
        self.posts = []
        self.job = dict(JOB)          # what /api/status returns; tests may change it
        self.geo, self.geo_posts = {}, []   # /api/geo: places set up so far, and what was saved
        self.errors = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        sse = sse_body

        def handle(route):
            req = route.request
            path = req.url.split("//", 1)[1].split("/", 1)[1].split("?")[0]
            j = lambda obj: route.fulfill(status=200, content_type="application/json", body=json.dumps(obj))
            if path == "":
                return route.fulfill(status=200, content_type="text/html", body=INDEX)
            if path == "api/titles":
                return j(["Engineering Manager", "QA Manager", "Product Manager"])
            if path == "api/title-presets":
                return j({"Recruiters": ["Technical Recruiter", "Recruiter"],
                          "Founders & CTOs": ["Founder", "CTO"]})
            if path == "api/locations":
                return j(["Bengaluru, Karnataka, India", "Mumbai, Maharashtra, India"])
            if path == "api/extension/info":
                return j({"version": latest})
            if path == "api/settings":
                return j({"save_only_confirmed": True, "daily_cap": 300})
            if path == "api/leads":
                return j({"total": 0, "page": 1, "page_size": 50, "items": []})
            if path == "api/events":
                return route.fulfill(status=200, content_type="text/event-stream", body=sse)
            if path == "api/search/start":
                self.posts.append(json.loads(req.post_data))
                return j({"job_id": "job1", "url": "https://www.linkedin.com/search/results/people/?keywords=x",
                          "geo_url_base": "", "locations": ["Bengaluru"], "target": 25})
            if path == "api/status":
                return j(self.job)
            if path == "api/geo":
                if req.method == "POST":
                    self.geo_posts.append(json.loads(req.post_data))
                return j({k.lower(): v for k, v in self.geo.items()})
            if path == "api/settings" or path == "api/stop":
                return j({"ok": True})
            return route.fulfill(status=404, body="{}")

        self.page.route("http://localhost/**", handle)
        if ext_version is not None:
            # the real extension sets these at document start; <html> may not exist yet, so wait for it
            self.page.add_init_script(
                "(() => { const set = () => { const r = document.documentElement; if (!r) return false;"
                f" r.dataset.hbfExt = '1'; r.dataset.hbfExtVersion = '{ext_version}'; return true; }};"
                " if (!set()) new MutationObserver((_, o) => { if (set()) o.disconnect(); }).observe(document, { childList: true }); })();")
        self.page.add_init_script(
            "window.__posted=[];window.addEventListener('message',e=>{const d=e.data;if(!d||d.source!=='hbf-dashboard')return;"
            "if(d.type==='open'||d.type==='stop'){window.__posted.push(d);return;}"
            "if(window.HBF_FAKE_EXT!==false&&document.documentElement.dataset.hbfExt==='1'){"
            "if(d.type==='resolve'){window.__resolved=(window.__resolved||[]).concat([d.places]);"
            "const r=window.HBF_RESOLVE||{ok:true,done:d.places.map(p=>({place:p,id:'1',label:p}))};"
            "if(r.ok)window.HBF_NEED_PLACES=[];"
            "setTimeout(()=>window.postMessage(Object.assign({source:'hbf-extension',type:'resolve-result',jobId:d.jobId},r),'*'),300);return;}"
            "window.postMessage({source:'hbf-extension',type:d.type+'-result',jobId:d.jobId,ok:true,"
            "needPlaces:(window.HBF_NEED_PLACES||[]),places:{}},'*')}})")
        self.page.goto("http://localhost/#search")          # the New Search view (the form)

    def close(self):
        self.browser.close()
        self.pw.stop()


def test_install_banner_shows_when_no_extension_and_can_be_hidden():
    p = Page()
    try:
        pg = p.page
        pg.wait_for_selector("#installBanner:not(.hidden)")
        assert "Add the extension to your browser" in pg.inner_text("#installBanner")
        assert pg.locator("#installBanner ol li").count() == 3   # load the project's extension folder; no zip step
        assert pg.get_attribute("#installBanner a.primary", "href") == "/api/extension.zip"
        pg.click("#hideInstall")
        pg.wait_for_selector("#installBanner.hidden", state="attached")
        assert p.errors == []
    finally:
        p.close()


def test_no_banner_when_extension_is_current():
    p = Page(ext_version="1.0.0")
    try:
        p.page.wait_for_timeout(500)
        assert "hidden" in p.page.get_attribute("#installBanner", "class")
        assert "hidden" in p.page.get_attribute("#updateBanner", "class")
    finally:
        p.close()


def test_update_banner_when_extension_is_older():
    p = Page(ext_version="0.9.0")
    try:
        p.page.wait_for_selector("#updateBanner:not(.hidden)")
        assert "0.9.0" in p.page.inner_text("#extHave") and "1.0.0" in p.page.inner_text("#extNeed")
        assert "hidden" in p.page.get_attribute("#installBanner", "class")
    finally:
        p.close()


def test_chips_limits_and_suggestions():
    p = Page(ext_version="1.0.0")
    try:
        pg = p.page
        pg.fill("#roleInput", "eng")
        pg.wait_for_selector("#roleBox li")
        assert pg.inner_text("#roleBox li") == "Engineering Manager"
        pg.press("#roleInput", "ArrowDown")
        pg.press("#roleInput", "Enter")
        for t in ["A", "B", "C", "D", "E", "F"]:
            pg.fill("#roleInput", t)
            pg.press("#roleInput", "Enter")
        assert pg.locator("#roleBox .chip").count() == 5          # limit of 5 titles
        pg.click("#roleBox .chip button")
        assert pg.locator("#roleBox .chip").count() == 4
        pg.fill("#roleInput", "a")                                  # duplicates (any case) are ignored
        pg.press("#roleInput", "Enter")
        assert pg.locator("#roleBox .chip").count() == 4
        assert p.errors == []
    finally:
        p.close()


def test_launch_needs_extension_title_and_location():
    p = Page()                                    # no extension
    try:
        pg = p.page
        pg.click("#launchBtn")
        assert "extension is not installed" in pg.inner_text("#errorBox")
    finally:
        p.close()
    p = Page(ext_version="1.0.0")
    try:
        pg = p.page
        pg.click("#launchBtn")
        assert "at least one job title" not in pg.inner_text("#errorBox")   # titles are optional
        assert "at least one location" in pg.inner_text("#errorBox")
        assert p.posts == []
    finally:
        p.close()


def test_launch_sends_search_and_posts_open_message():
    p = Page(ext_version="1.0.0")
    try:
        pg = p.page
        pg.fill("#roleInput", "Engineering Manager"); pg.press("#roleInput", "Enter")
        pg.fill("#locInput", "Bengaluru"); pg.press("#locInput", "Enter")
        pg.click("#targets button[data-n='50']")
        pg.click("#launchBtn")
        pg.wait_for_function("window.__posted.length > 0")
        msg = pg.evaluate("window.__posted[0]")
        assert msg["type"] == "open" and msg["jobId"] == "job1"
        assert msg["url"].startswith("https://www.linkedin.com/search/results/people/")
        assert p.posts == [{"roles": ["Engineering Manager"], "locations": ["Bengaluru"], "keywords": [], "target": 50, "resume": True}]
        assert pg.inner_text("#summary").startswith("0 of 25 saved")
        assert not pg.is_disabled("#stopBtn")
    finally:
        p.close()


def test_live_events_update_heartbeat_error_and_table_without_html_injection():
    sse = ("event: heartbeat\ndata: " + json.dumps({"job_id": "job1", "message": "page 1: 10 cards, 3 badges"}) + "\n\n"
           "event: person\ndata: " + json.dumps(PERSON) + "\n\n"
           "event: error\ndata: " + json.dumps({"job_id": "job1", "message": "LinkedIn is asking for a security check."}) + "\n\n")
    p = Page(ext_version="1.0.0", sse_body=sse)
    try:
        pg = p.page
        pg.fill("#roleInput", "EM"); pg.press("#roleInput", "Enter")
        pg.fill("#locInput", "Pune"); pg.press("#locInput", "Enter")
        pg.click("#launchBtn")
        pg.wait_for_function("document.getElementById('beat').textContent.includes('3 badges')")
        pg.wait_for_selector("#resTable .lead")
        row = pg.inner_text("#resTable .lead")
        assert "Ann <b>Lee</b>" in row and "Badge confirmed" in row and "frame" in row
        assert pg.locator("#resTable b").count() == 0                      # name was escaped, not rendered as HTML
        pg.wait_for_function("document.getElementById('errorBox').textContent.includes('security check')")
        assert "hidden" not in pg.get_attribute("#errorBox", "class")
    finally:
        p.close()


def test_theme_toggle_and_phone_width_has_no_page_scroll():
    p = Page(ext_version="1.0.0")
    try:
        pg = p.page
        first = pg.evaluate("document.documentElement.dataset.theme")
        pg.click("#themeBtn")
        assert pg.evaluate("document.documentElement.dataset.theme") != first
        assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        p.close()


def test_presets_fill_titles_and_skill_goes_to_keywords():
    p = Page(ext_version="1.0.0")
    try:
        pg = p.page
        pg.wait_for_selector("#presets button")
        pg.fill("#roleInput", "Old title"); pg.press("#roleInput", "Enter")
        pg.click("#presets button:has-text('Recruiters')")             # replaces what was there
        chips = [t.replace("×", "").strip() for t in pg.locator("#roleBox .chip").all_inner_texts()]
        assert chips == ["Technical Recruiter", "Recruiter"]
        pg.fill("#locInput", "Bengaluru"); pg.press("#locInput", "Enter")
        pg.fill("#kwInput", "Java"); pg.press("#kwInput", "Enter")
        pg.click("#launchBtn")
        pg.wait_for_function("window.__posted.length > 0")
        assert p.posts == [{"roles": ["Technical Recruiter", "Recruiter"], "locations": ["Bengaluru"],
                            "keywords": ["Java"], "target": 25, "resume": True}]
    finally:
        p.close()


def test_empty_title_box_shows_suggestions_on_focus():
    p = Page(ext_version="1.0.0")
    try:
        pg = p.page
        pg.wait_for_timeout(300)                                       # suggestion list loaded
        pg.click("#roleInput")
        assert pg.locator("#roleBox ul li").count() == 3               # all mock titles, nothing typed
        pg.click("#locInput")
        assert pg.locator("#locBox ul li").count() == 2                # locations too, like LinkedIn's box
    finally:
        p.close()


@pytest.mark.parametrize("typed,first", [("bangalore", "Bengaluru, Karnataka, India"),
                                         ("bombay", "Mumbai, Maharashtra, India"),
                                         ("mum", "Mumbai, Maharashtra, India"),
                                         ("karnataka", "Bengaluru, Karnataka, India")])
def test_location_suggestions_understand_other_spellings(typed, first):
    p = Page(ext_version="1.0.0")
    try:
        pg = p.page
        pg.wait_for_timeout(300)
        pg.fill("#locInput", typed)
        assert pg.locator("#locBox ul li").first.inner_text() == first
        pg.press("#locInput", "ArrowDown"); pg.press("#locInput", "Enter")
        chips = [t.replace("×", "").strip() for t in pg.locator("#locBox .chip").all_inner_texts()]
        assert chips == [first]                                         # the real place name is used
    finally:
        p.close()



def _fill(pg):
    pg.fill("#roleInput", "Engineering Manager"); pg.press("#roleInput", "Enter")
    pg.fill("#locInput", "Bengaluru"); pg.press("#locInput", "Enter")


def test_older_extension_cannot_launch_old_code():
    p = Page(ext_version="1.0.0", latest="1.9.0")
    try:
        pg = p.page
        _fill(pg)
        pg.click("#launchBtn")
        pg.wait_for_selector("#errorBox:not(.hidden)")
        assert "version 1.0.0 but this dashboard needs 1.9.0" in pg.inner_text("#errorBox")
        assert "Reload" in pg.inner_text("#errorBox")
        assert p.posts == [] and pg.evaluate("window.__posted.length") == 0      # no search started
    finally:
        p.close()


def test_no_answer_from_extension_shows_error_instead_of_hanging():
    p = Page(ext_version="1.0.0")              # marker present, but nothing answers the open message
    try:
        pg = p.page
        pg.evaluate("window.HBF_OPEN_TIMEOUT_MS = 800")
        _fill(pg)
        pg.click("#launchBtn")
        pg.wait_for_selector("#errorBox:not(.hidden)", timeout=6000)
        assert "The extension did not answer" in pg.inner_text("#errorBox")
        assert not pg.is_disabled("#launchBtn")
    finally:
        p.close()



def test_progress_updates_even_when_the_live_stream_is_silent():
    """Some networks hold back the live stream: the dashboard also polls the status while a search runs."""
    p = Page(ext_version="1.0.0")                       # the mocked live stream sends nothing
    try:
        pg = p.page
        pg.evaluate("window.HBF_POLL_MS = 300")         # (read once at load; the default 3 s is used below)
        _fill(pg)
        pg.click("#launchBtn")
        pg.wait_for_function("document.getElementById('summary').textContent.startsWith('0 of 25')")
        p.job.update(saved=18, pages=2, message="page 2: waiting 4s before the next page",
                     summary="18 of 25 saved · read 20 people on 2 pages · filtered out: 2 not hiring")
        pg.wait_for_function("document.getElementById('summary').textContent.startsWith('18 of 25')", timeout=8000)
        assert pg.inner_text("#beat") == "page 2: waiting 4s before the next page"
        p.job.update(status="done", saved=25, message="", summary="25 of 25 saved · read 30 people on 3 pages")
        pg.wait_for_function("document.getElementById('beat').textContent === 'Finished.'", timeout=8000)
    finally:
        p.close()



def _fill_places(pg, places):
    pg.fill("#roleInput", "EM"); pg.press("#roleInput", "Enter")
    for pl in places:
        pg.fill("#locInput", pl); pg.press("#locInput", "Enter")


def test_one_launch_sets_up_unknown_places_then_searches():
    p = Page(ext_version="1.0.0")
    try:
        pg = p.page
        pg.evaluate("window.HBF_NEED_PLACES = ['Pune']")
        _fill_places(pg, ["Pune"])
        pg.click("#launchBtn")                                             # the only click
        pg.wait_for_function("window.__posted.length > 0", timeout=10000)
        assert pg.evaluate("window.__resolved") == [["Pune"]]             # set up automatically first
        assert pg.evaluate("window.__posted[0].type") == "open" and len(p.posts) == 1
        assert p.geo_posts == [{"place": "Pune", "geo_id": "1"}]           # saved for everyone
        log = pg.inner_text("#timeline")
        assert "Setting up 1 new place on LinkedIn" in log and "Applying filters" in log
        assert p.errors == []
    finally:
        p.close()


def test_more_than_three_unknown_places_warn_first():
    p = Page(ext_version="1.0.0")
    try:
        pg = p.page
        four = ["Pune", "Delhi", "Noida", "Kolkata"]
        pg.evaluate(f"window.HBF_NEED_PLACES = {four!r}")
        _fill_places(pg, four)
        pg.click("#launchBtn")
        pg.wait_for_selector("#warnBox:not(.hidden)")
        assert pg.inner_text("#warnBox") == "Warning: 4 new places: each new place costs one extra LinkedIn page load."
        pg.wait_for_function("window.__posted.length > 0", timeout=10000)
    finally:
        p.close()


def test_failed_place_shows_why_offers_manual_and_the_run_continues():
    p = Page(ext_version="1.0.0")
    try:
        pg = p.page
        pg.evaluate("window.HBF_NEED_PLACES = ['Aurangabad']")
        pg.evaluate("window.HBF_RESOLVE = {ok:false, failedPlace:'Aurangabad', reason:'LinkedIn offered: Aurangabad, Maharashtra, India /"
                    " Aurangabad, Bihar, India. Type the full name exactly as shown.', offered:[]}")
        pg.evaluate("window.__teach = []; window.addEventListener('message', e => {"
                    " if (e.data && e.data.source === 'hbf-dashboard' && e.data.type === 'teach') window.__teach.push(e.data.place); })")
        _fill_places(pg, ["Aurangabad"])
        pg.click("#launchBtn")
        pg.wait_for_function("document.getElementById('setupRows').textContent.includes('✗ LinkedIn offered')")
        pg.wait_for_timeout(800)
        assert p.posts == [] and pg.evaluate("window.__posted.length") == 0          # people search not started
        pg.click("#setupRows button:has-text('Do it myself on LinkedIn')")
        pg.wait_for_function("window.__teach.length === 1")
        # the person picks it by hand; the same run continues by itself
        pg.evaluate("window.HBF_NEED_PLACES = []; window.HBF_RESOLVE = null")
        pg.evaluate("window.postMessage({source:'hbf-extension', type:'geo-learned', place:'Aurangabad', id:'7'}, '*')")
        pg.wait_for_function("window.__posted.length > 0", timeout=10000)
        assert pg.evaluate("window.__posted[0].type") == "open"
        assert {"place": "Aurangabad", "geo_id": "7"} in p.geo_posts
        assert p.errors == []
    finally:
        p.close()


WRONG_CITY = ("Warning: Most results are not in Mumbai. The saved location may be wrong. "
              "Check Settings -> Locations set up on LinkedIn.")


def _launch_and_poll(p):
    pg = p.page
    pg.evaluate("window.HBF_POLL_MS = 300")
    pg.fill("#roleInput", "EM"); pg.press("#roleInput", "Enter")
    pg.fill("#locInput", "Mumbai"); pg.press("#locInput", "Enter")
    pg.click("#launchBtn")
    pg.wait_for_function("document.getElementById('summary').textContent.startsWith('0 of 25')")


def test_matching_city_shows_no_wrong_city_warning():
    p = Page(ext_version="1.0.0")
    try:
        _launch_and_poll(p)
        p.job = dict(p.job, saved=3, message="page 1: saved 3/25", warning="")
        p.page.wait_for_function("document.getElementById('summary').textContent.length > 0")
        p.page.wait_for_timeout(1500)
        assert "hidden" in p.page.get_attribute("#warnBox", "class")
        assert p.errors == []
    finally:
        p.close()


def test_mismatching_city_shows_the_yellow_warning_and_run_continues():
    sse = ("event: heartbeat\ndata: " + json.dumps({"job_id": "job1", "message": WRONG_CITY}) + "\n\n"
           "event: heartbeat\ndata: " + json.dumps({"job_id": "job1", "message": "page 1: saved 1/25"}) + "\n\n")
    p = Page(ext_version="1.0.0", sse_body=sse)
    try:
        _launch_and_poll(p)
        pg = p.page
        pg.wait_for_selector("#warnBox:not(.hidden)")
        assert pg.inner_text("#warnBox") == WRONG_CITY
        pg.wait_for_timeout(800)                                       # a later heartbeat does not hide it
        assert "hidden" not in pg.get_attribute("#warnBox", "class")
        assert not pg.is_disabled("#stopBtn")                          # still running
        assert p.errors == []
    finally:
        p.close()


def test_wrong_city_warning_also_shown_when_only_polling():
    p = Page(ext_version="1.0.0")              # no live stream: the status poll carries the warning
    try:
        _launch_and_poll(p)
        p.job = dict(p.job, message="page 1: saved 1/25", warning=WRONG_CITY)
        p.page.wait_for_selector("#warnBox:not(.hidden)", timeout=8000)
        assert p.page.inner_text("#warnBox") == WRONG_CITY
    finally:
        p.close()



def test_city_set_up_on_the_server_is_used_without_asking():
    p = Page(ext_version="1.0.0")
    p.geo = {"pune, maharashtra, india": "114806696"}
    try:
        pg = p.page
        pg.evaluate("window.__checks = []; window.addEventListener('message', e => {"
                    " if (e.data && e.data.source === 'hbf-dashboard' && e.data.type === 'geo-check') window.__checks.push(e.data.known); })")
        pg.fill("#roleInput", "EM"); pg.press("#roleInput", "Enter")
        pg.fill("#locInput", "Pune, Maharashtra, India"); pg.press("#locInput", "Enter")
        pg.click("#launchBtn")
        pg.wait_for_function("window.__posted.length > 0")
        assert pg.evaluate("window.__checks[0]") == {"pune, maharashtra, india": "114806696"}   # handed to the extension
        assert pg.evaluate("window.__posted[0].known") == {"pune, maharashtra, india": "114806696"}
        assert "hidden" in pg.get_attribute("#placeSetup", "class")
    finally:
        p.close()


def test_overall_bar_reaches_100_only_when_company_details_are_done():
    """Half for finding leads, half for company details; 100% only after "Lead details: done"."""
    p = Page(ext_version="1.0.0")
    try:
        pg = p.page
        pg.add_init_script("window.HBF_POLL_MS = 150;")
        pg.reload()
        p.job = dict(JOB, target=50, saved=25, details_done=0, details_total=25, details_on=True)
        pg.fill("#roleInput", "EM"); pg.press("#roleInput", "Enter")
        pg.fill("#locInput", "Pune"); pg.press("#locInput", "Enter")
        pg.click("#launchBtn")
        pct = lambda: pg.inner_text("#overallPct")
        pg.wait_for_function("document.getElementById('overallPct').textContent === '25%'")
        assert "Finding leads 25 / 50" in pg.inner_text("#overallText")
        # search finished, details still being read: not 100% even though the dial says 50/50
        p.job = dict(p.job, status="done", end_reason="target reached", saved=50, details_done=20,
                     details_total=50, message="Lead details: Acme: Software Development")
        pg.wait_for_function("document.getElementById('overallPct').textContent === '70%'")
        assert "Company details 20 / 50" in pg.inner_text("#overallText")
        assert pg.inner_text("#dialNum") == "50 / 50"
        p.job = dict(p.job, details_done=50, message="Lead details: done")
        pg.wait_for_function("document.getElementById('overallPct').textContent === '100%'")
        assert "Complete" in pg.inner_text("#overallText")
        assert pg.get_attribute("#bar", "aria-valuenow") == "100"
        assert pct() == "100%" and not p.errors
    finally:
        p.close()


def test_overall_bar_never_shows_100_when_details_stop_early():
    p = Page(ext_version="1.0.0")
    try:
        pg = p.page
        pg.add_init_script("window.HBF_POLL_MS = 150;")
        pg.reload()
        p.job = dict(JOB, target=25, saved=25, status="done", end_reason="target reached", details_done=10,
                     details_total=25, details_on=True, message="Lead details: done (limit of 60 pages per search reached)")
        pg.fill("#roleInput", "EM"); pg.press("#roleInput", "Enter")
        pg.fill("#locInput", "Pune"); pg.press("#locInput", "Enter")
        pg.click("#launchBtn")
        pg.wait_for_function("document.getElementById('overallPct').textContent === '70%'")
        assert "ended" in pg.get_attribute("#overall", "class")
        assert "company details 10 / 25" in pg.inner_text("#overallText")
    finally:
        p.close()
