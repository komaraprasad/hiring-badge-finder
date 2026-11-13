# Deploying Hiring Badge Finder to Render

The dashboard and database run on Render. The LinkedIn reading still happens in each user's own
browser, through the extension. Render never contacts LinkedIn.

## Before you start: no login

This deployment has **no password**. Anyone who has the address can open the dashboard, start searches
and download every saved lead (names, LinkedIn URLs, companies). Share the address only with the people
who should use it. Adding a shared password later is a small change.

## 1. Put the project on GitHub (or GitLab)

Render deploys from a git repository. In the `hiring-badge-finder` folder:

```
git init
git add .
git commit -m "Hiring Badge Finder"
```

Then create a **private** repository and push to it. `.gitignore` already leaves out the local database
(`data/`), `backups/`, `.venv/` and `.env`, so no lead data is uploaded.

## 2. Create the service on Render

1. On https://dashboard.render.com click **New → Blueprint** and choose the repository.
2. Render reads `render.yaml` and proposes:
   - a web service `hiring-badge-finder` on the **Starter** plan, with exactly one instance;
   - a 1 GB disk mounted at `/var/data`, where the database lives (`DATABASE_URL`).
3. Click **Apply**. The first build takes 2–3 minutes. When it says **Live**, open the address shown,
   for example `https://hiring-badge-finder.onrender.com`.

Why a paid plan: the free plan has no disk, so all leads would be lost on every restart or deploy.
Why one instance: the database is a single SQLite file on that disk.

## 3. Install the extension (each user)

The extension must come **from the deployed dashboard**, not from the project folder. The downloaded
copy is set up to trust and talk to that one address only.

1. Open the deployed dashboard and click **Download** in the install box. Extract the zip.
2. In Edge, open `edge://extensions`, turn on **Developer mode**, click **Load unpacked** and choose
   the extracted folder.
3. Refresh the dashboard (Ctrl+F5). The install box disappears.
4. Sign in to LinkedIn **in the same Edge profile** with the account meant for this tool, never the
   company account.

After each new deploy that changes the extension, users download it again, replace the folder and click
**Reload** on the extension card. The dashboard tells them when this is needed.

## Settings

| Variable | Set by | Meaning |
|---|---|---|
| `DATABASE_URL` | render.yaml | `sqlite:////var/data/hiring_badge_finder.db` (on the disk) |
| `RENDER_EXTERNAL_URL` | Render, automatically | The service's own address; the only website the server and the extension trust |
| `HBF_PUBLIC_URL` | you, only with a custom domain | e.g. `https://leads.example.com` (https, no trailing slash) |
| `HBF_WEBSITE_LOOKUP` | render.yaml | `1` fills Company Website by DNS lookup; `0` turns it off |
| `PYTHON_VERSION` | render.yaml | 3.12.8 |

## Backups

Render keeps daily snapshots of the disk for 7 days (Dashboard → the service → **Disks**). To download a
copy: **Shell** tab → `sqlite3 /var/data/hiring_badge_finder.db ".backup /tmp/hbf.db"`, then copy it out,
or use **Export all** on the Leads tab for an Excel file.

## If it stops working after a deploy

| What you see | Why | Fix |
|---|---|---|
| Dashboard says the extension is too old / asks to Reload | The deploy changed the extension | Download it again from the dashboard, replace the folder, click **Reload** in `edge://extensions`, Ctrl+F5 |
| "Could not reach the dashboard server" | Render was restarting for more than ~2 minutes (the extension retries that long) | Wait until the service is **Live**, then Launch again: it continues where it stopped |
| "Already 5 searches running" | Old searches lost their browser tab | Wait 15 minutes (quiet searches close by themselves) or press **Stop** on them |
| Page does not load / "Service Unavailable" | Deploy or build in progress, or failed | Render → the service → **Events** / **Logs** |
| All leads gone after a restart | The service runs without the disk (e.g. free plan) | Use the Starter plan with the `hbf-data` disk, as in render.yaml |

Avoid pushing changes while a search is running: each push restarts the server.

## Running on a computer instead

Nothing changes: `run.bat` still starts it at http://localhost:8000, and the extension loaded from the
`extension` folder talks to it.
