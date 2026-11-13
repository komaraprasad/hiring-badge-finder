"""Profile-URL normalization."""
from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

_IN_RE = re.compile(r"^/in/([^/?#]+)", re.IGNORECASE)


def normalize_profile_url(url: str) -> str:
    """Return 'https://www.linkedin.com/in/<slug>' or '' when not a profile URL.

    - query string and fragment removed
    - trailing slash removed
    - host forced to www.linkedin.com (country sub-domains like in.linkedin.com)
    - slug lower-cased so the same person never appears twice
    """
    if not url:
        return ""
    url = url.strip()
    if url.startswith("/"):
        url = "https://www.linkedin.com" + url
    try:
        p = urlparse(url)
    except ValueError:
        return ""
    host = (p.hostname or "").lower()
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        return ""
    m = _IN_RE.match(p.path)
    if not m:
        return ""
    slug = unquote(m.group(1)).strip().lower()
    if not slug:
        return ""
    return f"https://www.linkedin.com/in/{slug}"
