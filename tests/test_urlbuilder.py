import json
from urllib.parse import parse_qs, unquote, urlparse

import pytest

from app.normalize import normalize_profile_url
from app.urlbuilder import (build_keywords, build_url, clean_list, location_matches,
                            place_tokens)


def test_keywords_single_role():
    assert build_keywords(["Engineering Manager"], []) == '"Engineering Manager" AND hiring'


def test_keywords_multiple_roles_and_extras():
    kw = build_keywords(["Engineering Manager", "QA Lead"], ["SDET", "fin tech"])
    assert kw == '("Engineering Manager" OR "QA Lead") AND hiring AND (SDET OR "fin tech")'


def test_keywords_with_place_names():
    kw = build_keywords(["CTO"], [], ["Bengaluru", "Mumbai"])
    assert kw == "CTO AND hiring AND (Bengaluru OR Mumbai)"


def test_url_without_geo():
    url = build_url(["Engineering Manager"], [], None, ["Bengaluru, Karnataka, India"])
    p = urlparse(url)
    assert (p.scheme, p.netloc, p.path) == ("https", "www.linkedin.com", "/search/results/people/")
    q = parse_qs(p.query)
    assert q["origin"] == ["GLOBAL_SEARCH_HEADER"]
    assert q["keywords"] == ['"Engineering Manager" AND hiring AND Bengaluru']   # city only, not the full phrase
    assert "geoUrn" not in q


def test_url_with_geo_drops_place_names_from_keywords():
    url = build_url(["Engineering Manager"], [], ["102713980", "105556991"], ["Bengaluru"])
    q = parse_qs(urlparse(url).query)
    assert json.loads(q["geoUrn"][0]) == ["102713980", "105556991"]
    assert "Bengaluru" not in q["keywords"][0]


def test_url_with_real_bengaluru_id():
    url = build_url(["Engineering Manager"], [], ["105214831"], ["Bengaluru, Karnataka, India"])
    assert "&geoUrn=%5B%22105214831%22%5D" in url


def test_url_actively_hiring_flag():
    url = build_url(["Engineering Manager"], [], None, ["Bengaluru"], actively_hiring=True)
    assert url.endswith("&activelyHiringForJobTitles=%5B%22-100%22%5D")
    assert "activelyHiring" not in build_url(["Engineering Manager"], [], None, ["Bengaluru"])


def test_url_encodes_special_characters():
    url = build_url(['Dev & "Ops"'], [], None, None)
    assert "&Ops" not in url.split("keywords=")[1].split("&origin")[0]
    assert unquote(url.split("keywords=")[1].split("&origin")[0]) == '"Dev & Ops" AND hiring'


def test_clean_list_trims_dedupes_and_caps():
    assert clean_list([" a ", "A", "", "b", "c"], 2) == ["a", "b"]
    assert clean_list(None, 5) == []


# ------------------------------------------------------------ location filter
@pytest.mark.parametrize("person,places,expected", [
    ("Bengaluru, Karnataka, India", ["Bengaluru, Karnataka, India"], True),
    ("Greater Bengaluru Area", ["Bengaluru, Karnataka, India"], True),
    ("Bangalore Urban, Karnataka, India", ["Bengaluru, Karnataka, India"], True),  # alias spelling
    ("Bangalore", ["Bengaluru"], True),
    ("Mumbai, Maharashtra, India", ["Bengaluru"], False),
    ("Mumbai, Maharashtra, India", ["Bengaluru", "Mumbai"], True),
    ("", ["Bengaluru"], False),                       # unknown location cannot be verified
    ("India", ["India"], True),
    ("Pune, Maharashtra, India", ["Maharashtra"], True),   # state typed alone matches the displayed text
    ("Pune, Maharashtra, India", ["Karnataka, India"], False),
])
def test_location_matches(person, places, expected):
    assert location_matches(person, places) is expected


def test_place_tokens_use_city_and_aliases():
    assert place_tokens("Bengaluru, Karnataka, India") == ["bengaluru", "bangalore"]
    assert place_tokens("") == []


def test_location_no_partial_word_match():
    assert not location_matches("Newark, New Jersey", ["York"])


# ------------------------------------------------------- URL normalization
@pytest.mark.parametrize("raw,expected", [
    ("https://www.linkedin.com/in/ann-lee/?miniProfileUrn=x", "https://www.linkedin.com/in/ann-lee"),
    ("https://www.linkedin.com/in/ann-lee/", "https://www.linkedin.com/in/ann-lee"),
    ("https://in.linkedin.com/in/Ann-Lee#top", "https://www.linkedin.com/in/ann-lee"),
    ("/in/ann-lee/?x=1", "https://www.linkedin.com/in/ann-lee"),
    ("https://www.linkedin.com/in/ann%2Dlee", "https://www.linkedin.com/in/ann-lee"),
    ("https://www.linkedin.com/company/acme/", ""),
    ("https://evil.example.com/in/ann-lee", ""),
    ("https://notlinkedin.com/in/ann-lee", ""),
    ("", ""),
    ("not a url", ""),
])
def test_normalize_profile_url(raw, expected):
    assert normalize_profile_url(raw) == expected



def test_url_page_number_only_after_page_one():
    assert "&page=" not in build_url(["EM"], [], None, ["Pune"])
    assert build_url(["EM"], [], None, ["Pune"], page=4).endswith("&page=4")



@pytest.mark.parametrize("places,expected", [
    (["Mumbai, Maharashtra, India"], "CTO AND hiring AND Mumbai"),
    (["Delhi, India"], "CTO AND hiring AND Delhi"),
    (["New Delhi, Delhi, India", "Pune, Maharashtra, India"], 'CTO AND hiring AND ("New Delhi" OR Pune)'),
    (["Mumbai", "mumbai, India"], "CTO AND hiring AND Mumbai"),          # same city once
])
def test_places_without_ids_use_only_the_city_in_keywords(places, expected):
    assert build_keywords(["CTO"], [], places) == expected


def test_actively_hiring_filter_replaces_the_hiring_keyword():
    # with the filter on, "hiring" is not required in the profile text (it cut engineering-manager searches to 1 page)
    url = build_url(["Engineering Manager", "Senior Engineering Manager"], [], None, None, actively_hiring=True)
    kw = unquote(url.split("keywords=")[1].split("&origin")[0])
    assert kw == '("Engineering Manager" OR "Senior Engineering Manager")'
    assert "activelyHiringForJobTitles" in url
    # filter off: unchanged, the word stays
    assert "AND hiring" in unquote(build_url(["Engineering Manager"], [], None, None))
    # skills and places are kept
    assert build_keywords(["CTO"], ["SDET"], ["Pune"], require_hiring=False) == "CTO AND Pune AND SDET"



def test_wrong_city_warning():
    from app.urlbuilder import wrong_city_warning
    mumbai = ["Mumbai, Maharashtra, India"]
    assert wrong_city_warning(["Mumbai, Maharashtra, India", "Greater Bombay Area", "Pune"], mumbai) == ""   # 2 of 3
    assert wrong_city_warning(["Mumbai", "Pune"], mumbai) == ""                                      # half is enough
    w = wrong_city_warning(["Bengaluru, Karnataka, India", "Bengaluru", "Mumbai"], mumbai)                # 1 of 3
    assert w == ("Warning: Most results are not in Mumbai. The saved location may be wrong. "
                 "Check Settings -> Locations set up on LinkedIn.")
    assert wrong_city_warning(["", ""], mumbai) == ""                                                # nothing to check
    assert "Mumbai or Pune" in wrong_city_warning(["Delhi", "Delhi"], mumbai + ["Pune, Maharashtra, India"])
