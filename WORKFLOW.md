# Hiring Badge Finder: complete workflow (version 1.54.0)

> **1.50.0:** Step 5 (Company Industry / Size and Hiring For) is ON and runs WHILE the search runs. Its main way
> is "fast details": each lead's profile data from LinkedIn's internal profile API (see step 5); the page-based
> Tasks A and B below are the fallback. Hiring For came back in 1.49.0. The Excel/CSV has 15 columns.
> Business Email, Hiring Company and Posted stay removed.

This describes exactly how the tool works, step by step, so the same workflow can be rebuilt in
another tool. File and endpoint names refer to this project.

---

## 1. Parts

| Part | Where | Job |
|---|---|---|
| Dashboard (web page) | `app/static/index.html`, served by FastAPI at `http://localhost:8010` | The person builds a search, launches it, watches progress, exports Excel/CSV |
| Server | `app/main.py` (API), `app/storage.py` (SQLite), `app/urlbuilder.py`, `app/detection.py`, `app/export.py`, `app/website.py` | Builds the LinkedIn address, stores people, removes duplicates, enforces limits, hands out company-detail tasks, builds the files |
| Browser extension (Chrome/Edge, Manifest V3) | `extension/` | Opens LinkedIn **in the person's own signed-in browser** (hidden tab), reads what the page shows, sends it to the server |
| Bridge | `extension/dashboard_bridge.js` | Lets the dashboard page talk to the extension (`window.postMessage`) |
| Page readers | `extension/content_search.js` (people results), `extension/content_company.js` (company search, About page, Experience page) | Read the page text/markup; never click except where stated |
| Worker | `extension/background.js` | Opens/moves the hidden tab, does every pause, decides the next page |
| LinkedIn markup settings | `extension/config.js` | The ONLY place with LinkedIn-specific selectors, labels, regexes, pacing |

Data store: one SQLite file `data/hiring_badge_finder.db`.

---

## 2. Safety rules (enforced in code, not optional)

- Runs in the person's own browser with their own LinkedIn sign-in. No headless browser, no login
  automation, no stored cookies or passwords.
- Random **1-2.5 s pause before every LinkedIn page or request** (search pages, profile data, company
  pages). Set in `extension/config.js` (`pacing`, `fastDetails`). The search and 2 lead-details readers
  run at the same time, so LinkedIn sees up to 3 streams of requests together.
- **Search:** max **30 result pages per run**, max **300 people read per day** per browser.
- **Company details:** separate limits, per run at least **60** (2 per requested lead + 10) and **600 per day**.
- Stop **immediately** on any LinkedIn limit, "unusual activity", security check, captcha or sign-in
  page; keep that tab open so the person can see it.
- Nothing is invented: a value that cannot be read stays empty.
- One exception to "read only": the first time a new city is used, its LinkedIn location ID is looked
  up once (see step 4) and then remembered.

---

## 3. Step 1: the person builds a search (dashboard)

Inputs:
- **Who hires** (job titles): optional, 0-5. Presets: Recruiters, Tech hiring managers, Founders & CTOs.
- **Locations:** required, 1-10 cities.
- **Skills / keywords:** optional.
- **Leads wanted:** 25, 50, 75 or 100.
- **Use LinkedIn's "Actively hiring" filter:** on/off.
- **Continue where the last same search stopped:** on/off (skips pages already read).

Click **Launch search** -> `POST /api/search/start`.

## 4. Step 2: server builds the LinkedIn address

`app/urlbuilder.py`:

```
https://www.linkedin.com/search/results/people/
  ?keywords=("Title 1" OR "Title 2") AND hiring      <- "AND hiring" left out when Actively hiring is on
                                                      <- no titles: keywords = hiring
  &origin=GLOBAL_SEARCH_HEADER
  &geoUrn=["<city id>", ...]                          <- added by the extension (step 3)
  &activelyHiringForJobTitles=["-100"]                <- when Actively hiring is on
  &page=N                                             <- when continuing
```

The server creates a **job** row (status `running`) and returns the job id plus this address.

## 5. Step 3: city -> LinkedIn location ID

LinkedIn filters by city only with its numeric location ID. Order tried:
1. IDs learned before in this browser (`chrome.storage`).
2. IDs shared on the dashboard server (`/api/geo`, table `geo_places`).
3. Built-in table in `extension/config.js` (Bengaluru 105214831, Mumbai 106164952, Hyderabad 105556991,
   Chennai 106888327, ...). A city name only matches places in India or with no country.
4. Unknown city: a hidden set-up tab on LinkedIn looks it up once (LinkedIn's own location
   suggestions; exact name only), saves it, then the search starts.
5. If that fails: the dashboard shows why and offers **Do it myself on LinkedIn**.

## 6. Step 4: reading the results (extension)

1. The worker opens the address in a **background tab** inside a collapsed tab group
   "Hiring Badge Finder" (focus never moves).
2. `content_search.js` waits for result cards, zooms the page out so every card is drawn, and reads
   each card: name, profile link, headline, location, connection degree, the card's
   **"Current: Title at Company"** line, and the card text (for classification only).
3. **Hiring badge rule:** a person counts only if the photo has a frame (`profile-framedphoto`) **AND**
   the card has a "See open roles" link (`/opportunities/hiring-opportunities/`). A frame alone
   (e.g. green #OpenToWork) does not count.
4. Sends the page's people to `POST /api/ingest`.
5. The server: classifies each person, drops people outside the asked cities (text check, only when no
   location ID was used), removes duplicates (same profile in this run or any earlier run), applies
   the daily cap, saves the new people, and answers **continue / stop**.
6. If continue: the worker pauses 1-2.5 s, then loads `&page=N+1` in the same tab.
   Ends when: target reached, no more pages, 30 pages, daily cap, or a LinkedIn warning.

### What is saved per person (table `people`)

| Field | Source |
|---|---|
| Name, profile URL, headline, location | Results card |
| Current designation, Company | Headline "Title at/@ Company"; if none, the card's "Current: ..." line; later possibly the Experience page (step 5) |
| Hiring status, badge frame, open roles, evidence | Badge rule above |
| Search titles / locations, job id, first found, last seen | The tool |
| Company website | Company name + common endings, kept if public DNS shows it receives mail (a lookup by name) |
| company_url, profile_checked, detail_note | Company-details step |

Company name rules (`app/detection.py`): email addresses are ignored; the name ends at `|`, ` - `, `;`,
`!!`, a lone ` l `, or a second `@`; a list ("A, B, C") is treated as unknown; "Ex-", "Former",
"Alum" before "at" means a past job and is ignored.

## 7. Step 5: Company Industry, Company Size and Hiring For (while the search runs)

**Main way: fast details** (`fastDetails.on` in `extension/config.js`, `fastReader` in `background.js`).
When the search opens, one more hidden tab opens `https://www.linkedin.com/robots.txt` (a plain linkedin.com
page, so requests carry the person's session). 2 readers then loop:
1. `POST /api/details/fast`: saves the lead just read, counts the next page against the limits and hands
   out the next lead (claimed for 3 min, stored in the database, so two readers never get the same lead,
   even after a server restart; a refused page frees its lead at once).
2. Pause a random 1-2.5 s, then request that lead's **profile data from LinkedIn's internal profile API**
   (`/voyager/api/identity/dash/profiles?...memberIdentity=<slug>`) inside the helper tab.
3. From it: the current position (no end date) gives company, exact company link, industry and size.
4. Open roles: a second request fetches the person's profile page HTML (`/in/<slug>/`, in the background,
   never shown) and reads its "Hiring" box: up to 5 roles. Roles that show an age over 31 days are dropped.

**Fallback (only when the profile data cannot be read): page-based tasks** in hidden tabs. The server hands
out **one task at a time** (`GET /api/details/next`), in this order:

**Task A, company named on the card** (and never looked up before):
1. Pause 1-2.5 s, ask `POST /api/details/page` (counts the page; refuses past the limits).
2. Open LinkedIn company search: `/search/results/companies/?keywords=<name>`.
3. Keep the result only if exactly **one company page** carries that exact name (case, punctuation and
   Pvt/Ltd/Inc ignored). None or several different companies -> saved as `not_found` with the reason.
4. Pause, count, open `/company/<id>/about/`, read the values under **"Industry"** and
   **"Company size"** (size must look like "51-200 employees").
5. Save to table `companies` (`POST /api/companies`).

**Task B, person whose company is unknown** (no company on the card, or Task A found no single match):
1. Pause, count, open the person's Experience page `/in/<id>/details/experience/`.
2. Read the page text: the first line with job dates; it must end in **"Present"** (current job).
   The lines above it are the title and the company, in LinkedIn's on-screen order:
   - one role: `Title / Company · Full-time / Jan 2023 - Present`
   - several roles: `Company / Full-time · 3 yrs / Title / Jan 2023 - Present`
   No current job, or self-employed / freelance -> left empty, with the reason.
3. The company's link on that page gives the **exact company page** (no name matching).
   `POST /api/details/profile` saves company + link on the person.
4. If that company page was not read before: pause, count, open its About page, read Industry and
   Size, save (keyed by the page address). If the company had no link: Task A by name.

Repeats until no tasks are left or a limit is reached. Each company and each profile is read
**once**; later runs reuse them. A page that never answers ends the step after 60 s (alarm).
Any LinkedIn notice stops it at once. The run log shows every step, ending with
**"Company details: done, N companies read"**.

Joining for the export: a person with a company link gets that exact company's values; otherwise
the values of the company with the same name. Unknown -> empty.

## 8. Step 6: export

- **Export this run (Excel)**, or the **Leads** tab with filters (several titles / cities / dates /
  text) -> **Export filtered** / **Export all**. CSV at `/api/export.csv`.
- Excel: a formatted table (filters, frozen header, clickable LinkedIn links, real dates);
  spreadsheet formulas in cell text are neutralised.
- Columns, in order (15): Person Name, Current Designation, Headline, Location, Location Matches Search,
  Person LinkedIn URL, Hiring Status, Company Name, Company Industry, Company Size, Company Website,
  Source Platform, Hiring For (Open Roles), Date Collected (IST), Last Verified Date (IST).
- Wait for "Lead details: done" before exporting, or the company columns are still filling.

## 9. Server API (summary)

| Method + path | Used by | Purpose |
|---|---|---|
| `POST /api/search/start` | dashboard | create the job, build the address |
| `POST /api/search/geo` | extension | which location IDs were used |
| `POST /api/ingest` | extension | people of one page; answers continue/stop |
| `POST /api/heartbeat`, `/api/error`, `/api/search/finish`, `/api/search/note` | extension | run log, errors, end reason |
| `POST /api/stop` | dashboard | Stop safely |
| `GET /api/status`, `/api/jobs`, `/api/events` (live stream) | dashboard | progress |
| `GET /api/details/next` | extension | next company-details task |
| `POST /api/details/page` | extension | count one company-details page (or refuse) |
| `POST /api/details/profile` | extension | company read from a person's Experience page |
| `POST /api/companies` | extension | industry/size of one company, or not found + reason |
| `GET/POST/DELETE /api/geo` | both | shared city location IDs |
| `GET /api/leads`, `/api/export.xlsx`, `/api/export.csv` | dashboard | leads list and files |
| `GET/POST /api/settings` | dashboard | daily cap, filters |

## 10. Tables

`people`, `jobs`, `job_seen` (duplicates per run), `reads_by_day` (daily cap), `search_progress`
(continue where stopped), `geo_places` (city IDs), `companies` (industry/size + reason),
`detail_pages_by_day` (company-details daily limit), `settings`.

## 11. Known limits (stated honestly)

- LinkedIn's terms restrict automated collection. The rules above reduce the risk to the account;
  they do not remove it. Use the separate test account, never the company account.
- The company search, About page and Experience page readers were written from LinkedIn's visible
  labels and have only partly been confirmed on real pages (1.31.0: 6 of 25 leads filled; 1.32.0
  not yet run on LinkedIn).
- Title matching is LinkedIn's: some people's headlines do not contain the searched title (see the
  "Headline Has Searched Title" column).
- Emails are mostly unverified guesses.
