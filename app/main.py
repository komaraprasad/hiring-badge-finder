"""Hiring Badge Finder - FastAPI server.

The server never contacts LinkedIn. It only receives data that the browser
extension reads from the user's own logged-in LinkedIn tab.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import threading
import time
import zipfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from . import export, website
from .detection import looks_like_job_title
from .data import LOCATIONS, SKILLS, TITLE_PRESETS, TITLES
from .events import EventBus
from .storage import (MAX_DETAIL_PAGES_PER_DAY, MAX_DETAIL_PAGES_PER_RUN, MAX_PAGES_PER_RUN, Storage,
                      open_storage)
from .summary import short_reason, summary_line
from .urlbuilder import (MAX_KEYWORDS, MAX_LOCATIONS, MAX_ROLES, build_url, clean_list,
                         wrong_city_warning)

ROOT = Path(__file__).resolve().parent.parent
EXT_DIR = ROOT / "extension"
STATIC = Path(__file__).resolve().parent / "static"
VALID_TARGETS = (25, 50, 75, 100)

def public_url() -> str:
    """This server's own public address when deployed ('' when it runs on this computer).
    HBF_PUBLIC_URL wins; on Render, RENDER_EXTERNAL_URL is set automatically."""
    url = os.environ.get("HBF_PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or ""
    url = url.strip().rstrip("/")
    return url if re.fullmatch(r"https://[\w.-]+(:\d+)?", url) else ""


app = FastAPI(title="Hiring Badge Finder", version="1.0.0")
# Only this computer and this deployment's OWN address are trusted, never a whole hosting domain
# (any stranger can put a page on *.onrender.com).
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_origins=[public_url()] if public_url() else [],
    allow_methods=["*"], allow_headers=["*"],
)

bus = EventBus()
_storage: Storage | None = None


def db() -> Storage:
    global _storage
    if _storage is None:
        _storage = open_storage()
    return _storage


def set_storage(s: Storage | None) -> None:
    """Used by tests to point the app at a temporary database."""
    global _storage
    _storage = s


# ------------------------------------------------------------- website worker
def _website_worker() -> None:
    """Fills Company Website for saved people (see app/website.py). Never contacts LinkedIn.
    One lookup per company name (cached), whatever the person's name looks like."""
    cache: dict = {}
    while True:
        todo: list = []
        try:
            todo = db().people_without_website_check(20)
            for p in todo:
                ok = p["company"] and not looks_like_job_title(p["company"])
                db().set_website(p["id"], website.find_domain(p["company"], cache=cache) if ok else "")
        except OSError:
            todo = []          # offline or DNS service unreachable: leave them for the next round
        except Exception:      # noqa: BLE001 - the worker must never stop the server
            todo = []
        time.sleep(1 if todo else 15)


@app.on_event("startup")
def _start_website_worker() -> None:
    if os.environ.get("HBF_WEBSITE_LOOKUP", "1") == "1":
        threading.Thread(target=_website_worker, name="website-worker", daemon=True).start()


# ------------------------------------------------------------------- models
class StartBody(BaseModel):
    roles: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list, max_length=MAX_LOCATIONS)   # 11 places: refused
    keywords: list[str] = Field(default_factory=list)
    target: int = 25
    resume: bool = True          # continue where the last identical search stopped
    posted_within: str = ""      # ignored: every search keeps open roles of the last month (POSTED_WITHIN)


class Person(BaseModel):
    name: str = Field("", max_length=300)
    profile_url: str = Field("", max_length=600)
    headline: str = Field("", max_length=600)
    location: str = Field("", max_length=300)
    degree: str = Field("", max_length=20)
    badge_frame: bool = False
    open_roles: bool = False
    card_text: str = Field("", max_length=3000)
    current: str = Field("", max_length=400)      # the card's "Current: Title at Company" line, if shown
    posted_ago: str = Field("", max_length=60)    # newest open role, as LinkedIn shows it ("3 months ago")
    posted_days: int | None = Field(None, ge=0, le=36500)   # its age in days; None = not found


class IngestBody(BaseModel):
    job_id: str
    people: list[Person] = Field(default_factory=list, max_length=100)
    browser_id: str = Field("default", max_length=80)


class HeartbeatBody(BaseModel):
    job_id: str
    message: str = Field("", max_length=500)


class ErrorBody(BaseModel):
    job_id: str
    message: str = Field("", max_length=500)


class FinishBody(BaseModel):
    job_id: str
    reason: str = Field("no more pages", max_length=120)


class GeoBody(BaseModel):
    job_id: str
    geo_ids: list[str] = Field(default_factory=list, max_length=MAX_LOCATIONS)
    widened: bool = False      # a place was searched within a larger region (e.g. Mumbai within India)


class GeoPlaceBody(BaseModel):
    place: str = Field(..., min_length=1, max_length=120)
    geo_id: str = Field(..., pattern=r"^\d{1,15}$")


class SettingsBody(BaseModel):
    save_only_confirmed: bool | None = None
    daily_cap: int | None = Field(None, ge=1, le=5000)
    actively_hiring: bool | None = None


# ------------------------------------------------------------------- helpers
def _job_or_404(job_id: str) -> dict:
    job = db().get_job(job_id)
    if not job:
        raise HTTPException(404, "Unknown search. Start a new one.")
    return job


# Wrong-city warnings by job id (kept in memory: shown during the run, not part of the saved data).
WARNINGS: dict[str, str] = {}
# Short notes added to a run's summary line (e.g. "filter not applied"), by job id; in memory.
NOTES: dict[str, list[str]] = {}


def _public_job(job: dict) -> dict:
    out = dict(job)
    out["warning"] = WARNINGS.get(job.get("job_id"), "")
    out["summary"] = summary_line(job)
    notes = NOTES.get(job.get("job_id")) or []
    if notes:
        out["summary"] = out["summary"] + " · " + ", ".join(notes)
    out["short_reason"] = short_reason(job)
    out["max_pages"] = MAX_PAGES_PER_RUN
    return out


def _publish_status(job_id: str) -> None:
    job = db().get_job(job_id)
    if job:
        bus.publish("status", _public_job(job))


# ----------------------------------------------------------------- endpoints
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/titles")
def titles():
    return TITLES


@app.get("/api/skills")
def skills():
    return SKILLS


@app.get("/api/title-presets")
def title_presets():
    return TITLE_PRESETS


@app.get("/api/locations")
def locations():
    return LOCATIONS


@app.get("/api/geo")
def geo_places():
    """Location IDs set up so far (place -> ID); the dashboard hands them to the extension."""
    return db().geo_places()


@app.post("/api/geo")
def save_geo_place(body: GeoPlaceBody):
    db().save_geo_place(body.place, body.geo_id)
    return {"ok": True, "places": db().geo_places()}


@app.delete("/api/geo")
def forget_geo_place(place: str = Query(..., min_length=1)):
    return {"ok": db().forget_geo_place(place), "places": db().geo_places()}


@app.get("/api/settings")
def get_settings():
    return {**db().get_settings(), "reads_today": db().reads_today_most()}


@app.post("/api/settings")
def post_settings(body: SettingsBody):
    return db().set_settings(body.save_only_confirmed, body.daily_cap, body.actively_hiring)


# Server rule (not on the dashboard, on request Nov 2026): keep only people whose newest open role was
# posted within the last month. A person is skipped only when that date is KNOWN to be older.
POSTED_WITHIN = "month"


@app.post("/api/search/start")
def search_start(body: StartBody):
    roles = clean_list(body.roles, MAX_ROLES)
    places = clean_list(body.locations, MAX_LOCATIONS)
    keywords = clean_list(body.keywords, MAX_KEYWORDS)
    if not places:
        raise HTTPException(400, "Add at least one location.")
    if len(body.roles) > MAX_ROLES and len(roles) >= MAX_ROLES:
        raise HTTPException(400, f"Use at most {MAX_ROLES} job titles.")
    if body.target not in VALID_TARGETS:
        raise HTTPException(400, "Leads wanted must be 25, 50, 75 or 100.")
    ah = db().get_settings()["actively_hiring"]
    key = search_key(roles, places, keywords, ah) + POSTED_WITHIN
    page = db().next_page(key) if body.resume else 1
    try:
        # Until the extension confirms location IDs, filter by location text.
        job_id = db().create_job(roles, places, keywords, body.target, text_filter_places=places,
                                 search_key=key, start_page=page, posted_within=POSTED_WITHIN)
    except RuntimeError as e:
        raise HTTPException(429, str(e))
    if page > 1:
        db().set_message(job_id, f"Continuing from page {page} (where the last same search stopped)")
    fallback_url = build_url(roles, keywords, None, places, ah, page)   # place names in keywords
    base_url = build_url(roles, keywords, None, None, ah, page)         # extension adds &geoUrn=
    _publish_status(job_id)
    return {"job_id": job_id, "url": fallback_url, "geo_url_base": base_url,
            "locations": places, "target": body.target, "start_page": page}


def search_key(roles: list[str], places: list[str], keywords: list[str], actively_hiring: bool) -> str:
    """Same titles, places, skills and filter (any order or case) = the same search."""
    norm = lambda xs: sorted(x.strip().lower() for x in xs)  # noqa: E731
    return json.dumps([norm(roles), norm(places), norm(keywords), bool(actively_hiring)])


class RewindBody(BaseModel):
    job_id: str


@app.post("/api/search/rewind")
def search_rewind(body: RewindBody):
    """The continued page was empty (fewer results now): this job restarts at page 1."""
    _job_or_404(body.job_id)
    ok = db().rewind_job(body.job_id)
    if ok:
        db().set_message(body.job_id, "That page was empty; starting again from page 1")
        _publish_status(body.job_id)
    return {"ok": ok}


@app.post("/api/search/geo")
def search_geo(body: GeoBody):
    """The extension reports which places it resolved to LinkedIn location IDs.

    All places resolved exactly -> LinkedIn filters by place, no text filter needed.
    Widened (a city searched within its country) -> keep the text filter for the asked places.
    No IDs -> the extension used place names in keywords, so we filter by text.
    """
    job = _job_or_404(body.job_id)
    places = job["locations"]
    resolved_all = len(body.geo_ids) == len(places) and len(places) > 0
    exact = resolved_all and not body.widened
    db().set_text_filter(body.job_id, [] if exact else places, body.geo_ids if resolved_all else [])
    return {"ok": True, "text_filter": not exact}


@app.post("/api/ingest")
def ingest(body: IngestBody):
    job = _job_or_404(body.job_id)
    people = [p.model_dump() for p in body.people]
    result = db().ingest(body.job_id, people, body.browser_id)
    # First page of a search that LinkedIn filtered by location IDs: are the people in the asked
    # places? If most are not, the saved ID is probably wrong. Warn only; the run goes on.
    if job.get("pages", 0) == 0 and job.get("geo_ids") and job.get("locations"):
        warning = wrong_city_warning([p.get("location") or "" for p in people], job["locations"])
        if warning:
            WARNINGS[body.job_id] = warning
            db().set_message(body.job_id, warning)
            bus.publish("heartbeat", {"job_id": body.job_id, "message": warning})
    for person in result["new_people"]:
        bus.publish("person", person)
    _publish_status(body.job_id)
    return {"continue": result["continue"], "saved": result["saved"],
            "total": result["total"], "target": result["target"], "reason": result["reason"],
            "warning": WARNINGS.get(body.job_id, "")}


class NoteBody(BaseModel):
    job_id: str
    note: str = Field(..., min_length=1, max_length=60)


@app.post("/api/search/note")
def add_note(body: NoteBody):
    """A short note for the run's summary line, e.g. "filter not applied" (kept once)."""
    _job_or_404(body.job_id)
    notes = NOTES.setdefault(body.job_id, [])
    if body.note not in notes:
        notes.append(body.note)
    _publish_status(body.job_id)
    return {"ok": True}


@app.post("/api/heartbeat")
def heartbeat(body: HeartbeatBody):
    job = db().get_job(body.job_id)
    if job and job["status"] in ("stopped", "error"):
        # Stopped: late messages from a tab that is closing must not make the run
        # look as if it is still going (the dashboard keeps "Stopped.").
        return {"ok": True, "ignored": True}
    if not db().set_message(body.job_id, body.message):
        raise HTTPException(404, "Unknown search.")
    bus.publish("heartbeat", {"job_id": body.job_id, "message": body.message})
    return {"ok": True}


@app.post("/api/error")
def report_error(body: ErrorBody):
    if not db().set_error(body.job_id, body.message):
        raise HTTPException(404, "Unknown search.")
    bus.publish("error", {"job_id": body.job_id, "message": body.message})
    _publish_status(body.job_id)
    return {"ok": True}


@app.post("/api/search/finish")
def finish(body: FinishBody):
    _job_or_404(body.job_id)
    db().finish_job(body.job_id, body.reason)
    _publish_status(body.job_id)
    return {"ok": True}


class CompanyBody(BaseModel):
    job_id: str
    name: str = Field(..., min_length=1, max_length=200)
    status: str                                   # found | not_found
    industry: str = Field("", max_length=120)
    size: str = Field("", max_length=60)
    linkedin_url: str = Field("", max_length=300)
    by_link: bool = False                         # the page came from a profile's company link (exact)
    page_name: str = Field("", max_length=200)     # the company's name as its own LinkedIn page shows it
    website: str = Field("", max_length=200)       # the "Website" on its LinkedIn About page
    note: str = Field("", max_length=300)         # why it is empty, for checking


class DetailPageBody(BaseModel):
    job_id: str


class ProfileCompanyBody(BaseModel):
    job_id: str
    person_id: int
    company: str = Field("", max_length=200)
    title: str = Field("", max_length=200)
    company_url: str = Field("", max_length=300)
    note: str = Field("", max_length=300)


_SIZE_RE = re.compile(r"^[\d,.\s+\-–]+(?:K)?\s*employees?$", re.IGNORECASE)
_COMPANY_URL_RE = re.compile(r"^https://www\.linkedin\.com/company/[^/?#\s]+/?$")
_DOMAIN_RE = re.compile(r"^(?:https?://)?(?:www\.)?([a-z0-9-]+(?:\.[a-z0-9-]+)+)(?:[/?#].*)?$", re.IGNORECASE)


def _clean_website(text: str) -> str:
    """'https://www.Acme.com/careers' -> 'acme.com'. Anything else (LinkedIn's own links, text): ''."""
    m = _DOMAIN_RE.match((text or "").strip())
    if not m:
        return ""
    host = m.group(1).lower()
    return "" if host.endswith(("linkedin.com", "lnkd.in")) else host


# Company Industry / Size (the second phase after a search) is OFF on request (Nov 2026): the
# server hands out no company task.
COMPANY_DETAILS_ON = True   # back ON (Nov 2026): Industry / Size in a parallel second tab


@app.get("/api/details/next")
def details_next(job_id: str = Query(...)):
    """The next company-details task for this run (a person whose Experience page shows where they
    work). Several hidden tabs ask at once: each person is handed to ONE tab (claimed for 3 min)."""
    _job_or_404(job_id)
    if not COMPANY_DETAILS_ON:
        return {"task": None}
    with _CLAIMS_LOCK:
        now = time.time()
        claims = {pid: t for pid, t in _CLAIMS.setdefault(job_id, {}).items() if now - t < 180}
        task = db().next_detail_task(job_id, exclude=set(claims))
        if task:
            claims[task["person_id"]] = now
        _CLAIMS[job_id] = claims
    return {"task": task}


_CLAIMS: dict = {}            # job_id -> {person_id: claimed at}
_ABOUT: dict = {}             # job_id -> company pages a tab is reading now (read once per run)
_CLAIMS_LOCK = threading.Lock()


@app.post("/api/details/page")
def details_page(body: DetailPageBody):
    """Asked before EVERY LinkedIn page loaded for company details: counts it, or refuses past the
    limits (per run and per day, separate from the 30 search pages)."""
    _job_or_404(body.job_id)
    job = _job_or_404(body.job_id)
    per_run = max(MAX_DETAIL_PAGES_PER_RUN, 2 * int(job.get("target") or 0) + 10)
    ok = db().count_detail_page(body.job_id, per_run, MAX_DETAIL_PAGES_PER_DAY)
    return {"ok": ok, "reason": "" if ok else
            f"limit of {per_run} pages per search / {MAX_DETAIL_PAGES_PER_DAY} per day reached"}


@app.post("/api/details/profile")
def details_profile(body: ProfileCompanyBody):
    """The current company as shown on the person's own profile. Empty = the profile showed none."""
    _job_or_404(body.job_id)
    company = " ".join(body.company.split())
    title = " ".join(body.title.split())
    url = body.company_url.strip()
    if url and not _COMPANY_URL_RE.match(url):
        url = ""
    r = db().save_profile_company(body.job_id, body.person_id, company, title, url, " ".join(body.note.split()))
    if not r["ok"]:
        raise HTTPException(404, "Unknown person for this search.")
    if r.get("about"):
        with _CLAIMS_LOCK:                     # two tabs, same company: only one reads its page
            busy = _ABOUT.setdefault(body.job_id, set())
            if r["about"] in busy:
                r["about"] = ""
            else:
                busy.add(r["about"])
    return r


@app.post("/api/companies")
def save_company(body: CompanyBody):
    """One company's details as read from its LinkedIn About page. Anything not in LinkedIn's
    own format is dropped (stays empty), never guessed."""
    _job_or_404(body.job_id)
    if body.status not in ("found", "not_found"):
        raise HTTPException(400, "status must be found or not_found")
    industry = " ".join(body.industry.split())
    size = " ".join(body.size.split())
    url = body.linkedin_url.strip()
    if size and not _SIZE_RE.match(size):
        size = ""
    if url and not _COMPANY_URL_RE.match(url):
        url = ""
    page_name = " ".join(body.page_name.split())
    site = _clean_website(body.website)
    if body.status == "not_found":
        industry = size = url = page_name = site = ""
    db().save_company(body.name, industry, size, url, body.status, by_link=body.by_link and bool(url),
                      note=" ".join(body.note.split()), page_name=page_name, website=site)
    return {"ok": True}


@app.post("/api/stop")
def stop(job_id: str = Query(...)):
    _job_or_404(job_id)
    db().stop_job(job_id)
    _publish_status(job_id)
    return {"ok": True}


@app.get("/api/status")
def status(job_id: str = Query(...)):
    return _public_job(_job_or_404(job_id))


@app.get("/api/jobs")
def jobs():
    return [_public_job(j) for j in db().list_jobs()]


@app.get("/api/events")
async def events():
    q = bus.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=15)
                    yield bus.format(payload)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _filters(status, role, location, date, text, job_id, date_from, date_to) -> dict:
    return {"status": status, "role": role, "location": location, "date": date,
            "text": text, "job_id": job_id, "date_from": date_from, "date_to": date_to}


@app.get("/api/leads")
def leads(status: str | None = None, role: str | None = None, location: str | None = None,
          date: str | None = None, text: str | None = None, job_id: str | None = None,
          date_from: str | None = None, date_to: str | None = None,
          page: int = 1, page_size: int = 50):
    return db().list_leads(_filters(status, role, location, date, text, job_id, date_from, date_to),
                           page, page_size)


@app.get("/api/export.csv")
def export_csv(status: str | None = None, role: str | None = None, location: str | None = None,
               date: str | None = None, text: str | None = None, job_id: str | None = None,
               date_from: str | None = None, date_to: str | None = None):
    people = db().all_leads(_filters(status, role, location, date, text, job_id, date_from, date_to))
    return Response(export.to_csv(people), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="hiring_people.csv"'})


@app.get("/api/export.xlsx")
def export_xlsx(status: str | None = None, role: str | None = None, location: str | None = None,
                date: str | None = None, text: str | None = None, job_id: str | None = None,
                date_from: str | None = None, date_to: str | None = None):
    people = db().all_leads(_filters(status, role, location, date, text, job_id, date_from, date_to))
    return Response(
        export.to_xlsx(people),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="hiring_people.xlsx"'})


def _ext_version() -> str:
    return json.loads((EXT_DIR / "manifest.json").read_text(encoding="utf-8"))["version"]


@app.get("/api/extension/info")
def extension_info():
    return {"version": _ext_version()}


LOCAL_SERVER = "http://localhost:8000"


def _for_deployment(name: str, data: bytes, site: str) -> bytes:
    """The extension as handed out by a deployed server: it trusts and talks to THIS address only."""
    if name == "manifest.json":
        m = json.loads(data.decode("utf-8"))
        m["host_permissions"].append(site + "/*")
        for cs in m["content_scripts"]:
            if "dashboard_bridge.js" in cs["js"]:
                cs["matches"].append(site + "/*")
        return (json.dumps(m, indent=2) + "\n").encode("utf-8")
    if name in ("background.js", "options.js"):
        text = data.decode("utf-8").replace(f"const DEFAULT_SERVER = '{LOCAL_SERVER}';",
                                            f"const DEFAULT_SERVER = '{site}';")
        if name == "options.js":
            text = text.replace("const DEPLOYED_SERVER = '';", f"const DEPLOYED_SERVER = '{site}';")
        return text.encode("utf-8")
    return data


@app.get("/api/extension.zip")
def extension_zip():
    site = public_url()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(EXT_DIR.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                name = path.relative_to(EXT_DIR).as_posix()        # manifest.json at top level
                data = path.read_bytes()
                z.writestr(name, _for_deployment(name, data, site) if site else data)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="hiring-badge-finder-extension.zip"'})
