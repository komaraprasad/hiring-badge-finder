"""Static checks of the extension files (no browser needed)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

EXT = Path(__file__).resolve().parent.parent / "extension"
MANIFEST = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))


def test_manifest_v3_and_required_permissions():
    assert MANIFEST["manifest_version"] == 3
    assert set(MANIFEST["permissions"]) == {"storage", "tabs", "tabGroups", "scripting", "alarms"}   # alarms: own timer only
    assert "https://www.linkedin.com/*" in MANIFEST["host_permissions"]
    assert "http://localhost/*" in MANIFEST["host_permissions"]
    assert "http://127.0.0.1/*" in MANIFEST["host_permissions"]
    assert MANIFEST["background"]["service_worker"] == "background.js"


def test_content_scripts_are_scoped():
    by_js = {tuple(cs["js"]): cs for cs in MANIFEST["content_scripts"]}
    search = by_js[("config.js", "content_search.js")]
    assert search["matches"] == ["https://www.linkedin.com/search/results/people*"]
    bridge = by_js[("dashboard_bridge.js",)]
    assert not any("linkedin.com" in m for m in bridge["matches"])


def test_every_file_in_manifest_exists():
    files = [MANIFEST["background"]["service_worker"], MANIFEST["options_page"]]
    for cs in MANIFEST["content_scripts"]:
        files += cs["js"]
    for f in files:
        assert (EXT / f).is_file(), f


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("name", ["background.js", "content_search.js", "dashboard_bridge.js",
                                  "options.js", "config.js"])
def test_javascript_syntax(name):
    r = subprocess.run(["node", "--check", str(EXT / name)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_config_is_single_place_and_honest_about_verification():
    cfg = (EXT / "config.js").read_text(encoding="utf-8")
    assert "HBF_CONFIG" in cfg
    assert "verified: true" in cfg       # real markers applied (Nov 2026 capture)
    assert "profile-framedphoto" in cfg and '[role="listitem"]' in cfg


def _without_lookup(text):
    """The page script without its one allowed request: the location lookup."""
    return text[:text.index("async function lookupPlace")] + text[text.index("async function autoSetUp"):]


def _without_lead_reader(text):
    """background.js without readLeadInPage (runs inside the LinkedIn tab; see the test below)."""
    return text[:text.index("async function readLeadInPage")] + text[text.index("async function startFastDetails"):]


def test_fast_reader_only_reads_linkedins_profile_data_and_keeps_nothing():
    text = (EXT / "background.js").read_text(encoding="utf-8")
    fn = text[text.index("async function readLeadInPage"):text.index("async function readOpenRolesInPage")]
    assert fn.count("fetch(") == 1                       # one request: the configured profile-data path
    assert "pathTemplate" in fn and "http" not in fn.split("fetch(")[1].split(")")[0]   # relative: LinkedIn only
    for banned in ("callServer", "chrome.", "localStorage", "sessionStorage", "postMessage", "XMLHttpRequest"):
        assert banned not in fn, banned                  # the session value is used as a header, never stored or sent
    cfg = (EXT / "config.js").read_text(encoding="utf-8")
    assert "profileUrl: '/voyager/api/identity/dash/profiles?" in cfg


def test_open_roles_reader_is_one_relative_profile_page_request_and_keeps_nothing():
    """1.49.0: open roles come from the person's own profile page HTML: one request, LinkedIn only,
    no cookie read, nothing stored or sent anywhere by the reader itself."""
    text = (EXT / "background.js").read_text(encoding="utf-8")
    fn = text[text.index("async function readOpenRolesInPage"):text.index("async function startFastDetails")]
    assert fn.count("fetch(") == 1
    target = fn.split("fetch(")[1].split(",")[0]
    assert target.startswith("'/in/'") and "http" not in target
    for banned in ("document.cookie", "callServer", "chrome.", "localStorage", "sessionStorage", "postMessage",
                   "XMLHttpRequest", "click(", "window.open", "location.href ="):
        assert banned not in fn, banned


def test_no_secrets_or_external_calls_in_extension():
    for p in EXT.glob("*.js"):
        text = p.read_text(encoding="utf-8")
        if p.name == "content_search.js":                # the one location lookup may read it, nothing else
            text = _without_lookup(text)
        if p.name == "background.js":                    # ...and the fast lead-details reader (1.46.0)
            text = _without_lead_reader(text)
        assert "document.cookie" not in text, p.name
        assert "chrome.cookies" not in text, p.name
        assert "XMLHttpRequest" not in text, p.name


def test_page_script_never_opens_profile_pages():
    text = _without_lookup((EXT / "content_search.js").read_text(encoding="utf-8"))
    assert "location.href =" not in text and "window.open" not in text and "fetch(" not in text


def test_the_lookup_only_calls_linkedins_location_address_and_keeps_nothing():
    text = (EXT / "content_search.js").read_text(encoding="utf-8")
    fn = text[text.index("async function lookupPlace"):text.index("async function autoSetUp")]
    assert fn.count("fetch(") == 1 and fn.count("'/voyager/api/typeahead/") == 2
    assert "chrome.storage" not in fn and "send(" not in fn


def test_background_only_opens_linkedin_search_urls():
    text = (EXT / "background.js").read_text(encoding="utf-8")
    assert "https://www.linkedin.com/search/results/people/" in text



def test_built_in_location_ids_are_only_captured_ones_with_a_source():
    """Item B: every built-in location ID has a source comment, and only the captured ID is in the table."""
    import re as _re
    cfg = (EXT / "config.js").read_text(encoding="utf-8")
    block = cfg[cfg.index("ids: {"):cfg.index("},", cfg.index("ids: {"))]
    entries = [ln for ln in block.splitlines() if _re.match(r"\s*'[^']+':\s*'\d+'", ln)]
    assert entries, "no built-in entries found"
    for ln in entries:
        assert "// source:" in ln, ln
    ids = {_re.search(r":\s*'(\d+)'", ln).group(1) for ln in entries}
    assert {"105214831", "106164952", "105556991", "106888327"} <= ids
    for removed in ("111421590", "102713980"):           # never sourced
        assert f"'{removed}'" not in block


def test_city_table_values_from_the_supplied_list():
    cfg = (EXT / "config.js").read_text(encoding="utf-8")
    assert "'ahmedabad': '104990346'" in cfg and "90009627" not in cfg
    for line in ("'nagpur, maharashtra, india': '103504847'", "'nagpur': '103504847'",
                 "'dubai, united arab emirates': '104305776'", "'new york': '102571732'"):
        assert line in cfg, line


def test_private_member_line_is_not_read_as_a_headline():
    assert "/^linkedin member$/i.test(line)" in (EXT / "content_search.js").read_text(encoding="utf-8")


def test_removed_features_are_gone_from_the_extension():
    """Business email, open-roles pop-ups and open-role dates were removed (1.40.0): no code left."""
    for p in EXT.glob("*.js"):
        text = p.read_text(encoding="utf-8")
        for gone in ("fetchRolesDate", "addRoleDates", "readCardPopups", "readRoles", "openRolesPopup",
                     "/api/details/roles", "/api/roles/card", "role_dates", "popupMinMs"):
            assert gone not in text, (p.name, gone)
