"""Company Website for saved people. Never contacts LinkedIn.

The company name is turned into one word, common endings are tried ("acmelabs.com", ".in", ...), and the
first domain that can receive mail is kept (public DNS MX lookup via Cloudflare). It is a lookup by
NAME: a matching domain may belong to a different company with the same name. Nothing else is fetched.
"""
from __future__ import annotations

import json
import re
import ssl
import unicodedata
import urllib.parse
import urllib.request

TLDS = (".com", ".in", ".co.in", ".io", ".ai", ".co", ".net", ".tech")
# Dropped from company names before building a domain ("Acme Labs Pvt Ltd" -> "acmelabs").
_LEGAL = {"pvt", "private", "ltd", "limited", "llp", "llc", "inc", "incorporated", "corp", "corporation",
          "co", "company", "plc", "gmbh", "group", "the"}


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


# Single words that are not a company's own domain ("Talent" -> talent.com was a wrong guess).
_GENERIC = {"talent", "senior", "global", "recruiting", "recruitment", "hiring", "hr", "human", "people",
            "team", "group", "tech", "technology", "technologies", "solutions", "services", "consulting",
            "india", "software", "digital", "lead", "manager", "partner", "specialist", "careers", "jobs"}


def domain_stems(company: str) -> list[str]:
    """'Okta (Auth0)' -> ['okta']; 'Acme Labs Pvt Ltd' -> ['acmelabs', 'acme']."""
    base = re.sub(r"\(.*?\)", " ", company or "")
    words = [w for w in re.split(r"[^a-z0-9]+", _ascii(base)) if w and w not in _LEGAL]
    stems = []
    if words:
        stems.append("".join(words))
        if len(words) > 1:
            stems.append(words[0])
    return [s for s in dict.fromkeys(stems) if len(s) >= 2 and s not in _GENERIC]


def _tls_context() -> ssl.SSLContext:
    """Trust what Windows trusts (works behind HTTPS-inspecting proxies); certificates are still checked."""
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return ssl.create_default_context()


_TLS = _tls_context()


def cloudflare_has_mx(domain: str, timeout: float = 6.0) -> bool:
    """True when public DNS lists a mail server for the domain. Raises OSError on network trouble."""
    url = "https://cloudflare-dns.com/dns-query?" + urllib.parse.urlencode({"name": domain, "type": "MX"})
    req = urllib.request.Request(url, headers={"Accept": "application/dns-json"})
    with urllib.request.urlopen(req, timeout=timeout, context=_TLS) as r:  # noqa: S310 (fixed https host)
        data = json.loads(r.read().decode())
    return data.get("Status") == 0 and any(a.get("type") == 15 for a in data.get("Answer") or [])


def find_domain(company: str, has_mx=cloudflare_has_mx, cache: dict | None = None) -> str:
    """First '<stem><tld>' that can receive mail, or ''. Network errors propagate (retry later)."""
    key = (company or "").strip().lower()
    if cache is not None and key in cache:
        return cache[key]
    found = ""
    named = (company or "").strip().lower()
    if re.fullmatch(r"[a-z0-9-]+(\.[a-z]{2,})+", named) and has_mx(named):   # "Eightfold.ai" is its own domain
        found = named
    for stem in ([] if found else domain_stems(company)):
        for tld in TLDS:
            if has_mx(stem + tld):
                found = stem + tld
                break
        if found:
            break
    if cache is not None:
        cache[key] = found
    return found
