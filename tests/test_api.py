"""API tests for app/main.py.

These need FastAPI and httpx (requirements-dev.txt). They were written in a
workspace where those packages could not be installed, so they have NOT been
run by the author. They are skipped when the packages are missing.
"""
import csv
import io
import json
import zipfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.storage import Storage  # noqa: E402


@pytest.fixture()
def client(tmp_path):
    main.set_storage(Storage(str(tmp_path / "api.db")))
    with TestClient(main.app) as c:
        yield c
    main.set_storage(None)


def start(client, **kw):
    body = {"roles": ["Engineering Manager"], "locations": ["Bengaluru, Karnataka, India"],
            "keywords": [], "target": 25}
    body.update(kw)
    return client.post("/api/search/start", json=body)


def person(n, frame=True, **kw):
    d = {"name": f"P{n}", "profile_url": f"https://www.linkedin.com/in/p{n}/?q=1",
         "headline": f"Engineer at Co{n}", "location": "Bengaluru, Karnataka, India",
         "badge_frame": frame, "open_roles": False, "card_text": ""}
    d.update(kw)
    return d


def test_full_flow_start_ingest_status_stop_export(client):
    r = start(client)
    assert r.status_code == 200
    j = r.json()
    assert j["url"].startswith("https://www.linkedin.com/search/results/people/?keywords=")
    assert "geoUrn" not in j["url"] and "geo_url_base" in j
    job_id = j["job_id"]

    g = client.post("/api/search/geo", json={"job_id": job_id, "geo_ids": []})
    assert g.status_code == 200 and g.json()["text_filter"] is True

    ing = client.post("/api/ingest", json={"job_id": job_id, "people": [
        person(1), person(2), person(3, frame=False), person(4, location="Mumbai, India")]})
    assert ing.status_code == 200
    body = ing.json()
    assert body["continue"] is True and body["saved"] == 2 and body["total"] == 4

    st = client.get("/api/status", params={"job_id": job_id}).json()
    assert st["saved"] == 2 and st["not_hiring"] == 1 and st["other_locations"] == 1
    assert st["summary"].startswith("2 of 25 saved · read 4 people on 1 page · filtered out:")
    # overall-completion bar: lead details read so far, out of the leads saved
    assert (st["details_on"], st["details_done"], st["details_total"]) == (True, 0, 2)

    assert client.post("/api/heartbeat", json={"job_id": job_id, "message": "page 1: 4 cards"}).status_code == 200
    assert client.get("/api/status", params={"job_id": job_id}).json()["message"] == "page 1: 4 cards"

    assert client.post("/api/stop", params={"job_id": job_id}).status_code == 200
    assert client.get("/api/status", params={"job_id": job_id}).json()["status"] == "stopped"
    assert client.post("/api/ingest", json={"job_id": job_id, "people": [person(9)]}).json()["continue"] is False
    # a late message from the closing tab does not overwrite the stopped run's message
    before = client.get("/api/status", params={"job_id": job_id}).json()["message"]
    assert client.post("/api/heartbeat", json={"job_id": job_id, "message": "page 2: waiting 5s"}).json()["ignored"]
    assert client.get("/api/status", params={"job_id": job_id}).json()["message"] == before

    leads = client.get("/api/leads", params={"job_id": job_id}).json()
    assert leads["total"] == 2

    csv_res = client.get("/api/export.csv", params={"job_id": job_id})
    assert csv_res.status_code == 200 and "text/csv" in csv_res.headers["content-type"]
    rows = list(csv.reader(io.StringIO(csv_res.content.decode("utf-8-sig"))))
    from app.export import COLUMNS
    assert rows[0] == COLUMNS and "Business Email" not in COLUMNS and len(COLUMNS) == 15 and "Company Industry" in COLUMNS
    assert len(rows) == 3

    x = client.get("/api/export.xlsx", params={"job_id": job_id})
    assert x.status_code == 200 and x.content[:2] == b"PK"


def test_validation_errors(client):
    assert start(client, locations=[]).status_code == 400
    assert start(client, target=30).status_code == 400
    assert start(client, roles=[f"r{i}" for i in range(6)]).status_code == 400


def test_job_titles_are_optional(client):
    r = start(client, roles=[])
    assert r.status_code == 200
    assert "keywords=hiring&" in r.json()["geo_url_base"]


def _job_with_companies(client, pages=0):
    from app import main
    import uuid
    tag = uuid.uuid4().hex[:6]
    job_id = start(client).json()["job_id"]
    st = main.db()
    st.ingest(job_id, [{"profile_url": f"https://www.linkedin.com/in/c{i}-{tag}", "name": f"C{i}",
                        "headline": f"EM at Co{i}{'' if pages == 0 else tag}", "location": "Bengaluru, Karnataka, India",
                        "badge_frame": True, "open_roles": True, "card_text": ""} for i in range(20)])
    with st._tx() as c:
        c.execute("UPDATE jobs SET pages=? WHERE job_id=?", (pages, job_id))
    return job_id


def test_each_person_goes_to_one_tab_only(client):
    job_id = _job_with_companies(client)
    got = [client.get("/api/details/next", params={"job_id": job_id}).json()["task"] for _ in range(4)]
    assert all(t["kind"] == "profile" for t in got)
    assert len({t["person_id"] for t in got}) == 4                 # 4 tabs asking: 4 different people


def test_detail_endpoints_hand_out_tasks_and_count_pages(client):
    job_id = _job_with_companies(client)
    task = client.get("/api/details/next", params={"job_id": job_id}).json()["task"]
    assert task["kind"] == "profile"
    assert client.post("/api/details/page", json={"job_id": job_id}).json()["ok"] is True
    assert client.get("/api/details/next", params={"job_id": "nope"}).status_code == 404
    from app import main
    pid = main.db().all_leads({"job_id": job_id})[0]["id"]
    r = client.post("/api/details/profile", json={"job_id": job_id, "person_id": pid, "company": "Real Co",
                                                   "company_url": "https://evil.example/company/x/"})
    assert r.json() == {"ok": True, "about": "", "search": "Real Co"}             # a bad link is dropped
    # two tabs reach the same company page: only the first one reads it
    p2, p3 = [p["id"] for p in main.db().all_leads({"job_id": job_id})[1:3]]
    link = "https://www.linkedin.com/company/same-co/"
    a = client.post("/api/details/profile", json={"job_id": job_id, "person_id": p2, "company": "Same Co", "company_url": link})
    b = client.post("/api/details/profile", json={"job_id": job_id, "person_id": p3, "company": "Same Co", "company_url": link})
    assert (a.json()["about"], b.json()["about"]) == (link, "")
    assert client.post("/api/details/profile", json={"job_id": job_id, "person_id": 10 ** 9}).status_code == 404
    client.post("/api/stop", params={"job_id": job_id})
    assert client.post("/api/details/page", json={"job_id": job_id}).json()["ok"] is False   # stopped: no page


def test_detail_pages_stop_at_the_run_limit(client):
    from app.storage import MAX_DETAIL_PAGES_PER_RUN
    job_id = _job_with_companies(client)
    oks = [client.post("/api/details/page", json={"job_id": job_id}).json()["ok"] for _ in range(MAX_DETAIL_PAGES_PER_RUN + 1)]
    assert oks.count(True) == MAX_DETAIL_PAGES_PER_RUN and oks[-1] is False
    assert "limit" in client.post("/api/details/page", json={"job_id": job_id}).json()["reason"]


def test_company_values_not_in_linkedins_format_are_dropped(client):
    job_id = _job_with_companies(client)
    ok = client.post("/api/companies", json={"job_id": job_id, "name": "Co1", "status": "found",
                                             "industry": "Software  Development", "size": "11-50 employees",
                                             "linkedin_url": "https://www.linkedin.com/company/co1/"})
    assert ok.status_code == 200
    client.post("/api/companies", json={"job_id": job_id, "name": "Co2", "status": "found",
                                        "industry": "Banking", "size": "about fifty", "linkedin_url": "https://evil.example/x"})
    client.post("/api/companies", json={"job_id": job_id, "name": "Co3", "status": "not_found", "industry": "made up"})
    assert client.post("/api/companies", json={"job_id": job_id, "name": "Co4", "status": "guess"}).status_code == 400
    by = {p["company"]: p for p in client.get("/api/leads", params={"job_id": job_id, "page_size": 100}).json()["items"]}
    assert (by["Co1"]["company_industry"], by["Co1"]["company_size"]) == ("Software Development", "11-50 employees")
    assert (by["Co2"]["company_industry"], by["Co2"]["company_size"]) == ("Banking", "")
    assert (by["Co3"]["company_industry"], by["Co3"]["company_size"]) == ("", "")


def test_unknown_job_is_404(client):
    assert client.get("/api/status", params={"job_id": "nope"}).status_code == 404
    assert client.post("/api/ingest", json={"job_id": "nope", "people": []}).status_code == 404
    assert client.post("/api/heartbeat", json={"job_id": "nope", "message": "x"}).status_code == 404


def test_sixth_parallel_search_is_refused(client):
    for _ in range(5):
        assert start(client).status_code == 200
    assert start(client).status_code == 429


def test_geo_resolved_turns_text_filter_off(client):
    job_id = start(client).json()["job_id"]
    g = client.post("/api/search/geo", json={"job_id": job_id, "geo_ids": ["102713980"]})
    assert g.json()["text_filter"] is False
    r = client.post("/api/ingest", json={"job_id": job_id, "people": [person(1, location="Mumbai")]})
    assert r.json()["saved"] == 1


def test_error_and_finish_endpoints(client):
    job_id = start(client).json()["job_id"]
    client.post("/api/error", json={"job_id": job_id, "message": "limit page"})
    st = client.get("/api/status", params={"job_id": job_id}).json()
    assert st["status"] == "error" and st["error"] == "limit page"
    j2 = start(client).json()["job_id"]
    client.post("/api/search/finish", json={"job_id": j2, "reason": "no more pages"})
    st2 = client.get("/api/status", params={"job_id": j2}).json()
    assert st2["status"] == "done" and "Only 0 new people" in st2["short_reason"]


def test_oversized_ingest_rejected(client):
    job_id = start(client).json()["job_id"]
    r = client.post("/api/ingest", json={"job_id": job_id, "people": [person(i) for i in range(101)]})
    assert r.status_code == 422


def test_settings_roundtrip(client):
    assert client.get("/api/settings").json()["save_only_confirmed"] is True
    r = client.post("/api/settings", json={"save_only_confirmed": False, "daily_cap": 120})
    assert r.json() == {"save_only_confirmed": False, "daily_cap": 120, "actively_hiring": True}
    assert client.post("/api/settings", json={"daily_cap": 0}).status_code == 422


def test_actively_hiring_setting_controls_both_urls(client):
    assert client.get("/api/settings").json()["actively_hiring"] is True       # default ON
    j = start(client).json()
    for u in (j["url"], j["geo_url_base"]):
        assert "&activelyHiringForJobTitles=%5B%22-100%22%5D" in u
    client.post("/api/settings", json={"actively_hiring": False})
    j = start(client).json()
    assert "activelyHiring" not in j["url"] and "activelyHiring" not in j["geo_url_base"]
    assert "Bengaluru" not in j["geo_url_base"]          # extension adds &geoUrn= instead


def test_geo_one_id_per_place_required(client):
    job_id = start(client, locations=["Bengaluru, Karnataka, India", "Pune"]).json()["job_id"]
    g = client.post("/api/search/geo", json={"job_id": job_id, "geo_ids": ["105214831"]})
    assert g.json()["text_filter"] is True               # Pune not resolved -> keep text filter


def test_extension_info_and_zip(client):
    info = client.get("/api/extension/info").json()
    manifest = json.loads((main.EXT_DIR / "manifest.json").read_text())
    assert info == {"version": manifest["version"]}
    z = client.get("/api/extension.zip")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    for need in ("manifest.json", "background.js", "content_search.js", "dashboard_bridge.js",
                 "options.html", "options.js", "config.js"):
        assert need in names, need
    assert not any(n.startswith("extension/") for n in names)


def test_dashboard_page_and_suggestions(client):
    r = client.get("/")
    assert r.status_code == 200 and "Hiring Badge Finder" in r.text
    titles = client.get("/api/titles").json()
    assert "Engineering Manager" in titles and "Technical Recruiter" in titles
    presets = client.get("/api/title-presets").json()
    assert set(presets) == {"Recruiters", "Tech hiring managers", "Founders & CTOs"}
    for names in presets.values():
        assert 1 <= len(names) <= 5 and set(names) <= set(titles)       # fits the 5-title limit
    assert any("Bengaluru" in x for x in client.get("/api/locations").json())


def test_two_searches_do_not_mix_via_api(client):
    a = start(client).json()["job_id"]
    b = start(client).json()["job_id"]
    client.post("/api/ingest", json={"job_id": a, "people": [person(1), person(2)]})
    client.post("/api/ingest", json={"job_id": b, "people": [person(3)]})
    assert client.get("/api/leads", params={"job_id": a}).json()["total"] == 2
    assert client.get("/api/leads", params={"job_id": b}).json()["total"] == 1



def test_same_search_continues_and_resume_off_starts_fresh(client):
    j = start(client).json()
    assert j["start_page"] == 1 and "&page=" not in j["url"]
    client.post("/api/ingest", json={"job_id": j["job_id"], "people": [person(i, frame=False) for i in range(10)]})
    client.post("/api/search/finish", json={"job_id": j["job_id"], "reason": "stopped"})
    # same search, titles in another order and case -> continues at page 2
    j2 = start(client, roles=["engineering manager"]).json()
    assert j2["start_page"] == 2 and j2["url"].endswith("&page=2") and j2["geo_url_base"].endswith("&page=2")
    assert "Continuing from page 2" in client.get("/api/status", params={"job_id": j2["job_id"]}).json()["message"]
    client.post("/api/stop", params={"job_id": j2["job_id"]})
    j3 = start(client, resume=False).json()
    assert j3["start_page"] == 1 and "&page=" not in j3["url"]
    other = start(client, locations=["Pune"]).json()                  # a different search starts at 1
    assert other["start_page"] == 1


def test_rewind_endpoint(client):
    j = start(client).json()["job_id"]
    assert client.post("/api/search/rewind", json={"job_id": j}).json()["ok"] is True
    assert client.post("/api/search/rewind", json={"job_id": "nope"}).status_code == 404



def test_widened_location_keeps_the_city_filter(client):
    job_id = start(client, locations=["Mumbai, Maharashtra, India"]).json()["job_id"]
    g = client.post("/api/search/geo", json={"job_id": job_id, "geo_ids": ["102713980"], "widened": True})
    assert g.json()["text_filter"] is True                     # searched in India, keep only Mumbai
    r = client.post("/api/ingest", json={"job_id": job_id, "people": [
        person(1, location="Mumbai, Maharashtra, India"), person(2, location="Pune, Maharashtra, India"),
        person(3, location="Greater Bombay Area")]})
    assert r.json()["saved"] == 2                               # Mumbai and Bombay, not Pune



def _geo_job(client, place):
    job_id = start(client, locations=[place]).json()["job_id"]
    client.post("/api/search/geo", json={"job_id": job_id, "geo_ids": ["111"], "widened": False})
    return job_id


def test_first_page_in_the_right_city_gives_no_warning(client):
    job_id = _geo_job(client, "Mumbai, Maharashtra, India")
    r = client.post("/api/ingest", json={"job_id": job_id, "people": [
        person(1, location="Mumbai, Maharashtra, India"), person(2, location="Greater Bombay Area"),
        person(3, location="Pune, Maharashtra, India")]}).json()
    assert r["warning"] == "" and client.get(f"/api/status?job_id={job_id}").json()["warning"] == ""


def test_first_page_mostly_elsewhere_warns_but_keeps_running(client):
    job_id = _geo_job(client, "Mumbai, Maharashtra, India")
    r = client.post("/api/ingest", json={"job_id": job_id, "people": [
        person(1, location="Bengaluru, Karnataka, India"), person(2, location="Bengaluru"),
        person(3, location="Mumbai, Maharashtra, India")]}).json()
    text = ("Warning: Most results are not in Mumbai. The saved location may be wrong. "
            "Check Settings -> Locations set up on LinkedIn.")
    assert r["warning"] == text and r["continue"] is True                 # the run is not stopped
    st = client.get(f"/api/status?job_id={job_id}").json()
    assert st["warning"] == text and st["status"] == "running"
    # only the first page is checked
    job2 = _geo_job(client, "Pune, Maharashtra, India")
    client.post("/api/ingest", json={"job_id": job2, "people": [person(10, location="Pune")]})
    r2 = client.post("/api/ingest", json={"job_id": job2, "people": [person(11, location="Delhi"), person(12, location="Delhi")]}).json()
    assert r2["warning"] == ""



def test_geo_places_are_saved_shared_and_removable(client):
    assert client.get("/api/geo").json() == {}
    r = client.post("/api/geo", json={"place": "Mumbai,  Maharashtra, India", "geo_id": "106164952"}).json()
    assert r["places"] == {"mumbai, maharashtra, india": "106164952"}
    client.post("/api/geo", json={"place": "Mumbai, Maharashtra, India", "geo_id": "999"})       # set up again
    assert client.get("/api/geo").json() == {"mumbai, maharashtra, india": "999"}
    assert client.post("/api/geo", json={"place": "Pune", "geo_id": "12a"}).status_code == 422  # digits only
    assert client.delete("/api/geo", params={"place": "MUMBAI, Maharashtra, India"}).json()["ok"] is True
    assert client.get("/api/geo").json() == {}


def test_ten_places_accepted_eleven_refused(client):
    ten = [f"City{i}, State, India" for i in range(10)]
    assert start(client, locations=ten).status_code == 200
    assert start(client, locations=ten + ["One more, India"]).status_code == 422


def test_filter_not_applied_note_is_in_the_summary(client):
    job_id = start(client).json()["job_id"]
    assert client.post("/api/search/note", json={"job_id": job_id, "note": "filter not applied"}).json()["ok"] is True
    client.post("/api/search/note", json={"job_id": job_id, "note": "filter not applied"})          # once only
    s = client.get(f"/api/status?job_id={job_id}").json()["summary"]
    assert s.endswith(" · filter not applied") and s.count("filter not applied") == 1


def test_every_search_keeps_only_roles_of_the_last_month(client):
    from app import main
    job_id = start(client, posted_within="day").json()["job_id"]     # a value from the page is ignored
    assert main.db().get_job(job_id)["posted_within"] == "month"


# ------------------------------------------------------------- deployment
SITE = "https://hbf-test.onrender.com"


def test_cors_trusts_only_local_and_own_deployment(client):
    hdr = lambda o: {"Origin": o, "Access-Control-Request-Method": "GET"}  # noqa: E731
    ok = client.options("/api/jobs", headers=hdr("http://localhost:8010"))
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:8010"
    bad = client.options("/api/jobs", headers=hdr("https://stranger.onrender.com"))
    assert "access-control-allow-origin" not in bad.headers


def test_public_url_only_accepts_https(monkeypatch):
    monkeypatch.delenv("HBF_PUBLIC_URL", raising=False)
    monkeypatch.setenv("RENDER_EXTERNAL_URL", SITE + "/")
    assert main.public_url() == SITE
    monkeypatch.setenv("HBF_PUBLIC_URL", "http://insecure.example.com")
    assert main.public_url() == ""


def test_local_extension_zip_trusts_no_hosting_domain(client, monkeypatch):
    monkeypatch.delenv("HBF_PUBLIC_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/extension.zip").content))
    assert "onrender" not in z.read("manifest.json").decode()
    assert "const DEFAULT_SERVER = 'http://localhost:8000';" in z.read("background.js").decode()


def test_deployed_extension_zip_points_at_its_own_address(client, monkeypatch):
    monkeypatch.setenv("HBF_PUBLIC_URL", SITE)
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/extension.zip").content))
    m = json.loads(z.read("manifest.json"))
    assert SITE + "/*" in m["host_permissions"]
    bridge = next(cs for cs in m["content_scripts"] if "dashboard_bridge.js" in cs["js"])
    assert SITE + "/*" in bridge["matches"]
    assert not any("*.onrender" in x for x in m["host_permissions"] + bridge["matches"])
    assert f"const DEFAULT_SERVER = '{SITE}';" in z.read("background.js").decode()
    opts = z.read("options.js").decode()
    assert f"const DEFAULT_SERVER = '{SITE}';" in opts and f"const DEPLOYED_SERVER = '{SITE}';" in opts


# ------------------------------------------------------------- fast lead details (one call per lead)
def test_fast_details_one_call_saves_and_hands_out_the_next(client):
    job = start(client).json()["job_id"]
    client.post("/api/ingest", json={"job_id": job, "people": [person(1), person(2)]})
    r = client.post("/api/details/fast", json={"job_id": job}).json()
    first = r["task"]
    assert r["stop"] == "" and first["kind"] == "profile"
    done = {"person_id": first["person_id"], "company": "MediaTek", "title": "Senior Recruiter",
            "company_url": "https://www.linkedin.com/company/mediatek/", "page_name": "MediaTek",
            "industry": "Semiconductors", "size": "10,001+ employees"}
    r = client.post("/api/details/fast", json={"job_id": job, "done": done}).json()
    second = r["task"]
    assert second and second["person_id"] != first["person_id"]          # the next lead, not the same one
    r = client.post("/api/details/fast", json={"job_id": job, "done": {"person_id": second["person_id"],
                                                                         "note": "no current job on the profile"}}).json()
    assert r["task"] is None
    leads = {p["id"]: p for p in client.get("/api/leads", params={"job_id": job}).json()["items"]}
    got = leads[first["person_id"]]
    assert (got["company"], got["company_industry"], got["company_size"]) == ("MediaTek", "Semiconductors", "10,001+ employees")
    assert leads[second["person_id"]]["company_industry"] in ("", None)
    assert client.get("/api/status", params={"job_id": job}).json()["detail_pages"] == 2     # each lead counted once


def test_fast_details_stops_when_the_search_was_stopped(client):
    job = start(client).json()["job_id"]
    client.post("/api/ingest", json={"job_id": job, "people": [person(1)]})
    client.post("/api/stop", params={"job_id": job})
    r = client.post("/api/details/fast", json={"job_id": job}).json()
    assert r["task"] is None and r["stop"]


def test_server_time_header(client):
    assert "x-process-ms" in client.get("/api/settings").headers


def test_open_roles_from_the_profile_data_are_saved_matched_and_filtered(client):
    """Open roles ride along in the same /api/details/fast call (no extra LinkedIn request).
    With skills in the search, "Open Role Matches" is Yes only when an open role names one."""
    job = start(client, keywords=["Java", "Spring Boot"]).json()["job_id"]
    client.post("/api/ingest", json={"job_id": job, "people": [person(1), person(2), person(3)]})
    t1 = client.post("/api/details/fast", json={"job_id": job}).json()["task"]
    t2 = client.post("/api/details/fast", json={"job_id": job, "done": {
        "person_id": t1["person_id"], "company": "Acme", "title": "Recruiter",
        "open_roles": ["Senior Java Developer · Acme · Chennai (On-site)"], "role_types": ["x.jobs.JobPosting"]}}).json()["task"]
    t3 = client.post("/api/details/fast", json={"job_id": job, "done": {
        "person_id": t2["person_id"], "company": "Daasho", "title": "TA",
        "open_roles": ["Business Development Executive (BDE) · Daasho · Chennai (On-site)"]}}).json()["task"]
    client.post("/api/details/fast", json={"job_id": job, "done": {"person_id": t3["person_id"], "company": "Globex",
                                                                     "roles_status": "none"}})
    leads = {p["id"]: p for p in client.get("/api/leads", params={"job_id": job}).json()["items"]}
    assert leads[t1["person_id"]]["hiring_for"].startswith("Senior Java Developer")
    assert [leads[t["person_id"]]["open_role_match"] for t in (t1, t2, t3)] == ["Yes", "No", "No open role"]
    only = client.get("/api/leads", params={"job_id": job, "open_match": "1"}).json()
    assert only["total"] == 1 and only["items"][0]["id"] == t1["person_id"]
    rows = list(csv.reader(io.StringIO(client.get("/api/export.csv", params={"job_id": job, "open_match": "1"}).content.decode("utf-8-sig"))))
    r = dict(zip(rows[0], rows[1]))
    assert len(rows) == 2 and r["Hiring For (Open Roles)"].startswith("Senior Java Developer")
    assert "Open Role Matches" not in rows[0] and rows[0][-2:] == ["Date Collected (IST)", "Last Verified Date (IST)"]


def test_old_fast_payload_without_open_roles_still_works(client):
    """A 1.47 extension sends no open_roles: nothing breaks, and the lead is NOT called 'No open role'."""
    job = start(client).json()["job_id"]
    client.post("/api/ingest", json={"job_id": job, "people": [person(1)]})
    t = client.post("/api/details/fast", json={"job_id": job}).json()["task"]
    r = client.post("/api/details/fast", json={"job_id": job, "done": {"person_id": t["person_id"], "company": "Acme"}})
    assert r.status_code == 200
    lead = client.get("/api/leads", params={"job_id": job}).json()["items"][0]
    assert (lead["hiring_for"], lead["open_role_match"]) == ("", "")      # not read: blank, never "No open role"


def test_unreadable_hiring_box_stays_blank_and_the_run_log_says_why(client):
    job = start(client).json()["job_id"]
    client.post("/api/ingest", json={"job_id": job, "people": [person(n) for n in range(1, 6)]})
    t = client.post("/api/details/fast", json={"job_id": job}).json()["task"]
    seen = []
    orig = main.bus.publish
    main.bus.publish = lambda kind, data: (seen.append(data.get("message", "")), orig(kind, data))
    try:
        for _ in range(5):
            t = client.post("/api/details/fast", json={"job_id": job, "done": {
                "person_id": t["person_id"], "company": "Acme", "roles_status": "unknown",
                "roles_hint": "Hiring something odd"}}).json()["task"] or t
    finally:
        main.bus.publish = orig
    leads = client.get("/api/leads", params={"job_id": job}).json()["items"]
    assert all(p["open_role_match"] == "" for p in leads)
    assert any(m.startswith("Open roles: could not read") and "Hiring something odd" in m for m in seen)
