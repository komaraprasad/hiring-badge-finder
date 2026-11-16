"""CSV / Excel export.

Columns come from what the tool read on LinkedIn's search results. Company Website
comes from app/website.py (a lookup by company name: a guess, not checked); Company Industry /
Company Size from the company's own LinkedIn About page.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.utils import get_column_letter

from .detection import BADGE, hiring_text_match
from .urlbuilder import location_matches

SOURCE_PLATFORM = "LinkedIn – People search"

# Hiring Signal Type, Hiring Evidence, Qualification Reason, Email Status and Email Source were
# removed from the files on request (Nov 2026); their helpers below are kept for the dashboard/tests.
# "Hiring Role(s)" and "Headline Has Searched Title" removed on request (Nov 2026).
# Business Email removed on request (Nov 2026). Order: the person, what they hire for, then all the
# company details together, then where/when it was collected. "Hiring For" / "Hiring Company" /
# "Posted" come from the card's "See open roles" pop-up; Industry / Size from the company's About page.
COLUMNS = ["Person Name", "Current Designation", "Headline", "Location", "Location Matches Search",
           "Person LinkedIn URL", "Hiring Status",
           "Company Name", "Company Industry", "Company Size", "Company Website",
           "Source Platform", "Date Collected (IST)", "Last Verified Date (IST)",
           "Hiring For (Open Roles)", "Open Role Matches"]   # added last (Nov 2026): from the profile data

# Fixed, readable widths (characters). Long text is cut off at the cell edge (never spills into the
# next cell); the full text is still in the cell.
WIDTHS = {"Person Name": 24, "Current Designation": 30, "Headline": 50, "Location": 26,
          "Location Matches Search": 14, "Person LinkedIn URL": 42, "Hiring Status": 16,
          "Hiring For": 34, "Posted": 14, "Company Name": 26, "Hiring Company": 26,
          "Company Industry": 26, "Company Size": 18, "Company Website": 24, "Source Platform": 24,
          "Date Collected (IST)": 18, "Last Verified Date (IST)": 18,
          "Hiring For (Open Roles)": 44, "Open Role Matches": 16}


# Spreadsheet programs run text that starts with these characters as formulas.
_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")

# "Hiring SDETs for my team" -> "SDETs"; "We're hiring backend engineers | ..." -> "backend engineers".
# '#hiring' is skipped: what follows a hashtag is usually the person's own title.
_ROLE_RE = re.compile(r"(?<!#)\bhiring\s*(?:for\s+|:\s*|[-–—]\s*)?(?P<roles>[^|•·.;!()#]+)", re.IGNORECASE)
_ROLE_END_RE = re.compile(r"\s+(?:for\s+(?:my|our|the)\b|at\b|@|in\b|across\b|with\b|to\s+join\b).*$", re.IGNORECASE)
_NOT_A_ROLE = {"", "now", "manager", "managers", "team", "teams", "people", "talent", "partner", "lead", "specialist"}


def hiring_roles(headline: str) -> str:
    """Roles named in the headline's hiring wording, or '' (never guessed)."""
    headline = headline or ""
    if not hiring_text_match(headline):          # also rules out "Hiring Manager at X", "hiring process"
        return ""
    m = _ROLE_RE.search(headline)
    if not m:
        return ""
    roles = _ROLE_END_RE.sub("", m.group("roles")).strip(" -–—:&,")
    if roles.lower() in _NOT_A_ROLE or not re.search(r"[A-Za-z]{3}", roles):   # e.g. "1" from "Hiring: 1) ..."
        return ""
    return roles


def signal_type(p: dict) -> str:
    frame, roles = bool(p.get("badge_frame")), bool(p.get("open_roles"))
    if frame and roles:
        return "#HIRING frame + open roles"
    if frame:
        return "#HIRING frame"
    if roles:
        return "Open roles link (no frame)"
    return "Hiring wording only" if p.get("evidence") else ""


def qualification_reason(p: dict) -> str:
    parts = []
    if p.get("badge_frame"):
        parts.append("shows the purple #HIRING photo frame")
    if p.get("open_roles"):
        parts.append("has open roles linked to the profile")
    if hiring_text_match(p.get("headline") or ""):
        parts.append("headline says they are hiring")
    if not parts:
        return ""
    lead = "Badge confirmed" if p.get("status") == BADGE else (p.get("status") or "Hiring signal")
    return f"{lead}: " + ", ".join(parts) + "."


def title_in_headline(p: dict) -> str:
    """Yes/No: does the headline contain a searched title? LinkedIn matches the whole profile, so
    "No" means check this person, not that they are wrong. Nobody is dropped."""
    titles = [t.strip() for t in (p.get("search_roles") or "").split(",") if t.strip()]
    if not titles:
        return ""
    head = p.get("headline") or ""
    found = any(re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", head, re.IGNORECASE) for t in titles)
    return "Yes" if found else "No"


def location_in_search(p: dict) -> str:
    """Yes/No: does the shown location name a searched place? Empty when there is nothing to compare."""
    places = [x.strip() for x in (p.get("search_locations") or "").split(";") if x.strip()]
    if not places or not (p.get("location") or "").strip():
        return ""
    return "Yes" if location_matches(p.get("location"), places) else "No"


def _values(p: dict) -> list:
    return [p.get("name"), p.get("title"), p.get("headline"), p.get("location"), location_in_search(p),
            p.get("profile_url"), p.get("status"),
            p.get("company"), p.get("company_industry"), p.get("company_size"),
            p.get("company_website"), SOURCE_PLATFORM, p.get("first_found"), p.get("last_seen"),
            p.get("hiring_for"), p.get("open_role_match")]


def _safe(value) -> str:
    s = "" if value is None else str(value)
    return "'" + s if s.startswith(_DANGEROUS) else s


def rows(people: list[dict]) -> list[list[str]]:
    return [[_safe(v) for v in _values(p)] for p in people]


def to_csv(people: list[dict]) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.writer(buf)
    w.writerow(COLUMNS)
    w.writerows(rows(people))
    return ("﻿" + buf.getvalue()).encode("utf-8")  # BOM so Excel reads UTF-8 names


def to_xlsx(people: list[dict]) -> bytes:
    """A real Excel table: filter buttons, banded rows, frozen header, sized columns,
    clickable LinkedIn links and real date cells."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Hiring people"
    ws.append(COLUMNS)
    url_col = COLUMNS.index("Person LinkedIn URL") + 1
    date_cols = {i + 1 for i, c in enumerate(COLUMNS) if c.startswith(("Date Collected", "Last Verified"))}
    for r in rows(people):
        ws.append(r)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            if cell.column in date_cols and cell.value:
                try:
                    cell.value = datetime.strptime(str(cell.value), "%Y-%m-%d %H:%M:%S")
                    cell.number_format = "yyyy-mm-dd hh:mm"
                except ValueError:
                    pass                                    # keep the text as it was
            elif cell.column == url_col and str(cell.value or "").startswith("https://"):
                cell.hyperlink = cell.value
                cell.style = "Hyperlink"
            # Wrap ON inside a fixed-height row: long text stays INSIDE its own cell (only its first line
            # shows) and never spills over the next columns. (An empty string does not stop spill-over:
            # openpyxl does not save it, so the neighbour cell stays truly empty.)
            cell.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True, indent=0)
        ws.row_dimensions[row[0].row].height = 18

    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 34
    for i, name in enumerate(COLUMNS, 1):
        ws.column_dimensions[get_column_letter(i)].width = WIDTHS.get(name, 18)
    if ws.max_row > 1:
        ref = f"A1:{get_column_letter(len(COLUMNS))}{ws.max_row}"
        table = Table(displayName="HiringPeople", ref=ref)
        table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
        ws.add_table(table)
    else:
        for cell in ws[1]:
            cell.font = Font(bold=True)
    ws.freeze_panes = "B2"                                  # header row and the name column stay visible
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
