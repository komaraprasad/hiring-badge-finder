"""Hiring-badge detection (Step 2 of the spec).

Pure functions, no I/O, so they are easy to test.

Status values:
  "Badge confirmed" - the #HIRING photo frame was seen on the card
  "Likely hiring"   - no frame, but a "See open roles" link or hiring wording
  "Not hiring"      - none of the above (never saved)
"""
from __future__ import annotations

import re

BADGE = "Badge confirmed"
LIKELY = "Likely hiring"
NOT_HIRING = "Not hiring"

# Wording that suggests the person is hiring (case-insensitive).
HIRING_RE = re.compile(
    r"#hiring"
    r"|\bwe(?:'|’| a)?re\s+hiring\b"
    r"|\bi(?:'|’| a)?m\s+hiring\b"
    r"|\bnow\s+hiring\b"
    r"|\bhiring\s+for\b"
    r"|\bhiring\s*[:|\-–—]"
    # spec example: "Hiring SDETs for my team" (added: the listed patterns alone miss it)
    r"|\bhiring\b[^.|]{0,40}\bfor\s+(?:my|our)\s+team\b"
    r"|\bis\s+hiring\b"
    r"|\blooking\s+to\s+hire\b"
    r"|\bjoin\s+(?:my|our)\s+team\b",
    re.IGNORECASE,
)

# Topic words that mention hiring without the person actually hiring.
FALSE_POSITIVE_RE = re.compile(
    r"\bhiring\s+process\b"
    r"|\bhiring\s+strateg(?:y|ies)\b"
    r"|\bhiring\s+expert\b"
    r"|\bhiring\s+consultant\b"
    r"|\bex[\s\-]hiring\b",
    re.IGNORECASE,
)

HASHTAG_HIRING_RE = re.compile(r"#hiring", re.IGNORECASE)


def _strip_false_positives(text: str) -> str:
    return FALSE_POSITIVE_RE.sub(" ", text)


def hiring_text_match(text: str) -> str:
    """Return the matched hiring phrase, or '' when there is none.

    False-positive topics are removed first, unless #hiring is present.
    """
    if not text:
        return ""
    if HASHTAG_HIRING_RE.search(text):
        return "#hiring"
    cleaned = _strip_false_positives(text)
    m = HIRING_RE.search(cleaned)
    return m.group(0).strip() if m else ""


def classify(badge_frame: bool, open_roles: bool, headline: str = "",
             card_text: str = "") -> tuple[str, str]:
    """Return (status, evidence) for one result card.

    Evidence is a short string such as
    "frame + See open roles + 'Hiring SDETs for my team'".
    """
    headline = (headline or "").strip()
    text_for_match = f"{headline} \n {card_text or ''}"
    phrase = hiring_text_match(text_for_match)

    parts: list[str] = []
    if badge_frame:
        parts.append("frame")
    if open_roles:
        parts.append("See open roles")
    if phrase:
        if headline and hiring_text_match(headline):
            snippet = headline if len(headline) <= 60 else headline[:57] + "..."
            parts.append(f"'{snippet}'")
        else:
            parts.append(f"text: '{phrase}'")

    if badge_frame:
        return BADGE, " + ".join(parts)
    if open_roles or phrase:
        return LIKELY, " + ".join(parts)
    return NOT_HIRING, ""


# "at" needs spaces around it; "@" may touch both words ("Architect@eBay").
_TITLE_AT_RE = re.compile(r"^(?P<title>.+?)(?:\s+at\s+|\s*@\s*)(?P<company>.+)$", re.IGNORECASE)
# The company (and title) end at the first " | ", " || " or " • ".
_PART_END_RE = re.compile(r"\s+(?:\|\|?|•)\s+")
# The company alone also ends at any "|", at " - " ("Rippling - Always hiring!" -> "Rippling"), at a
# lone " l " used as a bar, and at a second "@".
_COMPANY_END_RE = re.compile(r"\s*\|\s*|\s+[-–—]\s+|\s*!{2,}\s*|\s*~|\s*;\s*|\s+l\s+|\s*@")
# Email addresses are not companies: "share your resume to a.b@acme.com" names no employer.
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# "Ex-Amazon", "Former X at Y", "Alum at Y": a past job, not where they work now.
_PAST_RE = re.compile(r"(?:\bex|\bformer(?:ly)?|\bpreviously|\balum(?:ni|nus|na)?)\s*[-:]?\s*$", re.IGNORECASE)
# "A, B, C": several companies listed, so which is current is unknown. "Acme, Inc." is one company.
_LIST_RE = re.compile(r",\s*(?!(?:inc|llc|ltd|llp|plc|co|corp|gmbh)\b)\S", re.IGNORECASE)
_DEGREE_SUFFIX_RE = re.compile(r"\s*[•·]\s*(?:1st|2nd|3rd\+?)\s*$", re.IGNORECASE)


# Words that make a text a JOB TITLE, not a company ("Senior Talent Acquisition Specialist",
# "Talent Scout", "HR"). Used to refuse such a text as a company name (real pages, Nov 2026: the
# Experience reader gave 13 of 25 titles as companies).
_JOB_WORDS = re.compile(
    r"\b(?:recruit(?:er|ing|ment)?|talent|acquisition|sourc(?:er|ing)|specialist|manager|management|partner|"
    r"lead|leader|head|director|executive|associate|officer|scout|analyst|generalist|coordinator|consultant|"
    r"engineer|developer|architect|intern|trainee|founder|co-founder|ceo|cto|cfo|coo|chro|vp|vice president|"
    r"president|hr|hrbp|human resources?|people (?:ops|operations|partner)|senior|sr\.?|junior|jr\.?|principal|"
    r"staff|chief|assistant|advisor|administrator|team|hiring|professional)\b", re.IGNORECASE)
# A "company" that is really a slogan: "We're hiring", "Actively hiring", "Open to work".
_SLOGAN_RE = re.compile(r"^(?:(?:we(?:'|’)?re|we are|i(?:'|’)?m|i am|actively|now|currently)\s+)?(?:hiring|recruiting)\s*!*$"
                        r"|^open to work\b", re.IGNORECASE)


def looks_like_job_title(text: str) -> bool:
    """True when the text reads as a job title or a hiring slogan, not as a company name.
    Two or more title words, or the whole text is one title word ("HR", "Recruiter")."""
    t = " ".join((text or "").split())
    if not t:
        return False
    if _SLOGAN_RE.match(t):
        return True
    if re.search(r"\w\.(?:com|in|io|ai|co|net|org|tech)\b", t, re.IGNORECASE):
        return False                              # "Talent.com", "Eightfold.ai": a company's own domain
    words = _JOB_WORDS.findall(t)
    n_words = len(re.findall(r"[A-Za-z][\w.&'-]*", t))
    return len(words) >= 2 or (len(words) == 1 and n_words == 1)


def strip_degree(name: str) -> str:
    """'Ann Lee • 2nd' -> 'Ann Lee' (also '·' and 1st / 3rd / 3rd+)."""
    return _DEGREE_SUFFIX_RE.sub("", (name or "").strip()).strip()


def parse_title_company(headline: str) -> tuple[str, str]:
    """Split 'Title at Company' / 'Title @ Company'. Empty strings if no match.

    Nothing is invented: when the headline has no 'at' or '@', both are empty.
    """
    headline = _EMAIL_RE.sub(" ", (headline or "")).strip()
    m = _TITLE_AT_RE.match(headline)
    if not m:
        return "", ""
    last = re.split(r"\s*[|•]\s*", m.group("title"))[-1]
    if _PAST_RE.search(m.group("title")) or re.match(r"(?:ex|former(?:ly)?)\b", last.strip(), re.IGNORECASE):
        return "", ""
    title = re.split(r"\s*\|", m.group("title"))[0].strip(" |-–—")
    if _SLOGAN_RE.match(title):                  # "We're hiring @ Nutanix": no title, only the company
        title = ""
    company = _COMPANY_END_RE.split(_PART_END_RE.split(m.group("company"))[0])[0].strip(" |-–—")
    if company.count("(") > company.count(")"):   # cut inside a bracket: "Paninian ( Hiring" -> "Paninian"
        company = company[:company.rfind("(")].strip(" |-–—")
    if company.endswith(".") and not re.search(r"\b(?:inc|ltd|co|corp|llc|pvt|bros)\.$", company, re.IGNORECASE):
        company = company.rstrip(".")             # "Nutanix." -> "Nutanix"; "Acme, Inc." keeps its dot
    if _LIST_RE.search(company):
        return title, ""
    return title, company
