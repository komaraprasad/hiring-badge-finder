import pytest

from app.detection import (BADGE, LIKELY, NOT_HIRING, classify, hiring_text_match,
                           parse_title_company, strip_degree)

TRUE_POSITIVES = [
    "#hiring",
    "#Hiring engineers now",
    "We're hiring",
    "we are hiring backend developers",
    "We’re hiring!",
    "I'm hiring",
    "I am hiring SDETs",
    "Now hiring: QA leads",
    "Hiring for my team",
    "Hiring: Java developers",
    "Engineering Manager | Hiring - SDET",
    "Acme is hiring",
    "Looking to hire a PM",
    "Join my team at Acme",
    "Join our team of engineers",
    "Senior Engineering Manager - Hiring SDETs for my team",
    "HIRING FOR data roles",
    "Director of Engineering | We're Hiring",
]

FALSE_POSITIVES = [
    "Hiring process consultant",
    "Hiring strategy advisor",
    "Hiring strategies for startups",
    "Hiring expert and speaker",
    "Hiring consultant at Acme",
    "ex-hiring manager",
    "Hiring Manager at Globex",
    "Talent Acquisition Specialist",
    "Engineering Manager at Acme",
    "Passionate about the hiring experience",
    "",
]


@pytest.mark.parametrize("text", TRUE_POSITIVES)
def test_hiring_regex_true_positives(text):
    assert hiring_text_match(text), text


@pytest.mark.parametrize("text", FALSE_POSITIVES)
def test_hiring_regex_false_positives(text):
    assert hiring_text_match(text) == "", text


def test_counts_meet_spec_minimums():
    assert len(TRUE_POSITIVES) >= 15
    assert len([t for t in FALSE_POSITIVES if t]) >= 10


def test_hashtag_overrides_false_positive_topic():
    assert hiring_text_match("Hiring process consultant #hiring")


def test_false_positive_removed_but_real_phrase_kept():
    assert hiring_text_match("Hiring process expert | we're hiring")


def test_badge_confirmed_with_all_signals():
    status, ev = classify(True, True, "Senior Engineering Manager - Hiring SDETs for my team")
    assert status == BADGE
    assert ev == "frame + See open roles + 'Senior Engineering Manager - Hiring SDETs for my team'"


def test_badge_alone():
    assert classify(True, False, "Engineer at Acme") == (BADGE, "frame")


def test_likely_from_open_roles():
    assert classify(False, True, "Engineer at Acme") == (LIKELY, "See open roles")


def test_likely_from_text():
    status, ev = classify(False, False, "We're hiring | EM at Acme")
    assert status == LIKELY and "hiring" in ev.lower()


def test_text_found_only_in_card_text():
    status, ev = classify(False, False, "EM at Acme", "Ann Lee EM at Acme now hiring for QA")
    assert status == LIKELY and ev.startswith("text:")


def test_not_hiring():
    assert classify(False, False, "Hiring process consultant") == (NOT_HIRING, "")


def test_long_headline_is_shortened_in_evidence():
    status, ev = classify(True, False, "We're hiring " + "x" * 200)
    assert status == BADGE and len(ev) < 100 and "..." in ev


@pytest.mark.parametrize("headline,title,company", [
    ("Engineering Manager at Acme", "Engineering Manager", "Acme"),
    ("Engineering Manager @ Acme Corp", "Engineering Manager", "Acme Corp"),
    ("Senior EM at Acme | Hiring", "Senior EM", "Acme"),
    ("Okta (Auth0) | Engineering Leadership | Identity", "", ""),          # no at/@: nothing invented
    ("Director at Okta (Auth0) | Engineering Leadership | Identity", "Director", "Okta (Auth0)"),
    ("Head of QA at Acme || Platform", "Head of QA", "Acme"),
    ("Head of QA at Acme • Platform", "Head of QA", "Acme"),
    ("Engineering Manager @NVIDIA - SRE/Platform | Leadership", "Engineering Manager", "NVIDIA"),
    ("Hiring Architect@eBay", "Hiring Architect", "eBay"),
    ("Hiring Architect@eBay | Ex Google, Ex IIT", "Hiring Architect", "eBay"),
    ("Senior EM | Platform at Acme", "Senior EM", "Acme"),                   # title cut at the first pipe
    ("Data Platform Lead", "", ""),                                         # 'at' inside a word is not a split
    ("Lead atAcme", "", ""),                                                # 'at' needs spaces
    # known limitation (README): the first '@' wins, so the real role is lost
    ("We are hiring @ All Levels | EM at Acme", "", "All Levels"),        # a slogan is not a title
    ("Just a headline with no company", "", ""),
    ("", "", ""),
])
def test_title_company_parse(headline, title, company):
    assert parse_title_company(headline) == (title, company)


@pytest.mark.parametrize("raw,clean", [
    ("Ravindra Pai • 2nd", "Ravindra Pai"),
    ("Ann Lee • 1st", "Ann Lee"),
    ("Ann Lee • 3rd", "Ann Lee"),
    ("Ann Lee • 3rd+", "Ann Lee"),
    ("Ann Lee · 2nd", "Ann Lee"),
    ("Ann Lee·3rd+", "Ann Lee"),
    ("Ann Lee", "Ann Lee"),
    ("2nd Avenue Consulting", "2nd Avenue Consulting"),      # only a trailing suffix is removed
])
def test_strip_degree(raw, clean):
    assert strip_degree(raw) == clean


@pytest.mark.parametrize("headline,company", [
    # real headlines from a run (Nov 2026) that gave wrong companies before
    ("Hiring @BIK + MANIFEST AI l HR Generalist @ Bik | PGDM @ ISBR Business School |", "BIK + MANIFEST AI"),
    ("Hiring for L4/L7 Developer, share your updated resume to lokesh.b@benisontech.com", ""),
    ("HR Professional | Teams|vloforce solutions| MBA aspirant swarnalisarbadhikari@gmail.com", ""),
    ("Seasoned TA Leader | XLRI Alum | Team Empowerment at Aster GCC, Unacademy, Cuemath", ""),
    ("Ex-Recruiter at Amazon", ""),
    ("Recruiter | Former HR at Initech", ""),
    ("Recruiter at Amazon | Ex-Flipkart", "Amazon"),
    ("Recruiter at Acme, Inc. | Hiring", "Acme, Inc."),
    ("Executive Recruiter at Acme", "Acme"),
    ("Talent Sourcer @Rippling | GTM Recruiter", "Rippling"),
])
def test_company_is_never_an_email_a_list_or_a_past_job(headline, company):
    assert parse_title_company(headline)[1] == company


# ---------------------------------------- job titles are never companies (real export, 12 Nov 2026)
from app.detection import looks_like_job_title


@pytest.mark.parametrize("text", [
    "Talent Acquisition Partner", "Human Resources Manager", "HR", "Talent Scout", "Senior Recruiter",
    "Recruiting Lead", "Global HR Talent Acquisition Specialist", "Talent Aquisition Specialist - APAC",
    "Senior Lead Talent Acquisition", "Technical Talent Acquisition Specialist", "We're hiring",
])
def test_job_titles_seen_as_companies_are_recognised(text):
    assert looks_like_job_title(text)


@pytest.mark.parametrize("text", [
    "Mirafra Technologies", "in-tech Group India", "Computacenter", "Meesho", "GE Vernova", "Nutanix",
    "Eightfold.ai", "VOIS", "Pioneer Technology", "ZopSmart", "G2", "QSC", "Talent.com", "Partners Group",
    "Lead Squared", "",
])
def test_real_companies_are_kept(text):
    assert not looks_like_job_title(text)


def test_slogan_and_trailing_dot():
    from app.detection import parse_title_company
    assert parse_title_company("We’re hiring @ Nutanix.") == ("", "Nutanix")
    assert parse_title_company("Recruiter at Acme, Inc.") == ("Recruiter", "Acme, Inc.")
