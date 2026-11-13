from openpyxl.utils import get_column_letter
import csv
import io
import threading

import pytest
from openpyxl import load_workbook

from app import export
from app.storage import MAX_PAGES_PER_RUN, Storage, open_storage


@pytest.fixture()
def st(tmp_path):
    return Storage(str(tmp_path / "t.db"))


def person(n, frame=True, **kw):
    d = {"profile_url": f"https://www.linkedin.com/in/p{n}/?x=1", "name": f"P{n}",
         "headline": f"Engineer at Co{n}", "location": "Bengaluru, Karnataka, India",
         "badge_frame": frame, "open_roles": False, "card_text": ""}
    d.update(kw)
    return d


def new_job(st, target=25, places=None, locs=("Bengaluru",)):
    return st.create_job(["Engineering Manager"], list(locs), [], target, text_filter_places=places or [])


# ------------------------------------------------------------------ ingest
def test_ingest_saves_and_counts(st):
    j = new_job(st)
    r = st.ingest(j, [person(1), person(2), person(3, frame=False)])
    assert r["saved"] == 2 and r["total"] == 3 and r["continue"] is True
    c = r["counters"]
    assert c["not_hiring"] == 1 and c["pages"] == 1
    rows = st.all_leads({"job_id": j})
    assert {p["profile_url"] for p in rows} == {"https://www.linkedin.com/in/p1", "https://www.linkedin.com/in/p2"}
    assert all(p["status"] == "Badge confirmed" for p in rows)


def test_company_and_title_parsed_and_missing_left_empty(st):
    j = new_job(st)
    st.ingest(j, [person(1, headline="Engineering Manager at Acme"),
                  person(2, headline="Hiring SDETs")])
    by = {p["name"]: p for p in st.all_leads()}
    assert (by["P1"]["title"], by["P1"]["company"]) == ("Engineering Manager", "Acme")
    assert (by["P2"]["title"], by["P2"]["company"]) == ("", "")


def test_degree_suffix_stripped_from_name_on_ingest(st):
    j = new_job(st)
    st.ingest(j, [person(1, name="Ravindra Pai • 2nd"), person(2, name="Ann Lee · 3rd+")])
    assert {p["name"] for p in st.all_leads()} == {"Ravindra Pai", "Ann Lee"}


def test_duplicates_within_run_are_counted(st):
    j = new_job(st)
    r = st.ingest(j, [person(1), person(1), person(1, profile_url="https://www.linkedin.com/in/P1/")])
    assert r["saved"] == 1 and r["counters"]["duplicates"] == 2


def test_duplicates_across_pages_of_same_run(st):
    j = new_job(st)
    st.ingest(j, [person(1)])
    r = st.ingest(j, [person(1), person(2)])
    assert r["saved"] == 2 and r["counters"]["duplicates"] == 1


def test_people_from_earlier_runs_are_skipped_and_counted(st):
    j1 = new_job(st)
    st.ingest(j1, [person(1)])
    j2 = new_job(st)
    r = st.ingest(j2, [person(1), person(2)])
    assert r["saved"] == 1 and r["counters"]["already_collected"] == 1
    assert st.count_people() == 2


def test_known_person_gets_last_seen_updated(st, monkeypatch):
    import app.storage as s
    j1 = new_job(st)
    monkeypatch.setattr(s, "now_ist", lambda: "2026-01-01 10:00:00")
    st.ingest(j1, [person(1)])
    monkeypatch.setattr(s, "now_ist", lambda: "2026-02-02 11:00:00")
    j2 = new_job(st)
    st.ingest(j2, [person(1)])
    p = st.all_leads()[0]
    assert p["first_found"] == "2026-01-01 10:00:00" and p["last_seen"] == "2026-02-02 11:00:00"


def test_stops_exactly_at_target(st):
    j = new_job(st, target=3)
    r = st.ingest(j, [person(i) for i in range(1, 11)])
    assert r["saved"] == 3 and r["continue"] is False and r["reason"] == "target reached"
    assert st.count_people() == 3
    assert st.get_job(j)["status"] == "done"
    # a late page after the end changes nothing
    r2 = st.ingest(j, [person(20)])
    assert r2["continue"] is False and st.count_people() == 3


def test_not_confirmed_filtered_when_setting_on(st):
    j = new_job(st)
    r = st.ingest(j, [person(1), person(2, frame=False, open_roles=True)])
    assert r["saved"] == 1 and r["counters"]["not_confirmed"] == 1


def test_likely_hiring_saved_when_setting_off(st):
    st.set_settings(save_only_confirmed=False)
    j = new_job(st)
    r = st.ingest(j, [person(1), person(2, frame=False, open_roles=True),
                      person(3, frame=False, headline="We're hiring | EM at Acme")])
    assert r["saved"] == 3
    statuses = {p["name"]: p["status"] for p in st.all_leads()}
    assert statuses == {"P1": "Badge confirmed", "P2": "Likely hiring", "P3": "Likely hiring"}
    ev = {p["name"]: p["evidence"] for p in st.all_leads()}
    assert ev["P2"] == "See open roles"


def test_likely_upgraded_when_later_seen_with_badge(st):
    st.set_settings(save_only_confirmed=False)
    j1 = new_job(st)
    st.ingest(j1, [person(1, frame=False, open_roles=True)])
    j2 = new_job(st)
    st.ingest(j2, [person(1, frame=True)])
    p = st.all_leads()[0]
    assert p["status"] == "Badge confirmed" and p["badge_frame"] == 1


def test_location_text_filter(st):
    j = new_job(st, places=["Bengaluru"])
    r = st.ingest(j, [person(1), person(2, location="Mumbai, India"), person(3, location="")])
    assert r["saved"] == 1 and r["counters"]["other_locations"] == 2


def test_no_location_filter_when_places_resolved(st):
    j = new_job(st, places=[])
    r = st.ingest(j, [person(1, location="Mumbai, India")])
    assert r["saved"] == 1


def test_set_text_filter_after_creation(st):
    j = new_job(st, places=["Bengaluru"])
    st.set_text_filter(j, [], ["123"])
    r = st.ingest(j, [person(1, location="Mumbai, India")])
    assert r["saved"] == 1 and st.get_job(j)["geo_ids"] == ["123"]


def test_invalid_urls_are_counted_not_saved(st):
    j = new_job(st)
    r = st.ingest(j, [person(1, profile_url="https://www.linkedin.com/company/acme"), person(2, profile_url="")])
    assert r["saved"] == 0 and r["counters"]["invalid"] == 2


def test_page_limit_ends_run(st):
    j = new_job(st, target=100)
    r = None
    for i in range(MAX_PAGES_PER_RUN):
        r = st.ingest(j, [person(1000 + i)])
    assert r["continue"] is False and r["reason"] == "page limit reached"
    assert st.get_job(j)["status"] == "done"


def test_daily_cap_truncates_and_stops(st):
    st.set_settings(daily_cap=5)
    j = new_job(st, target=100)
    r = st.ingest(j, [person(i) for i in range(1, 11)], browser_id="b1")
    assert r["total"] == 5 and r["saved"] == 5 and r["continue"] is False
    assert r["reason"] == "daily limit reached"
    assert st.reads_today("b1") == 5
    # same browser, new search: nothing more is read today
    j2 = new_job(st)
    r2 = st.ingest(j2, [person(50)], browser_id="b1")
    assert r2["total"] == 0 and r2["continue"] is False
    # a different browser has its own count
    j3 = new_job(st)
    assert st.ingest(j3, [person(60)], browser_id="b2")["saved"] == 1


def test_stopped_job_ignores_ingest(st):
    j = new_job(st)
    st.stop_job(j)
    r = st.ingest(j, [person(1)])
    assert r["continue"] is False and st.count_people() == 0
    assert st.get_job(j)["status"] == "stopped"


def test_unknown_job_raises(st):
    with pytest.raises(KeyError):
        st.ingest("nope", [])


def test_max_five_parallel_jobs(st):
    for _ in range(5):
        new_job(st)
    with pytest.raises(RuntimeError):
        new_job(st)


def test_error_and_finish_states(st):
    j = new_job(st)
    st.set_error(j, "boom")
    job = st.get_job(j)
    assert job["status"] == "error" and job["error"] == "boom"
    j2 = new_job(st)
    st.finish_job(j2, "no more pages")
    assert st.get_job(j2)["end_reason"] == "no more pages"


def test_timestamps_are_ist_format(st):
    j = new_job(st)
    st.ingest(j, [person(1)])
    ts = st.all_leads()[0]["first_found"]
    assert len(ts) == 19 and ts[4] == "-" and ts[10] == " "


# ------------------------------------------------------------ concurrency
def test_two_searches_do_not_mix_results(st):
    ja, jb = new_job(st, target=100), new_job(st, target=100)
    errs = []

    def run(job, offset):
        try:
            for page in range(5):
                st.ingest(job, [person(offset + page * 10 + i) for i in range(10)])
        except Exception as e:  # pragma: no cover
            errs.append(e)

    ta = threading.Thread(target=run, args=(ja, 1000))
    tb = threading.Thread(target=run, args=(jb, 2000))
    ta.start(); tb.start(); ta.join(); tb.join()
    assert not errs
    a = st.all_leads({"job_id": ja}); b = st.all_leads({"job_id": jb})
    assert len(a) == 50 and len(b) == 50
    assert all(int(p["name"][1:]) < 2000 for p in a)
    assert all(int(p["name"][1:]) >= 2000 for p in b)
    assert st.get_job(ja)["saved"] == 50 and st.get_job(jb)["saved"] == 50


def test_200_parallel_ingests_lose_nothing(st):
    """200 searches, each ingesting one person, run in parallel waves of 5 (the allowed maximum)."""
    results, errs = [], []
    lock = threading.Lock()

    def worker(n):
        try:
            job = st.create_job(["R"], ["L"], [], 25)
            r = st.ingest(job, [person(5000 + n)])
            st.finish_job(job, "test")
            with lock:
                results.append(r["saved"])
        except Exception as e:
            with lock:
                errs.append(repr(e))

    for wave in range(0, 200, 5):
        ts = [threading.Thread(target=worker, args=(n,)) for n in range(wave, wave + 5)]
        [t.start() for t in ts]
        [t.join() for t in ts]
    assert not errs, errs[:3]
    assert len(results) == 200 and sum(results) == 200
    assert st.count_people() == 200


def test_200_parallel_ingests_into_one_running_job(st, monkeypatch):
    """200 simultaneous requests against one search: every request is counted, none lost."""
    import app.storage as s
    monkeypatch.setattr(s, "MAX_PAGES_PER_RUN", 10_000)
    st.set_settings(daily_cap=5000)
    j = st.create_job(["R"], ["L"], [], 100)
    errs = []

    def worker(n):
        try:
            st.ingest(j, [person(7000 + n)])
        except Exception as e:  # pragma: no cover
            errs.append(repr(e))

    ts = [threading.Thread(target=worker, args=(n,)) for n in range(200)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not errs, errs[:3]
    job = st.get_job(j)
    # target is 100, so exactly 100 are saved and the other requests find the job finished
    assert job["saved"] == 100 and st.count_people() == 100
    assert job["status"] == "done"
    assert job["pages"] == 100


# ------------------------------------------------------------------ leads
def test_filters_and_paging(st):
    st.set_settings(save_only_confirmed=False)
    j = st.create_job(["Engineering Manager"], ["Bengaluru"], [], 100)
    st.ingest(j, [person(i, headline=f"Lead {i} at Acme") for i in range(1, 13)]
              + [person(99, frame=False, open_roles=True, name="Zed", location="Pune, India")])
    assert st.list_leads({}, 1, 5)["total"] == 13
    assert len(st.list_leads({}, 1, 5)["items"]) == 5
    assert st.list_leads({"status": "Likely hiring"})["total"] == 1
    assert st.list_leads({"text": "zed"})["total"] == 1
    assert st.list_leads({"location": "Pune"})["total"] == 1
    assert st.list_leads({"role": "engineering"})["total"] == 13
    assert st.list_leads({"text": "100%"})["total"] == 0        # LIKE wildcards are escaped
    assert st.list_leads({"job_id": "other"})["total"] == 0
    today = st.all_leads()[0]["first_found"][:10]
    assert st.list_leads({"date": today})["total"] == 13
    assert st.list_leads({"date": "2000-01-01"})["total"] == 0


# ------------------------------------------------------------------ export
def test_csv_columns_exact_and_values(st):
    j = new_job(st)
    st.ingest(j, [person(1, headline="Engineering Manager at Acme", name='Ann "AJ" Lee, PhD')])
    data = export.to_csv(st.all_leads())
    text = data.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ["Person Name", "Current Designation", "Headline", "Location", "Location Matches Search",
                       "Person LinkedIn URL", "Hiring Status",
                       "Company Name", "Company Industry", "Company Size", "Company Website",
                       "Source Platform", "Date Collected (IST)", "Last Verified Date (IST)"]
    for gone in ("Hiring Role(s)", "Headline Has Searched Title", "Business Email", "Hiring For", "Hiring Company", "Posted"):          # removed on request
        assert gone not in rows[0]
    for gone in ("Hiring Signal Type", "Hiring Evidence", "Qualification Reason", "Email Status", "Email Source"):
        assert gone not in rows[0]
    r = dict(zip(rows[0], rows[1]))
    assert r["Person Name"] == 'Ann "AJ" Lee, PhD'
    assert r["Person LinkedIn URL"] == "https://www.linkedin.com/in/p1"
    assert (r["Current Designation"], r["Company Name"]) == ("Engineering Manager", "Acme")
    assert r["Hiring Status"] == "Badge confirmed"
    assert r["Company Website"] == ""                                  # website worker not run yet
    assert r["Source Platform"] == "LinkedIn – People search"
    assert r["Date Collected (IST)"] and r["Last Verified Date (IST)"]
    assert len(rows) == 2


def test_csv_hiring_fields_from_frame_roles_and_headline(st):
    j = new_job(st)
    st.ingest(j, [person(1, open_roles=True, headline="Senior EM - Hiring SDETs for my team")])
    rows = list(csv.reader(io.StringIO(export.to_csv(st.all_leads()).decode("utf-8-sig"))))
    assert "Hiring Role(s)" not in rows[0]
    assert export.hiring_roles(st.all_leads()[0]["headline"]) == "SDETs"           # helper still works
    assert export.signal_type(st.all_leads()[0]) == "#HIRING frame + open roles"      # helper still works


@pytest.mark.parametrize("headline,roles", [
    ("Senior Engineering Manager - Hiring SDETs for my team", "SDETs"),
    ("We are hiring backend engineers | EM at Acme", "backend engineers"),
    ("Hiring: Java developers, QA leads", "Java developers, QA leads"),
    ("Hiring for Data Engineers in Bengaluru", "Data Engineers"),
    ("Talent Acquisition | Hiring for Tech roles", "Tech roles"),
    ("Hiring Manager at Globex", ""),                    # a job title, not a role being hired
    ("#hiring Engineering Manager | Platform", ""),      # after a hashtag it's their own title
    ("Hiring process consultant", ""),
    ("Now hiring!", ""),
    ("Hiring: 1) System Architect Java 2) Test Lead", ""),   # numbered list: not guessed
    ("Engineering Manager at Acme", ""),
])
def test_hiring_roles_from_headline(headline, roles):
    assert export.hiring_roles(headline) == roles


def test_csv_blocks_formula_injection(st):
    j = new_job(st)
    st.ingest(j, [person(1, name="=HYPERLINK(\"http://x\")"), person(2, name="+cmd"), person(3, name="@x")])
    rows = list(csv.reader(io.StringIO(export.to_csv(st.all_leads()).decode("utf-8-sig"))))[1:]
    assert all(r[0].startswith("'") for r in rows)


def test_xlsx_header_and_rows(st):
    j = new_job(st)
    st.ingest(j, [person(1), person(2)])
    wb = load_workbook(io.BytesIO(export.to_xlsx(st.all_leads())))
    ws = wb.active
    assert [c.value for c in ws[1]] == export.COLUMNS
    assert ws.max_row == 3


# -------------------------------------------------------------- DATABASE_URL
def test_open_storage_sqlite_url(tmp_path):
    s = open_storage(f"sqlite:///{tmp_path}/x.db")
    assert s.count_people() == 0


def test_open_storage_postgres_not_built_yet():
    with pytest.raises(NotImplementedError):
        open_storage("postgresql://u:p@h/db")


def test_open_storage_rejects_unknown():
    with pytest.raises(ValueError):
        open_storage("mysql://x")


def test_wal_mode_enabled(st):
    with st._read() as c:
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert c.execute("PRAGMA busy_timeout").fetchone()[0] == 30000


# ------------------------------------------------------- continue searches
def test_continue_progress_target_end_and_rewind(st):
    key = "same-search"
    assert st.next_page(key) == 1                                    # never run
    j = st.create_job(["EM"], ["Bengaluru"], [], 25, search_key=key)
    st.ingest(j, [person(i, frame=False) for i in range(10)])        # page 1, nothing saved
    assert st.next_page(key) == 2
    st.ingest(j, [person(10 + i, frame=False) for i in range(10)])  # page 2
    assert st.next_page(key) == 3
    st.finish_job(j, "page limit reached")
    assert st.next_page(key) == 3                                    # next run starts at page 3

    j2 = st.create_job(["EM"], ["Bengaluru"], [], 2, search_key=key, start_page=3)
    st.ingest(j2, [person(100 + i) for i in range(10)])             # target 2 reached part-way through page 3
    assert st.next_page(key) == 3                                    # page 3 is read again next time

    j3 = st.create_job(["EM"], ["Bengaluru"], [], 25, search_key=key, start_page=3)
    st.finish_job(j3, "no more pages")
    assert st.next_page(key) == 1                                    # end of results: start over

    j4 = st.create_job(["EM"], ["Bengaluru"], [], 25, search_key="k2", start_page=7)
    assert st.rewind_job(j4) is True and st.get_job(j4)["start_page"] == 1
    st.ingest(j4, [person(200, frame=False)])
    assert st.rewind_job(j4) is False                                # only before any page was read



def test_xlsx_is_a_formatted_table_with_links_and_dates(st):
    j = new_job(st)
    st.ingest(j, [person(1), person(2, name="=cmd")])
    ws = load_workbook(io.BytesIO(export.to_xlsx(st.all_leads()))).active
    assert list(ws.tables) == ["HiringPeople"] and ws.tables["HiringPeople"].ref == "A1:N3"
    assert ws.freeze_panes == "B2"
    hdr = [c.value for c in ws[1]]
    url = ws.cell(row=2, column=hdr.index("Person LinkedIn URL") + 1)
    assert url.hyperlink.target == url.value and url.value.startswith("https://www.linkedin.com/in/")
    when = ws.cell(row=2, column=hdr.index("Date Collected (IST)") + 1).value
    assert hasattr(when, "year")                                      # a real date, not text
    assert all(ws.column_dimensions[get_column_letter(i)].width >= 12 for i in range(1, 15))
    names = {ws.cell(row=r, column=1).value for r in (2, 3)}
    assert "'=cmd" in names                                           # formula injection still blocked


def test_xlsx_without_people_still_opens():
    ws = load_workbook(io.BytesIO(export.to_xlsx([]))).active
    assert [c.value for c in ws[1]] == export.COLUMNS and not ws.tables


def test_reads_today_most_counts_the_extensions_own_browser_id(st):
    j = new_job(st)
    st.ingest(j, [person(i) for i in range(1, 4)], browser_id="chrome-uuid")
    assert st.reads_today() == 0                      # nothing under "default"
    assert st.reads_today_most() == 3


def test_role_filter_takes_several_roles(st):
    a = st.create_job(["Engineering Manager"], ["Bengaluru"], [], 25)
    b = st.create_job(["Technical Recruiter"], ["Bengaluru"], [], 25)
    c = st.create_job(["CTO"], ["Bengaluru"], [], 25)
    st.ingest(a, [person(1)]); st.ingest(b, [person(2)]); st.ingest(c, [person(3)])
    names = lambda role: {p["profile_url"] for p in st.list_leads({"role": role}, 1, 50)["items"]}
    assert len(names("Engineering Manager")) == 1                          # one role: as before
    assert len(names("Engineering Manager|Technical Recruiter")) == 2       # any of several
    assert len(names(" | ")) == 3                                           # empty parts ignored


def test_location_filter_takes_several_places(st):
    a = st.create_job(["Engineering Manager"], ["Bengaluru"], [], 25)
    b = st.create_job(["Engineering Manager"], ["Pune"], [], 25)
    st.ingest(a, [person(1, location="Bengaluru, Karnataka, India")])
    st.ingest(b, [person(2, location="Pune, Maharashtra, India")])
    n = lambda loc: st.list_leads({"location": loc}, 1, 50)["total"]
    assert n("Bengaluru") == 1 and n("Pune") == 1                       # one place: as before
    assert n("Bengaluru|Pune") == 2                                      # any of several
    assert n("Mumbai") == 0


def test_match_columns_flag_without_dropping():
    from app.export import location_in_search, title_in_headline
    base = {"search_roles": "Recruiter, Engineering Manager", "search_locations": "Lucknow, Uttar Pradesh, India; Pune"}
    assert title_in_headline({**base, "headline": "Senior ENGINEERING manager | Cloud"}) == "Yes"
    assert title_in_headline({**base, "headline": "Head of HR"}) == "No"
    assert title_in_headline({**base, "headline": "Recruiters Inc founder"}) == "No"
    assert title_in_headline({"headline": "x"}) == ""
    assert location_in_search({**base, "location": "Greater Lucknow Area"}) == "Yes"
    assert location_in_search({**base, "location": "India"}) == "No"
    assert location_in_search({**base, "location": ""}) == ""


# ------------------------------------------------------- company details
def test_detail_tasks_are_profiles_and_known_exact_companies_need_no_page(st):
    """1.42.0: the company comes from the person's own Experience page (exact link), never from a
    name search (it picked wrong companies). A company already read by link is reused at once."""
    j = new_job(st)
    st.ingest(j, [person(1, headline="EM at Acme Pvt. Ltd"), person(2, headline="CTO at acme pvt ltd"),
                  person(3, headline="Hiring SDETs"), person(4, headline="VP at Globex")])
    ids = {p["name"]: p["id"] for p in st.all_leads({"job_id": j})}
    t1 = st.next_detail_task(j)
    assert t1 == {"kind": "profile", "person_id": ids["P1"], "url": "https://www.linkedin.com/in/p1"}
    r = st.save_profile_company(j, ids["P1"], "Acme Pvt. Ltd", "EM", "https://www.linkedin.com/company/acme/")
    assert r == {"ok": True, "about": "https://www.linkedin.com/company/acme/", "search": ""}
    st.save_company("Acme Pvt. Ltd", "Software Development", "51-200 employees",
                    "https://www.linkedin.com/company/acme/", "found", by_link=True, page_name="Acme")
    # P2 works at the same company: filled at once, no LinkedIn page; next is P3
    assert st.next_detail_task(j, exclude=set())["person_id"] == ids["P3"]
    assert st.next_detail_task(j, exclude={ids["P3"]})["person_id"] == ids["P4"]     # another tab has P3
    # P3: the exact link but no readable name -> its About page names it
    r = st.save_profile_company(j, ids["P3"], "", "", "https://www.linkedin.com/company/sdetco/")
    assert r == {"ok": True, "about": "https://www.linkedin.com/company/sdetco/", "search": ""}
    st.save_company("sdetco", "IT Services and IT Consulting", "11-50 employees",
                    "https://www.linkedin.com/company/sdetco/", "found", by_link=True, page_name="SDET Co")
    st.save_profile_company(j, ids["P4"], "", "", "")                              # showed no current job
    assert st.next_detail_task(j) is None                                        # nothing is ever repeated
    by = {p["name"]: p for p in st.all_leads({"job_id": j})}
    assert (by["P2"]["company"], by["P2"]["company_industry"]) == ("Acme", "Software Development")
    assert (by["P3"]["company"], by["P3"]["company_size"]) == ("SDET Co", "11-50 employees")
    assert (by["P4"]["company"], by["P4"]["company_industry"]) == ("Globex", "")
    assert st.save_profile_company(j, 99999, "X", "", "") == {"ok": False, "about": "", "search": ""}


def test_profile_company_link_is_exact_even_when_names_collide(st):
    j = new_job(st)
    st.ingest(j, [person(1, headline="Recruiter at Unity"), person(2, headline="Recruiter at Unity")])
    a, b = [p["id"] for p in sorted(st.all_leads({"job_id": j}), key=lambda p: p["id"])]
    st.save_company("Unity", "", "", "", "not_found")                            # two companies called Unity
    st.save_profile_company(j, a, "Unity", "", "https://www.linkedin.com/company/unity-technologies/")
    st.save_company("Unity", "Software Development", "5,001-10,000 employees",
                    "https://www.linkedin.com/company/unity-technologies/", "found", by_link=True)
    by = {p["id"]: p for p in st.all_leads({"job_id": j})}
    assert by[a]["company_industry"] == "Software Development"
    assert by[b]["company_industry"] == ""                  # same name, but not shown to be the same company


def test_detail_pages_have_their_own_run_and_day_limits(st):
    j1, j2 = new_job(st), new_job(st)
    assert all(st.count_detail_page(j1, 3, 5) for _ in range(3))
    assert st.count_detail_page(j1, 3, 5) is False                             # run limit
    assert st.count_detail_page(j2, 3, 5) and st.count_detail_page(j2, 3, 5)
    assert st.count_detail_page(j2, 3, 5) is False                             # day limit (5)
    assert st.detail_pages_today() == 5
    assert st.get_job(j1)["pages"] == 0                                          # search pages untouched


def test_company_details_reach_leads_and_excel_and_stay_empty_when_unknown(st):
    j = new_job(st)
    st.ingest(j, [person(1, headline="EM at Acme"), person(2, headline="CTO at Globex"), person(3, headline="Hiring")])
    st.save_company("Acme", "IT Services and IT Consulting", "1,001-5,000 employees",
                    "https://www.linkedin.com/company/acme/", "found")
    st.save_company("Globex", "", "", "", "not_found")
    by = {p["company"]: p for p in st.all_leads({"job_id": j})}
    assert by["Acme"]["company_industry"] == "IT Services and IT Consulting"
    assert by["Acme"]["company_size"] == "1,001-5,000 employees"
    assert by["Globex"]["company_industry"] == "" and by[""]["company_size"] == ""
    assert st.list_leads({"job_id": j})["items"][0].keys() >= {"company_industry", "company_size"}
    ws = load_workbook(io.BytesIO(export.to_xlsx(st.all_leads({"job_id": j})))).active
    hdr = [c.value for c in ws[1]]
    assert "Company Industry" in hdr and "Company Size" in hdr


def test_bad_company_status_is_refused(st):
    with pytest.raises(ValueError):
        st.save_company("Acme", "x", "", "", "guessed")


def test_company_from_the_cards_current_line_only_when_the_headline_has_none(st):
    j = new_job(st)
    st.ingest(j, [person(1, headline="Talent Acquisition Manager | Strategic Hiring", current="Talent Partner at Zscaler"),
                  person(2, headline="Recruiter at Acme", current="Advisor at Other Co"),
                  person(3, headline="Talent & People Operations"),
                  person(4, headline="Talent Acquisition @ Rippling - Always hiring!")])
    by = {p["name"]: p for p in st.all_leads({"job_id": j})}
    assert (by["P1"]["title"], by["P1"]["company"]) == ("Talent Partner", "Zscaler")
    assert (by["P2"]["title"], by["P2"]["company"]) == ("Recruiter", "Acme")       # headline wins
    assert (by["P3"]["title"], by["P3"]["company"]) == ("", "")                    # nothing shown: stays empty
    assert by["P4"]["company"] == "Rippling"


# ------------------------------------------------------- date posted
def test_date_posted_filter_skips_only_known_older_roles(st):
    j = st.create_job(["Recruiter"], ["Bengaluru"], [], 25, posted_within="week")
    r = st.ingest(j, [person(1, posted_ago="3 days ago", posted_days=3),
                      person(2, posted_ago="3 months ago", posted_days=90),
                      person(3),                                              # date not found: kept
                      person(4, posted_ago="1 week ago", posted_days=7)])
    assert r["counters"]["old_roles"] == 1 and r["saved"] == 3
    by = {p["name"]: p for p in st.all_leads({"job_id": j})}
    assert set(by) == {"P1", "P3", "P4"}
    assert (by["P1"]["latest_posted"], by["P3"]["latest_posted"]) == ("3 days ago", "")
    assert "1 with only older open roles" in __import__("app.summary", fromlist=["x"]).summary_line(st.get_job(j))


def test_any_date_keeps_everyone_and_still_fills_the_column(st):
    j = st.create_job(["Recruiter"], ["Bengaluru"], [], 25)
    st.ingest(j, [person(1, posted_ago="2 years ago", posted_days=730)])
    assert st.all_leads({"job_id": j})[0]["latest_posted"] == "2 years ago"


# ------------------------------------------------- "Hiring For" / "Posted" (Open roles pop-up)


def test_excel_rows_are_one_aligned_line_each_and_text_never_spills(st):
    j = new_job(st)
    st.ingest(j, [person(1, headline="A very long headline " * 10), person(2, headline="Recruiter at Acme")])
    ws = load_workbook(io.BytesIO(export.to_xlsx(st.all_leads({"job_id": j})))).active
    for row in ws.iter_rows(min_row=2):
        assert ws.row_dimensions[row[0].row].height == 18          # fixed: one line shows per row
        for cell in row:
            # wrap inside a fixed row: long text stays in its own cell, never spills into the next column
            assert (cell.alignment.horizontal, cell.alignment.vertical) == ("left", "top") and cell.alignment.wrap_text
    hdr = [c.value for c in ws[1]]
    assert ws.column_dimensions["A"].width == 24 and ws.column_dimensions[get_column_letter(hdr.index("Headline") + 1)].width == 50


# ------------------------------------------------- Company Website (business email removed in 1.40.0)
def test_website_is_looked_up_once_for_everyone_and_nothing_else_is_saved(st, tmp_path):
    j = new_job(st)
    st.ingest(j, [person(1, headline="EM at Acme | reach me at Ravi.K@Acme.io"), person(2, name="Madhu")])
    todo = st.people_without_website_check()
    assert [p["company"] for p in todo] == ["Acme", "Co2"]          # one-word names are looked up too
    st.set_website(todo[0]["id"], "acme.io")
    st.set_website(todo[0]["id"], "other.com")                     # never overwritten
    st.set_website(todo[1]["id"], "")
    assert st.people_without_website_check() == []
    by = {p["name"]: p for p in st.all_leads()}
    assert by["P1"]["company_website"] == "acme.io" and by["Madhu"]["company_website"] == ""
    assert "Business Email" not in export.COLUMNS
    # a database made before the website columns existed is upgraded in place
    import sqlite3
    old = tmp_path / "old.db"
    Storage(str(old))
    con = sqlite3.connect(old)
    for col in ("company_website", "website_checked"):
        con.execute(f"ALTER TABLE people DROP COLUMN {col}")
    con.commit(); con.close()
    Storage(str(old))
    cols = {r[1] for r in sqlite3.connect(old).execute("PRAGMA table_info(people)")}
    assert {"company_website", "website_checked"} <= cols


# ------------------------------------- company accuracy (real export, 12 Nov 2026: 13 of 25 were titles)
def test_a_job_title_from_the_profile_never_replaces_the_company(st):
    j = new_job(st)
    st.ingest(j, [person(1, headline="Technical Talent Acquisition Specialist @ VOIS")])
    pid = st.all_leads()[0]["id"]
    r = st.save_profile_company(j, pid, "Technical Talent Acquisition Specialist", "",
                                "https://www.linkedin.com/company/vois/")
    assert r == {"ok": True, "about": "https://www.linkedin.com/company/vois/", "search": ""}
    p = st.all_leads()[0]
    assert p["company"] == "VOIS" and p["company_url"] == "https://www.linkedin.com/company/vois/"
    assert "looked like a job title" in p["detail_note"]
    # the About page then gives the company's own name, website, industry and size
    st.save_company("VOIS", "IT Services and IT Consulting", "10,001+ employees",
                    "https://www.linkedin.com/company/vois/", "found", by_link=True,
                    page_name="_VOIS (Vodafone Intelligent Solutions)", website="vodafone.com")
    p = st.all_leads()[0]
    assert (p["company"], p["company_website"], p["company_industry"]) == (
        "_VOIS (Vodafone Intelligent Solutions)", "vodafone.com", "IT Services and IT Consulting")


def test_a_title_already_saved_as_company_is_not_exported_as_one(st):
    j = new_job(st)
    st.ingest(j, [person(1, headline="Talent Scout | AI")])
    with st._tx() as c:                                  # an old row, saved before this fix
        c.execute("UPDATE people SET company='Senior Talent Acquisition Specialist'")
    row = dict(zip(export.COLUMNS, export.rows(st.all_leads())[0]))
    assert row["Company Name"] == ""


def test_profile_title_fills_an_empty_designation(st):
    j = new_job(st)
    st.ingest(j, [person(1, headline="Building teams | AI hiring")])
    pid = st.all_leads()[0]["id"]
    st.save_profile_company(j, pid, "Eightfold.ai", "Talent Scout", "https://www.linkedin.com/company/eightfold/")
    p = st.all_leads()[0]
    assert (p["title"], p["company"]) == ("Talent Scout", "Eightfold.ai")
