"""Company Website lookup, with a fake DNS: tests never use the internet."""
import pytest

from app import website


def fake_mx(*domains):
    calls = []

    def has_mx(d):
        calls.append(d)
        return d in domains
    has_mx.calls = calls
    return has_mx


@pytest.mark.parametrize("company,stems", [
    ("Acme Labs Pvt Ltd", ["acmelabs", "acme"]),
    ("Okta (Auth0)", ["okta"]),
    ("Sonata Software", ["sonatasoftware", "sonata"]),
    ("", []),
])
def test_domain_stems(company, stems):
    assert website.domain_stems(company) == stems


def test_first_domain_that_receives_mail_is_kept_and_cached():
    mx = fake_mx("acmelabs.in")
    cache = {}
    assert website.find_domain("Acme Labs", has_mx=mx, cache=cache) == "acmelabs.in"
    n = len(mx.calls)
    assert website.find_domain("acme labs", has_mx=mx, cache=cache) == "acmelabs.in"
    assert len(mx.calls) == n                   # same company: no new lookups


def test_nothing_is_invented():
    assert website.find_domain("", has_mx=fake_mx("acme.com")) == ""
    assert website.find_domain("Nowhere Co", has_mx=fake_mx()) == ""


def test_network_errors_are_not_turned_into_not_found():
    def broken(_):
        raise OSError("offline")
    with pytest.raises(OSError):
        website.find_domain("Acme", has_mx=broken)


def test_company_name_that_is_a_domain_is_used_as_is():
    mx = fake_mx("eightfold.ai", "eightfoldai.com")
    assert website.find_domain("Eightfold.ai", has_mx=mx) == "eightfold.ai"
    assert mx.calls[0] == "eightfold.ai"


def test_generic_words_are_never_guessed_as_a_domain():
    mx = fake_mx("talent.com", "senior.com", "global.com", "recruiting.com")
    for name in ("Talent", "Senior", "Global", "Recruiting"):
        assert website.find_domain(name, has_mx=mx) == ""


def test_website_from_the_about_page_is_cleaned():
    from app.main import _clean_website
    assert _clean_website("https://www.Acme.com/careers") == "acme.com"
    assert _clean_website("http://lnkd.in/abc") == "" and _clean_website("not a site") == ""
