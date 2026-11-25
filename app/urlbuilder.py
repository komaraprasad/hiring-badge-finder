"""Search URL builder and location helpers (Step 3 of the spec)."""
from __future__ import annotations

import json
import re
from urllib.parse import quote

BASE = "https://www.linkedin.com/search/results/people/"
ORIGIN = "GLOBAL_SEARCH_HEADER"
# LinkedIn's own "Actively hiring" chip (seen labelled Premium; unverified on free accounts)
ACTIVELY_HIRING = ("activelyHiringForJobTitles", ["-100"])

MAX_ROLES = 5
MAX_LOCATIONS = 10
MAX_KEYWORDS = 10


def clean_list(items, limit: int) -> list[str]:
    """Trim, drop empties, drop case-insensitive duplicates, cap the length."""
    out: list[str] = []
    seen: set[str] = set()
    for it in items or []:
        s = re.sub(r"\s+", " ", str(it or "")).strip()
        if not s or s.lower() in seen:
            continue
        seen.add(s.lower())
        out.append(s)
        if len(out) >= limit:
            break
    return out


def _term(s: str) -> str:
    """Quote multi-word terms so LinkedIn treats them as phrases."""
    s = s.replace('"', "")
    return f'"{s}"' if " " in s else s


def _group(items: list[str]) -> str:
    terms = [_term(i) for i in items]
    return terms[0] if len(terms) == 1 else "(" + " OR ".join(terms) + ")"


def build_keywords(roles: list[str], keywords: list[str],
                   place_names: list[str] | None = None, require_hiring: bool = True) -> str:
    """(role1 OR role2) AND hiring [AND (city1 OR city2)] [extra keywords].

    require_hiring=False leaves out "AND hiring". Used when LinkedIn's Actively hiring filter is on:
    the filter already keeps people with open jobs, and the word only drops people who never write
    "hiring" in their profile (engineering managers mostly don't), so such searches ended after 1 page.

    Only the first part of a place goes in ("Mumbai, Maharashtra, India" -> Mumbai): the full
    quoted phrase almost never appears in profile text, so LinkedIn found no one. The server
    still keeps only people whose shown location matches (see location_matches).
    """
    parts = []
    if roles:
        parts.append(_group(roles))
    if require_hiring or not parts:
        parts.append("hiring")
    cities = clean_list([(p or "").split(",")[0] for p in place_names or []], MAX_LOCATIONS)
    if cities:
        parts.append(_group(cities))
    skills = [k for k in keywords or [] if (k or "").strip()]
    if skills:                       # "Hiring for" skills: ANY of them (OR), on request
        parts.append(_group(skills))
    return " AND ".join(parts)


def build_url(roles: list[str], keywords: list[str],
              geo_ids: list[str] | None = None,
              place_names: list[str] | None = None,
              actively_hiring: bool = False, page: int = 1) -> str:
    """Build the regular LinkedIn people-search URL.

    geo_ids         -> adds &geoUrn=["id",...]   (place names are NOT put in keywords)
    place_names     -> used in keywords when no geo IDs are available
    actively_hiring -> adds &activelyHiringForJobTitles=["-100"]
    page            -> adds &page=N when N > 1 (continue a search where the last one stopped)
    """
    kw = build_keywords(roles, keywords, None if geo_ids else place_names, require_hiring=not actively_hiring)
    url = f"{BASE}?keywords={quote(kw, safe='')}&origin={ORIGIN}"
    if geo_ids:
        geo = json.dumps([str(g) for g in geo_ids], separators=(",", ":"))
        url += f"&geoUrn={quote(geo, safe='')}"
    if actively_hiring:
        name, value = ACTIVELY_HIRING
        url += f"&{name}={quote(json.dumps(value, separators=(',', ':')), safe='')}"
    if page > 1:
        url += f"&page={int(page)}"
    return url


# ---------------------------------------------------------------- locations

# Small alias table: LinkedIn shows either spelling.
ALIASES = {
    "bengaluru": ["bangalore"],
    "bangalore": ["bengaluru"],
    "mumbai": ["bombay"],
    "kolkata": ["calcutta"],
    "chennai": ["madras"],
    "gurugram": ["gurgaon"],
    "gurgaon": ["gurugram"],
    "new delhi": ["delhi"],
    "delhi": ["new delhi"],
    "pune": ["poona"],
}


def _norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def place_tokens(place: str) -> list[str]:
    """Match tokens for a place like 'Bengaluru, Karnataka, India'.

    Uses the first part (the city) plus its aliases. If the user typed only a
    country or state, that single part is used.
    """
    first = _norm((place or "").split(",")[0])
    if not first:
        return []
    return [first] + [_norm(a) for a in ALIASES.get(first, [])]


def wrong_city_warning(locations_read: list[str], places: list[str]) -> str:
    """Warning text when fewer than half of the people read are in the searched places, else "".

    Used on the first page of a search that LinkedIn filtered by saved location IDs: if most
    people are elsewhere, the ID saved for the place is probably the wrong one. Empty locations
    are not counted (they cannot be checked).
    """
    shown = [loc for loc in locations_read if (loc or "").strip()]
    if not shown or not places:
        return ""
    matching = sum(1 for loc in shown if location_matches(loc, places))
    if matching * 2 >= len(shown):
        return ""
    cities = " or ".join(dict.fromkeys((p or "").split(",")[0].strip() for p in places if (p or "").strip()))
    return (f"Warning: Most results are not in {cities}. The saved location may be wrong. "
            "Check Settings -> Locations set up on LinkedIn.")


def clearly_elsewhere(person_location: str, places: list[str]) -> bool:
    """True only when the shown location names somewhere else ("Pune/Pimpri-Chinchwad Area" for a
    Hyderabad search). Empty, a wanted place, or only a wider region of it ("India",
    "Telangana, India") is not elsewhere: LinkedIn already filtered by place, so those are kept."""
    if not (person_location or "").strip() or location_matches(person_location, places):
        return False
    wider = {_norm(part) for p in places for part in (p or "").split(",") if _norm(part)}
    parts = [_norm(x) for x in person_location.split(",") if _norm(x)]
    return not (parts and all(x in wider for x in parts))


def location_matches(person_location: str, places: list[str]) -> bool:
    """True when the person's displayed location contains any wanted place.

    An empty location can't be verified, so it does not match.
    """
    loc = _norm(person_location)
    if not loc:
        return False
    padded = f" {loc} "
    for place in places:
        for tok in place_tokens(place):
            if tok and f" {tok} " in padded:
                return True
    return False
