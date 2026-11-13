"""Storage layer (SQLite now, swappable later).

All database access in the project goes through the `Storage` class, so a
Postgres version can be added later by writing another class with the same
methods and choosing it from the DATABASE_URL environment variable
(see `open_storage`). SQLite (default) and Postgres (app/pgstorage.py) are implemented.

SQLite runs in WAL mode with busy_timeout 30000, one short connection per call,
and every write sits inside BEGIN IMMEDIATE, so parallel requests are safe.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from .detection import BADGE, LIKELY, NOT_HIRING, classify, looks_like_job_title, parse_title_company, strip_degree
from .normalize import normalize_profile_url
from .website import _GENERIC as _GENERIC_SITE_STEMS
from .urlbuilder import location_matches

# India has no daylight saving, so a fixed +05:30 offset is exact.
IST = timezone(timedelta(hours=5, minutes=30), "IST")

MAX_PAGES_PER_RUN = 30
DEFAULT_DAILY_CAP = 300
MAX_PARALLEL_JOBS = 5
# A running search with no news (page, message, company page) for this long was abandoned: the
# browser was closed or the server restarted (e.g. a Render deploy). It must not block new searches.
STALE_JOB_MINUTES = 15
MAX_DETAIL_PAGES_PER_RUN = 60       # at least; a run may use 2 per requested lead + 10 (Experience + About page)
MAX_DETAIL_PAGES_PER_DAY = 600      # 2 per person at the 300-people-a-day limit (separate from search pages)
COMPANY_STATUSES = ("found", "not_found")

WEBSITE_COLUMNS = ("company_website", "website_checked")   # website_checked: when looked up
# company_url: the exact company page from the person's profile; profile_checked: when it was read.
DETAIL_COLUMNS = ("company_url", "profile_checked", "detail_note",
                  "latest_posted")      # newest open role, as LinkedIn shows it ("3 months ago"); '' = unknown

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_url TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL DEFAULT '',
  headline TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  company TEXT NOT NULL DEFAULT '',
  location TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,
  badge_frame INTEGER NOT NULL DEFAULT 0,
  open_roles INTEGER NOT NULL DEFAULT 0,
  evidence TEXT NOT NULL DEFAULT '',
  search_roles TEXT NOT NULL DEFAULT '',
  search_locations TEXT NOT NULL DEFAULT '',
  job_id TEXT NOT NULL DEFAULT '',
  first_found TEXT NOT NULL,
  last_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_people_status ON people(status);
CREATE INDEX IF NOT EXISTS idx_people_job ON people(job_id);
CREATE INDEX IF NOT EXISTS idx_people_found ON people(first_found);

CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY,
  roles TEXT NOT NULL,
  locations TEXT NOT NULL,
  keywords TEXT NOT NULL,
  target INTEGER NOT NULL,
  status TEXT NOT NULL,              -- running | done | stopped | error
  end_reason TEXT NOT NULL DEFAULT '',
  saved INTEGER NOT NULL DEFAULT 0,
  total_read INTEGER NOT NULL DEFAULT 0,
  pages INTEGER NOT NULL DEFAULT 0,
  not_hiring INTEGER NOT NULL DEFAULT 0,
  not_confirmed INTEGER NOT NULL DEFAULT 0,
  other_locations INTEGER NOT NULL DEFAULT 0,
  already_collected INTEGER NOT NULL DEFAULT 0,
  duplicates INTEGER NOT NULL DEFAULT 0,
  invalid INTEGER NOT NULL DEFAULT 0,
  message TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '',
  text_filter_places TEXT NOT NULL DEFAULT '[]',
  geo_ids TEXT NOT NULL DEFAULT '[]',
  started TEXT NOT NULL,
  finished TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS geo_places (       -- LinkedIn location IDs picked once in LinkedIn's own filter
  place_key TEXT PRIMARY KEY,                  -- lower case, single spaces ("mumbai, maharashtra, india")
  place TEXT NOT NULL,
  geo_id TEXT NOT NULL,
  saved TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS search_progress (   -- "continue from last time": next page per identical search
  search_key TEXT PRIMARY KEY,
  next_page INTEGER NOT NULL DEFAULT 1,
  updated TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS job_seen (
  job_id TEXT NOT NULL,
  profile_url TEXT NOT NULL,
  PRIMARY KEY (job_id, profile_url)
);

CREATE TABLE IF NOT EXISTS reads_by_day (
  browser_id TEXT NOT NULL,
  day TEXT NOT NULL,
  count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (browser_id, day)
);

CREATE TABLE IF NOT EXISTS companies (        -- read once from the company's LinkedIn About page
  company_key TEXT PRIMARY KEY,                 -- see company_key(): lower case, no punctuation
  name TEXT NOT NULL,
  industry TEXT NOT NULL DEFAULT '',
  size TEXT NOT NULL DEFAULT '',                -- as LinkedIn shows it, e.g. "1,001-5,000 employees"
  linkedin_url TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,                         -- found | not_found (no exact name match on LinkedIn)
  note TEXT NOT NULL DEFAULT '',                -- why it is empty (for checking), e.g. "2 different companies"
  checked TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS detail_pages_by_day (   -- LinkedIn pages loaded for company details
  day TEXT PRIMARY KEY,
  count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

JOB_COUNTERS = ("saved", "total_read", "pages", "not_hiring", "not_confirmed",
                "other_locations", "already_collected", "duplicates", "invalid", "old_roles")

# "Date posted" filter: the person's newest open role must be at most this many days old.
POSTED_WITHIN_DAYS = {"day": 1, "week": 7, "month": 31}

DEFAULT_SETTINGS = {
    "save_only_confirmed": "true",
    "actively_hiring": "true",
    "daily_cap": str(DEFAULT_DAILY_CAP),
}


def now_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")


def today_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def company_key(name: str) -> str:
    """'Acme Pvt. Ltd' and 'acme pvt ltd' are the same company; nothing else is merged."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w&+ ]", " ", (name or "").lower())).strip()


def _like(s: str) -> str:
    return "%" + re.sub(r"([%_\\])", r"\\\1", s) + "%"


class Storage:
    def __init__(self, path: str):
        self.path = path
        folder = os.path.dirname(os.path.abspath(path))
        os.makedirs(folder, exist_ok=True)
        self.init()

    # -------------------------------------------------------------- plumbing
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    @contextmanager
    def _tx(self):
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    @contextmanager
    def _read(self):
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def init(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(SCHEMA)
            # Email columns added later: add them to older databases, keeping their rows.
            have = {r[1] for r in conn.execute("PRAGMA table_info(people)")}
            for col in WEBSITE_COLUMNS + DETAIL_COLUMNS:
                if col not in have:
                    conn.execute(f"ALTER TABLE people ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
            have = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
            if "search_key" not in have:
                conn.execute("ALTER TABLE jobs ADD COLUMN search_key TEXT NOT NULL DEFAULT ''")
            have_co = {r[1] for r in conn.execute("PRAGMA table_info(companies)")}
            # page_name / website: the company's own name and website, from its LinkedIn About page
            for col in ("note", "page_name", "website"):
                if col not in have_co:
                    conn.execute(f"ALTER TABLE companies ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
            if "posted_within" not in have:
                conn.execute("ALTER TABLE jobs ADD COLUMN posted_within TEXT NOT NULL DEFAULT ''")
            if "old_roles" not in have:
                conn.execute("ALTER TABLE jobs ADD COLUMN old_roles INTEGER NOT NULL DEFAULT 0")
            if "detail_pages" not in have:
                conn.execute("ALTER TABLE jobs ADD COLUMN detail_pages INTEGER NOT NULL DEFAULT 0")
            if "start_page" not in have:
                conn.execute("ALTER TABLE jobs ADD COLUMN start_page INTEGER NOT NULL DEFAULT 1")
            if "updated" not in have:          # last news from the extension ('' = only `started`)
                conn.execute("ALTER TABLE jobs ADD COLUMN updated TEXT NOT NULL DEFAULT ''")
            for k, v in DEFAULT_SETTINGS.items():
                conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (k, v))
        finally:
            conn.close()

    # ------------------------------------------------------- location IDs
    @staticmethod
    def place_key(place: str) -> str:
        return re.sub(r"\s+", " ", (place or "").lower()).strip()

    def geo_places(self) -> dict:
        """{place_key: geo_id} for every place set up so far (shared by everyone using this dashboard)."""
        with self._read() as c:
            return {r["place_key"]: r["geo_id"] for r in c.execute("SELECT place_key, geo_id FROM geo_places")}

    def save_geo_place(self, place: str, geo_id: str) -> None:
        with self._tx() as c:
            c.execute("INSERT INTO geo_places(place_key, place, geo_id, saved) VALUES(?,?,?,?) "
                      "ON CONFLICT(place_key) DO UPDATE SET place=excluded.place, geo_id=excluded.geo_id, saved=excluded.saved",
                      (self.place_key(place), place.strip(), geo_id, now_ist()))

    def forget_geo_place(self, place: str) -> bool:
        with self._tx() as c:
            return c.execute("DELETE FROM geo_places WHERE place_key=?", (self.place_key(place),)).rowcount > 0

    # -------------------------------------------------------------- settings
    def get_settings(self) -> dict:
        with self._read() as c:
            rows = c.execute("SELECT key, value FROM settings").fetchall()
        raw = {r["key"]: r["value"] for r in rows}
        return {
            "save_only_confirmed": raw.get("save_only_confirmed", "true") == "true",
            "daily_cap": int(raw.get("daily_cap", DEFAULT_DAILY_CAP)),
            "actively_hiring": raw.get("actively_hiring", "true") == "true",
            # Open-role dates (background request per #HIRING person): OFF unless switched on. On real
            # LinkedIn the open-roles data is not in that page (0 of 9 found, Nov 2026), so it only cost time.
        }

    def set_settings(self, save_only_confirmed: bool | None = None,
                     daily_cap: int | None = None,
                     actively_hiring: bool | None = None) -> dict:
        with self._tx() as c:
            if save_only_confirmed is not None:
                c.execute("REPLACE INTO settings(key, value) VALUES('save_only_confirmed', ?)",
                          ("true" if save_only_confirmed else "false",))
            if daily_cap is not None:
                cap = max(1, min(int(daily_cap), 5000))
                c.execute("REPLACE INTO settings(key, value) VALUES('daily_cap', ?)", (str(cap),))
            if actively_hiring is not None:
                c.execute("REPLACE INTO settings(key, value) VALUES('actively_hiring', ?)",
                          ("true" if actively_hiring else "false",))
        return self.get_settings()

    # ------------------------------------------------------------------ jobs
    def running_jobs(self) -> int:
        with self._read() as c:
            return c.execute("SELECT COUNT(*) FROM jobs WHERE status='running'").fetchone()[0]

    def create_job(self, roles: list[str], locations: list[str], keywords: list[str],
                   target: int, text_filter_places: list[str] | None = None,
                   search_key: str = "", start_page: int = 1, posted_within: str = "") -> str:
        """Create a job. Raises RuntimeError when 5 searches already run."""
        job_id = uuid.uuid4().hex[:12]
        with self._tx() as c:
            self._close_stale(c)
            n = c.execute("SELECT COUNT(*) FROM jobs WHERE status='running'").fetchone()[0]
            if n >= MAX_PARALLEL_JOBS:
                raise RuntimeError(f"Already {MAX_PARALLEL_JOBS} searches running. Stop one or wait.")
            c.execute(
                "INSERT INTO jobs(job_id, roles, locations, keywords, target, status,"
                " text_filter_places, started, search_key, start_page, posted_within)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, json.dumps(roles), json.dumps(locations), json.dumps(keywords),
                 int(target), "running", json.dumps(text_filter_places or []), now_ist(),
                 search_key, max(1, int(start_page)),
                 posted_within if posted_within in POSTED_WITHIN_DAYS else ""),
            )
        return job_id

    def get_job(self, job_id: str) -> dict | None:
        with self._read() as c:
            r = c.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return self._job_dict(r) if r else None

    @staticmethod
    def _close_stale(c) -> None:
        """End running searches that have sent nothing for STALE_JOB_MINUTES (inside a write)."""
        cutoff = (datetime.now(IST) - timedelta(minutes=STALE_JOB_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
        c.execute("UPDATE jobs SET status='error', end_reason='no news', finished=?, error=?"
                  " WHERE status='running' AND (CASE WHEN updated<>'' THEN updated ELSE started END) < ?",
                  (now_ist(), f"No news from the browser for {STALE_JOB_MINUTES} minutes (browser closed, or the"
                   " server restarted). Launch the search again: it continues where it stopped.", cutoff))

    def close_stale_jobs(self) -> None:
        with self._tx() as c:
            self._close_stale(c)

    def list_jobs(self, limit: int = 20) -> list[dict]:
        self.close_stale_jobs()
        with self._read() as c:
            rows = c.execute("SELECT * FROM jobs ORDER BY started DESC, rowid DESC LIMIT ?",
                             (limit,)).fetchall()
        return [self._job_dict(r) for r in rows]

    @staticmethod
    def _job_dict(r: sqlite3.Row) -> dict:
        d = dict(r)
        for k in ("roles", "locations", "keywords", "text_filter_places", "geo_ids"):
            d[k] = json.loads(d[k] or "[]")
        return d

    def set_text_filter(self, job_id: str, places: list[str], geo_ids: list[str]) -> None:
        with self._tx() as c:
            c.execute("UPDATE jobs SET text_filter_places=?, geo_ids=? WHERE job_id=?",
                      (json.dumps(places), json.dumps(geo_ids), job_id))

    def set_message(self, job_id: str, message: str) -> bool:
        with self._tx() as c:
            cur = c.execute("UPDATE jobs SET message=?, updated=? WHERE job_id=?", (message[:500], now_ist(), job_id))
            return cur.rowcount > 0

    def set_error(self, job_id: str, message: str) -> bool:
        with self._tx() as c:
            cur = c.execute(
                "UPDATE jobs SET error=?, status=CASE WHEN status='running' THEN 'error' ELSE status END,"
                " end_reason=CASE WHEN status='running' THEN 'error' ELSE end_reason END,"
                " finished=CASE WHEN status='running' THEN ? ELSE finished END WHERE job_id=?",
                (message[:500], now_ist(), job_id))
            return cur.rowcount > 0

    def finish_job(self, job_id: str, reason: str) -> dict | None:
        with self._tx() as c:
            cur = c.execute("UPDATE jobs SET status='done', end_reason=?, finished=? "
                            "WHERE job_id=? AND status='running'", (reason, now_ist(), job_id))
            if cur.rowcount and reason == "no more pages":
                # End of LinkedIn's results: the next identical search starts again at page 1.
                c.execute("UPDATE search_progress SET next_page=1, updated=? WHERE search_key="
                          "(SELECT search_key FROM jobs WHERE job_id=?)", (now_ist(), job_id))
        return self.get_job(job_id)

    # ---------------------------------------------------- continue searches
    def next_page(self, search_key: str) -> int:
        """Page an identical search should start at (1 when never run or at the end)."""
        with self._read() as c:
            r = c.execute("SELECT next_page FROM search_progress WHERE search_key=?", (search_key,)).fetchone()
        return int(r["next_page"]) if r else 1

    def rewind_job(self, job_id: str) -> bool:
        """The continued page was empty; the extension reloads page 1 for this same job."""
        with self._tx() as c:
            cur = c.execute("UPDATE jobs SET start_page=1 WHERE job_id=? AND status='running' AND pages=0",
                            (job_id,))
            if cur.rowcount:
                c.execute("UPDATE search_progress SET next_page=1, updated=? WHERE search_key="
                          "(SELECT search_key FROM jobs WHERE job_id=?)", (now_ist(), job_id))
            return cur.rowcount > 0

    def stop_job(self, job_id: str) -> dict | None:
        with self._tx() as c:
            c.execute("UPDATE jobs SET status='stopped', end_reason='stopped by user', finished=? "
                      "WHERE job_id=? AND status='running'", (now_ist(), job_id))
        return self.get_job(job_id)

    # --------------------------------------------------------- daily reads
    def reads_today(self, browser_id: str = "default") -> int:
        with self._read() as c:
            r = c.execute("SELECT count FROM reads_by_day WHERE browser_id=? AND day=?",
                          (browser_id, today_ist())).fetchone()
        return int(r["count"]) if r else 0

    def reads_today_most(self) -> int:
        """Today's reads of the busiest browser (the daily limit is per browser; the extension
        counts under its own browser id, never "default")."""
        with self._read() as c:
            r = c.execute("SELECT MAX(count) AS n FROM reads_by_day WHERE day=?", (today_ist(),)).fetchone()
        return int(r["n"] or 0) if r else 0

    # ---------------------------------------------------------------- ingest
    def ingest(self, job_id: str, people: list[dict], browser_id: str = "default") -> dict:
        """Process one page of cards. Atomic: all counters and rows commit together."""
        stamp = now_ist()
        day = today_ist()
        new_people: list[dict] = []
        with self._tx() as c:
            job = c.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if job is None:
                raise KeyError(job_id)
            settings = {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM settings")}
            only_confirmed = settings.get("save_only_confirmed", "true") == "true"
            cap = int(settings.get("daily_cap", DEFAULT_DAILY_CAP))
            places = json.loads(job["text_filter_places"] or "[]")
            roles = json.loads(job["roles"])
            locs = json.loads(job["locations"])
            target = int(job["target"])

            counters = {k: int(job[k]) for k in JOB_COUNTERS}
            reason = ""

            if job["status"] != "running":
                return self._result(job, counters, False, job["end_reason"] or job["status"], [])

            row = c.execute("SELECT count FROM reads_by_day WHERE browser_id=? AND day=?",
                            (browser_id, day)).fetchone()
            read_today = int(row["count"]) if row else 0
            room = max(0, cap - read_today)
            batch = list(people or [])
            if len(batch) > room:
                batch = batch[:room]
                reason = "daily limit reached"

            counters["pages"] += 1
            counters["total_read"] += len(batch)
            c.execute("INSERT INTO reads_by_day(browser_id, day, count) VALUES(?,?,?) "
                      "ON CONFLICT(browser_id, day) DO UPDATE SET count=count+excluded.count",
                      (browser_id, day, len(batch)))
            read_today += len(batch)

            for p in batch:
                url = normalize_profile_url(p.get("profile_url") or p.get("url") or "")
                if not url:
                    counters["invalid"] += 1
                    continue
                seen = c.execute("INSERT OR IGNORE INTO job_seen(job_id, profile_url) VALUES(?,?)",
                                 (job_id, url))
                if seen.rowcount == 0:
                    counters["duplicates"] += 1
                    continue

                headline = (p.get("headline") or "").strip()
                status, evidence = classify(bool(p.get("badge_frame")), bool(p.get("open_roles")),
                                            headline, p.get("card_text") or "")
                if status == NOT_HIRING:
                    counters["not_hiring"] += 1
                    continue
                if only_confirmed and status != BADGE:
                    counters["not_confirmed"] += 1
                    continue
                location = (p.get("location") or "").strip()
                if places and not location_matches(location, places):
                    counters["other_locations"] += 1
                    continue
                # "Date posted": skip only when the newest open role is KNOWN to be older than asked.
                # Unknown date: kept (never dropped on a guess), with an empty Latest Role Posted.
                posted = (p.get("posted_ago") or "").strip()[:40]
                days = p.get("posted_days")
                limit = POSTED_WITHIN_DAYS.get(job["posted_within"] or "")
                if limit is not None and isinstance(days, int) and days > limit:
                    counters["old_roles"] += 1
                    continue

                existing = c.execute("SELECT id, status FROM people WHERE profile_url=?",
                                     (url,)).fetchone()
                if existing:
                    counters["already_collected"] += 1
                    if existing["status"] != BADGE and status == BADGE:
                        c.execute("UPDATE people SET last_seen=?, status=?, badge_frame=1,"
                                  " evidence=? WHERE id=?", (stamp, status, evidence, existing["id"]))
                    else:
                        c.execute("UPDATE people SET last_seen=? WHERE id=?", (stamp, existing["id"]))
                    if posted:
                        c.execute("UPDATE people SET latest_posted=? WHERE id=?", (posted, existing["id"]))
                    continue
                if counters["saved"] >= target:
                    break

                title, company = parse_title_company(headline)
                if not company:
                    # LinkedIn's own "Current: Title at Company" line on the card, when the headline has none.
                    cur_title, company = parse_title_company(p.get("current") or "")
                    title = title or cur_title
                cur = c.execute(
                    "INSERT INTO people(profile_url, name, headline, title, company, location, status,"
                    " badge_frame, open_roles, evidence, search_roles, search_locations, job_id,"
                    " first_found, last_seen, latest_posted)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (url, strip_degree(p.get("name") or ""), headline, title, company, location, status,
                     1 if p.get("badge_frame") else 0, 1 if p.get("open_roles") else 0, evidence,
                     ", ".join(roles), "; ".join(locs), job_id, stamp, stamp, posted))
                counters["saved"] += 1
                new_people.append(self._person_row(c, cur.lastrowid))

            cont = True
            if counters["saved"] >= target:
                cont, reason = False, "target reached"
            elif counters["pages"] >= MAX_PAGES_PER_RUN:
                cont, reason = False, "page limit reached"
            elif read_today >= cap:
                cont, reason = False, "daily limit reached"
            elif reason:
                cont = False

            if job["search_key"]:
                # Next identical search continues after this page; if it stopped part-way through
                # this page (target or daily limit), it re-reads this page so nobody is skipped.
                last = int(job["start_page"]) + counters["pages"] - 1
                nxt = last if reason in ("target reached", "daily limit reached") else last + 1
                c.execute("INSERT INTO search_progress(search_key, next_page, updated) VALUES(?,?,?) "
                          "ON CONFLICT(search_key) DO UPDATE SET next_page=excluded.next_page, updated=excluded.updated",
                          (job["search_key"], max(1, nxt), stamp))

            sets = ", ".join(f"{k}=?" for k in JOB_COUNTERS)
            args = [counters[k] for k in JOB_COUNTERS]
            if cont:
                c.execute(f"UPDATE jobs SET {sets}, updated=? WHERE job_id=?", (*args, stamp, job_id))
            else:
                c.execute(f"UPDATE jobs SET {sets}, status='done', end_reason=?, finished=? "
                          "WHERE job_id=?", (*args, reason, stamp, job_id))
            job = c.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            return self._result(job, counters, cont, reason, new_people)

    @staticmethod
    def _result(job, counters, cont, reason, new_people) -> dict:
        return {"continue": cont, "saved": counters["saved"], "total": counters["total_read"],
                "target": int(job["target"]), "reason": reason, "counters": counters,
                "new_people": new_people}

    @staticmethod
    def _person_row(c, row_id) -> dict:
        return dict(c.execute("SELECT * FROM people WHERE id=?", (row_id,)).fetchone())

    # ---------------------------------------------------------------- website
    def people_without_website_check(self, limit: int = 20) -> list[dict]:
        """Saved people the website worker has not looked at yet."""
        with self._read() as c:
            return [dict(r) for r in c.execute(
                "SELECT id, company FROM people WHERE website_checked='' ORDER BY id LIMIT ?", (limit,))]

    def set_website(self, person_id: int, site: str) -> None:
        with self._tx() as c:
            c.execute("UPDATE people SET company_website=?, website_checked=? WHERE id=? AND website_checked=''",
                      (site, now_ist(), person_id))

    # ----------------------------------------------------------------- leads
    @staticmethod
    def _where(f: dict) -> tuple[str, list]:
        cond, args = [], []
        if f.get("status"):
            cond.append("status=?")
            args.append(f["status"])
        if f.get("job_id"):
            cond.append("job_id=?")
            args.append(f["job_id"])
        if f.get("role"):
            # several roles separated by "|" (Leads tab multi-select): match any of them
            roles = [r.strip() for r in f["role"].split("|") if r.strip()]
            if roles:
                cond.append("(" + " OR ".join(["search_roles LIKE ? ESCAPE '\\'"] * len(roles)) + ")")
                args += [_like(r) for r in roles]
        if f.get("location"):
            # several places separated by "|" (Leads tab multi-select): match any of them
            places = [x.strip() for x in f["location"].split("|") if x.strip()]
            if places:
                one = "(location LIKE ? ESCAPE '\\' OR search_locations LIKE ? ESCAPE '\\')"
                cond.append("(" + " OR ".join([one] * len(places)) + ")")
                for x in places:
                    args += [_like(x)] * 2
        if f.get("date_from"):
            cond.append("substr(first_found,1,10) >= ?")
            args.append(f["date_from"])
        if f.get("date_to"):
            cond.append("substr(first_found,1,10) <= ?")
            args.append(f["date_to"])
        if f.get("date"):
            cond.append("substr(first_found,1,10) = ?")
            args.append(f["date"])
        if f.get("text"):
            like = _like(f["text"])
            cond.append("(name LIKE ? ESCAPE '\\' OR headline LIKE ? ESCAPE '\\'"
                        " OR company LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\')")
            args += [like] * 4
        return (" WHERE " + " AND ".join(cond)) if cond else "", args

    # ------------------------------------------------------------- companies
    # Company Industry / Company Size are read from the company's own LinkedIn About page, after a
    # search. Where the person works comes from (1) the headline or the card's "Current:" line, looked
    # up by exact name, or (2) when that is missing or matches no single company, the person's own
    # profile, whose current-job link names the exact company page. The server hands out one task at
    # a time and counts every LinkedIn page the extension loads for it (per run and per day).
    def next_detail_task(self, job_id: str, exclude: set | None = None) -> dict | None:
        """The next person whose current company must be read from their own Experience page (its
        company LINK is exact; a name search picked wrong companies, e.g. a 2-10 person "Hexaware").
        A person whose card names a company already read EXACTLY (by link, with its own name) gets
        that company at once, with no LinkedIn page. `exclude`: people another tab is reading now."""
        exclude = exclude or set()
        with self._tx() as c:
            exact = {}
            for r in c.execute("SELECT name, page_name, linkedin_url FROM companies WHERE status='found'"
                               " AND page_name<>'' AND linkedin_url<>'' AND company_key LIKE 'li:%'"):
                for n in (r["page_name"], r["name"]):
                    if company_key(n):
                        exact.setdefault(company_key(n), r["linkedin_url"])
            for r in c.execute("SELECT id, profile_url, company FROM people WHERE job_id=? AND profile_checked=''"
                               " AND company_url='' ORDER BY id", (job_id,)).fetchall():
                url = exact.get(company_key(r["company"] or ""))
                if url:
                    c.execute("UPDATE people SET company_url=?, profile_checked=?, detail_note=? WHERE id=?",
                              (url, now_ist(), "company known from an earlier exact read", r["id"]))
                    continue
                if r["id"] not in exclude:
                    return {"kind": "profile", "person_id": r["id"], "url": r["profile_url"]}
        return None

    def detail_pages_today(self) -> int:
        with self._read() as c:
            r = c.execute("SELECT count FROM detail_pages_by_day WHERE day=?", (today_ist(),)).fetchone()
            return r[0] if r else 0

    def count_detail_page(self, job_id: str, per_run: int, per_day: int) -> bool:
        """One more LinkedIn page for company details: allowed only inside both limits."""
        with self._tx() as c:
            run = c.execute("SELECT detail_pages FROM jobs WHERE job_id=? AND status NOT IN ('stopped','error')",
                            (job_id,)).fetchone()
            day = c.execute("SELECT count FROM detail_pages_by_day WHERE day=?", (today_ist(),)).fetchone()
            if not run or run[0] >= per_run or (day[0] if day else 0) >= per_day:
                return False
            c.execute("UPDATE jobs SET detail_pages=detail_pages+1, updated=? WHERE job_id=?", (now_ist(), job_id))
            c.execute("INSERT INTO detail_pages_by_day(day, count) VALUES(?, 1)"
                      " ON CONFLICT(day) DO UPDATE SET count=count+1", (today_ist(),))
            return True

    def save_profile_company(self, job_id: str, person_id: int, company: str, title: str,
                             company_url: str, note: str = "") -> dict:
        """What the person's profile shows as their current job. It replaces the company only when the
        card gave none or a name that matched no single company. Returns the next step."""
        with self._tx() as c:
            p = c.execute("SELECT * FROM people WHERE id=? AND job_id=?", (person_id, job_id)).fetchone()
            if not p:
                return {"ok": False, "about": "", "search": ""}
            c.execute("UPDATE people SET profile_checked=?, detail_note=? WHERE id=?", (now_ist(), note[:200], person_id))
            if title and looks_like_job_title(title):
                c.execute("UPDATE people SET title=? WHERE id=? AND title=''", (title, person_id))
            if company and (looks_like_job_title(company) or company.lower() == (title or "").lower()):
                # A job title read as the company: keep the card's name; the exact company LINK still
                # gives Industry / Size, and its About page gives the real name.
                note = (note + " (company text looked like a job title: '" + company[:60] + "')").strip()
                c.execute("UPDATE people SET detail_note=? WHERE id=?", (note[:200], person_id))
                if company_url:
                    c.execute("UPDATE people SET company_url=? WHERE id=?", (company_url, person_id))
                    have = c.execute("SELECT 1 FROM companies WHERE linkedin_url=? AND status='found' AND page_name<>''",
                                     (company_url,)).fetchone()
                    return {"ok": True, "about": "" if have else company_url, "search": ""}
                return {"ok": True, "about": "", "search": ""}
            if not company:
                if company_url:
                    c.execute("UPDATE people SET company_url=? WHERE id=?", (company_url, person_id))
                    have = c.execute("SELECT 1 FROM companies WHERE linkedin_url=? AND status='found' AND page_name<>''",
                                     (company_url,)).fetchone()
                    return {"ok": True, "about": "" if have else company_url, "search": ""}
                return {"ok": True, "about": "", "search": ""}
            c.execute("UPDATE people SET company=?, company_url=?, title=CASE WHEN title='' THEN ? ELSE title END"
                      " WHERE id=?", (company, company_url, title, person_id))
            if company_url:
                have = c.execute("SELECT 1 FROM companies WHERE linkedin_url=? AND status='found' AND page_name<>''",
                                 (company_url,)).fetchone()
                return {"ok": True, "about": "" if have else company_url, "search": ""}
            have = c.execute("SELECT 1 FROM companies WHERE company_key=?", (company_key(company),)).fetchone()
            return {"ok": True, "about": "", "search": "" if have else company}

    def save_company(self, name: str, industry: str, size: str, linkedin_url: str, status: str,
                     by_link: bool = False, note: str = "", page_name: str = "", website: str = "") -> None:
        """by_link: the company page came from a profile's link (exact), so it is kept under its
        address; a name-search result is kept under the name."""
        if status not in COMPANY_STATUSES or not company_key(name):
            raise ValueError("bad company")
        key = ("li:" + linkedin_url.lower()) if by_link and linkedin_url else company_key(name)
        with self._tx() as c:
            c.execute("INSERT INTO companies(company_key, name, industry, size, linkedin_url, status, checked, note,"
                      " page_name, website) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(company_key) DO UPDATE SET"
                      " name=excluded.name, industry=excluded.industry, size=excluded.size,"
                      " linkedin_url=excluded.linkedin_url, status=excluded.status, checked=excluded.checked,"
                      " note=excluded.note, page_name=excluded.page_name, website=excluded.website",
                      (key, name.strip(), industry, size, linkedin_url, status, now_ist(), note[:200],
                       page_name.strip()[:200], website.strip()[:200]))

    def all_companies(self) -> list[dict]:
        with self._read() as c:
            return [dict(r) for r in c.execute("SELECT * FROM companies ORDER BY checked")]

    def _with_company(self, c, rows) -> list[dict]:
        by_name, by_url = {}, {}
        for r in c.execute("SELECT company_key, industry, size, linkedin_url, status, page_name, website FROM companies"):
            by_name[r["company_key"]] = r
            if r["status"] == "found" and r["linkedin_url"]:
                by_url[r["linkedin_url"]] = r
        out = []
        for r in rows:
            d = dict(r)
            if d.get("company_url"):               # exact company from the profile: never matched by name
                ci = by_url.get(d["company_url"])
            else:
                ci = by_name.get(company_key(d.get("company") or ""))
            d["company_industry"] = ci["industry"] if ci else ""
            d["company_size"] = ci["size"] if ci else ""
            if ci and ci["page_name"] and (d.get("company_url") or not d.get("company")
                                           or looks_like_job_title(d.get("company") or "")):
                d["company"] = ci["page_name"]        # the company's own name, from its LinkedIn page
            elif looks_like_job_title(d.get("company") or ""):
                # A job title is never shown as the company: the headline's own "X at Company" if it
                # names one, else empty. A website guessed from a generic title word is dropped too.
                from_headline = parse_title_company(d.get("headline") or "")[1]
                d["company"] = "" if looks_like_job_title(from_headline) else from_headline
                stem = (d.get("company_website") or "").split(".")[0]
                if stem in _GENERIC_SITE_STEMS:
                    d["company_website"] = ""
            if ci and ci["website"]:
                d["company_website"] = ci["website"]  # the company's own website, from its LinkedIn page
            out.append(d)
        return out

    def list_leads(self, filters: dict | None = None, page: int = 1, page_size: int = 50) -> dict:
        filters = filters or {}
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 500))
        where, args = self._where(filters)
        with self._read() as c:
            total = c.execute(f"SELECT COUNT(*) FROM people{where}", args).fetchone()[0]
            rows = c.execute(
                f"SELECT * FROM people{where} ORDER BY first_found DESC, id DESC LIMIT ? OFFSET ?",
                (*args, page_size, (page - 1) * page_size)).fetchall()
            items = self._with_company(c, rows)
        return {"total": total, "page": page, "page_size": page_size, "items": items}

    def all_leads(self, filters: dict | None = None) -> list[dict]:
        where, args = self._where(filters or {})
        with self._read() as c:
            rows = c.execute(f"SELECT * FROM people{where} ORDER BY first_found DESC, id DESC",
                             args).fetchall()
            return self._with_company(c, rows)

    def count_people(self) -> int:
        with self._read() as c:
            return c.execute("SELECT COUNT(*) FROM people").fetchone()[0]


def open_storage(database_url: str | None = None) -> Storage:
    """Choose the storage from DATABASE_URL (default: SQLite file in ./data)."""
    url = database_url if database_url is not None else os.environ.get("DATABASE_URL", "")
    if not url:
        return Storage(os.path.join("data", "hiring_badge_finder.db"))
    if url.startswith("sqlite:///"):
        return Storage(url[len("sqlite:///"):])
    if url.startswith(("postgres://", "postgresql://")):
        from .pgstorage import PostgresStorage      # e.g. a free Neon database (see DEPLOY.md)
        return PostgresStorage(url)
    raise ValueError(f"Unsupported DATABASE_URL: {url.split(':')[0]}")
