"""Company details (industry, size): the REAL extension in Chrome, against mock LinkedIn pages.

The mock company-search, company About and profile markup (tests/harness.py) is an ASSUMPTION:
none of it was captured from a real LinkedIn page. These tests prove the flow, the pacing, the
exact-company rules, the limits and the stop rules, with the real Storage class behind the server.

The mock search saves Person 1-3 (target 3). Their companies from the card: Company1 (from the
"Current:" line; the headline has none), Company2 and Company3 (from "Software Engineer at ...").
"""
import time

import pytest

from tests.test_extension_browser import CHROME, Env, new_job

# Company details are ON again (1.39.0+): a second hidden tab reads them while the search runs.
pytestmark = pytest.mark.skipif(not CHROME, reason="no Chrome/Chromium that can load extensions")


@pytest.fixture()
def env(tmp_path):
    e = Env(tmp_path)
    e.h.details_on = True
    e.h.companies_on = True
    yield e
    e.close()


def _tabs(env):
    return env.worker().evaluate("chrome.tabs.query({}).then(t => t.map(x => x.url))")


def _wait(env, cond, timeout=150):
    end = time.time() + timeout
    while time.time() < end and not cond():
        time.sleep(0.5)


def _done(env):
    return any(b.startswith("Company details: done") or b.startswith("Company details stopped") for b in env.h.heartbeats)


def _run(env):
    job_id = new_job(env, target=3)
    env.launch(job_id)
    env.wait_job(job_id)
    _wait(env, lambda: _done(env))
    return job_id


def _leads(env, job_id):
    return {p["name"]: p for p in env.storage.all_leads({"job_id": job_id})}


def test_company_from_the_card_then_from_the_profile_link(env):
    env.h.company_results = {"Company2": [("company-2-x", "Company2 Labs"), ("company-2", "Company2")]}
    env.h.company_about = {"company-2": ("Banking", "51-200 employees", "dl"),
                           "globex": ("IT Services and IT Consulting", "1,001-5,000 employees", "lines")}
    env.h.profiles = {
        # Company1 matched no company page: the profile's current job links to the exact company
        "person-1": {"entries": [{"title": "Talent Partner", "company": "Globex Corp", "slug": "globex",
                                  "dates": "Jan 2023 - Present · 2 yrs 3 mos"},
                                 {"title": "Recruiter", "company": "Old Co", "slug": "old-co",
                                  "dates": "Jan 2019 - Dec 2022 · 4 yrs"}]},
        # only past jobs shown: nothing current, so nothing is guessed
        "person-3": {"entries": [{"title": "Recruiter", "company": "Past Co", "slug": "past-co",
                                  "dates": "Jan 2019 - Dec 2022 · 4 yrs"}]},
    }
    t0 = time.time()
    job_id = _run(env)
    elapsed = time.time() - t0
    people = _leads(env, job_id)
    assert (people["Person 2"]["company"], people["Person 2"]["company_industry"], people["Person 2"]["company_size"]) == (
        "Company2", "Banking", "51-200 employees")
    p1 = people["Person 1"]
    assert (p1["company"], p1["company_url"]) == ("Globex Corp", "https://www.linkedin.com/company/globex/")
    assert (p1["company_industry"], p1["company_size"]) == ("IT Services and IT Consulting", "1,001-5,000 employees")
    assert p1["title"] == "Team lead"                       # the card's title is kept
    p3 = people["Person 3"]
    assert (p3["company"], p3["company_industry"], p3["company_size"]) == ("Company3", "", "")
    assert p3["profile_checked"] and not p3["company_url"]
    # exactly these pages, in this order: 3 name searches, 1 About, 2 profiles, 1 About by link
    assert env.h.company_loads == [
        "/search/results/companies/?keywords=Company1&origin=GLOBAL_SEARCH_HEADER",
        "/search/results/companies/?keywords=Company2&origin=GLOBAL_SEARCH_HEADER",
        "/company/company-2/about/",
        "/search/results/companies/?keywords=Company3&origin=GLOBAL_SEARCH_HEADER",
        "/in/person-1/details/experience/", "/company/globex/about/", "/in/person-3/details/experience/"]
    assert env.storage.get_job(job_id)["detail_pages"] == 7 and env.storage.detail_pages_today() == 7
    assert elapsed >= 7 * 4                                 # the 4-8 s pause before every one of them
    assert "Company details: done, 2 companies read" in env.h.heartbeats
    _wait(env, lambda: not any("/in/" in u or "/company/" in u or "/search/results/" in u for u in _tabs(env)), 15)
    assert not any("/in/" in u or "/company/" in u or "/search/results/" in u for u in _tabs(env))
    # a second identical run costs nothing: every company and profile is remembered
    before = len(env.h.company_loads)
    assert env.storage.next_detail_task(job_id) is None
    assert len(env.h.company_loads) == before


def test_profile_with_several_roles_and_a_top_card_is_read(env):
    env.h.company_about = {"initech": ("Software Development", "10,001+ employees", "dl")}
    env.h.profiles = {
        "person-1": {"top": "Initech",
                     "entries": [{"title": "Head of Talent", "company": "Initech", "slug": "initech",
                                  "dates": "Mar 2024 - Present · 1 yr", "grouped": True}]},
    }
    job_id = _run(env)
    p1 = _leads(env, job_id)["Person 1"]
    assert (p1["company"], p1["company_industry"], p1["company_size"]) == ("Initech", "Software Development", "10,001+ employees")
    assert env.h.profile_reads[0]["company"] == "Initech"


def test_current_job_without_a_company_link_is_not_guessed(env):
    # Real pages (Nov 2026): reading the company from the ORDER of text lines gave job titles and
    # places as companies. Only LinkedIn's own company link is trusted now.
    env.h.profiles = {"person-1": {"entries": [
        {"title": "TA Lead", "company": "Hooli", "slug": "", "dates": "Jan 2024 - Present"},          # current, no link
        {"title": "Recruiter", "company": "Old Co", "slug": "old-co", "dates": "2019 - 2022 · 3 yrs"}]}}   # past, linked
    job_id = _run(env)
    p1 = _leads(env, job_id)["Person 1"]
    assert (p1["company"], p1["company_url"], p1["company_industry"]) == ("Company1", "", "")     # card's value kept, nothing guessed
    assert p1["detail_note"].startswith('latest company on the page has no "Present"')
    assert "/company/old-co/about/" not in env.h.company_loads


def test_location_and_extra_lines_between_title_and_dates_do_not_confuse_it(env):
    env.h.company_about = {"atlan": ("Software Development", "201-500 employees", "dl")}
    env.h.profiles = {"person-1": {"variant": "messy", "entries": [
        {"title": "Engineering Recruiter", "company": "Atlan", "slug": "atlan", "dates": "Mar 2025 - Present · 9 mos"}]}}
    job_id = _run(env)
    p1 = _leads(env, job_id)["Person 1"]
    assert (p1["company"], p1["company_industry"]) == ("Atlan", "Software Development")


def test_limit_page_on_a_profile_stops_everything_and_keeps_the_tab(env):
    env.h.profiles = {"person-1": {"variant": "limit"}}
    _run(env)
    assert any("Company details stopped" in b and "unusual activity" in b for b in env.h.heartbeats)
    n = len(env.h.company_loads)
    time.sleep(10)                                          # long enough for a next load, if there were one
    assert len(env.h.company_loads) == n and env.h.profile_reads == []
    assert any("/in/person-1" in u for u in _tabs(env))


def test_page_limit_ends_the_details_cleanly(env):
    job_id = new_job(env, target=3)
    with env.storage._tx() as c:
        c.execute("UPDATE jobs SET detail_pages=59 WHERE job_id=?", (job_id,))
    env.launch(job_id)
    env.wait_job(job_id)
    _wait(env, lambda: _done(env))
    assert len(env.h.company_loads) == 1                    # page 60 allowed, 61 refused
    assert any("limit of" in b or "done" in b for b in env.h.heartbeats if b.startswith("Company details"))


def test_no_details_when_nothing_is_left_to_look_up(env):
    env.h.details_on = True
    for name in ("Company1", "Company2", "Company3"):
        env.storage.save_company(name, "X", "11-50 employees", "", "found")
    job_id = new_job(env, target=3)
    env.launch(job_id)
    env.wait_job(job_id)
    _wait(env, lambda: not any("/search/results/" in u for u in _tabs(env)), 20)
    assert env.h.company_loads == [] and not any("/search/results/" in u for u in _tabs(env))
    assert _leads(env, job_id)["Person 2"]["company_industry"] == "X"


def test_result_card_linking_its_company_twice_is_one_company(env):
    env.h.company_results = {"Company2": [("company-2", "Company2", "98765")]}     # vanity + numeric link, one card
    env.h.company_about = {"company-2": ("Insurance", "11-50 employees", "dl")}
    job_id = _run(env)
    assert _leads(env, job_id)["Person 2"]["company_industry"] == "Insurance"
    assert "/company/company-2/about/" in env.h.company_loads


def test_experience_page_layout_and_reasons_are_kept(env):
    env.h.company_about = {"umbrella": ("Pharmaceutical Manufacturing", "5,001-10,000 employees", "dl")}
    env.h.profiles = {
        "person-1": {"variant": "details", "entries": [{"title": "TA Partner", "company": "Umbrella", "slug": "umbrella",
                                                        "dates": "Feb 2025 - Present · 9 mos"}]},
        "person-3": {"variant": "details", "entries": [{"title": "Recruiter", "company": "Past Co", "slug": "past-co",
                                                        "dates": "2019 - 2022 · 3 yrs"}]},
    }
    job_id = _run(env)
    people = _leads(env, job_id)
    assert (people["Person 1"]["company"], people["Person 1"]["company_industry"]) == ("Umbrella", "Pharmaceutical Manufacturing")
    assert people["Person 3"]["company_industry"] == ""
    assert people["Person 3"]["detail_note"].startswith('latest job has no "Present" (no current job shown) [page: ')
    notes = {r["name"]: r["note"] for r in env.storage.all_companies()}
    assert notes["Company1"].startswith('no result named exactly "Company1"')


def test_layouts_without_list_items_are_read_from_the_text(env):
    env.h.company_variant = "divs"
    env.h.company_results = {"Company2": [("company-2", "Company2")]}         # name linked twice, no li
    env.h.company_about = {"company-2": ("Telecom", "11-50 employees", "dl"),
                           "acme-x": ("Retail", "201-500 employees", "dl")}
    env.h.profiles = {"person-1": {"variant": "divs", "entries": [
        {"title": "Recruiter", "company": "Acme X", "slug": "acme-x", "dates": "Jan 2024 - Present · 1 yr"}]}}
    job_id = _run(env)
    people = _leads(env, job_id)
    assert people["Person 2"]["company_industry"] == "Telecom"
    assert (people["Person 1"]["company"], people["Person 1"]["company_url"], people["Person 1"]["company_industry"]) == (
        "Acme X", "https://www.linkedin.com/company/acme-x/", "Retail")
