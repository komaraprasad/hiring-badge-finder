# Your live test - about 15 minutes (Windows 11 + Edge)

I can't do this part and I never open LinkedIn myself. You run it in your own browser.
Never send me a LinkedIn password: the tool doesn't need it.

**Use the LinkedIn account you were given for this test, NOT the company account.**
To keep them apart, use a separate Edge profile with the extension installed in that profile only.

Extension version for this test: **1.42.0**.

## Part A. A separate Edge profile (one time)

1. In Edge, click your profile picture (top right) > **Other profiles** > **Add profile** > **Continue without signing in**
   (or sign in to a personal Microsoft account; not the company one). Name it `HBF test`.
2. A new Edge window opens for that profile. Do everything below in **that window only**.
3. In that window, go to linkedin.com and sign in with the **test account you were given**.
   Check the photo at the top right: it must be the test account, not the company account.
4. If the test account is brand new, use it normally for a few days first (fill in the profile, add a few
   connections). New accounts are watched more closely.

## Part B. Start the dashboard and install the extension (in the `HBF test` window)

1. Start the dashboard:
   - If port 8000 is free: double-click **run.bat** and wait for `Starting Hiring Badge Finder at http://localhost:8000`.
   - If another program uses port 8000 (it did on this computer): in the project folder run
     `.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8010` and use http://localhost:8010 below.
   Keep the black window open.
2. Open the dashboard address in the `HBF test` window (close any copy that opened in your normal profile).
   The extension sends results to whichever address you launch from; nothing to set.
3. In the `HBF test` window open `edge://extensions` and turn on **Developer mode**.
   If an older Hiring Badge Finder is listed (for example one loaded from a downloaded zip), click **Remove** on it.
4. Click **Load unpacked** and choose the project's own extension folder:
   `C:\Users\Inspironlabs\Downloads\hiring-badge-finder\hiring-badge-finder\extension`
   (not the nested `...\hiring-badge-finder\hiring-badge-finder\hiring-badge-finder` copy).
   After every update just click **Reload** on the extension card. Check the card shows version **1.42.0**.
   Check that the extension is **not** installed in your normal (company) profile.
5. Refresh the dashboard (Ctrl+F5). The install/update box disappears.

## Part C. One Launch with a new city (about 2 minutes)

Listed cities (Mumbai, Hyderabad, Chennai, Delhi, Pune, Nagpur and others) need no set-up. To test the automatic route use a
city not on the list, such as **Lucknow, Uttar Pradesh, India**, in place of Mumbai in this part:

1. Job title `Engineering Manager`, location `Mumbai, Maharashtra, India`, leads **25**, Actively hiring ticked.
   Click **Launch search** once and **don't click anything else**.
2. The dashboard shows "Setting up 1 new place on LinkedIn" and a row for Mumbai: ⏳ then ✓ ready
   (Saved: Mumbai -> <number>). A hidden LinkedIn tab does this in the "Hiring Badge Finder" tab group; it costs one
   extra LinkedIn page load.
3. The search then starts by itself ("Applying filters…", "Reading page 1…").
4. If the Mumbai row shows ✗ with a reason instead: copy that reason for me. Then click **Do it myself on LinkedIn**,
   choose Mumbai in LinkedIn's Locations filter, click Show results; the run continues by itself.
5. Settings → **Locations set up on LinkedIn** lists Mumbai with the same number. A second search with Mumbai
   must not set it up again.

## Part D. The search

1. Job title: `Engineering Manager`. Location: `Mumbai, Maharashtra, India` (or `Bengaluru, Karnataka, India`).
   Leads wanted: **25**.
2. Leave **Use LinkedIn's Actively hiring filter** ticked (default). Click **Launch search** and note the time.
   Stay on the dashboard tab. LinkedIn runs in a closed tab group named "Hiring Badge Finder".
3. Wait until the run log says the search finished (usually 1 to 5 minutes).
4. If a yellow box says **Most results are not in Mumbai. The saved location may be wrong. Check Settings ->
   Locations set up on LinkedIn.**, the city you picked in Part C was probably not Mumbai. Note it, then remove Mumbai
   in Settings and set it up again.
5. If LinkedIn shows a security check, "unusual activity" or a limit page: **stop there**, do not retry,
   and send me the red message.

## Part E. What to send me

1. **Time taken**, from Launch search to the end of the run.
2. **The summary line** under the progress bar (it starts with "N of 25 saved · read ...").
3. **The last progress line** in the run log (for example `Finished: LinkedIn has no more results for this search`).
4. **5 CSV rows, including the Location column.** Download from the dashboard address + `/api/export.csv`
   (for example http://localhost:8010/api/export.csv), open it in Notepad, copy the header and 5 rows.
   Names may be replaced by initials.
5. **Actively hiring filter: applied or dropped?**
   - Dropped if the run log or a yellow box says `Warning: LinkedIn ignored the "Actively hiring" filter`.
   - Applied if no such warning appeared (you can also check the LinkedIn tab's address still contains
     `activelyHiringForJobTitles`).
6. **Ring colour for 3 names.** For 3 people marked **Badge confirmed**, open their profile on LinkedIn yourself and
   tell me the photo ring colour: **purple** (#HIRING) or **green** (#OpenToWork).
7. **Any red message**, copied exactly. Also any yellow warning (wrong city, filter dropped).
8. From Part C: the **Saved: Mumbai -> <number>** line, or the ✗ reason if it failed.
9. Whether the summary line ends with **filter not applied** (Actively hiring was not available).

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

## After your results

I fix only what your results show. Before each change I copy the project into `backups\`. Nothing is pushed until you
say "push".
