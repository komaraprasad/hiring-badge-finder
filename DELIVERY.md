# Hiring Badge Finder - delivery summary

## 1. Summary

**Problem.** People who show LinkedIn's purple **#HIRING** ring on their photo are hiring right now, which makes them
the best people to offer staffing services to. Finding them by hand means scrolling search results and looking at
every photo. **Solution.** A small program on your own computer plus a browser add-on. You type the job titles and
cities you want; the add-on reads the LinkedIn search results your own browser shows, keeps only people with the
#HIRING ring and open jobs linked, and the program builds an Excel list (name, job title, company, company industry and size, city, LinkedIn link,
and more). **Result.** In 13 test runs on a real account (4 and 5 Nov 2026) it saved 226 people. 7 of those runs
collected the full 25 of 25 leads from only 3 to 5 result pages each. People without the hiring signal are not saved.

## 2. Setup and use (Windows 11 + Edge)

Setup, once:

1. Install Python 3.12 from python.org and tick **Add python.exe to PATH**.
2. In Edge, create a separate profile (profile picture > Other profiles > Add profile) named `HBF test`.
   Sign in to LinkedIn there with the **account given for this tool, never the company account**.
3. In the project folder, double-click **run.bat**. It installs what it needs and opens the dashboard at
   http://localhost:8000. If another program already uses that address, start it on port 8010 instead
   (see README, "Server address and ports") and open http://localhost:8010. Keep the black window open.
4. In the `HBF test` window open `edge://extensions`, turn on **Developer mode**, click **Load unpacked** and choose
   the project's `extension` folder. Check the card says version **1.42.0**.
5. Refresh the dashboard (Ctrl+F5).

Every day:

1. Open the dashboard in the `HBF test` window.
2. Type job titles (or click a preset such as **Recruiters**), type a city, choose 25 to 100 leads.
3. Click **Launch search** and stay on the dashboard.
4. A new city is set up by itself during that same Launch, in a hidden LinkedIn tab (one extra page load per city),
   then the search starts. If that fails, the row shows why and offers **Do it myself on LinkedIn**. Each city is set
   up once for everyone using this dashboard.
5. When the run finishes, click **Export this run (Excel)**, or use the **Leads** tab to filter everything collected and
   **Export filtered** / **Export all**.

## 3. Demo flow (6 steps, about 5 minutes)

1. **Open the dashboard** and point out the top bar: the "Live" light and today's reads out of the daily safety limit
   of 300.
2. **Build a search:** click the **Recruiters** preset, type **Bengaluru**, choose **25** leads. Read out the
   plain-English sentence under the form.
3. **Launch.** Show the progress ring, the run log (one line per page with a 4 to 8 second pause between pages) and
   the counters for pages read, people seen, not hiring and duplicates.
4. **Signal stream:** people appear one by one with the #HIRING tag, name, headline, company and city, and a link to
   their profile.
5. **Export:** click **Export this run (Excel)** and open the file: a formatted table with filters and clickable links.
6. **Leads tab and Recent searches:** filter by several roles and cities, export only those, and open a past search
   to see its people or run it again.

## 4. Cost

| Item | Cost |
|---|---|
| The tool itself (Python, FastAPI, SQLite, openpyxl and the browser add-on) | **$0**: free, open-source software |
| Hosting | **$0**: it runs on your own computer; nothing is hosted |
| LinkedIn | The tool uses an ordinary LinkedIn account. It does not need a paid plan, but LinkedIn's "Actively hiring" filter may only work on paid plans (see section 5) |
| Email lookup | **$0**: email guesses use free public DNS. No paid email-finder service is used |

**Account risk, stated honestly.** LinkedIn's terms of use restrict automated collection of its data. Searches only
read search results in your own browser, at a human pace: a random 4 to 8 second pause between pages, at most 30
pages per search and 300 people per day. The one exception is the first search in a new city: the tool then
calls LinkedIn's private location lookup with the signed-in session and, if that fails, operates its Locations filter
once. Both are more noticeable than reading, and cities in the built-in table need neither. It stops at once if LinkedIn shows a limit, security check or "unusual
activity" page. These rules **reduce** the risk of the account being restricted; they **do not remove** it. That is why
it must run on the separate account given for this purpose, never the company account. Whether that risk is
acceptable is a business decision.

## 5. Accuracy

The tool does not claim "100% accuracy" or "zero waiting". Every value comes from what LinkedIn shows on the results
page; missing values are left empty, never invented. The 4 to 8 second pause between pages is a **safety rule** to
protect the account, not a slowness: a 4-page search has 3 pauses, so 12 to 24 seconds of waiting alone.

**Verified**

- The page markers (photo ring, "See open roles" link, name, headline, city) were checked on a real signed-in LinkedIn
  search page (Nov 2026).
- A person is saved as **Badge confirmed** only when the photo has a ring **and** the card has a "See open roles" link.
  In the captured sample, 9 of 9 purple-ring cards had that link.
- Bengaluru's LinkedIn location number (105214831) was taken from a real LinkedIn search address.
- Real runs on the test account (13 runs, 4-5 Nov 2026, from the tool's own records; 226 people saved in total):
  - Recruiters in Hyderabad: **25 of 25** saved from **3** pages (and again from **4** pages the next day).
  - Recruiters in Bengaluru: **25 of 25** from **3**, **4** and **3** pages in three runs.
  - Founders & CTOs in Hyderabad: **25 of 25** from **4** pages.
  - Engineering Managers in Hyderabad: **22 of 25** from **3** pages, stopped by the daily safety limit.
  - Engineering Managers in Chennai: **12 of 25** from **2** pages; LinkedIn had no more results.
  - Engineering Managers in Mumbai, before the city set-up existed: **9 of 25** from **27** pages (260 people read),
    because Mumbai had to be searched across all of India. This is why every city now needs its own location number.
- Paging: one real run read **27** result pages, so moving past page 3 has worked on LinkedIn itself.
- Duplicates are removed, including across searches.

**Not verified**

- **Ring type:** the photo address only proves there is *a* ring. A green (#OpenToWork) ring looks the same in the
  address; the "See open roles" rule separates them, but only one other-ring example has been seen.
- **Desktop page layout:** the markers were captured on a narrow window; the full-width layout was not captured.
- **Free accounts:** LinkedIn marks the "Actively hiring" filter as a paid feature. On a free account it may be ignored;
  the tool then shows a warning and still applies the ring check.
- **Cities:** only Bengaluru's number was captured here; the other built-in numbers were supplied by the user and not
  checked. The automatic lookup for other cities uses an undocumented LinkedIn address and was tested only on a mock. A wrong result is caught by exact-name checks and the "Most results are not in <city>" warning.
- **The "Next" button itself:** the tool opens later pages directly (page=2, 3, ...) and uses LinkedIn's "Next" button
  only to see whether more pages exist. That check worked in the real runs above (up to page 27), but the button's
  markup beyond page 3 was never captured and inspected, so a LinkedIn change there could end a search early.
- **Emails** are mostly guesses (first.last@company), not checked.

**What was tested and how**

- **Automated tests on version 1.40.0 (12 Nov 2026):** 225 passed; the extension-in-Chrome tests were skipped
  (no Chrome that can load extensions on the build PC), so the in-browser flow of 1.40.0 is NOT verified by tests.
- Stability: not fully established. On earlier versions the browser tests sometimes failed at random: one limit-page
  test, and test-browser crashes on 1.23.0 and 1.24.0 that passed when rerun. The cause was not found.
- Mumbai was confirmed on real LinkedIn with 106164952: its filter showed "Mumbai, Maharashtra, India". The other new
  city numbers were supplied by the user and have not been checked on LinkedIn here.
- The browser tests load the real add-on into a test copy of Chrome and run it against a saved copy of LinkedIn's
  results page (no LinkedIn connection). They cover reading cards, the ring rule, paging with the safety pauses,
  stopping on LinkedIn warnings, the location set-up, the wrong-city warning and sending results to a dashboard on any
  port.

## Known limits

- **Company / Industry / Size / Website** (1.42.0): each new lead's own Experience page gives the exact
  company link, then that company's About page gives its name, industry, size and website. Read by 4 hidden
  tabs in parallel WHILE the search runs (each tab: random 4-8 s pause per page; per run at most 2 pages per
  requested lead + 10, per day 600). A company read once by link is reused at once, with no page. About
  60-75 s for 25 new companies; more parallel tabs = more page loads a minute = higher LinkedIn limit risk. That page markup was written from LinkedIn's visible labels and was only partly
  confirmed on real pages: when it is drawn differently the cells stay empty and the run log says why.
- **Company Website** is a lookup by company NAME (public DNS, never LinkedIn): it can be a different
  company with the same name. Treat it as a hint.
- **Removed in 1.40.0:** Business Email, Hiring For, Hiring Company, Posted, the "Open roles" pop-up reading
  and the background open-role date fetching. No code is left for them.
