# Hiring Badge Finder

Finds LinkedIn people who show the purple **#HIRING** photo frame, filtered by job title and
location, and exports them to CSV or Excel.

## Read this first: what is and is not finished

| Part | State |
|---|---|
| Server, database, export, dashboard, extension | Built and tested (see "Tests") |
| **LinkedIn page markers** (`extension/config.js`) | **Verified** on one real signed-in people-search page (Nov 2026, narrow 734px layout). The desktop layout was not captured. See `frame_type_unverified` below. |
| **Location IDs** (geoUrn) | Built in: **Bengaluru only** (the one ID captured from a real LinkedIn address). Every other place is set up automatically on its first search: LinkedIn opens, the extension chooses the city in LinkedIn's Locations filter, and the search starts by itself (see "Locations: built-in table and one-time set-up"). No place is ever widened to the whole country. |
| **"Actively hiring" filter** | Dashboard option, **on by default**. Adds `activelyHiringForJobTitles=["-100"]`. LinkedIn labels it Premium: **unverified on free accounts**. If LinkedIn drops it, the run shows a warning and carries on without it. |
| `app/main.py` (FastAPI) | Run and tested (`tests/test_api.py`). |
| Postgres | Built (`app/pgstorage.py`), chosen when `DATABASE_URL` starts with `postgres://` (used by the Render deployment with a Neon database). The full test suite runs against it when `HBF_TEST_PG` is set. |

**"100% accuracy" and "0 latency" are not possible, and the tool does not pretend otherwise.**
Every field is exactly what LinkedIn displays on the results page (nothing is invented; missing fields stay
empty), but LinkedIn decides what it shows. The random 1 to 2.5 second wait between pages is a safety rule
that protects the account, so a search of 100 people takes about 3 minutes (search plus lead details), not zero seconds.

## What it does

1. You fill in job titles, locations and how many leads you want on the dashboard.
2. Your own browser (through the small extension) opens the LinkedIn people search in a hidden tab.
3. The extension reads the result cards LinkedIn already shows and sends them to the dashboard.
4. The dashboard keeps people who show the frame (**Badge confirmed**) and, if you switch the option off,
   people who only look like they are hiring (**Likely hiring**).
5. You download the list as CSV or Excel.

The server never talks to LinkedIn. It stores no LinkedIn cookies and does no login.

## Version

Extension **1.60.0** (`extension/manifest.json`). The dashboard refuses to launch with an older extension and tells you
to click **Reload** in `edge://extensions`.

## Install (Windows 11 + Edge)

**Use a separate Edge profile signed in to the LinkedIn account you were given for this tool, never the company
account.** Install the extension in that profile only (steps in `LIVE_TEST.md`, Part A).

1. Install Python 3.12 from python.org. Tick **Add python.exe to PATH**.
2. Double-click **run.bat**. The first run installs the packages (about a minute) and opens
   http://localhost:8000. Keep the black window open. If port 8000 is taken by another program, start the server
   on another port instead (for example `.venv\Scripts\python -m uvicorn app.main:app --port 8010`) and open
   http://localhost:8010 (see "Server address and ports").
3. Open `edge://extensions` (Chrome: `chrome://extensions`), turn on **Developer mode**, click **Load unpacked**
   and choose the `extension` folder inside this project. After an update, click **Reload** on the extension card
   and refresh the dashboard (Ctrl+F5). The dashboard will not launch a search while the extension is older than
   the dashboard (it tells you to click Reload), and shows an error if the extension does not answer within 15 s.
4. (On a computer without the project folder: click **Download** on the dashboard, extract the zip, and load that
   folder instead.)
5. Refresh the dashboard. The install box disappears.
6. Sign in to LinkedIn in the same browser.

## Use

1. **Search** tab: add 1 to 5 job titles and 1 to 10 locations (type and press Enter), pick 25, 50, 75 or 100 leads.
2. Click **Launch search**. Stay on the dashboard. The LinkedIn tab runs in the background in a closed tab group
   named "Hiring Badge Finder". (A place without a location ID asks for the one-time set-up first, see above.)
3. Watch the progress dial and run log. People appear in the signal stream as they are found.
4. **Export this run (Excel)**, or the **Excel** button on a Recent search card. The **Leads** tab shows everyone saved
   so far, with filters (several roles and places at once), **Export filtered** and **Export all**. The CSV is still
   available at `/api/export.csv`.

CSV and Excel columns, in this order (15): Person Name, Current Designation, Headline, Location, Location Matches Search, Person LinkedIn URL,
Hiring Status, Company Name, Company Industry, Company Size, Company Website, Source Platform,
Hiring For (Open Roles), Date Collected (IST), Last Verified Date (IST).

**Hiring For (Open Roles)** comes from the same LinkedIn profile data as the company (see below): up to 5 of
the person's open roles as "Title · Company · Place". A role that shows its age and is older than 31 days is
left out; a role with no age shown is kept. Empty = none shown, or not read.
The Excel file is a formatted table (filters, clickable links, real dates); long text stays inside its own cell.

**Company Industry and Company Size (ON).** Read WHILE the search runs, in a hidden helper tab of the same group.

**Main way (fast details, `fastDetails` in `extension/config.js`):** the helper tab opens a plain
linkedin.com address and, from there, requests each lead's **profile data from LinkedIn's internal profile
API** (`/voyager/api/identity/dash/profiles`, the same data LinkedIn's own profile page loads), with your
signed-in session. That request gives the current company, its exact LinkedIn link, industry and size. Open
roles come from a second request: the person's own profile page (`/in/<x>/`), fetched in the background and
read, never shown. 2 readers at once, each with a random 1-2.5 s pause between its leads. **So every saved
lead's profile is fetched from LinkedIn** (no visible tab shows it).

**Fallback (only when that data cannot be read):** visible pages in hidden tabs, as below.

1. Company named on the card (headline "X at Y" or the card's "Current: X at Y" line): LinkedIn company search for
   the name, then the About page of the ONE result with exactly that name (ignoring case, punctuation, Pvt/Ltd/Inc).
2. No company on the card, or that name matched no single company: the person's own Experience page
   (/in/<x>/details/experience/, every job listed at once). Only a CURRENT job
   (dates say "Present") is used; its link goes to the exact company page, so no name guessing. A profile showing
   no current job leaves the cells empty.

Every company and every profile is read once and remembered. Cost: one LinkedIn page per profile / company search /
About page / profile data request, each after a random 1-2.5 s pause, with their own limits: per search at least
60 (2 per requested lead + 10) and 600 per day (separate from the 30 search pages per run). It stops at once on a limit, sign-in or security page. Expect about 3-5 extra
minutes for 25 new leads; the run log shows each step, and leads appear before this starts. The profile and company
page markup was written from LinkedIn's visible labels; it was NOT captured from a real page yet.

- **Company Website** (`app/website.py`, filled in the background after people are saved): the company name
  plus common endings (.com, .in, .co.in, .io, .ai, .co, .net, .tech), kept only if public DNS (Cloudflare)
  shows it receives mail. A lookup by name: it can belong to a different company with the same name. Never
  contacts LinkedIn. Switch it off by starting the server with `HBF_WEBSITE_LOOKUP=0`.
- **Last Verified Date** = the last time the person was seen again in a search (not a separate re-check).

## Server address and ports

- A search sends its results to **the dashboard it was launched from**, on whatever local port that dashboard runs
  (8000, 8010, ...). No setting is needed.
- The address on the extension's **Options** page (default `http://localhost:8000`) is only a fallback, for a search
  not started from a dashboard page. Launching never changes it.
- If that server can't be reached, the dashboard shows a red message naming the address tried, e.g.
  `Could not reach the dashboard server at http://localhost:8010 (...)`. The LinkedIn tab's stop message names it too.

## Locations: built-in table and one-time set-up

LinkedIn filters a search by its own location ID. Every place in a search must have one, so LinkedIn returns only
people in that place.

| Place | ID | Source |
|---|---|---|
| Bengaluru (also "Bangalore", "Bengaluru, Karnataka, India", ...) | 105214831 | Captured from a LinkedIn people-search address by the user, Nov 2026 |

Removed from the table (no documented capture; set them up like any other place): Hyderabad 105556991, Chennai
106888327, Visakhapatnam 111421590 (seen only in another project's Sales Navigator cache) and India 102713980
(source unknown). A test (`test_built_in_location_ids_are_only_captured_ones_with_a_source`) checks every built-in
entry has a `// source:` comment.

**One Launch does everything (new places are set up on the way):**

1. You press **Launch search** once. The dashboard sends the places (1 to 10; 11 are refused) and the
   "Use LinkedIn's Actively hiring filter" setting.
2. Places with a known ID (built in, or set up before by anyone using this dashboard) cost nothing extra.
3. Unknown places are set up **one at a time in one hidden LinkedIn tab** (in the "Hiring Badge Finder" tab group).
   That tab reads no people. For each place it:
   - goes back to a plain people search (no filters), so places never stack;
   - clicks the **Locations** chip (`div[aria-label="Filter by Locations"]`) and types the place into
     `Add a location` (never into the main search box, never the People chip);
   - takes an option only if its full text equals what you typed, or, for a short name like "Mumbai", if exactly
     one option starts with "Mumbai," (so never "Navi Mumbai" or "Mumbai Metropolitan Region"). Zero or several:
     it clicks nothing and the dashboard lists the names LinkedIn offered, asking you to type one exactly;
   - checks the only ticked place is the chosen one (another place already ticked: it stops, unticks nothing);
   - clicks **Show results**, waits up to 15 s for exactly one `geoUrn` ID in the address, checks the chip now shows
     the place, and saves it.
   - Pauses: 0.8 to 1.8 s between actions, 4 to 8 s between places. **Each new place costs one extra LinkedIn page
     load**; more than 3 new places shows a warning first.
4. When every place has an ID, one search address with all IDs (and `activelyHiringForJobTitles=["-100"]` when the
   setting is on) opens in the background tab group, and people are read as before.
5. The dashboard shows each place (✓ ready, ⏳ setting up, ✗ failed with the reason) and the run log
   ("Setting up Mumbai (1 of 2)…", "Applying filters…", "Reading page 1…").
6. A place that fails gets a **Do it myself on LinkedIn** button (3 clicks in a tab in front, with a banner). Once you
   pick it, the same run continues by itself.
7. A login, limit, "unusual activity" or security-check page at any point stops everything, shows the message in
   red and leaves the LinkedIn tab open. Do not retry.

**Rule change (asked for on 2026-11-06):** setting up a new place is the only time the tool clicks or types on
LinkedIn. Searches themselves only read results.

**Actively hiring.** It is sent in the search address. If LinkedIn drops it (it is labelled Premium; free accounts may
not get it), the extension tries LinkedIn's own **Actively hiring** chip once ("Any job title", Show results). If that
fails too, the run continues without it, a yellow warning says so, and the summary line ends with
"filter not applied". The filter never blocks a run.

Saved places are listed under **Settings → Locations set up on LinkedIn**, each with **Remove** (removes it from the
server and this browser).

**Wrong-city warning.** As a last safety net, after the first
page of a search, the server compares the Location of the people read with the places asked for. If fewer than half
match, a yellow warning appears (the run continues; nothing saved is changed):
`Most results are not in <city>. The saved location may be wrong. Check Settings -> Locations set up on LinkedIn.`
Remove the place there and set it up again.

## Repeating a search ("Continue from where the last same search stopped")

On by default. The tool remembers, for each identical search (same titles, locations, skills and Actively-hiring
setting, in any order or case), the next page to read. Running it again opens LinkedIn directly at that page
(`&page=N`) instead of re-reading pages you already collected, so it takes seconds per *new* page. The random 1 to
2.5 second safety wait between pages is unchanged.

- **Per browser:** the page is remembered separately for each browser (each person's own LinkedIn account, which
  sees its own result order). Someone else running the same search starts at page 1 in their account, so their
  own first pages are never skipped. Already-saved people are still skipped, so there are no duplicates.
- **Same day only (India time):** a new day starts at page 1 again (new people put on the frame every day and
  LinkedIn reorders results).

- If a run stopped part-way through a page (target or daily limit), that page is read again next time so nobody
  is skipped (already-saved people are skipped as usual).
- At the end of LinkedIn's results the next run starts at page 1 again.
- If the continued page is empty now (LinkedIn shows fewer results), the same tab goes back to page 1 by itself.
- Switch it off for a fresh scan from page 1: LinkedIn reorders results, so new people can appear on early pages.

## Limits you should know

- **`frame_type_unverified`**: the photo address (`profile-framedphoto`) only proves the photo has *a* frame. A real
  capture showed a green (open-to-work style) frame with the same address. So the rule is now:
  **Badge confirmed = framed photo AND a "See open roles" link (`/opportunities/hiring-opportunities/`) in the same card.**
  A framed photo without that link is skipped (not a badge, not saved) and counted in the progress line as
  "framed without open roles (skipped)".
  - Possible missed leads: a person with the purple #HIRING frame but no job linked to it has no such link and is skipped.
  - Evidence so far: 9 of 9 purple-frame cards had the link; only **one** other-frame example has been seen, so the
    rule is unverified beyond that sample.

- People who hid their profile photo, or are outside your network, can't be checked for the frame.
- LinkedIn free accounts may hit a monthly search limit ("commercial use limit"). The tool stops and tells you.
- At most 30 result pages per search, 10 people per page.
- At most 300 people read per day per browser (change it under Settings on the dashboard).
- Up to 5 searches can run at the same time.
- "Likely hiring" is a guess from words and the "See open roles" link. Only "Badge confirmed" means the frame was seen.

## Known limits

- **Company / Industry / Size / Website**: mainly from each lead's profile data (LinkedIn's internal profile
  API, 2 readers, random 1-2.5 s pause each; see "Company Industry and Company Size" above). Fallback: the
  lead's Experience page, then the company's About page, in 2 hidden tabs. Per run at most 2 pages per requested
  lead + 10, per day 600. A company read once by link is reused at once, with no page. More readers or shorter
  pauses = more LinkedIn requests a minute = higher LinkedIn limit risk. The fallback page markup was written
  from LinkedIn's visible labels and was only partly confirmed on real pages: when it is drawn differently the
  cells stay empty and the run log says why.
- **Company Website** is a lookup by company NAME (public DNS, never LinkedIn): it can be a different
  company with the same name. Treat it as a hint.
- **Removed in 1.40.0:** Business Email, Hiring Company, Posted, the "Open roles" pop-up reading and the
  background open-role date fetching. (**Hiring For** came back in 1.49.0, read from the profile data.)

## Where each field comes from (accuracy)

| Field | Source | Known failure cases |
|---|---|---|
| Name | Text of the first link to the card's own person (`/in/<id>`); a name repeated for screen readers is reduced to one copy; a trailing " • 2nd" / " · 3rd+" is removed (in the extension and again on the server) | Mutual-connection names are ignored. A name with odd spacing in the repeat may stay doubled |
| URL | First `/in/<id>` link in the card, cut at the ID (drops `?...` and `/opportunities/...`) | None seen |
| Headline | First text line after the degree and before the Connect/Follow/Message button | No headline: a single line that looks like a place ("X, Y" or "... Area") is taken as the location; a headline shaped like "Engineer, Acme" with no location line would be misread as a location |
| Title, Company | Headline split at the first " at " (spaces needed) or "@" (spaces optional: "Architect@eBay"). The title ends at the first "\|"; the company ends at the first " \| ", " \|\| " or " • " | **Known limitation:** the first "@" wins, so "We are hiring @ All Levels \| EM at Acme" gives title "We are hiring", company "All Levels". An "@" in an e-mail address would also split. No "at"/"@": both empty (nothing invented) |
| Location | Second line before the button | No location line: empty, so the location text filter drops the person (safe, but a possible miss) |
| Status | Framed own photo AND hiring-opportunities link → **Badge confirmed**; framed without the link → skipped; else "See open roles" or hiring wording → **Likely hiring** | #HIRING frame with no job linked is skipped (missed). No photo → never framed. "#hiring" in the headline without a frame is only **Likely hiring** |
| Evidence | What was seen: `frame`, `See open roles`, the headline or matched text | |
| Degree | The `• 2nd` / `• 3rd+` line | Not exported |

## Account-safety rules (built in)

- What is read from LinkedIn: people-search result pages, and for **each saved lead** their profile data
  (LinkedIn's internal profile API, for the company) and their profile page HTML (fetched in the background,
  for open roles). Only if that data cannot be read: the lead's Experience
  page and the company's About page. Location set-up opens LinkedIn's Locations filter once per new place.
- A random 1 to 2.5 second wait between result pages, and between each lead-details reader's requests
  (2 readers run at the same time as the search). Set in `extension/config.js` (`pacing`, `fastDetails`).
- Limits: 30 result pages per search, 300 people per day per browser; lead details at least 60 per search
  (2 per requested lead + 10) and 600 per day.
- The run stops at once if LinkedIn shows a limit, "unusual activity", sign-in or security-check page. The tab is left
  open (but not brought to the front) so you can read LinkedIn's message. Do not keep retrying.
- LinkedIn's terms of use restrict automated collection. Even at low volume LinkedIn may restrict an account.
  Decide with your manager whether that risk is acceptable. The pacing limits reduce the risk; they do not remove it.

## Updating the markers when LinkedIn changes its page

Only `extension/config.js` needs editing. Run `discovery/discover_cards.js` in the browser console on a LinkedIn
people-search page (F12, Console, paste, Enter), paste the result to your assistant, and put the markers into the
`badge`, `name`, `headline`, `location` and `next` entries. Then set `verified: true`, reload the extension, and
download the new zip from the dashboard (the version number in `manifest.json` triggers the Update box).

## Tests

```
.venv\Scripts\activate
pip install -r requirements-dev.txt
python -m playwright install chromium     (only for the browser tests)
pytest
```

The browser tests load the real extension into Chrome for Testing or Chromium against a **mock** results page. The mock
follows the structure captured from a real LinkedIn page (anonymised; see `tests/harness.py`). Set `CHROME_PATH` to
choose the browser, e.g. Playwright's Chromium under `%LOCALAPPDATA%\ms-playwright\chromium-*\chrome-win64\chrome.exe`.
Normal branded Chrome 137 and newer ignores `--load-extension`, so those tests need Chrome for Testing or Chromium.

## Pinned versions

`run.bat` installs `requirements.txt` and writes the exact versions it installed to `requirements.lock.txt`.
Every package is pinned to the version the tests passed with on Python 3.12 (fastapi 0.162.0, starlette 1.10.0,
pydantic 2.14.2).

## Folder map

```
app/            server (main.py, storage.py, detection.py, urlbuilder.py, export.py, static/index.html)
extension/      the browser extension (config.js holds every LinkedIn-specific marker)
discovery/      read-only console snippets to capture LinkedIn's real markup
tests/          pytest tests, mock results page, stand-in server
backups/        copy of the project before each fix round (not committed)
run.bat         creates the venv, installs, starts the server
```

Nothing is pushed to GitHub or deployed until you say "push".

